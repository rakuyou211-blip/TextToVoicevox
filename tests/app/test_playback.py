# -*- coding: utf-8 -*-
"""再生・試聴まわりの見張り（レビュー指摘 A / B / C の再現）。

もとは _legacy/test_review_abc.py。上から下へ流れる1本の台本だったものを、
落ちた場所が名前で分かるように割りました。名前は元のまま残しています。

見張っているのは3つです。

 A. 止められた再生が、次の再生の音を奪わないこと。
    Windows は再生をプロセスに1本しか持てないので、止めたはずの再生が
    そのまま PlaySound まで進むと、いま鳴っている音を差し替えたうえ
    直後の PURGE で黙らせてしまう（■停止→すぐ別の行を試聴、で無音になっていた）
 B. 先読み中の行を▶試聴したときに、同じ合成をもう1本投げないこと。
    投げるとエンジンを取り合って、先読みが無かったときより遅くなる
 C. 先読みが走っている間にカーソルが移った行を、捨てずに見直しに行くこと

どれもエンジンは要りません（合成と再生は差し替えます）。
"""
import glob
import os
import tempfile
import threading
import time

import pytest

import core


def _play_tmp():
    """再生用の一時ファイル（t2v_play_*.wav）の一覧。

    Windows の経路は「メモリから非同期」が使えないので一度ファイルに書く。
    止めた再生でここが増えていたら、書く前に切り上げていないということ。
    """
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "t2v_play_*.wav")))


# ================================================================ A（core側）
# 止められた再生は、音を出す手前で引き返す。
# winsound は差し替えて、本当に音が出ないところまで見る。
@pytest.fixture
def playsound_calls(monkeypatch):
    """winsound.PlaySound の呼び出しを覚えておく（実際には鳴らさない）。
    引数が増えても黙って通らないよう *a, **kw で受ける。"""
    calls = []
    if core.IS_WIN:
        import winsound

        def fake_playsound(*a, **kw):
            calls.append(a)

        monkeypatch.setattr(winsound, "PlaySound", fake_playsound)
    return calls


@pytest.mark.skipif(not core.IS_WIN, reason="winsound を通る経路は Windows だけ")
def test_止まっている再生はPlaySoundを1回も出さない(playsound_calls, seed):
    stop = threading.Event()
    stop.set()
    core.play_wav_blocking(seed.wav(0.3), stop_event=stop)
    assert not playsound_calls, ("止めた再生が PlaySound を%d回出した"
                                "（いま鳴っている音を奪う）" % len(playsound_calls))


def test_一時ファイルも作らない(playsound_calls, seed):
    before = _play_tmp()
    stop = threading.Event()
    stop.set()
    core.play_wav_blocking(seed.wav(0.3), stop_event=stop)
    assert _play_tmp() == before, "止めた再生が一時ファイルを書いた"


def test_すぐ返る(playsound_calls, seed):
    """止められていると分かっているのだから、待たずに帰ってくること。"""
    stop = threading.Event()
    stop.set()
    started = time.perf_counter()
    core.play_wav_blocking(seed.wav(0.3), stop_event=stop)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.05, "止めた再生が%.0fms居座った" % (elapsed * 1000)


# ================================================================ A（画面側）
# 試聴・声サンプル・辞書の読み試聴の3経路すべてで、
# 「合成を待っている間に止められた／次の再生が始まった」ら鳴らさない。
class PlayWatch:
    """差し替えたものが何をされたかの控え。

    played … play_wav_blocking を呼んだ回数
    msgs   … 画面へ投げた通知（("preview_done", ...) など）
    synth  … 合成を投げた本文。0件であることを見るために要る
    cache  … 先読みがキャッシュに置いた、ということにする置き場（"hit" だけ見る）
    """

    def __init__(self, app, wav):
        self.app = app
        self.wav = wav
        self.played = []
        self.msgs = []
        self.synth = []
        self.cache = {}
        self.voice = dict(speed=1.0, pitch=0.0, intonation=1.0, volume=1.0)

    def target(self, line, style_id):
        """_prefetch_target と同じ形。作り方が1つでもずれると当たらないので、
        本体（_prefetch_line）と同じ材料から組む。"""
        return (line, style_id, tuple(sorted(self.voice.items())))


