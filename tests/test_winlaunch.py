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


@pytest.mark.parametrize("name", ["起動.pyw", "デバッグ起動.pyw", "winlaunch.py", "winsetup.py"])
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


def _venv(app, base_python_exists=True, stamp=True, engine=True):
    """セットアップ済みの venv の形を作る。home は venv を作った元の Python。
    engine: 画像の文字を読む部品（ocr_engine）も入れ終えた形にする。"""
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
    if engine:
        (app / winlaunch.ENGINE_DIR).mkdir()
        (app / winlaunch.ENGINE_DIR / "ok.txt").write_text("x", encoding="utf-8")


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


def test_remove_legacy_only_removes_old_files(tmp_path):
    app = _app(tmp_path)
    for name in winlaunch.LEGACY_FILES + ("自分のメモ.bat", "起動.pyw"):
        (app / name).write_text("", encoding="utf-8")
    winlaunch.remove_legacy(str(app))
    left = sorted(os.listdir(app))
    for name in winlaunch.LEGACY_FILES:
        assert name not in left
    assert "自分のメモ.bat" in left and "起動.pyw" in left


def test_unblock_skips_the_engine_folder(tmp_path, monkeypatch):
    """強い文字読み取りの部品は数が多く、自分の機械で入れた物なので印も付かない。"""
    (tmp_path / winlaunch.ENGINE_DIR / "deps").mkdir(parents=True)
    (tmp_path / winlaunch.ENGINE_DIR / "deps" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    removed = []
    monkeypatch.setattr(winlaunch.os, "remove", lambda p: removed.append(p))
    winlaunch.unblock(str(tmp_path))
    assert any(p.endswith("main.py:Zone.Identifier") for p in removed)
    assert not any(winlaunch.ENGINE_DIR in p for p in removed)


def _record_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(winlaunch, "unblock", lambda here: calls.append("unblock"))
    monkeypatch.setattr(winlaunch, "remove_legacy", lambda here: calls.append("legacy"))
    monkeypatch.setattr(winlaunch, "ensure_shortcut", lambda top, here=None: calls.append("shortcut"))
    monkeypatch.setattr(winlaunch, "move_into_app", lambda top, here: True)
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
    assert calls == ["unblock", "legacy", "shortcut", "app"]


def test_run_opens_console_for_first_setup(tmp_path, monkeypatch):
    app = _app(tmp_path)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), "launch") == 0
    assert calls == ["unblock", "legacy", "shortcut", "console:launch"]


@pytest.mark.parametrize("mode", ["debug", "ocr"])
def test_run_other_modes_always_use_console(tmp_path, monkeypatch, mode):
    app = _app(tmp_path)
    _venv(app)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), mode) == 0
    assert calls == ["unblock", "legacy", "shortcut", "console:" + mode]


def test_run_refuses_outside_extracted_folder(tmp_path, monkeypatch):
    """zip の中から開いた（隣に main.py が無い）ときは、何も動かさずに知らせる。"""
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(tmp_path), "launch") == 1
    assert calls == ["tell"]


def test_engine_counts_as_part_of_setup(tmp_path):
    """部品が無ければ、セットアップはまだ済んでいない（前の版から更新した人にも入るように）。"""
    app = _app(tmp_path)
    _venv(app, engine=False)
    assert winlaunch.venv_ok(str(app))
    assert not winlaunch.setup_done(str(app))
    (app / winlaunch.ENGINE_DIR).mkdir()
    assert not winlaunch.setup_done(str(app)), "作りかけ（印の無いフォルダ）は、入っていない"
    (app / winlaunch.ENGINE_DIR / "ok.txt").write_text("x", encoding="utf-8")
    assert winlaunch.setup_done(str(app))


def test_failed_engine_is_not_retried_at_every_start(tmp_path):
    """入らなかったPCで、起動のたびに黒い窓を出さない。"""
    app = _app(tmp_path)
    _venv(app, engine=False)
    (app / "venv" / winlaunch.ENGINE_TRIED_NAME).write_text("tried", encoding="utf-8")
    assert winlaunch.setup_done(str(app))


def test_update_from_older_version_goes_through_setup(tmp_path, monkeypatch):
    """前の版の venv に部品が無いときは、次の起動で黒い窓のセットアップを通す。"""
    app = _app(tmp_path)
    _venv(app, engine=False)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), "launch") == 0
    assert calls == ["unblock", "legacy", "shortcut", "console:launch"]


def _setup_app(tmp_path, monkeypatch, engine):
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _venv(app, stamp=False, engine=engine)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "python_usable", lambda: True)
    monkeypatch.setattr(winsetup, "pip_works", lambda py: True)
    monkeypatch.setattr(winsetup, "shrink_venv", lambda venv: True)
    monkeypatch.setattr(winsetup, "engine_supported", lambda: True)
    monkeypatch.setattr(winsetup.subprocess, "call", lambda args, **kw: 0)
    return winsetup, app


