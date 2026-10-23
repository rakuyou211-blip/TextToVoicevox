# -*- coding: utf-8 -*-
"""Mac 用 setup.command / 起動.command の Python の選び方を、偽の python で確かめる。

2026-10-03 の配布後チェックで見つかった2つの穴の再発防止:
  - 「ある」だけで python3 を選んでいたので、tkinter の無い Homebrew 版を選ぶと
    アプリが何も言わずに起動しなかった
  - 前の venv_mac をそのまま使い回していたので、作り直しが効かなかった
2026-10-23 のリリース前チェックで見つかった穴の再発防止:
  - 1行導入が入れる自分用 Python（python-build-standalone）から作った venv では、
    tkinter の確かめが main.py の _fix_tcl_library の手当て無しに走って必ず落ち、
    動く venv を毎回作り直したうえ「起動できませんでした」で止まっていた
  - 作り直しの途中で部品のダウンロードに失敗すると（オフライン等）、動いていた venv が消えていた

本物の Python や pip は使わず、呼ばれ方を記録するだけの偽 python（bash）を置く。
候補は setup.command のテスト用の T2V_PY_CANDIDATES で差し替える。bash があれば
Linux の CI でも動く（Windows は .command を使わないので飛ばす）。
"""
import os
import re
import shutil
import subprocess
import sys
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="Mac 用のシェルスクリプトは bash のある Mac / Linux で確かめる")

# kind=good … tkinter の確かめ（-c に tkinter を含む）が通る Python
# kind=bad  … 動きはするが tkinter が無い Python（Homebrew の python-tk 無しなど）
# -m venv で作る venv の python は、元の Python の性質を引き継ぐ（自分をコピーする）
FAKE_PY = """#!/bin/bash
echo "$0 $*" >> "$T2V_TEST_LOG"
kind={kind}
if [ "$1" = -c ]; then
    case "$2" in *tkinter*) [ "$kind" = good ] && exit 0; exit 1 ;; esac
    exit 0
fi
if [ "$1" = -m ] && [ "$2" = venv ]; then
    for d; do :; done
    [ "$3" = --clear ] && rm -rf "$d"
    mkdir -p "$d/bin" && cp "$0" "$d/bin/python" && chmod +x "$d/bin/python"
    exit 0
fi
# T2V_FAKE_PIP_FAIL=1 … 部品のダウンロードに失敗した（オフライン等）ふりをする
if [ "$1" = -m ] && [ "$2" = pip ] && [ "$3" = install ] && [ "$4" = -r ] \
        && [ -n "${{T2V_FAKE_PIP_FAIL:-}}" ]; then
    exit 1
fi
exit 0
"""


def _fake_python(path, kind):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(FAKE_PY.format(kind=kind))
    os.chmod(path, 0o755)
    return path


@pytest.fixture
def app(tmp_path):
    """配布 zip を展開したのと同じ並びの作業フォルダ（スクリプト＋requirements.txt）。"""
    d = tmp_path / "app"
    d.mkdir()
    for name in ("setup.command", "起動.command"):
        shutil.copy(os.path.join(ROOT, name), d / name)
    (d / "requirements.txt").write_text("", encoding="utf-8")
    (d / "main.py").write_text("", encoding="utf-8")
    pys = tmp_path / "pys"
    return {
        "dir": d,
        "log": tmp_path / "calls.log",
        "good": _fake_python(str(pys / "good" / "python3"), "good"),
        "bad": _fake_python(str(pys / "bad" / "python3"), "bad"),
    }


def _run(app, script, candidates, **extra_env):
    env = dict(os.environ, T2V_PY_CANDIDATES=":".join(candidates),
               T2V_TEST_LOG=str(app["log"]), LANG="ja_JP.UTF-8", **extra_env)
    r = subprocess.run(["bash", str(app["dir"] / script)], cwd=str(app["dir"]),
                       stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60)
    return r.returncode, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")


