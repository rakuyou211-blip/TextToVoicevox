# -*- coding: utf-8 -*-
"""英語の読み直し（2周目）が「止まる・知らせる・速い」の見張り。

もとは _legacy/test_second_pass_cancel.py（2026-09-15 に直したときの台本）。

日本語で読んだ結果が化けている画像だけ、あとから英語で読み直す（これが2周目）。
1枚1〜3秒かかる重い仕事なので、次の3つが崩れると利用者は「固まった」と思って
窓を閉じてしまう。ここで見張るのはその3つ。

 (A) core.run_rapidocr … 取り消しが1枚ごとに効く／進捗が1枚ごとに届く
 (B) core._english_second_pass（RapidOCR経路）… 途中で止めたら残りは読まず、
     読めた分だけ使う。進捗は「english」の段として、英語の画像の枚数を分母に届く
 (C) core._english_second_pass（Windowsの英語OCR経路）… チャンクの切れ目で止まる
 (D) 本物の core.extract_files … 読み直し中は「英語を読み直しています… i/n枚」と出る
 (E) スレッド数の上限（4まで）… 本物の読み取りが既定より速く、
     かつ1枚あたりの秒数が桁違いになっていない

画面は作らないので main は読み込まない（＝本人の設定にも触らない）。
種の画像は seed fixture に作らせる。もとの台本は本人のスクリーンショットを
直に見ていて、ほかのPCでは再現できなかった。

取り消しの実演は、どれも「押すまで待つ」時間が要る（1回1.3〜1.8秒）。
節ごとに1回だけ実演して、その結果を小さなテストが1つずつ見る形にしている
（fixture を module スコープにした理由。テストごとに実演し直すと、この
ファイルだけで20秒以上が待ち時間で消えた）。
"""
import os
import sys
import threading
import time
import types

import pytest

import core

# 偽のOCRが返す英文。(D) で「英語になったか」を見るので decoy を含めておく。
ENGLISH_LINE = "Your decoy files are NOT being watched right now."

# 日本語OCRが英文を読むと、こういう化け方をする（実物から採った）。
# 2周目の出来と比べる相手なので、化け方はいじらずそのまま置く。
GARBLED = "物し「decoy創es are NO丁匯i叩watched「ight now. Resta代 it with 「i pt.exe"

# (E) の読み取り時間を測る紙。もとの台本は本人のスクリーンショット1枚で測って
# いたので、同じくらい文字の乗った紙を種から作る。
# 速さの判定は「既定より速いか」の比で見る（秒数そのものは紙と機体で変わる）。
SPEED_LINES = [ENGLISH_LINE] * 9

# ただし比だけでは、上限つきも既定も一緒に10倍遅くなったときに比が保たれて
# 通ってしまう。「誰が見ても遅い」はこの絶対の上限で止める。
# 1枚5秒は、この機体の実測（上限つき1.47秒）の3倍以上の余裕をとった値。
# もとの台本の1.1秒は、この機体で本人のスクリーンショット1枚を測って決めた値
# なので採らない（紙や機体が変わると、直っているのに落ちる）。
SPEED_ABS_LIMIT = 5.0


def _english_pages(seed, count, prefix="ocr_cancel"):
    """英文のPNGを count 枚そろえる。
    中身は同じでかまわない（見ているのは経路の止まり方と進捗の数え方）。"""
    return [seed.image("%s_p%02d.png" % (prefix, i), [ENGLISH_LINE])
            for i in range(count)]


class SlowEngine:
    """1枚0.5秒かかる偽のRapidOCR（本物は1枚1〜3秒）。

    呼び出しは **kw で余りを受ける。偽物が本体の引数追加に追いつかず、
    黙って通ってしまう事故が過去に3回あった。"""

    def __init__(self, seconds=0.5):
        self.calls = 0
        self.seconds = seconds

    def __call__(self, path, **kw):
        self.calls += 1
        time.sleep(self.seconds)
        # RapidOCR の戻りの形（[四隅の座標, 文字, 確からしさ] の並び, かかった時間）
        return ([[[[0, 0], [600, 0], [600, 40], [0, 40]], ENGLISH_LINE, 0.99]],
                self.seconds)


