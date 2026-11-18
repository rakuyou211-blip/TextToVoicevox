# -*- coding: utf-8 -*-
"""
core.py - テキスト抽出・整形・VOICEVOX連携のコアロジック（GUI非依存）
GUIから呼び出すほか、単体テストにも使用する。
"""
import io
import os
import re
import sys
import json
import math
import time
import uuid
import wave
import queue
import shutil
import zipfile
import tempfile
import posixpath
import subprocess
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote
# xml.etree は docx/epub 抽出でのみ使う。importに40msほどかかるため、
# 起動時ではなく使う関数の中で読み込む（extract_docx / extract_epub 参照）

# アプリのバージョン（タイトルバー・CLI --version・不具合報告の目印に使う）。
# リリースごとにここだけ更新する。
APP_VERSION = "1.26.1"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_PS1 = os.path.join(APP_DIR, "ocr_win.ps1")
OCR_HELPER = os.path.join(APP_DIR, "ocr_helper.py")
AUDIO_PS1 = os.path.join(APP_DIR, "audio_win.ps1")   # Windows に入っている変換で M4A / MP3 を作る
WIN_ENCODER = "windows"   # audio_encoders() が返す印（ffmpeg の場所の代わり）
OCR_ENGINE_DIR = os.path.join(APP_DIR, "ocr_engine")   # 強い文字読み取り（Windows の初回セットアップで入る）

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXT = {".pdf"}
TXT_EXT = {".txt"}
DOCX_EXT = {".docx"}
EPUB_EXT = {".epub"}
DOC_EXT = TXT_EXT | DOCX_EXT | EPUB_EXT  # OCR不要のテキスト系入力

CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0  # Winでサブプロセスのコンソール窓を出さない

# Windows.Media.Ocr の OcrEngine.MaxImageDimension（実機では通常2600）。これを超える辺の
# 画像は RecognizeAsync が例外を投げ「無言でOCR空振り」になるため、前処理で必ず収める。
WIN_OCR_MAX_DIM = 2600


# ============================================================
#  テキスト整形
# ============================================================
def is_cjk(ch: str) -> bool:
    """日本語・CJK系の文字（ひらがな/カタカナ/漢字/全角記号など）か。"""
    if not ch:
        return False
    o = ord(ch)
    return (
        0x3000 <= o <= 0x303F or   # CJK記号・句読点（、。「」等）
        0x3040 <= o <= 0x309F or   # ひらがな
        0x30A0 <= o <= 0x30FF or   # カタカナ（ー含む）
        0x3400 <= o <= 0x4DBF or   # 漢字拡張A
        0x4E00 <= o <= 0x9FFF or   # 漢字
        0xF900 <= o <= 0xFAFF or   # 互換漢字
        0xFF00 <= o <= 0xFFEF      # 全角英数・半角カナ等
    )


def is_jp_script_char(ch: str) -> bool:
    """『日本語の中身の文字』か＝ひらがな・カタカナ・漢字（半角カナ含む）。
    is_cjk と違い、全角の英数・記号や句読点は含めない（“文字”と“区切り記号”を分ける用途）。"""
    if not ch:
        return False
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF or   # ひらがな・カタカナ（ー含む）
        0x3400 <= o <= 0x4DBF or   # 漢字拡張A
        0x4E00 <= o <= 0x9FFF or   # 漢字
        0xF900 <= o <= 0xFAFF or   # 互換漢字
        0xFF66 <= o <= 0xFF9D       # 半角カタカナ
    )


def remove_cjk_spaces(text: str, keep_ascii_spaces: bool = True) -> str:
    """
    OCR/抽出で文字間に挿入された空白を除去する。
    隣のどちらかがCJK文字なら空白を削除。両側がASCII語の場合のみ
    （keep_ascii_spaces=Trueなら）空白を1つ残す（例: "Excel 365"）。
    """
    out = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch in (" ", "\t", "　"):
            # 連続する空白をまとめる
            j = i
            while j < n and text[j] in (" ", "\t", "　"):
                j += 1
            prev = out[-1] if out else ""
            nxt = text[j] if j < n else ""
            # 改行前後の空白は捨てる
            if prev == "\n" or nxt == "\n" or nxt == "":
                pass
            elif is_cjk(prev) or is_cjk(nxt):
                pass  # CJKが絡む空白は削除
            elif keep_ascii_spaces:
                out.append(" ")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_SENT_ENDERS = "。．！？!?"


# clean_text の途中で、行頭の字下げ（段落の始まり）を運ぶ見えない印（私用領域の1文字。
# 空白ではないので strip で消えず、本文に出てくることも無い）
_PARA_MARK = ""


def join_wrapped_lines(text: str, max_len: int = 90) -> str:
    """
    視覚的な改行で途切れた文を連結する（小説・段落向け）。
    行末が文末記号で終わらない場合は次行と連結（CJKは空白なし）。
    ただし連結後が max_len を超える場合は連結せず改行を保つ
    （箇条書きや表データを巨大な1行にまとめないための安全弁）。
    空行は段落の区切りとして残す。
    """
    paragraphs = text.split("\n\n")
    result_paras = []
    for para in paragraphs:
        lines = [ln.strip() for ln in para.split("\n")]
        buf = ""
        for ln in lines:
            if not ln:
                continue
            if buf == "":
                buf = ln
            elif ln.startswith(_PARA_MARK) or buf[-1] in _SENT_ENDERS or len(buf) >= max_len:
                # 字下げで始まる（新しい段落）・文末記号で終わる・既に長い → 連結せず改行
                buf += "\n" + ln
            else:
                # 連結：両端がASCIIなら空白、それ以外は詰める
                if (not is_cjk(buf[-1])) and (not is_cjk(ln[0])) and buf[-1].isascii() and ln[0].isascii():
                    buf += " " + ln
                else:
                    buf += ln
        if buf:
            result_paras.append(buf)
    return "\n\n".join(result_paras)


# 折り返し（視覚的な改行）で1文が複数行に割れたものだけを連結するための下限長。
# これ以上の長さの行が文末記号なしで途切れていれば「折り返しらしい」とみなす。
# 未満の短い行は箇条書き・ラベル・短い見出しの可能性が高いので連結しない。
# ※ レイアウト座標が無いテキストだけからでは「折り返し」と「別々の短い見出し」を
#   完全には区別できない（日本語の見出しは句点で終わらないため）。誤連結を避けたい
#   場合はこの機能をOFFにする。既定OFF。真に堅牢にするにはOCRの行の外接矩形が必要。
_SMART_JOIN_MIN_LEN = 16


def _smart_join_sep(last: str, first: str):
    """境界文字が“折り返しの継続”として連結可能かを返す。可なら区切り文字、不可なら None。
    日本語スクリプト同士は空文字（詰める）、半角英数同士は空白。それ以外（記号・箇条書き・
    括弧・日本語⇄英字の切替）は別ブロックとみなして連結しない。"""
    if is_jp_script_char(last) and is_jp_script_char(first):
        return ""
    if last.isascii() and last.isalnum() and first.isascii() and first.isalnum():
        return " "
    return None


def smart_join_wrapped(text: str) -> str:
    """OCR等の“折り返しで途切れた文”を連結する（join_wrapped_lines より保守的）。
    隣接行を連結するのは、直前の“元の行”が一定長以上・文末記号なしで、境界の字種が
    継続可能なとき（詳細は _smart_join_sep）。空行/空白のみ行は段落境界として連結を断つ。

    注意: レイアウト座標を持たないテキストだけからでは、折り返しと「別々の短い見出し」を
    完全には区別できない。長い見出しが2本続くと連結され得る点に留意（既定OFF・opt-in）。"""
    out = []
    prev_len = 0  # 直前に置いた“元の行”の長さ。段落先頭・空行直後は0＝連結不可（連鎖防止）
    for raw in text.split("\n"):
        ln = raw.strip()
        if not ln:
            out.append("")        # 空行/空白のみ行＝段落境界
            prev_len = 0
            continue
        if (prev_len >= _SMART_JOIN_MIN_LEN and out and out[-1]
                and out[-1][-1] not in _SENT_ENDERS):
            sep = _smart_join_sep(out[-1][-1], ln[0])
            if sep is not None:
                out[-1] += sep + ln
                prev_len = len(ln)   # 連鎖防止: 次の判定は“今つないだ元の行”の長さで見る
                continue
        out.append(ln)
        prev_len = len(ln)
    return "\n".join(out)


# OCRの行“座標”を使った折り返し連結のしきい値（すべて画像サイズに対する正規化[0,1]）。
_REFLOW_COL_TOL = 0.06      # 左端がこの差以内なら同じ列（段落）
_REFLOW_GAP_MAX = 1.0       # 行間がこの倍率(×行高)以内なら折り返し、超えたら別ブロック
_REFLOW_MARGIN_TOL = 0.04   # 右端が列の右余白にこの差以内まで達していれば「行が一杯＝折り返し」
_REFLOW_MIN_MARGIN = 0.30   # 列の右余白がこの位置未満（＝とても短い塊）なら折り返し判定しない

# 段組（複数列レイアウト）の検出しきい値。誤検出は読み順を壊すため、全ガードを
# 満たすときだけ列分割し、迷ったら従来どおり単一列として扱う。
_COL_MIN_LINES = 3    # 列として認めるのに必要な行数（1行だけの孤立塊は列ではない）
_COL_MIN_WIDTH = 0.25  # 列の最小幅。設定画面等の「左ラベル/右値」2列は左が狭いので除外される
_COL_MIN_GAP = 0.03    # 列間に必要な水平ギャップ（これ未満は同じ列にマージ）


def _split_columns(items: list) -> list:
    """行のx区間を推移的にマージして列（段組）に分ける。
    2段組PDFのOCRは y昇順ソートだけだと左右の行が交互に並び読み順が壊れるため、
    確信できるときだけ列単位（左→右）で処理する。確信の条件（すべて必須）:
    列が2つ以上・各列に _COL_MIN_LINES 行以上・各列の幅が _COL_MIN_WIDTH 以上・
    列間の水平ギャップが _COL_MIN_GAP 以上（マージ規則により自動的に保証）。
    満たさなければ [items]（単一列＝従来動作）を返す。"""
    clusters = []   # [x0, x1, [lines]] を x0 昇順で保持
    for l in sorted(items, key=lambda l: float(l["x0"])):
        x0, x1 = float(l["x0"]), float(l["x1"])
        if clusters and x0 < clusters[-1][1] + _COL_MIN_GAP:
            c = clusters[-1]
            c[0] = min(c[0], x0)
            c[1] = max(c[1], x1)
            c[2].append(l)
        else:
            clusters.append([x0, x1, [l]])
    if len(clusters) < 2:
        return [items]
    for c in clusters:
        if len(c[2]) < _COL_MIN_LINES or (c[1] - c[0]) < _COL_MIN_WIDTH:
            return [items]

    # 本当の段組は「左右の行が同じ高さに並んで共存」する。チャットスクショ
    # （左右の吹き出しが時系列で交互＝行の高さは重ならない）を段組と誤検出して
    # 会話順を壊さないよう、隣接列間で行の過半数がy方向に対になることを要求する。
    def _paired_frac(c1, c2):
        n = 0
        for l in c1:
            for m in c2:
                ov = (min(float(l["y1"]), float(m["y1"]))
                      - max(float(l["y0"]), float(m["y0"])))
                if ov > 0.5 * min(_ocr_line_height(l), _ocr_line_height(m)):
                    n += 1
                    break
        return n / len(c1)
    for i in range(len(clusters) - 1):
        a, b = clusters[i][2], clusters[i + 1][2]
        if _paired_frac(a, b) < 0.5 or _paired_frac(b, a) < 0.5:
            return [items]
    return [c[2] for c in clusters]


def _ocr_line_height(l) -> float:
    """1行の外接矩形の高さ（正規化）。退化（0以下）は極小値に丸める。"""
    return max(float(l["y1"]) - float(l["y0"]), 1e-6)


