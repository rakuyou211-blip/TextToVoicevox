# -*- coding: utf-8 -*-
"""1行導入が入れる「Python 入りの Windows 版」（tools/make_bundle.py と install.ps1）のテスト。

本物の Python を落としたり、全部入りの zip を作ったりはしない（それは CI の bundle-windows が
実際にやる）。決め事と、作る途中の小さな部品を確かめる（どの OS でも動く）。
"""
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
import make_bundle  # noqa: E402
import make_release  # noqa: E402
import winlaunch  # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _files(folder):
    return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/")
                  for d, _sub, names in os.walk(folder) for f in names)


def _touch(base, *names):
    for name in names:
        p = os.path.join(str(base), *name.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")


def test_runtime_is_pinned_to_one_python():
    """落とす Python は版とハッシュを決めてある。._pth の名前（python3xx）も同じ版。"""
    m = re.fullmatch(r"https://www\.python\.org/ftp/python/(3\.(\d+)\.\d+)/python-\1-amd64\.zip",
                     make_bundle.RUNTIME_URL)
    assert m, make_bundle.RUNTIME_URL
    assert re.fullmatch(r"[0-9a-f]{64}", make_bundle.RUNTIME_SHA256)
    assert make_bundle.RUNTIME_PTH == "python3%s._pth" % m.group(2)


def test_runtime_sees_only_itself_and_the_app():
    """環境変数やほかの Python の部品と混ざらない。アプリのフォルダ（..）は先頭で読む
    （._pth があると、開いた main.py の隣を自動では見ないため）。"""
    lines = make_bundle.runtime_pth().splitlines()
    assert lines[0] == ".."
    for need in ("DLLs", "Lib", "Lib\\site-packages"):
        assert need in lines
    assert lines[-1] == "import site"
    assert not any(os.path.isabs(l) or ":" in l for l in lines), "どこに入れても動くよう、相対で書く"


def test_names_match_the_app():
    assert make_bundle.RUNTIME == winlaunch.RUNTIME_DIR
    assert make_bundle.ENGINE == winlaunch.ENGINE_DIR
    assert make_bundle.TOP == make_release.TOP


def test_bundle_zip_is_only_for_the_one_line_installer():
    """README からはリンクしない（ブラウザで落として Explorer で展開すると、Defender が約4分止める）。
    install.ps1・release.yml・ci.yml で同じ名前。古い install.ps1 は '*Windows*.zip' を探すので、
    名前に Windows を入れない（入れると、普通の zip と取り違えられる）。"""
    name = make_bundle.NAME
    assert "windows" not in name.lower()
    assert "$BundleName = '%s'" % name in _read("install.ps1")
    assert "dist/%s" % name in _read(".github", "workflows", "release.yml")
    assert name in _read(".github", "workflows", "ci.yml")
    for readme in ("README.md", "README.en.md", "README.zh-TW.md", "README.zh-CN.md"):
        assert "download/" + name not in _read(readme), readme


def test_installer_keeps_the_plain_zip_for_the_old_way():
    """前からの入れ方（venv）は、Python 入りの版ではなく普通の zip を使う。"""
    body = _read("install.ps1")
    plain = body.split("function Get-SourceZipUrl", 1)[1].split("function ", 1)[0]
    assert "$_.name -ne $BundleName" in plain


def test_installer_extracts_without_the_download_mark():
    """Python 入りの版は Explorer ではなく .NET で広げる（印が付かず、数秒で終わる）。
    入れる場所の隣で広げて、名前の付け替えで入れ替える（260 文字の壁と、途中での失敗に備える）。"""
    body = _read("install.ps1")
    bundle = body.split("function Install-Bundle", 1)[1].split("function Install-WithPython", 1)[0]
    assert "ZipFile]::ExtractToDirectory" in bundle
    assert "Expand-Archive" not in bundle
    assert '"$Dest.new"' in bundle and "Move-Item" in bundle
    assert "Wait-AppClosed" in bundle, "開いたまま入れ替えない"


def test_installer_cleans_up_the_other_way():
    """入れ方を替えたら、使わなくなった方の Python を片付ける（残ると起動.pyw が古い方を使う）。"""
    body = _read("install.ps1")
    bundle = body.split("function Install-Bundle", 1)[1].split("function Install-WithPython", 1)[0]
    old_way = body.split("function Install-WithPython", 1)[1].split("\n    try {", 1)[0]
    assert "'venv'" in bundle and "pyvenv.cfg" in bundle
    assert "'runtime'" in old_way


def test_copy_app_takes_the_windows_zip_files(tmp_path, monkeypatch):
    """中身は Windows 用 zip と同じ（Mac 用・テスト・1行導入の台本は入れない）。"""
    tracked = ["main.py", "起動.pyw", "起動.command", "install.ps1", "tests/test_x.py", "assets/a.png"]
    _touch(tmp_path / "src", *tracked)
    monkeypatch.setattr(make_release, "tracked_files", lambda root: tracked)
    app = tmp_path / "out" / "TextToVoicevox"
    make_bundle.copy_app(str(tmp_path / "src"), str(app))
    assert _files(app) == ["assets/a.png", "main.py", "起動.pyw"]


def test_runtime_from_a_local_python_takes_only_pip(tmp_path):
    """手元の Python から作るとき、その人が入れた部品（numpy など）は混ぜない。
    説明書・試験・起動口（Scripts）・古い .pyc も配らない。"""
    src = tmp_path / "py"
    _touch(src, "python.exe", "pythonw.exe", "python315.dll", "Lib/os.py", "Lib/test/t.py",
           "Lib/site-packages/pip/__init__.py", "Lib/site-packages/numpy/__init__.py",
           "Doc/x.html", "Scripts/f2py.exe", "__install__.json", "Lib/__pycache__/os.cpython-315.pyc")
    rt = tmp_path / "rt"
    make_bundle.put_runtime(str(rt), runtime_dir=str(src))
    names = _files(rt)
    assert "Lib/site-packages/pip/__init__.py" in names and "Lib/os.py" in names
    assert not any(n.startswith(("Lib/site-packages/numpy", "Doc/", "Scripts/", "Lib/test/")) for n in names)
    assert "__install__.json" not in names and not any("__pycache__" in n for n in names)
    assert (rt / make_bundle.RUNTIME_PTH).read_text(encoding="utf-8") == make_bundle.runtime_pth()


def test_deps_from_a_venv_skip_pip_and_launchers(tmp_path):
    """署名の無い起動口の exe（pip が作る Scripts）は配らない。pip は Python の物を使う。"""
    rt = tmp_path / "rt"
    _touch(rt, "Lib/site-packages/pip/__init__.py", "Scripts/pypdfium2.exe")
    sp = tmp_path / "sp"
    _touch(sp, "pip/__init__.py", "PIL/__init__.py", "PIL/__pycache__/x.pyc", "pillow-12.dist-info/METADATA")
    make_bundle.put_deps(str(rt), ROOT, deps_from=str(sp))
    assert _files(rt) == ["Lib/site-packages/PIL/__init__.py", "Lib/site-packages/pillow-12.dist-info/METADATA",
                          "Lib/site-packages/pip/__init__.py"]


def test_pack_keeps_japanese_names_and_the_top_folder(tmp_path):
    """.NET（install.ps1）で広げても日本語の名前が崩れないよう、UTF-8 の印を付けて書く。"""
    app = tmp_path / "TextToVoicevox"
    _touch(app, "起動.pyw", "assets/a.txt")
    out = tmp_path / "b.zip"
    assert make_bundle.pack(str(app), str(out)) == 2
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["TextToVoicevox/assets/a.txt", "TextToVoicevox/起動.pyw"]
        jp = [i for i in z.infolist() if not i.filename.isascii()]
        assert jp and all(i.flag_bits & 0x800 for i in jp)


def test_longest_counts_from_the_top_folder(tmp_path):
    app = tmp_path / "TextToVoicevox"
    _touch(app, "runtime/Lib/abc.py", "a.py")
    n, worst = make_bundle.longest(str(app))
    assert worst == os.path.join("TextToVoicevox", "runtime", "Lib", "abc.py")
    assert n == len(worst)


def test_notices_list_each_part_and_its_license_file(tmp_path):
    """一緒に配る部品は、名前・版・ライセンスと、本文の在りかを書き出す（GEOS など LGPL もある）。"""
    app = tmp_path / "TextToVoicevox"
    info = app / "runtime" / "Lib" / "site-packages" / "requests-2.34.2.dist-info"
    _touch(info, "licenses/LICENSE")
    (info / "METADATA").write_text("Metadata-Version: 2.4\nName: requests\nVersion: 2.34.2\n"
                                   "License-Expression: Apache-2.0\n", encoding="utf-8")
    eng = app / "ocr_engine" / "deps" / "shapely-2.1.dist-info"
    _touch(eng, "LICENSE_GEOS")
    (eng / "METADATA").write_text("Metadata-Version: 2.1\nName: shapely\nVersion: 2.1\n"
                                  "Classifier: License :: OSI Approved :: BSD License\n", encoding="utf-8")
    make_bundle.write_notices(str(app))
    text = (app / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    assert "requests 2.34.2  [Apache-2.0]" in text
    assert "runtime/Lib/site-packages/requests-2.34.2.dist-info/licenses/LICENSE" in text
    assert "shapely 2.1  [BSD License]" in text
    assert "ocr_engine/deps/shapely-2.1.dist-info/LICENSE_GEOS" in text
    assert "runtime/LICENSE.txt" in text
