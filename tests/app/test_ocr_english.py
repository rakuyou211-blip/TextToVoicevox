# -*- coding: utf-8 -*-
"""英語まじりの画像の読み取りと、その案内の見張り。

もとは _legacy/test_english_ocr.py（45項目）と _legacy/test_english_help.py（20項目）。
同じ機能の表（読み取り）と裏（案内）なので、落ちたときに並べて読めるよう1つに
まとめました。名前は元の台本のまま残しています。

見張っているのは、2026-09-15 に RapidOCR を同梱してから増えた次の6つです。

 1. 判定 … 英文の多い画像だけを拾う。英単語だらけの日本語は拾わない
    （決め手は英字の割合ではなく**ひらがな**。技術文書は英字7割でも日本語）
 2. このPCでの本物の経路 … 英語の画像が英語で読める／警告は出ない／
    日本語の画像は読み直さない（重い部品を無駄に読み込まない）
 3. ocr_win.ps1 が「実際に使った言語・使える言語」を報告する
 4. 読み手の選び方 … Windowsの英語OCR → 同梱のRapidOCR → どちらも無ければ案内。
    読み直して良くならなければ日本語の結果のまま。RapidOCRが壊れていても、
    崩れた結果を黙って返さない（以前はこれで黙って崩れた文が出ていた）
 5. RapidOCR の行の組み立てと、英語で読み直した結果を採る決め方
 6. アプリの中の案内（「英語の画像は？」の窓）と、警告からそこへ回る道

元の台本は本人のスクリーンショット（Pictures\\Screenshots\\…）を直に読んでいたため、
他のPCでは再現できませんでした。ここでは種データ（seed fixture）と、そのとき実際に
化けた文字列を写し取ったもの（GARBLED）に置き換えています。

元の台本に無い確認が3本あります。前処理で2倍に拡大される枝（長辺1600px未満）を
通る分が2本と、透過(RGBA)の画像を本物の経路で通す分が1本（元の台本では
run_rapidocr を直に呼んでいた枝を、extract_files から通す形に移したもの）です。
拡大した写しをRapidOCRに渡すと本文に無い文字が混ざることは実測でも出ますが、
紙しだいで助けにも害にもなる割り切りなので（下の「拡大される枝」の節に実測を
残しました）、本体の不具合とは書かず、そこは assert していません。
"""
import glob
import os

import pytest

import core
# 画面（tkinter）が無い環境では、この1本ごと飛ばす。CI の Linux には python3-tk が
# 入っていないので、ここで素の import をすると「集める」段階でCIごと倒れる（2026-09-24 実測）。
main = pytest.importorskip("main")
from _seed import EN_LINES


# macOS の Vision は日本語と英語を一緒に読むので、この問題はそもそも起きない。
requires_windows = pytest.mark.skipif(
    not core.IS_WIN, reason="Windowsの読み取り経路だけの話")

# 英語の読み取り部品（RapidOCR）は、欲しい人だけが入れる約240MBの追加物。
# 入っていないPCでは「同梱の英語OCRで読む」確認そのものが成り立たないので飛ばす。
requires_rapidocr = pytest.mark.skipif(
    not core.rapidocr_available(),
    reason="同梱の英語OCR（rapidocr_onnxruntime）が入っていない")


# 日本語OCRが英文を化かした実物（本人の英語ダイアログを読んだ結果を写し取ったもの）。
# NOT→NO丁、files→創es のように英字が漢字に化ける。英字の割合は 0.79 まで上がるのに
# ひらがなは 0.02 しか無い、というのがこの手の結果の特徴。
GARBLED = "物し「decoy創es are NO丁匯i叩watched「ight now. Resta代 it with 「i pt.exe"
CLEAN = ("Your decoy files are NOT being watched right now. "
         "Restart it with wscript.exe")
NIHONGO = "吾輩は猫である。名前はまだ無い。どこで生れたかとんと見当がつかぬ。"


def kanji_garbage(text):
    """日本語OCRが英文を化かしたときに出る漢字の数（英文なら0に近いはず）。"""
    return sum(1 for c in text if "一" <= c <= "鿿")


def texts(widget):
    """その窓の中の文字を全部集める。"""
    out = []
    for child in widget.winfo_children():
        try:
            t = child.cget("text")
            if t:
                out.append(str(t))
        except Exception:
            pass
        out.extend(texts(child))
    return out


def box(x0, x1, y0, y1):
    """RapidOCR が返す四隅の座標（左上から時計回り）。"""
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def help_alive(app):
    """案内の窓がいま開いているか。
    一度も開いていない画面には属性そのものが無いので、getattr で受ける。"""
    win = getattr(app, "_en_help_win", None)
    try:
        return win is not None and bool(win.winfo_exists())
    except Exception:
        return False


