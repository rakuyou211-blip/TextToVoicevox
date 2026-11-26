# -*- coding: utf-8 -*-
"""配る説明書（説明書.html と Manual (English).html）を作る。

    python tools/make_manual.py

元原稿は docs/manual/index.html（日本語）と docs/manual/en/index.html（英語）、画像は
docs/manual/img/ にある（画像は tools/manual/shoot.py で撮り直せる）。元原稿はそのまま
Web 版（GitHub Pages の …/TextToVoicevox/manual/ と …/manual/en/）としても読まれる。
配る説明書は1つのファイルで開けるよう、画像を data: URI で埋め込む（zip を展開した人が
フォルダの場所を気にせず開けるように。ネットにもつながない）。元原稿や画像を変えたら、
これを動かして作り直す（tests/test_manual.py が、作り直し忘れを見つける）。
"""
import base64
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs", "manual")
IMG_DIR = os.path.join(DOCS, "img")
# (元原稿, 配る説明書)。配る物は zip のいちばん上（winlaunch.TOP_FILES）に入る
MANUALS = [
    (os.path.join(DOCS, "index.html"), os.path.join(ROOT, "説明書.html")),
    (os.path.join(DOCS, "en", "index.html"), os.path.join(ROOT, "Manual (English).html")),
]
SRC, OUT = MANUALS[0]   # 日本語版

_IMG = re.compile(r'src="(?:\.\./)?img/([A-Za-z0-9_.-]+\.(?:jpg|png))"')
_MIME = {"jpg": "image/jpeg", "png": "image/png"}
# Web 版と配る説明書で行き先が変わるリンク（もう一方の言語の版）:
#   href="Web 版での行き先" data-local="配る説明書での行き先"
_LOCAL = re.compile(r'href="[^"]*" data-local="([^"]*)"')
# 元原稿だけに要る説明は、配る版から外す
_SOURCE_NOTE = re.compile(r"<!-- (?:このファイルは元原稿|This file is the source)[^\n]*-->\n")


def build(src=SRC, img_dir=IMG_DIR):
    """元原稿の画像を埋め込んだ HTML（文字列）を返す。無い画像があれば止める。"""
    with open(src, encoding="utf-8") as f:
        html = f.read()

    def embed(m):
        name = m.group(1)
        path = os.path.join(img_dir, name)
        if not os.path.exists(path):
            raise FileNotFoundError(f"説明書の画像が見つかりません: {path}")
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        return f'src="data:{_MIME[name.rsplit(".", 1)[1]]};base64,{data}"'

    html = _IMG.sub(embed, html)
    html = _LOCAL.sub(lambda m: f'href="{m.group(1)}"', html)
    return _SOURCE_NOTE.sub("", html)


def main():
    for src, out in MANUALS:
        html = build(src)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print(f"{out}  ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