def _ocr_median_height(items) -> float:
    """行高の代表値（中央値）。少数のロゴ/日時ラベルが混じっても本文の高さに寄る。"""
    heights = sorted(_ocr_line_height(l) for l in items)
    return heights[len(heights) // 2]


def _group_ocr_blocks(items: list, h: float) -> list:
    """縦に連続し同じ列（左端が一致）の行を1ブロック（段落候補）にまとめる。
    items は (y0, x0) 昇順に整列済み・非空であること。h は行高の代表値（中央値）。
    行間が大きい／左端がずれる行は別ブロックの先頭になる。右余白等をブロック内だけで
    測れるようにして、離れたフッター等が別ブロックの判定を汚染するのを防ぐ。
    reflow_ocr_lines（折り返し連結）と strip_overlay_labels（ラベル除去）で共有する。"""
    blocks = [[items[0]]]
    for line in items[1:]:
        prev = blocks[-1][-1]
        same_col = abs(float(line["x0"]) - float(prev["x0"])) <= _REFLOW_COL_TOL
        gap = float(line["y0"]) - float(prev["y1"])
        if same_col and -0.5 * h <= gap <= _REFLOW_GAP_MAX * h:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


# 行の箱の縦が横のこの倍以上で、2字以上あれば、縦書きの列とみなす
_TATE_RATIO = 1.5


def image_aspect(path) -> float:
    """画像の 幅÷高さ。開けなければ 1.0（画像の先頭だけ読むので軽い）。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.width / im.height
    except Exception:
        return 1.0


def tate_to_yoko(lines: list, aspect: float = 1.0) -> list:
    """縦書きのページなら、列を「横書きの行」に見立てた座標に直して返す（横書きはそのまま）。
    読み手は列を左から順に返すが、縦書きは右の列から読む。以前は順番を直さず、電子書籍の
    縦書きをページの終わりから読み上げていた。座標を回せば、並べ替えも段組も折り返しの連結も
    ラベルの除去も、横書きと同じ処理で済む（列の上端→左端、列の下端→右端、右の列ほど上）。
    列の字が過半数のページだけを回す。aspect は画像の 幅÷高さ（座標は幅と高さで割ってあり、
    そのままでは縦長か比べられない。横に細長い切り抜きの「OK」を列と見誤らないため）。"""
    total = tall = 0
    for l in lines:
        n = sum(1 for c in str(l["text"]) if not c.isspace())
        total += n
        height = float(l["y1"]) - float(l["y0"])
        width = (float(l["x1"]) - float(l["x0"])) * aspect
        if n >= 2 and height >= _TATE_RATIO * width:
            tall += n
    if tall * 2 <= total:
        return lines
    return [dict(l, x0=float(l["y0"]), x1=float(l["y1"]),
                 y0=1.0 - float(l["x1"]), y1=1.0 - float(l["x0"])) for l in lines]


def reflow_ocr_lines(lines: list) -> str:
    """OCRの行（テキスト＋外接矩形）から、折り返しで割れた1文だけを座標で確実に連結する。
    lines: [{"text": str, "x0","x1","y0","y1": float(正規化, y0=上端 / y1=下端)}, ...]
    連結の条件:
      ・直前の“元の行”が列の右余白いっぱいまで達している（＝折り返された行）
      ・同じ列（左端が一致）で、行間が通常の1行ぶん以内（別ブロックの大きな空きではない）
      ・直前の行が文末記号(。！？…)で終わっていない
    右余白まで届いている＝折り返しと座標で確定しているので、境界が鉤括弧「」や読点、で
    終わっていても（文末記号でなければ）連結する。英単語同士だけ空白、他は詰める。
    見出し・箇条書き・別段落は「右端が短い」「行間が空く」ので自然に連結対象外になる。
    座標が使えるため、テキストだけの smart_join_wrapped と違い誤連結がほぼ起きない。
    2段組などの複数列レイアウトは、確信できる場合のみ列ごと（左→右）に処理して
    読み順を守る（_split_columns。確信できなければ従来どおり単一列扱い）。"""
    items = [l for l in lines if str(l.get("text", "")).strip()]
    if not items:
        return ""
    h = _ocr_median_height(items)

    out = []
    for col in _split_columns(items):
        col = sorted(col, key=lambda l: (round(float(l["y0"]), 4),
                                         float(l["x0"])))
        # 1) 縦に連続し同じ列の行を「ブロック（段落候補）」にまとめる。
        blocks = _group_ocr_blocks(col, h)

        # 2) 各ブロック内で「右余白いっぱいまで達した行＝折り返し」の連続を連結する。
        for block in blocks:
            margin = max(float(l["x1"]) for l in block)

            def _full(line):
                return (margin >= _REFLOW_MIN_MARGIN
                        and float(line["x1"]) >= margin - _REFLOW_MARGIN_TOL)

            merged = [str(block[0]["text"]).strip()]
            prev_full = _full(block[0])
            for line in block[1:]:
                t = str(line["text"]).strip()
                pa = merged[-1][-1] if merged[-1] else ""
                if prev_full and pa and pa not in _SENT_ENDERS:
                    # 折り返しは座標で確定済み。英単語同士だけ空白、他（日本語・記号）は詰める。
                    sep = " " if (pa.isascii() and pa.isalnum()
                                  and t[0].isascii() and t[0].isalnum()) else ""
                    merged[-1] += sep + t
                else:
                    merged.append(t)
                prev_full = _full(line)
            out.extend(merged)
    return "\n".join(out)


# ============================================================
#  映像内オーバーレイ・ラベル行の除去（strip_overlay_labels）
# ============================================================
# ニュース画面のOCRには、局ロゴ・番組名・日時・カテゴリ表示（例「MBSニュース」「国内」）など
# “記事本文でない短い日本語ラベル”が混じる。これらは正しい日本語なので、テキストだけでは
# 本文中の短語（本文に出る「国内」等）と区別できない＝denoise では消せない。そこでOCRの
# 行座標・文字サイズを使い、本文から外れた孤立ラベル行“だけ”を保守的に除く。
# 最優先原則: 本文の取りこぼしは厳禁（迷ったら残す）。単一条件では絶対に落とさない。
#
# しきい値の根拠（実画像=MBSニュースの Apple Vision 実測で調整。本文中央値 H=0.035 に対し
# ロゴ=0.57H・カテゴリ=0.67H・日時=0.72H と、本文（0.95〜1.09H）と明確に分かれた）:
#   ・行高が本文中央値 H に対し 0.75H 未満 / 1.40H 超 → 異常サイズ（小ロゴ/カテゴリ・大ロゴ）。
#     本文の行高ばらつき（±約10%）は含めない安全な境目。
_LABEL_SMALL_H = 0.75
_LABEL_LARGE_H = 1.40
#   ・画面最上部（上位15%）はロゴ・日時・カテゴリの定位置。ここにある短い行はラベル候補。
_LABEL_HEADER_Y = 0.15
#   ・“短い行”＝ラベル長の上限（文字数）。実測ラベルは 国内=2, MBSニュース≈6〜10, TBSテレビ≈10。
#     記事の本文・見出しはこれより長い。この長さ以下の行だけを位置/孤立シグナルの対象にする。
_LABEL_SHORT_CHARS = 10
#   ・これ以上の長さの行は“ラベルではない本文/見出し”として無条件に残す（保護）。日時ラベルも
#     ここで保護され、日付だけの行は後段の denoise が確実に落とす（役割分担）。
_LABEL_KEEP_CHARS = 16
#   ・孤立ラベルは本文の右端（本文ブロックの最大x1）にこの差以上届かない＝全幅の本文行ではない。
_LABEL_MARGIN_TOL = 0.10
#   ・ラベルと判定するのに必要な“シグナル数”。単一条件では落とさない（誤除去防止の要）。
#     位置・孤立の両シグナルは「短い行」を含意するので、2つ以上満たす行は必ず短い。
_LABEL_MIN_SIGNALS = 2
#   ・本文ブロック（複数行の段落）が成立しない少数行の入力ではラベル判定をしない（安全側）。
_LABEL_MIN_LINES = 4

# 本文シグナル（文末記号・鉤括弧）。これを含む行は文/引用＝本文とみなし必ず残す。
# ラベル（国内/MBSニュース/国際/日時）はこれらを含まないので保護対象を汚さない。
_LABEL_PROTECT_PUNCT = "。．！？!?「」『』"


def strip_overlay_labels(lines: list) -> list:
    """OCRの行（テキスト＋外接矩形）から、記事本文でない“映像内オーバーレイ・ラベル行”
    （局ロゴ・番組名・日時・カテゴリ表示 例「MBSニュース」「国内」）だけを座標で保守的に除く。
    入力/出力とも [{"text","x0","x1","y0","y1"}] のリスト（reflow_ocr_lines の前段で使う）。

    最優先原則は本文の取りこぼし厳禁（迷ったら残す）＝単一条件では絶対に落とさない。
    本文＝複数行が縦に連なる段落ブロック。そこから外れた行のうち、次の3シグナルを
    2つ以上（_LABEL_MIN_SIGNALS）満たす行“だけ”を落とす:
      A. 文字サイズ（行高）が本文の中央値と大きく違う（小さいロゴ/カテゴリ・大きい局ロゴ）
      B. 画面最上部（ヘッダ域）にある短い行（ロゴ・日時・カテゴリの定位置）
      C. 1行だけのブロックで本文の右端に届かない短い行（本文の折り返しではない孤立行）
    次のいずれかに当てはまる行は本文とみなし“必ず”残す（保護）:
      ・文末記号や鉤括弧（。！？「」）を含む＝文/引用
      ・複数行ブロックの一部＝段落本文
      ・一定長以上（_LABEL_KEEP_CHARS）＝見出し/本文
    本文ブロックが立たない少数行の入力では何もしない（安全側で全て残す）。

    既知の限界: “最上部・小・孤立・ごく短い”行は、カテゴリ表示（国内/国際）と、稀にある
    ごく短い見出し（例「第一章」「速報」）とを座標だけでは区別できない。この位置・形の行は
    ラベルとして落とし得る（本タスクの狙いは前者の除去なので許容範囲）。段落本文・句読点を
    含む行・一定長以上の見出しは常に保護され、記事本文の取りこぼしは起きない。"""
    items = [l for l in lines if str(l.get("text", "")).strip()]
    if len(items) < _LABEL_MIN_LINES:
        return lines
    items = sorted(items, key=lambda l: (round(float(l["y0"]), 4), float(l["x0"])))
    h = _ocr_median_height(items)
    blocks = _group_ocr_blocks(items, h)
    block_len_of = {id(l): len(b) for b in blocks for l in b}

    # 本文＝複数行ブロックの行。ここから本文の行高中央値 H と右端 body_right を測る。
    body = [l for b in blocks if len(b) >= 2 for l in b]
    if not body:
        return lines            # 段落が立たない＝本文を特定できない。安全側で全て残す。
    body_h = _ocr_median_height(body)
    body_right = max(float(l["x1"]) for l in body)

    keep = []
    for l in items:
        text = str(l["text"]).strip()
        block_len = block_len_of[id(l)]
        # 日時・数値・記号・ハンドルの“厳密ノイズ”（denoise と同じ判定）。本文ではないので
        # 下の長さ保護からは外し、位置/サイズが伴えばラベルとして落とせるようにする。
        strict_noise = _denoise_is_strict_noise(text)
        # --- 保護（本文シグナル）: どれか1つでも該当すれば必ず残す ---
        #   句読点/鉤括弧を含む＝文・引用 / 複数行ブロックの一部＝段落本文 / 一定長以上＝本文。
        #   ただし“厳密ノイズ”は長さがあっても本文ではないため長さ保護の対象にしない。
        if (any(ch in text for ch in _LABEL_PROTECT_PUNCT)
                or block_len >= 2
                or (len(text) >= _LABEL_KEEP_CHARS and not strict_noise)):
            keep.append(l)
            continue
        # --- ラベル・シグナル（2つ以上満たせば落とす）---
        # “ラベルらしい中身”＝短い行、または日時/数値/記号/ハンドルの厳密ノイズ。位置(B)と
        # 孤立(C)のシグナルはこの条件を伴わせることで、長い本文行が位置だけで落ちるのを防ぐ。
        labely = len(text) <= _LABEL_SHORT_CHARS or strict_noise
        lh = _ocr_line_height(l)
        sig_size = lh < _LABEL_SMALL_H * body_h or lh > _LABEL_LARGE_H * body_h
        sig_header = float(l["y0"]) <= _LABEL_HEADER_Y and labely
        sig_isolated = (block_len == 1 and labely
                        and float(l["x1"]) < body_right - _LABEL_MARGIN_TOL)
        if sig_size + sig_header + sig_isolated >= _LABEL_MIN_SIGNALS:
            continue            # ラベル行 → 落とす
        keep.append(l)
    return keep


# 文分割で「内側では区切らない」括弧の対。半角()も含める（normalize_ascii で
# （）→() に変換された後に split_sentences が走るため）。深さ上限は、OCRで開き括弧が
# 誤検出されたときに以降の文がまとまり続けるのを防ぐ暴走ガード。
_SENT_OPENERS = "「『（(【"
_SENT_CLOSERS = "」』）)】"
_SENT_DEPTH_MAX = 3


def split_sentences(text: str) -> list:
    """文末記号で文を分割し、1文1要素のリストを返す（記号は保持）。
    鉤括弧・丸括弧の内側では区切らない（「もう帰る。」と彼は言った。→1文のまま。
    途中で切るとVOICEVOXの合成単位・ポーズ・SRT字幕が不自然になるため）。
    文末記号の直後に続く閉じ括弧・連続する文末記号（えっ！？）は同じ文に取り込む。
    改行は常に文の区切りで、括弧の深さもリセットする（OCRで括弧が欠けても
    行を跨いで巻き込まない安全弁）。"""
    sentences = []
    buf = ""
    depth = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
            depth = 0
            i += 1
            continue
        buf += ch
        if ch in _SENT_OPENERS:
            depth = min(depth + 1, _SENT_DEPTH_MAX)
        elif ch in _SENT_CLOSERS:
            depth = max(depth - 1, 0)   # 【なし】等の非対称でも負にしない
        elif ch in _SENT_ENDERS and depth == 0:
            # 直後の閉じ括弧（A。」B の迷い込み）・連続する文末記号（！？）を取り込む
            while i + 1 < n and text[i + 1] in _SENT_ENDERS + _SENT_CLOSERS:
                i += 1
                buf += text[i]
            sentences.append(buf.strip())
            buf = ""
        i += 1
    if buf.strip():
        sentences.append(buf.strip())
    return [s for s in sentences if s]


# ============================================================
#  OCRの紛らわしい文字の文脈補正（fix_ocr_confusables）
# ============================================================
# 日本語OCR（特に言語補正の弱い Windows.Media.Ocr）は、字形がほぼ同一の
# 「漢字⇄カタカナ」（力/カ・口/ロ・工/エ・二/ニ・卜/ト・夕/タ・一/ー）や
# 「英字⇄数字」（O/0・l/1）を取り違える。VOICEVOX は誤字をそのまま読む
# （例:「口ボット」→“くちボット”）ため読み上げ品質に直結する。
# 方針は denoise と同じく保守的＝前後の文字種で確定できるときだけ直す（迷ったら触らない）。
# 適用は extract_files(fix_confusables=True) で OCR 由来テキストに限る（txt/docx/EPUB の
# テキスト層には適用しない）。OCR特有の文字間空白（例「サ 一 ビス」）に耐えるよう、
# 前後の文字は空白を読み飛ばして参照する（改行はまたがない）。

# カタカナ→漢字変換をしない「直後の漢字」の例外。
# カ: 「数カ月」「三カ所」「百カ日」等の助数詞表記 / ハ: 「ハ長調」「嬰ハ短調」等の調性
_CONF_KATA_GUARD_NEXT = {"カ": set("月所国年条村寺載日"), "ハ": set("長短")}
# ハ→八 は隣が漢数字（十ハ番・二十ハ歳）のときだけ。文語カタカナ文の係助詞ハ
# （「吾輩ハ猫デアル」）が「漢字+ハ+漢字」の形で頻出するため、数値文脈に限定する。
_CONF_KANJI_NUM = set("一二三四五六七八九十百千万億兆〇")
# 「誰か」「何か」の“か”をOCRがカタカナ「カ」に誤認したケース。力に変えると悪化するため
# 直前がこれらの漢字なら カタカナ→漢字 変換をしない。
_CONF_PREV_GUARD = set("誰何幾数僅")
# 「口」「二」は語頭・ひらがな直後でもカタカナ語との実在複合語が多い
# （口コミ・口パク・入り口ドア・二ヶ月・二チーム・二コマ…）。この2字の語頭変換は、
# 実在語では後続し得ない「小書きカナ・長音」が直後のときだけに絞る
# （口ッカー→ロッカー・二ュース→ニュース は直り、口パク・二チーム は温存）。
_CONF_HEAD_STRICT = set("口二")
_CONF_HEAD_STRICT_NEXT = set("ァィゥェォッャュョー")
# 「一→ー」「カタカナ→漢字」変換で“文末に相当”とみなす直後の文字。
_CONF_TRAIL_PUNCT = set("。、．，！？!?・…」』）)】")

_CONF_KANJI_TO_KATA = {"力": "カ", "口": "ロ", "工": "エ", "二": "ニ",
                       "卜": "ト", "夕": "タ"}
# 逆方向は 卜（漢字として稀）と 夕/タ の対を外した確実な組のみ。
# ハ→八 は「十ハ番→十八番」等の漢字挟みだけで効く（八→ハ は「八ミリ」「八ッ橋」等の
# 実在語が多すぎるため対象にしない）。
_CONF_KATA_TO_KANJI = {"カ": "力", "ロ": "口", "エ": "工", "ニ": "二", "ハ": "八"}
# 数字トークン内の英字誤認（2O26→2026・1l時→11時）。対象文字以外がすべて数字の
# トークンだけ変換し、H2O・500ml のような型番・単位は保護する。
_CONF_NUM_TOKEN = re.compile(r"[0-9A-Za-z]+")
_CONF_NUM_ONLY = re.compile(r"[0-9OolI]+")
# 英字が先頭に固まる形（O157・O2・l0）は実在の型番・記号名なので変換しない。
# 2O26（数字に挟まれる）・1l（数字が先行）はOCR誤認として補正対象のまま。
_CONF_LEADING_ALPHA = re.compile(r"[OolI]+[0-9]+")
_CONF_DIGIT_MAP = str.maketrans("OolI", "0011")


def _conf_is_kata(ch: str) -> bool:
    """カタカナ（長音「ー」・半角カナ含む）か。"""
    if not ch:
        return False
    o = ord(ch)
    return 0x30A1 <= o <= 0x30FE or 0xFF66 <= o <= 0xFF9D


def _conf_is_kanji(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch)
    return (0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF
            or 0xF900 <= o <= 0xFAFF or ch in "々〆")


def _conf_is_hira(ch: str) -> bool:
    return bool(ch) and 0x3041 <= ord(ch) <= 0x309F


def _conf_neighbor(text: str, i: int, step: int) -> str:
    """空白（半角/タブ/全角）を読み飛ばした隣の実文字。行頭・行末は ""。"""
    j = i + step
    while 0 <= j < len(text) and text[j] in " \t　":
        j += step
    if 0 <= j < len(text) and text[j] != "\n":
        return text[j]
    return ""


# 行の頭（や、かぎ括弧・句読点のすぐあと）の「ー」を「一」に直すときの、すぐあとの字。
# のばす音の印は、かなのあとにしか来ない（行の頭には置かない決まりもある）ので、ここに
# ある「ー」は漢数字の「一」か線（―）のどちらか。すぐあとが、数える言葉や「一」で
# 始まるよくある言葉のときだけ「一」と見る（ー人 → 一人・ー番 → 一番・ーページ → 一ページ）。
# 強い読み取り（PP-OCR）は、はっきりした字でも行の頭の「一ページ目」を「ーページ目」と
# 読んだ（2026-11-13 に実測。游ゴシック・游明朝・MS明朝・メイリオの4つとも）。
# 線（―・――）のほうは、読み取りが「一」と読むか消すかで、「ー」と読んだのは「――一人」を
# まとめて「ー人」と読んだ1回だけだった（このときは「一人」に直すほうが元に近い）。
# それでも、線のあとに来やすい動きの言葉の頭（―言った・―見ろ・―気がつくと）は入れない。
# 入っているのは、数える言葉と、「一」で始まる名詞によくある字だけ。
_CONF_ICHI_NEXT_KANJI = set(
    "人日月年回個本枚冊台番度章話巻部匹頭羽階位点件歳才時分秒円万億千百十兆杯軒通着足組"
    "品種行列段節項条号週倍割例名社校曲句語字画泊食口票戦勝敗球歩面場幕編代席隻艘基株箱"
    "袋缶皿膳坪畳錠包首色周局家族同緒般体部覧連括貫斉旦層瞬帯様端角期区昨")
_CONF_ICHI_NEXT_KATA = (
    "ページ", "キロ", "メートル", "センチ", "ミリ", "グラム", "リットル", "パーセント", "ドル",
    "ユーロ", "カ月", "ヵ月", "ヶ月", "ケ月", "カ所", "ヵ所", "ヶ所", "カ国", "ヵ国", "ヶ国",
    "カ年", "ヶ年", "コマ", "セット", "ケース", "パック", "ダース", "チーム", "クラス", "ステップ",
    "ポイント", "インチ", "フィート", "マイル", "トン", "ヘクタール", "ワット", "バイト",
    "ピクセル", "フレーム", "シーズン", "ラウンド", "ゲーム", "イニング", "ホール", "グループ",
    "ブロック")
# 行の頭と同じに扱う、すぐ前の字（ここから文や言葉が始まる）
_CONF_ICHI_HEAD_PREV = set("「『（(【〈《、。．，")
_CONF_SPACES = str.maketrans("", "", " \t　\r")


def _conf_ichi_for_choonpu(text: str, i: int) -> bool:
    """text[i] の「ー」が、本当は漢数字の「一」か（_CONF_ICHI_NEXT_KANJI の説明）。
    すぐあとが数える言葉などのときだけ True。
      ・行の頭、または かぎ括弧・句読点のすぐあと（ーページ目 → 一ページ目・「ー人で → 「一人で）
      ・漢字のすぐあとで、カタカナの数える言葉が続く（第ーページ → 第一ページ）
    前後の空白は読み飛ばす（Windows の読み取りは「ー キ ロ 先」のように字のあいだを空ける）。
    行の頭でも、前の行がカタカナで終わっていれば触らない（禁則の無い画面で、のばす音が
    次の行へ折り返した形かもしれない: コンピュ／ーター）。"""
    end = text.find("\n", i + 1)
    after = text[i + 1:] if end < 0 else text[i + 1:end]
    after = after.translate(_CONF_SPACES)
    j = i - 1
    while j >= 0 and text[j] in " \t　\r":
        j -= 1
    if j >= 0 and text[j] != "\n":
        if text[j] not in _CONF_ICHI_HEAD_PREV:
            return _conf_is_kanji(text[j]) and after.startswith(_CONF_ICHI_NEXT_KATA)
    else:
        k = j - 1
        while k >= 0 and text[k] in " \t　\r":
            k -= 1
        if k >= 0 and _conf_is_kata(text[k]):
            return False
    return (after[:1] == "つ" or after[:1] in _CONF_ICHI_NEXT_KANJI
            or after.startswith(_CONF_ICHI_NEXT_KATA))


def _conf_pass_choonpu(text: str) -> str:
    """第1パス: 一⇄ー の補正（判定は“この段階のテキスト”の近傍）。
      1. カタカナ直後の「一」で、直後もカタカナ/長音/行末/句読点 → 長音「ー」
         （サ一ビス→サービス。直後がひらがな・漢字なら「メロン一つ」型なので触らない）
      2. 漢字に挟まれた「ー」 → 漢数字「一」（第ー章→第一章。長音は直前カナ以外に現れない）
      3. 行の頭などの「ー」で、すぐあとが数える言葉など → 漢数字「一」
         （ーページ目→一ページ目。_conf_ichi_for_choonpu）"""
    out = list(text)
    for i, ch in enumerate(text):
        if ch == "一":
            prev = _conf_neighbor(text, i, -1)
            nxt = _conf_neighbor(text, i, +1)
            if _conf_is_kata(prev) and (_conf_is_kata(nxt) or nxt == ""
                                        or nxt in _CONF_TRAIL_PUNCT):
                out[i] = "ー"
        elif ch == "ー":
            prev = _conf_neighbor(text, i, -1)
            nxt = _conf_neighbor(text, i, +1)
            if _conf_is_kanji(prev) and _conf_is_kanji(nxt):
                out[i] = "一"
            elif _conf_ichi_for_choonpu(text, i):
                out[i] = "一"
    return "".join(out)


def _conf_pass_kana_kanji(text: str) -> str:
    """第2パス: 漢字⇄カタカナ の補正。一⇄ーの補正“後”のテキストで判定することで、
    「顧客ニ一ズ」の「一」を漢字と誤解して正しい「ニ」を壊すような矛盾を防ぐ。
      3. カタカナに挟まれた同形漢字 力口工二卜夕 → カタカナ（デジ夕ル→デジタル）。
         語頭（直前が日本語・英数でない/ひらがな）でも直後カタカナなら適用（卜ヨタ→トヨタ）。
         ただし「口」「二」の語頭変換は直後が小書きカナ・長音のときだけ
         （口コミ・口パク・二ヶ月・二チーム等の実在複合語を壊さない）。
         直後の「ヶヵ」は助数詞マーカーなので常に変換しない。
      4. 漢字に挟まれたカタカナ カロエニ → 同形漢字（入カ完了→入力完了・第ニ章→第二章）。
         直後がかな・英数・長音のときは「誰カ」「入カした」型の判別がつかないため触らない。
         「数カ月」「百カ日」等の助数詞・「誰カ」等の直前語はガードで保護。"""
    out = list(text)
    for i, ch in enumerate(text):
        if ch in _CONF_KANJI_TO_KATA:
            prev = _conf_neighbor(text, i, -1)
            nxt = _conf_neighbor(text, i, +1)
            if not _conf_is_kata(nxt) or nxt in "ヶヵ":
                continue
            # 前が漢字・英数字なら熟語や型番の一部（山口ロープウェイ等）なので触らない。
            # 前がカタカナ＝語中（デジ夕ル）、前がひらがな・句読点・行頭＝語頭
            # （卜ヨタ・と二ュース）として適用する
            if (_conf_is_kanji(prev)
                    or (prev.isalnum() and not _conf_is_kata(prev)
                        and not _conf_is_hira(prev))):
                continue
            if (not _conf_is_kata(prev) and ch in _CONF_HEAD_STRICT
                    and nxt not in _CONF_HEAD_STRICT_NEXT):
                continue
            out[i] = _CONF_KANJI_TO_KATA[ch]
        elif ch in _CONF_KATA_TO_KANJI:
            prev = _conf_neighbor(text, i, -1)
            nxt = _conf_neighbor(text, i, +1)
            if (_conf_is_kanji(prev) and prev not in _CONF_PREV_GUARD
                    and (_conf_is_kanji(nxt) or nxt == ""
                         or nxt in _CONF_TRAIL_PUNCT)
                    and nxt not in _CONF_KATA_GUARD_NEXT.get(ch, ())
                    and not (ch == "ハ" and prev not in _CONF_KANJI_NUM
                             and nxt not in _CONF_KANJI_NUM)):
                out[i] = _CONF_KATA_TO_KANJI[ch]
    return "".join(out)


# Windows の読み取りは、日付や時刻の字のあいだにも空白を入れる（「2026 / 1 1 / 13 18 : 07」）。
# 空白のままだと日付・時刻と分からず「イチ、イチ、…ゼロナナ」とばらばらに読まれた
# （2026-11-14 に実測）。数字にはさまれた / と : のまわりと、/ と / のあいだ・: の前後で
# 2つに割れた数字だけをつなぐ。読み取った文字にだけ使う（打った文の空白には触らない）
_CONF_DT_SEP = re.compile(r"(?<=\d)[ \t　]*([/:])[ \t　]*(?=\d)")
_CONF_DT_SPLIT = (re.compile(r"(?<=/)(\d) (\d)(?=/)"),           # 2026/1 1/13
                  re.compile(r"(?<![\d/:.])(\d) (\d)(?=:\d)"),    # 1 8:07
                  re.compile(r"(?<=\d:)(\d) (\d)(?![\d:/])"))     # 18:0 7


def _conf_join_datetime(text: str) -> str:
    text = _CONF_DT_SEP.sub(r"\1", text)
    for rx in _CONF_DT_SPLIT:
        text = rx.sub(r"\1\2", text)
    return text


def fix_ocr_confusables(text: str) -> str:
    """OCRが取り違えやすい同形文字を、前後の文字種で確定できる場合だけ補正する。
    2パス構成: 先に 一⇄ー（_conf_pass_choonpu）、その結果に対して 漢字⇄カタカナ
    （_conf_pass_kana_kanji）。「診察カ一ド」のような複合誤認で、後段が「一」を
    漢字と誤解して正しい「カ」を壊さないための順序依存（意図した連鎖）。
    最後に数字トークン内の O/o→0・l/I→1（2O26年→2026年）。対象文字以外がすべて
    数字のトークンだけ変換し、さらに「英字が先頭に固まる形」（O157・O2・l0）は
    実在の型番・記号として保護する（H2O・500ml はトークン条件で保護される）。
    既知の限界: 「ピカ一。」（ぴかいち）等ごく稀な語は誤補正し得る。「入カした」
    「口ボット」のような、かな が続く・実在語と衝突し得る形は安全側に倒して補正しない。
    半角英数のみ対象（全角は normalize_ascii 適用後なら半角になっている）。"""
    fixed = _conf_join_datetime(_conf_pass_kana_kanji(_conf_pass_choonpu(text)))

    def _fix_token(m):
        tok = m.group(0)
        if (_CONF_NUM_ONLY.fullmatch(tok) and any(c.isdigit() for c in tok)
                and not _CONF_LEADING_ALPHA.fullmatch(tok)):
            return tok.translate(_CONF_DIGIT_MAP)
        return tok
    return _CONF_NUM_TOKEN.sub(_fix_token, fixed)


# ============================================================
#  画面キャプチャのノイズ除去（denoise_capture）
# ============================================================
# ニュース番組やSNS埋め込みを含む「画面キャプチャ」のOCR結果には、記事本文以外の
# “映像内オーバーレイ文字”（局ロゴ・時刻・SNSハンドル・矢印など）が混ざる。これらを
# 1行単位で落とす。方針は「高精度・低誤削除」＝迷ったら残す。本文の取りこぼしは厳禁。
#
# しきい値（根拠つき）:
#   ・行のCJK比率＝その行の非空白文字のうち日本語・全角系(is_cjk)が占める割合。
#     普通の日本語文は英単語や数字が混ざっても 0.5 を大きく超える。0.30 未満なら
#     「実質ほぼ非日本語」とみなす保守的な下限。
_DENOISE_LINE_CJK_MIN = 0.30
#   ・短い英字断片(ルールA)を除去してよいのは「日本語主体の文書」だけ。
#     判定は “比率が高い” か “日本語が一定量ある” のどちらか。後者を入れるのは、
#     ニュース番組にSNSの英文ツイートが長々と埋め込まれると比率だけでは日本語が
#     少数派に見えてしまうため。見出し＋本文ぶんの日本語があれば日本語文書とみなす。
#     英語主体（日本語ほぼ皆無）の入力は両条件とも満たさず、英字行の全消しを防ぐ。
_DENOISE_DOC_CJK_MIN = 0.50
_DENOISE_DOC_CJK_MIN_CHARS = 20  # 見出し1本ぶん程度の日本語量
# 行の日本語スクリプト比率がこれ未満なら「外国語ブロック/英字断片」とみなして落とす。
# ひらがな・句読点を含む本文はこの判定より前（ルール1）で保護されるため、ここに来るのは
# 助詞も句点も無いラベル/英文。0.20 は『20%減』(0.25)『iOS版』(0.25)を残し、英文(≈0)を落とす境目。
_DENOISE_LINE_JP_MIN = 0.20

# 本文を示す句読点・鉤括弧（全角）。これを含む行はノイズ判定に触れても必ず残す。
_DENOISE_PROTECT_PUNCT = "。、！？「」『』"

# 標準の日本語ノイズ除去対象になりやすい記号（矢印・囲み・箇条書き記号・区切り）。
# 非空白文字がすべてこの集合なら「記号だけの行」として落とす。
_DENOISE_SYMBOLS = set("←→↑↓⇒⇐▶◀▲▼△▽■□●○◆◇★☆♦♢•‣·・…‥※｜|/\\＿_—–―ー－~〜＞＜<>»«‹›【】〔〕")

# 単独のタイムスタンプ／日付だけの行（行“全体”がこれらのトークンと区切りで埋まる）。
# 文中に日付が現れる本文（例:「2026年7月14日に表明した。」）は助詞や句読点が残るため
# フルマッチせず、この判定には掛からない（さらに保護ルールでも守られる）。
_DENOISE_DATE_TOKEN = (
    r"\d{4}年|\d{1,2}月|\d{1,2}日|"          # 和暦表記の年月日
    r"\d{1,2}[:：]\d{2}(?:[:：]\d{2})?|"      # 時刻 HH:MM(:SS)
    r"\d{1,4}/\d{1,2}(?:/\d{1,4})?|"          # スラッシュ日付 M/D・Y/M/D
    r"[（(][日月火水木金土](?:曜日?)?[)）]|"  # 曜日 （火）
    r"午前|午後"
)
# 日付トークン単体と、トークン間の区切り。行“全体”が日付だけかは、これらを
# 左から貪欲に食べていく _denoise_is_date_only() で線形時間に判定する。
# ※ ^(?:token|sep)+$ を1本のregexで書くと、スラッシュ日付トークンが数字列を
#   指数的に分割し得て破滅的バックトラック（ReDoS）になるため、そうはしない。
_DENOISE_DATE_TOKEN_RE = re.compile(_DENOISE_DATE_TOKEN)
_DENOISE_DATE_SEP_RE = re.compile(r"[\s・,，.．\-]+")

# 単独の数値・カウント行（例: 1.04 / 1.2万 / 1,234件 / 1.2万人）。位取り＋単位の
# 2連接尾辞（万人・万回等、SNSの視聴数オーバーレイ）まで許容。「1.2万人が視聴」の
# ような文はひらがな保護（判定1）で先に残るため掛からない。※「円」は価格表の本文に
# なり得るため含めない。
_DENOISE_NUM_LINE = re.compile(
    r"^\s*[+\-]?\d+(?:[.,]\d+)*\s*(?:万|億|千|兆|K|k|M)?(?:人|件|回|票|位|%|％)?\s*$")

# 写真・映像クレジット行（例「写真：ロイター」「撮影＝共同」）。ひらがなを含む文
# （「写真は田中さんの提供です」）はここでは判定せず必ず残す（自己完結ガード）。
_DENOISE_CREDIT_RE = re.compile(
    r"^[（(]?(?:写真|画像|映像|資料|提供|撮影|出典|引用)[:：=＝]")

# 日本語記事中の英数見出し行（製品名・型番。例「iPhone 17 Pro Max」「Nintendo Switch 2」）。
# 英字始まりで英字と数字の両方を含む短い行だけを保護し、「THE」「NEWS」等の英字断片・
# 「2026 07 14」等の数字断片・「1.2K views」等の数字始まりオーバーレイは従来どおり落とす。
_DENOISE_PRODUCT_RE = re.compile(r"[A-Za-z0-9 .\-']+")
# 英語SNS/動画オーバーレイの定型（視聴数・経過時間・英語日付）。製品名保護の対象外にする。
_DENOISE_SNS_OVERLAY_RE = re.compile(
    r"\d[\d.,]*\s*[KkMmBb]?\s*(?:views?|likes?|subscribers?|followers?|replies|"
    r"reposts?|comments?|votes?|shares?)\b"
    r"|\b(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b"
    r"|^[A-Za-z]{3}\.?\s+\d{1,2},?\s+\d{4}$",
    re.IGNORECASE)


def _denoise_cjk_ratio(s: str) -> float:
    """行の非空白文字に占めるCJK文字の割合（0〜1）。空文字なら0。"""
    chars = [c for c in s if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if is_cjk(c)) / len(chars)


def _denoise_has_hiragana(s: str) -> bool:
    """ひらがな（助詞・送り仮名）を含むか。日本語“文”であることの強い目印。
    タイムスタンプ・数値・ハンドル・矢印・英字断片にはひらがなが無いため、
    『日付だけの行』と『文中に日付を含む本文』を安全に切り分けられる。"""
    return any(0x3041 <= ord(c) <= 0x3096 for c in s)


def _denoise_jp_ratio(s: str) -> float:
    """行の非空白文字に占める『日本語の中身の文字』（かな・漢字）の割合（0〜1）。
    英字だけの断片や英文ブロックは0付近、日本語ラベル・見出しは高い。
    『20%減』『iOS版』のように数字・英字が多くても漢字を含む短い見出しは中程度で残る。"""
    chars = [c for c in s if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if is_jp_script_char(c)) / len(chars)


def _denoise_symbol_only(s: str) -> bool:
    """非空白文字がすべて記号集合なら True（矢印だけ・囲みだけの行）。"""
    core_chars = [c for c in s if not c.isspace()]
    return bool(core_chars) and all(c in _DENOISE_SYMBOLS for c in core_chars)


def _denoise_is_date_only(s: str) -> bool:
    """行“全体”が日付／時刻トークンと区切りだけで構成されるか（線形時間）。
    左から貪欲にトークンor区切りを食べ、余りが出ずトークンを1つ以上含めば True。
    途中に助詞や本文が残ればフルには食べきれず False（＝本文として残す・安全側）。"""
    i, n = 0, len(s)
    matched_token = False
    while i < n:
        m = _DENOISE_DATE_SEP_RE.match(s, i)
        if m:
            i = m.end()
            continue
        m = _DENOISE_DATE_TOKEN_RE.match(s, i)
        if m and m.end() > i:
            i = m.end()
            matched_token = True
            continue
        return False  # トークンでも区切りでもない文字 → 日付だけの行ではない
    return matched_token


def _denoise_has_strong_jp(s: str) -> bool:
    """本文であることの強いシグナル（句読点・鉤括弧・ひらがな）を含むか。
    タイムスタンプ・数値・ハンドル・矢印・英字断片には現れないため、
    『日付だけの行』と『文中に日付を含む本文』を確実に切り分けられる。"""
    if any(p in s for p in _DENOISE_PROTECT_PUNCT):
        return True
    return _denoise_has_hiragana(s)


def _denoise_is_strict_noise(s: str) -> bool:
    """行“全体”が特定のノイズ形（記号だけ／日付だけ／数値だけ／ハンドル）か。
    いずれも厳密一致なので本文には掛からない。長さ保護よりも優先する。"""
    if not s:
        return False
    # E: 記号だけの行（矢印・囲み等）
    if _denoise_symbol_only(s):
        return True
    # B: 単独のタイムスタンプ／日付だけの行
    if _denoise_is_date_only(s):
        return True
    # C: 単独の数値・カウント行
    if _DENOISE_NUM_LINE.fullmatch(s):
        return True
    # D: SNSハンドル行（先頭@／＠で、行が実質ASCII＝ハンドルそのもの）
    if s[0] in "@＠" and _denoise_cjk_ratio(s) < _DENOISE_LINE_CJK_MIN:
        return True
    # F: 写真・映像クレジット行（「写真：ロイター」）。ひらがなを含む本文は対象外
    #    （strip_overlay_labels からも呼ばれるため、ここで自己完結的に保護する）
    if (len(s) <= 20 and not _denoise_has_hiragana(s)
            and _DENOISE_CREDIT_RE.match(s)):
        return True
    return False


def denoise_capture(text: str) -> str:
    """画面キャプチャOCRから“映像内オーバーレイ文字”を1行単位で除去する。
    入力: 抽出直後の生テキスト（複数行）。出力: ノイズ行を落としたテキスト。
    記事本文は絶対に残す方針（保守的判定・迷ったら残す）。空行は構造として残し、
    後段の clean_text がまとめて処理する。

    1行ごとの判定順（上ほど優先）:
      1. 句読点・鉤括弧・ひらがなを含む → 本文シグナル。必ず残す。
      2. 記号だけ／日付だけ／数値だけ／ハンドルの厳密一致 → 落とす。
      3. 日本語の中身の文字（かな・漢字）を一切含まない行 → 落とす（文書が日本語主体のとき）。
         英字オーバーレイ（THE/NEWS）や埋め込みの英文ツイート等の外国語ブロックが対象。
         ※英語主体の入力は doc_is_jp が False になり、丸ごと消えることはない。
      4. それ以外 → 残す（迷ったら残す）。2〜3文字の日本語ラベル等はここで残る。
    """
    lines = text.split("\n")
    # 文書全体の日本語度（全行の非空白文字をまとめて評価）。英語主体の入力で
    # 英字行を誤って全消ししないための doc レベルの門番。
    all_chars = [c for ln in lines for c in ln if not c.isspace()]
    cjk_count = sum(1 for c in all_chars if is_cjk(c))
    doc_is_jp = bool(all_chars) and (
        cjk_count / len(all_chars) >= _DENOISE_DOC_CJK_MIN
        or cjk_count >= _DENOISE_DOC_CJK_MIN_CHARS)
    out = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append(ln)                       # 1'. 空行は残す（後段で整理）
            continue
        if _denoise_has_strong_jp(s):
            out.append(ln)                       # 1. 本文シグナル → 残す
            continue
        if _denoise_is_strict_noise(s):
            continue                             # 2. 厳密なノイズ → 落とす
        if (_DENOISE_PRODUCT_RE.fullmatch(s) and len(s) <= 30
                and len(s.split()) <= 5
                and s[:1].isalpha() and any(c.isdigit() for c in s)
                and not _DENOISE_SNS_OVERLAY_RE.search(s)):
            out.append(ln)                       # 2'. 英数の製品名・型番見出し → 残す
            continue
        if doc_is_jp and _denoise_jp_ratio(s) < _DENOISE_LINE_JP_MIN:
            continue                             # 3. 日本語がごく僅かな行（英字/外国語ブロック）→ 落とす
        out.append(ln)                           # 4. それ以外 → 残す
    return "\n".join(out)


def denoise_removed_lines(raw: str) -> list:
    """denoise_capture が除去する行の一覧を返す（整形レポート用）。
    denoise は行の削除しかしない（行の中身は編集しない）ため、原文と結果の
    2ポインタ走査で除去行が正確に求まる。"""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    kept = denoise_capture(text).split("\n")
    removed = []
    ki = 0
    for ln in text.split("\n"):
        if ki < len(kept) and kept[ki] == ln:
            ki += 1
        elif ln.strip():
            removed.append(ln)
    return removed


def clean_text(raw: str, mode: str = "sentence",
               remove_blank: bool = True, keep_ascii_spaces: bool = True,
               join_wrapped: bool = False, smart_join: bool = False,
               paren_ruby: bool = False, normalize: bool = False,
               denoise: bool = False, remove_urls: bool = False) -> str:
    """
    抽出生テキストをVOICEVOX向けに整形する。
    mode: "sentence" = 文ごとに改行（VOICEVOX推奨）/ "keep" = 元の改行を保持
    join_wrapped: True で改行をまたいだ文を積極的に連結（小説・段落向け）。
    smart_join: True で“折り返しで途切れた文”だけを賢く連結（見出し・リスト・別ブロックは尊重）。
                join_wrapped が True のときはそちら（積極連結）を優先する。
                いずれも既定Falseでは元の改行を文の区切りとして尊重する（後方互換）。
    paren_ruby: True で「漢字(かんじ)」型ルビを除去（Web小説向け）
    normalize: True で全角英数記号を半角に正規化し、囲み数字・組文字等の
               読み上げ困難な記号を読みに展開（①→1・㈱→株式会社・㎡→平方メートル）
    denoise: True で画面キャプチャの“映像内オーバーレイ文字”を前処理で除去
             （既定Falseでは従来の出力を1バイトも変えない）
    remove_urls: True でURL・メールアドレスを除去（読み上げると1文字ずつ読まれるため）
    """
    # 改行コード統一・全角スペース正規化の前処理
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if denoise:
        text = denoise_capture(text)
    if paren_ruby:
        text = strip_paren_ruby(text)
    if normalize:
        text = normalize_ascii(text)
        text = normalize_halfwidth_kana(text)
        text = expand_readable_chars(text)
        text = normalize_readings(text)
    if remove_urls:
        # normalize の後に適用する（全角URL ｈｔｔｐｓ：…も半角化後なら除去できる）
        text = strip_urls(text)
    # 連続する3つ以上の改行は2つに
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    # 文字間スペース除去。ただし章見出し行（第N章 タイトル）の区切り空白は保護する
    # （消すと detect_chapters が効かず、M4Bのチャプターが典型的な見出しで作れない）。
    # 行頭の字下げ（全角スペース）は段落の始まりの印。空行を消しても段落の間を長めに
    # 取れるよう（paragraph_ids）、下の連結・文分割を見えない印で通り抜け、最後に
    # 全角スペース1つに戻す
    def _tidy_line(ln):
        if is_chapter_heading(ln):
            return ln
        body = remove_cjk_spaces(ln, keep_ascii_spaces=keep_ascii_spaces).strip(" \t　")
        if body and ln.lstrip(" \t").startswith("　"):
            return _PARA_MARK + body
        return body
    text = "\n".join(_tidy_line(ln) for ln in text.split("\n"))

    if mode == "sentence":
        if join_wrapped:
            text = join_wrapped_lines(text)          # 小説向け：積極連結
        elif smart_join:
            text = smart_join_wrapped(text)          # 既定推奨：折り返しだけ賢く連結
        # split_sentences は改行も区切りとして扱うため、
        # join_wrapped/smart_join がFalseなら元の改行は保たれる
        if remove_blank:
            text = "\n".join(split_sentences(text))
        else:
            # 空行を残す指定では、段落（空行区切り）を保ったまま文分割する。
            # 従来は文分割が空行を全て潰し、「段落ごと」のまとめ方（GUI/CLIの
            # unit=para）が文ごとモードでは絶対に効かなかった
            paras = ["\n".join(split_sentences(p)) for p in text.split("\n\n")]
            text = "\n\n".join(p for p in paras if p)
    else:  # keep
        lines = [ln.rstrip() for ln in text.split("\n")]
        if remove_blank:
            lines = [ln for ln in lines if ln.strip()]
        text = "\n".join(lines)

    if remove_blank:
        lines = [ln for ln in text.split("\n") if ln.strip()]
        text = "\n".join(lines)

    return text.replace(_PARA_MARK, "　").strip()


# ============================================================
#  テキスト系ファイル（.txt / .docx / .epub）の読み込み
# ============================================================
# カクヨムの傍点（強調）記法《《…》》。中身は本文なので展開して残す（無条件の《…》
# 削除より先に処理しないと「《《大事》》」→「》」と本文が消えて閉じ括弧が残る）
_KAKUYOMU_EMPH = re.compile(r"《《([^《》]*)》》")
# ｜指定ルビ「｜北海道《ほっかいどう》」: ルビ範囲が明示されるので確実に読みだけ捨てる
_AOZORA_BAR_RUBY = re.compile(r"｜([^《》｜\n]*)《[^《》]*》")
# 「漢字」の文字クラス。基本面（一-鿿・拡張A）に加え、互換漢字（﨑=U+FA11等の人名）と
# 拡張B以降の非BMP漢字（𠮟=U+20B9F等）・繰り返し記号・〇 を含める。
# 取りこぼすと直後のルビが削除されず、VOICEVOXが読みを重ねて読んでしまう
_KANJI_CLS = "一-鿿㐀-䶿々〆ヶ〇豈-﫿\U00020000-\U0003134F"
# 漢字直後の《…》のみルビとして削除。青空文庫仕様ではルビは直前の漢字列か｜指定に
# 付くため、これで正規のルビは全て拾える。文頭・かな直後の《…》はWeb小説の
# スキル名・強調（「彼は《ファイアボール》を放った」）なので本文として温存する
_AOZORA_RUBY = re.compile(f"(?<=[{_KANJI_CLS}])《[^《》]*》")
_AOZORA_NOTE = re.compile(r"［＃[^］]*］")        # 入力者注（傍点・字下げ指定等）
_AOZORA_BAR = "｜"                                # ルビ範囲の開始記号
# 一の字点（ゝゞヽヾ）: 直前のかな1文字の繰り返し。ゞヾは繰り返しを濁音化。
# 直前がかなのときだけ展開する（文頭・記号直後のゝは複製しない）。
_ODORIJI_RE = re.compile(r"([ぁ-ゖァ-ヶ])([ゝゞヽヾ])")
# くの字点の横書き代用表記（／＼・／″＼）: 直前のかな2文字の繰り返し。″付きは先頭を濁音化。
# 直前2文字が両方かな かつ その前がかなでない（＝繰り返し単位が2文字と確定できる）
# ときだけ展開する。3文字以上の繰り返し（かはる／″＼等）を2文字で複製すると
# 実在しない語（かはるばる）を作ってしまうため、確定できない場合は原文のまま残す。
# アスキーアートの／＼（前が記号・漢字）もこの条件で自然に除外される。
# ／=U+FF0F ＼=U+FF3C。濁点の中間文字は青空文庫標準の″(U+2033)のほか゛/合成用濁点も許容。
_KUNOJI_RE = re.compile(
    r"(?<![ぁ-ゖァ-ヶー])([ぁ-ゖァ-ヶー][ぁ-ゖァ-ヶー])／([″゛゙])?＼")
_AOZORA_FOOTER_RE = re.compile(r"^底本：", re.MULTILINE)
_AOZORA_HR_RE = re.compile(r"-{10,}\s*")          # 記号説明ブロックの区切り線（行全体）


def _dakuten(ch: str) -> str:
    """かなを濁音化した1文字を返す（合成できない字はそのまま）。"""
    d = unicodedata.normalize("NFC", ch + "゙")
    return d if len(d) == 1 else ch


def _expand_odoriji(m):
    base, mark = m.group(1), m.group(2)
    if mark in "ゞヾ":
        return base + _dakuten(base)
    return base + base


def _expand_kunoji(m):
    pair, mark = m.group(1), m.group(2)
    if mark:
        return pair + _dakuten(pair[0]) + pair[1]
    return pair + pair


def _strip_aozora_header(text: str) -> str:
    """冒頭の記号説明ブロック（区切り線2本に挟まれた「テキスト中に現れる記号について」節）
    を除去する。誤爆防止のため、先頭50行以内に区切り線が2本そろい、かつブロック内に
    見出し文言を含むときだけ削る（Markdown風の水平線だけの本文は削らない）。"""
    lines = text.split("\n")
    hr = [i for i, ln in enumerate(lines[:50]) if _AOZORA_HR_RE.fullmatch(ln)]
    if len(hr) >= 2:
        s, e = hr[0], hr[1]
        block = "\n".join(lines[s:e + 1])
        if "テキスト中に現れる記号について" in block:
            del lines[s:e + 1]
            return "\n".join(lines)
    return text


# 底本フッターとして削除を許す末尾ブロックの上限サイズ。実際の底本情報は
# 10〜30行・1500字程度に収まる。これより長い「底本：以降」は後続の本文
# （複数作品の連結txt）を巻き込んでいる可能性が高いので削らない。
_AOZORA_FOOTER_MAX_CHARS = 2000


def _strip_aozora_footer(text: str) -> str:
    """末尾の底本情報（「底本：…」の行以降）を除去する。VOICEVOXがそのまま読み上げて
    しまうため。複数作品の連結txtで本文が消えないよう、(1) 最後の「底本：」行だけを
    対象にし、(2) テキスト後半にあり、(3) そこから末尾までが十分短い（＝奥付だけ）
    ときに限って削る。"""
    m = None
    for m in _AOZORA_FOOTER_RE.finditer(text):
        pass
    if (m and m.start() >= len(text) // 2
            and len(text) - m.start() <= _AOZORA_FOOTER_MAX_CHARS):
        return text[:m.start()].rstrip() + "\n"
    return text


def strip_aozora(text: str) -> str:
    """青空文庫・Web小説の注記類を除去・展開する:
    カクヨム傍点《《…》》の展開、ルビ（｜指定・漢字直後の《…》のみ）と
    入力者注［＃…］の除去、踊り字（こゝろ→こころ・つゞく→つづく）と
    くの字点（どき／＼→どきどき・しみ／″＼→しみじみ）の展開、冒頭の記号説明ブロックと
    末尾の底本情報の除去。ルビは文脈で限定するため、なろう/カクヨム系の
    「彼は《ファイアボール》を放った」型の本文《…》は消えない。"""
    text = _KAKUYOMU_EMPH.sub(r"\1", text)
    text = _AOZORA_BAR_RUBY.sub(r"\1", text)
    text = _AOZORA_RUBY.sub("", text)
    text = _AOZORA_NOTE.sub("", text)
    text = text.replace(_AOZORA_BAR, "")
    text = _ODORIJI_RE.sub(_expand_odoriji, text)
    text = _KUNOJI_RE.sub(_expand_kunoji, text)
    text = _strip_aozora_header(text)
    return _strip_aozora_footer(text)


# 「漢字(かんじ)」型ルビ: 漢字の直後の丸括弧内が すべて かな のときだけ除去する
# （漢字クラスは《》ルビと共通。互換漢字・拡張B以降も対象）
_PAREN_RUBY = re.compile(
    f"([{_KANJI_CLS}]+)[（(]([ぁ-ゖァ-ヶーゝゞヽヾ]+)[)）]")


def strip_paren_ruby(text: str) -> str:
    """Web小説等の「漢字(かんじ)」形式ルビを除去する（読みがな部分を捨てる）。
    括弧内にかな以外が混ざる場合（例: 補足(2023年)）は注釈とみなして残す。"""
    return _PAREN_RUBY.sub(r"\1", text)


# 全角英数記号(FF01-FF5E) → 半角(21-7E)。全角スペースは対象外（既存処理が扱う）
_Z2H = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}


# VOICEVOX(OpenJTalk)が読み飛ばし・誤読しやすい囲み数字・組文字・単位記号の読み。
# unicodedataのNFKCは「㈱→(株)」「℃→°C」となり読み上げ目的を果たさないため明示表で持つ。
# 数字系（丸数字・ローマ数字）は列挙で連続することがあり（①②③）、裸の数字に置換すると
# 「123」に連結されて誤読されるため、記号系のtranslateとは別に読点区切りで展開する。
def _build_readable_num_map():
    m = {}
    for i in range(20):                      # ①〜⑳
        m[chr(0x2460 + i)] = str(i + 1)
    for i in range(15):                      # ㉑〜㉟
        m[chr(0x3251 + i)] = str(i + 21)
    for i in range(15):                      # ㊱〜㊿
        m[chr(0x32B1 + i)] = str(i + 36)
    for i in range(12):                      # ローマ数字 Ⅰ〜Ⅻ / ⅰ〜ⅻ
        m[chr(0x2160 + i)] = str(i + 1)
        m[chr(0x2170 + i)] = str(i + 1)
    return m


_READABLE_NUM_MAP = _build_readable_num_map()
_READABLE_NUM_RE = re.compile(
    "[①-⑳㉑-㉟㊱-㊿Ⅰ-Ⅻⅰ-ⅻ]+")
_READABLE_SYM_MAP = {ord(ch): yomi for ch, yomi in {
    "㈱": "株式会社", "㈲": "有限会社", "㍿": "株式会社",
    "㎜": "ミリメートル", "㎝": "センチメートル", "㍍": "メートル",
    "㎞": "キロメートル", "㎎": "ミリグラム", "㎏": "キログラム",
    "㎖": "ミリリットル", "㍑": "リットル",
    "㎠": "平方センチメートル", "㎡": "平方メートル", "㎢": "平方キロメートル",
    "㎥": "立方メートル", "℃": "度", "№": "ナンバー", "〒": "郵便番号",
    # 組文字の単位・元号（OCR結果・古い文書に現れ、VOICEVOXは読み飛ばす）
    "㌔": "キロ", "㌢": "センチ", "㍉": "ミリ", "㌘": "グラム",
    "㌧": "トン", "㌫": "パーセント", "㌍": "カロリー", "㌦": "ドル",
    "㍗": "ワット", "㍾": "明治", "㍽": "大正", "㍼": "昭和", "㍻": "平成",
    "㋿": "令和",
}.items()}


def expand_readable_chars(text: str) -> str:
    """VOICEVOXが読めない/誤読しやすい特殊文字を読みに展開する
    （例: ①→1・Ⅲ→3・㈱→株式会社・50㎡→50平方メートル・25℃→25度）。
    連続する丸数字は「①②③→1、2、3」と読点で区切り、前後の算用数字とも
    連結しない（「手順①2番目」→「手順1、2番目」）。"""
    def _num(m):
        s = "、".join(_READABLE_NUM_MAP[c] for c in m.group(0))
        if m.string[m.start() - 1: m.start()].isdigit():
            s = "、" + s
        if m.string[m.end(): m.end() + 1].isdigit():
            s = s + "、"
        return s
    text = _READABLE_NUM_RE.sub(_num, text)
    return text.translate(_READABLE_SYM_MAP)


# 数字の桁区切りカンマ（1,234）。VOICEVOXが「いち、にさんよん」と区切って
# 誤読するため除去する。カンマの後がちょうど3桁のときだけ（リスト「1,23」等は残す）
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
# 中黒の連続（・・・）。三点リーダの代用表記として使われるが個別に読まれ得るため統一
_NAKAGURO_RUN_RE = re.compile(r"・{3,}")
# 数値レンジの波ダッシュ「10〜20」。VOICEVOXは〜を読まないため「10 20」と意味不明になる。
# 数字両挟みに限定するので「よろしく〜」等の語尾の装飾には触れない。
# 半角~も対象（全角～は normalize_ascii で ~ に変換された後にここへ来るため）
_RANGE_RE = re.compile(r"(?<=\d)\s*[〜~](?=\d)")


def normalize_readings(text: str) -> str:
    """読み上げで誤読になりやすい表記を整える
    （例: 1,234円→1234円・待って・・・→待って…・10〜20人→10から20人）。
    normalize=True の経路で適用。"""
    text = _THOUSANDS_RE.sub("", text)
    text = _RANGE_RE.sub("から", text)
    return _NAKAGURO_RUN_RE.sub("…", text)


# 半角カナ（｡｢｣､･ｦ-ﾟ）。NFKCで全角へ正規化する（ｶ+ﾞ→ガ の濁点合成も一括）。
# 半角のままだと (1)濁点分離の誤読 (2)読み方辞書の全角surfaceと不一致
# (3)半角句点｡が文分割に効かない、の3つの実害がある。
_HALFKANA_RE = re.compile(r"[｡-ﾟ]+")


def normalize_halfwidth_kana(text: str) -> str:
    """半角カナ・半角句読点を全角に正規化する（normalize=True の経路で適用）。"""
    return _HALFKANA_RE.sub(lambda m: unicodedata.normalize("NFKC", m.group(0)),
                            text)


# URL・メールアドレス。読み上げると1文字ずつ読まれて聞くに堪えないため、
# strip_urls=True で本文から取り除く（行ごと消すのではなく該当部分だけ）。
# 文字クラスはURLに使われるASCIIに限定する。日本語はURLの直後に空白を置かないのが
# 普通なので、「空白まで」を1マッチにすると後続の本文まで消えてしまう。
_URL_RE = re.compile(
    r"https?://[A-Za-z0-9:/?#@!$&'()*+,;=%._~\[\]-]+"
    r"|www\.[A-Za-z0-9][A-Za-z0-9:/?#@!$&'()*+,;=%._~\[\]-]*"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# URL末尾に食い込みがちな文末記号。マッチから外して本文側に残す
_URL_TRAIL = ".,;:!?)'\""


def strip_urls(text: str) -> str:
    """URL・メールアドレスを除去する（読み上げ用。残りの文はそのまま）。
    URL直後の句読点・閉じ括弧はURLの一部とみなさず本文に残す。"""
    def _repl(m):
        s = m.group(0)
        keep = ""
        while s and s[-1] in _URL_TRAIL:
            keep = s[-1] + keep
            s = s[:-1]
        return keep
    return _URL_RE.sub(_repl, text)


def normalize_ascii(text: str) -> str:
    """全角英数・記号を半角に正規化する（例: Ｅｘｃｅｌ２０２３ → Excel2023）。"""
    return text.translate(_Z2H)


# ============================================================
#  読み上げる直前に、読み方を直す（画面の本文は変えない）
# ============================================================
# VOICEVOX が読み違える書き方だけを、エンジンへ渡す直前に読める形へ直す。本文の文字は
# そのまま。エンジンへ文を渡す関数（vv_audio_query・vv_reading・vv_synthesize_with_kana）
# がここを通すので、連続再生・試し聴き・音声の保存・ふりがなが同じ読みになる
# （ふりがなと音がずれない）。実際のエンジン（VOICEVOX 0.26.1）の読み:
#   25℃→ニジュウゴドシイ  ㈱・①②③→読まない  10〜20人→ジュウ、ニジュウ
#   2026/11/7→ニセンニジュウロク、ジュウイチ、ナナ  2026-11-07→ニイゼロニイロク、…
#   10:30→ジュウ、サンジュウ  10時〜12時→ジュウジ、ジュウニジ  10-20人→イチゼロ、ニジュウニン
#   100m→ヒャクエム  10g→ジュウジイ  -5℃→ゴド（マイナスが消える）  +3→タスサン
#   ver.2→ヴァア、ニ  1st→イチショオトトン  いいねw→イイネダブリュウ
# もともと正しく読める物（iPhone・YouTube・50%・5km・3×4・文中の――）には触らない。
# （1,280円 の桁区切りのカンマだけは外す。読みは同じで、区切って読まれるのを防ぐ）
# どちらとも取れる物は直さない（「1/3」「11/7」は分数か日付か分からない。曜日か時刻が
# 続けば日付: 11/7(土)・11/13 18:07）。ほかに 18時07分→ジュウハチジ ゼロ ナナフン、
# 6:07 PM→ロクジ ナナフン ピイエム も直す（18時7分・午後6時7分。2026-11-14 に実測）。
_RF_DATE = re.compile(
    r"(?<![\d/.\-])((?:1[89]|2[01])\d\d)([/.\-])(0?[1-9]|1[0-2])\2"
    r"(0?[1-9]|[12]\d|3[01])(?!\d|[/\-]|\.\d)")
_RF_MD_WEEK = re.compile(
    r"(?<![\d/.])(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])"
    r"(?=\s*[(（][月火水木金土日](?:曜日?)?[)）])")
# 年の無い「11/13 18:07」。すぐあとに時刻が続くなら日付（エクスプローラーの一覧・メールの
# 受信日時・予定表など）。前は「ジュウイチ、ジュウサン」と読んでいた（2026-11-14 に実測）
_RF_MD_TIME = re.compile(
    r"(?<![\d/.])(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])"
    r"(?=\s*(?:[(（][月火水木金土日](?:曜日?)?[)）]\s*)?"
    r"(?:(?:AM|PM|am|pm|午前|午後)\s*)?(?:[01]?\d|2[0-4]):[0-5]\d(?!\d))")
# 「18時07分」「2026年01月05日」の頭の0。VOICEVOX は「ジュウハチジ ゼロ ナナフン」と読む
_RF_ZERO_PAD = re.compile(r"(?<![\d.,])0(?=\d[月日時分秒])")
# 時刻の前後の AM / PM（「エエエム」「ピイエム」と読まれる）→ 午前・午後（時刻の前に置く）
_RF_AMPM_BEFORE = re.compile(
    r"(?<![A-Za-z])(AM|PM|am|pm|[ap]\.m\.)\s*(?=(?:[01]?\d|2[0-4]):[0-5]\d(?!\d))")
_RF_AMPM_AFTER = re.compile(
    r"(?<![\d:.])((?:[01]?\d|2[0-4]):[0-5]\d(?::[0-5]\d)?)\s*(AM|PM|am|pm|[ap]\.m\.)(?![A-Za-z])")
_RF_TIME = re.compile(r"(?<![\d:.])([01]?\d|2\d):([0-5]\d)(?::([0-5]\d))?(?![\d:])")
# 範囲の「〜」「-」のあとに来る、数の単位（10時〜12時・3-5日・10%〜20%）
_RF_COUNTER = "時分秒日月年円人歳才個回件枚冊本台匹頭羽週度号階位点章話巻頁"
_RF_RANGE_WAVE = re.compile(
    rf"(?:(?<=[\d%])|(?<=\d[{_RF_COUNTER}]))\s*[〜~∼]\s*(?=\d)")
# 「10-20人」。電話番号（03-1234-5678）・型番（ABC-10）・点数（3-1で）は直さない:
# 左の数の前に数字・-・英字が無く、右の数のすぐあとに単位があるときだけ
_RF_RANGE_HYPHEN = re.compile(
    rf"(?<![\d\-‐−–A-Za-z.])(\d+)\s*[-‐−–]\s*(\d+)"
    rf"(?=[{_RF_COUNTER}%]|ページ|(?:km|cm|mm|m|kg|mg|g|ml)(?![a-zA-Z]))")
_RF_RANGE_HYPHEN2 = re.compile(rf"(?<=\d[{_RF_COUNTER}])\s*[-‐−–]\s*(?=\d)")
# 「-5℃」「前年比-3%」のマイナス。引き算（10-5）と範囲は前に数字があるので除く
_RF_MINUS = re.compile(r"(?<![0-9A-Za-z)\]])[-−‐](?=\d+(?:\.\d+)?\s*(?:度|%|°))")
_RF_PLUS = re.compile(r"(?<![0-9A-Za-z)\]])\+(?=\d)")
_RF_DEGREE = re.compile(r"(?<=\d)\s*°C?")
_RF_M2 = re.compile(r"(?<=[\d万千])m²")
_RF_M3 = re.compile(r"(?<=[\d万千])m³")
_RF_METER = re.compile(r"(?<=[\d万千])m(?![a-zA-Z0-9²³/])")
_RF_GRAM = re.compile(r"(?<=[\d万千])g(?![a-zA-Z0-9])")
# ファイルの大きさ（エクスプローラーの「1,234 KB」など）。数と単位のあいだに空白があると
# 「イチ、ケイ ビイ」と英字で読まれる（空白が無い「64GB」は正しく読む。2026-11-14 に実測）。
# 単位を読みにして空白も詰める。「/s」は「エス」と読まれるので「毎秒」に
_RF_BYTES = re.compile(r"(?<=\d)[ \t]?(KB|kB|MB|GB|TB)(?![A-Za-z])")
_RF_BYTES_YOMI = {"KB": "キロバイト", "kB": "キロバイト", "MB": "メガバイト", "GB": "ギガバイト",
                  "TB": "テラバイト"}
_RF_PER_SEC = re.compile(r"(?<=バイト)/s(?![A-Za-z])")
_RF_FIRST = re.compile(r"(?<![0-9A-Za-z])1st(?![A-Za-z])")
_RF_VER = re.compile(r"(?<![A-Za-z])(?i:ver)\.?\s*(?=\d)")
# 笑いの「w」（いいねw・草www）。日本語のすぐあとで、文の終わりか、2つ以上続くとき
# だけ消す（「幅wと高さh」の w は残す）。URL（www.～）は日本語のあとに来ないので残る
_RF_JP = "ぁ-ゖァ-ヺー一-鿿々〆！？!?。、」』）)…・"
_RF_W_END = re.compile(rf"(?<=[{_RF_JP}])w+(?=$|[\s。、！？!?」』）)…・])", re.M)
_RF_W_RUN = re.compile(rf"(?<=[{_RF_JP}])w{{2,}}(?![A-Za-z0-9.])")


def _rf_time(m):
    hour, minute, sec = int(m.group(1)), int(m.group(2)), m.group(3)
    out = f"{hour}時"
    if minute:
        out += f"{minute}分"
    if sec is not None and int(sec):
        out += f"{int(sec)}秒"
    return out


def fix_readings(text: str) -> str:
    """VOICEVOX が読み違える書き方を、読める形に直した文を返す（読み上げ用。上の説明）。
    2回かけても同じ結果になる（直した後の文には、直す対象が残らない）。"""
    text = normalize_ascii(text)
    text = normalize_halfwidth_kana(text)
    text = expand_readable_chars(text)
    text = _THOUSANDS_RE.sub("", text)
    text = _RF_DEGREE.sub("度", text)
    text = _RF_DATE.sub(lambda m: f"{m.group(1)}年{int(m.group(3))}月{int(m.group(4))}日",
                        text)
    text = _RF_MD_WEEK.sub(lambda m: f"{int(m.group(1))}月{int(m.group(2))}日", text)
    text = _RF_MD_TIME.sub(lambda m: f"{int(m.group(1))}月{int(m.group(2))}日", text)
    text = _RF_AMPM_AFTER.sub(
        lambda m: ("午前" if m.group(2)[0] in "Aa" else "午後") + m.group(1), text)
    text = _RF_AMPM_BEFORE.sub(lambda m: "午前" if m.group(1)[0] in "Aa" else "午後", text)
    text = _RF_TIME.sub(_rf_time, text)
    text = _RF_ZERO_PAD.sub("", text)
    text = _RF_RANGE_HYPHEN.sub(r"\1から\2", text)
    text = _RF_RANGE_HYPHEN2.sub("から", text)
    text = _RF_RANGE_WAVE.sub("から", text)
    text = _RF_MINUS.sub("マイナス", text)
    text = _RF_PLUS.sub("プラス", text)
    text = text.replace("±", "プラスマイナス")
    text = _RF_M2.sub("平方メートル", text)
    text = _RF_M3.sub("立方メートル", text)
    text = _RF_METER.sub("メートル", text)
    text = _RF_GRAM.sub("グラム", text)
    text = _RF_BYTES.sub(lambda m: _RF_BYTES_YOMI[m.group(1)], text)
    text = _RF_PER_SEC.sub("毎秒", text)
    text = _RF_FIRST.sub("ファースト", text)
    text = _RF_VER.sub("バージョン", text)
    text = _RF_W_RUN.sub("", text)
    text = _RF_W_END.sub("", text)
    return _NAKAGURO_RUN_RE.sub("…", text)


# 読み方を直すか（読み方辞書の窓の「数字・日付・記号の読み方を直す」。既定はオン）。
# 画面からは UI のスレッドで set_read_fix を呼ぶだけで、合成のスレッドは値を読むだけ
_READ_FIX = True


def set_read_fix(on):
    global _READ_FIX
    _READ_FIX = bool(on)


def read_fix_on():
    return _READ_FIX


def speech_text(text, read_fix=None):
    """エンジンへ渡す文。read_fix が None なら set_read_fix の設定に従う。
    直した結果に読む字が残らないとき（まず無い）は、元の文をそのまま渡す。"""
    if read_fix is None:
        read_fix = _READ_FIX
    if not read_fix or not text:
        return text
    fixed = fix_readings(text)
    return fixed if fixed.strip() else text


# ファイル名に使えない文字（Windowsの禁止文字。macは / のみだが持ち運びを考え共通で除く）
_FILENAME_BAD = set('<>:"/\\|?*')


def filename_snippet(text: str, max_chars: int = 12) -> str:
    """行テキストから、分割出力のファイル名に添える短い断片を作る
    （001_こんにちは.wav 形式用。連番の後ろに付けて中身を推測できるようにする）。
    OS禁止文字・空白・制御文字を除いて max_chars で切り詰める。使える文字が
    無ければ空文字（呼び出し側は連番だけのファイル名にフォールバックする）。"""
    s = "".join(c for c in str(text)
                if c not in _FILENAME_BAD and not c.isspace() and c.isprintable())
    # 末尾の半角ピリオドはWindowsで不正なファイル名になるため落とす
    return s[:max_chars].rstrip(".")


def reveal_in_file_manager(path):
    """保存先を Finder / エクスプローラーで開く（ファイルなら選択状態で表示）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"見つかりません: {path}")
    if IS_WIN:
        if os.path.isdir(path):
            os.startfile(path)
        else:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif IS_MAC:
        if os.path.isdir(path):
            subprocess.Popen(["/usr/bin/open", path])
        else:
            subprocess.Popen(["/usr/bin/open", "-R", path])
    else:
        raise RuntimeError("この環境ではフォルダ表示を利用できません。")


