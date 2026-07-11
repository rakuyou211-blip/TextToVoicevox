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
import zipfile
import tempfile
import subprocess
from html.parser import HTMLParser
from xml.etree import ElementTree

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


def extract_epub(path: str) -> str:
    """EPUBから本文テキストを抽出する（spine順・追加ライブラリ不要）。"""
    ns_c = "{urn:oasis:names:tc:opendocument:xmlns:container}"
    ns_o = "{http://www.idpf.org/2007/opf}"
    with zipfile.ZipFile(path) as z:
        container = ElementTree.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(f".//{ns_c}rootfile")
        opf_path = rootfile.get("full-path")
        opf_dir = os.path.dirname(opf_path)
        opf = ElementTree.fromstring(z.read(opf_path))
        items = {}
        for it in opf.iter(ns_o + "item"):
            items[it.get("id")] = it.get("href")
        chapters = []
        for ref in opf.iter(ns_o + "itemref"):
            href = items.get(ref.get("idref"))
            if not href:
                continue
            full = (opf_dir + "/" + href) if opf_dir else href
            try:
                html = z.read(full).decode("utf-8", errors="replace")
            except KeyError:
                continue
            p = _HTMLTextExtractor()
            p.feed(html)
            t = p.text()
            if t:
                chapters.append(t)
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
    複数ファイル（PDF/画像/テキスト/Word/EPUB）からテキストを抽出して結合文字列を返す。
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
        proc = subprocess.run(cmd, capture_output=True, text=True,
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