def _log(app):
    try:
        return app["log"].read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _wait_for_main(app, timeout=5.0):
    """起動.command は main.py を nohup で裏に回すので、記録に出るまで少し待つ。"""
    end = time.time() + timeout
    while time.time() < end:
        if " main.py" in _log(app):
            return True
        time.sleep(0.1)
    return False


def test_setup_skips_python_without_tkinter(app):
    """tkinter の無い Python は飛ばし、次の候補（tkinter あり）で venv を作る。"""
    rc, out = _run(app, "setup.command", [app["bad"], "/nonexistent/python3", app["good"]])
    assert rc == 0, out
    assert "見送ります" in out and app["bad"] in out, out
    assert f"使用する Python: {app['good']}" in out, out
    log = _log(app)
    assert f"{app['good']} -m venv venv_mac" in log, log
    assert f"{app['bad']} -m venv" not in log, log
    assert "venv_mac/bin/python -m pip install -r requirements.txt" in log, log


def test_setup_stops_with_guidance_when_no_tk_python(app):
    """使える Python が無ければ、venv を作らずに python.org / python-tk を案内して止まる。"""
    rc, out = _run(app, "setup.command", [app["bad"], "/nonexistent/python3"])
    assert rc == 1, out
    assert "https://www.python.org/downloads/" in out, out
    assert "brew install python-tk" in out, out
    assert "-m venv" not in _log(app)
    assert not (app["dir"] / "venv_mac").exists()


def test_setup_rebuilds_existing_venv(app):
    """前に作った venv_mac が残っていても、今の Python で作り直す（作り直しが効く）。
    出来上がったら、退かしておいた前のもの（venv_mac.old）は片付ける。"""
    _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "bad")
    (app["dir"] / "venv_mac" / "old-marker").write_text("", encoding="utf-8")
    rc, out = _run(app, "setup.command", [app["good"]])
    assert rc == 0, out
    assert f"{app['good']} -m venv venv_mac" in _log(app)
    assert "kind=good" in (app["dir"] / "venv_mac" / "bin" / "python").read_text(encoding="utf-8")
    assert not (app["dir"] / "venv_mac" / "old-marker").exists()
    assert not (app["dir"] / "venv_mac.old").exists()


def test_setup_keeps_old_venv_when_download_fails(app):
    """作り直しの途中で部品のダウンロードに失敗したら（オフライン等）、前の venv_mac に戻す。
    v1.24.2 の最初の版は --clear で先に中を空にしていたので、動いていた venv が消えていた。"""
    _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "good")
    (app["dir"] / "venv_mac" / "old-marker").write_text("", encoding="utf-8")
    rc, out = _run(app, "setup.command", [app["good"]], T2V_FAKE_PIP_FAIL="1")
    assert rc == 1, out
    assert "インストールに失敗しました" in out and "前の venv_mac に戻しました" in out, out
    assert (app["dir"] / "venv_mac" / "old-marker").exists()
    assert not (app["dir"] / "venv_mac.old").exists()


def test_launcher_still_starts_after_failed_rebuild_offline(app):
    """動く venv があるときに setup.command を開き直してオフラインで失敗しても、
    venv は元のまま残るので、起動.command はそのまま起動できる。"""
    _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "good")
    rc, _ = _run(app, "setup.command", [app["good"]], T2V_FAKE_PIP_FAIL="1")
    assert rc == 1
    rc, out = _run(app, "起動.command", [app["good"]], T2V_FAKE_PIP_FAIL="1")
    assert rc == 0, out
    assert "作り直します" not in out, out
    assert _wait_for_main(app), _log(app)


def test_setup_ignores_python_inside_venv_mac(app):
    """venv_mac の中の Python は、作り直す当の物なので候補から外す。"""
    inner = _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "good")
    rc, out = _run(app, "setup.command", [inner, app["good"]])
    assert rc == 0, out
    assert f"使用する Python: {app['good']}" in out, out