def test_first_setup_installs_the_engine_without_asking(tmp_path, monkeypatch):
    """押すものを増やさない。セットアップの中で入れて、そのあとで済んだ印を置く
    （途中で窓を閉じたら、次の起動でやり直すように）。"""
    winsetup, app = _setup_app(tmp_path, monkeypatch, engine=False)
    monkeypatch.setattr(winsetup, "wait_close", lambda: pytest.fail("質問した"))
    seen = []

    def install(py):
        seen.append((app / "venv" / winlaunch.STAMP_NAME).exists())
        (app / winlaunch.ENGINE_DIR).mkdir()
        (app / winlaunch.ENGINE_DIR / "ok.txt").write_text("x", encoding="utf-8")
        return True
    monkeypatch.setattr(winsetup, "install_engine", install)
    assert winsetup.setup() is True
    assert seen == [False]
    assert winlaunch.setup_done(str(app))


def test_setup_does_not_download_the_engine_twice(tmp_path, monkeypatch):
    winsetup, app = _setup_app(tmp_path, monkeypatch, engine=True)
    monkeypatch.setattr(winsetup, "install_engine", lambda py: pytest.fail("入れ直した"))
    assert winsetup.setup() is True


def test_setup_goes_on_when_the_engine_fails(tmp_path, monkeypatch, capsys):
    """入らなくてもアプリは開く（Windows の読み取りで読む）。次の起動では試し直さない。"""
    winsetup, app = _setup_app(tmp_path, monkeypatch, engine=False)
    monkeypatch.setattr(winsetup, "install_engine", lambda py: False)
    assert winsetup.setup() is True
    assert "Windows の読み取りで読みます" in capsys.readouterr().out
    assert winlaunch.setup_done(str(app))


def test_setup_skips_the_engine_on_an_unsupported_machine(tmp_path, monkeypatch):
    winsetup, app = _setup_app(tmp_path, monkeypatch, engine=False)
    monkeypatch.setattr(winsetup, "engine_supported", lambda: False)
    monkeypatch.setattr(winsetup, "install_engine", lambda py: pytest.fail("入れようとした"))
    assert winsetup.setup() is True
    assert winlaunch.setup_done(str(app))


def test_engine_mode_never_asks_and_never_fails(tmp_path, monkeypatch):
    """1行導入から呼ぶ入口。窓を閉じる操作を待たず、入らなくても導入全体を止めない。"""
    winsetup, app = _setup_app(tmp_path, monkeypatch, engine=False)
    monkeypatch.setattr(winsetup, "wait_close", lambda: pytest.fail("待った"))
    monkeypatch.setattr(winsetup, "install_engine", lambda py: False)
    assert winsetup.main(["winsetup.py", "engine"]) == 0


def test_ocr_accepts_venv_without_stamp(tmp_path, monkeypatch):
    """1行導入（install.ps1）の古い版で入れた venv には済んだ印が無いことがある。
    文字の読み取りは venv が動けば入れられること（印まで求めると、アプリのボタンが毎回断る）。"""
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _venv(app, stamp=False)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "wait_close", lambda: None)
    monkeypatch.setattr(winsetup.time, "sleep", lambda sec: None)
    monkeypatch.setattr(winsetup, "engine_supported", lambda: True)
    got = []
    monkeypatch.setattr(winsetup, "install_engine", lambda py: got.append(py) or True)
    assert winsetup.do_ocr() == 0
    assert got == [winlaunch.venv_exes(str(app))[0]]


def test_ocr_stops_on_an_unsupported_machine(tmp_path, monkeypatch):
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _venv(app)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "wait_close", lambda: None)
    monkeypatch.setattr(winsetup, "engine_supported", lambda: False)
    monkeypatch.setattr(winsetup, "install_engine", lambda py: pytest.fail("入れようとした"))
    assert winsetup.do_ocr() == 1


def _fake_build(winsetup, ok=True):
    def build(py, engine_dir):
        os.makedirs(engine_dir)
        with open(os.path.join(engine_dir, "ok.txt"), "w", encoding="utf-8") as f:
            f.write("new")
        return ok
    return build


def test_install_engine_puts_it_in_place(tmp_path, monkeypatch):
    sys.path.insert(0, ROOT)
    import winsetup
    monkeypatch.setattr(winsetup, "HERE", str(tmp_path))
    monkeypatch.setattr(winsetup, "build_engine", _fake_build(winsetup))
    assert winsetup.install_engine("py") is True
    assert (tmp_path / "ocr_engine" / "ok.txt").read_text(encoding="utf-8") == "new"
    assert sorted(os.listdir(tmp_path)) == ["ocr_engine"], "作業用のフォルダが残っています"


