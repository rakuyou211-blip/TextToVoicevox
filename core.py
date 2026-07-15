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
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree

# アプリのバージョン（タイトルバー・CLI --version・不具合報告の目印に使う）。
# リリースごとにここだけ更新する。
APP_VERSION = "1.9.0"

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
    座標が使えるため、テキストだけの smart_join_wrapped と違い誤連結がほぼ起きない。"""
    items = [l for l in lines if str(l.get("text", "")).strip()]
    if not items:
        return ""
    items = sorted(items, key=lambda l: (round(float(l["y0"]), 4), float(l["x0"])))
    h = _ocr_median_height(items)

    # 1) 縦に連続し同じ列の行を「ブロック（段落候補）」にまとめる。
    blocks = _group_ocr_blocks(items, h)

    # 2) 各ブロック内で「右余白いっぱいまで達した行＝折り返し」の連続を連結する。
    out = []
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


def split_sentences(text: str) -> list:
    """文末記号で文を分割し、1文1要素のリストを返す（記号は保持）。"""
    sentences = []
    buf = ""
    for ch in text:
        if ch == "\n":
            if buf.strip():
                sentences.append(buf.strip())
            buf = ""
            continue
        buf += ch
        if ch in _SENT_ENDERS:
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    return [s for s in sentences if s]


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

# 単独の数値・カウント行（例: 1.04 / 1.2万 / 1,234件）。単位の接尾辞まで許容。
_DENOISE_NUM_LINE = re.compile(
    r"^\s*[+\-]?\d+(?:[.,]\d+)*\s*(?:万|億|千|兆|%|％|人|件|回|票|位|K|k|M)?\s*$")


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


def _denoise_has_jp_script(s: str) -> bool:
    """『日本語の中身の文字』＝ひらがな・カタカナ・漢字（半角カナ含む）を含むか。"""
    return any(is_jp_script_char(c) for c in s)


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
        if doc_is_jp and _denoise_jp_ratio(s) < _DENOISE_LINE_JP_MIN:
            continue                             # 3. 日本語がごく僅かな行（英字/外国語ブロック）→ 落とす
        out.append(ln)                           # 4. それ以外 → 残す
    return "\n".join(out)


def clean_text(raw: str, mode: str = "sentence",
               remove_blank: bool = True, keep_ascii_spaces: bool = True,
               join_wrapped: bool = False, smart_join: bool = False,
               paren_ruby: bool = False, normalize: bool = False,
               denoise: bool = False) -> str:
    """
    抽出生テキストをVOICEVOX向けに整形する。
    mode: "sentence" = 文ごとに改行（VOICEVOX推奨）/ "keep" = 元の改行を保持
    join_wrapped: True で改行をまたいだ文を積極的に連結（小説・段落向け）。
    smart_join: True で“折り返しで途切れた文”だけを賢く連結（見出し・リスト・別ブロックは尊重）。
                join_wrapped が True のときはそちら（積極連結）を優先する。
                いずれも既定Falseでは元の改行を文の区切りとして尊重する（後方互換）。
    paren_ruby: True で「漢字(かんじ)」型ルビを除去（Web小説向け）
    normalize: True で全角英数記号を半角に正規化
    denoise: True で画面キャプチャの“映像内オーバーレイ文字”を前処理で除去
             （既定Falseでは従来の出力を1バイトも変えない）
    """
    # 改行コード統一・全角スペース正規化の前処理
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if denoise:
        text = denoise_capture(text)
    if paren_ruby:
        text = strip_paren_ruby(text)
    if normalize:
        text = normalize_ascii(text)
    # 連続する3つ以上の改行は2つに
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    # 文字間スペース除去
    text = remove_cjk_spaces(text, keep_ascii_spaces=keep_ascii_spaces)

    if mode == "sentence":
        if join_wrapped:
            text = join_wrapped_lines(text)          # 小説向け：積極連結
        elif smart_join:
            text = smart_join_wrapped(text)          # 既定推奨：折り返しだけ賢く連結
        # split_sentences は改行も区切りとして扱うため、
        # join_wrapped/smart_join がFalseなら元の改行は保たれる
        sents = split_sentences(text)
        text = "\n".join(sents)
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
_AOZORA_RUBY = re.compile(r"《[^》]*》")          # ルビ本体
_AOZORA_NOTE = re.compile(r"［＃[^］]*］")        # 入力者注（傍点・字下げ指定等）
_AOZORA_BAR = "｜"                                # ルビ範囲の開始記号


def strip_aozora(text: str) -> str:
    """青空文庫形式の注記を除去する（ルビ《…》・｜・［＃…］）。
    通常のテキストにはまず現れない記号のため、常に適用して安全。"""
    text = _AOZORA_RUBY.sub("", text)
    text = _AOZORA_NOTE.sub("", text)
    return text.replace(_AOZORA_BAR, "")


# 「漢字(かんじ)」型ルビ: 漢字の直後の丸括弧内が すべて かな のときだけ除去する
_PAREN_RUBY = re.compile(
    r"([一-鿿㐀-䶿々〆ヶ]+)[（(]([ぁ-ゖァ-ヶーゝゞヽヾ]+)[)）]")


def strip_paren_ruby(text: str) -> str:
    """Web小説等の「漢字(かんじ)」形式ルビを除去する（読みがな部分を捨てる）。
    括弧内にかな以外が混ざる場合（例: 補足(2023年)）は注釈とみなして残す。"""
    return _PAREN_RUBY.sub(r"\1", text)


# 全角英数記号(FF01-FF5E) → 半角(21-7E)。全角スペースは対象外（既存処理が扱う）
_Z2H = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}


def normalize_ascii(text: str) -> str:
    """全角英数・記号を半角に正規化する（例: Ｅｘｃｅｌ２０２３ → Excel2023）。"""
    return text.translate(_Z2H)


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
    """テキストファイルを読む。UTF-8 → CP932(Shift_JIS) → UTF-16 の順で試す。"""
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "cp932", "utf-16"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("utf-8", errors="replace")


_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx(path: str) -> str:
    """Word文書(.docx)から段落テキストを抽出する（追加ライブラリ不要）。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paras = []
    for p in root.iter(_DOCX_NS + "p"):
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
        for it in opf.iter(ns_o + "item"):
            items[it.get("id")] = it.get("href")
        chapters = []
        missing = 0
        for ref in opf.iter(ns_o + "itemref"):
            href = items.get(ref.get("idref"))
            if not href:
                continue
            entry = name_map.get(_epub_norm((opf_dir + "/" + href) if opf_dir else href))
            if entry is None:
                missing += 1  # 見つからない章は数える（無言で捨てない）
                continue
            html = z.read(entry).decode("utf-8", errors="replace")
            p = _HTMLTextExtractor()
            p.feed(html)
            t = p.text()
            if t:
                chapters.append(t)
    if missing and not chapters:
        # 全章取りこぼした＝ほぼ確実に構造の読み違い。呼び出し側が気づけるよう例外に。
        raise RuntimeError(f"EPUBの本文を取り出せませんでした（{missing}章が見つからず）。")
    return "\n\n".join(chapters)


