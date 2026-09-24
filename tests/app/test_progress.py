# -*- coding: utf-8 -*-
"""進捗の見え方と、異常終了の後片づけ（第2段）の見張り。

もとは _legacy/test_stage2.py。見張っているのは3つです。

 (1) 前回の異常終了で %TEMP% に残った作業用ファイルを、起動時に掃除する
 (2) OCR の進捗がページ数の目盛りになる（PDFを1冊入れて 0% のまま止まらない）
 (3) 保存フェーズが喋り続ける（進捗バーが100%のまま無言にならない）

どれも発端は「固まったと誤解される」でした。中身が正しいかではなく、
動いていることが見えるかどうかの話なので、ここが落ちたら
見た目の退行だと思って読んでください。
"""
import io
import os
import shutil
import tempfile
import time
import types

import pytest

import core


# ================================================================ (1) %TEMP%の掃除
# 合成の途中でプロセスごと落とされると、スプールが残る（実例: 13MBが1週間居座った）。
# 起動時に掃除するが、消してよいのは1日以上前のものだけ。いま動いている別の
# インスタンスの作業フォルダを巻き添えにすると、そちらの合成が壊れる。
TWO_DAYS = 2 * 86400


@pytest.fixture
def swept():
    """%TEMP% におとりを置いて、掃除を1回走らせた結果を渡す。

    ここだけは tmp_path を使えない（sweep_stale_tmp が見るのは
    tempfile.gettempdir() の直下だけ）。置いたものは後で必ず片づける。

    数える前に一度素振りしておく。本人の %TEMP% に前から居る古い t2v_* まで
    数に入ると、「おとり2件」の勘定が合わなくなるため（素振りで消えるのは、
    どのみち次の起動で本体が消すごみだけ）。"""
    core.sweep_stale_tmp()
    root = tempfile.gettempdir()
    old_dir = tempfile.mkdtemp(prefix="t2v_spool_", dir=root)
    with io.open(os.path.join(old_dir, "a.wav"), "wb") as f:
        f.write(b"x" * 5000)
    new_dir = tempfile.mkdtemp(prefix="t2v_spool_", dir=root)
    fd, old_file = tempfile.mkstemp(prefix="t2v_play_", suffix=".wav", dir=root)
    os.close(fd)
    fd, other = tempfile.mkstemp(prefix="zzz_other_", suffix=".wav", dir=root)
    os.close(fd)
    stale = time.time() - TWO_DAYS
    for path in (old_dir, old_file, other):
        os.utime(path, (stale, stale))

    try:
        # 掃除そのものも try の中で走らせる。ここで例外が出ると、外に置いていた頃は
        # おとりが片づかないまま fixture が倒れていた。zzz_other_ は土台の見張り
        # （%TEMP% の t2v_* しか見ない）にも拾われないので、%TEMP% に残り続ける
        count, freed = core.sweep_stale_tmp()
        yield types.SimpleNamespace(old_dir=old_dir, new_dir=new_dir,
                                    old_file=old_file, other=other,
                                    count=count, freed=freed)
    finally:
        for path in (old_dir, new_dir):
            shutil.rmtree(path, ignore_errors=True)
        for path in (old_file, other):
            try:
                os.remove(path)
            except OSError:
                pass


def test_1日以上前のフォルダを消す(swept):
    assert not os.path.exists(swept.old_dir), "古いスプールが残った"


def test_1日以上前のファイルも消す(swept):
    assert not os.path.exists(swept.old_file), "古い再生用ファイルが残った"


def test_今できたばかりのものは消さない(swept):
    """動作中の別インスタンスの保護。ここを消すと、よその合成が途中で壊れる。"""
    assert os.path.exists(swept.new_dir), \
        "できたばかりのスプールを消してしまった（別インスタンスを巻き添えにする）"


def test_よそのファイルには触らない(swept):
    assert os.path.exists(swept.other), "t2v_ で始まらないファイルまで消した"


def test_回収量を数えている(swept):
    """何件・何バイト戻ったかは、そのまま起動時の知らせに出る数字。"""
    assert swept.count == 2, "消した数が合わない（%d件）" % swept.count
    assert swept.freed >= 5000, "回収したバイト数が合わない（%dB）" % swept.freed