@pytest.fixture
def preview(app, monkeypatch, seed):
    """合成・再生・通知を差し替えた画面。エンジンにはつながない。

    偽物はどれも **kw で余りを受ける（本体の引数が増えたときに
    黙って通ってしまう事故が過去に3回あった）。
    引数の名前も本体の署名に合わせてある（core.play_wav_blocking /
    vv_synthesize_with_kana / vv_dict_hash / synth_cache_get / synth_cache_put
    の並びと名前を 2026-09-23 に照合済み。名前がずれていると、本体が
    キーワードで呼ぶようになった日に「偽物だけ落ちる」ことになる）。"""
    watch = PlayWatch(app, seed.wav(0.3))

    def fake_play(wav_bytes, stop_event=None, **kw):
        watch.played.append(1)

    def fake_synth(base_url, text, speaker_id, **kw):
        watch.synth.append(text)
        return watch.wav, "テスト"

    monkeypatch.setattr(core, "play_wav_blocking", fake_play)
    monkeypatch.setattr(core, "vv_synthesize_with_kana", fake_synth)
    monkeypatch.setattr(core, "vv_dict_hash", lambda base_url, timeout=10, **kw: "x")
    monkeypatch.setattr(core, "vv_reading", lambda *a, **kw: "テスト")
    monkeypatch.setattr(core, "synth_cache_get",
                        lambda key, **kw: watch.cache.get("hit"))
    monkeypatch.setattr(core, "synth_cache_put", lambda key, wav_bytes, **kw: None)

    # 通知は本物のキューに積まず控えに回す（画面の tick に拾われると数えられない）
    def fake_put(m, **kw):
        # ここだけ **kw を落としていた（上の「偽物はどれも **kw」の破れ）。
        # 受けていないと、本体が put に一言添えた日に TypeError になり、
        # 通知を見ている見張りがまとめて落ちる
        watch.msgs.append(m)

    monkeypatch.setattr(app.q, "put", fake_put)
    return watch


@pytest.fixture
def stopped_preview(preview):
    """合成を待っている間に■停止された、という場面をひとつ作る。
    ここで作った1回の試聴を、下の3つの見張りが別々の角度から見る。"""
    stop = threading.Event()
    stop.set()
    preview.app._play_gen = 5
    preview.app._preview_worker("一行目です。", 1, preview.voice, 1, stop, 5)
    return preview


@pytest.mark.gui
def test_試聴_止められていたら鳴らさない(stopped_preview):
    assert not stopped_preview.played, "止めたのに鳴らした（次の再生の音を奪う）"


@pytest.mark.gui
def test_試聴_画面を戻す通知は出す(stopped_preview):
    """鳴らさないのは正しいが、黙って終わるとボタンが灰色のまま残る。"""
    assert any(m[0] == "preview_done" and m[-1] == 5
               for m in stopped_preview.msgs), "画面を戻す通知が出ていない"


@pytest.mark.gui
def test_試聴_再生中表示を出さない(stopped_preview):
    """鳴っていないのに「再生中」と出すと、■停止が効かないように見える。"""
    assert not any(m[0] == "preview_playing"
                   for m in stopped_preview.msgs), "鳴らさないのに再生中と出した"


@pytest.mark.gui
def test_試聴_世代が古ければ鳴らさない(preview):
    """止められてはいないが、待っている間に次の再生が始まっていた場合。"""
    preview.app._play_gen = 6
    preview.app._preview_worker("一行目です。", 1, preview.voice, 1,
                                threading.Event(), 5)
    assert not preview.played, "古い世代の試聴が鳴った（今の再生の音を奪う）"