# ============================================================
#  画像前処理 + OCR
# ============================================================
def preprocess_image(img, enable: bool = True, max_side: int = 4000):
    """OCR精度向上のための前処理。enable=Falseでもサイズ上限だけは適用。"""
    from PIL import Image, ImageOps, ImageFilter
    if img.mode != "L":
        img = img.convert("L")
    w, h = img.size
    long_side = max(w, h)
    if enable and long_side < 1600:
        img = img.resize((w * 2, h * 2), Image.LANCZOS)
    long_side = max(img.size)
    if long_side > max_side:
        s = max_side / long_side
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    if enable:
        img = img.filter(ImageFilter.SHARPEN)
        img = ImageOps.autocontrast(img)
    return img


def run_ocr(image_paths, lang="ja", strip_labels=True):
    """
    画像パスのリストをOS標準のオフラインOCRに渡し、{path: text} を返す。
    Windows: Windows.Media.Ocr（PowerShellヘルパー） / macOS: Apple Vision（pyobjc）
    strip_labels=True のとき、macOSでは行座標を使って“映像内オーバーレイ・ラベル行”
    （局ロゴ・番組名・日時・カテゴリ）を除去する。denoise と同じON/OFFで効かせる想定。
    Windowsは座標を持たないためラベル除去は非適用（現状維持）。
    """
    if not image_paths:
        return {}
    if IS_WIN:
        return run_windows_ocr(image_paths, lang=lang)
    if IS_MAC:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)  # 他ディレクトリからのimportでも ocr_mac を見つける
        import ocr_mac
        return ocr_mac.recognize_files(image_paths, lang=lang, strip_labels=strip_labels)
    raise RuntimeError("この環境ではオフラインOCRを利用できません（Windows / macOS のみ対応）。")


def run_windows_ocr(image_paths, lang="ja"):
    """
    画像パスのリストをWindows標準OCRに渡し、{path: text} を返す。
    PowerShellヘルパー(ocr_win.ps1)を1回だけ起動して全件処理する。
    """
    if not image_paths:
        return {}
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
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=CREATE_NO_WINDOW)
        if not os.path.exists(out_json):
            raise RuntimeError("OCR失敗: " + (proc.stderr or proc.stdout or "出力なし"))

        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "fatal" in data:
            raise RuntimeError(data["fatal"])
        if isinstance(data, dict):
            data = [data]
        result = {}
        for item in data:
            result[item.get("path", "")] = item.get("text", "") if item.get("ok") else ""
        return result
    finally:
        # OCRが済めばmanifest.txt/result.json（＝抽出全文）は不要。%TEMP%に残さない。
        shutil.rmtree(tmpdir, ignore_errors=True)


def _find_powershell():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cand = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return cand if os.path.exists(cand) else "powershell"