def _run_aside(func):
    """別のスレッドで走らせ、結果の箱とスレッドを返す。
    中で落ちたときは例外も箱に入れる（取り消しの経路が落ちたのを
    「止まった」と勘違いしないため）。"""
    box = {}

    def runner():
        try:
            box["out"] = func()
        except BaseException as exc:
            box["exc"] = exc

    th = threading.Thread(target=runner)
    th.start()
    return box, th


def _swap():
    """差し替えの道具（節ごとに1回だけ走らせる fixture 用）。

    monkeypatch fixture は1つのテストぶんしか生きないので、実演を1回で
    済ませる module スコープの fixture では使えない。pytest が公開している
    MonkeyPatch を with で借りて、**実演が終わったその場で戻す**
    （with なので途中で落ちても戻る）。
    後の節へ持ち越さないことが肝心で、(D)(E) は本物の RapidOCR を使うから、
    ここの偽物が生き残っていると本物を測ったつもりで黙って通ってしまう。"""
    return pytest.MonkeyPatch.context()


def _stop_and_join(cancel, th, box, what):
    """取り消しを押してから、実際に止まるまでの秒数を測る。"""
    t_cancel = time.perf_counter()
    cancel.set()
    th.join(10)
    lag = time.perf_counter() - t_cancel
    assert not th.is_alive(), "%s: 取り消しを押しても10秒たっても止まらない" % what
    assert "exc" not in box, "%s: 途中で例外が出た: %r" % (what, box.get("exc"))
    return lag


# ================================================================ (A) run_rapidocr
# もとは「20枚まとめて読んでから取り消しを見る」形だったので、押しても
# 最大20枚ぶん（＝30秒以上）待たされた。1枚ごとに見るように直したところ。
@pytest.fixture(scope="module")
def cancelled_rapidocr(seed):
    """10枚を読ませて、2〜3枚目の最中に取り消しを押した結果（この節で使い回す）。"""
    pages = _english_pages(seed, 10)
    engine = SlowEngine()
    prog = []
    cancel = threading.Event()
    with _swap() as mp:
        mp.setattr(core, "_rapidocr_engine", lambda: engine)
        box, th = _run_aside(lambda: core.run_rapidocr(
            pages, cancel_event=cancel,
            progress_cb=lambda done, total: prog.append((done, total))))
        time.sleep(1.3)                  # 2〜3枚目の最中に押す
        lag = _stop_and_join(cancel, th, box, "run_rapidocr")
    return types.SimpleNamespace(pages=pages, engine=engine, prog=prog,
                                 out=box.get("out", {}), lag=lag)


def test_キャンセルは1枚ぶん以内で効く(cancelled_rapidocr):
    assert cancelled_rapidocr.lag < 0.8, (
        "押してから止まるまで%.2f秒（1枚0.5秒なので0.8秒未満のはず。"
        "従来は最大20枚ぶん待たされた）" % cancelled_rapidocr.lag)


def test_残りは読まない(cancelled_rapidocr):
    assert cancelled_rapidocr.engine.calls < 10, (
        "取り消したのに10枚全部読んでいる（読んだ枚数 %d）"
        % cancelled_rapidocr.engine.calls)


def test_読めた分は返す(cancelled_rapidocr):
    """止めた時点までの結果は捨てない（捨てると、押した人の待ち時間が丸損になる）。"""
    got = len(cancelled_rapidocr.out)
    assert 0 < got < 10, "読めた分だけ返っていない（返った枚数 %d / 10）" % got


