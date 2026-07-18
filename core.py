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
import time
import uuid
import wave
import shutil
import zipfile
import tempfile
import posixpath
import subprocess
import unicodedata
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree

# アプリのバージョン（タイトルバー・CLI --version・不具合報告の目印に使う）。
# リリースごとにここだけ更新する。
APP_VERSION = "1.17.0"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_PS1 = os.path.join(APP_DIR, "ocr_win.ps1")

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
            elif buf[-1] in _SENT_ENDERS or len(buf) >= max_len:
                # 文末記号で終わる、または既に長い → 連結せず改行
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


def _conf_pass_choonpu(text: str) -> str:
    """第1パス: 一⇄ー の補正（判定は“この段階のテキスト”の近傍）。
      1. カタカナ直後の「一」で、直後もカタカナ/長音/行末/句読点 → 長音「ー」
         （サ一ビス→サービス。直後がひらがな・漢字なら「メロン一つ」型なので触らない）
      2. 漢字に挟まれた「ー」 → 漢数字「一」（第ー章→第一章。長音は直前カナ以外に現れない）"""
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
    fixed = _conf_pass_kana_kanji(_conf_pass_choonpu(text))

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
    # （消すと detect_chapters が効かず、M4Bのチャプターが典型的な見出しで作れない）
    text = "\n".join(
        ln if is_chapter_heading(ln)
        else remove_cjk_spaces(ln, keep_ascii_spaces=keep_ascii_spaces).strip(" \t　")
        for ln in text.split("\n"))

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

    return text.strip()


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


