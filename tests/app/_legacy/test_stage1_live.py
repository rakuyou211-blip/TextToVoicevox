# -*- coding: utf-8 -*-
"""第1段の実機確認（世代番号を入れた再生経路が本当に動くか）。
エンジンは自分でヘッドレス起動し、終わったら自分で止める。
ユーザーが自分で開いた VOICEVOX には触らない（起動していれば、それに繋ぐ）。"""
import glob
import os
import re
import shutil
import tempfile
import time

# 私物の絶対パス（本人のPCのユーザー名まで見えてしまう）は、公開のときに困るので外した。
# この台本は tests/_legacy/ にあるので、3つ上がアプリのフォルダ。
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEXT = "一行目です。\n二行目です。\n三行目です。\n四行目です。"

keep = {}
for name in ("settings.json", "last_text.txt"):
    p = os.path.join(APP, name)
    if os.path.exists(p):
        keep[p] = p + ".livebak"
        shutil.copy2(p, keep[p])

ok = []


def check(name, cond):
    if cond:
        ok.append(name)
        print("  OK  " + name)
    else:
        raise AssertionError("NG: " + name)


def play_tmp():
    return glob.glob(os.path.join(tempfile.gettempdir(), "t2v_play_*.wav"))


import core
import main

proc = None
before_tmp = set(play_tmp())

try:
    if not core.vv_probe(main.VOICEVOX_DEFAULT, timeout=2)["ok"]:
        print("エンジンを自分で起動します（ヘッドレス）…")
        proc, url = core.launch_voicevox_engine("127.0.0.1", 50021)
        for _ in range(60):
            if core.vv_probe(url, timeout=1)["ok"]:
                break
            time.sleep(0.5)
    app = main.App()

    def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            app.update()
            time.sleep(0.03)

    t0 = time.time()
    while time.time() - t0 < 40 and app.conn_state != "ok":
        pump(0.3)
    check("エンジンに接続できた", app.conn_state == "ok")

    app.text.delete("1.0", "end")
    app.text.insert("1.0", TEXT)
    app.update()

    def wait_idle(sec=180):
        t = time.time()
        while time.time() - t < sec and app._previewing:
            pump(0.2)
        pump(0.6)

    def lines_read(wait=10.0):
        end = time.time() + wait
        while time.time() < end:
            m = re.search(r"（(\d+)行", app.status_var.get())
            if m:
                return int(m.group(1))
            pump(0.2)
        return -1

    print("\n=== 再生の基本（世代番号つきの通知が届くか）===")
    app.text.mark_set("insert", "2.0")
    app.update()
    app.preview_selected()
    wait_idle()
    check("試聴が最後まで通る", "試聴 おわり" in app.status_var.get())
    check("試聴後は待機に戻る", app._previewing is False)

    gen0 = app._play_gen
    app.text.mark_set("insert", "3.0")
    app.update()
    app.play_all()
    wait_idle()
    check("連続再生は先頭から4行", lines_read() == 4)
    check("世代番号が進んでいる", app._play_gen > gen0)

    print("\n=== 途中で止める → しおりから再開 ===")
    app.text.mark_set("insert", "1.0")
    app.update()
    app.play_all()
    pump(2.0)
    app.stop_playall()
    wait_idle()
    check("停止後は待機に戻る", app._previewing is False)
    check("しおりができている", app._bookmark is not None)
    mark = app._bookmark
    app.play_from_bookmark()
    wait_idle()
    check("しおりの行から再開（先頭に戻らない）",
          lines_read() == 4 - mark + 1)

    print("\n=== 停止直後にすぐ次を始めても止められるか（バグ4の本番）===")
    app.text.mark_set("insert", "1.0")
    app.update()
    app.play_all()
    pump(1.0)
    app.stop_playall()          # 1.5秒後に _recover_if_stuck が動く
    pump(2.0)                   # そのタイミングを跨がせる
    app.text.mark_set("insert", "1.0")
    app.update()
    app.play_all()              # 新しい再生
    pump(1.5)
    check("新しい再生の停止ボタンが生きている",
          str(app.stop_btn.cget("state")) == "normal" and app._previewing)
    app.stop_playall()
    wait_idle()
    check("新しい再生もちゃんと止まる", app._previewing is False)

    print("\n=== 声サンプル ===")
    app.play_all()
    pump(1.0)
    app.stop_playall()          # 1.5秒後に _recover_if_stuck を予約
    pump(0.3)
    app.play_speaker_sample()   # その予約が生きているうちに次を始める
    pump(2.0)                   # 予約の時刻を跨がせる
    check("停止の安全網が次の再生に割り込まない", app._previewing is True)
    wait_idle(60)
    check("声サンプルが鳴って戻る", app._previewing is False)

    print("\n=== 一時ファイル ===")
    check("再生が終われば一時ファイルはその場で消える",
          not (set(play_tmp()) - before_tmp))
    core.sweep_play_tmp_on_exit()   # _on_close がやること
    leftover = set(play_tmp()) - before_tmp
    check("再生の一時ファイルが残っていない（%d個）" % len(leftover),
          not leftover)

    app._stop_ticks()
    app.destroy()
    print("\n*** 実機 %d項目すべて通過 ***" % len(ok))
finally:
    if proc is not None:
        core.stop_process(proc)
        print("自分で起動したエンジンを止めた")
    for orig, bak in keep.items():
        shutil.copy2(bak, orig)
        os.remove(bak)
    print("設定と本文を戻した")
