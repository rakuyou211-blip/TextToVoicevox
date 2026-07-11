# -*- coding: utf-8 -*-
"""
PDF・画像 → テキスト抽出 → VOICEVOX 連携ツール（オフライン）
GUI本体。テキスト抽出(core)とVOICEVOXエンジン連携を tkinter で操作する。
"""
import os
import json
import queue
import threading
import traceback
import tempfile

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import ImageGrab

import core

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# ドラッグ＆ドロップ対応（tkinterdnd2 が無くてもアプリは動く）
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
    _Base = TkinterDnD.Tk
except Exception:
    _HAS_DND = False
    _Base = tk.Tk
    DND_FILES = None

APP_TITLE = "テキスト抽出 → VOICEVOX  (オフライン)"
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


class App(_Base):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
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

        self._build_ui()
        self._load_settings()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._poll_queue)

    # ---------------- UI構築 ----------------
    def _build_ui(self):
        if UI_FONT:
            try:
                self.option_add("*Font", UI_FONT)
            except Exception:
                pass
        pad = {"padx": 6, "pady": 4}

        # === 上段: ファイル ===
        hint = "（ここにファイル/フォルダをドラッグ＆ドロップ）" if _HAS_DND else ""
        top = ttk.LabelFrame(self, text="1. 入力ファイル（PDF・画像）" + hint)
        top.pack(fill="x", **pad)
        btns = ttk.Frame(top)
        btns.pack(side="left", fill="y", padx=4, pady=4)
        ttk.Button(btns, text="ファイル追加", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="フォルダ追加", command=self.add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="選択削除", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btns, text="全クリア", command=self.clear_files).pack(fill="x", pady=2)
        self.clip_btn = ttk.Button(btns, text="クリップボードOCR", command=self.clipboard_ocr)
        self.clip_btn.pack(fill="x", pady=(8, 2))

        lst = ttk.Frame(top)
        lst.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self.listbox = tk.Listbox(lst, height=5, selectmode="extended")
        self.listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lst, command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=sb.set)

        # ドラッグ＆ドロップ登録（リストボックスとウィンドウ全体）
        if _HAS_DND:
            for w in (self.listbox, self):
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass

        # === 中段: オプション + 実行 ===
        opt = ttk.LabelFrame(self, text="2. 抽出オプション")
        opt.pack(fill="x", **pad)

        row1 = ttk.Frame(opt); row1.pack(fill="x", padx=6, pady=3)
        ttk.Label(row1, text="出力形式:").pack(side="left")
        self.mode_var = tk.StringVar(value="sentence")
        ttk.Radiobutton(row1, text="文ごとに改行（VOICEVOX推奨）",
                        variable=self.mode_var, value="sentence").pack(side="left", padx=4)
        ttk.Radiobutton(row1, text="元の改行を保持",
                        variable=self.mode_var, value="keep").pack(side="left", padx=4)

        row2 = ttk.Frame(opt); row2.pack(fill="x", padx=6, pady=3)
        ttk.Label(row2, text="PDF処理:").pack(side="left")
        self.pdf_var = tk.StringVar(value="auto")
        ttk.Radiobutton(row2, text="自動（テキスト層→無ければOCR）",
                        variable=self.pdf_var, value="auto").pack(side="left", padx=4)
        ttk.Radiobutton(row2, text="常にOCR（スキャン/文字化け対策）",
                        variable=self.pdf_var, value="ocr").pack(side="left", padx=4)
        ttk.Label(row2, text="  OCR解像度(DPI):").pack(side="left")
        self.dpi_var = tk.IntVar(value=300)
        ttk.Spinbox(row2, from_=150, to=400, increment=50, width=5,
                    textvariable=self.dpi_var).pack(side="left")

        row3 = ttk.Frame(opt); row3.pack(fill="x", padx=6, pady=3)
        self.pre_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="画像前処理（精度向上）", variable=self.pre_var).pack(side="left", padx=4)
        self.blank_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="空行を削除", variable=self.blank_var).pack(side="left", padx=4)
        self.ascii_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="英数字間の空白を保持", variable=self.ascii_var).pack(side="left", padx=4)
        self.join_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="改行で途切れた文を連結（小説向け）",
                        variable=self.join_var).pack(side="left", padx=4)

        run = ttk.Frame(opt); run.pack(fill="x", padx=6, pady=5)
        self.extract_btn = ttk.Button(run, text="▶ テキスト抽出 実行", command=self.start_extract)
        self.extract_btn.pack(side="left")
        self.progress = ttk.Progressbar(run, mode="determinate", length=300)
        self.progress.pack(side="left", padx=10)
        self.status_var = tk.StringVar(value="待機中")
        ttk.Label(run, textvariable=self.status_var).pack(side="left")

        # === 下段: VOICEVOX ===
        # 先に side="bottom" で確保し、ウィンドウが小さくても隠れないようにする
        bottom = ttk.LabelFrame(self, text="4. VOICEVOX へ")
        bottom.pack(side="bottom", fill="x", **pad)

        vrow1 = ttk.Frame(bottom); vrow1.pack(fill="x", padx=6, pady=3)
        ttk.Button(vrow1, text="VOICEVOX用に保存(.txt)",
                   command=self.save_txt).pack(side="left", padx=2)
        ttk.Button(vrow1, text="クリップボードにコピー",
                   command=self.copy_clip).pack(side="left", padx=2)
        self.vvproj_btn = ttk.Button(vrow1, text="プロジェクト保存(.vvproj)",
                                     command=self.save_vvproj, state="disabled")
        self.vvproj_btn.pack(side="left", padx=2)
        ttk.Separator(vrow1, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(vrow1, text="VOICEVOX起動", command=self.launch_voicevox).pack(side="left", padx=2)
        ttk.Label(vrow1, text="URL:").pack(side="left", padx=(8, 0))
        self.url_var = tk.StringVar(value=self.base_url)
        ttk.Entry(vrow1, textvariable=self.url_var, width=22).pack(side="left", padx=2)
        ttk.Button(vrow1, text="エンジン接続確認", command=self.check_engine).pack(side="left", padx=2)
        self.engine_var = tk.StringVar(value="エンジン: 未接続")
        ttk.Label(vrow1, textvariable=self.engine_var).pack(side="left", padx=8)

        vrow2 = ttk.Frame(bottom); vrow2.pack(fill="x", padx=6, pady=3)
        ttk.Label(vrow2, text="話者:").pack(side="left")
        self.speaker_cb = ttk.Combobox(vrow2, width=34, state="disabled")
        self.speaker_cb.pack(side="left", padx=4)
        ttk.Label(vrow2, text="話速:").pack(side="left", padx=(10, 0))
        self.speed_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(vrow2, from_=0.5, to=2.0, increment=0.1, width=5,
                    textvariable=self.speed_var).pack(side="left")
        self.combine_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(vrow2, text="全文を1つのWAVに結合",
                        variable=self.combine_var).pack(side="left", padx=10)
        ttk.Label(vrow2, text="文間の無音(秒):").pack(side="left", padx=(4, 0))
        self.gap_var = tk.DoubleVar(value=0.4)
        ttk.Spinbox(vrow2, from_=0.0, to=3.0, increment=0.1, width=5,
                    textvariable=self.gap_var).pack(side="left")

        vrow3 = ttk.Frame(bottom); vrow3.pack(fill="x", padx=6, pady=3)
        self.preview_btn = ttk.Button(vrow3, text="▶ 試聴(カーソル行)",
                                       command=self.preview_selected, state="disabled")
        self.preview_btn.pack(side="left", padx=4)
        self.playall_btn = ttk.Button(vrow3, text="▶▶ 連続再生(カーソル行から)",
                                      command=self.play_all, state="disabled")
        self.playall_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(vrow3, text="■ 停止",
                                   command=self.stop_playall, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.synth_btn = ttk.Button(vrow3, text="🔊 音声を生成(WAV)",
                                    command=self.start_synth, state="disabled")
        self.synth_btn.pack(side="left", padx=12)
        self.dict_btn = ttk.Button(vrow3, text="読み方辞書...",
                                   command=self.open_dict_dialog, state="disabled")
        self.dict_btn.pack(side="left", padx=4)

        # === 結果テキスト（編集可能）=== 残りスペースを埋める（最後にpack）
        mid = ttk.LabelFrame(self, text="3. 抽出結果（手動で修正できます）")
        mid.pack(fill="both", expand=True, **pad)

        # 一括置換バー
        rep = ttk.Frame(mid)
        rep.pack(fill="x", padx=4, pady=(4, 0))
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
        ttk.Separator(rep, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(rep, text="保存ルール:").pack(side="left")
        self.rule_cb = ttk.Combobox(rep, width=16, state="readonly", values=[])
        self.rule_cb.pack(side="left", padx=2)
        self.rule_cb.bind("<<ComboboxSelected>>", self._rule_selected)
        ttk.Button(rep, text="登録", width=4, command=self.add_rule).pack(side="left", padx=1)
        ttk.Button(rep, text="削除", width=4, command=self.del_rule).pack(side="left", padx=1)
        ttk.Button(rep, text="全ルール適用", command=self.apply_all_rules).pack(side="left", padx=4)

        body = ttk.Frame(mid)
        body.pack(fill="both", expand=True)
        text_opts = {"font": TEXT_FONT} if TEXT_FONT else {}
        self.text = tk.Text(body, wrap="word", undo=True, **text_opts)
        self.text.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        tsb = ttk.Scrollbar(body, command=self.text.yview)
        tsb.pack(side="right", fill="y")
        self.text.config(yscrollcommand=tsb.set)

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
        if self.busy:
            self.status_var.set("他の処理を実行中です。完了までお待ちください。")
            return
        if not self.files:
            messagebox.showinfo("情報", "先にファイルを追加してください。")
            return
        self._set_busy(True)
        self.progress.config(mode="determinate", maximum=len(self.files), value=0)
        # tkinter変数はメインスレッドでのみ読めるため、ここで全て取得して渡す
        params = dict(
            paths=list(self.files),
            pdf_mode=self.pdf_var.get(),
            dpi=self.dpi_var.get(),
            preprocess=self.pre_var.get(),
        )
        clean_opts = dict(
            mode=self.mode_var.get(),
            remove_blank=self.blank_var.get(),
            keep_ascii_spaces=self.ascii_var.get(),
            join_wrapped=self.join_var.get(),
        )
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
        if self.busy:
            self.status_var.set("他の処理を実行中です。完了までお待ちください。")
            return
        text = self.text.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("情報", "テキストがありません。")
            return
        if not self.speakers or self.speaker_cb.current() < 0:
            messagebox.showinfo("情報", "話者を選択してください（先にエンジン接続確認）。")
            return
        lines = [ln for ln in text.split("\n") if ln.strip()]
        if self.combine_var.get():
            out = filedialog.asksaveasfilename(
                title="結合WAVの保存先", defaultextension=".wav",
                filetypes=[("WAVファイル", "*.wav")], initialfile="voicevox_output.wav")
            if not out:
                return
            target = out
        else:
            d = filedialog.askdirectory(title="WAVの出力フォルダ")
            if not d:
                return
            target = d
        speaker_id = self.speakers[self.speaker_cb.current()][1]
        speed = self.speed_var.get()
        self._set_busy(True)
        self.progress.config(mode="determinate", maximum=len(lines), value=0)
        threading.Thread(target=self._synth_worker,
                         args=(lines, speaker_id, speed, target, self.combine_var.get(),
                               self.gap_var.get()),
                         daemon=True).start()

    def _synth_worker(self, lines, speaker_id, speed, target, combine, gap):
        try:
            wavs = []
            for i, ln in enumerate(lines):
                self.q.put(("progress", i, len(lines), f"音声生成中 {i+1}/{len(lines)}"))
                wb = core.vv_synthesize_one(self.base_url, ln, speaker_id, speed=speed)
                if combine:
                    wavs.append(wb)
                else:
                    fn = os.path.join(target, f"{i+1:03d}.wav")
                    with open(fn, "wb") as f:
                        f.write(wb)
            if combine:
                merged = core.concat_wavs(wavs, gap_sec=gap)
                with open(target, "wb") as f:
                    f.write(merged)
                self.q.put(("synth_done", f"結合WAVを保存しました:\n{target}"))
            else:
                self.q.put(("synth_done", f"{len(lines)}個のWAVを保存しました:\n{target}"))
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
        sp = self.speakers[self.speaker_cb.current()]
        style_id, speaker_uuid = sp[1], sp[2]
        lines = [ln for ln in text.split("\n") if ln.strip()]
        with open(out, "w", encoding="utf-8") as f:
            f.write(core.make_vvproj(lines, style_id, speaker_uuid))
        messagebox.showinfo("保存完了",
                            f"保存しました:\n{out}\n\n"
                            "VOICEVOXの「ファイル → プロジェクト読み込み」で開くと、\n"
                            "1行 = 1ブロックとして読み込まれ、行ごとに話者や\n"
                            "イントネーションを調整できます。")

    # ---------------- クリップボード画像OCR ----------------
    def clipboard_ocr(self):
        if self.busy:
            self.status_var.set("他の処理を実行中です。完了までお待ちください。")
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
                          keep_ascii_spaces=self.ascii_var.get(), join_wrapped=self.join_var.get())
        self._set_busy(True)
        self.status_var.set("クリップボード画像をOCR中...")
        threading.Thread(target=self._clipboard_worker,
                         args=(data, self.pre_var.get(), clean_opts), daemon=True).start()

    def _clipboard_worker(self, img, preprocess, clean_opts):
        try:
            tmpdir = tempfile.mkdtemp(prefix="t2v_clip_")
            png = os.path.join(tmpdir, "clip.png")
            core.preprocess_image(img, enable=preprocess).save(png)
            res = core.run_ocr([png])
            cleaned = core.clean_text(res.get(png, ""), **clean_opts)
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
        speaker_id = self.speakers[self.speaker_cb.current()][1]
        speed = self.speed_var.get()
        self._previewing = True
        self.preview_btn.config(state="disabled")
        self.status_var.set("試聴を生成中...")
        threading.Thread(target=self._preview_worker,
                         args=(line, speaker_id, speed), daemon=True).start()

    def _preview_worker(self, line, speaker_id, speed):
        try:
            wb = core.vv_synthesize_one(self.base_url, line, speaker_id, speed=speed)
            self._preview_buf = wb
            self.q.put(("preview_playing", line))
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
        # (行番号, テキスト) を集め、カーソル行以降を再生対象にする
        all_lines = self.text.get("1.0", "end-1c").split("\n")
        numbered = [(i, ln.strip()) for i, ln in enumerate(all_lines, start=1) if ln.strip()]
        if not numbered:
            self.status_var.set("再生するテキストがありません。")
            return
        cur = int(self.text.index("insert").split(".")[0])
        start = next((k for k, (no, _) in enumerate(numbered) if no >= cur), 0)
        targets = numbered[start:]
        speaker_id = self.speakers[self.speaker_cb.current()][1]
        speed = self.speed_var.get()
        self._playall_stop = threading.Event()
        self._previewing = True
        self.preview_btn.config(state="disabled")
        self.playall_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        threading.Thread(target=self._playall_worker,
                         args=(targets, speaker_id, speed), daemon=True).start()

    def stop_playall(self):
        if self._playall_stop is not None:
            self._playall_stop.set()
        self.stop_btn.config(state="disabled")
        self.status_var.set("停止しています...")

    def _playall_worker(self, targets, speaker_id, speed):
        stop = self._playall_stop
        played = 0
        try:
            for lineno, ln in targets:
                if stop.is_set():
                    break
                self.q.put(("playall_line", lineno, ln, played, len(targets)))
                wb = core.vv_synthesize_one(self.base_url, ln, speaker_id, speed=speed)
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
            self.status_var.set("保存済みのルールがありません（「登録」で追加できます）。")
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

    # ---------------- 設定の保存/復元 ----------------
    def _settings_dict(self):
        return {
            "mode": self.mode_var.get(), "pdf": self.pdf_var.get(),
            "dpi": self.dpi_var.get(), "preprocess": self.pre_var.get(),
            "blank": self.blank_var.get(), "ascii": self.ascii_var.get(),
            "join": self.join_var.get(), "combine": self.combine_var.get(),
            "speed": self.speed_var.get(), "speaker": self.speaker_cb.get(),
            "gap": self.gap_var.get(),
            "replace_rules": self.replace_rules,
            "base_url": self.url_var.get().strip() or self.base_url,
            "geometry": self.geometry(),
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
            self.combine_var.set(bool(s.get("combine", False)))
            self.speed_var.set(float(s.get("speed", 1.0)))
            self.gap_var.set(float(s.get("gap", 0.4)))
            rules = s.get("replace_rules", [])
            if isinstance(rules, list):
                self.replace_rules = [[str(x[0]), str(x[1])] for x in rules
                                      if isinstance(x, (list, tuple)) and len(x) == 2]
                self._refresh_rules()
            self._saved_speaker = s.get("speaker") or None
            if s.get("base_url"):
                self.base_url = s["base_url"]
                self.url_var.set(self.base_url)
            if s.get("geometry"):
                self.geometry(s["geometry"])
        except Exception:
            pass

    def _save_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self._settings_dict(), f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        if self._playall_stop is not None:
            self._playall_stop.set()  # 連続再生中でも即終了できるように
        self._save_settings()
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
                    self.progress.config(value=self.progress["maximum"])
                    n = len([l for l in cleaned.split("\n") if l.strip()])
                    self.status_var.set(f"抽出完了：{n}行 / {len(cleaned)}文字")
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
                        self.engine_var.set(f"エンジン: 接続OK (v{ver})")
                        self.dict_btn.config(state="normal")
                        self.vvproj_btn.config(state="normal")
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
                    _, line = msg
                    self.status_var.set(f"試聴 再生中: {line[:30]}")
                elif kind == "preview_done":
                    _, ok, info = msg
                    self._previewing = False
                    if self.speakers and not self.busy:
                        self.preview_btn.config(state="normal")
                        self.playall_btn.config(state="normal")
                    if ok:
                        self.status_var.set("試聴 完了")
                    else:
                        self.status_var.set("試聴エラー")
                        messagebox.showerror("エラー", info[-1500:])
                elif kind == "playall_line":
                    _, lineno, line, done, total = msg
                    # 再生中の行にカーソルを移してハイライト表示
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
                    self.text.tag_remove("playing", "1.0", "end")
                    self.stop_btn.config(state="disabled")
                    if self.speakers and not self.busy:
                        self.preview_btn.config(state="normal")
                        self.playall_btn.config(state="normal")
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
        elif self.speakers:
            self.synth_btn.config(state="normal")
            if not self._previewing:
                self.preview_btn.config(state="normal")
                self.playall_btn.config(state="normal")


if __name__ == "__main__":
    App().mainloop()
