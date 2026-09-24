# -*- coding: utf-8 -*-
"""本物のエンジンで、本物の音を鳴らして確かめる分。

もとは _legacy/test_stage1_live.py。第1段で入れた「再生の世代番号」が、
偽物の差し替えではなく実機で効いているかを見ます。ここで見張っている事故は
どれも実際に起きたもので、偽物では再現できませんでした。

見張っているもの:

  - 試聴と連続再生が最後まで通り、終わったら待機に戻る
  - 途中で止めるとしおりができ、そこから再開する（先頭に戻らない）
  - 停止した直後に次を始めても、その再生を止められる
    （前の再生から遅れて届く完了通知が、いまの再生を止めていた＝バグ4）
  - 停止の安全網（停止から1.5秒後の _recover_if_stuck）が、
    すぐ始めた次の再生に割り込まない
  - 鳴らしたあとの一時ファイルを %TEMP% に残さない

元の台本の check() は14個。1つも落とさずに残してありますが、**同じ再生を
見ている確認は1本の test にまとめてあります**（下の「なぜまとめたか」）。

エンジンと、音が出る環境が要ります。エンジンが動いていなければ土台
（engine fixture）がヘッドレスで起こし、自分で起こした分だけを止めます
（本人が開いている VOICEVOX やエディタには触りません）。

なぜまとめたか:
  1本ごとに App を作り直して実機で鳴らすので、確認の数だけ時間がかかる。
  14本に割った形は2分近くかかり、そのうえ App を作り直す回数だけ
  「画面がエンジンにつながらない」「Tcl が自分の部品を見失う」を踏む機会が
  増えていた（実測で14本のうち1本が接続できずに飛んだ）。
  1回の再生で分かることは1本の test にまとめ、assert はそのまま全部残した。
  落ちたときにどれが駄目だったかは、assert の日本語のメッセージで分かる。

まとめたときに落ちていたもの（2026-09-23 に足し直した）:
  元の台本は1つの App で 試聴 → 連続再生 → 停止 → しおり再開 → 連続再生 →
  停止 → 連続再生 → 停止 → 声サンプル を続けて走らせていた。後半が動いたこと
  自体が「前の再生のあとでも次の操作を受け付ける」の確認になっていて、
  _previewing が立ちっぱなしになる事故はこの並びでしか表に出ない。
  テストごとに App を作り直す形にしたことで、その連続性がどこにも残らなく
  なっていた。下の「ひと続きに通す」が、その並びをそのまま1本にしたもの。
  再生の一時ファイルの照合も、元と同じく通し全体（試聴・途中で止めた連続
  再生・声サンプルを含む）の前後で見る。
"""
import glob
import os
import re
import tempfile
import time

import pytest

import core

# 画面（tkinter）が無い環境では、この1本ごと飛ばす。CI の Linux には python3-tk が
# 入っていないので、ここで素の import をすると「集める」段階でCIごと倒れる（2026-09-24 実測）。
main = pytest.importorskip("main")

# 4行あることに意味がある。「先頭から4行」「しおりから残り何行」を数えるため。
TEXT = "一行目です。\n二行目です。\n三行目です。\n四行目です。"
LINES = 4


# ---------------------------------------------------------------- 小道具
def _play_tmp():
    """再生が %TEMP% に置く作業ファイルの一覧。

    winsound は「メモリから非同期再生」を受け付けないので、途中で止められる
    再生だけ一度ファイルに書き出す経路がある（core.play_wav_blocking）。
    その置き土産を数えるためのもの。"""
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "t2v_play_*.wav")))


def _wait_idle(app, pump, seconds=180):
    """再生が終わって待機に戻るまで、画面を回しながら待つ。"""
    end = time.time() + seconds
    while time.time() < end and app._previewing:
        pump(0.2)
    pump(0.6)          # 完了通知がキューから画面に届くまでの一息


