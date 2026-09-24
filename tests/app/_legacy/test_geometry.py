# -*- coding: utf-8 -*-
"""レビュー指摘D（ウィンドウ位置と最大化）の確認。設定と本文は退避して戻す。"""
import os, shutil
# 私物の絶対パス（本人のPCのユーザー名まで見えてしまう）は、公開のときに困るので外した。
# この台本は tests/_legacy/ にあるので、3つ上がアプリのフォルダ。
APP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
keep = {}
for name in ("settings.json", "last_text.txt"):
    p = os.path.join(APP, name)
    if os.path.exists(p):
        keep[p] = p + ".geobak"; shutil.copy2(p, keep[p])
ok = []
def check(name, cond):
    if cond: ok.append(name); print("  OK  " + name)
    else: raise AssertionError("NG: " + name)
try:
    import main
    app = main.App(); app.withdraw()
    vx, vy, vw, vh = app._virtual_screen()
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    print("  仮想デスクトップ: x=%d y=%d %dx%d / プライマリ %dx%d" % (vx, vy, vw, vh, sw, sh))
    check("仮想デスクトップが取れている", vw >= sw and vh >= sh)

    def applied(geo):
        app._apply_saved_geometry(geo); app.update_idletasks()
        g = app.geometry()
        import re
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", g)
        return int(m[3]), int(m[4])

    x, y = applied("800x600+120+90")
    check("画面内の位置はそのまま（%d,%d）" % (x, y), (x, y) == (120, 90))
    x, y = applied("800x600+-5000+100")
    check("外したモニタの負の座標は画面内へ寄せる（%d,%d）" % (x, y),
          0 <= x <= sw - 100 and 0 <= y <= sh - 60)
    x, y = applied("800x600+%d+100" % (vx + vw + 3000))
    check("右の彼方の座標も寄せる（%d,%d）" % (x, y), 0 <= x <= sw - 100)
    st = app._settings_dict()
    check("最大化の状態を保存する（zoomed=%r）" % st.get("zoomed"),
          "zoomed" in st and st["zoomed"] is False)
    app._stop_ticks(); app.destroy()
    print("\n*** ウィンドウ位置 %d項目すべて通過 ***" % len(ok))
finally:
    for orig, bak in keep.items():
        shutil.copy2(bak, orig); os.remove(bak)
    print("設定と本文を戻した")