def test_進捗は1枚ごとに届く(cancelled_rapidocr):
    # 「届いた分が 0,1,2,… の順か」だけを見ていると、1つも届かなかったとき
    # （[] == []）にも通ってしまう。(B)(C) と同じく、まず届いたこと自体を見る。
    # 本体は1枚目を読む前に progress_cb(0, 総数) を呼ぶので、取り消しを押す
    # 1.3秒のあいだに必ず1つは届く。
    prog = cancelled_rapidocr.prog
    assert prog, "進捗が1つも届いていない（2〜3枚読んでいるはずの時点で取り消した）"
    done = [d for d, _total in prog]
    assert done == list(range(len(done))), (
        "進捗が1枚ずつ順に届いていない: %r" % (prog,))


def test_進捗の分母は全体の枚数(cancelled_rapidocr):
    # all() は空のとき真なので、こちらも「届いたこと」を先に見る
    prog = cancelled_rapidocr.prog
    assert prog, "進捗が1つも届いていない（分母を見る前に、届くこと自体を見る）"
    assert all(total == 10 for _done, total in prog), (
        "分母が10枚になっていない: %r" % (prog,))


@pytest.fixture(scope="module")
def finished_rapidocr(seed):
    """取り消さずに3枚読み切った結果（最後の1つが届くかを見る）。"""
    pages = _english_pages(seed, 3)
    engine = SlowEngine()
    prog = []
    with _swap() as mp:
        mp.setattr(core, "_rapidocr_engine", lambda: engine)
        out = core.run_rapidocr(
            pages, progress_cb=lambda done, total: prog.append((done, total)))
    return types.SimpleNamespace(prog=prog, out=out)


def test_最後まで読んだら3枚目まで届く(finished_rapidocr):
    """読み終わりの通知が無いと、進捗の帯が 2/3 で止まったまま終わる。"""
    assert finished_rapidocr.prog[-1] == (3, 3), (
        "最後の進捗が 3/3 で終わっていない: %r" % (finished_rapidocr.prog,))
    assert len(finished_rapidocr.out) == 3, (
        "3枚とも返っていない（返った枚数 %d）" % len(finished_rapidocr.out))


# ================================================================ (B) 読み直し・RapidOCR経路
# Windowsに英語のOCRが入っていない機体（本人のPCもこちら）では、同梱の
# RapidOCR が1枚ずつ引き受ける。だから取り消しも進捗も1枚ごとに効くはず。
@pytest.fixture(scope="module")
def cancelled_second_pass(seed):
    """化けた10枚を読み直させて、途中で取り消しを押した結果（この節で使い回す）。"""
    pages = _english_pages(seed, 10)
    engine = SlowEngine()
    result = {p: GARBLED for p in pages}
    notices, prog = [], []
    cancel = threading.Event()

    def pcb(*args):
        prog.append(args)

    with _swap() as mp:
        mp.setattr(core, "_rapidocr_engine", lambda: engine)
        mp.setattr(core, "rapidocr_available", lambda: True)
        # available_langs に en が無い＝Windows側に英語のOCRが無い機体のふり
        box, th = _run_aside(lambda: core._english_second_pass(
            result, {"available_langs": ["ja"]}, False, notices, cancel,
            progress_cb=pcb))
        time.sleep(1.8)
        lag = _stop_and_join(cancel, th, box, "_english_second_pass")
    replaced = sum(1 for p in pages if result[p] != GARBLED)
    return types.SimpleNamespace(pages=pages, result=result, prog=prog,
                                 notices=notices, lag=lag, replaced=replaced)


def test_読み直し中のキャンセルも1枚ぶん以内で効く(cancelled_second_pass):
    assert cancelled_second_pass.lag < 0.8, (
        "押してから止まるまで%.2f秒（1枚0.5秒なので0.8秒未満のはず）"
        % cancelled_second_pass.lag)


def test_読めた分だけ英語にし残りは元のまま(cancelled_second_pass):
    """途中で止めたのに全部差し替わる／1枚も差し替わらない、のどちらも困る。"""
    assert 0 < cancelled_second_pass.replaced < 10, (
        "英語にできた枚数がおかしい（%d / 10枚）" % cancelled_second_pass.replaced)


