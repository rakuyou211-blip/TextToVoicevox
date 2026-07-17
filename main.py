# -*- coding: utf-8 -*-
"""
PDF・画像 → テキスト抽出 → VOICEVOX 連携ツール（オフライン）
GUI本体。テキスト抽出(core)とVOICEVOXエンジン連携を tkinter で操作する。
"""
import os
import re
import json
import time
import queue
import random
import threading
import traceback
import tempfile

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

from PIL import ImageGrab

import core

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
TEXT_CACHE_PATH = os.path.join(APP_DIR, "last_text.txt")
# キャラ立ち絵とアプリアイコン（任意・ローカル資産）。無くてもアプリは動く。
PORTRAIT_DIR = os.path.join(APP_DIR, "assets", "立ち絵")
APP_ICON_PATH = os.path.join(APP_DIR, "assets", "app-icon.png")

# ドラッグ＆ドロップ対応（tkinterdnd2 が無くてもアプリは動く）
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
    _Base = TkinterDnD.Tk
except Exception:
    _HAS_DND = False
    _Base = tk.Tk
    DND_FILES = None

APP_TITLE = f"テキスト抽出 → VOICEVOX  v{core.APP_VERSION}（オフライン）"
VOICEVOX_DEFAULT = "http://127.0.0.1:50021"
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

# レイアウトの余白定数（全セクションで統一し、バラバラなpadx/padyを解消する）
PAD_X = 8    # セクション外周の水平余白
PAD_Y = 6    # セクション外周の垂直余白
GAPX = 6     # 同一行のラベルと入力欄・小ブロック間の間隔
GAPY = 4     # grid の行間


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


