# -*- coding: utf-8 -*-
"""pytest のテストを1件ずつ別プロセスで流す（macOS の CI 用）。

macOS の Tk は、1プロセスでルート窓を何十回も作っては壊すと落ちることがある。
実際のアプリは1起動で窓1つなので、GUIテストはそれに合わせて1件ずつ流す。
1件ごとに時間切れを設け、止まったテストは名前を出して失敗にする（無言で固まらない）。
    python tools/run_each.py tests/test_gui.py [--timeout 90]
"""
import subprocess
import sys


def main(argv):
    timeout = 90
    if "--timeout" in argv:
        i = argv.index("--timeout")
        timeout = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    target = argv[0]
    out = subprocess.run([sys.executable, "-m", "pytest", target, "--collect-only", "-q"],
                         capture_output=True, text=True)
    ids = [l.strip() for l in out.stdout.splitlines() if "::" in l]
    if not ids:
        print(out.stdout, out.stderr)
        return 1
    failed = []
    for n, t in enumerate(ids, 1):
        try:
            # 固まったら60秒で全スレッドの今いる行を書き出させる（どこで止まったかを残す）
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                "-o", f"faulthandler_timeout={max(10, timeout - 30)}", t],
                               capture_output=True, text=True, timeout=timeout)
            ok = r.returncode in (0, 5)
            if not ok:
                print(r.stdout[-4000:], r.stderr[-4000:])
        except subprocess.TimeoutExpired as e:
            ok = False
            print(f"TIMEOUT ({timeout}s): {t}")
            for part in (e.stdout, e.stderr):
                if isinstance(part, bytes):
                    part = part.decode("utf-8", "replace")
                print((part or "")[-8000:])
        print(f"[{n}/{len(ids)}] {'ok' if ok else 'FAILED'} {t}", flush=True)
        if not ok:
            failed.append(t)
    print(f"{len(ids) - len(failed)} passed, {len(failed)} failed (one process per test)")
    for t in failed:
        print("FAILED", t)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