def ui_screenshot():
    """日本語のアプリ画面（docs/*.png）。

    以前は「無ければ skip」だったが、docs/*.png は README が貼っている配布物の
    一部で、無いのは環境の違いではなくリポジトリの欠落。skip のままだと、
    docs/ ごと消えても2本が無言で減るだけで誰も気づけないので、落とす。"""
    found = sorted(glob.glob(os.path.join(core.APP_DIR, "docs", "*.png")))
    assert found, ("docs/*.png が見つかりません（README が貼っている画面の写しで、"
                   "配布物に入っているはずのものです）")
    return found[0]


# ================================================================ (1) 判定
# 英字の割合だけで判定すると、技術文書（GitHub / Pull Request / merge …）が
# 英文の画像として誤って拾われ、日本語の本文が英語OCRで潰される。
# しきい値は下の5種類の画像で実測して決めたものなので、ここが動いたら疑う。
@pytest.fixture(scope="module")
def raw_ja_ocr(seed):
    """日本語OCRの生の結果を、5種類の画像ぶん1回だけ取る。

    判定そのものを見たいので、英語での読み直し（_english_second_pass）は
    止めておく。読み直した結果で判定すると、判定が甘いのか読み直しが効いた
    のか区別できない。"""
    if not core.IS_WIN:
        pytest.skip("WindowsのOCRを直に呼ぶ")
    paths = {"jp": seed.japanese(), "mix": seed.mixed(),
             "tech": seed.technical(), "en": seed.english(),
             "ui": ui_screenshot()}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(core, "_english_second_pass", lambda *a, **k: None)
        raw = core.run_ocr(list(paths.values()), lang="ja", strip_labels=False)
    return {name: raw.get(path, "") for name, path in paths.items()}


@requires_windows
@pytest.mark.slow
def test_日本語だけの画像は日本語扱い(raw_ja_ocr):
    assert core.ocr_looks_english(raw_ja_ocr["jp"]) is False, \
        "日本語だけの画像を英語と判定した"


@requires_windows
@pytest.mark.slow
def test_英単語まじりの日本語は日本語扱い(raw_ja_ocr):
    """iPhone / Wi-Fi / Settings が混ざっても、助詞と語尾でひらがなが残る。"""
    assert core.ocr_looks_english(raw_ja_ocr["mix"]) is False, \
        "英単語まじりの日本語を英語と判定した"


@requires_windows
@pytest.mark.slow
def test_技術文書_英語多め_は日本語扱い(raw_ja_ocr):
    """英字の割合は 0.68 まで行く。英字だけで判定すると誤って引っかかる境目。"""
    assert core.ocr_looks_english(raw_ja_ocr["tech"]) is False, \
        "技術文書（英語多めの日本語）を英語と判定した"


@requires_windows
@pytest.mark.slow
def test_日本語のアプリ画面は日本語扱い(raw_ja_ocr):
    assert core.ocr_looks_english(raw_ja_ocr["ui"]) is False, \
        "日本語のアプリ画面を英語と判定した"


@requires_windows
@pytest.mark.slow
def test_英語だけの画像は英語と見なす(raw_ja_ocr):
    assert core.ocr_looks_english(raw_ja_ocr["en"]) is True, \
        "英語だけの画像を拾えていない（読み直しが始まらない）"


def test_英語のダイアログの読み取り結果も英語と見なす():
    """実物の英語ダイアログを日本語OCRで読んだ結果（GARBLED）。
    化けて漢字が混ざっていても、ひらがなが無いので英語と分かる。"""
    assert core.ocr_looks_english(GARBLED) is True, \
        "化けた英文を拾えていない（崩れた結果がそのまま出てしまう）"


def test_短すぎる断片は判定しない():
    """ボタンのラベルだけのような短い断片で読み直しを始めない（20文字未満）。"""
    assert core.ocr_looks_english("OK Cancel") is False, \
        "短すぎる断片で英語と判定した"


# ================================================ (2) このPCで本物の経路を通す
# ここだけは模擬ではなく、Windowsの日本語OCR → 同梱のRapidOCR という本当の道を
# 通す。模擬だけにすると、つなぎ目（前処理した一時コピーを渡している等）の
# 取り違えに気づけない。
#
# 読ませるのは横1800pxに描いた英文の画像。前処理の「長辺1600px未満なら2倍に
# 拡大する」枝を**通らない**大きさなので、ここでは拡大なしの道を踏む。
# （ここには「元の台本が読んでいた本人のスクリーンショットと同じくらいの大きさ」と
#  書いてあったが、実物は 1150x745 で、必ず拡大される側だった。2026-09-23 訂正。
#  拡大される側は下の「拡大される枝」の節で別に踏む）
# 判定（1）のしきい値は横1400pxで測ったものなので、あちらの画像は変えていない。
SHOT_WIDTH = 1800


def english_shot(seed):
    """拡大される枝を通らない大きさ（長辺1600px以上）の、英文だけの画像。"""
    return seed.image("en_shot.png", EN_LINES, width=SHOT_WIDTH)


