# -*- coding: utf-8 -*-
"""レビュー指摘 A/B/C の再現テスト（エンジン不要）。
設定と本文は退避して必ず戻す。終了は destroy() だけ。"""
import glob, io, os, shutil, sys, tempfile, threading, time, wave

# 私物の絶対パス（本人のPCのユーザー名まで見えてしまう）は、公開のときに困るので外した。
# この台本は tests/_legacy/ にあるので、3つ上がアプリのフォルダ。
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
keep = {}
for name in ("settings.json", "last_text.txt"):
    p = os.path.join(APP, name)
    if os.path.exists(p):
        keep[p] = p + ".abcbak"
        shutil.copy2(p, keep[p])
ok = []


def check(name, cond):
    if cond:
        ok.append(name); print("  OK  " + name)
    else:
        raise AssertionError("NG: " + name)


def tiny_wav(sec=0.3):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b"\x00\x00" * int(24000 * sec))
    return b.getvalue()


def play_tmp():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "t2v_play_*.wav")))


try:
    import core, main

    # ================= A: 止められた再生は音を出さない =================
    print("=== A: 止めた再生の生き残りが、次の再生の音を奪わない ===")
    calls = []
    if core.IS_WIN:
        import winsound
        real_ps = winsound.PlaySound
        winsound.PlaySound = lambda *a, **k: calls.append(a)
    before = play_tmp()
    ev = threading.Event(); ev.set()
    t = time.perf_counter()
    core.play_wav_blocking(tiny_wav(), stop_event=ev)
    dt = time.perf_counter() - t
    if core.IS_WIN:
        winsound.PlaySound = real_ps
    check("止まっている再生は PlaySound を1回も出さない（%d回）" % len(calls),
          not calls)
    check("一時ファイルも作らない", play_tmp() == before)
    check("すぐ返る（%.0fms）" % (dt * 1000), dt < 0.05)

    app = main.App()
    app.withdraw()
    played, msgs = [], []
    real_play = core.play_wav_blocking
    core.play_wav_blocking = lambda wb, stop_event=None: played.append(1)
    app.q.put = lambda m: msgs.append(m)
    core.vv_dict_hash = lambda base, timeout=10: "x"
    core.vv_reading = lambda *a, **k: "テスト"
    core.synth_cache_get = lambda key: None
    core.synth_cache_put = lambda key, wb: None
    synth_n = []

    def fake_synth(base, text, sid, **kw):
        synth_n.append(text)
        return tiny_wav(), "テスト"
    core.vv_synthesize_with_kana = fake_synth
    voice = dict(speed=1.0, pitch=0.0, intonation=1.0, volume=1.0)

    # 合成を待っている間に■停止された
    stop = threading.Event(); stop.set()
    app._play_gen = 5
    app._preview_worker("一行目です。", 1, voice, 1, stop, 5)
    check("試聴: 止められていたら鳴らさない", not played)
    check("試聴: 画面を戻す通知は出す",
          any(m[0] == "preview_done" and m[-1] == 5 for m in msgs))
    check("試聴: 「再生中」表示を出さない",
          not any(m[0] == "preview_playing" for m in msgs))

    # 止めてはいないが、次の再生が始まって世代が進んでいた
    msgs.clear()
    app._play_gen = 6
    app._preview_worker("一行目です。", 1, voice, 1, threading.Event(), 5)
    check("試聴: 世代が古ければ鳴らさない", not played)

    msgs.clear()
    app._play_gen = 7
    app._sample_cache[("u", 3)] = tiny_wav()
    app._sample_worker("声", 3, "u", stop, 7)
    check("声サンプル: 止められていたら鳴らさない", not played)
    app._dict_preview_worker("単語", 1, voice, stop, 7)
    check("辞書の読み試聴: 止められていたら鳴らさない", not played)

    # 普通のときはちゃんと鳴る（直しすぎていない）
    msgs.clear()
    app._preview_worker("一行目です。", 1, voice, 1, threading.Event(), 7)
    check("何もなければ普通に鳴る", played == [1])

    # ================= B: 先読み中の同じ行は二重に合成しない =================
    print("\n=== B: 先読み中の行を試聴しても、二重に合成しない ===")
    played.clear(); synth_n.clear(); msgs.clear()
    cache = {}
    core.synth_cache_get = lambda key: cache.get("hit")
    done = threading.Event()
    app._prefetch_done = done
    app._prefetch_target = ("二行目です。", 1, tuple(sorted(voice.items())))
    app._play_gen = 8
    th = threading.Thread(target=app._preview_worker,
                          args=("二行目です。", 1, voice, 2, threading.Event(), 8))
    th.start()
    time.sleep(0.4)
    check("先読みが終わるのを待っている（合成を投げていない）",
          th.is_alive() and not synth_n)
    cache["hit"] = tiny_wav()      # 先読みがキャッシュに置いた
    done.set()
    th.join(3)
    check("先読みの結果を使って鳴らす（合成0回）", not synth_n and played == [1])

    # 別の行なら待たない
    played.clear(); synth_n.clear(); cache.clear()
    done2 = threading.Event()
    app._prefetch_done = done2
    app._prefetch_target = ("三行目です。", 1, tuple(sorted(voice.items())))
    t = time.perf_counter()
    app._preview_worker("四行目です。", 1, voice, 4, threading.Event(), 8)
    check("違う行なら待たずに合成する（%.0fms）" % ((time.perf_counter() - t) * 1000),
          synth_n == ["四行目です。"] and time.perf_counter() - t < 0.3)

    # 待っている最中の■停止にはすぐ応じる
    played.clear(); synth_n.clear()
    done3 = threading.Event()
    app._prefetch_done = done3
    app._prefetch_target = ("五行目です。", 1, tuple(sorted(voice.items())))
    st = threading.Event()
    th = threading.Thread(target=app._preview_worker,
                          args=("五行目です。", 1, voice, 5, st, 8))
    th.start(); time.sleep(0.2)
    t = time.perf_counter(); st.set(); th.join(2)
    check("待っている最中の■停止にすぐ応じる（%.0fms）"
          % ((time.perf_counter() - t) * 1000),
          not th.is_alive() and not played)

    # ================= C: 先読み中に移った行を捨てない =================
    print("\n=== C: 先読みが走っている間に移った行を、見直しに行く ===")
    app._prefetching = True
    app._prefetch_after = None
    app._prefetch_line()
    check("先読み中なら、あとで見直す予約を入れる", app._prefetch_after is not None)
    app.after_cancel(app._prefetch_after)
    app._prefetching = False
    app._prefetch_after = None
    app.busy = True
    app._prefetch_line()
    check("本番の処理中は予約し続けない（空回りしない）", app._prefetch_after is None)
    app.busy = False

    core.play_wav_blocking = real_play
    app._stop_ticks()
    app.destroy()
    print("\n*** レビュー指摘A/B/C %d項目すべて通過 ***" % len(ok))
finally:
    for orig, bak in keep.items():
        shutil.copy2(bak, orig); os.remove(bak)
    print("設定と本文を戻した")
