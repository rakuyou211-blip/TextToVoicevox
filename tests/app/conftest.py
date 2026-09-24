# -*- coding: utf-8 -*-
"""テストの共通の土台。

ここに置いてある仕掛けは、どれも「過去に実際にやらかしたこと」を二度と
起こさないためのものです。

 1. 利用者の settings.json / last_text.txt を、どのテストの前後でも必ず元に戻す
    （2026-09-04 に、テストが話者としおりを書き換えたまま1週間気づかなかった）
 2. テストから App._on_close() を呼べなくする（設定を保存してしまう唯一の経路）
 3. %TEMP% に t2v_* の置き土産が残っていないか、テストごとに確かめる
    （中断したテストが 13MB のフォルダを1週間残していた）
 4. エラーの記録（エラー.log など）が生えたら落とす。本体は例外を握りつぶして
    ここに書くので、気づけないと「例外が出ていたのにテストは通った」が成り立つ
 5. 打ち間違えたマーカーで止める（engine の打ち間違いは CI をすり抜ける）
 6. VOICEVOX が要るテストは、engine マーカーを付ければ自動で用意される。
    自分で起動したエンジンだけを止める（手元で開いている VOICEVOX は触らない）
 7. Tk をくり返し作ると Tcl がまれに転ぶので、そこだけ作り直す
    （本体のバグは隠さない。README の「Tk をくり返し作るときの注意」を参照）

回し方は tests/README.md を見てください。
"""
import gc
import glob
import os
import shutil
import sys
import tempfile
import time

import pytest