def read_with_spy(path, **kw):
    """本物の経路（extract_files）で1枚読む。

    返すのは (本文, 警告, RapidOCRに回った画像の名前, RapidOCRが返した文字)。
    4つ目があると「英語で読み直した結果がそのまま本文になったか」を、読み取りの
    中身を当てにせず確かめられる（採られなければ本文は日本語OCRの結果のままで、
    2つは一致しない）。偽物は **kw で余りを受ける。"""
    reread, rapid_text = [], []
    with pytest.MonkeyPatch.context() as mp:
        real = core.run_rapidocr

        def spy(image_paths, strip_labels=True, **kw2):
            reread.extend(os.path.basename(p) for p in image_paths)
            out = real(image_paths, strip_labels=strip_labels, **kw2)
            rapid_text.extend(out.get(p, "") for p in image_paths)
            return out
        mp.setattr(core, "run_rapidocr", spy)
        opts = dict(pdf_mode="auto", dpi=300, preprocess=True,
                    fix_confusables=True, denoise=True)
        opts.update(kw)
        text, warnings = core.extract_files([path], **opts)
    return text, warnings, reread, "\n".join(rapid_text)


@pytest.fixture
def rapid_spy(monkeypatch):
    """RapidOCR に回った画像の名前を覚える（読むのは本物のまま）。
    余りの引数は **kw で受ける（本体の引数が増えたときに黙って通らないように）。"""
    seen = []
    real = core.run_rapidocr

    def spy(image_paths, strip_labels=True, **kw):
        seen.extend(os.path.basename(p) for p in image_paths)
        return real(image_paths, strip_labels=strip_labels, **kw)
    monkeypatch.setattr(core, "run_rapidocr", spy)
    return seen


@pytest.fixture(scope="module")
def english_read(seed):
    """英語の画像を、本物の経路（extract_files）で1回だけ読む。
    返すのは (本文, 警告, RapidOCRに回った画像の名前)。"""
    if not core.IS_WIN:
        pytest.skip("WindowsのOCR経路だけの話")
    if not core.rapidocr_available():
        pytest.skip("同梱の英語OCR（rapidocr_onnxruntime）が入っていない")
    text, warnings, reread, _rapid = read_with_spy(english_shot(seed))
    return text, warnings, reread


@requires_rapidocr
@pytest.mark.slow
def test_RapidOCRは入っているだけでなく本当に読み込める():
    """元は「入っている」を assert していたが、飛ばす条件（rapidocr_available）と
    同じ述語だったので、どう転んでも通る確認になっていた。

    rapidocr_available() が見ているのは find_spec、つまり「置いてあるか」だけ。
    置いてあっても読み込めないPC（DLLが足りない等）はあり、本体はそのとき
    案内に回る側へ落ちる。ここで先に落としておけば、下の一連の失敗を
    「読み込めていない」1点に絞れる。初回は読み込みに15秒ほどかかる。"""
    try:
        engine = core._rapidocr_engine()
    except Exception as exc:
        pytest.fail("入っているのに読み込めない（%s: %s）。"
                    "本体はこのとき英語で読み直さず案内に回る"
                    % (type(exc).__name__, exc))
    assert engine is not None, "読み込めたのに読み手が返ってこない"


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_英語の画像を英語で読み直した(english_read):
    """extract_files は前処理した一時コピー（img_0.png など）をOCRに渡すので、
    元のファイル名ではなく「1枚だけ読み直した」ことで見る。"""
    _text, _warnings, reread = english_read
    assert len(reread) == 1, "読み直した枚数がおかしい（%s）" % reread


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_本文の英文が読める(english_read):
    low = english_read[0].lower()
    assert "restart it with" in low and "log files" in low, \
        "英文の行が読めていない: %s" % low[:120]


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_filesareとbeingwatchedも読める(english_read):
    """日本語OCRだと「創es are」「i叩watched」に化ける並び。"""
    low = english_read[0].lower()
    assert "files are" in low and "being watched" in low, \
        "化けやすい並びが読めていない: %s" % low[:120]


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_漢字に化けていない(english_read):
    text = english_read[0]
    assert kanji_garbage(text) <= 2, \
        "英文が漢字に化けている（%d字）" % kanji_garbage(text)


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_入っているので部品が無いとは言わない(english_read):
    assert core.OCR_ENGLISH_MISSING_MSG not in english_read[1], \
        "部品が入っているのに「入っていません」の案内が出た"


