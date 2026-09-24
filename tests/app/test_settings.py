# -*- coding: utf-8 -*-
"""設定の見張り。いまは窓の位置と大きさだけで、ほかの設定はここに合流させる。

もとは _legacy/test_geometry.py（レビュー指摘D「ウィンドウ位置と最大化」）。

見張っているのは「次に開いたとき、窓が見えるところに座るか」。
サブモニタを外したまま起動すると、保存してあった座標がそのまま当たって
窓が画面の外へ出てしまい、本人には「起動しても何も出ない」と見えていた。
いまは、ほとんど見えなくなる座標だけプライマリ画面へ寄せる作りになっている。
ここが落ちたら、その寄せが効かなくなったということです。

もとの台本は settings.json と last_text.txt を自分で退避して戻していたが、
いまは土台（conftest の guard_user_files）が同じことをやる。ここのテストは
どれも窓を動かして見るだけで、設定は書かない（_settings_dict() は保存用の
中身を組み立てるだけで、ファイルには触らない）。

設定まわりの見張りは、いずれこのファイルへ合流させる想定なので節を分けてある。
"""
import re

import pytest


def _applied(app, geo):
    """保存された文字列を当てて、窓が実際に座った位置 (x, y) を返す。

    もとの台本にもあった小道具。Tk は左や上のモニタだと
    "800x600+-5000+100" のように「+」のあとに負の数を書くので、
    読み取る側の正規表現もその形を許しておく。"""
    app._apply_saved_geometry(geo)
    app.update_idletasks()
    g = app.geometry()
    m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", g)
    assert m, "geometry() が読めない形で返ってきた: %r" % g
    return int(m[3]), int(m[4])


def _gsm_virtual_screen():
    """GetSystemMetrics から仮想デスクトップを直に取る。取れなければ None。

    本体の _virtual_screen() と同じ道筋を、テスト側でもう一度たどるための
    小道具。本体が黙ってプライマリしか返さなくなったとき、比べる相手が
    要る（本体の戻り値と本体の戻り値を比べても何も分からないので）。"""
    import core
    if not core.IS_WIN:
        return None
    try:
        import ctypes
        gsm = ctypes.windll.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN=76 / SM_YVIRTUALSCREEN=77 /
        # SM_CXVIRTUALSCREEN=78 / SM_CYVIRTUALSCREEN=79
        vw, vh = gsm(78), gsm(79)
        if vw <= 0 or vh <= 0:
            return None
        return gsm(76), gsm(77), vw, vh
    except Exception:
        return None


# ================================================================ モニタの範囲
# 画面外かどうかの判定は「つながっている全モニタを合わせた範囲」を基準にする。
# Tk の winfo_vroot* は Windows ではプライマリの大きさしか返さないので、
# 本体は GetSystemMetrics で本当の仮想デスクトップを取っている。
# ここが黙ってプライマリだけを返すようになると、サブモニタに置いた窓が
# 「画面外」と誤って判定され、起動のたびにプライマリへ引き戻される。
@pytest.mark.gui
def test_仮想デスクトップが取れている(app, monkeypatch):
    vx, vy, vw, vh = app._virtual_screen()
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    assert vw >= sw and vh >= sh, (
        "全モニタの範囲がプライマリより狭い"
        "（x=%d y=%d %dx%d / プライマリ %dx%d）" % (vx, vy, vw, vh, sw, sh))
    # ↑ だけでは空振りする。本体は GetSystemMetrics を取れないとき
    # (0, 0, sw, sh) を返すので、まさに見張りたい「プライマリしか返さない」
    # 状態でも vw >= sw と vh >= sh は必ず成り立ってしまう。
    # なので、取れる環境では GetSystemMetrics の値と一致するところまで見る。
    expected = _gsm_virtual_screen()
    if expected is None:
        pytest.skip("GetSystemMetrics が使えない環境（Windows以外、または"
                    "呼び出しに失敗）。この場合は本体もプライマリを返す作りなので、"
                    "比べる相手が無く、何を確かめても空振りになる")
    assert (vx, vy, vw, vh) == expected, (
        "仮想デスクトップの値が GetSystemMetrics と食い違う"
        "（本体 x=%d y=%d %dx%d / GetSystemMetrics x=%d y=%d %dx%d）。"
        "プライマリだけを返していませんか" % ((vx, vy, vw, vh) + expected))

    # それでもまだ足りない。この開発機は1画面で、仮想デスクトップと
    # プライマリが同じ値になる（2026-09-23 実測: x=0 y=0 1536x864）。
    # つまり GetSystemMetrics を読む道が丸ごと消えても、上の一致は成り立つ。
    # そこで GetSystemMetrics の返事を「左に伸びた2画面」に偽って、
    # その値が本当に効いているかを見る（本体には手を入れない）。
    import ctypes
    fake = {76: -1920, 77: -100, 78: 3456, 79: 1080}
    monkeypatch.setattr(ctypes.windll.user32, "GetSystemMetrics",
                        lambda index, **kw: fake.get(index, 0))
    got = app._virtual_screen()
    assert got == (-1920, -100, 3456, 1080), (
        "GetSystemMetrics の値が使われていない（返ってきたのは %r）。"
        "プライマリ（winfo_screen*）に戻っていませんか" % (got,))


