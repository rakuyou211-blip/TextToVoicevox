# -*- coding: utf-8 -*-
"""説明書の画像の元になる、本物のアプリの画面を撮る（Windows 専用）。

    python tools/manual/capture.py <出力フォルダ> [声などを借りる settings.json]

ふつうは shoot.py から、見えないデスクトップの中で動かす（そのまま動かすと、撮っている
あいだ窓が画面に出る）。VOICEVOX が使える状態で動かす（ふりがなと再生中の画面に要る）。

撮る物（PNG。窓の外枠ごと）は SHOTS。それと、「画面の見かた」の図で枠を描く各部分の
位置（boxes.json）。どれかを撮れなかったときは capture.log に理由を書いて、残りを続ける。

使っている人の物には書き込まない。設定は写し（出力フォルダの settings_copy.json）を使い、
本文の自動保存・エラーの記録・声の保管も出力フォルダへ向ける。
"""
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# 説明書に載せる本文。1画面で、色の付くふりがな（東雲駅）・日付の読み・@ での声の指定・
# 読まないメモの行が見えるようにしてある
SAMPLE_TEXT = ("第一章　朝の駅\n"
               "明日、東雲駅で待ち合わせ。\n"
               "その日、主人公は河原で少女と出会った。\n"
               "最後の頁をめくると、手紙が挟まっていた。\n"
               "封筒には「2026/11/13 18:07」と書いてある。\n"
               "@四国めたん:「ここで待っていればいいのね」\n"
               "# このメモの行は読みません")

SHOTS = ("01_empty.png", "02_text.png", "03_opt.png", "04_voice.png", "05_finish.png",
         "06_conn.png", "07_replace.png", "08_dict.png", "09_cache.png", "10_playing.png",
         "11_reading.png", "12_theme_dark.png", "12_theme_zunda.png", "12_theme_hc.png")

# 窓の大きさ（拡大率 100% のときの画素）。切り抜く範囲（make_images.py）はこの大きさで決めてある
WINDOW = (1000, 740)