# ------------------------------------------------ 拡大される枝（長辺1600px未満）
# 本体の前処理は、長辺が1600px未満の画像を2倍に拡大してから（Windowsでは
# グレースケール化＋輪郭強調も掛けてから）OCRに渡す。上の english_read は
# 横1800px＝この枝を通らないので、通る側をここで踏む。
#
# 2026-09-23 実測（前処理 → RapidOCR。この機体）:
#   強調あり（＝本体が通す道）
#     種データ1400px → "…are NOT being I watched…" / "the log 1f files"
#                       と、本文に無い文字が行の切れ目に混ざる
#     種データ1800px → 本文どおりだが "NOT" が "NoT" になる（拡大枝は通らない）
#     実物のスクリーンショット1150x745 → 正しく読める（円マークまで正しい）
#   強調なし
#     種データ1400px・1800px → きれいに読める
#     実物のスクリーンショット → "NoT"、円マークが "?" に化ける
# （実物のスクリーンショットの行は本人の私物での実測。種データの行はここで再現できる）
# つまり強調は、紙しだいで助けにも害にもなる。以前ここには
# 「強調した写しを RapidOCR に渡すのは本体の不具合」と書いて xfail にしてあったが、
# 上のとおり本体の割り切りであって不具合とは言えない（xfail は不具合が確定して
# いるときだけ）。読み取りの中身は本体が保証していないので assert にはせず、
# 本体が保証していること（英語と判定される・英語の結果が採られる）だけを見る。
@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_小さめの英語の画像は拡大されても英語として通る(seed, tmp_path):
    """横1400pxの英語の画像。本体が通す2つの手順（前処理 → RapidOCR）をそのまま踏む。"""
    from PIL import Image
    small = tmp_path / "前処理後.png"
    with Image.open(seed.english()) as img:
        before = max(img.size)
        processed = core.preprocess_image(img, enable=True)
        after = max(processed.size)
        processed.save(str(small))
    # 種データの大きさが変わって、いつの間にかこの枝を踏まなくなっていないか
    assert before < 1600 and after > before, \
        "拡大される枝を通っていない（長辺 %d→%d。1600px未満なら2倍のはず）" % (
            before, after)
    got = core.run_rapidocr([str(small)]).get(str(small), "")
    assert core.ocr_looks_english(got) is True, \
        "拡大した写しの読み取りが英語と判定されない（%r）" % got[:100]
    # 日本語OCRが化かした結果（GARBLED）と並べたとき、採られる側であること。
    # 拡大や強調で中身が大きく欠けたり、化けの印が増えたりすればここで落ちる。
    assert core._english_ocr_better(GARBLED, got), \
        "日本語OCRの化けた結果に負けて捨てられる読み取りになった（%r）" % got[:100]


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_拡大される枝でも英語の結果が採られる(seed):
    """同じ横1400pxを、本物の経路（extract_files）で通す。

    OCRのあとの補正（fix_confusables / denoise）は掛けない。本文と読み直した
    結果をそのまま比べたいだけで、補正そのものは別の持ち場なので。"""
    text, warnings, reread, rapid = read_with_spy(
        seed.english(), fix_confusables=False, denoise=False)
    assert len(reread) == 1, "英語と判定されず、読み直しに回らなかった（%s）" % reread
    assert rapid.strip(), "読み直しに回ったのにRapidOCRが何も返していない"
    assert "".join(text.split()) == "".join(rapid.split()), \
        "英語で読み直した結果が採られていない（本文=%r / 読み直し=%r）" % (
            text[:100], rapid[:100])
    assert core.OCR_ENGLISH_MISSING_MSG not in warnings, \
        "部品が入っているのに「入っていません」の案内が出た"


@requires_windows
@requires_rapidocr
@pytest.mark.slow
def test_透過PNGでも英語の結果が採られる(seed):
    """透過（RGBA）の画像を extract_files から通す枝。元の台本では run_rapidocr を
    直に呼んでいて、移すときに落ちていた。

    ここで踏みたいのは入口のほう。本体は透過を白に合成してからPNGに書き出して
    渡す（そのまま渡すとWin/Macとも透過部が黒く潰れる、というのが本体側の言い分）。
    読み取りの中身は上と同じ画になるので、見るのは「詰まらずに通って、英語の結果が
    そのまま本文になる」ことだけ。"""
    text, warnings, reread, rapid = read_with_spy(
        seed.english(name="en_rgba.png", mode="RGBA"),
        fix_confusables=False, denoise=False)
    assert not warnings, "透過の画像で警告が出た（%s）" % warnings
    assert len(reread) == 1, \
        "透過の画像が英語と判定されず、読み直しに回らなかった（%s）" % reread
    assert rapid.strip(), "読み直しに回ったのにRapidOCRが何も返していない"
    assert "".join(text.split()) == "".join(rapid.split()), \
        "透過の画像で英語の結果が採られていない（本文=%r / 読み直し=%r）" % (
            text[:100], rapid[:100])


@requires_windows
@pytest.mark.slow
def test_日本語のアプリ画面は読み直さない(rapid_spy):
    """重いRapidOCRを、日本語の画像で読み込んでしまわないこと。"""
    _text, warnings = core.extract_files([ui_screenshot()], pdf_mode="auto",
                                         dpi=300, preprocess=True)
    assert not rapid_spy, "日本語の画像をRapidOCRに回した（%s）" % rapid_spy
    assert core.OCR_ENGLISH_MISSING_MSG not in warnings, \
        "日本語の画像で英語の案内が出た"