# ================================================================ (2) OCRの進捗
# 目盛りがファイル数のままだと、PDFを1冊だけ入れたときの分母が1になる。
# 十数分かかっても 0% で止まって見えるので、「固まった」と誤解される
# 一番の原因だった。OCRに入ったらページ数の目盛りに切り替える。
@pytest.fixture
def ocr_marks(seed, monkeypatch):
    """画像1枚を、20ページぶんの進捗を演じる偽OCRに通し、
    「OCR実行中」の進捗通知だけを集めて返す。"""
    pytest.importorskip("PIL")      # 画像を読めない環境では見られない

    def fake_run_ocr(image_paths, **kw):
        """notices= は英語OCRの知らせ用に run_ocr へ足した引数（2026-09-15）。
        本体の引数が増えたときに黙って通らないよう **kw で受ける。"""
        progress_cb = kw.get("progress_cb")
        for i in range(1, 21):
            if progress_cb:
                progress_cb(i, 20)
            time.sleep(0.01)
        return {path: "これはテストのページです。" for path in image_paths}

    monkeypatch.setattr(core, "run_ocr", fake_run_ocr)

    # 残り時間は「5秒を超えるときだけ」出す作り。0.2秒で終わる偽OCRでは出ないので、
    # 1ページ10秒かかったことにして時計を進める。
    clock = [time.monotonic()]

    def fake_monotonic():
        clock[0] += 10.0
        return clock[0]

    marks = []

    def note(done, total, message):
        marks.append((done, total, message))

    # core.time は stdlib の time そのもの。fixture の monkeypatch に任せると、
    # このテストが終わるまでプロセス全体の monotonic が1回呼ぶごとに10秒進んだまま
    # になる（テスト本体や pytest 自身の計測まで巻き込む）。細工は extract_files を
    # 呼ぶ区間だけに閉じ、抜けた時点で本物に戻す。
    image = seed.blank()            # 種づくりは細工の外で済ませておく
    with pytest.MonkeyPatch.context() as clock_patch:
        clock_patch.setattr(core.time, "monotonic", fake_monotonic)
        core.extract_files([image], pdf_mode="auto", dpi=300,
                           progress_cb=note)
    return [m for m in marks if "OCR実行中" in m[2]]


def test_OCR中の分母がページ数になる(ocr_marks):
    """分母が1（＝ファイル数）のままでないこと。"""
    assert ocr_marks, "OCR中の進捗が1件も来ない"
    assert ocr_marks[-1][1] == 20, \
        "分母がページ数になっていない（最後の通知: %r）" % (ocr_marks[-1],)


def test_分子が動く(ocr_marks):
    """0%のまま止まらないこと。ここが動かないのが誤解の元だった。"""
    assert ocr_marks, "OCR中の進捗が1件も来ない"
    assert ocr_marks[-1][0] == 20, \
        "分子が進んでいない（最後の通知: %r）" % (ocr_marks[-1],)


def test_残り時間の見当が出る(ocr_marks):
    """何分かかるのか分からない待ちがいちばん不安なので、見当を添える。"""
    assert any("残り" in m[2] for m in ocr_marks), \
        "残り時間の見当が出ていない（%d件の通知を見た）" % len(ocr_marks)


