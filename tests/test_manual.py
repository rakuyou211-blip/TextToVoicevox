# -*- coding: utf-8 -*-
"""配る説明書（説明書.html）のテスト。"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_manual  # noqa: E402
import make_release  # noqa: E402
import winlaunch  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_manual_is_rebuilt_from_source():
    """元原稿や画像を変えたのに、説明書.html を作り直し忘れていない。"""
    assert _read(make_manual.OUT) == make_manual.build()


def test_manual_is_self_contained():
    """画像はすべて埋め込み。外のファイルや、ネットの画像・スクリプトを読まない。"""
    html = _read(make_manual.OUT)
    assert 'src="img/' not in html
    assert not re.search(r'<(?:img|script)[^>]+src="https?:', html)
    assert "<link" not in html
    assert html.count('src="data:image/') >= 10


def test_manual_anchors_exist():
    """目次や本文の中のリンク（#…）の行き先が、すべてある。"""
    html = _read(make_manual.OUT)
    ids = set(re.findall(r'id="([^"]+)"', html))
    for target in re.findall(r'href="#([^"]+)"', html):
        assert target in ids, target


def test_manual_names_the_current_version():
    """表紙の「対象: vX.Y 以降」が、アプリの版（X.Y）と合っている（版を上げたら説明書も見直す）。"""
    import core
    major_minor = ".".join(core.APP_VERSION.split(".")[:2])
    m = re.search(r"対象: v(\d+\.\d+) 以降", _read(make_manual.SRC))
    assert m and m.group(1) == major_minor, (m and m.group(1), major_minor)


def test_first_readme_points_to_the_manual():
    """はじめにお読みください.txt と日本語の案内から、説明書へたどれる。"""
    for name in ("はじめにお読みください.txt", "How to use (日本語).html"):
        assert "説明書.html" in _read(os.path.join(ROOT, name)), name


def test_manual_ships_at_top_of_both_zips():
    """説明書は Windows の zip のいちばん上（起動.pyw のとなり）と、Mac の zip に入る。
    元原稿と画像（docs/manual/）は、埋め込み済みなので配らない。"""
    assert "説明書.html" in winlaunch.TOP_FILES
    tracked = make_release.tracked_files()
    for target in ("windows", "mac"):
        files = make_release.files_for(target, tracked)
        assert "説明書.html" in files
        assert not any(f.startswith("docs/manual/") for f in files)
    assert make_release.zip_name("windows", "説明書.html") == "TextToVoicevox/説明書.html"