def _lines_read(app, pump, wait=10.0):
    """状況欄の「（N行…）」から、読んだ行数を拾う。分からなければ -1。"""
    end = time.time() + wait
    while time.time() < end:
        found = re.search(r"（(\d+)行", app.status_var.get())
        if found:
            return int(found.group(1))
        pump(0.2)
    return -1


def _put_cursor(app, line):
    app.text.mark_set("insert", "%d.0" % line)
    app.update()


def _start(app, action, label):
    """再生をひとつ始めて、本当に始まったことをその場で確かめる。

    preview_selected / play_from_cursor / play_speaker_sample は、前の再生が
    残っている（_previewing が立っている）と何もせず return する。始まって
    いないのに先へ進むと、「立ちっぱなしの _previewing」を新しい再生と
    見間違えて通ってしまうので、世代番号が進んだことを見る。
    世代番号はどの再生経路でも、ワーカーを起こす直前に必ず1つ進む。"""
    gen = app._play_gen
    action()
    assert app._play_gen > gen, \
        "%sが始まりませんでした（前の再生のあとの操作を受け付けていない。" \
        "状況欄は「%s」）" % (label, app.status_var.get())


# ---------------------------------------------------------------- 前提
@pytest.fixture
def connected(app, engine, pump):
    """エンジンにつながった画面。土台の connected_app と同じ役目を、
    こちらから接続をうながす形でやる。

    起動直後の自動接続は「20秒だけ2秒おきに試して、あとは黙る」
    （main._auto_connect → _start_connect_retry(20)）。手で使うぶんには
    ちょうどいいが、実機の確認は App を何度も作り直すので、前の再生で
    エンジンがふさがっているあいだにこの20秒を使い切ってしまうことがある。
    そうなると、あとはただ待つだけになって接続できずに飛ぶ
    （2026-09-23 実測。14本のうち1本が40秒待って skip した）。
    ここでは、つながるまで「エンジン接続確認」を自分で押し続ける
    （ボタンと同じ check_engine。二重に走らないよう本体側で弾かれる）。"""
    end = time.time() + 60
    while time.time() < end and app.conn_state != "ok":
        app.check_engine(quiet=True)
        pump(1.0)
    if app.conn_state != "ok":
        # エンジンがこちらから見えているのに画面がつながらないなら、環境の話では
        # なく本当に困っている。skip で黙って消すと、誰も気づかないまま
        # 「実機の確認が1本減っている」状態が続く（2026-09-24 に2回続けて
        # conn_state="unknown" で消えていた）。見えているときは落とす。
        reachable = core.vv_probe(main.VOICEVOX_DEFAULT, timeout=3)
        detail = "conn_state=%s／画面の表示=%r／こちらからの確認=%s" % (
            app.conn_state, app.engine_var.get(), reachable.get("status"))
        if reachable.get("ok"):
            pytest.fail("エンジンは動いているのに画面がつながりません（%s）。"
                        "接続確認の経路を見てください" % detail)
        pytest.skip("エンジンに届きません（%s）" % detail)
    return app


@pytest.fixture
def live_app(connected, monkeypatch, tmp_path):
    """4行の本文を入れた、エンジンにつながった画面。

    本文の自動保存（60秒ごと）の行き先を作業用フォルダへ逃がしておく。
    実機の確認は1本で1分近くかかるので、放っておくと本人の last_text.txt が
    テストの本文で上書きされる。見張りを黙らせる（writes_settings）のではなく
    書き込み先をずらすことで、うっかりの書き換えは今までどおり落ちるようにする。"""
    app = connected
    monkeypatch.setattr(main, "TEXT_CACHE_PATH", str(tmp_path / "last_text.txt"))
    if not core.can_play():
        pytest.skip("この環境では音を鳴らせません（再生の確認はできない）")
    # 話者が取れていないまま再生を呼ぶと案内の小窓が開き、テストが止まる。
    assert app._current_speaker() is not None, \
        "話者が選ばれていない（このまま再生を呼ぶと小窓が開いて止まります）"
    app.text.delete("1.0", "end")
    app.text.insert("1.0", TEXT)
    app.update()
    return app