def run_ocr(image_paths, lang="ja", strip_labels=True, errors=None,
            progress_cb=None, cancel_event=None):
    """
    画像パスのリストをOS標準のオフラインOCRに渡し、{path: text} を返す。
    Windows: Windows.Media.Ocr（PowerShellヘルパー） / macOS: Apple Vision（pyobjc）
    どちらも行の外接矩形を使った折り返し連結(reflow_ocr_lines)を適用し、
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
        return run_windows_ocr(image_paths, lang=lang, strip_labels=strip_labels,
                               errors=errors, progress_cb=progress_cb,
                               cancel_event=cancel_event)
    if IS_MAC:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)  # 他ディレクトリからのimportでも ocr_mac を見つける
        import ocr_mac
        return ocr_mac.recognize_files(image_paths, lang=lang,
                                       strip_labels=strip_labels, errors=errors,
                                       progress_cb=progress_cb,
                                       cancel_event=cancel_event)
    raise RuntimeError("この環境ではオフラインOCRを利用できません（Windows / macOS のみ対応）。")


def _parse_windows_ocr_result(data, strip_labels=True, errors=None):
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
                    if strip_labels:
                        valid = strip_overlay_labels(valid)
                    text = reflow_ocr_lines(valid) if valid else ""
                except Exception:
                    pass  # 座標後処理に失敗しても行テキストで続行（本文は保持される）
        result[path] = text
    return result


def run_windows_ocr(image_paths, lang="ja", strip_labels=True, errors=None,
                    progress_cb=None, cancel_event=None, chunk_size=20):
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
    n = len(image_paths)
    for start in range(0, n, chunk_size):
        if cancel_event is not None and cancel_event.is_set():
            break   # 部分結果を返す
        chunk = image_paths[start:start + chunk_size]
        try:
            result.update(_run_windows_ocr_chunk(chunk, lang=lang,
                                                 strip_labels=strip_labels,
                                                 errors=errors))
        except Exception as e:
            fatal.append(str(e))
        if progress_cb:
            progress_cb(min(start + chunk_size, n), n)
    if fatal and not any(result.values()):
        # 全チャンク失敗＝従来の「全滅時は例外」と同じ扱い
        raise RuntimeError(fatal[0])
    if errors is not None:
        errors.extend(fatal)   # 一部チャンクの失敗は警告として通知
    return result


def _run_windows_ocr_chunk(image_paths, lang="ja", strip_labels=True,
                           errors=None):
    """PowerShellヘルパーを1回起動して image_paths を処理する（チャンク実体）。"""
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
                f"OCRが応答しませんでした（{len(image_paths)}枚待機）。"
                "枚数を減らすか、PCを再起動してお試しください。")
        if not os.path.exists(out_json):
            raise RuntimeError("OCR失敗: " + (proc.stderr or proc.stdout or "出力なし"))

        # utf-8-sig: PS5.1のOut-File -Encoding utf8はBOM付きで書くことがある
        # （fatal経路）。BOMなしも読めるため常にこちらで開く。
        with open(out_json, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict) and "fatal" in data:
            raise RuntimeError(data["fatal"])
        return _parse_windows_ocr_result(data, strip_labels=strip_labels,
                                         errors=errors)
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


def extract_files(paths, pdf_mode="auto", dpi=300, preprocess=True,
                  lang="ja", progress_cb=None, strip_labels=True,
                  fix_confusables=False, report=None, denoise=False,
                  cancel_event=None, pdf_pages=None):
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
                            pil = preprocess_image(pil, enable=preprocess)
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
                    img = preprocess_image(img, enable=preprocess)
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
                progress_cb(total - 1, total, f"OCR実行中... ({len(ocr_jobs)}枚)")
            png_paths = [p for _, p, _o, _lb in ocr_jobs]
            ocr_errors = []
            ocr_failed = False
            # ページ単位の進捗と、OCR実行中のキャンセル即応（従来はOCRバッチ全体が
            # 1呼び出しで、キャンセルを押してもバッチ完了まで効かなかった）
            ocr_progress = None
            if progress_cb:
                def ocr_progress(i, n):
                    progress_cb(total - 1, total, f"OCR実行中... ({i}/{n}枚)")
            try:
                ocr_result = run_ocr(png_paths, lang=lang,
                                     strip_labels=strip_labels, errors=ocr_errors,
                                     progress_cb=ocr_progress,
                                     cancel_event=cancel_event)
            except Exception as e:
                # 全滅時の例外メッセージに理由は集約済み。個別エラーと空ページ警告を
                # 重ねると同じ障害が三重に表示されるため、ここで抑止する
                warnings.append(f"OCRエラー: {e}")
                ocr_result = {}
                ocr_errors = []
                ocr_failed = True
            # 一部ファイルの失敗理由を警告として表示（従来は無言で空になっていた）
            for e in ocr_errors[:5]:
                warnings.append(f"OCR失敗 {e}")
            if len(ocr_errors) > 5:
                warnings.append(f"…ほか{len(ocr_errors) - 5}件のOCR失敗")
            empty = 0
            for key, png, orig, layer_backed in ocr_jobs:
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
                        progress_cb(total - 1, total, f"再OCR中（画像補正）: {name}")
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
def find_voicevox():
    """VOICEVOX本体のインストール先を探して返す。見つからなければ None。"""
    if IS_WIN:
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\VOICEVOX\VOICEVOX.exe"),
            os.path.expandvars(r"%ProgramFiles%\VOICEVOX\VOICEVOX.exe"),
        ]
    elif IS_MAC:
        candidates = [
            "/Applications/VOICEVOX.app",
            os.path.expanduser("~/Applications/VOICEVOX.app"),
        ]
    else:
        return None
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def launch_voicevox():
    """VOICEVOX本体を起動する。見つからなければ FileNotFoundError。"""
    path = find_voicevox()
    if not path:
        hint = (r"%LOCALAPPDATA%\Programs\VOICEVOX\VOICEVOX.exe" if IS_WIN
                else "/Applications/VOICEVOX.app")
        raise FileNotFoundError(f"VOICEVOXが見つかりません。\n想定の場所: {hint}")
    if IS_MAC:
        subprocess.Popen(["/usr/bin/open", path])
    else:
        subprocess.Popen([path])
    return path


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


def play_wav_blocking(wav_bytes, stop_event=None):
    """WAVバイト列を同期再生する（ワーカースレッドから呼ぶ想定）。
    stop_event (threading.Event) がセットされたら途中で再生を打ち切る。"""
    if IS_WIN:
        import winsound
        if stop_event is None:
            # SND_MEMORY 同期再生（従来どおり）
            winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
            return
        # 非同期再生し、停止要求を監視しながら再生時間ぶん待つ
        dur = wav_duration(wav_bytes)
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY | winsound.SND_ASYNC)
        deadline = time.monotonic() + dur
        while time.monotonic() < deadline:
            if stop_event.is_set():
                winsound.PlaySound(None, winsound.SND_PURGE)
                return
            time.sleep(0.05)
        return
    if IS_MAC:
        fd, path = tempfile.mkstemp(prefix="t2v_prev_", suffix=".wav")
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
                        break
                    time.sleep(0.05)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return
    raise RuntimeError("この環境では再生を利用できません。")


# ============================================================
#  VOICEVOX エンジン連携
# ============================================================
def vv_check(base_url, timeout=3):
    """エンジン到達確認。バージョン文字列 or None。"""
    import requests
    try:
        r = requests.get(base_url + "/version", timeout=timeout)
        if r.ok:
            return r.text.strip().strip('"')
    except Exception:
        return None
    return None


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
    raise RuntimeError("この話者のサンプル音声が見つかりませんでした。")


def _format_kana(kana: str) -> str:
    """audio_query の kana（AquesTalk風記法）からアクセント記号・区切りを除いて
    人が読みやすい形にする。"""
    return (kana.replace("'", "").replace("_", "")
                .replace("/", " ").replace("？", "?"))


def vv_reading(base_url, text, speaker_id, timeout=15):
    """VOICEVOXがこのテキストを“どう読むか”（カナ）を返す（誤読チェック用）。
    誤読を見つけたら「読み方辞書...」で直せる。"""
    import requests
    q = requests.post(base_url + "/audio_query",
                      params={"text": text, "speaker": speaker_id},
                      timeout=timeout)
    q.raise_for_status()
    return _format_kana(q.json().get("kana", ""))


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
                            pitch=0.0, intonation=1.0, volume=1.0, timeout=None):
    """1文を合成し (WAVバイト列, 読みカナ) を返す。audio_query は1回だけ実行し、
    その kana を読み確認（vv_reading 相当）に再利用する（試聴の往復削減）。
    timeout 省略時は文長に比例（整形で生まれた数千字の1行が60秒固定で全滅しない保険）。
    speed=話速(0.5〜2) / pitch=音高(-0.15〜0.15) / intonation=抑揚(0〜2) / volume=音量(0〜2)"""
    import requests
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
                      pitch=0.0, intonation=1.0, volume=1.0, timeout=None):
    """1文を合成してWAVバイト列を返す（vv_synthesize_with_kana の音声のみ版）。"""
    return vv_synthesize_with_kana(base_url, text, speaker_id, speed=speed,
                                   pitch=pitch, intonation=intonation,
                                   volume=volume, timeout=timeout)[0]


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
                         engine_ver="", dict_hash=""):
    """vv_synthesize_one のキャッシュ付き版。engine_ver / dict_hash が空のときは
    キーの精度が担保できないためキャッシュせず素通しする。"""
    if not engine_ver or not dict_hash:
        return vv_synthesize_one(base_url, text, speaker_id, speed=speed,
                                 pitch=pitch, intonation=intonation,
                                 volume=volume, timeout=timeout)
    key = synth_cache_key(text, speaker_id, speed, pitch, intonation, volume,
                          engine_ver, dict_hash)
    wav = synth_cache_get(key)
    if wav is not None:
        return wav
    wav = vv_synthesize_one(base_url, text, speaker_id, speed=speed,
                            pitch=pitch, intonation=intonation,
                            volume=volume, timeout=timeout)
    synth_cache_put(key, wav)
    return wav


# RIFF/WAVの32bitサイズ上限。超えるとwaveモジュールのヘッダ確定がstruct.errorになり
# 全行合成後の保存フェーズで全損するため、書き込み前に見積もって明示エラーにする
_WAV_MAX_DATA = 0xFFFFFFFF - 44


def concat_wavs_to_file(sources, out_path, gap_sec=0.4, chunk_frames=1 << 18):
    """WAV（bytes または ファイルパス）の列を無音を挟んで out_path へ逐次連結し、
    各ソースの再生秒のリストを返す（SRT・チャプター計算にそのまま使える）。
    全体をメモリに持たないため、10時間級の本でもメモリは1チャンク分で済む。
    合計が4GB（RIFF上限）を超える場合は書く前に RuntimeError（保存フェーズ全損防止）。"""
    durations = []
    out = wave.open(out_path, "wb")
    params, silence, written = None, b"", 0
    try:
        for i, src in enumerate(sources):
            f = (io.BytesIO(src) if isinstance(src, (bytes, bytearray))
                 else open(src, "rb"))
            with f, wave.open(f, "rb") as w:
                if params is None:
                    params = w.getparams()
                    out.setparams(params)
                    fw = params.sampwidth * params.nchannels
                    silence = b"\x00" * (int(params.framerate * gap_sec) * fw)
                n = w.getnframes()
                durations.append(n / float(params.framerate))
                need = (n * params.sampwidth * params.nchannels
                        + (len(silence) if i else 0))
                if written + need > _WAV_MAX_DATA:
                    raise RuntimeError(
                        "結合WAVが4GB（WAV形式の上限）を超えます。"
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
    """同一フォーマットのWAVバイト列を無音を挟んで連結し、WAVバイト列を返す。"""
    if not wav_bytes_list:
        return b""
    params = None
    frames = []
    for wb in wav_bytes_list:
        with wave.open(io.BytesIO(wb), "rb") as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))
    silence = b"\x00" * int(params.framerate * gap_sec) * params.sampwidth * params.nchannels
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        for i, fr in enumerate(frames):
            w.writeframes(fr)
            if i < len(frames) - 1:
                w.writeframes(silence)
    return out.getvalue()


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
    Mac: afconvert(OS標準)でM4A。ffmpegがあればMP3も。Win/その他: ffmpegがあれば両方。"""
    import shutil
    enc = {}
    if IS_MAC and os.path.exists("/usr/bin/afconvert"):
        enc["m4a"] = "afconvert"
    ff = shutil.which("ffmpeg")
    if ff:
        enc.setdefault("m4a", ff)
        enc["mp3"] = ff
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
def vv_dict_list(base_url, timeout=10):
    """登録済みユーザー辞書を [(uuid, surface, pronunciation, accent_type)] で返す。"""
    import requests
    r = requests.get(base_url + "/user_dict", timeout=timeout)
    r.raise_for_status()
    out = []
    for word_uuid, w in r.json().items():
        out.append((word_uuid, w.get("surface", ""),
                    w.get("pronunciation", ""), w.get("accent_type", 0)))
    out.sort(key=lambda x: x[1])
    return out