def test_install_engine_replaces_the_old_one(tmp_path, monkeypatch):
    sys.path.insert(0, ROOT)
    import winsetup
    (tmp_path / "ocr_engine").mkdir()
    (tmp_path / "ocr_engine" / "ok.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(winsetup, "HERE", str(tmp_path))
    monkeypatch.setattr(winsetup, "build_engine", _fake_build(winsetup))
    assert winsetup.install_engine("py") is True
    assert (tmp_path / "ocr_engine" / "ok.txt").read_text(encoding="utf-8") == "new"
    assert sorted(os.listdir(tmp_path)) == ["ocr_engine"]


def test_failed_install_keeps_what_was_there(tmp_path, monkeypatch):
    """途中で失敗しても、今ある読み取りは壊さない。作りかけも残さない。"""
    sys.path.insert(0, ROOT)
    import winsetup
    (tmp_path / "ocr_engine").mkdir()
    (tmp_path / "ocr_engine" / "ok.txt").write_text("old", encoding="utf-8")
    monkeypatch.setattr(winsetup, "HERE", str(tmp_path))
    monkeypatch.setattr(winsetup, "build_engine", _fake_build(winsetup, ok=False))
    assert winsetup.install_engine("py") is False
    assert (tmp_path / "ocr_engine" / "ok.txt").read_text(encoding="utf-8") == "old"
    assert sorted(os.listdir(tmp_path)) == ["ocr_engine"]


def test_replacing_while_in_use_explains_what_to_do(tmp_path, monkeypatch, capsys):
    """動いている裏方がいると置き換えられない。閉じてもらう。"""
    sys.path.insert(0, ROOT)
    import winsetup
    (tmp_path / "ocr_engine").mkdir()
    monkeypatch.setattr(winsetup, "HERE", str(tmp_path))
    monkeypatch.setattr(winsetup, "build_engine", _fake_build(winsetup))

    def busy(src, dst):
        raise PermissionError("使用中です")
    monkeypatch.setattr(winsetup.os, "replace", busy)
    assert winsetup.install_engine("py") is False
    assert "アプリを閉じて" in capsys.readouterr().out
    assert sorted(os.listdir(tmp_path)) == ["ocr_engine"]


def test_download_checks_the_contents(tmp_path):
    """落とした物がハッシュと違えば使わない。"""
    import hashlib
    import pathlib
    sys.path.insert(0, ROOT)
    import winsetup
    src = tmp_path / "file.bin"
    src.write_bytes(b"abc" * 1000)
    url = pathlib.Path(src).as_uri()
    right = hashlib.sha256(src.read_bytes()).hexdigest()
    assert winsetup.download(url, str(tmp_path / "ok.bin"), right) is True
    assert (tmp_path / "ok.bin").read_bytes() == src.read_bytes()
    assert winsetup.download(url, str(tmp_path / "bad.bin"), "0" * 64) is False
    assert winsetup.download(pathlib.Path(tmp_path / "none").as_uri(), str(tmp_path / "x"), right) is False


def test_trim_engine_removes_only_what_is_not_needed(tmp_path):
    sys.path.insert(0, ROOT)
    import winsetup
    deps = tmp_path / "deps"
    for rel in ("cv2/opencv_videoio_ffmpeg500_64.dll", "cv2/cv2.pyd", "onnxruntime/tools/a.py",
                "onnxruntime/capi/onnxruntime.dll", "numpy/tests/a.py", "numpy/lib/tests/a.py",
                "numpy/lib/keep.py", "rapidocr/models/PP-OCRv6_rec_small.onnx"):
        (deps / rel).parent.mkdir(parents=True, exist_ok=True)
        (deps / rel).write_bytes(b"x")
    winsetup.trim_engine(str(deps))
    left = sorted(p.relative_to(deps).as_posix() for p in deps.rglob("*") if p.is_file())
    assert left == ["cv2/cv2.pyd", "numpy/lib/keep.py", "onnxruntime/capi/onnxruntime.dll",
                    "rapidocr/models/PP-OCRv6_rec_small.onnx"]


def _embed_zip(path):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("python.exe", "")
        z.writestr("python314._pth", "python314.zip\n.\n")


def test_build_engine_goes_through_every_step(tmp_path, monkeypatch):
    sys.path.insert(0, ROOT)
    import shutil
    import winsetup
    monkeypatch.setattr(winsetup, "HERE", str(tmp_path))
    src = tmp_path / "embed.zip"
    _embed_zip(src)
    monkeypatch.setattr(winsetup, "download", lambda url, dest, sha: shutil.copy(src, dest) and True)
    pip = []

    def fake_call(args, **kw):
        pip.append(args)
        return 0
    monkeypatch.setattr(winsetup.subprocess, "call", fake_call)
    monkeypatch.setattr(winsetup, "engine_reads", lambda d: True)
    monkeypatch.setattr(winsetup, "compact_folder", lambda d: False)
    engine = tmp_path / "ocr_engine.new"
    assert winsetup.build_engine("venv-python", str(engine)) is True
    # この Python が見るのは部品のフォルダだけ
    assert (engine / "python" / "python314._pth").read_text(encoding="utf-8") == "python314.zip\n.\n..\\deps\n"
    assert (engine / "ok.txt").exists()
    args = pip[0]
    assert args[0] == "venv-python"
    assert "--require-hashes" in args and "--no-deps" in args and "--only-binary=:all:" in args
    assert args[args.index("--target") + 1] == str(engine / "deps")
    assert args[-1].endswith("requirements-ocr-engine.txt")


@pytest.mark.parametrize("step", ["download", "pip", "read"])
def test_build_engine_leaves_no_mark_on_failure(tmp_path, monkeypatch, step):
    """印（ok.txt）は、最後に読めたときだけ置く。"""
    sys.path.insert(0, ROOT)
    import shutil
    import winsetup
    monkeypatch.setattr(winsetup, "HERE", str(tmp_path))
    src = tmp_path / "embed.zip"
    _embed_zip(src)
    monkeypatch.setattr(winsetup, "download",
                        lambda url, dest, sha: step != "download" and shutil.copy(src, dest) and True)
    monkeypatch.setattr(winsetup.subprocess, "call", lambda args, **kw: 1 if step == "pip" else 0)
    monkeypatch.setattr(winsetup, "engine_reads", lambda d: step != "read")
    monkeypatch.setattr(winsetup, "compact_folder", lambda d: False)
    engine = tmp_path / "ocr_engine.new"
    assert winsetup.build_engine("py", str(engine)) is False
    assert not (engine / "ok.txt").exists()


def test_engine_reads_points_to_the_missing_windows_part(tmp_path, monkeypatch, capsys):
    """部品の読み込みに失敗したら、足りないかもしれない Windows の部品を案内する。"""
    import types
    sys.path.insert(0, ROOT)
    import winsetup
    monkeypatch.setattr(winsetup.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=1, stdout="", stderr="ImportError: DLL load failed while importing cv2"))
    assert winsetup.engine_reads(str(tmp_path)) is False
    assert winsetup.VC_REDIST_URL in capsys.readouterr().out
    monkeypatch.setattr(winsetup.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        returncode=0, stdout='{"ok": true}', stderr=""))
    assert winsetup.engine_reads(str(tmp_path)) is True


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