# ================================================================ (3) 保存フェーズ
# 保存は「合成中」より長いことがある（10時間の本なら結合だけで数分）。
# 従来はここで一度しか喋らず、進捗バーは100%のまま止まって見えた。
# いまは何をしているか（つないでいる・変換中・字幕）を出し続ける。
@pytest.fixture
def saving_phase(app, monkeypatch, tmp_path, seed):
    """40行を「1本に結合＋字幕」で保存させ、保存フェーズ以降の通知を集める。"""
    lines = 40

    def fake_synth(base_url, text, speaker_id, **kw):
        """本体の引数が増えても黙って通らないよう **kw で受ける。
        3つ目の名前は本体（core.vv_synthesize_cached）に合わせて speaker_id。
        いまは位置で渡っているので speaker でも通るが、本体が名前で渡すように
        変わった日に「偽物だけが落ちる」ずれを残さないため。"""
        return seed.wav(0.05)

    monkeypatch.setattr(core, "vv_synthesize_cached", fake_synth)

    def fake_dict_hash_tracker(**kw):
        """辞書の指紋はエンジンに問い合わせる作り。ここでは中身を見ないので固定値。
        外側も内側も **kw で余りを受ける（前は素の lambda で、本体が引数を
        足した日に TypeError で倒れる形になっていた）。"""
        return lambda **kw2: "x"

    monkeypatch.setattr(app, "_dict_hash_tracker", fake_dict_hash_tracker)

    seen = []
    original_put = app.q.put

    def spy(msg, *args, **kw):
        if msg and msg[0] in ("progress", "synth_saving", "synth_done", "error"):
            seen.append(msg)
        return original_put(msg, *args, **kw)

    monkeypatch.setattr(app.q, "put", spy)

    jobs = [("%d行目の文です。" % i, 1, None) for i in range(1, lines + 1)]
    out = tmp_path / "out.wav"
    app._synth_worker(jobs, [list(range(lines))], {}, str(out),
                      "combine", 0.4, "wav", True)

    # 本体は例外を握りつぶして ("error", ...) を積むので、ここで見つけて中身を見せる
    # （見せないと、下の確認が「通知が0件」という分かりにくい形で落ちる）
    failures = [m for m in seen if m[0] == "error"]
    # 中身の取り出しは m[1] 決め打ちにしない。要素が1つだけの ("error",) が来ると、
    # 落ちた理由を見せるはずの行が IndexError にすり替わって、何が起きたのか
    # いちばん分からない形で倒れる
    detail = "\n".join(str(m[1]) if len(m) > 1 else repr(m) for m in failures)
    assert not failures, "保存の途中で例外が出た:\n%s" % detail
    kinds = [m[0] for m in seen]
    assert "synth_saving" in kinds, "保存フェーズに入った合図が来ない"
    start = kinds.index("synth_saving")
    after = [m for m in seen[start:] if m[0] == "progress"]
    return types.SimpleNamespace(after=after, out=out,
                                 srt=tmp_path / "out.srt")


@pytest.mark.gui
def test_保存フェーズでも喋る(saving_phase):
    assert len(saving_phase.after) >= 2, \
        "保存フェーズの進捗通知が%d件しかない（従来は0件で、無言に見えた）" \
        % len(saving_phase.after)


@pytest.mark.gui
def test_進捗が逆戻りしない(saving_phase):
    """保存フェーズの中では前にしか進まない（行ったり戻ったりすると余計に不安）。"""
    values = [m[1] for m in saving_phase.after]
    # 比べる前に「2件以上届いている」ことを確かめる。0件や1件だと
    # zip(values, values[1:]) が空になり all([]) が True になるので、保存フェーズが
    # 一度も喋らなかったとき——この一群が防ぎたい退行そのもの——に、ここだけが
    # 緑で通ってしまっていた
    assert len(values) >= 2, \
        "比べられる進捗が%d件しかない（保存フェーズが無言になっていないか）: %s" \
        % (len(values), values)
    assert all(b >= a for a, b in zip(values, values[1:])), \
        "進捗が逆戻りした: %s" % values


@pytest.mark.gui
def test_結合の最中も動く(saving_phase):
    """結合は行数ぶんかかる。その間もバーが動くこと。"""
    assert any("つないで" in m[3] for m in saving_phase.after), \
        "結合中の知らせが無い: %s" % [m[3] for m in saving_phase.after]


@pytest.mark.gui
def test_変換中だと分かる(saving_phase):
    assert any("変換中" in m[3] for m in saving_phase.after), \
        "変換中の知らせが無い: %s" % [m[3] for m in saving_phase.after]


@pytest.mark.gui
def test_字幕の書き出しも伝える(saving_phase):
    assert any("字幕" in m[3] for m in saving_phase.after), \
        "字幕の書き出しの知らせが無い: %s" % [m[3] for m in saving_phase.after]


@pytest.mark.gui
def test_最後まで完走してファイルができる(saving_phase):
    assert saving_phase.out.exists(), "結合したWAVができていない"


@pytest.mark.gui
def test_字幕も出ている(saving_phase):
    assert saving_phase.srt.exists(), "字幕(SRT)ができていない"
