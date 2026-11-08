# -*- coding: utf-8 -*-
"""1行導入（install.ps1）が入れる「Python 入りの Windows 版」を作る（Windows で動かす）。

    python tools/make_bundle.py [出力フォルダ]

できるもの: TextToVoicevox_OneLine_Win64.zip（約200MB）
    TextToVoicevox/
        main.py ほか   … Windows 用 zip（make_release.py）と同じ中身
        runtime/       … python.org の Python（画面の部品 tkinter 入り・PSF の署名付き）に、
                         アプリの部品（requirements.txt）を入れた物。入れる人の PC に Python は要らない
        ocr_engine/    … 画像の文字を読む部品（winsetup.py が初回に作る物と同じ）
        THIRD-PARTY-NOTICES.txt … 一緒に配る部品と、そのライセンスの在りか

1行導入の専用で、README からはリンクしない。ブラウザで落とした zip には「インターネットから
来た」印が付き、それを Explorer で展開すると、Windows の Defender が python.exe と
python3xx.dll の組で固まって、240秒たって諦めるまで DLL を離さない（2026-11-06 実測。
Python 3.14・3.15 とも。Git の curl や Chrome の exe と DLL の組では起きず、zip の中の
並べ方でも変わらない）。1行導入は PowerShell 自身が落とすので印が付かず、展開は数秒で終わる。

手元で確かめるとき（ネットから落とさない）:
    --runtime-dir 展開済みの python.org の Python（py install で入れた物など。
                  site-packages は pip だけ使う）
    --deps-from   部品の入った site-packages（同じ版の Python で作った venv など）
    --engine-from 出来上がった ocr_engine フォルダ
"""
import argparse
import email.parser
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_release  # noqa: E402

NAME = "TextToVoicevox_OneLine_Win64.zip"
TOP = make_release.TOP
RUNTIME = "runtime"     # winlaunch.RUNTIME_DIR と同じ
ENGINE = "ocr_engine"   # winlaunch.ENGINE_DIR と同じ

# アプリを動かす Python（python.org の配布物。Python install manager が入れるのと同じ zip）。
# 版を上げるときは、https://www.python.org/ftp/python/index-windows.json にある
# 同じ版の sha256 に替え、RUNTIME_PTH の名前（python3xx）も合わせる
RUNTIME_URL = "https://www.python.org/ftp/python/3.15.0/python-3.15.0-amd64.zip"
RUNTIME_SHA256 = "6b1a3f885ce416cf3a68af203e62203664eb5224d8d0c1d2a1945fddec5c500b"
RUNTIME_PTH = "python315._pth"

# 動かすのに要らない物（説明書・C の見出しと部品・ほかの Python 用の起動口・試験・IDLE）
RUNTIME_DROP = ("Doc", "include", "libs", "Scripts", "Lib/test", "Lib/idlelib", "Lib/turtledemo",
                "__install__.json")

# winsetup.py と同じく、入らなくても止めない部品（ドラッグ＆ドロップ）
OPTIONAL_DEPS = ["tkinterdnd2>=0.3,<1"]

# 1行導入は、入れる場所の隣（%LOCALAPPDATA%\TextToVoicevox.new）で広げる。名前の長い
# ユーザーでも Windows の 260 文字に収まるよう、zip の中の道のりはこれ以下にする
LONGEST = 150


def say(text=""):
    print(text, flush=True)