def test_launcher_rebuilds_venv_without_tkinter(app):
    """venv_mac の Python で tkinter が読めなければ、無言で終わらずに案内して作り直し、起動する。"""
    _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "bad")
    rc, out = _run(app, "起動.command", [app["good"]])
    assert rc == 0, out
    assert "tkinter" in out and "作り直します" in out, out
    assert f"{app['good']} -m venv venv_mac" in _log(app)
    assert _wait_for_main(app), _log(app)


def test_launcher_stops_with_guidance_when_setup_fails(app):
    """作り直しても tkinter が使えなければ、main.py を起動せず、何を入れればよいか案内する。"""
    _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "bad")
    rc, out = _run(app, "起動.command", [app["bad"]])
    assert rc == 1, out
    assert "起動できませんでした" in out, out
    assert "brew install python-tk" in out and "setup.command" in out, out
    time.sleep(0.5)
    assert " main.py" not in _log(app)


def test_launcher_starts_directly_when_venv_is_fine(app):
    """venv_mac が健全なら、セットアップを挟まずにそのまま起動する。"""
    _fake_python(str(app["dir"] / "venv_mac" / "bin" / "python"), "good")
    rc, out = _run(app, "起動.command", [app["bad"]])
    assert rc == 0, out
    assert "-m venv" not in _log(app)
    assert _wait_for_main(app), _log(app)


# ---- tkinter の確かめ方そのもの（T2V_TK_CHECK） ----

_SCRIPTS = ("setup.command", "起動.command", "install.sh")


def _tk_check(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        m = re.search(r"T2V_TK_CHECK='([^']*)'", f.read())
    assert m, f"{name} に T2V_TK_CHECK が無い"
    return m.group(1)


def test_tk_check_is_the_same_in_all_mac_scripts():
    """setup.command・起動.command・install.sh の確かめ方は一字一句同じ。ずれると、
    片方で作った venv をもう片方が弾く（作り直しが止まらなくなる）。"""
    checks = {name: _tk_check(name) for name in _SCRIPTS}
    assert len(set(checks.values())) == 1, checks
    for name in _SCRIPTS:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            text = f.read()
        # 古い1行の確かめ方（手当て無し）が残っていない
        assert "-c 'import sys, tkinter" not in text, name
        assert '-c "$T2V_TK_CHECK"' in text, name


def test_tk_check_does_the_same_fix_as_main_py():
    """確かめ方は、main.py の _fix_tcl_library と同じ所（元の Python の lib/tcl8.*・tk8.*）を
    同じ目印（init.tcl・tk.tcl）で探す。アプリ本体が動く venv を、確かめ方だけが弾かないように。"""
    check = _tk_check("setup.command")
    with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as f:
        main = f.read()
    for piece in ('"TCL_LIBRARY", "tcl8.*", "init.tcl"', '"TK_LIBRARY", "tk8.*", "tk.tcl"',
                  "darwin", "sys.base_prefix"):
        assert piece in check, piece
        assert piece in main, piece
    # 手当てするのは venv の中だけ（main.py は「同じなら何もしない」、確かめ方は「違うときだけする」）
    assert "sys.prefix == sys.base_prefix" in main
    assert "sys.prefix != sys.base_prefix" in check


def _exec_check(monkeypatch, base, prefix, platform="darwin", tk_version=8.6):
    """確かめ方を、元の Python の置き場所（sys.base_prefix）と tkinter を差し替えて動かす。
    Tcl() が呼ばれた時点の TCL_LIBRARY / TK_LIBRARY を返す。"""
    seen = {}

    class _Tcl:
        def eval(self, _cmd):
            seen["TCL_LIBRARY"] = os.environ.get("TCL_LIBRARY")
            seen["TK_LIBRARY"] = os.environ.get("TK_LIBRARY")
            return "8.6.15"

    fake_tk = types.ModuleType("tkinter")
    fake_tk.Tcl = _Tcl
    fake_tk.TkVersion = tk_version
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "base_prefix", str(base))
    saved = {k: os.environ.pop(k, None) for k in ("TCL_LIBRARY", "TK_LIBRARY")}
    try:
        with pytest.raises(SystemExit) as e:
            exec(compile(_tk_check("起動.command"), "T2V_TK_CHECK", "exec"), {"__name__": "__main__"})
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
    return e.value.code, seen