def test_setup_shrinks_dictionary_and_never_fails_on_it(tmp_path, monkeypatch):
    """セットアップの最後に、ふりがなの辞書を Windows の圧縮機能で小さくする。
    小さくできなくても（圧縮できない場所など）、セットアップは成功のまま。"""
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _venv(app, stamp=False)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "python_usable", lambda: True)
    monkeypatch.setattr(winsetup, "pip_works", lambda py: True)
    monkeypatch.setattr(winsetup.subprocess, "call", lambda args, **kw: 0)
    called = []
    monkeypatch.setattr(winsetup, "shrink_venv",
                        lambda venv: called.append(venv) or False)
    assert winsetup.setup() is True
    assert called == [os.path.join(str(app), "venv")]


@pytest.mark.skipif(os.name != "nt", reason="Windows の圧縮機能（compact /exe）は Windows だけ")
def test_shrink_venv_keeps_contents(tmp_path):
    """venv の部品（辞書もほかの部品も）を圧縮しても、中身は1バイトも変わらない
    （ディスク上だけ小さくなる）。venv が無ければ何もしない。"""
    sys.path.insert(0, ROOT)
    import winsetup
    parts = tmp_path / "venv" / "Lib" / "site-packages"
    (parts / "janome").mkdir(parents=True)
    (parts / "PIL").mkdir(parents=True)
    data = ("ENTRIES = " + repr(bytes(range(256)) * 400) + "\n").encode()
    (parts / "janome" / "entries.py").write_bytes(data)
    (parts / "PIL" / "_imaging.pyd").write_bytes(data)
    assert winsetup.shrink_venv(str(tmp_path / "venv")) is True
    assert (parts / "janome" / "entries.py").read_bytes() == data
    assert (parts / "PIL" / "_imaging.pyd").read_bytes() == data
    assert winsetup.shrink_venv(str(tmp_path / "nothing")) is False


# ---- 1行導入で入れた「Python 入りの版」（tools/make_bundle.py が作る。venv は無い） ----

def _bundle(app, engine=True):
    """Python 入りの版の形を作る（runtime の中に Python。部品も入れ終えてある）。"""
    rt = app / winlaunch.RUNTIME_DIR
    rt.mkdir()
    (rt / "python.exe").write_bytes(b"")
    (rt / "pythonw.exe").write_bytes(b"")
    if engine:
        (app / winlaunch.ENGINE_DIR).mkdir()
        (app / winlaunch.ENGINE_DIR / "ok.txt").write_text("x", encoding="utf-8")