def run(cmd, **kw):
    say("  > " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def download(url, dest, sha256):
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise SystemExit("ダウンロードした %s が、決めてある物と違います" % url)


def runtime_pth():
    """runtime の Python が見る場所を、この中とアプリのフォルダ（..）だけにする。
    Windows の環境変数（PYTHONPATH など）や、別に入っている Python の部品と混ざらない。
    ._pth があると、開いたスクリプトの隣を自動では見ないので、アプリのフォルダを先頭に書く。"""
    return "..\nDLLs\nLib\nLib\\site-packages\nimport site\n"


def drop_caches(folder):
    for path in glob.glob(os.path.join(folder, "**", "__pycache__"), recursive=True):
        shutil.rmtree(path, ignore_errors=True)


def copy_app(root, app):
    """Windows 用 zip と同じファイルを置く（git 管理の物だけ）。"""
    for f in make_release.files_for("windows", make_release.tracked_files(root)):
        dst = os.path.join(app, *f.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(root, *f.split("/")), dst)


def put_runtime(rt, runtime_dir=None, work=None):
    if runtime_dir:
        own_sp = os.path.normcase(os.path.join(os.path.abspath(runtime_dir), "Lib", "site-packages"))

        def skip(folder, names):
            # 手元の Python の site-packages には、その人が入れた物もある。pip だけ使う
            if os.path.normcase(os.path.abspath(folder)) == own_sp:
                return [n for n in names if not n.lower().startswith("pip")]
            return [n for n in names if n == "__pycache__"]
        shutil.copytree(runtime_dir, rt, ignore=skip)
    else:
        archive = os.path.join(work, "python.zip")
        say("Python を落としています: " + RUNTIME_URL)
        download(RUNTIME_URL, archive, RUNTIME_SHA256)
        with zipfile.ZipFile(archive) as z:
            z.extractall(rt)
        os.remove(archive)
    if not os.path.exists(os.path.join(rt, "pythonw.exe")):
        raise SystemExit("Python の中に pythonw.exe がありません: " + rt)
    for rel in RUNTIME_DROP:
        path = os.path.join(rt, *rel.split("/"))
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    drop_caches(rt)
    with open(os.path.join(rt, RUNTIME_PTH), "w", encoding="utf-8") as f:
        f.write(runtime_pth())


def put_deps(rt, root, deps_from=None):
    py = os.path.join(rt, "python.exe")
    sp = os.path.join(rt, "Lib", "site-packages")
    if deps_from:
        for name in os.listdir(deps_from):
            if name.lower().startswith("pip") or name == "__pycache__":
                continue
            src = os.path.join(deps_from, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(sp, name), ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src, os.path.join(sp, name))
    else:
        if subprocess.call([py, "-m", "pip", "--version"]) != 0:
            run([py, "-m", "ensurepip"])
        pip = [py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
               "--no-compile", "--no-input"]
        run(pip + ["-r", os.path.join(root, "requirements.txt")])
        if subprocess.call(pip + OPTIONAL_DEPS) != 0:
            say("  ドラッグ＆ドロップ部品は入りませんでした（無くても動きます）")
    # pip が作る起動口（pypdfium2.exe など）は使わない。署名の無い exe は配らない
    shutil.rmtree(os.path.join(rt, "Scripts"), ignore_errors=True)


def put_engine(app, engine_from=None):
    """画像の文字を読む部品。無いと強い読み取りが使えないので、作れなければ止める。"""
    dest = os.path.join(app, ENGINE)
    if engine_from:
        shutil.copytree(engine_from, dest, ignore=shutil.ignore_patterns("__pycache__"))
    else:
        run([os.path.join(app, RUNTIME, "python.exe"), os.path.join(app, "winsetup.py"), "engine"], cwd=app)
    if not os.path.exists(os.path.join(dest, "ok.txt")):
        raise SystemExit("画像の文字を読む部品を作れませんでした")
    for path in glob.glob(os.path.join(app, "*", ".t2v_engine_tried")):
        os.remove(path)


def compile_all(app):
    """初回の起動で .pyc を作らずに済むよう、先に作っておく（無いと最初の画面が出るまで遅い）。"""
    rt_py = os.path.join(app, RUNTIME, "python.exe")
    # Python と部品: 更新ではフォルダごと入れ替わるので、元のファイルを見に行かない形（速い）。
    # ふりがなの辞書（Janome の entries_compact*.py）はデータとして読むので作らない
    run([rt_py, "-m", "compileall", "-q", "-f", "-j", "0", "--invalidation-mode", "unchecked-hash",
         "-x", "entries_compact", os.path.join(app, RUNTIME, "Lib")])
    # アプリ本体: 更新で .py が上書きされても古い .pyc を使わないよう、中身で確かめる形
    run([rt_py, "-m", "compileall", "-q", "-f", "-l", "--invalidation-mode", "checked-hash", app])
    eng_py = os.path.join(app, ENGINE, "python", "python.exe")
    if subprocess.call([eng_py, "-m", "compileall", "-q", "-f", "-j", "0", "--invalidation-mode",
                        "unchecked-hash", os.path.join(app, ENGINE, "deps")]) != 0:
        say("  文字を読む部品の .pyc は作れませんでした（初回に作られます）")


def check(app):
    """入れた Python で、画面の部品とアプリの部品が読めるか。"""
    run([os.path.join(app, RUNTIME, "python.exe"), "-c",
         "import sys, tkinter, PIL, pypdfium2, requests, janome, core; tkinter.Tcl(); "
         "print(sys.version.split()[0], 'Tk', tkinter.TkVersion)"], cwd=app)


def _dists(site):
    out = []
    for info in sorted(glob.glob(os.path.join(site, "*.dist-info"))):
        try:
            with open(os.path.join(info, "METADATA"), encoding="utf-8", errors="replace") as f:
                meta = email.parser.Parser().parse(f, headersonly=True)
        except OSError:
            continue
        lic = meta.get("License-Expression") or ", ".join(
            c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::"))
        if not lic:
            first = (meta.get("License") or "").strip().splitlines()
            lic = first[0][:80] if first else ""
        files = [os.path.relpath(p, info).replace(os.sep, "/")
                 for p in glob.glob(os.path.join(info, "**", "*"), recursive=True)
                 if os.path.isfile(p) and ("LICEN" in os.path.basename(p).upper()
                                           or "COPYING" in os.path.basename(p).upper()
                                           or "NOTICE" in os.path.basename(p).upper())]
        out.append((meta.get("Name") or os.path.basename(info), meta.get("Version") or "", lic or "?",
                    os.path.basename(info), files))
    return out


def write_notices(app):
    """一緒に配る部品の一覧と、ライセンス本文の在りか（それぞれの .dist-info の中）。"""
    lines = ["TextToVoicevox（Python 入りの版）に一緒に入っている部品と、そのライセンスです。",
             "ライセンスの本文は、それぞれのフォルダの中にあります。", "",
             "== runtime/  Python %s（Python Software Foundation License。本文: runtime/LICENSE.txt）"
             % RUNTIME_URL.split("/")[-2], ""]
    groups = (("runtime/Lib/site-packages", os.path.join(app, RUNTIME, "Lib", "site-packages")),
              ("ocr_engine/deps", os.path.join(app, ENGINE, "deps")))
    for label, site in groups:
        if label.startswith("ocr_engine"):
            lines += ["", "== ocr_engine/python  Python（Python Software Foundation License。"
                          "本文: ocr_engine/python/LICENSE.txt）", ""]
        lines.append("-- %s" % label)
        for name, version, lic, folder, files in _dists(site):
            lines.append("%s %s  [%s]" % (name, version, lic))
            for f in files:
                lines.append("    %s/%s/%s" % (label, folder, f))
    with open(os.path.join(app, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


def longest(app):
    base = os.path.dirname(app)
    n, worst = 0, ""
    for folder, dirs, files in os.walk(app):
        for name in files:
            rel = os.path.relpath(os.path.join(folder, name), base)
            if len(rel) > n:
                n, worst = len(rel), rel
    return n, worst


def pack(app, out):
    base = os.path.dirname(app)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for folder, dirs, files in os.walk(app):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(folder, name)
                z.write(path, os.path.relpath(path, base).replace(os.sep, "/"))
                count += 1
    return count


def build(out_dir, root=ROOT, runtime_dir=None, deps_from=None, engine_from=None, work=None, keep=False):
    """zip を作り、そのパスを返す。"""
    if os.name != "nt":
        raise SystemExit("Windows で動かしてください（中の Python を動かして部品を入れるため）")
    os.makedirs(out_dir, exist_ok=True)
    work = work or tempfile.mkdtemp(prefix="t2v_bundle_")
    app = os.path.join(work, TOP)
    if os.path.exists(app):
        shutil.rmtree(app)
    t0 = time.time()
    try:
        say("== アプリのファイル")
        copy_app(root, app)
        say("== Python")
        put_runtime(os.path.join(app, RUNTIME), runtime_dir, work)
        say("== アプリの部品")
        put_deps(os.path.join(app, RUNTIME), root, deps_from)
        say("== 画像の文字を読む部品")
        put_engine(app, engine_from)
        say("== .pyc")
        compile_all(app)
        say("== 確かめる")
        check(app)
        write_notices(app)
        n, worst = longest(app)
        say("  一番長い道のり: %d 文字 (%s)" % (n, worst))
        if n > LONGEST:
            raise SystemExit("道のりが長すぎます（%d > %d）: %s" % (n, LONGEST, worst))
        say("== zip")
        out = os.path.join(out_dir, NAME)
        count = pack(app, out)
        say("%s  (%d files, %.0f MB, %.0f秒)" % (out, count, os.path.getsize(out) / 1e6, time.time() - t0))
        return out
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="1行導入が入れる、Python 入りの Windows 版を作る")
    ap.add_argument("out", nargs="?", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--runtime-dir", help="展開済みの python.org の Python（手元で確かめる用）")
    ap.add_argument("--deps-from", help="部品の入った site-packages（手元で確かめる用）")
    ap.add_argument("--engine-from", help="出来上がった ocr_engine（手元で確かめる用）")
    ap.add_argument("--work", help="作業用のフォルダ（既定は一時フォルダ）")
    ap.add_argument("--keep", action="store_true", help="作業用のフォルダを残す")
    a = ap.parse_args(argv)
    build(a.out, runtime_dir=a.runtime_dir, deps_from=a.deps_from, engine_from=a.engine_from,
          work=a.work, keep=a.keep)


if __name__ == "__main__":
    main()