@requires_windows
@pytest.mark.slow
def test_技術文書は読み直さない(seed, rapid_spy):
    _text, warnings = core.extract_files([seed.technical()], pdf_mode="auto",
                                         dpi=300, preprocess=True)
    assert not rapid_spy, "技術文書をRapidOCRに回した（%s）" % rapid_spy
    assert core.OCR_ENGLISH_MISSING_MSG not in warnings, \
        "技術文書で英語の案内が出た"


# ======================================== (3) ocr_win.ps1 が言語を報告するか
# 以前は「英語で読んで」と頼んでも、英語が入っていないWindowsは黙って日本語で
# 読み、崩れた結果を返していた。ps1 が「実際に使った言語」と「使える言語」を
# 報告するようになったので、呼ぶ側が読み手を選べる。
@pytest.fixture(scope="module")
def ps1_meta(seed):
    """英語を頼んで ocr_win.ps1 を1回呼び、その報告（meta）を返す。"""
    if not core.IS_WIN:
        pytest.skip("ocr_win.ps1 はWindowsだけ")
    meta = {}
    core._run_windows_ocr_chunk([seed.japanese()], lang="en-US", meta=meta)
    return meta


@requires_windows
def test_英語を頼んでも実際に使ったのは日本語_と分かる(ps1_meta):
    assert ps1_meta.get("engine_lang", "").lower().startswith("ja"), \
        "使った言語の報告が無い、または日本語ではない（%s）" % ps1_meta


@requires_windows
def test_使える言語の一覧が届く(ps1_meta):
    """このPCのWindowsには日本語だけが入っている。
    ここが ["ja"] でなくなったら、Windowsに英語（米国）が追加された印
    （そのときは Windows 側の英語OCRが使われる経路に入る）。"""
    assert ps1_meta.get("available_langs") == ["ja"], \
        "使える言語の一覧がおかしい（%s）" % ps1_meta


# ================================================== (4) 読み手の選び方（模擬）
# Windowsの英語OCR → 同梱のRapidOCR → どちらも無ければ案内、の順に落ちること。
# 本物のOCRだと「英語が入っていないPC」を作れないので、ここは模擬で見る。
class _FakeReaders:
    """Windows の OCR と RapidOCR をまとめて偽物に差し替える道具。

    偽物はどれも **kw で余りを受ける。本体の引数が増えたときに、偽物が
    黙って通ってしまう事故が過去に3回あった（run_ocr の notices、
    run_rapidocr の cancel_event と progress_cb）。"""

    def __init__(self, monkeypatch):
        self._mp = monkeypatch
        self.calls = []            # (渡した画像, 頼んだ言語)
        self.rapid_calls = []      # RapidOCR に回った画像

    def windows(self, langs=("ja",)):
        """Windows側のOCR。langs=None は言語を報告しない古い ocr_win.ps1。
        日本語で頼まれたら、名前に en が入る画像だけ化けた英文を返す。"""
        def fake_chunk(image_paths, lang="ja", strip_labels=True,
                       errors=None, meta=None, **kw):
            self.calls.append((tuple(image_paths), lang))
            if meta is not None and langs is not None:
                meta["available_langs"] = list(langs)
            if lang == "ja":
                return {p: (GARBLED if "en" in os.path.basename(p) else NIHONGO)
                        for p in image_paths}
            return {p: CLEAN for p in image_paths}
        self._mp.setattr(core, "_run_windows_ocr_chunk", fake_chunk)
        return self

    def rapidocr(self, text=CLEAN):
        """同梱のRapidOCRが使える状態にする（text="" は何も読めなかったとき）。"""
        def fake_rapid(image_paths, strip_labels=True, **kw):
            self.rapid_calls.append(tuple(image_paths))
            return {p: text for p in image_paths}
        # rapidocr_available と _rapidocr_engine も **kw で受ける。本体が引数を
        # 増やしたとき、引数を取らない偽物は TypeError で落ちるが、本体は
        # _rapidocr_engine() を try/except で包んでいるのでそれを握りつぶし、
        # 「案内に回る」道へ静かに落ちる＝下の確認が別の理由で通ってしまう。
        self._mp.setattr(core, "rapidocr_available", lambda *a, **kw: True)
        self._mp.setattr(core, "_rapidocr_engine", lambda *a, **kw: object())
        self._mp.setattr(core, "run_rapidocr", fake_rapid)
        return self

    def no_rapidocr(self):
        """入っていないPC。"""
        self._mp.setattr(core, "rapidocr_available", lambda *a, **kw: False)
        return self

    def broken_rapidocr(self):
        """入っているのに読み込めない（DLLが足りない等）。"""
        def broken(*a, **kw):
            raise ImportError("DLL load failed")
        self._mp.setattr(core, "rapidocr_available", lambda *a, **kw: True)
        self._mp.setattr(core, "_rapidocr_engine", broken)
        return self


@pytest.fixture
def readers(monkeypatch):
    return _FakeReaders(monkeypatch)