def test_bundle_needs_no_setup(tmp_path):
    """部品ごと入っているので、venv が無くても黒い窓を出さずに開く。"""
    app = _app(tmp_path)
    _bundle(app)
    assert not winlaunch.venv_ok(str(app))
    assert winlaunch.bundled(str(app))
    assert winlaunch.setup_done(str(app))
    assert winlaunch.app_exes(str(app)) == winlaunch.runtime_exes(str(app))


def test_run_opens_the_bundle_directly(tmp_path, monkeypatch):
    app = _app(tmp_path)
    _bundle(app)
    calls = _record_calls(monkeypatch)
    assert winlaunch.run(str(app), "launch") == 0
    assert calls == ["unblock", "legacy", "shortcut", "app"]


def test_bundle_starts_with_its_own_python(tmp_path, monkeypatch):
    app = _app(tmp_path)
    _bundle(app)
    got = []
    monkeypatch.setattr(winlaunch.subprocess, "Popen", lambda args, **kw: got.append(args))
    winlaunch.start_app(str(app))
    assert got == [[os.path.join(str(app), "runtime", "pythonw.exe"), os.path.join(str(app), "main.py")]]


def test_half_copied_runtime_is_not_a_bundle(tmp_path):
    """python.exe と pythonw.exe がそろわなければ Python 入りの版とみなさない（venv の道のまま）。"""
    app = _app(tmp_path)
    (app / winlaunch.RUNTIME_DIR).mkdir()
    (app / winlaunch.RUNTIME_DIR / "pythonw.exe").write_bytes(b"")
    assert not winlaunch.bundled(str(app))
    assert winlaunch.app_exes(str(app)) == winlaunch.venv_exes(str(app))


def test_unblock_skips_the_bundled_python(tmp_path, monkeypatch):
    """Python 入りの版の中身は数が多く、1行導入が入れた物なので印も付かない。"""
    (tmp_path / winlaunch.RUNTIME_DIR / "Lib").mkdir(parents=True)
    (tmp_path / winlaunch.RUNTIME_DIR / "Lib" / "os.py").write_text("", encoding="utf-8")
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    removed = []
    monkeypatch.setattr(winlaunch.os, "remove", lambda p: removed.append(p))
    winlaunch.unblock(str(tmp_path))
    assert any(p.endswith("main.py:Zone.Identifier") for p in removed)
    assert not any(os.sep + winlaunch.RUNTIME_DIR + os.sep in p for p in removed)


def test_engine_tried_mark_sits_next_to_the_python(tmp_path):
    app = _app(tmp_path)
    assert winlaunch.engine_tried_path(str(app)) == os.path.join(str(app), "venv", winlaunch.ENGINE_TRIED_NAME)
    _bundle(app, engine=False)
    assert winlaunch.engine_tried_path(str(app)) == os.path.join(
        str(app), winlaunch.RUNTIME_DIR, winlaunch.ENGINE_TRIED_NAME)


def test_bundle_installs_the_engine_with_its_own_python(tmp_path, monkeypatch):
    """アプリの「入れる」ボタン（winsetup.py ocr）と、塊を作るときの winsetup.py engine は、
    Python 入りの版ではその中の Python で部品を入れる。入らなければ印も runtime に置く。"""
    sys.path.insert(0, ROOT)
    import winsetup
    app = _app(tmp_path)
    _bundle(app, engine=False)
    monkeypatch.setattr(winsetup, "HERE", str(app))
    monkeypatch.setattr(winsetup, "wait_close", lambda: None)
    monkeypatch.setattr(winsetup.time, "sleep", lambda sec: None)
    monkeypatch.setattr(winsetup, "engine_supported", lambda: True)
    got = []
    monkeypatch.setattr(winsetup, "install_engine", lambda py: got.append(py) or True)
    assert winsetup.do_ocr() == 0
    assert winsetup.do_engine() == 0
    rt_py = winlaunch.runtime_exes(str(app))[0]
    assert got == [rt_py, rt_py]
    monkeypatch.setattr(winsetup, "install_engine", lambda py: False)
    assert winsetup.ensure_engine(rt_py) is False
    assert (app / winlaunch.RUNTIME_DIR / winlaunch.ENGINE_TRIED_NAME).exists()
    assert not (app / "venv").exists()


# ---- アプリの絵のショートカット（起動.pyw の絵は .pyw の種類で決まり、1つだけは変えられない） ----

def _launcher_folder(tmp_path, icon=True):
    (tmp_path / winlaunch.LAUNCHER_NAME).write_text("", encoding="utf-8")
    if icon:
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "app-icon.ico").write_bytes(b"ico")
    return tmp_path


