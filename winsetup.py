# -*- coding: utf-8 -*-
"""Windows の黒い窓で動く部分（初回セットアップ・デバッグ起動・強い文字読み取りの導入）。

自分では開かない。起動.pyw / デバッグ起動.pyw が、
ダウンロードの印を外してから、黒い窓（コンソール）でこれを動かす。
v1.23.1 までは .bat でやっていたことを、止められない Python に移した
（理由は winlaunch.py の先頭）。

  python winsetup.py launch   初回セットアップ → アプリを開く
  python winsetup.py debug    （要ればセットアップ →）エラーを画面に出したまま起動
  python winsetup.py ocr      強い文字読み取り（PP-OCRv6）の部品を入れ直す（アプリのボタンから）
  python winsetup.py engine   無ければ入れる。何も聞かない（1行導入 install.ps1 から）

強い文字読み取りは、初回セットアップで一緒に入る（押すものを増やさないため）。

Python は 3.9 以降が必要（画面の部品 tkinter も要る）。
"""
import glob
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import winlaunch  # noqa: E402

MIN_PYTHON = (3, 9)
PIP = ["-m", "pip", "install", "--disable-pip-version-check"]

# 強い文字読み取りを動かす Python（python.org の配布用）。アプリの Python とは別に持つ
# （アプリの Python が新しいと、読み取りの部品 onnxruntime が入らないため）。
# 版を上げるときは、ハッシュと requirements-ocr-engine.txt も一緒に替える
ENGINE_PY_URL = "https://www.python.org/ftp/python/3.14.0/python-3.14.0-embed-amd64.zip"
ENGINE_PY_SHA256 = "8d4d3590c10449d78aa4375f534e6d5f3027d67fdc362dd1a882279db6f90fdf"
ENGINE_PY_PTH = "python314._pth"
ENGINE_PIP = ["--no-input", "--no-compile", "--no-deps", "--require-hashes", "--only-binary=:all:",
              "--python-version", "3.14", "--implementation", "cp", "--abi", "cp314",
              "--platform", "win_amd64"]
ENGINE_SIZE_NOTE = "約122MBのダウンロードで（python.orgとPyPIから）、アプリが約100MB大きくなります。"
# 読み取りに要らないので、入れたあと消す（動画の部品・モデルを変換する道具・部品の試験）
ENGINE_TRIM = ("cv2/opencv_videoio_ffmpeg*.dll", "onnxruntime/tools", "onnxruntime/quantization",
               "onnxruntime/transformers", "numpy/tests", "numpy/*/tests", "shapely/tests")
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"


def say(text=""):
    print(text, flush=True)


def wait_close():
    try:
        input("\nEnter キーを押すと閉じます…")
    except (EOFError, KeyboardInterrupt):
        pass


def python_usable():
    """この Python で venv を作れるか。だめなら理由を出して False。"""
    if sys.version_info < MIN_PYTHON:
        say("[!] Python %d.%d では動きません（3.9 以降が必要です）。" % sys.version_info[:2])
        say("    https://www.python.org/downloads/ から新しい Python を入れてください。")
        say("    インストール画面の下にある \"Add python.exe to PATH\" にも")
        say("    チェックを入れてください。")
        return False
    try:
        import tkinter  # noqa: F401
    except ImportError:
        say("[!] この Python には画面の部品（tkinter）が入っていません。")
        say("    https://www.python.org/downloads/ の公式版を入れ直してください")
        say("    （インストール画面の「tcl/tk and IDLE」にチェック）。")
        return False
    return True