# ============================================================
#  ファイルからの抽出（progress_cb(done, total, msg) で進捗通知）
# ============================================================
def extract_files(paths, pdf_mode="auto", dpi=300, preprocess=True,
                  lang="ja", progress_cb=None, strip_labels=True):
    """
    複数ファイル（PDF/画像/テキスト/Word/EPUB）からテキストを抽出して結合文字列を返す。
    pdf_mode: "auto"（テキスト層→無ければOCR） / "ocr"（常にOCR）
    strip_labels: True で画像OCR時に“映像内オーバーレイ・ラベル行”を座標で除去（macOSのみ）。
                  denoise と同じ値を渡す想定（--no-denoise / GUIチェックOFFでラベル除去もしない）。
    戻り値: (text, warnings:list)
    """
    from PIL import Image
    import pypdfium2 as pdfium

    warnings = []
    # OCR対象を一旦集める（temp PNG化）→ 最後にまとめて1回OCR
    ocr_jobs = []           # [(key, temp_png_path)]
    text_parts = {}         # key -> text(テキスト層のもの)
    order = []              # 出力順 key
    tmpdir = tempfile.mkdtemp(prefix="t2v_img_")
    try:
        total = len(paths)
        for idx, path in enumerate(paths):
            if progress_cb:
                progress_cb(idx, total, f"解析中: {os.path.basename(path)}")
            ext = os.path.splitext(path)[1].lower()
            try:
                if ext in PDF_EXT:
                    doc = pdfium.PdfDocument(path)
                    npages = len(doc)
                    for pi in range(npages):
                        key = f"{path}#p{pi+1}"
                        order.append(key)
                        page = doc[pi]
                        use_ocr = (pdf_mode == "ocr")
                        if not use_ocr:
                            tp = page.get_textpage()
                            layer = tp.get_text_range()
                            if len(layer.strip()) >= 1:
                                text_parts[key] = layer
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
                            ocr_jobs.append((key, png))
                        if progress_cb:
                            progress_cb(idx, total,
                                        f"解析中: {os.path.basename(path)} ({pi+1}/{npages}p)")
                    doc.close()
                elif ext in IMG_EXT:
                    key = path
                    order.append(key)
                    img = Image.open(path)
                    img = preprocess_image(img, enable=preprocess)
                    png = os.path.join(tmpdir, f"img_{idx}.png")
                    img.save(png)
                    ocr_jobs.append((key, png))
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

        # まとめてOCR
        if ocr_jobs:
            if progress_cb:
                progress_cb(total - 1, total, f"OCR実行中... ({len(ocr_jobs)}枚)")
            png_paths = [p for _, p in ocr_jobs]
            try:
                ocr_result = run_ocr(png_paths, lang=lang, strip_labels=strip_labels)
            except Exception as e:
                warnings.append(f"OCRエラー: {e}")
                ocr_result = {}
            for key, png in ocr_jobs:
                text_parts[key] = ocr_result.get(png, "")

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


def vv_synthesize_one(base_url, text, speaker_id, speed=1.0,
                      pitch=0.0, intonation=1.0, volume=1.0, timeout=60):
    """1文を合成してWAVバイト列を返す。
    speed=話速(0.5〜2) / pitch=音高(-0.15〜0.15) / intonation=抑揚(0〜2) / volume=音量(0〜2)"""
    import requests
    q = requests.post(base_url + "/audio_query",
                      params={"text": text, "speaker": speaker_id}, timeout=timeout)
    q.raise_for_status()
    query = q.json()
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
    return s.content


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


def encode_audio(wav_bytes, out_path, fmt, encoders=None):
    """WAVバイト列を M4A/MP3 に変換して out_path に保存する。fmt="wav"はそのまま保存。"""
    if fmt == "wav":
        with open(out_path, "wb") as f:
            f.write(wav_bytes)
        return
    if encoders is None:
        encoders = audio_encoders()
    cmd_or_path = encoders.get(fmt)
    if not cmd_or_path:
        raise RuntimeError(f"{fmt.upper()}への変換ツールが見つかりません。")
    fd, tmp = tempfile.mkstemp(prefix="t2v_enc_", suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        if cmd_or_path == "afconvert":
            cmd = ["/usr/bin/afconvert", tmp, "-f", "m4af", "-d", "aac", out_path]
        elif fmt == "mp3":
            cmd = [cmd_or_path, "-y", "-loglevel", "error", "-i", tmp,
                   "-codec:a", "libmp3lame", "-q:a", "2", out_path]
        else:  # ffmpegでm4a
            cmd = [cmd_or_path, "-y", "-loglevel", "error", "-i", tmp,
                   "-codec:a", "aac", out_path]
        # encoding/errors 指定なしだと、失敗時に ffmpeg が日本語パスをUTF-8で
        # 吐き返したとき cp932 厳格デコードが例外を投げ、下の RuntimeError に届かない。
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=CREATE_NO_WINDOW)
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(f"変換失敗: {proc.stderr or proc.stdout or 'unknown'}")
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


def vv_dict_delete(base_url, word_uuid, timeout=10):
    """登録済み単語を削除する。"""
    import requests
    r = requests.delete(base_url + f"/user_dict_word/{word_uuid}", timeout=timeout)
    r.raise_for_status()


# ============================================================
#  SRT字幕の生成
# ============================================================
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