def test_shortcut_points_to_the_launcher_with_the_app_icon(tmp_path, monkeypatch):
    """行き先は 起動.pyw（開いたときの動きはダブルクリックと同じ）、絵はアプリの絵。"""
    app = _launcher_folder(tmp_path)
    made = []
    monkeypatch.setattr(winlaunch, "_IS_WIN", True)
    monkeypatch.setattr(winlaunch, "_shell_link", lambda *a: made.append(a))
    assert winlaunch.ensure_shortcut(str(app)) is True
    assert made == [(os.path.join(str(app), "TextToVoicevox.lnk"), os.path.join(str(app), "起動.pyw"),
                     os.path.join(str(app), "assets", "app-icon.ico"), "TextToVoicevox を開く")]


def test_no_shortcut_without_the_icon_or_off_windows(tmp_path, monkeypatch):
    app = _launcher_folder(tmp_path, icon=False)
    monkeypatch.setattr(winlaunch, "_shell_link", lambda *a: pytest.fail("作った"))
    monkeypatch.setattr(winlaunch, "_IS_WIN", True)
    assert winlaunch.ensure_shortcut(str(app)) is False, "アプリの絵が無い"
    (app / "assets").mkdir()
    (app / "assets" / "app-icon.ico").write_bytes(b"ico")
    monkeypatch.setattr(winlaunch, "_IS_WIN", False)
    assert winlaunch.ensure_shortcut(str(app)) is False, "Windows 以外"


def test_shortcut_is_remade_only_after_the_folder_moved(tmp_path, monkeypatch):
    """いつもは中を見るだけ。前の場所の絵を指していたら（フォルダを動かした）作り直す。"""
    app = _launcher_folder(tmp_path)
    made = []
    monkeypatch.setattr(winlaunch, "_IS_WIN", True)
    monkeypatch.setattr(winlaunch, "_shell_link", lambda *a: made.append(a))
    lnk = app / "TextToVoicevox.lnk"
    icon = os.path.join(str(app), "assets", "app-icon.ico")
    lnk.write_bytes(b"\x4c\x00\x00\x00" + icon.encode("utf-16-le") + b"\x00\x00"
                    + r".\起動.pyw".encode("utf-16-le"))
    assert winlaunch.ensure_shortcut(str(app)) is False and made == []
    lnk.write_bytes(b"\x4c\x00\x00\x00" + r"C:\old\assets\app-icon.ico".encode("utf-16-le"))
    assert winlaunch.ensure_shortcut(str(app)) is True and len(made) == 1


def test_shortcut_trouble_never_stops_the_launch(tmp_path, monkeypatch):
    app = _launcher_folder(tmp_path)
    monkeypatch.setattr(winlaunch, "_IS_WIN", True)

    def broken(*a):
        raise OSError("COM が使えない")
    monkeypatch.setattr(winlaunch, "_shell_link", broken)
    assert winlaunch.ensure_shortcut(str(app)) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows のショートカット")
def test_real_shortcut_is_written(tmp_path):
    r"""本物の .lnk を作る。フォルダを動かしても行き先を探せるよう、相対の道（.\起動.pyw）も入る。"""
    app = _launcher_folder(tmp_path)
    assert winlaunch.ensure_shortcut(str(app)) is True
    data = (app / "TextToVoicevox.lnk").read_bytes()
    assert data[:4] == b"\x4c\x00\x00\x00"
    assert os.path.join(str(app), "assets", "app-icon.ico").encode("utf-16-le") in data
    assert r".\起動.pyw".encode("utf-16-le") in data
    assert winlaunch.ensure_shortcut(str(app)) is False, "2回目は作り直さない"


# ---- zip の並び（いちばん上は 起動.pyw と説明だけ。プログラム・部品・設定は app フォルダ） ----

def _files_under(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/")
                  for d, _sub, names in os.walk(folder) for f in names)


def _put(base, files):
    for rel, text in files.items():
        p = base.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_top_is_one_up_only_in_the_zip_layout(tmp_path):
    """zip の並びでは、起動.pyw とショートカットは app の1つ上。1つのフォルダの並びでは同じ所。"""
    top = tmp_path / "TextToVoicevox"
    app = top / "app"
    app.mkdir(parents=True)
    (top / "起動.pyw").write_text("", encoding="utf-8")
    assert winlaunch.top_of(str(app)) == str(top)
    flat = tmp_path / "flat"
    flat.mkdir()
    assert winlaunch.top_of(str(flat)) == str(flat)
    (top / "起動.pyw").unlink()
    (top / "TextToVoicevox.lnk").write_bytes(b"")
    assert winlaunch.top_of(str(app)) == str(top), "起動.pyw をしまったあとはショートカットで見分ける"
    lone = tmp_path / "x" / "app"
    lone.mkdir(parents=True)
    assert winlaunch.top_of(str(lone)) == str(lone), "1つ上に 起動.pyw が無ければ、ただの app という名前のフォルダ"