def _settings_copy(out_dir, settings):
    """撮影に使う設定の写しを作って、その場所を返す。声などは settings から借りるが、
    見た目は撮影用にそろえる（ライトのテーマ・窓の大きさや開いた欄は既定から）。"""
    s = {}
    if settings and os.path.exists(settings):
        with open(settings, encoding="utf-8") as f:
            s = json.load(f)
    s["update_check"] = False
    s["theme"] = "light"
    for k in ("geometry", "finish_open", "reading"):
        s.pop(k, None)
    path = os.path.join(out_dir, "settings_copy.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)
    return path


def main(out_dir, settings=None):
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    os.makedirs(out_dir, exist_ok=True)
    os.environ["T2V_NO_SPLASH"] = "1"
    sys.path.insert(0, ROOT)
    sys.path.insert(0, HERE)
    os.chdir(ROOT)
    log = open(os.path.join(out_dir, "capture.log"), "w", encoding="utf-8")

    def p(*a):
        print(*a, file=log, flush=True)

    app = None
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        import core
        import main as appmain
        import pwcap

        appmain.SETTINGS_PATH = _settings_copy(out_dir, settings)
        appmain.TEXT_CACHE_PATH = os.path.join(out_dir, "last_text.txt")
        if os.path.exists(appmain.TEXT_CACHE_PATH):
            os.remove(appmain.TEXT_CACHE_PATH)
        appmain.App.ERROR_LOG_PATH = os.path.join(out_dir, "エラー.log")
        core.SYNTH_CACHE_DIR = os.path.join(out_dir, "vc")
        # 見えないデスクトップでは答えられないので、問いかけの窓は出さない
        for n in ("showerror", "showwarning", "showinfo"):
            setattr(messagebox, n, lambda *a, **k: None)
        filedialog.askopenfilenames = lambda **kw: ()

        app = appmain.App()
        app.update()
        app.geometry(f"{appmain._px(WINDOW[0])}x{appmain._px(WINDOW[1])}+0+0")

        def wait(cond, sec):
            end = time.monotonic() + sec
            while time.monotonic() < end:
                app.update()
                if cond():
                    return True
                time.sleep(0.03)
            return False

        def shot(name, win=None):
            wait(lambda: False, 0.6)
            pwcap.capture(int((win or app).wm_frame(), 16)).save(os.path.join(out_dir, name))
            p("撮った:", name)

        def step(name, fn):
            try:
                fn()
            except Exception:
                p("撮れなかった:", name, traceback.format_exc())

        p("VOICEVOX:", wait(lambda: app._engine_ready(), 120), app.engine_var.get())
        wait(lambda: False, 2)
        shot("01_empty.png")

        app.text.insert("1.0", SAMPLE_TEXT)
        app.text.mark_set("insert", "1.0")
        wait(lambda: sum(1 for lb in app._furi_labels if lb.winfo_manager()) >= 10, 30)
        wait(lambda: False, 2.5)
        shot("02_text.png")
        step("boxes", lambda: _write_boxes(app, appmain, pwcap, out_dir, p))

        step("opt", lambda: (app._toggle_opt_rows(), shot("03_opt.png"), app._toggle_opt_rows()))
        step("voice", lambda: (app._toggle_voice_detail(), shot("04_voice.png"),
                               app._toggle_voice_detail()))
        step("finish", lambda: (app._toggle_finish(), shot("05_finish.png"), app._toggle_finish()))
        step("conn", lambda: (app._set_conn_compact(False), shot("06_conn.png"),
                              app._set_conn_compact(True)))
        step("replace", lambda: (app._set_replace(True), shot("07_replace.png"),
                                 app._set_replace(False)))

        def dict_shot():
            # 色の付いたふりがな（読み間違いの候補）を押したときと同じ開き方。無ければふつうに開く
            for i, lb in enumerate(app._furi_labels):
                if lb.winfo_manager() and "アズモ" in str(lb.cget("text")):
                    app._furi_fix(app._furi_states[i].get("span"), lb.cget("text"))
                    break
            else:
                app.open_dict_dialog()
            wait(lambda: app._dict_win is not None and app._dict_win.winfo_ismapped(), 10)
            shot("08_dict.png", app._dict_win)
            app._dict_win.destroy()
            app._dict_win = None
        step("dict", dict_shot)

        def cache_shot():
            before = set(app.winfo_children())
            app.open_cache_dialog()
            wait(lambda: False, 1.0)
            wins = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w not in before]
            if wins:
                shot("09_cache.png", wins[0])
                wins[0].destroy()
        step("cache", cache_shot)

        def play_shot():
            app.text.mark_set("insert", "2.0")
            app.play_from_cursor()
            wait(lambda: app._previewing, 10)
            wait(lambda: False, 2.0)
            shot("10_playing.png")
            app.stop_playall()
            wait(lambda: not app._previewing, 10)
        step("play", play_shot)

        step("reading", lambda: (app.toggle_reading(), wait(lambda: False, 1.0),
                                 shot("11_reading.png"), app.toggle_reading()))

        for key in ("dark", "zunda", "hc"):
            step("theme " + key, lambda key=key: (app.theme_var.set(key), app.apply_theme(),
                                                  wait(lambda: False, 1.0),
                                                  shot(f"12_theme_{key}.png")))
        app.theme_var.set("light")
        app.apply_theme()
        wait(lambda: False, 0.5)
        missing = [n for n in SHOTS if not os.path.exists(os.path.join(out_dir, n))]
        p("撮れなかった画面:", missing or "なし")
        app._on_close()
        time.sleep(1.5)
    except Exception:
        p(traceback.format_exc())
    finally:
        log.close()
        os._exit(0)   # Tk と VOICEVOX の後片付けを待たずに終える（撮った物は保存済み）


def _write_boxes(app, appmain, pwcap, out_dir, p):
    """「画面の見かた」の図のために、02_text.png の各部分の位置（窓の外枠の左上から、画素）を
    boxes.json に書く。_scale は画面の拡大率（250% なら 2.5）。"""
    left, top, _r, _b = pwcap.rect(int(app.wm_frame(), 16))

    def box(w):
        x, y = w.winfo_rootx() - left, w.winfo_rooty() - top
        return [x, y, x + w.winfo_width(), y + w.winfo_height()]

    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from walk(c)

    def by_text(pred):
        out = []
        for w in walk(app):
            try:
                t = str(w.cget("text"))
            except Exception:
                continue
            if w.winfo_ismapped() and pred(t):
                out.append(w)
        return out

    boxes = {
        "_scale": appmain._px(1000) / 1000,
        "status": box(app._status_box),
        "theme": box(app.theme_cb),
        "steps": box(app._steps_col),
        "conn": box(app._conn_box),
        "rbar": box(app._rbar[0]),
        "text": box(app.text),
        "player": box(app._player_rows[0].master),
    }
    for key, head in (("step1", "①"), ("step2", "②"), ("step3", "③")):
        ws = by_text(lambda t, h=head: t.startswith(h) and len(t) <= 8)
        if ws:
            boxes[key] = box(ws[0])
    links = [box(w) for w in by_text(lambda t: t in ("使い方", "感想を送る"))]
    if links:
        boxes["links"] = [min(b[0] for b in links), min(b[1] for b in links),
                          max(b[2] for b in links), max(b[3] for b in links)]
    furi = [lb for lb in app._furi_labels if lb.winfo_manager()]
    if furi:
        boxes["furi"] = box(furi[0])
    with open(os.path.join(out_dir, "boxes.json"), "w", encoding="utf-8") as f:
        json.dump(boxes, f, indent=1)
    p("位置:", boxes)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
