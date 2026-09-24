# -*- coding: utf-8 -*-
"""壊れにくさ（第1段で直したバグ5件）の見張り。

もとは _legacy/test_stage1.py。どれも「実際に起きていたこと」の再現なので、
ここが落ちたら、直したはずのものが戻ってきたということです。
"""
import io
import json
import os
import threading
import time

import pytest

import conftest as base
import core
import main

SETTINGS = os.path.join(base.APP_DIR, "settings.json")


# ================================================================ バグ2
# 設定が1つ壊れていると、置換ルールも声プリセットも話者の記憶も
# まとめて失われていた（int() が落ちて、以降の読み込みが丸ごと諦められていた）。
BROKEN_SETTINGS = {
    "theme": "zunda",
    "dpi": "こわれた値",                 # ここで int() が落ちる
    "replace_rules": [["あ", "い"], ["う", "え"]],
    "presets": [{"name": "テスト声", "speed": 1.2}],
    "speaker": "春日部つむぎ（ノーマル）",
    "gap": 0.4,
}


@pytest.fixture
def broken_settings():
    """壊れた設定を置く。app より先に書くこと（App() が読むのは起動時だけ）。
    元に戻すのは土台（guard_user_files）がやる。"""
    with io.open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(BROKEN_SETTINGS, f, ensure_ascii=False)
    return BROKEN_SETTINGS


@pytest.mark.gui
@pytest.mark.writes_settings
def test_壊れた設定に巻き込まれない(broken_settings, app):
    assert len(app.replace_rules) == 2, "置換ルール2件が生き残らない"
    assert len(app.presets) == 1, "声プリセット1件が生き残らない"
    assert app._saved_speaker == "春日部つむぎ（ノーマル）", "話者の記憶が消えた"
    assert app.dpi_var.get() == 300, "壊れた項目が既定値になっていない"


@pytest.mark.gui
@pytest.mark.writes_settings
def test_保存内容にもルールとプリセットが入る(broken_settings, app):
    """終了時に空で上書きされないこと（_on_close は呼べないので中身で見る）。"""
    saved = app._settings_dict()
    assert len(saved["replace_rules"]) == 2
    assert len(saved["presets"]) == 1


# ================================================================ バグ1
# .vvproj の保存をキャンセルしたあと、ボタンが押せないままだった。
@pytest.fixture
def vvproj_app(app):
    app.conn_state = "ok"
    app.speakers = [("テスト話者", 1, "uuid")]
    return app


@pytest.mark.gui
def test_キャンセル中はvvprojボタンを押せない(vvproj_app):
    vvproj_app._vvproj_cancel = threading.Event()
    vvproj_app.cancel_vvproj()
    assert str(vvproj_app.vvproj_btn.cget("state")) == "disabled"


@pytest.mark.gui
def test_戻したら押せる_接続中(vvproj_app):
    vvproj_app._vvproj_cancel = threading.Event()
    vvproj_app.cancel_vvproj()
    vvproj_app._vvproj_restore_button()
    assert str(vvproj_app.vvproj_btn.cget("state")) == "normal"
    assert "プロジェクト保存" in str(vvproj_app.vvproj_btn.cget("text")), "ラベルが戻らない"


@pytest.mark.gui
def test_未接続なら灰色のまま(vvproj_app):
    """勝手に有効化しない（つながっていないのに押せると、押した先で失敗する）。"""
    vvproj_app.conn_state = "lost"
    vvproj_app._vvproj_cancel = threading.Event()
    vvproj_app.cancel_vvproj()
    vvproj_app._vvproj_restore_button()
    assert str(vvproj_app.vvproj_btn.cget("state")) == "disabled"


# ================================================================ バグ4
# ひとつ前の再生から遅れて届いた完了通知が、今の再生を止めていた。
# 世代（_play_gen）で古い通知を捨てる。
@pytest.fixture
def playing_app(app):
    app._play_gen = 7
    app._previewing = True
    app.stop_btn.config(state="normal")
    return app


