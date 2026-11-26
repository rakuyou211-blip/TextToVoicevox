# -*- coding: utf-8 -*-
"""配る説明書（説明書.html と Manual (English).html）のテスト。"""
import html as _html
import os
import re
import sys
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "manual"))
import make_manual  # noqa: E402
import make_release  # noqa: E402
import winlaunch  # noqa: E402

SOURCES = [src for src, _out in make_manual.MANUALS]
OUTS = [out for _src, out in make_manual.MANUALS]


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_manual_is_rebuilt_from_source():
    """元原稿や画像を変えたのに、配る説明書を作り直し忘れていない。"""
    for src, out in make_manual.MANUALS:
        assert _read(out) == make_manual.build(src), out


def test_manual_is_self_contained():
    """画像はすべて埋め込み。外のファイルや、ネットの画像・スクリプトを読まない。"""
    for out in OUTS:
        html = _read(out)
        assert 'src="img/' not in html and 'src="../img/' not in html, out
        assert not re.search(r'<(?:img|script)[^>]+src="https?:', html), out
        assert "<link" not in html, out
        assert "data-local=" not in html, out
        assert html.count('src="data:image/') >= 10, out


def test_manual_anchors_exist():
    """目次や本文の中のリンク（#…）の行き先が、すべてある。"""
    for out in OUTS:
        html = _read(out)
        ids = set(re.findall(r'id="([^"]+)"', html))
        for target in re.findall(r'href="#([^"]+)"', html):
            assert target in ids, (out, target)


def test_manuals_link_to_each_other():
    """日本語版と英語版は、互いに行き来できる。配る説明書では zip のいちばん上の
    となりのファイルへ、Web 版ではもう一方の元原稿へ。"""
    for src, out in make_manual.MANUALS:
        local = [unquote(h) for h in re.findall(r'href="([^":#]+\.html)"', _read(out))]
        others = [os.path.basename(o) for o in OUTS if o != out]
        assert set(others) <= set(local), (out, local)
        assert set(local) <= set(winlaunch.TOP_FILES), (out, local)
        for web in re.findall(r'href="([^"]*)" data-local=', _read(src)):
            target = os.path.normpath(os.path.join(os.path.dirname(src), web, "index.html"))
            assert target in [os.path.normpath(s) for s in SOURCES if s != src], (src, web)


def test_manual_names_the_current_version():
    """表紙の「対象: vX.Y 以降」（英語版は For vX.Y and later）が、アプリの版（X.Y）と合っている
    （版を上げたら説明書も見直す）。"""
    import core
    major_minor = ".".join(core.APP_VERSION.split(".")[:2])
    for src, pattern in zip(SOURCES, (r"対象: v(\d+\.\d+) 以降", r"For v(\d+\.\d+) and later")):
        m = re.search(pattern, _read(src))
        assert m and m.group(1) == major_minor, (src, m and m.group(1), major_minor)


def test_first_readme_points_to_the_manual():
    """はじめにお読みください.txt と、日本語・英語の案内から、説明書へたどれる。"""
    for name in ("はじめにお読みください.txt", "How to use (日本語).html"):
        assert "説明書.html" in _read(os.path.join(ROOT, name)), name
    guide = unquote(_read(os.path.join(ROOT, "How to use (English).html")))
    assert "Manual (English).html" in guide


def test_manual_ships_at_top_of_both_zips():
    """説明書は Windows の zip のいちばん上（起動.pyw のとなり）と、Mac の zip に入る。
    元原稿と画像（docs/manual/）は、埋め込み済みなので配らない。"""
    tracked = make_release.tracked_files()
    for out in OUTS:
        name = os.path.basename(out)
        assert name in winlaunch.TOP_FILES, name
        assert make_release.zip_name("windows", name) == "TextToVoicevox/" + name
        for target in ("windows", "mac"):
            assert name in make_release.files_for(target, tracked), (target, name)
    for target in ("windows", "mac"):
        assert not any(f.startswith("docs/manual/") for f in make_release.files_for(target, tracked))


def _button_names(text):
    # class="btn" と "btn main" だけ。"btn demo" は見た目の見本（「塗ったボタン」など）で、名前ではない
    return {_html.unescape(re.sub(r"\s+", " ", m)).strip()
            for m in re.findall(r'<span class="btn(?: main)?"[^>]*>(.*?)</span>', text, re.S)}


def test_manual_button_names_are_in_the_app():
    """説明書に書いたボタンの名前が、アプリに本当にある（ボタンの名前を変えたのに、説明書を
    直し忘れていない）。英語版も、ボタンはアプリの画面と同じ日本語で書く。"""
    app = _read(os.path.join(ROOT, "main.py"))
    for src in SOURCES:
        names = _button_names(_read(src))
        assert len(names) >= 20, (src, names)
        missing = sorted(n for n in names if n not in app)
        assert not missing, (src, missing)


def test_manual_images_are_made_by_the_shoot_tools():
    """説明書が使う画像は、すべて tools/manual で撮り直せる（make_images.py が作る）。その元の
    画面は capture.py が撮る。使わなくなった画像を置きっぱなしにしない。"""
    import capture
    import make_images
    made = set(make_images.names())
    for src in SOURCES:
        used = set(re.findall(r'src="(?:\.\./)?img/([^"]+)"', _read(src)))
        assert used and used <= made, (src, sorted(used - made))
    assert set(os.listdir(make_manual.IMG_DIR)) == made
    assert {shot for _name, shot, _box, _w in make_images.JOBS} <= set(capture.SHOTS)
