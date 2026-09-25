# -*- coding: utf-8 -*-
"""ドキュメント整合テスト。

APP_VERSION を上げたのに README / CHANGELOG を更新し忘れたままコミットする事故
（v1.11.0表記が4リリース分取り残された実績あり）を、コードと同じテストゲートで塞ぐ。
stdlibのみでCIでもそのまま動く。
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_changelog_top_matches_app_version():
    m = re.search(r"^## v(\d+\.\d+\.\d+)", _read("CHANGELOG.md"), re.MULTILINE)
    assert m, "CHANGELOG.md に ## vX.Y.Z 見出しがありません"
    assert m.group(1) == core.APP_VERSION, (
        f"CHANGELOG先頭は v{m.group(1)} だが APP_VERSION は {core.APP_VERSION}")


def test_readme_mentions_current_version():
    for name in ("README.md", "README.en.md"):
        assert f"v{core.APP_VERSION}" in _read(name), (
            f"{name} に v{core.APP_VERSION} の記載がありません（版数の更新漏れ）")


def test_no_stale_zip_names():
    pat = re.compile(r"TextToVoicevox_v([\d.]+)\.zip")
    for name in ("README.md", "README.en.md"):
        for m in pat.finditer(_read(name)):
            assert m.group(1) == core.APP_VERSION, (
                f"{name} の zip 名が古い版です: {m.group(0)}")


def test_first_readme_txt_covers_both_os():
    """はじめにお読みください.txt に Win/Mac 双方の「開けないとき」導線がある。"""
    txt = _read("はじめにお読みください.txt")
    for needle in ("起動.bat", "詳細情報", "ブロックの解除",       # Windows
                   "起動.command", "このまま開く", "setup_mac.sh",  # macOS
                   "Unblock-File", "bash"):                         # コピペ1行
        assert needle in txt, f"はじめにお読みください.txt に「{needle}」の案内がありません"


def test_windows_scripts_self_unblock():
    """.bat が Mark of the Web を自己解除する（setup は再帰・起動系は直下のみ）。"""
    for name in ("setup.bat", "起動.bat", "デバッグ起動.bat"):
        body = _read(name)
        assert "Unblock-File" in body, f"{name} に Unblock-File の自己解除がありません"
        assert "-ErrorAction SilentlyContinue" in body, \
            f"{name} の Unblock-File が防御的（失敗しても続行）になっていません"
    assert "-Recurse" in _read("setup.bat"), "setup.bat の解除が -Recurse ではありません"


def test_mac_scripts_self_repair():
    """.command / .sh が検疫フラグと実行権限を自己修復する。"""
    for name in ("setup.command", "起動.command", "デバッグ起動.command", "setup_mac.sh"):
        body = _read(name)
        assert "com.apple.quarantine" in body, f"{name} に検疫フラグの自己修復がありません"
        assert "chmod +x" in body, f"{name} に実行権限の自己修復がありません"


def test_setup_mac_sh_is_gatekeeper_free_entry():
    """setup_mac.sh は bash 用スクリプトで、修復後に 起動.command へ続く。"""
    body = _read("setup_mac.sh")
    assert body.startswith("#!/bin/bash"), "setup_mac.sh の先頭が #!/bin/bash ではありません"
    assert "\ufeff" not in body, "setup_mac.sh に BOM が混入しています（shは BOM 不可）"
    assert re.search(r"bash\s+\./起動\.command", body), \
        "setup_mac.sh が 起動.command へ続いていません"


def test_readmes_mention_rescue_paths():
    """README（日英）が新しい救済導線（setup_mac.sh・案内テキスト）に触れている。"""
    for name in ("README.md", "README.en.md"):
        body = _read(name)
        assert "setup_mac.sh" in body, f"{name} に setup_mac.sh の案内がありません"
        assert "はじめにお読みください" in body, \
            f"{name} に はじめにお読みください.txt への言及がありません"


def test_zip_ships_expected_files():
    """配布zip（git archive HEAD 由来）に入る顔ぶれをインデックスで検証。
    PNG は3枚だけ（app-icon＋スクショ2枚）。公式素材の立ち絵PNGが紛れたら即失敗。"""
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                             capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git が使えない環境")
    if out.returncode != 0:
        pytest.skip("git リポジトリではない（zip展開などで実行された）")
    files = [f for f in out.stdout.decode("utf-8").split("\0") if f]
    # OCRベンチの画像は tools/ocr_bench/make_fixtures.py で自前生成したもの
    # （立ち絵ではない）。tests/ は配布zipにも入らないので、ここだけ別枠で認める
    bench_dir = "tests/fixtures/screen_ocr/"
    pngs = sorted(f for f in files if f.lower().endswith(".png")
                  and not f.startswith(bench_dir))
    assert pngs == ["assets/app-icon.png",
                    "docs/screenshot-dark.png",
                    "docs/screenshot-light.png"], \
        f"追跡中の PNG が想定と違います（立ち絵の混入?）: {pngs}"
    for required in ("はじめにお読みください.txt", "setup_mac.sh"):
        assert required in files, f"{required} が git 管理に入っていません（git add 忘れ）"


def test_zip_scripts_keep_exec_bit():
    """配布zipの実行ビットの源泉 = git index のモードが 100755 であること。
    git archive は index のモードを zip にそのまま書くため、ここが 100644 に
    退行すると Mac で「開けません／アクセス権がありません」が再発する。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-s", "-z", "--", "setup_mac.sh", "*.command"],
            cwd=ROOT, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git が使えない環境")
    if out.returncode != 0:
        pytest.skip("git リポジトリではない（zip展開などで実行された）")
    modes = {}
    for entry in out.stdout.decode("utf-8").split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        modes[path] = meta.split(" ", 1)[0]
    expected = {"setup_mac.sh", "setup.command", "起動.command", "デバッグ起動.command"}
    assert set(modes) == expected, f"実行スクリプトの顔ぶれが想定と違います: {sorted(modes)}"
    bad = {p: m for p, m in modes.items() if m != "100755"}
    assert not bad, f"実行ビットが退行しています（git add し直しで消えがち）: {bad}"


