# -*- coding: utf-8 -*-
"""
core.py - テキスト抽出・整形・VOICEVOX連携のコアロジック（GUI非依存）
GUIから呼び出すほか、単体テストにも使用する。
"""
import io
import os
import sys
import json
import wave
import tempfile
import subprocess

APP_DIR = os.path.dirname(os.path.abspath(__file__))
OCR_PS1 = os.path.join(APP_DIR, "ocr_win.ps1")

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
PDF_EXT = {".pdf"}

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


def clean_text(raw: str, mode: str = "sentence",
               remove_blank: bool = True, keep_ascii_spaces: bool = True,
               join_wrapped: bool = False) -> str:
    """
    抽出生テキストをVOICEVOX向けに整形する。
    mode: "sentence" = 文ごとに改行（VOICEVOX推奨）/ "keep" = 元の改行を保持
    join_wrapped: True で改行をまたいだ文を連結（小説・段落向け）。
                  既定Falseでは元の改行を文の区切りとして尊重する（構造化文書で安全）。
    """
    # 改行コード統一・全角スペース正規化の前処理
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    # 連続する3つ以上の改行は2つに
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    # 文字間スペース除去
    text = remove_cjk_spaces(text, keep_ascii_spaces=keep_ascii_spaces)

    if mode == "sentence":
        if join_wrapped:
            text = join_wrapped_lines(text)
        # split_sentences は改行も区切りとして扱うため、
        # join_wrapped=False なら元の改行は保たれる
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


def run_ocr(image_paths, lang="ja"):
    """
    画像パスのリストをOS標準のオフラインOCRに渡し、{path: text} を返す。
    Windows: Windows.Media.Ocr（PowerShellヘルパー） / macOS: Apple Vision（pyobjc）
    """
    if not image_paths:
        return {}
    if IS_WIN:
        return run_windows_ocr(image_paths, lang=lang)
    if IS_MAC:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)  # 他ディレクトリからのimportでも ocr_mac を見つける
        import ocr_mac
        return ocr_mac.recognize_files(image_paths, lang=lang)
    raise RuntimeError("この環境ではオフラインOCRを利用できません（Windows / macOS のみ対応）。")


def run_windows_ocr(image_paths, lang="ja"):
    """
    画像パスのリストをWindows標準OCRに渡し、{path: text} を返す。
    PowerShellヘルパー(ocr_win.ps1)を1回だけ起動して全件処理する。
    """
    if not image_paths:
        return {}
    tmpdir = tempfile.mkdtemp(prefix="t2v_ocr_")
    manifest = os.path.join(tmpdir, "manifest.txt")
    out_json = os.path.join(tmpdir, "result.json")
    with open(manifest, "w", encoding="utf-8") as f:
        f.write("\n".join(image_paths))

    ps_exe = _find_powershell()
    cmd = [ps_exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", OCR_PS1, "-Manifest", manifest, "-Out", out_json, "-Lang", lang]
    proc = subprocess.run(cmd, capture_output=True, text=True,
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


def _find_powershell():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    cand = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return cand if os.path.exists(cand) else "powershell"


# ============================================================
#  ファイルからの抽出（progress_cb(done, total, msg) で進捗通知）
# ============================================================
def extract_files(paths, pdf_mode="auto", dpi=300, preprocess=True,
                  lang="ja", progress_cb=None):
    """
    複数ファイル（PDF/画像）からテキストを抽出して結合文字列を返す。
    pdf_mode: "auto"（テキスト層→無ければOCR） / "ocr"（常にOCR）
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
            ocr_result = run_ocr(png_paths, lang=lang)
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


def play_wav_blocking(wav_bytes):
    """WAVバイト列を同期再生する（ワーカースレッドから呼ぶ想定）。"""
    if IS_WIN:
        import winsound
        # winsound は SND_MEMORY と SND_ASYNC を併用不可。同期再生でよい。
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
        return
    if IS_MAC:
        fd, path = tempfile.mkstemp(prefix="t2v_prev_", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav_bytes)
            subprocess.run(["/usr/bin/afplay", path], check=False)
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
    """[(label, speaker_id)] のリストを返す。"""
    import requests
    r = requests.get(base_url + "/speakers", timeout=timeout)
    r.raise_for_status()
    out = []
    for sp in r.json():
        name = sp.get("name", "")
        for st in sp.get("styles", []):
            out.append((f"{name}（{st.get('name','')}）", st.get("id")))
    return out


def vv_synthesize_one(base_url, text, speaker_id, speed=1.0, timeout=60):
    """1文を合成してWAVバイト列を返す。"""
    import requests
    q = requests.post(base_url + "/audio_query",
                      params={"text": text, "speaker": speaker_id}, timeout=timeout)
    q.raise_for_status()
    query = q.json()
    if speed and speed != 1.0:
        query["speedScale"] = float(speed)
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
