# TextToVoicevox のアンインストール（Windows。1行導入 install.ps1 で入れたもの）
#
# 消し方（どれもこの台本が動く）:
#   - Windows の「設定 → アプリ → インストールされているアプリ」で TextToVoicevox の「…」→
#     「アンインストール」（v1.26.3 から、1行導入がここに載せる）
#   - PowerShell に次の1行を貼り付けて Enter:
#       irm https://raw.githubusercontent.com/rakuyou211-blip/TextToVoicevox/main/uninstall.ps1 | iex
#
# 消すもの: 入れたフォルダ（ふつうは %LOCALAPPDATA%\TextToVoicevox。設定と本文の自動保存も
#   この中）、そこを指すデスクトップとスタートメニューの「TextToVoicevox」アイコン、
#   「設定 → アプリ」の登録。
# 消さないもの: VOICEVOX（別のソフト。読み方辞書も VOICEVOX の側にある）、作った音声ファイル、
#   zip から入れた TextToVoicevox（それは展開したフォルダを消すだけ）。
# 消すのは、TextToVoicevox が入っているフォルダ（main.py・core.py・winlaunch.py がある）だけ。
#
# 環境変数:
#   T2V_DIR=フォルダ … 入れた場所（install.ps1 と同じ。無ければ「設定 → アプリ」の登録の場所、
#                      それも無ければ %LOCALAPPDATA%\TextToVoicevox）
#   T2V_YES=1        … 確認を全部「はい」で進める（CI用）
#
# ※ irm ... | iex で使うので BOM を付けない（付けると最初の行が壊れる）。「設定 → アプリ」からは、
#    UTF-8 として読んでから動かす（-File で開くと、日本語の Windows では文字が化ける）。
#    & { } で包んであるので、途中で止まっても開いている PowerShell の窓は閉じない。