def test_進捗はenglishの段として届く(cancelled_second_pass):
    """3つ目の引数で段を伝える。知らない受け手には2つで呼び直す作りなので、
    ここが崩れると「OCR実行中」の帯が巻き戻ったように見える。"""
    prog = cancelled_second_pass.prog
    assert prog, "読み直しの進捗が1つも届いていない"
    assert all(len(a) == 3 and a[2] == "english" for a in prog), (
        "englishの段として届いていない: %r" % (prog,))


def test_分母は英語の画像の枚数(cancelled_second_pass):
    """全体の枚数ではなく、読み直す枚数（10枚）が分母。"""
    # (A) と同じ理由。all() は空のとき真なので、届いたことを先に見る
    prog = cancelled_second_pass.prog
    assert prog, "読み直しの進捗が1つも届いていない（分母はその次に見る）"
    assert all(a[1] == 10 for a in prog), (
        "分母が英語の画像の枚数になっていない: %r" % (prog,))


# ================================================================ (C) 読み直し・Windows経路
# Windowsに英語のOCRが入っている機体では、PowerShellを1回起動するごとに
# まとめて読む。だから取り消しはチャンクの切れ目までしか待たない、が期待。
@pytest.fixture(scope="module")
def cancelled_windows_pass(seed):
    """2枚ずつのチャンクで読み直させて、途中で取り消しを押した結果（この節で使い回す）。"""
    pages = _english_pages(seed, 10)
    chunk_calls = []

    def slow_chunk(image_paths, **kw):
        """PowerShellを1回起こす代わり（本物は起動だけで1〜2秒）。
        **kw で受けるのは、本体が引数を増やしたときに気づくため。"""
        chunk_calls.append(len(image_paths))
        time.sleep(0.6)
        return {p: ENGLISH_LINE for p in image_paths}

    result = {p: GARBLED for p in pages}
    prog = []
    cancel = threading.Event()
    with _swap() as mp:
        mp.setattr(core, "_run_windows_ocr_chunk", slow_chunk)
        box, th = _run_aside(lambda: core._english_second_pass(
            result, {"available_langs": ["ja", "en-US"]}, False, [], cancel,
            chunk_size=2, progress_cb=lambda *a: prog.append(a)))
        time.sleep(1.4)
        lag = _stop_and_join(cancel, th, box, "_english_second_pass（Windows経路）")
    return types.SimpleNamespace(chunk_calls=chunk_calls, prog=prog,
                                 result=result, lag=lag)


def test_チャンクの切れ目でキャンセルが効く(cancelled_windows_pass):
    """10枚を2枚ずつ＝5回。押した時点で残りのチャンクへ進まないこと。"""
    assert len(cancelled_windows_pass.chunk_calls) < 5, (
        "取り消したのに最後のチャンクまで読んでいる（呼び出し %r）"
        % (cancelled_windows_pass.chunk_calls,))


def test_Windows経路でも進捗はenglishの段(cancelled_windows_pass):
    prog = cancelled_windows_pass.prog
    assert prog, "読み直しの進捗が1つも届いていない"
    assert all(a[2] == "english" for a in prog), (
        "englishの段として届いていない: %r" % (prog,))


# ================================================================ (D) 本物の抽出
# 偽物どうしで噛み合っていても、本物の文言が出ていなければ意味がない。
# 「英語を読み直しています…」が出ないあいだ、利用者には理由の分からない
# 待ち時間（1枚1〜3秒 × 枚数）だけが残る。
@pytest.fixture(scope="module")
def real_second_pass(seed):
    """本物の extract_files を1回だけ通す（重いので、この節の4本で使い回す）。

    この機体のWindows標準OCRには日本語しか入っていないので、読み直しは
    同梱の RapidOCR が1枚ずつ引き受ける＝進捗も1枚ごとに届く。"""
    pages = _english_pages(seed, 3, prefix="ocr_cancel_real")
    msgs = []
    text, warnings = core.extract_files(
        pages, pdf_mode="auto", dpi=300, preprocess=True,
        progress_cb=lambda done, total, msg: msgs.append((done, total, msg)))
    english = [m for m in msgs if "英語を読み直しています" in m[2]]
    return types.SimpleNamespace(text=text, warnings=warnings,
                                 msgs=msgs, english=english)