def fmt_duration(sec: float) -> str:
    """秒数を「約N秒」「約N分」の残り時間表示にする。"""
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"約{sec}秒"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"約{m}分{s:02d}秒"
    h, m = divmod(m, 60)
    return f"約{h}時間{m:02d}分"


def fmt_left(sec: float) -> str:
    """再生バーの「あと◯分」の長さ。行ごとに変わるので、細かい数字は出さない
    （1分未満は10秒きざみ、1時間未満は分まで、10時間からは時間だけ）。"""
    sec = max(0, int(round(sec)))
    if sec < 55:
        return f"約{max(10, (sec + 5) // 10 * 10)}秒"
    m = (sec + 30) // 60
    if m < 60:
        return f"約{m}分"
    h, m = divmod(m, 60)
    return f"約{h}時間" if h >= 10 or not m else f"約{h}時間{m}分"


def read_txt(path: str) -> str:
    """テキストファイルを読む。UTF-8で読めればそのまま（誤判定が実質ないため高速パス）。
    ダメなら CP932 / EUC-JP / UTF-16 を試し、成功した候補を「日本語らしさ」
    （かな・漢字の比率）でスコアリングして最良を採用する。従来の早い者勝ちだと
    EUC-JP のファイル（古い青空文庫系配布等）が CP932 で“デコード成功”してしまい、
    文字化けのまま読み上げられていた。同点は従来の優先順（cp932が先）で後方互換。"""
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8-sig")
    except (UnicodeDecodeError, UnicodeError):
        pass
    best = None
    best_score = -1.0
    for enc in ("cp932", "euc_jp", "utf-16"):
        try:
            t = enc, data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        score = sum(1 for c in t[1] if is_jp_script_char(c)) / max(len(t[1]), 1)
        if score > best_score:
            best, best_score = t[1], score
    if best is not None:
        return best
    return data.decode("utf-8", errors="replace")


_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_MC_FALLBACK = ("{http://schemas.openxmlformats.org/markup-compatibility/2006}"
                     "Fallback")


def extract_docx(path: str) -> str:
    """Word文書(.docx)から段落テキストを抽出する（追加ライブラリ不要）。
    テキストボックス（mc:AlternateContent）は Choice/Fallback の両分岐に同一内容が
    入り、さらに内側の w:p が独立段落としても列挙されるため、対策しないと同じ文言が
    最大4回読み上げられる。Fallback 分岐を捨て、他の w:p の子孫である w:p は
    スキップする（内側の文言は外側段落の p.iter() が1回だけ拾う）。"""
    from xml.etree import ElementTree  # 遅延import（起動短縮）
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    # mc:Fallback（mc:Choice と同一内容の互換用コピー）をツリーから除去
    parent_of = {c: p for p in root.iter() for c in p}
    for fb in list(root.iter(_DOCX_MC_FALLBACK)):
        parent = parent_of.get(fb)
        if parent is not None:
            parent.remove(fb)
    # 入れ子の w:p（テキストボックス内の段落）を特定する
    nested = set()
    for p in root.iter(_DOCX_NS + "p"):
        for child in p.iter(_DOCX_NS + "p"):
            if child is not p:
                nested.add(id(child))
    paras = []
    for p in root.iter(_DOCX_NS + "p"):
        if id(p) in nested:
            continue
        parts = []
        for node in p.iter():
            if node.tag == _DOCX_NS + "t" and node.text:
                parts.append(node.text)
            elif node.tag in (_DOCX_NS + "br", _DOCX_NS + "cr"):
                parts.append("\n")
            elif node.tag == _DOCX_NS + "tab":
                parts.append(" ")
        text = "".join(parts).strip()
        if text:
            paras.append(text)
    return "\n".join(paras)


class _HTMLTextExtractor(HTMLParser):
    """EPUB内のXHTMLから本文テキストを取り出す。<rt>(ルビ読み)や<script>等は捨てる。"""
    _SKIP = {"script", "style", "rt", "rp", "head", "title"}
    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "tr", "section", "article", "blockquote"}

    def __init__(self):
        super().__init__()
        self._out = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self._out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK and tag != "br":
            # brは単なる改行（開始タグ分のみ）。他のブロック要素は
            # 開始+終了で改行2つ→空行1つ＝段落区切りになる
            self._out.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._out.append(data)

    def text(self) -> str:
        raw = "".join(self._out)
        lines = [ln.strip() for ln in raw.split("\n")]
        out = []
        for ln in lines:
            if ln:
                out.append(ln)
            elif out and out[-1] != "":
                out.append("")  # 空行は1つに詰める
        return "\n".join(out).strip()


def _epub_norm(p: str) -> str:
    """EPUB内パスを引き当て用に正規化する。
    href はURI（空白や日本語はパーセントエンコードされる）なので unquote し、
    ../ や ./ を畳んで先頭スラッシュを外す。zip実エントリ名と突き合わせるため。"""
    return posixpath.normpath(unquote(p)).lstrip("/")


def extract_epub(path: str) -> str:
    """EPUBから本文テキストを抽出する（spine順・追加ライブラリ不要）。
    章ファイル名に空白や日本語（=hrefがパーセントエンコードされる）を含むEPUBでも
    取りこぼさないよう、zipエントリ名を正規化して突き合わせる。"""
    from xml.etree import ElementTree  # 遅延import（起動短縮）
    ns_c = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    ns_o = "{http://www.idpf.org/2007/opf}"
    with zipfile.ZipFile(path) as z:
        # 正規化した名前 → 実際のzipエントリ名 の対応表（hrefの表記揺れを吸収）
        name_map = {_epub_norm(n): n for n in z.namelist()}
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(f".//{ns_c}rootfile")
        opf_path = rootfile.get("full-path")
        opf_dir = os.path.dirname(opf_path)
        opf = ElementTree.fromstring(z.read(opf_path))
        items = {}
        nav_ids = set()   # 目次（HTMLナビゲーション）文書。本文として読まない
        for it in opf.iter(ns_o + "item"):
            items[it.get("id")] = it.get("href")
            if "nav" in (it.get("properties") or "").split():
                nav_ids.add(it.get("id"))

        def _read_chapters(skip_aux):
            """spine順に本文を集める。skip_aux=True で目次・linear=no の付録を除く。"""
            chapters = []
            missing = 0
            for ref in opf.iter(ns_o + "itemref"):
                idref = ref.get("idref")
                if skip_aux and (idref in nav_ids
                                 or (ref.get("linear") or "yes").lower() == "no"):
                    continue
                href = items.get(idref)
                if not href:
                    continue
                entry = name_map.get(
                    _epub_norm((opf_dir + "/" + href) if opf_dir else href))
                if entry is None:
                    missing += 1  # 見つからない章は数える（無言で捨てない）
                    continue
                html = z.read(entry).decode("utf-8", errors="replace")
                p = _HTMLTextExtractor()
                p.feed(html)
                t = p.text()
                if t:
                    chapters.append(t)
            return chapters, missing

        chapters, missing = _read_chapters(skip_aux=True)
        if not chapters:
            # 全章が nav/linear=no 扱いになった＝ラベル誤りのEPUB。除外なしで読み直す
            chapters, missing = _read_chapters(skip_aux=False)
    if missing and not chapters:
        # 全章取りこぼした＝ほぼ確実に構造の読み違い。呼び出し側が気づけるよう例外に。
        raise RuntimeError(f"EPUBの本文を取り出せませんでした（{missing}章が見つからず）。")
    return "\n\n".join(chapters)


# ============================================================
#  画面の範囲選択（「📷 画面から読む」）
# ============================================================
SCREEN_MIN_DRAG = 6   # これより小さい囲み（ほぼクリック）は選択とみなさない（Tkの画面座標）


def screen_selection_box(p0, p1, region, image_size, min_drag=SCREEN_MIN_DRAG):
    """画面上でドラッグした2点を、スクリーンショット画像の切り抜き範囲へ直す。

    p0, p1 はTkの画面座標（event.x_root/y_root）。region はスクショが写している
    画面の範囲 (x, y, 幅, 高さ)（同じくTkの画面座標。左や上のモニタは負になる）。
    image_size はスクショ画像の (幅, 高さ)。Macの高解像度画面では画像のほうが
    2倍大きいなど、座標と画素が一致しないので比で直す。
    返り値は PIL の crop に渡せる (左, 上, 右, 下)。小さすぎる・画面の外なら None。"""
    (x0, y0), (x1, y1) = p0, p1
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right - left < min_drag or bottom - top < min_drag:
        return None
    rx, ry, rw, rh = region
    iw, ih = image_size
    if rw <= 0 or rh <= 0 or iw <= 0 or ih <= 0:
        return None
    import math
    sx, sy = iw / rw, ih / rh
    # 端は外側へ丸める（文字の端を1画素でも削らないため）
    box = (max(0, math.floor((left - rx) * sx)),
           max(0, math.floor((top - ry) * sy)),
           min(iw, math.ceil((right - rx) * sx)),
           min(ih, math.ceil((bottom - ry) * sy)))
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        return None
    return box


def screen_selection_box_multi(p0, p1, layout, image_size, min_drag=SCREEN_MIN_DRAG):
    """screen_selection_box の、画面が何台かあるとき用。

    layout は画面ごとの対応 [(vx, vy, vw, vh, ix, iy, iw, ih), ...]:
    アプリから見たその画面の範囲 (vx, vy, vw, vh)（Tkの画面座標）と、その画面が
    スクリーンショットの画像のどこに写っているか (ix, iy, iw, ih)（画素）。
    倍率のちがう画面が混ざると（250% と 125% の2台など）、アプリから見た座標と画像の
    画素は、画面ごとに別の比でずれる。全体を1つの比で直すと、囲んだ所とちがう場所を
    切り抜いていた。画面が1台なら、layout に全体を1つ入れれば screen_selection_box と同じ。

    囲み始めた点のある画面で切り抜く（ほかの画面にはみ出したぶんは、その画面の端で切る）。
    囲み始めがどの画面にも無いとき（画面の構成が変わった等）は、いちばん重なる画面で。"""
    (x0, y0), (x1, y1) = p0, p1
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))
    if right - left < min_drag or bottom - top < min_drag:
        return None
    pick = None
    for m in layout:
        vx, vy, vw, vh = m[:4]
        if vx <= x0 < vx + vw and vy <= y0 < vy + vh:
            pick = m
            break
    if pick is None:
        best = 0
        for m in layout:
            vx, vy, vw, vh = m[:4]
            w = min(right, vx + vw) - max(left, vx)
            h = min(bottom, vy + vh) - max(top, vy)
            if w > 0 and h > 0 and w * h > best:
                pick, best = m, w * h
    if pick is None:
        return None
    vx, vy, vw, vh, ix, iy, iw, ih = pick
    box = screen_selection_box(
        (min(max(left, vx), vx + vw), min(max(top, vy), vy + vh)),
        (min(max(right, vx), vx + vw), min(max(bottom, vy), vy + vh)),
        (vx, vy, vw, vh), (iw, ih), min_drag=1)
    if box is None:
        return None
    width, height = image_size
    out = (max(0, box[0] + ix), max(0, box[1] + iy),
           min(width, box[2] + ix), min(height, box[3] + iy))
    if out[2] - out[0] < 1 or out[3] - out[1] < 1:
        return None
    return out


# 「自動めくり読み」：囲んだ範囲のページが変わったかを、縮めた白黒画像の差で見る。
# 差は「ページの文字（地の色から離れた濃さ）の量」に対する割合で測る。ただの平均差だと、
# 広い範囲に小さな文字が少しだけ、という場面で差が薄まってページ替えを見逃すため。
# 実測（96x96）：ページ替え 0.43〜0.68／1文字違い 0.009〜0.020／
# めくりの途中（前後のページが半々）と次のページ 0.22〜0.34（白地・ダーク・小さい英字）。
PAGE_SIG_SIZE = 96
PAGE_CHANGE_DIFF = 0.15   # 前に読んだページとの差がこれ以上なら「ページが変わった」
PAGE_STABLE_DIFF = 0.05   # 続けて撮った2枚の差がこれ未満なら「めくり終わって止まった」


def page_signature(img, size=PAGE_SIG_SIZE):
    """ページ替えの判定に使う、縮めた白黒画像のバイト列。"""
    from PIL import Image
    return img.convert("L").resize((size, size), Image.BILINEAR).tobytes()


def _page_ink(sig):
    """地の色（いちばん多い明るさ）から離れた濃さの平均＝ページの文字の量。"""
    hist = [0] * 256
    for v in sig:
        hist[v] += 1
    bg = hist.index(max(hist))
    return sum(abs(v - bg) for v in sig) / len(sig)


def signature_diff(a, b):
    """page_signature どうしの差（ページの文字の量に対する割合）。比べられなければ大きい値。"""
    if not a or not b or len(a) != len(b):
        return 255.0
    mad = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return mad / max(_page_ink(a), _page_ink(b), 1.0)


# ============================================================
#  画像前処理 + OCR
# ============================================================
def preprocess_image(img, enable: bool = True, max_side: int = None):
    """OCR精度向上のための前処理。enable=Falseでも「透過の白合成・PNG保存可能な
    モードへの正規化・サイズ上限」は常に適用する（OCRエンジンに渡す前提の整え。
    透過をそのまま渡すとWin/Macとも透過部が黒く潰れてOCRを阻害する）。
    enable=True の強調（グレースケール化・アンシャープ・コントラスト伸長）は
    古典的エンジンの Windows.Media.Ocr にだけ有効なので Windows のみで行い、
    macOS の Apple Vision（NN系・カラーで学習）にはカラーのまま渡す。
    max_side 省略時は Windows OCR の上限 WIN_OCR_MAX_DIM / それ以外 4000
    （呼び出し時に解決するのでテストから IS_WIN を差し替えられる）。"""
    from PIL import Image, ImageOps, ImageFilter
    if max_side is None:
        max_side = WIN_OCR_MAX_DIM if IS_WIN else 4000
    try:
        # スマホ写真はEXIFの向き情報だけ回転している（ピクセルは横向きのまま）ことが
        # 多く、そのままOCRに渡すと読めない。向きを実ピクセルへ反映する
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass   # EXIFが壊れていても元画像で続行
    if (img.mode in ("RGBA", "LA")
            or (img.mode == "P" and "transparency" in img.info)):
        # 透過は白背景に合成（macのウィンドウ撮影・クリップボード画像で頻出）
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, rgba).convert("RGB")
    elif img.mode in ("P", "1"):
        # パレット/2値のままだと Pillow の resize が resample 指定を無視して
        # NEAREST になり、拡大縮小がブロック状に劣化してOCR精度を下げる
        img = img.convert("RGB")
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")   # CMYK/YCbCr等をPNG保存可能なモードへ
    if enable and not IS_MAC and img.mode != "L":
        img = img.convert("L")
    w, h = img.size
    long_side = max(w, h)
    if enable and long_side < 1600:
        img = img.resize((w * 2, h * 2), Image.LANCZOS)
    long_side = max(img.size)
    if long_side > max_side:
        s = max_side / long_side
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    if enable and not IS_MAC:
        # SHARPENよりハローの少ないUnsharpMask。autocontrastはノイズの白点/黒点で
        # 伸長が無効化されないよう外れ値1%を無視する
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=100))
        img = ImageOps.autocontrast(img, cutoff=1)
    return img


