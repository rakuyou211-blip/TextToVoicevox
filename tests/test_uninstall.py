# -*- coding: utf-8 -*-
"""1行導入で入れた物のアンインストール（uninstall.ps1 と、install.ps1 が「設定 → アプリ」に載せる所）。"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNINSTALL_ONE_LINER = ("irm https://raw.githubusercontent.com/rakuyou211-blip/"
                       "TextToVoicevox/main/uninstall.ps1 | iex")


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_uninstall_ps1_has_no_bom():
    """irm | iex で読まれるので BOM を付けない（install.ps1 と同じ）。"""
    with open(os.path.join(ROOT, "uninstall.ps1"), "rb") as f:
        assert not f.read(3).startswith(b"\xef\xbb\xbf")


def test_uninstall_one_liner_is_documented():
    """消し方の1行が、README と説明書（日英）と台本に正しく載っている。"""
    for name in ("README.md", "README.en.md", "install.ps1", "uninstall.ps1",
                 os.path.join("docs", "manual", "index.html"),
                 os.path.join("docs", "manual", "en", "index.html")):
        assert UNINSTALL_ONE_LINER in _read(name), name


def test_install_lists_the_app_only_with_an_uninstaller():
    """「設定 → アプリ」に載せるのは、消す台本（uninstall.ps1）を置いたときだけ（install.ps1 は main
    から読まれ、入れるのは最新の Release なので、台本の無い前の版を入れることがある）。
    台本は UTF-8 として読んで動かす（-File だと、日本語の Windows で文字が化ける）。"""
    body = _read("install.ps1")
    func = body.split("function Register-Uninstall", 1)[1].split("\n    try {", 1)[0]
    assert "if (-not (Test-Path -LiteralPath $script)) { return $false }" in func
    assert "[Text.Encoding]::UTF8" in func and "-NoProfile -Command" in func and "-NoProfile -File" not in func
    assert "T2V_UNINSTALL_FROM" in func and "T2V_UNINSTALL_FROM" in _read("uninstall.ps1")
    for name in ("DisplayName", "DisplayVersion", "InstallLocation", "UninstallString", "DisplayIcon"):
        assert name in func, name
    # アイコンを作った後に載せる（DisplayIcon に使う）。載せられなくても導入は止めない
    after = body.split("CreateShortcut", 1)[1]
    assert "$listed = Register-Uninstall $ico" in after
    assert after.split("$listed = Register-Uninstall $ico", 1)[0].rstrip().endswith("try {")


def test_uninstaller_ships_only_for_windows():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import make_release
    finally:
        sys.path.pop(0)
    try:
        tracked = make_release.tracked_files(ROOT)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git が使えない環境")
    if "uninstall.ps1" not in tracked:
        pytest.skip("uninstall.ps1 がまだ git に入っていない")
    assert "uninstall.ps1" in make_release.files_for("windows", tracked)
    assert "uninstall.ps1" not in make_release.files_for("mac", tracked)


def _run_uninstall(dest):
    """「設定 → アプリ」から動くときと同じく、UTF-8 として読んで動かす（確認は全部「はい」）。"""
    script = os.path.join(ROOT, "uninstall.ps1").replace("'", "''")
    env = dict(os.environ, T2V_DIR=str(dest), T2V_YES="1")
    env.pop("T2V_UNINSTALL_FROM", None)
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         f"& ([ScriptBlock]::Create([IO.File]::ReadAllText('{script}', [Text.Encoding]::UTF8)))"],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows の PowerShell で動かす")
def test_uninstall_removes_the_app_folder(tmp_path):
    """入れたフォルダを消す。ほかの場所のアイコンや「設定 → アプリ」の登録には触らない
    （それらは、このフォルダを指している物だけ消す）。"""
    app = tmp_path / "Text'To Voicevox"
    (app / "assets").mkdir(parents=True)
    for name in ("main.py", "core.py", "winlaunch.py", "settings.json", "assets/app-icon.ico"):
        (app / name).write_text("x", encoding="utf-8")
    r = _run_uninstall(app)
    assert r.returncode == 0, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
    assert not app.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows の PowerShell で動かす")
def test_uninstall_refuses_a_folder_without_the_app(tmp_path):
    """TextToVoicevox が入っていないフォルダは、言われても消さない（T2V_DIR の書き間違いなど）。"""
    other = tmp_path / "documents"
    other.mkdir()
    (other / "main.py").write_text("x", encoding="utf-8")   # 一部だけ似ていても消さない
    (other / "keep.txt").write_text("x", encoding="utf-8")
    r = _run_uninstall(other)
    assert r.returncode != 0
    assert (other / "keep.txt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows の PowerShell で動かす")
def test_uninstall_with_nothing_installed_is_quiet(tmp_path):
    r = _run_uninstall(tmp_path / "none")
    assert r.returncode == 0
