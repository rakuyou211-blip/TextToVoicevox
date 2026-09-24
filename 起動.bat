@echo off
rem === Text-to-VOICEVOX launcher (no console window) ===
chcp 65001 >nul
cd /d "%~dp0"

rem zip を開いた中身のまま この .bat だけを実行すると、Windows は .bat だけを一時
rem フォルダへ取り出して走らせるため、隣にあるはずのファイルが無い状態で進んでしまう。
rem そのまま進めても必ず失敗するので、ここで止めて案内する。
if not exist "%~dp0main.py" goto not_extracted
if not exist "%~dp0core.py" goto not_extracted

if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo 初回セットアップがまだのようなので、先に setup.bat を実行します。
    echo （初回だけネット接続が必要です。数分かかることがあります）
    echo.
    call "%~dp0setup.bat"
) else (
    rem venv があっても、フォルダごと別のPCから持ってきた場合や Python を
    rem 入れ直した場合は、中の Python が動かない（venv は機械ごとに作る物）。
    rem 実際に動くか一度だけ確かめて、ダメなら setup.bat で作り直す。
    "%~dp0venv\Scripts\python.exe" -c "" >nul 2>&1
    if errorlevel 1 (
        echo venv が今のパソコンでは動かないため、setup.bat で作り直します。
        echo.
        call "%~dp0setup.bat"
    )
)
if not exist "%~dp0venv\Scripts\pythonw.exe" (
    echo.
    echo [!] セットアップが完了していないため、起動できませんでした。
    echo     上に出ているメッセージを確認してください。
    pause
    exit /b 1
)

rem ダウンロード由来の警告ブロックを解除する（このフォルダ直下のみ）。
rem PowerShellは呼び出すだけで毎回1〜2秒かかるため、済んだら venv の中に印を残して
rem 二回目以降は丸ごと飛ばす（venvは配布物に入らない＝別PCでは初回やり直しになる）。
rem 印には版数を書く。上書きで新しい版を展開すると venv ごと古い印も残るので、
rem 版が変わったかどうかで「解除をやり直すか」を決める。
set "APPVER="
for /f "tokens=2 delims==" %%A in ('findstr /b /c:"APP_VERSION = " "%~dp0core.py"') do set APPVER=%%A
if not defined APPVER goto do_unblock
rem 取り出した値は引用符つきなので、空白と引用符を落として数字だけにする
set APPVER=%APPVER: =%
set APPVER=%APPVER:"=%
set "DONEVER="
if not exist "%~dp0venv\.unblocked" goto do_unblock
set /p DONEVER=<"%~dp0venv\.unblocked"
if "%DONEVER%"=="%APPVER%" goto unblock_done

:do_unblock
rem 解除できたかは Zone.Identifier が残っていないかで確かめる。PowerShell が
rem 止められている環境では黙って失敗するため、成否を見てから印を置く。
set "APPDIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $f = Get-ChildItem -LiteralPath $env:APPDIR -File -ErrorAction Stop; $f | Unblock-File -ErrorAction SilentlyContinue; if ($f | Get-Item -Stream Zone.Identifier -ErrorAction SilentlyContinue) { exit 1 }; exit 0 } catch { exit 1 }" >nul 2>&1
rem 異常終了（負の終了コード）は errorlevel 1 では拾えないので、0 かどうかで見る
if errorlevel 1 goto unblock_failed
if not "%ERRORLEVEL%"=="0" goto unblock_failed
if not defined APPVER goto unblock_done
> "%~dp0venv\.unblocked" echo %APPVER%
goto unblock_done

:unblock_failed
echo [!] ダウンロード時の警告（ブロック）は外せませんでした。毎回警告が出るときは、
echo     zip を右クリック→プロパティ→「ブロックの解除」→もう一度「すべて展開」でなおります。
echo.

:unblock_done
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
exit /b 0

:not_extracted
echo [!] 同じフォルダに main.py が見つかりません。
echo     zip を右クリック→「すべて展開」してから、出てきたフォルダの中の
echo     起動.bat を開いてください。
echo.
pause
exit /b 1
