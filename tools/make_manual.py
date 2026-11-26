# -*- coding: utf-8 -*-
"""配る説明書（説明書.html）を作る。

    python tools/make_manual.py

元原稿は docs/manual/index.html、画像は docs/manual/img/ にある。元原稿はそのまま
Web 版（GitHub Pages の …/TextToVoicevox/manual/）としても読まれる。配る説明書は1つの
ファイルで開けるよう、画像を data: URI で埋め込む（zip を展開した人がフォルダの場所を
気にせず開けるように。ネットにもつながない）。元原稿や画像を変えたら、これを動かして
説明書.html を作り直す（tests/test_manual.py が、作り直し忘れを見つける）。
"""
import base64
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "docs", "manual", "index.html")
IMG_DIR = os.path.join(ROOT, "docs", "manual", "img")
OUT = os.path.join(ROOT, "説明書.html")

_IMG = re.compile(r'src="img/([A-Za-z0-9_.-]+\.(?:jpg|png))"')
_MIME = {"jpg": "image/jpeg", "png": "image/png"}


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
    # 元原稿だけに要る説明は、配る版から外す
    html = html.replace("<!-- このファイルは元原稿。配る「説明書.html」は tools/make_manual.py が画像を埋め込んで作る。 -->\n", "")
    return html


def main():
    html = build()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print(f"{OUT}  ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
