# -*- coding: utf-8 -*-
"""Windows の起動口（*.pyw）が共通で使う部分。

なぜ .bat を1つも使わないのか:
  ブラウザで落とした zip を展開すると、中のファイル全部に「インターネットから
  来た」印（Zone.Identifier / Mark of the Web）が付く。Windows 11 の
  「スマート アプリ コントロール」は、この印が付いた .bat を、中身も署名も
  評判も見ずに拡張子だけで止める。しかも SmartScreen と違って通すボタンが無い。

  2026-09-27 に実測したところ、同じ印が付いていても .py / .pyw は止められない。
      .bat / .cmd / .vbs / .js       → ブロック
      .lnk（署名済み py.exe を指す） → ブロック
      .py / .pyw                     → 通る

  v1.23 では .pyw で印を外してから .bat を呼んでいたが、zip には「起動.bat」と
  「起動.pyw」が並んでいて、拡張子を隠す Windows の既定では、どちらも「起動」に
  見える。.bat の方を選んだ人は止められる（2026-10-03、本人の実機で再発）。
  そこで v1.24.0 から、セットアップも起動も Python だけでやる。止められる入口が
  zip に1つも無ければ、どれを開いても止められない。

  2回目からは cmd も PowerShell も通さず、venv の pythonw で main.py を直接開く。
  初回（と、部品の入れ直しが要るとき）だけ、黒い窓で winsetup.py を動かして
  進み具合を見せる。

  Python が入っていないと .pyw はダブルクリックしても無反応になるが、
  このアプリは元々 Python が要るので、そこは案内で補う。
"""
import hashlib
import os
import shutil
import subprocess
import sys

TITLE = "TextToVoicevox"

# Windows の zip では、開いたときに見える物を減らすため、プログラム・部品・設定は app フォルダに
# まとめ、いちばん上には 起動.pyw と説明だけを置く（tools/make_release.py が並べる）。
# GitHub の中身と、1行導入の入れ先は、今までどおり1つのフォルダに全部並ぶ
APP_SUBDIR = "app"
TOP_FILES = ("起動.pyw", "はじめにお読みください.txt", "説明書.html",
             "How to use (日本語).html", "How to use (English).html",
             "How to use (繁體中文).html", "How to use (简体中文).html")
# 前の版（全部がいちばん上にあった形）から app へ移す、自分のデータと部品。app に無いときだけ移す。
# 使用中で動かせないことがあるフォルダを先に移す（動かせなければ、設定などには触らずにやめる）
OWN_DATA = ("venv", "ocr_engine", "voice_cache", "settings.json", "settings.json.bak",
            "last_text.txt", "起動エラー.log", "エラー.log")

# 強い文字読み取りの部品を入れるフォルダ（初回セットアップで winsetup.py が作る）
ENGINE_DIR = "ocr_engine"
# その部品が入らなかった印（venv の中）。起動のたびに黒い窓で試し直さないため。
# venv を作り直すと消えるので、部品を入れ直すときにまた試す
ENGINE_TRIED_NAME = ".t2v_engine_tried"

# 1行導入（install.ps1）が入れる「Python 入りの版」の Python（tools/make_bundle.py が作る）。
# アプリの部品も文字を読む部品も最初から入っているので、venv もセットアップも要らない
RUNTIME_DIR = "runtime"

# 印を外さないフォルダ（自分の機械で作った物なので印は付かない。数が多い）
SKIP_DIRS = {"venv", ".git", "__pycache__", RUNTIME_DIR, ENGINE_DIR, ENGINE_DIR + ".new", ENGINE_DIR + ".old"}

# 前の版の zip に入っていた物。上書きで展開すると残るので、見つけたら消す（どれも今は
# 使っていない）。v1.23.1 までの .bat は「起動」が2つ並んでしまう。英語OCRを入れる.pyw は、
# 文字を読む部品が最初のセットアップで入るようになって要らなくなった
LEGACY_FILES = ("起動.bat", "setup.bat", "デバッグ起動.bat", "英語OCRを入れる.bat",
                "英語OCRを入れる.pyw", "requirements-english-ocr.txt")

# セットアップが済んだ印。requirements.txt の中身を控えておき、新しい版で
# 必要な部品が変わったときだけ入れ直す
STAMP_NAME = ".t2v_setup"

# アプリの絵のショートカット（起動.pyw の隣に置く）。起動.pyw の絵は Windows がファイルの
# 種類（.pyw）で決めるので、1つだけ変えることはできない。Python の絵の「起動」では
# 分かりにくいので、アプリの絵の「TextToVoicevox」を置いて、次からはそれを開けるようにする。
# 自分の PC で作るのでダウンロードの印は付かず、スマート アプリ コントロールにも止められない
SHORTCUT_NAME = "TextToVoicevox.lnk"
LAUNCHER_NAME = "起動.pyw"
ICON_REL = os.path.join("assets", "app-icon.ico")
_IS_WIN = os.name == "nt"