def flatten_illumination(img):
    """照明ムラ（影・グラデーション）を背景差分で平坦化したグレースケール画像を返す。
    スマホ写真のページ撮影など、明るさが不均一な画像の再OCR用。GaussianBlurで
    背景（照明成分）を推定し、原画との差分を中庸グレー起点で再構成してから
    コントラストを伸長する。PIL標準機能のみで完結する。"""
    from PIL import ImageChops, ImageFilter, ImageOps
    g = img.convert("L")
    radius = max(8, max(g.size) // 40)   # 文字より十分大きく、照明勾配より小さい半径
    bg = g.filter(ImageFilter.GaussianBlur(radius=radius))
    flat = ImageChops.subtract(g, bg, scale=1.0, offset=128)
    return ImageOps.autocontrast(flat, cutoff=1)


def _ocr_text_score(text: str):
    """OCR結果の良し悪しの簡易スコア: (日本語文字数, 非空白文字数)。"""
    chars = [c for c in text if not c.isspace()]
    jp = sum(1 for c in chars if is_jp_script_char(c))
    return jp, len(chars)


# 英語の読み取り（OCR）が入っていないPCで、英文の多い画像を読んだときの一言。
# ファイルからの抽出とクリップボードOCRで同じ文面を使う
# 入っていないPCで英文の多い画像を読んだら、この2つの道を案内する
OCR_ENGLISH_MISSING_MSG = (
    "英語の多い画像がありました。英語を読み取る部品が入っていないため、"
    "英文が崩れて読み取られています。次のどちらかで読めるようになります。\n\n"
    "・Windows の［設定］→［時刻と言語］→［言語と地域］で"
    "「英語（米国）」を追加する（アプリは大きくなりません）\n"
    "・① の「設定 ▷」にある「読み取りを強くする」から部品を入れる"
    "（約122MBのダウンロード。日本語も、ずっと正しく読めるようになります）")

# 強い文字読み取りを入れるときの重さ（案内の窓と黒い窓で同じ言葉を使う）
OCR_ENGINE_SIZE_NOTE = ("約122MBのダウンロードで（python.orgとPyPIから）、"
                        "アプリが約100MB大きくなります。")

# 強い文字読み取りが動かなかったときの一言（そのときは Windows の読み取りで読み直す）
OCR_STRONG_FAILED_MSG = (
    "強い文字読み取りが動かなかったので、今回は Windows の読み取りで読みました。"
    "直らないときは、「読み取りを強くする」からもう一度入れてください。")


def ocr_looks_english(text: str) -> bool:
    """日本語OCRの結果を見て「元は英文の多い画像だった」と言えるか。
    日本語OCRは英文を漢字に化かす（NOT→NO丁、files→創es）ので、日本語文字の
    割合は当てにならない。決め手は**ひらがな**: 日本語の文は英単語だらけでも
    助詞や語尾が残るが、英文を読み違えた結果にはほとんど出ない。
    しきい値は実測（2026-09-15、日本語OCRのみのWindowsで同じ条件で読んだ値）:
        英字の割合 / ひらがなの割合
        日本語だけの文          0.00 / 0.67
        英単語まじりの日本語    0.45 / 0.30
        技術文書（英語多め）    0.68 / 0.22   <- 英字だけで判定すると誤って引っかかる
        日本語のアプリ画面      0.13 / 0.13
        英語のダイアログ（実物）0.69 / 0.02
        英語だけ                0.91 / 0.00
    ひらがなが 0.05 未満、かつ英字が半分以上、かつ短すぎない（20文字以上）ときだけ真。"""
    chars = [c for c in text if not c.isspace()]
    if len(chars) < 20:
        return False
    latin = sum(1 for c in chars if c.isascii() and c.isalpha())
    hira = sum(1 for c in chars if "\u3041" <= c <= "\u309f")
    return latin / len(chars) >= 0.5 and hira / len(chars) < 0.05


def _english_ocr_better(ja_text: str, en_text: str) -> bool:
    """英語で読み直した結果のほうが良いか。
    日本語OCRは英字を漢字や記号に化かす（NOT→NO丁、files→創es）。だから決め手は
    「化けの印（漢字・かな・全角文字）が減ったか」。英字の数だけで比べると、
    化けた結果が英字をたくさん含んでいるとき、少し欠けたきれいな英文が負けて
    捨てられてしまう（テストで実際に起きた: 化け44字 / きれいな英文40字）。
    ・化けの印が減っていて、英字が日本語OCRの7割以上残っていれば採る
      （英語で読み直すと改行や記号が少し落ちるのは普通なので、多少の欠けは許す）
    ・日本語OCRの方に化けの印が無いなら、英字の多い方（従来どおり）"""
    if not en_text or not en_text.strip():
        return False
    ja_latin = sum(1 for c in ja_text if c.isascii() and c.isalpha())
    en_latin = sum(1 for c in en_text if c.isascii() and c.isalpha())
    ja_noise = sum(1 for c in ja_text if is_cjk(c))
    en_noise = sum(1 for c in en_text if is_cjk(c))
    if ja_noise == 0:
        return en_latin >= ja_latin
    return en_noise < ja_noise and en_latin >= ja_latin * 0.7


import threading as _threading

# 英語OCR（RapidOCR。以前の版の「英語OCRを入れる.pyw」で入れた人だけが持っている。
# 今は強い文字読み取りを勧める）。読み込みは重い（インストール直後の初回はウイルス
# 検査で15秒かかった。2回目以降は0.2秒）ので、英文の多い画像が来たときに
# 初めて読み込み、以後は使い回す
_RAPIDOCR = {"engine": None}
_RAPIDOCR_LOCK = _threading.Lock()


def rapidocr_available() -> bool:
    """同梱の英語OCR（rapidocr_onnxruntime）が入っているか。
    読み込まずに有無だけを調べる（読み込むと重いので、判定のために払わない）。"""
    try:
        import importlib
        import importlib.util
        # インポートの探し先の覚えを捨ててから探す（呼ばれるのは英文の画像のときだけ）
        importlib.invalidate_caches()
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None
    except Exception:
        return False


def _rapidocr_engine():
    """RapidOCR を1つだけ作って使い回す（複数スレッドから来ても1回だけ読む）。
    モデルは wheel に同梱されていて、ネットには一切つながない（中身を確認済み）。"""
    with _RAPIDOCR_LOCK:
        if _RAPIDOCR["engine"] is None:
            from rapidocr_onnxruntime import RapidOCR
            # スレッド数は4まで。既定の「全コア」だと、コアの多い機体でかえって遅い
            # （実測 2026-09-15・20コア: 全コア1.48秒 / 4スレッド0.65秒 /
            #  2スレッド0.73秒 / 1スレッド1.11秒。メモリはどれも同じ）。
            # コアの少ない機体では、その数まで
            n = max(1, min(4, os.cpu_count() or 4))
            try:
                _RAPIDOCR["engine"] = RapidOCR(intra_op_num_threads=n,
                                               inter_op_num_threads=n)
            except TypeError:
                _RAPIDOCR["engine"] = RapidOCR()   # 指定を受け付けない版でも動かす
        return _RAPIDOCR["engine"]


def _rapidocr_lines(result, width, height):
    """RapidOCR の結果（[四隅の座標, 文字, 確からしさ] の並び）を、アプリ共通の
    行の形（text と 正規化した x0/x1/y0/y1）に直す。
    RapidOCR は1行を「Your decoy f」「files are」のように横に分けて返すことが
    あるので、縦の位置が重なる断片を左から順につなげて1行にする。
    分割の境目で同じ文字が二重に出る（f + files）ときは、前の断片の欠けた単語を捨てる。"""
    boxes = []
    for item in result or []:
        try:
            box, text = item[0], str(item[1]).strip()
        except (IndexError, TypeError):
            continue
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        boxes.append([min(xs), max(xs), min(ys), max(ys), text])
    boxes.sort(key=lambda b: ((b[2] + b[3]) / 2, b[0]))
    rows = []
    for b in boxes:
        cy = (b[2] + b[3]) / 2
        for r in rows:
            h = max(r["y1"] - r["y0"], b[3] - b[2])
            if abs((r["y0"] + r["y1"]) / 2 - cy) <= h * 0.5:
                r["parts"].append(b)
                r["y0"], r["y1"] = min(r["y0"], b[2]), max(r["y1"], b[3])
                break
        else:
            rows.append({"y0": b[2], "y1": b[3], "parts": [b]})
    lines = []
    for r in rows:
        parts = sorted(r["parts"], key=lambda b: b[0])
        words = parts[0][4].split(" ")
        for prev, cur in zip(parts, parts[1:]):
            nxt = cur[4].split(" ")
            # 横に重なっていて、前の末尾の単語が次の先頭の単語の書きかけなら捨てる
            if (cur[0] < prev[1] and words and nxt
                    and nxt[0].lower().startswith(words[-1].lower())
                    and len(words[-1]) < len(nxt[0])):
                words.pop()
            words.extend(nxt)
        lines.append({"text": " ".join(w for w in words if w),
                      "x0": parts[0][0] / width,
                      "x1": max(p[1] for p in parts) / width,
                      "y0": r["y0"] / height, "y1": r["y1"] / height})
    lines.sort(key=lambda l: (l["y0"], l["x0"]))
    return lines


def run_rapidocr(image_paths, strip_labels=True, cancel_event=None,
                 progress_cb=None):
    """同梱の RapidOCR で画像を読み、{path: text} を返す（英文用。日本語は
    ひらがなを落とすので、英文の多い画像にだけ使うこと）。
    行の組み立て後は Windows / macOS の経路と同じく、strip_labels のときだけ
    strip_overlay_labels、その後 reflow_ocr_lines で折り返しをつなぐ。
    1枚の失敗は他を止めない（その画像は結果に入らない＝元の結果が残る）。
    cancel_event: 1枚ごとに見る（1枚1〜3秒かかるので、まとめて見ると止まらない）。
    progress_cb(done, total): 1枚読むごとに呼ぶ。"""
    engine = _rapidocr_engine()
    from PIL import Image as _Image
    out = {}
    total = len(image_paths)
    for i, path in enumerate(image_paths):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress_cb:
            progress_cb(i, total)
        try:
            with _Image.open(path) as im:
                width, height = im.size
            result, _elapse = engine(path)
            lines = _rapidocr_lines(result, width, height)
            if strip_labels and lines:
                lines = strip_overlay_labels(lines)
            out[path] = reflow_ocr_lines(lines) if lines else ""
        except Exception:
            continue
    if progress_cb and not (cancel_event is not None and cancel_event.is_set()):
        progress_cb(total, total)
    return out


def _ocr_needs_retry(text: str, lang: str = "ja") -> bool:
    """前処理を変えて再OCRを試す価値がある低品質結果か
    （ほとんど読めていない・日本語文書のはずなのに日本語比率が低すぎる）。"""
    jp, total = _ocr_text_score(text)
    if total < 10:
        return True
    return lang == "ja" and jp / total < 0.2


def _ocr_retry_better(first: str, retry: str) -> bool:
    """リトライ結果が明確に良い（スコア1.2倍超）ときだけ差し替える。
    同等・僅差なら必ず1回目を採用する（誤差し替え防止のヒステリシス）。"""
    j1, t1 = _ocr_text_score(first)
    j2, t2 = _ocr_text_score(retry)
    # 日本語が全く取れない入力（英語文書等）は総文字数で比較する
    s1, s2 = (j1, j2) if (j1 or j2) else (t1, t2)
    return s2 > s1 * 1.2 + 1


def ocr_retry_if_poor(text, img, tmpdir, lang="ja", strip_labels=True):
    """OCR結果が低品質なら、前処理を変えた候補で再OCRし明確に良い結果へ差し替える。
    候補は順に (1)照明平坦化（影・ムラのある写真）(2)90度回転 (3)270度回転
    （EXIFなしで横倒しになっている写真・スキャン）。各候補ともスコアが1.2倍超の
    ときだけ採用し、十分読めた時点で残りは試さない。
    綺麗なスクショ・PDFではしきい値に掛からず1パスのまま（速度影響ゼロ）。macOS限定
    （Windowsは1回のOCRでPowerShellを起動するためリトライのコストが大きい）。"""
    if not IS_MAC or not _ocr_needs_retry(text, lang=lang):
        return text
    best = text
    try:
        candidates = (
            ("flat", lambda: flatten_illumination(img)),
            ("rot90", lambda: img.rotate(90, expand=True)),
            ("rot270", lambda: img.rotate(-90, expand=True)),
        )
        for name, make in candidates:
            cand = preprocess_image(make(), enable=False)
            fd, png = tempfile.mkstemp(prefix=f"t2v_retry_{name}_",
                                       suffix=".png", dir=tmpdir)
            os.close(fd)
            cand.save(png)
            t2 = run_ocr([png], lang=lang, strip_labels=strip_labels).get(png, "")
            if _ocr_retry_better(best, t2):
                best = t2
            if not _ocr_needs_retry(best, lang=lang):
                break   # 十分読めたら残りの候補は試さない
    except Exception:
        pass   # リトライ失敗はそれまでの最良結果で続行（悪化はさせない）
    return best


# ============================================================
#  強い文字読み取り（PP-OCRv6。Windows）
# ============================================================
# 初回セットアップ（winsetup.py）が ocr_engine/ に部品を入れる。入らなかったPCは、
# これまでの Windows の読み取りで読む。
# アプリの Python とは別の Python で ocr_helper.py を動かし、1枚ずつ読んでもらう
# （アプリの Python が新しすぎると、読み取りの部品が入らないため）。
# 字の正解率の実測（2026-11-05。Windows の読み取り → これ）:
#   試験の画像258枚 0.887 → 0.997 / 写真の上の白い字 0.58〜0.78 → 0.99 /
#   ゲームの文字窓 0.88〜0.93 → 0.99 / 白地の本のページ 0.97〜0.99 → 0.99〜1.00 /
#   英語（日本語だけの Windows）0.94〜0.97 → 1.00 / 縦書き 0.965 → 0.977（_merge_tate_reading）
class StrongOcrFailed(Exception):
    """強い文字読み取りが動かない（部品が消された・検査ソフトに止められた等）。"""


def _strong_python():
    return os.path.join(OCR_ENGINE_DIR, "python", "python.exe")


def _strong_mark():
    # 入れ終わって、試しに読めたときだけ winsetup.py が置く印
    return os.path.join(OCR_ENGINE_DIR, "ok.txt")


_STRONG_BROKEN = {"mark_time": None}   # 動かなかったときの印の時刻（入れ直すと変わる）


def strong_ocr_available() -> bool:
    """強い文字読み取りが使えるか。軽いので読むたびに調べる（アプリを開いたまま入れても、
    次に読むときから使える）。動かなかった部品は、入れ直すまで使わない。"""
    if not IS_WIN:
        return False
    try:
        stamp = os.path.getmtime(_strong_mark())
    except OSError:
        return False
    return (stamp != _STRONG_BROKEN["mark_time"]
            and os.path.isfile(_strong_python()) and os.path.isfile(OCR_HELPER))


def _note_strong_broken():
    try:
        _STRONG_BROKEN["mark_time"] = os.path.getmtime(_strong_mark())
    except OSError:
        pass


def prepare_for_ocr(img, enable=True):
    """読み取りに渡す前の下ごしらえ（preprocess_image）を、読み手に合わせてする。
    強い読み取りはカラーのまま読むほうが正しい（実測 258枚 0.997 / 灰色にすると 0.995。
    写真の上の字ほど差が出る）。大きさの上限も Windows の読み取りに合わせなくてよい。"""
    if strong_ocr_available():
        return preprocess_image(img, enable=False, max_side=4000)
    return preprocess_image(img, enable=enable)


class _StrongOcrHelper:
    """裏方（ocr_helper.py）を1つだけ動かして、1枚ずつ頼む。どのスレッドから呼んでもよい。
    アプリが閉じれば標準入力が切れるので、裏方も終わる（残らない）。"""
    START_TIMEOUT = 240   # 入れた直後の初回はウイルス検査で長くかかることがある
    READ_TIMEOUT = 300    # 固まった裏方を、アプリがいつまでも待たないように

    def __init__(self):
        self._lock = _threading.Lock()
        self._proc = None
        self._replies = None

    def _command(self):
        return [_strong_python(), OCR_HELPER]

    def _stop(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.stdin.close()
            proc.wait(5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _next_reply(self, timeout):
        try:
            line = self._replies.get(timeout=timeout)
        except queue.Empty:
            self._stop()
            raise StrongOcrFailed("応答がありません")
        if line is None:
            raise EOFError   # 裏方が終わった
        return json.loads(line.decode("ascii"))

    def _ensure_running(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        self._stop()
        try:
            # 少し低い優先度で動かす（遊んでいるゲームや、読み上げの音を邪魔しないように）
            proc = subprocess.Popen(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=APP_DIR,
                creationflags=(CREATE_NO_WINDOW
                               | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)))
        except OSError as e:
            raise StrongOcrFailed(str(e))
        replies = queue.Queue()

        def pump():
            for line in proc.stdout:
                replies.put(line)
            replies.put(None)
        _threading.Thread(target=pump, daemon=True).start()
        self._proc, self._replies = proc, replies
        try:
            first = self._next_reply(self.START_TIMEOUT)
        except (EOFError, ValueError):
            self._stop()
            raise StrongOcrFailed("起動できませんでした")
        if not first.get("ready"):
            self._stop()
            raise StrongOcrFailed(str(first.get("fatal", "起動できませんでした")))

    def wake(self):
        with self._lock:
            self._ensure_running()

    def read(self, path):
        """1枚読んでもらい、返事（ocr_helper.py の先頭を参照）を返す。"""
        with self._lock:
            for last_try in (False, True):
                self._ensure_running()
                try:
                    self._proc.stdin.write(json.dumps({"path": path}).encode("ascii") + b"\n")
                    self._proc.stdin.flush()
                    return self._next_reply(self.READ_TIMEOUT)
                except (OSError, EOFError, ValueError):
                    # 裏方がちょうど休みに入った（または落ちた）。起こし直して、もう一度だけ頼む
                    self._stop()
                    if last_try:
                        raise StrongOcrFailed("途中で止まりました")


_STRONG_HELPER = _StrongOcrHelper()


def strong_ocr_shutdown():
    """裏方を止める（アプリを閉じるとき）。アプリが終われば裏方も終わるが、閉じたあとで
    アプリのプロセスが少し残っても、裏方（約140MB）を待たせたままにしないように。"""
    try:
        with _STRONG_HELPER._lock:
            _STRONG_HELPER._stop()
    except Exception:
        pass


def strong_ocr_prewarm():
    """裏方を先に起こしておく（読み込みに1秒ほどかかるので、範囲を選んでいるあいだに）。
    待たない。失敗しても何もしない（読むときに改めて試して、だめなら知らせる）。"""
    if not strong_ocr_available():
        return

    def wake():
        try:
            _STRONG_HELPER.wake()
        except Exception:
            pass
    _threading.Thread(target=wake, daemon=True).start()


def _strong_ocr_boxes(items):
    """裏方の返事を、文字の箱の並びに直す（座標はピクセル）。"""
    boxes = []
    for item in items:
        try:
            pts, text = item[0], str(item[1]).strip()
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
            # 字の太さは箱の短い辺で測る（傾いたページでは、外接矩形が行より太るため）
            thick = min(math.hypot(xs[1] - xs[0], ys[1] - ys[0]),
                        math.hypot(xs[3] - xs[0], ys[3] - ys[0]))
        except (IndexError, TypeError, ValueError):
            continue
        if text:
            boxes.append({"text": text, "x0": min(xs), "x1": max(xs),
                          "y0": min(ys), "y1": max(ys), "thick": max(thick, 1.0)})
    return boxes


def _is_tate_box(b):
    return (len(b["text"]) >= 2
            and b["y1"] - b["y0"] >= _TATE_RATIO * (b["x1"] - b["x0"]))


_TATE_SMALL = str.maketrans("つやゆよあいうえおツヤユヨアイウエオ",
                            "っゃゅょぁぃぅぇぉッャュョァィゥェォ")
_TATE_FILL = set("っゃゅょぁぃぅぇぉッャュョァィゥェォ、。ー「」『』（）()…！？!?")
_TATE_CHOON = re.compile(r"(?<=[ァ-ヺー])一(?![一-鿿々])")


def _merge_tate_reading(base: str, other: str) -> str:
    """縦書きの1列を、PP-OCRv6 の読み（base）を土台に、Windows の読み（other）で補う。
    PP-OCRv6 は漢字に強いが、列を回して読むので小さいかな・句読点・長音を落とす
    （あった→あた）。Windows は縦書きの小さい字に強いが、漢字を読み違える。そこで
    base に無い字のうち、落としやすい種類の字だけを足す。実測（本物の縦書き10ページ）:
        字の正解率 / 小さいかな / 句読点
        PP-OCRv6 0.908 / 0.47 / 0.13    Windows 0.965 / 0.96 / 0.95    合わせる 0.977 / 0.98 / 0.93
    PP-OCRv6 が落とすのは小さい字だけなので、Windows が「しよう」と大きく読んでいても、
    足すのは「ょ」にする（Windows は「よ」と「ょ」を読み分けるのが苦手）。"""
    import difflib
    other = "".join(other.split())
    out = []
    matcher = difflib.SequenceMatcher(None, base, other, autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "insert":
            out.append("".join(c for c in other[b0:b1].translate(_TATE_SMALL)
                               if c in _TATE_FILL))
        elif op == "replace" and base[a0:a1] == "一" and other[b0:b1] == "ー":
            out.append("ー")
        else:
            out.append(base[a0:a1])
    return "".join(out)


def _tidy_tate(text: str) -> str:
    """縦書きの列でだけ起きる読み違いを直す（回して読むので、「。」が「°」、長音が「一」に
    なる）。カタカナのあとの「一」は、後ろが漢字でなければ長音（スーと / ページ一覧）。"""
    return _TATE_CHOON.sub("ー", text.replace("°", "。"))


def _windows_lines(path):
    """Windows の読み取りで画像を読み、行の並びを返す。失敗したら空。"""
    tmpdir = tempfile.mkdtemp(prefix="t2v_tate_")
    try:
        from PIL import Image
        png = os.path.join(tmpdir, "page.png")
        with Image.open(path) as im:
            preprocess_image(im, enable=True).save(png, compress_level=1)
        data = _windows_ocr_json([png])
        item = data[0] if isinstance(data, list) else data
        lines = item.get("lines") or []
        if isinstance(lines, dict):
            lines = [lines]   # PS5.1のConvertTo-Jsonは1要素配列をオブジェクトに畳む
        return [{"text": str(l["text"]), "x0": float(l["x0"]), "x1": float(l["x1"]),
                 "y0": float(l["y0"]), "y1": float(l["y1"])} for l in lines]
    except Exception:
        return []
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _fill_tate_columns(boxes, path, width, height):
    """縦書きの列があれば、抜けやすい字を Windows の読みで補う（無ければ何もしない）。"""
    tall = [b for b in boxes if _is_tate_box(b)]
    if not tall:
        return boxes
    columns = [l for l in _windows_lines(path)
               if (l["y1"] - l["y0"]) * height > (l["x1"] - l["x0"]) * width]
    for b in tall:
        x0, x1, y0, y1 = b["x0"] / width, b["x1"] / width, b["y0"] / height, b["y1"] / height
        near = [l for l in columns
                if min(x1, l["x1"]) - max(x0, l["x0"]) > 0.4 * min(x1 - x0, l["x1"] - l["x0"])
                and min(y1, l["y1"]) > max(y0, l["y0"])]
        near.sort(key=lambda l: l["y0"])
        b["text"] = _tidy_tate(_merge_tate_reading(b["text"], "".join(l["text"] for l in near)))
    return boxes


def _strong_ocr_lines(boxes, width, height):
    """文字の箱を、アプリ共通の行の形（text と 正規化した x0/x1/y0/y1）に直す。
    横に並ぶ断片のうち、近いものだけを1行につなぐ。離れていれば別の行のまま（2段組の
    左右や、表の欄をつないでしまうと、読み順が壊れて段組の判定にもかからない）。
    高さは字の太さから作る（傾いた写真でも行が太らず、行間の判定が狂わない）。"""
    total = sum(len(b["text"]) for b in boxes)
    tall = sum(len(b["text"]) for b in boxes if _is_tate_box(b))
    if tall * 2 > total:
        # 縦書きのページ。座標を回して横書きと同じに扱う（tate_to_yoko と同じ考え方）
        boxes = [dict(b, x0=b["y0"], x1=b["y1"], y0=width - b["x1"], y1=width - b["x0"])
                 for b in boxes]
        width, height = height, width
    rows = []
    for b in sorted(boxes, key=lambda b: b["x0"]):
        center = (b["y0"] + b["y1"]) / 2
        best, best_off = None, None
        for parts in rows:
            last = parts[-1]
            t = max(b["thick"], last["thick"])
            off = abs(center - (last["y0"] + last["y1"]) / 2)
            if (off <= 0.5 * t and -0.5 * t <= b["x0"] - last["x1"] <= 1.5 * t
                    and (best is None or off < best_off)):
                best, best_off = parts, off
        if best is None:
            rows.append([b])
        else:
            best.append(b)
    lines = []
    for parts in rows:
        text = parts[0]["text"]
        for prev, cur in zip(parts, parts[1:]):
            spaced = (cur["x0"] - prev["x1"] > cur["thick"]
                      or (text[-1].isascii() and cur["text"][0].isascii()))
            text += (" " if spaced else "") + cur["text"]
        center = sum((p["y0"] + p["y1"]) / 2 for p in parts) / len(parts)
        half = sum(p["thick"] for p in parts) / len(parts) / 2
        lines.append({"text": text, "x0": parts[0]["x0"] / width,
                      "x1": max(p["x1"] for p in parts) / width,
                      "y0": (center - half) / height, "y1": (center + half) / height})
    lines.sort(key=lambda l: l["y0"] + l["y1"])
    # 同じ高さに並ぶ行は上下をそろえる（reflow_ocr_lines は上端→左端の順に並べるので、
    # わずかな傾きで右の塊が先に来ないように）
    for prev, cur in zip(lines, lines[1:]):
        if abs(cur["y0"] + cur["y1"] - prev["y0"] - prev["y1"]) / 2 <= 0.25 * (prev["y1"] - prev["y0"]):
            cur["y0"], cur["y1"] = prev["y0"], prev["y1"]
    lines.sort(key=lambda l: (l["y0"], l["x0"]))
    return lines


def _strong_ocr_text(reply, path, strip_labels):
    width, height = max(int(reply["width"]), 1), max(int(reply["height"]), 1)
    boxes = _strong_ocr_boxes(reply["items"])
    if not reply.get("turned"):   # 回して読んだ画像は、Windows の読みと座標が合わない
        boxes = _fill_tate_columns(boxes, path, width, height)
    lines = _strong_ocr_lines(boxes, width, height)
    if strip_labels and lines:
        lines = strip_overlay_labels(lines)
    return reflow_ocr_lines(lines) if lines else ""


def run_strong_ocr(image_paths, strip_labels=True, errors=None, progress_cb=None,
                   cancel_event=None, done=None):
    """強い文字読み取りで画像を読み、{path: text} を返す。日本語も英語も一緒に読む。
    1枚の失敗は他を止めない（errors に理由を足す）。裏方そのものが動かなくなったら
    StrongOcrFailed を投げる（そこまでに読めた分は done に残っている）。"""
    done = {} if done is None else done
    total = len(image_paths)
    for i, path in enumerate(image_paths):
        if cancel_event is not None and cancel_event.is_set():
            return done
        if progress_cb:
            progress_cb(i, total)
        reply = _STRONG_HELPER.read(path)
        try:
            if not reply.get("ok"):
                raise ValueError(reply.get("error", "読めませんでした"))
            done[path] = _strong_ocr_text(reply, path, strip_labels)
        except Exception as e:
            done[path] = ""
            if errors is not None:
                errors.append(f"{os.path.basename(path)}: {e}")
    if progress_cb:
        progress_cb(total, total)
    return done


def run_ocr(image_paths, lang="ja", strip_labels=True, errors=None,
            progress_cb=None, cancel_event=None, notices=None):
    """
    画像パスのリストをオフラインOCRに渡し、{path: text} を返す。
    Windows: 強い文字読み取りが入っていればそれ（run_strong_ocr）、無ければ
    Windows.Media.Ocr（PowerShellヘルパー） / macOS: Apple Vision（pyobjc）
    どれも行の外接矩形を使った折り返し連結(reflow_ocr_lines)を適用し、
    strip_labels=True のときは“映像内オーバーレイ・ラベル行”（局ロゴ・番組名・
    日時・カテゴリ）を座標で除去する。denoise と同じON/OFFで効かせる想定。
    errors: list を渡すと、一部ファイルのOCR失敗理由（"ファイル名: 理由"）を
    追記する（全滅時は従来どおり例外）。
    progress_cb(done, total): 進捗通知（macは1枚ごと・Winはチャンクごと）。
    cancel_event: 途中中断（それまでの部分結果を返す）。
    """
    if not image_paths:
        return {}
    if IS_WIN:
        done = {}
        if strong_ocr_available():
            try:
                return run_strong_ocr(image_paths, strip_labels=strip_labels, errors=errors,
                                      progress_cb=progress_cb, cancel_event=cancel_event,
                                      done=done)
            except StrongOcrFailed:
                # 読めた分は活かし、残りをこれまでの読み取りで読む。入れ直すまで試さない
                _note_strong_broken()
                if notices is not None:
                    notices.append("strong_ocr_failed")
                image_paths = [p for p in image_paths if p not in done]
        # notices: 「英語のOCRが入っていない」などの知らせを受け取るリスト。
        # macOS の Vision は日本語と英語を一緒に読むので、この問題は起きない
        done.update(run_windows_ocr(image_paths, lang=lang, strip_labels=strip_labels,
                                    errors=errors, progress_cb=progress_cb,
                                    cancel_event=cancel_event, notices=notices))
        return done
    if IS_MAC:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)  # 他ディレクトリからのimportでも ocr_mac を見つける
        import ocr_mac
        return ocr_mac.recognize_files(image_paths, lang=lang,
                                       strip_labels=strip_labels, errors=errors,
                                       progress_cb=progress_cb,
                                       cancel_event=cancel_event)
    raise RuntimeError("このパソコンでは、画像から文字を読む機能を使えません（Windows と Mac だけです）。")


def _parse_windows_ocr_result(data, strip_labels=True, errors=None, meta=None):
    """ocr_win.ps1 の出力JSONを {path: text} に変換する（純関数・単体テスト用に分離）。
    行の外接矩形(lines)があれば ocr_mac.py と同じ座標パイプラインを適用する:
    strip_labels=True のときだけ strip_overlay_labels、reflow_ocr_lines は常時。
    後処理に失敗した場合と旧形式（linesなし）は従来どおり text をそのまま使う。
    errors: list を渡すと ok=false のファイルの失敗理由を追記する
    （従来は理由が捨てられ「無言で空」になっていた）。"""
    if isinstance(data, dict):
        data = [data]
    result = {}
    for item in data:
        if meta is not None:
            # 実際に使った言語と、使える言語（新しい ocr_win.ps1 だけが載せる）。
            # PS5.1 の ConvertTo-Json は1要素の配列を文字列に畳むので両方受ける
            if item.get("engine_lang"):
                meta["engine_lang"] = str(item["engine_lang"])
            av = item.get("available_langs")
            if isinstance(av, str):
                av = [av]
            if isinstance(av, list):
                meta["available_langs"] = [str(x) for x in av if x]
        path = item.get("path", "")
        text = item.get("text", "") if item.get("ok") else ""
        if not item.get("ok") and errors is not None and item.get("error"):
            errors.append(f"{os.path.basename(path)}: {item['error']}")
        lines = item.get("lines")
        if isinstance(lines, dict):
            lines = [lines]   # PS5.1のConvertTo-Jsonは1要素配列をオブジェクトに畳む
        if item.get("ok") and isinstance(lines, list) and lines:
            # 形式を検証してから使う。1行でも壊れていれば lines 全体を信用せず
            # text へフォールバック（欠けた座標で誤った連結・除去をしないため）
            valid = []
            for l in lines:
                try:
                    valid.append({"text": str(l["text"]),
                                  "x0": float(l["x0"]), "x1": float(l["x1"]),
                                  "y0": float(l["y0"]), "y1": float(l["y1"])})
                except (KeyError, TypeError, ValueError):
                    valid = []
                    break
            if valid:
                try:
                    valid = tate_to_yoko(valid, image_aspect(path))
                    if strip_labels:
                        valid = strip_overlay_labels(valid)
                    text = reflow_ocr_lines(valid) if valid else ""
                except Exception:
                    pass  # 座標後処理に失敗しても行テキストで続行（本文は保持される）
        result[path] = text
    return result


def run_windows_ocr(image_paths, lang="ja", strip_labels=True, errors=None,
                    progress_cb=None, cancel_event=None, chunk_size=20,
                    notices=None):
    """
    画像パスのリストをWindows標準OCRに渡し、{path: text} を返す。
    chunk_size 枚ずつPowerShellヘルパー(ocr_win.ps1)を起動して処理する
    （チャンク間でキャンセル判定・進捗通知ができる。起動オーバーヘッド
    1〜2秒/回と中断粒度のバランスで20枚）。errors: 一部失敗の理由を追記。
    """
    if not image_paths:
        return {}
    result = {}
    fatal = []
    meta = {}
    n = len(image_paths)
    for start in range(0, n, chunk_size):
        if cancel_event is not None and cancel_event.is_set():
            break   # 部分結果を返す
        chunk = image_paths[start:start + chunk_size]
        try:
            result.update(_run_windows_ocr_chunk(chunk, lang=lang,
                                                 strip_labels=strip_labels,
                                                 errors=errors, meta=meta))
        except Exception as e:
            fatal.append(str(e))
        if progress_cb:
            progress_cb(min(start + chunk_size, n), n)
    if fatal and not any(result.values()):
        # 全チャンク失敗＝従来の「全滅時は例外」と同じ扱い
        raise RuntimeError(fatal[0])
    if errors is not None:
        errors.extend(fatal)   # 一部チャンクの失敗は警告として通知
    if lang == "ja" and result:
        _english_second_pass(result, meta, strip_labels, notices,
                             cancel_event, chunk_size, progress_cb=progress_cb)
    return result


def _english_second_pass(result, meta, strip_labels, notices, cancel_event,
                         chunk_size=20, progress_cb=None):
    """日本語で読んだ結果のうち、英文の多い画像だけを英語で読み直す（Windows）。
    読み手は、Windowsの英語OCR（入っていれば）→ 同梱の RapidOCR の順。
    どちらも使えなければ読み直さず、notices に "english_ocr_missing" を足す
    ＝呼び出し側が利用者に知らせる。以前は英語を頼んでも黙って日本語で読み、
    崩れた結果をそのまま返していた。
    ここでの失敗は本番の結果を壊さない（日本語の結果のまま返す）。"""
    targets = [p for p, t in result.items() if ocr_looks_english(t)]
    if not targets:
        return
    en_tag = next((l for l in meta.get("available_langs", [])
                   if l.lower().startswith("en")), None)
    total = len(targets)

    def tell(done):
        # 読み直しは「英語」の段として知らせる（3つ目の引数を知らない受け手は、
        # 2つだけで呼ぶ run_windows_ocr 側の通知と同じに扱える）
        if progress_cb:
            try:
                progress_cb(done, total, "english")
            except TypeError:
                progress_cb(done, total)

    if en_tag is not None:
        # Windows に英語のOCRが入っている＝追加の重さ無しで読める。
        # PowerShell を1回起動するごとにまとめて読むので、キャンセルはチャンクごと
        def read_all():
            got = {}
            for start in range(0, total, chunk_size):
                if cancel_event is not None and cancel_event.is_set():
                    break
                chunk = targets[start:start + chunk_size]
                tell(start)
                try:
                    got.update(_run_windows_ocr_chunk(chunk, lang=en_tag,
                                                      strip_labels=strip_labels))
                except Exception:
                    continue
            return got
    elif rapidocr_available():
        # 入っていなければ同梱の RapidOCR。読み込みに失敗する環境
        # （DLLが足りない等）では、黙って崩れた結果を返さず案内に回す
        try:
            _rapidocr_engine()
        except Exception:
            if notices is not None and "english_ocr_missing" not in notices:
                notices.append("english_ocr_missing")
            return

        def read_all():
            # 1枚ずつ読むので、キャンセルも進捗も1枚ごとに効く
            try:
                return run_rapidocr(targets, strip_labels=strip_labels,
                                    cancel_event=cancel_event,
                                    progress_cb=lambda d, t: tell(d))
            except Exception:
                return {}
    else:
        if notices is not None and "english_ocr_missing" not in notices:
            notices.append("english_ocr_missing")
        return
    again = read_all()
    used = False
    for p in targets:
        if p in again and _english_ocr_better(result[p], again[p]):
            result[p] = again[p]
            used = True
    if used and notices is not None and "english_ocr_used" not in notices:
        notices.append("english_ocr_used")


def _run_windows_ocr_chunk(image_paths, lang="ja", strip_labels=True,
                           errors=None, meta=None):
    """PowerShellヘルパーを1回起動して image_paths を処理する（チャンク実体）。"""
    return _parse_windows_ocr_result(_windows_ocr_json(image_paths, lang=lang),
                                     strip_labels=strip_labels, errors=errors, meta=meta)


def _windows_ocr_json(image_paths, lang="ja"):
    """PowerShellヘルパーを1回起動して、その出力JSONを返す。"""
    tmpdir = tempfile.mkdtemp(prefix="t2v_ocr_")
    try:
        manifest = os.path.join(tmpdir, "manifest.txt")
        out_json = os.path.join(tmpdir, "result.json")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write("\n".join(image_paths))

        ps_exe = _find_powershell()
        cmd = [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", OCR_PS1, "-Manifest", manifest, "-Out", out_json, "-Lang", lang]
        # encoding/errors 指定なしだと日本語Windows(cp932)でOCR側の出力に
        # 非cp932バイトが混ざったとき decode で例外→エラーメッセージが潰れる。
        # timeout: WinRT OCRは1〜3秒/枚。固まったPowerShellでUIが永久busyに
        # ならないよう十分な余裕をもって打ち切る（子プロセスはkillされる）
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=max(120, 30 * len(image_paths)),
                                  creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"画像の読み取りが返事をしませんでした（{len(image_paths)}枚待ちました）。"
                "枚数を減らすか、PCを再起動してお試しください。")
        if not os.path.exists(out_json):
            raise RuntimeError("読み取りに失敗: " + (proc.stderr or proc.stdout or "出力なし"))

        # utf-8-sig: PS5.1のOut-File -Encoding utf8はBOM付きで書くことがある
        # （fatal経路）。BOMなしも読めるため常にこちらで開く。
        with open(out_json, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and "fatal" in data:
            raise RuntimeError(data["fatal"])
        return data
    finally:
        # OCRが済めばmanifest.txt/result.json（＝抽出全文）は不要。%TEMP%に残さない。
        shutil.rmtree(tmpdir, ignore_errors=True)


def _find_powershell():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cand = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return cand if os.path.exists(cand) else "powershell"


def _render_pdf_page(path, page_index, dpi):
    """PDFの1ページを未加工のPIL画像として再レンダリングする（低品質OCRのリトライ用）。"""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(path)
    try:
        page = doc[page_index]
        w_pt, h_pt = page.get_size()
        scale = dpi / 72.0
        if max(w_pt, h_pt) * scale > 4000:
            scale = 4000.0 / max(w_pt, h_pt)
        return page.render(scale=scale).to_pil()
    finally:
        doc.close()


# ============================================================
#  ファイルからの抽出（progress_cb(done, total, msg) で進捗通知）
# ============================================================
# PDFテキスト層がこれ未満（空白除く文字数）なら「透かし・ページ番号だけ」の疑いが
# 濃いのでOCRも実行し、明確に良い方を採用する（スキャンPDF+透かし層の本文欠落対策）
_PDF_LAYER_MIN_CHARS = 20

# ページ範囲指定の正規化テーブル（全角数字・全角区切り・波ダッシュを半角へ。
# 日本語入力のまま「５−１０、２０」のように書いても通す）
_PAGES_NORM = str.maketrans("０１２３４５６７８９", "0123456789",
                            " \t　")
_PAGES_SEP = str.maketrans({"、": ",", "，": ",", "〜": "-", "～": "-",
                            "−": "-", "ー": "-"})


def parse_page_ranges(spec):
    """「5-320」「1-3,7,10-」「-20」形式のページ範囲を [(start, end|None), ...] に
    パースする（1始まり・両端含む）。空・None は None（=全ページ）。
    不正な字句は ValueError（呼び出し側が入力エラーとして案内する）。"""
    if spec is None:
        return None
    s = str(spec).translate(_PAGES_NORM).translate(_PAGES_SEP)
    if not s:
        return None
    ranges = []
    for part in s.split(","):
        if not part:
            continue
        if "-" in part:
            a, _sep, b = part.partition("-")
            try:
                start = int(a) if a else 1
                end = int(b) if b else None
            except ValueError:
                raise ValueError(part)
            if start < 1 or (end is not None and end < 1):
                raise ValueError(part)
            if end is not None and end < start:
                start, end = end, start   # 逆順（20-5）は入れ替えて許容
        else:
            try:
                start = end = int(part)
            except ValueError:
                raise ValueError(part)
            if start < 1:
                raise ValueError(part)
        ranges.append((start, end))
    return ranges or None


def page_in_ranges(page_no, ranges):
    """1始まりのページ番号が範囲リストに含まれるか（ranges=None は常にTrue）。"""
    if ranges is None:
        return True
    return any(a <= page_no and (b is None or page_no <= b)
               for a, b in ranges)


def apply_ocr_corrections(text, fix_confusables=False, denoise=False):
    """OCR由来テキストへの後処理（誤字補正→ノイズ除去）を1箇所に集約して
    (補正後テキスト, confusablesペア, 除去行) を返す。extract_files と
    クリップボードOCRの両経路で共有し、適用順のドリフトを防ぐ。"""
    conf_pairs = []
    if fix_confusables and text:
        t2 = fix_ocr_confusables(text)
        if t2 != text:
            # 補正は文字の1:1置換のみで行の対応が崩れないため、
            # 行単位の before/after を整形レポートに記録できる
            conf_pairs = [(b, a) for b, a
                          in zip(text.split("\n"), t2.split("\n")) if b != a]
        text = t2
    removed = []
    if denoise and text:
        removed = denoise_removed_lines(text)
        text = denoise_capture(text)
    return text, conf_pairs, removed


# ============================================================
#  PDF の柱（毎ページの上や下に出る、本や章の名前）とページ番号
# ============================================================
# PDF の本は、ページの上か下に、本の名前や章の名前（柱）とページ番号が入っている。
# そのまま読むと、ページが変わるたびに「吾輩は猫である」と読み上げる。柱が
# 「第一章　猫の一日」なら章の見出しと見なされ、毎ページの前に長い間が入り、
# M4B の章もページごとにできていた。そこで、多くのページの最初か最後の数行に同じ形で
# 出てくる短い行を柱として外す（ページ番号の数字は違ってよい）。章の見出しの形
# （第一章 など）の柱は、最初の1回（章の始まりの見出し）を残す。1ページ目（扉）のものも
# 残す。それ以外（2ページ目から出る本の名前など）は全部外す。ページ番号だけの行も外す。
_HEAD_ZONE = 2           # ページの最初と最後の、この行数だけを見る
_HEAD_MIN_PAGES = 3      # これだけのページに出てきたら柱
_HEAD_MAX_CHARS = 40     # 柱は短い
_HEAD_MIN_DOC_PAGES = 4  # これより少ないページの PDF には何もしない
_HEAD_SENTENCE_END = tuple("。．！？!?」』…、.")
_PAGE_NUM_DECOR = re.compile(r"[\s\-–—―‐・·.()（）\[\]［］/／|｜pP頁ページ第]")


def _is_page_number(line):
    """ページ番号だけの行か（12・- 12 -・p.12・12/300・xii など）。"""
    s = _PAGE_NUM_DECOR.sub("", unicodedata.normalize("NFKC", line))
    if not s:
        return False
    # ローマ数字は i〜xxxix の正しい形だけ（civil のような英単語を数字と見ない）
    return s.isdigit() or bool(re.fullmatch(r"(?i)(?=[ivx])x{0,3}(ix|iv|v?i{0,3})", s))


def _head_key(line):
    """柱の見分け用。空白を詰め、数字（ページ番号）はどれも同じに扱う。"""
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line))
    return re.sub(r"\d+", "#", s)


def _head_candidate(line):
    """柱になりうる行か（短く、文の終わりの形でない。「はい。」や「」で終わるセリフは違う）。"""
    s = line.strip()
    return 0 < len(s) <= _HEAD_MAX_CHARS and not s.endswith(_HEAD_SENTENCE_END)


def strip_running_heads(pages):
    """1冊の PDF のページの文（ページ順）から、柱とページ番号を外す。
    戻り値: (外したあとのページの文のリスト, 外した行のリスト)"""
    if len(pages) < _HEAD_MIN_DOC_PAGES:
        return list(pages), []
    split = [str(p).split("\n") for p in pages]
    zones = []
    counts = {}
    for lines in split:
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        zone = set(filled[:_HEAD_ZONE] + filled[-_HEAD_ZONE:])
        zones.append(zone)
        seen = set()
        for i in zone:
            ln = lines[i].strip()
            if _is_page_number(ln) or not _head_candidate(ln):
                continue
            key = _head_key(ln)
            if key not in seen:
                seen.add(key)
                counts[key] = counts.get(key, 0) + 1
    heads = {k for k, n in counts.items() if n >= _HEAD_MIN_PAGES}
    removed, kept_once, out = [], set(), []
    for page_no, (lines, zone) in enumerate(zip(split, zones)):
        keep = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if i in zone and s:
                if _is_page_number(s):
                    removed.append(s)
                    continue
                key = _head_key(s)
                if key in heads and _head_candidate(s):
                    # 最初の1回を残すのは、章の見出し（章の始まり）と1ページ目（扉）だけ
                    first_ok = page_no == 0 or is_chapter_heading(s)
                    if key in kept_once or not first_ok:
                        removed.append(s)
                        continue
                    kept_once.add(key)
            keep.append(ln)
        out.append("\n".join(keep))
    return out, removed


def extract_files(paths, pdf_mode="auto", dpi=300, preprocess=True,
                  lang="ja", progress_cb=None, strip_labels=True,
                  fix_confusables=False, report=None, denoise=False,
                  cancel_event=None, pdf_pages=None, strip_heads=True):
    """
    複数ファイル（PDF/画像/テキスト/Word/EPUB）からテキストを抽出して結合文字列を返す。
    pdf_mode: "auto"（テキスト層→無ければOCR） / "ocr"（常にOCR）
    strip_labels: True で画像OCR時に“映像内オーバーレイ・ラベル行”を座標で除去（Win/Mac両対応）。
                  denoise と同じ値を渡す想定（--no-denoise / GUIチェックOFFでラベル除去もしない）。
    fix_confusables: True で OCR由来のテキストにだけ fix_ocr_confusables（同形文字の
                     文脈補正）を適用する。txt/docx/EPUB・PDFテキスト層には適用しない。
    denoise: True で OCR由来のテキストにだけ denoise_capture（映像内オーバーレイ文字の
             除去）を適用する。txt/docx/EPUB・PDFテキスト層には適用しない（小説txt中の
             英文行・年号だけの行が誤って消える事故の防止。v1.16.0でOCR限定に変更）。
    report: dict を渡すと整形レポート用の情報を追記する。
            "confusables": [(補正前の行, 補正後の行), ...]（fix_confusables の変更箇所）
            "removed": [行, ...]（denoise が除去した行）
    cancel_event: threading.Event を渡すと、ファイル/ページ境界・OCRの途中
                  （macは1枚ごと・Winはチャンクごと）で中断できる。
                  中断時はそれまでの部分結果と警告を返す。
    pdf_pages: parse_page_ranges() の結果（None=全ページ）。PDFの読み取り範囲を
               1始まりで制限する（表紙・目次・索引の読み飛ばし用。全PDFに同適用）。
    strip_heads: True で PDF の柱（毎ページの本や章の名前）とページ番号を外す
                 （strip_running_heads。テキスト層・OCR のどちらのページにも効く）。
                 外した行は report["heads"] に入れる。
    戻り値: (text, warnings:list)
    """
    from PIL import Image
    import pypdfium2 as pdfium

    warnings = []
    # OCR対象を一旦集める（temp PNG化）→ 最後にまとめて1回OCR
    ocr_jobs = []           # [(key, temp_png_path, 元画像パス or None)]
    text_parts = {}         # key -> text(テキスト層のもの)
    order = []              # 出力順 key
    tmpdir = tempfile.mkdtemp(prefix="t2v_img_")
    try:
        total = len(paths)
        for idx, path in enumerate(paths):
            if cancel_event is not None and cancel_event.is_set():
                break
            if progress_cb:
                progress_cb(idx, total, f"解析中: {os.path.basename(path)}")
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext in PDF_EXT:
                    doc = pdfium.PdfDocument(path)
                    npages = len(doc)
                    matched = 0
                    for pi in range(npages):
                        if cancel_event is not None and cancel_event.is_set():
                            break
                        if not page_in_ranges(pi + 1, pdf_pages):
                            continue   # 範囲外ページは出力順・OCRとも対象外
                        matched += 1
                        key = f"{path}#p{pi+1}"
                        order.append(key)
                        page = doc[pi]
                        use_ocr = (pdf_mode == "ocr")
                        layer_backed = False  # 短い層あり＝OCRと比較して良い方を採用
                        if not use_ocr:
                            tp = page.get_textpage()
                            layer = tp.get_text_range()
                            n_chars = len("".join(layer.split()))
                            if n_chars >= _PDF_LAYER_MIN_CHARS:
                                text_parts[key] = layer
                            elif n_chars >= 1:
                                # 透かし（Scanned by …）やページ番号だけの層に
                                # スキャン本文が隠れる典型ケース。層を仮置きしつつ
                                # OCRも実行し、明確に良い方だけ採用する
                                text_parts[key] = layer
                                use_ocr = True
                                layer_backed = True
                            else:
                                use_ocr = True
                        if use_ocr:
                            w_pt, h_pt = page.get_size()
                            scale = dpi / 72.0
                            if max(w_pt, h_pt) * scale > 4000:
                                scale = 4000.0 / max(w_pt, h_pt)
                            bitmap = page.render(scale=scale)
                            pil = bitmap.to_pil()
                            pil = prepare_for_ocr(pil, enable=preprocess)
                            png = os.path.join(tmpdir, f"pdf_{idx}_{pi}.png")
                            pil.save(png)
                            # (path, ページ番号) を持たせ、低品質時は再レンダリングして
                            # リトライできるようにする（元画像はディスクに無いため）
                            ocr_jobs.append((key, png, (path, pi), layer_backed))
                        if progress_cb:
                            progress_cb(idx, total,
                                        f"解析中: {os.path.basename(path)} ({pi+1}/{npages}p)")
                    doc.close()
                    if pdf_pages is not None and matched == 0:
                        warnings.append(f"{os.path.basename(path)}: 指定ページ範囲に"
                                        f"該当なし（全{npages}ページ）")
                elif ext in IMG_EXT:
                    key = path
                    order.append(key)
                    img = Image.open(path)
                    img = prepare_for_ocr(img, enable=preprocess)
                    png = os.path.join(tmpdir, f"img_{idx}.png")
                    img.save(png)
                    ocr_jobs.append((key, png, path, False))
                elif ext in TXT_EXT:
                    order.append(path)
                    text_parts[path] = strip_aozora(read_txt(path))
                elif ext in DOCX_EXT:
                    order.append(path)
                    text_parts[path] = extract_docx(path)
                elif ext in EPUB_EXT:
                    order.append(path)
                    text_parts[path] = strip_aozora(extract_epub(path))
                else:
                    warnings.append(f"未対応の形式をスキップ: {os.path.basename(path)}")
            except Exception as e:
                warnings.append(f"読み込み失敗 {os.path.basename(path)}: {e}")

        # まとめてOCR（キャンセル済みならスキップして部分結果へ）
        if ocr_jobs and not (cancel_event is not None and cancel_event.is_set()):
            if progress_cb:
                # 目盛りをページ数に切り替える。ファイル数のままだと、PDFを
                # 1冊だけ入れたときは分母が1で、十数分かかっても 0% で止まって
                # 見える（「固まった」と誤解される一番の原因だった）
                progress_cb(0, len(ocr_jobs),
                            f"画像から文字を読んでいます… 0/{len(ocr_jobs)}枚")
            png_paths = [p for _, p, _o, _lb in ocr_jobs]
            ocr_errors = []
            ocr_failed = False
            # ページ単位の進捗と、OCR実行中のキャンセル即応（従来はOCRバッチ全体が
            # 1呼び出しで、キャンセルを押してもバッチ完了まで効かなかった）
            ocr_progress = None
            if progress_cb:
                phase_clock = {"phase": None, "t": time.monotonic()}

                def ocr_progress(i, n, phase=None):
                    # 残り時間の見当も出す（音声生成側と同じ数え方）。
                    # 何分かかるのか分からない待ちがいちばん不安なので。
                    # 英語の読み直しは速さが違うので、段が変わったら数え直す
                    if phase != phase_clock["phase"]:
                        phase_clock.update(phase=phase, t=time.monotonic())
                    note = ""
                    if i:
                        eta = (time.monotonic() - phase_clock["t"]) / i * (n - i)
                        if eta > 5:
                            note = f"（残り{fmt_duration(eta)}）"
                    label = ("英語を読み直しています…" if phase == "english"
                             else "画像から文字を読んでいます…")
                    progress_cb(i, n, f"{label} {i}/{n}枚{note}")
            ocr_notices = []
            try:
                ocr_result = run_ocr(png_paths, lang=lang,
                                     strip_labels=strip_labels, errors=ocr_errors,
                                     progress_cb=ocr_progress,
                                     cancel_event=cancel_event,
                                     notices=ocr_notices)
            except Exception as e:
                # 全滅時の例外メッセージに理由は集約済み。個別エラーと空ページ警告を
                # 重ねると同じ障害が三重に表示されるため、ここで抑止する
                warnings.append(f"読み取りのエラー: {e}")
                ocr_result = {}
                ocr_errors = []
                ocr_failed = True
            if "english_ocr_missing" in ocr_notices:
                warnings.append(OCR_ENGLISH_MISSING_MSG)
            if "strong_ocr_failed" in ocr_notices:
                warnings.append(OCR_STRONG_FAILED_MSG)
            # 一部ファイルの失敗理由を警告として表示（従来は無言で空になっていた）
            for e in ocr_errors[:5]:
                warnings.append(f"読み取りに失敗 {e}")
            if len(ocr_errors) > 5:
                warnings.append(f"…ほか{len(ocr_errors) - 5}件の読み取りの失敗")
            empty = 0
            for retry_done, (key, png, orig, layer_backed) in enumerate(ocr_jobs):
                t = ocr_result.get(png, "")
                # 低品質（写真の影・ムラ・横倒し等）なら前処理を変えて再OCRし、
                # 良い方を採用。単体画像は元ファイルから、PDFページは再レンダリングで
                # “未加工の元画像”を作ってリトライする。キャンセル後はリトライしない。
                if (orig and _ocr_needs_retry(t, lang=lang) and IS_MAC
                        and not (cancel_event is not None
                                 and cancel_event.is_set())):
                    name = (os.path.basename(orig) if isinstance(orig, str)
                            else f"{os.path.basename(orig[0])} {orig[1]+1}p")
                    if progress_cb:
                        progress_cb(retry_done, len(ocr_jobs),
                                    f"画像を見やすくして読み直しています: {name}")
                    try:
                        src = (Image.open(orig) if isinstance(orig, str)
                               else _render_pdf_page(orig[0], orig[1], dpi))
                        t = ocr_retry_if_poor(t, src, tmpdir,
                                              lang=lang, strip_labels=strip_labels)
                    except Exception:
                        pass
                # 誤字補正・ノイズ除去はOCR由来テキスト限定。処理はクリップボード
                # OCRと共有の apply_ocr_corrections に集約（適用順のドリフト防止）
                t, conf_pairs, removed = apply_ocr_corrections(
                    t, fix_confusables=fix_confusables, denoise=denoise)
                if layer_backed:
                    # 短いテキスト層（透かし・ページ番号疑い）とOCRを比較し、
                    # OCRが明確に良い（1.2倍超）ときだけ差し替える。同等なら層を信じる。
                    # OCR不採用ならレポートにも載せない（最終テキストに存在しない
                    # 行を「消えた行」として貼り戻させないため、記録は採用確定後）
                    if not _ocr_retry_better(text_parts.get(key, ""), t):
                        continue
                if report is not None:
                    if conf_pairs:
                        report.setdefault("confusables", []).extend(conf_pairs)
                    if removed:
                        report.setdefault("removed", []).extend(removed)
                text_parts[key] = t
                if not t.strip():
                    empty += 1
            if (empty and not ocr_failed
                    and not (cancel_event is not None
                             and cancel_event.is_set())):
                # キャンセル時は未OCRページが「空」に見えるため重ねて警告しない
                warnings.append(
                    f"{empty}枚/ページで文字を検出できませんでした"
                    "（白紙、または画質不足の可能性）")

        if cancel_event is not None and cancel_event.is_set():
            warnings.append("抽出をキャンセルしました（そこまでの結果を表示しています）")

        # PDF ごとに、柱（毎ページの本や章の名前）とページ番号を外す
        if strip_heads:
            books = {}
            for key in order:
                if "#p" in key:
                    books.setdefault(key.rsplit("#p", 1)[0], []).append(key)
            for keys in books.values():
                pages, gone = strip_running_heads([text_parts.get(k, "") for k in keys])
                for k, t in zip(keys, pages):
                    text_parts[k] = t
                if gone and report is not None:
                    report.setdefault("heads", []).extend(gone)
        # 出力順に結合（ファイル/ページ境界は空行）
        chunks = []
        for key in order:
            t = text_parts.get(key, "").strip()
            if t:
                chunks.append(t)
        if progress_cb:
            progress_cb(total, total, "抽出完了")
        return "\n\n".join(chunks), warnings
    finally:
        # OCR用に描き出した一時PNG（＝元文書の画像コピー）は用済み。%TEMPに残さない。
        shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
#  VOICEVOX 本体の検出・起動 / 試聴再生（OS別）
# ============================================================
_VV_PATH_CACHE = {}


def _win_registry_voicevox():
    """アンインストール情報からVOICEVOXの場所を拾う（Windowsのみ）。
    標準以外の場所（Dドライブなど）へ入れた人でも見つけられるようにする。"""
    try:
        import winreg   # Windows専用。Macでは import 自体が失敗するので関数内で
    except Exception:
        return None
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    views = [(winreg.HKEY_CURRENT_USER, 0),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY)]
    for root, flag in views:
        try:
            with winreg.OpenKey(root, key_path, 0,
                                winreg.KEY_READ | flag) as k:
                for i in range(winreg.QueryInfoKey(k)[0]):
                    try:
                        sub = winreg.EnumKey(k, i)
                        with winreg.OpenKey(k, sub) as sk:
                            name = str(winreg.QueryValueEx(sk, "DisplayName")[0])
                            if "voicevox" not in name.lower():
                                continue
                            loc = ""
                            for value in ("InstallLocation", "UninstallString"):
                                try:
                                    v = str(winreg.QueryValueEx(sk, value)[0])
                                except Exception:
                                    continue
                                v = v.strip()
                                if not v:
                                    continue
                                if value == "UninstallString":
                                    # 実測: VOICEVOX は InstallLocation が空で、
                                    # 場所はアンインストーラのパスにしか入っていない。
                                    # 「"C:\\...\\Uninstall VOICEVOX.exe" /currentuser」
                                    # の形なので、引用符の中を取り出して親フォルダを使う
                                    if v.startswith('"'):
                                        v = v[1:].split('"', 1)[0]
                                    else:
                                        v = v.split(" /")[0].split(" -")[0]
                                    v = os.path.dirname(v)
                                if v:
                                    loc = v
                                    break
                    except Exception:
                        continue   # 読めないキーは飛ばす（数百件あるので普通のこと）
                    exe = os.path.join(loc, "VOICEVOX.exe") if loc else ""
                    if exe and os.path.exists(exe):
                        return exe
        except Exception:
            continue
    return None


def _fixed_drives():
    """固定ディスクのドライブ文字だけを返す（Windows）。
    USBやネットワークドライブを触ると、回転待ちで何秒も固まることがある。"""
    try:
        import ctypes
        out = []
        bits = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not (bits >> i) & 1:
                continue
            d = "%s:\\" % chr(ord("A") + i)
            if ctypes.windll.kernel32.GetDriveTypeW(d) == 3:   # DRIVE_FIXED
                out.append(d)
        return out
    except Exception:
        return []


def _user_folders():
    """デスクトップ・ダウンロード・ドキュメントの実際の場所（Windows）。
    OneDrive に移したデスクトップ（OneDrive\\デスクトップ）もここで分かる。
    取れないときは、ホームの下の決まった名前で代わりにする。"""
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def known(guid):
        import uuid
        b = uuid.UUID(guid).bytes_le
        g = _GUID.from_buffer_copy(b)
        p = ctypes.c_wchar_p()
        try:
            if ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(g), 0, None, ctypes.byref(p)) != 0:
                return None
            return p.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(p)

    home = os.path.expanduser("~")
    out = []
    for guid, name in (("B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop"),
                       ("374DE290-123F-4565-9164-39C4925E467B", "Downloads"),
                       ("FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents")):
        try:
            p = known(guid)
        except Exception:
            p = None
        for d in (p, os.path.join(home, name)):
            if d and os.path.isdir(d) and d not in out:
                out.append(d)
    return out