@pytest.mark.gui
def test_古い連続再生の通知は捨てる(playing_app):
    playing_app._dispatch_msg(("playall_done", True, True, 3, 0, 6))
    assert playing_app._previewing is True, "古い通知で再生が止まった"
    assert str(playing_app.stop_btn.cget("state")) == "normal", "停止ボタンが死んだ"


@pytest.mark.gui
def test_古い試聴の通知も捨てる(playing_app):
    playing_app._dispatch_msg(("preview_done", True, "テスト", 6))
    assert playing_app._previewing is True


@pytest.mark.gui
def test_古い行通知でしおりが動かない(playing_app):
    playing_app._dispatch_msg(("playall_line", 1, "本文", 1, 0, 4, 6))
    assert playing_app._bookmark != 1


@pytest.mark.gui
def test_今の世代の通知は効く(playing_app):
    playing_app._dispatch_msg(("playall_done", True, True, 3, 0, 7))
    assert playing_app._previewing is False


# ================================================================ バグ3
# 終了時にワーカーの後片づけを待たず、書きかけのファイルが残ることがあった。
@pytest.mark.gui
def test_終了時にワーカーを待つ(app, tmp_path):
    marker = tmp_path / "片づけた.txt"
    stop = threading.Event()

    def slow_worker():
        try:
            while not stop.is_set():
                time.sleep(0.02)
        finally:
            marker.write_text("片づけた", encoding="utf-8")

    app._spawn(slow_worker)
    assert any(t.is_alive() for t in app._workers), "ワーカーを控えていない"

    start = time.monotonic()
    assert app._wait_workers(0.4) is False, "終わらないワーカーを待ちきったと言った"
    waited = time.monotonic() - start
    # 上は緩めにとる（他の仕事で混んでいるPCだと、0.4秒の待ちが1秒以上に伸びる）
    assert 0.3 < waited < 3.0, "待ち時間がおかしい（%.2f秒）" % waited

    stop.set()
    assert app._wait_workers(2.0) is True, "終わったのに待ち切れない"
    assert marker.exists(), "finally が動いていない（後片づけされていない）"


# ================================================================ バグ5
# 「1行の失敗で全行投げ切る」は実在しなかった（executor.map の結果イテレータが、
# 例外で抜けるときに未着手のジョブを自分でキャンセルする）。
# 改修の前後で同じ結果になることを実測済み。退行の見張りとして残す。
@pytest.mark.gui
def test_合成が失敗したら残りを投げない(app, tmp_path, monkeypatch, seed):
    calls = []
    lock = threading.Lock()
    real_sleep = time.sleep

    def fake_synth(base_url, text, speaker, **kw):
        """本体の引数が増えても黙って通らないよう **kw で受ける。"""
        with lock:
            calls.append(text)
        if text == "BAD":
            raise RuntimeError("わざと失敗")
        real_sleep(0.2)                      # 1行あたりの合成時間の代わり
        return seed.wav(0.05)

    monkeypatch.setattr(core, "vv_synthesize_cached", fake_synth)
    monkeypatch.setattr(main.time, "sleep", lambda s: None)   # リトライの待ちを飛ばす
    monkeypatch.setattr(app, "_dict_hash_tracker", lambda: (lambda: "x"))

    jobs = [("BAD", 1, None)] + [("ok%02d" % i, 1, None) for i in range(29)]
    out = tmp_path / "出力.wav"
    app._synth_worker(jobs, [[i] for i in range(len(jobs))], {},
                      str(out), "combine", 0.0, "wav", False)

    tried = len([t for t in calls if t != "BAD"])
    assert tried < 10, "失敗したあとも投げ続けている（%d / 29行）" % tried
    assert any(m[0] == "error" for m in list(app.q.queue)), "エラーが通知されない"
    assert not out.exists(), "一時ファイルが残った"