# Windows のショートカットの部品（IShellLinkW / IPersistFile）
_CLSID_SHELL_LINK = "00021401-0000-0000-C000-000000000046"
_IID_SHELL_LINK_W = "000214F9-0000-0000-C000-000000000046"
_IID_PERSIST_FILE = "0000010B-0000-0000-C000-000000000046"

DETACHED = (getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def tell(here, message):
    """コンソールが無いので窓で知らせる。tkinter が無ければログに落とす。"""
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showwarning(TITLE, message)
        root.destroy()
        return
    except Exception:
        pass
    try:
        with open(os.path.join(here, "起動エラー.log"), "w", encoding="utf-8") as f:
            f.write(message + "\n")
    except OSError:
        pass
    sys.stderr.write(message + "\n")


def unblock(root):
    """Zone.Identifier（ダウンロードの印）を消す。消せなくても止めない。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
            except OSError:
                pass  # 印が無い・読めない。できたところまでで十分


def remove_legacy(here):
    """前の版の残り物を消す。消せなくても止めない。"""
    for name in LEGACY_FILES:
        try:
            os.remove(os.path.join(here, name))
        except OSError:
            pass


def _shell_link(lnk, target, icon, description):
    """lnk に、target を開くショートカットを作る（Windows の IShellLinkW を ctypes から使う。
    pywin32 は要らない）。フォルダごと動かしても行き先を見つけられるよう、ショートカットから
    見た相対の道も入れる。失敗したら OSError。"""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    def guid(text):
        g = GUID()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
        return g

    def call(obj, index, *args, argtypes=(), restype=ctypes.HRESULT):
        # COM の関数表（vtable）の index 番目を呼ぶ。HRESULT が失敗なら OSError になる
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])(obj, *args)

    text = wintypes.LPCWSTR
    ole32 = ctypes.OleDLL("ole32")
    try:
        ole32.CoInitialize(None)
        inited = True
    except OSError:
        inited = False   # このスレッドは別の形で初期化済み。そのまま使える
    link = ctypes.c_void_p()
    try:
        ole32.CoCreateInstance(ctypes.byref(guid(_CLSID_SHELL_LINK)), None, 1,   # CLSCTX_INPROC_SERVER
                               ctypes.byref(guid(_IID_SHELL_LINK_W)), ctypes.byref(link))
        try:
            call(link, 20, target, argtypes=(text,))                          # SetPath
            call(link, 18, lnk, 0, argtypes=(text, wintypes.DWORD))           # SetRelativePath
            call(link, 17, icon, 0, argtypes=(text, ctypes.c_int))            # SetIconLocation
            call(link, 7, description, argtypes=(text,))                      # SetDescription
            persist = ctypes.c_void_p()
            call(link, 0, ctypes.byref(guid(_IID_PERSIST_FILE)), ctypes.byref(persist),
                 argtypes=(ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)))  # QueryInterface
            try:
                call(persist, 6, lnk, True, argtypes=(text, wintypes.BOOL))   # IPersistFile::Save
            finally:
                call(persist, 2, restype=ctypes.c_ulong)                      # Release
        finally:
            call(link, 2, restype=ctypes.c_ulong)                             # Release
    finally:
        if inited:
            ole32.CoUninitialize()


def launcher_path(top, here=None):
    """いまの 起動.pyw の場所。zip の並びでは、1回開いたあとは app の中にしまってある。"""
    here = here or top
    inside = os.path.join(here, LAUNCHER_NAME)
    if os.path.normcase(top) != os.path.normcase(here) and os.path.exists(inside):
        return inside
    return os.path.join(top, LAUNCHER_NAME)


def shortcut_current(top, here=None):
    """いちばん上のショートカットが、今の場所の絵と 起動.pyw を指しているか（中を見るだけ）。
    行き先は、ショートカットから見た相対の道（.\\起動.pyw や .\\app\\起動.pyw）で見る。"""
    here = here or top
    icon = os.path.join(here, ICON_REL)
    # ショートカットは Windows の物なので、区切りはいつも \（Mac・Linux のテストでも同じ答え）
    rel = ".\\" + os.path.relpath(launcher_path(top, here), top).replace("/", "\\")
    try:
        with open(os.path.join(top, SHORTCUT_NAME), "rb") as f:
            data = f.read()
    except OSError:
        return False
    return icon.encode("utf-16-le") in data and rel.encode("utf-16-le") in data


def ensure_shortcut(top, here=None):
    """アプリの絵のショートカット（TextToVoicevox）を、いちばん上（top）に置く。絵は here
    （app フォルダ。1つのフォルダの形では top と同じ）の中の物を使い、行き先は今の 起動.pyw。
    無いとき・フォルダを動かしたり 起動.pyw をしまったりして古い所を指しているときだけ作り直す
    （いつもは中を見るだけ）。作れなくても止めない（起動.pyw から開ける）。作ったら True。"""
    if not _IS_WIN:
        return False
    here = here or top
    icon = os.path.join(here, ICON_REL)
    target = launcher_path(top, here)
    if not (os.path.exists(icon) and os.path.exists(target)):
        return False
    if shortcut_current(top, here):
        return False
    try:
        _shell_link(os.path.join(top, SHORTCUT_NAME), target, icon, "TextToVoicevox を開く")
        return True
    except Exception:
        return False


def place_launcher(top, here):
    """起動.pyw とアプリの絵のショートカットを置く。zip の並び（top と here が別）では、
    起動.pyw を app の中へしまい、いちばん上にはアプリの絵の「TextToVoicevox」だけを残す
    （開いたときに見える物を減らすため。初めての1回だけは、いちばん上の 起動.pyw を開いて
    もらうしかない）。ショートカットを作れなかったときは、起動.pyw をいちばん上に戻す
    （入口が無くならないように）。新しい zip を上書きで展開して、いちばん上にまた 起動.pyw が
    できたときも、ここで app の中の古い物と入れ替えてしまう。"""
    up = os.path.join(top, LAUNCHER_NAME)
    inside = os.path.join(here, LAUNCHER_NAME)
    if not _IS_WIN or os.path.normcase(top) == os.path.normcase(here):
        ensure_shortcut(top, here)
        return
    if os.path.exists(up):
        try:
            os.replace(up, inside)   # 動いている最中の自分自身でも、読み終えてあるので動かせる
        except OSError:
            pass   # しまえないときは、いちばん上のまま使う
    ensure_shortcut(top, here)
    if not shortcut_current(top, here) and os.path.exists(inside) and not os.path.exists(up):
        try:
            os.replace(inside, up)
        except OSError:
            pass


def top_of(here):
    """ショートカットと説明を置く所（開いたときに見えるいちばん上）。zip の形では app フォルダの
    1つ上、ほかは同じフォルダ。起動.pyw は1回開いたあと app の中にしまうので、ショートカットや
    説明があることでも見分ける。"""
    parent = os.path.dirname(here)
    if os.path.basename(here).lower() == APP_SUBDIR and any(
            os.path.exists(os.path.join(parent, name)) for name in TOP_FILES + (SHORTCUT_NAME,)):
        return parent
    return here


def _merge_dir(src, dst):
    """src の中身を dst へ寄せる。dst に同じ物があれば src の方（前の版の写し）を消し、
    無い物（自分で置いた立ち絵・案内など）は移す。空になったフォルダは消す。"""
    for folder, _dirs, files in os.walk(src, topdown=False):
        rel = os.path.relpath(folder, src)
        for name in files:
            s = os.path.join(folder, name)
            d = os.path.normpath(os.path.join(dst, rel, name))
            try:
                if os.path.exists(d):
                    os.remove(s)
                else:
                    os.makedirs(os.path.dirname(d), exist_ok=True)
                    os.replace(s, d)
            except OSError:
                pass   # 消せない・移せない物は、そのまま残す
        try:
            os.rmdir(folder)
        except OSError:
            pass   # まだ何か残っている


def move_into_app(top, here):
    """前の版（全部がいちばん上にある形）の上に新しい zip を展開したときに、app の形へ移す。

    設定・声の保管・部品（venv・ocr_engine）と、自分で置いた立ち絵などは app へ移し、
    app に新しい物がある前の版のプログラムと説明は片付ける（上に残ると、どれを開けばいいか
    分からない）。見知らぬファイル（自分のメモなど）は消さずに残す。部品を動かせないとき
    （アプリが開いたまま）は、途中でやめて False（次に開いたときに続きから移す）。"""
    if os.path.normcase(top) == os.path.normcase(here) or not os.path.exists(os.path.join(top, "main.py")):
        return True
    for name in OWN_DATA:
        src, dst = os.path.join(top, name), os.path.join(here, name)
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                os.replace(src, dst)   # 同じドライブなので、名前の付け替えだけで済む
            except OSError:
                if os.path.isdir(src):
                    return False
    keep = set(TOP_FILES) | set(OWN_DATA) | set(LEGACY_FILES) | {SHORTCUT_NAME, "TextToVoicevox.exe", APP_SUBDIR}
    for name in os.listdir(top):
        if name in keep:
            continue
        src, dst = os.path.join(top, name), os.path.join(here, name)
        try:
            if name == "__pycache__":
                shutil.rmtree(src, ignore_errors=True)
            elif os.path.isdir(src):
                if os.path.isdir(dst):
                    _merge_dir(src, dst)
            elif os.path.exists(dst):
                os.remove(src)   # 前の版のプログラム・説明（新しい物が app にある）
        except OSError:
            pass
    return True


def venv_exes(here):
    """venv の (python.exe, pythonw.exe)。"""
    scripts = os.path.join(here, "venv", "Scripts")
    return os.path.join(scripts, "python.exe"), os.path.join(scripts, "pythonw.exe")


def runtime_exes(here):
    """Python 入りの版の (python.exe, pythonw.exe)。"""
    folder = os.path.join(here, RUNTIME_DIR)
    return os.path.join(folder, "python.exe"), os.path.join(folder, "pythonw.exe")


def bundled(here):
    """Python 入りの版（1行導入で入れた形）か。"""
    return all(os.path.exists(p) for p in runtime_exes(here))


def app_exes(here):
    """アプリを動かす (python.exe, pythonw.exe)。Python 入りの版ならその中の物、ほかは venv の物。"""
    return runtime_exes(here) if bundled(here) else venv_exes(here)


def engine_tried_path(here):
    """強い文字読み取りの部品が入らなかった印の置き場所。"""
    return os.path.join(here, RUNTIME_DIR if bundled(here) else "venv", ENGINE_TRIED_NAME)


def venv_ok(here):
    """venv が今のパソコンで動くか。フォルダごと別のPCから持ってきたときや、
    Python を入れ直したときは、元の Python が無くて動かない（venv は機械ごとに
    作る物）。pyvenv.cfg の home に python.exe があるかで見る（起動のたびに
    Python を1回走らせるより速い）。"""
    py, pyw = venv_exes(here)
    if not (os.path.exists(py) and os.path.exists(pyw)):
        return False
    try:
        with open(os.path.join(here, "venv", "pyvenv.cfg"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition("=")
                if key.strip().lower() == "home":
                    return os.path.exists(os.path.join(value.strip(), "python.exe"))
    except OSError:
        return False
    return False


def wanted_stamp(here):
    try:
        with open(os.path.join(here, "requirements.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def engine_installed(here):
    """強い文字読み取りの部品が入っているか（入れ終えて、試しに読めたときだけ印がある）。"""
    return os.path.exists(os.path.join(here, ENGINE_DIR, "ok.txt"))


def setup_done(here):
    """venv が動き、今の requirements.txt で部品を入れ終わっているか。
    強い文字読み取りも部品のうち（前の版から更新した人にも、次の起動で入る）。
    入らなかったPCには印があるので、起動のたびに試し直さない。
    Python 入りの版は、部品ごと入れてあるので済んでいる。"""
    if bundled(here):
        return True
    if not venv_ok(here):
        return False
    if not (engine_installed(here) or os.path.exists(engine_tried_path(here))):
        return False
    try:
        with open(os.path.join(here, "venv", STAMP_NAME), encoding="utf-8") as f:
            return f.read().strip() == wanted_stamp(here)
    except OSError:
        return False


def console_python():
    """黒い窓で動かす python.exe（.pyw は pythonw.exe で動いているので隣を探す）。"""
    exe = sys.executable or "python"
    cand = os.path.join(os.path.dirname(exe), "python.exe")
    return cand if os.path.exists(cand) else exe


def start_app(here):
    """アプリの pythonw で main.py を開く（窓なし・この起動口とは切り離す）。"""
    _, pyw = app_exes(here)
    subprocess.Popen([pyw, os.path.join(here, "main.py")], cwd=here,
                     creationflags=DETACHED, close_fds=True)


def open_console(here, mode):
    """黒い窓で winsetup.py を動かす（セットアップ・デバッグ起動）。"""
    subprocess.Popen([console_python(), os.path.join(here, "winsetup.py"), mode],
                     cwd=here, creationflags=NEW_CONSOLE)


def run(here, mode):
    """mode: "launch"（起動）/ "debug"（デバッグ起動）。
    印を外してから動かす。終了コードを返す。"""
    if not (os.path.exists(os.path.join(here, "main.py"))
            and os.path.exists(os.path.join(here, "winsetup.py"))):
        tell(here,
             "同じフォルダにアプリの本体（main.py）が見つかりません。\n\n"
             "zip を右クリック →「すべて展開」してから、出てきたフォルダの中の\n"
             "起動.pyw を開いてください。")
        return 1

    top = top_of(here)
    if not move_into_app(top, here):
        tell(here,
             "前の版のフォルダを、新しい並び（app フォルダ）へ移す途中で、\n"
             "使っている最中のファイルがありました。\n\n"
             "TextToVoicevox を閉じてから、もう一度「起動」を開いてください。")
        return 1
    unblock(top)
    remove_legacy(top)
    place_launcher(top, here)

    try:
        if mode == "launch" and setup_done(here):
            start_app(here)
        else:
            open_console(here, mode)
    except OSError as e:
        tell(here, "起動できませんでした: %s" % e)
        return 1
    return 0
