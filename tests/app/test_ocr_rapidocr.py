# -*- coding: utf-8 -*-
"""同梱の英語OCR（RapidOCR）に意地悪な入力を与えて、落ちない・固まらないかを見る。

もとは _legacy/test_rapidocr_edges.py。1本の台本だったものを、落ちた場所が
名前で分かるように割りました。ケースの名前は元のまま残しています。

見張っているのは3つです。

 1. 際どい入力で例外を投げないこと。`core.run_rapidocr` は1枚の失敗を握りつぶして
    次の画像へ進む（その画像は結果に入らない＝前の結果が残る）ので、壊れた入力で
    例外が出るようになっても、画面の側では「読めなかった」と見分けが付かない。
 2. 時間内に返ること。上限は元の台本と同じ30秒。
 3. エンジンは1つだけ作って使い回すこと（同時に呼ばれても二重に読み込まない）。

色の持ち方（RGBA・L・P）と大きさ（1×1・9000×6000）を分けて見ているのは、
RapidOCR には画像の**場所**を渡し、縦横の大きさだけ PIL 側で見ているためです。
PIL が開ける形でも RapidOCR が扱えない、ということが起こり得ます。

元の台本と同じく、アプリ本体（main）は読み込みません＝本人の設定に触りません。
作った画像は tmp_path_factory の捨て場に置きます（アプリのフォルダには作らない）。
巨大画像だけは、元の台本の finally と同じく使い終わったらその場で消します。
"""
import os
import sys
import threading
import time
import types

import pytest

import _seed
import core

# 同梱されていない環境（英語OCRを入れていない・macOS）では、このファイルは丸ごと飛ばす。
# 有無の判定は読み込みを伴わないので、ここで払う時間はほぼ無い。
pytestmark = pytest.mark.skipif(
    not core.rapidocr_available(),
    reason="同梱の英語OCR（rapidocr_onnxruntime）が入っていません")

# 判定に使う英文。seed が描く英語1行目で、"decoy" が読めれば「読めた」と見る
# （元の台本と同じ文）。
LINE = _seed.EN_LINES[0]

# 1枚にかけていい時間の上限（秒）。元の台本の「時間がかかりすぎ」と同じ値。
# 混んだPCで誤報が出ても、ここは緩めない（緩めると遅くなったことに気づけない）。
# 代わりに、落ちたときのメッセージに実測値と上限の両方を出す（_assert_fast）。
LIMIT = 30.0


@pytest.fixture(scope="session")
def ocr_ready():
    """先に RapidOCR を読み込んでおく。

    初回の読み込みは重い（インストール直後はウイルス検査で15秒、2回目以降は0.2秒）。
    各ケースの計測にそれを混ぜないため、元の台本と同じく先に一度だけ呼んでおく。

    読み込みに失敗したらそのまま落とす（skip にしない）。「入っているのに読めない」は
    本体の経路でも起きる不具合なので、飛ばすと気づけなくなる。"""
    core._rapidocr_engine()
    return True


def _read(path):
    """1枚読んで（テキスト, かかった秒数）を返す。読めなかった画像は None。"""
    start = time.perf_counter()
    out = core.run_rapidocr([path])
    elapsed = time.perf_counter() - start
    assert isinstance(out, dict), "結果が {場所: 文字} の辞書で返らない: %r" % (out,)
    assert set(out) <= {path}, "頼んでいない画像が結果に混ざっている: %r" % (sorted(out),)
    return out.get(path), elapsed


def _assert_fast(label, elapsed):
    """時間の確認。落ちたときに実測値と上限の両方が見えるようにする
    （「遅い」とだけ言われても、惜しかったのか桁違いなのか分からない）。"""
    assert elapsed < LIMIT, ("%sで時間がかかりすぎ: %.1f秒（上限 %.1f秒）"
                             % (label, elapsed, LIMIT))


def _read_ok(label, path):
    """「読めるべき」ケースの共通の確認（本文が読めていて、時間内に返る）。

    中身の確認を先に、時間の確認を後ろに置く。逆にすると、混んでいて遅いだけの
    ときに時間の assert が先に落ちて、肝心の「読めていたのかどうか」が
    見えなくなる（元の台本は両方を集めて最後にまとめて出していた）。"""
    text, elapsed = _read(path)
    assert text is not None, "%sが結果に入っていない（読み取りが例外で捨てられた）" % label
    assert "decoy" in text.lower(), "%sから本文が読めない: %r" % (label, text)
    _assert_fast(label, elapsed)


# ---------------------------------------------------------------- 種データ
# seed に無い形（1×1・パレット・巨大）だけ、ここで seed の英文とフォントから作る。
# 本人のスクリーンショットには頼らない。
@pytest.fixture(scope="session")
def tiny_image(tmp_path_factory):
    """1×1ピクセルの画像。"""
    from PIL import Image
    path = str(tmp_path_factory.mktemp("tiny") / "tiny.png")
    Image.new("RGB", (1, 1), "white").save(path)
    return path