def test_Windowsに英語があればそれで読み直す(readers):
    readers.windows(("ja", "en-US")).rapidocr()
    res = core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=[])
    assert res["a_en.png"] == CLEAN, "英語で読み直した結果になっていない"
    assert [c for c in readers.calls if c[1] != "ja"] \
        == [(("a_en.png",), "en-US")], \
        "英語で頼み直した相手が違う（%s）" % readers.calls


def test_そのときRapidOCRは使わない(readers):
    """Windowsに英語があるなら、重い部品を読み込む理由が無い。"""
    readers.windows(("ja", "en-US")).rapidocr()
    core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=[])
    assert not readers.rapid_calls, \
        "Windowsの英語で読めるのにRapidOCRを使った（%s）" % readers.rapid_calls


def test_日本語の画像はそのまま(readers):
    readers.windows(("ja", "en-US")).rapidocr()
    res = core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=[])
    assert res["b_jp.png"] == NIHONGO, "日本語の画像の結果が差し替わった"


def test_Windowsが日本語だけならRapidOCRで読み直す(readers):
    readers.windows(("ja",)).rapidocr()
    res = core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=[])
    assert res["a_en.png"] == CLEAN, "RapidOCRの結果が採られていない"
    assert readers.rapid_calls == [("a_en.png",)], \
        "RapidOCRに回した画像が違う（%s）" % readers.rapid_calls


def test_日本語の画像はRapidOCRに回さない(readers):
    readers.windows(("ja",)).rapidocr()
    res = core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=[])
    assert ("b_jp.png",) not in readers.rapid_calls, \
        "日本語の画像をRapidOCRに回した（%s）" % readers.rapid_calls
    assert res["b_jp.png"] == NIHONGO, "日本語の画像の結果が差し替わった"


def test_警告は出さず英語で読んだことを記録(readers):
    readers.windows(("ja",)).rapidocr()
    notes = []
    core.run_windows_ocr(["a_en.png", "b_jp.png"], notices=notes)
    assert "english_ocr_missing" not in notes, \
        "読めているのに「部品が無い」と知らせた（%s）" % notes
    assert "english_ocr_used" in notes, \
        "英語で読み直したことが記録されない（%s）" % notes


def test_読み直しても読めなければ元の結果を捨てない(readers):
    """英語で読んで空になったからといって、日本語の結果を捨てたら本文が消える。"""
    readers.windows(("ja",)).rapidocr(text="")
    res = core.run_windows_ocr(["a_en.png"], notices=[])
    assert res["a_en.png"] == GARBLED, "読めなかったのに元の結果を捨てた"


def test_どちらも無ければ案内する(readers):
    readers.windows(("ja",)).no_rapidocr()
    notes = []
    core.run_windows_ocr(["a_en.png"], notices=notes)
    assert "english_ocr_missing" in notes, \
        "読めないのに黙って崩れた結果を返した（%s）" % notes


def test_RapidOCRが壊れていたら案内に回す(readers):
    """入っているのに読み込めないPC（DLL不足など）。
    ここで黙ると、崩れた英文がそのまま本文になる。"""
    readers.windows(("ja",)).broken_rapidocr()
    notes = []
    core.run_windows_ocr(["a_en.png"], notices=notes)
    assert "english_ocr_missing" in notes, \
        "読み込みに失敗したのに知らせない（%s）" % notes


def test_古いps1でも安全側_案内する(readers):
    """言語を報告しない古い ocr_win.ps1。使える言語が分からないときは、
    「英語がある」と決めてしまわない。"""
    readers.windows(None).no_rapidocr()
    notes = []
    core.run_windows_ocr(["a_en.png"], notices=notes)
    assert "english_ocr_missing" in notes, \
        "言語の報告が無いのに読めるつもりで進んだ（%s）" % notes


def test_noticesを渡さない呼び出しでも落ちない(readers):
    """cli からは notices を渡さない。None に append しようとして落ちないこと。"""
    readers.windows(None).no_rapidocr()
    assert core.run_windows_ocr(["a_en.png"]) is not None, \
        "notices 無しの呼び出しで結果が返らない"


# ============================================ (5) RapidOCRの行の組み立て
# RapidOCR は1行を「Your decoy f」「files are」のように横に分けて返すことがある。
# 縦の位置が重なる断片を左から順につなぎ、境目で二重になった書きかけの単語を捨てる。
@pytest.fixture
def split_line():
    """横に割れて返ってきた1行＋その下の行。順番はわざとばらばらにしてある。"""
    return [
        [box(300, 520, 20, 60), "files are", 0.9],      # 同じ行の右側（先に来る）
        [box(10, 330, 22, 58), "Your decoy f", 0.98],   # 右端が次の断片に重なる
        [box(10, 400, 90, 130), "Restart it with:", 0.99],
    ]


def test_横に割れた断片を1行につなぐ(split_line):
    lines = core._rapidocr_lines(split_line, 1000, 200)
    assert len(lines) == 2, "行数がおかしい（%s）" % [l["text"] for l in lines]