def _find_app_dir():
    """main.py の置いてある場所を、このファイルから上へたどって探す。
    tests/ の下に置いても、さらに一段深く置いても迷子にならないように。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        here = os.path.dirname(here)
        if os.path.exists(os.path.join(here, "main.py")):
            return here
    raise RuntimeError("main.py が見つかりません（テストの置き場所を確かめてください）")


APP_DIR = _find_app_dir()
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _pin_tcl_library():
    """TclとTkの部品の場所を環境変数で固定する。

    1つのプロセスで Tk を何度も作り直すと、探し直しに失敗して
    「Can't find a usable tk.tcl」や
    「invalid command name "tcl_findLibrary"」で倒れることがある。
    場所は sys.base_prefix から分かるので、**Tk を作らずに**渡しておく
    （確かめるために一度 Tk を作って壊す、をやると、その次の Tk 生成が
    必ず倒れた。2026-09-19 実測）。"""
    root = os.path.join(sys.base_prefix, "tcl")
    if not os.path.isdir(root):
        return                       # macOS など。gui のテストは飛ばされるか素で動く
    for env_name, prefix in (("TCL_LIBRARY", "tcl8."), ("TK_LIBRARY", "tk8.")):
        if os.environ.get(env_name):
            continue
        found = sorted(name for name in os.listdir(root)
                       if name.startswith(prefix)
                       and os.path.isdir(os.path.join(root, name)))
        if found:
            os.environ[env_name] = os.path.join(root, found[-1])


_pin_tcl_library()


USER_FILES = ("settings.json", "last_text.txt")
# 例外が起きたときだけ生える記録。テストで生えたら「黙って握りつぶした」印なので
# 見逃さない（settings.json.bak は保存経路を踏んだ印）。
WATCHED_LOGS = ("エラー.log", "起動エラー.log", "settings.json.bak")
TMP_PATTERN = os.path.join(tempfile.gettempdir(), "t2v_*")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "engine: VOICEVOXエンジンが要る（無ければ自動で起動、それも無理なら飛ばす）")
    config.addinivalue_line(
        "markers", "gui: 画面（Tk）を作る。ヘッドレスのCIでは飛ばす")
    config.addinivalue_line(
        "markers", "slow: 数十秒かかる")
    config.addinivalue_line(
        "markers", "writes_settings: settings.json を意図的に書き換える"
                   "（後片づけは土台がやる）")
    # 固まったときに、どのスレッドがどこで止まっているかを吐かせる。
    # 2026-09-23 に、全体を回している最中に一度だけプロセスごと落ちた
    # （再現せず・記録も残らなかった）。次に起きたら足跡が残るように。
    config.option.faulthandler_timeout = 600
    config.addinivalue_line(
        "markers", "writes_error_log: エラー.log が生えるのを承知で踏む"
                   "（後片づけは土台がやる）")



# 打ち間違えたマーカーを黙って通さないための一覧。
# `engine` を打ち間違えると、エンジンの要るテストが CI で走ってしまう。
# （pytest 本体の --strict-markers は conftest からは入れられないので自前でやる）
OUR_MARKERS = {"engine", "gui", "slow", "writes_settings", "writes_error_log"}
PYTEST_MARKERS = {"skip", "skipif", "xfail", "parametrize", "usefixtures",
                  "filterwarnings", "tryfirst", "trylast", "no_cover"}


_HERE = os.path.dirname(os.path.abspath(__file__))


def pytest_collection_modifyitems(config, items):
    """知らないマーカーが付いていたら、集める段階で止める。

    この見張りはフォルダをまたいで呼ばれるので、**このフォルダの下のテストだけ**を
    見る。同じリポジトリにある別のテスト群（独自のマーカーを使っているかもしれない）を
    巻き込んで止めてしまわないように。"""
    ours = [item for item in items
            if str(getattr(item, "fspath", "")).startswith(_HERE)]
    unknown = sorted({(item.nodeid, mark.name)
                      for item in ours for mark in item.iter_markers()
                      if mark.name not in OUR_MARKERS | PYTEST_MARKERS})
    if unknown:
        raise pytest.UsageError(
            "知らないマーカーが付いています（打ち間違いではありませんか）:"
            + "".join("\n  %s に @pytest.mark.%s" % (node, name)
                      for node, name in unknown)
            + "\n  使えるのは: " + "・".join(sorted(OUR_MARKERS)))


# ---------------------------------------------------------------- 利用者のファイル
@pytest.fixture(autouse=True)
def guard_user_files(request):
    """設定と本文を、テストの前に覚えて、後で必ず元に戻す。
    意図せず書き換わっていたら、戻したうえでテストを失敗させる
    （黙って直すと、次も同じことをするので）。"""
    saved = {}
    missing = []                 # 触る前から無かったもの（作られたら消す）
    for name in USER_FILES:
        path = os.path.join(APP_DIR, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                saved[path] = f.read()
        else:
            missing.append(path)
    yield
    changed = []
    for path, data in saved.items():
        now = open(path, "rb").read() if os.path.exists(path) else None
        if now != data:
            changed.append(os.path.basename(path))
            with open(path, "wb") as f:
                f.write(data)
    for path in missing:
        # 無かったファイルが生えた＝テストが作った。元は「無い」状態なので消す。
        # （clone したばかりの作業場所には settings.json が無い。そこで気づいた穴）
        if os.path.exists(path):
            changed.append(os.path.basename(path))
            os.remove(path)
    if changed and request.node.get_closest_marker("writes_settings") is None:
        pytest.fail("テストが %s を書き換えました（元に戻しましたが、"
                    "原因を直してください。_on_close を呼んでいませんか）"
                    % "・".join(changed))


# ---------------------------------------------------------------- エラーの記録
@pytest.fixture(autouse=True)
def guard_error_logs(request):
    """テスト中に エラー.log が生えたら（増えたら）落とす。

    本体は例外を握りつぶしてここに書くので、生えたことに気づけないと
    「例外が出ていたのにテストは通った」が成り立ってしまう。
    承知で踏むテストには @pytest.mark.writes_error_log を付ける。"""
    before = {}
    for name in WATCHED_LOGS:
        path = os.path.join(APP_DIR, name)
        if os.path.exists(path):
            with open(path, "rb") as f:
                before[path] = f.read()
    yield
    grew = []
    for name in WATCHED_LOGS:
        path = os.path.join(APP_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            now = f.read()
        old = before.get(path)
        if old is None:
            grew.append((name, now))
            os.remove(path)                      # テストが生やした分は片づける
        elif now != old:
            grew.append((name, now[len(old):]))
            with open(path, "wb") as f:          # 本人の分は元に戻す
                f.write(old)
    if grew and request.node.get_closest_marker("writes_error_log") is None:
        details = "\n".join(
            "%s に書かれた内容:\n%s"
            % (name, added.decode("utf-8", "replace")[-600:])
            for name, added in grew)
        pytest.fail("テストが記録を生やしました（例外を握りつぶしていませんか）。\n"
                    + details)


# ---------------------------------------------------------------- 置き土産
@pytest.fixture(autouse=True)
def guard_temp_files():
    """%TEMP% に t2v_* が増えていないか見る。
    再生の一時ファイルは「次の再生のときに片づける」持ち越しが仕様なので、
    数える前に終了時と同じ片づけを一度走らせる。"""
    before = set(glob.glob(TMP_PATTERN))
    yield
    try:
        import core
        core.sweep_play_tmp_on_exit()
    except Exception:
        pass
    leftover = sorted(set(glob.glob(TMP_PATTERN)) - before)
    for path in leftover:          # 次のテストに持ち越さないよう片づけてから報告
        try:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass
    assert not leftover, ("%%TEMP%% に置き土産が残りました: %s"
                          % [os.path.basename(p) for p in leftover])


# ---------------------------------------------------------------- アプリ
@pytest.fixture(scope="session")
def tk_keeper():
    """空の窓をひとつ、テストが終わるまで開いたままにしておく（隠してある）。

    Tk は、最後の窓を閉じた時点で Tcl 側の共通の後始末まで走ってしまう。
    そのあとに窓を作り直すと、5回に1回くらい
    「invalid command name "tcl_findLibrary"」や
    「Can't find a usable tk.tcl」で倒れる（2026-09-19 実測）。
    番人をひとつ残しておけば、その後始末が走らない。

    既定の窓（_default_root）の座は譲る。番人に居座らせると、
    master を省いて作られた変数が番人側にぶら下がってしまう。"""
    import tkinter
    try:
        keeper = tkinter.Tk()
    except Exception as exc:
        pytest.skip("画面が使えません: %s" % exc)
    keeper.withdraw()
    tkinter._default_root = None
    try:
        yield keeper
    finally:
        try:
            keeper.destroy()
        except Exception:
            pass


@pytest.fixture
def app(request, tk_keeper):
    """画面を作って渡し、終わったら安全に閉じる。
    テスト中は _on_close を呼べない（呼ぶと本人の設定を保存してしまう）。"""
    if request.node.get_closest_marker("gui") is None:
        pytest.fail("app を使うテストには @pytest.mark.gui を付けてください")
    import main
    if request.node.get_closest_marker("engine") is None:
        _mute_engine_probe(request)
    window = _new_window(main)
    window.withdraw()
    original = main.App._on_close

    def forbidden(self):
        raise AssertionError(
            "_on_close をテストから呼ばないこと（設定を保存してしまいます）。"
            "終了は _stop_ticks() と destroy() で足ります。")
    main.App._on_close = forbidden
    try:
        yield window
    finally:
        main.App._on_close = original
        try:
            window._stop_ticks()
        except Exception:
            pass
        # 背後で動いているワーカー（接続確認など）を見送ってから閉じる。
        # 見送らないと、窓を壊したあとも _check_worker が vv_probe の通信を
        # 続け、次のテストの最中まで生き残る。2026-09-24 に、その居残りが
        # RapidOCR（onnxruntime）の推論とGCに重なって、pytest ごと落ちた
        # （Windows fatal exception: code 0x80000003）。
        try:
            window._wait_workers(6.0)
        except Exception:
            pass
        _cancel_pending_after(window)
        try:
            window.destroy()
        except Exception:
            pass
        # 壊した窓の置き土産（StringVar や PhotoImage）を、**いまここで**
        # 片づける。放っておくと、次のテストの最中に走る GC が回収し、
        # そのときの実行スレッドから Tcl を呼んでしまう。背後で通信している
        # スレッド（接続確認の vv_probe）が当たりくじを引くと、Tcl が panic を
        # 起こして pytest ごと落ちる
        # （Windows fatal exception: code 0x80000003。2026-09-24 実測。
        #  落ちたのは毎回 vv_probe の中の Garbage-collecting）。
        # メインスレッドで回収すれば、同じ後始末でも警告1行で済む。
        gc.collect()


# Tcl が自分の部品を見失ったときの言い回し（どれもファイルは在るのに読めない）
_TCL_HICCUPS = ("tcl_findLibrary", "tk.tcl", "couldn't read file",
                "can't find package", "invalid command name")


def _is_tcl_hiccup(exc):
    import tkinter
    if not isinstance(exc, tkinter.TclError):
        return False
    text = str(exc)
    return any(word in text for word in _TCL_HICCUPS)


def _mute_engine_probe(request):
    """エンジンを使わないテストでは、画面が背後で通信しないようにする。

    App は起き抜けに自動接続を試み、別のスレッドで core.vv_probe（HTTP）を呼ぶ。
    エンジンが要らないテストにとっては要らない通信で、しかも実害があった:
    OCR（onnxruntime）を読み込んだプロセスの中でこのスレッドとGCが重なると、
    pytest ごと落ちる（Windows fatal exception: code 0x80000003。2026-09-24 実測。
    落ちた場所は毎回 vv_probe の中の Garbage-collecting）。

    ここで返すのは「誰もいない」と同じ答え。つながっていない前提のテストが
    実行するたびに変わらなくなる（本人の VOICEVOX が起きているかどうかに
    左右されない）という利点もある。engine マーカーの付いたテストは素通り。"""
    import core
    patcher = pytest.MonkeyPatch()
    request.addfinalizer(patcher.undo)
    patcher.setattr(core, "vv_probe", lambda base_url, timeout=3: {
        "ok": False, "version": "", "status": "refused", "http": None,
        "detail": "テスト中は通信しません（engine マーカーを付けると本物に触ります）"})


def _new_window(main, attempts=3):
    """画面を作る。Tcl の一時的な不調だけ、少し待って作り直す。

    窓を作り直すたびに、Tcl がまれに自分の部品を読めなくなる
    （「couldn't read file .../ttk/notebook.tcl」など。ファイルは在る）。
    同じプロセスでもう一度やれば通るので、ここで吸収する。

    **それ以外の例外はそのまま投げる。** 本体の起動が壊れたのを
    「Tclの不調」として飛ばしてしまうと、バグが skip の裏に隠れる。"""
    last = None
    for i in range(attempts):
        try:
            return main.App()
        except Exception as exc:
            if not _is_tcl_hiccup(exc):
                raise
            last = exc
            time.sleep(0.4 * (i + 1))
    pytest.skip("画面を作れませんでした（Tcl側の一時的な不調が%d回続いた）: %s"
                % (attempts, last))


def _cancel_pending_after(window):
    """_stop_ticks() が畳まなかった after を全部取り消す。
    残したまま destroy すると、消えた窓を呼ぼうとした after が Tcl 側で
    エラーになり、次のテストの Tk 生成が
    「invalid command name "tcl_findLibrary"」で倒れることがある（実測で3回に1回）。"""
    try:
        pending = window.tk.call("after", "info")
    except Exception:
        return
    for ident in window.tk.splitlist(pending):
        try:
            window.after_cancel(ident)
        except Exception:
            pass
    try:
        window.update_idletasks()
    except Exception:
        pass


# ---------------------------------------------------------------- エンジン
@pytest.fixture(scope="session")
def engine():
    """VOICEVOXエンジンのURL。動いていればそれを使い、無ければヘッドレスで
    起こす。自分で起こしたものだけを止める（手元で開いている VOICEVOX や
    エディタには絶対に触らない）。用意できなければテストを飛ばす。"""
    import core
    import main
    url = main.VOICEVOX_DEFAULT
    if core.vv_probe(url, timeout=2)["ok"]:
        yield url          # もともと動いていた＝こちらは止めない
        return
    try:
        proc, started_url = core.launch_voicevox_engine("127.0.0.1", 50021)
    except Exception as exc:
        pytest.skip("VOICEVOXエンジンを起動できません: %s" % exc)
    for _ in range(60):
        if core.vv_probe(started_url, timeout=1)["ok"]:
            break
        time.sleep(0.5)
    else:
        core.stop_process(proc)
        pytest.skip("起動したエンジンが応答しませんでした")
    try:
        yield started_url
    finally:
        core.stop_process(proc)


@pytest.fixture
def connected_app(app, engine):
    """エンジンにつながった状態の画面。つながるまで待ってから渡す。"""
    end = time.time() + 40
    while time.time() < end and app.conn_state != "ok":
        app.update()
        time.sleep(0.05)
    if app.conn_state != "ok":
        pytest.skip("画面がエンジンにつながりませんでした")
    return app


# ---------------------------------------------------------------- 種データ
@pytest.fixture(scope="session")
def seed(tmp_path_factory):
    """テスト用の画像・音を作って渡す（本人の私物には触らない）。
    以前は手元のスクリーンショットを直接使っていて、他のPCでは再現できなかった。"""
    from _seed import Seed
    return Seed(str(tmp_path_factory.mktemp("seed")))


@pytest.fixture
def pump(app):
    """画面のイベントを回しながら待つ（秒）。"""
    def _pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            app.update()
            time.sleep(0.02)
    return _pump


# ---------------------------------------------------------------- 移植前の置き場
# tests/_legacy/ は pytest に載せる前の古い台本（自分で main を呼んで回す形）。
# 移植が終わるたびに1本ずつ消していくので、それまでは拾わない。
collect_ignore_glob = ["_legacy/*", "probe_*.py"]


# ---------------------------------------------------------------- 後片づけ
@pytest.fixture(scope="session", autouse=True)
def sweep_probe_files():
    """煙テストが入れ子で置く probe_*.py の置き忘れを、最後に掃除する
    （途中で Ctrl+C されると残るため）。"""
    yield
    if os.environ.get("T2V_PROBE_RUN"):
        return          # 入れ子で走っている側。いま使っている台本を消してしまう
    for path in glob.glob(os.path.join(os.path.dirname(__file__), "probe_*.py")):
        try:
            os.remove(path)
        except OSError:
            pass