@pytest.fixture
def stopped_midway(live_app, pump):
    """先頭から連続再生して、2秒だけ鳴らしてから止めた状態。"""
    app = live_app
    _put_cursor(app, 1)
    app.play_all()
    pump(2.0)
    app.stop_playall()
    _wait_idle(app, pump)
    return app


@pytest.fixture
def restarted_after_stop(live_app, pump):
    """止めたあと、安全網が効く時刻（停止から1.5秒後）を跨いでから
    新しい連続再生を始めた状態。バグ4が起きていた並びそのもの。

    このあと旧ワーカーの完了通知が遅れて届く。世代番号で捨てられなければ、
    新しい再生の停止ボタンが消え、鳴っている音を止められなくなる。"""
    app = live_app
    _put_cursor(app, 1)
    app.play_all()
    pump(1.0)
    app.stop_playall()          # 1.5秒後に _recover_if_stuck が動く
    pump(2.0)                   # そのタイミングを跨がせる
    _put_cursor(app, 1)
    gen = app._play_gen
    app.play_all()              # 新しい再生
    # 前の再生が終わっていないと play_all は黙って return する。そのまま進むと
    # 「_previewing が立ちっぱなし」を新しい再生と見間違えて通ってしまう
    assert app._play_gen > gen, \
        "新しい連続再生が始まりませんでした（前提が崩れています）"
    pump(1.5)
    try:
        yield app
    finally:
        app.stop_playall()      # 鳴らしたままテストを抜けない
        _wait_idle(app, pump)


@pytest.fixture
def sample_after_stop(live_app, pump):
    """停止の予約（_recover_if_stuck）が生きているうちに声サンプルを始め、
    その予約の時刻を跨いだ状態。

    元の台本は停止から0.3秒で次を始めていた。だが前の再生が終わる前に呼ぶと
    play_speaker_sample は黙って return するので、「立ちっぱなしの
    _previewing」を見て通ってしまう。そこで前の再生が終わるのを待ち、
    もう一度■停止を押して予約を張り直してから次を始める
    （人が「止める → すぐ声サンプルを押す」のと同じ並び。待つあいだに
    1.5秒が過ぎてしまうので、予約だけ取り直している）。"""
    app = live_app
    app.play_all()
    pump(1.0)
    gen = app._play_gen
    app.stop_playall()
    _wait_idle(app, pump)       # 前の再生が本当に終わるまで待つ
    app.stop_playall()          # 1.5秒後の _recover_if_stuck を張り直す
    # 予約が本当に入っているところまで見る。入っていなければ「跨ぐもの」が
    # 無いので、このあとの「割り込まない」はどう転んでも通ってしまう
    assert "recover" in app._ticks, \
        "停止の安全網（1.5秒後の _recover_if_stuck）が予約されていない"
    app.play_speaker_sample()   # その予約が生きているうちに次を始める
    assert app._play_gen > gen, \
        "声サンプルが始まりませんでした（前提が崩れています）"
    pump(2.0)                   # 予約の時刻を跨がせる
    try:
        yield app
    finally:
        app.stop_playall()
        _wait_idle(app, pump, 60)


@pytest.fixture
def play_tmp_before(live_app):
    """鳴らす前の %TEMP% の一時ファイルの一覧。

    以前は played_once が (app, 一覧) のタプルで返していて、受け側が
    `_app, before = played_once` と分解していた。使わないほうの名前が
    残って読みにくいので、一覧はこちらに分けた。played_once と通しの
    test がこれを先に呼ぶので、控えを取るのは必ず鳴らす前になる。"""
    return _play_tmp()


@pytest.fixture
def played_once(live_app, play_tmp_before, pump):
    """連続再生を最後まで通した状態。"""
    app = live_app
    _put_cursor(app, 1)
    app.play_all()
    _wait_idle(app, pump)
    return app