@pytest.mark.gui
def test_声サンプル_止められていたら鳴らさない(preview):
    stop = threading.Event()
    stop.set()
    preview.app._play_gen = 7
    # サンプルは取得済みということにする（エンジンに聞きに行かせない）
    preview.app._sample_cache[("u", 3)] = preview.wav
    preview.app._sample_worker("声", 3, "u", stop, 7)
    assert not preview.played, "止めたのに声サンプルが鳴った"


@pytest.mark.gui
def test_辞書の読み試聴_止められていたら鳴らさない(preview):
    stop = threading.Event()
    stop.set()
    preview.app._play_gen = 7
    preview.app._dict_preview_worker("単語", 1, preview.voice, stop, 7)
    assert not preview.played, "止めたのに辞書の読み試聴が鳴った"


@pytest.mark.gui
def test_何もなければ普通に鳴る(preview):
    """直しすぎていないことの確認。止めてもいない・世代も合っているなら鳴る。"""
    preview.app._play_gen = 7
    preview.app._preview_worker("一行目です。", 1, preview.voice, 1,
                                threading.Event(), 7)
    assert preview.played == [1], "普通の試聴が鳴らなくなった"


# ================================================================ B
# 先読み中の行を▶試聴しても、二重に合成しない。
# 同じ合成を2本投げるとエンジンを取り合って、先読み前より遅くなっていた。
@pytest.mark.gui
def test_先読み中の同じ行は二重に合成しない(preview):
    app = preview.app
    done = threading.Event()
    app._prefetch_done = done
    app._prefetch_target = preview.target("二行目です。", 1)
    app._play_gen = 8

    th = threading.Thread(target=app._preview_worker,
                          args=("二行目です。", 1, preview.voice, 2,
                                threading.Event(), 8),
                          daemon=True)
    th.start()
    try:
        time.sleep(0.4)
        assert th.is_alive() and not preview.synth, (
            "先読みが終わるのを待たずに合成を投げた（投げた本文: %s）" % preview.synth)

        preview.cache["hit"] = preview.wav      # 先読みがキャッシュに置いた
        done.set()
        th.join(3)
        assert not preview.synth, "先読みの結果を使わず合成した（%s）" % preview.synth
        assert preview.played == [1], "先読みの結果で鳴らしていない"
    finally:
        done.set()          # 途中で落ちても待ち続けさせない
        th.join(3)


@pytest.mark.gui
def test_違う行なら待たずに合成する(preview):
    """先読みしているのが別の行なら、待つ理由がない（待つと▶試聴が固まる）。"""
    app = preview.app
    app._prefetch_done = threading.Event()      # 立てないまま＝先読み中
    app._prefetch_target = preview.target("三行目です。", 1)
    app._play_gen = 8

    started = time.perf_counter()
    app._preview_worker("四行目です。", 1, preview.voice, 4, threading.Event(), 8)
    elapsed = time.perf_counter() - started

    assert preview.synth == ["四行目です。"], "合成した本文がおかしい（%s）" % preview.synth
    assert elapsed < 0.3, "別の行の先読みを待ってしまった（%.0fms）" % (elapsed * 1000)


@pytest.mark.gui
def test_待っている最中の停止にすぐ応じる(preview):
    """先読み待ちのループが■停止を見ていないと、止めても30秒粘ってしまう。"""
    app = preview.app
    done = threading.Event()
    app._prefetch_done = done
    app._prefetch_target = preview.target("五行目です。", 1)
    app._play_gen = 8

    stop = threading.Event()
    th = threading.Thread(target=app._preview_worker,
                          args=("五行目です。", 1, preview.voice, 5, stop, 8),
                          daemon=True)
    th.start()
    try:
        time.sleep(0.2)
        pressed = time.perf_counter()
        stop.set()
        th.join(2)
        elapsed = time.perf_counter() - pressed
        assert not th.is_alive(), ("■停止から%.0fmsたっても先読み待ちから抜けない"
                                  % (elapsed * 1000))
        assert not preview.played, "止めたのに鳴った"
    finally:
        done.set()
        stop.set()
        th.join(3)