@pytest.mark.slow
def test_英語を読み直していますと出る(real_second_pass):
    assert len(real_second_pass.english) >= 2, (
        "読み直し中の文言が1枚ごとに出ていない。出た通知: %r / 警告: %r"
        % ([m[2] for m in real_second_pass.msgs], real_second_pass.warnings))


@pytest.mark.slow
def test_読み直しの分母は英語の画像の枚数(real_second_pass):
    # 読み直しの通知が1つも無ければ、分母の話にならない（all() は空のとき真）
    english = real_second_pass.english
    assert english, (
        "読み直しの通知が1つも出ていない。出た通知: %r"
        % ([m[2] for m in real_second_pass.msgs],))
    assert all(m[1] == 3 for m in english), (
        "分母が英語の画像の枚数（3）になっていない: %r" % ([m[2] for m in english],))


@pytest.mark.slow
def test_読み直しの進捗は最後まで届く(real_second_pass):
    assert real_second_pass.english[-1][0] == 3, (
        "最後が 3/3 まで届いていない: %r"
        % ([m[2] for m in real_second_pass.english],))


@pytest.mark.slow
def test_読み直した結果は英語になる(real_second_pass):
    """通知だけ出て、中身が化けたままでは意味がない。"""
    assert "decoy" in real_second_pass.text.lower(), (
        "読み直した結果が英語になっていない: %r" % real_second_pass.text[:200])


# ================================================================ (E) スレッド数の上限
# 既定の「全コア」だと、コアの多い機体でかえって遅い
# （実測 2026-09-15・20コア: 全コア1.48秒 / 4スレッド0.65秒 /
#  2スレッド0.73秒 / 1スレッド1.11秒。メモリはどれも同じ）。
def test_スレッド数の上限が効いている(monkeypatch):
    """作るときにスレッド数を渡しているか、渡す値は4までか。

    もとの台本は本人のスクリーンショットを読ませた秒数だけで見ていたが、
    それでは機体と紙で結果が変わる。上限そのものはここで直に見る。"""
    recorded = {}
    fake_module = types.ModuleType("rapidocr_onnxruntime")

    class FakeRapidOCR:
        def __init__(self, **kw):        # 余りは **kw で受ける（偽物の作法）
            recorded.update(kw)

        def __call__(self, path, **kw):
            return ([], 0.0)

    fake_module.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_module)
    monkeypatch.setitem(core._RAPIDOCR, "engine", None)   # 作り直させる

    core._rapidocr_engine()
    want = max(1, min(4, os.cpu_count() or 4))
    assert recorded.get("intra_op_num_threads") == want, (
        "スレッド数の上限が渡っていない（渡した値 %r / 期待 %d）"
        % (recorded.get("intra_op_num_threads"), want))
    assert recorded.get("inter_op_num_threads") == want, (
        "inter側にも上限が渡っていない（渡した値 %r / 期待 %d）"
        % (recorded.get("inter_op_num_threads"), want))


def _average_seconds(engine, page, reps, warmup=True):
    """1枚あたりの秒数。読み込みの分が乗る1回目は捨てる
    （測り直すときは、もう温まっているので warmup=False）。"""
    if warmup:
        engine(page)
    times = []
    for _ in range(reps):
        t0 = time.perf_counter()
        engine(page)
        times.append(time.perf_counter() - t0)
    return sum(times) / len(times)


@pytest.mark.slow
@pytest.mark.skipif((os.cpu_count() or 4) <= 4,
                    reason="コアが4つ以下の機体では、上限つきと既定が同じスレッド数に"
                           "なる＝比べる意味がない（上限そのものは "
                           "test_スレッド数の上限が効いている が機体を問わず見張る）")