def _find_unzipped_voicevox():
    """zip を展開しただけの VOICEVOX を、デスクトップ・ダウンロード・ドキュメントから
    探す（Windows）。インストーラを使わない人はここに置くことが多いのに、以前は
    探していなかった。見るのは「その下の VOICEVOX.exe」と「名前に voicevox を含む
    フォルダの1段下まで」だけにして、大きなフォルダでも待たせない。"""
    import glob
    for d in _user_folders():
        for pattern in (os.path.join(d, "VOICEVOX", "VOICEVOX.exe"),
                        os.path.join(d, "*voicevox*", "VOICEVOX.exe"),
                        os.path.join(d, "*voicevox*", "*", "VOICEVOX.exe")):
            for p in sorted(glob.glob(pattern)):
                if os.path.isfile(p):
                    return p
    return None


def find_voicevox(user_path=None, refresh=False):
    """VOICEVOX本体のインストール先を探して返す。見つからなければ None。
    user_path: 設定で手動指定された場所（あれば最優先）。
    探すのに少し時間がかかるので、一度見つけたら覚えておく（refresh=Trueで再探索）。"""
    if user_path and os.path.exists(user_path):
        return user_path
    if not refresh and "path" in _VV_PATH_CACHE:
        return _VV_PATH_CACHE["path"]
    found = None
    if IS_WIN:
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\VOICEVOX\VOICEVOX.exe"),
            os.path.expandvars(r"%ProgramFiles%\VOICEVOX\VOICEVOX.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\VOICEVOX\VOICEVOX.exe"),
            os.path.expandvars(r"%ProgramW6432%\VOICEVOX\VOICEVOX.exe"),
        ]
        for p in candidates:
            if "%" not in p and os.path.exists(p):
                found = p
                break
        if not found:
            found = _win_registry_voicevox()
        if not found:
            try:
                found = _find_unzipped_voicevox()
            except Exception:
                found = None
        if not found:
            # 最後の手段。固定ディスクの分かりやすい場所だけを見る
            for d in _fixed_drives():
                for sub in ("VOICEVOX", r"Program Files\VOICEVOX"):
                    p = os.path.join(d, sub, "VOICEVOX.exe")
                    if os.path.exists(p):
                        found = p
                        break
                if found:
                    break
    elif IS_MAC:
        for p in ("/Applications/VOICEVOX.app",
                  os.path.expanduser("~/Applications/VOICEVOX.app")):
            if os.path.exists(p):
                found = p
                break
        if not found:
            # Spotlight に聞く（切っている環境では空が返るだけ・害はない）
            try:
                r = subprocess.run(
                    ["/usr/bin/mdfind",
                     "kMDItemCFBundleIdentifier == 'jp.hiroshiba.voicevox'"],
                    capture_output=True, text=True, timeout=2)
                for line in (r.stdout or "").splitlines():
                    if line.strip().endswith(".app") and os.path.exists(line.strip()):
                        found = line.strip()
                        break
            except Exception:
                pass
    # 見つかったときだけ覚える。見つからなかった結果まで覚えると、
    # あとからVOICEVOXを入れた人が再起動するまで気づけない
    if found:
        _VV_PATH_CACHE["path"] = found
    return found


def find_voicevox_engine(user_path=None, data_dir=None):
    """VOICEVOX の「エンジンだけ」の実行ファイルを返す。
    これを直接動かすと、エディタ画面（重いElectron）を立ち上げずに
    読み上げだけができる。見つからなければ None。
    探す所は2つ。本体に同梱のエンジン（vv-engine）と、VOICEVOX が自分の設定
    フォルダに入れたエンジン（vvpp-engines）。最近の VOICEVOX は後者にしか
    置かないことがあり、以前はそれを探さず「エンジンのみ」が使えなかった。"""
    app = find_voicevox(user_path)
    if app:
        if IS_MAC:
            # ※Mac側は未検証（このパスに無ければ vvpp-engines を探す）
            run = os.path.join(app, "Contents", "Resources", "vv-engine", "run")
        else:
            run = os.path.join(os.path.dirname(app), "vv-engine", "run.exe")
        if os.path.exists(run):
            return run
    return _find_vvpp_engine(data_dir)


def _vv_data_dir():
    """VOICEVOX（エディタ）が設定やエンジンを置くフォルダ。"""
    if IS_WIN:
        base = os.environ.get("APPDATA")
        return os.path.join(base, "voicevox") if base else None
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/voicevox")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "voicevox")