# ================================================================ C
# 先読みが走っている間にカーソルが移ると、その行の先読みが捨てられていた。
# 終わるころに見直す予約を入れて拾い直す。
@pytest.mark.gui
def test_先読み中なら_あとで見直す予約を入れる(app):
    app._prefetching = True
    app._prefetch_after = None
    try:
        app._prefetch_line()
        assert app._prefetch_after is not None, "見直す予約を入れていない（行を捨てている）"
    finally:
        # 立てた _prefetching も戻す（元の台本は戻していた）。画面は app fixture が
        # 作り直すので実害は出ないが、場面を立てたまま去ると、あとで同じ画面を
        # 使い回す書き方にしたときに次のテストが「先読み中」から始まってしまう
        if app._prefetch_after:
            app.after_cancel(app._prefetch_after)
            app._prefetch_after = None
        app._prefetching = False


def _make_engine_ready(app):
    """「エンジンにつながっていて、話者も本文も決まっている」画面にする。

    実際につなぎはしない。_engine_ready() が見ているのは conn_state と
    speakers の2つだけなので、接続時に本体がやるのと同じ順で自分で整える。
    本文は起動時に last_text.txt から戻っていることがあるため、分かっている
    1行に置き換える（保存するのは _on_close と定期保存だけで、どちらも
    テスト中は走らない。万一書けても土台が元に戻して落としてくれる）。"""
    app.speakers = [("テスト話者（ノーマル）", 1, "test-uuid")]
    app._build_char_map()
    app.char_cb.current(0)
    app._char_selected()            # ここまでで _current_speaker() が返るようになる
    app._set_conn_state("ok", "0.0.0-test")
    app.text.delete("1.0", "end")
    app.text.insert("1.0", "先読みの行です。")
    app.text.mark_set("insert", "1.0")


@pytest.mark.gui
def test_本番の処理中は予約し続けない(preview, monkeypatch):
    """書き出しや連続再生の最中は、予約を取り直して空回りしないこと
    （700msごとに自分を呼び直す形なので、ここで止めないと回り続ける）。

    _prefetch_line の門は
    「busy or _previewing or not _engine_ready() or not can_play()」で
    ひとつながりなので、つながっていない画面のまま呼ぶと _engine_ready() が
    False の時点で抜ける。それだと busy の門を丸ごと外しても通ってしまい、
    名前は「本番の処理中は」なのに確かめているのは「つながっていないから」
    だった。エンジンが使える形を整えて、busy だけが理由になる場面で見る。
    """
    app = preview.app
    _make_engine_ready(app)
    # 再生できるかどうかを環境任せにしない（できない環境では、やはり busy を
    # 見る前に抜けてしまう）
    monkeypatch.setattr(core, "can_play", lambda *a, **kw: True)

    # まず、この場面なら本当に先読みが始まることを確かめる。ここが始まらない
    # 場面だと、下の見張りはまた busy 以外の理由で通ってしまう
    # （話者が選べていない・行が空、などでも静かに return する）
    app._prefetching = False
    app._prefetch_target = None
    app._prefetch_done = None
    app.busy = False
    app._prefetch_line()
    started, done = app._prefetch_target, app._prefetch_done
    if done is not None:
        done.wait(3)          # 走り出した先読みを回収してから本番へ
    app._prefetching = False
    app._prefetch_target = None
    app._prefetch_done = None
    assert started is not None, ("この場面では先読みが始まらない。"
                                 "下の見張りが busy 以外の理由で通ってしまう")

    app._prefetch_after = None
    app.busy = True
    try:
        app._prefetch_line()
    finally:
        app.busy = False
    assert app._prefetch_after is None, "本番の処理中なのに予約を入れ続けている"
    # 予約だけでなく、先読みそのものを始めていないこと。busy の門を外すと
    # ここに行き先が入る（＝本番の合成とエンジンを取り合う）ので、
    # 「busy を見た」ことがこの1行で分かる
    assert app._prefetch_target is None, "本番の処理中なのに先読みを始めた"