def test_first_readme_txt_bom_and_crlf_attr():
    """はじめにお読みください.txt の BOM と eol=crlf 属性が生きていること。
    どちらも Windows の古いメモ帳で読めるための本質（BOM無し→文字化け、
    LF→1行につぶれる）。ファイル作り直しや .gitattributes 編集で消えやすい。"""
    with open(os.path.join(ROOT, "はじめにお読みください.txt"), "rb") as f:
        head = f.read(3)
    assert head == b"\xef\xbb\xbf", \
        "はじめにお読みください.txt の先頭に UTF-8 BOM がありません"
    attrs = _read(".gitattributes")
    assert re.search(r"^はじめにお読みください\.txt\s+text\s+eol=crlf\s*$",
                     attrs, re.MULTILINE), \
        ".gitattributes に はじめにお読みください.txt の eol=crlf 指定がありません"


def test_python_version_requirement_consistent():
    """最低Pythonバージョンの記載が README(日英)・setup.bat で一致している。"""
    versions = {}
    for name, pat in (("README.md", r"Python\s*([\d.]+)\s*以降"),
                      ("README.en.md",
                       r"Python\s*([\d.]+)\s*or\s*(?:later|newer)"),
                      ("setup.bat", r"(3\.\d+)\s*以降")):
        m = re.search(pat, _read(name))
        assert m, f"{name} に最低Pythonバージョンの記載が見つかりません"
        versions[name] = m.group(1)
    assert len(set(versions.values())) == 1, \
        f"最低Pythonバージョンの記載が不一致: {versions}"


INSTALL_ONE_LINER = ("irm https://raw.githubusercontent.com/rakuyou211-blip/"
                     "TextToVoicevox/main/install.ps1 | iex")


def test_install_one_liner_is_documented():
    """Windowsの1行導入が、READMEと同梱の案内の両方に正しく載っている。"""
    for name in ("README.md", "README.en.md", "はじめにお読みください.txt"):
        assert INSTALL_ONE_LINER in _read(name), f"{name} に1行導入の案内がありません"
    assert INSTALL_ONE_LINER in _read("install.ps1")