def test_重なった書きかけの単語を捨てる(split_line):
    lines = core._rapidocr_lines(split_line, 1000, 200)
    assert lines[0]["text"] == "Your decoy files are", \
        "書きかけの f が残った（%s）" % lines[0]["text"]


def test_上から順に並ぶ(split_line):
    lines = core._rapidocr_lines(split_line, 1000, 200)
    assert lines[1]["text"] == "Restart it with:", \
        "行の順番が入れ替わった（%s）" % [l["text"] for l in lines]


def test_座標は0から1に正規化されている(split_line):
    lines = core._rapidocr_lines(split_line, 1000, 200)
    assert all(0 <= l[k] <= 1 for l in lines
               for k in ("x0", "x1", "y0", "y1")), \
        "座標が画素のまま（ラベル除去の判定が狂う）: %s" % lines


def test_重なっていない別の単語は捨てない():
    """Check と Checklist のように、前の単語で始まる別の単語を消さないこと。"""
    lines = core._rapidocr_lines(
        [[box(10, 100, 0, 40), "Check", 1.0],
         [box(120, 300, 0, 40), "Checklist", 1.0]], 1000, 100)
    assert [l["text"] for l in lines] == ["Check Checklist"], \
        "重なっていない単語を捨てた（%s）" % [l["text"] for l in lines]


# ==================================== (5b) 英語で読み直した結果を採る決め方
# 英字の数だけで比べると、化けた結果が英字をたくさん含んでいるとき、少し欠けた
# きれいな英文が負けて捨てられる（化け44字 / きれいな英文40字で実際に起きた）。
# 決め手は「化けの印（漢字・かな・全角）が減ったか」。
G44 = GARBLED
C40 = "Your decoy files are NOT being watched right now."


def test_化けた結果より少し欠けたきれいな英文を採る():
    assert core._english_ocr_better(G44, C40), \
        "英字の数で比べて、きれいな英文を捨てている"


def test_何も読めなかったら採らない():
    assert not core._english_ocr_better(G44, ""), "空の結果を採ってしまう"


def test_化けの印が増えるなら採らない():
    assert not core._english_ocr_better(G44, C40 + " " + "漢字" * 15), \
        "もっと化けた結果を採ってしまう"


def test_英字が7割未満しか残らないなら採らない():
    """英語で読み直すと改行や記号は少し落ちるが、本文まで欠けるのは別の話。"""
    assert not core._english_ocr_better(G44, "Your decoy"), \
        "ほとんど欠けた結果を採ってしまう"


def test_日本語OCRが化けていなければ英字の多い方():
    assert not core._english_ocr_better(C40, "Your decoy files"), \
        "化けていない結果を、短い結果で上書きしている"


# ==================================== (6) クリップボードOCRと案内の文面
# 以前は、そっけない警告の窓で文章を読ませて終わりだった。2026-09-16 から、
# 直し方まで出す案内の窓に回している。
@pytest.fixture
def opened(monkeypatch):
    """os.startfile を止めて、開こうとした先だけ覚える（実際には何も開かない）。
    raising=False は macOS 向け（os.startfile が無い。戻すのは monkeypatch）。"""
    seen = []
    monkeypatch.setattr(os, "startfile",
                        lambda path, *a, **kw: seen.append(str(path)),
                        raising=False)
    return seen


@pytest.fixture
def warned(monkeypatch):
    """そっけない警告の窓を止めて、その文だけ覚える。"""
    seen = []
    monkeypatch.setattr(main.messagebox, "showwarning",
                        lambda title, msg, **kw: seen.append(msg))
    return seen


@pytest.fixture
def en_help(app):
    """案内の窓を開ける画面。開いたままの窓は、テストのあとで畳む。"""
    yield app
    win = getattr(app, "_en_help_win", None)
    if win is not None:
        try:
            if win.winfo_exists():
                win.destroy()
        except Exception:
            pass


@pytest.mark.gui
def test_部品が使えないときは案内の窓を開く(en_help, warned):
    en_help._dispatch_msg(("clip_done", "テスト", {},
                           [core.OCR_ENGLISH_MISSING_MSG]))
    assert help_alive(en_help), "クリップボードOCRから案内の窓に回らない"
    assert not warned, "そっけない警告の窓で終わっている（%s）" % warned


@pytest.mark.gui
def test_何もなければ警告も案内も出さない(en_help, warned):
    en_help._dispatch_msg(("clip_done", "テスト", {}, []))
    assert not warned, "知らせが無いのに警告が出た（%s）" % warned
    assert not help_alive(en_help), "知らせが無いのに案内の窓が開いた"


def test_案内に英語OCRを入れるbatの道が書いてある():
    assert "英語OCRを入れる.bat" in core.OCR_ENGLISH_MISSING_MSG, \
        "案内に .bat の道が無い"