@pytest.fixture(scope="session")
def palette_image(seed, tmp_path_factory):
    """パレット（P）の画像。

    P は Image.new に色の名前を渡せないので、元の台本と同じく RGB から変換して作る。"""
    from PIL import Image
    path = str(tmp_path_factory.mktemp("palette") / "palette.png")
    with Image.open(seed.english()) as im:
        im.convert("P").save(path)
    return path


@pytest.fixture(scope="session")
def huge_image(seed, tmp_path_factory):
    """9000×6000 の巨大画像（seed の作る大きさでは足りないのでここで作る）。

    使い終わったら消す。tmp_path_factory の捨て場は pytest が数回分ためておくので、
    置きっぱなしにすると巨大画像だけが何度分も残る（元の台本は finally で
    作業フォルダごと消していた）。"""
    from PIL import Image, ImageDraw
    path = str(tmp_path_factory.mktemp("huge") / "huge.png")
    img = Image.new("RGB", (9000, 6000), "white")
    ImageDraw.Draw(img).text((200, 200), LINE, fill="black", font=seed.font(160))
    img.save(path)
    img.close()          # 162MBある。次のケースに持ち越さないよう、ここで手放す
    try:
        yield path
    finally:
        try:
            os.remove(path)
        except OSError:
            pass         # 消せなくてもテストの結果は変えない


@pytest.fixture(scope="session")
def rgba_image(seed):
    """透過PNG（RGBA）。読み取りと、3スレッド同時のケースで使い回す。

    名前を RGB 版と分けているのは、seed が同じ名前なら同じファイルに上書きするため。"""
    return seed.english(name="en_rgba.png", mode="RGBA")


# ---------------------------------------------------------------- 読めなくてよい入力
# ここの4つは「読めること」ではなく「落ちないこと・固まらないこと」を見ている。
# 元の台本も、何か読めてしまった場合は責めていない（白紙から点をひとつ拾うなど）。
def test_壊れたPNGでも落ちない(seed, ocr_ready):
    """PNGの先頭だけ本物で中身が乱数のファイル。PIL が開けないので結果に入らない。"""
    # 以下4つとも、_read_ok と同じ理由で中身の確認が先・時間の確認が後。
    text, elapsed = _read(seed.corrupt())
    assert not (text or "").strip(), \
        "壊れたPNGから文字が読めたことになっている: %r" % (text,)
    _assert_fast("壊れたPNG", elapsed)


def test_存在しないファイルでも落ちない(seed, ocr_ready):
    """消えた画像を渡されても、例外を投げずにその画像を飛ばすだけ。"""
    text, elapsed = _read(seed.missing())
    assert not (text or "").strip(), \
        "存在しないファイルから文字が読めたことになっている: %r" % (text,)
    _assert_fast("存在しないファイル", elapsed)


def test_1x1ピクセルでも落ちない(tiny_image, ocr_ready):
    """縮小や切り出しの計算で0割りにならないか（幅・高さで割って位置を出している）。"""
    text, elapsed = _read(tiny_image)
    assert text is None or isinstance(text, str), \
        "文字列でも無しでもないものが返った: %r" % (text,)
    assert "decoy" not in (text or "").lower(), \
        "1×1ピクセルから本文が読めるのはおかしい（別の画像の結果が混ざっていないか）"
    _assert_fast("1×1ピクセル", elapsed)


def test_真っ白な画像でも落ちない(seed, ocr_ready):
    """文字が1つも無い画像。行が0件のときの組み立てで転ばないか。"""
    text, elapsed = _read(seed.blank())
    assert text is None or isinstance(text, str), \
        "文字列でも無しでもないものが返った: %r" % (text,)
    assert "decoy" not in (text or "").lower(), \
        "白紙から本文が読めるのはおかしい（別の画像の結果が混ざっていないか）"
    _assert_fast("真っ白な画像", elapsed)


# ---------------------------------------------------------------- 読めるべき入力
# 色の持ち方・大きさが違うだけで読めなくなっていないか。
# run_rapidocr は大きさを PIL で見て、読み取りは画像の場所を RapidOCR に渡すので、
# PIL が開ける形でも RapidOCR 側が扱えない、ということが起こり得る。
def test_透過PNG_RGBA_から読める(rgba_image, ocr_ready):
    _read_ok("透過PNG（RGBA）", rgba_image)


def test_グレースケール_L_から読める(seed, ocr_ready):
    _read_ok("グレースケール（L）",
             seed.image("en_gray.png", _seed.EN_LINES, mode="L"))