& {
    $ErrorActionPreference = 'Stop'
    $Repo = 'rakuyou211-blip/TextToVoicevox'
    $AppName = 'TextToVoicevox'
    $Key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
    # 「設定 → アプリ」から開いた窓は、終わるとすぐ消えて読めないので、最後に Enter を待つ
    $FromSettings = [bool]$env:T2V_UNINSTALL_FROM

    function Say($msg) { Write-Host $msg }

    function Ask($question) {
        # 消すのは取り返しがつかないので、Enter だけなら「いいえ」
        if ($env:T2V_YES) { return $true }
        $a = Read-Host "$question [y/N]"
        return ($a -match '^(y|yes|はい|ｙ)$')
    }

    function Wait-End {
        if ($FromSettings -and -not $env:T2V_YES) { [void](Read-Host '  Enter キーを押すと閉じます') }
    }

    function Get-Entry {
        try { return Get-ItemProperty -LiteralPath $Key -ErrorAction Stop } catch { return $null }
    }

    function Get-Full($path) {
        return ([IO.Path]::GetFullPath([string]$path)).TrimEnd('\')
    }

    function Test-Inside($path, $dir) {
        # path が dir そのものか、その中か
        if (-not $path) { return $false }
        try { $p = Get-Full $path } catch { return $false }
        $d = Get-Full $dir
        return ($p.Equals($d, [StringComparison]::OrdinalIgnoreCase) -or
                $p.StartsWith($d + '\', [StringComparison]::OrdinalIgnoreCase))
    }

    function Test-AppFolder($dir) {
        # TextToVoicevox を入れたフォルダか（別の物を消さないよう、中身で見分ける）
        foreach ($need in @('main.py', 'core.py', 'winlaunch.py')) {
            if (-not (Test-Path -LiteralPath (Join-Path $dir $need) -PathType Leaf)) { return $false }
        }
        return $true
    }

    function Get-RunningApp($dir) {
        # この場所の TextToVoicevox が起動中か（開いたまま消すと、書きかけの本文を失う）
        try {
            @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                    $_.CommandLine -match 'main\.py'
                })
        } catch {
            @()
        }
    }

    function Stop-Reader($dir) {
        # 画像の文字を読む係（ocr_helper.py）は、アプリを閉じたあとも少し残ることがある
        try {
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                    $_.CommandLine -match 'ocr_helper\.py'
                } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        } catch {}
    }

    try {
        $entry = Get-Entry
        if ($env:T2V_DIR) {
            $Dest = $env:T2V_DIR.TrimEnd('\')
        } elseif ($entry -and $entry.InstallLocation) {
            $Dest = ([string]$entry.InstallLocation).TrimEnd('\')
        } else {
            $Dest = Join-Path $env:LOCALAPPDATA $AppName
        }
        Write-Host ''
        Write-Host '  TextToVoicevox をこのパソコンから消します' -ForegroundColor Green
        Say "  入れた場所: $Dest"

        $hasFolder = Test-Path -LiteralPath $Dest
        if ($hasFolder -and -not (Test-AppFolder $Dest)) {
            throw "このフォルダには TextToVoicevox が入っていないようなので、何も消しませんでした: $Dest"
        }
        # 「設定 → アプリ」の登録は、この場所を入れたときの物だけ消す
        $entryMine = [bool]($entry -and $entry.InstallLocation -and
                            (Test-Inside $entry.InstallLocation $Dest) -and (Test-Inside $Dest $entry.InstallLocation))
        # アイコンは、この場所のアプリを開く物だけ消す（別の場所に入れた TextToVoicevox の物は残す）
        $links = @()
        $shell = $null
        try { $shell = New-Object -ComObject WScript.Shell } catch {}
        foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
            if (-not $folder) { continue }
            $lnkPath = Join-Path $folder "$AppName.lnk"
            if (-not (Test-Path -LiteralPath $lnkPath)) { continue }
            $mine = $false
            if ($shell) {
                try {
                    $lnk = $shell.CreateShortcut($lnkPath)
                    $mine = (Test-Inside $lnk.TargetPath $Dest) -or (Test-Inside $lnk.WorkingDirectory $Dest)
                } catch {}
            }
            if ($mine) {
                $links += $lnkPath
            } else {
                Say "  （$lnkPath は別の場所の TextToVoicevox を開くアイコンなので、残します）"
            }
        }
        if (-not $hasFolder -and -not $entryMine -and $links.Count -eq 0) {
            Say '  ここには TextToVoicevox が入っていません（消す物はありませんでした）。'
            Wait-End
            return
        }
        if (-not $hasFolder) {
            Say '  （このフォルダはもうありません。残っているアイコンと「設定 → アプリ」の登録を片付けます）'
        }

        Say ''
        Say '  消すもの:'
        if ($hasFolder) { Say "    ・$Dest（アプリ本体。設定と本文の自動保存もこの中）" }
        foreach ($l in $links) { Say "    ・$l（アイコン）" }
        if ($entryMine) { Say '    ・Windows の「設定 → アプリ」の TextToVoicevox' }
        Say '  消さないもの: VOICEVOX（別のソフトです。読み方辞書も VOICEVOX の側に残ります）・作った音声ファイル'
        if (-not (Ask '  消しますか？')) {
            Say '  やめました（何も消していません）。'
            Wait-End
            return
        }

        if ($hasFolder -and @(Get-RunningApp $Dest).Count -gt 0) {
            Say '  TextToVoicevox が起動中です。アプリを閉じてください。'
            if (-not $env:T2V_YES) { [void](Read-Host '  閉じたら Enter を押してください') }
            if (@(Get-RunningApp $Dest).Count -gt 0) {
                throw 'まだ起動中なので、やめました（何も消していません）。アプリを閉じてから、もう一度試してください。'
            }
        }
        Stop-Reader $Dest

        foreach ($l in $links) {
            Remove-Item -LiteralPath $l -Force
            Say "  消しました: $l"
        }
        if ($entryMine) {
            Remove-Item -LiteralPath $Key -Recurse -Force
            Say '  消しました: 「設定 → アプリ」の TextToVoicevox'
        }

        if ($hasFolder) {
            # 消すフォルダの中にいると消せないので、外へ出る
            $tmp = [IO.Path]::GetTempPath()
            Set-Location -LiteralPath $tmp
            [Environment]::CurrentDirectory = $tmp
            for ($i = 0; $i -lt 3 -and (Test-Path -LiteralPath $Dest); $i++) {
                if ($i) { Start-Sleep -Seconds 2 }
                Remove-Item -LiteralPath $Dest -Recurse -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path -LiteralPath $Dest) {
                Say ''
                Say '  [!] 一部のファイルを消せませんでした（どこかで使っているのかもしれません）。'
                Say '      パソコンを再起動してから、次のフォルダを消してください:'
                Say "      $Dest"
                try { Start-Process explorer.exe -ArgumentList ('"' + (Split-Path $Dest -Parent) + '"') } catch {}
                if ($env:T2V_YES) { throw "消せなかったファイルがあります: $Dest" }
                Wait-End
                return
            }
            Say "  消しました: $Dest"
        }

        Write-Host ''
        Write-Host '  消し終わりました。使ってくれてありがとう！' -ForegroundColor Green
        Say '  VOICEVOX は別のソフトなので残っています。要らなければ、「設定 → アプリ」から消せます。'
        Say '  また使うときは、入れたときと同じ1行で入れ直せます。'
        Wait-End
    } catch {
        Write-Host ''
        Write-Host "  [!] $($_.Exception.Message)" -ForegroundColor Yellow
        Say '      直らないときは、この画面の文字をそのまま作者に送ってください:'
        Say "      https://github.com/$Repo/issues"
        Wait-End
        if ($env:T2V_YES) { throw }   # CI では失敗として止める
    }
}