def _old_folder_with_new_zip(tmp_path):
    """前の版（全部がいちばん上）のフォルダに、新しい zip（app の並び）を上書きで展開した形。"""
    top = tmp_path / "TextToVoicevox"
    _put(top, {
        # 前の版のプログラム・説明（新しい物が app にある）
        "main.py": "old", "core.py": "old", "LICENSE": "old", "README.md": "old", "デバッグ起動.pyw": "old",
        "assets/app-icon.png": "old", "docs/screenshot-light.png": "old",
        "__pycache__/core.cpython-315.pyc": "old",
        # 自分のデータと部品
        "settings.json": "mine", "voice_cache/a.wav": "mine", "venv/pyvenv.cfg": "mine",
        "ocr_engine/ok.txt": "mine", "起動エラー.log": "mine",
        # 自分で置いた物
        "assets/立ち絵/ずんだもん.png": "mine", "docs/案内図.html": "mine", "メモ.txt": "mine",
        # 新しい zip のいちばん上（上書きされた物）と、前に作ったショートカット・古い .bat
        "起動.pyw": "new", "How to use (日本語).html": "new", "TextToVoicevox.lnk": "old", "起動.bat": "old",
    })
    _put(top / "app", {
        "main.py": "new", "core.py": "new", "winsetup.py": "new", "winlaunch.py": "new", "LICENSE": "new",
        "README.md": "new", "デバッグ起動.pyw": "new", "assets/app-icon.png": "new", "assets/app-icon.ico": "new",
        "assets/立ち絵/README.md": "new", "docs/screenshot-light.png": "new",
    })
    return top, top / "app"


def test_old_folder_moves_into_app(tmp_path):
    """設定・声の保管・部品・自分で置いた物は app へ移し、前の版のプログラムと説明は片付ける。
    見知らぬファイル（自分のメモ）は消さない。古い .bat は remove_legacy に任せる。"""
    top, app = _old_folder_with_new_zip(tmp_path)
    assert winlaunch.move_into_app(str(top), str(app)) is True
    assert sorted(os.listdir(top)) == sorted(["app", "How to use (日本語).html", "TextToVoicevox.lnk",
                                              "メモ.txt", "起動.bat", "起動.pyw"])
    for rel in ("settings.json", "voice_cache/a.wav", "venv/pyvenv.cfg", "ocr_engine/ok.txt", "起動エラー.log",
                "assets/立ち絵/ずんだもん.png", "docs/案内図.html"):
        assert (app / rel).read_text(encoding="utf-8") == "mine", rel
    for rel in ("main.py", "core.py", "LICENSE", "README.md", "デバッグ起動.pyw", "assets/app-icon.png",
                "docs/screenshot-light.png"):
        assert (app / rel).read_text(encoding="utf-8") == "new", rel
    assert winlaunch.move_into_app(str(top), str(app)) is True, "2回目は何もしない"


def test_moving_never_overwrites_what_app_already_has(tmp_path):
    """app 側にもう設定があるとき（新しい版で一度開いたあと）は、どちらも消さない。"""
    top, app = _old_folder_with_new_zip(tmp_path)
    (app / "settings.json").write_text("app", encoding="utf-8")
    assert winlaunch.move_into_app(str(top), str(app)) is True
    assert (app / "settings.json").read_text(encoding="utf-8") == "app"
    assert (top / "settings.json").read_text(encoding="utf-8") == "mine"


def test_moving_stops_untouched_while_the_old_app_is_open(tmp_path, monkeypatch):
    """前の版が開いたまま（venv を動かせない）なら、設定にもプログラムにも触らずにやめる。"""
    top, app = _old_folder_with_new_zip(tmp_path)
    real = os.replace

    def busy(src, dst):
        if os.path.basename(src) == "venv":
            raise PermissionError("使用中")
        return real(src, dst)
    monkeypatch.setattr(winlaunch.os, "replace", busy)
    assert winlaunch.move_into_app(str(top), str(app)) is False
    assert (top / "settings.json").exists() and (top / "main.py").exists()
    assert not (app / "settings.json").exists()


def test_run_stops_and_says_why_when_it_cannot_move(tmp_path, monkeypatch):
    top, app = _old_folder_with_new_zip(tmp_path)
    calls = _record_calls(monkeypatch)
    monkeypatch.setattr(winlaunch, "move_into_app", lambda t, h: False)
    assert winlaunch.run(str(app), "launch") == 1
    assert calls == ["tell"]


def _fake_link(made):
    """本物と同じく、絵の道と、ショートカットから見た行き先の相対の道を書いておく偽物。"""
    def write(lnk, target, icon, desc):
        made.append((lnk, target, icon, desc))
        rel = "." + os.sep + os.path.relpath(target, os.path.dirname(lnk))
        with open(lnk, "wb") as f:
            f.write(b"\x4c\x00\x00\x00" + icon.encode("utf-16-le") + rel.encode("utf-16-le"))
    return write