# ================================================================ 保存した位置を戻す
# ここが本題。以前の正規表現が "980x880+-1500+120" の形（「+」のあとの負の数）に
# 合っておらず、補正を素通りしていた。外したモニタの座標がそのまま当たって、
# 窓が見えない場所に置かれていた。
@pytest.mark.gui
def test_画面内の位置はそのまま(app):
    """見えているなら動かさない。よけいに寄せると、置いた場所が毎回変わる。"""
    x, y = _applied(app, "800x600+120+90")
    assert (x, y) == (120, 90), "画面内の位置が動かされた（%d,%d）" % (x, y)


@pytest.mark.gui
def test_外したモニタの負の座標は画面内へ寄せる(app):
    sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
    x, y = _applied(app, "800x600+-5000+100")
    assert 0 <= x <= sw - 100 and 0 <= y <= sh - 60, (
        "画面の外に置かれたまま（%d,%d / プライマリ %dx%d）" % (x, y, sw, sh))


@pytest.mark.gui
def test_右の彼方の座標も寄せる(app):
    """負の側だけ直して正の側を忘れる、をやりがちなので両方を見る。"""
    sw = app.winfo_screenwidth()
    vx, _vy, vw, _vh = app._virtual_screen()
    x, _y = _applied(app, "800x600+%d+100" % (vx + vw + 3000))
    assert 0 <= x <= sw - 100, (
        "右の外に置かれたまま（x=%d / プライマリ幅 %d）" % (x, sw))


# ================================================================ 最大化の記憶
# 最大化したまま閉じたら、次も最大化で開く。保存する側にキーが無いと
# 静かに忘れるだけで誰も気づかないので、キーの有無まで見る。
# （当てるのは窓を見せる直前。構築途中の窓を zoomed にすると中身が見えてしまう）
@pytest.mark.gui
def test_最大化の状態を保存する(app, monkeypatch):
    st = app._settings_dict()
    assert "zoomed" in st, "保存する内容に zoomed が無い（最大化を忘れる）"
    # もとは st["zoomed"] is False を見ていたが、app fixture が App() の直後に
    # withdraw() するので state() は必ず "withdrawn"。つまり何をどう壊しても
    # False が返る＝空振りだった。見るべきは「保存する値が _is_zoomed() の
    # 答えをそのまま運んでいるか」で、ここが切れると（例えば False の決め打ちに
    # なると）最大化を静かに忘れる。
    assert st["zoomed"] == app._is_zoomed(), (
        "保存する zoomed が _is_zoomed() と食い違う"
        "（保存=%r / _is_zoomed()=%r）" % (st["zoomed"], app._is_zoomed()))
    assert isinstance(st["zoomed"], bool), (
        "zoomed が真偽値ではない（%r）。JSON に入れて読み直したとき"
        "bool(s.get(\"zoomed\")) の答えが変わる" % (st["zoomed"],))

    # 逆の状況も作る。最大化した窓は実際には用意できない（隠してある窓を
    # zoomed にすると本人の画面に出てしまう）ので、state() の返事だけを
    # 偽って、そこから先が True を運ぶかを見る。本体には手を入れない。
    monkeypatch.setattr(app, "state", lambda *a, **kw: "zoomed")
    assert app._is_zoomed() is True, (
        "state() が \"zoomed\" なのに _is_zoomed() が %r"
        % (app._is_zoomed(),))
    zoomed_st = app._settings_dict()
    assert zoomed_st["zoomed"] is True, (
        "最大化の状態が保存する内容に届いていない（zoomed=%r）。"
        "次に開いたとき最大化が戻らない" % (zoomed_st["zoomed"],))
