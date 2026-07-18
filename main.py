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
import shutil
import threading
import traceback
import tempfile

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import font as tkfont

from PIL import ImageGrab

import core

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
    除く）。話者名「小夜/SAYO」やファイル名の表記ゆれを吸収して同じキャラに寄せる。"""
    s = _PORTRAIT_ALIASES.get(str(name), str(name))
    return "".join(c for c in s if c not in _PORTRAIT_BAD and not c.isspace())

# ドラッグ＆ドロップ対応（tkinterdnd2 が無くてもアプリは動く）
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
    _Base = TkinterDnD.Tk
except Exception:
    _HAS_DND = False
    _Base = tk.Tk
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
        self.speakers = []              # [(label, style_id, speaker_uuid)]
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
        self._sample_cache = {}         # (speaker_uuid, style_id) -> サンプルWAV
        self._shape_report = {}         # 整形レポート {"removed": [...], "confusables": [...]}
        self._report_win = None         # 整形レポートのウィンドウ（多重表示防止）
        self._synth_cancel = None       # 音声生成のキャンセルEvent（生成中のみ非None）
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
        # 画像等を画面のどこに落としても効くよう、UI全ウィジェットをドロップ先に登録する
        self._register_drop_tree(self)
        self._load_settings()
        self._on_unit_selected()   # まとめ方に応じてN行/無音欄の有効無効を反映
        # ライト/ダークとも clam ベースのデザインパレットを常に適用する（起動時に美観を反映）
        self.apply_theme()
        self._restore_text_cache()
        self._update_step_highlight()   # 本文の有無に応じて「次に押すボタン」を絞る
        self._schedule_stats()          # 復元本文の行数・めやすを表示
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._poll_queue)
        self.after(600, self._auto_connect)  # 起動時にエンジンへ自動接続
        self.after(60000, self._autosave_tick)  # 本文の自動保存（クラッシュ対策）

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
        # 時間帯のあいさつ＋初回起動なら3ステップの道案内（ちょっとした人間味）
        hour = time.localtime().tm_hour
        hello = ("おはよう☀️" if 5 <= hour < 11
                 else "こんにちは🍵" if 11 <= hour < 17 else "こんばんは🌙")
        self._hello = hello   # 前回テキスト復元メッセージ等でもあいさつを残す
        if os.path.exists(SETTINGS_PATH):
            first = f"{hello} まずは 1. にファイルを追加してね🍂"
        else:
            first = (f"{hello} はじめまして！ ①ファイル追加 → ②抽出 → "
                     "③音声生成 の3ステップだよ🍂")
        self.status_var = tk.StringVar(value=first)
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
        # 並べ替え（ファイルの順序＝読み上げ・結合の順序なので調整できるように）
        mv = ttk.Frame(btns)
        mv.pack(fill="x", pady=2)
        up = ttk.Button(mv, text="▲ 上へ", width=6,
                        command=lambda: self._move_selected(-1))
        up.pack(side="left", expand=True, fill="x")
        dn = ttk.Button(mv, text="▼ 下へ", width=6,
                        command=lambda: self._move_selected(+1))
        dn.pack(side="left", expand=True, fill="x", padx=(2, 0))
        for b in (up, dn):
            _Tooltip(b, "選択したファイルの順序を入れ替えます\n"
                        "（上から順に抽出・結合されます）。")
        self.clip_btn = ttk.Button(btns, text="クリップボードOCR", command=self.clipboard_ocr)
        self.clip_btn.pack(fill="x", pady=(8, 2))

        lst = ttk.Frame(top)
        lst.pack(side="left", fill="both", expand=True, padx=GAPX, pady=GAPY)
        self.listbox = tk.Listbox(lst, height=5, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lst, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)
        # キーボード操作: Delete/BackSpace=削除・Alt+↑↓=並べ替え。
        # 選択1件のときはフルパスを状態欄に表示（同名ファイルの区別用）
        self.listbox.bind("<Delete>", self._kb_remove_files)
        self.listbox.bind("<BackSpace>", self._kb_remove_files)
        self.listbox.bind("<Alt-Up>",
                          lambda e: (self._move_selected(-1), "break")[1])
        self.listbox.bind("<Alt-Down>",
                          lambda e: (self._move_selected(+1), "break")[1])
        self.listbox.bind("<<ListboxSelect>>", self._show_file_path)
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
        ttk.Label(prow, text="ページ:").pack(side="left", padx=(GAPX, 2))
        self.pages_var = tk.StringVar()
        pe = ttk.Entry(prow, textvariable=self.pages_var, width=9)
        pe.pack(side="left")
        _Tooltip(pe, "読み取るPDFのページ範囲。例: 5-320 / 1-3,10-\n"
                     "空欄=全ページ。表紙・目次・索引を飛ばすのに\n"
                     "（複数PDFには同じ範囲が適用されます）。")

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
        self.urlskip_var = tk.BooleanVar(value=True)
        adv_defs = [
            (self.join_var, "改行で途切れた文を連結（小説向け）",
             "句点で終わらない改行を前の行につなげます。"),
            (self.pruby_var, "括弧ルビ除去 例:漢字(かんじ)",
             "漢字の後の括弧内の読みがなを削除します。"),
            (self.norm_var, "全角英数→半角・記号を読みに展開",
             "全角の英数字を半角にし、①や㈱・㎡などの記号を\n"
             "読み（1・株式会社・平方メートル）に展開します。"),
            (self.urlskip_var, "URL・メールを読み飛ばす",
             "本文中のURLやメールアドレスを除きます\n"
             "（読み上げると1文字ずつ読まれてしまうため。既定ON）。"),
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
        """接続クラスタの表示を切り替える。compact=True で詳細（起動・URL・接続確認）を
        畳んで「接続設定…」ボタンだけにする。順序維持のため engine_lbl を before= の
        アンカーに使う。"""
        if compact:
            self._conn_detail.pack_forget()
            self._conn_edit_btn.pack(side="left", before=self.engine_lbl)
        else:
            self._conn_edit_btn.pack_forget()
            self._conn_detail.pack(side="left", before=self.engine_lbl)

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
        # 接続成功後は詳細（起動ボタン・URL欄・接続確認）をサブフレームごと畳み、
        # 「● 接続OK + 接続設定…」のコンパクト表示にする（誤操作防止と情報整理）。
        # 失敗・切断時は必ずフル表示へ戻す（_set_conn_compact）。
        c = ttk.Frame(bottom)
        c.pack(fill="x", padx=GAPX, pady=(GAPY, 0))
        ttk.Label(c, text="接続", style="Cluster.TLabel").pack(side="left", padx=(0, GAPX))
        self._conn_detail = ttk.Frame(c)
        self._conn_detail.pack(side="left")
        ttk.Button(self._conn_detail, text="VOICEVOX起動",
                   command=self.launch_voicevox).pack(side="left")
        ttk.Label(self._conn_detail, text="URL:").pack(side="left", padx=(GAPX, 2))
        self.url_var = tk.StringVar(value=self.base_url)
        ttk.Entry(self._conn_detail, textvariable=self.url_var,
                  width=22).pack(side="left")
        ttk.Button(self._conn_detail, text="エンジン接続確認",
                   command=self.check_engine).pack(side="left", padx=(GAPX, 0))
        self._conn_edit_btn = ttk.Button(c, text="接続設定…", width=10,
                                         command=lambda: self._set_conn_compact(False))
        _Tooltip(self._conn_edit_btn, "URL変更や再接続が必要なときに開きます。")
        self.engine_var = tk.StringVar(value="エンジン: 未接続")
        self.engine_lbl = ttk.Label(c, textvariable=self.engine_var)
        self.engine_lbl.pack(side="left", padx=GAPX)
        self.cache_btn = ttk.Button(c, text="キャッシュ…", width=10,
                                    command=self.open_cache_dialog)
        self.cache_btn.pack(side="right")
        _Tooltip(self.cache_btn,
                 "合成キャッシュ（2回目以降の生成・再生を速くする仕組み）の\n"
                 "容量確認・上限変更・削除ができます。")
        _sep()

        # === 声・調整（話速/音高/抑揚/音量を grid の列で揃える。1行に収める） ===
        va = ttk.Frame(bottom)
        va.pack(fill="x", padx=GAPX)
        ttk.Label(va, text="声・調整", style="Cluster.TLabel").pack(side="left", padx=(0, GAPX))
        # 話者選択は2段（キャラ→スタイル）。170超のスタイルが1本に並ぶ長大な
        # ドロップダウンを解消する。speaker_cb はそのキャラのスタイルだけを持つ
        ttk.Label(va, text="話者:").pack(side="left")
        self.char_cb = ttk.Combobox(va, width=13, state="disabled")
        self.char_cb.pack(side="left", padx=(2, 2))
        self.char_cb.bind("<<ComboboxSelected>>", self._char_selected)
        _Tooltip(self.char_cb, "キャラクターを選びます（スタイルは右で選択）。")
        self.speaker_cb = ttk.Combobox(va, width=14, state="disabled")
        self.speaker_cb.pack(side="left", padx=(0, 2))
        self.speaker_cb.bind("<<ComboboxSelected>>", self._update_portrait, add="+")
        _Tooltip(self.speaker_cb, "スタイル（ノーマル・あまあま等）を選びます。\n"
                                  "「🔊 声を聴く」で聴き比べできます。")
        # 声サンプル試聴（エンジン同梱の公式サンプルを再生。声選びが楽になる）
        self.sample_btn = ttk.Button(va, text="🔊 声を聴く", width=9,
                                     command=self.play_speaker_sample,
                                     state="disabled")
        self.sample_btn.pack(side="left", padx=(0, GAPX))
        _Tooltip(self.sample_btn, self._tip_engine_gate(
            "選択中の話者の公式ボイスサンプルを再生します。"))
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
        self.fmt_cb.bind("<<ComboboxSelected>>", self._on_format_selected, add="+")
        _Tooltip(self.fmt_cb,
                 "WAV/M4A/MP3は通常の音声ファイル。\n"
                 "M4Bはオーディオブック：全文を1冊にまとめ、章見出し（第◯章など）から\n"
                 "自動でチャプターを付けます（Apple Books等で頭出しできます）。")
        ttk.Label(oa, text="まとめ方:").pack(side="left", padx=(GAPX, 0))
        self.unit_cb = ttk.Combobox(oa, width=13, state="readonly",
                                    values=list(self._UNITS.values()))
        self.unit_cb.current(0)
        self.unit_cb.pack(side="left", padx=2)
        self.unit_cb.bind("<<ComboboxSelected>>", self._on_unit_selected, add="+")
        self.nlines_var = tk.IntVar(value=50)
        self.nlines_sb = ttk.Spinbox(oa, from_=2, to=1000, increment=10, width=5,
                                     textvariable=self.nlines_var)
        self.nlines_sb.pack(side="left", padx=(2, 0))
        _Tooltip(self.nlines_sb,
                 "まとめ方が「N行ごと」のときの分割行数です\n（他のまとめ方では使いません）。")
        ttk.Label(oa, text="行").pack(side="left")
        ttk.Label(oa, text="文間の無音(秒):").pack(side="left", padx=(GAPX, 0))
        self.gap_var = tk.DoubleVar(value=0.4)
        self.gap_sb = ttk.Spinbox(oa, from_=0.0, to=3.0, increment=0.1, width=5,
                                  textvariable=self.gap_var)
        self.gap_sb.pack(side="left")
        _Tooltip(self.gap_sb,
                 "複数の行を1ファイルにまとめるとき、行間に入れる無音の長さです\n"
                 "（「1行=1ファイル」では使いません）。")
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
        self.pause_btn = ttk.Button(p, text="⏸ 一時停止", width=10,
                                    command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=4)
        _Tooltip(self.pause_btn,
                 "連続再生を一時停止/再開します（スペースキー）。\n"
                 "再開すると同じ行の頭から読み直します。")
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
        self.report_btn = ttk.Button(rep, text="レポート", width=7,
                                     command=self.show_shape_report,
                                     state="disabled")
        self.report_btn.pack(side="left", padx=1)
        _Tooltip(self.report_btn, lambda: (
            "ノイズ除去で消えた行とOCR誤字補正の内容を確認できます。"
            if (self._shape_report.get("removed")
                or self._shape_report.get("confusables"))
            else "抽出すると、ノイズ除去・誤字補正の内容をここで確認できます。"))
        # 本文の全消去 / 復元は1ボタンで切替（押せない「復元」を常設しない）。
        # 属性名 restore_btn は結線・テストの互換のため維持。幅固定でラベル切替時に跳ねない
        self.restore_btn = ttk.Button(rep, text="本文を全消去", width=13,
                                      command=self.clear_text)
        self.restore_btn.pack(side="right", padx=2)
        # 行数・文字数・読み上げめやすの常時表示（台本編集中に常に見たい情報。
        # 従来は抽出完了の一瞬だけ状態欄に出て他のメッセージで流れていた）
        self.stats_var = tk.StringVar()
        ttk.Label(rep, textvariable=self.stats_var).pack(side="right",
                                                         padx=(0, 8))
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
        for mod in ("Control", "Command"):
            try:
                self.bind_all(f"<{mod}-f>", lambda e: self.open_search())
                self.bind_all(f"<{mod}-equal>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-plus>", lambda e: self.change_font(+1))
                self.bind_all(f"<{mod}-minus>", lambda e: self.change_font(-1))
                self.bind_all(f"<{mod}-0>", lambda e: self.change_font(0))
                # 主要操作: 抽出(Return)・音声生成(G)・ファイル追加(O)・txt保存(S)・試聴(P)
                self.bind_all(f"<{mod}-Return>", self._kb_extract)
                self.bind_all(f"<{mod}-g>", self._kb_synth)
                self.bind_all(f"<{mod}-o>", self._kb_add_files)
                self.bind_all(f"<{mod}-s>", self._kb_save_txt)
                self.bind_all(f"<{mod}-p>", lambda e: self._kb_invoke(self.preview_btn))
            except tk.TclError:
                pass  # Command修飾子はmacOS以外に無い
        self.bind_all("<Escape>", self._kb_escape)
        self.bind_all("<space>", self._kb_space)
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
        二重発火しないよう、Entry系にフォーカスがあるときは何もしない。
        抽出中はボタンが「⛔キャンセル」に変わっているため何もしない
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
        """Ctrl/Cmd+G で音声生成。生成中はボタンが「⛔キャンセル」に変わっているため
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
        if self._synth_cancel is not None and self.busy:
            self.cancel_synth()
            return "break"
        if self._extract_cancel is not None and self.busy:
            self.cancel_extract()
            return "break"
        return self._kb_invoke(self.stop_btn)

    def _kb_space(self, event=None):
        """スペース: 連続再生中だけ一時停止/再開。本文・入力欄の編集は妨げない。
        ボタン類にフォーカスがあるときはTkのクラスバインド（スペース=そのボタンを押す）が
        先に発火するため、ここでは何もしない（二重発火で一時停止が相殺されるのを防ぐ。
        ⏸ボタン自身にフォーカスがある場合もクラスバインド側のcommandだけで正しく動く）。"""
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
        can_speak = ("normal" if (self.speakers and not self.busy
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
        m.add_command(
            label=(f"「{sel[:10]}」を読み方辞書に登録..." if sel
                   else "選択語を読み方辞書に登録..."),
            state="normal" if (sel and self.speakers) else "disabled",
            command=lambda w=sel: self._register_word_to_dict(w))
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
            m.add_cascade(label="この行を別の話者で読む（@タグ挿入）", menu=tag_menu)
        else:
            m.add_command(label="この行を別の話者で読む（@タグ挿入）",
                          state="disabled")
        if core.parse_speaker_tag(line)[0] is not None:
            m.add_command(label="この行の@話者タグを外す",
                          command=self._remove_speaker_tag)
        memo_on = line.strip().startswith(("#", "＃"))
        m.add_command(label=("＃メモ解除（読み上げ対象に戻す）" if memo_on
                             else "＃メモ行にする（読み上げから除外）"),
                      command=self._toggle_memo_lines)
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
            self._app_icon = tk.PhotoImage(file=APP_ICON_PATH)
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
        """選択中の話者に応じて立ち絵を切り替える。そのキャラの立ち絵が無ければ
        別キャラを誤表示せず、枠だけ残して画像を消す（全キャラ対応の素直な挙動）。"""
        if not getattr(self, "_portrait_label", None):
            return
        key = self._portrait_key_for(self._current_speaker_label())
        self._portrait_key = key
        if key is None:
            self._portrait_label.config(image="")
            self._portrait_label.image = None
            return
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
            key = self._portrait_key_for(self._speaker_label_for_id(speaker_id))
            self._portrait_key = key
            if key is None:
                self._portrait_label.config(image="")
                self._portrait_label.image = None
                return
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
        if "m4a" in self.encoders:
            choices.append("M4B")   # オーディオブック（全文結合・章見出しでチャプター）
        return choices

    def _out_format(self):
        v = (self.fmt_cb.get() or "WAV").lower()
        return v if v in ("wav", "m4a", "mp3", "m4b") else "wav"

    def _unit(self):
        i = self.unit_cb.current()
        keys = list(self._UNITS.keys())
        return keys[i] if 0 <= i < len(keys) else "each"

    def _voice_params(self):
        return dict(speed=self.speed_var.get(), pitch=self.pitch_var.get(),
                    intonation=self.into_var.get(), volume=self.vol_var.get())

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
                                   "話速・音高・抑揚・音量のいずれかが空か不正です。\n"
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
        sel = self.listbox.curselection()
        for i in reversed(sel):
            self.listbox.delete(i)
            del self.files[i]
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
                                   "数値の欄（OCR解像度など）が空か不正です。\n"
                                   "数字を入れてからもう一度お試しください。")
            return
        # 抽出中は「テキスト抽出」ボタンをキャンセルボタンとして使う
        # （長いスキャンPDFのOCRを途中で止められる。音声生成のキャンセルと同じ流儀）
        self._extract_cancel = threading.Event()
        params["cancel_event"] = self._extract_cancel
        self._set_busy(True)
        self.extract_btn.config(text="⛔ キャンセル", command=self.cancel_extract,
                                state="normal")
        self.progress.config(mode="determinate", maximum=len(self.files), value=0)
        threading.Thread(target=self._extract_worker,
                         args=(params, clean_opts), daemon=True).start()

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
            core.launch_voicevox()
            self.status_var.set("VOICEVOXを起動したよ。準備ができたら自動でつなぐね🍂")
            self._start_connect_retry()
        except FileNotFoundError as e:
            messagebox.showwarning("VOICEVOX", str(e))
        except Exception as e:
            messagebox.showerror("VOICEVOX", f"起動に失敗: {e}")

    def _start_connect_retry(self, seconds=90):
        """VOICEVOX起動後、エンジンが応答するまで自動で接続を試みる
        （従来は起動→手動で「エンジン接続確認」の2ステップだった）。"""
        self._conn_retry_until = time.monotonic() + seconds
        self.after(3000, self._connect_retry_tick)

    def _connect_retry_tick(self):
        if self.speakers or time.monotonic() > self._conn_retry_until:
            return   # 接続できた/諦めた（以降は手動の接続確認で）
        if not (self.busy or self._previewing):
            self.check_engine(quiet=True)   # ボタンを明滅させずに静かに確認
        try:
            self.after(3000, self._connect_retry_tick)
        except tk.TclError:
            pass   # 終了中

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
        url = self.url_var.get().strip().rstrip("/")
        self.base_url = url or VOICEVOX_DEFAULT
        self._conn_checking = True
        if not quiet:
            self._set_busy(True)
        self.engine_var.set("エンジン: 接続確認中...")
        threading.Thread(target=self._check_worker, args=(quiet,),
                         daemon=True).start()

    def _check_worker(self, quiet=False):
        try:
            ver = core.vv_check(self.base_url)
            if not ver:
                self.q.put(("engine", None, None, quiet))
                return
            speakers = core.vv_speakers(self.base_url)
            self.q.put(("engine", ver, speakers, quiet))
        except Exception:
            self.q.put(("engine", None, None, quiet))

    def _confirm_speaker_tags(self, tail="該当行はタグ文字列ごと既定話者が読み上げます。"
                                          "続けますか？\n（「いいえ」で最初の該当行へジャンプ）"):
        """未解決の@話者タグを合成/vvproj出力の前に確認する。続行なら True。
        検証はウィジェット全文（strip前）で行い、返る行番号がウィジェットの
        行番号と一致するようにする（先頭空行があってもジャンプ先がずれない）。"""
        bad = core.unresolved_speaker_tags(
            self.text.get("1.0", "end-1c"), self.speakers)
        if not bad:
            return True
        ln0, name0 = bad[0]
        if messagebox.askyesno(
                "@話者タグの確認",
                f"解決できない@話者タグが{len(bad)}行あります"
                f"（例: {ln0}行目「@{name0}:」）。\n{tail}"):
            return True
        self.text.mark_set("insert", f"{ln0}.0")
        self.text.see(f"{ln0}.0")
        return False

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
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        # @話者タグのタイプミスは合成前に指摘する（従来は行全体をタグごと既定話者が
        # 読む仕様のため、長編では全編を聴き直すまで気づけなかった）。
        # 検証はウィジェット全文（strip前）で行う＝返る行番号がウィジェットの
        # 行番号と一致し、「いいえ」のジャンプ先が先頭空行の分ずれない
        if not self._confirm_speaker_tags():
            return
        # 行別話者を解決して (テキスト, style_id, 段落番号) のジョブ一覧を作る
        default_id = default_sp[1]
        jobs = []
        para = 0
        memo_skipped = 0
        for ln in text.split("\n"):
            if not ln.strip():
                para += 1
                continue
            if core.is_memo_line(ln):
                memo_skipped += 1   # 行頭#はメモ行（読み上げ対象外）
                continue
            spoken, sp = self._resolve_line(ln)
            if not spoken.strip():
                continue  # タグのみの行
            jobs.append((spoken, sp[1] if sp else default_id, para))
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
            srt = self.srt_var.get()
        except tk.TclError:
            messagebox.showwarning("入力エラー",
                                   "話速・音高・抑揚・音量・無音・行数のいずれかが空か不正です。\n"
                                   "数字を入れてからもう一度お試しください。")
            return
        if fmt == "m4b":
            unit = "combine"   # オーディオブックは常に全文結合（章はチャプターで分ける）
        # 出力単位ごとのグループ化はGUI/CLI共有の core.group_output_indices で
        groups = core.group_output_indices(unit, [j[2] for j in jobs],
                                           nlines or 50)
        if unit == "combine":
            out = filedialog.asksaveasfilename(
                title=f"結合{fmt.upper()}の保存先", defaultextension=f".{fmt}",
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
        self.synth_btn.config(text="⛔ キャンセル", command=self.cancel_synth,
                              state="normal")
        self.progress.config(mode="determinate", maximum=len(jobs), value=0)
        threading.Thread(target=self._synth_worker,
                         args=(jobs, groups, voice, target, unit,
                               gap, fmt, srt),
                         daemon=True).start()

    def cancel_synth(self):
        """実行中の音声生成を中断する（未完了のファイルは保存しない）。"""
        if self._synth_cancel is not None:
            self._synth_cancel.set()
            self.synth_btn.config(state="disabled")
            self.status_var.set("キャンセルしています...")

    def _synth_restore_button(self):
        """生成の完了/キャンセル/エラー後に「音声を生成」ボタンを元へ戻す。"""
        self._synth_cancel = None
        self.synth_btn.config(text="🔊 音声を生成", command=self.start_synth)

    def _synth_worker(self, jobs, groups, voice, target, unit, gap, fmt, srt):
        cancel = self._synth_cancel
        # 行WAVはスプール（一時フォルダ）へ書き、メモリには (パス, 再生秒) だけ
        # 保持する。従来は全行のWAVバイト列をリストに持ち、10時間級の本で
        # メモリが数GBに達していた（結合時はさらに倍）。
        # mkdtemp は try の中で作る（ディスクフル等で失敗しても except が
        # ("error",…) を積みUIのbusyが解除されるように。外に置くと通知されず永久ロック）
        spool = None
        try:
            from concurrent.futures import ThreadPoolExecutor
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
                    except Exception:
                        if attempt == 2:
                            raise
                        if cancel is not None and cancel.is_set():
                            return None
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

            # エンジンへ3並列で投げる（順序はexecutor.mapが保持する）
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
                            gap_sec=gap)
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
                        "target": target, "fmt": fmt, "srt": srt, "gap": gap,
                    }))
                    return
                self.q.put(("synth_cancelled", done_count[0], len(jobs), 0))
                return
            # ここから保存フェーズ。キャンセルボタンを畳む（保存は途中で止めると
            # 壊れたファイルが残るため受け付けない）。直前の押下は下のループ先頭で拾う
            self.q.put(("synth_saving",))

            width = max(3, len(str(len(groups))))   # 999超でも名前順が崩れない
            multi = len({j[1] for j in jobs}) > 1
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
                if len(paths) == 1:
                    core.encode_audio_file(paths[0], out_path, fmt,
                                           self.encoders, keep_input=True)
                else:
                    # 逐次結合（メモリに全体を持たない）→ 変換。中間WAVは出力先と
                    # 同じフォルダに .part 名で作り、終わったら消す
                    part = out_path + ".part.wav"
                    try:
                        core.concat_wavs_to_file(paths, part, gap_sec=gap)
                        core.encode_audio_file(part, out_path, fmt,
                                               self.encoders)
                    finally:
                        if os.path.exists(part):
                            try:
                                os.remove(part)
                            except OSError:
                                pass
                if fmt == "m4b":
                    chap_note = self._embed_chapters(out_path, jobs, idxs,
                                                    durs, gap)
                if srt:
                    lines = [jobs[i][0] for i in idxs]
                    srt_path = os.path.splitext(out_path)[0] + ".srt"
                    with open(srt_path, "w", encoding="utf-8") as f:
                        f.write(core.make_srt(lines, durs, gap_sec=gap))
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
            credit = core.voicevox_credit(used) or "VOICEVOX:（話者名）"
            note = f"（字幕{srt_count}件も保存）" if srt_count else ""
            if unit == "combine":
                msg = (f"結合{fmt.upper()}を保存しました{note}{chap_note}:\n"
                       f"{target}")
            else:
                msg = f"{len(groups)}個の{fmt.upper()}を保存しました{note}:\n{target}"
            self.q.put(("synth_done", msg, target, credit))
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
            credit = core.voicevox_credit(used) or "VOICEVOX:（話者名）"
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
        default = self._current_speaker()
        if default is None:
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        # 合成前と同じく@タグのタイプミスを事前に指摘（プロジェクトに混入させない）
        if not self._confirm_speaker_tags(
                "該当行はタグ文字列ごと既定話者のブロックになります。続けますか？"):
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
        clean_opts = self._gather_clean_opts()
        self._set_busy(True)
        self.status_var.set("クリップボード画像をOCR中...")
        threading.Thread(target=self._clipboard_worker,
                         args=(data, self.pre_var.get(), clean_opts,
                               self.fixconf_var.get(), self.denoise_var.get()),
                         daemon=True).start()

    def _clipboard_worker(self, img, preprocess, clean_opts,
                          fix_confusables=False, denoise=True):
        try:
            report = {}
            # OCRが済めばPNG（＝クリップボード画像のコピー）は不要。%TEMP%に残さない
            with tempfile.TemporaryDirectory(prefix="t2v_clip_") as tmpdir:
                png = os.path.join(tmpdir, "clip.png")
                core.preprocess_image(img, enable=preprocess).save(png)
                res = core.run_ocr([png], strip_labels=denoise)
                raw = res.get(png, "")
                # 低品質（写真の影・ムラ）なら照明平坦化で再OCR（macのみ・自動）
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
            self.q.put(("clip_done", cleaned, report))
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
        tree.bind("<Double-1>", self._dict_edit_selected)
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
        b_add = ttk.Button(btns, text="追加/上書き", command=self._dict_add)
        b_add.pack(side="left", padx=2)
        _Tooltip(b_add, "同じ単語が登録済みなら読みを上書きします\n"
                        "（一覧のダブルクリックで編集用に読み込めます）。")
        b_prev = ttk.Button(btns, text="▶ 読みを試聴", command=self._dict_preview)
        b_prev.pack(side="left", padx=2)
        _Tooltip(b_prev, "単語欄のテキストを今の話者で読み上げて、\n"
                         "登録した読みが効いているか確認します。")
        ttk.Button(btns, text="選択を削除", command=self._dict_delete).pack(side="left", padx=2)
        ttk.Button(btns, text="再読込", command=self._dict_refresh).pack(side="left", padx=2)
        ttk.Button(btns, text="書き出し...", command=self._dict_export).pack(side="left", padx=(10, 2))
        ttk.Button(btns, text="読み込み...", command=self._dict_import).pack(side="left", padx=2)
        ttk.Label(btns, text="※読みはひらがなでもOK（自動でカタカナ化）"
                  ).pack(side="left", padx=8)
        self._dict_refresh()

    def _dict_refresh(self):
        threading.Thread(target=self._dict_list_worker, daemon=True).start()

    def _dict_list_worker(self):
        try:
            rows = core.vv_dict_list(self.base_url)
            self.q.put(("dict_list", rows))
        except Exception as e:
            self.q.put(("dict_status", f"辞書の取得に失敗: {e}"))

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
        # 同じ単語が一覧にあれば上書き（従来は重複エントリが2つできていた）
        existing_uuid = None
        for iid in self._dict_tree.get_children():
            vals = self._dict_tree.item(iid, "values")
            if vals and vals[0] == surface:
                existing_uuid = iid   # iid = word_uuid
                break

        def worker():
            try:
                if existing_uuid:
                    core.vv_dict_update(self.base_url, existing_uuid,
                                        surface, pron, accent)
                    self.q.put(("dict_status", f"上書きしました：{surface} → {pron}"))
                else:
                    core.vv_dict_add(self.base_url, surface, pron, accent)
                    self.q.put(("dict_status", f"登録しました：{surface} → {pron}"))
                self._dict_gen += 1   # 実行中の合成に辞書変更を知らせる（キャッシュ鮮度）
                rows = core.vv_dict_list(self.base_url)
                self.q.put(("dict_list", rows))
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
            self.status_var.set("エンジン接続後に試聴できます。")
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
        threading.Thread(target=self._dict_preview_worker,
                         args=(surface, sp[1], voice), daemon=True).start()

    def _dict_preview_worker(self, text, speaker_id, voice):
        # 登録直後の読みを確認する用途なのでキャッシュは通さない
        try:
            wb, reading = core.vv_synthesize_with_kana(self.base_url, text,
                                                       speaker_id, **voice)
            self._preview_buf = wb
            self.q.put(("preview_playing", text, speaker_id, None))
            core.play_wav_blocking(wb, stop_event=self._preview_stop)
            self.q.put(("preview_done", True, reading))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc()))

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
                    # JSON内の同一単語の2件目以降もスキップさせる（重複登録防止）
                    existing.add(surface)
                if added:
                    self._dict_gen += 1   # キャッシュ鮮度（実行中の合成へ変更通知）
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
            # 無音returnだと「押しても反応しない」に見えるので理由を出す
            self.status_var.set("再生／処理の実行中です。停止してからお試しください"
                                "（止まらないときは■停止/Esc）。")
            return
        default_sp = self._current_speaker()
        if default_sp is None:
            messagebox.showinfo(
                "情報", "先にVOICEVOXエンジンに接続してください。\n"
                "4. の「エンジン接続確認」を押すか、VOICEVOXアプリ（またはエンジン）を"
                "起動してください。接続できると話者が選べて試聴できます。")
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
        threading.Thread(target=self._preview_worker,
                         args=(spoken, speaker_id, voice, lineno),
                         daemon=True).start()

    def _preview_worker(self, line, speaker_id, voice, lineno=None):
        try:
            # audio_query 1回で合成と読み確認の両方をまかなう（往復削減）。
            # キャッシュにあれば合成せず、読みだけ audio_query で取り直す
            dict_hash = core.vv_dict_hash(self.base_url)
            key = core.synth_cache_key(line, speaker_id, engine_ver=self._engine_ver,
                                       dict_hash=dict_hash, **voice)
            wb = core.synth_cache_get(key)
            reading = ""
            if wb is None:
                wb, reading = core.vv_synthesize_with_kana(
                    self.base_url, line, speaker_id, **voice)
                if self._engine_ver and dict_hash:
                    core.synth_cache_put(key, wb)
            else:
                try:
                    reading = core.vv_reading(self.base_url, line, speaker_id)
                except Exception:
                    pass   # 読み取得の失敗は試聴を妨げない
            self._preview_buf = wb
            self.q.put(("preview_playing", line, speaker_id, lineno))
            # ワーカースレッドなので同期再生でブロックして問題ない
            core.play_wav_blocking(wb, stop_event=self._preview_stop)
            self.q.put(("preview_done", True, reading))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc()))

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
        threading.Thread(target=self._sample_worker,
                         args=(label, style_id, sp_uuid), daemon=True).start()

    def _sample_worker(self, label, style_id, sp_uuid):
        try:
            wav = self._sample_cache.get((sp_uuid, style_id))
            if wav is None:
                wav = core.vv_speaker_sample(self.base_url, sp_uuid, style_id)
                self._sample_cache[(sp_uuid, style_id)] = wav
            self.q.put(("preview_playing", f"（声サンプル: {label}）", style_id, None))
            core.play_wav_blocking(wav, stop_event=self._preview_stop)
            self.q.put(("preview_done", True, ""))
        except Exception:
            self.q.put(("preview_done", False, traceback.format_exc()))

    # ---------------- 連続再生（カーソル行から最後まで） ----------------
    def play_all(self):
        if self._previewing or self.busy:
            self.status_var.set("再生／処理の実行中です。停止してからお試しください"
                                "（止まらないときは■停止/Esc）。")
            return
        default_sp = self._current_speaker()
        if default_sp is None:
            messagebox.showinfo(
                "情報", "先にVOICEVOXエンジンに接続してください。\n"
                "4. の「エンジン接続確認」を押すか、VOICEVOXアプリ（またはエンジン）を"
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
        default_id = default_sp[1]
        all_lines = self.text.get("1.0", "end-1c").split("\n")
        numbered = []
        for i, ln in enumerate(all_lines, start=1):
            if not ln.strip() or core.is_memo_line(ln):
                continue   # 空行・メモ行（行頭#）は読まない
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
        self._playall_pause = threading.Event()
        self._previewing = True
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.resume_btn.config(state="disabled")
        self.sample_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pause_btn.config(text="⏸ 一時停止", state="normal")
        threading.Thread(target=self._playall_worker,
                         args=(targets, voice), daemon=True).start()

    def toggle_pause(self):
        """連続再生の一時停止/再開（再開時は同じ行の頭から読み直す）。"""
        ev = self._playall_pause
        if ev is None:
            return
        if ev.is_set():
            ev.clear()
            self.pause_btn.config(text="⏸ 一時停止")
            self.status_var.set("再開します...")
        else:
            ev.set()
            self.pause_btn.config(text="⏵ 再開")
            self.status_var.set("一時停止中（⏵ 再開 または スペースキーで続き）")

    def _mark_bookmark(self):
        """しおり（⏵続きから の再開位置）の行を淡色でマークして見えるようにする。
        連続再生の停止後・前回テキスト復元後に呼ばれ、どこから再開するかが一目で分かる。"""
        try:
            self.text.tag_remove("bookmark", "1.0", "end")
            if self._bookmark is None:
                return
            last = int(self.text.index("end-1c").split(".")[0])
            line = min(self._bookmark, last)
            self.text.tag_add("bookmark", f"{line}.0", f"{line}.end")
        except tk.TclError:
            pass

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
        if self._preview_stop is not None:
            self._preview_stop.set()   # 試聴・声サンプルの再生も同じボタンで止める
        self.stop_btn.config(state="disabled")
        self.status_var.set("停止しています...")
        # 万一ワーカーが応答せず状態が残ったら（完了通知が来ない等）強制復帰する。
        # これが無いと _previewing が立ちっぱなしになり、以降の試聴・連続再生が
        # 反応しなくなる（＝「試聴が動かない」に見える）
        self.after(1500, self._recover_if_stuck)

    def _recover_if_stuck(self):
        """停止後も再生状態が残っていたらUIを強制的に待機状態へ戻す（安全網）。"""
        if not self._previewing:
            return
        self._previewing = False
        self._preview_stop = None
        self._playall_stop = None
        self._playall_pause = None
        self._stop_mouth()
        self.text.tag_remove("playing", "1.0", "end")
        self.pause_btn.config(text="⏸ 一時停止", state="disabled")
        if self.speakers and not self.busy:
            self.preview_btn.config(state="normal")
            self.playall_btn.config(state="normal")
            self.sample_btn.config(state="normal")
            if self._bookmark is not None:
                self.resume_btn.config(state="normal")
        self.status_var.set("停止しました（待機中）。")

    def _playall_worker(self, targets, voice):
        stop = self._playall_stop
        pause = self._playall_pause
        played = 0
        skipped = 0
        core.synth_cache_protect(time.time())   # 長い本の途中でキャッシュを守る
        try:
            from concurrent.futures import ThreadPoolExecutor
            # 合成キャッシュ（聴き終えた行は音声生成でも再合成不要になる）
            dict_hash = self._dict_hash_tracker()
            last_tb = [None]

            def synth(t):
                # 1行の失敗（極端に長い行のtimeout・特殊文字のエンジンエラー等）で
                # 読書全体を止めない。失敗は None で返しスキップ判定に回す
                try:
                    return core.vv_synthesize_cached(
                        self.base_url, t[1], t[2], engine_ver=self._engine_ver,
                        dict_hash=dict_hash(), **voice)
                except Exception:
                    last_tb[0] = traceback.format_exc()
                    return None

            # 停止・一時停止のどちらでも現在行の再生を打ち切る（afplay/winsoundの引数用）
            either = _EitherEvent(stop, pause)
            consec = 0
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
                    if wb is None:
                        skipped += 1
                        consec += 1
                        if consec >= 3:
                            # 連続3失敗＝エンジンが落ちている。全行を無言スキップ
                            # して「読み終わったよ」と誤報しないため従来のエラーへ
                            raise RuntimeError(last_tb[0] or "連続して合成に失敗")
                        self.q.put(("playall_skip", lineno))
                        continue
                    consec = 0
                    while True:
                        if stop.is_set():
                            break
                        self._preview_buf = wb
                        core.play_wav_blocking(wb, stop_event=either)
                        if pause.is_set() and not stop.is_set():
                            # 一時停止: 解除（または停止）まで待ち、同じ行を頭から読み直す
                            while pause.is_set() and not stop.is_set():
                                time.sleep(0.1)
                            continue
                        break
                    if stop.is_set():
                        break
                    played += 1
            self.q.put(("playall_done", True, stop.is_set(), played, skipped))
        except Exception:
            self.q.put(("playall_done", False, traceback.format_exc(),
                        played, skipped))
        finally:
            core.synth_cache_protect(0.0)

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
        self.status_var.set(f"{count}件置換しました：「{find}」→「{repl}」")

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
        self.restore_btn.config(text="復元", command=self.restore_text)
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
        self._cleared_text = None
        self.restore_btn.config(text="本文を全消去", command=self.clear_text)
        self._update_step_highlight()
        self._schedule_stats()
        self.status_var.set("本文を復元しました。")

    # ---------------- エラーの人間語化 ----------------
    # (正規表現, やさしい説明と次の一手)。上から順に最初にマッチしたものを使う
    _ERROR_HINTS = [
        (r"ConnectionError|Connection refused|Failed to establish|Max retries",
         "VOICEVOXエンジンにつながりませんでした。\n"
         "VOICEVOXが起動しているか確認してね（4.の「VOICEVOX起動」からも起動できるよ）。"),
        (r"ReadTimeout|timed out",
         "エンジンの応答が遅いみたい…。\n"
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
         "解像度(DPI)を下げるか、ファイルを分けて試してみてね。"),
    ]

    def _show_friendly_error(self, tb):
        """エラーを人間の言葉で伝える。原因のヒントと次の一手を提案し、
        技術的な詳細（traceback）は「詳細を表示」を押したときだけ見せる
        （従来は生のtracebackがいきなり表示されて不親切だった）。"""
        friendly = ("うまくいきませんでした…ごめんなさい！\n"
                    "もう一度試しても続くようなら「詳細を表示」の内容を添えて教えてね。")
        for pat, msg in self._ERROR_HINTS:
            if re.search(pat, tb):
                friendly = msg
                break
        win = tk.Toplevel(self)
        win.title("エラー")
        win.transient(self)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frm, text="⚠️ " + friendly, wraplength=480,
                  justify="left").pack(anchor="w")
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

    # ---------------- 音声キャッシュの管理ダイアログ ----------------
    def open_cache_dialog(self):
        """合成キャッシュの容量確認・上限変更・クリアの小窓（多重表示防止付き）。"""
        if (getattr(self, "_cache_win", None) is not None
                and self._cache_win.winfo_exists()):
            self._cache_win.lift()
            return
        win = tk.Toplevel(self)
        self._cache_win = win
        win.title("音声キャッシュ")
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(frm, justify="left", text=(
            "同じ行・同じ声の合成結果を再利用して、再生成や連続再生を速くしています。\n"
            "消しても安全です（必要になればまた作られます）。\n"
            "長い本（3時間超）をよく作るなら上限を大きめにすると効きが良くなります。"
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
            self.status_var.set("キャッシュ上限を変更しています…"
                                "（次回起動にも引き継がれます）。")
        ttk.Button(row, text="適用", command=_apply_limit).pack(side="left")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))

        def _clear():
            if self.busy or self._previewing:
                self.status_var.set("処理・再生の実行中はクリアできません。")
                return
            n = core.synth_cache_clear()
            self.status_var.set(f"音声キャッシュを{n}件削除しました。")
            _refresh()

        def _open_dir():
            try:
                os.makedirs(core.SYNTH_CACHE_DIR, exist_ok=True)
            except OSError:
                pass
            self._open_output_location(core.SYNTH_CACHE_DIR)
        ttk.Button(btns, text="キャッシュをクリア", command=_clear).pack(side="left")
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
        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=14, pady=12)
        ttk.Label(frm, text=info, wraplength=520,
                  justify="left").pack(anchor="w")
        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(frm, text="音声を公開するときは、このクレジット表記をどこかに載せてね:",
                  wraplength=520, justify="left").pack(anchor="w")
        ttk.Label(frm, text=credit, style="Cluster.TLabel",
                  wraplength=520, justify="left").pack(anchor="w", pady=(2, 0))
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
        for k in ("removed", "confusables"):
            if report.get(k):
                self._shape_report.setdefault(k, []).extend(report[k])
        n_r = len(self._shape_report.get("removed", []))
        n_c = len(self._shape_report.get("confusables", []))
        self.report_btn.config(state="normal" if (n_r or n_c) else "disabled")
        return n_r, n_c

    def show_shape_report(self):
        """ノイズ除去で消えた行・OCR誤字補正の内容を一覧表示する。
        誤って消えた本文はここからコピーして本文へ戻せる（読み取り専用でも選択・コピー可）。"""
        removed = self._shape_report.get("removed", [])
        conf = self._shape_report.get("confusables", [])
        if not removed and not conf:
            self.status_var.set("表示できる整形レポートがありません（抽出後に使えます）。")
            return
        # 多重表示防止: 既存ウィンドウは破棄して常に最新内容で作り直す
        if self._report_win is not None and self._report_win.winfo_exists():
            self._report_win.destroy()
        win = tk.Toplevel(self)
        self._report_win = win
        win.title("整形レポート")
        win.geometry("640x480")
        win.transient(self)
        head = ttk.Frame(win)
        head.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(head, text="自動整形の内容（選択してコピーできます）").pack(side="left")
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
        if removed:
            content.append(f"■ ノイズ除去で消えた行（{len(removed)}行）")
            content.append("  ※本文だった行が消えていたら、ここからコピーして戻してください。")
            content.append("  ※§2「画面キャプチャのノイズを除去」をOFFにすると消えなくなります。")
            content.append("")
            content.extend(f"  {ln.strip()}" for ln in removed)
            content.append("")
        if conf:
            content.append(f"■ OCR誤字の補正（{len(conf)}箇所）")
            content.append("  ※§2「OCR誤字を補正」をOFFにすると補正されなくなります。")
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
        ins, y = self.text.index("insert"), self.text.yview()[0]
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self._restore_view(ins, y)   # 読み進めていた位置を見失わない
        self.status_var.set(f"全{len(self.replace_rules)}ルールで計{total}件置換しました。")

    # ---------------- テーマ（選択式・4種） ----------------
    # すべて clam ベースの統一配色。ライト/ダーク＝落ち葉、くっきり＝高コントラスト、
    # ずんだ＝ずんだもんの若草色。文字と地の色差（コントラスト）を優先して選定。
    LIGHT = dict(
        bg="#f4efe6", card="#faf6ee", field="#ffffff", textbg="#ffffff",
        fg="#3d342c", subtle="#827564", head_fg="#5a4636",
        accent="#b95f22", accent_hi="#d0712f", accent_fg="#ffffff",
        btn="#ece1cf", btn_hi="#e2d3b9", border="#ddd0bd",
        sel="#f0dcbf", disabled="#b7ab9a", ok="#3a7d44",
    )
    DARK = dict(
        bg="#272320", card="#302b26", field="#3a342d", textbg="#1f1c19",
        fg="#ece3d5", subtle="#a89b89", head_fg="#f0e6d6",
        accent="#e08a45", accent_hi="#ef9a55", accent_fg="#241a12",
        btn="#3c362f", btn_hi="#4a433a", border="#4a4239",
        sel="#5c4632", disabled="#6b6255", ok="#8fd694",
    )
    HC = dict(  # くっきり：白地＋濃紺文字＋強い枠線。小さな文字も読みやすく
        bg="#ffffff", card="#ffffff", field="#ffffff", textbg="#ffffff",
        fg="#111111", subtle="#3d3d3d", head_fg="#000000",
        accent="#0a58ca", accent_hi="#2f74d8", accent_fg="#ffffff",
        btn="#ededed", btn_hi="#dcdcdc", border="#5a5a5a",
        sel="#bcd7ff", disabled="#8a8a8a", ok="#1b5e20",
    )
    ZUNDA = dict(  # ずんだ：ずんだもんの若草色。やわらかい緑の明色テーマ
        bg="#edf6e8", card="#f7fbf3", field="#ffffff", textbg="#ffffff",
        fg="#243329", subtle="#5f7264", head_fg="#2c5c3d",
        accent="#357a4c", accent_hi="#43955e", accent_fg="#ffffff",
        btn="#d9ead0", btn_hi="#c9e0bd", border="#b9cfae",
        sel="#c9ecca", disabled="#9db3a2", ok="#2c7a44",
    )
    # (設定キー, 表示名, パレット)。表示名がテーマ選択プルダウンの並びになる
    THEMES = [("light", "🍂 ライト", LIGHT), ("dark", "🌙 ダーク", DARK),
              ("hc", "☀️ くっきり", HC), ("zunda", "🌿 ずんだ", ZUNDA)]

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
        # 検索/再生/しおりのハイライトは明色固定なので、文字色を黒にして全テーマで読めるように
        for tag, bg in (("playing", "#cde8ff"), ("hit", "#fff3a3"),
                        ("curhit", "#ffb347"), ("bookmark", "#e6d8f5")):
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
        # エンジン接続OKの状態表示（テーマごとの緑。固定色だとダーク/くっきりで破綻する）
        style.configure("EngineOK.TLabel", background=p["bg"],
                        foreground=p["ok"], font=self._heading_font)
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
            self.check_engine(quiet=True)

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
            self.after(60000, self._autosave_tick)
        except tk.TclError:
            pass   # 終了中

    def _restore_text_cache(self):
        try:
            with open(TEXT_CACHE_PATH, encoding="utf-8") as f:
                cached = f.read()
        except Exception:
            return
        if cached.strip() and not self.text.get("1.0", "end").strip():
            self.text.insert("1.0", cached)
            self._mark_bookmark()   # 前回のしおり位置を淡色でマーク
            self.status_var.set(f"{self._hello} 前回のテキストを復元したよ"
                                "（しおりの「⏵ 続きから」も使えます）")

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
        self.status_var.set(f"プリセット「{p['name']}」を適用しました。")

    def save_preset(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("プリセット保存", "プリセット名:", parent=self)
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
            "dark": self.dark_var.get(),
            "theme": self.theme_var.get(),
            "unit": self._unit(), "nlines": self._get_num(self.nlines_var, 50),
            "srt": self.srt_var.get(),
            "font_size": int(self.text_font.cget("size")),
            "speed": self._get_num(self.speed_var, 1.0),
            # エンジン未接続のセッションではコンボが空＝保存済みの選択を保持する
            # （空文字で上書きすると次回接続時に先頭話者へ戻ってしまう）
            "speaker": self._current_speaker_label() or (self._saved_speaker or ""),
            "pitch": self._get_num(self.pitch_var, 0.0),
            "intonation": self._get_num(self.into_var, 1.0),
            "volume": self._get_num(self.vol_var, 1.0),
            "fmt": self._out_format(),
            "gap": self._get_num(self.gap_var, 0.4),
            "replace_rules": self.replace_rules,
            "presets": self.presets,
            "dlg_enabled": self.dlg_var.get(),
            "dlg_speaker": (self.dlg_speaker_cb.get()
                            or (self._saved_dlg_speaker or "")),
            "bookmark": self._bookmark,
            "base_url": self.url_var.get().strip() or self.base_url,
            "geometry": self.geometry(),
            "adv_open": bool(self._adv_open),
            "voice_detail_open": bool(self._vdetail_open),
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
            self.urlskip_var.set(bool(s.get("remove_urls", True)))
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
                self._on_format_selected()   # M4B復元時は「まとめ方」無効化も反映
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
            # 上限を設定し、前回セッションからの超過分を起動時に一度掃除する
            # （上限を下げた設定で終了した場合の持ち越し解消。別スレッドで）
            core.set_synth_cache_limit(s.get("synth_cache_mb", 500))
            threading.Thread(target=core._synth_cache_evict, daemon=True).start()
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
                       self._preview_stop):
                if ev is not None:
                    ev.set()
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
                try:
                    self._dispatch_msg(msg)
                except Exception:
                    # 1メッセージの処理例外でキューポンプ全体を殺さない（従来は
                    # after再スケジュールに届かず、以後ワーカー完了が二度と処理
                    # されない＝UI永久フリーズの単一障害点だった）。状態を復旧して続行
                    traceback.print_exc()
                    self._synth_restore_button()
                    self._extract_restore_button()
                    self._set_busy(False)
        except queue.Empty:
            pass
        finally:
            try:
                self.after(120, self._poll_queue)
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
            # 本文が変わったので復元ポイントは _on_text_modified が自動で無効化する
            self.progress.config(value=self.progress["maximum"])
            self._shape_report = {}   # 新しい抽出でレポートを作り直す
            n_r, n_c = self._merge_report(report)
            shaped = ""
            if n_r or n_c:
                shaped = f"｜ノイズ除去{n_r}行・誤字補正{n_c}件（レポート参照）"
            n = len([l for l in cleaned.split("\n") if l.strip()])
            try:
                # ＃メモ行・@タグは読まれないので、めやすからも除いて概算する
                est = core.fmt_duration(core.estimate_read_seconds(
                    core.speakable_text(cleaned), self.speed_var.get()))
            except tk.TclError:
                est = "?"
            self.status_var.set(f"✅ 抽出できたよ：{n}行・読み上げめやす{est}"
                                f"（3.で直したら「🔊 音声を生成」へ🍂）{shaped}")
            self._set_busy(False)
            if warnings:
                messagebox.showwarning("注意", "\n".join(warnings))
        elif kind == "engine":
            _, ver, speakers, quiet = msg
            if ver:
                self._engine_ver = ver
                self.speakers = speakers
                labels = [s[0] for s in speakers]
                # 2段選択（キャラ→スタイル）を構築し、保存済み話者を復元
                self._build_char_map()
                if not (self._saved_speaker and
                        self._select_speaker_label(self._saved_speaker)):
                    if self._char_map:
                        self.char_cb.current(0)
                        self._char_selected()
                self.dlg_speaker_cb.config(values=labels, state="readonly")
                if labels:
                    didx = 0
                    if (self._saved_dlg_speaker
                            and self._saved_dlg_speaker in labels):
                        didx = labels.index(self._saved_dlg_speaker)
                    self.dlg_speaker_cb.current(didx)
                self.engine_var.set(f"● 接続OK (v{ver})")
                self.engine_lbl.config(style="EngineOK.TLabel")
                self._set_conn_compact(True)
                self.dict_btn.config(state="normal")
                self.vvproj_btn.config(state="normal")
                self.sample_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
            else:
                # 起動直後の自動リトライ中は「未接続」と脅かさず待ちを伝える
                if time.monotonic() < self._conn_retry_until:
                    self.engine_var.set("エンジン: 起動を待っています…（自動で再接続）")
                else:
                    self.engine_var.set("エンジン: 未接続（VOICEVOXを起動してください）")
                self.engine_lbl.config(style="TLabel")
                self._set_conn_compact(False)
            self._conn_checking = False
            if not quiet:
                self._set_busy(False)
            elif ver and not self.busy and not self._previewing:
                # quiet成功時もボタン有効化は必要（実処理は走っていないので安全）
                self._set_busy(False)
        elif kind == "clip_done":
            _, cleaned, report = msg
            self._set_busy(False)
            self._merge_report(report)  # 追記なのでレポートは累積する
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
            _, line, sid, lineno = msg
            self._start_mouth(sid)  # 立ち絵の口パク（喋る話者のキャラに切替）
            if lineno:
                # 連続再生と同じく、いま読んでいる行をハイライト表示
                self.text.tag_remove("playing", "1.0", "end")
                self.text.tag_add("playing", f"{lineno}.0", f"{lineno}.end")
            self.status_var.set(f"試聴 再生中: {line[:30]}")
        elif kind == "preview_done":
            _, ok, info = msg
            self._previewing = False
            self._preview_stop = None
            self._stop_mouth()
            self.text.tag_remove("playing", "1.0", "end")
            self.stop_btn.config(state="disabled")
            if self.speakers and not self.busy:
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
                self._show_friendly_error(info)
        elif kind == "playall_line":
            _, lineno, line, sid, done, total = msg
            # 再生中の行にカーソルを移してハイライト表示。しおりも更新
            self._bookmark = lineno
            self._start_mouth(sid)  # 行の話者に合わせてキャラ切替＋口パク
            self.text.tag_remove("playing", "1.0", "end")
            self.text.tag_add("playing", f"{lineno}.0", f"{lineno}.end")
            self.text.mark_set("insert", f"{lineno}.0")
            self.text.see(f"{lineno}.0")
            self.status_var.set(f"連続再生中 {done+1}/{total}: {line[:30]}")
        elif kind == "playall_skip":
            _, lineno = msg
            self.status_var.set(f"⚠ {lineno}行目は合成できなかったので"
                                "スキップしたよ（続きは読むね）")
        elif kind == "playall_done":
            _, ok, info, played, skipped = msg
            self._previewing = False
            self._playall_stop = None
            self._playall_pause = None
            self._stop_mouth()
            self.text.tag_remove("playing", "1.0", "end")
            self._mark_bookmark()   # 次に「⏵続きから」で再開する行を可視化
            self.stop_btn.config(state="disabled")
            self.pause_btn.config(text="⏸ 一時停止", state="disabled")
            if self.speakers and not self.busy:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")
                self.sample_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
            if ok:
                skip_note = f"・{skipped}行スキップ" if skipped else ""
                self.status_var.set(
                    f"連続再生を停止しました（{played}行読んだよ{skip_note}）" if info
                    else f"📖 最後まで読み終わったよ（{played}行{skip_note}）"
                         "おつかれさま！")
            else:
                self.status_var.set("連続再生がうまくいきませんでした…")
                self._show_friendly_error(info)
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
        elif kind == "cache_limit_applied":
            # キャッシュ上限のevict完了。ダイアログが開いていれば統計を更新
            r = getattr(self, "_cache_refresh", None)
            if r is not None and getattr(self, "_cache_win", None) is not None \
                    and self._cache_win.winfo_exists():
                try:
                    r()
                except tk.TclError:
                    pass
            self.status_var.set("キャッシュ上限を変更しました"
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
                threading.Thread(target=self._partial_save_worker,
                                 args=(info,), daemon=True).start()
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
        elif kind == "error":
            _, tb = msg
            self._synth_restore_button()    # 生成エラー時もボタンを元に戻す
            self._extract_restore_button()  # 抽出エラー時も同様
            self._set_busy(False)
            self.status_var.set("うまくいきませんでした…（内容を確認してね）")
            self._show_friendly_error(tb)

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        # 抽出中は extract_btn が「⛔キャンセル」に切り替わるため無効化しない
        if self._extract_cancel is None:
            self.extract_btn.config(state=state)
        self.clip_btn.config(state=state)
        if busy:
            # 音声生成中は synth_btn が「キャンセル」に切り替わるため無効化しない
            if self._synth_cancel is None:
                self.synth_btn.config(state="disabled")
            self.preview_btn.config(state="disabled")
            self.playall_btn.config(state="disabled")
            self.resume_btn.config(state="disabled")
            self.sample_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
        elif self.speakers:
            self.synth_btn.config(state="normal")
            if not self._previewing:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")
                self.sample_btn.config(state="normal")
                if self._bookmark is not None:
                    self.resume_btn.config(state="normal")
        self._update_step_highlight()


if __name__ == "__main__":
    App().mainloop()
