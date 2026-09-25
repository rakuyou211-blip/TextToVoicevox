# -*- coding: utf-8 -*-
"""配布用の zip を、Windows 用と Mac 用に分けて作る。

    python tools/make_release.py [出力フォルダ] [--win-exe 署名済みの TextToVoicevox.exe]

できるもの（名前に版数を入れないので、ダウンロードのリンクが版をまたいで変わらない）:
    TextToVoicevox_Windows.zip … .bat・ocr_win.ps1 など Windows で使うものだけ
    TextToVoicevox_Mac.zip     … .command・.sh・ocr_mac.py など Mac で使うものだけ

中身は git 管理のファイル（作業中の変更は入らない）。テスト・開発用の道具・
1行導入の台本（install.ps1 / install.sh。ネットから直接読むもの）は入れない。
.bat は CRLF、.command / .sh は LF のまま（.gitattributes どおり）入れ、
.command / .sh には実行権限を付ける。Release を出すと GitHub Actions
（.github/workflows/release.yml）がこれを動かして、2つの zip を自動で添付する。
"""
import os
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP = "TextToVoicevox"   # zip を開いたときのフォルダ名（これまでと同じ）

NAMES = {"windows": "TextToVoicevox_Windows.zip", "mac": "TextToVoicevox_Mac.zip"}

# どちらにも入れないもの
_SKIP_DIRS = ("tests/", "tools/", ".github/", ".claude/", "launcher/")
_SKIP_FILES = {".gitattributes", ".gitignore", "install.ps1", "install.sh"}


def _windows_only(path):
    return path.endswith((".bat", ".ps1")) or path == "requirements-english-ocr.txt"


def _mac_only(path):
    return path.endswith((".command", ".sh")) or path == "ocr_mac.py"


def files_for(target, tracked):
    """target（"windows" / "mac"）の zip に入れるファイルを、並べて返す。"""
    other_only = _mac_only if target == "windows" else _windows_only
    out = []
    for f in tracked:
        if f.startswith(_SKIP_DIRS) or f in _SKIP_FILES or other_only(f):
            continue
        out.append(f)
    return sorted(out)


def tracked_files(root=ROOT):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root,
                         capture_output=True, check=True).stdout
    return [f for f in out.decode("utf-8").split("\0") if f]


def build(target, out_dir, root=ROOT, extra=None):
    """target 用の zip を out_dir に作り、そのパスを返す。
    extra: zip の一番上に足すファイルのパス（署名済みの起動用 .exe など）。"""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, NAMES[target])
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files_for(target, tracked_files(root)):
            src = os.path.join(root, f)
            info = zipfile.ZipInfo(f"{TOP}/{f}",
                                   time.localtime(os.path.getmtime(src))[:6])
            mode = 0o755 if f.endswith((".command", ".sh", ".bat")) else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(src, "rb") as fh:
                z.writestr(info, fh.read())
        for src in extra or ():
            info = zipfile.ZipInfo(f"{TOP}/{os.path.basename(src)}",
                                   time.localtime(os.path.getmtime(src))[:6])
            info.external_attr = (0o100000 | 0o755) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(src, "rb") as fh:
                z.writestr(info, fh.read())
    return path


def main(argv):
    args = list(argv[1:])
    win_extra = []
    if "--win-exe" in args:
        i = args.index("--win-exe")
        win_extra.append(args[i + 1])
        del args[i:i + 2]
    out_dir = args[0] if args else os.path.join(ROOT, "dist")
    for target in ("windows", "mac"):
        path = build(target, out_dir, extra=win_extra if target == "windows" else None)
        with zipfile.ZipFile(path) as z:
            n = len(z.infolist())
        print(f"{path}  ({n} files, {os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv)