def test_案内にWindowsへ英語を足す道も書いてある():
    """こちらはアプリが大きくならない道。先に書いておきたい。"""
    msg = core.OCR_ENGLISH_MISSING_MSG
    assert "英語（米国）" in msg and "アプリは大きくなりません" in msg, \
        "Windowsに英語を足す道が案内に無い"


def test_setupbatを実行させる古い案内は残っていない():
    assert "setup.bat" not in core.OCR_ENGLISH_MISSING_MSG, \
        "存在しない setup.bat を案内している"


# ==================================== (7) アプリの中の案内（「英語の画像は？」）
# もとは _legacy/test_english_help.py。窓は何もインストールしない。
# 実行するかどうかは本人が決める、という作りを崩さないこと。
@pytest.mark.gui
def test_英語の画像はボタンがある(en_help, opened):
    assert str(en_help._en_btn.cget("text")) == "英語の画像は？", \
        "常設の入口のラベルが変わった（%s）" % en_help._en_btn.cget("text")
    assert not opened, "押す前に何かを開こうとした（%s）" % opened


@pytest.fixture
def installed(monkeypatch):
    """部品が入っているPCとして案内を出させる。
    本物の有無に任せると、入っていないPCでこの文面を確かめられない。"""
    monkeypatch.setattr(core, "rapidocr_available", lambda *a, **kw: True)


@pytest.fixture
def not_installed(monkeypatch):
    """部品が入っていないPCとして案内を出させる（このPCには入っているので模擬）。"""
    monkeypatch.setattr(core, "rapidocr_available", lambda *a, **kw: False)


@requires_windows
@pytest.mark.gui
def test_部品が入っているときの案内文(en_help, installed):
    en_help.open_english_ocr_help()
    assert help_alive(en_help), "説明の窓が開かない"
    body = " ".join(texts(en_help._en_help_win))
    assert "入っています" in body, "入っていると分かる文が無い"
    assert "日本語だけ" in body and "NO丁" in body, "崩れる理由が書いていない"
    assert "英語（米国）" in body and "英語OCRを入れる.bat" in body, \
        "2つの方法が書いていない"
    assert "240MB" in body and "88MB" in body, "大きさの目安が書いていない"


@requires_windows
@pytest.mark.gui
def test_入っているので入れるボタンは出さない(en_help, installed):
    en_help.open_english_ocr_help()
    assert "install" not in en_help._en_help_buttons, \
        "入っているのに「入れる」ボタンを出している"
    assert "settings" in en_help._en_help_buttons, \
        "Windowsの言語設定を開くボタンが無い"


@requires_windows
@pytest.mark.gui
def test_言語設定を開こうとする(en_help, installed, opened):
    en_help.open_english_ocr_help()
    en_help._en_help_buttons["settings"].invoke()
    assert opened == ["ms-settings:regionlanguage"], \
        "言語設定ではないものを開こうとした（%s）" % opened


@requires_windows
@pytest.mark.gui
def test_部品が入っていないときの案内文(en_help, not_installed):
    en_help.open_english_ocr_help(reason=True)
    body = " ".join(texts(en_help._en_help_win))
    assert "入っていません" in body, "入っていないと分かる文が無い"
    assert "英語の文が多く" in body, \
        "「いま読んだ画像が英文だった」の前置きが無い"
    assert "install" in en_help._en_help_buttons, "「入れる」ボタンが出ない"


@requires_windows
@pytest.mark.gui
def test_英語OCRを入れるbatを開こうとする(en_help, not_installed, opened):
    en_help.open_english_ocr_help(reason=True)
    en_help._en_help_buttons["install"].invoke()
    assert len(opened) == 1 and opened[0].endswith("英語OCRを入れる.bat"), \
        "開こうとした先が違う（%s）" % [os.path.basename(p) for p in opened]
    assert os.path.exists(opened[0]), "案内している .bat が実在しない"


@requires_windows
@pytest.mark.gui
def test_英語の件だけなら素っ気ない警告は出さない(en_help, not_installed, warned):
    en_help._warn_with_english([core.OCR_ENGLISH_MISSING_MSG])
    assert not warned, "文章を読ませて終わりにしている（%s）" % warned
    assert help_alive(en_help), "代わりの案内の窓が開かない"


@requires_windows
@pytest.mark.gui
def test_他の警告はこれまでどおり出す(en_help, not_installed, warned):
    en_help._warn_with_english([core.OCR_ENGLISH_MISSING_MSG,
                                "OCR失敗 なにか.png: 理由"])
    assert warned == ["OCR失敗 なにか.png: 理由"], \
        "他の警告が出ない、または混ざった（%s）" % warned
    assert (core.OCR_ENGLISH_MISSING_MSG not in " ".join(warned)
            and help_alive(en_help)), \
        "英語の件を警告文に混ぜている（案内の窓に回すこと）"


@pytest.mark.gui
def test_2回押しても窓は1つ(en_help):
    en_help.open_english_ocr_help()
    first = en_help._en_help_win
    en_help.open_english_ocr_help()
    assert en_help._en_help_win is first, "案内の窓が二重に開いた"