# ================================================================ 接続
# slow が要る。connected は「エンジン接続確認」を押しながら最長60秒待つので、
# つながらない日はここだけで1分持っていかれる（slow を外して回しても同じ）。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_エンジンに接続できた(connected):
    """つながらなければ connected fixture がテストを飛ばす。
    ここでは「つながった」と言える中身が揃っているかまで見る。話者が
    取れていないと、このあとの再生はどれも案内の小窓で止まってしまう。"""
    assert connected.conn_state == "ok", "接続状態が ok にならない"
    assert connected.speakers, "話者の一覧が空（つながったとは言えない）"
    assert connected._current_speaker() is not None, "話者が選ばれていない"


# ================================================================ 再生の基本
# 世代番号を入れた通知の経路が、実機でも素直に流れるか。
# 「試聴が最後まで通る」と「試聴後は待機に戻る」は同じ1回の試聴を見ている。
# _previewing が立ったままだと、以降の試聴も連続再生も反応しなくなる
# （＝「試聴が動かない」に見える）ので、終わり方まで続けて確かめる。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_試聴が最後まで通って待機に戻る(live_app, pump):
    _put_cursor(live_app, 2)
    live_app.preview_selected()
    _wait_idle(live_app, pump)
    assert "試聴 おわり" in live_app.status_var.get(), \
        "試聴が終わったと言わない（状況欄は「%s」）" % live_app.status_var.get()
    assert live_app._previewing is False, "再生状態が立ったままで待機に戻らない"


# 「連続再生は先頭から4行」と「世代番号が進んでいる」も、同じ1回の連続再生を
# 見ている。世代番号は古い完了通知を捨てる仕組みの土台で、ここが進まないと
# 前の再生の通知でいまの再生が止まる（バグ4）。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_連続再生は先頭から4行読んで世代番号が進む(live_app, pump):
    """カーソルが3行目にあっても頭から読む（押した人の期待に一番近いのがこれ。
    途中から聴きたいときは「⏵ 続きから」を使う）。"""
    gen0 = live_app._play_gen
    _put_cursor(live_app, 3)
    live_app.play_all()
    _wait_idle(live_app, pump)
    read = _lines_read(live_app, pump)
    assert read == LINES, "先頭から4行にならない（%d行と言っている）" % read
    assert live_app._play_gen > gen0, "再生したのに世代番号が進んでいない"


# ================================================================ 途中で止める → しおりから再開
# 元の台本の「停止後は待機に戻る」「しおりができている」
# 「しおりの行から再開（先頭に戻らない）」の3つ。ひと続きの話なのでまとめた。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_途中で止めるとしおりができそこから再開する(stopped_midway, pump):
    """再開の実体が play_all を呼ぶと先頭に戻り、しおりが無意味になる。

    しおりは「あるかどうか」だけでは足りない。settings.json には前回の
    しおり（今回は10行目）が入っていて App が起動時に読み込むので、
    is not None は鳴らす前から真になってしまう。いま入れた4行の中を
    指しているところまで見る。"""
    app = stopped_midway
    assert app._previewing is False, "止めたのに再生状態が残っている"
    mark = app._bookmark
    assert mark is not None, \
        "どこまで読んだかを覚えていない（「⏵ 続きから」が使えない）"
    assert 1 <= mark <= LINES, \
        "しおりが今の本文の外を指している（%s行目。前回の設定のままでは？）" % mark

    app.play_from_bookmark()
    _wait_idle(app, pump)
    read = _lines_read(app, pump)
    assert read == LINES - mark + 1, \
        "しおり（%d行目）から残り%d行のはずが %d行（先頭に戻っていませんか）" \
        % (mark, LINES - mark + 1, read)


# ================================================================ 停止直後にすぐ次を始める（バグ4の本番）
# 遅れて届いた古い完了通知で、新しい再生の停止ボタンが消えていた。
# 「止められるか」まで見ないと直ったと言えないので、続けて止める。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_停止直後に始めた再生もちゃんと止められる(restarted_after_stop, pump):
    app = restarted_after_stop
    assert app._previewing is True, \
        "新しい再生が続いていない（遅れて届いた古い通知に消された）"
    assert str(app.stop_btn.cget("state")) == "normal", \
        "停止ボタンが死んでいる（鳴っている音を止められない）"
    app.stop_playall()
    _wait_idle(app, pump)
    assert app._previewing is False, "新しい再生が止まらない"