def test_パレット_P_から読める(palette_image, ocr_ready):
    _read_ok("パレット（P）", palette_image)


@pytest.mark.slow
def test_巨大画像_9000x6000_から読める(huge_image, ocr_ready):
    """4Kのスクショより大きい画像。読み込みで溢れず、30秒以内に返ること。"""
    _read_ok("巨大画像 9000×6000", huge_image)


# ---------------------------------------------------------------- その他
def test_空のリストは空の結果(ocr_ready):
    """画像が0枚でも空の辞書を返す（呼ぶ側で枚数を分岐しなくていいように）。"""
    assert core.run_rapidocr([]) == {}, "0枚のときに空の辞書が返らない"


@pytest.mark.slow
def test_3スレッドから同時に呼んでも壊れない(rgba_image, ocr_ready):
    """アプリは busy の印で同時には走らせないが、壊れないかは見ておく。

    1つのエンジン（_RAPIDOCR）を3本で取り合っても、例外にならず、結果が
    混ざらず、止まらないこと。

    **二重読み込みはここでは見ていない。** ocr_ready が先に温めてしまうので、
    3本が入る時点でエンジンはもう出来ている（前はここに「二重読み込みをしない」
    と書いてあったが、それは空振りだった）。冷えた状態からの確認は
    下の test_エンジンは同時に呼ばれても一度しか作られない が受け持つ。"""
    results, errors = [], []
    ready = threading.Barrier(3)

    def go():
        try:
            ready.wait(60)                       # 3本がそろってから入る＝本当に同時
            results.append(core.run_rapidocr([rgba_image]).get(rgba_image, ""))
        except Exception as exc:                 # 落ちた理由を名前ごと残す
            errors.append("%s: %s" % (type(exc).__name__, exc))

    threads = [threading.Thread(target=go) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(120)

    assert not [th for th in threads if th.is_alive()], \
        "120秒たっても終わらないスレッドがある（エンジンの取り合いで止まっていないか）"
    assert not errors, "同時に呼んだら例外が出た: %r" % (errors,)
    assert len(results) == 3, "3本のうち %d 本しか結果を返していない" % len(results)
    assert all("decoy" in text.lower() for text in results), \
        "同時に呼ぶと読めなくなる: %r" % (results,)


def test_エンジンは同時に呼ばれても一度しか作られない(monkeypatch):
    """冷えた状態から3本で取りに行って、エンジンが1つしか作られないこと。

    上のケースでは温まったあとにしか3本を入れられないので、二重読み込みは
    そこでは見えない。ここは `_RAPIDOCR` をいったん空に戻してから3本そろえて入る。
    本物を作り直すと重い（初回は15秒）うえにメモリも2つ分になるので、
    偽物を1つ数えるだけにする。見たいのは本体の使い回し（鍵とNone判定）であって
    RapidOCR そのものではない。"""
    created = []
    fake_module = types.ModuleType("rapidocr_onnxruntime")

    class FakeRapidOCR:
        def __init__(self, **kw):                # 余りは **kw で受ける（偽物の作法）
            # わざと少し居座る。作るのが一瞬だと、鍵を外しても3本がすれ違わずに
            # 1回で済んでしまい、この確認が運任せになる。ここで待つことで
            # 「鍵が無ければ3本とも None を見て3つ作る」が必ず起きる。
            time.sleep(0.05)
            created.append(kw)

        def __call__(self, path, **kw):
            return ([], 0.0)

    fake_module.RapidOCR = FakeRapidOCR
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_module)
    monkeypatch.setitem(core._RAPIDOCR, "engine", None)   # 冷やす（後で本物に戻る）

    got, errors = [], []
    ready = threading.Barrier(3)

    def go():
        try:
            ready.wait(60)                       # 3本そろってから鍵を取りに行く
            got.append(core._rapidocr_engine())
        except Exception as exc:
            errors.append("%s: %s" % (type(exc).__name__, exc))

    threads = [threading.Thread(target=go) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(60)

    assert not [th for th in threads if th.is_alive()], \
        "60秒たっても終わらないスレッドがある（エンジンの鍵で止まっていないか）"
    assert not errors, "同時にエンジンを取りに行ったら例外が出た: %r" % (errors,)
    assert len(created) == 1, \
        "エンジンが %d 回作られた（同時に来ても1回だけ読む約束が守られていない）" % len(created)
    assert len(got) == 3, "3本のうち %d 本しかエンジンを受け取れていない" % len(got)
    assert len({id(engine) for engine in got}) == 1, \
        "3本が別々のエンジンを受け取っている（使い回しになっていない）"
    assert core._RAPIDOCR["engine"] is got[0], \
        "作ったエンジンが _RAPIDOCR に残っていない（毎回作り直しになる）"