def _find_vvpp_engine(data_dir=None):
    """VOICEVOX が vvpp-engines に入れた、公式の VOICEVOX ENGINE の実行ファイル。
    ほかのエンジン（別の会社の声など）は使わない。目印は engine_manifest.json の
    uuid（公式エンジンの uuid と同じもの）で、実行ファイルの名前もそこに書いてある。"""
    base = os.path.join(data_dir or _vv_data_dir() or "", "vvpp-engines")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        folder = os.path.join(base, name)
        try:
            with open(os.path.join(folder, "engine_manifest.json"),
                      encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or manifest.get("uuid") != VV_ENGINE_ID:
            continue
        command = str(manifest.get("command") or "")
        run = os.path.join(folder, command) if command else ""
        if command and os.path.basename(command) == command and os.path.isfile(run):
            return run
    return None


def free_port(host="127.0.0.1"):
    """今すぐ使えるポート番号をひとつ借りる。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.bind((host, 0))
        return sk.getsockname()[1]


def launch_voicevox(user_path=None):
    """VOICEVOX本体（エディタ画面つき）を起動する。見つからなければ FileNotFoundError。"""
    path = find_voicevox(user_path)
    if not path:
        hint = (r"%LOCALAPPDATA%\Programs\VOICEVOX\VOICEVOX.exe" if IS_WIN
                else "/Applications/VOICEVOX.app")
        raise FileNotFoundError(f"VOICEVOXが見つかりません。\n想定の場所: {hint}")
    if IS_MAC:
        subprocess.Popen(["/usr/bin/open", path])
    else:
        subprocess.Popen([path])
    return path


# ============================================================
#  GPU を使うか（このアプリが VOICEVOX を画面なしで起こすとき）
# ============================================================
# VOICEVOX の GPU 版（Windows は DirectML。読み上げの部分のフォルダに DirectML.dll が
# ある）は、--use_gpu で GPU を使える。RTX 5060 Laptop・Ryzen 9 8940HX の PC で測ると、
# 1文が 0.46秒 → 0.12秒（30万字の本で 約86分 → 約22分）。かわりに起こすのに約3秒
# 長くかかり、最初の1文も遅い（つないだあとに温めておく）。CPU に入っている小さな GPU
# では、かえって遅いことがあるので、自分のメモリが 2GB 以上ある GPU があるときだけ使う。
_GPU_MIN_MEMORY = 2 * 1024 ** 3
_GPU_CLASS_KEY = (r"SYSTEM\CurrentControlSet\Control\Class"
                  r"\{4d36e968-e325-11ce-bfc1-08002be10318}")


def engine_supports_gpu(run_path):
    """この VOICEVOX（読み上げの部分）が、GPU を使える版か。"""
    if not run_path:
        return False
    try:
        names = {n.lower() for n in os.listdir(os.path.dirname(run_path))}
    except OSError:
        return False
    return "directml.dll" in names


def gpu_memory_sizes():
    """この PC の GPU の [(名前, 自分のメモリのバイト数)]（Windows。レジストリを読むだけ）。"""
    if not IS_WIN:
        return []
    import winreg
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _GPU_CLASS_KEY)
    except OSError:
        return []
    with root:
        for i in range(64):
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, sub) as k:
                    size = 0
                    for name in ("HardwareInformation.qwMemorySize",
                                 "HardwareInformation.MemorySize"):
                        try:
                            v = winreg.QueryValueEx(k, name)[0]
                        except OSError:
                            continue
                        if isinstance(v, bytes):
                            v = int.from_bytes(v[:8], "little")
                        try:
                            size = max(size, int(v))
                        except (TypeError, ValueError):
                            pass
                    try:
                        desc = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                    except OSError:
                        desc = ""
            except OSError:
                continue
            if desc or size:
                out.append((desc, size))
    return out


def want_gpu(run_path, sizes=None):
    """このアプリが VOICEVOX を起こすとき、GPU を使うか。GPU を使える版で、
    自分のメモリが 2GB 以上ある GPU があるときだけ使う（小さな GPU では遅いことがある）。"""
    if not engine_supports_gpu(run_path):
        return False
    if sizes is None:
        sizes = gpu_memory_sizes()
    return any(size >= _GPU_MIN_MEMORY for _name, size in sizes)


def launch_voicevox_engine(host="127.0.0.1", port=50021, use_gpu=False,
                           user_path=None):
    """エンジンだけを起動して (プロセス, URL) を返す。
    渡す引数は、VOICEVOX本体が自分で使っているものと同じ3つだけにしてある
    （--host / --port / --use_gpu）。素性の分からないオプションは使わない。
    use_gpu: True / False / "auto"（want_gpu で決める）。返すプロセスの uses_gpu に
    GPU で起こしたか、run_path に起こした実行ファイルを入れておく（だめなら CPU で起こし直すため）。"""
    run = find_voicevox_engine(user_path)
    if not run:
        raise FileNotFoundError(
            "VOICEVOX の読み上げの部分（vv-engine）が見つかりませんでした。\n"
            "VOICEVOX を起動してください。")
    if use_gpu == "auto":
        use_gpu = want_gpu(run)
    argv = [run, "--host", str(host), "--port", str(int(port))]
    if use_gpu:
        argv.append("--use_gpu")
    env = dict(os.environ, VV_OUTPUT_LOG_UTF8="1")   # 本体と同じ環境変数
    kw = {}
    if IS_WIN:
        # 黒いコンソール窓を出さない。CTRL+C がこちらへ飛ばないよう別グループに
        kw["creationflags"] = (CREATE_NO_WINDOW
                               | subprocess.CREATE_NEW_PROCESS_GROUP)
    # 出力は捨てる。PIPE にして読まないと、たまった時点でエンジンが止まる
    proc = subprocess.Popen(argv, cwd=os.path.dirname(run), env=env,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kw)
    proc.uses_gpu = bool(use_gpu)
    proc.run_path = run
    return proc, f"http://{host}:{int(port)}"


def stop_process(proc, timeout=5):
    """自分が起動したプロセスを終わらせる。子プロセスごと確実に片づける。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout)
    except Exception:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True,
                               creationflags=CREATE_NO_WINDOW)
            else:
                proc.kill()
        except Exception:
            pass


def can_play():
    """この環境で試聴（WAV再生）が可能か。"""
    if IS_WIN:
        try:
            import winsound  # noqa: F401
            return True
        except Exception:
            return False
    if IS_MAC:
        return os.path.exists("/usr/bin/afplay")
    return False