# ================================================================ 声サンプル
# 停止の安全網は「止めた再生の世代」のときだけ働くこと。1.5秒後に無条件で
# 効いてしまうと、鳴っている最中なのに待機表示に戻り、行のハイライトも
# 一時停止ボタンも消える（実測で起きていた）。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_停止の安全網が次の声サンプルに割り込まない(sample_after_stop, pump):
    app = sample_after_stop
    assert app._previewing is True, \
        "停止から1.5秒後の安全網が、次に始めた声サンプルを待機に戻した"
    _wait_idle(app, pump, 60)
    assert app._previewing is False, "声サンプルが鳴り終わっても待機に戻らない"


# ================================================================ 一時ファイル
# 正常に終われば finally で消える。消せなかったぶんは次の再生のときに
# 片づける持ち越しになっていて、最後は終了時の片づけが引き取る。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_再生の一時ファイルが残っていない(played_once, play_tmp_before):
    """元の台本では「再生が終われば一時ファイルはその場で消える」と
    「再生の一時ファイルが残っていない（%d個）」の2つ。
    後者は終了時の片づけ（_on_close がやること）まで済ませた状態を見る。
    _on_close はテストから呼べないので、同じ後片づけを直に呼ぶ。

    鳴らすのは played_once、鳴らす前の一覧は play_tmp_before が持っている
    （どちらも fixture なので、本文では leftover だけを見ればよい）。

    ここが見ているのは「正常に最後まで通した連続再生1回」だけ。
    試聴・途中で止めた連続再生・声サンプルまで含めた元の範囲は、
    下の通しの test が同じやり方で見ている。"""
    leftover = sorted(_play_tmp() - play_tmp_before)
    assert not leftover, "鳴り終わったのに %s が残っている" \
        % [os.path.basename(p) for p in leftover]

    core.sweep_play_tmp_on_exit()
    leftover = sorted(_play_tmp() - play_tmp_before)
    assert not leftover, "終了時の片づけでも消えない一時ファイルが%d個残った: %s" \
        % (len(leftover), [os.path.basename(p) for p in leftover])