MAC_INSTALL_ONE_LINER = ("curl -fsSL https://raw.githubusercontent.com/rakuyou211-blip/"
                         "TextToVoicevox/main/install.sh | bash")


def test_mac_install_one_liner_is_documented():
    """Macの1行導入が、READMEと同梱の案内の両方に正しく載っている。"""
    for name in ("README.md", "README.en.md", "はじめにお読みください.txt"):
        assert MAC_INSTALL_ONE_LINER in _read(name), f"{name} にMacの1行導入の案内がありません"
    assert MAC_INSTALL_ONE_LINER in _read("install.sh")


def test_install_sh_runs_only_after_full_download():
    """curl | bash は届いた所から順に実行する。途中で切れても書きかけの行が走らないよう、
    全体を関数に包み、最後の行でだけ呼ぶ。改行は LF（CRLF だと bash が壊れる）。"""
    with open(os.path.join(ROOT, "install.sh"), "rb") as f:
        raw = f.read()
    assert b"\r\n" not in raw
    lines = [l for l in raw.decode("utf-8").splitlines() if l.strip()]
    assert lines[0] == "#!/bin/bash"
    assert lines[-1] == 't2v_install "$@"'
    body = [l for l in lines if not l.startswith("#")]
    assert body[0].startswith("t2v_install()")


def test_install_ps1_has_no_bom():
    """install.ps1 は irm | iex で読まれる。先頭のBOMは文字列に混ざって
    最初の行を壊しうるので付けない（ocr_win.ps1 はファイル実行なのでBOM付きで正しい）。"""
    with open(os.path.join(ROOT, "install.ps1"), "rb") as f:
        assert not f.read(3).startswith(b"\xef\xbb\xbf")


def test_release_zips_are_split_by_os():
    """配布 zip は Windows 用と Mac 用に分かれ、相手の OS のファイルが混ざらない。"""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        import make_release
    finally:
        sys.path.pop(0)
    try:
        tracked = make_release.tracked_files(ROOT)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git が使えない環境")
    win = make_release.files_for("windows", tracked)
    mac = make_release.files_for("mac", tracked)
    for files in (win, mac):
        for need in ("main.py", "core.py", "requirements.txt", "はじめにお読みください.txt"):
            assert need in files
        assert not any(f.startswith(("tests/", "tools/", ".github/")) for f in files)
        assert "install.ps1" not in files and "install.sh" not in files
    assert "起動.bat" in win and "ocr_win.ps1" in win
    assert not any(f.endswith((".command", ".sh")) for f in win) and "ocr_mac.py" not in win
    assert "起動.command" in mac and "ocr_mac.py" in mac and "setup_mac.sh" in mac
    assert not any(f.endswith((".bat", ".ps1")) for f in mac)


def test_download_links_match_release_zip_names():
    """README のダウンロードのリンクが、Release に添付する zip の名前と一致する。"""
    for name in ("README.md", "README.en.md"):
        body = _read(name)
        for z in ("TextToVoicevox_Windows.zip", "TextToVoicevox_Mac.zip"):
            assert f"releases/latest/download/{z}" in body, f"{name} に {z} のリンクがありません"
    body = _read(os.path.join(".github", "workflows", "release.yml"))
    assert "TextToVoicevox_Windows.zip" in body and "TextToVoicevox_Mac.zip" in body


def test_shell_vars_are_braced_before_multibyte():
    """Mac 標準の bash 3.2 は、日本語（UTF-8）のターミナルだと "$vv（" の全角文字の
    1バイト目まで変数名に数えてしまい、set -u で「vv?: unbound variable」と落ちる
    （v1.20.0 の1行導入が、完了表示の直後に止まった実績あり）。直後が日本語なら ${vv} と括る。"""
    names = subprocess.run(["git", "ls-files", "-z", "--", "*.sh", "*.command"],
                           cwd=ROOT, capture_output=True, check=True).stdout
    bad = []
    for name in filter(None, names.decode("utf-8").split("\0")):
        for no, line in enumerate(_read(name).splitlines(), 1):
            if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", line):
                bad.append(f"{name}:{no}: {line.strip()}")
    assert not bad, "変数の直後に日本語があります（${名前} と括る）:\n" + "\n".join(bad)