def vv_dict_add(base_url, surface, pronunciation, accent_type=0, timeout=10):
    """単語を登録し、word_uuid を返す。pronunciation は全角カタカナ。"""
    import requests
    r = requests.post(base_url + "/user_dict_word",
                      params={"surface": surface,
                              "pronunciation": pronunciation,
                              "accent_type": int(accent_type)},
                      timeout=timeout)
    r.raise_for_status()
    return r.json()


def vv_dict_update(base_url, word_uuid, surface, pronunciation,
                   accent_type=0, timeout=10):
    """登録済み単語を更新する（読みの修正。削除→再追加が不要になる）。"""
    import requests
    r = requests.put(base_url + f"/user_dict_word/{word_uuid}",
                     params={"surface": surface,
                             "pronunciation": pronunciation,
                             "accent_type": int(accent_type)},
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
    durations: 各行の再生秒。開始時刻は durations[i]+gap の累積で刻む。"""
    starts = []
    t = 0.0
    for d in durations:
        starts.append(t)
        t += d + gap
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
    結合音声と同じ並び・同じ無音間隔(gap_sec)を前提にタイミングを刻む。"""
    cues = []
    t = 0.0
    for i, (ln, dur) in enumerate(zip(lines, durations), start=1):
        cues.append(f"{i}\n{_srt_ts(t)} --> {_srt_ts(t + dur)}\n{ln}\n")
        t += dur + gap_sec
    return "\n".join(cues)


# ============================================================
#  VOICEVOX プロジェクトファイル (.vvproj) 出力
# ============================================================
# 公式VOICEVOXエンジンのID。旧形式(0.14)プロジェクトの engineId として使う。
VV_ENGINE_ID = "074fc39e-678b-4c13-8916-ffca8d505d1d"


def make_vvproj(lines, style_id, speaker_uuid, engine_id=VV_ENGINE_ID):
    """
    行リストから VOICEVOX エディタで開けるプロジェクト(JSON文字列)を作る。
    appVersion 0.22.0 形式（talk/song 構造・query なし）で出力する:
    ・0.22未満と偽ると query 必須のマイグレーションでエディタが落ちる
    ・0.22以降のスキーマ追加（phonemeTimingEditData 等）はエディタ側の
      マイグレーションが自動補完するため、この形式が安全な最小構成
    speaker_uuid: vv_speakers() が返す話者UUID（voice.speakerId に入る）
    lines の要素は文字列、または行別話者の (text, style_id, speaker_uuid) タプル
    （タプルの style_id/speaker_uuid が None なら既定値を使う）
    """
    audio_keys = []
    audio_items = {}
    for ln in lines:
        if isinstance(ln, (tuple, list)):
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