# ================================================================ ひと続きに通す
# ここまでの test は1本ごとに App を作り直して、1つの並びだけを見ている。
# それだと「前の再生のあとでも次の操作を受け付ける」が誰も見ていない。
# _previewing が立ちっぱなしになる事故は、同じ App で操作を重ねたときだけ
# 表に出る（次の play_all / preview_selected は黙って return するので、
# 画面は静かなまま「押しても反応しない」になる）。
#
# そこで元の台本と同じ並びを1つの App に通す。節目ごとに _start() で
# 「次が本当に始まったか」を見るので、途中で受け付けなくなればそこで落ちる。
# 一時ファイルの照合も、元と同じく通し全体の前後で行う。
@pytest.mark.engine
@pytest.mark.gui
@pytest.mark.slow
def test_ひと続きに操作しても次の再生を受け付ける(live_app, pump, play_tmp_before):
    app = live_app
    try:
        # --- 試聴
        _put_cursor(app, 2)
        _start(app, app.preview_selected, "試聴")
        _wait_idle(app, pump)
        assert "試聴 おわり" in app.status_var.get(), \
            "試聴が終わったと言わない（状況欄は「%s」）" % app.status_var.get()
        assert app._previewing is False, "試聴のあと待機に戻らない"

        # --- 試聴のあとの連続再生
        _put_cursor(app, 3)
        _start(app, app.play_all, "試聴のあとの連続再生")
        _wait_idle(app, pump)
        read = _lines_read(app, pump)
        assert read == LINES, \
            "先頭から4行にならない（%d行と言っている）" % read

        # --- 最後まで通したあとの連続再生を、途中で止める → しおりから再開
        _put_cursor(app, 1)
        _start(app, app.play_all, "連続再生のあとの連続再生")
        pump(2.0)
        app.stop_playall()
        _wait_idle(app, pump)
        assert app._previewing is False, "止めたのに再生状態が残っている"
        mark = app._bookmark
        assert mark is not None, \
            "どこまで読んだかを覚えていない（「⏵ 続きから」が使えない）"
        assert 1 <= mark <= LINES, \
            "しおりが今の本文の外を指している（%s行目。前回の設定のままでは？）" % mark
        _start(app, app.play_from_bookmark, "止めたあとのしおり再開")
        _wait_idle(app, pump)
        read = _lines_read(app, pump)
        assert read == LINES - mark + 1, \
            "しおり（%d行目）から残り%d行のはずが %d行（先頭に戻っていませんか）" \
            % (mark, LINES - mark + 1, read)

        # --- 停止直後に始めた再生も止められるか（バグ4の並び）
        _put_cursor(app, 1)
        _start(app, app.play_all, "しおり再開のあとの連続再生")
        pump(1.0)
        app.stop_playall()      # 1.5秒後に _recover_if_stuck が動く
        pump(2.0)               # そのタイミングを跨がせる
        _put_cursor(app, 1)
        _start(app, app.play_all, "停止直後の連続再生")
        pump(1.5)
        assert app._previewing is True, \
            "停止直後に始めた再生が続いていない（遅れて届いた古い通知に消された）"
        assert str(app.stop_btn.cget("state")) == "normal", \
            "停止ボタンが死んでいる（鳴っている音を止められない）"
        app.stop_playall()
        _wait_idle(app, pump)
        assert app._previewing is False, "停止直後に始めた再生が止まらない"

        # --- 声サンプル（停止の安全網が生きているうちに始める）
        # 元の台本は停止から0.3秒で次を始めていたが、前の再生が終わる前に呼ぶと
        # play_speaker_sample は黙って return する。前の再生を待ってから、もう
        # 一度■停止を押して 1.5秒後の予約を張り直す（sample_after_stop と同じ）。
        _start(app, app.play_all, "停止のあとの連続再生")
        pump(1.0)
        app.stop_playall()
        _wait_idle(app, pump)
        app.stop_playall()
        # 予約が入っていなければ「跨ぐもの」が無く、下の確認が空振りになる
        assert "recover" in app._ticks, \
            "停止の安全網（1.5秒後の _recover_if_stuck）が予約されていない"
        _start(app, app.play_speaker_sample, "停止直後の声サンプル")
        pump(2.0)               # 予約の時刻を跨がせる
        assert app._previewing is True, \
            "停止から1.5秒後の安全網が、次に始めた声サンプルを待機に戻した"
        _wait_idle(app, pump, 60)
        assert app._previewing is False, "声サンプルが鳴り終わっても待機に戻らない"

        # --- 一時ファイル（通し全体の前後で照合する）
        # 元の台本と同じ範囲。試聴・途中で止めた連続再生・声サンプルが置いた分も
        # ここに入る（止めた再生の分は次の再生のときに片づく持ち越し）。
        leftover = sorted(_play_tmp() - play_tmp_before)
        assert not leftover, "ひと続きに鳴らし終わったのに %s が残っている" \
            % [os.path.basename(p) for p in leftover]
        core.sweep_play_tmp_on_exit()
        leftover = sorted(_play_tmp() - play_tmp_before)
        assert not leftover, "終了時の片づけでも消えない一時ファイルが%d個残った: %s" \
            % (len(leftover), [os.path.basename(p) for p in leftover])
    finally:
        # 途中で落ちたときに音を鳴らしたまま抜けない。鳴っているファイルを
        # Windows が掴んだままだと、%TEMP% の見張りまで巻き添えで落ちる。
        app.stop_playall()
        _wait_idle(app, pump, 60)
