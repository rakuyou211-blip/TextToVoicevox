# -*- coding: utf-8 -*-
"""コマンドを、見えない別のデスクトップで動かす（Windows 専用）。

使っている人の画面に窓を出さず、マウスやキーボードも奪わない。終わるまで待って、
終了コードを返す。説明書の画面撮り（capture.py）を、作業の邪魔をせずに動かすのに使う。
コマンドの出力はこの窓には出ないので、コマンドの側でファイルに書く。

    python tools/manual/hidden_desktop.py <作業フォルダ> <コマンド行>
"""
import ctypes
import sys
from ctypes import wintypes

NAME = "T2VManualShots"
_GENERIC_ALL = 0x10000000
_CREATE_NO_WINDOW = 0x08000000
_WAIT_TIMEOUT = 0x102


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def run(cwd, cmdline, timeout=900, name=NAME):
    """cmdline を見えないデスクトップで動かし、終了コードを返す。timeout 秒たっても
    終わらなければ止めて TimeoutError（撮影中に何かの窓が答えを待って止まったときなど）。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.restype = ctypes.c_void_p
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    desk = user32.CreateDesktopW(name, None, None, 0, _GENERIC_ALL, None)
    if not desk:
        raise OSError(f"CreateDesktop failed: {ctypes.get_last_error()}")
    try:
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(si)
        si.lpDesktop = name
        pi = _PROCESS_INFORMATION()
        buf = ctypes.create_unicode_buffer(cmdline)
        if not kernel32.CreateProcessW(None, buf, None, None, False, _CREATE_NO_WINDOW,
                                       None, cwd, ctypes.byref(si), ctypes.byref(pi)):
            raise OSError(f"CreateProcess failed: {ctypes.get_last_error()}")
        try:
            if kernel32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == _WAIT_TIMEOUT:
                kernel32.TerminateProcess(pi.hProcess, 1)
                raise TimeoutError(f"{timeout} 秒たっても終わらなかったので止めました: {cmdline}")
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
            return code.value
        finally:
            kernel32.CloseHandle(pi.hThread)
            kernel32.CloseHandle(pi.hProcess)
    finally:
        user32.CloseDesktop(desk)


if __name__ == "__main__":
    print("exit", run(sys.argv[1], sys.argv[2]))