def wav_duration(wav_bytes) -> float:
    """WAVバイト列の再生時間（秒）を返す。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getnframes() / float(w.getframerate())


# 再生に使った一時ファイルのうち、まだ消せていないもの。
# 鳴らし終わった直後は Windows がファイルを掴んだままのことがあり、その場に
# 居座って待つと連続再生の間合いが延びてしまう。次の再生が始まれば前のファイルは
# 解放されるので、そのときに片づける（居残るのは多くても1つ）。
_PLAY_TMP_PENDING = []


def _sweep_play_tmp():
    """前回までに消せなかった一時ファイルを片づける。"""
    for path in list(_PLAY_TMP_PENDING):
        try:
            os.remove(path)
            _PLAY_TMP_PENDING.remove(path)
        except OSError:
            pass   # まだ掴まれている。次の機会に


# このアプリが %TEMP%（Macなら /var/folders/...）に作る作業用の名前。
# 正常に終われば finally で消えるが、プロセスごと落とされると残る
# （実例: 合成の途中で強制終了され、13MBのスプールが1週間居座っていた）。
_TMP_PREFIXES = ("t2v_play_", "t2v_prev_", "t2v_spool_", "t2v_ocr_",
                 "t2v_img_", "t2v_clip_", "t2v_enc_")


def sweep_stale_tmp(max_age_sec=86400):
    """前回までの異常終了で残った作業用ファイル/フォルダを片づける。
    いま動いている別のインスタンスの作業を巻き添えにしないよう、
    **最終更新から1日以上たったものだけ**を消す（合成キャッシュの .tmp を
    掃除している _synth_cache_evict と同じ考え方）。
    消せなかったものは黙って見送る＝次の起動でまた挑む。
    戻り値は (消した数, 回収したバイト数)。"""
    freed, count = 0, 0
    try:
        root = tempfile.gettempdir()
        now = time.time()
        for name in os.listdir(root):
            if not name.startswith(_TMP_PREFIXES):
                continue
            path = os.path.join(root, name)
            try:
                if now - os.stat(path).st_mtime <= max_age_sec:
                    continue
                if os.path.isdir(path):
                    size = 0
                    for base, _dirs, files in os.walk(path):
                        for f in files:
                            try:
                                size += os.path.getsize(os.path.join(base, f))
                            except OSError:
                                pass
                    shutil.rmtree(path, ignore_errors=True)
                    if not os.path.exists(path):
                        freed += size
                        count += 1
                else:
                    size = os.path.getsize(path)
                    os.remove(path)
                    freed += size
                    count += 1
            except OSError:
                pass   # 使用中・権限なし。次の機会に
    except OSError:
        pass
    return count, freed


def sweep_play_tmp_on_exit():
    """終了時の後片づけ。最後に鳴らした音のファイルを1つ手放して消す。
    自然に鳴り終わったときは末尾を切らないよう音を止めていないので、
    Windowsがそのファイルを掴んだままセッションが終わる（%TEMPに1個残る）。
    もう何も鳴らさないと決まった場面でだけ呼ぶこと。"""
    if IS_WIN and _PLAY_TMP_PENDING:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
    for _ in range(10):
        _sweep_play_tmp()
        if not _PLAY_TMP_PENDING:
            break
        time.sleep(0.05)


def _remove_play_tmp(path, tries=6):
    """一時ファイルを消す。消せなければ次回に持ち越す（待ち続けない）。"""
    for i in range(tries):
        try:
            os.remove(path)
            return
        except OSError:
            if i < tries - 1:
                time.sleep(0.02)
    if path not in _PLAY_TMP_PENDING:
        _PLAY_TMP_PENDING.append(path)


# Windows の非同期 PlaySound は鳴り始めが少し遅れるため、WAVの長さぶん待って
# すぐ次の行を PlaySound すると、前の行の語尾が上書きされて切れる。
# 再生するときだけ末尾に無音を足し、さらに少し長く待って語尾を鳴り切らせる
# （保存用WAV・キャッシュ・字幕の秒数は変えない）。ふだんの連続再生は1本の音の
# 流れ（下の LineStream）で鳴らすので、これを使うのは試聴と、音の流れを開けなかった
# ときの行ごとの再生。
_PLAY_TAIL_SILENCE_SEC = 0.25
_WIN_PLAY_TAIL_PAD = 0.12
# 1行の再生で、音声の後ろに実際に入る無音の秒数。行ごとの連続再生では「間」から
# 差し引いて、行間の無音が保存ファイルと同じ長さになるようにする
PLAY_TAIL_SEC = (_PLAY_TAIL_SILENCE_SEC + _WIN_PLAY_TAIL_PAD) if IS_WIN else 0.0


def _pad_wav_for_play(wav_bytes, tail_sec=_PLAY_TAIL_SILENCE_SEC):
    """再生用に、WAV末尾へ短いフェードアウト(10ms)と無音を足したバイト列を返す。
    末尾が0でないと急に途切れて「ぷつっ」と鳴りうるので、最後の値から0へ下げる。
    非圧縮PCMでないときや読めないときは、元のバイト列をそのまま返す。"""
    if not wav_bytes or tail_sec <= 0:
        return wav_bytes
    try:
        import struct
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            params = w.getparams()
            if (params.comptype != "NONE" or params.framerate <= 0
                    or params.nchannels <= 0 or params.sampwidth <= 0):
                return wav_bytes
            frames = w.readframes(params.nframes)
        n_pad = int(params.framerate * tail_sec)
        if n_pad <= 0:
            return wav_bytes
        fw = params.sampwidth * params.nchannels
        fade = b""
        if params.sampwidth == 2 and len(frames) >= fw:
            last = struct.unpack(f"<{params.nchannels}h", frames[-fw:])
            if any(last):
                fade_n = min(n_pad, max(1, int(params.framerate * 0.01)))
                vals = [int(round(s * (1.0 - i / fade_n)))
                        for i in range(1, fade_n + 1) for s in last]
                fade = struct.pack(f"<{len(vals)}h", *vals)
        pad = fade + b"\x00" * (n_pad * fw - len(fade))
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setparams(params)
            w.writeframes(frames + pad)
        return out.getvalue()
    except Exception:
        return wav_bytes


def play_wav_blocking(wav_bytes, stop_event=None):
    """WAVバイト列を同期再生する（ワーカースレッドから呼ぶ想定）。
    stop_event (threading.Event) がセットされたら途中で再生を打ち切る。
    声を最後まで鳴らせたら True、声の途中で止めた（または鳴らさなかった）ら False。
    Windows で末尾に足した無音の間に止めたときは、声は聞き終えているので True。"""
    if stop_event is not None and stop_event.is_set():
        # もう止められた再生。ここで PlaySound を出すと、Windows は再生を
        # プロセスに1本しか持てないので、今鳴っている「次の再生」の音を
        # 差し替えたうえ直後の PURGE で黙らせてしまう（停止→すぐ別の行を
        # 試聴、で音が消えていた）
        return False
    if IS_WIN:
        import winsound
        play_bytes = _pad_wav_for_play(wav_bytes)
        if stop_event is None:
            # SND_MEMORY 同期再生（止める必要がないときはこれで足りる）
            winsound.PlaySound(play_bytes, winsound.SND_MEMORY)
            return True
        # 途中で止められるようにするには非同期再生が要るが、winsound は
        # 「メモリから非同期」を受け付けない（SND_MEMORY|SND_ASYNC は必ず
        # RuntimeError: Cannot play asynchronously from memory になる。
        # CPython の winsound が参照カウントの都合で明示的に禁じている）。
        # そこで一度だけ一時ファイルへ書き、ファイル指定で非同期再生する
        # ――Mac の afplay 経路と同じ考え方。
        _sweep_play_tmp()   # 前回の置き土産をここで片づける
        fd, path = tempfile.mkstemp(prefix="t2v_play_", suffix=".wav")
        stopped = False
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(play_bytes)
            try:
                dur = wav_duration(play_bytes)
                speech = wav_duration(wav_bytes)
            except Exception:
                dur = speech = 0.0   # 長さが読めなくても再生自体は試す
            started = time.monotonic()
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            # 鳴り始めの遅れぶん長めに待つ（無音を足してあるので次の行に語尾を潰されない）
            pad = _WIN_PLAY_TAIL_PAD if dur > 0 else 0.0
            deadline = started + dur + pad
            while time.monotonic() < deadline:
                if stop_event.is_set():
                    # 声が鳴り終わったあと（足した無音の間）に止められたら、鳴らし切った
                    # 扱いにする。ここで PURGE すると、鳴り始めの遅れのぶんまだ残って
                    # いる語尾まで切ってしまう。連続再生も、この行を読み直さずに次へ進む
                    stopped = time.monotonic() - started < speech
                    break
                time.sleep(0.05)
        finally:
            # 音を切るのは「途中で止めたとき」だけ。最後まで鳴らしたときに
            # 切ると、再生が始まるまでのわずかな遅れのぶん末尾が欠ける
            if stopped:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
            _remove_play_tmp(path)
        return not stopped
    if IS_MAC:
        fd, path = tempfile.mkstemp(prefix="t2v_prev_", suffix=".wav")
        stopped = False
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            if stop_event is None:
                subprocess.run(["/usr/bin/afplay", path], check=False)
            else:
                proc = subprocess.Popen(["/usr/bin/afplay", path])
                while proc.poll() is None:
                    if stop_event.is_set():
                        proc.terminate()
                        proc.wait()
                        stopped = True
                        break
                    time.sleep(0.05)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return not stopped
    raise RuntimeError("この環境では再生を利用できません。")


# ============================================================
#  連続再生（Windows）: 1本の音の流れに、行の音声を順に足していく
# ============================================================
# 行ごとに PlaySound を呼ぶやり方には、聞き心地の面で限界があった。
#  - 鳴り始めが少し遅れるので、次の行の PlaySound が前の行の語尾を潰す。
#    語尾の後ろに無音を足して避けると、そのぶん行間が「間」の秒数より延びる
#  - 一時停止は「止めて、再開したら行の頭から読み直す」しかできない
# 連続再生では winmm の waveOut を1つ開き、行の音声と行間の無音を、同じ流れの
# 後ろへ順に足していく。前の音を上書きしないので語尾は切れず、行間は「間」の
# 秒数ちょうど（保存するファイルと同じ）になり、一時停止はその場で止まって続きから
# 鳴る。標準の ctypes だけで動く。音の出力を開けない環境（出力機器の無いPC・CI）
# では OSError になるので、呼び出し側は行ごとの再生に戻す。

_WINMM = None


def _winmm():
    """winmm の関数と構造体を用意する（Windows で初めて使うときに1回だけ）。"""
    global _WINMM
    if _WINMM is not None:
        return _WINMM
    import ctypes
    from types import SimpleNamespace

    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", ctypes.c_uint16), ("nChannels", ctypes.c_uint16),
                    ("nSamplesPerSec", ctypes.c_uint32),
                    ("nAvgBytesPerSec", ctypes.c_uint32),
                    ("nBlockAlign", ctypes.c_uint16), ("wBitsPerSample", ctypes.c_uint16),
                    ("cbSize", ctypes.c_uint16)]

    class WAVEHDR(ctypes.Structure):
        pass
    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint32),
                        ("dwBytesRecorded", ctypes.c_uint32), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", ctypes.c_uint32), ("dwLoops", ctypes.c_uint32),
                        ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]

    class MMTIME(ctypes.Structure):
        # wType と共用体（いちばん大きい SMPTE が8バイト）
        _fields_ = [("wType", ctypes.c_uint32), ("u", ctypes.c_uint32),
                    ("pad", ctypes.c_uint32)]

    dll = ctypes.WinDLL("winmm")
    h = ctypes.c_void_p
    sigs = {
        "waveOutOpen": [ctypes.POINTER(h), ctypes.c_uint32, ctypes.POINTER(WAVEFORMATEX),
                        ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint32],
        "waveOutPrepareHeader": [h, ctypes.POINTER(WAVEHDR), ctypes.c_uint32],
        "waveOutUnprepareHeader": [h, ctypes.POINTER(WAVEHDR), ctypes.c_uint32],
        "waveOutWrite": [h, ctypes.POINTER(WAVEHDR), ctypes.c_uint32],
        "waveOutGetPosition": [h, ctypes.POINTER(MMTIME), ctypes.c_uint32],
        "waveOutPause": [h], "waveOutRestart": [h], "waveOutReset": [h],
        "waveOutClose": [h],
    }
    for name, args in sigs.items():
        fn = getattr(dll, name)
        fn.argtypes = args
        fn.restype = ctypes.c_uint32
    _WINMM = SimpleNamespace(dll=dll, ctypes=ctypes, WAVEFORMATEX=WAVEFORMATEX,
                             WAVEHDR=WAVEHDR, MMTIME=MMTIME)
    return _WINMM


class WaveOutStream:
    """waveOut の出力を1つ開き、PCM を後ろへ足していく音の流れ（Windows専用）。
    position() で鳴らし終えた量（フレーム数）が分かる。1つのスレッドから使うこと。"""

    _WAVE_MAPPER = 0xFFFFFFFF   # 既定の出力機器
    _WHDR_DONE = 0x1
    _TIME_MS, _TIME_SAMPLES, _TIME_BYTES = 1, 2, 4

    def __init__(self, rate, channels=1, sampwidth=2):
        if not IS_WIN:
            raise OSError("waveOut は Windows でだけ使えます")
        w = _winmm()
        c = w.ctypes
        self._w = w
        self.fmt = (int(rate), int(channels), int(sampwidth))
        self.rate = int(rate)
        self.frame_bytes = int(channels) * int(sampwidth)
        wfx = w.WAVEFORMATEX(1, int(channels), self.rate, self.rate * self.frame_bytes,
                             self.frame_bytes, int(sampwidth) * 8, 0)
        handle = c.c_void_p()
        r = w.dll.waveOutOpen(c.byref(handle), self._WAVE_MAPPER, c.byref(wfx), 0, 0, 0)
        if r:
            raise OSError(f"音の出力を開けませんでした（waveOutOpen={r}）")
        self._h = handle
        self._bufs = []        # 出力に渡して、まだ返ってきていない (データ, ヘッダ)
        self.written = 0       # 足したフレーム数
        self.paused = False
        self._pos = 0
        self._last_raw = 0
        self._wraps = 0        # 位置（32bit）の折り返し回数
        self._closed = False

    def write(self, pcm):
        """PCM を流れの後ろへ足す（前の音が鳴り終わるのを待たずに戻る）。"""
        n = len(pcm) - len(pcm) % self.frame_bytes
        if n <= 0 or self._closed:
            return
        w = self._w
        c = w.ctypes
        buf = c.create_string_buffer(bytes(pcm[:n]), n)
        hdr = w.WAVEHDR()
        hdr.lpData = c.cast(buf, c.c_void_p)
        hdr.dwBufferLength = n
        size = c.sizeof(hdr)
        r = w.dll.waveOutPrepareHeader(self._h, c.byref(hdr), size)
        if r:
            raise OSError(f"音の出力に渡せませんでした（waveOutPrepareHeader={r}）")
        r = w.dll.waveOutWrite(self._h, c.byref(hdr), size)
        if r:
            w.dll.waveOutUnprepareHeader(self._h, c.byref(hdr), size)
            raise OSError(f"音の出力に渡せませんでした（waveOutWrite={r}）")
        self._bufs.append((buf, hdr))   # 鳴り終わるまで手放さない
        self.written += n // self.frame_bytes
        self._reap()

    def _reap(self):
        """鳴らし終えて返ってきたバッファを片づける。"""
        c = self._w.ctypes
        keep = []
        for buf, hdr in self._bufs:
            if hdr.dwFlags & self._WHDR_DONE:
                self._w.dll.waveOutUnprepareHeader(self._h, c.byref(hdr), c.sizeof(hdr))
            else:
                keep.append((buf, hdr))
        self._bufs = keep

    def position(self):
        """これまでに鳴らし終えたフレーム数（足した量を超えない）。"""
        if self._closed:
            return self._pos
        w = self._w
        c = w.ctypes
        t = w.MMTIME(self._TIME_SAMPLES, 0, 0)
        if w.dll.waveOutGetPosition(self._h, c.byref(t), c.sizeof(t)):
            return self._pos   # 読めなかったときは前回の値
        raw = t.u
        if self._last_raw - raw > 1 << 31:
            self._wraps += 1   # 32bit の折り返し（24kHz なら約50時間ごと）
        self._last_raw = raw
        raw += self._wraps << 32
        if t.wType == self._TIME_BYTES:
            frames = raw // self.frame_bytes
        elif t.wType == self._TIME_MS:
            frames = raw * self.rate // 1000
        else:
            frames = raw
        self._reap()
        self._pos = max(self._pos, min(frames, self.written))
        return self._pos

    def pause(self):
        """その場で止める（あとから足した音も、resume まで鳴らさない）。"""
        if not self.paused and not self._closed:
            self._w.dll.waveOutPause(self._h)
            self.paused = True

    def resume(self):
        if self.paused and not self._closed:
            self._w.dll.waveOutRestart(self._h)
            self.paused = False

    def close(self):
        """すぐ音を止めて、出力を閉じる。"""
        if self._closed:
            return
        w = self._w
        c = w.ctypes
        w.dll.waveOutReset(self._h)   # 鳴っている音を止め、渡したバッファを返してもらう
        for buf, hdr in self._bufs:
            w.dll.waveOutUnprepareHeader(self._h, c.byref(hdr), c.sizeof(hdr))
        self._bufs = []
        w.dll.waveOutClose(self._h)
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class LineStream:
    """連続再生用: 行の音声を、行間の無音をはさみながら1本の音の流れに並べる。
    open_stream(rate, channels, sampwidth) は WaveOutStream と同じ形のものを返す
    （既定は WaveOutStream。テストでは偽物を渡す）。1つのスレッドから使うこと。
    poll() を小まめに呼ぶと、鳴り始めた行・鳴り終えた行を ("start"|"end", tag) で返す。
    行の形式（サンプリング周波数など）が変わったら、前の音を鳴らし終えてから開き直す。"""

    def __init__(self, open_stream=None, clock=time.monotonic):
        self._open = open_stream or WaveOutStream
        self._clock = clock
        self._stream = None
        self._pending = []     # まだ流れに足していない行 (tag, fmt, pcm, gap秒)
        self._segs = []        # 流れに足した行 [tag, 始まり, 終わり, 始まった, 終わった]
        self._events = []      # poll() で返す、鳴り始め・鳴り終わり
        self._paused = False
        self._idle_since = None   # 流れが鳴り終えて空になった時刻

    def add(self, tag, wav_bytes, gap_sec=0.0):
        """行を予約する。gap_sec は、この行の前に入れる無音の秒数。"""
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            p = w.getparams()
            if p.comptype != "NONE":
                raise ValueError("非圧縮のPCMではないWAVは連続再生できません")
            pcm = w.readframes(p.nframes)
        fmt = (p.framerate, p.nchannels, p.sampwidth)
        self._pending.append((tag, fmt, pcm, max(0.0, float(gap_sec))))
        self._feed()

    def _feed(self):
        """予約した行を、鳴らせるものから流れの後ろへ足す。"""
        while self._pending:
            tag, fmt, pcm, gap = self._pending[0]
            s = self._stream
            if s is not None and s.fmt != fmt:
                pos = s.position()
                if pos < s.written:
                    return   # 形式の違う行は、今の音を鳴らし終えてから
                self._collect(pos)   # 閉じる前に、鳴り終わりを取りこぼさない
                self._close_stream()
            if self._stream is None:
                self._stream = s = self._open(*fmt)
                if self._paused:
                    s.pause()
            self._pending.pop(0)
            if self._idle_since is not None:
                # 合成を待つあいだ流れが空だった時間は、行間の無音に数える
                gap = max(0.0, gap - (self._clock() - self._idle_since))
                self._idle_since = None
            frame_bytes = fmt[1] * fmt[2]
            n_gap = int(round(gap * fmt[0]))
            if n_gap > 0:
                # 8bit PCM は 0x80 が無音（16bit 以上は 0）
                s.write((b"\x80" if fmt[2] == 1 else b"\x00") * (n_gap * frame_bytes))
            start = s.written
            s.write(pcm)
            self._segs.append([tag, start, s.written, False, False])

    def _collect(self, pos):
        """鳴らした位置 pos までに始まった行・終わった行を、返す分にためる。"""
        for seg in self._segs:
            tag, start, end, began, ended = seg
            if not began and (pos > start or pos >= end):
                seg[3] = began = True
                self._events.append(("start", tag))
            if began and not ended and pos >= end:
                seg[4] = True
                self._events.append(("end", tag))
        self._segs = [seg for seg in self._segs if not seg[4]]

    def poll(self):
        """鳴り始めた行・鳴り終えた行を返す。予約した行の流し込みもここで進める。"""
        s = self._stream
        if s is not None:
            self._collect(s.position())
        self._feed()
        s = self._stream
        if s is not None:
            pos = s.position()
            self._collect(pos)
            if pos >= s.written and not self._pending:
                if self._idle_since is None:
                    self._idle_since = self._clock()
            else:
                self._idle_since = None
        events, self._events = self._events, []
        return events

    def remaining(self):
        """まだ鳴らしていない音の秒数（予約しただけの行も含む）。"""
        s = self._stream
        left = (s.written - s.position()) / s.rate if s is not None else 0.0
        for _tag, fmt, pcm, gap in self._pending:
            left += gap + len(pcm) / float(fmt[0] * fmt[1] * fmt[2])
        return left

    def done(self):
        """予約した行を全部鳴らし終えたか。"""
        s = self._stream
        return not self._pending and (s is None or s.position() >= s.written)

    def pause(self):
        self._paused = True
        if self._stream is not None:
            self._stream.pause()

    def resume(self):
        self._paused = False
        if self._stream is not None:
            self._stream.resume()

    def _close_stream(self):
        if self._stream is not None:
            try:
                self._stream.close()
            finally:
                self._stream = None
                self._segs = []

    def close(self):
        """すぐ音を止めて片づける（予約した行も捨てる）。"""
        self._pending = []
        self._close_stream()


# ============================================================
#  VOICEVOX エンジン連携
# ============================================================
# 既定のエンジンURL（VOICEVOX本体の engine_manifest.json の port と同じ）
VOICEVOX_URL = "http://127.0.0.1:50021"


def vv_normalize_url(text):
    """入力されたURLを整える。"http://" を書き忘れても通るようにする
    （素のままだと requests が MissingSchema で落ち、「未接続」という
    無関係な表示になってしまう）。空文字なら空文字を返す。"""
    u = str(text or "").strip().rstrip("/")
    if not u:
        return ""
    if "://" not in u:
        u = "http://" + u
    return u


def _is_local_url(url):
    """このURLが自分のPC（ループバック）を指しているか。"""
    try:
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")


def vv_probe(base_url, timeout=3):
    """エンジンの状態を調べて内訳つきで返す。
    {"ok": bool, "version": str, "status": str, "http": int|None, "detail": str}
    status は次のどれか:
      ok           つながった
      refused      誰も応答しない（VOICEVOXが起動していない）
      timeout      応答が遅い（起動中かもしれない）
      bad_url      URLの形が正しくない
      http_error   応答はあるがエラー（404など）
      not_voicevox 別のソフトがそのポートを使っている
      unknown      それ以外
    vv_check は「版番号 or None」しか返せず、この4つを区別できなかった。"""
    import requests
    from requests import exceptions as rex
    url = vv_normalize_url(base_url)
    if not url:
        return {"ok": False, "version": "", "status": "bad_url",
                "http": None, "detail": "URLが空です"}
    try:
        r = requests.get(url + "/version", timeout=timeout)
    # except の順番が大事: ConnectTimeout は ConnectionError と Timeout の
    # 両方を継承しているので、Timeout を先に見ないと refused に吸われる
    except (rex.MissingSchema, rex.InvalidSchema, rex.InvalidURL) as e:
        return {"ok": False, "version": "", "status": "bad_url",
                "http": None, "detail": str(e)}
    except rex.ConnectTimeout as e:
        # つなぐ段階でのタイムアウト。相手が自分のPCなら「誰もいない」と同じ意味
        # （Windowsでは閉じたポートが拒否を返さず、黙って時間切れになる）。
        # 外のホストなら本当に遅いのかもしれないので分けて扱う
        return {"ok": False, "version": "",
                "status": "refused" if _is_local_url(url) else "timeout",
                "http": None, "detail": str(e)}
    except rex.ReadTimeout as e:
        # つながったが応答が返らない＝起動中でモデルを読んでいる最中など
        return {"ok": False, "version": "", "status": "timeout",
                "http": None, "detail": str(e)}
    except rex.Timeout as e:
        return {"ok": False, "version": "", "status": "timeout",
                "http": None, "detail": str(e)}
    except rex.ConnectionError as e:
        return {"ok": False, "version": "", "status": "refused",
                "http": None, "detail": str(e)}
    except Exception as e:
        return {"ok": False, "version": "", "status": "unknown",
                "http": None, "detail": str(e)}
    if not r.ok:
        # 応答はあるのにエラー＝そのポートで動いているのは別のソフト。
        # 本物のVOICEVOXは /version で失敗しないので、こう案内したほうが早い
        return {"ok": False, "version": "", "status": "not_voicevox",
                "http": r.status_code, "detail": r.reason or ""}
    ver = r.text.strip().strip('"')
    # 版番号らしくない応答＝別のソフトがそのポートで動いている
    if not ver or not re.match(r"^\d", ver) or len(ver) > 40:
        return {"ok": False, "version": "", "status": "not_voicevox",
                "http": r.status_code, "detail": ver[:60]}
    return {"ok": True, "version": ver, "status": "ok",
            "http": r.status_code, "detail": ""}


def vv_check(base_url, timeout=3):
    """エンジン到達確認。バージョン文字列 or None。
    内訳が要るときは vv_probe を使う（この関数は cli.py が使う従来の契約）。"""
    return vv_probe(base_url, timeout=timeout)["version"] or None


def vv_runtime_info_path():
    """VOICEVOX本体が「今どのURLでエンジンを動かしているか」を書き出すファイル。
    ポートが取り合いになったとき本体は別のポートへ逃げるので、決め打ちの
    50021 だけを見ていると見失う。無ければ None。"""
    if IS_WIN:
        base = os.environ.get("APPDATA")
        p = os.path.join(base, "voicevox", "runtime-info.json") if base else None
    elif IS_MAC:
        p = os.path.expanduser(
            "~/Library/Application Support/voicevox/runtime-info.json")
    else:
        p = None
    return p if (p and os.path.exists(p)) else None


def vv_runtime_urls():
    """runtime-info.json に載っているエンジンのURL一覧。読めなければ空リスト。
    ※このファイルはVOICEVOXを終了しても残る（実測確認済み）。つまり
    「書いてある＝生きている」ではないので、必ず vv_probe で確かめること。"""
    p = vv_runtime_info_path()
    if not p:
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        infos = data.get("engineInfos")
        if not isinstance(infos, list):
            return []
        urls = []
        # 名前に VOICEVOX を含むものを先に試す（追加エンジンがある場合の優先順）
        for want_official in (True, False):
            for e in infos:
                if not isinstance(e, dict):
                    continue
                u = vv_normalize_url(e.get("url"))
                is_official = "voicevox" in str(e.get("name", "")).lower()
                if u and is_official == want_official and u not in urls:
                    urls.append(u)
        return urls
    except Exception:
        return []   # 壊れていても起動を止めない


# 探索のときの待ち時間（秒）。ローカルの閉じたポートは拒否ではなく時間切れに
# なるので、候補ごとにこの秒数を丸ごと待つ。短くしすぎると起動直後の
# エンジンを取り逃がすため、実測（応答は1〜2秒）を踏まえて1.2秒にする
DISCOVER_TIMEOUT = 1.2


def vv_find_engine(preferred=None, timeout=DISCOVER_TIMEOUT):
    """候補URLを順に試して、最初に応答したものを (url, probe) で返す。
    どれも駄目なら (None, 最後のprobe結果) を返す。"""
    last = {"ok": False, "version": "", "status": "refused",
            "http": None, "detail": ""}
    for u in vv_discover_urls(preferred):
        p = vv_probe(u, timeout=timeout)
        if p["ok"]:
            return u, p
        last = p
    return None, last


def vv_discover_urls(preferred=None):
    """試す価値のあるエンジンURLを、試す順に並べて返す。
    ポート番号を総当たりで探すことはしない（時間がかかるうえ、ウイルス対策
    ソフトに嫌われる）。runtime-info.json が代替ポートを正確に持っている。"""
    out = []
    for u in [vv_normalize_url(preferred)] + vv_runtime_urls() + [VOICEVOX_URL]:
        if u and u not in out:
            out.append(u)
    return out


def vv_speakers(base_url, timeout=10):
    """[(label, style_id, speaker_uuid)] のリストを返す。
    speaker_uuid は .vvproj 出力の voice.speakerId に使う。"""
    import requests
    r = requests.get(base_url + "/speakers", timeout=timeout)
    r.raise_for_status()
    out = []
    for sp in r.json():
        name = sp.get("name", "")
        sp_uuid = sp.get("speaker_uuid", "")
        for st in sp.get("styles", []):
            out.append((f"{name}（{st.get('name','')}）", st.get("id"), sp_uuid))
    return out


def vv_speaker_sample(base_url, speaker_uuid, style_id, timeout=15):
    """話者スタイルの公式ボイスサンプル（WAVバイト列）を1つ返す。
    /speaker_info の style_infos[].voice_samples（base64）から該当スタイルの先頭を使う。
    サンプルはローカルエンジン同梱の公式音源で、アプリ内の試聴にのみ使う
    （書き出しはしない＝キャラクター利用ガイドラインの範囲内）。"""
    import base64
    import requests
    r = requests.get(base_url + "/speaker_info",
                     params={"speaker_uuid": speaker_uuid,
                             "resource_format": "base64"},
                     timeout=timeout)
    r.raise_for_status()
    for si in r.json().get("style_infos", []):
        if si.get("id") == style_id and si.get("voice_samples"):
            return base64.b64decode(si["voice_samples"][0])
    raise RuntimeError("この声の見本が見つかりませんでした。")


def vv_style_icon(base_url, speaker_uuid, style_id, timeout=10):
    """話者スタイルの公式の顔アイコン（PNG のバイト列・256×256）を返す。無ければ None。
    resource_format=url で受け取ると、音声サンプルや立ち絵まで base64 で丸ごと受け取らずに
    済む（ずんだもんで 10MB → 4KB と、アイコン1枚 約90KB）。古いエンジンはこの指定を
    知らずに base64 で返すので、どちらでも読む。アイコンは、つないでいるエンジンからだけ
    取る。画像はアプリの中で表示するだけで、保存も書き出しもしない（立ち絵と同じく、
    公式素材を配らない）。"""
    import base64
    from urllib.parse import urlsplit
    import requests
    r = requests.get(base_url + "/speaker_info",
                     params={"speaker_uuid": speaker_uuid, "resource_format": "url"},
                     timeout=timeout)
    r.raise_for_status()
    for si in r.json().get("style_infos", []):
        if si.get("id") != style_id:
            continue
        icon = si.get("icon") or ""
        if not icon:
            return None
        if icon.startswith(("http://", "https://")):
            got = requests.get(base_url.rstrip("/") + urlsplit(icon).path, timeout=timeout)
            got.raise_for_status()
            return got.content
        return base64.b64decode(icon)
    return None


def _format_kana(kana: str) -> str:
    """audio_query の kana（AquesTalk風記法）からアクセント記号・区切りを除いて
    人が読みやすい形にする。"""
    return (kana.replace("'", "").replace("_", "")
                .replace("/", " ").replace("？", "?"))


def vv_reading(base_url, text, speaker_id, timeout=15, read_fix=None):
    """VOICEVOXがこのテキストを“どう読むか”（カナ）を返す（誤読チェック用）。
    誤読を見つけたら「読み方辞書...」で直せる。read_fix は speech_text と同じ。"""
    import requests
    text = speech_text(text, read_fix)
    q = requests.post(base_url + "/audio_query",
                      params={"text": text, "speaker": speaker_id},
                      timeout=timeout)
    q.raise_for_status()
    return _format_kana(q.json().get("kana", ""))


def vv_audio_query(base_url, text, speaker_id, timeout=None, read_fix=None):
    """1文ぶんの audio_query（読み・アクセント・調整値の入れ物）をそのまま返す。
    合成はせず、.vvproj にエディタと同じ形の query を埋めるために使う。
    文は speech_text で読み方を直してから渡す（read_fix=False で、そのまま）。"""
    import requests
    text = speech_text(text, read_fix)
    if timeout is None:
        timeout = max(30, 15 + len(text) // 20)
    q = requests.post(base_url + "/audio_query",
                      params={"text": text, "speaker": speaker_id},
                      timeout=timeout)
    q.raise_for_status()
    return q.json()


# ============================================================
#  ふりがな（VOICEVOX が実際に読む読みを、本文の漢字の上に出す）
# ============================================================
# 読みはエンジンの audio_query（実際に読み上げる発音）から作るので、ふりがなと音は
# 必ず一致する。発音は「トオキョオ」「ツズク」のように書き方と違うことがあるので、
# 辞書（Janome。入れたい人だけが入れる）の読みと照らし、同じ音なら辞書の書き方
# （とうきょう・つづく）をひらがなで出す。違えばエンジンの発音をカタカナで出す
# （誤読かもしれない印）。どの漢字にどの読みが付くかは、本文のかなとエンジンの発音を
# 突き合わせて決め、1通りに決まらない所には出さない（違う所に出すくらいなら出さない）。

FURI_OK = "ok"        # 辞書の書き方と、エンジンの発音が一致（ひらがなで出す）
FURI_DIFF = "diff"    # 辞書と違う読み（エンジンの発音をカタカナで。誤読かもしれない）
FURI_RAW = "raw"      # 辞書が無い・辞書に読みが無い（数字・英字など。エンジンの発音）
# これより長い行には出さない（PDF の段落が丸ごと1行のときなど。突き合わせが重くなる）
FURIGANA_MAX_CHARS = 1500

# かな（ヵ・ヶは「三ヶ月」のように漢字の仲間として読むので除く）
_FURI_KANA_RE = re.compile(r"[ぁ-ゖァ-ヴヷ-ヺー]")
# 数字・英字にくっついていると読まれる記号（20% → パーセント、1920×1200 → カケル）
_FURI_READ_SYMBOLS = set("%％×＋+＆&=＝°℃")
# 数字・英字のあいだにあるときだけ、かたまりの一部になる記号（1.25・7/14・10:30）
_FURI_SEPARATORS = set(".．,，:：/／-－_~〜～")
_FURI_VOWEL = {}
for _row, _v in (("アカサタナハマヤラワガザダバパァャ", "ア"),
                 ("イキシチニヒミリギジヂビピィ", "イ"),
                 ("ウクスツヌフムユルグズヅブプゥュヴ", "ウ"),
                 ("エケセテネヘメレゲゼデベペェ", "エ"),
                 ("オコソトノホモヨロヲゴゾドボポォョ", "オ")):
    for _ch in _row:
        _FURI_VOWEL[_ch] = _v
for _v in "アイウエオ":
    _FURI_VOWEL[_v] = _v
_FURI_PAUSE = "、"    # エンジンが記号のところで入れる「間」


def kata_to_hira(s: str) -> str:
    """全角カタカナをひらがなにする（ー などはそのまま）。"""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def _furi_is_alnum(ch):
    """数字・英字か（漢字やかなは含めない）。"""
    return ch.isascii() and ch.isalnum() or "０" <= ch <= "９" or "Ａ" <= ch <= "ｚ"


def furigana_segments(text):
    """本文を「かな」「読まない記号」「その他（漢字・数字・英字など）」の区間
    [種類, 開始, 終了] に分ける。数字や英字にくっついた記号（20%・1.25・2026-10-03）は、
    そのかたまりの一部として読まれうるので「その他」に含める。"""
    n = len(text)
    kinds = []
    for i, ch in enumerate(text):
        if _FURI_KANA_RE.match(ch):
            kinds.append("kana")
        elif ch in _FURI_READ_SYMBOLS:
            near = ((i > 0 and _furi_is_alnum(text[i - 1]))
                    or (i + 1 < n and _furi_is_alnum(text[i + 1])))
            kinds.append("wild" if near else "silent")
        elif ch in _FURI_SEPARATORS:
            between = (0 < i < n - 1 and _furi_is_alnum(text[i - 1])
                       and _furi_is_alnum(text[i + 1]))
            kinds.append("wild" if between else "silent")
        elif ch.isalnum():
            kinds.append("wild")
        else:
            kinds.append("silent")
    segs = []
    for i, kind in enumerate(kinds):
        if segs and segs[-1][0] == kind and kind != "kana":
            segs[-1][2] = i + 1
        else:
            segs.append([kind, i, i + 1])
    return segs


def _furi_kana_options(ch, prev_vowel, particles):
    """本文のかな1文字が、エンジンの発音ではどう出うるか（候補）。"""
    c = hira_to_kata(ch)
    if not particles:
        return [c]               # 辞書の読みは書き方どおり（ー もそのまま）
    if c == "ー":
        return [prev_vowel] if prev_vowel else ["ー"]
    if c == "ハ":
        return ["ハ", "ワ"]      # 助詞の「は」
    if c == "ヘ":
        return ["ヘ", "エ"]      # 助詞の「へ」
    if c == "ヲ":
        return ["オ", "ヲ"]
    if c == "ヂ":
        return ["ジ", "ヂ"]
    if c == "ヅ":
        return ["ズ", "ヅ"]
    if c == "ウ" and prev_vowel == "オ":
        return ["ウ", "オ"]      # そう → ソオ
    if c == "イ" and prev_vowel == "エ":
        return ["イ", "エ"]      # ていねい → テエネエ
    return [c]


def _furi_items(text, base=0, particles=True):
    """突き合わせ用の並び: ("k", 候補) / ("s", 間の有無) / ("w", (開始, 終了, 記号入り))。"""
    items = []
    prev_v = None
    for kind, a, b in furigana_segments(text):
        if kind == "silent":
            if particles:
                items.append(("s", [_FURI_PAUSE, ""]))
            prev_v = None
        elif kind == "wild":
            items.append(("w", (base + a, base + b,
                                any(not c.isalnum() for c in text[a:b]))))
            prev_v = None
        else:
            for ch in text[a:b]:
                opts = _furi_kana_options(ch, prev_v, particles)
                items.append(("k", opts))
                last = opts[0][-1] if opts[0] else ""
                prev_v = _FURI_VOWEL.get(last)
    return items


def furigana_assign(items, pron, score=None):
    """並び items を発音 pron に当てはめ、読みが1通りに決まる漢字などの区間だけを
    {(開始, 終了): 発音} で返す。当てはめ方が1つも無ければ None。
    当てはめ方が何通りもあるときは、全部を調べて（列挙はしない）、どの当てはめ方でも
    同じ区切りになる区間だけを残す。score(区間, 発音) を渡すと、その合計がいちばん
    大きい当てはめ方だけで考える（辞書の読みと合う区切りを優先するため）。
    漢字の区間に「間」（、）は入らない（間は記号からしか生まれない。記号入りの数字の
    かたまりは別）。"""
    n = len(pron)
    K = len(items)

    def ends(k, p):
        t, v = items[k]
        if t != "w":
            for o in dict.fromkeys(v):
                if pron.startswith(o, p):
                    yield p + len(o)
        else:
            if score is not None:
                yield p   # 読まれない字（OCRで混ざる「丿」など）。辞書と照らすときだけ考える
            for q in range(p + 1, n + 1):
                if pron[q - 1] == _FURI_PAUSE and not v[2]:
                    break
                yield q

    def gain(k, p, q):
        t, v = items[k]
        if t != "w" or score is None:
            return 0
        if q == p:
            return -1   # 読まれない、は最後の手段（辞書と合う区間が増えるときだけ選ばれる）
        return score(v[:2], pron[p:q].replace(_FURI_PAUSE, ""))

    # 長い行でも深い再帰にしない（前から届く位置 → 後ろから点数、の2回で求める）
    reach = [set() for _ in range(K + 1)]
    reach[0].add(0)
    for k in range(K):
        for p in reach[k]:
            reach[k + 1].update(ends(k, p))
    if n not in reach[K]:
        return None
    best = [dict() for _ in range(K + 1)]   # best[k][p]: (k, p) から最後までの最高点
    best[K][n] = 0
    for k in range(K - 1, -1, -1):
        row, after = best[k], best[k + 1]
        for p in reach[k]:
            top = None
            for q in ends(k, p):
                if q in after:
                    total = after[q] + gain(k, p, q)
                    if top is None or total > top:
                        top = total
            if top is not None:
                row[p] = top
    if 0 not in best[0]:
        return None
    # いちばん点の高い当てはめ方に乗る (k, p) を前から順にたどり、
    # 区間ごとに「通りうる区切り」を集める
    here = {0}
    cuts = {}
    for k in range(K):
        nxt = set()
        after = best[k + 1]
        for p in here:
            for q in ends(k, p):
                if q in after and after[q] + gain(k, p, q) == best[k][p]:
                    nxt.add(q)
                    if items[k][0] == "w":
                        cuts.setdefault(k, set()).add((p, q))
        here = nxt
    out = {}
    for k, pairs in cuts.items():
        if len(pairs) == 1:
            (p, q), = pairs
            out[items[k][1][:2]] = pron[p:q].replace(_FURI_PAUSE, "")
    return out


def furigana_norm(kata):
    """同じ音になる書き分けをそろえる（比べる用）: ー→前の母音、エイ→エエ、オウ→オオ、
    ヅ→ズ、ヂ→ジ、ヲ→オ。辞書の書き方（トウキョウ）とエンジンの発音（トオキョオ）を
    「同じ音か」で比べるため。"""
    out = []
    for ch in kata.replace("ヅ", "ズ").replace("ヂ", "ジ").replace("ヲ", "オ"):
        prev = _FURI_VOWEL.get(out[-1]) if out else None
        if ch == "ー" and prev:
            out.append(prev)
        elif ch == "イ" and prev == "エ":
            out.append("エ")
        elif ch == "ウ" and prev == "オ":
            out.append("オ")
        else:
            out.append(ch)
    return "".join(out)


def engine_pronunciation(query):
    """audio_query の結果から、エンジンが実際に読む発音（カタカナ）を作る。
    記号のところで入れる「間」は 、 として残す（どこで区切れるかの目印になる）。"""
    out = []
    for ap in query.get("accent_phrases") or []:
        out.extend(m.get("text", "") for m in ap.get("moras") or [])
        if ap.get("pause_mora"):
            out.append(_FURI_PAUSE)
    return "".join(out)


_FURI_DICT = None          # Janome の Tokenizer（入っていなければ False）
_FURI_DICT_LOCK = _threading.Lock()


def furigana_dictionary():
    """ふりがなの表記に使う辞書（Janome）。入っていなければ None。
    初回だけ読み込む（数百ms）。入れ直したあとは reset_furigana_dictionary() で読み直す。"""
    global _FURI_DICT
    with _FURI_DICT_LOCK:
        if _FURI_DICT is None:
            try:
                from janome.tokenizer import Tokenizer
                _FURI_DICT = Tokenizer()
            except Exception:
                _FURI_DICT = False
        return _FURI_DICT or None


def reset_furigana_dictionary():
    """辞書を入れたあと、次に使うときに読み込み直させる。"""
    global _FURI_DICT
    with _FURI_DICT_LOCK:
        _FURI_DICT = None
    import importlib
    importlib.invalidate_caches()


def furigana_dict_spans(text, tokenizer):
    """辞書で引いた読み（書き方どおりのカタカナ）を、本文の区間 {(開始, 終了): 読み} で返す。
    1語の中に漢字のかたまりが複数あれば、送り仮名を目印に分ける（取り出す → 取|出）。"""
    out = {}
    pos = 0
    with _FURI_DICT_LOCK:
        tokens = [(t.surface, t.reading) for t in tokenizer.tokenize(text)]
    for surf, reading in tokens:
        a0 = pos
        pos += len(surf)
        if not reading or reading == "*":
            continue
        items = _furi_items(surf, a0, particles=False)
        if not any(t == "w" for t, _ in items):
            continue
        out.update(furigana_assign(items, reading) or {})
    return out


def _furi_dict_reading(span, dict_spans):
    """本文の区間 span を、すき間なくちょうど覆う辞書の読みのつながり（無ければ ""）と、その区切り。"""
    a, b = span
    parts = sorted(x for x in dict_spans if a <= x[0] and x[1] <= b)
    if not parts or parts[0][0] != a or parts[-1][1] != b:
        return "", []
    for x, y in zip(parts, parts[1:]):
        if x[1] != y[0]:
            return "", []
    return "".join(dict_spans[x] for x in parts), parts


_FURI_SOKUON_HEAD = set("カキクケコサシスセソタチツテトハヒフヘホパピプペポ")
_FURI_H_TO_P = dict(zip("ハヒフヘホ", "パピプペポ"))


def _furi_dict_match(span, dict_spans, got):
    """本文の区間 span の辞書の読みが、エンジンの発音 got と同じ音か。同じなら、語ごとの
    [(区間, 読み)] を返す（違えば None）。語のつなぎ目でつまる音になる読み（第一章 →
    ダイ｜イチ｜ショウ が ダイ｜イッ｜ショウ、十本 → ジッ｜ポン）も、同じ読みとして扱う。
    前は別の読みと見なして、誤読の候補としてカタカナで出していた。"""
    want, parts = _furi_dict_reading(span, dict_spans)
    if not want:
        return None
    target = furigana_norm(got)
    if furigana_norm(want) == target:
        return [(x, dict_spans[x]) for x in parts]
    if not 2 <= len(parts) <= 6:
        return None
    reads = [dict_spans[x] for x in parts]

    def forms(i, shifted):
        """i 番目の語の読みの候補 [(読み, 次の語がパ行に変わりうるか)]。"""
        r = reads[i]
        heads = [r]
        if shifted and r[:1] in _FURI_H_TO_P:
            heads.append(_FURI_H_TO_P[r[0]] + r[1:])   # イッ｜ポン
        out = []
        for h in heads:
            out.append((h, False))
            nxt = reads[i + 1][:1] if i + 1 < len(reads) else ""
            if nxt in _FURI_SOKUON_HEAD and len(h) >= 2:
                if h[-1] in "チツクキ":
                    out.append((h[:-1] + "ッ", True))
                elif h.endswith("ジュウ"):
                    out.extend([(h[:-3] + "ジュッ", True), (h[:-3] + "ジッ", True)])
        return out

    def walk(i, shifted, acc):
        if i == len(reads):
            return acc if furigana_norm("".join(acc)) == target else None
        for r, sh in forms(i, shifted):
            hit = walk(i + 1, sh, acc + [r])
            if hit:
                return hit
        return None

    hit = walk(0, False, [])
    return list(zip(parts, hit)) if hit else None


def furigana(text, query, dict_spans=None):
    """本文 text と、その audio_query の結果から、ふりがなの一覧
    [(開始, 終了, 読み, 種類)] を作る。種類は FURI_OK / FURI_DIFF / FURI_RAW。
    dict_spans は furigana_dict_spans の結果（辞書が無ければ None。すべてカタカナになる）。
    読みが1通りに決まらない区間には出さない。"""
    if len(text) > FURIGANA_MAX_CHARS:
        return []
    pron = engine_pronunciation(query)
    dict_spans = dict_spans or {}

    def agrees(span, got):
        # 辞書と合う区間1つは、読まれない字1つ（-1）より重い
        return 2 if _furi_dict_match(span, dict_spans, got) else 0

    # 区切り方が何通りもあるときは、辞書の読みと合う区間が多い区切り方を選ぶ。
    # それでも区切り方しだいで読みが変わる区間には出さない
    spans = furigana_assign(_furi_items(text), pron, agrees if dict_spans else None)
    out = []
    for span in sorted(spans or {}):
        got = spans[span]
        if not got:
            continue   # 読まれない字には出さない
        want, parts = _furi_dict_reading(span, dict_spans)
        match = _furi_dict_match(span, dict_spans, got)
        if match:
            # 辞書の語ごとに分けて出す（連続再生 → れんぞく｜さいせい）
            out.extend((x[0], x[1], kata_to_hira(r), FURI_OK) for x, r in match)
        else:
            out.append((span[0], span[1], got, FURI_DIFF if want else FURI_RAW))
    return out


def vv_furigana(base_url, text, speaker_id, use_dictionary=True, timeout=15,
                read_fix=None):
    """1行ぶんのふりがなを作る（エンジンに audio_query を1回だけ問い合わせる）。
    エンジンには読み方を直した文を聞き、その読みを本文の字に合わせる（音と同じ読みになる）。"""
    query = vv_audio_query(base_url, text, speaker_id, timeout=timeout,
                           read_fix=read_fix)
    tok = furigana_dictionary() if use_dictionary else None
    spans = furigana_dict_spans(text, tok) if tok else None
    return furigana(text, query, spans)


def estimate_read_seconds(text: str, speed: float = 1.0) -> float:
    """読み上げ時間のめやす（秒）。話速1.0で約320字/分の概算。
    行間の無音は含まない“ざっくり値”（表示には「めやす」と添えること）。"""
    n = len([c for c in text if not c.isspace()])
    return n / (320.0 / 60.0) / max(float(speed), 0.1)


def is_memo_line(line: str) -> bool:
    """行頭が # / ＃ の「メモ行」（読み上げ・音声生成・vvproj出力の対象外）か。
    GUI・CLI・時間概算の全経路がこの1関数で判定を共有する。"""
    return str(line).strip().startswith(("#", "＃"))


def speakable_text(text: str) -> str:
    """実際に読み上げ対象になる部分だけを返す（＃メモ行を除き、行頭の@話者タグを剥がす）。
    読み上げ時間の概算（estimate_read_seconds）が台本のメモやタグで水増しされないための前処理。"""
    out = []
    for ln in str(text).split("\n"):
        s = ln.strip()
        if not s or is_memo_line(s):
            continue
        name, rest = parse_speaker_tag(s)
        s = rest.strip() if name is not None else s
        if s:
            out.append(s)
    return "\n".join(out)


def voicevox_credit(speaker_labels) -> str:
    """VOICEVOX利用規約が求めるクレジット表記（音声の公開時に必要）の例文を作る。
    話者ラベル「ずんだもん（ノーマル）」からキャラ名を取り出し、重複なく列挙する。"""
    names = []
    for lb in speaker_labels:
        name = str(lb).split("（")[0].strip()
        if name and name not in names:
            names.append(name)
    return "、".join(f"VOICEVOX:{n}" for n in names)


def vv_synthesize_with_kana(base_url, text, speaker_id, speed=1.0,
                            pitch=0.0, intonation=1.0, volume=1.0, timeout=None,
                            read_fix=None):
    """1文を合成し (WAVバイト列, 読みカナ) を返す。audio_query は1回だけ実行し、
    その kana を読み確認（vv_reading 相当）に再利用する（試聴の往復削減）。
    timeout 省略時は文長に比例（整形で生まれた数千字の1行が60秒固定で全滅しない保険）。
    speed=速さ(0.5〜2) / pitch=高さ(-0.15〜0.15) / intonation=抑揚(0〜2) / volume=音量(0〜2)
    文は speech_text で読み方を直してから渡す（read_fix=False で、そのまま）。"""
    import requests
    text = speech_text(text, read_fix)
    if timeout is None:
        timeout = max(60, 30 + len(text) // 10)
    q = requests.post(base_url + "/audio_query",
                      params={"text": text, "speaker": speaker_id}, timeout=timeout)
    q.raise_for_status()
    query = q.json()
    reading = _format_kana(query.get("kana", ""))
    if speed and speed != 1.0:
        query["speedScale"] = float(speed)
    if pitch:
        query["pitchScale"] = float(pitch)
    if intonation != 1.0:
        query["intonationScale"] = float(intonation)
    if volume != 1.0:
        query["volumeScale"] = float(volume)
    s = requests.post(base_url + "/synthesis",
                      params={"speaker": speaker_id},
                      json=query,
                      headers={"Content-Type": "application/json"},
                      timeout=timeout)
    s.raise_for_status()
    return s.content, reading


def vv_synthesize_one(base_url, text, speaker_id, speed=1.0,
                      pitch=0.0, intonation=1.0, volume=1.0, timeout=None,
                      read_fix=None):
    """1文を合成してWAVバイト列を返す（vv_synthesize_with_kana の音声のみ版）。"""
    return vv_synthesize_with_kana(base_url, text, speaker_id, speed=speed,
                                   pitch=pitch, intonation=intonation,
                                   volume=volume, timeout=timeout,
                                   read_fix=read_fix)[0]


# ============================================================
#  行単位の合成キャッシュ（修正した行だけ再合成するための仕組み）
# ============================================================
# 同じ (テキスト, スタイル, 話速/音高/抑揚/音量, エンジン版, 辞書内容) の合成結果を
# voice_cache/ にWAVで保存し、再生成・試聴・連続再生で再利用する。辞書やエンジンを
# 変えるとキーが変わるため明示的な無効化は不要（古いファイルはLRUで消える）。
SYNTH_CACHE_DIR = os.path.join(APP_DIR, "voice_cache")
_SYNTH_CACHE_MAX_BYTES = 500 * 1024 * 1024   # 上限（既定500MB。settingsで変更可）
# この時刻以降に触れた（読んだ/書いた）エントリはLRU削除から保護する。
# 生成実行中に「自分の前半行を自分で追い出す」自己破壊を防ぐ（音声は約173MB/時
# なので、3時間超の本は上限より大きくなり得る）。実行終了時に 0 に戻す
_synth_cache_protect_since = 0.0
_synth_cache_put_count = 0   # evict間引き用（putごとの全stat走査はO(N^2)になる）


def set_synth_cache_limit(mb, evict=False):
    """キャッシュ上限をMB単位で設定する（settings.json の synth_cache_mb から）。
    10時間級の本を常用するなら2000MB程度にすると再生成が本当に即完了になる。
    evict=True で設定直後に超過分を削除する（上限を下げた直後に反映させる用途。
    put間引き・ジョブ終了時のevictを待たずに効かせる）。"""
    global _SYNTH_CACHE_MAX_BYTES
    try:
        mb = int(mb)
    except (TypeError, ValueError):
        return
    if 50 <= mb <= 100000:
        _SYNTH_CACHE_MAX_BYTES = mb * 1024 * 1024
        if evict:
            _synth_cache_evict()


def synth_cache_protect(since_ts):
    """since_ts 以降に触れたエントリをLRU削除から保護する（0.0で解除）。
    合成ジョブの開始時に time.time() を渡し、終了時に 0.0 で解除する。"""
    global _synth_cache_protect_since
    _synth_cache_protect_since = float(since_ts)
    if not since_ts:
        _synth_cache_evict()   # 解除時に一度だけ上限へ戻す


def synth_cache_stats():
    """(ファイル数, 合計バイト数) を返す（キャッシュ管理UI用）。"""
    n = total = 0
    try:
        for name in os.listdir(SYNTH_CACHE_DIR):
            if name.endswith(".wav"):
                try:
                    total += os.stat(os.path.join(SYNTH_CACHE_DIR, name)).st_size
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n, total


def synth_cache_clear():
    """キャッシュを全削除して削除件数を返す。高速化専用の層なのでいつ消しても安全
    （必要になれば合成し直されるだけ）。"""
    n = 0
    try:
        for name in os.listdir(SYNTH_CACHE_DIR):
            if name.endswith((".wav", ".tmp")):
                try:
                    os.remove(os.path.join(SYNTH_CACHE_DIR, name))
                    n += 1
                except OSError:
                    pass
    except OSError:
        pass
    return n


def synth_cache_key(text, style_id, speed, pitch, intonation, volume,
                    engine_ver="", dict_hash=""):
    """行合成キャッシュのキー（sha1）。読みに影響する全入力を含める。"""
    import hashlib
    payload = "\x1f".join([str(text), str(style_id),
                           f"{float(speed):.3f}", f"{float(pitch):.3f}",
                           f"{float(intonation):.3f}", f"{float(volume):.3f}",
                           str(engine_ver), str(dict_hash)])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def vv_warm_speaker(base_url, style_id, timeout=120):
    """話者の音声モデルをエンジンに読み込ませておく（VOICEVOXエディタと同じ手）。
    読み込みは最初の合成のときに暗黙で走るので、何もしないと「押してから
    最初の音が出るまで」だけ極端に遅い（実測 2.05秒。温めておくと 0.60秒）。
    skip_reinit=true なので、既に読み込み済みならエンジン側で素通りする。
    失敗しても黙って False を返す（温まっていないだけで、動作には影響しない）。"""
    import requests
    try:
        r = requests.post(base_url + "/initialize_speaker",
                          params={"speaker": int(style_id),
                                  "skip_reinit": "true"},
                          timeout=timeout)
        return r.status_code < 400
    except Exception:
        return False


def vv_dict_hash(base_url, timeout=10):
    """ユーザー辞書全体の内容ハッシュ（合成キャッシュのキー用）。
    読みに影響し得る全フィールド（表記・読み・アクセント・品詞・優先度）を含める
    （VOICEVOXエディタ側で「優先度」だけ変えた場合もキャッシュが正しく切り替わる）。
    取得に失敗したら ""（＝vv_synthesize_cached がキャッシュを素通し）を返す。
    以前は毎回変わる一意値を返していたが、それだと「絶対にヒットしないキーで
    putし続ける」＝キャッシュ汚染＋有効エントリのLRU押し出しになっていた。"""
    import hashlib
    import requests
    try:
        r = requests.get(base_url + "/user_dict", timeout=timeout)
        r.raise_for_status()
        parts = []
        for word_uuid, w in sorted(r.json().items()):
            parts.append("\x1e".join(
                str(w.get(k, "")) for k in ("surface", "pronunciation",
                                            "accent_type", "word_type",
                                            "priority")))
        return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()
    except Exception:
        return ""


def synth_cache_get(key):
    """キャッシュ済みWAVを返す（無ければ None）。ヒット時は mtime を更新して
    LRUの新しい側へ移す。中身がWAVでない残骸（電源断等）は削除してミス扱い。"""
    path = os.path.join(SYNTH_CACHE_DIR, key + ".wav")
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) <= 44 or data[:4] != b"RIFF":
            os.remove(path)   # 壊れたエントリは自己回復（合成し直される）
            return None
        os.utime(path, None)
        return data
    except OSError:
        return None


def synth_cache_put(key, wav_bytes):
    """WAVをキャッシュへ保存し、総量が上限を超えたら古い順に削除する。
    一時ファイル名は一意にする（同じ行を3並列で合成した場合の書き込み衝突防止）。
    失敗は無視（キャッシュは高速化のためだけの層で、無くても結果は同じ）。"""
    if not wav_bytes or wav_bytes[:4] != b"RIFF":
        return   # WAVでないものはキャッシュ汚染になるだけなので保存しない
    tmp = None
    try:
        os.makedirs(SYNTH_CACHE_DIR, exist_ok=True)
        path = os.path.join(SYNTH_CACHE_DIR, key + ".wav")
        fd, tmp = tempfile.mkstemp(dir=SYNTH_CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        os.replace(tmp, path)
        tmp = None
        # evictはput 50回に1回に間引く（毎回のlistdir+全statは数千行の本で
        # O(N^2)になる）。ジョブ終了時の synth_cache_protect(0.0) でも走る
        global _synth_cache_put_count
        _synth_cache_put_count += 1
        if _synth_cache_put_count % 50 == 0:
            _synth_cache_evict()
    except OSError:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _synth_cache_evict(max_bytes=None):
    """キャッシュ総量が上限を超えていたら mtime の古い順に削除する。
    クラッシュで残った古い .tmp も掃除する（1日以上前のもの）。"""
    if max_bytes is None:
        max_bytes = _SYNTH_CACHE_MAX_BYTES
    try:
        entries = []
        total = 0
        now = time.time()
        for name in os.listdir(SYNTH_CACHE_DIR):
            p = os.path.join(SYNTH_CACHE_DIR, name)
            if name.endswith(".tmp"):
                try:
                    if now - os.stat(p).st_mtime > 86400:
                        os.remove(p)
                except OSError:
                    pass
                continue
            if not name.endswith(".wav"):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
            total += st.st_size
        if total <= max_bytes:
            return
        for mtime, size, p in sorted(entries):
            # 実行中ジョブが触れたエントリは削除しない（getがutimeで更新するため
            # 「今回読んだ/書いた行」が自然に保護される。上限は実行中だけ一時的に
            # 本のサイズまで膨らみ、解除後のevictで古い実行分から削られる）
            if _synth_cache_protect_since and mtime >= _synth_cache_protect_since:
                continue
            try:
                os.remove(p)
                total -= size
            except OSError:
                pass
            if total <= max_bytes:
                return
    except OSError:
        pass


def vv_synthesize_cached(base_url, text, speaker_id, speed=1.0, pitch=0.0,
                         intonation=1.0, volume=1.0, timeout=None,
                         engine_ver="", dict_hash="", read_fix=None):
    """vv_synthesize_one のキャッシュ付き版。engine_ver / dict_hash が空のときは
    キーの精度が担保できないためキャッシュせず素通しする。
    キーは読み方を直した後の文（エンジンへ実際に渡す文）で作る。直しのオン・オフを
    切り替えたときに、前の読みの声が出てこないように。"""
    text = speech_text(text, read_fix)
    if not engine_ver or not dict_hash:
        return vv_synthesize_one(base_url, text, speaker_id, speed=speed,
                                 pitch=pitch, intonation=intonation,
                                 volume=volume, timeout=timeout, read_fix=False)
    key = synth_cache_key(text, speaker_id, speed, pitch, intonation, volume,
                          engine_ver, dict_hash)
    wav = synth_cache_get(key)
    if wav is not None:
        return wav
    wav = vv_synthesize_one(base_url, text, speaker_id, speed=speed,
                            pitch=pitch, intonation=intonation,
                            volume=volume, timeout=timeout, read_fix=False)
    synth_cache_put(key, wav)
    return wav


# RIFF/WAVの32bitサイズ上限。超えるとwaveモジュールのヘッダ確定がstruct.errorになり
# 全行合成後の保存フェーズで全損するため、書き込み前に見積もって明示エラーにする
_WAV_MAX_DATA = 0xFFFFFFFF - 44


def concat_wavs_to_file(sources, out_path, gap_sec=0.4, chunk_frames=1 << 18,
                        progress_cb=None):
    """WAV（bytes または ファイルパス）の列を無音を挟んで out_path へ逐次連結し、
    各ソースの再生秒のリストを返す（SRT・チャプター計算にそのまま使える）。
    gap_sec は全部の行で同じ秒数か、行ごとのリスト（line_gaps。i 番目の行の前の無音）。
    全体をメモリに持たないため、10時間級の本でもメモリは1チャンク分で済む。
    合計が4GB（RIFF上限）を超える場合は書く前に RuntimeError（保存フェーズ全損防止）。"""
    durations = []
    out = wave.open(out_path, "wb")
    params, silence, written = None, b"", 0
    nsrc = len(sources) if hasattr(sources, "__len__") else 0
    try:
        for i, src in enumerate(sources):
            if progress_cb:
                progress_cb(i, nsrc)   # 長い本ではここだけで何分もかかる
            f = (io.BytesIO(src) if isinstance(src, (bytes, bytearray))
                 else open(src, "rb"))
            with f, wave.open(f, "rb") as w:
                if params is None:
                    params = w.getparams()
                    out.setparams(params)
                    fw = params.sampwidth * params.nchannels
                if i:
                    silence = b"\x00" * (int(params.framerate * _gap_at(gap_sec, i)) * fw)
                n = w.getnframes()
                durations.append(n / float(params.framerate))
                need = (n * params.sampwidth * params.nchannels
                        + (len(silence) if i else 0))
                if written + need > _WAV_MAX_DATA:
                    raise RuntimeError(
                        "1つにまとめたWAVが4GB（WAV形式の上限）を超えます。"
                        "M4A/M4B/MP3にするか、まとめ方を分割にしてください。")
                if i and silence:
                    # 無音は「各ソースの前（先頭以外）」＝従来の「各ソースの後
                    # （末尾以外）」とバイト同一
                    out.writeframesraw(silence)
                    written += len(silence)
                while True:
                    chunk = w.readframes(chunk_frames)
                    if not chunk:
                        break
                    out.writeframesraw(chunk)
                    written += len(chunk)
    finally:
        out.close()   # closeでRIFFヘッダのサイズが確定する（waveがseekしてパッチ）
    return durations


def concat_wavs(wav_bytes_list, gap_sec=0.4):
    """同一フォーマットのWAVバイト列を無音を挟んで連結し、WAVバイト列を返す。
    gap_sec は全部の行で同じ秒数か、行ごとのリスト（line_gaps）。"""
    if not wav_bytes_list:
        return b""
    params = None
    frames = []
    for wb in wav_bytes_list:
        with wave.open(io.BytesIO(wb), "rb") as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))
    fw = params.sampwidth * params.nchannels
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        for i, fr in enumerate(frames):
            if i:
                w.writeframes(b"\x00" * int(params.framerate * _gap_at(gap_sec, i)) * fw)
            w.writeframes(fr)
    return out.getvalue()


# ---- 行と行のあいだの無音（段落・章の切れ目は長め） ----
# 本を聴くとき、話の区切りで一息つけるように、段落が変わる所（空行のあと）と章の
# 見出しの前は、文と文のあいだより長く空ける。どちらも「文の間」に比例させ、間を
# 0 にしている人（動画の素材づくりなど）では、どこも 0 のまま。足す分には上限を付け、
# 文の間を長くしている人で伸びすぎないようにする
PARA_GAP_FACTOR = 2.5       # 段落の切れ目は、文の間の何倍か（0.4秒 → 1.0秒）
CHAPTER_GAP_FACTOR = 5.0    # 章の見出しの前は、文の間の何倍か（0.4秒 → 2.0秒）
PARA_GAP_MAX_EXTRA = 2.0    # 段落の切れ目で足す秒数の上限
CHAPTER_GAP_MAX_EXTRA = 4.0  # 章の見出しの前で足す秒数の上限


def paragraph_ids(lines):
    """各行の段落番号。空行のあとと、行頭の字下げ（全角スペース。小説や Web 小説の段落の
    頭）で次の段落になる。字下げも見るので、空行を消した本（「空行を削除」は最初からオン）
    でも、段落の切れ目で長めの間を取れる（以前は空行だけを見ていて、取り出した本では
    章の前しか長くならなかった）。空行は読まないので、空行の番号は使われない。"""
    out = []
    para = 0
    prev_text = False   # 直前の行に文字があるか（空行のあとの字下げで2回数えない）
    for ln in lines:
        if not ln.strip():
            para += 1
            prev_text = False
        else:
            if prev_text and ln.lstrip(" \t").startswith("　"):
                para += 1
            prev_text = True
        out.append(para)
    return out


def line_breaks(lines, para_ids):
    """各行の前の切れ目の種類のリスト。""＝文と文（同じ段落）／"para"＝段落が変わる
    （空行のあと・行頭の字下げ）／"chapter"＝章の見出し（第一章・プロローグ など）。
    先頭の行は ""。para_ids は各行の段落番号（paragraph_ids で作る）。"""
    out = []
    for i, ln in enumerate(lines):
        if i == 0:
            out.append("")
        elif is_chapter_heading(ln):
            out.append("chapter")
        elif para_ids[i] != para_ids[i - 1]:
            out.append("para")
        else:
            out.append("")
    return out


def break_gap(kind, gap):
    """切れ目の種類 kind（line_breaks）の前に入れる無音の秒数。文と文は gap のまま。"""
    gap = max(0.0, float(gap))
    if kind == "chapter":
        return gap + min(gap * (CHAPTER_GAP_FACTOR - 1), CHAPTER_GAP_MAX_EXTRA)
    if kind == "para":
        return gap + min(gap * (PARA_GAP_FACTOR - 1), PARA_GAP_MAX_EXTRA)
    return gap


def line_gaps(lines, para_ids, gap, longer=True):
    """各行の前に入れる無音（秒）のリスト（先頭の行の前は 0）。longer なら段落・章の
    切れ目を長めにし、longer=False なら、どこも gap（以前と同じ）。結合・字幕・章の
    時刻（concat_wavs_to_file・make_srt・build_chapters）に、そのまま渡せる。"""
    kinds = line_breaks(lines, para_ids) if longer else [""] * len(lines)
    return [0.0 if i == 0 else break_gap(k, gap) for i, k in enumerate(kinds)]


def _gap_at(gap_sec, i):
    """i 番目の行の前に入れる無音（秒）。gap_sec は全部の行で同じ秒数か、行ごとの
    リスト（line_gaps）。先頭の行の前には入れない。"""
    if i <= 0:
        return 0.0
    if isinstance(gap_sec, (list, tuple)):
        return float(gap_sec[i])
    return float(gap_sec)


# ============================================================
#  行ごとの話者割り当て（@タグ / セリフ検出）
# ============================================================
_SPEAKER_TAG = re.compile(r"^@([^:：]+)[:：]\s*(.*)$")


def parse_speaker_tag(line: str):
    """行頭の話者タグ「@話者名: テキスト」を解析して (話者名 or None, テキスト) を返す。
    タグが無ければ (None, 元の行)。区切りは半角/全角コロン両対応。"""
    m = _SPEAKER_TAG.match(line.strip())
    if not m:
        return None, line
    return m.group(1).strip(), m.group(2).strip()


def is_dialogue_line(line: str) -> bool:
    """セリフ行（「…」『…』で始まる行）か。会話文の自動話者振り分けに使う。"""
    s = line.strip()
    return s.startswith("「") or s.startswith("『")


def unresolved_speaker_tags(text, speakers):
    """本文中の @話者タグのうち、話者一覧に解決できないものを
    [(行番号(1始まり), 話者名), ...] で返す（タイプミスの事前検知用）。
    現状の仕様では未解決タグは行全体（タグ文字列ごと）を既定話者が読むため、
    長編の掛け合い台本では全編を聴き直すまで気づけない。合成前に指摘する。"""
    out = []
    for i, ln in enumerate(str(text).split("\n"), start=1):
        s = ln.strip()
        if not s or is_memo_line(s):
            continue
        name, _rest = parse_speaker_tag(s)
        if name is not None and resolve_speaker(name, speakers) is None:
            out.append((i, name))
    return out


def resolve_speaker(name: str, speakers):
    """話者名からvv_speakers()の要素を探す。
    完全一致 → 前方一致（スタイル省略時は最初のスタイル） → 部分一致 の順。"""
    if not name:
        return None
    for sp in speakers:
        if sp[0] == name:
            return sp
    for sp in speakers:
        if sp[0].startswith(name):
            return sp
    for sp in speakers:
        if name in sp[0]:
            return sp
    return None


# ============================================================
#  音声エンコード（WAV → M4A / MP3）
# ============================================================
def audio_encoders():
    """この環境で使える出力形式と変換コマンドを {"m4a": ..., "mp3": ...} で返す。
    Mac: afconvert(OS標準)でM4A。ffmpegがあればMP3も。Win: ffmpeg があればそれ、無ければ
    Windows に入っている変換（audio_win.ps1。Windows 10 以降）で両方。その他: ffmpegがあれば両方。
    前は Windows に ffmpeg が無いと WAV しか選べず、なぜ選べないかも分からなかった。"""
    import shutil
    enc = {}
    if IS_MAC and os.path.exists("/usr/bin/afconvert"):
        enc["m4a"] = "afconvert"
    ff = shutil.which("ffmpeg")
    if ff:
        enc.setdefault("m4a", ff)
        enc["mp3"] = ff
    elif IS_WIN and os.path.isfile(AUDIO_PS1) and sys.getwindowsversion().major >= 10:
        enc["m4a"] = enc["mp3"] = WIN_ENCODER
    return enc


def encode_audio_file(wav_path, out_path, fmt, encoders=None, keep_input=False):
    """WAVファイルを M4A/MP3 に変換して out_path に保存する。fmt="wav" は移動/コピー
    （同一ボリュームなら一瞬・原子的）。fmt="m4b" は中身がM4Aと同一のAAC/MP4なので
    m4aとして変換する（拡張子だけ .m4b。チャプターは呼び出し側が後付けする）。
    ファイル入力なので巨大WAVでもメモリを消費しない（encode_audio のファイル版）。"""
    if fmt == "wav":
        if os.path.abspath(wav_path) != os.path.abspath(out_path):
            if keep_input:
                shutil.copyfile(wav_path, out_path)
            else:
                shutil.move(wav_path, out_path)
        return
    if fmt == "m4b":
        fmt = "m4a"
    if encoders is None:
        encoders = audio_encoders()
    cmd_or_path = encoders.get(fmt)
    if not cmd_or_path:
        raise RuntimeError(f"{fmt.upper()}への変換ツールが見つかりません。")
    if cmd_or_path == "afconvert":
        cmd = ["/usr/bin/afconvert", wav_path, "-f", "m4af", "-d", "aac", out_path]
    elif cmd_or_path == WIN_ENCODER:
        cmd = [_find_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", AUDIO_PS1, "-In", os.path.abspath(wav_path),
               "-Out", os.path.abspath(out_path), "-Fmt", fmt]
    elif fmt == "mp3":
        cmd = [cmd_or_path, "-y", "-loglevel", "error", "-i", wav_path,
               "-codec:a", "libmp3lame", "-q:a", "2", out_path]
    else:  # ffmpegでm4a
        cmd = [cmd_or_path, "-y", "-loglevel", "error", "-i", wav_path,
               "-codec:a", "aac", out_path]
    # timeout: WAV長（24kHz/16bit/mono≈48KB/s）に比例＋余裕。変換ツールが固まった
    # ままUIが永久busyになるのを防ぐ（timeout時は子プロセスもkillされる）
    try:
        secs = os.path.getsize(wav_path) / 48000
    except OSError:
        secs = 600
    # encoding/errors 指定なしだと、失敗時に ffmpeg が日本語パスをUTF-8で
    # 吐き返したとき cp932 厳格デコードが例外を投げ、下の RuntimeError に届かない。
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=120 + int(secs),
                              creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{fmt.upper()}への変換がタイムアウトしました"
                           "（変換ツールが応答しません）")
    if proc.returncode != 0 or not os.path.exists(out_path):
        if cmd_or_path == WIN_ENCODER:
            try:   # 先に空のファイルを作るので、失敗したら残さない
                os.remove(out_path)
            except OSError:
                pass
        raise RuntimeError(f"変換失敗: {proc.stderr or proc.stdout or 'unknown'}")


def encode_audio(wav_bytes, out_path, fmt, encoders=None):
    """WAVバイト列を保存/変換する（encode_audio_file の互換ラッパ）。"""
    if fmt == "wav":
        with open(out_path, "wb") as f:
            f.write(wav_bytes)
        return
    fd, tmp = tempfile.mkstemp(prefix="t2v_enc_", suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        encode_audio_file(tmp, out_path, fmt, encoders)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


# ============================================================
#  VOICEVOX ユーザー辞書（読み方の登録）
# ============================================================
def hira_to_kata(s: str) -> str:
    """ひらがなを全角カタカナに変換する（辞書の読みはカタカナ必須のため）。"""
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)
# 単語の種類（word_type）と、エンジンが返す品詞の対応。
# エンジンは POST/PUT では word_type を受け取るが、GET /user_dict では返さず
# part_of_speech（品詞）としてしか見えない。更新のときに元の種類を引き継ぐには
# ここから逆引きする必要がある。実機(0.25.2)で1件ずつ登録して確認した対応。
_WORD_TYPE_BY_POS = {
    ("名詞", "固有名詞"): "PROPER_NOUN",
    ("名詞", "一般"): "COMMON_NOUN",
    ("動詞", "自立"): "VERB",
    ("形容詞", "自立"): "ADJECTIVE",
    ("名詞", "接尾"): "SUFFIX",
}
# 画面に出す名前（内部値は英語のまま送る）
WORD_TYPE_LABELS = [
    ("PROPER_NOUN", "固有名詞"),
    ("COMMON_NOUN", "普通名詞"),
    ("VERB", "動詞"),
    ("ADJECTIVE", "形容詞"),
    ("SUFFIX", "語尾"),
]


def word_type_of(word):
    """GET /user_dict の1語から word_type を逆引きする。分からなければ None。
    None のときは更新時に word_type を送らない＝エンジンの既定に任せる。"""
    key = (str(word.get("part_of_speech", "")),
           str(word.get("part_of_speech_detail_1", "")))
    return _WORD_TYPE_BY_POS.get(key)


def dict_surface_key(s):
    """辞書の重複を調べるための、表記の比較用キー。
    エンジンは登録時に表記を正規化するので、こちらが打った "ABC" が
    エンジン側では "ＡＢＣ" になっていることがある。素の文字列比較だと
    「登録済みなのに見つからない」＝二重登録になるため、幅と大小をそろえて比べる。
    ※比較にだけ使う。登録する値そのものは加工しない。"""
    return unicodedata.normalize("NFKC", str(s)).strip().lower()


def vv_dict_list(base_url, timeout=10, full=False):
    """登録済みユーザー辞書を返す。
    full=False: [(uuid, surface, pronunciation, accent_type)]（従来どおり）
    full=True : [{uuid, surface, pronunciation, accent_type, word_type, priority}]
                （品詞と優先度を保ったまま更新するのに使う）"""
    import requests
    r = requests.get(base_url + "/user_dict", timeout=timeout)
    r.raise_for_status()
    out = []
    for word_uuid, w in r.json().items():
        if full:
            out.append({"uuid": word_uuid,
                        "surface": w.get("surface", ""),
                        "pronunciation": w.get("pronunciation", ""),
                        "accent_type": w.get("accent_type", 0),
                        "word_type": word_type_of(w),
                        "priority": w.get("priority"),
                        "raw": w})
        else:
            out.append((word_uuid, w.get("surface", ""),
                        w.get("pronunciation", ""), w.get("accent_type", 0)))
    out.sort(key=lambda x: x["surface"] if full else x[1])
    return out


def _dict_params(surface, pronunciation, accent_type, word_type, priority):
    """POST/PUT に渡すパラメータを組む。word_type / priority は None のとき
    「送らない」＝エンジンの既定に任せる（従来の呼び出しと同じ挙動になる）。"""
    p = {"surface": surface, "pronunciation": pronunciation,
         "accent_type": int(accent_type)}
    if word_type:
        p["word_type"] = str(word_type)
    if priority is not None:
        p["priority"] = int(priority)
    return p


def vv_dict_add(base_url, surface, pronunciation, accent_type=0, timeout=10,
                word_type=None, priority=None):
    """単語を登録し、word_uuid を返す。pronunciation は全角カタカナ。"""
    import requests
    r = requests.post(base_url + "/user_dict_word",
                      params=_dict_params(surface, pronunciation, accent_type,
                                          word_type, priority),
                      timeout=timeout)
    r.raise_for_status()
    return r.json()


def vv_dict_update(base_url, word_uuid, surface, pronunciation,
                   accent_type=0, timeout=10, word_type=None, priority=None):
    """登録済み単語を更新する（読みの修正。削除→再追加が不要になる）。
    word_type / priority を渡さないとエンジンは既定値で作り直すため、
    読みを直しただけのつもりでも品詞や優先度が巻き戻る。呼び出し側は
    vv_dict_list(full=True) で読んだ今の値を渡すこと。"""
    import requests
    r = requests.put(base_url + f"/user_dict_word/{word_uuid}",
                     params=_dict_params(surface, pronunciation, accent_type,
                                         word_type, priority),
                     timeout=timeout)
    r.raise_for_status()


def vv_dict_delete(base_url, word_uuid, timeout=10):
    """登録済み単語を削除する。"""
    import requests
    r = requests.delete(base_url + f"/user_dict_word/{word_uuid}", timeout=timeout)
    r.raise_for_status()


# ============================================================
#  SRT字幕の生成
# ============================================================
# オーディオブックの章見出しとみなす行のパターン。短い行（25字以下・句点なし）だけを
# 対象にし、さらに見出し語の直後が「行末・空白・コロン」であることを要求して、
# 本文の「第一章では〜」「その3人が〜」「終章のない〜」「はじめに言葉ありき」等の
# 文への前方一致を誤検出しない。
_CHAPTER_RE = re.compile(
    r"^(?:第[0-9０-９一二三四五六七八九十百千]+[章話部節巻編]"
    r"|その[0-9０-９一二三四五六七八九十]+"
    r"|プロローグ|エピローグ|序章|終章|間章|幕間"
    r"|まえがき|あとがき|はじめに|おわりに)"
    r"(?=$|[\s　：:])")


def is_chapter_heading(line: str) -> bool:
    """この行が章見出し（第N章・プロローグ等＋短い・句点なし）か。
    detect_chapters のほか、clean_text が見出しの区切り空白を保護する判定にも使う。"""
    s = str(line).strip()
    return bool(s and len(s) <= 25 and "。" not in s and _CHAPTER_RE.match(s))


def detect_chapters(lines: list) -> list:
    """行リストから章見出しを検出し [(章タイトル, 行index), ...] を返す。
    オーディオブック（M4B）のチャプター分割に使う。見出しが無ければ空リスト
    （呼び出し側が「全体で1章」等にフォールバックする）。"""
    return [(str(ln).strip(), i) for i, ln in enumerate(lines)
            if is_chapter_heading(ln)]


def fallback_chapters(starts, lines, interval_sec=600):
    """章見出しが無い本のための自動チャプター（約 interval_sec ごとに行頭で区切る）。
    starts: 各行の開始秒（昇順） / lines: 各行のテキスト。
    戻り値: [(タイトル, 開始秒), ...]。先頭は必ず ("冒頭", 0.0)。
    タイトルは区切り行の本文冒頭12字（Apple Books等の章一覧で中身の見当がつく）。
    interval に届かない短い本は先頭のみ＝実質チャプターなしと同じ。"""
    if not starts:
        return []
    chapters = [("冒頭", 0.0)]
    next_mark = float(interval_sec)
    for t, ln in zip(starts, lines):
        if t >= next_mark:
            s = str(ln).strip()
            chapters.append((s[:12] + ("…" if len(s) > 12 else ""), t))
            next_mark = t + interval_sec
    return chapters


def build_chapters(lines, durations, gap=0.0):
    """M4Bチャプター列を構築して (chapters, kind) を返す（GUI/CLIで共有）。
    kind: "heads"=章見出し検出（1個でも埋め込む・冒頭補完あり） /
          "auto"=約10分ごとの自動チャプター（2個以上のときだけ） / "none"=なし。
    durations: 各行の再生秒。開始時刻は「前の行の長さ＋その行の前の無音」の累積で
    刻む。gap は全部の行で同じ秒数か、行ごとのリスト（line_gaps）。"""
    starts = []
    t = 0.0
    for i, d in enumerate(durations):
        t += _gap_at(gap, i)
        starts.append(t)
        t += d
    heads = detect_chapters(lines)
    if heads:
        chapters = [(title, starts[k]) for title, k in heads]
        if chapters and chapters[0][1] > 0:
            chapters.insert(0, ("冒頭", 0.0))   # 最初の見出しより前の本文ぶん
        return chapters, "heads"
    chapters = fallback_chapters(starts, lines)
    if len(chapters) > 1:
        return chapters, "auto"
    return [], "none"


def group_output_indices(unit, para_ids, nlines=50):
    """まとめ方 unit に応じて行indexをグループ化する（グループ=1出力ファイル）。
    para_ids: 各行の段落番号（unit="para" のときだけ使う）。GUI/CLIで共有し、
    まとめ方を増やすとき片方だけ実装される事故を防ぐ。"""
    n = len(para_ids)
    if unit == "combine":
        return [list(range(n))]
    if unit == "nlines":
        k = max(2, int(nlines))
        return [list(range(i, min(i + k, n))) for i in range(0, n, k)]
    if unit == "para":
        groups = []
        for i, pid in enumerate(para_ids):
            if groups and para_ids[groups[-1][-1]] == pid:
                groups[-1].append(i)
            else:
                groups.append([i])
        return groups
    return [[i] for i in range(n)]   # each


def _srt_ts(sec: float) -> str:
    """秒を SRT のタイムスタンプ形式 HH:MM:SS,mmm にする。"""
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def make_srt(lines, durations, gap_sec=0.0) -> str:
    """行テキストと各行の音声長(秒)から SRT 字幕文字列を作る。
    結合音声と同じ並び・同じ無音間隔(gap_sec)を前提にタイミングを刻む。gap_sec は
    全部の行で同じ秒数か、行ごとのリスト（line_gaps。段落・章の切れ目は長い）。"""
    cues = []
    t = 0.0
    for i, (ln, dur) in enumerate(zip(lines, durations)):
        t += _gap_at(gap_sec, i)
        cues.append(f"{i + 1}\n{_srt_ts(t)} --> {_srt_ts(t + dur)}\n{ln}\n")
        t += dur
    return "\n".join(cues)


# ============================================================
#  VOICEVOX プロジェクトファイル (.vvproj) 出力
# ============================================================
# 公式VOICEVOXエンジンのID。旧形式(0.14)プロジェクトの engineId として使う。
VV_ENGINE_ID = "074fc39e-678b-4c13-8916-ffca8d505d1d"


def vv_engine_id(base_url, timeout=5):
    """接続中エンジンの識別子(UUID)を返す。取れなければ公式エンジンのIDを返す。
    .vvproj の voice.engineId は「どのエンジンで作った音声か」の印で、ここが実物と
    違うとエディタ側で音声を復元できない。公式エンジンなら値は同じなので、
    普段づかいでは何も変わらない（別エンジンを繋いだときだけ効く）。"""
    import requests
    try:
        r = requests.get(base_url + "/engine_manifest", timeout=timeout)
        if r.ok:
            uid = str(r.json().get("uuid", "")).strip()
            # 壊れた値を書き込むほうが危ないので、UUIDの形だけは確かめる
            if len(uid) == 36 and uid.count("-") == 4:
                return uid
    except Exception:
        pass
    return VV_ENGINE_ID


# .vvproj(0.22形式) の query が持つキー。ここに無いキーは書き出さない。
# ・新しいエンジンが増やしたキー → 0.22 として読まれるときに邪魔なので落とす
# ・古いエンジンに無いキー → 既定値で埋めて形をそろえる
_VVPROJ_QUERY_DEFAULTS = {
    "accentPhrases": [],
    "speedScale": 1.0,
    "pitchScale": 0.0,
    "intonationScale": 1.0,
    "volumeScale": 1.0,
    "prePhonemeLength": 0.1,
    "postPhonemeLength": 0.1,
    "pauseLength": None,
    "pauseLengthScale": 1.0,
    "outputSamplingRate": 24000,
    "outputStereo": False,
    "kana": "",
}


def _to_camel(name):
    """accent_phrases → accentPhrases のように、先頭以外の単語を大文字始まりにする。"""
    head, _, rest = str(name).partition("_")
    if not rest:
        return str(name)
    return head + "".join(w[:1].upper() + w[1:] for w in rest.split("_"))


def _camelize(obj):
    """辞書のキーを snake_case から camelCase へ再帰的に変換する。
    VOICEVOXは「エンジンAPIは snake_case・エディタのプロジェクト形式は camelCase」
    という食い違いがあり、そのまま書くとエディタの型チェックに弾かれて
    「プロジェクトを読み込めません」になる（accent_phrases / pause_mora /
    is_interrogative / consonant_length / vowel_length が該当）。"""
    if isinstance(obj, dict):
        return {_to_camel(k): _camelize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_camelize(v) for v in obj]
    return obj


def _drop_nulls(obj):
    """値が None のキーを再帰的に取り除く。
    エンジンは「無い」を null で返すが（子音のないモーラの consonant、間のない句の
    pause_mora など）、エディタ側は「キーごと無い」を期待しており、null のまま渡すと
    型チェックに弾かれて「プロジェクトを読み込めません」になる。"""
    if isinstance(obj, dict):
        return {k: _drop_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_nulls(v) for v in obj]
    return obj


def normalize_vvproj_query(query, speed=1.0, pitch=0.0, intonation=1.0,
                           volume=1.0, pause_scale=1.0):
    """audio_query の生レスポンスを .vvproj に入れられる形へ整えて返す。
    元の dict は変更せず、新しい dict を作る。
    アクセント情報の中身は組み立て直さない：キー名を camelCase へ直すだけで、
    値はエンジンが返したものを丸ごと通す（自前で作ると必ず壊れる）。"""
    src = query if isinstance(query, dict) else {}
    out = {}
    for key, default in _VVPROJ_QUERY_DEFAULTS.items():
        out[key] = src.get(key, default)
    # エンジンは accent_phrases、エディタは accentPhrases。中身のキー
    # （pause_mora 等）も含めて丸ごと camelCase へ直してから載せる
    out["accentPhrases"] = _drop_nulls(_camelize(
        src.get("accent_phrases", src.get("accentPhrases", []))))
    # 調整値を上書き。エディタの検証は型に厳しいので、ここで型を確定させる
    out["speedScale"] = float(speed)
    out["pitchScale"] = float(pitch)
    out["intonationScale"] = float(intonation)
    out["volumeScale"] = float(volume)
    # 句読点の間は、エンジンが対応しているとき（キーを返したとき）だけ触る
    if "pauseLengthScale" in src:
        out["pauseLengthScale"] = float(pause_scale)
    out["prePhonemeLength"] = float(out.get("prePhonemeLength") or 0.0)
    out["postPhonemeLength"] = float(out.get("postPhonemeLength") or 0.0)
    out["outputSamplingRate"] = int(out.get("outputSamplingRate") or 24000)
    out["outputStereo"] = bool(out.get("outputStereo"))
    out["kana"] = str(out.get("kana") or "")
    if out.get("pauseLength") is not None:
        out["pauseLength"] = float(out["pauseLength"])
    out["pauseLengthScale"] = float(out.get("pauseLengthScale") or 1.0)
    return out


def validate_vvproj(text):
    """書き出す直前の自己点検。おかしければ ValueError を投げる。
    エディタに「開けない」と言われても原因が見えないので、こちらで先に落とす。"""
    try:
        proj = json.loads(text)
    except Exception as e:
        raise ValueError("JSONとして壊れています: %s" % e)
    if not isinstance(proj.get("appVersion"), str):
        raise ValueError("appVersion がありません")
    talk = proj.get("talk")
    if not isinstance(talk, dict):
        raise ValueError("talk セクションがありません")
    keys, items = talk.get("audioKeys"), talk.get("audioItems")
    if not isinstance(keys, list) or not isinstance(items, dict):
        raise ValueError("audioKeys / audioItems の形が違います")
    # 片方にしか無いキーがあるとエディタは確実に壊れる
    if set(keys) != set(items.keys()):
        raise ValueError("audioKeys と audioItems の対応が取れていません")
    if len(keys) != len(set(keys)):
        raise ValueError("audioKeys に重複があります")
    allowed = set(_VVPROJ_QUERY_DEFAULTS)
    for k in keys:
        item = items[k]
        if not isinstance(item.get("text"), str):
            raise ValueError("text が文字列ではありません")
        v = item.get("voice")
        if not isinstance(v, dict):
            raise ValueError("voice がありません")
        if not isinstance(v.get("engineId"), str) or not isinstance(
                v.get("speakerId"), str):
            raise ValueError("engineId / speakerId が文字列ではありません")
        if not isinstance(v.get("styleId"), int):
            raise ValueError("styleId が整数ではありません")
        q = item.get("query")
        if q is None:
            continue
        if not isinstance(q, dict):
            raise ValueError("query が辞書ではありません")
        extra = set(q) - allowed
        if extra:
            raise ValueError("query に想定外のキー: %s" % sorted(extra))
        if not isinstance(q.get("accentPhrases"), list):
            raise ValueError("accentPhrases がリストではありません")
        for nk in ("speedScale", "pitchScale", "intonationScale", "volumeScale"):
            if not isinstance(q.get(nk), float):
                raise ValueError("%s が小数ではありません" % nk)
    return proj


def make_vvproj(lines, style_id, speaker_uuid, engine_id=VV_ENGINE_ID):
    """
    行リストから VOICEVOX エディタで開けるプロジェクト(JSON文字列)を作る。
    appVersion 0.22.0 形式（talk/song 構造・query なし）で出力する:
    ・0.22未満と偽ると query 必須のマイグレーションでエディタが落ちる
    ・0.22以降のスキーマ追加（phonemeTimingEditData 等）はエディタ側の
      マイグレーションが自動補完するため、この形式が安全な最小構成
    speaker_uuid: vv_speakers() が返す話者UUID（voice.speakerId に入る）
    lines の要素は次の3つのいずれか:
      ・文字列
      ・(text, style_id, speaker_uuid)            行別話者
      ・(text, style_id, speaker_uuid, query)     調整値つき
    （style_id/speaker_uuid が None なら既定値を使う。query が None なら
      そのブロックだけ query 無しになる＝有り無しが混ざってよい）
    """
    audio_keys = []
    audio_items = {}
    for ln in lines:
        query = None
        if isinstance(ln, (tuple, list)):
            if len(ln) >= 4:
                text, sid, sp_uuid, query = ln[0], ln[1], ln[2], ln[3]
            else:
                text, sid, sp_uuid = ln
            sid = style_id if sid is None else sid
            sp_uuid = speaker_uuid if sp_uuid is None else sp_uuid
        else:
            text, sid, sp_uuid = ln, style_id, speaker_uuid
        text = text.strip()
        if not text:
            continue
        key = str(uuid.uuid4())
        audio_keys.append(key)
        audio_items[key] = {
            "text": text,
            "voice": {
                "engineId": engine_id,
                "speakerId": sp_uuid,
                "styleId": int(sid),
            },
        }
        # query は 0.22 形式でも省略できるフィールド。持たせると話速・音高・
        # 抑揚・音量がエディタ側にそのまま引き継がれる（無ければ既定値で開く）
        if query is not None:
            audio_items[key]["query"] = query
    track_id = str(uuid.uuid4())
    proj = {
        "appVersion": "0.22.0",
        "talk": {
            "audioKeys": audio_keys,
            "audioItems": audio_items,
        },
        "song": {
            "tpqn": 480,
            "tempos": [{"position": 0, "bpm": 120}],
            "timeSignatures": [{"measureNumber": 1, "beats": 4, "beatType": 4}],
            "tracks": {track_id: {
                "name": "トラック1",
                "keyRangeAdjustment": 0,
                "volumeRangeAdjustment": 0,
                "notes": [],
                "pitchEditData": [],
                "solo": False,
                "mute": False,
                "gain": 1,
                "pan": 0,
            }},
            "trackOrder": [track_id],
        },
    }
    return json.dumps(proj, ensure_ascii=False, indent=2)


# ============================================================
#  新しい版のお知らせ（開いたときに1日1回、GitHub の最新リリースの版だけを見る）
# ============================================================
REPO = "rakuyou211-blip/TextToVoicevox"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Windows の1行導入（install.ps1）。もう一度動かすと、設定はそのままで新しい版に入れ替わる
INSTALL_ONE_LINER = f"irm https://raw.githubusercontent.com/{REPO}/main/install.ps1 | iex"
INSTALL_ONE_LINER_MAC = f"curl -fsSL https://raw.githubusercontent.com/{REPO}/main/install.sh | bash"
UPDATE_CHECK_EVERY = 24 * 3600   # 聞くのは1日1回まで（秒）


def parse_version(text):
    """'v1.26.0' / '1.26' → (1, 26, 0)。版の形でなければ None。"""
    m = re.match(r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?", str(text or ""))
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


def is_newer(latest, current=None):
    """latest が今の版（current。既定は APP_VERSION）より新しいか。読めない版は新しくない。"""
    a = parse_version(latest)
    b = parse_version(APP_VERSION if current is None else current)
    return bool(a and b and a > b)


def update_due(last_checked, now=None, every=UPDATE_CHECK_EVERY):
    """前に聞いた時刻（秒）から every 秒たったか。まだ聞いたことが無い・時計が戻っていたら聞く。"""
    now = time.time() if now is None else now
    try:
        last = float(last_checked or 0)
    except (TypeError, ValueError):
        return True
    return last <= 0 or now - last >= every or now < last


def fetch_latest_release(timeout=5.0, urlopen=None):
    """GitHub の最新リリースの (版のタグ, ページの URL)。取れなければ (None, None)。
    送るのはアプリの名前と版（User-Agent）だけ。下書き・お試しの版は知らせない。"""
    import urllib.request
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"TextToVoicevox/{APP_VERSION}"})
    try:
        with (urlopen or urllib.request.urlopen)(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None, None
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None, None
    tag = data.get("tag_name")
    if not parse_version(tag):
        return None, None
    return tag, data.get("html_url") or RELEASES_PAGE


def install_kind(app_dir, is_win=None, local_appdata=None):
    """新しい版への入れ替え方。
    "oneline" … Windows の1行導入で入れた（もう一度動かせば、押すだけで入れ替わる）
    "zip"     … Windows の zip（新しい zip を落として上書きで展開する）
    "mac"     … Mac（ターミナルの1行をもう一度、または新しい zip）
    "dev"     … GitHub から取ってきた中身（git）。お知らせは出さない"""
    if os.path.isdir(os.path.join(app_dir, ".git")):
        return "dev"
    if not (IS_WIN if is_win is None else is_win):
        return "mac"
    if os.path.isdir(os.path.join(app_dir, "runtime")):
        return "oneline"   # Python 入りの版は1行導入でしか入らない
    base = os.environ.get("LOCALAPPDATA", "") if local_appdata is None else local_appdata
    if base and (os.path.normcase(os.path.abspath(app_dir))
                 == os.path.normcase(os.path.abspath(os.path.join(base, "TextToVoicevox")))):
        return "oneline"   # 1行導入の前からの入れ方（venv）
    return "zip"