def _pbs_like_base(tmp_path):
    """自分用 Python（python-build-standalone）のように、Tcl/Tk の部品が lib/tcl8.6・lib/tk8.6 にある置き場所。"""
    base = tmp_path / "py312"
    (base / "lib" / "tcl8.6").mkdir(parents=True)
    (base / "lib" / "tcl8.6" / "init.tcl").write_text("", encoding="utf-8")
    (base / "lib" / "tk8.6").mkdir(parents=True)
    (base / "lib" / "tk8.6" / "tk.tcl").write_text("", encoding="utf-8")
    (base / "lib" / "tcl8").mkdir()   # 目印の無い紛らわしいフォルダは選ばない
    return base


def test_tk_check_points_tcl_at_the_base_python_inside_a_venv(tmp_path, monkeypatch):
    """venv の中（prefix と base_prefix が違う）では、Tcl() の前に元の Python の
    lib/tcl8.6・lib/tk8.6 を教える。これが無いと自分用 Python の venv で init.tcl が見つからない。"""
    base = _pbs_like_base(tmp_path)
    code, seen = _exec_check(monkeypatch, base, tmp_path / "venv_mac")
    assert code == 0
    assert seen["TCL_LIBRARY"] == str(base / "lib" / "tcl8.6")
    assert seen["TK_LIBRARY"] == str(base / "lib" / "tk8.6")


def test_tk_check_leaves_tcl_alone_outside_a_venv(tmp_path, monkeypatch):
    """venv の外（元の Python そのもの）や Mac 以外では、何も足さない（ほかの Python は元から動く）。"""
    base = _pbs_like_base(tmp_path)
    code, seen = _exec_check(monkeypatch, base, base)
    assert code == 0 and seen == {"TCL_LIBRARY": None, "TK_LIBRARY": None}
    code, seen = _exec_check(monkeypatch, base, tmp_path / "venv_mac", platform="linux")
    assert code == 0 and seen == {"TCL_LIBRARY": None, "TK_LIBRARY": None}


def test_tk_check_rejects_old_tk(tmp_path, monkeypatch):
    """Tk 8.5（Apple の開発ツール付属の Python）は、venv の中でも外でも弾く。"""
    base = _pbs_like_base(tmp_path)
    assert _exec_check(monkeypatch, base, base, tk_version=8.5)[0] == 1
    assert _exec_check(monkeypatch, base, tmp_path / "venv_mac", tk_version=8.5)[0] == 1
    assert _exec_check(monkeypatch, base, base, tk_version=9.0)[0] == 0


def _real_tk_works():
    try:
        r = subprocess.run([sys.executable, "-c", "import tkinter; tkinter.Tcl()"],
                           capture_output=True, timeout=30)
    except Exception:
        return False
    return r.returncode == 0


@pytest.mark.skipif(not _real_tk_works(), reason="この Python では Tcl を動かせない（tkinter 無しの CI など）")
def test_tk_check_passes_on_this_python_and_its_venv(tmp_path):
    """本物の Python と、そこから作った本物の venv で、確かめ方がそのまま通る（Tk 8.6 以上の環境）。"""
    import tkinter
    if tkinter.TkVersion < 8.6:
        pytest.skip("Tk 8.6 より古い")
    check = _tk_check("起動.command")
    assert subprocess.run([sys.executable, "-c", check], timeout=60).returncode == 0
    venv = tmp_path / "venv_mac"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True, timeout=120)
    vpy = venv / ("Scripts" if os.name == "nt" else "bin") / "python"
    assert subprocess.run([str(vpy), "-c", check], timeout=60).returncode == 0
