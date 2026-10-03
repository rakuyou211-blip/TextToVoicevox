# -*- coding: utf-8 -*-
"""Windows の起動口（winlaunch.py）の動きのテスト。

本物の venv や Python は作らず、置き場所だけを再現して確かめる（どの OS でも動く）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import winlaunch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("name", ["起動.pyw", "デバッグ起動.pyw", "英語OCRを入れる.pyw",
                                  "winlaunch.py", "winsetup.py"])
def test_windows_entry_files_compile(name):
    """.pyw は pythonw で動くので、構文エラーでも画面に何も出ずに終わる
    （v1.24.0 の作りかけで、文字列の中に改行が紛れ込んで実際に起きた）。"""
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        compile(f.read(), name, "exec")


def _app(tmp_path, requirements="pillow\n"):
    """main.py などが置かれた、展開直後のアプリのフォルダを作る。"""
    for name in ("main.py", "core.py", "winsetup.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(requirements, encoding="utf-8")
    return tmp_path


def _venv(app, base_python_exists=True, stamp=True):
    """セットアップ済みの venv の形を作る。home は venv を作った元の Python。"""
    scripts = app / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")
    (scripts / "pythonw.exe").write_bytes(b"")
    home = app / "base_python"
    home.mkdir()
    if base_python_exists:
        (home / "python.exe").write_bytes(b"")
    (app / "venv" / "pyvenv.cfg").write_text(
        "home = %s\ninclude-system-site-packages = false\n" % home, encoding="utf-8")
    if stamp:
        (app / "venv" / winlaunch.STAMP_NAME).write_text(
            winlaunch.wanted_stamp(str(app)), encoding="utf-8")


def test_setup_not_done_without_venv(tmp_path):
    app = _app(tmp_path)
    assert not winlaunch.venv_ok(str(app))
    assert not winlaunch.setup_done(str(app))


def test_setup_done_with_matching_stamp(tmp_path):
    app = _app(tmp_path)
    _venv(app)
    assert winlaunch.venv_ok(str(app))
    assert winlaunch.setup_done(str(app))


def test_moved_venv_is_not_ok(tmp_path):
    """別のPCから持ってきた venv（元の Python が無い）は使えない扱いにする。"""
    app = _app(tmp_path)
    _venv(app, base_python_exists=False)
    assert not winlaunch.venv_ok(str(app))
    assert not winlaunch.setup_done(str(app))


def test_new_requirements_trigger_setup(tmp_path):
    """新しい版で requirements.txt が変わったら、部品を入れ直す。"""
    app = _app(tmp_path)
    _venv(app)
    (app / "requirements.txt").write_text("pillow\nrequests\n", encoding="utf-8")
    assert winlaunch.venv_ok(str(app))
    assert not winlaunch.setup_done(str(app))


def test_interrupted_setup_is_redone(tmp_path):
    """途中で窓を閉じた（済んだ印が無い）ときは、次の起動でやり直す。"""
    app = _app(tmp_path)
    _venv(app, stamp=False)
    assert not winlaunch.setup_done(str(app))


def test_remove_legacy_only_removes_old_bats(tmp_path):
    app = _app(tmp_path)
    for name in winlaunch.LEGACY_BATS + ("自分のメモ.bat", "起動.pyw"):
        (app / name).write_text("", encoding="utf-8")
    winlaunch.remove_legacy(str(app))
    left = sorted(os.listdir(app))
    for name in winlaunch.LEGACY_BATS:
        assert name not in left
    assert "自分のメモ.bat" in left and "起動.pyw" in left


def _record_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(winlaunch, "unblock", lambda here: calls.append("unblock"))
    monkeypatch.setattr(winlaunch, "remove_legacy", lambda here: calls.append("legacy"))
    monkeypatch.setattr(winlaunch, "start_app", lambda here: calls.append("app"))
    monkeypatch.setattr(winlaunch, "open_console",
                        lambda here, mode: calls.append("console:" + mode))
    monkeypatch.setattr(winlaunch, "tell", lambda here, msg: calls.append("tell"))
    return calls


def test_run_starts_app_directly_after_setup(tmp_path, monkeypatch):
    """2回目からは黒い窓を出さずに、アプリを直接開く。"""
    app = _app(tmp_path)
    _venv(app)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), "launch") == 0
    assert calls == ["unblock", "legacy", "app"]


def test_run_opens_console_for_first_setup(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), "launch") == 0
    assert calls == ["unblock", "legacy", "console:launch"]


@pytest.mark.parametrize("mode", ["debug", "ocr"])
def test_run_other_modes_always_use_console(tmp_path, monkeypatch, mode):
    app = _app(tmp_path)
    _venv(app)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), mode) == 0
    assert calls == ["unblock", "legacy", "console:" + mode]


def test_run_refuses_outside_extracted_folder(tmp_path, monkeypatch):
    """zip の中から開いた（隣に main.py が無い）ときは、何も動かさずに知らせる。"""
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(tmp_path), "launch") == 1
    assert calls == ["tell"]


def test_ocr_accepts_venv_without_stamp(tmp_path, monkeypatch):
    """1行導入（install.ps1）の古い版で入れた venv には済んだ印が無いことがある。
    英語OCRは venv が動けば入れられること（印まで求めると、アプリのボタンが毎回断る）。"""
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _venv(app, stamp=False)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "wait_close", lambda: None)
    ran = []
    monkeypatch.setattr(winsetup.subprocess, "call", lambda args, **kw: ran.append(args) or 0)
    assert winsetup.do_ocr() == 0
    assert any("requirements-english-ocr.txt" in " ".join(a) for a in ran)


def test_setup_rebuilds_venv_without_pip(tmp_path, monkeypatch):
    """venv を作る途中で窓を閉じると、pip の無い venv が残る。見た目が揃っていても、
    pip が動かなければ作り直すこと（作り直さないと、何度開いても失敗し続ける）。"""
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _venv(app, stamp=False)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "python_usable", lambda: True)
    ran = []

    def fake_call(args, **kw):
        ran.append(args)
        return 1 if args[1:4] == ["-m", "pip", "--version"] else 0
    monkeypatch.setattr(winsetup.subprocess, "call", fake_call)
    assert winsetup.setup() is True
    assert any("--clear" in a for a in ran), "pip の無い venv を作り直していません"


@pytest.mark.skipif(os.name != "nt", reason="Zone.Identifier は Windows（NTFS）だけ")
def test_unblock_removes_download_mark(tmp_path):
    app = _app(tmp_path)
    (app / "assets").mkdir()
    marked = [app / "main.py", app / "assets" / "x.png"]
    marked[1].write_bytes(b"")
    for f in marked:
        with open(str(f) + ":Zone.Identifier", "w") as ads:
            ads.write("[ZoneTransfer]\nZoneId=3\n")
    winlaunch.unblock(str(app))
    for f in marked:
        assert not os.path.exists(str(f) + ":Zone.Identifier")