class App(_Base):
    # 出力単位: 内部キー → 表示ラベル（コンボボックスの並び順と一致させる）
    _UNITS = {"each": "1行=1ファイル", "combine": "全文を結合",
              "nlines": "N行ごと", "para": "段落ごと"}

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self._set_window_icon()
        self.geometry("980x880")
        self.minsize(860, 700)

        self.files = []                 # 入力ファイルパス
        self.q = queue.Queue()          # ワーカー→UI 通知
        self.busy = False
        self.speakers = []              # [(label, id)]
        self.base_url = VOICEVOX_DEFAULT
        self._previewing = False
        self._preview_buf = None        # 再生中WAVの参照保持（GC防止）
        self._saved_speaker = None      # 設定から復元する話者ラベル
        self._playall_stop = None       # 連続再生の停止イベント
        self.replace_rules = []         # 保存済み置換ルール [[find, repl], ...]
        self._dict_win = None           # ユーザー辞書ダイアログ
        self.presets = []               # 声プリセット [{name, speaker, speed, ...}]
        self._bookmark = None           # 連続再生のしおり（最後に再生した行番号）
        self._saved_dlg_speaker = None  # 設定から復元するセリフ話者ラベル
        self._cleared_text = None       # 「本文を全消去」で退避した本文（復元用）
        self._suppress_modified = False  # <<Modified>>ハンドラの一時抑止（全消去/復元の自編集用）
        self._vdetail_open = False      # プリセット/セリフ行の開閉状態（§4の折りたたみ）
        self._resize_after = None       # <Configure>デバウンス用のafterハンドル
        self.encoders = core.audio_encoders()  # 使える音声変換 {"m4a":..., "mp3":...}

        self.dark_var = tk.BooleanVar(value=False)   # 旧設定キー互換（theme=="dark"と同期）
        self.theme_var = tk.StringVar(value="light")  # テーマの本体（light/dark/hc/zunda）
        self._build_ui()
        # 画像等を画面のどこに落としても効くよう、UI全ウィジェットをドロップ先に登録する
        self._register_drop_tree(self)
        self._load_settings()
        # ライト/ダークとも clam ベースのデザインパレットを常に適用する（起動時に美観を反映）
        self.apply_theme()
        self._restore_text_cache()
        self._update_step_highlight()   # 本文の有無に応じて「次に押すボタン」を絞る
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._poll_queue)
        self.after(600, self._auto_connect)  # 起動時にエンジンへ自動接続

    # ---------------- UI構築 ----------------
    def _build_ui(self):
        if UI_FONT:
            try:
                self.option_add("*Font", UI_FONT)
            except Exception:
                pass
        self._setup_styles()
        # 状態表示はパネル（吹き出し）とオプション欄の両方から参照するため最初に作る
        # （初期文は短く：長いとrun行の右端コントロールを押し出す。D&DのヒントはS1見出しにある）
        self.status_var = tk.StringVar(value="まずは 1. にファイルを追加してね🍂")
        # 右にキャラ立ち絵パネル（あれば）、左にメイン。右を先に side="right" で確保して
        # 幅をリザーブし、メインは残りを expand で埋める。狭い窓では立ち絵を自動で畳む。
        self._portraits = self._load_portraits()
        self._side = None
        self._side_shown = False
        if self._portraits:
            self._side = ttk.Frame(self)
            self._side.pack(side="right", fill="y", padx=(0, PAD_X), pady=PAD_Y)
            self._side_shown = True
            self._build_portrait_panel(self._side)
        self._main = ttk.Frame(self)
        self._main.pack(side="left", fill="both", expand=True)

        # pack順の意味は不変（親は self._main）：上から files→options を積み、VOICEVOX を
        # side="bottom" で先に確保（窓が小さくても隠れない意図）。最後に result(本文) を
        # expand で残りに広げる（本文テキスト欄が伸縮の主役）。
        self._build_files_section()
        self._build_options_section()
        self._build_voicevox_section()
        self._build_result_section()

        if self._side is not None:
            self.bind("<Configure>", self._on_configure, add="+")

    # ---------------- スタイル（フォント） ----------------
    def _setup_styles(self):
        """見出し・主要ボタン・クレジット用のフォントを用意する。
        実際の色付け（テーマ）は apply_theme()→_paint() が一括で行う。"""
        try:
            base = tkfont.nametofont("TkDefaultFont")
            fam, size = base.cget("family"), int(base.cget("size"))
        except Exception:
            fam, size = "TkDefaultFont", 12
        self._heading_font = (fam, size, "bold")
        self._primary_font = (fam, size, "bold")
        self._credit_font = (fam, max(9, size - 2))

    # ---------------- §1 入力ファイル ----------------
    def _build_files_section(self):
        hint = "（画像・PDF・フォルダを画面のどこにでもドラッグ＆ドロップ）" if _HAS_DND else ""
        top = ttk.LabelFrame(self._main, text="1. 📥 入力ファイル" + hint)
        top.pack(fill="x", padx=PAD_X, pady=(PAD_Y, PAD_Y // 2))

        btns = ttk.Frame(top)
        btns.pack(side="left", fill="y", padx=GAPX, pady=GAPY)
        ttk.Button(btns, text="ファイル追加", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="フォルダ追加", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="選択削除", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="全クリア", command=self.clear_files).pack(fill="x", pady=2)
        self.clip_btn = ttk.Button(btns, text="クリップボードOCR", command=self.clipboard_ocr)
        self.clip_btn.pack(fill="x", pady=(8, 2))

        lst = ttk.Frame(top)
        lst.pack(side="left", fill="both", expand=True, padx=GAPX, pady=GAPY)
        self.listbox = tk.Listbox(lst, height=5, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lst, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        # ドラッグ＆ドロップは _build_ui 完了後に _register_drop_tree が全ウィジェットへ
        # 一括登録する（新設フレームも子ツリーに繋がっていれば自動で対象になる）。macOSでは
        # 子ウィジェットがルート窓を覆うため、個別登録でないと「落としても反応しない」ため。

    # ---------------- §2 抽出オプション ----------------
    def _build_options_section(self):
        opt = ttk.LabelFrame(self._main, text="2. ⚙️ 抽出オプション")
        opt.pack(fill="x", padx=PAD_X, pady=PAD_Y // 2)

        # --- 基本（入出力）: 改行の扱い / PDF処理 / 解像度。ラベルを col0 で縦に揃える ---
        basic = ttk.Frame(opt)
        basic.pack(fill="x", padx=GAPX, pady=(GAPY, 2))
        ttk.Label(basic, text="改行の扱い:").grid(row=0, column=0, sticky="w", pady=1)
        self.mode_var = tk.StringVar(value="sentence")
        mrow = ttk.Frame(basic)
        mrow.grid(row=0, column=1, sticky="w", padx=(GAPX, 0))
        ttk.Radiobutton(mrow, text="文ごとに改行（VOICEVOX推奨）",
                        variable=self.mode_var, value="sentence").pack(side="left")
        ttk.Radiobutton(mrow, text="元の改行を保持",
                        variable=self.mode_var, value="keep").pack(side="left", padx=(GAPX, 0))

        ttk.Label(basic, text="PDF処理:").grid(row=1, column=0, sticky="w", pady=1)
        self.pdf_var = tk.StringVar(value="auto")
        prow = ttk.Frame(basic)
        prow.grid(row=1, column=1, sticky="w", padx=(GAPX, 0))
        ttk.Radiobutton(prow, text="自動（テキスト層→無ければOCR）",
                        variable=self.pdf_var, value="auto").pack(side="left")
        ttk.Radiobutton(prow, text="常にOCR（スキャン/文字化け対策）",
                        variable=self.pdf_var, value="ocr").pack(side="left", padx=(GAPX, 0))
        ttk.Label(prow, text="解像度(DPI):").pack(side="left", padx=(GAPX, 2))
        self.dpi_var = tk.IntVar(value=300)
        ttk.Spinbox(prow, from_=150, to=400, increment=50, width=5,
                    textvariable=self.dpi_var).pack(side="left")

        ttk.Separator(opt, orient="horizontal").pack(fill="x", padx=GAPX, pady=(4, 2))

        # --- 整形: よく使う項目は常時表示、上級者向けは「詳細設定」で折りたたむ ---
        head = ttk.Frame(opt)
        head.pack(fill="x", padx=GAPX)
        ttk.Label(head, text="整形", style="Cluster.TLabel").pack(side="left")
        self._adv_btn = ttk.Button(head, text="詳細設定 ▸", width=12,
                                   command=self._toggle_advanced)
        self._adv_btn.pack(side="right")

        common = ttk.Frame(opt)
        common.pack(fill="x", padx=GAPX, pady=(2, 0))
        self.pre_var = tk.BooleanVar(value=True)
        self.blank_var = tk.BooleanVar(value=True)
        self.ascii_var = tk.BooleanVar(value=True)
        self.smartjoin_var = tk.BooleanVar(value=False)
        self.denoise_var = tk.BooleanVar(value=True)
        self.fixconf_var = tk.BooleanVar(value=True)
        common_defs = [
            (self.pre_var, "画像前処理（精度向上）",
             "写真やスキャンを補正してOCRの精度を上げます。"),
            (self.blank_var, "空行を削除", "連続する空行を1つにまとめます。"),
            (self.ascii_var, "英数字間の空白を保持",
             "英単語の間の半角スペースを残します。"),
            (self.smartjoin_var, "折り返しを連結（1段組みの本文向け）",
             "ページ幅で折り返された行を1文につなげます。"),
            (self.denoise_var, "画面キャプチャのノイズを除去",
             "字幕・局ロゴ・UIラベルなど画面上の余計な文字を取り除きます。"),
            (self.fixconf_var, "OCR誤字を補正（力⇄カ・一⇄ー）",
             "OCRが取り違えやすい同形文字を前後の文脈で自動修正します。\n"
             "画像・スキャンPDFのOCR結果にだけ適用されます（テキストファイルは対象外）。"),
        ]
        for i, (var, label, tip) in enumerate(common_defs):
            cb = ttk.Checkbutton(common, text=label, variable=var)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, GAPX), pady=1)
            _Tooltip(cb, tip)
        for cidx in range(3):
            common.columnconfigure(cidx, weight=1)

        # 折りたたみ対象（既定は畳む＝pack しない。子ツリーには繋がるのでD&D登録は効く）
        self._adv_open = False
        self._adv_frame = ttk.Frame(opt)
        self.join_var = tk.BooleanVar(value=False)
        self.pruby_var = tk.BooleanVar(value=False)
        self.norm_var = tk.BooleanVar(value=False)
        adv_defs = [
            (self.join_var, "改行で途切れた文を連結（小説向け）",
             "句点で終わらない改行を前の行につなげます。"),
            (self.pruby_var, "括弧ルビ除去 例:漢字(かんじ)",
             "漢字の後の括弧内の読みがなを削除します。"),
            (self.norm_var, "全角英数→半角・記号を読みに展開",
             "全角の英数字を半角にし、①や㈱・㎡などの記号を\n"
             "読み（1・株式会社・平方メートル）に展開します。"),
        ]
        for i, (var, label, tip) in enumerate(adv_defs):
            cb = ttk.Checkbutton(self._adv_frame, text=label, variable=var)
            cb.grid(row=0, column=i, sticky="w", padx=(0, GAPX), pady=1)
            _Tooltip(cb, tip)

        # --- 実行行：主要操作(抽出)・進捗・状態・テーマ切替 ---
        run = ttk.Frame(opt)
        run.pack(fill="x", padx=GAPX, pady=(4, GAPY))
        self._run_frame = run  # 折りたたみ枠をこの直前(before=)に差し込むための基準
        self.extract_btn = ttk.Button(run, text="▶ テキスト抽出 実行",
                                       style="Primary.TButton", command=self.start_extract)
        self.extract_btn.pack(side="left")
        _Tooltip(self.extract_btn,
                 "追加したファイルからテキストを抽出します"
                 f"（{'⌘' if core.IS_MAC else 'Ctrl+'}Return）。")
        self.progress = ttk.Progressbar(run, mode="determinate", length=240)
        self.progress.pack(side="left", padx=10)
        # テーマ選択（🍂ライト/🌙ダーク/☀️くっきり/🌿ずんだ）。packは後詰めから縮むため、
        # 固定コントロールのテーマ選択を先に確保し、伸縮できる状態文を最後にする
        self.theme_cb = ttk.Combobox(run, width=11, state="readonly",
                                     values=[l for _k, l, _p in self.THEMES])
        self.theme_cb.pack(side="right", padx=4)
        self.theme_cb.bind("<<ComboboxSelected>>", self._theme_selected)
        ttk.Label(run, text="テーマ:").pack(side="right")
        # status_var は _build_ui 冒頭で生成済み（吹き出しと共有）。最後にpack＝
        # 長い状態文があふれた時はここだけが縮み、ボタンやテーマ選択は消えない
        ttk.Label(run, textvariable=self.status_var).pack(side="left")

    def _toggle_advanced(self):
        self._set_advanced(not self._adv_open)

    def _set_advanced(self, show):
        """整形の詳細設定パネルを開閉する（pack/pack_forget と矢印表示の更新のみ）。"""
        self._adv_open = bool(show)
        if self._adv_open:
            self._adv_frame.pack(fill="x", padx=GAPX, pady=(2, 0),
                                 before=self._run_frame)
            self._adv_btn.config(text="詳細設定 ▾")
        else:
            self._adv_frame.pack_forget()
            self._adv_btn.config(text="詳細設定 ▸")

    def _tip_engine_gate(self, ready_text, busy_gated=True):
        """エンジン接続が必要なボタンのツールチップ文を返す関数を作る。
        無効中は「なぜ押せないか」、有効なら本来の説明を出す（ホバー時に評価）。
        busy_gated: 処理・再生中に実際に無効化されるボタン（生成/試聴/連続再生）は
        True。busy中も押せるボタン（辞書・vvproj保存）は False にして誤案内を防ぐ。"""
        def _text():
            if not self.speakers:
                return ("VOICEVOXエンジン未接続です。\n"
                        "「エンジン接続確認」を押すと使えるようになります。")
            if busy_gated and (self.busy or self._previewing):
                return "処理・再生の実行中です。完了までお待ちください。"
            return ready_text
        return _text

    def _toggle_voice_detail(self):
        self._set_voice_detail(not self._vdetail_open)

    def _set_voice_detail(self, show):
        """プリセット/セリフ別話者の行を開閉する（§2の詳細設定と同じpack切替）。"""
        self._vdetail_open = bool(show)
        if self._vdetail_open:
            self._vb_frame.pack(fill="x", padx=GAPX, pady=(2, 0),
                                before=self._vb_sep)
            self._vdetail_btn.config(text="プリセット/セリフ ▾")
        else:
            self._vb_frame.pack_forget()
            self._vdetail_btn.config(text="プリセット/セリフ ▸")

    def _update_step_highlight(self):
        """「次に押す主要ボタン」を1つに絞る：本文が空なら抽出、あれば音声生成を
        Primary（オレンジ）にし、もう一方は同寸のSecondaryへ落とす（レイアウト不変）。"""
        try:
            has_text = bool(self.text.get("1.0", "end-1c").strip())
            self.extract_btn.config(style="Secondary.TButton" if has_text
                                    else "Primary.TButton")
            self.synth_btn.config(style="Primary.TButton" if has_text
                                  else "Secondary.TButton")
        except (tk.TclError, AttributeError):
            pass  # 構築途中・破棄中は無視

    # ---------------- §4 VOICEVOX へ ----------------
    def _build_voicevox_section(self):
        # 先に side="bottom" で確保し、ウィンドウが小さくても隠れないようにする（順序不変）。
        # 4クラスタ［接続］［声・調整］［出力］［再生・生成］を、太字の見出しラベルと薄い
        # 区切り線で分ける。内側LabelFrameは縦を食い本文欄を潰すので使わず、伸縮の主役
        # （§3の本文）を優先して各クラスタは1〜2行に抑える。
        bottom = ttk.LabelFrame(self._main, text="4. 🔊 VOICEVOX へ")
        bottom.pack(side="bottom", fill="x", padx=PAD_X, pady=(PAD_Y // 2, PAD_Y))

        def _sep():
            ttk.Separator(bottom, orient="horizontal").pack(fill="x", padx=GAPX, pady=4)

        # === 接続 ===
        c = ttk.Frame(bottom)
        c.pack(fill="x", padx=GAPX, pady=(GAPY, 0))
        ttk.Label(c, text="接続", style="Cluster.TLabel").pack(side="left", padx=(0, GAPX))
        ttk.Button(c, text="VOICEVOX起動", command=self.launch_voicevox).pack(side="left")
        ttk.Label(c, text="URL:").pack(side="left", padx=(GAPX, 2))
        self.url_var = tk.StringVar(value=self.base_url)
        ttk.Entry(c, textvariable=self.url_var, width=22).pack(side="left")
        ttk.Button(c, text="エンジン接続確認",
                   command=self.check_engine).pack(side="left", padx=(GAPX, 0))
        self.engine_var = tk.StringVar(value="エンジン: 未接続")
        ttk.Label(c, textvariable=self.engine_var).pack(side="left", padx=GAPX)
        _sep()

        # === 声・調整（話速/音高/抑揚/音量を grid の列で揃える。1行に収める） ===
        va = ttk.Frame(bottom)
        va.pack(fill="x", padx=GAPX)
        ttk.Label(va, text="声・調整", style="Cluster.TLabel").pack(side="left", padx=(0, GAPX))
        ttk.Label(va, text="話者:").pack(side="left")
        self.speaker_cb = ttk.Combobox(va, width=30, state="disabled")
        self.speaker_cb.pack(side="left", padx=(2, GAPX))
        self.speaker_cb.bind("<<ComboboxSelected>>", self._update_portrait, add="+")
        # プリセット/セリフ別話者の行（vb）は使わない人も多いので折りたたみ式にする。
        # トグルは独立行を作らず右端に置き、行数削減の効果を保つ
        self._vdetail_btn = ttk.Button(va, text="プリセット/セリフ ▸", width=17,
                                       command=self._toggle_voice_detail)
        self._vdetail_btn.pack(side="right")
        _Tooltip(self._vdetail_btn,
                 "声のプリセット保存と、セリフ行（「」開始）を別話者で読む設定を開閉します。")
        params = ttk.Frame(va)
        params.pack(side="left")
        self.speed_var = tk.DoubleVar(value=1.0)
        self.pitch_var = tk.DoubleVar(value=0.0)
        self.into_var = tk.DoubleVar(value=1.0)
        self.vol_var = tk.DoubleVar(value=1.0)
        specs = [("話速", self.speed_var, 0.5, 2.0, 0.1, 5),
                 ("音高", self.pitch_var, -0.15, 0.15, 0.01, 6),
                 ("抑揚", self.into_var, 0.0, 2.0, 0.1, 5),
                 ("音量", self.vol_var, 0.0, 2.0, 0.1, 5)]
        for col, (name, var, lo, hi, inc, w) in enumerate(specs):
            ttk.Label(params, text=name).grid(row=0, column=2 * col,
                                              padx=(GAPX if col else 0, 2))
            ttk.Spinbox(params, from_=lo, to=hi, increment=inc, width=w,
                        textvariable=var).grid(row=0, column=2 * col + 1)

        # vb行は既定で畳む（_set_voice_detail が pack/pack_forget で開閉。
        # 子ウィジェットはツリーに繋がるため、未表示でもD&D登録や値設定は効く）
        vb = ttk.Frame(bottom)
        self._vb_frame = vb
        ttk.Label(vb, text="プリセット:").pack(side="left")
        self.preset_cb = ttk.Combobox(vb, width=14, state="readonly", values=[])
        self.preset_cb.pack(side="left", padx=2)
        self.preset_cb.bind("<<ComboboxSelected>>", self._preset_selected)
        ttk.Button(vb, text="保存", width=4, command=self.save_preset).pack(side="left", padx=1)
        ttk.Button(vb, text="削除", width=4, command=self.del_preset).pack(side="left", padx=1)
        ttk.Separator(vb, orient="vertical").pack(side="left", fill="y", padx=GAPX)
        self.dlg_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(vb, text="セリフ行(「」開始)を別話者:",
                        variable=self.dlg_var).pack(side="left")
        self.dlg_speaker_cb = ttk.Combobox(vb, width=24, state="disabled")
        self.dlg_speaker_cb.pack(side="left", padx=2)
        ttk.Label(vb, text="※行頭「@話者名:」でも指定可").pack(side="left", padx=GAPX)
        # vb再表示時の挿入位置アンカー（pack(before=)用に参照を保持する）
        self._vb_sep = ttk.Separator(bottom, orient="horizontal")
        self._vb_sep.pack(fill="x", padx=GAPX, pady=4)

        # === 出力（形式と保存） ===
        oa = ttk.Frame(bottom)
        oa.pack(fill="x", padx=GAPX)
        ttk.Label(oa, text="出力", style="Cluster.TLabel").pack(side="left", padx=(0, GAPX))
        ttk.Label(oa, text="音声形式:").pack(side="left")
        self.fmt_cb = ttk.Combobox(oa, width=6, state="readonly",
                                   values=self._format_choices())
        self.fmt_cb.current(0)
        self.fmt_cb.pack(side="left", padx=2)
        ttk.Label(oa, text="まとめ方:").pack(side="left", padx=(GAPX, 0))
        self.unit_cb = ttk.Combobox(oa, width=13, state="readonly",
                                    values=list(self._UNITS.values()))
        self.unit_cb.current(0)
        self.unit_cb.pack(side="left", padx=2)
        self.nlines_var = tk.IntVar(value=50)
        ttk.Spinbox(oa, from_=2, to=1000, increment=10, width=5,
                    textvariable=self.nlines_var).pack(side="left", padx=(2, 0))
        ttk.Label(oa, text="行").pack(side="left")
        ttk.Label(oa, text="文間の無音(秒):").pack(side="left", padx=(GAPX, 0))
        self.gap_var = tk.DoubleVar(value=0.4)
        ttk.Spinbox(oa, from_=0.0, to=3.0, increment=0.1, width=5,
                    textvariable=self.gap_var).pack(side="left")
        self.srt_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(oa, text="字幕(.srt)も保存",
                        variable=self.srt_var).pack(side="left", padx=GAPX)

        ob = ttk.Frame(bottom)
        ob.pack(fill="x", padx=GAPX, pady=(2, 0))
        ttk.Button(ob, text="VOICEVOX用に保存(.txt)",
                   command=self.save_txt).pack(side="left", padx=(0, 2))
        ttk.Button(ob, text="クリップボードにコピー",
                   command=self.copy_clip).pack(side="left", padx=2)
        self.vvproj_btn = ttk.Button(ob, text="プロジェクト保存(.vvproj)",
                                     command=self.save_vvproj, state="disabled")
        self.vvproj_btn.pack(side="left", padx=2)
        _Tooltip(self.vvproj_btn, self._tip_engine_gate(
            "VOICEVOXエディタでそのまま開けるプロジェクト(.vvproj)を保存します。",
            busy_gated=False))
        _sep()

        # === 再生・生成 ===
        p = ttk.Frame(bottom)
        p.pack(fill="x", padx=GAPX, pady=(0, GAPY))
        ttk.Label(p, text="再生・生成", style="Cluster.TLabel").pack(side="left", padx=(0, GAPX))
        # 仕上げの主要ボタンは右端（終端位置＝マウスを流す先）に大きく。packは後詰めから
        # 切れるため、主要ボタンを最初に確保して狭い窓でも必ず残す。辞書は誤クリック防止に
        # 間隔をあけ、優先度が低いので最後（＝最初に畳まれる）
        mod = "⌘" if core.IS_MAC else "Ctrl+"
        self.synth_btn = ttk.Button(p, text="🔊 音声を生成", style="Primary.TButton",
                                    command=self.start_synth, state="disabled")
        self.synth_btn.pack(side="right")
        _Tooltip(self.synth_btn, self._tip_engine_gate(
            f"本文を音声ファイルに書き出します（{mod}G）。"))
        # 補足説明はラベルでなくツールチップに（最小幅860でも行全体が収まるように）。
        # 無効中は「なぜ押せないか」を _tip_engine_gate が状態に応じて表示する
        self.preview_btn = ttk.Button(p, text="▶ 試聴",
                                      command=self.preview_selected, state="disabled")
        self.preview_btn.pack(side="left")
        _Tooltip(self.preview_btn, self._tip_engine_gate(
            f"カーソルのある行を1行だけ試し聴きします（{mod}P）。"))
        self.playall_btn = ttk.Button(p, text="▶▶ 連続再生",
                                      command=self.play_all, state="disabled")
        self.playall_btn.pack(side="left", padx=4)
        _Tooltip(self.playall_btn, self._tip_engine_gate(
            "カーソル行から最後まで順に読み上げます。"))
        self.resume_btn = ttk.Button(p, text="⏵ 続きから",
                                     command=self.play_from_bookmark, state="disabled")
        self.resume_btn.pack(side="left", padx=4)

        def _resume_tip():
            if not self.speakers:
                return ("VOICEVOXエンジン接続後、連続再生すると\n"
                        "しおりが作られ、続きから再生できます。")
            if self._bookmark is None:
                return "連続再生するとしおり（最後に再生した行）が作られ、続きから再生できます。"
            return "前回の続き（しおりの行）から再生します。"
        _Tooltip(self.resume_btn, _resume_tip)
        self.stop_btn = ttk.Button(p, text="■ 停止",
                                   command=self.stop_playall, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        _Tooltip(self.stop_btn, "再生を停止します（Esc。再生中だけ押せます）。")
        self.dict_btn = ttk.Button(p, text="読み方辞書...",
                                   command=self.open_dict_dialog, state="disabled")
        self.dict_btn.pack(side="right", padx=(0, 14))
        _Tooltip(self.dict_btn, self._tip_engine_gate(
            "固有名詞などの読み方をVOICEVOXのユーザー辞書に登録します。",
            busy_gated=False))

    # ---------------- §3 抽出結果（編集可能・伸縮の主役） ----------------
    def _build_result_section(self):
        mid = ttk.LabelFrame(self._main, text="3. ✏️ 抽出結果（手動で修正できます）")
        mid.pack(fill="both", expand=True, padx=PAD_X, pady=PAD_Y // 2)

        # 一括置換バー
        rep = ttk.Frame(mid)
        rep.pack(fill="x", padx=GAPX, pady=(GAPY, 0))
        ttk.Label(rep, text="一括置換:").pack(side="left")
        self.find_var = tk.StringVar()
        self.repl_var = tk.StringVar()
        fe = ttk.Entry(rep, textvariable=self.find_var, width=18)
        fe.pack(side="left", padx=2)
        ttk.Label(rep, text="→").pack(side="left")
        re_ = ttk.Entry(rep, textvariable=self.repl_var, width=18)
        re_.pack(side="left", padx=2)
        ttk.Button(rep, text="すべて置換", command=self.replace_all_text).pack(side="left", padx=4)
        fe.bind("<Return>", lambda e: self.replace_all_text())
        re_.bind("<Return>", lambda e: self.replace_all_text())
        ttk.Separator(rep, orient="vertical").pack(side="left", fill="y", padx=GAPX)
        ttk.Label(rep, text="保存ルール:").pack(side="left")
        self.rule_cb = ttk.Combobox(rep, width=16, state="readonly", values=[])
        self.rule_cb.pack(side="left", padx=2)
        self.rule_cb.bind("<<ComboboxSelected>>", self._rule_selected)
        # 低頻度の「登録/削除」はメニューにまとめ、最頻用の「全ルール適用」だけボタンで残す
        self.rule_menu_btn = ttk.Menubutton(rep, text="ルール ▾", width=8)
        rule_menu = tk.Menu(self.rule_menu_btn, tearoff=0)
        rule_menu.add_command(label="今の置換内容をルールに登録", command=self.add_rule)
        rule_menu.add_command(label="選択中のルールを削除", command=self.del_rule)
        self.rule_menu_btn.config(menu=rule_menu)
        self.rule_menu_btn.pack(side="left", padx=1)
        _Tooltip(self.rule_menu_btn, "置換ルールの登録・削除（保存され次回起動時も使えます）。")
        ttk.Button(rep, text="全ルール適用", command=self.apply_all_rules).pack(side="left", padx=4)
        # 本文の全消去 / 復元は1ボタンで切替（押せない「復元」を常設しない）。
        # 属性名 restore_btn は結線・テストの互換のため維持。幅固定でラベル切替時に跳ねない
        self.restore_btn = ttk.Button(rep, text="本文を全消去", width=12,
                                      command=self.clear_text)
        self.restore_btn.pack(side="right", padx=2)
        _Tooltip(self.restore_btn, lambda: (
            "全消去した本文を元に戻します。" if self._cleared_text is not None
            else "本文をすべて消します（直後に「復元」で戻せます）。"))

        body = ttk.Frame(mid)
        body.pack(fill="both", expand=True)
        self.text_font = tkfont.Font(font=(TEXT_FONT or "TkTextFont"))
        self.text = tk.Text(body, wrap="word", undo=True, font=self.text_font)
        self.text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        tsb = ttk.Scrollbar(body, command=self.text.yview)
        tsb.pack(side="right", fill="y")
        self.text.config(yscrollcommand=tsb.set)
        # 本文が（全消去/復元“以外”の理由で）変わったら復元ポイントを無効化する一元フック
        self.text.bind("<<Modified>>", self._on_text_modified)

        # ショートカット: 検索(Ctrl/Cmd+F)・文字サイズ(Ctrl/Cmd + = / - / 0)
        self._font_size0 = int(self.text_font.cget("size"))  # リセット用の既定サイズ
        for mod in ("Control", "Command"):
            try:
                self.bind_all(f"<{mod}-f>", lambda e: self.open_search())
                self.bind_all(f"<{mod}-equal>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-plus>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-minus>", lambda e: self.change_font(-1))
                self.bind_all(f"<{mod}-0>", lambda e: self.change_font(0))
                # 主要操作: 抽出(Return)・音声生成(G)・ファイル追加(O)・txt保存(S)・試聴(P)
                self.bind_all(f"<{mod}-Return>", self._kb_extract)
                self.bind_all(f"<{mod}-g>", lambda e: self._kb_invoke(self.synth_btn))
                self.bind_all(f"<{mod}-o>", self._kb_add_files)
                self.bind_all(f"<{mod}-s>", self._kb_save_txt)
                self.bind_all(f"<{mod}-p>", lambda e: self._kb_invoke(self.preview_btn))
            except tk.TclError:
                pass  # Command修飾子はmacOS以外に無い
        self.bind_all("<Escape>", lambda e: self._kb_invoke(self.stop_btn))
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
        self._search_win = None
        self._search_hits = []
        self._search_idx = -1

    # ---------------- 主要操作のキーボードショートカット ----------------
    def _kb_invoke(self, btn):
        """ショートカットからボタンを押す（無効中は何もしない）。"""
        try:
            if btn.instate(["!disabled"]):
                btn.invoke()
        except tk.TclError:
            pass
        return "break"

    def _kb_extract(self, event=None):
        """Ctrl/Cmd+Return で抽出実行。一括置換・検索欄の<Return>バインドと
        二重発火しないよう、Entry系にフォーカスがあるときは何もしない。"""
        w = getattr(event, "widget", None)
        try:
            if w is not None and w.winfo_class() in ("Entry", "TEntry",
                                                     "TCombobox", "TSpinbox"):
                return None
        except (tk.TclError, AttributeError):
            pass
        return self._kb_invoke(self.extract_btn)

    def _kb_add_files(self, event=None):
        if not self.busy:
            self.add_files()
        return "break"

    def _kb_save_txt(self, event=None):
        self.save_txt()   # 空本文は save_txt 側がガードする
        return "break"

    # ---------------- キャラ立ち絵パネル（任意・ローカル資産） ----------------
    def _set_window_icon(self):
        """ウィンドウ/タスクバーのアイコンを設定（無ければ何もしない）。"""
        try:
            self._app_icon = tk.PhotoImage(file=APP_ICON_PATH)
            self.iconphoto(True, self._app_icon)
        except Exception:
            pass

    def _load_portraits(self):
        """assets/立ち絵/ の透過PNGを読み込む。無ければ空dict（パネルを出さない）。
        追加フレーム（_closed/_open/_blink）があれば まばたき・口パクに使う。
        フレームは公式立ち絵の口パク差分（同ポーズ・同寸法）を想定。"""
        out = {}
        for key in ("zundamon", "metan"):
            frames = {}
            for frame, suffix in (("base", ""), ("closed", "_closed"),
                                  ("open", "_open"), ("blink", "_blink")):
                img = self._load_scaled_image(
                    os.path.join(PORTRAIT_DIR, f"{key}{suffix}.png"),
                    max_w=230, max_h=640)
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
            return ImageTk.PhotoImage(im)
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
        ttk.Label(parent, text="VOICEVOX / 立ち絵:坂本アヒル",
                  style="Credit.TLabel").pack(side="bottom", pady=(4, 2))
        self._portrait_key = None
        self._mouth_after = None
        self._mouth_open = False
        self._blink_after = None
        self._update_portrait()
        self._blink_after = self.after(3800, self._blink_tick)

    def _portrait_key_for(self, label):
        s = label or ""
        if "四国めたん" in s or "めたん" in s:
            return "metan"
        if "ずんだもん" in s:
            return "zundamon"
        return None

    def _speaker_label_for_id(self, speaker_id):
        for s in self.speakers:
            if s[1] == speaker_id:
                return s[0]
        return ""

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

    def _update_portrait(self, event=None):
        """選択中の話者に応じて立ち絵を切り替える（対応が無ければ既定=ずんだもん）。"""
        if not getattr(self, "_portrait_label", None):
            return
        label = self.speaker_cb.get() if getattr(self, "speaker_cb", None) else ""
        key = self._portrait_key_for(label)
        if key not in self._portraits:
            key = "zundamon" if "zundamon" in self._portraits else \
                next(iter(self._portraits), None)
        self._portrait_key = key
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
            key = self._portrait_key_for(self._speaker_label_for_id(speaker_id))
            if key in self._portraits:
                self._portrait_key = key
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

    # 立ち絵の自動開閉しきい値。表示/非表示で値を分ける（ヒステリシス）ことで、
    # 境界付近でウィンドウをドラッグしたときの pack/pack_forget 連打・チラつきを防ぐ
    _SIDE_SHOW_W = 1120
    _SIDE_HIDE_W = 1060

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
        except tk.TclError:
            return
        if w >= self._SIDE_SHOW_W and not self._side_shown:
            self._side.pack(side="right", fill="y", padx=(0, PAD_X), pady=PAD_Y,
                            before=self._main)
            self._side_shown = True
        elif w < self._SIDE_HIDE_W and self._side_shown:
            self._side.pack_forget()
            self._side_shown = False

    # ---------------- 合成パラメータ・行別話者ヘルパー ----------------
    def _format_choices(self):
        choices = ["WAV"]
        if "m4a" in self.encoders:
            choices.append("M4A")
        if "mp3" in self.encoders:
            choices.append("MP3")
        return choices

    def _out_format(self):
        v = (self.fmt_cb.get() or "WAV").lower()
        return v if v in ("wav", "m4a", "mp3") else "wav"

    def _unit(self):
        i = self.unit_cb.current()
        keys = list(self._UNITS.keys())
        return keys[i] if 0 <= i < len(keys) else "each"

    def _voice_params(self):
        return dict(speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    intonation=self.into_var.get(), volume=self.vol_var.get())

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
        for p in filedialog.askopenfilenames(title="ファイルを選択", filetypes=ft):
            self._add_one(p)

    def add_folder(self):
        d = filedialog.askdirectory(title="フォルダを選択")
        if not d:
            return
        self._add_path(d)

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
            return 1
        return 0

    def _enable_drop(self, widget):
        """ウィジェットをファイルのドロップ先として登録する（tkinterdnd2が無ければ無視）。"""
        if not _HAS_DND:
            return
        try:
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _register_drop_tree(self, widget):
        """ウィジェット木の全要素をドロップ先に登録する（画面のどこに落としても効く）。"""
        if not _HAS_DND:
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

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            del self.files[i]

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, "end")

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
                # 同形文字の文脈補正はOCR由来テキストにだけ効く（テキスト層は対象外）
                fix_confusables=self.fixconf_var.get(),
            )
            clean_opts = dict(
                mode=self.mode_var.get(),
                remove_blank=self.blank_var.get(),
                keep_ascii_spaces=self.ascii_var.get(),
                join_wrapped=self.join_var.get(),
                smart_join=self.smartjoin_var.get(),
                paren_ruby=self.pruby_var.get(),
                normalize=self.norm_var.get(),
                denoise=self.denoise_var.get(),
            )
        except tk.TclError:
            messagebox.showwarning("入力エラー",
                                   "数値の欄（OCR解像度など）が空か不正です。\n"
                                   "数字を入れてからもう一度お試しください。")
            return
        self._set_busy(True)
        self.progress.config(mode="determinate", maximum=len(self.files), value=0)
        threading.Thread(target=self._extract_worker,
                         args=(params, clean_opts), daemon=True).start()

    def _extract_worker(self, params, clean_opts):
        def cb(done, total, msg):
            self.q.put(("progress", done, total, msg))
        try:
            raw, warnings = core.extract_files(progress_cb=cb, **params)
            cleaned = core.clean_text(raw, **clean_opts)
            self.q.put(("extract_done", cleaned, warnings))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    # ---------------- VOICEVOX ----------------
    def launch_voicevox(self):
        try:
            core.launch_voicevox()
            self.status_var.set("VOICEVOXを起動しました。少し待ってから接続確認してください。")
        except FileNotFoundError as e:
            messagebox.showwarning("VOICEVOX", str(e))
        except Exception as e:
            messagebox.showerror("VOICEVOX", f"起動に失敗: {e}")

    def check_engine(self):
        if self.busy:
            self.status_var.set("他の処理を実行中です。完了までお待ちください。")
            return
        url = self.url_var.get().strip().rstrip("/")
        self.base_url = url or VOICEVOX_DEFAULT
        self._set_busy(True)
        self.engine_var.set("エンジン: 接続確認中...")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        try:
            ver = core.vv_check(self.base_url)
            if not ver:
                self.q.put(("engine", None, None))
                return
            speakers = core.vv_speakers(self.base_url)
            self.q.put(("engine", ver, speakers))
        except Exception:
            self.q.put(("engine", None, None))

    def start_synth(self):
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("情報", "テキストがありません。")
            return
        if not self.speakers or self.speaker_cb.current() < 0:
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        # 行別話者を解決して (テキスト, style_id, 段落番号) のジョブ一覧を作る
        default_id = self.speakers[self.speaker_cb.current()][1]
        jobs = []
        para = 0
        for ln in text.split("\n"):
            if not ln.strip():
                para += 1
                continue
            spoken, sp = self._resolve_line(ln)
            if not spoken.strip():
                continue  # タグのみの行
            jobs.append((spoken, sp[1] if sp else default_id, para))
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
            srt = self.srt_var.get()
        except tk.TclError:
            messagebox.showwarning("入力エラー",
                                   "話速・音高・抑揚・音量・無音・行数のいずれかが空か不正です。\n"
                                   "数字を入れてからもう一度お試しください。")
            return
        # 出力単位ごとにジョブをグループ化（グループ = 1出力ファイル）
        if unit == "combine":
            groups = [list(range(len(jobs)))]
        elif unit == "nlines":
            n = nlines
            groups = [list(range(i, min(i + n, len(jobs))))
                      for i in range(0, len(jobs), n)]
        elif unit == "para":
            groups = []
            for i, j in enumerate(jobs):
                if groups and jobs[groups[-1][-1]][2] == j[2]:
                    groups[-1].append(i)
                else:
                    groups.append([i])
        else:  # each
            groups = [[i] for i in range(len(jobs))]
        if unit == "combine":
            out = filedialog.asksaveasfilename(
                title=f"結合{fmt.upper()}の保存先", defaultextension=f".{fmt}",
                filetypes=[(f"{fmt.upper()}ファイル", f"*.{fmt}")],
                initialfile=f"voicevox_output.{fmt}")
            if not out:
                return
            target = out
        else:
            d = filedialog.askdirectory(title=f"{fmt.upper()}の出力フォルダ")
            if not d:
                return
            target = d
        self._set_busy(True)
        self.progress.config(mode="determinate", maximum=len(jobs), value=0)
        threading.Thread(target=self._synth_worker,
                         args=(jobs, groups, voice, target, unit,
                               gap, fmt, srt),
                         daemon=True).start()

    def _synth_worker(self, jobs, groups, voice, target, unit, gap, fmt, srt):
        try:
            from concurrent.futures import ThreadPoolExecutor
            done_count = [0]
            lock = threading.Lock()
            t0 = time.monotonic()

            def synth(job):
                text, spk, _para = job
                wb = core.vv_synthesize_one(self.base_url, text, spk, **voice)
                with lock:
                    done_count[0] += 1
                    n = done_count[0]
                eta = (time.monotonic() - t0) / n * (len(jobs) - n)
                self.q.put(("progress", n, len(jobs),
                            f"音声生成中 {n}/{len(jobs)}"
                            + (f"（残り{core.fmt_duration(eta)}）" if n < len(jobs) else "")))
                return wb

            # エンジンへ3並列で投げる（順序はexecutor.mapが保持する）
            with ThreadPoolExecutor(max_workers=3) as ex:
                wavs = list(ex.map(synth, jobs))

            srt_count = 0
            for gi, idxs in enumerate(groups):
                if unit == "combine":
                    out_path = target
                else:
                    out_path = os.path.join(target, f"{gi+1:03d}.{fmt}")
                group_wavs = [wavs[i] for i in idxs]
                merged = (group_wavs[0] if len(group_wavs) == 1
                          else core.concat_wavs(group_wavs, gap_sec=gap))
                core.encode_audio(merged, out_path, fmt, self.encoders)
                if srt:
                    lines = [jobs[i][0] for i in idxs]
                    durations = [core.wav_duration(wavs[i]) for i in idxs]
                    srt_path = os.path.splitext(out_path)[0] + ".srt"
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(core.make_srt(lines, durations, gap_sec=gap))
                    srt_count += 1

            note = f"（字幕{srt_count}件も保存）" if srt_count else ""
            if unit == "combine":
                self.q.put(("synth_done",
                            f"結合{fmt.upper()}を保存しました{note}:\n{target}"))
            else:
                self.q.put(("synth_done",
                            f"{len(groups)}個の{fmt.upper()}を保存しました{note}:\n{target}"))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

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
        self.status_var.set("クリップボードにコピーしました。")

    def save_vvproj(self):
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("情報", "保存するテキストがありません。")
            return
        if not self.speakers or self.speaker_cb.current() < 0:
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        out = filedialog.asksaveasfilename(
            title="VOICEVOXプロジェクトを保存", defaultextension=".vvproj",
            filetypes=[("VOICEVOXプロジェクト", "*.vvproj")],
            initialfile="voicevox_project.vvproj")
        if not out:
            return
        default = self.speakers[self.speaker_cb.current()]
        entries = []
        for ln in text.split("\n"):
            if not ln.strip():
                continue
            spoken, sp = self._resolve_line(ln.strip())
            if spoken.strip():
                entries.append((spoken, sp[1] if sp else None, sp[2] if sp else None))
        with open(out, "w", encoding="utf-8") as f:
            f.write(core.make_vvproj(entries, default[1], default[2]))
        messagebox.showinfo("保存完了",
                            f"保存しました:\n{out}\n\n"
                            "VOICEVOXの「ファイル → プロジェクト読み込み」で開くと、\n"
                            "1行 = 1ブロックとして読み込まれ、行ごとに話者や\n"
                            "イントネーションを調整できます。")

    # ---------------- クリップボード画像OCR ----------------
    def clipboard_ocr(self):
        if self.busy or self._previewing:
            self.status_var.set("再生／処理の実行中です。停止・完了してからお試しください。")
            return
        try:
            data = ImageGrab.grabclipboard()
        except Exception as e:
            messagebox.showerror("クリップボード", f"取得に失敗しました: {e}")
            return
        if data is None:
            messagebox.showinfo("クリップボード",
                                "クリップボードに画像がありません。\n"
                                "スクリーンショット等で画像をコピーしてから押してください。")
            return
        if isinstance(data, list):  # ファイルがコピーされていた場合は取り込み
            added = 0
            for p in data:
                if isinstance(p, str):
                    before = len(self.files)
                    self._add_path(p)
                    added += len(self.files) - before
            self.status_var.set(f"{added}件追加しました（クリップボードのファイル）")
            return
        clean_opts = dict(mode=self.mode_var.get(), remove_blank=self.blank_var.get(),
                          keep_ascii_spaces=self.ascii_var.get(), join_wrapped=self.join_var.get(),
                          smart_join=self.smartjoin_var.get(),
                          paren_ruby=self.pruby_var.get(), normalize=self.norm_var.get(),
                          denoise=self.denoise_var.get())
        self._set_busy(True)
        self.status_var.set("クリップボード画像をOCR中...")
        threading.Thread(target=self._clipboard_worker,
                         args=(data, self.pre_var.get(), clean_opts,
                               self.fixconf_var.get()), daemon=True).start()

    def _clipboard_worker(self, img, preprocess, clean_opts, fix_confusables=False):
        try:
            # OCRが済めばPNG（＝クリップボード画像のコピー）は不要。%TEMP%に残さない
            with tempfile.TemporaryDirectory(prefix="t2v_clip_") as tmpdir:
                png = os.path.join(tmpdir, "clip.png")
                core.preprocess_image(img, enable=preprocess).save(png)
                res = core.run_ocr([png], strip_labels=clean_opts.get("denoise", True))
                raw = res.get(png, "")
            if fix_confusables and raw:
                raw = core.fix_ocr_confusables(raw)
            cleaned = core.clean_text(raw, **clean_opts)
            self.q.put(("clip_done", cleaned))
        except Exception:
            self.q.put(("error", traceback.format_exc()))

    # ---------------- ユーザー辞書（読み方の登録） ----------------
    def open_dict_dialog(self):
        if self._dict_win is not None and self._dict_win.winfo_exists():
            self._dict_win.lift()
            return
        win = tk.Toplevel(self)
        win.title("読み方辞書（VOICEVOXユーザー辞書）")
        win.geometry("560x420")
        self._dict_win = win

        ttk.Label(win, text="固有名詞などの読み間違いを登録できます。"
                            "登録した読みはVOICEVOX全体で使われます。").pack(anchor="w", padx=8, pady=(8, 2))

        cols = ("surface", "pron", "accent")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=10)
        tree.heading("surface", text="単語")
        tree.heading("pron", text="読み（カタカナ）")
        tree.heading("accent", text="アクセント核")
        tree.column("surface", width=180)
        tree.column("pron", width=220)
        tree.column("accent", width=90, anchor="center")
        tree.pack(fill="both", expand=True, padx=8, pady=4)
        self._dict_tree = tree

        form = ttk.Frame(win); form.pack(fill="x", padx=8, pady=4)
        ttk.Label(form, text="単語:").pack(side="left")
        self._dict_surface = tk.StringVar()
        ttk.Entry(form, textvariable=self._dict_surface, width=14).pack(side="left", padx=2)
        ttk.Label(form, text="読み:").pack(side="left", padx=(8, 0))
        self._dict_pron = tk.StringVar()
        ttk.Entry(form, textvariable=self._dict_pron, width=18).pack(side="left", padx=2)
        ttk.Label(form, text="ｱｸｾﾝﾄ核:").pack(side="left", padx=(8, 0))
        self._dict_accent = tk.IntVar(value=0)
        ttk.Spinbox(form, from_=0, to=30, width=4,
                    textvariable=self._dict_accent).pack(side="left", padx=2)

        btns = ttk.Frame(win); btns.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Button(btns, text="追加", command=self._dict_add).pack(side="left", padx=2)
        ttk.Button(btns, text="選択を削除", command=self._dict_delete).pack(side="left", padx=2)
        ttk.Button(btns, text="再読込", command=self._dict_refresh).pack(side="left", padx=2)
        ttk.Button(btns, text="書き出し...", command=self._dict_export).pack(side="left", padx=(10, 2))
        ttk.Button(btns, text="読み込み...", command=self._dict_import).pack(side="left", padx=2)
        ttk.Label(btns, text="※読みはひらがなでもOK（自動でカタカナに変換）。"
                            "ｱｸｾﾝﾄ核0=平板").pack(side="left", padx=8)
        self._dict_refresh()

    def _dict_refresh(self):
        threading.Thread(target=self._dict_list_worker, daemon=True).start()

    def _dict_list_worker(self):
        try:
            rows = core.vv_dict_list(self.base_url)
            self.q.put(("dict_list", rows))
        except Exception as e:
            self.q.put(("dict_status", f"辞書の取得に失敗: {e}"))

    def _dict_add(self):
        surface = self._dict_surface.get().strip()
        pron = core.hira_to_kata(self._dict_pron.get().strip())
        if not surface or not pron:
            self.status_var.set("単語と読みを入力してください。")
            return
        accent = self._dict_accent.get()

        def worker():
            try:
                core.vv_dict_add(self.base_url, surface, pron, accent)
                self.q.put(("dict_status", f"登録しました：{surface} → {pron}"))
                rows = core.vv_dict_list(self.base_url)
                self.q.put(("dict_list", rows))
            except Exception as e:
                self.q.put(("dict_status", f"登録に失敗: {e}（読みは全角カタカナのみ）"))
        threading.Thread(target=worker, daemon=True).start()

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
                self.q.put(("dict_status", f"{len(uuids)}件削除しました。"))
                rows = core.vv_dict_list(self.base_url)
                self.q.put(("dict_list", rows))
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
                rows = core.vv_dict_list(self.base_url)
                data = [{"surface": s, "pronunciation": p, "accent_type": a}
                        for _u, s, p, a in rows]
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
                existing = {s for _u, s, _p, _a in core.vv_dict_list(self.base_url)}
                added = skipped = 0
                for w in data:
                    surface = (w.get("surface") or "").strip()
                    pron = (w.get("pronunciation") or "").strip()
                    if not surface or not pron:
                        continue
                    if surface in existing:
                        skipped += 1
                        continue
                    core.vv_dict_add(self.base_url, surface,
                                     core.hira_to_kata(pron),
                                     int(w.get("accent_type", 0)))
                    added += 1
                self.q.put(("dict_status",
                            f"辞書読み込み: {added}語追加"
                            + (f" / {skipped}語は登録済みのためスキップ" if skipped else "")))
                self.q.put(("dict_list", core.vv_dict_list(self.base_url)))
            except Exception as e:
                self.q.put(("dict_status", f"読み込みに失敗: {e}"))
        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 試聴 ----------------
    def preview_selected(self):
        if self._previewing or self.busy:
            return
        if not self.speakers or self.speaker_cb.current() < 0:
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        if not core.can_play():
            messagebox.showinfo("情報", "この環境では試聴(再生)を利用できません。")
            return
        line = self.text.get("insert linestart", "insert lineend").strip()
        if not line:
            for ln in self.text.get("1.0", "end").split("\n"):
                if ln.strip():
                    line = ln.strip()
                    break
        if not line:
            self.status_var.set("試聴するテキストがありません。")
            return
        spoken, sp = self._resolve_line(line)
        if not spoken.strip():
            self.status_var.set("試聴するテキストがありません。")
            return
        speaker_id = sp[1] if sp else self.speakers[self.speaker_cb.current()][1]
        self._previewing = True
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.status_var.set("試聴を生成中...")
        threading.Thread(target=self._preview_worker,
                         args=(spoken, speaker_id, self._voice_params()),
                         daemon=True).start()

    def _preview_worker(self, line, speaker_id, voice):
        try:
            wb = core.vv_synthesize_one(self.base_url, line, speaker_id, **voice)
            self._preview_buf = wb
            self.q.put(("preview_playing", line, speaker_id))
            # ワーカースレッドなので同期再生でブロックして問題ない
            core.play_wav_blocking(wb)
            self.q.put(("preview_done", True, line))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc()))

    # ---------------- 連続再生（カーソル行から最後まで） ----------------
    def play_all(self):
        if self._previewing or self.busy:
            return
        if not self.speakers or self.speaker_cb.current() < 0:
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        if not core.can_play():
            messagebox.showinfo("情報", "この環境では再生を利用できません。")
            return
        # (行番号, 読み上げテキスト, style_id) を集め、カーソル行以降を再生対象にする
        default_id = self.speakers[self.speaker_cb.current()][1]
        all_lines = self.text.get("1.0", "end-1c").split("\n")
        numbered = []
        for i, ln in enumerate(all_lines, start=1):
            if not ln.strip():
                continue
            spoken, sp = self._resolve_line(ln.strip())
            if spoken.strip():
                numbered.append((i, spoken, sp[1] if sp else default_id))
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
        self._playall_stop = threading.Event()
        self._previewing = True
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.resume_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self._playall_worker,
                         args=(targets, self._voice_params()), daemon=True).start()

    def play_from_bookmark(self):
        """しおり（最後に再生した行）から連続再生を再開する。"""
        if self._bookmark is None:
            self.status_var.set("しおりがありません（連続再生すると自動で記憶されます）。")
            return
        last = int(self.text.index("end-1c").split(".")[0])
        line = min(self._bookmark, last)
        self.text.mark_set("insert", f"{line}.0")
        self.text.see(f"{line}.0")
        self.play_all()

    def stop_playall(self):
        if self._playall_stop is not None:
            self._playall_stop.set()
        self.stop_btn.config(state="disabled")
        self.status_var.set("停止しています...")

    def _playall_worker(self, targets, voice):
        stop = self._playall_stop
        played = 0
        try:
            from concurrent.futures import ThreadPoolExecutor

            def synth(t):
                return core.vv_synthesize_one(self.base_url, t[1], t[2], **voice)

            # 再生中に次の行を裏で合成しておく（行間の待ちをほぼゼロに）
            with ThreadPoolExecutor(max_workers=1) as ex:
                nxt = ex.submit(synth, targets[0])
                for k, (lineno, ln, sid) in enumerate(targets):
                    if stop.is_set():
                        break
                    self.q.put(("playall_line", lineno, ln, sid,
                                played, len(targets)))
                    wb = nxt.result()
                    if k + 1 < len(targets):
                        nxt = ex.submit(synth, targets[k + 1])
                    if stop.is_set():
                        break
                    self._preview_buf = wb
                    core.play_wav_blocking(wb, stop_event=stop)
                    played += 1
            self.q.put(("playall_done", True, stop.is_set(), played))
        except Exception:
            self.q.put(("playall_done", False, traceback.format_exc(), played))

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
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content.replace(find, repl))
        self.status_var.set(f"{count}件置換しました：「{find}」→「{repl}」")

    # ---------------- 本文の全消去 / 復元 ----------------
    def _on_text_modified(self, event=None):
        """本文が全消去/復元“以外”の理由で変更されたら、復元ポイントを無効化する。
        抽出・クリップボード・置換・ルール適用・手動編集・Ctrl/Cmd+Zのどれでも一元的に捕捉し、
        古い退避内容で新しい本文を上書きしてしまう「復元」事故を防ぐ。"""
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
            self.restore_btn.config(text="本文を全消去", command=self.clear_text)
        self._update_step_highlight()

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
        self.restore_btn.config(text="復元", command=self.restore_text)
        self._update_step_highlight()
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
        self._cleared_text = None
        self.restore_btn.config(text="本文を全消去", command=self.clear_text)
        self._update_step_highlight()
        self.status_var.set("本文を復元しました。")

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
            self.status_var.set("登録する検索文字を入力してください。")
            return
        rule = [find, self.repl_var.get()]
        if rule in self.replace_rules:
            self.status_var.set("同じルールが登録済みです。")
            return
        self.replace_rules.append(rule)
        self._refresh_rules()
        self.rule_cb.current(len(self.replace_rules) - 1)
        self.status_var.set(f"ルールを登録しました：「{rule[0]}」→「{rule[1]}」（次回起動時も使えます）")

    def del_rule(self):
        i = self.rule_cb.current()
        if not (0 <= i < len(self.replace_rules)):
            self.status_var.set("削除するルールを選択してください。")
            return
        f, r = self.replace_rules.pop(i)
        self._refresh_rules()
        self.rule_cb.set("")
        self.status_var.set(f"ルールを削除しました：「{f}」→「{r}」")

    def apply_all_rules(self):
        if not self.replace_rules:
            self.status_var.set(
                "保存済みのルールがありません（「ルール ▾」→「登録」で追加できます）。")
            return
        content = self.text.get("1.0", "end-1c")
        total = 0
        for f, r in self.replace_rules:
            c = content.count(f)
            if c:
                content = content.replace(f, r)
                total += c
        if total == 0:
            self.status_var.set("置換対象が見つかりませんでした。")
            return
        self.text.edit_separator()  # Undo境界（Ctrl+Zで戻せる）
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.status_var.set(f"全{len(self.replace_rules)}ルールで計{total}件置換しました。")

    # ---------------- テーマ（選択式・4種） ----------------
    # すべて clam ベースの統一配色。ライト/ダーク＝落ち葉、くっきり＝高コントラスト、
    # ずんだ＝ずんだもんの若草色。文字と地の色差（コントラスト）を優先して選定。
    LIGHT = dict(
        bg="#f4efe6", card="#faf6ee", field="#ffffff", textbg="#ffffff",
        fg="#3d342c", subtle="#827564", head_fg="#5a4636",
        accent="#b95f22", accent_hi="#d0712f", accent_fg="#ffffff",
        btn="#ece1cf", btn_hi="#e2d3b9", border="#ddd0bd",
        sel="#f0dcbf", disabled="#b7ab9a",
    )
    DARK = dict(
        bg="#272320", card="#302b26", field="#3a342d", textbg="#1f1c19",
        fg="#ece3d5", subtle="#a89b89", head_fg="#f0e6d6",
        accent="#e08a45", accent_hi="#ef9a55", accent_fg="#241a12",
        btn="#3c362f", btn_hi="#4a433a", border="#4a4239",
        sel="#5c4632", disabled="#6b6255",
    )
    HC = dict(  # くっきり：白地＋濃紺文字＋強い枠線。小さな文字も読みやすく
        bg="#ffffff", card="#ffffff", field="#ffffff", textbg="#ffffff",
        fg="#111111", subtle="#3d3d3d", head_fg="#000000",
        accent="#0a58ca", accent_hi="#2f74d8", accent_fg="#ffffff",
        btn="#ededed", btn_hi="#dcdcdc", border="#5a5a5a",
        sel="#bcd7ff", disabled="#8a8a8a",
    )
    ZUNDA = dict(  # ずんだ：ずんだもんの若草色。やわらかい緑の明色テーマ
        bg="#edf6e8", card="#f7fbf3", field="#ffffff", textbg="#ffffff",
        fg="#243329", subtle="#5f7264", head_fg="#2c5c3d",
        accent="#357a4c", accent_hi="#43955e", accent_fg="#ffffff",
        btn="#d9ead0", btn_hi="#c9e0bd", border="#b9cfae",
        sel="#c9ecca", disabled="#9db3a2",
    )
    # (設定キー, 表示名, パレット)。表示名がテーマ選択プルダウンの並びになる
    THEMES = [("light", "🍂 ライト", LIGHT), ("dark", "🌙 ダーク", DARK),
              ("hc", "☀️ くっきり", HC), ("zunda", "🌿 ずんだ", ZUNDA)]

    def toggle_theme(self):
        """ライト⇄ダークの簡易切替（旧APIとの互換用。選択はテーマプルダウンで）。"""
        self.theme_var.set("light" if self.theme_var.get() == "dark" else "dark")
        self.apply_theme()

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
        # 検索/再生のハイライトは明色固定なので、文字色を黒にして全テーマで読めるように
        for tag, bg in (("playing", "#cde8ff"), ("hit", "#fff3a3"), ("curhit", "#ffb347")):
            self.text.tag_config(tag, background=bg, foreground="#000000")

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
        style.configure("Credit.TLabel", background=p["bg"],
                        foreground=p["subtle"], font=self._credit_font)
        # 通常ボタン（フラット・少し余白）
        style.configure("TButton", background=p["btn"], foreground=p["fg"],
                        bordercolor=p["border"], relief="flat", padding=(7, 3),
                        focuscolor=p["btn"])
        style.map("TButton",
                  background=[("pressed", p["btn_hi"]), ("active", p["btn_hi"]),
                              ("disabled", p["bg"])],
                  foreground=[("disabled", p["disabled"])])
        # 主要ボタン（アクセント＝落ち葉オレンジ・太字）
        style.configure("Primary.TButton", background=p["accent"],
                        foreground=p["accent_fg"], bordercolor=p["accent"],
                        font=self._primary_font, relief="flat", padding=(14, 8))
        style.map("Primary.TButton",
                  background=[("pressed", p["accent_hi"]), ("active", p["accent_hi"]),
                              ("disabled", p["btn"])],
                  foreground=[("disabled", p["disabled"])])
        # 準主要ボタン＝「次に押すボタン」ハイライトの降格先。フォント・paddingを
        # Primaryと同一にし、Primary⇄Secondary切替でボタン寸法が変わらないようにする
        style.configure("Secondary.TButton", background=p["btn"],
                        foreground=p["fg"], bordercolor=p["border"],
                        font=self._primary_font, relief="flat", padding=(14, 8))
        style.map("Secondary.TButton",
                  background=[("pressed", p["btn_hi"]), ("active", p["btn_hi"]),
                              ("disabled", p["bg"])],
                  foreground=[("disabled", p["disabled"])])
        # ルール操作のMenubutton（TButtonと同じ見た目に揃える）
        style.configure("TMenubutton", background=p["btn"], foreground=p["fg"],
                        bordercolor=p["border"], relief="flat", padding=(7, 3),
                        arrowcolor=p["fg"])
        style.map("TMenubutton",
                  background=[("pressed", p["btn_hi"]), ("active", p["btn_hi"]),
                              ("disabled", p["bg"])],
                  foreground=[("disabled", p["disabled"])])
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
        # チェック/ラジオ（選択インジケータをアクセント色に）
        for w in ("TCheckbutton", "TRadiobutton"):
            style.configure(w, background=p["bg"], foreground=p["fg"],
                            indicatorcolor=p["field"], focuscolor=p["bg"])
            style.map(w, background=[("active", p["bg"])],
                      indicatorcolor=[("selected", p["accent"]),
                                      ("pressed", p["accent_hi"])],
                      foreground=[("disabled", p["disabled"])])
        # 進捗・区切り・スクロールバー
        style.configure("TProgressbar", background=p["accent"],
                        troughcolor=p["field"], bordercolor=p["border"])
        style.configure("TSeparator", background=p["border"])
        style.configure("TScrollbar", background=p["btn"], troughcolor=p["bg"],
                        bordercolor=p["border"], arrowcolor=p["fg"])
        style.map("TScrollbar", background=[("active", p["btn_hi"])])
        # 非ttk（tk）ウィジェットは直接配色
        self.configure(bg=p["bg"])
        self.text.config(bg=p["textbg"], fg=p["fg"], insertbackground=p["fg"],
                         selectbackground=p["sel"], selectforeground=p["fg"],
                         highlightthickness=0, borderwidth=0)
        self.listbox.config(bg=p["field"], fg=p["fg"],
                            selectbackground=p["sel"], selectforeground=p["fg"],
                            highlightthickness=0, borderwidth=0)
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
        """起動直後に保存済みURLへ接続を試みる。失敗しても静かに未接続表示のまま。"""
        if not self.busy:
            self.check_engine()

    # ---------------- テキスト検索（Ctrl/Cmd+F） ----------------
    def open_search(self):
        if self._search_win is not None and self._search_win.winfo_exists():
            self._search_win.lift()
            self._search_entry.focus_set()
            return
        win = tk.Toplevel(self)
        win.title("検索")
        win.geometry("360x40")
        win.transient(self)
        self._search_win = win
        row = ttk.Frame(win); row.pack(fill="x", padx=6, pady=6)
        self._search_var = tk.StringVar()
        self._search_entry = ttk.Entry(row, textvariable=self._search_var, width=22)
        self._search_entry.pack(side="left", padx=2)
        self._search_entry.focus_set()
        ttk.Button(row, text="↓次", width=4,
                   command=lambda: self._search_jump(+1)).pack(side="left", padx=1)
        ttk.Button(row, text="↑前", width=4,
                   command=lambda: self._search_jump(-1)).pack(side="left", padx=1)
        self._search_count = tk.StringVar(value="")
        ttk.Label(row, textvariable=self._search_count).pack(side="left", padx=6)
        self._search_entry.bind("<Return>", lambda e: self._search_jump(+1))
        self._search_var.trace_add("write", lambda *a: self._search_refresh())
        win.protocol("WM_DELETE_WINDOW", self._close_search)
        # "break" を返し、Escの全体バインド（再生停止）と二重発火しないようにする
        win.bind("<Escape>", lambda e: (self._close_search(), "break")[1])

    def _close_search(self):
        self.text.tag_remove("hit", "1.0", "end")
        self.text.tag_remove("curhit", "1.0", "end")
        if self._search_win is not None and self._search_win.winfo_exists():
            self._search_win.destroy()
        self._search_win = None

    def _search_refresh(self):
        word = self._search_var.get()
        self.text.tag_remove("hit", "1.0", "end")
        self.text.tag_remove("curhit", "1.0", "end")
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
        self.text.tag_config("hit", background="#fff3a3")
        self.text.tag_config("curhit", background="#ffb347")
        self._search_count.set(f"{len(self._search_hits)}件")

    def _search_jump(self, direction):
        if not self._search_hits:
            self._search_refresh()
            if not self._search_hits:
                return
        self._search_idx = (self._search_idx + direction) % len(self._search_hits)
        pos = self._search_hits[self._search_idx]
        word = self._search_var.get()
        self.text.tag_remove("curhit", "1.0", "end")
        self.text.tag_add("curhit", pos, f"{pos}+{len(word)}c")
        self.text.mark_set("insert", pos)
        self.text.see(pos)
        self._search_count.set(f"{self._search_idx + 1}/{len(self._search_hits)}件")

    # ---------------- 文字サイズ（Ctrl/Cmd + = / - / 0） ----------------
    def change_font(self, delta):
        size = int(self.text_font.cget("size"))
        if delta == 0:
            size = self._font_size0
        else:
            size = max(8, min(40, size + delta))
        self.text_font.config(size=size)
        self.status_var.set(f"文字サイズ: {size}")

    # ---------------- テキストの自動保存・復元 ----------------
    def _save_text_cache(self):
        text = self.text.get("1.0", "end-1c")
        try:
            if text.strip():
                with open(TEXT_CACHE_PATH, "w", encoding="utf-8") as f:
                    f.write(text)
            elif os.path.exists(TEXT_CACHE_PATH):
                os.remove(TEXT_CACHE_PATH)
        except Exception:
            pass

    def _restore_text_cache(self):
        try:
            with open(TEXT_CACHE_PATH, encoding="utf-8") as f:
                cached = f.read()
        except Exception:
            return
        if cached.strip() and not self.text.get("1.0", "end").strip():
            self.text.insert("1.0", cached)
            self.status_var.set("前回のテキストを復元しました（しおりの「⏵ 続きから」も使えます）。")

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
        labels = list(self.speaker_cb["values"])
        if p.get("speaker") in labels:
            self.speaker_cb.current(labels.index(p["speaker"]))
            self._update_portrait()
        self.status_var.set(f"プリセット「{p['name']}」を適用しました。")

    def save_preset(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("プリセット保存", "プリセット名:", parent=self)
        if not name:
            return
        name = name.strip()
        preset = {"name": name, "speaker": self.speaker_cb.get(),
                  "speed": self.speed_var.get(), "pitch": self.pitch_var.get(),
                  "intonation": self.into_var.get(), "volume": self.vol_var.get()}
        for i, p in enumerate(self.presets):
            if p["name"] == name:
                self.presets[i] = preset  # 同名は上書き
                break
        else:
            self.presets.append(preset)
        self._refresh_presets()
        self.preset_cb.set(name)
        self.status_var.set(f"プリセット「{name}」を保存しました（次回起動時も使えます）。")

    def del_preset(self):
        i = self.preset_cb.current()
        if not (0 <= i < len(self.presets)):
            self.status_var.set("削除するプリセットを選択してください。")
            return
        p = self.presets.pop(i)
        self._refresh_presets()
        self.preset_cb.set("")
        self.status_var.set(f"プリセット「{p['name']}」を削除しました。")

    # ---------------- 設定の保存/復元 ----------------
    def _settings_dict(self):
        return {
            "mode": self.mode_var.get(), "pdf": self.pdf_var.get(),
            "dpi": self.dpi_var.get(), "preprocess": self.pre_var.get(),
            "blank": self.blank_var.get(), "ascii": self.ascii_var.get(),
            "join": self.join_var.get(),
            "smart_join": self.smartjoin_var.get(),
            "paren_ruby": self.pruby_var.get(), "normalize": self.norm_var.get(),
            "denoise": self.denoise_var.get(),
            "fix_confusables": self.fixconf_var.get(),
            "dark": self.dark_var.get(),
            "theme": self.theme_var.get(),
            "unit": self._unit(), "nlines": self.nlines_var.get(),
            "srt": self.srt_var.get(),
            "font_size": int(self.text_font.cget("size")),
            "speed": self.speed_var.get(), "speaker": self.speaker_cb.get(),
            "pitch": self.pitch_var.get(), "intonation": self.into_var.get(),
            "volume": self.vol_var.get(),
            "fmt": self._out_format(),
            "gap": self.gap_var.get(),
            "replace_rules": self.replace_rules,
            "presets": self.presets,
            "dlg_enabled": self.dlg_var.get(),
            "dlg_speaker": self.dlg_speaker_cb.get(),
            "bookmark": self._bookmark,
            "base_url": self.url_var.get().strip() or self.base_url,
            "geometry": self.geometry(),
            "adv_open": bool(self._adv_open),
            "voice_detail_open": bool(self._vdetail_open),
        }

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                s = json.load(f)
        except Exception:
            return
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
            self.denoise_var.set(bool(s.get("denoise", True)))
            self.fixconf_var.set(bool(s.get("fix_confusables", True)))
            self.dark_var.set(bool(s.get("dark", False)))
            # テーマ：新キー "theme" を優先。無い旧設定は "dark": true → ダーク で引き継ぐ
            theme = s.get("theme")
            if theme is None and bool(s.get("dark", False)):
                theme = "dark"
            if theme in {k for k, _l, _p in self.THEMES}:
                self.theme_var.set(theme)
            self._set_advanced(bool(s.get("adv_open", False)))
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
                self.text_font.config(size=int(fs))
            self.speed_var.set(float(s.get("speed", 1.0)))
            self.pitch_var.set(float(s.get("pitch", 0.0)))
            self.into_var.set(float(s.get("intonation", 1.0)))
            self.vol_var.set(float(s.get("volume", 1.0)))
            fmt = str(s.get("fmt", "wav")).upper()
            if fmt in self._format_choices():
                self.fmt_cb.set(fmt)
            self.gap_var.set(float(s.get("gap", 0.4)))
            rules = s.get("replace_rules", [])
            if isinstance(rules, list):
                self.replace_rules = [[str(x[0]), str(x[1])] for x in rules
                                      if isinstance(x, (list, tuple)) and len(x) == 2]
                self._refresh_rules()
            presets = s.get("presets", [])
            if isinstance(presets, list):
                self.presets = [p for p in presets
                                if isinstance(p, dict) and p.get("name")]
                self._refresh_presets()
            self.dlg_var.set(bool(s.get("dlg_enabled", False)))
            # プリセット/セリフ行：保存した開閉状態を復元。セリフ別話者ONなら
            # 設定が隠れて見えないままにならないよう自動で開く
            self._set_voice_detail(bool(s.get("voice_detail_open", False))
                                   or self.dlg_var.get())
            self._saved_dlg_speaker = s.get("dlg_speaker") or None
            bm = s.get("bookmark")
            self._bookmark = int(bm) if isinstance(bm, (int, float)) else None
            self._saved_speaker = s.get("speaker") or None
            if s.get("base_url"):
                self.base_url = s["base_url"]
                self.url_var.set(self.base_url)
            if s.get("geometry"):
                self._apply_saved_geometry(s["geometry"])
        except Exception:
            pass

    def _apply_saved_geometry(self, geo):
        """保存したウィンドウ位置を復元する。ただしサブモニタ切断などで画面外に
        なる座標はプライマリ画面内へ寄せ、必ず一部が見えるようにする
        （そのまま適用すると『起動しても窓が出ない』と誤解される）。"""
        m = re.match(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geo)
        if not m:
            try:
                self.geometry(geo)
            except tk.TclError:
                pass
            return
        w, h, x, y = int(m[1]), int(m[2]), int(m[3]), int(m[4])
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        # 大部分が画面外なら収まる位置へ補正（少しでも見えているならそのまま尊重）
        if x < 0 or y < 0 or x > sw - 100 or y > sh - 60:
            x = max(0, min(x, max(0, sw - w)))
            y = max(0, min(y, max(0, sh - h)))
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _save_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._settings_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        if self._playall_stop is not None:
            self._playall_stop.set()  # 連続再生中でも即終了できるように
        for attr in ("_blink_after", "_mouth_after"):  # 立ち絵アニメの後始末
            h = getattr(self, attr, None)
            if h:
                try:
                    self.after_cancel(h)
                except tk.TclError:
                    pass
        self._save_settings()
        self._save_text_cache()
        self.destroy()

    # ---------------- キュー処理（UIスレッド） ----------------
    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, text = msg
                    self.progress.config(maximum=max(total, 1), value=done)
                    self.status_var.set(text)
                elif kind == "extract_done":
                    _, cleaned, warnings = msg
                    self.text.delete("1.0", "end")
                    self.text.insert("1.0", cleaned)
                    # 本文が変わったので復元ポイントは _on_text_modified が自動で無効化する
                    self.progress.config(value=self.progress["maximum"])
                    n = len([l for l in cleaned.split("\n") if l.strip()])
                    self.status_var.set(f"抽出完了：{n}行 / {len(cleaned)}文字"
                                        "（3.で直したら「🔊 音声を生成」へ🍂）")
                    self._set_busy(False)
                    if warnings:
                        messagebox.showwarning("注意", "\n".join(warnings))
                elif kind == "engine":
                    _, ver, speakers = msg
                    if ver:
                        self.speakers = speakers
                        labels = [s[0] for s in speakers]
                        self.speaker_cb.config(values=labels, state="readonly")
                        if labels:
                            idx = 0
                            if self._saved_speaker and self._saved_speaker in labels:
                                idx = labels.index(self._saved_speaker)
                            self.speaker_cb.current(idx)
                            self._update_portrait()
                        self.dlg_speaker_cb.config(values=labels, state="readonly")
                        if labels:
                            didx = 0
                            if (self._saved_dlg_speaker
                                    and self._saved_dlg_speaker in labels):
                                didx = labels.index(self._saved_dlg_speaker)
                            self.dlg_speaker_cb.current(didx)
                        self.engine_var.set(f"エンジン: 接続OK (v{ver})")
                        self.dict_btn.config(state="normal")
                        self.vvproj_btn.config(state="normal")
                        if self._bookmark is not None:
                            self.resume_btn.config(state="normal")
                    else:
                        self.engine_var.set("エンジン: 未接続（VOICEVOXを起動してください）")
                    self._set_busy(False)
                elif kind == "clip_done":
                    _, cleaned = msg
                    self._set_busy(False)
                    if not cleaned:
                        self.status_var.set("クリップボード画像から文字を検出できませんでした。")
                    else:
                        cur = self.text.get("1.0", "end").strip()
                        if cur:
                            self.text.insert("end", "\n" + cleaned)
                        else:
                            self.text.insert("1.0", cleaned)
                        self.status_var.set(f"クリップボード画像をOCRしました（{len(cleaned)}文字を追記）")
                elif kind == "preview_playing":
                    _, line, sid = msg
                    self._start_mouth(sid)  # 立ち絵の口パク（喋る話者のキャラに切替）
                    self.status_var.set(f"試聴 再生中: {line[:30]}")
                elif kind == "preview_done":
                    _, ok, info = msg
                    self._previewing = False
                    self._stop_mouth()
                    if self.speakers and not self.busy:
                        self.preview_btn.config(state="normal")
                        self.playall_btn.config(state="normal")
                    if ok:
                        self.status_var.set("試聴 完了")
                    else:
                        self.status_var.set("試聴エラー")
                        messagebox.showerror("エラー", info[-1500:])
                elif kind == "playall_line":
                    _, lineno, line, sid, done, total = msg
                    # 再生中の行にカーソルを移してハイライト表示。しおりも更新
                    self._bookmark = lineno
                    self._start_mouth(sid)  # 行の話者に合わせてキャラ切替＋口パク
                    self.text.tag_remove("playing", "1.0", "end")
                    self.text.tag_add("playing", f"{lineno}.0", f"{lineno}.end")
                    self.text.tag_config("playing", background="#cde8ff")
                    self.text.mark_set("insert", f"{lineno}.0")
                    self.text.see(f"{lineno}.0")
                    self.status_var.set(f"連続再生中 {done+1}/{total}: {line[:30]}")
                elif kind == "playall_done":
                    _, ok, info, played = msg
                    self._previewing = False
                    self._playall_stop = None
                    self._stop_mouth()
                    self.text.tag_remove("playing", "1.0", "end")
                    self.stop_btn.config(state="disabled")
                    if self.speakers and not self.busy:
                        self.preview_btn.config(state="normal")
                        self.playall_btn.config(state="normal")
                        if self._bookmark is not None:
                            self.resume_btn.config(state="normal")
                    if ok:
                        self.status_var.set(
                            f"連続再生を停止しました（{played}行再生）" if info
                            else f"連続再生 完了（{played}行）")
                    else:
                        self.status_var.set("連続再生エラー")
                        messagebox.showerror("エラー", info[-1500:])
                elif kind == "dict_list":
                    _, rows = msg
                    if self._dict_win is not None and self._dict_win.winfo_exists():
                        tree = self._dict_tree
                        tree.delete(*tree.get_children())
                        for word_uuid, surface, pron, accent in rows:
                            tree.insert("", "end", iid=word_uuid,
                                        values=(surface, pron, accent))
                elif kind == "dict_status":
                    _, info = msg
                    self.status_var.set(info)
                elif kind == "synth_done":
                    _, info = msg
                    self.status_var.set("音声生成 完了")
                    self._set_busy(False)
                    messagebox.showinfo("完了", info)
                elif kind == "error":
                    _, tb = msg
                    self._set_busy(False)
                    self.status_var.set("エラーが発生しました")
                    messagebox.showerror("エラー", tb[-1500:])
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.extract_btn.config(state=state)
        self.clip_btn.config(state=state)
        if busy:
            self.synth_btn.config(state="disabled")
            self.preview_btn.config(state="disabled")
            self.playall_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
        elif self.speakers:
            self.synth_btn.config(state="normal")
            if not self._previewing:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
        self._update_step_highlight()


if __name__ == "__main__":
    App().mainloop()
