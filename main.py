# -*- coding: utf-8 -*-
"""
TextToVoicevox — PDF・画像の文字を取り出して、VOICEVOX で読み上げるツール（文字も画像も外へ送らない）
GUI本体。テキスト抽出(core)とVOICEVOXエンジン連携を tkinter で操作する。
"""
import os
import re
import sys
import json
import math
import time
import queue
import random
import shutil
import threading
import traceback
import tempfile
import unicodedata



def _fix_tcl_library(environ=None):
    """venv から、自分用 Python（python-build-standalone。install.sh が入れる）を使うと、
    Tcl/Tk が自分の部品（init.tcl・tk.tcl）を venv の中に探しに行って見つけられず、
    窓を1枚も開けずに落ちる。元の Python の lib/tcl8.x・lib/tk8.x に本物があれば、
    そこを教える。すでに指定がある・見つからないときは何もしない（ほかの Python は元から動く）。"""
    if environ is None:
        environ = os.environ
    if sys.platform != "darwin" or sys.prefix == sys.base_prefix:
        return
    import glob
    lib = os.path.join(sys.base_prefix, "lib")
    for var, pattern, marker in (("TCL_LIBRARY", "tcl8.*", "init.tcl"),
                                 ("TK_LIBRARY", "tk8.*", "tk.tcl")):
        if environ.get(var):
            continue
        for d in sorted(glob.glob(os.path.join(lib, pattern)), reverse=True):
            if os.path.isfile(os.path.join(d, marker)):
                environ[var] = d
                break


_fix_tcl_library()

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

import core

FEEDBACK_URL = ("https://github.com/rakuyou211-blip/TextToVoicevox/issues/new"
                "?template=feedback.yml")
# 「感想を送る」の行き先。アカウント無しで送れる Googleフォーム（ログイン不要の
# 設定）。GitHub の投稿フォームは GitHub のアカウントが無いと送れず、このアプリを
# 使ってほしい人の多くがそこで止まっていた。空のあいだは GitHub を開く。
# FORM_URL は「…/viewform」まで。ENV_ENTRY は「アプリの版・OS」欄の entry.番号
# （フォームの「事前入力した URL を取得」で分かる）で、版とOSを入れた状態で開く。
# 送る人は中身を見てから送るので、黙って集めることにはならない
FEEDBACK_FORM_URL = ("https://docs.google.com/forms/d/e/"
                     "1FAIpQLSfHMtYozt4m_yHkdi569i-FALb_4JuwhMPS6qH4I8zU1nqy2A/viewform")
FEEDBACK_FORM_ENV_ENTRY = "entry.1044076331"   # 「Mac版/Windows版」の欄

try:
    import mp4chapters   # M4Bオーディオブックのチャプター埋め込み（純Python・同梱）
except Exception:
    mp4chapters = None   # 無くてもM4B自体は書き出せる（章なしになるだけ）


class _EitherEvent:
    """複数の threading.Event のどれかがセットされていれば is_set() が真になるアダプタ。
    play_wav_blocking の stop_event に「停止」と「一時停止」の両方を効かせるために使う
    （呼び出し側は is_set() しか見ないのでダックタイピングで足りる）。"""
    def __init__(self, *events):
        self._events = events

    def is_set(self):
        return any(e.is_set() for e in self._events)


# 開いたときに見えるいちばん上にある物（winlaunch.TOP_FILES とショートカット）。起動.pyw は
# 1回開いたあと app の中にしまうので、ほかの物でも見分ける
_TOP_MARKERS = ("起動.pyw", "TextToVoicevox.lnk", "はじめにお読みください.txt", "説明書.html",
                "Manual (English).html", "How to use (日本語).html", "How to use (English).html")


def _top_dir():
    """使い方の案内とアプリの絵のショートカットがある所。Windows の zip では、プログラムや設定は
    app フォルダに入っていて、その1つ上（開いたときに見える物を減らすため）。GitHub の中身と
    1行導入では同じ所。"""
    parent = os.path.dirname(APP_DIR)
    if os.path.basename(APP_DIR).lower() == "app" and any(
            os.path.exists(os.path.join(parent, m)) for m in _TOP_MARKERS):
        return parent
    return APP_DIR


def _in_app_folder(name):
    """アプリのフォルダの中の物を、見つけられる言い方で（zip の並びでは app フォルダの中）。"""
    if _top_dir() != APP_DIR:
        return "アプリのフォルダの「app」の中の「%s」" % name
    return "アプリのフォルダの「%s」" % name


class _LineSynthError(Exception):
    """音声生成で、ある1行だけがエンジンに断られ続けた（リトライしても失敗した）。
    何番目のジョブかを UI まで運ぶための入れ物で、元の例外は __cause__ に入る。
    以前は生の例外がそのまま出て、長い本だとどの行を直せばいいか分からなかった。"""
    def __init__(self, index):
        super().__init__(index)
        self.index = index

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
TEXT_CACHE_PATH = os.path.join(APP_DIR, "last_text.txt")
# キャラ立ち絵とアプリアイコン（任意・ローカル資産）。無くてもアプリは動く。
PORTRAIT_DIR = os.path.join(APP_DIR, "assets", "立ち絵")
APP_ICON_PATH = os.path.join(APP_DIR, "assets", "app-icon.png")

# 立ち絵ファイルのフレーム差分サフィックス（口閉じ/口開き/まばたき）
_PORTRAIT_FRAMES = (("closed", "_closed"), ("open", "_open"), ("blink", "_blink"))
# 旧バージョンのローマ字ファイル名 → 実キャラ名（後方互換。既存ユーザーの
# zundamon.png / metan.png をそのまま活かす）
_PORTRAIT_ALIASES = {"zundamon": "ずんだもん", "metan": "四国めたん"}
# 照合キーから除くファイル名禁止文字（小夜/SAYO の「/」等）＋空白
_PORTRAIT_BAD = set('<>:"/\\|?*')


def _portrait_key(name):
    """キャラ名/ファイル名を照合用キーに正規化する（ファイル名に使えない文字・空白を
    除く）。話者名「小夜/SAYO」やファイル名の表記ゆれを吸収して同じキャラに寄せる。
    Unicodeは NFC に正規化する: macOSの os.listdir は濁点を分解した NFD 形の
    ファイル名を返すことがあり、VOICEVOX API のキャラ名（NFC）と見た目は同じでも
    文字列比較で不一致になる（ずんだもん・春日部つむぎ等の立ち絵が出ない罠）。"""
    s = unicodedata.normalize("NFC", str(name))
    s = _PORTRAIT_ALIASES.get(s, s)
    return "".join(c for c in s if c not in _PORTRAIT_BAD and not c.isspace())

# ドラッグ＆ドロップ対応（tkinterdnd2 が無くてもアプリは動く）
# ネイティブ部品(tkdnd)は、窓が出たあとで読み込む（_register_drop_tree_later）。
# 以前は窓を作るとき（TkinterDnD.Tk）に読み込んでいて、その中の platform.system()
# が Windows では WMI に問い合わせるため、起動が 40ms ほど遅れていた。読み込めない
# 環境（ARM64版Windows・壊れたwheel等）では D&D だけ無しで動く（以前は、D&D 無しで
# 起動し直していた）。TTV_NO_DND=1 のときは最初から読み込まない。
try:
    if os.environ.get("TTV_NO_DND"):
        raise ImportError("D&D disabled by TTV_NO_DND")
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except Exception:
    _HAS_DND = False
    DND_FILES = None

def _write_atomic(path, data):
    """一時ファイルに書いて os.replace で置き換える。書き込み途中のクラッシュ・
    電源断・引数評価中の例外でも既存ファイルが壊れない（従来の open("w") は
    先に0バイトへ切り詰めるため、settings.json 全設定消失の原因だった）。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=".t2v_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# 窓の名前は、アイコン・見出しと同じ「TextToVoicevox」にそろえる。以前は
# 「テキスト抽出 → VOICEVOX vX.Y.Z（オフライン）」で、接続OKのときも「オフライン」と出て、
# つながっていないように読めた（ネットに送らないことは README と説明に書いてある）
APP_TITLE = f"TextToVoicevox v{core.APP_VERSION}"
VOICEVOX_DEFAULT = "http://127.0.0.1:50021"
VOICEVOX_SITE = "https://voicevox.hiroshiba.jp/"   # VOICEVOX の入手先（公式サイト）
ALL_EXT = sorted(core.IMG_EXT | core.PDF_EXT | core.DOC_EXT)

# OS別のUIフォント（Noneなら環境の既定フォントを使う）
if core.IS_WIN:
    UI_FONT = "{Yu Gothic UI} 10"
    TEXT_FONT = "{Yu Gothic UI} 11"
elif core.IS_MAC:
    UI_FONT = None                     # macOSは既定のシステムフォントが日本語も綺麗
    TEXT_FONT = ("Hiragino Sans", 14)
else:
    UI_FONT = None
    TEXT_FONT = None

# 高DPI対応（Windowsのみ）。宣言しないとWindowsが窓全体をビットマップ拡大する
# ため、150%や250%表示の画面では文字がボケる（Tk自身は宣言してくれない）。
# ここで「システムDPI対応」を宣言し、文字はTkが点(pt)指定から正しい解像度で
# 描き直す。pxで書いた余白は小さくなってしまうので、倍率 UI_SCALE で補正する。
UI_SCALE = 1.0
if core.IS_WIN:
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        try:
            UI_SCALE = ctypes.windll.user32.GetDpiForSystem() / 96.0
        except Exception:
            dc = ctypes.windll.user32.GetDC(0)
            UI_SCALE = ctypes.windll.gdi32.GetDeviceCaps(dc, 88) / 96.0  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(0, dc)
    except Exception:
        UI_SCALE = 1.0


def _px(n):
    """レイアウト用のピクセル値をDPI倍率で補正する（Mac・100%表示では素通し）。"""
    return int(round(n * UI_SCALE))


def _mark_coverage(shape, size):
    """チェック（"check"）とラジオ（"radio"）の印の形を、ピクセルごとの濃さ
    （外形, 内側, 印）にして返す。色を混ぜる元なので、形ごとに1回だけ計算する。"""
    def clamp(v):
        return max(0.0, min(1.0, v))

    def to_segment(px, py, a, b):
        ax, ay, bx, by = a[0], a[1], b[0], b[1]
        dx, dy = bx - ax, by - ay
        t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy))
        return math.hypot(px - ax - t * dx, py - ay - t * dy)

    mid = size / 2
    half = mid - 0.5
    corner = size * 0.18
    border = max(1.0, size / 14)
    # ✓ の折れ線（左下へ降りて、右上へ上がる）
    p1, p2, p3 = ((size * 0.24, size * 0.52), (size * 0.43, size * 0.71),
                  (size * 0.77, size * 0.31))
    stroke = max(1.5, size * 0.14)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            if shape == "radio":
                d = math.hypot(px - mid, py - mid) - half
                mark = clamp(0.5 - (math.hypot(px - mid, py - mid) - size * 0.24))
            else:
                qx, qy = abs(px - mid) - (half - corner), abs(py - mid) - (half - corner)
                d = (math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0)
                     - corner)
                mark = clamp(0.5 + stroke / 2 - min(to_segment(px, py, p1, p2),
                                                    to_segment(px, py, p2, p3)))
            row.append((clamp(0.5 - d), clamp(0.5 - (d + border)), mark))
        rows.append(row)
    return rows


def _mark_pixels(coverage, around, fill, edge, mark):
    """濃さ（_mark_coverage）に色を混ぜて、PhotoImage.put に渡す文字列にする。"""
    lines = []
    for row in coverage:
        cells = []
        for outer, inner, m in row:
            c = []
            for i in range(3):
                v = around[i] + (edge[i] - around[i]) * outer
                v += (fill[i] - v) * inner
                if mark is not None:
                    v += (mark[i] - v) * m
                c.append(int(v + 0.5))
            cells.append("#%02x%02x%02x" % tuple(c))
        lines.append("{" + " ".join(cells) + "}")
    return " ".join(lines)


def _swap_element(layout, old, new):
    """ttk のレイアウトの中の部品 old を new に置き換える（並びと余白はテーマのまま）。"""
    out = []
    for name, opts in layout:
        opts = dict(opts)
        if "children" in opts:
            opts["children"] = _swap_element(opts["children"], old, new)
        out.append((new if name == old else name, opts))
    return out


def _packed_width(frame, skip=()):
    """横に pack した部品がすべて入る幅（部品の要る幅と左右の余白の合計）。
    枠そのものの要る幅（winfo_reqwidth）は、中の文字や余白が変わってから並べ直しが
    済むまで古いままのことがある（ふりがなの「（VOICEVOX待ち）」が付いたのに、
    1行に入ると見て行数の表示が切れていた）。部品の要る幅はすぐ変わるので、
    そこから足し上げる。"""
    return sum(w.winfo_reqwidth() + _pack_padx(w)
               for w in frame.pack_slaves() if w not in skip)


def _pack_padx(w):
    """pack で置いた部品の左右の余白の合計（置いていなければ 0）。"""
    try:
        info = w.tk.splitlist(w.tk.call("pack", "info", w))
    except tk.TclError:
        return 0
    pad = dict(zip(map(str, info[::2]), info[1::2])).get("-padx", 0)
    if not isinstance(pad, (tuple, list)):   # 「4」も「4 0」も来る
        pad = w.tk.splitlist(str(pad))
    pad = [w.winfo_pixels(str(p)) for p in pad] or [0]
    return sum(pad) if len(pad) > 1 else 2 * pad[0]


def _gear_xbm(size):
    """歯車の絵（XBM・1色）。設定のボタンの印に使う。字体の記号（⚙）は Windows で
    描くのが遅い（字体を探しに行く）ので、自分で描く。色は BitmapImage の foreground。"""
    import math
    c = (size - 1) / 2
    r_out, r_base, r_hole = size * 0.5, size * 0.35, size * 0.16
    rows = []
    for y in range(size):
        bits = 0
        for x in range(size):
            r = math.hypot(x - c, y - c)
            tooth = math.cos(math.atan2(y - c, x - c) * 8) > 0.15   # 歯は8つ
            if r_hole < r <= (r_out if tooth else r_base):
                bits |= 1 << x
        rows.append(bits)
    return _xbm("gear", size, rows)


def _camera_xbm(size):
    """カメラの絵（XBM・1色）。「スクリーンショット」のボタンの印に使う。歯車と同じく、
    字体の絵文字（📷）は Windows で描くのが遅いので、自分で描く。角を丸めた横長の
    本体に小さな出っぱり、真ん中にレンズ（輪の部分を抜いて、ボタンの面の色を見せる）。"""
    import math
    cx, cy = size / 2, size * 0.62
    r_out, r_in = size * 0.25, size * 0.17
    top, bot, left, right = size * 0.30, size * 0.92, size * 0.04, size * 0.96
    corner = size * 0.10
    rows = []
    for y in range(size):
        bits = 0
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            on = False
            if top <= py <= bot and left <= px <= right:   # 本体（角は丸める）
                dx = max(left + corner - px, 0, px - (right - corner))
                dy = max(top + corner - py, 0, py - (bot - corner))
                on = dx * dx + dy * dy <= corner * corner
            if size * 0.30 <= px <= size * 0.62 and size * 0.14 <= py <= top:
                on = True                                  # 上の出っぱり
            d = math.hypot(px - cx, py - cy)
            if r_in < d <= r_out:
                on = False                                 # レンズの輪
            elif d <= r_in * 0.62:
                on = True                                  # レンズの芯
            if on:
                bits |= 1 << x
        rows.append(bits)
    return _xbm("camera", size, rows)


def _folder_xbm(size):
    """フォルダの絵（XBM・1色）。「フォルダ追加」のボタンの印に使う（カメラと同じ作り）。
    左上につまみ、本体は角を少し丸めた横長の四角。本体の上の方に細い抜き線を入れて、
    手前のふた（前の面）に見せる。"""
    tab_l, tab_r, tab_t = size * 0.06, size * 0.44, size * 0.18
    top, bot, left, right = size * 0.30, size * 0.88, size * 0.06, size * 0.94
    flap, corner = size * 0.46, size * 0.08
    rows = []
    for y in range(size):
        bits = 0
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            on = tab_t <= py <= top and tab_l <= px <= tab_r      # つまみ
            if top <= py <= bot and left <= px <= right:          # 本体（角は丸める）
                dx = max(left + corner - px, 0, px - (right - corner))
                dy = max(top + corner - py, 0, py - (bot - corner))
                on = dx * dx + dy * dy <= corner * corner
            if abs(py - flap) < 0.5 and left + 1 < px < right - 1:
                on = False                                         # ふたの線
            if on:
                bits |= 1 << x
        rows.append(bits)
    return _xbm("folder", size, rows)


def _file_xbm(size):
    """書類の絵（XBM・1色）。「ファイル追加」のボタンの印に使う。右上の角を折った紙に、
    文字の行を3本（抜き線）。"""
    left, right, top, bot = size * 0.20, size * 0.80, size * 0.06, size * 0.94
    ear = size * 0.28
    rows = []
    for y in range(size):
        bits = 0
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            on = left <= px <= right and top <= py <= bot
            if px > right - ear and py < top + ear and px - (right - ear) > py - top:
                on = False                                         # 折った角
            for line in (0.46, 0.62, 0.78):                        # 文字の行
                if abs(py - size * line) < 0.5 and left + size * 0.12 < px < right - size * 0.12:
                    on = False
            if on:
                bits |= 1 << x
        rows.append(bits)
    return _xbm("file", size, rows)


def _picture_xbm(size):
    """写真の絵（XBM・1色）。「コピーした画像を読む」のボタンの印に使う。細い枠の中に、
    山が2つと太陽。"""
    import math
    left, right, top, bot = size * 0.06, size * 0.94, size * 0.16, size * 0.86
    t = max(1, round(size * 0.07))   # 枠の太さ
    rows = []
    for y in range(size):
        bits = 0
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            inner = left + t < px < right - t and top + t < py < bot - t
            on = left <= px <= right and top <= py <= bot and not inner   # 枠
            if inner:
                peak = max(size * 0.40 - abs(px - size * 0.36) * 1.15,   # 左の大きい山
                           size * 0.24 - abs(px - size * 0.68) * 1.15)   # 右の小さい山
                on = (py >= bot - t - peak
                      or math.hypot(px - size * 0.70, py - size * 0.40) <= size * 0.09)
            if on:
                bits |= 1 << x
        rows.append(bits)
    return _xbm("picture", size, rows)


def _person_xbm(size):
    """人の形（頭と肩）の絵（XBM・1色）。② の顔の場所に、VOICEVOX につながる前や
    顔を取ってくる前だけ、薄い色で出しておく（つながっても並びが動かないように）。"""
    import math
    cx = size / 2
    rows = []
    for y in range(size):
        bits = 0
        for x in range(size):
            px, py = x + 0.5, y + 0.5
            head = math.hypot(px - cx, py - size * 0.36) <= size * 0.19
            body = (py >= size * 0.60 and ((px - cx) / (size * 0.38)) ** 2
                    + ((py - size) / (size * 0.38)) ** 2 <= 1)
            if head or body:
                bits |= 1 << x
        rows.append(bits)
    return _xbm("person", size, rows)


def _face_png(png, size):
    """VOICEVOX の顔アイコン（PNG）を size×size に縮めて、Tk の PhotoImage にそのまま
    渡せる base64 の PNG にする。縮められなければ None（顔は出さず、人の形のまま）。"""
    import base64
    import io
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGBA")
        im = im.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _xbm(name, size, rows):
    """行ごとのビット列（下位ビットが左）から、BitmapImage に渡す XBM の文字列を作る。"""
    nbytes = (size + 7) // 8
    data = ", ".join(f"0x{(bits >> (8 * i)) & 0xFF:02x}"
                     for bits in rows for i in range(nbytes))
    return (f"#define {name}_width {size}\n#define {name}_height {size}\n"
            f"static unsigned char {name}_bits[] = {{ {data} }};")


# テーマの決めたタイトルバーの色 (地, 文字)。None なら OS の色（apply_theme が決める）
_TITLE_COLORS = None


def _colorref(color):
    """"#rrggbb" を Windows の色の値（0x00bbggrr）にする。"""
    return int(color[5:7] + color[3:5] + color[1:3], 16)


def _dark_title_bar(win, dark, tries=10):
    """Windows のタイトルバーを暗く/明るくする（Windows 10 20H1 以降。ほかでは何もしない）。
    Tk は自分ではタイトルバーの色を変えないので、ダークテーマでもそこだけ白かった。
    テーマに色があれば、タイトルバーもその色にする（Windows 11。10 では何も起きない）。"""
    if not core.IS_WIN:
        return False
    try:
        import ctypes
        win._dark_title = dark   # 外枠を待つあいだにテーマが変わったら、後の方の色で塗る
        hwnd = int(win.wm_frame(), 16)
        if hwnd == win.winfo_id():
            # 作った直後の窓には、タイトルバーの付いた外枠の窓がまだない（Tk が次の
            # 手すきに、隠したままでも作る）。中の窓に設定しても効かないので、外枠が
            # できてから塗る。after_idle だと Tk が外枠を作るより先に呼ばれてしまう。
            # 小窓は出す（50ms 後）より前に間に合うので、白く光らない
            if tries > 0:
                win.after(20, lambda: win.winfo_exists()
                          and _dark_title_bar(win, win._dark_title, tries - 1))
            return False
        value = ctypes.c_int(1 if dark else 0)
        ok = False
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE（古い Windows 10 は 19）
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                ok = True
                break
        back, text = _TITLE_COLORS or (None, None)
        # DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR（0xFFFFFFFF は OS の色に戻す）
        for attr, color in ((35, back), (36, text)):
            ref = ctypes.c_uint(0xFFFFFFFF if color is None else _colorref(color))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
        # 枠を描き直させる（そのままだと、次に窓が動くまで色が変わらないことがある）
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                          0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
        return ok
    except Exception:
        return False


def _win_monitor_pairs():
    """Windows: 画面ごとの [(アプリから見た範囲, 本当の画素での範囲)]（どちらも 左, 上, 右, 下）。
    このアプリは「システムDPI対応」なので、メインとちがう倍率の画面の座標は、Windows が
    倍率の比で直して見せる（250% がメインなら、125% の画面は位置も大きさも2倍に見える）。
    画面を撮った画像は本当の画素なので、この対応を知らないと切り抜く場所がずれる。
    取れないとき（古い Windows など）は None。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def rects():
        out = {}

        def cb(mon, _dc, rc, _lp):
            r = rc.contents
            out[mon] = (r.left, r.top, r.right, r.bottom)
            return 1
        keep = proc_t(cb)   # 呼んでいるあいだ、手放さない
        return out if user32.EnumDisplayMonitors(None, None, keep, 0) else None

    seen = rects()
    try:
        set_ctx = user32.SetThreadDpiAwarenessContext   # Windows 10 (1607) から
    except AttributeError:
        return None
    set_ctx.restype = ctypes.c_void_p
    set_ctx.argtypes = [ctypes.c_void_p]
    # このスレッドだけ、少しのあいだ「画面ごとのDPI対応」（V2。だめなら V1）にして聞き直す
    old = set_ctx(ctypes.c_void_p(-4)) or set_ctx(ctypes.c_void_p(-3))
    if not old:
        return None
    try:
        real = rects()
    finally:
        set_ctx(ctypes.c_void_p(old))
    if not seen or not real or set(seen) != set(real):
        return None
    return [(seen[m], real[m]) for m in seen]


def _screen_layout_from(region, pairs, image_size):
    """撮った画像と、アプリから見た画面の対応（core.screen_selection_box_multi の layout）。
    画面が1台のとき・どの画面も座標と画素が同じとき・対応が取れないときは、全体を1枚として
    扱う（これまでと同じ）。倍率のちがう画面が混ざるときだけ、画面ごとに分ける。"""
    rx, ry, rw, rh = region
    iw, ih = image_size
    whole = [(rx, ry, rw, rh, 0, 0, iw, ih)]
    if not pairs or all(v == r for v, r in pairs):
        return whole
    left = min(r[0] for _v, r in pairs)
    top = min(r[1] for _v, r in pairs)
    if (max(r[2] for _v, r in pairs) - left, max(r[3] for _v, r in pairs) - top) != (iw, ih):
        return whole   # 画像の大きさが、画面の画素の合計と合わない（想定していない撮り方）
    # メイン画面（座標 0,0 のある画面）を先頭に（選ぶ窓の1枚目になり、キー入力を受ける）
    pairs = sorted(pairs, key=lambda p: not (p[0][0] <= 0 < p[0][2] and p[0][1] <= 0 < p[0][3]))
    return [(v[0], v[1], v[2] - v[0], v[3] - v[1],
             r[0] - left, r[1] - top, r[2] - r[0], r[3] - r[1]) for v, r in pairs]


# レイアウトの余白定数（全セクションで統一し、バラバラなpadx/padyを解消する）
PAD_X = _px(8)   # セクション外周の水平余白
PAD_Y = _px(6)   # セクション外周の垂直余白
GAPX = _px(6)    # 同一行のラベルと入力欄・小ブロック間の間隔
GAPY = _px(4)    # grid の行間


def _fit_rect_in_work_area(w, h, x, y, work, frame_x, frame_y, caption):
    """Tk の geometry（中身の幅・高さ w, h と、外枠の左上 x, y）を、画面の作業領域
    work=(左, 上, 右, 下)（タスクバーを除いた所）に収める。はみ出すときはまず
    動かし、それでも入らないときだけ縮める。
    外枠は 左右と下に frame_x / frame_y、上にタイトルバー caption ぶん中身より大きい。
    Windows 10 以降はこの左右と下の枠が見えないリサイズ用の縁なので、そこは
    作業領域の外に出てもよい（画面端にスナップした窓もそうなっている）。"""
    wl, wt, wr, wb = work
    w = max(1, min(w, wr - wl))
    h = max(1, min(h, wb - wt - caption - frame_y))
    x = max(wl - frame_x, min(x, wr - w - frame_x))
    y = max(wt, min(y, wb - caption - h - frame_y))
    return w, h, x, y


class _Tooltip:
    """軽量ツールチップ（ホバーで説明を表示）。既存のバインドを壊さないよう add='+'。
    text には文字列のほか「呼び出しの度に評価される関数」も渡せる（無効ボタンに
    “なぜ押せないか”を状態に応じて出すため）。関数が空文字を返せば表示しない。"""
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        try:
            self._id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            self._id = None

    def _cancel(self):
        if self._id:
            try:
                self.widget.after_cancel(self._id)
            except tk.TclError:
                pass
            self._id = None

    def _show(self):
        text = self.text() if callable(self.text) else self.text
        if self._tip or not text:
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
            self._tip = tw = tk.Toplevel(self.widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, justify="left", background="#ffffe0",
                     foreground="#000000", relief="solid", borderwidth=1,
                     padx=6, pady=3).pack()
        except tk.TclError:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


class App(tk.Tk):
    # 出力単位: 内部キー → 表示ラベル（コンボボックスの並び順と一致させる）
    # 最初は「全部を1つに」（本を聴くならこれ。前は「1行=1ファイル」が最初で、本1冊を
    # 保存すると何も聞かれずに約2万個のファイルができていた）
    _UNITS = {"combine": "全部を1つに", "each": "1行=1ファイル",
              "nlines": "行数ごと", "para": "段落ごと"}
    # 1回の保存でこれより多くファイルができるときは、作る前に聞く（_ask_many_files）
    _MANY_FILES = 300
    # 音声形式: 内部キー → 表示（M4B は何か分かるように「本」を添える）
    _FMT_LABELS = {"wav": "WAV", "m4a": "M4A", "mp3": "MP3", "m4b": "M4B(本)"}
    # 接続の見張りの間隔（ミリ秒）。ループバックの /version は1ミリ秒未満なので
    # 15秒おきでも負荷は実質ゼロ。接続中かつ待機中だけ動く
    _HEALTH_INTERVAL_MS = 15000
    # 接続できていないときの表示。状態ごとに理由が伝わる言葉にする
    _CONN_TEXT = {
        "waiting": "VOICEVOX: 起動を待っています…（自動でつなぎ直します）",
        "searching": "VOICEVOX: 探しています…",
        "refused": "VOICEVOX: つながっていません（VOICEVOX を起動してください）",
        "lost": "⚠ 接続が切れました（VOICEVOXを起動してください）",
        "timeout": "VOICEVOX: 返事が遅いです（起動中かもしれません）",
        "bad_url": "VOICEVOX: URL の書き方が正しくありません",
        "http_error": "VOICEVOX: エラーが返ってきました",
        "not_voicevox": "VOICEVOX: 別のソフトが返事をしています（URL の番号を確かめてください）",
        "unknown": "VOICEVOX: つながっていません",
    }

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self._set_window_icon()
        # 画面に収まる高さにする（13インチのノートなどで窓の下がはみ出すと、Mac は窓を
        # 縮めて本文欄（3.）が潰れる）。上のメニューバー・下の Dock／タスクバーのぶんを空ける
        try:
            room = self.winfo_screenheight() - _px(90)
        except Exception:
            room = _px(880)
        height = max(min(_px(880), room), _px(560))
        # Windows は位置まで決めて、タスクバーを除いた所に収める。位置を OS 任せに
        # すると、1920x1080 の125%表示などでは窓の下端がタスクバーの裏に入り、
        # いちばん下の「再生・生成」の行が見えなかった（その位置が保存されて毎回続く）
        w, height, x, y = self._fit_to_work_area(_px(980), height)
        self.geometry(f"{w}x{height}" + (f"+{x}+{y}" if x is not None else ""))
        self.minsize(_px(860), min(_px(700), height))
        # 構築中（Windowsは絵文字フォントの初回読み込みだけで最大1秒近く固まる）は
        # 本体を隠し、代わりに小さな「起動しています」窓を出す。ダブルクリックから
        # 無反応に見える時間をなくすため。窓が出せない環境でも起動自体は続ける
        self.withdraw()
        self._splash = self._show_splash()

        self.files = []                 # 入力ファイルパス
        self.q = queue.Queue()          # ワーカー→UI 通知
        self.busy = False
        self.speakers = []              # [(label, style_id, speaker_uuid)]
        # ② の、いま選んでいる声のキャラの顔（_update_face）
        self._face_lbl = None
        self._face_cache = {}           # style_id → 縮めた顔（PhotoImage）
        self._face_asked = set()        # 取りに行った style_id（何度も聞かない）
        self._face_want = None          # いま出したい顔の style_id
        # 接続状態はここだけが持つ。self.speakers は「話者リストの中身」で
        # あって「今つながっているか」ではない（切断後もリストは保持する）
        self.conn_state = "unknown"     # unknown / ok / waiting / refused / lost
        self._conn_fail = 0             # ヘルスチェックの連続失敗数（2回で切断）
        self._health_checking = False   # ヘルス確認スレッドの多重起動防止
        self._conn_retry_running = False   # 再接続tickが回っているか（二重起動防止）
        self._conn_retry_interval = 3000   # 再接続tickの間隔（ミリ秒）
        # エンジンURLの決め方: auto=別ポートに逃げたVOICEVOXも自動で見つける /
        # manual=ユーザーが指定したURLだけを使う。設定から復元する
        self._engine_url_mode = "auto"
        # 「画面なしで起動」で自分が起動したエンジン。None のときは
        # 「自分の子ではない」＝終了時に絶対に手を出さない（誤って
        # ユーザーが自分で開いたVOICEVOXを閉じてしまわないため）
        self._engine_proc = None
        self._voicevox_path = None      # 手動で指定された本体の場所
        # 定期的に自分を呼び直すループの予約。終了時にまとめて止める
        # （止めないと、窓を閉じた後にコールバックが動こうとして
        #  Tclがエラーを吐く。pythonw起動では見えないが行儀が悪い）
        self._ticks = {}
        self._engine_gpu = True         # GPU があれば使う（接続設定のチェック。core.want_gpu）
        self._engine_gpu_failed = ""    # GPU で動かなかった VOICEVOX（次からは CPU で起こす）
        self._gpu_warmed = None         # 最初の1文を温めた、GPU で起こした VOICEVOX
        self._engine_deaths = []        # 自分で起こした VOICEVOX が止まった時刻（起こし直す数を数える）
        self._engine_gpu_session_off = ""   # 使っている途中で GPU が続けて止まった VOICEVOX（閉じるまで CPU）
        # アプリと一緒にエンジンを起動するか（最初からオン。左下の「接続設定…」で切れる）
        self._engine_autostart = True
        self._autostart_pending = False  # 起動してすぐの確認が済んだら、起動する
        # 新しい版のお知らせ（_check_for_update。最初からオンで、「接続設定…」で切れる）
        self._update_check_on = True
        self._update_checked_at = 0.0   # 前に GitHub に聞いた時刻（1日1回まで）
        self._update_latest = ""        # そのとき見つけた最新の版（聞かずにすぐ知らせるため）
        self._update_url = ""
        self._drop_after = None          # ドロップ先の登録（窓が出たあとにする）
        self._dnd_ready = False          # D&D のネイティブ部品を読み込めたか
        self.base_url = VOICEVOX_DEFAULT
        self._previewing = False
        self._preview_buf = None        # 再生中WAVの参照保持（GC防止）
        self._saved_speaker = None      # 設定から復元する話者ラベル
        self._playall_stop = None       # 連続再生の停止イベント
        self.replace_rules = []         # 保存済み置換ルール [[find, repl], ...]
        self._dict_win = None           # ユーザー辞書ダイアログ
        self._screen_win = None         # 「スクリーンショット」の範囲選択（撮影待ちの間は True）
        self._screen_last = None        # 最後に囲んだ範囲 ((x0,y0),(x1,y1))（Tkの画面座標）
        self._screen_reading = False    # いまの連続再生が「スクリーンショット」由来か（読後の案内用）
        self._screen_last_text = None   # 画面から最後に読んだ文章（自動めくりで同じ文を読まない）
        self._auto_sig_read = None      # 自動めくり：最後に読んだページの縮小画像
        self._auto_pending = None       # 自動めくり：変わり始めたページ（止まるのを待つ）
        self._auto_grabbing = False     # 自動めくり：撮影スレッドが動いているか
        self._auto_warned = False       # 自動めくり：窓が重なっている案内を出したか
        self.presets = []               # 声プリセット [{name, speaker, speed, ...}]
        self._bookmark = None           # 連続再生のしおり（最後に再生した行番号）
        self._saved_dlg_speaker = None  # 設定から復元するセリフ話者ラベル
        self._cleared_text = None       # 「本文を全消去」で退避した本文（復元用）
        self._cleared_bookmark = None   # そのときのしおり（復元で一緒に戻す）
        self._suppress_modified = False  # <<Modified>>ハンドラの一時抑止（全消去/復元の自編集用）
        self._vdetail_open = False      # プリセット/セリフ行の開閉状態（② の折りたたみ）
        self._resize_after = None       # <Configure>デバウンス用のafterハンドル
        self._reflow_after = None       # 行の並び直し（_reflow）の予約
        self._destroying = False        # 窓を壊している最中か（予約した処理を止める）
        self._sample_cache = {}         # (speaker_uuid, style_id) -> サンプルWAV
        self._shape_report = {}         # 整形レポート {"removed": [...], "confusables": [...]}
        self._report_win = None         # 整形レポートのウィンドウ（多重表示防止）
        self._synth_cancel = None       # 音声生成のキャンセルEvent（生成中のみ非None）
        self._vvproj_cancel = None      # .vvproj書き出しのキャンセルEvent
        self._workers = []              # 終了時に後片づけを待つワーカースレッド
        self._warmed = set()            # 先読み済みの (エンジン, 話者) の組
        self._prefetching = False       # カーソル行の先読みが動いているか
        self._prefetch_after = None     # その予約（カーソルが動くたび取り直す）
        self._prefetch_target = None    # 先読み中の (本文, 話者, 声の数値)
        self._prefetch_done = None      # その先読みが終わったら立つ Event
        self._play_gen = 0              # 再生の世代番号（古い完了通知を捨てる）
        self._play_gap = 0.4            # 連続再生の文間の無音（秒。gap_varと同期）
        self._play_breaks = []          # 再生する各行の前の切れ目（core.line_breaks）
        self._play_longer = True        # 段落・章の切れ目は長め（「段落・章は長め」）
        self._play_left = []            # 読み上げる各行から最後までの秒数（あと◯分のもと）
        self._user_scrolled_at = 0.0    # 最後に自分で本文をスクロールした時刻（_follow_reading）
        self._reading = False           # 本文を広げているか（_set_reading）
        self._mark_imgs = None          # チェックとラジオの印の画像（_paint_marks）
        self._line_sounding = False     # 連続再生で、行の声が鳴っている最中か（口パク用）
        # ふりがな（_furi_refresh）。表示はVOICEVOXが実際に読む読みから作る
        self._furi_cache = {}           # (読む文, 辞書の世代, エンジン版, 辞書あり) → ふりがな
        self._furi_labels = []          # ふりがなの小さなラベル（使い回す）
        self._furi_light_cache = {}     # 下地の色が明るいか（_furi_light）
        self._furi_busy = False         # 読みを取りに行っている最中
        self._furi_retry_at = 0.0       # 取りに行けなかったら、この時刻まで待つ
        self._furi_spacing0 = None      # ふりがなを出す前の行間（戻す用）
        self._furi_font = None          # ふりがなの文字（本文の半分ほどの大きさ）
        self._furi_spaced = False       # ふりがなの余白を取っているか
        self._furi_states = []          # ラベルごとの今の文字・色・位置（変わらなければ触らない）
        self._seek = None               # 誤読候補を探している途中の状態（seek_misreading）
        self._furi_widths = {}          # ふりがなの文字の幅（測った結果を覚えておく）
        self._furi_rise = 0             # ふりがなを文字の上端からどれだけ上に置くか
        self._furi_has_dict = None      # 辞書が入っているか（調べた結果を覚えておく）
        self._furi_installing = False   # 辞書を入れている最中
        self._engine_id = core.VV_ENGINE_ID  # 接続中エンジンの識別子（実物で上書き）
        self._playall_pause = None      # 連続再生の一時停止Event（再生中のみ非None）
        self._preview_stop = None       # 試聴・声サンプルの停止Event（再生中のみ非None）
        self._extract_cancel = None     # テキスト抽出のキャンセルEvent（抽出中のみ非None）
        self._cache_saved = None        # 最後に自動保存した本文（無変化なら書き込まない）
        self._conn_retry_until = 0.0    # VOICEVOX起動後の自動接続リトライの期限
        self._conn_checking = False     # quiet接続確認の実行中フラグ（多重起動防止）
        self._engine_ver = ""           # 接続中エンジンの版（合成キャッシュのキー用）
        self._dict_gen = 0              # 辞書の世代番号（変更のたび+1。キャッシュ鮮度用）
        self.encoders = core.audio_encoders()  # 使える音声変換 {"m4a":..., "mp3":...}

        self.dark_var = tk.BooleanVar(value=False)   # 旧設定キー互換（theme=="dark"と同期）
        self.theme_var = tk.StringVar(value="light")  # テーマの本体（light/dark/hc/zunda）
        self._build_ui()
        # 画像等を画面のどこに落としても効くよう、UI全ウィジェットをドロップ先に登録する。
        # D&D の部品の読み込みと約180の部品の登録で 80ms ほどかかるので、窓を出して
        # 描き終えたあとにする（after_idle では、最初の描画より先に動いていた）
        self._drop_after = self.after(200, self._register_drop_tree_later)
        self._load_settings()
        self._fit_quiet_until = time.monotonic() + 3.0
        self._on_unit_selected()   # まとめ方に応じてN行/無音欄の有効無効を反映
        # ライト/ダークとも clam ベースのデザインパレットを常に適用する（起動時に美観を反映）
        self.apply_theme()
        self._restore_text_cache()
        self._update_step_highlight()   # 本文の有無に応じて「次に押すボタン」を絞る
        self._schedule_stats()          # 復元本文の行数・めやすを表示
        # 構築が済んだのでスプラッシュを引っ込め、本体を表示する
        if self._splash:
            try:
                self._splash.destroy()
            except tk.TclError:
                pass
            self._splash = None
        self.deiconify()
        if getattr(self, "_want_zoomed", False):
            try:
                self.state("zoomed")   # 前回、最大化したまま閉じていた
            except tk.TclError:
                pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick("poll", 120, self._poll_queue)
        self.after(300, self._warm_emoji_font)   # 窓が出たあと、お知らせの絵文字を先に読む
        self.after(600, self._auto_connect)  # 起動時にエンジンへ自動接続
        self.after(2500, self._prewarm_ocr)
        self.after(8000, self._check_for_update)   # 新しい版（1日1回。起動の邪魔をしない頃に）
        self._tick("autosave", 60000, self._autosave_tick)
        self._tick("health", self._HEALTH_INTERVAL_MS, self._health_tick)

    def _warm_emoji_font(self):
        """お知らせに使う絵文字の字体を、窓が出たあとに先に読み込んでおく。Windows は
        初めて絵文字を描くとき、字体を探しに行って 0.2〜1秒止まる。起動直後に見える
        文字からは絵文字を外して窓を早く出し、ここで読んでおけば、あとで「🍂」などの
        お知らせを出したときにも止まらない（測るだけで読み込まれる）。"""
        if self._destroying:
            return
        try:
            tkfont.Font(self, font=UI_FONT or "TkDefaultFont").measure("🍂✅📚📖🎉☕")
        except tk.TclError:
            pass

    def _show_splash(self):
        """起動中だけ出す小窓。失敗したら None（見た目だけの機能なので起動は続行）。
        文言に絵文字を使わないこと：Windowsでは初回の絵文字描画がフォント探索で
        数百ms固まるため、この窓だけは必ず一瞬で出す必要がある。
        T2V_NO_SPLASH=1 なら出さない（GUIテスト用。macOS の CI では、App を何十回も
        作るうちに、この枠なし窓の update で Tk が落ちる／固まることがあった）。"""
        if os.environ.get("T2V_NO_SPLASH"):
            return None
        try:
            sp = tk.Toplevel(self)
            sp.overrideredirect(True)
            sp.configure(bg="#fdfdfd", highlightthickness=1,
                         highlightbackground="#ced6e0")
            f_main = ("Yu Gothic UI", 11, "bold") if core.IS_WIN else None
            f_sub = ("Yu Gothic UI", 9) if core.IS_WIN else None
            tk.Label(sp, text="TextToVoicevox", bg="#fdfdfd",
                     fg="#171a1f", **(dict(font=f_main) if f_main else {})
                     ).pack(padx=_px(32), pady=(_px(20), _px(2)))
            tk.Label(sp, text="起動しています…", bg="#fdfdfd",
                     fg="#6e7781", **(dict(font=f_sub) if f_sub else {})
                     ).pack(padx=_px(32), pady=(0, _px(6)))
            # 名前に VOICEVOX が入っているので、公式のソフトではないことを毎回見せる
            tk.Label(sp, text="VOICEVOX 公式のソフトではありません（個人が作った非公式ツール）",
                     bg="#fdfdfd", fg="#6e7781", **(dict(font=f_sub) if f_sub else {})
                     ).pack(padx=_px(32), pady=(0, _px(16)))
            tk.Frame(sp, bg="#2e7cf0", height=_px(3)).pack(fill="x", side="bottom")
            sp.update_idletasks()
            x = (sp.winfo_screenwidth() - sp.winfo_reqwidth()) // 2
            y = (sp.winfo_screenheight() - sp.winfo_reqheight()) // 2
            sp.geometry(f"+{x}+{y}")
            sp.update()
            return sp
        except Exception:
            return None

    # ---------------- UI構築 ----------------
    def _build_ui(self):
        if UI_FONT:
            try:
                self.option_add("*Font", UI_FONT)
            except Exception:
                pass
        self._setup_styles()
        # 設定のボタンの印（歯車）。色はテーマに合わせて _paint で決める
        self._gear_img = tk.BitmapImage(master=self, data=_gear_xbm(_px(13)))
        # ① の4つのボタンの印（書類・フォルダ・カメラ・写真）。押せないとき用に
        # 薄い色の版も持つ。色は _paint で決める
        self._btn_icons = []
        self._file_img, self._file_img_off = self._icon_pair(_file_xbm)
        self._folder_img, self._folder_img_off = self._icon_pair(_folder_xbm)
        self._camera_img, self._camera_img_off = self._icon_pair(_camera_xbm)
        self._picture_img, self._picture_img_off = self._icon_pair(_picture_xbm)
        # 状態表示は上の帯と立ち絵の吹き出しの両方から参照するため最初に作る
        # 時間帯のあいさつ＋初回起動なら3ステップの道案内（ちょっとした人間味）。
        # 起動直後に見える文字には絵文字を入れない。Windows は初めて絵文字を描くとき、
        # 字体を探しに行って 0.2〜1秒止まる（起動がそのぶん遅れる）。あとで出す
        # お知らせの絵文字は、窓が出たあとに先に読み込んでおく（_warm_emoji_font）
        hour = time.localtime().tm_hour
        hello = ("おはよう" if 5 <= hour < 11
                 else "こんにちは" if 11 <= hour < 17 else "こんばんは")
        self._hello = hello   # 前回テキスト復元メッセージ等でもあいさつを残す
        if os.path.exists(SETTINGS_PATH):
            first = f"{hello}、まずは ① にファイルを追加してね"
        else:
            first = (f"{hello}、はじめまして！ ファイルを入れると文字を取り出すよ。"
                     "あとは ▶ 再生で聴くだけ")
        self.status_var = tk.StringVar(value=first)
        self._first_msg = first   # 本文が入ったら、次の一手の案内に替える（_update_step_highlight）
        # 画面は2列に組む:
        #   上の帯 … アプリ名・状態の文・テーマ・感想を送る（窓の幅いっぱい）
        #   左の列 … 手順 ①取り込む ②声 ③保存。いちばん下に VOICEVOX との接続
        #   右の列 … 本文（ふりがな付き）と、すぐ下の再生バー。伸縮の主役
        #   立ち絵 … 画像を置いた人だけ、さらに右に出す（狭い窓では自動で畳む）
        # 以前は4つの区画を縦に積んでいて、本文欄が画面の4割ほどしかなかった
        self._build_header()
        self._portraits = self._load_portraits()
        self._side = None
        self._side_shown = False
        if self._portraits:
            self._side = ttk.Frame(self, style="Card.TFrame", padding=_px(8))
            self._side.pack(side="right", fill="y", padx=(0, PAD_X), pady=(0, PAD_Y))
            self._side_shown = True
            self._build_portrait_panel(self._side)
        self._main = ttk.Frame(self, style="Ground.TFrame")
        self._main.pack(side="left", fill="both", expand=True)

        self._build_steps_column()
        self._build_files_section()
        self._build_options_section()
        self._build_voice_section()
        self._build_output_section()
        self._build_connection_section()
        self._build_result_section()
        self._hook_steps_wheel()

        if self._side is not None:
            self.bind("<Configure>", self._on_configure, add="+")
        # 右の列の幅が変わったら（窓のリサイズ・立ち絵の開閉）、行の並びを決め直す。
        # 本文欄の大きさが変わったとき（行の出し入れ）も同じ
        self._main.bind("<Configure>", self._schedule_reflow, add="+")
        self.text.bind("<Configure>", self._schedule_reflow, add="+")

    def _icon_pair(self, draw):
        """ふつうのボタンに付ける1色の印と、押せないとき用の薄い色の版（色は _paint で
        テーマに合わせる）。ttk には image=(印, "disabled", 薄い版) で渡す。"""
        pair = tuple(tk.BitmapImage(master=self, data=draw(_px(13))) for _ in range(2))
        self._btn_icons.append(pair)
        return pair

    # ---------------- スタイル（フォント） ----------------
    def _setup_styles(self):
        """見出し・主要ボタン・クレジット用のフォントと、進み具合の細い線の形を用意する。
        実際の色付け（テーマ）は apply_theme()→_paint() が一括で行う。"""
        try:
            base = tkfont.nametofont("TkDefaultFont")
            fam, size = base.cget("family"), int(base.cget("size"))
        except Exception:
            fam, size = "TkDefaultFont", 12

        def bigger(d):   # 負の大きさはピクセル指定なので、大きくするには負の方へ
            return size + d if size > 0 else size - d
        self._heading_font = (fam, size, "bold")
        self._primary_font = (fam, size, "bold")
        self._credit_font = (fam, max(9, size - 2))
        self._step_font = (fam, bigger(2), "bold")     # 「① 取り込む」などの見出し
        self._title_font = (fam, bigger(4), "bold")    # 上の帯のアプリ名
        # 進み具合は上の帯の下の細い線で見せる。clam の進み具合の部品は太さを
        # 変えられないので、太さを変えられる default の部品を借りる
        style = ttk.Style(self)
        style.theme_use("clam")
        for part in ("trough", "pbar"):
            try:
                style.element_create(f"Thin.Horizontal.Progressbar.{part}",
                                     "from", "default")
            except tk.TclError:
                pass   # 作成済み
        for name in ("Thin", "Track"):   # Thin=上の帯の下 / Track=再生バーの位置
            style.layout(f"{name}.Horizontal.TProgressbar", [
                ("Thin.Horizontal.Progressbar.trough", {"sticky": "nswe", "children": [
                    ("Thin.Horizontal.Progressbar.pbar",
                     {"side": "left", "sticky": "ns"})]})])

    # ---------------- 上の帯（アプリ名・状態・テーマ・感想） ----------------
    def _build_header(self):
        head = ttk.Frame(self, style="Ground.TFrame")
        head.pack(side="top", fill="x", padx=PAD_X, pady=(PAD_Y, 0))
        self._header = head
        # アプリ名と、その下にひとこと。名前に VOICEVOX が入っているので、公式の
        # ソフトではないことも書いておく（乗せると詳しい説明）
        name_box = ttk.Frame(head, style="Ground.TFrame")
        name_box.pack(side="left", padx=(_px(4), _px(18)))
        name = ttk.Label(name_box, text="TextToVoicevox", style="Title.TLabel",
                         font=self._title_font)
        name.pack(anchor="w")
        motto = ttk.Label(name_box, text=self.MOTTO, style="GroundSub.TLabel")
        motto.pack(anchor="w")
        for w in (name, motto):
            _Tooltip(w, self.UNOFFICIAL_NOTE)
        # 使った人が一言残せる入口（右端・常に見える所）。ダウンロードは数えられても、
        # 声は届く場所が無いと届かない。立ち絵パネルは画像を置いた人にしか出ないので不可
        fb = ttk.Label(head, text="感想を送る", style="GroundLink.TLabel",
                       cursor="hand2")
        fb.pack(side="right", padx=(_px(14), _px(4)))
        fb.bind("<Button-1>", lambda e: self._open_feedback())
        _Tooltip(fb, "使ってみた一言を送れます（ブラウザで感想フォームが開きます"
                     + ("。アカウントは要りません）。" if FEEDBACK_FORM_URL else "）。"))
        # 説明書（日本語）か使い方の案内（How to use）。パソコンの表示言語で選ぶ（_open_guide）
        guide = ttk.Label(head, text="使い方", style="GroundLink.TLabel", cursor="hand2")
        guide.pack(side="right", padx=(_px(14), 0))
        guide.bind("<Button-1>", lambda e: self._open_guide())
        _Tooltip(guide, "説明書をブラウザで開きます（入れ方・画面の見かた・困ったとき）。\n"
                        "日本語以外のパソコンでは、使い方の案内（How to use）を開きます\n"
                        "（English・繁體中文・简体中文）。")
        self._guide_lbl = guide
        # 新しい版のお知らせ（出ているときだけ、使い方の左に見せる。_show_update）
        self._update_lbl = ttk.Label(head, text="新しい版があります", style="GroundLink.TLabel",
                                     cursor="hand2")
        self._update_lbl.bind("<Button-1>", lambda e: self._open_update())
        _Tooltip(self._update_lbl, lambda: (
            f"{self._update_latest} が出ています（いまは v{core.APP_VERSION}）。\n"
            "押すと、入れ替え方を出します。"))
        # テーマ選択（🍂ライト/🌙ダーク/☀️くっきり/🌿ずんだ）
        self.theme_cb = ttk.Combobox(head, width=12, state="readonly",
                                     values=[l for _k, l, _p in self.THEMES])
        self.theme_cb.pack(side="right", padx=(4, 0))
        self.theme_cb.bind("<<ComboboxSelected>>", self._theme_selected)
        ttk.Label(head, text="テーマ:", style="Ground.TLabel").pack(side="right")
        # 状態の文。pack は後に詰めたものから縮むので最後に置く（長い文があふれたときは
        # ここだけが縮み、テーマ選択は消えない）。入りきらない文は折り返す。高さは2行
        # ぶんで固定して（_reflow_status）、文の長さで下の列が上下に動かないようにする。
        # 3行以上になる文は先頭2行を見せ、全文はマウスを乗せると出す
        box = ttk.Frame(head, width=1, style="Ground.TFrame")
        box.pack(side="left", fill="both", expand=True)
        box.pack_propagate(False)
        self._status_box = box
        self.status_var.trace_add("write", lambda *_: self._schedule_reflow())
        self._status_lbl = ttk.Label(box,
                                     textvariable=self._wrapping_copy(self.status_var),
                                     anchor="w", justify="left", style="Ground.TLabel")
        self._status_lbl.pack(fill="both", expand=True)
        box.bind("<Configure>", lambda e: self._status_lbl.configure(
            wraplength=max(e.width - _px(4), _px(80))), add="+")
        _Tooltip(self._status_lbl, lambda: (
            self.status_var.get()
            if self._status_lbl.winfo_reqheight() > self._status_box.winfo_height()
            else ""))
        # 進み具合（抽出・音声の生成・書き出し）。帯の下の細い線で、止まっているときは
        # 地の色に溶けて見えない（以前は空の進捗バーが入力欄のように見えていた）
        self.progress = ttk.Progressbar(self, mode="determinate",
                                        style="Thin.Horizontal.TProgressbar")
        self.progress.pack(side="top", fill="x", padx=PAD_X, pady=(_px(2), _px(4)))

    # ---------------- 左の列（手順 ①②③ と接続） ----------------
    def _build_steps_column(self):
        """左の列の入れ物。手順は上から並べ、窓が低くて入りきらないときは手順の部分
        だけを縦にスクロールできる（_sync_steps_view）。接続の状態はいつも見える
        よう、いちばん下にスクロールの外で置く。"""
        col = ttk.Frame(self._main, style="Card.TFrame", padding=1)
        col.pack(side="left", fill="y", padx=(PAD_X, 0), pady=(0, PAD_Y))
        self._steps_col = col
        self._conn_box = ttk.Frame(col, padding=(_px(12), _px(6), _px(12), _px(10)))
        self._conn_box.pack(side="bottom", fill="x")
        ttk.Separator(col, orient="horizontal").pack(side="bottom", fill="x",
                                                     padx=_px(12))
        view = tk.Canvas(col, width=1, height=1, highlightthickness=0, borderwidth=0,
                         yscrollincrement=_px(20))
        sb = ttk.Scrollbar(col, orient="vertical", command=view.yview)
        view.configure(yscrollcommand=sb.set)
        view.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(view, padding=(_px(12), _px(8), _px(12), _px(8)))
        self._steps_item = view.create_window(0, 0, window=inner, anchor="nw")
        self._steps, self._steps_view, self._steps_sb = inner, view, sb
        # 列の幅の下限（細すぎると窮屈に見えるため）。中身の方が広ければそちらに合わせる
        ttk.Frame(inner, width=_px(300), height=1).pack()
        inner.bind("<Configure>", self._sync_steps_view, add="+")
        view.bind("<Configure>", self._sync_steps_view, add="+")

    def _step_head(self, text):
        """手順の見出しの行（左に「① 取り込む」など。右に開閉のボタンを置ける）。"""
        row = ttk.Frame(self._steps)
        row.pack(fill="x", pady=(0, GAPY))
        # 文字は直接渡す（全体の文字の指定 *Font が、見た目の指定より優先されるため）
        mark = "①②③".find(text[:1])
        ttk.Label(row, text=text, style=f"Step{mark + 1}.TLabel" if mark >= 0 else "Step.TLabel",
                  font=self._step_font).pack(side="left")
        return row

    def _step_sep(self):
        ttk.Separator(self._steps, orient="horizontal").pack(
            fill="x", pady=(GAPY * 2, GAPY + 2))   # ①②③ の区切りは少し広めに

    def _sync_steps_view(self, event=None):
        """左の列の幅を中身に合わせ、入りきらないときだけスクロールバーを出す。"""
        view, inner, sb = self._steps_view, self._steps, self._steps_sb
        try:
            need = inner.winfo_reqwidth()
            if view.winfo_pixels(view.cget("width")) != need:
                view.configure(width=need)
            w, h = max(view.winfo_width(), need), inner.winfo_reqheight()
            view.itemconfigure(self._steps_item, width=w)
            view.configure(scrollregion=(0, 0, w, h))
            over = h > view.winfo_height() > 1
            if over != bool(sb.winfo_manager()):
                if over:
                    sb.pack(side="right", fill="y", before=view)
                else:
                    sb.pack_forget()
                    view.yview_moveto(0)
        except tk.TclError:
            pass   # 閉じている最中

    def _hook_steps_wheel(self):
        """左の列の上でホイールを回したら、列をスクロールする（入りきらないときだけ）。
        列の中のコンボボックス・数値欄は、カーソルを入れているときだけホイールで値が
        変わる（列をスクロールしているつもりで、話者や話速が勝手に変わらないように）。"""
        seqs = ("<MouseWheel>", "<Button-4>", "<Button-5>")
        for seq in seqs:
            self.bind_all(seq, self._on_steps_wheel, add="+")
        try:   # Tk 9 の Mac のトラックパッド
            self.bind_all("<TouchpadScroll>", self._on_steps_touchpad, add="+")
        except tk.TclError:
            pass

        self._guard_fields(self._steps)
        self.bind_all("<FocusIn>", self._steps_follow_focus, add="+")

    def _guard_fields(self, widget):
        """左の列のコンボボックス・数値欄は、カーソルを入れていないときはホイールで
        値を変えない（列をスクロールする）。後から作った部品にも当てられるようにする。"""
        for c in widget.winfo_children():
            if c.winfo_class() in ("TCombobox", "TSpinbox"):
                for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    c.bind(seq, lambda e, c=c: self._wheel_on_field(e, c))
            self._guard_fields(c)

    def _in_steps(self, widget):
        """部品が左の列のスクロールする部分の中にあるか。"""
        return str(widget).startswith(str(self._steps_view) + ".")

    def _on_steps_wheel(self, event):
        if not self._in_steps(event.widget):
            return None
        w = event.widget
        if isinstance(w, tk.Listbox) and w.yview() != (0.0, 1.0):
            return None   # 一覧が自分でスクロールする
        self._scroll_steps(event)
        return None

    def _wheel_on_field(self, event, field):
        if self.focus_get() is field:
            return None   # カーソルを入れているときは、ホイールで値を変える
        self._scroll_steps(event)
        return "break"

    def _scroll_steps(self, event):
        if not self._steps_sb.winfo_manager():
            return
        num = getattr(event, "num", None)
        if num in (4, 5):
            step = -3 if num == 4 else 3
        else:
            d = getattr(event, "delta", 0) or 0
            step = -int(d / 40) or (-1 if d > 0 else 1)
        self._steps_view.yview_scroll(step, "units")

    def _on_steps_touchpad(self, event):
        if not self._in_steps(event.widget) or not self._steps_sb.winfo_manager():
            return
        dy = event.delta & 0xFFFF   # 下位16ビットが縦の量（符号つき）
        if dy >= 0x8000:
            dy -= 0x10000
        self._steps_wheel_acc = getattr(self, "_steps_wheel_acc", 0) - dy
        step = int(self._steps_wheel_acc / _px(20))
        if step:
            self._steps_wheel_acc -= step * _px(20)
            self._steps_view.yview_scroll(step, "units")

    def _steps_follow_focus(self, event):
        """Tab で左の列の見えていない所へ移ったら、そこまでスクロールする
        （少しでも見えている部品は、クリックした所がずれないよう動かさない）。"""
        w = event.widget
        if not self._in_steps(w) or not self._steps_sb.winfo_manager():
            return
        try:
            view = self._steps_view
            top = w.winfo_rooty() - view.winfo_rooty()
            if top + w.winfo_height() <= 0 or top >= view.winfo_height():
                y = w.winfo_rooty() - self._steps.winfo_rooty()
                view.yview_moveto(max(0.0, (y - _px(24))
                                      / max(self._steps.winfo_height(), 1)))
        except (tk.TclError, AttributeError):
            pass

    # ---------------- ① 取り込む ----------------
    def _build_files_section(self):
        head = self._step_head("① 取り込む")
        # 取り出し方の設定（改行の扱い・PDF・整形）は畳んでおく（_set_opt_rows）
        self._opt_toggle = ttk.Button(head, text="設定 ▷", style="Tool.TButton",
                                      image=self._gear_img, compound="left",
                                      command=self._toggle_opt_rows)
        self._opt_toggle.pack(side="right")
        _Tooltip(self._opt_toggle, "取り出し方の設定（改行の扱い・PDFの読み方・文の整え方）を"
                                   "開きます。")
        btns = ttk.Frame(self._steps)
        btns.pack(fill="x")
        mod = "⌘" if core.IS_MAC else "Ctrl+"
        # 4つとも、何をするかが絵でも分かるように印を付ける（書類・フォルダ・カメラ・写真）
        add = ttk.Button(btns, text="ファイル追加",
                         image=(self._file_img, "disabled", self._file_img_off),
                         compound="left", command=self.add_files)
        fold = ttk.Button(btns, text="フォルダ追加",
                          image=(self._folder_img, "disabled", self._folder_img_off),
                          compound="left", command=self.add_folder)
        # 画面を撮って、囲んだ所を読む。誰でも知っている「スクリーンショット」の名前と
        # カメラの印にする（以前の「画面から読む」は、何が起きるか伝わりにくかった）
        self.screen_btn = ttk.Button(btns, text="スクリーンショット",
                                     image=(self._camera_img, "disabled",
                                            self._camera_img_off),
                                     compound="left", command=self.screen_read)
        # 「クリップボード」「OCR」は、ふだんの言葉ではないので使わない（何をする
        # ボタンかを言葉で書く）
        self.clip_btn = ttk.Button(btns, text="コピーした画像を読む",
                                   image=(self._picture_img, "disabled",
                                          self._picture_img_off),
                                   compound="left", command=self.clipboard_ocr)
        for w, r, c in ((add, 0, 0), (fold, 0, 1),
                        (self.screen_btn, 1, 0), (self.clip_btn, 1, 1)):
            w.grid(row=r, column=c, sticky="ew", padx=(0 if c == 0 else GAPX, 0), pady=2)
        btns.columnconfigure((0, 1), weight=1, uniform="in")
        _Tooltip(add, f"画像・PDF・テキストなどを選んで追加します（{mod}O）。")
        _Tooltip(self.screen_btn,
                 "画面を撮って、読みたい所をドラッグで囲むと、その文字を読み取って\n"
                 f"すぐ読み上げます（{mod}R）。電子書籍・PDF・Webページなどに。\n"
                 "読み取った文字は本文の最後に足されます。"
                 + ("\n※初回は「システム設定 → プライバシーとセキュリティ\n"
                    "　→ 画面収録」でこのアプリの許可が要ります。"
                    if core.IS_MAC else ""))
        _Tooltip(self.clip_btn,
                 "コピーした画像（ほかのアプリでコピーした図や写真など）の文字を\n"
                 "読み取って、本文の最後に足します。画面に出ている文字なら、\n"
                 "左の「スクリーンショット」の方が手早いです。\n"
                 "コピーしたファイル（画像・PDF など）なら、① の一覧に入れます。")
        # 「同じ範囲を読む」と「めくったら自動で読む」は、はじめて範囲を囲んだときに
        # この枠へ出す（_ensure_again_btn）。起動時の画面を増やさない（macOS の CI で、
        # 起動時にボタンを1つ足しただけで Tk が窓を出す瞬間に落ちるようになったため）
        self._screen_btns = ttk.Frame(self._steps)
        self._screen_btns.pack(fill="x")
        self._screen_btns.columnconfigure((0, 1), weight=1, uniform="in")
        self.screen_again_btn = None
        self.auto_page_cb = None
        self.auto_page_var = tk.BooleanVar(value=False)   # 自動めくり読み（毎回オフで起動）

        # ファイルの一覧（順序＝抽出・結合の順序）。右に並べ替えと外すボタン
        lst = ttk.Frame(self._steps)
        lst.pack(fill="x", pady=(GAPY, 0))
        tools = ttk.Frame(lst)
        tools.pack(side="right", fill="y", padx=(GAPX // 2, 0))
        up = ttk.Button(tools, text="▲", width=2, style="Mini.Quiet.TButton",
                        command=lambda: self._move_selected(-1))
        dn = ttk.Button(tools, text="▼", width=2, style="Mini.Quiet.TButton",
                        command=lambda: self._move_selected(+1))
        rm = ttk.Button(tools, text="×", width=2, style="Mini.Quiet.TButton",
                        command=self.remove_selected)
        for b in (up, dn, rm):
            b.pack(fill="x", pady=(0, 2))
        for b in (up, dn):
            _Tooltip(b, "選択したファイルの順序を入れ替えます\n"
                        "（上から順に取り出して、つなげます）。")
        _Tooltip(rm, "選択したファイルを一覧から外します（Delete）。\n"
                     "全部外すときは、一覧を右クリック →「全部外す」。")
        sb = ttk.Scrollbar(lst, command=lambda *a: self.listbox.yview(*a))
        sb.pack(side="right", fill="y")
        self.listbox = tk.Listbox(lst, height=3, width=10, selectmode="extended",
                                  activestyle="none")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.config(yscrollcommand=sb.set)
        # 空のときは、ここに落とせることを一覧の中に書いておく（_sync_list_hint）
        self._list_hint = tk.Label(
            self.listbox, justify="center", bd=0,
            text=("ここに画像・PDF・フォルダを\nドラッグ＆ドロップ" if _HAS_DND
                  else "「ファイル追加」で\n画像・PDFを選んでね"))
        self._list_hint.bind("<Button-1>", lambda e: self.listbox.focus_set())
        self._sync_list_hint()
        # キーボード操作: Delete/BackSpace=削除・Alt+↑↓=並べ替え。
        # 選択1件のときはフルパスを状態欄に表示（同名ファイルの区別用）
        self.listbox.bind("<Delete>", self._kb_remove_files)
        self.listbox.bind("<BackSpace>", self._kb_remove_files)
        self.listbox.bind("<Alt-Up>",
                          lambda e: (self._move_selected(-1), "break")[1])
        self.listbox.bind("<Alt-Down>",
                          lambda e: (self._move_selected(+1), "break")[1])
        self.listbox.bind("<<ListboxSelect>>", self._show_file_path)
        # 右クリック: 外す・全部外す・並べ替え（ボタンを減らした分の置き場）
        menu = tk.Menu(self.listbox, tearoff=0)
        menu.add_command(label="選択したファイルを外す", command=self.remove_selected)
        menu.add_command(label="全部外す（全クリア）", command=self.clear_files)
        menu.add_separator()
        menu.add_command(label="▲ 上へ", command=lambda: self._move_selected(-1))
        menu.add_command(label="▼ 下へ", command=lambda: self._move_selected(+1))

        def popup(e):
            i = self.listbox.nearest(e.y)
            if self.files and not self.listbox.selection_includes(i):
                self.listbox.selection_clear(0, "end")
                self.listbox.selection_set(i)
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()
            return "break"
        for seq in (("<Button-2>", "<Control-Button-1>") if core.IS_MAC
                    else ("<Button-3>",)):
            try:
                self.listbox.bind(seq, popup)
            except tk.TclError:
                pass
        # ドラッグ＆ドロップは _build_ui 完了後に _register_drop_tree が全ウィジェットへ
        # 一括登録する（新設フレームも子ツリーに繋がっていれば自動で対象になる）。macOSでは
        # 子ウィジェットがルート窓を覆うため、個別登録でないと「落としても反応しない」ため。

        # 主要操作（抽出）と整形レポート。設定を開くと、この行の上に出る（_set_opt_rows）
        run = ttk.Frame(self._steps)
        run.pack(fill="x", pady=(GAPY * 2, 0))
        self._extract_row = run
        self.report_btn = ttk.Button(run, text="直した所", width=7, style="Quiet.TButton",
                                     command=self.show_shape_report, state="disabled")
        # 見せる物があるときだけ出す（_merge_report）。前は、取り出す前から押せない
        # 灰色のボタンが大きく出ていて、① が重く見えた
        _Tooltip(self.report_btn, lambda: (
            "取り出すときに外した行（ノイズ・ページの見出し）と、直した読み取りの誤字を確かめられます。"
            if (self._shape_report.get("removed")
                or self._shape_report.get("confusables")
                or self._shape_report.get("heads"))
            else "取り出すと、消した行と直した誤字を、ここで確かめられます。"))
        self.extract_btn = ttk.Button(run, text="▶ テキスト抽出 実行",
                                      style="Primary.TButton", command=self.start_extract)
        self.extract_btn.pack(side="left", fill="x", expand=True)
        _Tooltip(self.extract_btn,
                 f"追加したファイルからテキストを抽出します（{mod}Return）。")

    def _sync_list_hint(self):
        """ファイルの一覧が空のときだけ、中に「ここに落とせる」案内を出す。"""
        hint = getattr(self, "_list_hint", None)
        if hint is None:
            return
        if self.files:
            hint.place_forget()
        else:
            hint.place(relx=0.5, rely=0.5, anchor="center")

    # ---------------- ① の取り出し方の設定（ふだんは畳む） ----------------
    def _build_options_section(self):
        """設定の値（変数）だけを先に作る。見た目（約40の部品）は、初めて開いたときに
        作る（_ensure_opt_panel）。開いたことのない人のほうが多く、起動のたびに作ると
        起動が約0.1秒遅くなっていた。"""
        self._opt_panel = None
        self._opt_open = False
        self._adv_open = False
        self._adv_frame = None
        self._adv_btn = None
        self._ocr_btn = None
        self.mode_var = tk.StringVar(value="sentence")
        self.pdf_var = tk.StringVar(value="auto")
        self.dpi_var = tk.IntVar(value=300)
        self.pages_var = tk.StringVar()
        self.pre_var = tk.BooleanVar(value=True)
        self.blank_var = tk.BooleanVar(value=True)
        self.ascii_var = tk.BooleanVar(value=True)
        self.smartjoin_var = tk.BooleanVar(value=False)
        self.denoise_var = tk.BooleanVar(value=True)
        self.fixconf_var = tk.BooleanVar(value=True)
        self.join_var = tk.BooleanVar(value=False)
        self.pruby_var = tk.BooleanVar(value=False)
        self.norm_var = tk.BooleanVar(value=False)
        self.urlskip_var = tk.BooleanVar(value=True)
        self.heads_var = tk.BooleanVar(value=True)   # PDF の柱とページ番号を外す
        # 読み上げる直前に、数字・日付・記号の読み方を直す（core.fix_readings。既定オン）。
        # 画面の本文は変えない。切り替えは読み方辞書の窓にある
        self.read_fix_var = tk.BooleanVar(value=True)
        self.read_fix_var.trace_add("write", self._on_read_fix)

    def _ensure_opt_panel(self):
        """① の取り出し方の設定の見た目を作る（初めて開いたときに1回だけ）。"""
        if self._opt_panel is not None:
            return self._opt_panel
        opt = ttk.Frame(self._steps)
        self._opt_panel = opt
        ind = (GAPX * 2, 0)   # 見出しの下の字下げ

        ttk.Label(opt, text="改行の扱い", style="Cluster.TLabel").pack(anchor="w")
        ttk.Radiobutton(opt, text="文ごとに改行（VOICEVOX推奨）", variable=self.mode_var,
                        value="sentence").pack(anchor="w", padx=ind)
        ttk.Radiobutton(opt, text="元の改行を保持", variable=self.mode_var,
                        value="keep").pack(anchor="w", padx=ind)

        ttk.Label(opt, text="PDF", style="Cluster.TLabel").pack(anchor="w",
                                                              pady=(GAPY * 2, 0))
        ttk.Radiobutton(opt, text="自動（文字の無いページは画像から読む）", variable=self.pdf_var,
                        value="auto").pack(anchor="w", padx=ind)
        ttk.Radiobutton(opt, text="いつも画像から読む（スキャン・文字化けに）",
                        variable=self.pdf_var, value="ocr").pack(anchor="w", padx=ind)
        prow = ttk.Frame(opt)
        prow.pack(anchor="w", padx=ind, pady=(2, 0))
        ttk.Label(prow, text="細かさ:").pack(side="left")
        dpi_sb = ttk.Spinbox(prow, from_=150, to=400, increment=50, width=5,
                             textvariable=self.dpi_var)
        dpi_sb.pack(side="left", padx=(2, GAPX))
        _Tooltip(dpi_sb, "画像から読むときの細かさ（DPI）です。大きいほど小さな字まで\n"
                         "読めますが、時間がかかります（ふつうは300）。")
        ttk.Label(prow, text="ページ:").pack(side="left")
        pe = ttk.Entry(prow, textvariable=self.pages_var, width=9)
        pe.pack(side="left", padx=(2, 0))
        _Tooltip(pe, "読み取るPDFのページ範囲。例: 5-320 / 1-3,10-\n"
                     "空欄=全ページ。表紙・目次・索引を飛ばすのに\n"
                     "（複数PDFには同じ範囲が適用されます）。")

        # 文の整え方: よく使う項目は並べ、上級者向けは「詳細設定」でさらに畳む
        ttk.Label(opt, text="文の整え方", style="Cluster.TLabel").pack(anchor="w",
                                                               pady=(GAPY * 2, 0))
        common_defs = [
            (self.pre_var, "画像を見やすくしてから読む",
             "写真やスキャンの画像を見やすく直してから読み、読み間違いを減らします。"),
            (self.blank_var, "空行を削除",
             "取り出した文章から空行を消します。段落の頭の字下げ（全角スペース）は残すので、\n"
             "③ の「段落・章は長め」は、空行を消しても効きます\n"
             "（字下げの無い文章では、空行が段落の切れ目です。段落の間を空けたいときは外してください）。"),
            (self.heads_var, "PDF のページの見出しと番号を読まない",
             "PDF の本で、ページごとの上や下に出る本や章の名前（柱）とページ番号を外します\n"
             "（読むと、ページが変わるたびに同じ名前を読み、章の名前なら毎ページ長い間が入るため）。\n"
             "章の見出し（第一章 など）は、最初の1回（章の始まり）を残します。外した行は「直した所」で見られます。"),
            (self.ascii_var, "英数字間の空白を保持",
             "英単語の間の半角スペースを残します。"),
            (self.smartjoin_var, "折り返しを連結（1段組みの本文向け）",
             "ページ幅で折り返された行を1文につなげます。"),
            (self.denoise_var, "画面キャプチャのノイズを除去",
             "字幕・局ロゴ・UIラベルなど画面上の余計な文字を取り除きます。"),
            (self.fixconf_var, "読み取りの間違いを直す（力⇄カ・一⇄ー）",
             "画像から読んだときに取り違えやすい、形の似た字を、前後から考えて直します。\n"
             "画像やスキャンした PDF から読んだ文にだけ効きます（テキストファイルには効きません）。"),
        ]
        for var, label, tip in common_defs:
            cb = ttk.Checkbutton(opt, text=label, variable=var)
            cb.pack(anchor="w", padx=ind, pady=1)
            _Tooltip(cb, tip)

        more = ttk.Frame(opt)
        more.pack(fill="x", padx=ind, pady=(GAPY, 0))
        self._adv_btn = ttk.Button(more, text="詳細設定 ▷", style="Tool.TButton",
                                   image=self._gear_img, compound="left",
                                   command=self._toggle_advanced)
        self._adv_btn.pack(side="left")
        # 文字を読む部品は最初のセットアップで入る。入らなかったときだけ、入れ直す入口を出す
        # （押しても何も入れない。説明を出すだけ）。Mac は Apple の読み取りがもともと強い
        if core.IS_WIN and not core.strong_ocr_available():
            self._ocr_btn = ttk.Button(more, text="読み取りを強くする", style="Tool.TButton",
                                       command=self.open_strong_ocr_help)
            self._ocr_btn.pack(side="left", padx=(GAPX, 0))
            _Tooltip(self._ocr_btn,
                     "ゲームや写真の上の文字、英語まじりの文まで正しく読めるようにする\n"
                     "部品の説明です。押しただけでは何も入りません。")

        # 詳細設定（既定は畳む＝pack しない）
        self._adv_frame = ttk.Frame(opt)
        adv_defs = [
            (self.join_var, "改行で途切れた文を連結（小説向け）",
             "句点で終わらない改行を前の行につなげます。"),
            (self.pruby_var, "括弧ルビ除去 例:漢字(かんじ)",
             "漢字の後の括弧内の読みがなを削除します。"),
            (self.norm_var, "字の形をそろえる（全角の英数字→半角など）",
             "取り出した本文の字そのものを書き換えて、形をそろえます\n"
             "（ＡＢＣ１２３→ABC123、ｶﾀｶﾅ→カタカナ、①→1、㈱→株式会社）。\n"
             "読み上げるときの読み方は、これを付けなくても直します\n"
             "（読み方辞書の窓の「数字・日付・記号の読み方を直す」）。"),
            (self.urlskip_var, "URL・メールを読み飛ばす",
             "本文中のURLやメールアドレスを除きます\n"
             "（読み上げると1文字ずつ読まれてしまうため。既定ON）。"),
        ]
        for var, label, tip in adv_defs:
            cb = ttk.Checkbutton(self._adv_frame, text=label, variable=var)
            cb.pack(anchor="w", pady=1)
            _Tooltip(cb, tip)
        self._set_advanced(self._adv_open)   # 覚えていた開閉を見た目にも当てる
        # 後から作った部品も、ドロップ先・列のホイールの扱いをほかと同じにする
        self._register_drop_tree(opt)
        self._guard_fields(opt)
        return opt

    def _toggle_opt_rows(self):
        self._set_opt_rows(not self._opt_open)

    def _set_opt_rows(self, show):
        """① の取り出し方の設定を開く/畳む（抽出ボタンのすぐ上に出す）。"""
        self._opt_open = bool(show)
        if self._opt_open:
            self._ensure_opt_panel().pack(fill="x", pady=(GAPY * 3, 0),
                                          before=self._extract_row)
            self._opt_toggle.config(text="設定 ▽")
        else:
            if self._opt_panel is not None:
                self._opt_panel.pack_forget()
            self._opt_toggle.config(text="設定 ▷")

    def _toggle_advanced(self):
        self._set_advanced(not self._adv_open)

    def _set_advanced(self, show):
        """整形の詳細設定を開閉する（pack/pack_forget と矢印表示の更新のみ）。
        まだ設定の見た目を作っていなければ、開閉だけ覚えておく（作ったときに当てる）。"""
        self._adv_open = bool(show)
        if self._adv_frame is None:
            return
        if self._adv_open:
            self._adv_frame.pack(fill="x", padx=(GAPX * 2, 0), pady=(GAPY, 0))
            self._adv_btn.config(text="詳細設定 ▽")
        else:
            self._adv_frame.pack_forget()
            self._adv_btn.config(text="詳細設定 ▷")

    def _tip_engine_gate(self, ready_text, busy_gated=True):
        """エンジン接続が必要なボタンのツールチップ文を返す関数を作る。
        無効中は「なぜ押せないか」、有効なら本来の説明を出す（ホバー時に評価）。
        busy_gated: 処理・再生中に実際に無効化されるボタン（生成/試聴/連続再生）は
        True。busy中も押せるボタン（辞書・vvproj保存）は False にして誤案内を防ぐ。"""
        def _text():
            if not self._engine_ready():
                return ("VOICEVOX につながっていません。\n"
                        "左下の「つながるか確かめる」を押すと使えるようになります。")
            if busy_gated and (self.busy or self._previewing):
                return "処理・再生の実行中です。完了までお待ちください。"
            return ready_text
        return _text

    def _on_format_selected(self, event=None):
        """音声形式の選択に応じたヒントと、M4B時の「まとめ方」無効化を行う
        （M4Bは常に全文結合のため、触れるのに無視されるコンボは混乱のもと）。"""
        if self._out_format() == "m4b":
            self.unit_cb.config(state="disabled")
            self.status_var.set("📚 M4B: 全文を1冊にまとめて、章見出し（第◯章など）から"
                                "自動でチャプターを付けるよ")
        else:
            self.unit_cb.config(state="readonly")
        self._on_unit_selected()

    def _on_unit_selected(self, event=None):
        """効かない設定を触れなくする：「N行」はまとめ方=N行ごとのときだけ、
        「文間の無音」は複数行をまとめる形式（M4B含む）のときだけ編集できる。
        値を変えても何も起きないコントロールを灰色にして混乱を防ぐ。"""
        if not getattr(self, "nlines_sb", None):
            return   # 構築順の都合でまだ無い場合は _build 後の呼び出しで反映される
        unit = self._unit()
        fmt = self._out_format()
        # M4Bは常に全文結合＝「N行」は使われないので灰色に（gap_sbと同じ考慮）
        self.nlines_sb.config(state="normal" if (unit == "nlines"
                                                 and fmt != "m4b")
                              else "disabled")
        gap_used = unit != "each" or fmt == "m4b"
        self.gap_sb.config(state="normal" if gap_used else "disabled")

    def _set_conn_compact(self, compact):
        """接続の表示を切り替える。compact=True で詳細（起動・URL・接続確認）を
        畳んで「接続設定…」ボタンだけにする（つながっているときは触る必要がない）。"""
        if compact:
            self._conn_detail.pack_forget()
            if not self._conn_edit_btn.winfo_manager():
                self._conn_edit_btn.pack(side="right", padx=(GAPX, 0),
                                         before=self.engine_lbl)
        else:
            self._conn_edit_btn.pack_forget()
            self._conn_detail.pack(fill="x")

    def _toggle_voice_detail(self):
        self._set_voice_detail(not self._vdetail_open)

    def _set_voice_detail(self, show):
        """② のプリセット/セリフ別話者を開閉する（① の設定と同じ pack 切替）。"""
        self._vdetail_open = bool(show)
        if self._vdetail_open:
            self._vb_frame.pack(fill="x", pady=(GAPY * 2, 0), before=self._vb_sep)
            self._vdetail_btn.config(text="お気に入り・セリフ ▽")
        else:
            self._vb_frame.pack_forget()
            self._vdetail_btn.config(text="お気に入り・セリフ ▷")

    def _update_step_highlight(self):
        """大事な操作の3つ（「▶ テキスト抽出 実行」「♪ 音声を生成」「▶ 再生」）を
        テーマの色で塗り、ほかのボタンとひと目で見分けられるようにする。本文が空の
        あいだは、まだ押しても意味のない「♪ 音声を生成」「▶ 再生」には色を付けない
        （最初にどれを押すかも分かる）。以前は「次に押す1つ」だけを塗っていて、
        抽出と音声の生成が、ふつうのボタンと大きさしか違わなかった。
        Primary⇄Secondary は同じ寸法なので、切り替えても並びは動かない。"""
        try:
            has_text = bool(self.text.get("1.0", "end-1c").strip())
            # 本文があるのに「まずは ① にファイルを追加してね」のままにしない
            # （貼り付けた・打ち込んだとき。取り出したときは、その結果の文が出る）
            if has_text and self.status_var.get() == getattr(self, "_first_msg", None):
                self.status_var.set("本文が入ったよ。▶ 再生で聴けます")   # 帯の1行に収まる長さで
            self.extract_btn.config(style="Primary.TButton")
            self.synth_btn.config(style="Primary.TButton" if has_text
                                  else "Secondary.TButton")
            self.playall_btn.config(style="Accent.TButton" if has_text else "Bold.TButton")
            self._sync_text_hint(has_text)
        except (tk.TclError, AttributeError):
            pass  # 構築途中・破棄中は無視

    def _sync_text_hint(self, has_text):
        """本文が空のときだけ、本文欄の中に案内を出す（初めての人に、何をすればいいか）。"""
        self._has_text = has_text
        self._sync_furi_notice()
        hint = getattr(self, "_text_hint", None)
        if hint is None:
            return
        btn = getattr(self, "_text_hint_btn", None)
        if has_text:
            hint.place_forget()
            if btn is not None:
                btn.place_forget()
        else:
            hint.place(relx=0.5, rely=0.4, anchor="center")
            if btn is not None:
                btn.place(relx=0.5, rely=0.4, y=_px(44), anchor="n")

    # ---------------- ② 声 ----------------
    def _build_voice_section(self):
        self._step_sep()
        head = self._step_head("② 声")
        # プリセット/セリフ別話者は使わない人も多いので畳んでおく（_set_voice_detail）
        self._vdetail_btn = ttk.Button(head, text="お気に入り・セリフ ▷",
                                       style="Tool.TButton",
                                       image=self._gear_img, compound="left",
                                       command=self._toggle_voice_detail)
        self._vdetail_btn.pack(side="right")
        _Tooltip(self._vdetail_btn,
                 "声のお気に入りの保存と、セリフ（「」で始まる行）を別の声で読む設定を開閉します。")
        # 話者選択は2段（キャラ→スタイル）。170超のスタイルが1本に並ぶ長大な
        # ドロップダウンを解消する。speaker_cb はそのキャラのスタイルだけを持つ。
        # 左には、いま選んでいる声のキャラの顔（VOICEVOX の公式イラスト。_update_face）。
        # 顔の高さに合わせて、キャラとスタイルは上下に並べる
        row = ttk.Frame(self._steps)
        row.pack(fill="x")
        self._face_px = _px(52)
        self._face_blank = tk.BitmapImage(master=self, data=_person_xbm(self._face_px))
        self._face_lbl = tk.Label(row, image=self._face_blank, bd=0, highlightthickness=0,
                                  width=self._face_px, height=self._face_px)
        self._face_lbl.pack(side="left", padx=(0, GAPX))
        self._face_lbl.bind("<Button-1>", self._face_clicked)
        _Tooltip(self._face_lbl, self._face_tip)
        sel = ttk.Frame(row)
        sel.pack(side="left", fill="x", expand=True)
        self.char_cb = ttk.Combobox(sel, width=10, state="disabled")
        self.char_cb.pack(fill="x")
        self.char_cb.bind("<<ComboboxSelected>>", self._char_selected)
        _Tooltip(self.char_cb, "キャラクターを選びます（スタイルは下で選択）。")
        self.speaker_cb = ttk.Combobox(sel, width=11, state="disabled")
        self.speaker_cb.pack(fill="x", pady=(GAPY, 0))
        self.speaker_cb.bind("<<ComboboxSelected>>", self._update_portrait, add="+")
        # 声を選び直したら、その声のモデルを裏で読み込ませておく
        self.speaker_cb.bind("<<ComboboxSelected>>",
                             lambda e: self._warm_speaker(), add="+")
        self.char_cb.bind("<<ComboboxSelected>>",
                          lambda e: self.after(50, self._warm_speaker), add="+")
        _Tooltip(self.speaker_cb, "スタイル（ノーマル・あまあま等）を選びます。\n"
                                  "「♪ 声を聴く」で聴き比べできます。")
        # 話速〜音量（2行）と、聴いて確かめるボタン（右）
        params = ttk.Frame(self._steps)
        params.pack(fill="x", pady=(GAPY, 0))
        self.speed_var = tk.DoubleVar(value=1.0)
        self.pitch_var = tk.DoubleVar(value=0.0)
        self.into_var = tk.DoubleVar(value=1.0)
        self.vol_var = tk.DoubleVar(value=1.0)
        specs = [("速さ", self.speed_var, 0.5, 2.0, 0.1, 5),
                 ("高さ", self.pitch_var, -0.15, 0.15, 0.01, 5),
                 ("抑揚", self.into_var, 0.0, 2.0, 0.1, 5),
                 ("音量", self.vol_var, 0.0, 2.0, 0.1, 5)]
        for i, (name, var, lo, hi, inc, w) in enumerate(specs):
            r, c = divmod(i, 2)
            ttk.Label(params, text=name).grid(row=r, column=2 * c, sticky="w",
                                              padx=(0 if c == 0 else GAPX, 2), pady=1)
            ttk.Spinbox(params, from_=lo, to=hi, increment=inc, width=w,
                        textvariable=var).grid(row=r, column=2 * c + 1, sticky="w", pady=1)
        params.columnconfigure(4, weight=1)
        mod = "⌘" if core.IS_MAC else "Ctrl+"
        # 声サンプル試聴（エンジン同梱の公式サンプルを再生。声選びが楽になる）
        self.sample_btn = ttk.Button(params, text="♪ 声を聴く",
                                     command=self.play_speaker_sample, state="disabled")
        self.sample_btn.grid(row=0, column=5, sticky="ew", padx=(GAPX, 0), pady=1)
        _Tooltip(self.sample_btn, self._tip_engine_gate(
            "選んでいる声の、公式の見本を流します。"))
        # いまの声と調整で、カーソルのある行を1行だけ聴く（調整の効き目を確かめる）
        self.preview_btn = ttk.Button(params, text="▶ 試聴",
                                      command=self.preview_selected, state="disabled")
        self.preview_btn.grid(row=1, column=5, sticky="ew", padx=(GAPX, 0), pady=1)
        _Tooltip(self.preview_btn, self._tip_engine_gate(
            f"カーソルのある行を1行だけ試し聴きします（{mod}P）。"))

        # プリセット/セリフ別話者（既定は畳む。_set_voice_detail が pack/pack_forget で
        # 開閉する。子ウィジェットはツリーに繋がるため、未表示でもD&D登録や値設定は効く）
        vb = ttk.Frame(self._steps)
        self._vb_frame = vb
        r1 = ttk.Frame(vb)
        r1.pack(fill="x")
        ttk.Label(r1, text="お気に入り:").pack(side="left")
        self.preset_cb = ttk.Combobox(r1, width=10, state="readonly", values=[])
        self.preset_cb.pack(side="left", fill="x", expand=True, padx=2)
        self.preset_cb.bind("<<ComboboxSelected>>", self._preset_selected)
        ttk.Button(r1, text="保存", width=4, style="Quiet.TButton",
                   command=self.save_preset).pack(side="left", padx=1)
        ttk.Button(r1, text="削除", width=4, style="Quiet.TButton",
                   command=self.del_preset).pack(side="left", padx=1)
        self.dlg_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(vb, text="セリフ（「」で始まる行）は別の声:",
                        variable=self.dlg_var).pack(anchor="w", pady=(GAPY, 0))
        self.dlg_speaker_cb = ttk.Combobox(vb, width=20, state="disabled")
        self.dlg_speaker_cb.pack(fill="x", padx=(GAPX * 3, 0))
        ttk.Label(vb, text="※行の頭に「@名前:」と書いても声を変えられます", style="Sub.TLabel").pack(
            anchor="w", padx=(GAPX * 3, 0))
        # vb再表示時の挿入位置アンカー（pack(before=)用に参照を保持する）。② と ③ の区切り
        self._vb_sep = ttk.Separator(self._steps, orient="horizontal")
        self._vb_sep.pack(fill="x", pady=(GAPY + 2, GAPY))

    # ---------------- ③ 保存 ----------------
    def _build_output_section(self):
        head = self._step_head("③ 保存")
        self.cache_btn = ttk.Button(head, text="声の保管…", style="Tool.TButton",
                                    image=self._gear_img, compound="left",
                                    command=self.open_cache_dialog)
        self.cache_btn.pack(side="right")
        _Tooltip(self.cache_btn,
                 "一度作った声をしまっておき、2回目からの生成・再生を速くしています。\n"
                 "しまってある量の確認・上限の変更・消すことができます。")
        grid = ttk.Frame(self._steps)
        grid.pack(fill="x")
        ttk.Label(grid, text="音声形式:").grid(row=0, column=0, sticky="w", pady=1)
        r0 = ttk.Frame(grid)
        r0.grid(row=0, column=1, sticky="w", padx=(GAPX, 0))
        self.fmt_cb = ttk.Combobox(r0, width=7, state="readonly",
                                   values=self._format_choices())
        self.fmt_cb.current(0)
        self.fmt_cb.pack(side="left")
        self.fmt_cb.bind("<<ComboboxSelected>>", self._on_format_selected, add="+")
        _Tooltip(self.fmt_cb,
                 "WAV/M4A/MP3は通常の音声ファイル。\n"
                 "M4B(本)はオーディオブック：全文を1冊にまとめ、章見出し（第◯章など）から\n"
                 "自動でチャプターを付けます（Apple Books等で頭出しできます）。")
        self.srt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r0, text="字幕(.srt)も保存",
                        variable=self.srt_var).pack(side="left", padx=(GAPX * 2, 0))
        ttk.Label(grid, text="まとめ方:").grid(row=1, column=0, sticky="w", pady=1)
        r1 = ttk.Frame(grid)
        r1.grid(row=1, column=1, sticky="w", padx=(GAPX, 0))
        self.unit_cb = ttk.Combobox(r1, width=13, state="readonly",
                                    values=list(self._UNITS.values()))
        self.unit_cb.current(0)
        self.unit_cb.pack(side="left")
        self.unit_cb.bind("<<ComboboxSelected>>", self._on_unit_selected, add="+")
        _Tooltip(self.unit_cb,
                 "全部を1つに: 本文を1つの音声ファイルにします（本を聴くならこれ）。\n"
                 "1行=1ファイル: 1行ごとに別のファイルにします（動画の素材などに）。\n"
                 "行数ごと: 右の数の行ずつ、1つのファイルにします。\n"
                 "段落ごと: 段落ごとに1つのファイルにします。\n"
                 f"ファイルが{self._MANY_FILES}個を超えるときは、作る前に聞きます。")
        self.nlines_var = tk.IntVar(value=50)
        self.nlines_sb = ttk.Spinbox(r1, from_=2, to=1000, increment=10, width=5,
                                     textvariable=self.nlines_var)
        self.nlines_sb.pack(side="left", padx=(GAPX, 0))
        _Tooltip(self.nlines_sb,
                 "まとめ方が「行数ごと」のときの、1つのファイルに入れる行の数です\n"
                 "（ほかのまとめ方では使いません）。")
        ttk.Label(r1, text="行").pack(side="left", padx=(2, 0))
        ttk.Label(grid, text="文の間:").grid(row=2, column=0, sticky="w", pady=1)
        r2 = ttk.Frame(grid)
        r2.grid(row=2, column=1, sticky="w", padx=(GAPX, 0))
        self.gap_var = tk.DoubleVar(value=0.4)
        self.gap_sb = ttk.Spinbox(r2, from_=0.0, to=3.0, increment=0.1, width=5,
                                  textvariable=self.gap_var)
        self.gap_sb.pack(side="left")
        ttk.Label(r2, text="秒").pack(side="left", padx=(2, 0))
        _Tooltip(self.gap_sb,
                 "複数の行を1ファイルにまとめるときや連続再生のときに、\n"
                 "文と文のあいだに入れる、音の無い時間です（再生バーの「文の間」と同じ設定です）。")
        # 段落・章の切れ目は長めに空ける（core.line_gaps。連続再生と保存の両方に効く）
        self.para_pause_var = tk.BooleanVar(value=True)
        para_cb = ttk.Checkbutton(r2, text="段落・章は長め", variable=self.para_pause_var,
                                  command=self._on_para_pause)
        para_cb.pack(side="left", padx=(GAPX * 2, 0))
        _Tooltip(para_cb,
                 "段落の切れ目（空行のあと・行頭の字下げ）は文の間の2.5倍、章の見出し（第一章 など）の\n"
                 "前は5倍の間を空けます（0.4秒なら 1.0秒・2.0秒）。本として聴きやすくなります。\n"
                 "連続再生にも、まとめて保存する音声と字幕にも効きます。")

        # 仕上げの主要ボタン（音声ファイルへの書き出し）
        mod = "⌘" if core.IS_MAC else "Ctrl+"
        self.synth_btn = ttk.Button(self._steps, text="♪ 音声を生成",
                                    style="Primary.TButton",
                                    command=self.start_synth, state="disabled")
        self.synth_btn.pack(fill="x", pady=(GAPY * 2, 0))
        _Tooltip(self.synth_btn, self._tip_engine_gate(
            f"本文を音声ファイルに書き出します（{mod}G）。"))

        # VOICEVOX（エディタ）で続きをするための書き出し
        # 使う人が限られるので畳んでおく（③ を短くして、主役の「音声を生成」を目立たせる。
        # 開いたかどうかは覚えておく。_set_finish）
        self._finish_btn = ttk.Button(self._steps, text="VOICEVOXで仕上げる ▷",
                                      style="Tool.TButton", command=self._toggle_finish)
        self._finish_btn.pack(anchor="w", pady=(GAPY, 0))
        _Tooltip(self._finish_btn, "VOICEVOX のエディタで続きをするための保存\n"
                                   "（.txt・本文のコピー・プロジェクト .vvproj）を開きます。")
        ob = ttk.Frame(self._steps)
        self._finish_frame = ob
        self._finish_open = False
        ttk.Button(ob, text="VOICEVOX用に保存(.txt)", style="Quiet.TButton",
                   command=self.save_txt).grid(row=0, column=0, sticky="ew", pady=1)
        copy_btn = ttk.Button(ob, text="本文をコピー", style="Quiet.TButton",
                              command=self.copy_clip)
        copy_btn.grid(row=0, column=1, sticky="ew", padx=(GAPX, 0), pady=1)
        _Tooltip(copy_btn, "本文をまるごとコピーします。VOICEVOX のエディタなどに、\n"
                           f"{'⌘' if core.IS_MAC else 'Ctrl+'}V で貼り付けられます。")
        self.vvproj_btn = ttk.Button(ob, text="プロジェクト保存(.vvproj)",
                                     style="Quiet.TButton",
                                     command=self.save_vvproj, state="disabled")
        self.vvproj_btn.grid(row=1, column=0, sticky="ew", pady=1)
        _Tooltip(self.vvproj_btn, self._tip_engine_gate(
            "VOICEVOXエディタでそのまま開けるプロジェクト(.vvproj)を保存します。",
            busy_gated=False))
        self.vvproj_query_var = tk.BooleanVar(value=True)
        _cb = ttk.Checkbutton(ob, text="調整値も入れる", variable=self.vvproj_query_var)
        _cb.grid(row=1, column=1, sticky="w", padx=(GAPX, 0))
        _Tooltip(_cb,
                 "速さ・高さ・抑揚・音量の調整を、プロジェクトにも書き込みます。\n"
                 "VOICEVOXで開いたとき、このアプリと同じ声の設定から始められます。\n"
                 "行数が多いと少し時間がかかります"
                 "（調整していないときは何も待ちません）。")
        ob.columnconfigure((0, 1), weight=1, uniform="out")

    def _toggle_finish(self):
        self._set_finish(not self._finish_open)

    def _set_finish(self, show):
        """③ の「VOICEVOXで仕上げる」（.txt・コピー・.vvproj）を開く/畳む。"""
        self._finish_open = bool(show)
        if self._finish_open:
            self._finish_frame.pack(fill="x", after=self._finish_btn)
            self._finish_btn.config(text="VOICEVOXで仕上げる ▽")
        else:
            self._finish_frame.pack_forget()
            self._finish_btn.config(text="VOICEVOXで仕上げる ▷")

    # ---------------- 接続（左の列のいちばん下・いつも見える） ----------------
    def _build_connection_section(self):
        c = self._conn_box
        top = ttk.Frame(c)
        top.pack(fill="x")
        ttk.Label(top, text="接続", style="Cluster.TLabel").pack(side="left",
                                                               padx=(0, GAPX))
        # 接続できたら詳細（起動ボタン・URL欄・接続確認）を畳み、状態の横の
        # 「接続設定…」だけにする（誤操作防止と情報整理。左の列を低く保つ）。
        # 失敗・切断時は必ず戻す（_set_conn_compact）
        self._conn_edit_btn = ttk.Button(top, text="接続設定…", style="Tool.TButton",
                                         image=self._gear_img, compound="left",
                                         command=lambda: self._set_conn_compact(False))
        _Tooltip(self._conn_edit_btn, "URL変更や再接続が必要なときに開きます。")
        self.engine_var = tk.StringVar(value="VOICEVOX: つながっていません")
        self.engine_lbl = ttk.Label(top, textvariable=self._wrapping_copy(self.engine_var),
                                    width=1)
        self.engine_lbl.pack(side="left", fill="x", expand=True)
        self.engine_lbl.bind("<Configure>", self._wrap_engine_lbl, add="+")
        self._conn_detail = ttk.Frame(c)
        r1 = ttk.Frame(self._conn_detail)
        r1.pack(fill="x", pady=(GAPY, 0))
        _b_gui = ttk.Button(r1, text="VOICEVOX起動", command=self.launch_voicevox)
        _b_gui.grid(row=0, column=0, sticky="ew")
        _Tooltip(_b_gui, "VOICEVOX本体（エディタ画面つき）を起動します。\n"
                         "あとから手直ししたいときはこちら。")
        self.engine_only_btn = ttk.Button(r1, text="画面なしで起動",
                                          command=self.launch_engine_only)
        self.engine_only_btn.grid(row=0, column=1, sticky="ew", padx=(GAPX, 0))
        _Tooltip(self.engine_only_btn,
                 "VOICEVOX の画面（エディタ）を開かずに、読み上げの部分だけを起動します。\n"
                 "起動が軽くて速いぶん、VOICEVOX の画面での手直しはできません。\n"
                 "このアプリを閉じると一緒に終わります。")
        r1.columnconfigure((0, 1), weight=1, uniform="conn")
        r2 = ttk.Frame(self._conn_detail)
        r2.pack(fill="x", pady=(GAPY, 0))
        ttk.Label(r2, text="URL:").pack(side="left")
        self._check_btn = ttk.Button(r2, text="つながるか確かめる",
                                     command=self.check_engine)
        self._check_btn.pack(side="right", padx=(GAPX, 0))
        self.url_var = tk.StringVar(value=self.base_url)
        ttk.Entry(r2, textvariable=self.url_var, width=8).pack(
            side="left", fill="x", expand=True, padx=(2, 0))
        # アプリと一緒に VOICEVOX（読み上げの部分）を起動する（最初からオンで、聞かない。ここで切れる）
        self.engine_autostart_var = tk.BooleanVar(value=True)
        auto = ttk.Checkbutton(self._conn_detail, text="アプリと一緒に VOICEVOX を起動",
                               variable=self.engine_autostart_var,
                               command=lambda: self._set_engine_autostart(
                                   self.engine_autostart_var.get()))
        auto.pack(anchor="w", pady=(GAPY, 0))
        # GPU があれば使う（最初からオン。このアプリが VOICEVOX を起こすときだけ効く）
        self.engine_gpu_var = tk.BooleanVar(value=True)
        gpu_cb = ttk.Checkbutton(self._conn_detail, text="GPU があれば使う（音声を速く作れます）",
                                 variable=self.engine_gpu_var,
                                 command=lambda: self._set_engine_gpu(self.engine_gpu_var.get()))
        gpu_cb.pack(anchor="w")
        _Tooltip(gpu_cb, "パソコンに GPU（画像の処理が得意な部品）があり、VOICEVOX が GPU を使える版なら、\n"
                         "音声を作るのに GPU を使います（測ったパソコンでは約4倍の速さでした）。\n"
                         "このアプリが VOICEVOX を起動するときに効きます（変えたら、次に起動したときから）。\n"
                         "GPU でうまく動かないときは、自動で CPU に戻します。")
        _Tooltip(auto, "このアプリを開いたとき、VOICEVOX が動いていなければ、\n"
                       "VOICEVOX の読み上げの部分だけを、画面を出さずに一緒に起動します。\n"
                       "このアプリを閉じると、一緒に終わります。")
        # 新しい版のお知らせ（最初からオン。ここで切れる）
        self.update_check_var = tk.BooleanVar(value=True)
        upd = ttk.Checkbutton(self._conn_detail, text="新しい版が出たら知らせる",
                              variable=self.update_check_var,
                              command=lambda: setattr(self, "_update_check_on",
                                                      bool(self.update_check_var.get())))
        upd.pack(anchor="w")
        _Tooltip(upd, "開いたとき、1日1回だけ GitHub に、新しい版が出ていないか聞きます。\n"
                      "送るのはアプリの名前と版だけです。切ると、このためにネットへはつなぎません。")
        self._set_conn_compact(False)

    def _wrap_engine_lbl(self, event):
        """接続の状態の文を欄の幅で折り返す。左の列のスクロールバーが出ている間は、
        その幅を引いた所で折り返す（出ていないときと同じ行数に保つ）。以前は、
        スクロールバーで列が広がる→折り返しが1行減る→接続欄が低くなり手順が入りきる
        →スクロールバーが消える→また入らない…を繰り返し、窓の高さによっては画面が
        並び直し続けて固まった（「起動を待っています…」のとき）。"""
        sb = self._steps_sb
        extra = sb.winfo_reqwidth() if sb.winfo_manager() else 0
        self.engine_lbl.configure(wraplength=max(event.width - extra, _px(80)))

    # ---------------- 右の列: 本文（編集可能・伸縮の主役） ----------------
    def _build_result_section(self):
        mid = ttk.Frame(self._main, style="Card.TFrame",
                        padding=(_px(12), _px(8), _px(12), _px(8)))
        mid.pack(side="left", fill="both", expand=True, padx=PAD_X, pady=(0, PAD_Y))
        self._text_col = mid

        # 見出しの行（bar）: 左に「本文」と行数・文字数・めやす（info）、右に本文の道具
        # （tools: ふりがな・読み方辞書・一括置換・全消去）。入りきらない幅のときは
        # info を本文の下の行（foot）へ送る（_reflow_result_bar）
        bar = ttk.Frame(mid)
        bar.pack(fill="x")
        head = ttk.Label(bar, text="本文", style="Step.TLabel", font=self._step_font)
        head.pack(side="left")
        tools = ttk.Frame(bar)
        tools.pack(side="right")
        # 本文の全消去 / 復元は1ボタンで切替（押せない「復元」を常設しない）。
        # 属性名 restore_btn は結線・テストの互換のため維持。幅固定でラベル切替時に跳ねない
        self.restore_btn = ttk.Button(tools, text="本文を全消去", width=12,
                                      style="Danger.Tool.TButton", command=self.clear_text)
        self.restore_btn.pack(side="right", padx=(GAPX // 2, 0))
        _Tooltip(self.restore_btn, lambda: (
            "全消去した本文を元に戻します。" if self._cleared_text is not None
            else "本文をすべて消します（直後に「復元」で戻せます）。"))
        self._rep_btn = ttk.Button(tools, text="置き換え ▷", style="Tool.TButton",
                                   command=self._toggle_replace)
        self._rep_btn.pack(side="right", padx=(GAPX // 2, 0))
        _Tooltip(self._rep_btn, "本文の言葉をまとめて置き換える道具を開きます\n"
                                "（よく使う置き換えは登録できます）。")
        # ふりがな・読み方辞書は、狭い窓では本文の下の行へ動かす（_reflow_result_bar）。
        # どちらの行にも置けるよう、親は両方の親（mid）にする
        self.dict_btn = ttk.Button(mid, text="読み方辞書...", style="Tool.TButton",
                                   command=self.open_dict_dialog, state="disabled")
        self.dict_btn.pack(in_=tools, side="right", padx=(GAPX // 2, 0))
        _Tooltip(self.dict_btn, self._tip_engine_gate(
            "固有名詞などの読み方をVOICEVOXのユーザー辞書に登録します。\n"
            "ふりがなが違っていたら、その語を選んで右クリック →「辞書登録」が早いです。",
            busy_gated=False))
        # ふりがな: VOICEVOX が実際に読む読みを、本文の漢字の上に出す（_furi_refresh）。
        # 誤読に聴く前に気づけるよう、最初からオン
        self.furigana_var = tk.BooleanVar(value=True)
        self.furigana_chk = ttk.Checkbutton(mid, text="ふりがな",
                                            variable=self.furigana_var,
                                            command=self._toggle_furigana)
        self.furigana_chk.pack(in_=tools, side="right", padx=(0, GAPX))
        _Tooltip(self.furigana_chk, self._furi_tip)
        # 誤読候補だけ: 読める人向けに、色つき（辞書と違う読み）だけを出して本文をすっきり
        self.furigana_only_var = tk.BooleanVar(value=False)
        self.furigana_only_chk = ttk.Checkbutton(mid, text="誤読候補だけ",
                                                 variable=self.furigana_only_var,
                                                 command=lambda: self._furi_schedule(0))
        self.furigana_only_chk.pack(in_=tools, side="right", padx=(0, GAPX),
                                    after=self.dict_btn)
        _Tooltip(self.furigana_only_chk,
                 "色つきのふりがな（辞書と違う読み＝誤読かもしれない所）だけを出します。\n"
                 "読める字にまで出ていると、うるさいときに。色つきのふりがなを押すと、\n"
                 "その語の読みを読み方辞書で直せます。")
        # 次の候補: 長い本でも、色つきのふりがな（誤読候補）の所を順に見て回る
        # （seek_misreading）。「誤読候補だけ」の隣なので短い名前にする。「次の誤読候補」
        # だと、いちばんよくある幅（125%表示の既定の窓）で、ふりがなの道具が本文の下の
        # 行へ押し出された
        self.furi_next_btn = ttk.Button(mid, text="次の候補 ▷", style="Tool.TButton",
                                        command=self.seek_misreading)
        self.furi_next_btn.pack(in_=tools, side="right", padx=(0, GAPX // 2),
                                after=self.dict_btn)
        self.furi_next_btn.bind("<Shift-Button-1>", self._seek_back_click)
        _Tooltip(self.furi_next_btn,
                 "次の誤読候補（色つきのふりがな＝辞書と違う読み）へ移ります（F8）。\n"
                 "前へ戻るときは Shift+F8（Shift を押しながらクリックでも）。\n"
                 "まだ読みを調べていない行は、VOICEVOX に聞きながら先へ探します。\n"
                 "探している間にもう一度押すか Esc で、やめます。")
        # 行数・文字数・読み上げめやすの常時表示（台本編集中に常に見たい情報）。
        # どちらの行にも置けるよう、親は両方の親（mid）にする（pack の in_ の決まり）。
        # bar・foot より後に作るので、重なり順でも両方の上に出る
        foot = ttk.Frame(mid)
        info = ttk.Frame(mid)
        self.stats_var = tk.StringVar()
        self._stats_show = tk.StringVar()   # 実際に出す文（入らなければ短くする。_fit_stats）
        stats = ttk.Label(info, textvariable=self._stats_show, style="Sub.TLabel")
        stats.pack(side="left")
        _Tooltip(stats, lambda: (self.stats_var.get()
                                 if self._stats_show.get() != self.stats_var.get()
                                 else ""))
        info.pack(in_=bar, side="left", padx=(GAPX * 2, 0))
        # 行数の文字が伸び縮みすると info の幅も変わるので、並びを見直す
        self.stats_var.trace_add("write", lambda *_: self._schedule_reflow())

        # 置き換えの道具（ふだんは畳む。_set_replace が見出しの行のすぐ下に出す）
        rep = ttk.Frame(mid)
        self._rep_frame = rep
        self._rep_open = False
        ra = ttk.Frame(rep)
        ra.pack(fill="x", pady=(GAPY, 0))
        ttk.Label(ra, text="置き換え:").pack(side="left")
        self.find_var = tk.StringVar()
        self.repl_var = tk.StringVar()
        fe = ttk.Entry(ra, textvariable=self.find_var, width=10)
        fe.pack(side="left", padx=2, fill="x", expand=True)
        ttk.Label(ra, text="→").pack(side="left")
        re_ = ttk.Entry(ra, textvariable=self.repl_var, width=10)
        re_.pack(side="left", padx=2, fill="x", expand=True)
        ttk.Button(ra, text="すべて置き換える", command=self.replace_all_text).pack(
            side="left", padx=(4, 0))
        fe.bind("<Return>", lambda e: self.replace_all_text())
        re_.bind("<Return>", lambda e: self.replace_all_text())
        self._find_entry = fe
        rb = ttk.Frame(rep)
        rb.pack(fill="x", pady=(GAPY, 0))
        ttk.Label(rb, text="登録した置き換え:").pack(side="left")
        self.rule_cb = ttk.Combobox(rb, width=16, state="readonly", values=[])
        self.rule_cb.pack(side="left", padx=2, fill="x", expand=True)
        self.rule_cb.bind("<<ComboboxSelected>>", self._rule_selected)
        # 低頻度の「登録/削除」はメニューにまとめ、最頻用の「登録を全部使う」だけボタンで残す
        self.rule_menu_btn = ttk.Menubutton(rb, text="足す・消す ▼", width=10,
                                            style="Plain.TMenubutton")
        rule_menu = tk.Menu(self.rule_menu_btn, tearoff=0)
        rule_menu.add_command(label="今の置き換えを登録する", command=self.add_rule)
        rule_menu.add_command(label="選んだ登録を消す", command=self.del_rule)
        self.rule_menu_btn.config(menu=rule_menu)
        self.rule_menu_btn.pack(side="left", padx=1)
        _Tooltip(self.rule_menu_btn, "置き換えの登録と削除（次に開いたときも使えます）。")
        ttk.Button(rb, text="登録を全部使う", command=self.apply_all_rules).pack(
            side="left", padx=(4, 0))

        # 再生バー（いちばん下）。本文より先に side="bottom" で確保する
        self._build_player_bar(mid)
        body = ttk.Frame(mid)
        body.pack(fill="both", expand=True, pady=(GAPY, 0))
        self._rbar = (bar, tools, info, foot, body)
        # ふりがなが出ない理由（VOICEVOX が起動していない）を、本文のすぐ上に一言で出す。
        # チェックの横の小さな「（VOICEVOX待ち）」では気づかれなかった（_sync_furi_notice）
        # 一言のすぐ右にボタンを置く。一言は、ボタンのぶんを空けた幅で折り返す（wrap_notice）
        row = ttk.Frame(mid)
        self._furi_notice = row
        self._furi_notice_var = tk.StringVar()
        self._furi_notice_lbl = ttk.Label(
            row, textvariable=self._wrapping_copy(self._furi_notice_var),
            style="Notice.TLabel", justify="left")
        self._furi_notice_lbl.pack(side="left")
        self._furi_notice_btn = ttk.Button(row, text="画面なしで起動",
                                           command=lambda: self.launch_engine_only())
        self._furi_notice_btn.pack(side="left", padx=(GAPX, 0))
        _Tooltip(self._furi_notice_btn,
                 "VOICEVOX の画面を開かずに、読み上げの部分だけを起動します\n"
                 "（左下の「画面なしで起動」と同じ）。つながると、ふりがなが出ます。")

        def wrap_notice(event):
            btn = self._furi_notice_btn
            room = event.width - (btn.winfo_reqwidth() + GAPX if btn.winfo_manager() else 0)
            self._furi_notice_lbl.configure(wraplength=max(room, _px(120)))
        row.bind("<Configure>", wrap_notice, add="+")
        self._rbar_head = head
        self._rbar_one = True
        self._rbar_level = 0
        self._rbar_compact = False   # 狭いとき「置換 ▷」「辞書...」に縮めているか
        self._rbar_full_req = (0, 0)  # 縮める前の 辞書・置き換え の幅
        self.text_font = tkfont.Font(font=(TEXT_FONT or "TkTextFont"))
        self.text = tk.Text(body, wrap="word", undo=True, font=self.text_font,
                            padx=_px(8), pady=_px(4))
        self.text.pack(side="left", fill="both", expand=True)
        # 本文が空のときの案内（_sync_text_hint）。押すと本文欄にカーソルが入る
        self._text_hint = tk.Label(
            self.text, bd=0, justify="center",
            text="ここに、取り出した文字が出ます。\n"
                 "左の ① にファイルを入れるか、文字をそのまま貼り付けてね。")
        self._text_hint.bind("<Button-1>",
                             lambda e: (self.text.focus_set(), "break")[1])
        # 案内のすぐ下に、最初の一手のボタンを置く（広い空の面の真ん中で、左上の
        # 「ファイル追加」を探さなくて済むように）
        self._text_hint_btn = ttk.Button(self.text, text="ファイルを選ぶ…",
                                         style="Secondary.TButton", cursor="arrow",
                                         command=self.add_files)
        def by_hand(*args):   # スクロールバーを動かした（読んでいる行へ引き戻さない）
            self._note_user_scroll()
            return self.text.yview(*args)
        tsb = ttk.Scrollbar(body, command=by_hand)
        tsb.pack(side="right", fill="y")

        def on_yscroll(*args):
            tsb.set(*args)
            # 見える行が変わったら、その場で置き直す（後回しにすると、本文が動いた
            # 次の瞬間にふりがなが遅れて付いてきて、ずれて見える）
            self._furi_follow_scroll()
        self.text.config(yscrollcommand=on_yscroll)
        self.text.bind("<Configure>", lambda e: self._furi_schedule(60), add="+")
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Prior>", "<Next>",
                    "<TouchpadScroll>"):
            try:
                self.text.bind(seq, self._note_user_scroll, add="+")
            except tk.TclError:
                pass   # TouchpadScroll は Tk 9 だけ
        self.text.bind("<KeyRelease>", lambda e: self._furi_schedule(400), add="+")
        for seq in ("<<Selection>>", "<FocusIn>", "<FocusOut>"):
            self.text.bind(seq, lambda e: self._furi_schedule(30), add="+")
        # 本文が（全消去/復元“以外”の理由で）変わったら復元ポイントを無効化する一元フック
        self.text.bind("<<Modified>>", self._on_text_modified)
        # カーソルが止まったら、その行を裏で先に合成しておく（▶試聴を待たせない）
        for seq in ("<KeyRelease>", "<ButtonRelease-1>"):
            self.text.bind(seq, self._schedule_prefetch, add="+")

        # 右クリックメニュー（編集・この行を試聴・辞書登録・@タグ・＃メモ行）。
        # macのTk(aqua)は右クリック=Button-2（Ctrl+クリックも慣習）、Win/LinuxはButton-3
        self._text_menu = tk.Menu(self.text, tearoff=0)
        seqs = (("<Button-2>", "<Control-Button-1>") if core.IS_MAC
                else ("<Button-3>",))
        for seq in seqs:
            try:
                self.text.bind(seq, self._show_text_menu)
            except tk.TclError:
                pass

        # ショートカット: 検索(Ctrl/Cmd+F)・文字サイズ(Ctrl/Cmd + = / - / 0)
        self._font_size0 = int(self.text_font.cget("size"))  # リセット用の既定サイズ
        # 本文の文字の大きさ: 選んだ大きさ（_font_pref）に、窓の幅に合わせて足す分を
        # 加えて使う（_fit_text_font）。足すかどうかは右クリックの「文字の大きさ」で選ぶ
        self._font_pref = self._font_size0
        self.font_auto_var = tk.BooleanVar(value=True)
        self._fit_cache = None        # 字体ごとの、1ポイントあたりの字の幅・行の高さ
        self._fit_hint_shown = False  # 窓に合わせて変えたことは、1回だけ知らせる
        self._fit_done = False        # 起動して最初の合わせは、境目のゆとりなしで決める
        self._fit_quiet_until = 0.0   # 起動の途中では知らせない（_load_settings のあとで決める）
        self.text.bind("<Configure>", lambda e: self._schedule_fit_text(), add="+")
        for seq in ("<Control-MouseWheel>", "<Command-MouseWheel>"):
            try:
                self.text.bind(seq, self._on_ctrl_wheel)
            except tk.TclError:
                pass   # Command は macOS だけ
        key = "⌘" if core.IS_MAC else "Ctrl+"
        self._font_menu = tk.Menu(self._text_menu, tearoff=0)
        self._font_menu.add_command(label="大きくする", accelerator=f"{key}＋",
                                    command=lambda: self.change_font(+1))
        self._font_menu.add_command(label="小さくする", accelerator=f"{key}－",
                                    command=lambda: self.change_font(-1))
        self._font_menu.add_command(label="元の大きさに戻す", accelerator=f"{key}0",
                                    command=lambda: self.change_font(0))
        self._font_menu.add_separator()
        self._font_menu.add_checkbutton(label="窓の幅に合わせて大きくする",
                                        variable=self.font_auto_var,
                                        command=self._on_font_auto)
        for mod in ("Control", "Command"):
            try:
                self.bind_all(f"<{mod}-f>", lambda e: self.open_search())
                self.bind_all(f"<{mod}-equal>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-plus>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-minus>", lambda e: self.change_font(-1))
                self.bind_all(f"<{mod}-semicolon>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-0>", lambda e: self.change_font(0))
                # 主要操作: 抽出(Return)・音声生成(G)・ファイル追加(O)・txt保存(S)・試聴(P)
                self.bind_all(f"<{mod}-Return>", self._kb_extract)
                self.bind_all(f"<{mod}-g>", self._kb_synth)
                self.bind_all(f"<{mod}-o>", self._kb_add_files)
                self.bind_all(f"<{mod}-s>", self._kb_save_txt)
                self.bind_all(f"<{mod}-p>", lambda e: self._kb_invoke(self.preview_btn))
                self.bind_all(f"<{mod}-r>", lambda e: self._kb_invoke(self.screen_btn))
                self.bind_all(f"<{mod}-R>", lambda e: self._kb_screen_again())
            except tk.TclError:
                pass  # Command修飾子はmacOS以外に無い
        self.bind_all("<Escape>", self._kb_escape)
        self.bind_all("<space>", self._kb_space)
        # 誤読候補を順に見て回る（F8＝次・Shift+F8＝前。開発ツールの「次の問題へ」と同じキー）
        self.bind_all("<F8>", lambda e: self.seek_misreading())
        self.bind_all("<Shift-F8>", lambda e: self.seek_misreading(back=True))
        self.bind_all("<F11>", lambda e: (self.toggle_reading(), "break")[1])
        # Textクラスの既定バインド（Ctrl+O=行挿入・Ctrl+P=行移動・Ctrl/Cmd+Return=改行）は
        # bind_all（allタグ＝classより後）では抑止できないため、ウィジェット段で先取りして
        # "break"（各ハンドラの戻り値）で止める
        for seq, handler in (("<Control-Return>", self._kb_extract),
                             ("<Command-Return>", self._kb_extract),
                             ("<Control-o>", self._kb_add_files),
                             ("<Control-p>",
                              lambda e: self._kb_invoke(self.preview_btn))):
            try:
                self.text.bind(seq, handler)
            except tk.TclError:
                pass
        if core.IS_WIN:
            # WindowsのRedo定番。Tkの既定（環境依存のpaste系）より明示バインドを優先
            try:
                self.text.bind("<Control-y>",
                               lambda e: (self._edit_redo(), "break")[1])
            except tk.TclError:
                pass
        self._search_win = None
        self._search_hits = []
        self._search_idx = -1

    # ---------------- 右の列: 再生バー（本文のすぐ下） ----------------
    def _build_player_bar(self, parent):
        """聴くための操作を本文のすぐ下にまとめる（前の行・再生/一時停止・次の行・
        停止・最初から・速さ・位置）。以前は「4. VOICEVOX へ」のいちばん下にあって本文から遠く、
        窓によってはタスクバーの裏に隠れていた。ボタンの属性名（playall_btn・
        pause_btn・stop_btn・resume_btn）は、押せる/押せないの切り替えが各所に
        あるので変えない。"""
        bar = ttk.Frame(parent)
        bar.pack(side="bottom", fill="x", padx=GAPX, pady=GAPY)

        # 起動したとき、しおりがあれば出す「前回の続き」（_show_resume）。ふだんは畳む
        self._resume_row = ttk.Frame(bar)
        self._resume_var = tk.StringVar()
        ttk.Label(self._resume_row, textvariable=self._resume_var).pack(side="left")
        self._resume_what = None   # (行番号, その行の文)。幅に合わせて切り詰め直す（_fit_resume）
        self.resume_btn = ttk.Button(self._resume_row, text="▶ 続きを聴く",
                                     command=self.play_from_bookmark, state="disabled")
        self.resume_btn.pack(side="left", padx=GAPX)

        def _resume_tip():
            if not self._engine_ready():
                return "VOICEVOX につながると押せます。"
            return "前回の続き（しおりの行）から読み上げます。"
        _Tooltip(self.resume_btn, _resume_tip)
        self._resume_close = ttk.Button(self._resume_row, text="×", width=3,
                                        style="Mini.Quiet.TButton", command=self._hide_resume)
        self._resume_close.pack(side="left")
        self._resume_row.bind("<Configure>", lambda e: self._fit_resume(), add="+")

        ctl = ttk.Frame(bar)
        ctl.pack(fill="x")
        self._player_ctl = ctl
        # 本文を広げる（読書モード。_set_reading）。右端に先に確保して、広げても同じ所で戻せる
        self.read_btn = ttk.Button(ctl, text=self._READ_LABELS[False], width=0,
                                   style="Quiet.TButton", command=self.toggle_reading)
        self.read_btn.pack(side="right", padx=(GAPX, 0))
        _Tooltip(self.read_btn, lambda: (
            "左の列を戻します（F11。何も鳴っていないときは Esc でも）。" if self._reading else
            "左の列を畳んで、本文を窓いっぱいに広げます（F11）。\n"
            "文字も、窓の幅に合わせて大きくなります。"))
        self.prev_btn = ttk.Button(ctl, text="◁ 前の行", width=0,
                                   command=lambda: self._play_step(-1))
        self.prev_btn.pack(side="left")
        _Tooltip(self.prev_btn, "ひとつ前の行へ。再生中なら、そこから読み直します。")
        # ▶ 再生 と ⏸ 一時停止 は同じ場所に、どちらか一方だけ出す（_sync_play_buttons）
        # 幅は2つでそろえる（入れ替えたときに右の部品が動かないように）。
        # 10 だと「⏸ 一時停止」の最後の字が切れた
        self.playall_btn = ttk.Button(ctl, text="▶ 再生", width=12,
                                      command=self.play_from_cursor, state="disabled")
        # いちばん使うボタン。本文があるときは色で目立たせる（_update_step_highlight）
        self.playall_btn.pack(side="left", padx=(4, 0))
        _Tooltip(self.playall_btn, self._tip_engine_gate(
            "カーソルのある行から最後まで読み上げます。\n"
            "本文をクリックした所から聴けます（止めたあと押せば、その続きから）。"))
        self.pause_btn = ttk.Button(ctl, text="‖ 一時停止", width=12,
                                    style="Accent.TButton",
                                    command=self.toggle_pause, state="disabled")
        _Tooltip(self.pause_btn,
                 "一時停止/再開します（スペースキー）。\n"
                 + ("再開すると、止めた所から続きを読みます。" if core.IS_WIN else
                    "再開すると同じ行の頭から読み直します。"))
        self.next_btn = ttk.Button(ctl, text="次の行 ▷", width=0,
                                   command=lambda: self._play_step(+1))
        self.next_btn.pack(side="left", padx=(4, 0))
        _Tooltip(self.next_btn, "ひとつ次の行へ。再生中なら、そこから読み直します。")
        self.stop_btn = ttk.Button(ctl, text="■ 停止", width=0,
                                   command=self.stop_playall, state="disabled")
        self.stop_btn.pack(side="left", padx=(4, 0))
        _Tooltip(self.stop_btn, "再生を停止します（Esc。再生中だけ押せます）。")
        top_btn = ttk.Button(ctl, text="« 最初から", width=0,
                             command=self._play_from_top)
        top_btn.pack(side="left", padx=(4, 0))
        _Tooltip(top_btn, "本文の最初から読み上げます（再生中なら頭に戻ります）。")
        # ボタンだけでも1行に入らない画面（1024×768 の100%表示など）では、この3つを
        # 記号だけにする（_reflow_player_row）。中身は吹き出しで読める
        self._player_short = ((self.prev_btn, "◁ 前の行", "◁"),
                              (self.next_btn, "次の行 ▷", "▷"),
                              (top_btn, "« 最初から", "«"))
        self._player_compact = False
        self._player_full_req = {}   # 元の文で出していたときの幅（_reflow_player_row）
        sep = ttk.Separator(ctl, orient="vertical")
        sep.pack(side="left", fill="y", padx=GAPX * 2)
        # 速さ・間（params）と、位置の線路・何行目か（tail）はそれぞれ1つの枠にまとめ、
        # 窓が狭くて1行に入らないときは、両方を下の行へ送る（_reflow_player_row）。
        # 1行目にはボタンだけが残る。どちらの行にも置けるよう、枠の親は bar にする
        row2 = ttk.Frame(bar)   # 狭いときだけ出す2行目
        # 2行目にも入らないとき（Mac の100%表示のいちばん狭い窓など）、何行目かを置く3行目
        self._player_row3 = ttk.Frame(bar)
        self._player_layout = 1
        params = ttk.Frame(bar)
        params.pack(in_=ctl, side="left")
        # 速さは ② の話速（speed_var）と同じ値。どちらで変えても両方がそろう
        ttk.Label(params, text="速さ").pack(side="left")
        self._speed_text = tk.StringVar(value="1.0")
        self._speed_scale = ttk.Scale(params, from_=0.5, to=2.0, orient="horizontal",
                                      length=_px(88), command=self._on_speed_scale)
        self._speed_scale.set(1.0)
        self._speed_scale.pack(side="left", padx=(4, 2))
        ttk.Label(params, textvariable=self._speed_text, width=4).pack(side="left")
        _Tooltip(self._speed_scale,
                 "読み上げの速さ（② の「速さ」と同じ値です）。\n"
                 "再生中に変えると、今の行の頭から新しい速さで読み直します。")
        self.speed_var.trace_add("write", self._on_speed_var)
        # 文間の無音（秒）は ③ の gap_var と同じ値。連続再生でも行間の間合いとして使い、
        # 再生中に変えても次の行間からすぐ反映される
        ttk.Label(params, text="文の間").pack(side="left", padx=(GAPX, 2))
        self.player_gap_sb = ttk.Spinbox(
            params, from_=0.0, to=3.0, increment=0.1, width=4,
            textvariable=self.gap_var)
        self.player_gap_sb.pack(side="left")
        ttk.Label(params, text="秒").pack(side="left", padx=(2, 0))
        _Tooltip(self.player_gap_sb,
                 "文と文のあいだに入れる、音の無い時間（秒）です。\n"
                 "③ の「文の間」と同じ設定なので、保存するファイルの\n"
                 "行間も同じ長さになります。再生中にも変えられます。")
        self.gap_var.trace_add("write", self._on_gap_var)
        # いま何行目か（読み上げる行のうち）と、その割合
        tail = ttk.Frame(bar)
        tail.pack(in_=ctl, side="left", fill="x", expand=True, padx=(GAPX * 2, 0))
        self._player_tail = tail
        self._pos_var = tk.StringVar()
        self._pos_show = tk.StringVar()   # 実際に出す文（入らなければ短くする。_fit_position）
        self._pos_lbl = ttk.Label(tail, textvariable=self._pos_show)
        self._pos_lbl.pack(side="right")
        _Tooltip(self._pos_lbl, lambda: (self._pos_var.get()
                                         if self._pos_show.get() != self._pos_var.get()
                                         else ""))
        tail.bind("<Configure>", lambda e: self._fit_position(), add="+")
        self._play_prog = ttk.Progressbar(tail, mode="determinate", length=_px(60),
                                          style="Track.Horizontal.TProgressbar")
        self._play_prog.pack(side="left", fill="x", expand=True, padx=(0, GAPX * 2))
        self._play_pos = {}   # 行番号 → 読み上げる行のうち何番目か（再生のたび作る）
        self._player_rows = (ctl, row2, params, sep)
        self._player_one = True
        # 「12,345 / 12,345行」のように表示が延びたら、1行に入るかを測り直す
        self._pos_var.trace_add("write", lambda *_: self._schedule_reflow())

    def _sync_play_buttons(self):
        """連続再生中は ▶ 再生 の場所に ‖ 一時停止 を出す（同じ所を押せば止まる/動く）。"""
        playing = self._playall_pause is not None
        show, hide = ((self.pause_btn, self.playall_btn) if playing
                      else (self.playall_btn, self.pause_btn))
        if not show.winfo_manager():
            hide.pack_forget()
            show.pack(side="left", padx=(4, 0), before=self.next_btn)

    def _play_kinds(self, numbered):
        """読み上げる各行の前の切れ目（core.line_breaks）。段落は空行と行頭の字下げで分ける
        （core.paragraph_ids。音声の保存と同じ決まり）。"""
        ids = core.paragraph_ids(self.text.get("1.0", "end-1c").split("\n"))
        return core.line_breaks([t[1] for t in numbered],
                                [ids[t[0] - 1] if 0 < t[0] <= len(ids) else 0 for t in numbered])

    def _index_play_lines(self, numbered, kinds=None):
        """再生バーの「何行目か」と「あと◯分」のもとを作る（_speakable_lines の一覧から）。"""
        self._play_pos = {t[0]: i for i, t in enumerate(numbered)}
        if kinds is None:
            kinds = self._play_kinds(numbered)
        longer = bool(self.para_pause_var.get())
        gap = self._get_num(self.gap_var, 0.4)
        speed = self._get_num(self.speed_var, 1.0)
        left = [0.0] * len(numbered)
        total = 0.0
        for i in range(len(numbered) - 1, -1, -1):
            total += core.estimate_read_seconds(numbered[i][1], speed)
            left[i] = total
            total += core.break_gap(kinds[i] if longer else "", gap)   # この行の前の間
        self._play_left = left

    def _set_position(self, lineno):
        """再生バーの「何行目 / 全部で何行」「あと◯分」と進み具合を、行番号から出す。"""
        total = len(self._play_pos)
        i = self._play_pos.get(lineno)
        if i is None or not total:
            return
        left = self._play_left[i] if i < len(self._play_left) else None
        self._pos_var.set(f"{i + 1:,} / {total:,}行"
                          + (f"・あと{core.fmt_left(left)}" if left else ""))
        self._play_prog.config(maximum=total, value=i + 1)

    def _speakable_lines(self, default_id=None):
        """読み上げる行の一覧 [(行番号, 読む文, style_id)]。空行・＃メモ行・
        @タグだけの行は除く。連続再生と、前の行/次の行の移動で同じ決まりを使う。"""
        numbered = []
        for i, ln in enumerate(self.text.get("1.0", "end-1c").split("\n"), start=1):
            if not ln.strip() or core.is_memo_line(ln):
                continue   # 空行・メモ行（行頭#）は読まない
            spoken, sp = self._resolve_line(ln.strip())
            if spoken.strip():
                numbered.append((i, spoken, sp[1] if sp else default_id))
        return numbered

    def _play_step(self, delta):
        """前の行（-1）・次の行（+1）へ。再生中ならそこから読み直し、止まっている
        ときは行を移って印を付けるだけ（▶ 再生でそこから読める）。"""
        lines = [t[0] for t in self._speakable_lines()]
        if not lines:
            self.status_var.set("再生するテキストがありません。")
            return
        if self._playall_pause is not None and self._bookmark is not None:
            cur = self._bookmark   # 再生中は、いま読んでいる行が基準
        else:
            cur = int(self.text.index("insert").split(".")[0])
        if delta < 0:
            target = next((n for n in reversed(lines) if n < cur), None)
        else:
            target = next((n for n in lines if n > cur), None)
        if target is None:
            self.status_var.set("ここが最初の行です。" if delta < 0
                                else "ここが最後の行です。")
            return
        self._play_jump(target)

    def _play_from_top(self):
        """最初から読む。再生中なら、止めて頭から読み直す。"""
        lines = self._speakable_lines()
        if not lines:
            self.status_var.set("再生するテキストがありません。")
            return
        if self._playall_pause is not None:
            self._play_jump(lines[0][0])
        else:
            self.play_all()

    def _play_jump(self, lineno):
        """lineno の行へ移る。連続再生中なら、止めてからそこから読み直す。
        止めて読み直すので、少し間があく（読んだ行と先読みした行は合成キャッシュに
        あるので、たいてい1秒もかからない）。"""
        def go():
            self.text.mark_set("insert", f"{lineno}.0")
            self._follow_reading(lineno, force=True)
            self.text.tag_remove("playing", "1.0", "end")
            self.text.tag_add("playing", f"{lineno}.0", f"{lineno}.end")
            self._furi_recolor()
        go()
        if self._playall_pause is not None:
            self._after_stopping(lambda: (go(), self.play_from_cursor()))
        else:
            self._index_play_lines(self._speakable_lines())
            self._set_position(lineno)

    # 読んでいる行は、本文欄の上から3分の1あたりに置く。see だけだと、いちばん下に張り付いて
    # 次の文が見えず、折り返す長い文は終わりが窓の下に隠れたまま読まれていた（案は
    # dtrknt15 さんの PR #23）。自分でスクロールした直後は、見ている所を奪わない
    _FOLLOW_PAUSE_SEC = 6.0

    def _note_user_scroll(self, event=None):
        """ホイール・スクロールバー・PageUp/Down で本文を動かしたことを覚える。"""
        self._user_scrolled_at = time.monotonic()

    def _follow_reading(self, lineno, force=False):
        """読んでいる行 lineno を、本文欄の上から3分の1あたりに置く。文全体が窓に入るよう、
        長い文ではもっと上に置く。文書の頭と終わりでは、Tk が端で止める。"""
        if (not force
                and time.monotonic() - self._user_scrolled_at < self._FOLLOW_PAUSE_SEC):
            return
        t = self.text
        idx = f"{lineno}.0"
        try:
            t.see(idx)
            info = t.dlineinfo(idx)
            if info is None:
                return
            pitch = max(info[3], 1)
            room = t.winfo_height() // pitch
            # 折り返しの行数は文字数から見積もる（Tk の count -displaylines は長い行でとても
            # 重く、1,000字で0.4秒、3,600字で13秒も画面が止まった）。少し外れてもいいよう1行空ける
            chars = int(t.index(f"{lineno}.end").split(".")[1])
            adv, _line = self._fit_metrics()
            per_char = adv * abs(int(self.text_font.cget("size")))
            rows = 1 + int(chars * per_char / max(t.winfo_width(), 1))
            above = min(room // 3, max(0, room - rows - 1))
            delta = round((info[1] - above * pitch) / pitch)
            if delta:
                t.yview_scroll(delta, "units")
        except (tk.TclError, ValueError):
            pass

    def _follow_playing(self):
        """文字の大きさや窓が変わったら、読んでいる行を置き直す（読んでいる間だけ）。"""
        playing = self.text.tag_ranges("playing")
        if playing and self._previewing:
            self._follow_reading(int(str(playing[0]).split(".")[0]))
    def _on_speed_scale(self, value):
        """再生バーの速さを動かしたとき。0.1刻みにそろえて ② の話速へ入れる。"""
        v = round(float(value) * 10) / 10
        try:
            same = abs(self.speed_var.get() - v) < 1e-9
        except tk.TclError:
            same = False   # 話速の欄が空・入力途中
        if not same:
            self.speed_var.set(v)

    def _on_speed_var(self, *_):
        """話速が変わったら、再生バーのつまみと数字を合わせる。連続再生中なら、
        動かし終わるのを少し待ってから、今の行の頭から新しい速さで読み直す。"""
        try:
            v = float(self.speed_var.get())
        except (tk.TclError, ValueError):
            return   # 入力途中の空欄など
        self._speed_text.set(f"{v:.1f}")
        if abs(float(self._speed_scale.get()) - v) > 1e-9:
            self._speed_scale.set(v)   # つまみの command が呼ばれても同じ値なので何もしない
        if self._playall_pause is None:
            return
        old = self._ticks.pop("speed_restart", None)
        if old:
            try:
                self.after_cancel(old)
            except tk.TclError:
                pass
        self._tick("speed_restart", 700, self._restart_at_speed)

    def _restart_at_speed(self):
        self._ticks.pop("speed_restart", None)
        if self._playall_pause is not None and self._bookmark is not None:
            self._play_jump(self._bookmark)

    def _on_gap_var(self, *_):
        """文間の無音（秒）が変わったら、連続再生ワーカーが参照する秒数を更新する。"""
        try:
            v = float(self.gap_var.get())
        except (tk.TclError, ValueError):
            return   # 入力途中の空欄など
        self._play_gap = max(0.0, min(10.0, v))

    def _gap_before(self, i):
        """連続再生で、再生する i 番目の行の前に入れる無音（秒）。段落・章の切れ目は
        長め（core.break_gap。保存する音声と同じ）。再生中に「間」を変えてもすぐ効く
        よう、そのときの値（_play_gap）から計算する。"""
        kinds = self._play_breaks
        kind = kinds[i] if self._play_longer and 0 <= i < len(kinds) else ""
        return core.break_gap(kind, self._play_gap)

    def _on_read_fix(self, *_args):
        """「数字・日付・記号の読み方を直す」を切り替えたとき。合成のスレッドは
        core の値を読むだけなので、ここ（UI のスレッド）で伝える。ふりがなは、
        覚えている読みの名札に直しのオン・オフも入っているので、出し直すだけでよい。"""
        try:
            core.set_read_fix(self.read_fix_var.get())
            self._furi_schedule(0)
        except (tk.TclError, AttributeError):
            pass   # 作っている途中（ふりがなの部品がまだ無い）

    def _on_para_pause(self):
        """「段落・章は長め」の切り替えを、再生中の連続再生にもすぐ効かせる
        （再生の裏の処理は Tk の変数を読めないので、ふつうの値に写しておく）。"""
        self._play_longer = bool(self.para_pause_var.get())

    def _wait_playall_gap(self, stop, pause, seconds=None):
        """行ごとの連続再生（_playall_lines）の行間で、指定された無音秒数だけ待つ
        （停止・一時停止には即応）。再生側で語尾の後ろに足している無音
        （core.PLAY_TAIL_SEC）は差し引き、行間の無音が保存ファイルの「文間の無音」と
        同じ長さに聞こえるようにする。1本の音の流れで鳴らすときは、間を無音として
        流れに入れるので、ここは通らない。seconds が無ければ「間」の秒数。"""
        base = getattr(self, "_play_gap", 0.4) if seconds is None else seconds
        gap = max(0.0, float(base) - core.PLAY_TAIL_SEC)
        if gap <= 0.0:
            return
        deadline = time.monotonic() + gap
        while time.monotonic() < deadline:
            if stop.is_set():
                return
            if pause.is_set():
                while pause.is_set() and not stop.is_set():
                    time.sleep(0.05)
                return
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _show_resume(self, line):
        """「前回の続き：◯行目「…」から ▶ 続きを聴く」を再生バーの上に出す。"""
        self._resume_what = (line, self.text.get(f"{line}.0", f"{line}.end").strip())
        self._fit_resume()
        self._resume_row.pack(fill="x", pady=(0, GAPY), before=self._player_ctl)

    def _fit_resume(self):
        """「前回の続き」の文を、行の幅に入る長さにする（その行の文は20字まで。入らなければ
        もっと短く）。長いままだと、いちばん狭い窓では「▶ 続きを聴く」が切れ、× は行の外へ
        押し出されて見えなかった。窓の幅を変えたら、もう一度合わせる。"""
        if not self._resume_what:
            return
        line, full = self._resume_what
        try:
            width = self._resume_row.winfo_width()
            if width <= 1:   # まだ出していない。すぐ下のボタンの行と同じ幅になる
                width = self._player_ctl.winfo_width()
            room = (width - self.resume_btn.winfo_reqwidth()
                    - self._resume_close.winfo_reqwidth() - 2 * GAPX - _px(4))
            n = min(20, len(full))
            while True:
                snippet = full if n >= len(full) else full[:n] + "…"
                text = f"前回の続き：{line}行目「{snippet}」から"
                # 幅がまだ分からないとき（窓を出す前）は、20字のまま
                if n <= 0 or width <= 1 or self._text_width(text) <= room:
                    break
                n -= 1
            if n <= 0 and full:
                text = f"前回の続き：{line}行目から"
        except tk.TclError:
            return
        if self._resume_var.get() != text:
            self._resume_var.set(text)

    def _hide_resume(self):
        self._resume_row.pack_forget()

    # ---------------- 主要操作のキーボードショートカット ----------------
    def _kb_invoke(self, btn):
        """ショートカットからボタンを押す（無効中は何もしない）。"""
        try:
            if btn.instate(["!disabled"]):
                btn.invoke()
        except tk.TclError:
            pass
        return "break"

    def _kb_screen_again(self, event=None):
        """Ctrl/Cmd+Shift+R：同じ範囲を読む（まだ囲んでいなければ囲むところから）。"""
        if self.screen_again_btn is not None:
            return self._kb_invoke(self.screen_again_btn)
        return self._kb_invoke(self.screen_btn)

    def _kb_extract(self, event=None):
        """Ctrl/Cmd+Return で抽出実行。一括置換・検索欄の<Return>バインドと
        二重発火しないよう、Entry系にフォーカスがあるときは何もしない。
        抽出中はボタンが「× キャンセル」に変わっているため何もしない
        （ショートカット再押下での誤キャンセル防止。キャンセルはEscで）。"""
        if self._extract_cancel is not None:
            return "break"
        w = getattr(event, "widget", None)
        try:
            if w is not None and w.winfo_class() in ("Entry", "TEntry",
                                                     "TCombobox", "TSpinbox"):
                return None
        except (tk.TclError, AttributeError):
            pass
        return self._kb_invoke(self.extract_btn)

    def _kb_synth(self, event=None):
        """Ctrl/Cmd+G で音声生成。生成中はボタンが「× キャンセル」に変わっているため
        何もしない（開始直後の不安な再押下で数分の合成が無警告で消えるのを防ぐ。
        キャンセルはEscかボタンで明示的に）。"""
        if self._synth_cancel is not None:
            return "break"
        return self._kb_invoke(self.synth_btn)

    def _kb_add_files(self, event=None):
        if not self.busy:
            self.add_files()
        return "break"

    def _kb_save_txt(self, event=None):
        self.save_txt()   # 空本文は save_txt 側がガードする
        return "break"

    def _kb_escape(self, event=None):
        """Esc: 音声生成・抽出の実行中はキャンセル、それ以外は再生停止。"""
        if self._seek is not None:   # 誤読候補を探している途中なら、それをやめる
            self._seek_stop("誤読候補を探すのをやめました。")
            return "break"
        if self._synth_cancel is not None and self.busy:
            self.cancel_synth()
            return "break"
        if self._extract_cancel is not None and self.busy:
            self.cancel_extract()
            return "break"
        if self._reading and not self._previewing:
            self._set_reading(False)   # 何も鳴っていなければ、広げた本文を戻す
            return "break"
        return self._kb_invoke(self.stop_btn)

    def _kb_space(self, event=None):
        """スペース: 連続再生中だけ一時停止/再開。本文・入力欄の編集は妨げない。
        ボタン類にフォーカスがあるときはTkのクラスバインド（スペース=そのボタンを押す）が
        先に発火するため、ここでは何もしない（二重発火で一時停止が相殺されるのを防ぐ。
        一時停止ボタン自身にフォーカスがある場合もクラスバインド側のcommandだけで正しく動く）。"""
        if self._playall_pause is None:
            return None
        w = getattr(event, "widget", None)
        try:
            if w is not None and w.winfo_class() in (
                    "Text", "Entry", "TEntry", "TCombobox", "TSpinbox",
                    "TButton", "Button", "TCheckbutton", "Checkbutton",
                    "TRadiobutton", "Radiobutton", "TMenubutton", "Menubutton"):
                return None
        except (tk.TclError, AttributeError):
            pass
        self.toggle_pause()
        return "break"

    # ---------------- 本文の右クリックメニュー ----------------
    def _show_text_menu(self, event):
        """本文の右クリックメニューを状態に応じて組み立てて表示する。
        編集の基本操作に加え、「この行を試聴」「選択語を辞書登録」「@話者タグ」
        「＃メモ行」といった“この画面でよくやる次の一手”への近道を出す。"""
        # クリック位置へ必ずカーソルを移す（選択タグは維持されるのでCut/Copyは
        # 従来どおり効く。以前は選択範囲内の右クリックでカーソルを動かさなかった
        # ため、「この行を試聴」「@タグ挿入」がクリック行と別の行に効いていた）
        try:
            self.text.mark_set("insert", self.text.index(f"@{event.x},{event.y}"))
        except tk.TclError:
            pass
        has_sel = bool(self.text.tag_ranges("sel"))
        sel = self.text.get("sel.first", "sel.last").strip() if has_sel else ""
        line = self.text.get("insert linestart", "insert lineend")
        can_speak = ("normal" if (self._engine_ready() and not self.busy
                                  and not self._previewing) else "disabled")
        m = self._text_menu
        m.delete(0, "end")
        m.add_command(label="元に戻す", command=self._edit_undo)
        m.add_command(label="やり直す", command=self._edit_redo)
        m.add_separator()
        m.add_command(label="切り取り", state="normal" if has_sel else "disabled",
                      command=lambda: self.text.event_generate("<<Cut>>"))
        m.add_command(label="コピー", state="normal" if has_sel else "disabled",
                      command=lambda: self.text.event_generate("<<Copy>>"))
        m.add_command(label="貼り付け",
                      command=lambda: self.text.event_generate("<<Paste>>"))
        m.add_command(label="すべて選択", command=self._select_all_text)
        m.add_separator()
        m.add_command(label="▶ この行を試聴", state=can_speak,
                      command=self.preview_selected)
        m.add_command(label="▶▶ ここから連続再生", state=can_speak,
                      command=self.play_from_cursor)
        m.add_command(
            label=(f"「{sel[:10]}」を読み方辞書に登録..." if sel
                   else "選択語を読み方辞書に登録..."),
            state="normal" if (sel and self._engine_ready()) else "disabled",
            command=lambda w=sel: self._register_word_to_dict(w))
        m.add_command(label="次の誤読候補へ（F8）",
                      state="normal" if self.furigana_var.get() else "disabled",
                      command=self.seek_misreading)
        m.add_separator()
        # @話者タグ: キャラ名だけのサブメニュー（スタイル名まで並べると多すぎるため。
        # 「@ずんだもん:」は resolve_speaker の前方一致で最初のスタイルに解決される）
        if getattr(self, "_tag_menu", None) is not None:
            self._tag_menu.destroy()   # 前回のサブメニューを捨てる（Widgetの取り残し防止）
        tag_menu = tk.Menu(m, tearoff=0)
        self._tag_menu = tag_menu
        names = []
        for lb, _sid, _u in self.speakers:
            n = lb.split("（")[0].strip()
            if n and n not in names:
                names.append(n)
        for n in names[:30]:
            tag_menu.add_command(
                label=n, command=lambda n=n: self._insert_speaker_tag(n))
        if names:
            m.add_cascade(label="この行を別の声で読む（@名前を入れる）", menu=tag_menu)
        else:
            m.add_command(label="この行を別の声で読む（@名前を入れる）",
                          state="disabled")
        if core.parse_speaker_tag(line)[0] is not None:
            m.add_command(label="この行の声の指定（@名前）を外す",
                          command=self._remove_speaker_tag)
        memo_on = line.strip().startswith(("#", "＃"))
        m.add_command(label=("＃メモ解除（読み上げ対象に戻す）" if memo_on
                             else "＃メモ行にする（読み上げから除外）"),
                      command=self._toggle_memo_lines)
        m.add_separator()
        m.add_cascade(label="文字の大きさ", menu=self._font_menu)
        m.add_command(label="左の列を戻す" if self._reading else "本文を広げる",
                      accelerator="F11", command=self.toggle_reading)
        if not self._furi_dict_installed():
            m.add_separator()
            m.add_command(label="ふりがなの辞書を入れる（ひらがなで出す）...",
                          state="disabled" if self._furi_installing else "normal",
                          command=lambda: self._furi_install_dict(ask=True))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()
        return "break"

    def _select_all_text(self):
        self.text.tag_add("sel", "1.0", "end-1c")
        self.text.mark_set("insert", "1.0")

    def _edit_undo(self):
        """本文のUndo（右クリックメニュー用。履歴が無ければ状態欄で知らせる）。"""
        try:
            self.text.edit_undo()
        except tk.TclError:
            self.status_var.set("これ以上戻せる操作はありません。")

    def _edit_redo(self):
        """本文のRedo（右クリックメニュー・WindowsのCtrl+Y用）。"""
        try:
            self.text.edit_redo()
        except tk.TclError:
            self.status_var.set("やり直せる操作はありません。")

    def _register_word_to_dict(self, word):
        """選択したテキストを単語欄に入れた状態で読み方辞書を開く（誤読修正の近道）。"""
        self.open_dict_dialog()
        self._dict_surface.set(word[:25])
        self._dict_pron.set("")

    def _insert_speaker_tag(self, name):
        """カーソル行の行頭に「@話者名: 」タグを挿入する（既存タグは置き換え）。"""
        ln = int(self.text.index("insert").split(".")[0])
        line = self.text.get(f"{ln}.0", f"{ln}.end")
        existing, rest = core.parse_speaker_tag(line)
        self.text.edit_separator()
        if existing is not None:
            self.text.delete(f"{ln}.0", f"{ln}.end")
            self.text.insert(f"{ln}.0", f"@{name}: {rest}")
        else:
            self.text.insert(f"{ln}.0", f"@{name}: ")
        self.text.edit_separator()
        self.status_var.set(f"この行は「{name}」が読むよ（行頭の@タグで指定）")

    def _remove_speaker_tag(self):
        """カーソル行の行頭の「@話者名:」タグを外し、本文だけを残す。"""
        ln = int(self.text.index("insert").split(".")[0])
        line = self.text.get(f"{ln}.0", f"{ln}.end")
        name, rest = core.parse_speaker_tag(line)
        if name is None:
            return
        self.text.edit_separator()
        self.text.delete(f"{ln}.0", f"{ln}.end")
        self.text.insert(f"{ln}.0", rest)
        self.text.edit_separator()

    def _toggle_memo_lines(self):
        """カーソル行（選択があれば選択範囲の全行）の＃メモ行を切り替える。
        混在時は「全行をメモにする」に倒す（台本の一括コメントアウトと同じ感覚）。"""
        try:
            first = int(self.text.index("sel.first").split(".")[0])
            last_idx = self.text.index("sel.last")
            last = int(last_idx.split(".")[0])
            # 終端が行頭（列0）＝その行は1文字も選択されていないので対象外
            # （Shift+クリックの行頭選択で次の行まで巻き込まないように）
            if last > first and last_idx.endswith(".0"):
                last -= 1
        except tk.TclError:
            first = last = int(self.text.index("insert").split(".")[0])
        lines = [(i, self.text.get(f"{i}.0", f"{i}.end"))
                 for i in range(first, last + 1)]
        non_empty = [l for _i, l in lines if l.strip()]
        if not non_empty:
            return
        make_memo = not all(l.strip().startswith(("#", "＃")) for l in non_empty)
        self.text.edit_separator()
        for i, l in lines:
            s = l.lstrip()
            if not s:
                continue
            if make_memo:
                if not s.startswith(("#", "＃")):
                    self.text.insert(f"{i}.0", "# ")
            elif s.startswith(("#", "＃")):
                off = len(l) - len(s)
                n = 2 if s[1:2] == " " else 1   # 「# 」は空白ごと外す
                self.text.delete(f"{i}.{off}", f"{i}.{off + n}")
        self.text.edit_separator()

    # ---------------- キャラ立ち絵パネル（任意・ローカル資産） ----------------
    def _set_window_icon(self):
        """ウィンドウ/タスクバーのアイコンを設定（無ければ何もしない）。"""
        try:
            self._app_icon = tk.PhotoImage(master=self, file=APP_ICON_PATH)
            self.iconphoto(True, self._app_icon)
        except Exception:
            pass

    def _load_portraits(self):
        """assets/立ち絵/ の透過PNGを走査し、どの話者の立ち絵も読み込む。
        ファイル名（拡張子とフレーム差分を除いた部分）を _portrait_key で正規化し、
        話者名（キャラ名）と同じキーに寄せる。これで `春日部つむぎ.png` のように
        キャラ名で置くだけで全43キャラのどれでも立ち絵が出る（旧 zundamon/metan も互換）。
        追加フレーム（_closed/_open/_blink）があれば まばたき・口パクに使う。
        無ければ空dict（パネルを出さない）。"""
        try:
            files = sorted(os.listdir(PORTRAIT_DIR))
        except OSError:
            return {}
        # 1) ファイルを キャラキー → {フレーム種別: パス} にまとめる
        groups = {}
        for fn in files:
            if not fn.lower().endswith(".png"):
                continue
            stem = fn[:-4]
            frame = "base"
            for fr, suf in _PORTRAIT_FRAMES:
                if stem.endswith(suf):
                    frame, stem = fr, stem[:-len(suf)]
                    break
            key = _portrait_key(stem)
            if key:
                groups.setdefault(key, {})[frame] = os.path.join(PORTRAIT_DIR, fn)
        # 2) 各キャラの画像を読み込む
        out = {}
        for key, framefiles in groups.items():
            frames = {}
            for frame, path in framefiles.items():
                img = self._load_scaled_image(path, max_w=230, max_h=640)
                if img is not None:
                    frames[frame] = img
            # 口閉じがあれば基準ポーズに（open/blink と同ポーズなので差し替えが滑らか）
            if "closed" in frames:
                frames["base"] = frames["closed"]
            if "base" in frames:
                out[key] = frames
        return out

    def _load_scaled_image(self, path, max_w, max_h):
        """PNGを縦横比維持でパネルに収まるよう縮小して返す。失敗時は None。"""
        if not os.path.exists(path):
            return None
        try:
            from PIL import Image, ImageTk
            im = Image.open(path).convert("RGBA")
            im.thumbnail((max_w, max_h), Image.LANCZOS)
            return ImageTk.PhotoImage(im, master=self)
        except Exception:
            return None

    def _build_portrait_panel(self, parent):
        # 上に状態の吹き出し、下に立ち絵（下端そろえ＝地面に立つ見た目）、最下段にクレジット。
        # 透過部と吹き出しの配色は _paint がテーマに追従させる。
        self._bubble = tk.Label(parent, textvariable=self.status_var,
                                wraplength=212, justify="left",
                                bd=0, padx=10, pady=8, anchor="w")
        self._bubble.pack(side="top", fill="x", pady=(0, 6))
        self._portrait_label = tk.Label(parent, bd=0, anchor="s")
        self._portrait_label.pack(side="top", fill="both", expand=True)
        # 立ち絵は全43キャラ対応（キャラごとに権利者が異なるため個別名は出さず、
        # 各キャラのガイドラインに従う旨を示す。音声のクレジットは生成時に別途案内）
        ttk.Label(parent, text="VOICEVOX ／ 立ち絵：各キャラのガイドライン準拠",
                  style="Credit.TLabel").pack(side="bottom", pady=(4, 2))
        self._portrait_key = None
        self._mouth_after = None
        self._mouth_open = False
        self._blink_after = None
        self._update_portrait()
        self._blink_after = self.after(3800, self._blink_tick)

    def _feedback_url(self):
        """「感想を送る」で開く URL。フォームがあれば版・OSを入れた状態で開く。"""
        if not FEEDBACK_FORM_URL:
            return FEEDBACK_URL
        if not FEEDBACK_FORM_ENV_ENTRY:
            return FEEDBACK_FORM_URL
        import platform
        from urllib.parse import urlencode
        if core.IS_MAC:
            os_name = f"macOS {platform.mac_ver()[0]}".strip()
        else:
            os_name = f"{platform.system()} {platform.release()}".strip()
        env = (f"v{core.APP_VERSION} / {os_name} / "
               f"Python {platform.python_version()}")
        return (FEEDBACK_FORM_URL + "?usp=pp_url&"
                + urlencode({FEEDBACK_FORM_ENV_ENTRY: env}))

    # 上の帯のアプリ名の下に出すひとこと（作者の言葉）
    MOTTO = "非公式だけど無料、勉強頑張ってね"
    # 上の帯のアプリ名に乗せると出す説明。名前に VOICEVOX が入っているので、公式の
    # ソフトではないことと、VOICEVOX 本体は別に入れることを書いておく
    UNOFFICIAL_NOTE = ("TextToVoicevox：文字を取り出して、VOICEVOX の声で読み上げるアプリ。\n"
                       "VOICEVOX 公式のソフトではありません（個人が作った非公式ツールです）。\n"
                       "VOICEVOX の開発元や、各キャラクターの権利者とは関係ありません。\n"
                       "VOICEVOX 本体は別に入れて使います（このアプリには入っていません）。")

    # 使い方の案内（アプリのフォルダにある How to use の4つ）
    _GUIDES = {"ja": "How to use (日本語).html", "en": "How to use (English).html",
               "zh-TW": "How to use (繁體中文).html", "zh-CN": "How to use (简体中文).html"}
    # 説明書（画面の画像入りの、くわしい版。日本語と英語）。その言語の版があれば、案内よりこちらを開く
    _MANUALS = {"ja": "説明書.html", "en": "Manual (English).html"}
    _GUIDE_FALLBACK = "https://github.com/rakuyou211-blip/TextToVoicevox#readme"

    @staticmethod
    def _guide_code(tag):
        """言語の名前（ja_JP・zh-Hant-TW・en-US など）→ 案内の版（ja/en/zh-TW/zh-CN）。"""
        t = (tag or "").replace("_", "-").lower()
        if not t or t.startswith("ja") or t in ("c", "posix") or t.startswith("c."):
            return "ja"   # 分からないときは日本語（このアプリの画面は日本語）
        if t.startswith("zh"):
            return ("zh-TW" if any(k in t for k in ("tw", "hk", "mo", "hant"))
                    else "zh-CN")
        return "en"

    def _guide_lang(self):
        """使い方の案内を開く版（パソコンの表示言語から決める）。"""
        tag = ""
        try:
            if core.IS_WIN:
                import ctypes
                lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
                tag = {0x0411: "ja", 0x0404: "zh-TW", 0x0C04: "zh-HK", 0x1404: "zh-MO",
                       0x0804: "zh-CN", 0x1004: "zh-SG"}.get(lid) or (
                    "ja" if lid & 0x3FF == 0x11 else "zh" if lid & 0x3FF == 0x04 else "en")
            elif core.IS_MAC:
                import subprocess
                out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                                     capture_output=True, text=True, timeout=2).stdout
                m = re.search(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*", out or "")
                tag = m.group(0) if m else ""
            if not tag:
                import locale
                tag = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                       or os.environ.get("LANG") or locale.getlocale()[0] or "")
        except Exception:
            tag = ""
        return self._guide_code(tag)

    def _open_guide(self):
        """説明書か使い方の案内（How to use）をブラウザで開く。パソコンの言語の説明書が
        あればそれ、無ければその言語の案内、それも無ければ日本語の説明書・案内、
        どれも無ければ GitHub の説明（README）を開く。"""
        import webbrowser
        from pathlib import Path
        target = self._GUIDE_FALLBACK
        is_manual = False
        # 説明書と案内は 起動.pyw の隣（zip の並びでは app の1つ上）にある。前の版の並びも見る
        names = []
        for code in (self._guide_lang(), "ja"):
            names += [(self._MANUALS[code], True)] if code in self._MANUALS else []
            names.append((self._GUIDES[code], False))
        places = [(os.path.join(folder, name), manual)
                  for name, manual in names for folder in (_top_dir(), APP_DIR)]
        for p, manual in places:
            if os.path.exists(p):
                target, is_manual = Path(p).as_uri(), manual
                break
        try:
            webbrowser.open(target)
            self.status_var.set("説明書をブラウザで開いたよ（左の目次から読みたい所へ飛べます）"
                                if is_manual else
                                "使い方の案内をブラウザで開いたよ"
                                "（ページの上で言語を切り替えられます）")
        except Exception:
            self.status_var.set("ブラウザを開けませんでした：" + target)

    # ---------------- 新しい版のお知らせ ----------------
    def _check_for_update(self, in_tests=None):
        """新しい版が出ていないか、1日1回だけ GitHub に聞く（裏で。送るのはアプリの名前と版
        だけ）。前に見つけた版がまだ新しければ、聞かずにすぐ知らせる。GitHub から取って
        きた中身（git）では知らせない。テスト中は聞かない（in_tests=False で試せる）。"""
        if in_tests is None:
            in_tests = "pytest" in sys.modules
        if self._destroying or not self._update_check_on or in_tests:
            return
        if core.install_kind(APP_DIR) == "dev":
            return
        if core.is_newer(self._update_latest):
            self._show_update()
        if not core.update_due(self._update_checked_at):
            return

        def worker():
            tag, url = core.fetch_latest_release()
            self.q.put(("update", tag, url))
        threading.Thread(target=worker, daemon=True, name="update-check").start()

    def _show_update(self):
        """上の帯の「使い方」の左に「新しい版があります」を出す。"""
        try:
            if not self._update_lbl.winfo_manager():
                self._update_lbl.pack(side="right", padx=(_px(14), 0), after=self._guide_lbl)
        except tk.TclError:
            pass

    def _open_update(self):
        """新しい版の入れ替え方を出す。1行導入で入れた人（Windows）は「更新する」を押す
        だけ。zip の人は、新しい zip を落として上書きで展開する。"""
        tag, url = self._update_latest, self._update_url or core.RELEASES_PAGE
        kind = core.install_kind(APP_DIR)
        win = tk.Toplevel(self)
        win.title("新しい版")
        win.transient(self)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=14, pady=12)
        W = _px(460)
        ttk.Label(frm, text=f"新しい版 {tag} が出ています（いまは v{core.APP_VERSION}）。",
                  wraplength=W, justify="left").pack(anchor="w")
        if kind == "oneline":
            how = ("「更新する」を押すと、このアプリを閉じて新しい版に入れ替え、終わるとまた開きます。"
                   "設定・辞書・本文の自動保存は、そのまま残ります（入れ替えの進み具合は黒い窓に出ます。"
                   "1〜2分ほど）。")
        elif kind == "mac":
            how = ("ターミナルに、入れたときと同じ1行をもう一度貼り付けると入れ替わります"
                   "（設定・辞書・本文の自動保存はそのまま）。zipで入れた人は、新しいzipを落として、"
                   "今のフォルダに上書きで展開してください。")
        else:
            # フォルダの道のりは長いので別の行に（文の途中に入れると、折り返しが崩れた）
            how = ("新しいzipを落として、今のフォルダに上書きで展開してください。"
                   "設定・辞書・本文の自動保存は、そのまま残ります。\n"
                   f"今のフォルダ: {_top_dir()}")
        ttk.Label(frm, text=how, wraplength=W, justify="left").pack(anchor="w", pady=(6, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="あとで", command=win.destroy).pack(side="right")

        def open_page():
            import webbrowser
            try:
                webbrowser.open(url)
            except Exception:
                pass
            win.destroy()
        if kind == "oneline":
            ttk.Button(btns, text="変更点を見る", command=open_page).pack(side="right", padx=6)
            ttk.Button(btns, text="更新する", style="Primary.TButton",
                       command=lambda: (win.destroy(), self._run_update())).pack(side="right")
        else:
            if kind == "mac":
                def copy_line():
                    self.clipboard_clear()
                    self.clipboard_append(core.INSTALL_ONE_LINER_MAC)
                    self.status_var.set("更新の1行をコピーしました。ターミナルに貼り付けて return を押してね。")
                    win.destroy()
                ttk.Button(btns, text="1行をコピー", command=copy_line).pack(side="right", padx=6)
            ttk.Button(btns, text="ダウンロードのページを開く", style="Primary.TButton",
                       command=open_page).pack(side="right", padx=(0, 6))
        self._place_dialog(win)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])

    def _run_update(self):
        """1行導入（install.ps1）をもう一度動かして、新しい版に入れ替える（Windows）。
        入れ替えはアプリが閉じている間にしかできないので、黒い窓を開いてから、このアプリを閉じる。
        install.ps1 はアプリが閉じるのを待って入れ替え、終わるとアプリを開き直す。"""
        if self.busy or self._previewing:
            self.status_var.set("処理・再生が終わってから、もう一度「更新する」を押してね。")
            return
        import subprocess
        env = dict(os.environ, T2V_DIR=APP_DIR, T2V_FROM_APP="1")
        try:
            subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-Command", core.INSTALL_ONE_LINER], env=env,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        except OSError as e:
            messagebox.showerror("更新", f"入れ替えを始められませんでした: {e}", parent=self)
            return
        self._on_close()

    def _open_feedback(self):
        import webbrowser
        url = self._feedback_url()
        try:
            webbrowser.open(url)
        except Exception:
            self.status_var.set("ブラウザを開けませんでした：" + url)

    def _portrait_key_for(self, label):
        """話者ラベル→立ち絵キー。そのキャラの立ち絵が置かれていればキー、無ければ None。
        全43キャラ対応：キャラ名を正規化して該当PNGがあれば表示する。"""
        if not label:
            return None
        key = _portrait_key(self._char_name(label))
        return key if key in getattr(self, "_portraits", {}) else None

    def _speaker_label_for_id(self, speaker_id):
        for s in self.speakers:
            if s[1] == speaker_id:
                return s[0]
        return ""

    # ---------------- 話者の2段選択（キャラ → スタイル） ----------------
    def _char_name(self, label):
        """フルラベル「ずんだもん（あまあま）」→ キャラ名「ずんだもん」。"""
        return str(label).split("（")[0].strip()

    def _style_name(self, label):
        """フルラベル → スタイル名「あまあま」（（）が無ければそのまま）。"""
        s = str(label)
        return s.split("（", 1)[1].rstrip("）") if "（" in s else s

    def _build_char_map(self):
        """self.speakers からキャラ名→スタイル一覧を作り、キャラコンボへ反映する。"""
        self._char_map = {}
        for sp in self.speakers:
            self._char_map.setdefault(self._char_name(sp[0]), []).append(sp)
        self.char_cb.config(values=list(self._char_map), state="readonly")

    def _char_selected(self, event=None):
        """キャラ選択→そのキャラのスタイル一覧に差し替え、先頭スタイルを選ぶ。"""
        styles = getattr(self, "_char_map", {}).get(self.char_cb.get(), [])
        self.speaker_cb.config(values=[self._style_name(s[0]) for s in styles],
                               state="readonly")
        if styles:
            self.speaker_cb.current(0)
        self._update_portrait()

    def _current_speaker(self):
        """選択中の (フルラベル, style_id, speaker_uuid)。未選択なら None。"""
        if not getattr(self, "char_cb", None):
            return None
        styles = getattr(self, "_char_map", {}).get(self.char_cb.get(), [])
        i = self.speaker_cb.current()
        if 0 <= i < len(styles):
            return styles[i]
        return None

    def _current_speaker_label(self):
        sp = self._current_speaker()
        return sp[0] if sp else ""

    def _select_speaker_label(self, label):
        """フルラベル（設定・プリセットの保存形式）で話者を選ぶ。無ければ False。"""
        for sp in self.speakers:
            if sp[0] == label:
                char = self._char_name(sp[0])
                styles = getattr(self, "_char_map", {}).get(char)
                if not styles:
                    return False
                self.char_cb.set(char)
                self._char_selected()
                self.speaker_cb.current(styles.index(sp))
                self._update_portrait()
                return True
        return False

    # ---------------- ② の、いま選んでいる声のキャラの顔 ----------------
    def _update_face(self):
        """② の顔を、いま選んでいる声のキャラ（スタイル）の公式イラストにする。
        VOICEVOX につながっていない・まだ取れていないときは、人の形を薄く出す。
        イラストはつないでいるエンジンから裏で取り、スタイルごとに覚えておく
        （アプリには入れない。立ち絵と同じく、公式素材を配らないため）。"""
        lbl = self._face_lbl
        if lbl is None:
            return
        sp = self._current_speaker() if self._engine_ready() else None
        self._face_want = sp[1] if sp else None
        img = self._face_cache.get(sp[1]) if sp else None
        try:
            lbl.configure(image=img or self._face_blank, cursor="hand2" if sp else "")
        except tk.TclError:
            return   # 閉じている最中
        if sp is None or img is not None or sp[1] in self._face_asked:
            return
        _label, style_id, uuid = sp
        self._face_asked.add(style_id)
        base, size = self.base_url, self._face_px

        def worker():
            try:
                png = core.vv_style_icon(base, uuid, style_id, timeout=10)
                data = _face_png(png, size) if png else None
            except Exception:
                data = None   # 顔が取れなくても、声は使える（人の形のまま）
            self.q.put(("face", style_id, data))
        threading.Thread(target=worker, daemon=True).start()

    def _face_done(self, style_id, data):
        """裏で取った顔が届いた。覚えて、いま出したい顔ならすぐ出す。取れなかった顔は、
        次につながったときにもう一度だけ取りに行く（_set_conn_state）。"""
        if data:
            try:
                self._face_cache[style_id] = tk.PhotoImage(master=self, data=data)
            except tk.TclError:
                return
            if style_id == self._face_want:
                self._update_face()

    def _face_clicked(self, event=None):
        """顔を押すと、その声のサンプルを聴く（「♪ 声を聴く」と同じ）。"""
        if self._face_want is not None and self.sample_btn.instate(["!disabled"]):
            self.play_speaker_sample()
        return "break"

    def _face_tip(self):
        sp = self._current_speaker() if self._engine_ready() else None
        if sp is None:
            return "VOICEVOX につながると、選んだ声のキャラの顔が出ます。"
        return (f"{sp[0]}（VOICEVOX の公式イラスト）\n"
                "押すと、この声のサンプルを聴けます。")

    def _portrait_frames(self):
        return self._portraits.get(getattr(self, "_portrait_key", None)) or {}

    def _show_frame(self, name):
        """現在のキャラの指定フレームを表示（無ければ base にフォールバック）。"""
        if not getattr(self, "_portrait_label", None):
            return
        frames = self._portrait_frames()
        img = frames.get(name) or frames.get("base")
        if img is not None:
            self._portrait_label.config(image=img)
            self._portrait_label.image = img  # GC防止の参照保持

    def _show_no_portrait(self, char=""):
        """立ち絵が無いキャラのとき、空にするだけでなく「置けば出る」案内を出す。
        別キャラを誤表示しない一方で、なぜ空なのか・どうすれば出るかが分かるように。"""
        if not getattr(self, "_portrait_label", None):
            return
        char = self._char_name(char) if char else ""
        fname = f"{_portrait_key(char)}.png" if char else "キャラ名.png"
        msg = (f"「{char}」の立ち絵はまだありません。\n\n"
               f"assets/立ち絵/ に\n{fname}\nを置くと、ここに表示されます。\n"
               "（全キャラ対応・入れ方は同フォルダの README を参照）"
               if char else "立ち絵を assets/立ち絵/ に置くと表示されます。")
        self._portrait_label.config(image="", text=msg, wraplength=210,
                                    justify="center", fg="#9a8f7d")
        self._portrait_label.image = None

    def _update_portrait(self, event=None):
        """選択中の話者に応じて立ち絵を切り替える。そのキャラの立ち絵が無ければ
        別キャラを誤表示せず、案内テキストを出す（全キャラ対応の素直な挙動）。
        ② の顔（VOICEVOX の公式イラスト）も、ここで一緒に切り替える。"""
        self._update_face()
        if not getattr(self, "_portrait_label", None):
            return
        label = self._current_speaker_label()
        key = self._portrait_key_for(label)
        self._portrait_key = key
        if key is None:
            self._show_no_portrait(label)
            return
        self._portrait_label.config(text="")   # 案内を消して画像へ
        self._show_frame("open" if self._mouth_open else "base")

    # --- まばたき（アイドル時の小さな生命感。blinkフレームが無ければ何もしない） ---
    def _blink_tick(self):
        try:
            if self._portrait_frames().get("blink") and not self._mouth_open:
                self._show_frame("blink")
                self.after(130, lambda: self._show_frame(
                    "open" if self._mouth_open else "base"))
            self._blink_after = self.after(random.randint(2800, 6400),
                                           self._blink_tick)
        except tk.TclError:
            self._blink_after = None

    # --- 口パク（試聴・連続再生の間だけ。openフレームが無ければ何もしない） ---
    def _start_mouth(self, speaker_id=None):
        if not self._portraits or not getattr(self, "_portrait_label", None):
            return
        if speaker_id is not None:
            # 喋る行のキャラに立ち絵を合わせる。立ち絵が無いキャラなら別キャラの口を
            # 動かさず枠だけにする（全キャラ対応：連続再生で話者が変わっても誤表示なし）
            label = self._speaker_label_for_id(speaker_id)
            key = self._portrait_key_for(label)
            self._portrait_key = key
            if key is None:
                self._show_no_portrait(label)
                return
            self._portrait_label.config(text="")   # 案内を消して画像へ
        self._stop_mouth(restore=False)
        self._mouth_after = self.after(90, self._mouth_tick)

    def _mouth_tick(self):
        try:
            if not self._portrait_frames().get("open"):
                self._mouth_after = None
                return
            self._mouth_open = not self._mouth_open
            self._show_frame("open" if self._mouth_open else "base")
            self._mouth_after = self.after(110, self._mouth_tick)
        except tk.TclError:
            self._mouth_after = None

    def _close_mouth(self):
        """口パクを止めて口を閉じる（キャラはそのまま）。一時停止中と行間に使う。"""
        self._stop_mouth(restore=False)
        self._show_frame("base")

    def _stop_mouth(self, restore=True):
        if getattr(self, "_mouth_after", None):
            try:
                self.after_cancel(self._mouth_after)
            except tk.TclError:
                pass
            self._mouth_after = None
        self._mouth_open = False
        if restore:
            self._update_portrait()  # 既定話者の base に戻す

    # 立ち絵の自動開閉。メインに最小の窓と同じ幅（_MAIN_MIN_W）が残るときだけ出す。
    # 以前は 1120/1060px の決め打ちで表示倍率を見ておらず、125%表示の既定の窓でも
    # 立ち絵が出て、メインが861pxに潰れていた（「全ルール適用」が「全」になる等）。
    # 表示/非表示で値を分ける（ヒステリシス）ことで、境界付近でウィンドウをドラッグ
    # したときの pack/pack_forget 連打・チラつきを防ぐ
    _MAIN_MIN_W = 860     # 論理px（_px で倍率補正）。窓の最小幅と同じ
    _SIDE_HYST_W = 60

    def _on_configure(self, event=None):
        """<Configure>はリサイズ中に連発するため、after_idleで1回にまとめてから
        立ち絵の開閉判定へ渡す（デバウンス）。"""
        if getattr(self, "_resize_after", None):
            try:
                self.after_cancel(self._resize_after)
            except tk.TclError:
                pass
        try:
            self._resize_after = self.after_idle(self._on_resize_debounced)
        except tk.TclError:
            self._resize_after = None

    def _on_resize_debounced(self):
        self._resize_after = None
        try:
            self._on_resize_toggle_side()
        except tk.TclError:
            pass  # destroy中のafterコールバック

    def _on_resize_toggle_side(self, event=None):
        """窓が狭いときは立ち絵パネルを自動で畳み、メインの横幅を確保する。"""
        if self._side is None:
            return
        try:
            w = self.winfo_width()
            hide_w = (_px(self._MAIN_MIN_W) + self._side.winfo_reqwidth()
                      + PAD_X)
        except tk.TclError:
            return
        show_w = hide_w + _px(self._SIDE_HYST_W)
        if w >= show_w and not self._side_shown:
            self._side.pack(side="right", fill="y", padx=(0, PAD_X), pady=PAD_Y,
                            before=self._main)
            self._side_shown = True
        elif w < hide_w and self._side_shown:
            self._side.pack_forget()
            self._side_shown = False

    # ---------------- 幅に合わせた行の並び ----------------
    def _wrapping_copy(self, var):
        """折り返して見せる文の、表示用の写しを返す（元の var に追従する）。
        空白を改行しない空白（U+00A0）に替える: Tk は空白でしか折り返さず、空白の
        無い日本語の文を1語とみなすので、「✅ 抽出できたよ：…」が「✅」だけの行と
        残りに割れていた。替えると行いっぱいに詰めて折り返す。"""
        disp = tk.StringVar(self, value=var.get().replace(" ", "\u00a0"))
        var.trace_add("write",
                      lambda *_: disp.set(var.get().replace(" ", "\u00a0")))
        return disp

    def destroy(self):
        """窓を閉じる。行の並び直しの予約は先に取り消す。macOS の Tk は、窓を壊す
        途中でたまっていた after_idle を動かすことがあり、壊れかけの部品を測ったり
        並べ替えたりして Tk ごと落ちていた（CI の macOS の GUI テストで再現）。"""
        self._destroying = True
        if getattr(self, "_drop_after", None) is not None:
            try:
                self.after_cancel(self._drop_after)
            except tk.TclError:
                pass
            self._drop_after = None
        if self._reflow_after is not None:
            try:
                self.after_cancel(self._reflow_after)
            except tk.TclError:
                pass
            self._reflow_after = None
        # 別の行へ置いた部品（本文の行数など・再生バーの速さと間）は、壊す前に外す。macOS の
        # Tk 8.6.12（Python 3.9 に付いてくるもの）は、親ではない枠に pack(in_=…) で
        # 置いた部品が残ったまま窓を壊すと落ちる（CI の macOS で、外すと4回とも落ちず、
        # 外さないと半分ほど落ちた）
        for w in (getattr(self, "_rbar", (None,) * 3)[2],
                  getattr(self, "furigana_chk", None), getattr(self, "dict_btn", None),
                  getattr(self, "furigana_only_chk", None),
                  getattr(self, "furi_next_btn", None),
                  getattr(self, "_player_rows", (None,) * 3)[2],
                  getattr(self, "_player_tail", None)):
            if w is not None:
                try:
                    w.pack_forget()
                except tk.TclError:
                    pass
        super().destroy()

    def _schedule_reflow(self, event=None):
        """窓の幅や文の長さが変わったら、行の並び（_reflow）を1回だけやり直す。
        リサイズ中は <Configure> が連発するので、after_idle でまとめる。"""
        if self._reflow_after is not None or self._destroying:
            return   # 予約済み・閉じている最中
        try:
            self._reflow_after = self.after_idle(self._reflow)
        except tk.TclError:
            self._reflow_after = None

    def _reflow(self):
        """狭い窓で切れる所の並びを、今の幅に合わせて自動で決める。
        どれも「今の並び」に左右されない幅（部品の必要な幅と、行の実際の幅）で
        決めるので、並びを変えたせいでまた並びが変わる、という行き来は起きない。
        ここでは update_idletasks を呼ばない（macOS の Tk は、after_idle の中から
        もう一度ためた処理を回すと落ちることがある）。測った幅が古くても、並べ終わって
        本文欄の <Configure> が来たら、もう一度ここが動いて正しい並びになる。"""
        self._reflow_after = None
        if self._destroying:
            return
        try:
            self._reflow_status()
            self._reflow_result_bar()
            self._reflow_player_row()
        except (tk.TclError, AttributeError):
            pass   # 構築途中（まだ無い行がある）・終了中は無視

    def _reflow_status(self):
        """上の帯の状態文: 欄は2行ぶんの高さに固定し、3行以上になる文は先頭から見せる。"""
        box, lbl = self._status_box, self._status_lbl
        font = str(lbl.cget("font")) or "TkDefaultFont"
        line = int(self.tk.call("font", "metrics", font, "-linespace"))
        height = max(2 * line + _px(4), self.theme_cb.winfo_reqheight())
        # cget の値は winfo_pixels で数にする。Python 3.9 の tkinter は Tcl のまま
        # 返すことがあり、int() に渡すと TypeError で並び直しが途中で止まっていた
        if box.winfo_pixels(box.cget("height")) != height:
            box.configure(height=height)
        anchor = "nw" if lbl.winfo_reqheight() > height else "w"
        if str(lbl.cget("anchor")) != anchor:
            lbl.configure(anchor=anchor)

    def _reflow_player_row(self):
        """本文の下の再生バー: 1行に入らないときは「速さ・間」と「何行目か」を下の
        行へ送り、1行目にはボタンだけを残す。入らないまま並べると、何行目かの表示と
        進み具合の線が潰れて切れていた（125%表示のいちばん狭い窓で「12,345 / 12,345行」
        の後ろが欠けた）。"""
        ctl, row2, params, sep = self._player_rows
        tail = self._player_tail
        # 短くして出していても、全部の文の幅で決める（並びを変えたせいで、また変わらないように）
        tail_w = (self._text_width(self._pos_var.get()) + _pack_padx(self._pos_lbl)
                  + self._play_prog.winfo_reqwidth() + _pack_padx(self._play_prog))
        extra = (_packed_width(params) + sep.winfo_reqwidth() + 4 * GAPX
                 + tail_w + 2 * GAPX)
        base = _packed_width(ctl, skip=(params, sep, tail))
        # 今の表示（記号だけかどうか）に左右されないよう、元の文で出したときの幅で決める。
        # 元の幅は字の幅からの見積もりではなく、元の文で出していたときに実際に測った幅を
        # 使う（見積もりが数 px ずれると、縮める・戻すを際限なく繰り返して固まった）
        if not self._player_compact:
            self._player_full_req = {btn: btn.winfo_reqwidth()
                                     for btn, _f, _s in self._player_short}
        else:
            for btn, _full, _short in self._player_short:
                base += self._player_full_req.get(btn, 0) - btn.winfo_reqwidth()
        compact = ctl.winfo_width() < base
        if compact != self._player_compact:
            self._player_compact = compact
            for btn, full, short in self._player_short:
                btn.configure(text=short if compact else full)
        one = ctl.winfo_width() >= base + extra
        # 2行目に「速さ・間」と並べて、何行目か（短い方）と線路（いちばん細くて _px(40)）が入るか
        tail_min = (self._text_width(self._pos_var.get().split("・")[-1])
                    + _pack_padx(self._pos_lbl) + _px(40) + _pack_padx(self._play_prog)
                    + 4 * GAPX)
        two = ctl.winfo_width() >= _packed_width(params) + tail_min
        layout = 1 if one else (2 if two else 3)
        self._fit_position()
        if layout == self._player_layout:
            return
        self._player_layout = layout
        self._player_one = one
        row3 = self._player_row3
        if one:
            row2.pack_forget()
            row3.pack_forget()
            sep.pack(side="left", fill="y", padx=GAPX * 2)
            params.pack(in_=ctl, side="left")
            tail.pack(in_=ctl, side="left", fill="x", expand=True, padx=(GAPX * 2, 0))
        else:
            sep.pack_forget()
            row2.pack(fill="x", pady=(2, 0), after=ctl)
            params.pack(in_=row2, side="left")
            if layout == 2:
                row3.pack_forget()
                tail.pack(in_=row2, side="left", fill="x", expand=True,
                          padx=(GAPX * 2, 0))
            else:
                row3.pack(fill="x", pady=(2, 0), after=row2)
                tail.pack(in_=row3, side="left", fill="x", expand=True, padx=0)
        params.lift()
        tail.lift()

    def _fit_position(self):
        """「何行目か・あと◯分」を、進み具合の線を潰さない長さで出す。入らないときは
        あと◯分だけにする（全部は吹き出しで読める）。"""
        full = self._pos_var.get()
        room = self._player_tail.winfo_width() - _pack_padx(self._play_prog) - _px(40)
        show = full
        if "・" in full and self._text_width(full) > room:
            show = full.split("・")[-1]
        if self._pos_show.get() != show:
            self._pos_show.set(show)

    def _reflow_result_bar(self):
        """本文の見出しの行。幅に合わせて3段階で並べる:
          広い … 「本文」・行数など・道具（ふりがな・誤読候補・辞書・一括置換・全消去）を1行に
          中くらい … 行数などを本文の下の行へ
          狭い … さらに、ふりがな・誤読候補・辞書も本文の下の行へ（一括置換・全消去は残す）
        どれも入りきらないまま並べると、左端の部品が切れていた。"""
        bar, tools, info, foot, body = self._rbar
        movable = (self.furigana_chk, self.furigana_only_chk, self.furi_next_btn,
                   self.dict_btn)
        head = self._rbar_head.winfo_reqwidth()
        # 動かす部品の余白は、どちらの行でも同じ（下で同じ padx で置く）。実際の余白で
        # 足す（多めに見ていたぶん、125%表示の既定の窓で 3px 足りず、下の行へ送っていた）
        mov = sum(w.winfo_reqwidth() + _pack_padx(w) for w in movable)
        fixed = _packed_width(tools, skip=movable)
        # 今の表示（縮めているかどうか）に左右されないよう、元の文の幅にして比べる。
        # 縮めると save だけ狭くなる
        # 元の幅は、元の文で出していたときに実際に測った幅（字の幅からの見積もりだと
        # 数 px ずれて、縮める・戻すを繰り返して固まることがある）
        tw = self._text_width
        dict_full, dict_short = "読み方辞書...", "辞書..."
        if not self._rbar_compact:
            self._rbar_full_req = (self.dict_btn.winfo_reqwidth(),
                                   self._rep_btn.winfo_reqwidth())
        else:
            mov += self._rbar_full_req[0] - self.dict_btn.winfo_reqwidth()
            fixed += self._rbar_full_req[1] - self._rep_btn.winfo_reqwidth()
        # 縮めたときに狭くなるぶん（見積もり。縮めるかどうかを決めるだけで、戻すときは
        # 上の実際の幅で決めるので、ずれても行き来しない）
        save = (tw(dict_full) - tw(dict_short)
                + tw(self._rep_label(False)) - tw(self._rep_label(True)))
        width = bar.winfo_width()
        full = self._text_width(self.stats_var.get())
        compact = False
        if width >= head + full + fixed + mov + 4 * GAPX:
            level = 0
        elif width >= head + fixed + mov + 2 * GAPX:
            level = 1
        elif width >= head + fixed + mov - save + 2 * GAPX:
            # 1024×768 の100%表示などで、既定の窓でもふりがなの道具が本文の下へ
            # 押し出されていた。短い名前にすれば上の行に入る
            level, compact = 1, True
        else:
            level = 2
        if compact != self._rbar_compact:
            self._rbar_compact = compact
            self.dict_btn.configure(text=dict_short if compact else dict_full)
            self._rep_btn.configure(text=self._rep_label())
        self._fit_stats(width - (mov if level == 2 else 0) - 2 * GAPX if level else None)
        if level == self._rbar_level:
            return
        self._rbar_level = level
        self._rbar_one = level == 0
        if level == 0:
            foot.pack_forget()
        else:
            foot.pack(side="bottom", fill="x", pady=(GAPY, 0), before=body)
        box = foot if level == 2 else tools
        info.pack_forget()   # 最後に詰め直す（足りない幅は、この文が縮んで受ける）
        # 右から 辞書・次の候補・誤読候補だけ・ふりがな の順に詰める（見た目は左から逆順）
        self.dict_btn.pack(in_=box, side="right", padx=(GAPX // 2, 0))
        if box is tools:   # 一括置換・全消去より左に
            self.dict_btn.pack_configure(after=self._rep_btn)
        self.furi_next_btn.pack(in_=box, side="right", padx=(0, GAPX // 2),
                                after=self.dict_btn)
        self.furigana_only_chk.pack(in_=box, side="right", padx=(0, GAPX),
                                    after=self.furi_next_btn)
        self.furigana_chk.pack(in_=box, side="right", padx=(0, GAPX // 2),
                               after=self.furigana_only_chk)
        if level == 0:
            info.pack(in_=bar, side="left", padx=(GAPX * 2, 0))
        else:
            info.pack(in_=foot, side="left", padx=0)
        for w in (info,) + movable:
            w.lift()

    def _text_width(self, text):
        """ふつうの字体で text を書いたときの幅（ピクセル）。部品の字体と同じ字体で測る:
        Windows の部品は UI_FONT（10pt）で、TkDefaultFont（OS の 9pt）ではない。前は
        TkDefaultFont で測っていて、実際より1割ほど狭く見積もっていた（入ると見た文が
        少し切れた。125%表示のいちばん狭い窓で、長い本の「12,345行・約25時間12分」など）。"""
        return int(self.tk.call("font", "measure", UI_FONT or "TkDefaultFont", text))

    def _fit_stats(self, room):
        """行数・文字数・めやすを、置かれた場所に入る長さで出す（全部は吹き出しで読める）。
        room は使える幅（None なら全部入る）。本文の下の行に置くいちばん狭い窓では、
        長い本だとめやすの時間が切れて読めなかった。"""
        full = self.stats_var.get()
        options = [full]
        parts = full.split("・")
        if len(parts) == 3:
            lines, est = parts[0], parts[2]
            short = est.replace("めやす", "")
            options += [f"{lines}・{est}", f"{lines}・{short}", short]
        show = options[-1]
        if room is None:
            show = full
        else:
            for text in options:
                if self._text_width(text) <= room:
                    show = text
                    break
        if self._stats_show.get() != show:
            self._stats_show.set(show)

    # ---------------- 本文を広げる（読書モード） ----------------
    # 短く（いちばん狭い窓では、再生バーのボタンの行にもう余りが少ない）
    _READ_LABELS = {False: "◁ 広げる", True: "▷ 戻す"}

    def toggle_reading(self):
        self._set_reading(not self._reading)

    def _set_reading(self, on):
        """左の列を畳んで、本文を窓いっぱいに広げる（案は dtrknt15 さんの PR #23）。
        本文欄と再生バーはそのままなので、文字の大きさは本文欄の <Configure> で合わせ直る
        （_fit_text_after_resize）。起動するときは、いつも左の列を出しておく。"""
        on = bool(on)
        if on == self._reading:
            return
        self._reading = on
        col = self._steps_col
        if on:
            self._steps_pack = {k: v for k, v in col.pack_info().items() if k != "in"}
            try:
                focus = self.focus_get()
            except (KeyError, tk.TclError):
                focus = None
            if focus is not None and str(focus).startswith(str(col) + "."):
                self.text.focus_set()   # 隠れる欄に文字が入り続けないように
            col.pack_forget()
        else:
            col.pack(before=self._text_col, **self._steps_pack)
        self.read_btn.config(text=self._READ_LABELS[on])
        self._schedule_reflow()

    def _toggle_replace(self):
        self._set_replace(not self._rep_open)
        if self._rep_open:
            self._find_entry.focus_set()

    def _set_replace(self, show):
        """本文の一括置換の道具を開く/畳む（見出しの行のすぐ下に出す）。"""
        self._rep_open = bool(show)
        if self._rep_open:
            self._rep_frame.pack(fill="x", after=self._rbar[0])
        else:
            self._rep_frame.pack_forget()
        self._rep_btn.config(text=self._rep_label())

    def _rep_label(self, short=None):
        """「置き換え ▷」の文。見出しの行が狭いときは「置換 ▷」（_reflow_result_bar）。"""
        if short is None:
            short = self._rbar_compact
        return ("置換" if short else "置き換え") + (" ▽" if self._rep_open else " ▷")

    # ---------------- 合成パラメータ・行別話者ヘルパー ----------------
    def _format_choices(self):
        choices = ["WAV"]
        if "m4a" in self.encoders:
            choices.append("M4A")
        if "mp3" in self.encoders:
            choices.append("MP3")
        if "m4a" in self.encoders:
            choices.append(self._FMT_LABELS["m4b"])   # オーディオブック（章見出しで目次）
        return choices

    def _out_format(self):
        shown = self.fmt_cb.get() or "WAV"
        for key, label in self._FMT_LABELS.items():
            if shown in (label, key.upper()):
                return key
        return "wav"

    def _unit(self):
        i = self.unit_cb.current()
        keys = list(self._UNITS.keys())
        return keys[i] if 0 <= i < len(keys) else "combine"

    def _voice_params(self):
        return dict(speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    intonation=self.into_var.get(), volume=self.vol_var.get())

    def _prewarm_ocr(self):
        """スクリーンショット・画像の読み取りで使う部品を、起動のあと裏で読み込んでおく。
        とくに Mac の Apple Vision は初回の読み込みだけで1秒以上かかり、
        何もしないと「初めて「スクリーンショット」を使ったときだけ遅い」になる。失敗しても何もしない
        （そのときは、これまでどおり使う時に読み込まれる）。テスト中はしない。"""
        if "pytest" in sys.modules:
            return

        def load():
            try:
                from PIL import Image, ImageGrab  # noqa: F401
                if core.IS_MAC:
                    import Vision  # noqa: F401
                    import Foundation  # noqa: F401
            except Exception:
                pass
        threading.Thread(target=load, daemon=True).start()

    def _warm_speaker(self, style_id=None):
        """選ばれている話者のモデルを、裏でエンジンに読み込ませておく。
        押してから最初の音が出るまでの待ちを縮めるための先回り。
        同じ話者は一度しか頼まない（エンジン側でも素通りするが、往復も省く）。"""
        if style_id is None:
            sp = self._current_speaker()
            style_id = sp[1] if sp else None
        if style_id is None or not self._engine_ready():
            return
        key = (self._engine_id, int(style_id))
        if key in self._warmed:
            return
        self._warmed.add(key)
        base = self.base_url
        proc = self._engine_proc
        first_gpu = (getattr(proc, "uses_gpu", False) and proc.poll() is None
                     and self._gpu_warmed is not proc)
        if first_gpu:
            self._gpu_warmed = proc

        def work():
            ok = core.vv_warm_speaker(base, style_id)
            if not first_gpu:
                return
            # GPU は最初の1文だけ遅い（約1.7秒）ので、押される前に1回作っておく。
            # ここで作れなければ GPU では動かないので、CPU で起こし直す
            try:
                core.vv_synthesize_one(base, "あ。", style_id, timeout=120, read_fix=False)
            except Exception:
                ok = False
            if not ok:
                self.q.put(("gpu_failed",))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- カーソル行の先読み ----------------
    _PREFETCH_MS = 700   # これだけ手が止まったら「読む行が決まった」とみなす
    _PREFETCH_MAX_CHARS = 200   # これより長い行は先読みしない（エンジンをふさがない）

    def _schedule_prefetch(self, event=None):
        """カーソルが止まったら先読みを予約する（動くたびに取り直す）。"""
        if self._prefetch_after:
            try:
                self.after_cancel(self._prefetch_after)
            except tk.TclError:
                pass
        try:
            self._prefetch_after = self.after(self._PREFETCH_MS,
                                              self._prefetch_line)
        except tk.TclError:
            self._prefetch_after = None

    def _prefetch_line(self):
        """カーソル行を裏で合成してキャッシュに置く。画面には何も出さない。
        つながっていて、かつ何もしていないときだけ動く（本番の合成・再生の
        じゃまをしない）。失敗しても黙って諦める＝押したときに普通に合成される。"""
        self._prefetch_after = None
        if self._prefetching:
            # 先読みが1本走っている。ここで捨てると、そのあいだに移った
            # 「本当に読ませたい行」が先読みされない。終わるころに見直す
            self._schedule_prefetch()
            return
        if (self.busy or self._previewing
                or not self._engine_ready() or not core.can_play()):
            return
        default_sp = self._current_speaker()
        if default_sp is None:
            return
        try:
            line = self.text.get("insert linestart", "insert lineend").strip()
        except tk.TclError:
            return   # 終了中
        if not line or core.is_memo_line(line):
            return
        spoken, sp = self._resolve_line(line)
        if not spoken.strip():
            return
        if len(spoken) > self._PREFETCH_MAX_CHARS:
            # PDF整形などで数千字の1行ができることがある。そんな行を頼まれても
            # いないのに合成すると、エンジンを数十秒ふさいで、その間に押した
            # ▶試聴まで遅くなる。待ちが気になるのは短い行なので、長い行は見送る
            return
        try:
            voice = self._voice_params()
        except tk.TclError:
            return   # 数値欄が空。先回りは黙って見送る（押したときに案内が出る）
        style_id = sp[1] if sp else default_sp[1]
        done = threading.Event()
        # ▶試聴 がこの先読みと同じ行だと分かるように、行き先を覚えておく
        self._prefetch_target = (spoken, style_id, tuple(sorted(voice.items())))
        self._prefetch_done = done
        self._prefetching = True
        try:
            threading.Thread(
                target=self._prefetch_worker,
                args=(self.base_url, self._engine_ver, spoken, style_id,
                      voice, done),
                daemon=True).start()
        except RuntimeError:
            done.set()
            self._prefetching = False

    def _prefetch_worker(self, base, ver, spoken, style_id, voice, done):
        """▶試聴 が使うのと同じキーで合成し、同じキャッシュへ置く。
        キーの作り方が1つでもずれると当たらないので、_preview_worker と
        同じ材料（辞書ハッシュ・エンジン版・声の数値）から作ること。"""
        try:
            dict_hash = core.vv_dict_hash(base)
            text = core.speech_text(spoken)   # エンジンへ実際に渡す文（読み方を直した後）
            key = core.synth_cache_key(text, style_id, engine_ver=ver,
                                       dict_hash=dict_hash, **voice)
            if core.synth_cache_get(key) is None:
                wb, _reading = core.vv_synthesize_with_kana(
                    base, text, style_id, read_fix=False, **voice)
                if ver and dict_hash:
                    core.synth_cache_put(key, wb)
        except Exception:
            pass
        finally:
            done.set()
            self._prefetching = False

    def _housekeeping(self):
        """起動時のお掃除（別スレッド。画面には何も出さない）。
        合成キャッシュの上限戻しに加えて、前回までの異常終了で %TEMP% に
        残った作業用ファイルも片づける。アプリが自分で閉じるときは finally で
        消えるが、プロセスごと落とされると誰も片づけないため、実際に
        13MB のスプールが1週間残っていたことがある。"""
        try:
            core._synth_cache_evict()
        except Exception:
            pass
        try:
            count, freed = core.sweep_stale_tmp()
            if count:
                print("[housekeeping] 前回の残骸を%d件片づけました（%.1fMB）"
                      % (count, freed / 1e6))
        except Exception:
            pass

    def _dict_hash_tracker(self):
        """合成ワーカー用: 「今の辞書ハッシュ」を返す関数を作る（ワーカースレッドで呼ぶ）。
        辞書ボタンはbusy中も押せる仕様のため、実行中に辞書を直すと (1)修正前の音声が
        キャッシュから使われ続ける (2)新しい辞書の音声が古いキーで保存され、後で
        辞書を戻したとき誤った音声がヒットする、の2つの汚染が起きる。世代番号
        （_dict_gen。辞書変更のたび+1）を見て、変わっていたらハッシュを取り直す。"""
        state = {"gen": self._dict_gen,
                 "hash": core.vv_dict_hash(self.base_url)}
        lock = threading.Lock()

        def current():
            gen = self._dict_gen
            if gen != state["gen"]:
                with lock:
                    if gen != state["gen"]:   # 3並列の同時再取得を1回に
                        state["hash"] = core.vv_dict_hash(self.base_url)
                        state["gen"] = gen
            return state["hash"]
        return current

    def _safe_voice_params(self):
        """_voice_params の空欄ガード付き版。話速などの数値欄が空だと .get() が
        TclError を投げるため、状態変更（_previewing 等）の“前”に必ずこちらで読む。
        （欄が空のまま試聴/連続再生するとUIが永久ロックしていたバグの恒久対策）"""
        try:
            return self._voice_params()
        except tk.TclError:
            messagebox.showwarning("入力エラー",
                                   "速さ・高さ・抑揚・音量のどれかが空か、数字ではありません。\n"
                                   "数字を入れてからもう一度お試しください。")
            return None

    def _resolve_line(self, line):
        """行から (読み上げテキスト, speakerタプル or None=既定話者) を返す。
        優先度: 行頭の@タグ > セリフ自動振り分け > 既定話者"""
        name, rest = core.parse_speaker_tag(line)
        if name is not None:
            sp = core.resolve_speaker(name, self.speakers)
            if sp:
                return rest, sp
            return line, None  # 未解決タグは行全体を既定話者で読む（気づけるように）
        if (self.dlg_var.get() and core.is_dialogue_line(line)
                and self.dlg_speaker_cb.current() >= 0):
            return line, self.speakers[self.dlg_speaker_cb.current()]
        return line, None

    # ---------------- ファイル操作 ----------------
    def add_files(self):
        ft = [("対応ファイル", " ".join("*" + e for e in ALL_EXT)),
              ("PDF", "*.pdf"), ("画像", "*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp *.gif"),
              ("テキスト・文書", "*.txt *.docx *.epub"),
              ("すべて", "*.*")]
        added = 0
        for p in filedialog.askopenfilenames(title="ファイルを選択", filetypes=ft):
            added += self._add_one(p)
        self._auto_extract(added)

    def add_folder(self):
        d = filedialog.askdirectory(title="フォルダを選択")
        if not d:
            return
        self._auto_extract(self._add_path(d))

    def _auto_extract(self, added):
        """ファイルを入れたら、すぐ文字を取り出す（「▶ テキスト抽出 実行」を押す
        手間をなくす）。本文が空のときだけ。本文があるときに取り出すと、直した所ごと
        入れ替わってしまうので、押してもらうよう案内するだけにする。"""
        if not added or self.busy or self._previewing:
            return
        if self.text.get("1.0", "end").strip():
            self.status_var.set(
                f"{added}件追加したよ。本文があるので、取り出すときは"
                "「▶ テキスト抽出 実行」を押してね（本文は入れ替わります）")
            return
        self.start_extract()

    def _add_path(self, p):
        """ファイルなら対応形式のとき追加、フォルダなら中の対応ファイルを追加。追加件数を返す。"""
        added = 0
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                fp = os.path.join(p, name)
                if os.path.isfile(fp) and os.path.splitext(fp)[1].lower() in ALL_EXT:
                    added += self._add_one(fp)
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in ALL_EXT:
            added += self._add_one(p)
        return added

    def _add_one(self, p):
        if p not in self.files:
            self.files.append(p)
            self.listbox.insert("end", os.path.basename(p))
            self._sync_list_hint()
            return 1
        return 0

    def _enable_drop(self, widget):
        """ウィジェットをファイルのドロップ先として登録する（tkinterdnd2が無ければ無視）。
        メニュー（右クリックの一覧など）は登録しない。Tk ではメニューもそれ自体が窓で、
        macOS ではドロップ先に登録しただけで、中身の無い黒い窓（題名「!menu」）として
        画面に出てしまう（v1.26 で登録を窓が出たあとに回してから、起動のたびに出ていた）"""
        if not (_HAS_DND and self._dnd_ready):
            return
        if widget.winfo_class() == "Menu":
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _register_drop_tree_later(self):
        """窓が出たあとで D&D の部品を読み込み、画面の全部品をドロップ先にする。"""
        self._drop_after = None
        if self._destroying:
            return
        if _HAS_DND and not self._dnd_ready:
            try:
                # 公開の require は新しい版にだけある（古い版は同じ中身の _require）
                load = getattr(TkinterDnD, "require", None) or TkinterDnD._require
                load(self)
                self._dnd_ready = True
            except Exception:
                # 部品を読み込めない環境では、D&D 無しで使う（一覧の案内も替える）
                self._list_hint.config(text="「ファイル追加」で\n画像・PDFを選んでね")
                return
        try:
            self._register_drop_tree(self)
        except tk.TclError:
            pass   # 閉じている最中

    def _register_drop_tree(self, widget):
        """ウィジェット木の全要素をドロップ先に登録する（画面のどこに落としても効く）。"""
        if not (_HAS_DND and self._dnd_ready):
            return
        self._enable_drop(widget)
        for child in widget.winfo_children():
            self._register_drop_tree(child)

    def _on_drop(self, event):
        """エクスプローラからのファイル/フォルダのドロップを処理する。"""
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        added = 0
        skipped = 0
        for p in paths:
            p = p.strip()
            if not p:
                continue
            before = len(self.files)
            self._add_path(p)
            if len(self.files) == before and not os.path.isdir(p):
                skipped += 1
            else:
                added += len(self.files) - before
        msg = f"{added}件追加しました（ドロップ）"
        if skipped:
            msg += f" / 非対応 {skipped}件をスキップ"
        self.status_var.set(msg)
        self._auto_extract(added)

    def remove_selected(self):
        sel = self.listbox.curselection()
        for i in reversed(sel):
            self.listbox.delete(i)
            del self.files[i]
        self._sync_list_hint()
        if sel:
            self.status_var.set(f"{len(sel)}件をリストから外しました。")

    def _kb_remove_files(self, event=None):
        """listbox上の Delete/BackSpace でファイルを削除（busy中は何もしない）。"""
        if not self.busy:
            self.remove_selected()
        return "break"

    def _show_file_path(self, event=None):
        """選択ファイルのフルパスを状態欄に表示（basename表示の同名ファイル対策）。"""
        sel = self.listbox.curselection()
        if len(sel) == 1 and sel[0] < len(self.files):
            self.status_var.set(self.files[sel[0]])

    def _move_selected(self, delta):
        """選択したファイルを1つ上/下へ動かす（ファイル順＝抽出・結合の順序）。
        複数選択にも対応し、端に達したら何もしない（選択のまとまりは崩さない）。"""
        sel = list(self.listbox.curselection())
        if not sel:
            self.status_var.set("並べ替えるファイルを選択してください。")
            return
        if (delta < 0 and sel[0] == 0) or \
                (delta > 0 and sel[-1] == len(self.files) - 1):
            return   # もう端にいる
        for i in (sel if delta < 0 else reversed(sel)):
            j = i + delta
            self.files[i], self.files[j] = self.files[j], self.files[i]
            label = self.listbox.get(i)
            self.listbox.delete(i)
            self.listbox.insert(j, label)
        self.listbox.selection_clear(0, "end")
        for i in sel:
            self.listbox.selection_set(i + delta)
        self.listbox.see(sel[0] + delta)

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, "end")
        self._sync_list_hint()

    # ---------------- 抽出（別スレッド） ----------------
    def start_extract(self):
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        if not self.files:
            messagebox.showinfo("情報", "先にファイルを追加してください。")
            return
        # tkinter変数はメインスレッドでのみ読める。_set_busy(True) の“前”に読み切る。
        # 数値欄（DPI等）が空だと .get() が TclError を投げるため、ここで捕まえておかないと
        # busy=True のまま例外で抜けてUI全体が固まる（ワーカー未起動→完了通知が来ない）。
        try:
            params = dict(
                paths=list(self.files),
                pdf_mode=self.pdf_var.get(),
                dpi=self.dpi_var.get(),
                preprocess=self.pre_var.get(),
                # 映像内ラベル除去は「ノイズ除去」チェックと同じON/OFFで効かせる（座標段階で適用）
                strip_labels=self.denoise_var.get(),
                # 同形文字の文脈補正・ノイズ除去はOCR由来テキストにだけ効く
                # （テキスト層・txt/docx/EPUBは対象外。小説の英文行の誤削除防止）
                fix_confusables=self.fixconf_var.get(),
                denoise=self.denoise_var.get(),
                strip_heads=self.heads_var.get(),
                # PDFの読み取り範囲（空欄=全ページ）。settingsには保存しない
                # （本ごとに違う値で、前回の指定が残ると本文欠落事故になるため）
                pdf_pages=core.parse_page_ranges(self.pages_var.get()),
            )
            clean_opts = self._gather_clean_opts()
        except ValueError:
            messagebox.showwarning("入力エラー",
                                   "ページ範囲の書き方が不正です"
                                   "（例: 5-320 / 1-3,10- / -20）。")
            return
        except tk.TclError:
            messagebox.showwarning("入力エラー",
                                   "数字の欄（読み取りの細かさなど）が空か、数字ではありません。\n"
                                   "数字を入れてからもう一度お試しください。")
            return
        # 抽出中は「テキスト抽出」ボタンをキャンセルボタンとして使う
        # （長いスキャンPDFのOCRを途中で止められる。音声生成のキャンセルと同じ流儀）
        self._extract_cancel = threading.Event()
        params["cancel_event"] = self._extract_cancel
        self._set_busy(True)
        self.extract_btn.config(text="× キャンセル", command=self.cancel_extract,
                                state="normal")
        self.progress.config(mode="determinate", maximum=len(self.files), value=0)
        self._spawn(self._extract_worker, (params, clean_opts))

    def cancel_extract(self):
        """実行中のテキスト抽出を中断する（そこまでの部分結果は表示される）。"""
        if self._extract_cancel is not None:
            self._extract_cancel.set()
            self.extract_btn.config(state="disabled")
            self.status_var.set("キャンセルしています...（区切りの良い所で止まります）")

    def _extract_restore_button(self):
        """抽出の完了/キャンセル/エラー後に「テキスト抽出」ボタンを元へ戻す。"""
        self._extract_cancel = None
        self.extract_btn.config(text="▶ テキスト抽出 実行",
                                command=self.start_extract)
        self._update_step_highlight()

    def _gather_clean_opts(self):
        """clean_text に渡す整形オプションを一括で読む（抽出・クリップボードOCRで共有。
        二重定義でキー追加漏れが起きるのを防ぐ）。denoise は v1.16.0 からOCR由来
        テキスト限定になったため clean_text へは常に False（適用は extract_files /
        クリップボードワーカー側）。"""
        return dict(
            mode=self.mode_var.get(),
            remove_blank=self.blank_var.get(),
            keep_ascii_spaces=self.ascii_var.get(),
            join_wrapped=self.join_var.get(),
            smart_join=self.smartjoin_var.get(),
            paren_ruby=self.pruby_var.get(),
            normalize=self.norm_var.get(),
            denoise=False,
            remove_urls=self.urlskip_var.get(),
        )

    def _extract_worker(self, params, clean_opts):
        def cb(done, total, msg):
            self.q.put(("progress", done, total, msg))
        try:
            report = {}
            # ノイズ除去（denoise）はOCR由来ページにだけ extract_files 内で適用され、
            # 除去行は report["removed"] に記録される（誤削除の確認・復元用）
            raw, warnings = core.extract_files(progress_cb=cb, report=report,
                                               **params)
            cleaned = core.clean_text(raw, **clean_opts)
            self.q.put(("extract_done", cleaned, warnings, report))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    # ---------------- VOICEVOX ----------------
    def launch_voicevox(self):
        try:
            core.launch_voicevox(self._voicevox_path)
            self.status_var.set("VOICEVOXを起動したよ。準備ができたら自動でつなぐね🍂")
            self._start_connect_retry()
        except FileNotFoundError as e:
            self._ask_voicevox_path(str(e), retry=self.launch_voicevox)
        except Exception as e:
            messagebox.showerror("VOICEVOX", f"起動に失敗: {e}")

    def launch_engine_only(self, quiet=False):
        """エディタ画面を出さず、読み上げエンジンだけを起動する。
        すでにVOICEVOXが動いているならそれを使う（二重に立ち上げない）。
        quiet: アプリを開いたときの自動の起動。頼まれてしたことではないので、
        窓で止めない（失敗しても、開くたびに窓を閉じる手間をかけない）。"""
        if self._engine_proc is not None and self._engine_proc.poll() is None:
            self.status_var.set("VOICEVOX はもう動いているよ。つながるか確かめるね🍂")
            self._start_connect_retry(30, interval=2000)
            self.check_engine(quiet=True)
            return
        try:
            # すでに誰かが使っているポートの扱いを、状態を見て決める
            p = core.vv_probe(self.base_url, timeout=core.DISCOVER_TIMEOUT)
            if p["ok"]:
                # 本人がVOICEVOXを開いている＝そこへ繋ぐだけ。勝手に増やさない
                self.status_var.set("VOICEVOXがもう動いていたので、そのままつなぐね🍂")
                self.check_engine(quiet=True)
                return
            port = 50021
            try:
                from urllib.parse import urlsplit
                port = urlsplit(self.base_url).port or 50021
            except Exception:
                pass
            if p["status"] == "not_voicevox":
                port = core.free_port()   # 別のソフトが使っているので空きを borrow
            proc, url = core.launch_voicevox_engine(
                port=port, use_gpu=self._gpu_choice(),
                user_path=self._voicevox_path)
            self._engine_proc = proc
            self.base_url = url
            self.url_var.set(url)
            if not quiet:   # 自動のときは、最初の案内の文を消さない（待ちは左下に出る）
                self.status_var.set(
                    f"VOICEVOX を画面なしで起動したよ（{url}）。準備ができたら自動でつなぐね🍂")
            self._start_connect_retry(60, interval=2000)
        except FileNotFoundError as e:
            if not quiet:
                self._ask_voicevox_path(str(e), retry=self.launch_engine_only)
        except Exception as e:
            if quiet:
                self.status_var.set("VOICEVOX を自動で起動できませんでした。"
                                    "左下の「VOICEVOX起動」から開いてみてね。")
            else:
                messagebox.showerror("VOICEVOX", f"VOICEVOX を起動できませんでした: {e}")

    def _gpu_choice(self):
        """このアプリが VOICEVOX を起こすとき、GPU を使うか（接続設定の「GPU があれば使う」）。
        GPU を使える版で、強い GPU があるときだけ使う（core.want_gpu）。一度 GPU で
        動かなかった VOICEVOX は、次からは CPU で起こす。"""
        if not self._engine_gpu:
            return False
        try:
            run = core.find_voicevox_engine(self._voicevox_path)
            return (bool(run) and run not in (self._engine_gpu_failed,
                                              self._engine_gpu_session_off)
                    and core.want_gpu(run))
        except Exception:
            return False

    def _gpu_fallback(self):
        """GPU で起こした VOICEVOX が動かなかったとき、CPU で起こし直す（次からも CPU）。
        GPU で起こしていなければ何もしないで False。"""
        proc = self._engine_proc
        if proc is None or not getattr(proc, "uses_gpu", False):
            return False
        self._engine_gpu_failed = getattr(proc, "run_path", "") or ""
        core.stop_process(proc)
        self._engine_proc = None
        if self.conn_state == "ok":
            self._set_conn_state("waiting")
        try:
            self._save_settings()
        except Exception:
            pass
        self.status_var.set("GPU では VOICEVOX がうまく動かなかったので、CPU で起動し直すね🍂")
        self.launch_engine_only(quiet=True)
        return True

    # 自分で起こした VOICEVOX が止まったとき、起こし直すのは、この秒数のあいだに3回まで
    _ENGINE_RESTART_WINDOW = 600
    _ENGINE_RESTART_MAX = 3

    def _check_engine_alive(self):
        """自分で起こした VOICEVOX が止まっていたら、起こし直す（_engine_died）。止まっていたら True。"""
        proc = self._engine_proc
        if proc is None or proc.poll() is None:
            return False
        self._engine_died()
        return True

    def _engine_died(self):
        """自分で起こした VOICEVOX が、使っている途中で止まった（落ちた・ほかのソフトに
        閉じられた）。前は「もう一度起動してください」と出すだけだったので、自動で起こし直す。
        GPU で起こしたものが続けて止まるときは、CPU で起こし直す（次からも CPU）。何度も
        止まるときは、起こし直すのをやめて知らせる（止まり続ける VOICEVOX を回し続けない）。"""
        dead = self._engine_proc
        self._engine_proc = None
        if self.conn_state == "ok":
            self._set_conn_state("lost")
        now = time.monotonic()
        self._engine_deaths = [t for t in self._engine_deaths
                               if now - t < self._ENGINE_RESTART_WINDOW] + [now]
        if len(self._engine_deaths) > self._ENGINE_RESTART_MAX:
            self.status_var.set("VOICEVOX が何度も止まったので、起動し直すのをやめました。"
                                "左下の「画面なしで起動」か「VOICEVOX起動」から起動してください。")
            return
        if getattr(dead, "uses_gpu", False) and len(self._engine_deaths) >= 2:
            # 続けて止まるなら、閉じるまでは CPU で。たまたまのこともあるので、設定には
            # 書かない（次に開いたときは、また GPU を試す）
            self._engine_gpu_session_off = getattr(dead, "run_path", "") or ""
        if self._previewing:
            after = "読み上げは、そのまま続きます"
        elif self.busy:
            after = "つながったら、そのまま続きます"
        else:
            after = "つながったら「▶ 続きを聴く」で、続きから聴けます"
        self.status_var.set(f"VOICEVOX が止まったので、起動し直しています…（{after}）")
        self.launch_engine_only(quiet=True)

    def _set_engine_gpu(self, value):
        """「GPU があれば使う」を決める（すぐ設定に書く。次に VOICEVOX を起こすときから効く）。
        付け直したら、前に GPU で動かなかった記録も消して、もう一度試す。"""
        self._engine_gpu = bool(value)
        self._engine_gpu_failed = ""
        self._engine_gpu_session_off = ""
        try:
            self._save_settings()
        except Exception:
            pass

    def _ask_voicevox_path(self, message, retry=None):
        """VOICEVOXが見つからないとき、入手先（公式サイト）か、場所を選ぶかを聞く。
        以前は「場所を手動で選びますか？」だけで、まだ入れていない人はアプリから
        入手先にたどり着けず、そこで行き止まりだった。場所を選んだら retry で
        そのまま起動し直す。"""
        win = tk.Toplevel(self)
        win.title("VOICEVOXが見つかりません")
        win.transient(self)
        win.resizable(False, False)
        self._place_dialog(win)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=_px(14), pady=_px(12))
        ttk.Label(frm, wraplength=_px(440), justify="left", text=(
            "VOICEVOXが見つかりませんでした。\n"
            "VOICEVOX は、このアプリとは別の無料のソフトです。まだ入れていなければ、"
            "公式サイトから入れてください。\n"
            "入れてあるのに見つからないとき（zip を展開して置いた場合など）は、"
            "場所を選んでください。")).pack(anchor="w")
        ttk.Label(frm, text=message, style="Credit.TLabel", wraplength=_px(440),
                  justify="left").pack(anchor="w", pady=(_px(6), 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(_px(12), 0))

        def open_site():
            win.destroy()
            import webbrowser
            try:
                webbrowser.open(VOICEVOX_SITE)
                self.status_var.set("VOICEVOXの公式サイトを開いたよ。入れたら、"
                                    "左下の「VOICEVOX起動」をもう一度押してね🍂")
            except Exception:
                self.status_var.set("ブラウザを開けませんでした：" + VOICEVOX_SITE)

        def choose():
            win.destroy()
            self._choose_voicevox_path(retry)
        ttk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="場所を選ぶ…", command=choose).pack(
            side="right", padx=(0, _px(6)))
        ttk.Button(btns, text="VOICEVOXを入手（公式サイト）", style="Primary.TButton",
                   command=open_site).pack(side="right", padx=(0, _px(6)))
        win.bind("<Escape>", lambda e: win.destroy())

    def _choose_voicevox_path(self, retry=None):
        """VOICEVOX本体の場所を選んでもらって覚える。覚えたら retry で起動し直す。"""
        if core.IS_MAC:
            # .app はフォルダ扱いなのでファイル選択では選べない
            path = filedialog.askdirectory(title="VOICEVOX.app を選んでください")
        else:
            path = filedialog.askopenfilename(
                title="VOICEVOX.exe を選んでください",
                filetypes=[("VOICEVOX", "VOICEVOX.exe"), ("すべて", "*.*")])
        if not path:
            return
        if "voicevox" not in os.path.basename(path).lower():
            messagebox.showwarning(
                "確認", "VOICEVOX本体ではないようです。\n"
                        "VOICEVOX.exe（Macは VOICEVOX.app）を選んでください。")
            return
        self._voicevox_path = path
        core.find_voicevox(refresh=True)
        self.status_var.set(f"VOICEVOXの場所を覚えたよ: {path}")
        if retry is not None:
            retry()   # 押し直してもらわなくても、そのまま起動する

    def _engine_ready(self):
        """音声機能が使える状態か。話者リストの有無ではなく接続状態で判断する。
        self.speakers は切断後も保持する（選択やタグ検証を生かしたままにする）
        ので、それだけでは「今つながっている」の根拠にならない。"""
        return self.conn_state == "ok" and bool(self.speakers)

    def _set_conn_state(self, state, ver=""):
        """接続状態の変更はすべてここを通す。ラベル・スタイル・ボタンの活性を
        一箇所で決めることで、「● 接続OK」と出ているのに実は切れている、という
        嘘の表示が起きないようにする。"""
        self.conn_state = state
        ok = (state == "ok")
        if ok:
            self._engine_ver = ver
            self._conn_fail = 0
            # 自分で GPU を使って起こした VOICEVOX につながっているときは、そう出す
            gpu = (getattr(self._engine_proc, "uses_gpu", False)
                   and self._engine_proc.poll() is None)
            self.engine_var.set(f"● 接続OK (v{ver}{'・GPU' if gpu else ''})")
            self.engine_lbl.config(style="EngineOK.TLabel")
            self._furi_retry_at = 0.0
            self._furi_schedule(0)   # つながったら、ふりがなを出せる
        else:
            # 版番号は「つながっている証拠」なので必ず捨てる。合成キャッシュの
            # キーにも使われており、残すと切断中のキャッシュ判定が狂う
            self._engine_ver = ""
            self._furi_schedule(0)   # ふりがなは出せないので、待っていることを示す
            self.engine_var.set(
                self._CONN_TEXT.get(state, self._CONN_TEXT["refused"]))
            self.engine_lbl.config(style="TLabel")
            # 直し方が分かる状態のときだけ、具体的な手がかりを添える
            if state == "bad_url":
                self.status_var.set(
                    "URLは「http://」から書いてください（例: "
                    f"{VOICEVOX_DEFAULT}）。")
            elif state == "not_voicevox":
                self.status_var.set(
                    "そのポートは別のソフトが使っています。"
                    "VOICEVOXを起動し直すと空いているポートに移ることがあります。")
        self._set_conn_compact(ok)
        # エンジンが要るボタンの活性。処理中・再生中は _set_busy 側が持ち場なので
        # 触らない（ここで有効化すると生成中のボタンが復活してしまう）
        ready = self._engine_ready()
        st = "normal" if ready else "disabled"
        self.dict_btn.config(state=st)
        self.vvproj_btn.config(state=st)
        if not (self.busy or self._previewing):
            self.sample_btn.config(state=st)
            self.preview_btn.config(state=st)
            self.playall_btn.config(state=st)
            if self._synth_cancel is None:
                self.synth_btn.config(state=st)
            self.resume_btn.config(state=("normal" if (ready and self._bookmark
                                                       is not None) else "disabled"))
        self._update_step_highlight()
        # ② の顔: つながったら出し、切れたら人の形に戻す。前に取れなかった顔は、
        # つながり直したときにもう一度だけ取りに行く
        if ok:
            self._face_asked &= set(self._face_cache)
        self._update_face()
        self._sync_furi_notice()

    def _health_tick(self):
        """接続中だけ、たまにエンジンの生存を確かめる見張り。VOICEVOXを閉じたのに
        「接続OK」のまま放置されるのを防ぐ。切断中は何もしない＝エンジンを使わない
        人の裏でずっと通信し続けることはない。"""
        # 次回の予約を先に済ませる（この先で何が起きてもループを止めないため）
        self._tick("health", self._HEALTH_INTERVAL_MS, self._health_tick)
        if "health" not in self._ticks:
            return   # 終了中
        # 自分で起動したエンジンが落ちていたら、通信するまでもなく切断とわかる。起こし直す
        # （再生中・処理中でも。待っていると、読み終わるまで起こし直せなかった）
        if self._check_engine_alive():
            return
        if self.busy or self._previewing or self._health_checking:
            return
        if self.conn_state != "ok":
            # 自分で起こした VOICEVOX が動いているのに、つながっていない（つなぎ直しの
            # 待ちが切れた等）。見張りのついでに、つなぎ直す
            proc = self._engine_proc
            if proc is not None and proc.poll() is None and not self._conn_checking:
                self.check_engine(quiet=True)
            return
        try:
            if self.state() == "iconic":
                return   # 最小化中は見張らない（ノートPCのバッテリー配慮）
        except tk.TclError:
            return
        self._health_probe_now()

    def _health_probe_now(self):
        """今すぐ一度だけ生存を確かめる（次回の予約はしない）。
        1回目の失敗のあと、本番の間隔まで待たずに念のためもう一度見るのに使う。"""
        if self._health_checking or self.conn_state != "ok":
            return
        self._health_checking = True
        threading.Thread(target=self._health_worker, daemon=True).start()

    def _health_worker(self):
        """見張りの実処理。ウィジェットには触れず、結果はキュー越しに返す
        （tkinter はワーカースレッドから触ると不定期に落ちる）。"""
        try:
            ver = core.vv_check(self.base_url, timeout=3)
        except Exception:
            ver = None
        self.q.put(("health", ver))

    def _start_connect_retry(self, seconds=90, interval=3000):
        """エンジンが応答するまで自動で接続を試みる窓を開く
        （従来は起動→手動で「エンジン接続確認」の2ステップだった）。
        窓が二重に開かないよう、tick は1本だけ回す。"""
        self._conn_retry_until = time.monotonic() + seconds
        self._conn_retry_interval = interval
        if not self._conn_retry_running:
            self._conn_retry_running = True
            self._tick("retry", interval, self._connect_retry_tick)

    def _connect_retry_tick(self):
        proc = self._engine_proc
        if (proc is not None and getattr(proc, "uses_gpu", False)
                and proc.poll() is not None and self.conn_state != "ok"):
            self._gpu_fallback()   # GPU で起こしたら、つながる前に止まった
        if self.busy or self._previewing:
            # 再生中・処理中は確かめられない（check_engine が待つ）。そのあいだは待つ時間に
            # 数えない。数えていると、長い再生のあいだに切れて、つなぎ直さないままになった
            self._conn_retry_until = max(self._conn_retry_until,
                                         time.monotonic() + self._conn_retry_interval / 1000 + 1)
        if self.conn_state == "ok" or time.monotonic() > self._conn_retry_until:
            self._conn_retry_running = False
            return   # 接続できた/諦めた（以降は手動の接続確認で）
        if not (self.busy or self._previewing):
            self.check_engine(quiet=True)   # ボタンを明滅させずに静かに確認
        self._tick("retry", self._conn_retry_interval, self._connect_retry_tick)
        if "retry" not in self._ticks:
            self._conn_retry_running = False   # 終了中

    def check_engine(self, quiet=False):
        # _previewing もガードする（連続再生中に実行すると _set_busy の往復で
        # ⏸一時停止ボタンが無効のまま取り残されるため。他のbusy操作と同じ扱い）
        if self.busy or self._previewing:
            if not quiet:
                self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        # quiet=True は起動時・VOICEVOX起動待ちの自動接続用: _set_busy を触らず
        # 静かに確認する（3秒ごとのリトライで全ボタンが明滅するのを防ぐ）
        if quiet and self._conn_checking:
            return
        # 「http://」の書き忘れをここで直す。直した結果を欄にも書き戻して、
        # 何が使われているかが見えるようにする
        url = core.vv_normalize_url(self.url_var.get())
        self.base_url = url or VOICEVOX_DEFAULT
        if self.url_var.get().strip() != self.base_url:
            self.url_var.set(self.base_url)
        # 自分でURLを変えた＝以後は自動で別ポートへ移らない（手動運用に切り替え）
        if not quiet and self.base_url != VOICEVOX_DEFAULT:
            self._engine_url_mode = "manual"
        self._conn_checking = True
        if not quiet:
            self._set_busy(True)
        self.engine_var.set("VOICEVOX: つながるか確かめています…")
        threading.Thread(target=self._check_worker, args=(quiet,),
                         daemon=True).start()

    def _check_worker(self, quiet=False):
        try:
            p = core.vv_probe(self.base_url, timeout=core.DISCOVER_TIMEOUT)
            url = self.base_url
            # 自動モードなら、既定のURLが駄目でも VOICEVOX が書き出している
            # 実際のURL（ポートが取り合いになると本体は別のポートへ逃げる）を試す
            if not p["ok"] and self._engine_url_mode == "auto":
                found, p2 = core.vv_find_engine(self.base_url)
                if found:
                    url, p = found, p2
            if not p["ok"]:
                self.q.put(("engine", None, None, quiet, p["status"], url))
                return
            speakers = core.vv_speakers(url)
            # .vvproj の engineId に使う。取れなければ公式エンジンのIDのまま
            self._engine_id = core.vv_engine_id(url)
            self.base_url = url
            self.q.put(("engine", p["version"], speakers, quiet, "ok", url))
        except Exception:
            self.q.put(("engine", None, None, quiet, "unknown", self.base_url))

    def _confirm_speaker_tags(self, tail="その行は「@名前:」も含めて、いつもの声で読みます。"
                                          "続けますか？\n（「いいえ」で、その最初の行へ移ります）"):
        """未解決の@話者タグを合成/vvproj出力の前に確認する。続行なら True。
        検証はウィジェット全文（strip前）で行い、返る行番号がウィジェットの
        行番号と一致するようにする（先頭空行があってもジャンプ先がずれない）。"""
        bad = core.unresolved_speaker_tags(
            self.text.get("1.0", "end-1c"), self.speakers)
        if not bad:
            return True
        ln0, name0 = bad[0]
        if messagebox.askyesno(
                "声の指定（@名前）の確認",
                f"どの声か分からない「@名前:」が{len(bad)}行あります"
                f"（例: {ln0}行目「@{name0}:」）。\n{tail}"):
            return True
        self.text.mark_set("insert", f"{ln0}.0")
        self.text.see(f"{ln0}.0")
        return False

    def _ask_many_files(self, n):
        """音声のファイルが多くなりすぎるとき、作る前に聞く。"combine"（1つにまとめる）・
        "keep"（このまま作る）・None（やめる）のどれかを返す。"""
        win = tk.Toplevel(self)
        win.title("ファイルの数")
        win.transient(self)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=14, pady=12)
        wrap = _px(420)
        ttk.Label(frm, text=f"このまま保存すると、音声のファイルが {n:,}個 できます。",
                  wraplength=wrap, justify="left").pack(anchor="w")
        ttk.Label(frm, text="本などを聴くなら、全部を1つのファイルにまとめるのがおすすめです。\n"
                            "（③ の「まとめ方」で、いつでも変えられます）",
                  wraplength=wrap, justify="left").pack(anchor="w", pady=(6, 0))
        choice = {"v": None}

        def pick(v):
            choice["v"] = v
            win.destroy()
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="やめる", command=lambda: pick(None)).pack(side="right")
        ttk.Button(btns, text=f"このまま {n:,}個 作る",
                   command=lambda: pick("keep")).pack(side="right", padx=6)
        one = ttk.Button(btns, text="1つにまとめる", style="Primary.TButton",
                         command=lambda: pick("combine"))
        one.pack(side="right")
        win.bind("<Escape>", lambda e: pick(None))
        win.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        self._place_dialog(win)
        one.focus_set()
        try:
            win.grab_set()
        except tk.TclError:
            pass
        self.wait_window(win)
        return choice["v"]

    def start_synth(self):
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("情報", "テキストがありません。")
            return
        default_sp = self._current_speaker()
        if default_sp is None:
            messagebox.showinfo("情報", "声を選んでください（先に VOICEVOX につないでください）。")
            return
        # @話者タグのタイプミスは合成前に指摘する（従来は行全体をタグごと既定話者が
        # 読む仕様のため、長編では全編を聴き直すまで気づけなかった）。
        # 検証はウィジェット全文（strip前）で行う＝返る行番号がウィジェットの
        # 行番号と一致し、「いいえ」のジャンプ先が先頭空行の分ずれない
        if not self._confirm_speaker_tags():
            return
        self.text.tag_remove("errline", "1.0", "end")   # 前回止まった行の印を外す
        self._furi_recolor()
        # 行別話者を解決して (テキスト, style_id, 段落番号) のジョブ一覧を作る。
        # linenos はジョブごとの本文欄の行番号（失敗した行へ移るため）。text は前後の
        # 空白を落としてあるので、落とした先頭の行数ぶんずらす
        raw = self.text.get("1.0", "end")
        lead = raw[:len(raw) - len(raw.lstrip())].count("\n")
        default_id = default_sp[1]
        jobs = []
        linenos = []
        memo_skipped = 0
        # 段落は空行と行頭の字下げで分ける（連続再生と同じ core.paragraph_ids）
        para_ids = core.paragraph_ids(text.split("\n"))
        for k, ln in enumerate(text.split("\n")):
            if not ln.strip():
                continue
            para = para_ids[k]
            if core.is_memo_line(ln):
                memo_skipped += 1   # 行頭#はメモ行（読み上げ対象外）
                continue
            # 行頭の字下げ（段落の頭）は読まない。連続再生（_speakable_lines）と同じ文に
            # そろえて、合成のキャッシュ・字幕にも余計な空白を入れない
            spoken, sp = self._resolve_line(ln.strip())
            if not spoken.strip():
                continue  # タグのみの行
            jobs.append((spoken, sp[1] if sp else default_id, para))
            linenos.append(lead + k + 1)
        if memo_skipped:
            self.status_var.set(f"＃メモ行 {memo_skipped}行は読み上げからスキップするよ")
        if not jobs:
            messagebox.showinfo("情報", "テキストがありません。")
            return
        # 数値の tk 変数は _set_busy(True) の“前”にまとめて読む。空欄だと .get() が
        # TclError を投げるので、ここで捕まえないと busy=True のままUIが固まる。
        try:
            fmt = self._out_format()
            unit = self._unit()
            nlines = max(2, self.nlines_var.get()) if unit == "nlines" else 0
            voice = self._voice_params()
            gap = self.gap_var.get()
            longer = bool(self.para_pause_var.get())
            srt = self.srt_var.get()
        except tk.TclError:
            messagebox.showwarning("入力エラー",
                                   "速さ・高さ・抑揚・音量・文の間・行数のどれかが空か、数字ではありません。\n"
                                   "数字を入れてからもう一度お試しください。")
            return
        if fmt == "m4b":
            unit = "combine"   # オーディオブックは常に全文結合（章はチャプターで分ける）
        # 各行の前の無音。段落・章の切れ目は長め（「段落・章は長め」。連続再生と同じ）
        gaps = core.line_gaps([j[0] for j in jobs], [j[2] for j in jobs], gap, longer)
        # 出力単位ごとのグループ化はGUI/CLI共有の core.group_output_indices で
        groups = core.group_output_indices(unit, [j[2] for j in jobs],
                                           nlines or 50)
        # ファイルがたくさんできるときは、作る前に聞く（本1冊を「1行=1ファイル」で
        # 保存すると、前は何も聞かれずに約2万個できていた）
        if unit != "combine" and len(groups) > self._MANY_FILES:
            choice = self._ask_many_files(len(groups))
            if choice is None:
                return
            if choice == "combine":
                unit = "combine"
                self.unit_cb.current(list(self._UNITS).index("combine"))
                self._on_unit_selected()
                groups = core.group_output_indices(unit, [j[2] for j in jobs], 50)
        if unit == "combine":
            out = filedialog.asksaveasfilename(
                title=f"{fmt.upper()}の保存先", defaultextension=f".{fmt}",
                filetypes=[(f"{fmt.upper()}ファイル", f"*.{fmt}")],
                initialfile=f"voicevox_output.{fmt}")
            if not out:
                return
            # 音声の上書きは保存ダイアログが確認済みだが、隣に書くSRTは無確認だった
            srt_path = os.path.splitext(out)[0] + ".srt"
            if srt and os.path.exists(srt_path) and not messagebox.askyesno(
                    "上書き確認",
                    f"字幕 {os.path.basename(srt_path)} も上書きされます。"
                    "続けますか？"):
                return
            target = out
        else:
            d = filedialog.askdirectory(title=f"{fmt.upper()}の出力フォルダ")
            if not d:
                return
            # フォルダ出力は保存ダイアログを通らないため無確認上書きだった。
            # 前回の出力（001_○○.wav 等）が残っていれば上書き・新旧混在を予告する
            try:
                old = [f for f in sorted(os.listdir(d))
                       if re.match(r"\d{3,}(_.*)?\.(wav|m4a|mp3|m4b|srt)$",
                                   f, re.IGNORECASE)
                       or f == "セリフ一覧.csv"]
            except OSError:
                old = []
            if old and not messagebox.askyesno(
                    "上書き確認",
                    f"出力フォルダに以前の音声らしきファイルが{len(old)}件あります"
                    f"（例: {old[0]}）。\n"
                    "上書き・新旧混在の可能性がありますが続けますか？"):
                return
            target = d
        # 生成中は「音声を生成」ボタンをキャンセルボタンとして使う
        self._synth_cancel = threading.Event()
        self._set_busy(True)
        self.synth_btn.config(text="× キャンセル", command=self.cancel_synth,
                              state="normal")
        self.progress.config(mode="determinate", maximum=len(jobs), value=0)
        self._spawn(self._synth_worker,
                    (jobs, groups, voice, target, unit, gaps, fmt, srt, linenos))

    def cancel_synth(self):
        """実行中の音声生成を中断する（未完了のファイルは保存しない）。"""
        if self._synth_cancel is not None:
            self._synth_cancel.set()
            self.synth_btn.config(state="disabled")
            self.status_var.set("キャンセルしています...")

    def _synth_restore_button(self):
        """生成の完了/キャンセル/エラー後に「音声を生成」ボタンを元へ戻す。"""
        self._synth_cancel = None
        self.synth_btn.config(text="♪ 音声を生成", command=self.start_synth)

    def _synth_worker(self, jobs, groups, voice, target, unit, gaps, fmt, srt,
                      linenos=None):
        # gaps: 各行の前に入れる無音（秒）のリスト（core.line_gaps。段落・章の切れ目は長め）
        cancel = self._synth_cancel
        # 行WAVはスプール（一時フォルダ）へ書き、メモリには (パス, 再生秒) だけ
        # 保持する。従来は全行のWAVバイト列をリストに持ち、10時間級の本で
        # メモリが数GBに達していた（結合時はさらに倍）。
        # mkdtemp は try の中で作る（ディスクフル等で失敗しても except が
        # ("error",…) を積みUIのbusyが解除されるように。外に置くと通知されず永久ロック）
        spool = None
        try:
            from concurrent.futures import ThreadPoolExecutor
            import requests
            spool = tempfile.mkdtemp(prefix="t2v_spool_")
            # 生成中に自分の前半行のキャッシュを自分で追い出さないよう保護
            core.synth_cache_protect(time.time())
            done_count = [0]
            lock = threading.Lock()
            t0 = time.monotonic()
            # 行単位の合成キャッシュ。辞書内容・エンジン版がキーに入るため、
            # 誤読を辞書で直した行だけが再合成され、他の行は即座に再利用される
            dict_hash = self._dict_hash_tracker()

            def synth(idx_job):
                i, job = idx_job
                if cancel is not None and cancel.is_set():
                    return None   # キャンセル後の残りジョブはエンジンに投げない
                text, spk, _para = job
                # エンジンの一時不調（500・timeout・接続断）で数時間の合成が
                # 全損しないよう、2回までバックオフ付きリトライ
                for attempt in range(3):
                    try:
                        wb = core.vv_synthesize_cached(
                            self.base_url, text, spk,
                            engine_ver=self._engine_ver,
                            dict_hash=dict_hash(), **voice)
                        break
                    except Exception as e:
                        if attempt == 2:
                            # エンジンが断った・この行だけ時間切れ、ならこの行のせい。
                            # つながらない（エンジンが落ちた）のは行のせいではない
                            if (isinstance(e, (requests.HTTPError, requests.Timeout))
                                    and not isinstance(e, requests.ConnectionError)):
                                raise _LineSynthError(i) from e
                            raise
                        if cancel is not None and cancel.is_set():
                            return None
                        if isinstance(e, requests.ConnectionError):
                            # VOICEVOX が落ちた。起こし直す（_engine_died）のを待ってから頼み直す
                            # （前は2秒・4秒だけ待って、起こし直す前に生成が止まっていた）
                            self._wait_engine(cancel, self._ENGINE_WAIT_SEC)
                        else:
                            time.sleep(2 * (attempt + 1))
                p = os.path.join(spool, f"{i:06d}.wav")
                with open(p, "wb") as f:
                    f.write(wb)
                dur = core.wav_duration(wb)
                with lock:
                    done_count[0] += 1
                    n = done_count[0]
                eta = (time.monotonic() - t0) / n * (len(jobs) - n)
                # 長丁場（残り2分超）はひとこと添える（人間味・不安の軽減）
                extra = "・のんびり待っててね☕" if eta > 120 else ""
                self.q.put(("progress", n, len(jobs),
                            f"音声生成中 {n}/{len(jobs)}"
                            + (f"（残り{core.fmt_duration(eta)}{extra}）"
                               if n < len(jobs) else "")))
                return (p, dur)

            # エンジンへ3並列で投げる（順序はexecutor.mapが保持する）。
            # 1行が力尽きたときは、map の結果イテレータが自分で未着手の
            # ジョブをキャンセルしてくれる（実測: 30行中3行で切り上がった）
            with ThreadPoolExecutor(max_workers=3) as ex:
                wavs = list(ex.map(synth, enumerate(jobs)))

            if cancel is not None and cancel.is_set():
                # 合成済みの連続した先頭部分は活かせる。全文結合なら
                # 「ここまでを保存する？」を提案する（従来は成果ゼロだった）
                done_prefix = 0
                for r in wavs:
                    if r is None:
                        break
                    done_prefix += 1
                if unit == "combine" and done_prefix > 0:
                    part = target + ".part.wav"
                    try:
                        core.concat_wavs_to_file(
                            [wavs[i][0] for i in range(done_prefix)], part,
                            gap_sec=gaps)
                    except Exception:
                        # 連結失敗（4GB超・ディスクフル等）は部分保存を諦め、
                        # 書きかけの .part を残さずキャンセル扱いにする
                        try:
                            os.remove(part)
                        except OSError:
                            pass
                        self.q.put(("synth_cancelled", done_count[0],
                                    len(jobs), 0))
                        return
                    self.q.put(("synth_partial", {
                        "part": part, "done": done_prefix, "total": len(jobs),
                        "durs": [wavs[i][1] for i in range(done_prefix)],
                        "lines": [jobs[i][0] for i in range(done_prefix)],
                        "sids": [jobs[i][1] for i in range(done_prefix)],
                        "target": target, "fmt": fmt, "srt": srt,
                        "gap": gaps[:done_prefix],
                    }))
                    return
                self.q.put(("synth_cancelled", done_count[0], len(jobs), 0))
                return
            # ここから保存フェーズ。キャンセルボタンを畳む（保存は途中で止めると
            # 壊れたファイルが残るため受け付けない）。直前の押下は下のループ先頭で拾う
            self.q.put(("synth_saving",))

            width = max(3, len(str(len(groups))))   # 999超でも名前順が崩れない
            multi = len({j[1] for j in jobs}) > 1
            # 保存フェーズは「合成中」より長いことがある（10時間の本なら結合
            # だけで数分）。従来はここで一度しか喋らず、進捗バーは100%のまま
            # 止まって見えたので、何をしているかを出し続ける
            nmark = max(len(groups), 1)
            last_say = [0.0]

            def say(pos, text, throttle=0.0):
                """pos は 0〜nmark の小数（グループ内の進み具合を含む）。
                保存は合成とは別の工程として、バーを0%から数え直す（文言も
                「つないでいます」「変換中」に変わるので工程の切り替わりが分かる）。
                保存フェーズの中では前にしか進まない。"""
                if throttle:
                    now = time.monotonic()
                    if now - last_say[0] < throttle:
                        return
                    last_say[0] = now
                self.q.put(("progress", pos, nmark, text))
            srt_count = 0
            chap_note = ""
            csv_rows = []
            for gi, idxs in enumerate(groups):
                if cancel is not None and cancel.is_set():
                    # 保存開始直前に押されたキャンセルの取りこぼし防止。
                    # 書き出し済みの gi ファイルはそのまま残す（メッセージで明示）
                    self.q.put(("synth_cancelled", done_count[0], len(jobs), gi))
                    return
                if unit == "combine":
                    out_path = target
                else:
                    # 連番だけだと中身が分からないので、本文の先頭を添える。
                    # 掛け合い（複数話者）ならファイル名だけで誰の声か分かるよう
                    # 話者名も挟む（001_ずんだもん_こんにちは.wav）
                    stem = f"{gi+1:0{width}d}"
                    if multi:
                        char = self._char_name(
                            self._speaker_label_for_id(jobs[idxs[0]][1]))
                        cs = core.filename_snippet(char, 8)
                        if cs:
                            stem += f"_{cs}"
                    snippet = core.filename_snippet(jobs[idxs[0]][0])
                    if snippet:
                        stem += f"_{snippet}"
                    out_path = os.path.join(target, f"{stem}.{fmt}")
                paths = [wavs[i][0] for i in idxs]
                durs = [wavs[i][1] for i in idxs]
                # このファイルの中の、各行の前の無音（先頭の行の前は無し）
                ggaps = [0.0] + [gaps[i] for i in idxs[1:]]
                where = (f"（{gi+1}/{len(groups)}）" if len(groups) > 1 else "")
                if len(paths) == 1:
                    say(gi + 0.5, f"{fmt.upper()}に変換中…{where}")
                    core.encode_audio_file(paths[0], out_path, fmt,
                                           self.encoders, keep_input=True)
                else:
                    # 逐次結合（メモリに全体を持たない）→ 変換。中間WAVは出力先と
                    # 同じフォルダに .part 名で作り、終わったら消す
                    part = out_path + ".part.wav"
                    try:
                        # 結合は行数ぶんかかる。バーは「今のファイル」の中の
                        # 進み具合で動かす（gi + 0〜1 の小数を渡す）
                        def cat_progress(k, total_src, _gi=gi, _w=where):
                            # 結合はこのグループの前半（0〜0.8）ぶんとして数える。
                            # 通知は0.2秒に1回まで（1万行で1万通の通知を投げると
                            # キューとUIがそれだけで詰まる）
                            frac = (k / total_src * 0.8) if total_src else 0
                            say(_gi + frac,
                                f"音声をつないでいます… {k}/{total_src}行{_w}",
                                throttle=0.2)
                        core.concat_wavs_to_file(paths, part, gap_sec=ggaps,
                                                 progress_cb=cat_progress)
                        say(gi + 0.85, f"{fmt.upper()}に変換中…{where}")
                        core.encode_audio_file(part, out_path, fmt,
                                               self.encoders)
                    finally:
                        if os.path.exists(part):
                            try:
                                os.remove(part)
                            except OSError:
                                pass
                if fmt == "m4b":
                    say(gi + 0.93, f"チャプターを埋め込んでいます…{where}")
                    chap_note = self._embed_chapters(out_path, jobs, idxs,
                                                    durs, ggaps)
                if srt:
                    say(gi + 0.97, f"字幕(SRT)を書き出しています…{where}")
                    lines = [jobs[i][0] for i in idxs]
                    srt_path = os.path.splitext(out_path)[0] + ".srt"
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(core.make_srt(lines, durs, gap_sec=ggaps))
                    srt_count += 1
                if unit != "combine":
                    # セリフ一覧CSV（動画編集ソフトでの素材整理用の対応表）
                    for i in idxs:
                        csv_rows.append((
                            os.path.basename(out_path),
                            self._char_name(
                                self._speaker_label_for_id(jobs[i][1])),
                            jobs[i][0], round(wavs[i][1], 2)))
            if csv_rows:
                self.q.put(("progress", nmark, nmark,
                            "セリフ一覧(CSV)を書き出しています…"))
                import csv as _csv
                csv_path = os.path.join(target, "セリフ一覧.csv")
                # utf-8-sig: BOM無しだとWindowsのExcelが文字化けする
                with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                    w = _csv.writer(f)
                    w.writerow(["ファイル名", "話者", "テキスト", "長さ秒"])
                    w.writerows(csv_rows)

            # 公開時に必要なクレジット表記（VOICEVOX利用規約）を完了ダイアログに渡す
            used = []
            for _t, sid, _p in jobs:
                lb = self._speaker_label_for_id(sid)
                if lb and lb not in used:
                    used.append(lb)
            credit = core.voicevox_credit(used) or "VOICEVOX:（キャラクター名）"
            note = f"（字幕{srt_count}件も保存）" if srt_count else ""
            if unit == "combine":
                msg = (f"{fmt.upper()}を1つにまとめて保存しました{note}{chap_note}:\n"
                       f"{target}")
            else:
                msg = f"{len(groups)}個の{fmt.upper()}を保存しました{note}:\n{target}"
            self.q.put(("synth_done", msg, target, credit))
        except _LineSynthError as e:
            cause = e.__cause__
            tb = "".join(traceback.format_exception(type(cause), cause,
                                                    cause.__traceback__))
            if linenos:
                self.q.put(("synth_line_error", tb, linenos[e.index],
                            jobs[e.index][0]))
            else:
                self.q.put(("error", tb))
        except Exception:
            self.q.put(("error", traceback.format_exc()))
        finally:
            core.synth_cache_protect(0.0)   # 保護解除（ここで上限へ戻すevictも走る）
            if spool:
                shutil.rmtree(spool, ignore_errors=True)

    def _partial_save_worker(self, info):
        """キャンセル時の部分保存: 連結済みの .part.wav を目的の形式で書き出す。
        ファイル名は「◯◯_途中まで.拡張子」（本来の出力と混ざらないように）。"""
        try:
            root, ext = os.path.splitext(info["target"])
            out = f"{root}_途中まで{ext}"
            core.encode_audio_file(info["part"], out, info["fmt"], self.encoders)
            chap_note = ""
            if info["fmt"] == "m4b" and mp4chapters is not None:
                try:
                    chapters, _kind = core.build_chapters(
                        info["lines"], info["durs"], gap=info["gap"])
                    if chapters:
                        mp4chapters.add_chapters(out, chapters)
                        chap_note = f"・チャプター{len(chapters)}個"
                except Exception:
                    chap_note = "・チャプター埋め込みに失敗"
            if info["srt"]:
                with open(os.path.splitext(out)[0] + ".srt", "w",
                          encoding="utf-8") as f:
                    f.write(core.make_srt(info["lines"], info["durs"],
                                          gap_sec=info["gap"]))
            used = []
            for sid in info["sids"]:
                lb = self._speaker_label_for_id(sid)
                if lb and lb not in used:
                    used.append(lb)
            credit = core.voicevox_credit(used) or "VOICEVOX:（キャラクター名）"
            self.q.put(("synth_done",
                        f"キャンセル位置まで（{info['done']}/{info['total']}行）を"
                        f"保存しました{chap_note}:\n{out}", out, credit))
        except Exception:
            self.q.put(("error", traceback.format_exc()))
        finally:
            try:
                os.remove(info["part"])
            except OSError:
                pass

    def _embed_chapters(self, out_path, jobs, idxs, durs, gap):
        """M4Bにチャプターを埋め込む（構築ロジックはGUI/CLI共有の core.build_chapters。
        章見出しがあればそこで、無い本でも約10分ごとの自動チャプター）。
        モジュールが無い・失敗した場合も音声自体はそのまま使える。
        戻り値は完了メッセージに添える短い注記。"""
        if mp4chapters is None:
            return "・チャプター埋め込み機能が見つからずスキップ"
        lines = [jobs[i][0] for i in idxs]
        chapters, kind = core.build_chapters(lines, durs, gap=gap)
        if kind == "none":
            return "・短い本のためチャプターなし"
        try:
            mp4chapters.add_chapters(out_path, chapters)
            return (f"・チャプター{len(chapters)}個" if kind == "heads"
                    else f"・約10分ごとのチャプター{len(chapters)}個（章見出しなし）")
        except Exception:
            return "・チャプター埋め込みに失敗（音声は保存済み）"

    # ---------------- 出力 ----------------
    def save_txt(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("情報", "保存するテキストがありません。")
            return
        out = filedialog.asksaveasfilename(
            title="テキストを保存", defaultextension=".txt",
            filetypes=[("テキスト", "*.txt")], initialfile="voicevox_text.txt")
        if not out:
            return
        # 行頭の字下げ（段落の頭の全角スペース）は、VOICEVOX ではブロックの頭の余計な空白に
        # なるだけなので外す（本文の段落の見分けには使うが、VOICEVOX には要らない）
        # 読み方の直し（オンのとき）も入れる。VOICEVOX はこの文をそのまま読むので
        text = "\n".join(core.speech_text(ln.strip(" \t　")) for ln in text.split("\n"))
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        messagebox.showinfo("保存完了",
                            f"保存しました:\n{out}\n\nVOICEVOXの「ファイル→テキスト読み込み」で読み込めます。")

    def copy_clip(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("本文をコピーしました。VOICEVOX のエディタなどに貼り付けられます"
                            f"（{'⌘' if core.IS_MAC else 'Ctrl+'}V）。")

    def save_vvproj(self):
        # 再生・処理中のガード（従来は無く、連続再生中でも押せてしまっていた）
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("情報", "保存するテキストがありません。")
            return
        default = self._current_speaker()
        if default is None:
            messagebox.showinfo("情報", "声を選んでください（先に VOICEVOX につないでください）。")
            return
        # 合成前と同じく@タグのタイプミスを事前に指摘（プロジェクトに混入させない）
        if not self._confirm_speaker_tags(
                "その行は「@名前:」も含めて、いつもの声のブロックになります。続けますか？"):
            return
        # 数値欄は _set_busy より前（UIスレッド）で読む
        try:
            v = self._voice_params()
        except tk.TclError:
            messagebox.showwarning(
                "数値の確認", "速さ・高さ・抑揚・音量に数字を入れてください。")
            return
        out = filedialog.asksaveasfilename(
            title="VOICEVOXプロジェクトを保存", defaultextension=".vvproj",
            filetypes=[("VOICEVOXプロジェクト", "*.vvproj")],
            initialfile="voicevox_project.vvproj")
        if not out:
            return
        entries = []
        for ln in text.split("\n"):
            if not ln.strip() or core.is_memo_line(ln):
                continue   # メモ行（行頭#）は音声生成と同様に含めない
            spoken, sp = self._resolve_line(ln.strip())
            if spoken.strip():
                # 読み方の直し（オンのとき）を入れた文で書く。VOICEVOX で開いたとき、
                # エディタが読み直してもこのアプリと同じ読みになるように
                entries.append((core.speech_text(spoken), sp[1] if sp else None,
                                sp[2] if sp else None))
        if not entries:
            messagebox.showinfo("情報", "書き出せる行がありませんでした。")
            return
        # 調整値がすべて既定のままなら、引き継ぐ情報は無い（アクセントはエディタが
        # 開くときに自分で取り直す）。エンジンへ一度も問い合わせず、これまでと
        # まったく同じ最小構成をその場で書く＝待ち時間ゼロ
        need_query = (self.vvproj_query_var.get()
                      and (v["speed"] != 1.0 or v["pitch"] != 0.0
                           or v["intonation"] != 1.0 or v["volume"] != 1.0))
        if not need_query:
            payload = core.make_vvproj(entries, default[1], default[2],
                                       engine_id=self._engine_id)
            self._finish_vvproj(out, payload, 0, entries, default)
            return
        # 行数ぶんエンジンへ問い合わせるので、ボタンをキャンセルに変えて裏で走らせる
        self._vvproj_cancel = threading.Event()
        self._set_busy(True)
        self.vvproj_btn.config(text="× キャンセル", command=self.cancel_vvproj,
                               state="normal")
        self.progress.config(mode="determinate", maximum=len(entries), value=0)
        self.status_var.set(f"調整値を取得中… 0/{len(entries)}行")
        self._spawn(self._vvproj_worker, (entries, default, out, v))

    def cancel_vvproj(self):
        """.vvproj の書き出しを中断する（ファイルは作らない）。"""
        if self._vvproj_cancel is not None:
            self._vvproj_cancel.set()
            self.vvproj_btn.config(state="disabled")
            self.status_var.set("キャンセルしています...")

    def _vvproj_restore_button(self):
        """書き出しの完了/キャンセル/エラー後にボタンを元へ戻す。"""
        self._vvproj_cancel = None
        # cancel_vvproj が state を disabled にしているので、ここで戻す。
        # 戻さないと、次に接続状態が変わるまでボタンが灰色のままになる
        self.vvproj_btn.config(text="プロジェクト保存(.vvproj)",
                               command=self.save_vvproj,
                               state=("normal" if self._engine_ready()
                                      else "disabled"))

    def _vvproj_worker(self, entries, default, out, v):
        """各行の audio_query を集めて .vvproj の中身を作る（ワーカースレッド）。
        取れなかった行は query 無しにして続ける＝その行だけ既定値で開かれる。"""
        from concurrent.futures import ThreadPoolExecutor
        cancel = self._vvproj_cancel
        base = self.base_url
        results = [None] * len(entries)
        memo, lock = {}, threading.Lock()

        def one(i):
            text, sid, _uid = entries[i]
            style = default[1] if sid is None else sid
            key = (text, style)   # 同じ文でも話者が違えば読みは別物
            with lock:
                if key in memo:
                    return i, memo[key], False
            for attempt in range(3):
                if cancel.is_set():
                    return i, None, False
                try:
                    raw = core.vv_audio_query(base, text, style, read_fix=False)
                    q = core.normalize_vvproj_query(
                        raw, v["speed"], v["pitch"], v["intonation"],
                        v["volume"])
                    with lock:
                        memo[key] = q
                    return i, q, False
                except Exception:
                    if attempt < 2:
                        time.sleep(0.5 * (attempt + 1))
            return i, None, True

        nfail = 0
        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                for done, (i, q, failed) in enumerate(
                        ex.map(one, range(len(entries))), 1):
                    results[i] = q
                    if failed:
                        nfail += 1
                    if done % 5 == 0 or done == len(entries):
                        self.q.put(("vvproj_progress", done, len(entries)))
            if cancel.is_set():
                self.q.put(("vvproj_cancelled",))
                return
            # 全滅＝エンジンが落ちた等。調整値は諦め、確実に開ける形で保存する
            if nfail >= len(entries):
                payload = core.make_vvproj(entries, default[1], default[2],
                                           engine_id=self._engine_id)
            else:
                withq = [(e[0], e[1], e[2], results[i])
                         for i, e in enumerate(entries)]
                payload = core.make_vvproj(withq, default[1], default[2],
                                           engine_id=self._engine_id)
            self.q.put(("vvproj_done", out, payload, nfail, entries, default))
        except Exception:
            self.q.put(("vvproj_error", traceback.format_exc()))

    def _finish_vvproj(self, out, payload, nfail, entries, default):
        """書き出す直前に自己点検し、問題があれば調整値を捨てた最小構成
        （これまでと同じ＝実績のある形）へ落としてから保存する。"""
        note = ""
        try:
            core.validate_vvproj(payload)
        except ValueError as e:
            payload = core.make_vvproj(entries, default[1], default[2],
                                       engine_id=self._engine_id)
            try:
                core.validate_vvproj(payload)
            except ValueError as e2:
                messagebox.showerror(
                    "保存できませんでした",
                    f"プロジェクトの形に問題があります:\n{e2}\n\n"
                    "壊れたファイルを書き出さないため、保存を中止しました。")
                return
            note += ("\n\n※調整値の形に問題があったため、"
                     "調整値なしで保存しました（開くことはできます）。")
        try:
            _write_atomic(out, payload)
        except Exception as e:
            messagebox.showerror("保存できませんでした",
                                 f"書き込みに失敗しました:\n{e}")
            return
        if nfail:
            note += (f"\n\n※うち{nfail}行は調整値を取得できませんでした"
                     "（その行だけ既定値で開かれます）。")
        messagebox.showinfo("保存完了",
                            f"保存しました:\n{out}\n\n"
                            "VOICEVOXの「ファイル → プロジェクト読み込み」で開くと、\n"
                            "1行 = 1ブロックとして読み込まれ、行ごとに声や\n"
                            "イントネーションを調整できます。" + note)

    # ---------------- クリップボード画像OCR ----------------
    def clipboard_ocr(self):
        """「コピーした画像を読む」: コピーしてある画像の文字を読み取って本文に足す。
        コピーしてあるのがファイルなら、① の一覧に入れる。画面に出す言葉には
        「クリップボード」「OCR」を使わない（ふだんの言葉ではなく、伝わらない）。"""
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        try:
            # PILは重いので起動時に読まず、初めて使うここで読み込む（起動短縮）
            from PIL import ImageGrab
            data = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("コピーした画像を読む",
                                 f"コピーした画像を取り出せませんでした: {e}")
            return
        if data is None:
            messagebox.showinfo("コピーした画像を読む",
                                "コピーされた画像が見つかりませんでした。\n"
                                "先に、読みたい画像をコピーしてから押してください。\n"
                                "（画面に出ている文字なら、左の「スクリーンショット」で"
                                "囲むと、そのまま読めます）")
            return
        if isinstance(data, list):  # ファイルがコピーされていた場合は取り込み
            added = 0
            for p in data:
                if isinstance(p, str):
                    before = len(self.files)
                    self._add_path(p)
                    added += len(self.files) - before
            self.status_var.set(f"{added}件追加しました（コピーしたファイル）")
            return
        clean_opts = self._gather_clean_opts()
        self._set_busy(True)
        self.status_var.set("コピーした画像の文字を読み取っています...")
        self._spawn(self._clipboard_worker,
                    (data, self.pre_var.get(), clean_opts,
                     self.fixconf_var.get(), self.denoise_var.get()))

    def _clipboard_worker(self, img, preprocess, clean_opts,
                          fix_confusables=False, denoise=True, source="clip"):
        """画像1枚をOCRして本文用に整える。source は結果の受け取り方
        （"clip"=コピーした画像を読む / "screen"=スクリーンショット＝読んだらすぐ読み上げ）。"""
        try:
            report = {}
            # OCRが済めばPNG（＝クリップボード画像のコピー）は不要。%TEMP%に残さない
            with tempfile.TemporaryDirectory(prefix="t2v_clip_") as tmpdir:
                png = os.path.join(tmpdir, "clip.png")
                # すぐ OCR に渡して消す一時ファイルなので、圧縮は最小（Retina の
                # 大きな画面だと、既定の圧縮だけで待ちが目に見えて延びる）
                core.prepare_for_ocr(img, enable=preprocess).save(png, compress_level=1)
                notices = []
                res = core.run_ocr([png], strip_labels=denoise, notices=notices)
                raw = res.get(png, "")
                # 低品質（写真の影・ムラ・横倒し）なら前処理を変えて再OCR（macのみ・自動）。
                # 画面から読んだ文字は影も横倒しも無く、短い範囲だと「低品質」と見なされて
                # 最大3回読み直すだけ（待ちが最大4倍）になるので、写真のときだけ行う
                if not source.startswith("screen"):
                    raw = core.ocr_retry_if_poor(raw, img, tmpdir, strip_labels=denoise)
            if fix_confusables and raw:
                fixed = core.fix_ocr_confusables(raw)
                if fixed != raw:
                    report["confusables"] = [
                        (b, a) for b, a in zip(raw.split("\n"), fixed.split("\n"))
                        if b != a]
                raw = fixed
            if denoise and raw:
                # クリップボードは全文OCR由来なので denoise をここで適用する
                removed = core.denoise_removed_lines(raw)
                if removed:
                    report["removed"] = removed
                raw = core.denoise_capture(raw)
            cleaned = core.clean_text(raw, **clean_opts)
            warnings = ([core.OCR_ENGLISH_MISSING_MSG]
                        if "english_ocr_missing" in notices else [])
            if "strong_ocr_failed" in notices:
                warnings.append(core.OCR_STRONG_FAILED_MSG)
            self.q.put(("clip_done", cleaned, report, warnings, source))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    # ---------------- 画面の範囲を選んで読み上げ ----------------
    SCREEN_HIDE_MS = 300   # 自分の窓が消えきるのを待ってから撮る（撮り込み防止）

    def screen_read(self):
        """「スクリーンショット」（以前の名前は「画面から読む」）。自分の窓をいったん隠して画面を撮り、その写真の上で
        読みたい所をドラッグで囲んでもらう。囲んだ所をOCRして、すぐ読み上げる。
        撮った“写真”の上で選ぶので、動画や自動で変わる画面でも選んだ瞬間の文字が読める。"""
        if self._previewing and not self.busy:
            # 読み上げ中でも止めて次へ（ページをめくってすぐ次を読みたいので待たせない）
            self._after_stopping(self.screen_read)
            return
        if self.busy:
            self.status_var.set("処理の実行中です。完了してからお試しください。")
            return
        if self._screen_win is not None:
            return   # 範囲選択の最中（ショートカットの連打など）
        self._screen_win = True
        self.withdraw()
        core.strong_ocr_prewarm()   # 範囲を選んでいるあいだに、読み取りの部品を起こしておく
        self._tick("screen", self.SCREEN_HIDE_MS, self._screen_grab)

    def _after_stopping(self, then, tries=0):
        """読み上げを止めてから then を呼ぶ。止まりきるまで最大4秒ほど待つ。"""
        if self._previewing and not self.busy:
            if tries == 0:
                self.status_var.set("読み上げを止めて、次を読みます…")
                self.stop_playall()
            if tries < 40:
                self._tick("screen_wait", 100,
                           lambda: self._after_stopping(then, tries + 1))
                return
            # 止まりきらなかった。何度も止め直さず、押し直してもらう
            self._ticks.pop("screen_wait", None)
            self.status_var.set("読み上げが止まりきりませんでした。■停止のあと、もう一度押してください。")
            return
        self._ticks.pop("screen_wait", None)
        then()

    def _ensure_again_btn(self):
        """「同じ範囲を読む」ボタンを、はじめて範囲を囲んだときに出す。"""
        if self.screen_again_btn is not None:
            return
        mod = "⌘" if core.IS_MAC else "Ctrl+"
        b = ttk.Button(self._screen_btns, text="同じ範囲を読む",
                       command=self.screen_read_again)
        b.grid(row=0, column=0, sticky="ew", pady=2)
        _Tooltip(b, "前に囲んだのと同じ場所を、囲み直さずにもう一度読みます"
                    f"（{mod}Shift+R）。\n電子書籍でページをめくったあとに押すと、"
                    "次のページをそのまま読み上げます。")
        self.screen_again_btn = b
        if self.busy:
            b.config(state="disabled")
        cb = ttk.Checkbutton(self._screen_btns, text="めくったら自動で読む",
                             variable=self.auto_page_var,
                             command=self._toggle_auto_page)
        cb.grid(row=0, column=1, sticky="w", padx=(GAPX, 0), pady=2)
        _Tooltip(cb, "オンにすると、囲んだ場所を見張って、ページが変わったら自動で"
                     "読み取って読み上げます。\n電子書籍を全画面にして、ページを"
                     "めくるだけで読み進められます（このアプリは最小化してOK）。\n"
                     "このアプリの窓が囲んだ場所に重なっていると動きません。")
        self.auto_page_cb = cb

    def screen_read_again(self):
        """「同じ範囲を読む」。前に囲んだ場所を、選び直さずに撮って読む。
        電子書籍でページをめくるたびに押す使い方を想定（囲む手間を毎回かけない）。"""
        if self._screen_last is None:
            self.screen_read()   # まだ一度も囲んでいない → 囲むところから
            return
        if self._previewing and not self.busy:
            self._after_stopping(self.screen_read_again)
            return
        if self.busy:
            self.status_var.set("処理の実行中です。完了してからお試しください。")
            return
        if self._screen_win is not None:
            return
        self._screen_win = True
        self.withdraw()
        core.strong_ocr_prewarm()
        self._tick("screen", self.SCREEN_HIDE_MS, self._screen_grab_again)

    def _screen_grab_again(self):
        self._ticks.pop("screen", None)
        try:
            region, all_screens = self._screen_region()
            from PIL import ImageGrab
            shot = (ImageGrab.grab(all_screens=True) if all_screens
                    else ImageGrab.grab())
        except Exception as e:
            self._close_screen_picker()
            messagebox.showerror("同じ範囲を読む", f"画面を撮れませんでした: {e}")
            return
        p0, p1 = self._screen_last
        box = core.screen_selection_box_multi(p0, p1, self._screen_layout(shot.size), shot.size)
        if box is None:
            # モニタを外した等で、前の場所が画面の外になった → 囲み直してもらう
            self._screen_last = None
            self._close_screen_picker()
            self._set_busy(False)
            self.status_var.set("前の範囲が画面の外になりました。「スクリーンショット」で囲み直してください。")
            return
        self._screen_selected(shot.crop(box))

    def _screen_region(self):
        """撮る範囲 (x, y, 幅, 高さ) と、全モニタを撮るか。
        Windows は全モニタ（本がサブモニタにあることも多い）。Mac はメイン画面
        （Pillowが撮れるのがメイン画面だけのため）。"""
        if core.IS_WIN:
            return self._virtual_screen(), True
        return (0, 0, self.winfo_screenwidth(), self.winfo_screenheight()), False

    def _screen_pairs(self):
        """（Windows）画面ごとの、アプリから見た範囲と本当の画素での範囲（_win_monitor_pairs）。
        取れなければ None。Tk には触らない。"""
        if not core.IS_WIN:
            return None
        try:
            return _win_monitor_pairs()
        except Exception:
            return None

    def _screen_layout(self, image_size):
        """撮った画像と、アプリから見た画面の対応（_screen_layout_from）。倍率のちがう画面が
        混ざるときだけ画面ごとに分かれ、ほかは全体を1枚として扱う。"""
        region, _all = self._screen_region()
        return _screen_layout_from(region, self._screen_pairs(), image_size)

    def _screen_grab(self):
        self._ticks.pop("screen", None)
        if isinstance(self._screen_win, tk.Toplevel):
            return   # もう選択中（二重に開くと下の窓が操作できないまま残る）
        try:
            region, all_screens = self._screen_region()
            # PILは重いので起動時に読まず、初めて使うここで読み込む（起動短縮）
            from PIL import ImageGrab
            shot = (ImageGrab.grab(all_screens=True) if all_screens
                    else ImageGrab.grab())
            self._open_screen_picker(shot, region, self._screen_layout(shot.size))
        except Exception as e:
            self._close_screen_picker()
            hint = ("\n\nMacでは「システム設定 → プライバシーとセキュリティ → 画面収録」で、"
                    "このアプリを起動したもの（ターミナル/Python）を許可してください。"
                    if core.IS_MAC else "")
            messagebox.showerror("スクリーンショット", f"画面を撮れませんでした: {e}{hint}")

    def _open_screen_picker(self, shot, region, layout=None):
        """撮った画面を暗めに映し、ドラッグで範囲を選ばせる窓を出す。
        layout（core.screen_selection_box_multi と同じ形）が画面ごとに分かれていれば、
        窓も画面ごとに1枚ずつ出す。倍率のちがう画面が混ざっていると（250% と 125% の2台
        など）、アプリから見た座標と撮った画像の画素は、画面ごとに別の比でずれる。全部の
        画面を1枚の窓で覆っていた前の作りでは、写真が引き伸ばされて本物の画面とずれ、
        囲んだ所とちがう場所を読んでいた。"""
        from PIL import Image, ImageEnhance, ImageTk
        if not layout:
            rx, ry, rw, rh = region
            layout = [(rx, ry, rw, rh, 0, 0, shot.size[0], shot.size[1])]
        # 画像のコピーは最小限に（多画面・4Kでは1枚で数十MBになる。古いPCでも軽く）
        base = shot if shot.mode == "RGB" else shot.convert("RGB")
        tip = ("読みたい所をドラッグで囲んでください　"
               + ("（やめる：右クリック か Ctrl+クリック）" if core.IS_MAC
                  else "（やめる：Esc か 右クリック）"))
        # 案内はメイン画面（座標0,0の画面）の上の中央に出す。その点のある窓に置く
        tip_at = (self.winfo_screenwidth() // 2, 24)
        # p0 は囲み始めた点、pane はその窓。lit_t は明るく見せる処理の間引き用（古いPCでも軽く）
        st = {"p0": None, "pane": None, "lit_t": 0.0}
        panes = []
        first = None
        for vx, vy, vw, vh, ix, iy, iw, ih in layout:
            view = base
            if (ix, iy, iw, ih) != (0, 0, base.size[0], base.size[1]):
                view = base.crop((ix, iy, ix + iw, iy + ih))   # この画面に写っている所だけ
            if view.size != (vw, vh):
                view = view.resize((vw, vh), Image.BILINEAR)   # 高解像度画面は縮めて映す
            # 暗くして「いまは選ぶ時間」と分かるようにする。選んだ所だけ元の明るさで見せる
            dim = ImageEnhance.Brightness(view).enhance(0.5)
            # 2枚目からは1枚目の子にする（1枚目を閉じれば、全部いっしょに閉じる）
            win = tk.Toplevel(self if first is None else first)
            if first is None:
                first = win
                self._screen_win = win
            win.overrideredirect(True)
            # 位置と大きさは、-topmost より先に決めること。-topmost を言うと Tk はその場で
            # 窓を作る。先に作ってから動かすと、倍率のちがう画面へは正しく動かない
            # （Windows は、動かす前にいた画面の倍率で座標を読む。2026-11-13 に実測:
            # メイン画面で作った窓を 125% の画面の座標へ動かすと、画面の外に出た）
            win.geometry(f"{vw}x{vh}+{vx}+{vy}")
            try:
                win.attributes("-topmost", True)
            except tk.TclError:
                pass
            cv = tk.Canvas(win, width=vw, height=vh, highlightthickness=0,
                           bd=0, cursor="crosshair", bg="black")
            cv.pack(fill="both", expand=True)
            # 画像は、どの窓のものかを必ず渡す（渡さないと最初に作った Tk のものになり、
            # 窓が複数あると「image does not exist」で映らないことがある）
            photo = ImageTk.PhotoImage(dim, master=win)
            panes.append({
                "win": win, "cv": cv, "v": (vx, vy, vw, vh), "bright": view,
                "photo": photo, "lit": None,   # PhotoImage はGC防止に保持
                "bg": cv.create_image(0, 0, image=photo, anchor="nw"),
                "lit_item": cv.create_image(0, 0, anchor="nw"),
                "rect": cv.create_rectangle(0, 0, 0, 0, outline="#ffcc33", width=2,
                                            state="hidden"),
                "tip": None})
        tip_pane = next((q for q in panes
                         if q["v"][0] <= tip_at[0] < q["v"][0] + q["v"][2]
                         and q["v"][1] <= tip_at[1] < q["v"][1] + q["v"][3]), panes[0])
        tip_pane["tip"] = (
            tip_pane["cv"].create_rectangle(0, 0, 0, 0, fill="#222222", outline="#ffcc33"),
            tip_pane["cv"].create_text(0, 0, text=tip, fill="white",
                                       font=self._heading_font, anchor="n"))

        def origin(pane):
            # 窓が本当に置かれた位置からずれを求める（Macはメニューバーの下へ
            # ずらされることがある）。写真は画面と同じ位置に来るよう逆にずらす
            return pane["win"].winfo_rootx(), pane["win"].winfo_rooty()

        def place(pane, _e=None):
            cv = pane["cv"]
            vx, vy, vw, vh = pane["v"]
            ox, oy = origin(pane)
            cv.coords(pane["bg"], vx - ox, vy - oy)
            if pane["tip"]:
                tip_bg, tip_item = pane["tip"]
                cx, cy = tip_at[0] - ox, tip_at[1] - oy
                if not (vx <= tip_at[0] < vx + vw and vy <= tip_at[1] < vy + vh):
                    cx, cy = vx + vw // 2 - ox, vy + 24 - oy   # メイン画面の無い並び: この窓の上の中央
                cv.coords(tip_item, cx, cy + 8)
                x0, y0, x1, y1 = cv.bbox(tip_item)
                cv.coords(tip_bg, x0 - 12, y0 - 8, x1 + 12, y1 + 8)
                cv.tag_raise(tip_bg)
                cv.tag_raise(tip_item)

        def show_lit(pane, box_canvas):
            # 囲んだ所だけ元の明るさで重ねる（どこを読むかが一目で分かる）
            cv = pane["cv"]
            vx, vy, vw, vh = pane["v"]
            ox, oy = origin(pane)
            x0, y0, x1, y1 = box_canvas
            sx0, sy0 = x0 + ox - vx, y0 + oy - vy
            sx1, sy1 = x1 + ox - vx, y1 + oy - vy
            sx0, sy0 = max(0, sx0), max(0, sy0)
            sx1, sy1 = min(vw, sx1), min(vh, sy1)
            if sx1 - sx0 < 1 or sy1 - sy0 < 1:
                cv.itemconfigure(pane["lit_item"], image="")
                return
            pane["lit"] = ImageTk.PhotoImage(pane["bright"].crop((sx0, sy0, sx1, sy1)),
                                             master=pane["win"])
            cv.itemconfigure(pane["lit_item"], image=pane["lit"])
            cv.coords(pane["lit_item"], sx0 + vx - ox, sy0 + vy - oy)

        def press(e, pane):
            st["p0"] = (e.x_root, e.y_root, e.x, e.y)
            st["pane"] = pane
            pane["cv"].coords(pane["rect"], e.x, e.y, e.x, e.y)
            pane["cv"].itemconfigure(pane["rect"], state="normal")

        def motion(e, pane):
            if st["p0"] is None or st["pane"] is not pane:
                return
            cv = pane["cv"]
            _, _, x0, y0 = st["p0"]
            box = (min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y))
            cv.coords(pane["rect"], *box)
            now = time.monotonic()
            if now - st["lit_t"] >= 0.04:   # 画像の切り出しは毎回だと重いので間引く
                st["lit_t"] = now
                show_lit(pane, box)
            cv.tag_raise(pane["rect"])

        def release(e, pane):
            if st["p0"] is None or st["pane"] is not pane:
                return
            cv = pane["cv"]
            xr0, yr0, _, _ = st["p0"]
            st["p0"] = st["pane"] = None
            box = core.screen_selection_box_multi((xr0, yr0), (e.x_root, e.y_root),
                                                  layout, shot.size)
            if box is None:
                # ほぼクリックだけ＝囲めていない。やめずに選び直してもらう
                cv.itemconfigure(pane["rect"], state="hidden")
                cv.itemconfigure(pane["lit_item"], image="")
                return
            try:
                img = shot.crop(box)
            except Exception:
                # 切り出しに失敗しても（メモリ不足など）隠した窓は必ず戻す
                self._screen_selected(None)
                self.status_var.set("囲んだ所を切り出せませんでした。もう一度お試しください。")
                return
            # 「同じ範囲を読む」用に、画像の画素ではなく画面の座標で覚えておく
            # （次に撮る画像の大きさが変わっても、同じ場所を切り出せるように）
            self._screen_last = ((xr0, yr0), (e.x_root, e.y_root))
            self._screen_selected(img)
            # ボタンは窓を元に戻したあとで作る。隠した窓にウィジェットを足してから
            # deiconify すると、macOS の Tk が落ちる（CI で Bus error を確認）
            self.after_idle(self._ensure_again_btn)

        def cancel(_e=None):
            self._screen_selected(None)
            return "break"

        for pane in panes:
            cv, win = pane["cv"], pane["win"]
            cv.bind("<ButtonPress-1>", lambda e, q=pane: press(e, q))
            cv.bind("<B1-Motion>", lambda e, q=pane: motion(e, q))
            cv.bind("<ButtonRelease-1>", lambda e, q=pane: release(e, q))
            # 右クリック（Macは Button-2 / Ctrl+クリック、Win は Button-3）でやめる
            for seq in ("<Button-2>", "<Button-3>") + (
                    ("<Control-Button-1>",) if core.IS_MAC else ()):
                cv.bind(seq, cancel)
            win.bind("<Escape>", cancel)
            cv.bind("<Escape>", cancel)
            # Alt+F4 などで閉じられたときも「やめた」扱いにする。何もしないと窓だけが
            # 壊れ、隠した本体の窓が戻らない（アプリが画面から消えたように見える）
            win.protocol("WM_DELETE_WINDOW", cancel)
            win.bind("<Configure>", lambda e, q=pane: place(q))
            place(pane)

        def on_destroy(e):
            if e.widget is first and self._screen_win is first:
                self._close_screen_picker()
        first.bind("<Destroy>", on_destroy, add="+")

        def grab_keys(_e=None):
            # Esc を受け取れるよう、枠なし窓にも入力を向ける（映った後でないと効かない）。
            # macOS の Tk は枠なし窓への focus_force で落ちることがある（CI で再現）ので、
            # Mac では強制しない（やめるのは右クリック／Ctrl+クリックでもできる）
            try:
                if not core.IS_MAC:
                    first.focus_force()
                panes[0]["cv"].focus_set()
            except tk.TclError:
                pass
        first.bind("<Map>", grab_keys)
        grab_keys()

    def _screen_read_aloud(self, first_line, nchars):
        """画面から読んだ文章（first_line 行目から最後まで）をそのまま読み上げる。
        VOICEVOXにつながっていなければ、ダイアログで止めずに本文へ入れるだけにする。"""
        try:
            self.text.mark_set("insert", f"{first_line}.0")
            self.text.see(f"{first_line}.0")
        except tk.TclError:
            pass
        if (self._engine_ready() and self._current_speaker() is not None
                and core.can_play()):
            self.play_from_cursor()
            self._screen_reading = self._previewing   # 読み終えたら「次のページ」を案内する
        else:
            self._schedule_prefetch()
            self.status_var.set(
                f"画面の文字を読み取りました（{nchars}文字を追記）。"
                "VOICEVOXにつなぐと、読み取ってすぐ読み上げます。")

    def _close_screen_picker(self):
        """範囲選択の窓を閉じて、隠していた自分の窓を戻す。"""
        win, self._screen_win = self._screen_win, None
        if isinstance(win, tk.Toplevel):
            try:
                win.destroy()
            except tk.TclError:
                pass
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    def _screen_selected(self, img):
        """囲み終わった（img）かやめた（None）。窓を戻し、囲んだ所をOCRへ回す。"""
        self._close_screen_picker()
        if img is None:
            self.status_var.set("スクリーンショットをやめました。")
            return
        self._auto_pending = None
        try:
            self._auto_sig_read = core.page_signature(img)   # 自動めくりで同じページを読まない
        except Exception:
            self._auto_sig_read = None
        self._screen_start_ocr(img, "screen")

    def _screen_start_ocr(self, img, source):
        """囲んだ所の画像をOCRへ回す（手動の「スクリーンショット」と自動めくり読みで共通）。"""
        clean_opts = self._gather_clean_opts()
        self._set_busy(True)
        self.status_var.set("ページが変わったので読み取っています…" if source == "screen_auto"
                            else "囲んだ所の文字を読み取っています…")
        # 自分で囲んだ範囲は「ここを読んで」という指定なので、映像内ラベル・時刻などの
        # ノイズ除去（denoise）はかけない。全面スクショ向けの除去をかけると、正しく
        # 読めた短い行（「html」「2026-09-24」など）まで捨ててしまう（OCRベンチで確認）
        self._spawn(self._clipboard_worker,
                    (img, self.pre_var.get(), clean_opts,
                     self.fixconf_var.get(), False, source))

    # ---------------- 自動めくり読み ----------------
    AUTO_PAGE_MS = 1200   # 見張る間隔（Macは撮影が重いので長めにしない。古いPCでも軽く）

    def _toggle_auto_page(self):
        self._auto_pending = None
        self._auto_warned = False
        h = self._ticks.pop("auto_page", None)
        if h is not None:
            try:
                self.after_cancel(h)
            except tk.TclError:
                pass
        if self.auto_page_var.get():
            self.status_var.set("ページが変わったら自動で読みます（このアプリは最小化してもOK）。")
            self._tick("auto_page", self.AUTO_PAGE_MS, self._auto_page_tick)
        else:
            self.status_var.set("自動めくり読みを止めました。")

    def _app_covers_region(self):
        """このアプリの窓が、囲んだ範囲に重なっているか（重なると自分を撮ってしまう）。"""
        if self._screen_last is None:
            return False
        try:
            if self.state() not in ("normal", "zoomed"):
                return False   # 最小化中・隠れている
            ax, ay = self.winfo_rootx(), self.winfo_rooty()
            aw, ah = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return False
        (x0, y0), (x1, y1) = self._screen_last
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        return not (ax + aw <= left or right <= ax or ay + ah <= top or bottom <= ay)

    def _auto_can_read(self):
        return (self.auto_page_var.get() and self._screen_last is not None
                and not self.busy and not self._previewing
                and self._screen_win is None)

    def _auto_page_tick(self):
        self._ticks.pop("auto_page", None)
        if not self.auto_page_var.get():
            return
        try:
            if self._auto_can_read() and not self._auto_grabbing:
                if self._app_covers_region():
                    if not self._auto_warned:
                        self._auto_warned = True
                        self.status_var.set("このアプリの窓が読む場所に重なっています。窓をずらすか"
                                            "最小化すると、自動めくり読みが動きます。")
                else:
                    self._auto_warned = False
                    region, all_screens = self._screen_region()
                    self._auto_grabbing = True
                    threading.Thread(target=self._auto_page_worker,
                                     args=(region, all_screens, self._screen_last,
                                           self._screen_pairs()),
                                     daemon=True).start()
        finally:
            self._tick("auto_page", self.AUTO_PAGE_MS, self._auto_page_tick)

    def _auto_page_worker(self, region, all_screens, last, pairs=None):
        """（別スレッド）囲んだ範囲だけを撮って UI スレッドへ渡す。Tk には触らない。
        pairs は画面ごとの対応（_screen_pairs。UI スレッドで取っておいたもの）。"""
        img = None
        try:
            from PIL import ImageGrab
            shot = (ImageGrab.grab(all_screens=True) if all_screens
                    else ImageGrab.grab())
            box = core.screen_selection_box_multi(
                last[0], last[1], _screen_layout_from(region, pairs, shot.size), shot.size)
            if box is not None:
                img = shot.crop(box)
        except Exception:
            img = None
        self.q.put(("auto_page_shot", img))

    def _auto_page_step(self, img):
        """撮った範囲を、前に読んだページと比べる。変わって、めくりの動きが止まったら読む。"""
        if img is None or not self._auto_can_read():
            return
        sig = core.page_signature(img)
        if (self._auto_sig_read is not None
                and core.signature_diff(sig, self._auto_sig_read) < core.PAGE_CHANGE_DIFF):
            self._auto_pending = None   # 同じページのまま
            return
        if (self._auto_pending is None
                or core.signature_diff(sig, self._auto_pending) >= core.PAGE_STABLE_DIFF):
            self._auto_pending = sig    # 変わり始めた。めくりの動きが止まるのを待つ
            return
        self._auto_pending = None
        self._auto_sig_read = sig
        self._screen_start_ocr(img, "screen_auto")

    # ---------------- ユーザー辞書（読み方の登録） ----------------
    def open_dict_dialog(self):
        if self._dict_win is not None and self._dict_win.winfo_exists():
            self._dict_win.lift()
            return
        win = tk.Toplevel(self)
        win.title("読み方辞書（VOICEVOXユーザー辞書）")
        # 表示倍率（125% など）に合わせて広げる。画素のままだと、入力欄の行の右端
        # （「優先」の数字の欄）と、ボタンの行の右の一言が切れていた
        win.geometry(f"{_px(800)}x{_px(520)}")
        self._place_dialog(win)
        self._dict_win = win

        fix = ttk.Frame(win)
        fix.pack(fill="x", padx=8, pady=(8, 0))
        fix_cb = ttk.Checkbutton(fix, text="数字・日付・記号の読み方を直す（おすすめ）",
                                 variable=self.read_fix_var)
        fix_cb.pack(anchor="w")
        _Tooltip(fix_cb, "VOICEVOX が読み間違える書き方を、読み上げる前にこのアプリが直します。\n"
                         "本文の字は変わりません。連続再生・試聴・音声の保存・ふりがなと、\n"
                         "VOICEVOX 用の保存（.txt・.vvproj）に効きます。\n"
                         "例: 25℃→25度、10:30→10時30分、2026/11/7→2026年11月7日、\n"
                         "10〜20人→10から20人、-5℃→マイナス5度、100m→100メートル、草www→草")
        ttk.Label(fix, text="例: 25℃→25度、10:30→10時30分、2026/11/7→2026年11月7日"
                            "（本文の字は変わりません）",
                  style="Sub.TLabel").pack(anchor="w", padx=(GAPX * 3, 0))
        ttk.Label(win, text="固有名詞などの読み間違いを登録できます。"
                            "登録した読みはVOICEVOX全体で使われます。").pack(anchor="w", padx=8, pady=(8, 2))

        cols = ("surface", "pron", "accent", "wtype", "prio")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=10)
        tree.heading("surface", text="単語")
        tree.heading("pron", text="読み（カタカナ）")
        tree.heading("accent", text="下がる所")
        tree.heading("wtype", text="種類")
        tree.heading("prio", text="優先")
        tree.column("surface", width=150)
        tree.column("pron", width=180)
        tree.column("accent", width=80, anchor="center")
        tree.column("wtype", width=80, anchor="center")
        tree.column("prio", width=60, anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=4)
        tree.bind("<Double-1>", self._dict_edit_selected)
        self._dict_tree = tree
        # VOICEVOXエディタ側で辞書を直してからこの窓に戻ってきたときに追いつく
        # （ポーリングはせず、窓が前面に来たときだけ読み直す）
        self._dict_refreshed_at = 0.0
        win.bind("<FocusIn>", self._dict_focus_refresh, add="+")

        form = ttk.Frame(win); form.pack(fill="x", padx=8, pady=4)
        ttk.Label(form, text="単語:").pack(side="left")
        self._dict_surface = tk.StringVar()
        # 単語欄の内容が変わったら、その単語が一覧にあるか見て品詞・優先度を
        # 合わせる。こうしないと「本文を右クリック→辞書に登録」の経路で
        # フォームの既定値（固有名詞・5）が既存の設定を上書きしてしまう。
        # 画面に出ている値がそのまま適用される、を常に成り立たせるための仕掛け
        self._dict_surface.trace_add("write", self._dict_sync_form_to_word)
        ttk.Entry(form, textvariable=self._dict_surface, width=14).pack(side="left", padx=2)
        ttk.Label(form, text="読み:").pack(side="left", padx=(8, 0))
        self._dict_pron = tk.StringVar()
        ttk.Entry(form, textvariable=self._dict_pron, width=18).pack(side="left", padx=2)
        ttk.Label(form, text="下がる所:").pack(side="left", padx=(8, 0))
        self._dict_accent = tk.IntVar(value=0)
        _sp_acc = ttk.Spinbox(form, from_=0, to=30, width=4,
                              textvariable=self._dict_accent)
        _sp_acc.pack(side="left", padx=2)
        _Tooltip(_sp_acc,
                 "声の高さが下がる所です（読みの何文字目のあとで下がるか）。\n"
                 "0 は下がらずに、平らに読みます。「▶ 読みを試聴」で確かめながら選んでください。")
        ttk.Label(form, text="種類:").pack(side="left", padx=(8, 0))
        self._dict_wtype = ttk.Combobox(
            form, width=8, state="readonly",
            values=[lb for _v, lb in core.WORD_TYPE_LABELS])
        self._dict_wtype.current(0)   # 既定は固有名詞（人名・地名の読み直しが主用途）
        self._dict_wtype.pack(side="left", padx=2)
        _Tooltip(self._dict_wtype,
                 "単語の種類です。人名・地名などは「固有名詞」のままで大丈夫です。\n"
                 "一覧をダブルクリックすると、その単語の今の設定が入ります。")
        ttk.Label(form, text="優先:").pack(side="left", padx=(8, 0))
        self._dict_prio = tk.IntVar(value=5)
        _sp_prio = ttk.Spinbox(form, from_=0, to=10, width=4,
                               textvariable=self._dict_prio)
        _sp_prio.pack(side="left", padx=2)
        _Tooltip(_sp_prio,
                 "同じ表記が複数あるとき、どれを優先するか。\n"
                 "普通は5のままで大丈夫です。")

        btns = ttk.Frame(win); btns.pack(fill="x", padx=8, pady=(2, 2))
        b_add = ttk.Button(btns, text="登録する", command=self._dict_add)
        b_add.pack(side="left", padx=2)
        _Tooltip(b_add, "同じ単語が登録済みなら読みを上書きします\n"
                        "（一覧のダブルクリックで編集用に読み込めます）。")
        b_prev = ttk.Button(btns, text="▶ 読みを試聴", command=self._dict_preview)
        b_prev.pack(side="left", padx=2)
        _Tooltip(b_prev, "単語欄の言葉を今の声で読み上げて、\n"
                         "登録した読みが効いているか確認します。")
        ttk.Button(btns, text="選んだ言葉を消す", command=self._dict_delete).pack(side="left", padx=2)
        ttk.Button(btns, text="読み直す", command=self._dict_refresh).pack(side="left", padx=2)
        ttk.Button(btns, text="書き出し...", command=self._dict_export).pack(side="left", padx=(10, 2))
        ttk.Button(btns, text="読み込み...", command=self._dict_import).pack(side="left", padx=2)
        # ボタンの行の右端に置くと、窓が狭いときに切れていたので、下の行に
        ttk.Label(win, text="※読みは、ひらがなで書いても大丈夫です（カタカナに直して登録します）",
                  style="Sub.TLabel").pack(anchor="w", padx=8, pady=(0, 8))
        self._dict_refresh()

    def _dict_refresh(self):
        self._dict_refreshed_at = time.monotonic()
        threading.Thread(target=self._dict_list_worker, daemon=True).start()

    def _dict_focus_refresh(self, _event=None):
        """辞書の窓が前面に戻ったら読み直す（エディタ側での変更に追いつくため）。
        窓を触るたびに何度も叩かないよう、直前2秒以内なら何もしない。"""
        if time.monotonic() - getattr(self, "_dict_refreshed_at", 0.0) > 2.0:
            self._dict_refresh()

    def _dict_list_worker(self):
        try:
            rows = core.vv_dict_list(self.base_url, full=True)
            self.q.put(("dict_list", rows))
        except Exception as e:
            self.q.put(("dict_status", f"辞書の取得に失敗: {e}"))

    def _dict_sync_form_to_word(self, *_args):
        """単語欄の単語が一覧にあれば、その品詞・優先度をフォームへ写す。
        一覧（Treeview）を引くだけでエンジンには問い合わせない。"""
        tree = getattr(self, "_dict_tree", None)
        if tree is None:
            return
        try:
            if not tree.winfo_exists():
                return
            key = core.dict_surface_key(self._dict_surface.get())
            if not key:
                return
            labels = [lb for _v, lb in core.WORD_TYPE_LABELS]
            for iid in tree.get_children():
                vals = tree.item(iid, "values")
                if len(vals) < 5 or core.dict_surface_key(vals[0]) != key:
                    continue
                if vals[3] in labels:
                    self._dict_wtype.current(labels.index(vals[3]))
                try:
                    self._dict_prio.set(int(vals[4]))
                except (ValueError, tk.TclError):
                    pass
                return
        except tk.TclError:
            pass   # 閉じている最中

    def _dict_edit_selected(self, event=None):
        """一覧のダブルクリックで単語をフォームへ読み込む（編集して「追加/上書き」）。"""
        sel = self._dict_tree.selection()
        if not sel:
            return
        vals = self._dict_tree.item(sel[0], "values")
        if len(vals) >= 3:
            self._dict_surface.set(vals[0])
            self._dict_pron.set(vals[1])
            try:
                self._dict_accent.set(int(vals[2]))
            except (ValueError, tk.TclError):
                self._dict_accent.set(0)
        # 品詞・優先度もフォームへ。こうしておくと「読みだけ直して上書き」でも
        # 元の設定がそのまま送られ、勝手に既定値へ戻ることがない
        if len(vals) >= 5:
            labels = [lb for _v, lb in core.WORD_TYPE_LABELS]
            if vals[3] in labels:
                self._dict_wtype.current(labels.index(vals[3]))
            try:
                self._dict_prio.set(int(vals[4]))
            except (ValueError, tk.TclError):
                pass

    def _dict_add(self):
        surface = self._dict_surface.get().strip()
        pron = core.hira_to_kata(self._dict_pron.get().strip())
        if not surface or not pron:
            self.status_var.set("単語と読みを入力してください。")
            return
        try:
            accent = self._dict_accent.get()
        except tk.TclError:
            accent = 0
        # フォームの品詞・優先度も一緒に読む（UIスレッドで）
        idx = self._dict_wtype.current()
        word_type = (core.WORD_TYPE_LABELS[idx][0]
                     if 0 <= idx < len(core.WORD_TYPE_LABELS) else None)
        try:
            priority = int(self._dict_prio.get())
        except (ValueError, tk.TclError):
            priority = None

        def worker():
            try:
                # 重複はエンジンの実データで調べる。画面の一覧だけを見ていると、
                # VOICEVOXエディタ側で先に登録された単語に気づけず二重登録になる
                rows = core.vv_dict_list(self.base_url, full=True)
                key = core.dict_surface_key(surface)
                hits = [w for w in rows
                        if core.dict_surface_key(w["surface"]) == key]
                if hits:
                    core.vv_dict_update(self.base_url, hits[0]["uuid"],
                                        surface, pron, accent,
                                        word_type=word_type or hits[0]["word_type"],
                                        priority=(priority if priority is not None
                                                  else hits[0]["priority"]))
                    extra = (f"（同じ単語が{len(hits)}件あります。1件目を更新しました）"
                             if len(hits) > 1 else "")
                    self.q.put(("dict_status",
                                f"上書きしました：{surface} → {pron}{extra}"))
                else:
                    core.vv_dict_add(self.base_url, surface, pron, accent,
                                     word_type=word_type, priority=priority)
                    self.q.put(("dict_status", f"登録しました：{surface} → {pron}"))
                self._dict_gen += 1   # 実行中の合成に辞書変更を知らせる（キャッシュ鮮度）
                self.q.put(("dict_list",
                            core.vv_dict_list(self.base_url, full=True)))
            except Exception as e:
                self.q.put(("dict_status", f"登録に失敗: {e}（読みは全角カタカナのみ）"))
        threading.Thread(target=worker, daemon=True).start()

    def _dict_preview(self):
        """単語欄のテキストを現在の話者で読み上げ、登録した読みが効くか確かめる。"""
        surface = self._dict_surface.get().strip()
        if not surface:
            self.status_var.set("試聴する単語を入力してください。")
            return
        if self._previewing or self.busy:
            self.status_var.set("再生／処理の実行中です。")
            return
        sp = self._current_speaker()
        if sp is None or not core.can_play():
            self.status_var.set("VOICEVOX につながると試聴できます。")
            return
        voice = self._safe_voice_params()
        if voice is None:
            return
        self._previewing = True
        self._preview_stop = threading.Event()
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.sample_btn.config(state="disabled")
        self.resume_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._play_gen += 1
        self._spawn(self._dict_preview_worker,
                    (surface, sp[1], voice, self._preview_stop, self._play_gen))

    def _dict_preview_worker(self, text, speaker_id, voice, stop, gen):
        # 登録直後の読みを確認する用途なのでキャッシュは通さない
        try:
            wb, reading = core.vv_synthesize_with_kana(self.base_url, text,
                                                       speaker_id, **voice)
            if stop.is_set() or gen != self._play_gen:
                # 合成を待っている間に止められた／次の再生が始まった。
                # 音は出さずに終わる（出すと次の再生の音を奪う）
                self.q.put(("preview_done", True, reading, gen))
                return
            self._preview_buf = wb
            self.q.put(("preview_playing", text, speaker_id, None, gen))
            core.play_wav_blocking(wb, stop_event=stop)
            self.q.put(("preview_done", True, reading, gen))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc(), gen))

    def _dict_delete(self):
        sel = self._dict_tree.selection()
        if not sel:
            self.status_var.set("削除する単語を選択してください。")
            return
        uuids = list(sel)  # iid = word_uuid

        def worker():
            try:
                for u in uuids:
                    core.vv_dict_delete(self.base_url, u)
                self._dict_gen += 1   # 実行中の合成に辞書変更を知らせる（キャッシュ鮮度）
                self.q.put(("dict_status", f"{len(uuids)}件削除しました。"))
                self.q.put(("dict_list",
                            core.vv_dict_list(self.base_url, full=True)))
            except Exception as e:
                self.q.put(("dict_status", f"削除に失敗: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _dict_export(self):
        """登録済みの読み方をJSONに書き出す（Win/Mac間の持ち運び用）。"""
        out = filedialog.asksaveasfilename(
            title="辞書を書き出し", defaultextension=".json",
            filetypes=[("JSON", "*.json")], initialfile="voicevox_dict.json",
            parent=self._dict_win)
        if not out:
            return

        def worker():
            try:
                rows = core.vv_dict_list(self.base_url, full=True)
                # リスト形式は変えない。各要素にキーを足すだけなら、
                # 旧バージョンのアプリでも知らないキーを無視して読める
                data = [{"surface": w["surface"],
                         "pronunciation": w["pronunciation"],
                         "accent_type": w["accent_type"],
                         "word_type": w["word_type"],
                         "priority": w["priority"]} for w in rows]
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.q.put(("dict_status", f"{len(data)}語を書き出しました: {out}"))
            except Exception as e:
                self.q.put(("dict_status", f"書き出しに失敗: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    def _dict_import(self):
        """JSONから読み方を取り込む（登録済みの単語はスキップ）。"""
        path = filedialog.askopenfilename(
            title="辞書を読み込み", filetypes=[("JSON", "*.json")],
            parent=self._dict_win)
        if not path:
            return

        def worker():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    raise ValueError("辞書JSONの形式が違います（リストではありません）")
                existing = {core.dict_surface_key(w["surface"])
                            for w in core.vv_dict_list(self.base_url, full=True)}
                added = skipped = 0
                for w in data:
                    surface = (w.get("surface") or "").strip()
                    pron = (w.get("pronunciation") or "").strip()
                    if not surface or not pron:
                        continue
                    key = core.dict_surface_key(surface)
                    if key in existing:
                        skipped += 1
                        continue
                    core.vv_dict_add(self.base_url, surface,
                                     core.hira_to_kata(pron),
                                     int(w.get("accent_type", 0)),
                                     word_type=w.get("word_type"),
                                     priority=w.get("priority"))
                    added += 1
                    # JSON内の同一単語の2件目以降もスキップさせる（重複登録防止）
                    existing.add(key)
                if added:
                    self._dict_gen += 1   # キャッシュ鮮度（実行中の合成へ変更通知）
                self.q.put(("dict_status",
                            f"辞書読み込み: {added}語追加"
                            + (f" / {skipped}語は登録済みのためスキップ" if skipped else "")))
                self.q.put(("dict_list",
                            core.vv_dict_list(self.base_url, full=True)))
            except Exception as e:
                self.q.put(("dict_status", f"読み込みに失敗: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 試聴 ----------------
    def preview_selected(self):
        if self._previewing or self.busy:
            # 無音returnだと「押しても反応しない」に見えるので理由を出す
            self.status_var.set("再生／処理の実行中です。停止してからお試しください"
                                "（止まらないときは■停止/Esc）。")
            return
        default_sp = self._current_speaker()
        if default_sp is None:
            messagebox.showinfo(
                "情報", "先に VOICEVOX につないでください。\n"
                "左下の「つながるか確かめる」を押すか、VOICEVOX を"
                "起動してください。つながると声を選べて、試聴できます。")
            return
        if not core.can_play():
            messagebox.showinfo("情報", "この環境では試聴(再生)を利用できません。")
            return
        line = self.text.get("insert linestart", "insert lineend").strip()
        lineno = int(self.text.index("insert").split(".")[0])
        if not line:
            for i, ln in enumerate(self.text.get("1.0", "end").split("\n"),
                                   start=1):
                if ln.strip():
                    line, lineno = ln.strip(), i
                    break
        if not line:
            self.status_var.set("試聴するテキストがありません。")
            return
        if core.is_memo_line(line):
            self.status_var.set("この行はメモ（#）なので読み上げ対象外だよ。")
            return
        spoken, sp = self._resolve_line(line)
        if not spoken.strip():
            self.status_var.set("試聴するテキストがありません。")
            return
        # 数値欄は状態変更の“前”に読む（空欄でUIが永久ロックしていたバグの修正）
        voice = self._safe_voice_params()
        if voice is None:
            return
        speaker_id = sp[1] if sp else default_sp[1]
        self._previewing = True
        self._preview_stop = threading.Event()
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.sample_btn.config(state="disabled")
        self.resume_btn.config(state="disabled")   # 押すと画面だけ飛ぶため
        self.stop_btn.config(state="normal")   # 試聴も■停止/Escで止められる
        self.status_var.set("試聴を生成中...")
        self._play_gen += 1
        self._spawn(self._preview_worker,
                    (spoken, speaker_id, voice, lineno,
                     self._preview_stop, self._play_gen))

    def _preview_worker(self, line, speaker_id, voice, lineno,
                        stop, gen):
        try:
            done, target = self._prefetch_done, self._prefetch_target
            if (done is not None and not done.is_set()
                    and target == (line, speaker_id,
                                   tuple(sorted(voice.items())))):
                # ちょうどこの行を裏で先読みしている最中。同じ合成をもう1本
                # 投げるとエンジンを取り合って先読み前より遅くなるので、
                # それが終わってキャッシュに置かれるのを待つ（■停止には応じる）
                limit = time.monotonic() + 30
                while not done.wait(0.05):
                    if stop.is_set() or time.monotonic() > limit:
                        break
            # audio_query 1回で合成と読み確認の両方をまかなう（往復削減）。
            # キャッシュにあれば合成せず、読みだけ audio_query で取り直す
            dict_hash = core.vv_dict_hash(self.base_url)
            text = core.speech_text(line)   # エンジンへ実際に渡す文（読み方を直した後）
            key = core.synth_cache_key(text, speaker_id, engine_ver=self._engine_ver,
                                       dict_hash=dict_hash, **voice)
            wb = core.synth_cache_get(key)
            reading = ""
            if wb is None:
                wb, reading = core.vv_synthesize_with_kana(
                    self.base_url, text, speaker_id, read_fix=False, **voice)
                if self._engine_ver and dict_hash:
                    core.synth_cache_put(key, wb)
            else:
                try:
                    reading = core.vv_reading(self.base_url, text, speaker_id,
                                              read_fix=False)
                except Exception:
                    pass   # 読み取得の失敗は試聴を妨げない
            if stop.is_set() or gen != self._play_gen:
                # 合成を待っている間に止められた／次の再生が始まった。
                # 音は出さずに終わる（出すと次の再生の音を奪う）
                self.q.put(("preview_done", True, reading, gen))
                return
            self._preview_buf = wb
            self.q.put(("preview_playing", line, speaker_id, lineno, gen))
            # ワーカースレッドなので同期再生でブロックして問題ない
            core.play_wav_blocking(wb, stop_event=stop)
            self.q.put(("preview_done", True, reading, gen))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc(), gen))

    # ---------------- 話者の声サンプル試聴 ----------------
    def play_speaker_sample(self):
        """選択中の話者スタイルの公式ボイスサンプルを1つ再生する（声選び用）。"""
        if self.busy or self._previewing:
            return
        sp = self._current_speaker()
        if sp is None:
            return
        if not core.can_play():
            messagebox.showinfo("情報", "この環境では再生を利用できません。")
            return
        label, style_id, sp_uuid = sp
        self._previewing = True
        self._preview_stop = threading.Event()
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.sample_btn.config(state="disabled")
        self.resume_btn.config(state="disabled")
        self.stop_btn.config(state="normal")   # サンプルも■停止/Escで止められる
        self.status_var.set(f"サンプル取得中: {label}")
        self._play_gen += 1
        self._spawn(self._sample_worker,
                    (label, style_id, sp_uuid,
                     self._preview_stop, self._play_gen))

    def _sample_worker(self, label, style_id, sp_uuid, stop, gen):
        try:
            wav = self._sample_cache.get((sp_uuid, style_id))
            if wav is None:
                wav = core.vv_speaker_sample(self.base_url, sp_uuid, style_id)
                self._sample_cache[(sp_uuid, style_id)] = wav
            if stop.is_set() or gen != self._play_gen:
                # 合成を待っている間に止められた／次の再生が始まった。
                # 音は出さずに終わる（出すと次の再生の音を奪う）
                self.q.put(("preview_done", True, "", gen))
                return
            self.q.put(("preview_playing", f"（声サンプル: {label}）", style_id,
                        None, gen))
            core.play_wav_blocking(wav, stop_event=stop)
            self.q.put(("preview_done", True, "", gen))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc(), gen))

    # ---------------- 連続再生（カーソル行から最後まで） ----------------
    def play_all(self):
        """本文の最初から順に読み上げる（「▶▶ 連続再生」ボタンの動作）。
        カーソルがどこにあっても頭から読む。押した人の期待に一番近いのがこれで、
        途中から聴きたいときは「▶ 続きを聴く」か、本文の右クリックから選ぶ。"""
        self._cursor_to_top()
        self.play_from_cursor()

    def play_from_cursor(self):
        """カーソル行から最後まで読み上げる。
        「▶ 続きを聴く」と本文の右クリック「ここから連続再生」の共通の実体。"""
        if self._previewing or self.busy:
            self.status_var.set("再生／処理の実行中です。停止してからお試しください"
                                "（止まらないときは■停止/Esc）。")
            return
        default_sp = self._current_speaker()
        if default_sp is None:
            messagebox.showinfo(
                "情報", "先に VOICEVOX につないでください。\n"
                "左下の「つながるか確かめる」を押すか、VOICEVOX を"
                "起動してください。")
            return
        if not core.can_play():
            messagebox.showinfo("情報", "この環境では再生を利用できません。")
            return
        # 数値欄は状態変更の“前”に読む（空欄でUIが永久ロックしていたバグの修正）
        voice = self._safe_voice_params()
        if voice is None:
            return
        # (行番号, 読み上げテキスト, style_id) を集め、カーソル行以降を再生対象にする
        numbered = self._speakable_lines(default_sp[1])
        if not numbered:
            self.status_var.set("再生するテキストがありません。")
            return
        cur = int(self.text.index("insert").split(".")[0])
        # カーソル以降に読み上げ行が無い場合は len(numbered) を既定値に。
        # 既定0だと末尾の空行にカーソルがあるとき文書全体を頭から再生してしまう。
        start = next((k for k, t in enumerate(numbered) if t[0] >= cur), len(numbered))
        targets = numbered[start:]
        if not targets:
            self.status_var.set("カーソルより後に再生する行がありません。")
            return
        # 段落・章の切れ目（空行のあと・行頭の字下げ・章の見出し）は、行の前の間を長めにする
        # （保存する音声と同じ）
        kinds = self._play_kinds(numbered)
        self._play_breaks = kinds[start:]
        self._play_longer = bool(self.para_pause_var.get())
        self._playall_stop = threading.Event()
        self._playall_pause = threading.Event()
        self._play_gap = max(0.0, min(10.0, float(self._get_num(self.gap_var, 0.4))))
        self._previewing = True
        self._index_play_lines(numbered, kinds)
        self._user_scrolled_at = 0.0   # 押したのだから、読む行へ連れていく
        self._hide_resume()
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.resume_btn.config(state="disabled")
        self.sample_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pause_btn.config(text="‖ 一時停止", state="normal")
        self._sync_play_buttons()
        # 最初の1行の合成が終わるまで1秒前後かかる。その間なにも言わないと
        # 「押しても反応しない」に見えるので、押した瞬間に返事をする
        self.status_var.set(f"読み上げの準備中…（{len(targets)}行）")
        self.update_idletasks()
        self._play_gen += 1
        self._spawn(self._playall_worker, (targets, voice, self._play_gen))

    def toggle_pause(self):
        """連続再生の一時停止/再開。Windows は止めた所から続きを鳴らし、Mac は
        同じ行の頭から読み直す（_playall_stream / _playall_lines）。"""
        ev = self._playall_pause
        if ev is None:
            return
        if ev.is_set():
            ev.clear()
            self.pause_btn.config(text="‖ 一時停止")
            self.status_var.set("再開します...")
            if self._line_sounding:
                self._start_mouth()   # 行の途中から再開。行間なら次の行で動かす
        else:
            ev.set()
            self.pause_btn.config(text="▶ 再開")
            self.status_var.set("一時停止中（▶ 再開 または スペースキーで続き）")
            self._close_mouth()   # 止まっている間は口も閉じる

    def _mark_bookmark(self):
        """しおり（▶ 続きを聴く の再開位置）の行を淡色でマークして見えるようにする。
        連続再生の停止後・前回テキスト復元後に呼ばれ、どこから再開するかが一目で分かる。"""
        try:
            self.text.tag_remove("bookmark", "1.0", "end")
            if self._bookmark is not None:
                last = int(self.text.index("end-1c").split(".")[0])
                line = min(self._bookmark, last)
                self.text.tag_add("bookmark", f"{line}.0", f"{line}.end")
        except tk.TclError:
            pass
        self._furi_recolor()

    def _reset_bookmark(self):
        """しおりを捨てる。本文を入れ替えたとき用。しおりは行番号なので、別の本文に
        残すと、次に開いたとき「前回の続き：312行目」が関係ない行を指していた。"""
        self._bookmark = None
        self._mark_bookmark()   # 淡色の印も外す
        if not self._previewing:
            self.resume_btn.config(state="disabled")

    def play_from_bookmark(self):
        """しおり（最後に再生した行）から連続再生を再開する。"""
        if self._bookmark is None:
            self.status_var.set("しおりがありません（連続再生すると自動で記憶されます）。")
            return
        last = int(self.text.index("end-1c").split(".")[0])
        line = min(self._bookmark, last)
        self.text.mark_set("insert", f"{line}.0")
        self.text.see(f"{line}.0")
        self.play_from_cursor()   # ここで play_all を呼ぶと先頭に戻ってしおりが無意味になる

    def stop_playall(self):
        if self._playall_stop is not None:
            self._playall_stop.set()
        if self._preview_stop is not None:
            self._preview_stop.set()   # 試聴・声サンプルの再生も同じボタンで止める
        self.stop_btn.config(state="disabled")
        self.status_var.set("停止しています...")
        # 万一ワーカーが応答せず状態が残ったら（完了通知が来ない等）強制復帰する。
        # これが無いと _previewing が立ちっぱなしになり、以降の試聴・連続再生が
        # 反応しなくなる（＝「試聴が動かない」に見える）
        gen = self._play_gen
        self._tick("recover", 1500, lambda: self._recover_if_stuck(gen))

    def _recover_if_stuck(self, gen):
        """停止後も再生状態が残っていたらUIを強制的に待機状態へ戻す（安全網）。
        止めた再生の世代のときだけ働く。すぐ次を再生し始めた場合、この予約が
        1.5秒後に効いてしまうと、鳴っている最中なのに待機表示に戻り、行の
        ハイライトも一時停止ボタンも消える（実測で起きていた）。
        なお、ここで待機状態に戻したあと旧ワーカーが完了通知を投げてくるが、
        世代番号の合わない通知は受け側で捨てるので巻き添えにはならない。"""
        self._ticks.pop("recover", None)
        if gen != self._play_gen or not self._previewing:
            return
        self._previewing = False
        self._preview_stop = None
        self._playall_stop = None
        self._playall_pause = None
        self._sync_play_buttons()
        self._stop_mouth()
        self.text.tag_remove("playing", "1.0", "end")
        self._furi_recolor()
        self.pause_btn.config(text="‖ 一時停止", state="disabled")
        if self._engine_ready() and not self.busy:
            self.preview_btn.config(state="normal")
            self.playall_btn.config(state="normal")
            self.sample_btn.config(state="normal")
            if self._bookmark is not None:
                self.resume_btn.config(state="normal")
        self.status_var.set("停止しました（待機中）。")

    # 連続再生で次の行を音の流れに足すのは、鳴っていない音の残りがこの秒数を切ってから。
    # 先に足しすぎないので、再生中に「間」を変えても次の行間からすぐ効く
    _STREAM_LOOKAHEAD = 0.6
    # 連続再生の音の流れを開く関数（None なら core.WaveOutStream。テストで差し替える）
    _play_stream_factory = None

    def _playall_worker(self, targets, voice, gen):
        """連続再生の本体。Windows では行を1本の音の流れに並べて鳴らし
        （_playall_stream）、Mac や、音の出力を開けなかったときは行ごとに鳴らす
        （_playall_lines）。"""
        stop = self._playall_stop
        pause = self._playall_pause
        counts = {"played": 0, "skipped": 0, "consec": 0}
        core.synth_cache_protect(time.time())   # 長い本の途中でキャッシュを守る
        try:
            from concurrent.futures import ThreadPoolExecutor
            # 合成キャッシュ（聴き終えた行は音声生成でも再合成不要になる）
            dict_hash = self._dict_hash_tracker()
            last_tb = [None]

            def synth(t):
                # 1行の失敗（極端に長い行のtimeout・特殊文字のエンジンエラー等）で
                # 読書全体を止めない。失敗は None で返しスキップ判定に回す
                return self._play_synth(t[1], t[2], voice, dict_hash, stop, last_tb)

            def skip(lineno):
                counts["skipped"] += 1
                counts["consec"] += 1
                if counts["consec"] >= 3:
                    # 連続3失敗＝エンジンが落ちている。全行を無言スキップ
                    # して「読み終わったよ」と誤報しないため従来のエラーへ
                    raise RuntimeError(last_tb[0] or "連続して合成に失敗")
                self.q.put(("playall_skip", lineno, gen))

            # 再生中に次の行を裏で合成しておく（行間の待ちをほぼゼロに）
            with ThreadPoolExecutor(max_workers=1) as ex:
                start = 0
                if core.IS_WIN or self._play_stream_factory is not None:
                    start = self._playall_stream(targets, ex, synth, skip, counts,
                                                 stop, pause, gen)
                if start < len(targets) and not stop.is_set():
                    self._playall_lines(targets, start, ex, synth, skip, counts,
                                        stop, pause, gen)
            self.q.put(("playall_done", True, stop.is_set(), counts["played"],
                        counts["skipped"], gen))
        except Exception:
            self.q.put(("playall_done", False, traceback.format_exc(),
                        counts["played"], counts["skipped"], gen))
        finally:
            core.synth_cache_protect(0.0)

    _ENGINE_WAIT_SEC = 60   # VOICEVOX につながらなくなった行は、これだけ待ってから頼み直す

    def _play_synth(self, text, style_id, voice, dict_hash, stop, last_tb):
        """連続再生の1行を作る（ワーカーのスレッドから呼ぶ）。失敗は None。
        VOICEVOX につながらない（落ちた）ときは、起こし直す（_engine_died）のを待って、
        もう一度だけ頼む。前は3行続けて失敗した時点で、再生が止まっていた。"""
        import requests
        for attempt in (0, 1):
            try:
                return core.vv_synthesize_cached(
                    self.base_url, text, style_id, engine_ver=self._engine_ver,
                    dict_hash=dict_hash(), **voice)
            except Exception as e:
                last_tb[0] = traceback.format_exc()
                if (attempt or not isinstance(e, requests.ConnectionError)
                        or not self._wait_engine(stop, self._ENGINE_WAIT_SEC)):
                    return None
        return None

    def _wait_engine(self, stop, seconds):
        """VOICEVOX がまた返事をするまで待つ（ワーカーのスレッドから呼ぶ）。止められたら False。"""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if stop is not None and stop.is_set():
                return False
            try:
                if core.vv_probe(self.base_url, timeout=1)["ok"]:
                    return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def _playall_stream(self, targets, ex, synth, skip, counts, stop, pause, gen):
        """Windows の連続再生: 行を1本の音の流れ（core.LineStream）に並べて鳴らす。
        語尾が切れず、行間は「間」の秒数ちょうど（保存するファイルと同じ）になり、
        一時停止はその場で止まって続きから鳴る。行のハイライトとしおりは、その行が
        実際に鳴り始めたときに移す。音の出力を開けなかったら（出力機器が無い・
        途中で抜けた等）、まだ鳴らし終えていない最初の行の番号を返す。そこからは
        行ごとの再生で続ける。最後まで鳴らした・止めたときは len(targets) を返す。"""
        total = len(targets)
        ls = core.LineStream(self._play_stream_factory)
        nxt = ex.submit(synth, targets[0])
        k = 0               # 次に流れへ足す行
        queued = False      # 1行でも足したか（最初の行の前には間を入れない）
        unfinished = 0      # まだ鳴らし終えていない最初の行

        def report():
            nonlocal unfinished
            for ev, i in ls.poll():
                lineno, ln, sid = targets[i]
                if ev == "start":
                    self.q.put(("playall_line", lineno, ln, sid, i, total, gen))
                else:
                    counts["played"] += 1
                    unfinished = i + 1
                    self.q.put(("playall_line_end", lineno, gen))

        try:
            while not stop.is_set():
                if pause.is_set():
                    ls.pause()
                else:
                    ls.resume()
                report()
                if (k < total and nxt.done()
                        and ls.remaining() <= self._STREAM_LOOKAHEAD):
                    wb = nxt.result()
                    i, k = k, k + 1
                    if k < total:
                        nxt = ex.submit(synth, targets[k])
                    if wb is None:
                        skip(targets[i][0])
                        continue
                    counts["consec"] = 0
                    ls.add(i, wb, self._gap_before(i) if queued else 0.0)
                    queued = True
                    continue   # 短い行が続くときは、すぐ次も足す
                if k >= total and ls.done():
                    report()   # 最後の行の鳴り終わりを取りこぼさない
                    break
                time.sleep(0.02)
        except OSError:
            return unfinished
        finally:
            ls.close()   # 止めたときは、ここで音がすぐ止まる
        return total

    def _playall_lines(self, targets, start, ex, synth, skip, counts, stop, pause, gen):
        """行ごとに鳴らす連続再生（Mac。Windows でも音の流れを開けなかったとき）。
        声の途中で一時停止したら、再開時に同じ行を頭から読み直す。声が鳴り終わって
        から（Windows で末尾に足した無音の間）の一時停止は、読み終えた扱いで次の行へ。"""
        total = len(targets)
        # 停止・一時停止のどちらでも現在行の再生を打ち切る（afplay/winsoundの引数用）
        either = _EitherEvent(stop, pause)
        nxt = ex.submit(synth, targets[start])
        for k in range(start, total):
            lineno, ln, sid = targets[k]
            if stop.is_set():
                break
            self.q.put(("playall_line", lineno, ln, sid, k, total, gen))
            wb = nxt.result()
            if k + 1 < total:
                nxt = ex.submit(synth, targets[k + 1])
            if wb is None:
                skip(lineno)
                continue
            counts["consec"] = 0
            while True:
                if stop.is_set():
                    break
                self._preview_buf = wb
                finished = core.play_wav_blocking(wb, stop_event=either)
                if pause.is_set() and not stop.is_set() and not finished:
                    # 声の途中の一時停止: 解除（または停止）まで待ち、同じ行を頭から読み直す
                    while pause.is_set() and not stop.is_set():
                        time.sleep(0.1)
                    continue
                break
            if stop.is_set():
                break
            counts["played"] += 1
            if k + 1 < total:
                # 一時停止中なら、ここで再開を待つ
                self._wait_playall_gap(stop, pause, self._gap_before(k + 1))

    # ---------------- 一括置換 ----------------
    def replace_all_text(self):
        find = self.find_var.get()
        if not find:
            self.status_var.set("検索文字を入力してください。")
            return
        repl = self.repl_var.get()
        content = self.text.get("1.0", "end-1c")
        count = content.count(find)
        if count == 0:
            self.status_var.set(f"「{find}」は見つかりませんでした。")
            return
        self.text.edit_separator()  # Undo境界（Ctrl+Zで戻せる）
        ins, y = self.text.index("insert"), self.text.yview()[0]
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content.replace(find, repl))
        self._restore_view(ins, y)   # 読み進めていた位置を見失わない
        self.status_var.set(f"{count}か所を置き換えました：「{find}」→「{repl}」")

    # ---------------- 行数・文字数・めやすの常時表示 ----------------
    def _schedule_stats(self):
        """統計表示の更新を400msデバウンスで予約する（キー連打のたびに
        speakable_text の全文走査をしない）。"""
        if getattr(self, "_stats_after", None):
            try:
                self.after_cancel(self._stats_after)
            except tk.TclError:
                pass
        try:
            self._stats_after = self.after(400, self._update_text_stats)
        except tk.TclError:
            self._stats_after = None

    def _update_text_stats(self):
        self._stats_after = None
        try:
            text = self.text.get("1.0", "end-1c")
        except tk.TclError:
            return   # 終了中
        if not text.strip():
            self.stats_var.set("")
            return
        lines = len([l for l in text.split("\n") if l.strip()])
        speak = core.speakable_text(text)
        chars = len(speak.replace("\n", ""))
        est = core.fmt_duration(core.estimate_read_seconds(
            speak, self._get_num(self.speed_var, 1.0)))
        self.stats_var.set(f"{lines}行・{chars:,}字・めやす{est}")

    def _restore_view(self, ins, yfrac):
        """全文差し替え後にカーソル位置とスクロール位置を戻す（一括置換用）。
        置換のたびに表示が先頭へ飛ぶと、長い本を読み進めながら直す使い方で
        現在位置を見失うため。行数が減っていた場合は末尾へクランプされる。"""
        try:
            self.text.mark_set("insert", ins)
        except tk.TclError:
            pass
        self.text.yview_moveto(yfrac)

    # ---------------- ふりがな（本文の漢字の上に、VOICEVOX が読む読みを出す） ----------------
    # 読みはエンジンが実際に読み上げる読み（core.vv_furigana）なので、ふりがなと音は必ず
    # 一致する。見えている行だけ、裏でエンジンに問い合わせて覚えておく（1行15msほど）。
    # 置き方: 行の上に余白（spacing1/2）を取り、そこへ小さな文字のラベルを置く。
    # Tk のテキスト欄にはルビの仕組みが無いため。最初からオン。余白はエンジンに
    # つながっている間だけ取る（つながる前から行間だけ空くと、壊れて見えるため）。
    _FURI_COLORS = {   # (辞書と一致・数字など, 辞書と違う読み＝誤読かも)
        # 暗い地の小さい字なので、控えめな文字の色（#a89b89）より一段明るくする
        "dark": ("#c9bba7", "#f2a65a"),
    }
    _FURI_COLORS_DEFAULT = ("#5e7685", "#c2410c")
    _FURI_CACHE_MAX = 4000   # 覚えておく行の数

    def _furi_dict_installed(self):
        """ふりがなの書き方に使う辞書（Janome）が入っているか（読み込まずに調べる）。
        スクロールのたびに聞かれるので、一度調べたら覚えておく（入れ直したら調べ直す）。"""
        if self._furi_has_dict is None:
            import importlib.util
            try:
                self._furi_has_dict = importlib.util.find_spec("janome") is not None
            except (ImportError, ValueError):
                self._furi_has_dict = False
        return self._furi_has_dict

    def _furi_tip(self):
        tip = ("本文の漢字の上に、VOICEVOX が実際に読む読みを出します。\n"
               "誤読を、聴く前に見つけられます（じゃまなときはチェックを外す）。")
        if self._furi_dict_installed():
            tip += ("\nひらがな＝辞書の書き方と同じ読み／色つきのカタカナ＝辞書と違う読み"
                    "（誤読かもしれない所）")
        else:
            tip += ("\n辞書が入っていないので、読みは VOICEVOX の発音どおりカタカナで"
                    "出ています。本文の右クリックから入れられます。")
        if not self._engine_ready():
            tip += "\n（VOICEVOX を起動すると出ます）"
        return tip

    def _sync_furi_notice(self):
        """ふりがながオンなのに VOICEVOX につながっていないとき、本文のすぐ上に、出ない
        理由と「画面なしで起動」を出す。つながった・ふりがなを外した・本文が空のときは
        消す。起動して最初の接続確認が済むまで（unknown）は出さない（すぐ消える一言で
        ちらつかせない）。起動を待っている間は、ボタンを隠して待っていると言う。"""
        row = getattr(self, "_furi_notice", None)
        if row is None:
            return
        state = self.conn_state
        show = (bool(self.furigana_var.get()) and not self._engine_ready()
                and state != "unknown" and getattr(self, "_has_text", False))
        if not show:
            if row.winfo_manager():
                row.pack_forget()
            return
        waiting = state in ("waiting", "searching", "timeout")
        self._furi_notice_var.set(
            "※ VOICEVOX の起動を待っています。つながると、ふりがなが出ます。" if waiting
            else "※ ふりがな（漢字の上の読み）は、VOICEVOX を起動すると出ます。")
        btn = self._furi_notice_btn
        if waiting and btn.winfo_manager():
            btn.pack_forget()
        elif not waiting and not btn.winfo_manager():
            btn.pack(side="left", padx=(GAPX, 0), after=self._furi_notice_lbl)
        if not row.winfo_manager():
            row.pack(fill="x", pady=(GAPY, 0), before=self._rbar[4])   # 本文のすぐ上

    def _toggle_furigana(self):
        """ふりがなの表示を切り替える（チェックの付け外し）。"""
        if self.furigana_var.get() and not self._engine_ready():
            self.status_var.set("ふりがなは、VOICEVOX を起動すると出ます。")
        self._sync_furi_notice()
        self._furi_schedule(0)
        # 低い窓では、ふりがなの場所のぶん見える行数が変わるので、文字の大きさも合わせ直す
        self._fit_text_font()

    def _furi_apply_spacing(self, on):
        """ふりがなを置く余白（行の上）を取る／戻す。文字の大きさにも合わせる。"""
        if self._furi_spacing0 is None:
            self._furi_spacing0 = (int(self.text.cget("spacing1") or 0),
                                   int(self.text.cget("spacing2") or 0))
        self._furi_spaced = on
        if not on:
            s1, s2 = self._furi_spacing0
            self.text.config(spacing1=s1, spacing2=s2)
            return
        size = abs(int(self.text_font.cget("size")))
        want = (self.text_font.cget("family"), max(7, round(size * 0.6)))
        if self._furi_font is None or (self._furi_font.cget("family"),
                                       self._furi_font.cget("size")) != want:
            self._furi_font = tkfont.Font(family=want[0], size=want[1])
            self._furi_widths = {}   # 文字の幅は大きさが変わったら測り直す
            for lab, state in zip(self._furi_labels, self._furi_states):
                lab.place_forget()   # 置き直すまで隠す（覚えていた位置も捨てるため）
                lab.config(font=self._furi_font)
                state.clear()
        room = self._furi_font.metrics("linespace") + 1
        self._furi_rise = room   # ふりがなを、文字の上端からどれだけ上に置くか
        self.text.config(spacing1=self._furi_spacing0[0] + room,
                         spacing2=self._furi_spacing0[1] + room)

    def _furi_schedule(self, ms=0):
        """ふりがなの置き直しを、少し待ってから1回だけする（スクロール・入力が続く間はまとめる）。"""
        if getattr(self, "_destroying", False) or not hasattr(self, "_ticks"):
            return
        if not self.furigana_var.get() and not self._furi_labels:
            return
        old = self._ticks.pop("furigana", None)
        if old:
            try:
                self.after_cancel(old)
            except tk.TclError:
                pass
        self._tick("furigana", ms, self._furi_refresh)

    def _furi_recolor(self):
        """色の付いた印（再生中・しおり・検索・止まった行）を付け外ししたあとに呼ぶ。
        ふりがなの下地は印の色に合わせてあるので、塗り直す（印の付け外しでは本文が
        動かないので、ほかに置き直すきっかけが無い）。"""
        if getattr(self, "_furi_labels", None):
            self._furi_schedule(0)

    def _furi_follow_scroll(self):
        """スクロールしたら、ふりがなを同じ描き直しの中で動かす。置き直しの途中で
        スクロール位置が変わったとき（行間を変えた等）は、二重にせず後回しにする。"""
        if getattr(self, "_furi_in_refresh", False) or not self._furi_spaced:
            self._furi_schedule(0)
            return
        self._furi_in_refresh = True
        try:
            self._furi_refresh()
        except tk.TclError:
            pass
        finally:
            self._furi_in_refresh = False

    def _furi_line(self, line):
        """本文の1行から、読み上げる文と、その本文の中での開始位置を返す（読まない行は None）。
        @話者タグは外して読むので、ふりがなの位置もタグの後ろからにずらす。"""
        if not line.strip() or core.is_memo_line(line):
            return None
        stripped = line.strip()
        spoken, _sp = self._resolve_line(stripped)
        if not spoken.strip():
            return None
        at = stripped.rfind(spoken)
        if at < 0:
            return None
        return (len(line) - len(line.lstrip())) + at, spoken

    def _furi_refresh(self):
        """見えている行のふりがなを置き直す。まだ読みを知らない行は、裏で取りに行く。"""
        self._ticks.pop("furigana", None)
        if getattr(self, "_destroying", False):
            return
        want = bool(self.furigana_var.get()) and self._engine_ready()
        # 「誤読候補だけ」「次の候補」は、ふりがなを出しているときだけ使える
        for w in (getattr(self, "furigana_only_chk", None),
                  getattr(self, "furi_next_btn", None)):
            if w is not None:
                w.state(["!disabled"] if self.furigana_var.get() else ["disabled"])
        if want != self._furi_spaced:
            self._furi_apply_spacing(want)
        if not want:
            for lab, state in zip(self._furi_labels, self._furi_states):
                if state.get("at") is not None:
                    lab.place_forget()
                    state["at"] = None
            return
        try:
            first = int(self.text.index("@0,0").split(".")[0])
            last = int(self.text.index(f"@0,{self.text.winfo_height()}").split(".")[0])
        except tk.TclError:
            return
        shown, missing = [], []
        installed = self._furi_dict_installed()
        # 見えている行をまとめて1回で読む（1行ずつ読むと、そのたびに Tk を呼ぶので遅い）
        block = self.text.get(f"{first}.0", f"{last}.end").split("\n")
        for ln, line in enumerate(block, start=first):
            item = self._furi_line(line)
            if item is None:
                continue
            off, spoken = item
            key = (spoken, self._dict_gen, self._engine_ver, installed,
                   core.read_fix_on())
            rubies = self._furi_cache.pop(key, None)
            if rubies is None:
                missing.append(spoken)
                continue
            self._furi_cache[key] = rubies   # 最近使った行を後ろへ（古い行から捨てる）
            shown.extend((ln, off + a, off + b, r, kind) for a, b, r, kind in rubies)
        if self.furigana_only_var.get():   # 誤読候補だけ（辞書と同じ読みは出さない）
            shown = [r for r in shown if r[4] != core.FURI_OK]
        self._furi_draw(shown)
        if missing:
            self._furi_fetch(list(dict.fromkeys(missing)))

    def _furi_fetch(self, texts):
        """読みを知らない行を、裏でエンジンに問い合わせる（終わったら置き直す）。"""
        if (self._furi_busy or not self._engine_ready()
                or time.monotonic() < self._furi_retry_at):
            return
        sp = self._current_speaker()
        if sp is None:
            return
        self._furi_busy = True
        base, sid = self.base_url, sp[1]
        keyinfo = self._furi_keyinfo()

        def worker():
            import requests
            got, failed = {}, False
            for t in texts[:80]:
                try:
                    got[t] = core.vv_furigana(base, t, sid,
                                              use_dictionary=keyinfo[2], timeout=10,
                                              read_fix=keyinfo[3])
                except requests.HTTPError:
                    got[t] = []     # エンジンがこの行を受け付けなかった（下と同じ扱い）
                except (requests.ConnectionError, requests.Timeout):
                    failed = True   # エンジンが止まった等。少し待ってから取り直す
                    break
                except Exception:
                    # この行だけ出せない（エンジンが受け付けない文字など）。覚えておいて
                    # 取り直さず、ほかの行は続ける。以前は1行のせいで全部の行が出なかった
                    got[t] = []
            self.q.put(("furigana", got, keyinfo, failed))
        threading.Thread(target=worker, daemon=True).start()

    def _furi_keyinfo(self):
        """覚えた読みの名札（本文の行のあとに付ける分）。辞書の世代・エンジンの版・
        ふりがなの辞書の有無・読み方の直しのオン・オフのどれかが変わったら、読み直す。"""
        return (self._dict_gen, self._engine_ver, self._furi_dict_installed(),
                core.read_fix_on())

    def _furi_remember(self, got, keyinfo):
        """取ってきた読みを覚える（覚える行の数を超えたら、古い行から捨てる）。"""
        for t, rubies in got.items():
            self._furi_cache[(t,) + tuple(keyinfo)] = rubies
        while len(self._furi_cache) > self._FURI_CACHE_MAX:
            self._furi_cache.pop(next(iter(self._furi_cache)))

    def _furi_store(self, got, keyinfo, failed):
        """取ってきた読みを覚えて、置き直す。"""
        self._furi_busy = False
        self._furi_remember(got, keyinfo)
        if failed:
            self._furi_retry_at = time.monotonic() + 10
        if got:
            self._furi_schedule(0)

    def _furi_draw(self, rubies):
        """ふりがな [(行, 開始, 終了, 読み, 種類)] を、その文字の上の余白に置く。
        スクロールのたびに呼ぶので、Tk を呼ぶ回数を減らしてある（同じ答えになるものは
        1回だけ調べ、文字や色が変わらないラベルは設定し直さず、位置が同じなら置き直さない）。"""
        font = self._furi_font
        if font is None:
            return
        theme_colors = self._FURI_COLORS.get(self.theme_var.get(),
                                             self._FURI_COLORS_DEFAULT)
        width = self.text.winfo_width()
        rise = self._furi_rise
        lines = [r[0] for r in rubies]
        marks, base_bg = self._furi_tag_bgs(min(lines, default=1), max(lines, default=1))
        want = []   # (同じふりがなかの目印, 見た目, 位置)
        for ln, a, b, reading, kind in rubies:
            try:
                b0 = self.text.bbox(f"{ln}.{a}")
                b1 = self.text.bbox(f"{ln}.{b - 1}") if b - a > 1 else b0
            except tk.TclError:
                continue
            if not b0 or not b1 or b0[1] != b1[1]:
                continue   # 見えていない・折り返しをまたぐ所は出さない
            w = self._furi_widths.get(reading)
            if w is None:
                w = self._furi_widths[reading] = font.measure(reading)
            x = (b0[0] + b1[0] + b1[2]) / 2 - w / 2
            x = int(max(0, min(x, width - w)))
            bg = self._furi_bg_at(ln, a, marks, base_bg)
            # 再生中・しおり・検索の印は明るい色なので、その上は濃い色で書く
            # （ダークの薄い色のままだと読めない。本文の字も、印の上では黒にしてある）
            plain, warn = (self._FURI_COLORS_DEFAULT
                           if bg != base_bg and self._furi_light(bg) else theme_colors)
            look = (reading, warn if kind == core.FURI_DIFF else plain, bg,
                    kind != core.FURI_OK)
            want.append(((ln, a, reading), look, (x, b0[1] - rise), (ln, a, b, kind)))
        want = self._furi_spread(self._furi_drop_overlaps(want), width)
        # スクロールしても、同じふりがなは同じラベルのまま位置だけ動かす。ラベルの文字を
        # 書き換えると Windows が描き直すので、それが一番重い（64個で約20ms）
        owner = {s.get("key"): i for i, s in enumerate(self._furi_states)
                 if s.get("key") is not None}
        taken = set()
        jobs = []
        for key, look, at, span in want:
            i = owner.get(key)
            if i is not None and i not in taken:
                taken.add(i)
                jobs.append((i, key, look, at, span))
            else:
                jobs.append((None, key, look, at, span))
        spare = (i for i in range(len(self._furi_labels) + len(jobs)) if i not in taken)
        for i, key, look, at, span in jobs:
            if i is None:
                i = next(spare)
                taken.add(i)
            lab = self._furi_label(i)
            state = self._furi_states[i]
            state["key"] = key
            state["span"] = span   # 押したときに、どの語の読みかを知るため
            if state.get("look") != look:
                # 辞書で確かめられない読み（色つきなど）は、押すと直せる
                lab.config(text=look[0], fg=look[1], bg=look[2],
                           cursor="hand2" if look[3] else "xterm")
                state["look"] = look
            if state.get("at") != at:
                # 位置は本文欄の外側の角から数える（bbox と同じ数え方）。既定の内側
                # からだと、本文欄の枠と内側の余白（padx・pady）のぶんずれて、
                # ふりがなが文字に重なった
                lab.place(x=at[0], y=at[1], bordermode="outside")
                state["at"] = at
        for i, (lab, state) in enumerate(zip(self._furi_labels, self._furi_states)):
            if i not in taken and state.get("at") is not None:
                lab.place_forget()
                state["at"] = None
                state["key"] = None

    def _furi_spread(self, want, width):
        """同じ段で重なる漢字のふりがなを、重ならないよう横に並べ直す。読みが漢字より
        広いと（十本 → じゅっ｜ぽん）、となりと重なって読めなかった。重なる組は、その
        漢字のかたまりの真ん中にそろえて、すき間なく順に置く。重ならない所は動かさない。"""
        gap = max(1, _px(1))
        rows = {}
        for item in want:
            rows.setdefault(item[2][1], []).append(item)
        out = []
        for y, items in rows.items():
            items.sort(key=lambda it: it[2][0])
            chain = []

            def flush():
                if len(chain) > 1:
                    ws = [self._furi_widths.get(it[1][0], 0) for it in chain]
                    total = sum(ws) + gap * (len(chain) - 1)
                    mid = (chain[0][2][0] + ws[0] / 2 + chain[-1][2][0] + ws[-1] / 2) / 2
                    x = int(max(0, min(mid - total / 2, width - total)))
                    for it, w in zip(chain, ws):
                        out.append((it[0], it[1], (x, y), it[3]))
                        x += w + gap
                else:
                    out.extend(chain)
                chain.clear()
            right = None
            for it in items:
                x0 = it[2][0]
                if right is not None and x0 >= right:
                    flush()
                chain.append(it)
                right = max(right or 0, x0 + self._furi_widths.get(it[1][0], 0)) if len(chain) > 1 \
                    else x0 + self._furi_widths.get(it[1][0], 0)
            flush()
        return out

    def _furi_drop_overlaps(self, want):
        """同じ段のふりがなが重なるときは、漢字のふりがなを残し、数字・英字などの読み
        （種類 FURI_RAW）を出さない。日付や時刻の読みは長い（2026/11/7 → ニセンニジュウ
        ロクネンジュウイチガツナノカ）ので、となりの漢字のふりがなに重なって、両方とも
        読めなくなっていた。漢字どうしが重なるときは、これまでどおり両方出す。"""
        rows = {}
        for item in want:
            rows.setdefault(item[2][1], []).append(item)   # 同じ高さ＝同じ段
        out = []
        for items in rows.values():
            items.sort(key=lambda it: it[2][0])
            placed = []   # [(右の端, 数字などの読みか, item)]
            for it in items:
                x0 = it[2][0]
                raw = it[3][3] == core.FURI_RAW
                skip = False
                while placed and x0 < placed[-1][0]:
                    if raw:
                        skip = True
                        break
                    if placed[-1][1]:
                        placed.pop()
                        continue
                    break
                if not skip:
                    placed.append((x0 + self._furi_widths.get(it[1][0], 0), raw, it))
            out.extend(p[2] for p in placed)
        return out

    def _furi_tag_bgs(self, first, last):
        """色の付いた印（選択・再生中・しおり・検索）の [(色, [(始め, 終わり)])]（強い順）と、
        本文の地の色。印の色は、ふりがなの余白まで塗られるので、ふりがなの下地も合わせる。
        選択の色は、本文欄に入力の焦点があるときだけ塗られる。調べるのは first〜last 行
        （見えている所）だけ。検索で何万か所に色が付いていても、スクロールを重くしない。"""
        try:
            focused = self.focus_get() is self.text
        except (KeyError, tk.TclError):
            focused = False
        lo, hi = f"{first}.0", f"{last}.end"
        marks = []
        for tag in reversed(self.text.tag_names()):   # 後ろほど強い
            if tag == "sel":
                bg = self.text.cget("selectbackground" if focused
                                    else "inactiveselectbackground")
            else:
                try:
                    bg = self.text.tag_cget(tag, "background")
                except tk.TclError:
                    bg = ""
            if not bg:
                continue
            spans = []
            # 見えている所より前から続いている印（行をまたぐ選択など）
            r = self.text.tag_prevrange(tag, lo)
            if r and self.text.compare(r[1], ">", lo):
                spans.append((self._furi_pos(r[0]), self._furi_pos(r[1])))
            at = lo
            while len(spans) < 5000:
                r = self.text.tag_nextrange(tag, at, hi)
                if not r:
                    break
                spans.append((self._furi_pos(r[0]), self._furi_pos(r[1])))
                at = r[1]
            if spans:
                marks.append((str(bg), spans))
        return marks, self.text.cget("bg")

    @staticmethod
    def _furi_pos(index):
        ln, col = str(index).split(".")
        return int(ln), int(col)

    def _furi_light(self, color):
        """色が明るいか（その上のふりがなを濃い色で書くか）。何度も聞くので覚えておく。"""
        light = self._furi_light_cache.get(color)
        if light is None:
            try:
                r, g, b = self.winfo_rgb(color)
                light = (0.299 * r + 0.587 * g + 0.114 * b) / 65535 > 0.5
            except tk.TclError:
                light = False
            self._furi_light_cache[color] = light
        return light

    @staticmethod
    def _furi_bg_at(ln, col, marks, base_bg):
        at = (ln, col)
        for bg, spans in marks:
            for s, e in spans:
                if s <= at < e:
                    return bg
        return base_bg

    def _furi_label(self, i):
        """i 番目のふりがな用ラベル（足りなければ作る）。クリックとホイールは本文へ渡す。"""
        while len(self._furi_labels) <= i:
            lab = tk.Label(self.text, bd=0, padx=0, pady=0, highlightthickness=0,
                           cursor="xterm", font=self._furi_font)
            self._furi_states.append({})   # 今の文字・色・位置（変わらなければ触らない）

            def click(e, lab=lab, idx=len(self._furi_labels)):
                span = self._furi_states[idx].get("span")
                if span and span[3] != core.FURI_OK:
                    return self._furi_fix(span, lab.cget("text"))
                x = lab.winfo_x() + e.x
                y = lab.winfo_y() + lab.winfo_height() + 2   # すぐ下の文字
                self.text.mark_set("insert", f"@{x},{y}")
                self.text.focus_set()
                return "break"

            def wheel(e):
                self._note_user_scroll()
                if e.num == 4 or getattr(e, "delta", 0) > 0:
                    self.text.yview_scroll(-1, "units")
                else:
                    self.text.yview_scroll(1, "units")
                return "break"
            lab.bind("<Button-1>", click)
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                lab.bind(seq, wheel)
            self._furi_labels.append(lab)
        return self._furi_labels[i]

    def _furi_fix(self, span, reading):
        """色つきのふりがな（辞書と違う読み＝誤読かもしれない所）を押したとき。その語を
        入れた状態で読み方辞書を開く。これまでは「語を選ぶ → 右クリック → 登録」の3手だった。"""
        ln, a, b, _kind = span
        word = self.text.get(f"{ln}.{a}", f"{ln}.{b}")
        if not word.strip():
            return "break"
        if not self._engine_ready():
            self.status_var.set("読み方辞書は、VOICEVOX につながると使えます。")
            return "break"
        self._register_word_to_dict(word)
        self.status_var.set(f"「{word}」は今「{reading}」と読んでいます。正しい読みを入れて"
                            "「追加/上書き」を押してね（ひらがなでもOK。送りがなも入れると確か）")
        return "break"

    # ---------------- 誤読候補を順に見て回る（次の候補 ▷・F8） ----------------
    _SEEK_BATCH = 24    # 一度にエンジンへ聞く行の数（探しながら、少しずつ聞く）
    _SEEK_AHEAD = 200   # 聞く行を集めるとき、どこまで先の行まで見るか

    def seek_misreading(self, back=False):
        """次（back なら前）の誤読候補（色つきのふりがな＝辞書と違う読み）へ移る。
        ふりがなは見えている行の分しか読みを調べていないので、まだ知らない行は
        エンジンに聞きながら先へ進む（聞くのは見つかるまでの分だけ。長い本でも、
        全部を先に調べない）。探している間にもう一度押すと、やめる。"""
        if self._seek is not None:
            self._seek_stop("誤読候補を探すのをやめました。")
            return "break"
        if not self.furigana_var.get():
            self.status_var.set("誤読候補は、本文の右上の「ふりがな」をオンにすると探せます。")
            return "break"
        if not self._engine_ready() or self._current_speaker() is None:
            self.status_var.set("誤読候補は、VOICEVOX につながると探せます。")
            return "break"
        if not self._furi_dict_installed():
            self.status_var.set("誤読候補を見分ける辞書（Janome）が入っていません。"
                                "本文の右クリックから入れられます。")
            return "break"
        try:   # 選んである語（前に見つけた候補など）の次から／前から
            if self.text.tag_ranges("sel"):
                here = self.text.index("sel.first" if back else "sel.last")
            else:
                here = self.text.index("insert")
        except tk.TclError:
            here = "1.0"
        ln, col = self._furi_pos(here)
        self._seek = {"back": back, "lines": self.text.get("1.0", "end-1c").split("\n"),
                      "ln": ln, "col": col, "start": ln, "wrapped": False,
                      "token": object()}
        self._seek_step()
        return "break"

    def _seek_back_click(self, event=None):
        """Shift を押しながら「次の候補」をクリック: 前の誤読候補へ。"""
        if self.furi_next_btn.instate(["!disabled"]):
            self.seek_misreading(back=True)
        return "break"

    def _seek_step(self):
        """誤読候補を探す本体。読みを知っている行は続けて調べ、知らない行に来たら、
        その先の何行かをまとめてエンジンに聞く（答えが来たら、ここから続ける）。
        端まで来たら、反対の端から、探し始めた行まで回る。"""
        st = self._seek
        if st is None or self._destroying:
            return
        lines, back = st["lines"], st["back"]
        step = -1 if back else 1
        keyinfo = self._furi_keyinfo()
        for _ in range(500):   # 一度に調べる行の数（多いと、その間は画面が止まる）
            ln = st["ln"]
            if st["wrapped"] and (ln < st["start"] if back else ln > st["start"]):
                self._seek_stop(f"誤読候補は見つかりませんでした（{len(lines):,}行すべて見ました）。")
                return
            if not 1 <= ln <= len(lines):
                st["wrapped"] = True   # 端まで来たので、反対の端から続ける
                st["ln"], st["col"] = (len(lines) if back else 1), None
                continue
            item = self._furi_line(lines[ln - 1])
            if item is not None:
                off, spoken = item
                rubies = self._furi_cache.get((spoken,) + keyinfo)
                if rubies is None:
                    self._seek_fetch(keyinfo)   # 知らない行（とその先）をエンジンに聞く
                    return
                col = st["col"]
                hits = [(off + a, off + b, reading) for a, b, reading, kind in rubies
                        if kind == core.FURI_DIFF and (
                            col is None or (off + b <= col if back else off + a >= col))]
                if hits:
                    self._seek_found(ln, *(hits[-1] if back else hits[0]))
                    return
            st["ln"], st["col"] = ln + step, None
        self.after(1, self._seek_step)   # 続きは次の回に（その間に画面を描く）

    def _seek_fetch(self, keyinfo):
        """まだ読みを知らない行を、探している向きに何行かまとめて、裏でエンジンに聞く。"""
        st = self._seek
        sp = self._current_speaker()
        if sp is None or not self._engine_ready():
            self._seek_stop("VOICEVOX につながらないので、誤読候補を探すのをやめました。")
            return
        lines, step = st["lines"], (-1 if st["back"] else 1)
        texts, ln = [], st["ln"]
        for _ in range(self._SEEK_AHEAD):
            if not 1 <= ln <= len(lines) or len(texts) >= self._SEEK_BATCH:
                break
            item = self._furi_line(lines[ln - 1])
            if item is not None and (item[1],) + keyinfo not in self._furi_cache:
                texts.append(item[1])
            ln += step
        texts = list(dict.fromkeys(texts))
        turn = ""
        if st["wrapped"]:
            turn = "最初まで見たので、最後から。" if st["back"] else "最後まで見たので、最初から。"
        self.status_var.set(f"誤読候補を探しています…（{st['ln']:,} / {len(lines):,} 行目。{turn}"
                            "もう一度押すか Esc で、やめます）")
        base, sid, token = self.base_url, sp[1], st["token"]

        def worker():
            import requests
            got, failed = {}, False
            for t in texts:
                seek = self._seek
                if seek is None or seek["token"] is not token:
                    break   # やめた（残りの行は聞かない）
                try:
                    got[t] = core.vv_furigana(base, t, sid,
                                              use_dictionary=keyinfo[2], timeout=10,
                                              read_fix=keyinfo[3])
                except requests.HTTPError:
                    got[t] = []     # エンジンがこの行を受け付けなかった
                except (requests.ConnectionError, requests.Timeout):
                    failed = True   # エンジンが止まった等
                    break
                except Exception:
                    got[t] = []     # この行だけ読みを出せない（ほかの行は続ける）
            self.q.put(("furi_seek", got, keyinfo, failed, token))
        threading.Thread(target=worker, daemon=True).start()

    def _seek_got(self, got, keyinfo, failed, token):
        """探しながら聞いた読みが届いた。覚えて（見えている行のふりがなにも使う）、続きを探す。"""
        self._furi_remember(got, keyinfo)
        if got:
            self._furi_schedule(0)
        st = self._seek
        if st is None or st["token"] is not token:
            return   # もう探していない（やめた・別に探し始めた）
        if failed:
            self._seek_stop("VOICEVOX につながらないので、誤読候補を探すのをやめました。")
            return
        self._seek_step()

    def _seek_found(self, ln, a, b, reading):
        """見つけた誤読候補の語を選び、本文欄の見やすい所に出す。"""
        st, self._seek = self._seek, None
        if self.text.get(f"{ln}.0", f"{ln}.end") != st["lines"][ln - 1]:
            # 探している間に本文が書き換わった。今の本文で、この行から探し直す
            st["lines"] = self.text.get("1.0", "end-1c").split("\n")
            self._seek = st
            self._seek_step()
            return
        word = self.text.get(f"{ln}.{a}", f"{ln}.{b}")
        self.text.tag_remove("sel", "1.0", "end")
        self.text.tag_add("sel", f"{ln}.{a}", f"{ln}.{b}")
        self.text.mark_set("insert", f"{ln}.{b}")
        self._seek_show(ln)
        self.text.focus_set()
        turn = ""
        if st["wrapped"]:
            turn = "（最初まで見たので、最後から探しました）" if st["back"] else \
                   "（最後まで見たので、最初から探しました）"
        self.status_var.set(f"誤読候補（{ln:,}行目）：「{word}」を「{reading}」と読んでいます。"
                            f"直すときは、色つきのふりがなを押してね。次は F8{turn}")

    def _seek_show(self, ln):
        """その行が見えていなければ（端にあっても）、本文欄の上から3分の1あたりへ動かす
        （端に出すと、前後の行が読めない）。"""
        try:
            first = int(self.text.index("@0,0").split(".")[0])
            last = int(self.text.index(f"@0,{self.text.winfo_height()}").split(".")[0])
        except tk.TclError:
            return
        if first < ln < last:
            return
        self.text.yview(f"{max(1, ln - max(1, (last - first) // 3))}.0")

    def _seek_stop(self, msg=None):
        """誤読候補を探すのをやめる（それまでに聞いた読みは覚えておく）。"""
        self._seek = None
        if msg:
            self.status_var.set(msg)

    def _furi_requirement(self):
        """requirements.txt に書いてある辞書（janome）の指定。版の決まりを1か所にするため。"""
        try:
            with open(os.path.join(core.APP_DIR, "requirements.txt"), encoding="utf-8") as f:
                for raw in f:
                    line = raw.split("#")[0].strip()
                    if line.lower().startswith("janome"):
                        return line
        except OSError:
            pass
        return "janome>=0.5,<1"

    def _furi_install_dict(self, ask=False):
        """ふりがなの辞書（Janome）を入れる。ふだんは初回セットアップで入っている
        （requirements.txt）。入っていないとき（セットアップ前の版から使い続けている等）に、
        アプリと同じ Python へ pip で入れ直す。"""
        if self._furi_installing:
            return
        if ask and not messagebox.askyesno(
                "ふりがな",
                "ふりがなを、正しい書き方のひらがなで出すための辞書（Janome）を入れます。\n\n"
                "・ダウンロード約20MB（Windows は入れたあと圧縮して約50MB）\n"
                "・ネットにつながっている必要があります\n\n入れますか？", parent=self):
            return
        self._furi_installing = True
        self.status_var.set("ふりがなの辞書を入れています…（ダウンロード約20MB。1分ほどかかることがあります）")
        spec = self._furi_requirement()
        exe = sys.executable
        if core.IS_WIN:
            py = os.path.join(os.path.dirname(exe), "python.exe")
            exe = py if os.path.exists(py) else exe

        def worker():
            import subprocess
            try:
                r = subprocess.run(
                    [exe, "-m", "pip", "install", "--disable-pip-version-check",
                     spec], cwd=core.APP_DIR, capture_output=True, text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=900)
                ok, info = r.returncode == 0, (r.stderr or r.stdout or "")[-600:]
            except Exception as e:
                ok, info = False, str(e)
            if ok and core.IS_WIN:
                # セットアップ（winsetup.shrink_venv）と同じく、Windows の圧縮機能で
                # 約250MB → 約50MB にする。中身はそのまま。できなくても動作には影響しない
                try:
                    import sysconfig
                    dic = os.path.join(sysconfig.get_paths()["purelib"], "janome")
                    if os.path.isdir(dic):
                        subprocess.run(["compact", "/c", "/s:" + dic, "/i", "/q", "/exe:lzx"],
                                       capture_output=True, timeout=600,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass
            self.q.put(("furigana_dict", ok, info))
        threading.Thread(target=worker, daemon=True).start()

    def _furi_dict_done(self, ok, info):
        self._furi_installing = False
        core.reset_furigana_dictionary()
        self._furi_has_dict = None   # 入ったかどうかを調べ直す
        if ok and self._furi_dict_installed():
            self.status_var.set("ふりがなの辞書が入りました。ふりがなを、ひらがなで出します。")
            self._furi_schedule(0)
        else:
            self.status_var.set("ふりがなの辞書を入れられませんでした。")
            messagebox.showwarning(
                "ふりがな", "ふりがなの辞書を入れられませんでした。\n"
                "ネットにつながっているか確かめて、本文の右クリックから"
                "もう一度お試しください。\n\n" + info.strip()[-300:], parent=self)

    # ---------------- 本文の全消去 / 復元 ----------------
    def _on_text_modified(self, event=None):
        """本文が全消去/復元“以外”の理由で変更されたら、復元ポイントを無効化する。
        抽出・クリップボード・置換・ルール適用・手動編集・Ctrl/Cmd+Zのどれでも一元的に捕捉し、
        古い退避内容で新しい本文を上書きしてしまう「復元」事故を防ぐ。"""
        self._furi_schedule(400)   # 本文が変わったら、ふりがなも作り直す
        if self._suppress_modified or not self.text.edit_modified():
            return
        # 変更フラグを戻す。edit_modified(False)自体が再度<<Modified>>を発火するため多重防止。
        self._suppress_modified = True
        try:
            self.text.edit_modified(False)
        finally:
            self._suppress_modified = False
        if self._cleared_text is not None:
            # 本文が変わったので古い退避内容は破棄し、ボタンを「全消去」へ戻す
            self._cleared_text = None
            self.restore_btn.config(text="本文を全消去", command=self.clear_text,
                                    style="Danger.Tool.TButton")
        self._update_step_highlight()
        self._schedule_stats()   # 行数・文字数・めやす表示を更新（デバウンス付き）

    def _edit_body(self, fn):
        """本文の全消去/復元まわりの自編集を、<<Modified>>フックに拾わせずに実行する。"""
        self._suppress_modified = True
        try:
            self.text.edit_separator()
            fn()
            self.text.edit_separator()
            self.text.edit_modified(False)
        finally:
            self._suppress_modified = False

    def clear_text(self):
        """抽出結果の本文を一気に消去する。直前の内容は「復元」ボタンで戻せるほか、
        テキストのUndo（Ctrl/Cmd+Z）でも戻せる。"""
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        content = self.text.get("1.0", "end-1c")
        if not content.strip():
            self.status_var.set("本文は空です。消去するものがありません。")
            return
        self._edit_body(lambda: self.text.delete("1.0", "end"))
        self._cleared_text = content
        self._cleared_bookmark = self._bookmark   # 「復元」で一緒に戻す
        self._reset_bookmark()
        self._hide_resume()
        self.restore_btn.config(text="復元", command=self.restore_text,
                                style="Tool.TButton")
        self._update_step_highlight()
        self._schedule_stats()
        self.status_var.set("本文を全消去しました（「復元」ボタン／Ctrl・Cmd+Zで戻せます）。")

    def restore_text(self):
        """「本文を全消去」で消した内容を元に戻す。"""
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        if not self._cleared_text:
            self.status_var.set("復元できる内容がありません。")
            return
        restored = self._cleared_text

        def _do():
            self.text.delete("1.0", "end")
            self.text.insert("1.0", restored)
        self._edit_body(_do)
        self._cursor_to_top()
        self._cleared_text = None
        if getattr(self, "_cleared_bookmark", None) is not None:
            self._bookmark = self._cleared_bookmark   # 消す前のしおりも戻す
            self._mark_bookmark()
        self._cleared_bookmark = None
        self.restore_btn.config(text="本文を全消去", command=self.clear_text,
                                style="Danger.Tool.TButton")
        self._update_step_highlight()
        self._schedule_stats()
        self.status_var.set("本文を復元しました。")

    # ---------------- エラーの人間語化 ----------------
    # (正規表現, やさしい説明と次の一手)。上から順に最初にマッチしたものを使う
    _ERROR_HINTS = [
        (r"ConnectionError|Connection refused|Failed to establish|Max retries",
         "VOICEVOX につながりませんでした。\n"
         "VOICEVOXが起動しているか確認してね（左下の「VOICEVOX起動」からも起動できるよ）。"),
        (r"ReadTimeout|timed out",
         "VOICEVOX の返事が遅いみたい…。\n"
         "長い文・重い処理は時間がかかることがあるよ。少し待ってからもう一度どうぞ。"),
        (r"PermissionError|Permission denied",
         "保存先に書き込めませんでした。\n"
         "別のフォルダを選ぶか、フォルダの権限を確認してみてね。"),
        (r"No space left",
         "ディスクの空き容量が足りないみたい…。\n"
         "不要なファイルを整理してからもう一度どうぞ。"),
        (r"FileNotFoundError|No such file",
         "ファイルが見つかりませんでした。\n"
         "移動・削除されていないか確認して、もう一度追加してみてね。"),
        (r"PdfiumError|password",
         "PDFを開けませんでした。\n"
         "ファイルが壊れているか、パスワード付きPDFの可能性があるよ。"),
        (r"MemoryError",
         "メモリが足りませんでした。\n"
         "① の設定の「細かさ」を下げるか、ファイルを分けて試してみてね。"),
        # 以下は「つながってはいるのに失敗した」系。原因も対処もまったく違うので分ける
        (r"Failed to play sound|PlaySound|winsound",
         "音を鳴らせませんでした。\n"
         "音声そのものは作れているので、ヘッドホン／スピーカーの接続と、\n"
         "Windowsの音量ミキサー（アプリごとの音量）を確認してみてね。"),
        (r"wave\.Error|does not start with RIFF|unknown format",
         "音声データが壊れていました。\n"
         "③ 保存の右上の「声の保管…」で、しまってある声を消して、もう一度お試しください。"),
        (r"HTTPError|Client Error|Server Error|422|500 Server",
         "VOICEVOXが読み上げを断りました。\n"
         "記号だけの行や、極端に長い行で起きることがあるよ。\n"
         "その行を短く区切るか、記号を減らして試してみてね。"),
    ]

    ERROR_LOG_PATH = os.path.join(APP_DIR, "エラー.log")
    _ERROR_LOG_MAX = 200 * 1024   # これを超えたら古い半分を捨てる

    def _log_error(self, tb, where=""):
        """エラーの詳細をファイルにも残す。ダイアログの「詳細を表示」は閉じると
        消えてしまい、後から「あのとき何が起きたのか」を追えなかった。
        ログのせいでアプリが止まっては本末転倒なので、失敗しても黙って諦める。"""
        try:
            path = self.ERROR_LOG_PATH
            old = ""
            try:
                if os.path.getsize(path) > self._ERROR_LOG_MAX:
                    # 肥大化させない。古い半分を捨てて、最近のぶんだけ残す
                    with open(path, encoding="utf-8", errors="replace") as f:
                        old = f.read()[-self._ERROR_LOG_MAX // 2:]
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("（ここより前の古い記録は容量のため削除しました）\n")
                        f.write(old)
            except OSError:
                pass   # まだファイルが無い等
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            head = f"\n==== {stamp}  {where or '不明な処理'} ====\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(head + str(tb).rstrip() + "\n")
        except Exception:
            pass

    def _show_friendly_error(self, tb, where="", lead=""):
        """エラーを人間の言葉で伝える。原因のヒントと次の一手を提案し、
        技術的な詳細（traceback）は「詳細を表示」を押したときだけ見せる
        （従来は生のtracebackがいきなり表示されて不親切だった）。
        lead は文頭に添える一言（どの行で止まったか、など）。"""
        self._log_error(tb, where)
        friendly = ("うまくいきませんでした…ごめんなさい！\n"
                    "もう一度試しても続くようなら「詳細を表示」の内容を添えて教えてね。")
        for pat, msg in self._ERROR_HINTS:
            if re.search(pat, tb):
                friendly = msg
                break
        if lead:
            friendly = f"{lead}\n{friendly}"
        win = tk.Toplevel(self)
        win.title("エラー")
        win.transient(self)
        self._place_dialog(win)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frm, text="⚠️ " + friendly, wraplength=_px(480),
                  justify="left").pack(anchor="w")
        ttk.Label(frm, style="Credit.TLabel", wraplength=_px(480), justify="left",
                  text="詳しい内容は" + _in_app_folder("エラー.log") + "にも保存したよ。"
                  ).pack(anchor="w", pady=(6, 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0), side="bottom")
        ttk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")
        detail = [None]

        def _toggle_detail():
            if detail[0] is None:
                df = ttk.Frame(frm)
                txt = tk.Text(df, height=10, wrap="word")
                sb = ttk.Scrollbar(df, command=txt.yview)
                txt.config(yscrollcommand=sb.set,
                           bg=self.text.cget("bg"), fg=self.text.cget("fg"))
                txt.insert("1.0", tb[-3000:])
                txt.config(state="disabled")
                txt.pack(side="left", fill="both", expand=True)
                sb.pack(side="right", fill="y")
                df.pack(fill="both", expand=True, pady=(8, 0))
                detail[0] = df
            else:
                detail[0].destroy()
                detail[0] = None
        ttk.Button(btns, text="詳細を表示",
                   command=_toggle_detail).pack(side="right", padx=6)
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])

    # ---------------- 思いがけないエラー ----------------
    # 続けて起きたとき、窓を出すのは30秒に1回まで（記録は毎回残す）
    _UNEXPECTED_QUIET_SEC = 30

    def _report_unexpected(self, tb, where):
        """作るときに考えていなかったエラーを、記録して知らせる。
        pythonw には黒い画面が無いので、以前は何も起きないように見えて、記録も残らなかった。"""
        now = time.monotonic()
        if now - getattr(self, "_last_unexpected", -1e9) < self._UNEXPECTED_QUIET_SEC:
            self._log_error(tb, where)
            return
        self._last_unexpected = now
        try:
            self.status_var.set("思いがけないエラーが起きました（内容を確認してね）")
            self._show_friendly_error(tb, where)
        except Exception:
            self._log_error(tb, where)

    def report_callback_exception(self, exc, val, tb):
        """ボタン・キー操作・タイマーの中で起きたエラー（Tk が呼ぶ。元は黒い画面に出すだけ）。"""
        text = "".join(traceback.format_exception(exc, val, tb))
        last = tb
        while last is not None and last.tb_next is not None:
            last = last.tb_next
        where = "ボタンなどの処理"
        if last is not None:
            where += "（%s）" % last.tb_frame.f_code.co_name
        self._report_unexpected(text, where)

    def _thread_exception(self, args):
        """裏で動く処理（スレッド）の中で、受け止めていなかったエラー（threading.excepthook）。
        Tk は別のスレッドから触れないので、知らせるのは画面のスレッドに頼む。"""
        if args.exc_type is SystemExit:
            return
        text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
        name = getattr(args.thread, "name", "") or "?"
        self.q.put(("unexpected", text, "裏で動く処理（%s）" % name))

    # ---------------- 文字の読み取りを強くする案内 ----------------
    def open_strong_ocr_help(self, reason=False):
        """強い文字読み取りの説明と、入れるボタンを出す（Windows）。
        reason=True のときは「いま読んだ画像が英文だった」という前置きを添える。
        この窓自体は何もインストールしない。実行するかどうかは本人が決める。"""
        if (getattr(self, "_ocr_help_win", None) is not None
                and self._ocr_help_win.winfo_exists()):
            self._ocr_help_win.lift()
            return
        installed = core.strong_ocr_available()
        setup_py = os.path.join(core.APP_DIR, "winsetup.py")
        win = tk.Toplevel(self)
        self._ocr_help_win = win
        win.title("文字の読み取りを強くする")
        win.transient(self)
        self._place_dialog(win)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        W = _px(520)

        if reason:
            ttk.Label(frm, text="いま読み取った画像に、英語の文が多くありました。",
                      wraplength=W, justify="left").pack(anchor="w", pady=(0, 6))
        if installed:
            ttk.Label(frm, wraplength=W, justify="left",
                      text="● 入っています。画像やスクリーンショットの文字は、"
                           "これで読んでいます。").pack(anchor="w")
            ttk.Label(frm, wraplength=W, justify="left",
                      text="読み取りはこのPCの中だけで行い、文字も画像も外へ送りません。\n"
                           "やめるときは、" + _in_app_folder("ocr_engine") + "を消してください。"
                      ).pack(anchor="w", pady=6)
        else:
            ttk.Label(frm, wraplength=W, justify="left",
                      text="― 文字を読む部品が入っていません。いまは Windows の読み取りで読んでいます。\n"
                           "ふつうは最初のセットアップで入ります。入らなかったときは、ここから入れられます。"
                      ).pack(anchor="w")
            ttk.Label(frm, wraplength=W, justify="left",
                      text="入れると、ゲームや写真の上の文字、小さな文字、英語まじりの文まで、"
                           "ずっと正しく読めます。\n"
                           "試した258枚の画像で、正しく読めた文字が 89% → 99.7% になりました。\n"
                           "読み取りはこのPCの中だけで行い、文字も画像も外へ送りません。"
                      ).pack(anchor="w", pady=6)
            ttk.Label(frm, wraplength=W, justify="left",
                      text=f"{core.OCR_ENGINE_SIZE_NOTE}\n"
                           "入れなくても、アプリはこれまでどおり使えます。"
                      ).pack(anchor="w", pady=(0, 6))
            ttk.Label(frm, wraplength=W, justify="left", style="Credit.TLabel",
                      text="英語の画像だけ読めればよいときは、Windowsに「英語（米国）」を"
                           "足す方法もあります（アプリは大きくなりません）。"
                      ).pack(anchor="w", pady=(0, 4))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0), side="bottom")
        ttk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")
        self._ocr_help_buttons = {}
        if not installed:
            def open_settings():
                try:
                    os.startfile("ms-settings:regionlanguage")
                except Exception:
                    messagebox.showinfo(
                        "情報", "設定アプリを開けませんでした。\n"
                        "［設定］→［時刻と言語］→［言語と地域］から"
                        "「英語（米国）」を追加してください。")
            b1 = ttk.Button(btns, text="Windowsの言語設定を開く",
                            command=open_settings)
            b1.pack(side="right", padx=6)
            self._ocr_help_buttons["settings"] = b1

            if os.path.exists(setup_py):
                def start_install():
                    import subprocess
                    try:
                        # 黒い窓で winsetup.py ocr を動かす
                        # （.bat は Windows に止められるので、v1.24.0 から使わない）
                        py = os.path.join(os.path.dirname(sys.executable),
                                          "python.exe")
                        subprocess.Popen(
                            [py if os.path.exists(py) else sys.executable,
                             setup_py, "ocr"], cwd=core.APP_DIR,
                            creationflags=getattr(
                                subprocess, "CREATE_NEW_CONSOLE", 0))
                        b2.state(["disabled"])   # 2つ同時に入れないように
                        self.status_var.set(
                            "文字の読み取りを入れ始めました"
                            "（黒い画面の指示に従ってね）。")
                    except Exception:
                        messagebox.showinfo(
                            "情報", "開けませんでした。PowerShell に1行導入の1行をもう一度"
                            "貼り付けると、一緒に入り直します。"
                            if os.path.isdir(os.path.join(core.APP_DIR, "runtime")) else
                            "開けませんでした。" + _in_app_folder("venv") + "を消してから"
                            "起動.pyw を開くと、セットアップと一緒に入り直します。")
                b2 = ttk.Button(btns, text="入れる", style="Primary.TButton",
                                command=start_install)
                b2.pack(side="right", padx=6)
                self._ocr_help_buttons["install"] = b2
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])

    def _warn_with_english(self, warnings):
        """警告を出す。英語の読み取りの案内が混ざっていたら、文章を読ませて終わりに
        せず、「どうすれば読めるか」まで案内する小窓に回す。"""
        rest = [w for w in warnings if w != core.OCR_ENGLISH_MISSING_MSG]
        if rest:
            messagebox.showwarning("注意", "\n".join(rest))
        if len(rest) != len(warnings):
            self.open_strong_ocr_help(reason=True)

    # ---------------- 音声キャッシュの管理ダイアログ ----------------
    def open_cache_dialog(self):
        """合成キャッシュの容量確認・上限変更・クリアの小窓（多重表示防止付き）。"""
        if (getattr(self, "_cache_win", None) is not None
                and self._cache_win.winfo_exists()):
            self._cache_win.lift()
            return
        win = tk.Toplevel(self)
        self._cache_win = win
        win.title("声の保管")
        win.transient(self)
        win.resizable(False, False)
        self._place_dialog(win)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frm, justify="left", text=(
            "一度作った声（同じ行・同じ声）をしまっておき、作り直しや連続再生を速くしています。\n"
            "消しても大丈夫です（要るときに、また作られます）。\n"
            "長い本（3時間超）をよく作るなら、上限を大きめにすると効きが良くなります。"
        )).pack(anchor="w")
        stat_var = tk.StringVar()
        ttk.Label(frm, textvariable=stat_var).pack(anchor="w", pady=(8, 0))

        def _refresh():
            n, total = core.synth_cache_stats()
            limit = core._SYNTH_CACHE_MAX_BYTES // (1024 * 1024)
            stat_var.set(f"現在: {n}ファイル・{total / 1048576:.1f}MB"
                         f"／上限 {limit}MB")

        row = ttk.Frame(frm)
        row.pack(anchor="w", pady=(6, 0))
        ttk.Label(row, text="上限(MB):").pack(side="left")
        mb_var = tk.IntVar(value=core._SYNTH_CACHE_MAX_BYTES // (1024 * 1024))
        ttk.Spinbox(row, from_=50, to=100000, increment=250, width=7,
                    textvariable=mb_var).pack(side="left", padx=4)

        def _apply_limit():
            # 上限を下げたら即座に超過分を削除する（evict=True。実行中ジョブが
            # あっても _synth_cache_evict は保護中エントリを尊重する）。数千
            # ファイルの削除でUIが固まらないよう別スレッドで走らせ、後で再表示
            mb = self._get_num(mb_var, 500)

            def _work():
                core.set_synth_cache_limit(mb, evict=True)
                self.q.put(("cache_limit_applied",))
            self._cache_refresh = _refresh
            threading.Thread(target=_work, daemon=True).start()
            self.status_var.set("しまう量の上限を変えています…"
                                "（次回起動にも引き継がれます）。")
        ttk.Button(row, text="適用", command=_apply_limit).pack(side="left")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))

        def _clear():
            if self.busy or self._previewing:
                self.status_var.set("処理・再生の実行中は消せません。")
                return
            n = core.synth_cache_clear()
            self.status_var.set(f"しまってあった声を{n}件消しました。")
            _refresh()

        def _open_dir():
            try:
                os.makedirs(core.SYNTH_CACHE_DIR, exist_ok=True)
            except OSError:
                pass
            self._open_output_location(core.SYNTH_CACHE_DIR)
        ttk.Button(btns, text="しまった声を消す", command=_clear).pack(side="left")
        ttk.Button(btns, text="フォルダを開く",
                   command=_open_dir).pack(side="left", padx=6)
        ttk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])
        _refresh()

    # ---------------- 音声生成の完了ダイアログ ----------------
    def _show_done_dialog(self, info, target, credit):
        """音声生成の完了ダイアログ。保存先を開く・クレジット表記のコピーがその場で
        できる（クレジットはVOICEVOX利用規約で公開時に必要。コピー導線で漏れを防ぐ）。"""
        win = tk.Toplevel(self)
        win.title("🎉 できあがり！")
        win.transient(self)
        win.resizable(False, False)
        self._place_dialog(win)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=_px(14), pady=_px(12))
        # 幅は表示の倍率に合わせる（px のままだと、250%表示では保存先が6行に折れていた）
        wrap = _px(520)
        ttk.Label(frm, text=info, wraplength=wrap,
                  justify="left").pack(anchor="w")
        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=_px(8))
        ttk.Label(frm, text="音声を公開するときは、このクレジット表記をどこかに載せてね:",
                  wraplength=wrap, justify="left").pack(anchor="w")
        ttk.Label(frm, text=credit, style="Cluster.TLabel",
                  wraplength=wrap, justify="left").pack(anchor="w", pady=(_px(2), 0))
        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))

        def _copy_credit():
            self.clipboard_clear()
            self.clipboard_append(credit)
            self.status_var.set("クレジット表記をコピーしました。")
        ttk.Button(btns, text="📂 保存先を開く",
                   command=lambda: self._open_output_location(target)
                   ).pack(side="left")
        ttk.Button(btns, text="📋 クレジットをコピー",
                   command=_copy_credit).pack(side="left", padx=6)
        ttk.Button(btns, text="閉じる", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])

    def _open_output_location(self, path):
        """保存先をFinder/エクスプローラーで開く（ファイルなら選択状態で表示）。"""
        try:
            core.reveal_in_file_manager(path)
        except Exception as e:
            self.status_var.set(f"保存先を開けませんでした: {e}")

    # ---------------- 整形レポート（何が消え・何が直ったか） ----------------
    def _merge_report(self, report):
        """抽出/クリップボードOCRの整形レポートを蓄積し、ボタンの有効状態を更新する。
        戻り値は (除去行数, 補正件数)。"""
        for k in ("removed", "confusables", "heads"):
            if report.get(k):
                self._shape_report.setdefault(k, []).extend(report[k])
        n_r = len(self._shape_report.get("removed", []))
        n_c = len(self._shape_report.get("confusables", []))
        n_h = len(self._shape_report.get("heads", []))
        self.report_btn.config(state="normal" if (n_r or n_c or n_h) else "disabled")
        try:
            if n_r or n_c or n_h:
                if not self.report_btn.winfo_manager():
                    self.report_btn.pack(side="right", fill="y", padx=(GAPX, 0),
                                         before=self.extract_btn)
            elif self.report_btn.winfo_manager():
                self.report_btn.pack_forget()
        except tk.TclError:
            pass
        return n_r, n_c

    def show_shape_report(self):
        """ノイズ除去で消えた行・OCR誤字補正の内容を一覧表示する。
        誤って消えた本文はここからコピーして本文へ戻せる（読み取り専用でも選択・コピー可）。"""
        removed = self._shape_report.get("removed", [])
        conf = self._shape_report.get("confusables", [])
        heads = self._shape_report.get("heads", [])
        if not removed and not conf and not heads:
            self.status_var.set("表示できる「直した所」がありません（取り出したあとに使えます）。")
            return
        # 多重表示防止: 既存ウィンドウは破棄して常に最新内容で作り直す
        if self._report_win is not None and self._report_win.winfo_exists():
            self._report_win.destroy()
        win = tk.Toplevel(self)
        self._report_win = win
        win.title("直した所")
        # 表示の倍率に合わせる（640x480 ピクセルの決め打ちだと、250%表示では小さすぎて
        # 「閉じる」が切れ、中身も数行しか見えなかった）。125%までは、これまでと同じ大きさ
        win.geometry(f"{max(640, _px(512))}x{max(480, _px(384))}")
        win.transient(self)
        self._place_dialog(win)
        head = ttk.Frame(win)
        head.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(head, text="自動で直した所（選んでコピーできます）").pack(side="left")
        ttk.Button(head, text="閉じる", command=win.destroy).pack(side="right")
        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=8, pady=8)
        txt = tk.Text(body, wrap="word")
        sb = ttk.Scrollbar(body, command=txt.yview)
        txt.config(yscrollcommand=sb.set,
                   bg=self.text.cget("bg"), fg=self.text.cget("fg"))
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        content = []
        if heads:
            content.append(f"■ ページの見出し（柱）・ページ番号として外した行（{len(heads)}行）")
            content.append("  ※第一章などの章の見出しは、最初の1回だけ本文に残しています。")
            content.append("  ※① の「設定」の「PDF のページの見出しと番号を読まない」を外すと、外さなくなります。")
            content.append("")
            content.extend(f"  {ln.strip()}" for ln in heads)
            content.append("")
        if removed:
            content.append(f"■ ノイズ除去で消えた行（{len(removed)}行）")
            content.append("  ※本文だった行が消えていたら、ここからコピーして戻してください。")
            content.append("  ※① の「設定」の「画面キャプチャのノイズを除去」をOFFにすると消えなくなります。")
            content.append("")
            content.extend(f"  {ln.strip()}" for ln in removed)
            content.append("")
        if conf:
            content.append(f"■ 読み取りの間違いを直した所（{len(conf)}箇所）")
            content.append("  ※① の「設定」の「読み取りの間違いを直す」を外すと、直さなくなります。")
            content.append("")
            content.extend(f"  {b.strip()}\n   → {a.strip()}" for b, a in conf)
        txt.insert("1.0", "\n".join(content))
        txt.config(state="disabled")
        win.bind("<Escape>", lambda e: (win.destroy(), "break")[1])

    # ---------------- 置換ルールの保存・適用 ----------------
    def _rule_labels(self):
        return [f"{f} → {r}" for f, r in self.replace_rules]

    def _refresh_rules(self):
        self.rule_cb.config(values=self._rule_labels())

    def _rule_selected(self, event=None):
        i = self.rule_cb.current()
        if 0 <= i < len(self.replace_rules):
            f, r = self.replace_rules[i]
            self.find_var.set(f)
            self.repl_var.set(r)

    def add_rule(self):
        find = self.find_var.get()
        if not find:
            self.status_var.set("置き換える前の言葉を入れてください。")
            return
        rule = [find, self.repl_var.get()]
        if rule in self.replace_rules:
            self.status_var.set("同じ置き換えが、もう登録されています。")
            return
        self.replace_rules.append(rule)
        self._refresh_rules()
        self.rule_cb.current(len(self.replace_rules) - 1)
        self.status_var.set(f"置き換えを登録しました：「{rule[0]}」→「{rule[1]}」（次に開いたときも使えます）")

    def del_rule(self):
        i = self.rule_cb.current()
        if not (0 <= i < len(self.replace_rules)):
            self.status_var.set("消す登録を選んでください。")
            return
        f, r = self.replace_rules.pop(i)
        self._refresh_rules()
        self.rule_cb.set("")
        self.status_var.set(f"登録を消しました：「{f}」→「{r}」")

    def apply_all_rules(self):
        if not self.replace_rules:
            self.status_var.set(
                "登録した置き換えがありません（「足す・消す ▼」→「今の置き換えを登録する」で足せます）。")
            return
        content = self.text.get("1.0", "end-1c")
        total = 0
        for f, r in self.replace_rules:
            c = content.count(f)
            if c:
                content = content.replace(f, r)
                total += c
        if total == 0:
            self.status_var.set("置き換える言葉が見つかりませんでした。")
            return
        self.text.edit_separator()  # Undo境界（Ctrl+Zで戻せる）
        ins, y = self.text.index("insert"), self.text.yview()[0]
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self._restore_view(ins, y)   # 読み進めていた位置を見失わない
        self.status_var.set(f"登録した{len(self.replace_rules)}個の置き換えで、{total}か所を置き換えました。")

    # ---------------- テーマ（選択式・4種） ----------------
    # すべて clam ベースの統一配色。ライト/ダーク＝落ち葉、くっきり＝高コントラスト、
    # ずんだ＝ずんだもんの若草色。文字と地の色差（コントラスト）を優先して選定。
    LIGHT = dict(  # 空と落ち葉：水色の帯に白い面。大事なボタンは落ち葉のオレンジ
        bg="#fbfdff", card="#fbfdff", field="#ffffff", textbg="#ffffff", ground="#cfe6f6",
        fg="#22333f", subtle="#5e7685", head_fg="#1f5f8f",
        accent="#c4500c", accent_hi="#d9601a", accent_fg="#ffffff",
        btn="#e4f2fb", btn_hi="#d3e9f8", border="#d8e6f0",
        sel="#cfe8fb", disabled="#a9bccb", ok="#2f7d3f",
        tool="#37596f", danger="#b3261e",
        # 枠は面より少し濃いだけにする（濃い枠が並ぶと、線ばかりが目に入る）
        act="#e3f0fa", act_hi="#cfe6f7", act_border="#a9cde8", quiet_border="#a6c1d4",
        # 上の帯の文字（無いテーマは fg・head_fg・subtle・accent）
        ground_fg="#1d3a4f", ground_title="#123a5a", ground_sub="#3a6480", ground_link="#a03c08",
        steps=("#c4500c", "#2a7d44", "#1f6fb5"),   # ① ② ③ の見出し（無いテーマは head_fg）
        caption="#cfe6f6", caption_fg="#123a5a",   # タイトルバー（無いテーマは OS の色）
    )
    DARK = dict(
        bg="#272320", card="#302b26", field="#3a342d", textbg="#1f1c19", ground="#1a1714",
        fg="#ece3d5", subtle="#a89b89", head_fg="#f0e6d6",
        accent="#e08a45", accent_hi="#ef9a55", accent_fg="#241a12",
        btn="#3c362f", btn_hi="#4a433a", border="#4a4239",
        sel="#5c4632", disabled="#6b6255", ok="#8fd694",
        tool="#c9bba7", danger="#f08a76",
        act="#4a4136", act_hi="#594e41", act_border="#76674f", quiet_border="#85765e",
    )
    HC = dict(  # くっきり：白地＋濃紺文字＋強い枠線。小さな文字も読みやすく
        bg="#ffffff", card="#ffffff", field="#ffffff", textbg="#ffffff", ground="#dfe3e8",
        fg="#111111", subtle="#3d3d3d", head_fg="#000000",
        accent="#0a58ca", accent_hi="#2f74d8", accent_fg="#ffffff",
        btn="#ededed", btn_hi="#dcdcdc", border="#5a5a5a",
        sel="#bcd7ff", disabled="#8a8a8a", ok="#1b5e20",
        tool="#1f1f1f", danger="#b00020",
        act="#e6e6e6", act_hi="#d2d2d2", act_border="#3a3a3a", quiet_border="#3a3a3a",
        caption="#dfe3e8", caption_fg="#111111",   # タイトルバーは帯と同じ色に
    )
    ZUNDA = dict(  # ずんだ：ずんだもんの若草色。やわらかい緑の明色テーマ
        bg="#edf6e8", card="#f7fbf3", field="#ffffff", textbg="#ffffff", ground="#d6e7cc",
        fg="#243329", subtle="#5f7264", head_fg="#2c5c3d",
        accent="#357a4c", accent_hi="#43955e", accent_fg="#ffffff",
        btn="#d9ead0", btn_hi="#c9e0bd", border="#b9cfae",
        sel="#c9ecca", disabled="#9db3a2", ok="#2c7a44",
        tool="#3f5b47", danger="#a8392b",
        act="#cfe5c0", act_hi="#bddaa9", act_border="#8db478", quiet_border="#7fa86b",
        # タイトルバーは帯と同じ色に（前は OS の白のままで、緑の帯の上で浮いて見えた）
        caption="#d6e7cc", caption_fg="#243329",
    )
    # (設定キー, 表示名, パレット)。表示名がテーマ選択プルダウンの並びになる
    THEMES = [("light", "ライト", LIGHT), ("dark", "ダーク", DARK),
              ("hc", "くっきり", HC), ("zunda", "ずんだ", ZUNDA)]

    def _theme_selected(self, event=None):
        i = self.theme_cb.current()
        if 0 <= i < len(self.THEMES):
            self.theme_var.set(self.THEMES[i][0])
            self.apply_theme()

    def apply_theme(self):
        keys = [k for k, _l, _p in self.THEMES]
        key = self.theme_var.get()
        if key not in keys:
            key = "dark" if self.dark_var.get() else "light"
            self.theme_var.set(key)
        label, pal = next((l, p) for k, l, p in self.THEMES if k == key)
        self.dark_var.set(key == "dark")  # 旧設定キー "dark" との互換を維持
        if getattr(self, "theme_cb", None):
            self.theme_cb.set(label)      # プルダウン表示を現テーマに同期
        self._paint(pal)
        global _TITLE_COLORS
        _TITLE_COLORS = (pal["caption"], pal["caption_fg"]) if "caption" in pal else None
        # Windows のタイトルバーもテーマに合わせる（開いている小窓も一緒に）
        for win in (self, *(w for w in self.winfo_children() if isinstance(w, tk.Toplevel))):
            _dark_title_bar(win, key == "dark")
        # 検索/再生/しおりのハイライトは明色固定なので、文字色を黒にして全テーマで読めるように
        for tag, bg in (("playing", "#cde8ff"), ("hit", "#fff3a3"),
                        ("curhit", "#ffb347"), ("bookmark", "#e6d8f5"),
                        ("errline", "#ffd6d6")):
            self.text.tag_config(tag, background=bg, foreground="#000000")
        self._schedule_reflow()   # テーマでボタンの余白が変わり、必要な幅も変わる
        self._furi_schedule(0)    # ふりがなの色と下地もテーマに合わせる

    def _paint_marks(self, style, p):
        """チェックとラジオの印を、テーマの色で描く。clam の印は × で、日本語では「だめ」に
        見える。Tk 9 の clam は色の指定（indicatorcolor）も受け付けず、ダークでも白い四角に
        なっていた。Tk 8.6 でも同じ見た目になるよう、ピクセルを自分で塗る。"""
        size = _px(14)
        looks = {"off": ("field", "quiet_border", None), "on": ("accent", "accent", "accent_fg"),
                 "off_dis": ("bg", "disabled", None), "on_dis": ("disabled", "disabled", "bg")}
        if self._mark_imgs is None:
            self._mark_imgs = {(shape, look): tk.PhotoImage(master=self, width=size, height=size)
                               for shape in ("check", "radio") for look in looks}
            self._mark_cov = {shape: _mark_coverage(shape, size) for shape in ("check", "radio")}
            for shape, cls, old in (("check", "TCheckbutton", "Checkbutton.indicator"),
                                    ("radio", "TRadiobutton", "Radiobutton.indicator")):
                img = {look: str(self._mark_imgs[shape, look]) for look in looks}
                style.element_create(f"Mark.{shape}", "image", img["off"],
                                     ("disabled", "selected", img["on_dis"]),
                                     ("disabled", img["off_dis"]),
                                     ("selected", img["on"]),
                                     width=size + _px(5), sticky="w")
                style.layout(cls, _swap_element(style.layout(cls), old, f"Mark.{shape}"))

        def rgb(key):
            return tuple(v / 257 for v in self.winfo_rgb(p[key]))
        around = rgb("bg")
        for (shape, look), img in self._mark_imgs.items():
            fill, edge, mark = looks[look]
            img.put(_mark_pixels(self._mark_cov[shape], around, rgb(fill), rgb(edge),
                                 rgb(mark) if mark else None))

    def _paint(self, p):
        """パレット p を clam ベースで全ウィジェットへ一括適用する。"""
        style = ttk.Style(self)
        style.theme_use("clam")   # 色指定が全OSで効く共通テーマ（ライトも native aqua をやめる）
        style.configure(".", background=p["bg"], foreground=p["fg"],
                        fieldbackground=p["field"], troughcolor=p["field"],
                        bordercolor=p["border"], darkcolor=p["bg"], lightcolor=p["bg"],
                        selectbackground=p["sel"], selectforeground=p["fg"],
                        insertcolor=p["fg"], focuscolor=p["accent"])
        style.configure("TFrame", background=p["bg"])
        style.configure("TLabel", background=p["bg"], foreground=p["fg"])
        # セクション枠を薄い罫線のカード風に
        style.configure("TLabelframe", background=p["bg"], bordercolor=p["border"],
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=p["bg"],
                        foreground=p["head_fg"], font=self._heading_font)
        style.configure("Cluster.TLabel", background=p["bg"],
                        foreground=p["accent"], font=self._heading_font)
        # エンジン接続OKの状態表示（テーマごとの緑。固定色だとダーク/くっきりで破綻する）
        style.configure("EngineOK.TLabel", background=p["bg"],
                        foreground=p["ok"], font=self._heading_font)
        style.configure("Credit.TLabel", background=p["bg"],
                        foreground=p["subtle"], font=self._credit_font)
        style.configure("Link.TLabel", background=p["bg"],
                        foreground=p["accent"], font=self._credit_font)
        # 窓の地（区画のあいだと上の帯）と、その上に置く文字
        g = p.get("ground", p["bg"])
        style.configure("Ground.TFrame", background=g)
        style.configure("Ground.TLabel", background=g,
                        foreground=p.get("ground_fg", p["fg"]))
        style.configure("Title.TLabel", background=g,
                        foreground=p.get("ground_title", p["head_fg"]),
                        font=self._title_font)
        style.configure("GroundLink.TLabel", background=g,
                        foreground=p.get("ground_link", p["accent"]),
                        font=self._credit_font)
        # アプリ名の下のひとこと（小さく控えめに）
        style.configure("GroundSub.TLabel", background=g,
                        foreground=p.get("ground_sub", p["subtle"]),
                        font=self._credit_font)
        # 区画（カード）: 地より一段明るい面に細い枠。中の見出しと控えめな文字
        style.configure("Card.TFrame", background=p["bg"], bordercolor=p["border"],
                        lightcolor=p["border"], darkcolor=p["border"],
                        relief="solid", borderwidth=1)
        style.configure("Step.TLabel", background=p["bg"], foreground=p["head_fg"],
                        font=self._step_font)
        # ① ② ③ の見出しは、それぞれの色で（どこを見ているか分かりやすいように）
        for i, color in enumerate(p.get("steps", (p["head_fg"],) * 3), start=1):
            style.configure(f"Step{i}.TLabel", background=p["bg"], foreground=color,
                            font=self._step_font)
        style.configure("Sub.TLabel", background=p["bg"], foreground=p["subtle"])
        # 本文のすぐ上の一言（ふりがなは VOICEVOX を起動すると出る）。目に留まる色で
        style.configure("Notice.TLabel", background=p["bg"], foreground=p["accent"])
        # 進み具合の細い線（止まっているときは地の色に溶けて見えない）
        style.configure("Thin.Horizontal.TProgressbar", thickness=_px(3),
                        borderwidth=0, troughborderwidth=0, troughrelief="flat",
                        pbarrelief="flat", background=p["accent"], troughcolor=g)
        # 再生バーの位置（何行目か）。入力欄に見えないよう、細い線路の形に
        style.configure("Track.Horizontal.TProgressbar", thickness=_px(6),
                        borderwidth=0, troughborderwidth=0, troughrelief="flat",
                        pbarrelief="flat", background=p["accent"],
                        troughcolor=p["btn"])
        # 通常ボタン（フラット・少し余白）
        # padding類はDPI倍率で補正（宣言だけだと高DPIでボタンだけ窮屈になる）
        # ボタンの中の余白は、縦だけ少し広めに（詰まって見えないように）。横は広げない:
        # いちばん狭い窓では再生バーのボタンの行に余りが無く、横を 9 にすると、125%表示で
        # 左の列にスクロールバーが出たとき「« 最初から」の最後の字が切れた
        btn_pad = tuple(_px(v) for v in p.get("btn_pad", (7, 4)))
        primary_pad = tuple(_px(v) for v in p.get("primary_pad", (14, 9)))
        # ふつうの操作のボタン: はっきりした面＋枠（以前は面が地とほぼ同じ色で、
        # ボタンなのか見分けにくかった）
        style.configure("TButton", background=p["act"], foreground=p["fg"],
                        bordercolor=p["act_border"], lightcolor=p["act"],
                        darkcolor=p["act"], relief="solid", borderwidth=1,
                        padding=btn_pad, focuscolor=p["accent"])
        style.map("TButton",
                  background=[("pressed", p["act_hi"]), ("active", p["act_hi"]),
                              ("disabled", p["bg"])],
                  lightcolor=[("pressed", p["act_hi"]), ("active", p["act_hi"]),
                              ("disabled", p["bg"])],
                  darkcolor=[("pressed", p["act_hi"]), ("active", p["act_hi"]),
                             ("disabled", p["bg"])],
                  bordercolor=[("disabled", p["border"])],
                  foreground=[("disabled", p["disabled"])])
        # 主要ボタン（アクセント＝落ち葉オレンジ・太字）
        style.configure("Primary.TButton", background=p["accent"],
                        foreground=p["accent_fg"], bordercolor=p["accent"],
                        font=self._primary_font, relief="flat",
                        padding=primary_pad, focuscolor=p["accent_fg"])
        style.map("Primary.TButton",
                  background=[("pressed", p["accent_hi"]), ("active", p["accent_hi"]),
                              ("disabled", p["btn"])],
                  foreground=[("disabled", p["disabled"])])
        # 準主要ボタン＝大事なボタンのうち、まだ押しても意味のないもの（本文が空のときの
        # 「♪ 音声を生成」）。フォント・paddingをPrimaryと同一にし、Primary⇄Secondary
        # 切替でボタン寸法が変わらないようにする
        style.configure("Secondary.TButton", background=p["act"],
                        foreground=p["fg"], bordercolor=p["act_border"],
                        lightcolor=p["act"], darkcolor=p["act"],
                        font=self._primary_font, relief="solid", borderwidth=1,
                        padding=primary_pad, focuscolor=p["accent"])
        style.map("Secondary.TButton",
                  background=[("pressed", p["act_hi"]), ("active", p["act_hi"]),
                              ("disabled", p["bg"])],
                  foreground=[("disabled", p["disabled"])])
        # ---- ボタンの見分け。役割ごとに見た目を変える ----
        #   大事な操作（テキスト抽出 実行・音声を生成・再生）… 色で塗る（Primary・Accent）
        #   ふつうの操作 … 淡い面（TButton）
        #   ときどき使う … 枠だけ（Quiet）
        #   設定・開閉 … 面も枠もない文字だけ。▷ ▽ や … が付く（Tool）
        #   消す … 赤い文字（Danger.Tool）
        # 主役（小）: 再生バーの ▶ 再生 / ‖ 一時停止。Primary と同じ色で、大きさはふつう
        style.configure("Accent.TButton", background=p["accent"],
                        foreground=p["accent_fg"], bordercolor=p["accent"],
                        font=self._primary_font, relief="flat", padding=btn_pad,
                        focuscolor=p["accent_fg"])
        style.map("Accent.TButton",
                  background=[("pressed", p["accent_hi"]), ("active", p["accent_hi"]),
                              ("disabled", p["btn"])],
                  foreground=[("disabled", p["disabled"])])
        # 色を付けないときの ▶ 再生（文字の太さを Accent とそろえ、切り替えても幅が動かない）
        style.configure("Bold.TButton", font=self._primary_font)
        # ときどき使う操作（.txt 保存・コピー・レポートなど）: 枠だけで、面は塗らない
        style.configure("Quiet.TButton", background=p["bg"], foreground=p["fg"],
                        bordercolor=p["quiet_border"], lightcolor=p["bg"],
                        darkcolor=p["bg"],
                        relief="solid", borderwidth=1, padding=btn_pad,
                        focuscolor=p["accent"])   # flat だと clam は枠を描かない
        style.map("Quiet.TButton",
                  background=[("pressed", p["btn_hi"]), ("active", p["btn"]),
                              ("disabled", p["bg"])],
                  lightcolor=[("active", p["btn"]), ("pressed", p["btn_hi"])],
                  darkcolor=[("active", p["btn"]), ("pressed", p["btn_hi"])],
                  foreground=[("disabled", p["disabled"])])
        # 設定・開閉（「設定 ▷」「声の保管…」など）: 面と同じ色で、文字だけ。
        # 主役の色（オレンジ）は使わず、落ち着いた色にして「操作」と見分ける。
        # 押せる物だと分かるよう、マウスを乗せると面が出る
        style.configure("Tool.TButton", background=p["bg"], foreground=p["tool"],
                        bordercolor=p["bg"], lightcolor=p["bg"], darkcolor=p["bg"],
                        relief="flat", focuscolor=p["accent"],
                        padding=tuple(_px(v) for v in (6, 1)))
        style.map("Tool.TButton",
                  background=[("pressed", p["btn_hi"]), ("active", p["btn"]),
                              ("disabled", p["bg"])],
                  bordercolor=[("active", p["btn"]), ("pressed", p["btn_hi"])],
                  lightcolor=[("active", p["btn"]), ("pressed", p["btn_hi"])],
                  darkcolor=[("active", p["btn"]), ("pressed", p["btn_hi"])],
                  foreground=[("disabled", p["disabled"])])
        # 消す（「本文を全消去」）: 赤い文字
        style.configure("Danger.Tool.TButton", foreground=p["danger"])
        # ファイル一覧の横の ▲▼×（小さく・枠だけ）
        style.configure("Mini.Quiet.TButton", padding=tuple(_px(v) for v in (2, 0)))
        # ルール操作のMenubutton（TButtonと同じ見た目に揃える）
        style.configure("TMenubutton", background=p["act"], foreground=p["fg"],
                        bordercolor=p["act_border"], lightcolor=p["act"],
                        darkcolor=p["act"], relief="solid", borderwidth=1,
                        padding=btn_pad)
        style.map("TMenubutton",
                  background=[("pressed", p["act_hi"]), ("active", p["act_hi"]),
                              ("disabled", p["bg"])],
                  foreground=[("disabled", p["disabled"])])
        # 「ルール ▼」は文字に ▼ を入れているので、ttk が右に描く矢印は外す。
        # 二重になるうえ、Tk 9 ではその矢印が文字より大きな黒い ▼ になっていた。
        # 「詳細設定 ▷」など、ほかの開閉ボタンも文字の矢印で揃えている
        def _drop_indicator(layout):
            return [(name, dict(opts, children=_drop_indicator(opts["children"]))
                     if "children" in opts else opts)
                    for name, opts in layout if not name.endswith(".indicator")]
        style.layout("Plain.TMenubutton",
                     _drop_indicator(style.layout("TMenubutton")))
        # 入力系
        for w in ("TEntry", "TSpinbox", "TCombobox"):
            style.configure(w, fieldbackground=p["field"], foreground=p["fg"],
                            bordercolor=p["border"], insertcolor=p["fg"],
                            arrowcolor=p["fg"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", p["field"]), ("disabled", p["bg"])],
                  foreground=[("readonly", p["fg"]), ("disabled", p["disabled"])],
                  selectbackground=[("readonly", p["field"])],
                  selectforeground=[("readonly", p["fg"])],
                  arrowcolor=[("disabled", p["disabled"])])
        style.map("TSpinbox", arrowcolor=[("disabled", p["disabled"])])
        # 入力欄は、カーソルが入っているときに枠を色で示す
        for w in ("TEntry", "TSpinbox", "TCombobox"):
            style.map(w, bordercolor=[("focus", p["accent"])],
                      lightcolor=[("focus", p["accent"])])
        # 高DPIのみ: clamの固定サイズ部品（矢印・チェック箱・スクロールバー幅）を
        # 倍率補正する。100%表示とMacは既定サイズのまま触らない
        if UI_SCALE > 1.0:
            style.configure("TSpinbox", arrowsize=_px(12))
            style.configure("TCombobox", arrowsize=_px(12))
            style.configure("TScrollbar", arrowsize=_px(13), width=_px(13))
        # チェック/ラジオ（印はテーマの色で自分で描く。_paint_marks）
        for w in ("TCheckbutton", "TRadiobutton"):
            style.configure(w, background=p["bg"], foreground=p["fg"],
                            focuscolor=p["accent"])
            style.map(w, background=[("active", p["bg"])],
                      foreground=[("disabled", p["disabled"])])
        self._paint_marks(style, p)
        # 進捗・区切り・スクロールバー
        style.configure("TProgressbar", background=p["accent"],
                        troughcolor=p["field"], bordercolor=p["border"])
        style.configure("TSeparator", background=p["border"])
        style.configure("TScrollbar", background=p["btn"], troughcolor=p["bg"],
                        bordercolor=p["border"], arrowcolor=p["fg"])
        style.map("TScrollbar", background=[("active", p["btn_hi"])])
        # 設定のボタンの印（歯車）は、設定の文字と同じ色
        if getattr(self, "_gear_img", None) is not None:
            self._gear_img.configure(foreground=p["tool"])
        # ① の4つのボタンの印（書類・フォルダ・カメラ・写真）は、ボタンの文字と同じ色（押せないときは薄く）
        for on, off in getattr(self, "_btn_icons", ()):
            on.configure(foreground=p["fg"])
            off.configure(foreground=p["disabled"])
        # ② の声のキャラの顔: 下地は区画の色、まだ顔が無いときの人の形は薄い枠の色
        if getattr(self, "_face_lbl", None) is not None:
            self._face_lbl.configure(bg=p["bg"])
            self._face_blank.configure(foreground=p["border"])
        # 非ttk（tk）ウィジェットは直接配色
        self.configure(bg=g)
        self.text.config(bg=p["textbg"], fg=p["fg"], insertbackground=p["fg"],
                         selectbackground=p["sel"], selectforeground=p["fg"],
                         highlightthickness=1, highlightbackground=p["border"],
                         highlightcolor=p["accent"], borderwidth=0)
        if getattr(self, "_text_hint", None) is not None:
            self._text_hint.config(bg=p["textbg"], fg=p["subtle"])
        if getattr(self, "_steps_view", None) is not None:
            self._steps_view.config(bg=p["bg"])   # 左の列（スクロールする部分）
        if getattr(self, "_list_hint", None) is not None:
            self._list_hint.config(bg=p["field"], fg=p["subtle"])
        self.listbox.config(bg=p["field"], fg=p["fg"],
                            selectbackground=p["sel"], selectforeground=p["fg"],
                            highlightthickness=1, highlightbackground=p["border"],
                            highlightcolor=p["accent"], borderwidth=0)
        if getattr(self, "_portrait_label", None):
            self._portrait_label.config(bg=p["bg"])  # 立ち絵の透過部を背景色に
        if getattr(self, "_bubble", None):
            # キャラ横の吹き出し（状態表示）。カード色＋細枠でテーマに追従
            self._bubble.config(bg=p["card"], fg=p["fg"],
                                highlightbackground=p["border"],
                                highlightcolor=p["border"], highlightthickness=1)
        # コンボボックスのドロップダウン一覧（option DB経由）
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", p["accent_fg"])

    # ---------------- 起動時のエンジン自動接続 ----------------
    def _auto_connect(self):
        """起動直後にエンジンを探す。見つからなければ少しの間だけ静かに待つ
        （アプリとVOICEVOXを同時に起動したとき、VOICEVOX側の準備が間に合わずに
        「未接続」で固まってしまうのを防ぐ）。窓が閉じたら完全に諦め、以後は
        裏で通信しない＝エンジンを使わない人の邪魔をしない。"""
        if self.busy:
            return
        # VOICEVOXが入っていて、しかも今まさに起動した気配があるなら長めに待つ
        seconds = 20
        try:
            p = core.vv_runtime_info_path()
            just_started = bool(p) and (time.time() - os.path.getmtime(p)) < 60
            if just_started and core.find_voicevox():
                seconds = 60
        except Exception:
            pass
        self._start_connect_retry(seconds, interval=2000)
        # 最初の確認で見つからなければ、エンジンを起動する（チェックを切っていなければ）。
        # VOICEVOX が今まさに起動中なら、そちらを待つ
        self._autostart_pending = seconds == 20 and "pytest" not in sys.modules
        self.check_engine(quiet=True)

    def _engine_autostart_check(self):
        """起動してすぐの確認で VOICEVOX が見つからなかったとき。エンジンが入って
        いれば、何も聞かずに一緒に起動する（エディタ画面は開かない。アプリを閉じると
        一緒に終わる）。左下の「接続設定…」のチェックを切っていれば、起動しない。"""
        if (self._destroying or self._engine_proc is not None
                or not self._engine_autostart):
            return
        try:
            found = core.find_voicevox_engine(self._voicevox_path)
        except Exception:
            found = None
        if not found:
            return   # エンジンが入っていない（入れ方は「VOICEVOX起動」の案内で）
        self.launch_engine_only(quiet=True)

    def _set_engine_autostart(self, value):
        """「アプリと一緒にエンジンを起動」を決める（すぐ設定に書く）。"""
        self._engine_autostart = bool(value)
        self.engine_autostart_var.set(self._engine_autostart)
        try:
            self._save_settings()
        except Exception:
            pass

    # ---------------- テキスト検索（Ctrl/Cmd+F） ----------------
    def open_search(self):
        if self._search_win is not None and self._search_win.winfo_exists():
            self._search_win.lift()
            self._search_entry.focus_set()
            return
        win = tk.Toplevel(self)
        win.title("検索")
        # 大きさは中身に任せる。前は 360x40 ピクセルに決め打ちで、250%表示では「↓次」
        # 「↑前」が窓の外に出て見えず、125%でもボタンの下が切れていた
        win.resizable(False, False)
        win.transient(self)
        # 検索は本文を隠さないよう、真ん中ではなく本文欄の右上に出す
        self._place_dialog(win, near=lambda w, h: (
            self.text.winfo_rootx() + self.text.winfo_width() - w - _px(24),
            self.text.winfo_rooty() + _px(8)),
            focus=lambda: self._search_entry)
        self._search_win = win
        row = ttk.Frame(win); row.pack(fill="x", padx=_px(6), pady=_px(6))
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(row, textvariable=self._search_var, width=22)
        self._search_entry.pack(side="left", padx=2)
        self._search_entry.focus_set()
        ttk.Button(row, text="↓次", width=4,
                   command=lambda: self._search_jump(+1)).pack(side="left", padx=1)
        ttk.Button(row, text="↑前", width=4,
                   command=lambda: self._search_jump(-1)).pack(side="left", padx=1)
        self._search_count = tk.StringVar(value="")
        # 件数の欄は先に幅を取っておく（出たり消えたりで、窓の幅が変わらないように）
        ttk.Label(row, textvariable=self._search_count, width=9).pack(side="left", padx=_px(6))
        self._search_entry.bind("<Return>", lambda e: self._search_jump(+1))
        self._search_var.trace_add("write", lambda *a: self._search_refresh())
        win.protocol("WM_DELETE_WINDOW", self._close_search)
        # "break" を返し、Escの全体バインド（再生停止）と二重発火しないようにする
        win.bind("<Escape>", lambda e: (self._close_search(), "break")[1])

    def _close_search(self):
        self.text.tag_remove("hit", "1.0", "end")
        self.text.tag_remove("curhit", "1.0", "end")
        self._furi_recolor()
        if self._search_win is not None and self._search_win.winfo_exists():
            self._search_win.destroy()
        self._search_win = None

    def _search_refresh(self):
        word = self._search_var.get()
        self.text.tag_remove("hit", "1.0", "end")
        self.text.tag_remove("curhit", "1.0", "end")
        self._furi_recolor()
        self._search_hits = []
        self._search_idx = -1
        if not word:
            self._search_count.set("")
            return
        pos = "1.0"
        while True:
            pos = self.text.search(word, pos, stopindex="end")
            if not pos:
                break
            end = f"{pos}+{len(word)}c"
            self.text.tag_add("hit", pos, end)
            self._search_hits.append(pos)
            pos = end
        # hit/curhit タグの配色は apply_theme が一元設定済み（ここでの再設定は不要）
        self._search_count.set(f"{len(self._search_hits)}件")

    def _search_jump(self, direction):
        if not self._search_hits:
            self._search_refresh()
            if not self._search_hits:
                return
        if self._search_idx < 0 and direction < 0:
            # 初回の「↑前」は末尾へ（-1からの剰余だと末尾から2番目に飛んでいた）
            self._search_idx = len(self._search_hits) - 1
        else:
            self._search_idx = (self._search_idx + direction) % len(self._search_hits)
        pos = self._search_hits[self._search_idx]
        word = self._search_var.get()
        self.text.tag_remove("curhit", "1.0", "end")
        self.text.tag_add("curhit", pos, f"{pos}+{len(word)}c")
        self._furi_recolor()
        self.text.mark_set("insert", pos)
        self.text.see(pos)
        self._search_count.set(f"{self._search_idx + 1}/{len(self._search_hits)}件")

    # ---------------- 文字サイズ（Ctrl/Cmd + = / - / 0・Ctrl+ホイール・窓に合わせて自動） ----
    # 窓を広げると、1行がだいたい _FIT_CHARS 字になるまで本文の文字を大きくする。
    # 選んだ大きさより小さくはしない（狭い窓で読みにくくしない）。足すのは元の大きさの
    # ぶんまで（11 → 22）で、本文欄に _FIT_MIN_LINES 行は見える大きさまで
    _FIT_CHARS = 36
    _FIT_MIN_LINES = 12
    _FIT_SAMPLE = "本文の文字は、窓の幅に合わせて大きくなります。ふりがなも一緒に変わります。"

    def change_font(self, delta):
        """選んだ大きさを1つ上げ下げする（0 で元の大きさ）。窓の幅に合わせて足す分は、
        その上に乗る（窓を変えても、選んだぶんの大小はそのまま）。"""
        if delta == 0:
            self._font_pref = self._font_size0
        else:
            self._font_pref = max(8, min(40, self._font_pref + delta))
        size = self._fit_text_font(force=True)
        extra = size - self._font_pref
        self.status_var.set(f"文字サイズ: {size}"
                            + (f"（窓の幅に合わせて +{extra}）" if extra > 0 else ""))

    def _on_ctrl_wheel(self, event):
        """Ctrl（Mac は ⌘）を押しながらホイールを回すと、本文の文字を大きく・小さくする。"""
        if event.delta:
            self.change_font(+1 if event.delta > 0 else -1)
        return "break"   # 本文はスクロールしない

    def _on_font_auto(self):
        """右クリックの「窓の幅に合わせて大きくする」を切り替えたとき。"""
        size = self._fit_text_font(force=True)
        self.status_var.set(f"文字サイズ: {size}（"
                            + ("窓の幅に合わせて大きくします" if self.font_auto_var.get()
                               else "窓の大きさでは変えません") + "）")

    def _schedule_fit_text(self, ms=120):
        """本文欄の大きさが変わったら、少し待ってから文字の大きさを合わせ直す
        （窓の端を引っぱっている間は、まとめて1回にする）。"""
        if getattr(self, "_destroying", False) or not hasattr(self, "_ticks"):
            return
        old = self._ticks.pop("fit_text", None)
        if old:
            try:
                self.after_cancel(old)
            except tk.TclError:
                pass
        self._tick("fit_text", ms, self._fit_text_after_resize)

    def _fit_text_after_resize(self):
        """窓の大きさが変わったあとの合わせ直し。初めて変えたときだけ、変え方を知らせる。"""
        self._ticks.pop("fit_text", None)
        before = int(self.text_font.cget("size"))
        # 最初の1回は丸めたとおりに決める（同じ窓なら、いつ開いても同じ大きさにする）
        size = self._fit_text_font(force=not self._fit_done)
        if self.text.winfo_width() > _px(100):
            self._fit_done = True   # 並べ終わる前の呼び出しでは、まだ「最初」のまま
        self._follow_playing()
        if (size != before and not self._fit_hint_shown
                and time.monotonic() >= self._fit_quiet_until):
            self._fit_hint_shown = True
            self.status_var.set(f"本文の文字を、窓の幅に合わせました（{size}）。"
                                "右クリックの「文字の大きさ」で変えられます。")

    def _fit_metrics(self):
        """本文の字体の、1ポイントあたりの1文字の幅と行の高さ（ピクセル）。
        字体ごとに1回だけ、大きめの大きさで測って覚えておく。"""
        fam = self.text_font.cget("family")
        if self._fit_cache is None or self._fit_cache[0] != fam:
            probe = tkfont.Font(family=fam, size=100)
            adv = probe.measure(self._FIT_SAMPLE) / len(self._FIT_SAMPLE) / 100
            self._fit_cache = (fam, adv, probe.metrics("linespace") / 100)
        return self._fit_cache[1], self._fit_cache[2]

    def _fitted_text_size(self):
        """本文に使う文字の大きさ（ポイント）。選んだ大きさに、窓の幅に合わせて足す分を
        加える。足すのは、1行がだいたい _FIT_CHARS 字になるまでで、元の大きさのぶんまで。
        本文欄に _FIT_MIN_LINES 行（ふりがなの場所も入れて）が見えない大きさにはしない。
        幅で決まるときは小数のまま返す（境目で行ったり来たりしないよう _fit_text_font が使う）。"""
        pref = self._font_pref
        if not self.font_auto_var.get():
            return float(pref)
        t = self.text
        try:
            edge = (t.winfo_pixels(t.cget("borderwidth"))
                    + t.winfo_pixels(t.cget("highlightthickness")))
            w = t.winfo_width() - 2 * (edge + t.winfo_pixels(t.cget("padx")))
            h = t.winfo_height() - 2 * (edge + t.winfo_pixels(t.cget("pady")))
        except tk.TclError:
            return float(pref)
        if w < _px(100) or h < _px(60):
            return float(pref)   # まだ並べ終わっていない（起動の途中など）
        adv, line = self._fit_metrics()
        base = self._font_size0
        want = min(40.0, pref + max(0.0, min(w / (self._FIT_CHARS * adv) - base,
                                             float(base))))
        furi = self.furigana_var.get()

        def fits(size):
            """本文欄に _FIT_MIN_LINES 行が見えるか（ふりがなを出すなら、その場所も入れる）。"""
            pitch = line * size + ((line * max(7, round(size * 0.6)) + 1) if furi else 0)
            return h >= self._FIT_MIN_LINES * pitch
        size = int(round(want))
        if size <= pref or fits(size):
            return want
        while size > pref and not fits(size):
            size -= 1   # 低い窓では、見える行数で止める
        return float(size)

    def _fit_text_font(self, force=False):
        """本文の文字の大きさを、いまの本文欄に合わせて付け直し、その大きさを返す。
        force でなければ、境目で大きさが行ったり来たりしないよう、0.75 ポイント以上
        ずれたときだけ変える。"""
        want = self._fitted_text_size()
        size = int(self.text_font.cget("size"))
        new = int(round(want))
        if new == size or (not force and abs(want - size) < 0.75):
            return size
        self.text_font.config(size=new)
        if self._furi_spaced:
            self._furi_apply_spacing(True)   # ふりがなも本文に合わせた大きさにする
        self._furi_schedule(0)
        self._follow_playing()
        return new

    # ---------------- テキストの自動保存・復元 ----------------
    def _save_text_cache(self):
        text = self.text.get("1.0", "end-1c")
        if text == self._cache_saved:
            return   # 前回の保存から変化なし（無駄なディスク書き込みをしない）
        try:
            if text.strip():
                # アトミック書き込み（自動保存の途中クラッシュで本文を壊さない）
                _write_atomic(TEXT_CACHE_PATH, text)
            elif os.path.exists(TEXT_CACHE_PATH):
                os.remove(TEXT_CACHE_PATH)
            self._cache_saved = text
        except Exception:
            pass

    def _autosave_tick(self):
        """本文を60秒ごとに自動保存する（従来は終了時のみ＝クラッシュや強制終了で
        編集が丸ごと消えていた。次回起動時の復元は従来と同じ仕組み）。"""
        try:
            self._save_text_cache()
            self._tick("autosave", 60000, self._autosave_tick)
        except tk.TclError:
            pass   # 終了中

    def _cursor_to_top(self):
        """本文を入れ替えたあと、カーソルを先頭に戻す。
        Tkは「1.0に挿入」すると入力位置マークを入れた文字列の末尾まで押し出すので、
        放っておくとカーソルが最終行に居座る。連続再生は「カーソル行から最後まで」
        なので、そのままだと最後の1行しか読まれない。"""
        try:
            self.text.mark_set("insert", "1.0")
            self.text.see("1.0")
        except tk.TclError:
            pass
        self._schedule_prefetch()   # 先頭行を裏で合成しておく

    def _restore_text_cache(self):
        try:
            with open(TEXT_CACHE_PATH, encoding="utf-8") as f:
                cached = f.read()
        except Exception:
            return
        if cached.strip() and not self.text.get("1.0", "end").strip():
            self.text.insert("1.0", cached)
            self._cursor_to_top()   # 連続再生が最後の1行だけにならないように
            self._mark_bookmark()   # 前回のしおり位置を淡色でマーク
            if self._bookmark is None:
                self.status_var.set(f"{self._hello}、前回のテキストを復元したよ")
                return
            # しおりがあれば、その行にカーソルを置いて「前回の続き」を出す。
            # ▶ 再生 でも ⏵ 続きを聴く でも、聴いていた所から続く
            last = int(self.text.index("end-1c").split(".")[0])
            line = min(self._bookmark, last)
            self.text.mark_set("insert", f"{line}.0")
            self.text.see(f"{line}.0")
            self._show_resume(line)
            self.status_var.set(f"{self._hello}、前回のテキストを復元したよ"
                                f"（{line}行目まで聴いていたよ）")

    # ---------------- 声プリセット ----------------
    def _preset_labels(self):
        return [p["name"] for p in self.presets]

    def _refresh_presets(self):
        self.preset_cb.config(values=self._preset_labels())

    def _preset_selected(self, event=None):
        i = self.preset_cb.current()
        if not (0 <= i < len(self.presets)):
            return
        p = self.presets[i]
        self.speed_var.set(p.get("speed", 1.0))
        self.pitch_var.set(p.get("pitch", 0.0))
        self.into_var.set(p.get("intonation", 1.0))
        self.vol_var.set(p.get("volume", 1.0))
        # 話者はエンジン接続済みでラベルが一致するときだけ切り替える
        if p.get("speaker"):
            self._select_speaker_label(p["speaker"])
        self.status_var.set(f"お気に入り「{p['name']}」にしました。")

    def save_preset(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("お気に入りに保存", "名前（例: ゆっくり朗読）:",
                                      parent=self)
        if not name:
            return
        name = name.strip()
        # 数値欄が空でもTclErrorで無反応にならないよう既定値へフォールバック
        preset = {"name": name, "speaker": self._current_speaker_label(),
                  "speed": self._get_num(self.speed_var, 1.0),
                  "pitch": self._get_num(self.pitch_var, 0.0),
                  "intonation": self._get_num(self.into_var, 1.0),
                  "volume": self._get_num(self.vol_var, 1.0)}
        for i, p in enumerate(self.presets):
            if p["name"] == name:
                self.presets[i] = preset  # 同名は上書き
                break
        else:
            self.presets.append(preset)
        self._refresh_presets()
        self.preset_cb.set(name)
        self.status_var.set(f"お気に入り「{name}」を保存しました（次に開いたときも使えます）。")

    def del_preset(self):
        i = self.preset_cb.current()
        if not (0 <= i < len(self.presets)):
            self.status_var.set("消すお気に入りを選んでください。")
            return
        p = self.presets.pop(i)
        self._refresh_presets()
        self.preset_cb.set("")
        self.status_var.set(f"お気に入り「{p['name']}」を消しました。")

    # ---------------- 設定の保存/復元 ----------------
    def _get_num(self, var, default):
        """数値のtk変数を安全に読む（空欄・不正なら既定値）。終了時保存が
        1つの空欄のTclErrorで全滅し、その回の設定変更が全部消えるのを防ぐ。"""
        try:
            return var.get()
        except tk.TclError:
            return default

    def _settings_dict(self):
        return {
            "mode": self.mode_var.get(), "pdf": self.pdf_var.get(),
            "dpi": self._get_num(self.dpi_var, 300),
            "preprocess": self.pre_var.get(),
            "blank": self.blank_var.get(), "ascii": self.ascii_var.get(),
            "join": self.join_var.get(),
            "smart_join": self.smartjoin_var.get(),
            "paren_ruby": self.pruby_var.get(), "normalize": self.norm_var.get(),
            "denoise": self.denoise_var.get(),
            "fix_confusables": self.fixconf_var.get(),
            "remove_urls": self.urlskip_var.get(),
            "strip_heads": bool(self.heads_var.get()),
            "dark": self.dark_var.get(),
            "theme": self.theme_var.get(),
            "unit": self._unit(), "nlines": self._get_num(self.nlines_var, 50),
            "srt": self.srt_var.get(),
            "font_size": int(self._font_pref),
            "font_auto": bool(self.font_auto_var.get()),
            "furigana": bool(self.furigana_var.get()),
            "furigana_only": bool(self.furigana_only_var.get()),
            "read_fix": bool(self.read_fix_var.get()),
            "speed": self._get_num(self.speed_var, 1.0),
            # エンジン未接続のセッションではコンボが空＝保存済みの選択を保持する
            # （空文字で上書きすると次回接続時に先頭話者へ戻ってしまう）
            "speaker": self._current_speaker_label() or (self._saved_speaker or ""),
            "pitch": self._get_num(self.pitch_var, 0.0),
            "intonation": self._get_num(self.into_var, 1.0),
            "volume": self._get_num(self.vol_var, 1.0),
            "fmt": self._out_format(),
            "gap": self._get_num(self.gap_var, 0.4),
            "para_pause": bool(self.para_pause_var.get()),
            "replace_rules": self.replace_rules,
            "presets": self.presets,
            "dlg_enabled": self.dlg_var.get(),
            "dlg_speaker": (self.dlg_speaker_cb.get()
                            or (self._saved_dlg_speaker or "")),
            "bookmark": self._bookmark,
            "base_url": self.url_var.get().strip() or self.base_url,
            "engine_url_mode": self._engine_url_mode,
            "voicevox_path": self._voicevox_path,
            "engine_gpu": bool(self._engine_gpu),
            "engine_gpu_failed": self._engine_gpu_failed or "",
            "engine_autostart": self._engine_autostart,
            "update_check": bool(self._update_check_on),
            "update_checked_at": float(self._update_checked_at or 0),
            "update_latest": self._update_latest,
            "update_url": self._update_url,
            "geometry": self.geometry(),
            # 窓の大きさはピクセルなので、保存したときの表示の倍率も控える
            # （125%の画面で保存して250%の画面で開くと、半分の大きさになっていた）
            "ui_scale": UI_SCALE,
            "zoomed": self._is_zoomed(),
            "vvproj_query": bool(self.vvproj_query_var.get()),
            "adv_open": bool(self._adv_open),
            "opt_open": bool(self._opt_open),
            "replace_open": bool(self._rep_open),
            "voice_detail_open": bool(self._vdetail_open),
            "finish_open": bool(self._finish_open),
            "synth_cache_mb": core._SYNTH_CACHE_MAX_BYTES // (1024 * 1024),
        }

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                s = json.load(f)
        except (FileNotFoundError, OSError):
            return
        except Exception:
            # 壊れた設定ファイルは .bak へ退避（無言で捨てず手動復旧の余地を残す）
            try:
                os.replace(SETTINGS_PATH, SETTINGS_PATH + ".bak")
            except OSError:
                pass
            return
        # 復元は小分けにして try で囲む。ひとまとめにすると、値が1つ壊れて
        # いるだけで以降の復元が全部飛び、置換ルールや声プリセットが黙って
        # 消えたまま、終了時に空で上書き保存されてしまう
        try:
            self.mode_var.set(s.get("mode", self.mode_var.get()))
            self.pdf_var.set(s.get("pdf", self.pdf_var.get()))
            self.dpi_var.set(int(s.get("dpi", self.dpi_var.get())))
            self.pre_var.set(bool(s.get("preprocess", True)))
            self.blank_var.set(bool(s.get("blank", True)))
            self.ascii_var.set(bool(s.get("ascii", True)))
            self.join_var.set(bool(s.get("join", False)))
            self.smartjoin_var.set(bool(s.get("smart_join", False)))
            self.pruby_var.set(bool(s.get("paren_ruby", False)))
            self.norm_var.set(bool(s.get("normalize", False)))
            self.read_fix_var.set(bool(s.get("read_fix", True)))
            self.denoise_var.set(bool(s.get("denoise", True)))
            self.fixconf_var.set(bool(s.get("fix_confusables", True)))
            self.urlskip_var.set(bool(s.get("remove_urls", True)))
            self.heads_var.set(bool(s.get("strip_heads", True)))
        except Exception:
            pass
        try:
            self.dark_var.set(bool(s.get("dark", False)))
            # テーマ：新キー "theme" を優先。無い旧設定は "dark": true → ダーク で引き継ぐ
            theme = s.get("theme")
            if theme is None and bool(s.get("dark", False)):
                theme = "dark"
            if theme in {k for k, _l, _p in self.THEMES}:
                self.theme_var.set(theme)
            self._set_advanced(bool(s.get("adv_open", False)))
            self._set_opt_rows(bool(s.get("opt_open", False)))
            self.vvproj_query_var.set(bool(s.get("vvproj_query", True)))
            self._set_finish(bool(s.get("finish_open", False)))
        except Exception:
            pass
        try:
            unit = s.get("unit")
            if unit is None and s.get("combine"):
                unit = "combine"  # 旧設定(combine: true)からの引き継ぎ
            keys = list(self._UNITS.keys())
            if unit in keys:
                self.unit_cb.current(keys.index(unit))
            self.nlines_var.set(int(s.get("nlines", 50)))
            self.srt_var.set(bool(s.get("srt", False)))
            fs = s.get("font_size")
            if isinstance(fs, (int, float)) and 8 <= int(fs) <= 40:
                self._font_pref = int(fs)
                self.text_font.config(size=int(fs))
            self.font_auto_var.set(bool(s.get("font_auto", True)))
            # ふりがなは最初からオン（前の版の設定ファイルには無いのでオンになる）
            self.furigana_var.set(bool(s.get("furigana", True)))
            self.furigana_only_var.set(bool(s.get("furigana_only", False)))
        except Exception:
            pass
        try:
            self.speed_var.set(float(s.get("speed", 1.0)))
            self.pitch_var.set(float(s.get("pitch", 0.0)))
            self.into_var.set(float(s.get("intonation", 1.0)))
            self.vol_var.set(float(s.get("volume", 1.0)))
            fmt = self._FMT_LABELS.get(str(s.get("fmt", "wav")).lower(), "WAV")
            if fmt in self._format_choices():
                self.fmt_cb.set(fmt)
                self._on_format_selected()   # M4B復元時は「まとめ方」無効化も反映
            self.gap_var.set(float(s.get("gap", 0.4)))
            self.para_pause_var.set(bool(s.get("para_pause", True)))
        except Exception:
            pass
        try:
            rules = s.get("replace_rules", [])
            if isinstance(rules, list):
                self.replace_rules = [[str(x[0]), str(x[1])] for x in rules
                                      if isinstance(x, (list, tuple)) and len(x) == 2]
                self._refresh_rules()
            # 一括置換の道具は、ルールを登録している人には開いておく（前の版では
            # いつも見えていた。使っている人の手順を変えない）
            self._set_replace(bool(s.get("replace_open", bool(self.replace_rules))))
        except Exception:
            pass
        try:
            presets = s.get("presets", [])
            if isinstance(presets, list):
                self.presets = [p for p in presets
                                if isinstance(p, dict) and p.get("name")]
                self._refresh_presets()
        except Exception:
            pass
        try:
            self.dlg_var.set(bool(s.get("dlg_enabled", False)))
            # プリセット/セリフ行：保存した開閉状態を復元。セリフ別話者ONなら
            # 設定が隠れて見えないままにならないよう自動で開く
            self._set_voice_detail(bool(s.get("voice_detail_open", False))
                                   or self.dlg_var.get())
            self._saved_dlg_speaker = s.get("dlg_speaker") or None
            bm = s.get("bookmark")
            self._bookmark = int(bm) if isinstance(bm, (int, float)) else None
            self._saved_speaker = s.get("speaker") or None
        except Exception:
            pass
        try:
            # 上限を設定し、前回セッションからの超過分を起動時に一度掃除する
            # （上限を下げた設定で終了した場合の持ち越し解消。別スレッドで）
            core.set_synth_cache_limit(s.get("synth_cache_mb", 500))
            threading.Thread(target=self._housekeeping, daemon=True).start()
        except Exception:
            pass
        try:
            if s.get("base_url"):
                saved = core.vv_normalize_url(s["base_url"])
                self.base_url = saved or VOICEVOX_DEFAULT
                self.url_var.set(self.base_url)
            # 設定にモードが無い古いファイルからの移行:
            # URLを既定のままにしている人は auto、自分で変えていた人は
            # manual とみなす（意図して変えた設定を勝手に奪わないため）
            mode = s.get("engine_url_mode")
            if mode not in ("auto", "manual"):
                mode = "auto" if self.base_url == VOICEVOX_DEFAULT else "manual"
            self._engine_url_mode = mode
            self._voicevox_path = s.get("voicevox_path") or None
            self._engine_gpu = bool(s.get("engine_gpu", True))
            self._engine_gpu_failed = str(s.get("engine_gpu_failed") or "")
            self.engine_gpu_var.set(self._engine_gpu)
            auto = s.get("engine_autostart")
            self._engine_autostart = auto if isinstance(auto, bool) else True
            self.engine_autostart_var.set(self._engine_autostart)
        except Exception:
            pass
        try:
            on = s.get("update_check")
            self._update_check_on = on if isinstance(on, bool) else True
            self.update_check_var.set(self._update_check_on)
            self._update_checked_at = float(s.get("update_checked_at") or 0)
            self._update_latest = str(s.get("update_latest") or "")
            self._update_url = str(s.get("update_url") or "")
        except Exception:
            pass
        try:
            if s.get("geometry"):
                self._apply_saved_geometry(s["geometry"], s.get("ui_scale"))
            # 最大化はここでは当てない（本体はまだ隠れている。今 zoomed に
            # すると構築途中の窓が見えてしまう）。表示する直前に当てる
            self._want_zoomed = bool(s.get("zoomed", False))
        except Exception:
            pass

    def _apply_saved_geometry(self, geo, saved_scale=None):
        """保存したウィンドウ位置を復元する。ただしサブモニタ切断などで画面外に
        なる座標はプライマリ画面内へ寄せ、必ず一部が見えるようにする
        （そのまま適用すると『起動しても窓が出ない』と誤解される）。
        saved_scale は保存したときの表示の倍率（UI_SCALE）。今と違えば、大きさを
        今の倍率に直す（125%の画面で保存して250%の画面で開くと、窓が半分の高さで
        開き、左の列の ③ が隠れていた）。"""
        # Tk の geometry() は左/上のモニタだと "980x880+-1500+120" のように
        # 「+」のあとに負の数を書く。以前の正規表現はこれに合わず、補正を
        # 素通りしていた（外したモニタの座標がそのまま当たる＝窓が見えない）
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", geo)
        if not m:
            try:
                self.geometry(geo)
            except tk.TclError:
                pass
            return
        w, h, x, y = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        try:
            scale = float(saved_scale or 0)
        except (TypeError, ValueError):
            scale = 0.0
        if scale > 0 and abs(scale - UI_SCALE) > 0.01:
            w, h = int(round(w * UI_SCALE / scale)), int(round(h * UI_SCALE / scale))
        # 最小の大きさより小さい値は、倍率の違う画面で保存したもの（倍率を控えていなかった
        # 前の版の設定など）。最小まで広げておく。画面が小さくて入らないときは、すぐ下の
        # _fit_to_work_area が縮める
        min_w, min_h = self.minsize()
        w, h = max(w, min_w), max(h, min_h)
        vx, vy, vw, vh = self._virtual_screen()
        # 仮想デスクトップ（つながっている全モニタを合わせた範囲）に
        # 100x60 以上見えていれば、サブモニタの位置でもそのまま尊重する。
        # ほとんど見えないときだけ、プライマリ画面に収まる位置へ寄せる
        visible_w = min(x + w, vx + vw) - max(x, vx)
        visible_h = min(y + h, vy + vh) - max(y, vy)
        if visible_w < 100 or visible_h < 60:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            x = max(0, min(x, max(0, sw - w)))
            y = max(0, min(y, max(0, sh - h)))
        # 見えてはいても、下端がタスクバーの裏に入っていると「再生・生成」の行が
        # 隠れる。窓がいちばん重なっている画面の作業領域に収める
        w, h, x, y = self._fit_to_work_area(w, h, x, y)
        min_w, min_h = self.minsize()
        if h < min_h:
            self.minsize(min_w, h)   # 最小サイズに押し戻されて、また隠れないように
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _fit_to_work_area(self, w, h, x=None, y=None):
        """Windows で、geometry（中身の w, h と外枠の左上 x, y）を作業領域
        （タスクバーを除いた所）に収めて返す。窓がいちばん重なっている画面の
        作業領域を使う。x, y が None（初回起動）なら、メインの画面の真ん中に置く。
        Windows 以外・取れないときは、受け取ったまま返す。"""
        if not core.IS_WIN:
            return w, h, x, y
        try:
            import ctypes
            from ctypes import wintypes

            class _MonitorInfo(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            gsm = user32.GetSystemMetrics
            # SM_CYCAPTION=4 / SM_CXSIZEFRAME=32 / SM_CYSIZEFRAME=33 /
            # SM_CXPADDEDBORDER=92。DPI対応を宣言済みなので実際のピクセルで返る
            # （125%表示の実測: 29 / 4 / 4 / 5 → 外枠は中身より横18・縦47大きい）
            caption = gsm(4)
            frame_x = gsm(32) + gsm(92)
            frame_y = gsm(33) + gsm(92)
            # HMONITOR はポインタ幅。restype を言わないと64bitで上が切れる
            user32.MonitorFromRect.restype = wintypes.HMONITOR
            user32.MonitorFromPoint.restype = wintypes.HMONITOR
            if x is None or y is None:
                # MONITOR_DEFAULTTOPRIMARY=1（(0,0) はメインの画面の左上）
                mon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)
            else:
                outer = wintypes.RECT(x, y, x + w + 2 * frame_x,
                                      y + caption + h + 2 * frame_y)
                # MONITOR_DEFAULTTONEAREST=2
                mon = user32.MonitorFromRect(ctypes.byref(outer), 2)
            info = _MonitorInfo()
            info.cbSize = ctypes.sizeof(_MonitorInfo)
            if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(info)):
                return w, h, x, y
            rc = info.rcWork
            if x is None or y is None:
                x = rc.left + (rc.right - rc.left - w - 2 * frame_x) // 2
                y = rc.top + (rc.bottom - rc.top - h - caption - 2 * frame_y) // 2
            return _fit_rect_in_work_area(
                w, h, x, y, (rc.left, rc.top, rc.right, rc.bottom),
                frame_x, frame_y, caption)
        except Exception:
            return w, h, x, y

    def _place_dialog(self, win, near=None, focus=None):
        """小さな窓（エラー・完了・キャッシュ等）を本体の真ん中に出す。near を渡すと、
        near(幅, 高さ) が返す位置に出す。focus には、出したあと入力を受ける部品を返す
        関数を渡す（隠している間の focus_set は Tk が覚えないため、出してから当てる）。以前は Tk 任せで、本体がどこにあっても画面の
        左上 (9,38) に出ていた（本体を2台目の画面に置くと、エラーの窓が別の画面に出て
        気づけない）。窓を作った直後に呼ぶ。中身ができるまで隠しておき、大きさが
        決まってから置く（50ms 待つあいだに Tk が並べ終える。update_idletasks は
        macOS の Tk で落ちることがあるので使わない）。"""
        win.withdraw()
        _dark_title_bar(win, self.theme_var.get() == "dark")   # 本体と同じ色の枠に

        def place():
            if self._destroying:
                return
            try:
                if not win.winfo_exists():
                    return
                m = re.match(r"(\d+)x(\d+)", win.geometry())
                w = int(m[1]) if m and int(m[1]) > 1 else win.winfo_reqwidth()
                h = int(m[2]) if m and int(m[2]) > 1 else win.winfo_reqheight()
                if near is None:
                    x = self.winfo_rootx() + (self.winfo_width() - w) // 2
                    y = self.winfo_rooty() + (self.winfo_height() - h) // 3
                else:
                    x, y = near(w, h)
                _w, _h, x, y = self._fit_to_work_area(w, h, x, y)
                win.geometry(f"+{x}+{y}")
                win.deiconify()
                # 入力を受ける部品（検索窓の入力欄など）へ入力を移す。移さないと、
                # 検索を開いてすぐ打った字が本文に入ってしまう
                if focus is not None:
                    target = focus()
                    if target is not None and target.winfo_exists():
                        target.focus_set()
            except tk.TclError:
                pass   # 置く前に閉じられた
        win.after(50, place)

    def _virtual_screen(self):
        """全モニタを合わせた範囲 (x, y, 幅, 高さ)。左や上のモニタは x, y が負になる。
        Windows は GetSystemMetrics で本当の仮想デスクトップを取る
        （Tk の winfo_vroot* は Windows ではプライマリの大きさしか返さない）。
        取れない環境ではプライマリ画面を返す＝従来と同じ判定になる。"""
        if core.IS_WIN:
            try:
                import ctypes
                gsm = ctypes.windll.user32.GetSystemMetrics
                # SM_XVIRTUALSCREEN=76 / SM_YVIRTUALSCREEN=77 /
                # SM_CXVIRTUALSCREEN=78 / SM_CYVIRTUALSCREEN=79
                vw, vh = gsm(78), gsm(79)
                if vw > 0 and vh > 0:
                    return gsm(76), gsm(77), vw, vh
            except Exception:
                pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _is_zoomed(self):
        """最大化しているか（保存用）。取れなければ False。"""
        try:
            return self.state() == "zoomed"
        except tk.TclError:
            return False

    def _save_settings(self):
        try:
            # 先に文字列化してからアトミック書き込み。open("w")→dump の順だと
            # 引数評価で例外が出た時点で既存ファイルが0バイトに切り詰められていた
            payload = json.dumps(self._settings_dict(),
                                 ensure_ascii=False, indent=2)
            _write_atomic(SETTINGS_PATH, payload)
        except Exception:
            pass

    def _on_close(self):
        # 音声生成の保存フェーズ等で閉じるとプロセスごと即死し、書きかけの
        # ファイルが壊れたまま残る。実行中は一度だけ確認する
        if self.busy or self._previewing:
            if not messagebox.askyesno(
                    "確認", "処理・再生の実行中です。中断して終了しますか？\n"
                    "（書き出し途中のファイルは不完全なまま残ることがあります）"):
                return
            for ev in (self._synth_cancel, self._extract_cancel,
                       self._preview_stop, self._vvproj_cancel):
                if ev is not None:
                    ev.set()
        if self._playall_stop is not None:
            self._playall_stop.set()  # 連続再生中でも即終了できるように
        # 中断を伝えたあと、ワーカーが finally で後片づけ（一時フォルダの削除・
        # 書きかけファイルの始末）を終えるのを数秒だけ待つ
        if any(t.is_alive() for t in self._workers):
            try:
                self.status_var.set("後片づけをしています… もう少しだけ待ってね")
                self.update_idletasks()
            except tk.TclError:
                pass
            self._wait_workers(4.0)
        # 自分で起動したエンジンだけを終わらせる。ユーザーが自分で開いた
        # VOICEVOXには絶対に触らない（_engine_proc が None ならここは素通り）
        if self._engine_proc is not None:
            core.stop_process(self._engine_proc)
            self._engine_proc = None
        core.strong_ocr_shutdown()   # 文字を読む裏方も、閉じたら止める
        for attr in ("_blink_after", "_mouth_after"):  # 立ち絵アニメの後始末
            h = getattr(self, attr, None)
            if h:
                try:
                    self.after_cancel(h)
                except tk.TclError:
                    pass
        core.sweep_play_tmp_on_exit()   # 最後に鳴らした音の一時ファイルを片づける
        self._stop_ticks()   # 窓を閉じた後にコールバックが動かないように
        self._save_settings()
        self._save_text_cache()
        self.destroy()

    # ---------------- キュー処理（UIスレッド） ----------------
    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                try:
                    self._dispatch_msg(msg)
                except Exception:
                    # 1メッセージの処理例外でキューポンプ全体を殺さない（従来は
                    # after再スケジュールに届かず、以後ワーカー完了が二度と処理
                    # されない＝UI永久フリーズの単一障害点だった）。状態を復旧して続行。
                    # pythonw では黒い画面に出しても誰も見ないので、エラー.log にも残す
                    traceback.print_exc()
                    self._log_error(traceback.format_exc(), "画面の書き換え（%s）" % (
                        msg[0] if isinstance(msg, tuple) and msg else "?"))
                    self._synth_restore_button()
                    self._extract_restore_button()
                    self._vvproj_restore_button()
                    self._set_busy(False)
        except queue.Empty:
            pass
        finally:
            try:
                self._tick("poll", 120, self._poll_queue)
            except tk.TclError:
                pass   # destroy後に残ったafterは無視

    def _dispatch_msg(self, msg):
        """ワーカーからの1メッセージをUIへ反映する（_poll_queue から呼ばれる）。"""
        kind = msg[0]
        if kind == "progress":
            _, done, total, text = msg
            self.progress.config(maximum=max(total, 1), value=done)
            self.status_var.set(text)
        elif kind == "extract_done":
            _, cleaned, warnings, report = msg
            self._extract_restore_button()
            self.text.delete("1.0", "end")
            self.text.insert("1.0", cleaned)
            self._hide_resume()     # 前回の続きは、入れ替わる前の本文の話
            self._reset_bookmark()  # しおりも前の本文の行番号なので捨てる
            self._cursor_to_top()   # 抽出直後に連続再生しても頭から読まれるように
            # 本文が変わったので復元ポイントは _on_text_modified が自動で無効化する
            self.progress.config(value=self.progress["maximum"])
            self._shape_report = {}   # 新しい抽出でレポートを作り直す
            n_r, n_c = self._merge_report(report)
            n_h = len(report.get("heads", []))
            parts = [p for p in (f"ページの見出し・番号{n_h}行" if n_h else "",
                                 f"ノイズ除去{n_r}行" if n_r else "",
                                 f"誤字補正{n_c}件" if n_c else "") if p]
            shaped = (f"｜{'・'.join(parts)}（「直した所」で見られます）" if parts else "")
            n = len([l for l in cleaned.split("\n") if l.strip()])
            try:
                # ＃メモ行・@タグは読まれないので、めやすからも除いて概算する
                est = core.fmt_duration(core.estimate_read_seconds(
                    core.speakable_text(cleaned), self.speed_var.get()))
            except tk.TclError:
                est = "?"
            self.status_var.set(f"✅ 抽出できたよ：{n}行・読み上げめやす{est}"
                                f"（▶ 再生で聴けるよ。ファイルにするなら"
                                f"「♪ 音声を生成」🍂）{shaped}")
            self._set_busy(False)
            if warnings:
                self._warn_with_english(warnings)
        elif kind == "engine":
            _, ver, speakers, quiet = msg[:4]
            status = msg[4] if len(msg) > 4 else ("ok" if ver else "refused")
            used = msg[5] if len(msg) > 5 else self.base_url
            if used and used != self.url_var.get().strip():
                # 別のポートで見つけたときは黙って繋がず、必ず画面に出す
                self.url_var.set(used)
                if ver and used != VOICEVOX_DEFAULT:
                    self.status_var.set(
                        f"VOICEVOXが別のポートで動いていたので、{used} につないだよ🍂")
            if ver:
                # 話者コンボの作り直しは、初回・エンジン更新・話者構成が変わった
                # ときだけ。毎回作り直すと再接続のたびに保存済み話者へ巻き戻り、
                # セッション中に選び直した声が黙って変わってしまう
                rebuild = (not self.speakers or ver != self._engine_ver
                           or speakers != self.speakers)
                self.speakers = speakers
                if rebuild:
                    cur = self._current_speaker_label()   # 今の選択を退避
                    labels = [s[0] for s in speakers]
                    self._build_char_map()
                    # 退避した選択 → 保存済み → 先頭、の順に復元を試みる
                    if not ((cur and self._select_speaker_label(cur))
                            or (self._saved_speaker and
                                self._select_speaker_label(self._saved_speaker))):
                        if self._char_map:
                            self.char_cb.current(0)
                            self._char_selected()
                    dcur = self.dlg_speaker_cb.get()
                    self.dlg_speaker_cb.config(values=labels, state="readonly")
                    if labels:
                        want = dcur or self._saved_dlg_speaker
                        self.dlg_speaker_cb.current(
                            labels.index(want) if want in labels else 0)
                self._autostart_pending = False   # つながった（起動しなくてよい）
                self._set_conn_state("ok", ver)
                self._warm_speaker()   # 最初の1回だけ遅い、をなくす
            else:
                # 起動直後の自動リトライ中は「未接続」と脅かさず待ちを伝える。
                # 一度つながった後に失われたときは、その事実が伝わる文言にする
                if time.monotonic() < self._conn_retry_until:
                    self._set_conn_state("waiting")
                elif self.conn_state == "ok":
                    self._set_conn_state("lost")
                else:
                    # 「未接続」で一括りにせず、理由が分かる文言を出す
                    self._set_conn_state(status if status in self._CONN_TEXT
                                         else "refused")
                if self._autostart_pending:
                    # 起動してすぐの確認で見つからなかった → 設定どおり起動する／聞く
                    self._autostart_pending = False
                    self._engine_autostart_check()
            self._conn_checking = False
            if not quiet:
                self._set_busy(False)
            elif ver and not self.busy and not self._previewing:
                # quiet成功時もボタン有効化は必要（実処理は走っていないので安全）
                self._set_busy(False)
        elif kind == "vvproj_progress":
            _, done, total = msg
            self.progress.config(value=done)
            self.status_var.set(f"調整値を取得中… {done}/{total}行")
        elif kind == "vvproj_done":
            _, out, payload, nfail, entries, default = msg
            self._vvproj_restore_button()
            self._set_busy(False)
            self.progress.config(value=0)
            self.status_var.set("プロジェクトを書き出しました。")
            self._finish_vvproj(out, payload, nfail, entries, default)
        elif kind == "vvproj_cancelled":
            self._vvproj_restore_button()
            self._set_busy(False)
            self.progress.config(value=0)
            self.status_var.set("書き出しをキャンセルしました（ファイルは作っていません）。")
        elif kind == "vvproj_error":
            _, tb = msg
            self._vvproj_restore_button()
            self._set_busy(False)
            self.progress.config(value=0)
            self.status_var.set("うまくいきませんでした…（内容を確認してね）")
            self._show_friendly_error(tb, "プロジェクト保存(.vvproj)")
        elif kind == "health":
            _, ver = msg
            self._health_checking = False
            if ver:
                self._conn_fail = 0
            elif self.conn_state == "ok":
                # 1回の取りこぼしでは切らない（瞬間的な失敗で脅かさないため）。
                # ただし本番の間隔まで待つと気づくのが遅いので、すぐ確かめ直す
                self._conn_fail += 1
                if self._conn_fail >= 2:
                    self._conn_fail = 0
                    self._set_conn_state("lost")
                else:
                    try:
                        self.after(3000, self._health_probe_now)
                    except tk.TclError:
                        pass   # 終了中
        elif kind == "auto_page_shot":
            _, img = msg
            self._auto_grabbing = False
            self._auto_page_step(img)
        elif kind == "clip_done":
            _, cleaned, report, warnings, source = msg
            self._set_busy(False)
            self._merge_report(report)  # 追記なのでレポートは累積する
            from_screen = source in ("screen", "screen_auto")
            what = "囲んだ所" if from_screen else "コピーした画像"
            if (source == "screen_auto" and cleaned
                    and cleaned.strip() == (self._screen_last_text or "").strip()):
                # 時計や点滅などで画面が変わっただけで、文章は同じ。読み直さない
                self.status_var.set("同じ文章だったので読みませんでした（ページが変わるのを待っています）。")
            elif not cleaned:
                self.status_var.set(f"{what}から文字を検出できませんでした。"
                                    + ("（Macで画面が真っ暗・壁紙だけのときは"
                                       "「画面収録」の許可が要ります）"
                                       if from_screen and core.IS_MAC else ""))
            else:
                cur = self.text.get("1.0", "end").strip()
                if cur:
                    # 足した文章の1行目（最終行の次の行）。スクリーンショットのときはここから読む
                    first = int(self.text.index("end-1c").split(".")[0]) + 1
                    self.text.insert("end", "\n" + cleaned)
                else:
                    first = 1
                    self._reset_bookmark()   # 空の本文に新しく入れた＝前のしおりは別の本文の話
                    self.text.insert("1.0", cleaned)
                if from_screen:
                    self._screen_last_text = cleaned
                    self._screen_read_aloud(first, len(cleaned))
                else:
                    self._cursor_to_top()
                    self.status_var.set(
                        f"コピーした画像の文字を読み取りました（{len(cleaned)}文字を足しました）")
            if warnings:
                # 英文が崩れて読まれたときは、直し方まで案内する小窓に回す
                self._warn_with_english(warnings)
        elif kind == "preview_playing":
            _, line, sid, lineno, gen = msg
            if gen != self._play_gen:
                return   # 前の再生から遅れて届いた通知
            self._start_mouth(sid)  # 立ち絵の口パク（喋る話者のキャラに切替）
            if lineno:
                # 連続再生と同じく、いま読んでいる行をハイライト表示
                self.text.tag_remove("playing", "1.0", "end")
                self.text.tag_add("playing", f"{lineno}.0", f"{lineno}.end")
                self._furi_recolor()
            self.status_var.set(f"試聴 再生中: {line[:30]}")
        elif kind == "preview_done":
            _, ok, info, gen = msg
            # 停止後の強制復帰（_recover_if_stuck）を挟んで次の再生が始まって
            # いると、この通知はもう古い。ここで処理すると新しい再生の停止
            # ボタンを消してしまい、鳴っている音を止められなくなる
            if gen != self._play_gen:
                return
            self._previewing = False
            self._preview_stop = None
            self._stop_mouth()
            self.text.tag_remove("playing", "1.0", "end")
            self._furi_recolor()
            self.stop_btn.config(state="disabled")
            if self._engine_ready() and not self.busy:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")
                self.sample_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
            if ok:
                # “どう読んだか”を残す（誤読はここで気づいて辞書登録できる）
                note = f"　読み「{info[:44]}…」" if len(info) > 44 else \
                    (f"　読み「{info}」" if info else "")
                self.status_var.set(f"試聴 おわり🍂{note}")
            else:
                self.status_var.set("試聴がうまくいきませんでした…")
                self._show_friendly_error(info, "試聴")
        elif kind == "playall_line":
            _, lineno, line, sid, done, total, gen = msg
            if gen != self._play_gen:
                return   # 前の再生から遅れて届いた通知
            # 再生中の行にカーソルを移してハイライト表示。しおりも更新
            self._bookmark = lineno
            self._line_sounding = True
            self._start_mouth(sid)  # 行の話者に合わせてキャラ切替＋口パク
            if self._playall_pause is not None and self._playall_pause.is_set():
                self._close_mouth()   # 一時停止中に届いた（止める直前に鳴り始めた行）
            self.text.tag_remove("playing", "1.0", "end")
            self.text.tag_add("playing", f"{lineno}.0", f"{lineno}.end")
            self._furi_recolor()
            self.text.mark_set("insert", f"{lineno}.0")
            self._follow_reading(lineno)
            self._set_position(lineno)
            self.status_var.set(f"連続再生中 {done+1}/{total}: {line[:30]}")
        elif kind == "playall_line_end":
            _, lineno, gen = msg
            if gen != self._play_gen:
                return
            # 行を読み終えた。行間は口を閉じる（次の行が鳴り始めたらまた動かす）
            self._line_sounding = False
            self._close_mouth()
        elif kind == "playall_skip":
            _, lineno, gen = msg
            if gen != self._play_gen:
                return
            self.status_var.set(f"⚠ {lineno}行目は合成できなかったので"
                                "スキップしたよ（続きは読むね）")
        elif kind == "playall_done":
            _, ok, info, played, skipped, gen = msg
            if gen != self._play_gen:
                return   # 上の preview_done と同じ理由で、古い通知は捨てる
            if not ok:
                self.after(0, self._check_engine_alive)   # VOICEVOX が止まったなら起こし直す
            self._previewing = False
            self._playall_stop = None
            self._playall_pause = None
            self._line_sounding = False
            self._sync_play_buttons()
            self._stop_mouth()
            self.text.tag_remove("playing", "1.0", "end")
            self._mark_bookmark()   # 次に「▶ 続きを聴く」で再開する行を可視化
            self.stop_btn.config(state="disabled")
            self.pause_btn.config(text="‖ 一時停止", state="disabled")
            if self._engine_ready() and not self.busy:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")
                self.sample_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
            from_screen, self._screen_reading = self._screen_reading, False
            if ok and not info and from_screen and self._screen_last is not None:
                mod = "⌘" if core.IS_MAC else "Ctrl+"
                self.status_var.set(
                    "📖 読み終わったよ。ページをめくると自動で読みます。"
                    if self.auto_page_var.get() else
                    f"📖 読み終わったよ。ページをめくったら「同じ範囲を読む」（{mod}Shift+R）。")
            elif ok:
                skip_note = f"・{skipped}行スキップ" if skipped else ""
                self.status_var.set(
                    f"連続再生を停止しました（{played}行読んだよ{skip_note}）" if info
                    else f"📖 最後まで読み終わったよ（{played}行{skip_note}）"
                         "おつかれさま！")
            else:
                self.status_var.set("連続再生がうまくいきませんでした…")
                self._show_friendly_error(info, "連続再生")
        elif kind == "furigana":
            _, got, keyinfo, failed = msg
            self._furi_store(got, keyinfo, failed)
        elif kind == "furi_seek":
            _, got, keyinfo, failed, token = msg
            self._seek_got(got, keyinfo, failed, token)
        elif kind == "face":
            _, style_id, data = msg
            self._face_done(style_id, data)
        elif kind == "furigana_dict":
            _, ok, info = msg
            self._furi_dict_done(ok, info)
        elif kind == "dict_list":
            _, rows = msg
            self._furi_schedule(200)   # 読み方辞書が変わったら、ふりがなも読み直す
            if self._dict_win is not None and self._dict_win.winfo_exists():
                tree = self._dict_tree
                tree.delete(*tree.get_children())
                labels = dict(core.WORD_TYPE_LABELS)
                for w in rows:
                    tree.insert("", "end", iid=w["uuid"],
                                values=(w["surface"], w["pronunciation"],
                                        w["accent_type"],
                                        labels.get(w["word_type"], "—"),
                                        "—" if w["priority"] is None
                                        else w["priority"]))
                # 一覧は後から届く。単語欄が先に埋まっていた場合（本文を
                # 右クリックして登録する経路）に備えて、ここでも合わせ直す
                self._dict_sync_form_to_word()
        elif kind == "dict_status":
            _, info = msg
            self.status_var.set(info)
        elif kind == "cache_limit_applied":
            # キャッシュ上限のevict完了。ダイアログが開いていれば統計を更新
            r = getattr(self, "_cache_refresh", None)
            if r is not None and getattr(self, "_cache_win", None) is not None \
                    and self._cache_win.winfo_exists():
                try:
                    r()
                except tk.TclError:
                    pass
            self.status_var.set("しまう量の上限を変えました"
                                "（次回起動にも引き継がれます）。")
        elif kind == "synth_done":
            _, info, target, credit = msg
            self._synth_restore_button()
            self.status_var.set("🎉 音声ができたよ！")
            self._set_busy(False)
            self._show_done_dialog(info, target, credit)
        elif kind == "synth_saving":
            # 合成が終わり保存フェーズへ。ここからはキャンセル不可にする
            if self._synth_cancel is not None:
                self.synth_btn.config(state="disabled")
                self.status_var.set("ファイルに保存中…（もう少しで完成！）")
        elif kind == "synth_partial":
            # キャンセルされたが合成済みの先頭部分がある（全文結合のみ）。
            # 「ここまでを保存する？」を確認し、Yesなら別ワーカーで書き出す
            _, info = msg
            self._synth_restore_button()
            if messagebox.askyesno(
                    "ここまでを保存",
                    f"キャンセルしました（{info['done']}/{info['total']}行まで"
                    "合成済み）。\nここまでの音声を保存しますか？"):
                self.status_var.set("ここまでの音声を保存中…")
                self._spawn(self._partial_save_worker, (info,))
            else:
                try:
                    os.remove(info["part"])
                except OSError:
                    pass
                self._set_busy(False)
                self.status_var.set(
                    f"音声生成をキャンセルしたよ（{info['done']}/{info['total']}行"
                    "まで合成・ファイルは保存していません）。またいつでもどうぞ🍂")
        elif kind == "synth_cancelled":
            _, done, total, saved = msg
            self._synth_restore_button()
            self._set_busy(False)
            saved_note = (f"{saved}ファイルは保存済み" if saved
                          else "ファイルは保存していません")
            self.status_var.set(
                f"音声生成をキャンセルしたよ（{done}/{total}行まで合成・"
                f"{saved_note}）。またいつでもどうぞ🍂")
        elif kind == "unexpected":
            _, tb, where = msg
            self._report_unexpected(tb, where)
        elif kind == "gpu_failed":
            self._gpu_fallback()
        elif kind == "update":
            _, tag, url = msg
            if tag:   # 聞けなかったとき（つながっていない等）は、次に開いたときにまた聞く
                self._update_checked_at = time.time()
                self._update_latest, self._update_url = tag, url or ""
                if core.is_newer(tag):
                    self._show_update()
        elif kind == "error":
            _, tb = msg
            self.after(0, self._check_engine_alive)   # VOICEVOX が止まったなら起こし直す
            self._synth_restore_button()    # 生成エラー時もボタンを元に戻す
            self._extract_restore_button()  # 抽出エラー時も同様
            self._set_busy(False)
            self.status_var.set("うまくいきませんでした…（内容を確認してね）")
            self._show_friendly_error(tb, "テキスト抽出／音声生成")
        elif kind == "synth_line_error":
            # 1行だけがエンジンに断られ続けて生成が止まった。その行を選択して見せ、
            # 何行目の何という文かをエラーの頭に添える（どこを直せばいいか分かるように）
            _, tb, lineno, line = msg
            self._synth_restore_button()
            self._set_busy(False)
            # 選択だけだと、エラーの窓に入力が移っている間は色が付かず見えない。
            # 色付きの印（errline）も付ける（次の音声生成で外す）
            self.text.tag_remove("sel", "1.0", "end")
            self.text.tag_add("sel", f"{lineno}.0", f"{lineno}.end")
            self.text.tag_add("errline", f"{lineno}.0", f"{lineno}.end")
            self._furi_recolor()
            self.text.mark_set("insert", f"{lineno}.0")
            self.text.see(f"{lineno}.0")
            snippet = line if len(line) <= 30 else line[:30] + "…"
            self.status_var.set(f"{lineno}行目で止まりました（その行を選んであるよ）")
            self._show_friendly_error(
                tb, f"音声生成（{lineno}行目）",
                lead=f"{lineno}行目「{snippet}」で止まりました。")

    def _tick(self, name, ms, func):
        """定期ループの予約を名前つきで持っておく（終了時にまとめて止めるため）。"""
        try:
            self._ticks[name] = self.after(ms, func)
        except tk.TclError:
            self._ticks.pop(name, None)   # 終了中

    def _stop_ticks(self):
        """予約してある定期ループをすべて止める。"""
        for h in list(self._ticks.values()):
            try:
                self.after_cancel(h)
            except tk.TclError:
                pass
        self._ticks.clear()

    def _spawn(self, target, args=()):
        """ファイルや一時フォルダを触るワーカーを、控えを取ってから起動する。
        窓を閉じるときに「終わったかどうか」を見られるようにするため。"""
        self._workers = [t for t in self._workers if t.is_alive()]
        t = threading.Thread(target=target, args=args, daemon=True)
        self._workers.append(t)
        t.start()
        return t

    def _wait_workers(self, sec=4.0):
        """中断を知らせたワーカーが後始末を終えるのを、短いあいだだけ待つ。
        待たずに destroy() するとプロセスごと消えて finally が動かず、
        書きかけのファイルと数GB級の一時フォルダがそのまま残る。
        待つのは数秒だけ（応答しないワーカーのために閉じられなくしない）。"""
        end = time.monotonic() + sec
        while any(t.is_alive() for t in self._workers):
            if time.monotonic() >= end:
                return False
            time.sleep(0.05)
        return True

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        # 抽出中は extract_btn が「× キャンセル」に切り替わるため無効化しない
        if self._extract_cancel is None:
            self.extract_btn.config(state=state)
        self.clip_btn.config(state=state)
        self.screen_btn.config(state=state)
        if self.screen_again_btn is not None:
            self.screen_again_btn.config(state=state)
        if busy:
            # 音声生成中は synth_btn が「キャンセル」に切り替わるため無効化しない
            if self._synth_cancel is None:
                self.synth_btn.config(state="disabled")
            self.preview_btn.config(state="disabled")
            self.playall_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.sample_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
        elif self._engine_ready():
            self.synth_btn.config(state="normal")
            if not self._previewing:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")
                self.sample_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
        self._update_step_highlight()


def _report_startup_error(err_text):
    """起動失敗をユーザーに伝える。起動.pyw は pythonw（コンソール無し）で起動する
    ため、ここで拾わないと例外は完全に無言＝「ダブルクリックしても何も起きない」に
    見える。ログに残した上でダイアログでも知らせる。Tk 自体が壊れているときは
    Windows標準の MessageBox に退避する。"""
    log_path = os.path.join(APP_DIR, "起動エラー.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(err_text)
    except Exception:
        pass
    # 構築途中で落ちた場合、スプラッシュ（起動中の小窓）が凍ったまま残るので、
    # エラーダイアログを出す前に元のTkごと巻き込んで確実に消す
    try:
        if tk._default_root is not None:
            tk._default_root.destroy()
    except Exception:
        pass
    if os.path.exists(os.path.join(APP_DIR, "runtime", "pythonw.exe")):
        # 1行導入で入れた Python 入りの版には venv も、.pyw を開く Python も無い
        redo = ("PowerShell に1行導入の1行をもう一度貼り付けると、入れ直します。\n"
                "直らないときは「起動エラー.log」の中身を作者に送ってください。")
    else:
        redo = ("Windows は" + _in_app_folder("venv") + "を消してから 起動.pyw を開くと、部品を入れ直します"
                "（Mac は setup.command をもう一度実行）。\n"
                "直らないときは「デバッグ起動」で画面に出るエラーを確認してください。")
    msg = ("起動に失敗しました。\n\n"
           "詳しい内容を" + _in_app_folder("起動エラー.log") + "に保存しました。\n" + redo)
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("TextToVoicevox", msg)
        root.destroy()
    except Exception:
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None, msg, "TextToVoicevox", 0x10)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        app = App()
        # 裏の処理で受け止めていないエラーも記録する（テストでは pytest の見張りを残すため、ここでだけ）
        threading.excepthook = app._thread_exception
        app.mainloop()
    except Exception:
        # D&D の部品（tkdnd）が壊れていても、ここには来ない（窓が出たあとで読み込み、
        # 読めなければ D&D 無しで動く。上の import 参照）
        _report_startup_error(traceback.format_exc())
        raise