def test_本物の読み取りが既定より速い(seed, monkeypatch):
    """上限つきで作ったエンジンと、既定（全コア）のエンジンを、同じ紙で比べる。

    見るのは2つ。「既定より速いこと」（比）と、「1枚 SPEED_ABS_LIMIT 秒より
    速いこと」（絶対）。比だけだと、上限つきも既定も一緒に10倍遅くなったときに
    比が保たれて通ってしまうので、桁違いの遅さは絶対の上限で受け止める。

    もとの台本は「1枚1.1秒未満」という秒数で見ていた。これは本人の
    スクリーンショット1枚をこの20コアの機体で測った値（4スレッド0.65秒）から
    決めたもので、紙が変われば意味を失う。実際、種から作った紙では上限つきでも
    1.48秒かかり、3倍速くなっているのに落ちた（2026-09-23 実測）。
    主に見たいのは秒数そのものではなく「既定より速いこと」なので、その場で
    両方測って比べる（そのうえで、上の絶対の上限で桁違いの遅さだけを拾う）。
    同じ紙・同じプロセスで続けて測るので、ほかの仕事で機体が混んでいても
    両方が一緒に遅くなるだけで、比は崩れにくい。

    実測 2026-09-23・20コア・この紙（1400x620・英文9行）:
      既定 4.46秒 / 上限つき 1.47秒（3.0倍）。測る順を入れ替えても同じ。
    もとの実測 2026-09-15（本人のスクリーンショット・同じ機体）:
      既定1.48秒 / 4スレッド0.65秒 / 2スレッド0.73秒 / 1スレッド1.11秒。
    """
    assert core.rapidocr_available(), (
        "同梱の英語OCR（rapidocr_onnxruntime）が venv に入っていない")
    page = seed.image("ocr_cancel_speed.png", SPEED_LINES)

    monkeypatch.setitem(core._RAPIDOCR, "engine", None)   # 上限つきで作り直す
    capped_engine = core._rapidocr_engine()
    from rapidocr_onnxruntime import RapidOCR
    plain_engine = RapidOCR()            # 上限を渡さない＝直す前の既定

    capped = _average_seconds(capped_engine, page, reps=2)
    # 既定側は1回でよい（3倍も違うので足りる。1枚4.5秒を何度も測ると、
    # それだけでこのファイルが10秒近く長くなる）
    plain = _average_seconds(plain_engine, page, reps=1)
    if capped >= plain * 0.8 or capped >= SPEED_ABS_LIMIT:
        # 秒数の測定は、機体が一瞬混むだけで崩れる（12回回して1回、比が落ちた。
        # 2026-09-23）。しきい値は動かさず、両方を測り直して、それぞれ速かった
        # ほうの値で決める（混んでいないときの値のほうが、その機体の本当の速さ）。
        # 本当に遅くなったのなら測り直しても遅いので、見張りは弱まらない。
        # 絶対の上限に引っかかったときも同じ扱いにする（比だけ通って絶対で
        # 落ちる、という一瞬の混みかたがあるため）。
        capped = min(capped, _average_seconds(capped_engine, page, reps=2,
                                             warmup=False))
        plain = min(plain, _average_seconds(plain_engine, page, reps=2,
                                            warmup=False))
    # 先に絶対の上限。比は「両方が一緒に遅くなった」を見逃すので、そこだけ補う
    # （紙が変わっても効くように、実測1.47秒の3倍以上の余裕をとってある）
    assert capped < SPEED_ABS_LIMIT, (
        "上限つきでも1枚%.2f秒かかっている（%.1f秒未満のはず。このとき既定は"
        "%.2f秒。比だけ見ていると、両方が一緒に遅くなったときに気づけない）"
        % (capped, SPEED_ABS_LIMIT, plain))
    assert capped < plain * 0.8, (
        "上限つきが既定より速くなっていない（上限つき%.2f秒 / 既定%.2f秒。"
        "2026-09-23 の実測では 1.47秒 / 4.46秒）" % (capped, plain))