def pip_works(py):
    try:
        return subprocess.call([py, "-m", "pip", "--version"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except OSError:
        return False


def compact_folder(target):
    """フォルダを Windows の圧縮機能（読み出し専用向けの LZX）で小さくする。中身はそのままで、
    ディスク上だけ小さくなる。圧縮できない場所（USB メモリなど）では圧縮されないだけで
    動作に影響しないので、できなくても止めない。"""
    if os.name != "nt" or not os.path.isdir(target):
        return False
    try:
        return subprocess.call(["compact", "/c", "/s:" + target, "/i", "/q", "/exe:lzx"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=600) == 0
    except Exception:
        return False


def shrink_venv(venv_dir):
    """venv の部品（site-packages。ふりがなの辞書 Janome の約250MB を含めて約300MB）を
    約90MBにする。読み込みの速さは変わらない（実測: 辞書 0.2秒・アプリが開くまで 0.6秒、
    圧縮あり・なしとも同じ）。以前は辞書だけを小さくしていて、ほかの部品（画像・PDF の
    部品など約40MB）は、そのままの大きさで置いていた。"""
    return compact_folder(os.path.join(venv_dir, "Lib", "site-packages"))


def setup():
    """venv を作って部品を入れる。成功なら True。"""
    py, _ = winlaunch.venv_exes(HERE)
    venv_dir = os.path.join(HERE, "venv")
    say("初回セットアップをしています。")
    say("（初回だけネット接続が必要です。数分かかることがあります）")
    say()
    if not python_usable():
        return False

    if winlaunch.venv_ok(HERE):
        # venv を作る途中（pip を入れる前）で窓を閉じた・ウイルス対策に止められた、など。
        # 見た目は揃っていても pip が無いので、作り直さないと何度開いても進めない
        need_new = not pip_works(py)
        if need_new:
            say("作りかけの venv があったので、作り直します。")
    else:
        need_new = True
        if os.path.exists(venv_dir):
            # 別のPCから持ってきた・Python を入れ直した venv は動かないので作り直す
            say("今のパソコンでは動かない venv があったので、作り直します。")
    if need_new:
        say("仮想環境（venv）を作っています…")
        if subprocess.call([sys.executable, "-m", "venv", "--clear", venv_dir]) != 0:
            say()
            say("[ERROR] 仮想環境の作成に失敗しました。")
            say("    Python 3.9 以降が正しく入っているか確認してください:")
            say("        https://www.python.org/downloads/")
            return False

    say("必要な部品を入れています（requirements.txt）…")
    if subprocess.call([py] + PIP + ["-r", os.path.join(HERE, "requirements.txt")]) != 0:
        say()
        say("[ERROR] 部品のインストールに失敗しました。")
        say("    ネット接続を確認して、もう一度 起動.pyw を開いてください。")
        say("    何度やっても同じなら、このフォルダの「venv」フォルダを消してから開くと、")
        say("    最初から作り直します。")
        return False

    # ドラッグ＆ドロップ部品は任意。対応する部品が無い環境（ARM64版Windowsなど）が
    # あるため requirements.txt の必須側からは外してある。入らなくても止めない
    say("ドラッグ＆ドロップ部品を入れています（任意）…")
    if subprocess.call([py] + PIP + ["tkinterdnd2>=0.3,<1"]) != 0:
        say("  入りませんでした。ファイルは「選ぶ」ボタンから使えます。")

    say("部品を小さくしています（約300MB → 約90MB）…")
    if not shrink_venv(venv_dir):
        say("  小さくできませんでした（動作には影響ありません）。")

    ensure_engine(py)

    # ここまで来たら済んだ印を置く（途中で窓を閉じたら、次の起動でやり直す）
    try:
        with open(os.path.join(venv_dir, winlaunch.STAMP_NAME), "w", encoding="utf-8") as f:
            f.write(winlaunch.wanted_stamp(HERE))
    except OSError:
        pass
    return True


def do_launch():
    if not winlaunch.setup_done(HERE) and not setup():
        say()
        say("[!] セットアップが完了していないため、起動できませんでした。")
        say("    上に出ているメッセージを確認してください。")
        wait_close()
        return 1
    say()
    say("セットアップ完了。アプリを開きます。")
    winlaunch.start_app(HERE)
    time.sleep(2)  # 完了の一言を読めるくらいは窓を残す
    return 0


def do_debug():
    if not winlaunch.setup_done(HERE) and not setup():
        wait_close()
        return 1
    py, _ = winlaunch.app_exes(HERE)
    say("デバッグ起動: エラーが起きたら、この窓に内容が出ます。")
    say()
    code = subprocess.call([py, os.path.join(HERE, "main.py")], cwd=HERE)
    say()
    say("---- 終了しました（終了コード %d）----" % code)
    wait_close()
    return 0


def engine_supported():
    """強い文字読み取りを動かせる Windows か（配布されている Python が動く範囲）。"""
    return (sys.getwindowsversion().major >= 10
            and platform.machine().upper() in ("AMD64", "X86_64", "ARM64"))


def download(url, dest, sha256):
    """url を dest に落とす。進み具合を出し、中身が決めたハッシュと違えば False。"""
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = shown = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if total and got * 100 // total >= shown + 25:
                    shown = got * 100 // total // 25 * 25
                    say("  %d%%" % shown)
    except OSError as e:
        say("[!] ダウンロードできませんでした: %s" % e)
        return False
    if digest.hexdigest() != sha256:
        say("[!] ダウンロードした中身が、決めてある物と違いました。入れるのをやめます。")
        return False
    return True


def trim_engine(deps):
    """読み取りに要らない物を消す（アプリをなるべく大きくしないため）。"""
    for pattern in ENGINE_TRIM:
        for path in glob.glob(os.path.join(deps, *pattern.split("/"))):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass


def engine_python(engine_dir):
    return os.path.join(engine_dir, "python", "python.exe")


def engine_reads(engine_dir):
    """入れた部品で、本当に文字を読めるか試す。読めれば True。"""
    try:
        proc = subprocess.run([engine_python(engine_dir), os.path.join(HERE, "ocr_helper.py"), "--selftest"],
                              cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        say("[!] 試しに読めませんでした: %s" % e)
        return False
    if proc.returncode == 0:
        return True
    say("[!] 試しに読めませんでした。")
    say((proc.stdout + proc.stderr).strip()[-800:])
    if "DLL" in proc.stdout + proc.stderr:
        say()
        say("Windows の部品（Microsoft Visual C++ 再頒布可能パッケージ）が足りないのかもしれません。")
        say("次のページから入れてから、もう一度お試しください:")
        say("    " + VC_REDIST_URL)
    return False


def build_engine(py, engine_dir):
    """engine_dir に部品一式を作り、試しに読めたら印（ok.txt）を置く。成功なら True。"""
    with tempfile.TemporaryDirectory(prefix="t2v_engine_") as tmp:
        say("読み取りを動かす Python を落としています（約12MB）…")
        archive = os.path.join(tmp, "python.zip")
        if not download(ENGINE_PY_URL, archive, ENGINE_PY_SHA256):
            return False
        python_dir = os.path.join(engine_dir, "python")
        with zipfile.ZipFile(archive) as z:
            z.extractall(python_dir)
    # この Python が見る場所を、ここだけにする（Windows の環境変数やアプリの部品と混ざらない）
    with open(os.path.join(python_dir, ENGINE_PY_PTH), "w", encoding="utf-8") as f:
        f.write("python314.zip\n.\n..\\deps\n")
    say()
    say("読み取りの部品を入れています（約110MB。数分かかることがあります）…")
    deps = os.path.join(engine_dir, "deps")
    req = os.path.join(HERE, "requirements-ocr-engine.txt")
    if subprocess.call([py] + PIP + ENGINE_PIP + ["--target", deps, "-r", req]) != 0:
        say("[!] 部品を入れられませんでした。ネット接続を確認して、もう一度お試しください。")
        return False
    trim_engine(deps)
    say()
    say("試しに読んでみています…（1分ほどかかることがあります）")
    if not engine_reads(engine_dir):
        return False
    say("小さくしています…")
    compact_folder(engine_dir)   # できなくても動く
    with open(os.path.join(engine_dir, "ok.txt"), "w", encoding="utf-8") as f:
        f.write("PP-OCRv6\n")
    return True


def install_engine(py):
    """強い文字読み取りを入れる。途中で失敗しても、今ある物は壊さない。成功なら True。"""
    final = os.path.join(HERE, winlaunch.ENGINE_DIR)
    fresh, old = final + ".new", final + ".old"
    shutil.rmtree(fresh, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    try:
        if not build_engine(py, fresh):
            return False
        if os.path.exists(final):
            os.replace(final, old)   # 動いている裏方がいると、ここで止まる
        os.replace(fresh, final)
    except OSError as e:
        say("[!] 入れられませんでした: %s" % e)
        say("    アプリを閉じてから、もう一度お試しください。")
        return False
    finally:
        shutil.rmtree(fresh, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return True


def ensure_engine(py):
    """強い文字読み取りが無ければ入れる。入らなくても止めない（Windows の読み取りで動く。
    あとからアプリの「読み取りを強くする」で入れられる）。入っていれば True。"""
    if winlaunch.engine_installed(HERE):
        return True
    say("画像の文字を読む部品を入れています（約122MB）。")
    if engine_supported() and install_engine(py):
        return True
    say("  入りませんでした。画像の文字は Windows の読み取りで読みます。")
    say("  あとから、アプリの ① の「設定 ▷」→「読み取りを強くする」で入れられます。")
    try:
        with open(winlaunch.engine_tried_path(HERE), "w", encoding="utf-8") as f:
            f.write("tried\n")
    except OSError:
        pass
    return False


def do_engine():
    """1行導入から呼ぶ。何も聞かず、入らなくても失敗にはしない。
    Python 入りの版を作るとき（tools/make_bundle.py）も、これで部品を作る。"""
    py, _ = winlaunch.app_exes(HERE)
    ensure_engine(py)
    return 0


def do_ocr():
    py, _ = winlaunch.app_exes(HERE)
    # 見るのは venv が動くかだけ（済んだ印は見ない）。1行導入（install.ps1）で入れた人の
    # venv は、起動.pyw を通らないので印が無いことがある。Python 入りの版には venv が無い
    if not (winlaunch.bundled(HERE) or winlaunch.venv_ok(HERE)):
        say("[!] まだセットアップが済んでいません。")
        say("    先に 起動.pyw を一度ダブルクリックして、アプリが開くのを確かめてから、")
        say("    もう一度これを開いてください。")
        wait_close()
        return 1
    say("文字の読み取りを強くする部品を入れます。")
    say("  ・" + ENGINE_SIZE_NOTE)
    say("  ・読み取りはこのPCの中だけで行い、文字も画像も外へ送りません")
    say("  ・入れなくても、アプリはこれまでどおり使えます")
    say()
    if not engine_supported():
        say("[!] このPCでは入れられません（Windows 10 以降の 64bit が対象です）。")
        say("    英語の画像だけなら、Windows に「英語（米国）」を追加すると読めるようになります。")
        wait_close()
        return 1
    if not install_engine(py):
        say()
        say("入れられませんでした。これまでの読み取りは、そのまま使えます。")
        wait_close()
        return 1
    say()
    say("入りました。次に文字を読むときから、自動で使います（アプリは開いたままで大丈夫です）。")
    time.sleep(4)  # 完了の一言を読めるくらいは窓を残す（閉じる操作は要らない）
    return 0


def main(argv):
    mode = argv[1] if len(argv) > 1 else "launch"
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("TextToVoicevox")
    except Exception:
        pass
    handler = {"launch": do_launch, "debug": do_debug, "ocr": do_ocr, "engine": do_engine}.get(mode)
    if handler is None:
        say("使い方: python winsetup.py [launch|debug|ocr|engine]")
        return 2
    try:
        return handler()
    except Exception as e:  # 想定外でも窓が一瞬で消えないように
        say()
        say("[ERROR] 想定外のエラー: %r" % (e,))
        wait_close()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