def _zip_folder(tmp_path, monkeypatch, made, link=None):
    top = tmp_path / "TextToVoicevox"
    _put(top, {"起動.pyw": "new", "app/main.py": "", "app/winsetup.py": "", "app/assets/app-icon.ico": ""})
    monkeypatch.setattr(winlaunch, "_IS_WIN", True)
    monkeypatch.setattr(winlaunch, "_shell_link", link or _fake_link(made))
    monkeypatch.setattr(winlaunch, "unblock", lambda here: None)
    monkeypatch.setattr(winlaunch, "start_app", lambda here: None)
    monkeypatch.setattr(winlaunch, "open_console", lambda here, mode: None)
    return top, top / "app"


def test_zip_layout_tucks_the_launcher_into_app(tmp_path, monkeypatch):
    """1回開いたら、起動.pyw は app の中へしまい、いちばん上にはアプリの絵の
    「TextToVoicevox」だけを残す（行き先は app の中の 起動.pyw、絵も app の中の物）。"""
    made = []
    top, app = _zip_folder(tmp_path, monkeypatch, made)
    assert winlaunch.run(str(app), "launch") == 0
    assert made == [(str(top / "TextToVoicevox.lnk"), str(app / "起動.pyw"),
                     str(app / "assets" / "app-icon.ico"), "TextToVoicevox を開く")]
    assert not (top / "起動.pyw").exists() and (app / "起動.pyw").read_text(encoding="utf-8") == "new"
    assert winlaunch.top_of(str(app)) == str(top), "しまったあとも、いちばん上はショートカットで見分ける"
    assert winlaunch.run(str(app), "launch") == 0 and len(made) == 1, "2回目は何もしない"


def test_launcher_stays_on_top_when_no_shortcut_can_be_made(tmp_path, monkeypatch):
    """ショートカットを作れない PC では、入口が無くならないよう 起動.pyw をいちばん上に残す。"""
    def broken(*a):
        raise OSError("COM が使えない")
    top, app = _zip_folder(tmp_path, monkeypatch, [], link=broken)
    assert winlaunch.run(str(app), "launch") == 0
    assert (top / "起動.pyw").exists() and not (app / "起動.pyw").exists()


def test_new_zip_launcher_replaces_the_tucked_one(tmp_path, monkeypatch):
    """新しい zip を上書きで展開すると、いちばん上にまた 起動.pyw ができる。次に開いたとき、
    app の中の古い物と入れ替えて、いちばん上からは消す。"""
    made = []
    top, app = _zip_folder(tmp_path, monkeypatch, made)
    assert winlaunch.run(str(app), "launch") == 0
    (app / "起動.pyw").write_text("old", encoding="utf-8")
    (top / "起動.pyw").write_text("newer", encoding="utf-8")
    assert winlaunch.run(str(app), "launch") == 0
    assert not (top / "起動.pyw").exists() and (app / "起動.pyw").read_text(encoding="utf-8") == "newer"


@pytest.mark.skipif(os.name != "nt", reason="Windows のショートカット")
def test_real_shortcut_points_into_app(tmp_path, monkeypatch):
    r"""本物の .lnk でも、しまったあとの行き先は相対の道 .\app\起動.pyw で入る。"""
    top = tmp_path / "TextToVoicevox"
    _put(top, {"起動.pyw": "", "app/main.py": "", "app/winsetup.py": ""})
    (top / "app" / "assets").mkdir()
    (top / "app" / "assets" / "app-icon.ico").write_bytes(b"ico")
    winlaunch.place_launcher(str(top), str(top / "app"))
    data = (top / "TextToVoicevox.lnk").read_bytes()
    assert (".\\app\\起動.pyw").encode("utf-16-le") in data
    assert winlaunch.shortcut_current(str(top), str(top / "app"))
    assert not (top / "起動.pyw").exists()


@pytest.mark.parametrize("name", ["起動.pyw", "デバッグ起動.pyw"])
def test_launchers_find_the_app_folder(tmp_path, name):
    """起動口は、隣に app フォルダがあればそこを、無ければ自分のフォルダをアプリとして開く
    （zip の並び・1つのフォルダの並びのどちらでも。デバッグ起動は zip では app の中にある）。"""
    import runpy
    src = os.path.join(ROOT, name)
    nested = tmp_path / "nested"
    (nested / "app").mkdir(parents=True)
    (nested / "app" / "winlaunch.py").write_text("", encoding="utf-8")
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "winlaunch.py").write_text("", encoding="utf-8")
    where = {"起動.pyw": (nested / name, nested / "app"), "デバッグ起動.pyw": (nested / "app" / name, nested / "app")}
    saved = list(sys.path)
    try:
        for path, expected in (where[name], (flat / name, flat)):
            path.write_bytes(open(src, "rb").read())
            got = runpy.run_path(str(path), run_name="t2v_launcher_test")
            assert got["APP"] == str(expected)
    finally:
        sys.path[:] = saved
