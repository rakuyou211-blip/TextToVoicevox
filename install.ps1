# TextToVoicevox かんたん導入（Windows）
#
# 使い方: PowerShell を開いて、次の1行を貼り付けて Enter。
#   irm https://raw.githubusercontent.com/rakuyou211-blip/TextToVoicevox/main/install.ps1 | iex
#
# なぜこの方法か:
#   ブラウザで落とした zip には「インターネットから来た」という印が付き、
#   そこから出した 起動.bat を開くと Windows が「PCが保護されました」で止める。
#   この1行は PowerShell 自身がファイルを取ってくるので印が付かず、
#   できたデスクトップのアイコンからは、警告なしで起動できる。
#
# やること（ぜんぶ自分のユーザーの中だけ。管理者権限は使わない）:
#   1. Python を探す（無ければ、許可をもらって winget で入れる）
#   2. 最新版を %LOCALAPPDATA%\TextToVoicevox に置く
#      （上書き更新。設定・辞書・本文の自動保存・立ち絵はそのまま残る）
#   3. 必要な部品を入れる（初回だけネット接続が必要。以降はオフラインで動く）
#   4. デスクトップとスタートメニューにアイコンを作って、起動する
#
# やめたいとき（アンインストール）:
#   %LOCALAPPDATA%\TextToVoicevox フォルダと、デスクトップ・スタートメニューの
#   「TextToVoicevox」アイコンを消すだけ。ほかには何も残さない。
#
# 上級者・テスト用の環境変数:
#   T2V_REF=ブランチ名   … 最新版の代わりに、そのブランチを入れる
#   T2V_SRC=フォルダ     … ネットから取らず、手元のフォルダを入れる（CI用）
#   T2V_DIR=フォルダ     … 入れる場所を変える
#   T2V_NO_LAUNCH=1      … 入れ終わっても起動しない
#   T2V_YES=1            … 確認を全部「はい」で進める
#
# ※ irm ... | iex で使う前提のファイル。& { } で包んであるので、途中で止まっても
#    開いている PowerShell の窓は閉じない。

& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # 進捗バーの描画でダウンロードが極端に遅くなるのを防ぐ
    try {
        # 古い Windows 10 の PowerShell 5.1 は既定で TLS1.2 を使わず、GitHub につながらない
        [Net.ServicePointManager]::SecurityProtocol =
            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    } catch {}

    $Repo = 'rakuyou211-blip/TextToVoicevox'
    $AppName = 'TextToVoicevox'
    $Dest = if ($env:T2V_DIR) { $env:T2V_DIR } else { Join-Path $env:LOCALAPPDATA $AppName }

    function Say($msg) { Write-Host $msg }
    function Step($msg) { Write-Host ''; Write-Host "== $msg" -ForegroundColor Cyan }

    function Test-Python($exe, [string[]]$pre) {
        # Python 3.9 以降で、画面表示の部品（tkinter）が入っているか。
        # Microsoft Store の「python」ダミーや、tkinter の無い Python は弾く
        $old = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $exe @pre -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>&1 | Out-Null
            return ($LASTEXITCODE -eq 0)
        } catch {
            return $false
        } finally {
            $ErrorActionPreference = $old
        }
    }

    function Find-Python {
        $cands = @(
            @{ exe = 'py'; pre = @('-3') },
            @{ exe = 'python'; pre = @() }
        )
        # winget / python.org で入れた直後は PATH が今の窓に反映されていないので、
        # よくある置き場所も直接見る（新しい版から）
        $dirs = @()
        foreach ($root in @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles", "${env:ProgramFiles(x86)}")) {
            if ($root -and (Test-Path -LiteralPath $root)) {
                $dirs += Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
                    Sort-Object { [int](($_.Name -replace '[^0-9]', '') + '0') } -Descending
            }
        }
        foreach ($d in $dirs) {
            $cands += @{ exe = (Join-Path $d.FullName 'python.exe'); pre = @() }
        }
        foreach ($launcher in @("$env:LOCALAPPDATA\Programs\Python\Launcher\py.exe", "$env:WINDIR\py.exe")) {
            $cands += @{ exe = $launcher; pre = @('-3') }
        }
        foreach ($c in $cands) {
            if ((Get-Command $c.exe -ErrorAction SilentlyContinue) -and (Test-Python $c.exe $c.pre)) {
                return $c
            }
        }
        return $null
    }

    function Ask($question) {
        if ($env:T2V_YES) { return $true }
        $a = Read-Host "$question [Y/n]"
        return (-not $a) -or ($a -match '^(y|yes|はい|ｙ)$')
    }

    function Get-SourceZipUrl {
        if ($env:T2V_REF) {
            return "https://github.com/$Repo/archive/refs/heads/$($env:T2V_REF).zip"
        }
        try {
            $rel = Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/$Repo/releases/latest"
            $asset = $rel.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1
            if ($asset) {
                Say "  最新版: $($rel.tag_name)"
                return $asset.browser_download_url
            }
        } catch {
            Say '  （最新版の情報を取れなかったので、main ブランチを使います）'
        }
        return "https://github.com/$Repo/archive/refs/heads/main.zip"
    }

    function Get-RunningApp {
        # この場所に入れた TextToVoicevox が起動中か（更新中に部品を入れ替えると失敗するため）
        try {
            @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf($Dest, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                    $_.CommandLine -match 'main\.py'
                })
        } catch {
            @()
        }
    }

    function Invoke-Native($what, $exe, [string[]]$argv) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & $exe @argv
            $code = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $old
        }
        if ($code -ne 0) { throw "$what に失敗しました（終了コード $code）。" }
    }

    try {
        Write-Host ''
        Write-Host "  TextToVoicevox をこのパソコンに入れます" -ForegroundColor Green
        Write-Host "  入れる場所: $Dest"

        # ---- 1. Python ----
        Step '1/4 Python を探しています'
        $py = Find-Python
        if (-not $py) {
            Say '  Python（3.9以降）が見つかりませんでした。'
            $winget = Get-Command winget -ErrorAction SilentlyContinue
            if ($winget -and (Ask '  Python 3.12 を自動で入れますか？（無料・公式版。数分かかります）')) {
                Invoke-Native 'Python のインストール' 'winget' @(
                    'install', '-e', '--id', 'Python.Python.3.12', '--scope', 'user',
                    '--accept-package-agreements', '--accept-source-agreements')
                $py = Find-Python
            }
            if (-not $py) {
                Say ''
                Say '  [!] Python を入れてから、もう一度この1行を貼り付けてください。'
                Say '      https://www.python.org/downloads/ （3.9以降）'
                Say '      インストール画面の「Add python.exe to PATH」にチェックを入れてください。'
                try { Start-Process 'https://www.python.org/downloads/' } catch {}
                return
            }
        }
        Say "  使う Python: $($py.exe) $($py.pre -join ' ')"

        # ---- 2. 本体を置く ----
        Step '2/4 アプリ本体を置いています'
        if (@(Get-RunningApp).Count -gt 0) {
            # 開いたまま部品を入れ替えると失敗したり、書きかけの本文を失ったりする
            Say '  TextToVoicevox が起動中です。新しい版に入れ替えるので、アプリを閉じてください。'
            if (-not $env:T2V_YES) { [void](Read-Host '  閉じたら Enter を押してください') }
            if (@(Get-RunningApp).Count -gt 0) {
                Say ''
                Say '  [!] まだ起動中なので、入れ替えをやめました（何も変えていません）。'
                Say '      アプリを閉じてから、もう一度この1行を貼り付けてください。'
                return
            }
        }
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("t2v_install_" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        try {
            if ($env:T2V_SRC) {
                $src = $env:T2V_SRC
                Say "  手元のフォルダから: $src"
            } else {
                $url = Get-SourceZipUrl
                $zip = Join-Path $tmp 'app.zip'
                Say "  ダウンロード中… $url"
                Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
                Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $tmp 'x') -Force
                $main = Get-ChildItem -LiteralPath (Join-Path $tmp 'x') -Recurse -Filter 'main.py' |
                    Select-Object -First 1
                if (-not $main) { throw 'ダウンロードした中に main.py がありません。' }
                $src = $main.DirectoryName
            }
            New-Item -ItemType Directory -Path $Dest -Force | Out-Null
            # 上書きコピー（消しはしない）。設定・辞書・自動保存・立ち絵・venv は残る
            & robocopy $src $Dest /E /XD .git venv __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "ファイルのコピーに失敗しました（robocopy $LASTEXITCODE）。" }
        } finally {
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path -LiteralPath (Join-Path $Dest 'main.py'))) { throw '本体を置けませんでした。' }

        # ---- 3. 部品 ----
        Step '3/4 必要な部品を入れています（初回は数分かかります）'
        $venv = Join-Path $Dest 'venv'
        $vpy = Join-Path $venv 'Scripts\python.exe'
        $vpyw = Join-Path $venv 'Scripts\pythonw.exe'
        if (-not (Test-Python $vpy @())) {
            # 無い・壊れている・別のPCから持ってきた venv は作り直す
            if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
            Invoke-Native '仮想環境（venv）の作成' $py.exe ($py.pre + @('-m', 'venv', $venv))
        }
        Invoke-Native '部品のインストール' $vpy @(
            '-m', 'pip', 'install', '--disable-pip-version-check', '-q',
            '-r', (Join-Path $Dest 'requirements.txt'))
        # ドラッグ＆ドロップ部品は任意（ARM64 など入らない環境がある。入らなくても動く）
        try {
            Invoke-Native 'ドラッグ＆ドロップ部品' $vpy @(
                '-m', 'pip', 'install', '--disable-pip-version-check', '-q', 'tkinterdnd2>=0.3,<1')
        } catch {
            Say '  （ドラッグ＆ドロップ部品は入りませんでした。ファイルは「ファイル追加」から使えます）'
        }

        # ---- 4. アイコン ----
        Step '4/4 デスクトップとスタートメニューにアイコンを作っています'
        $png = Join-Path $Dest 'assets\app-icon.png'
        $ico = Join-Path $Dest 'assets\app-icon.ico'
        try {
            Invoke-Native 'アイコンの変換' $vpy @('-c',
                "import sys; from PIL import Image; Image.open(sys.argv[1]).save(sys.argv[2], sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])",
                $png, $ico)
        } catch {
            $ico = $null   # アイコンが作れなくても起動はできる
        }
        $made = @()
        try {
            $shell = New-Object -ComObject WScript.Shell
            foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
                if (-not $folder -or -not (Test-Path -LiteralPath $folder)) { continue }
                try {
                    $lnkPath = Join-Path $folder "$AppName.lnk"
                    $lnk = $shell.CreateShortcut($lnkPath)
                    $lnk.TargetPath = $vpyw
                    $lnk.Arguments = '"' + (Join-Path $Dest 'main.py') + '"'
                    $lnk.WorkingDirectory = $Dest
                    $lnk.Description = 'PDF・画像・画面の文字を VOICEVOX で読み上げ'
                    if ($ico -and (Test-Path -LiteralPath $ico)) { $lnk.IconLocation = "$ico,0" }
                    $lnk.Save()
                    $made += $lnkPath
                } catch {
                    # 会社のPCなどで、デスクトップへの書き込みが制限されていることがある
                }
            }
        } catch {
            # アイコンの仕組み（COM）自体が使えない制限つきの環境。本体は入っているので続ける
        }
        foreach ($m in $made) { Say "  作成: $m" }
        if ($made.Count -eq 0) {
            Say '  （このPCの設定でアイコンを作れませんでした。アプリは入っています）'
            Say "  起動するときは、次のフォルダの「起動.bat」をダブルクリックしてください:"
            Say "    $Dest"
            try { Start-Process explorer.exe -ArgumentList ('"' + $Dest + '"') } catch {}
        }

        # ---- VOICEVOX（声を作る無料ソフト）が入っているか ----
        # アプリと同じ探し方（core.find_voicevox）で確かめる
        $vvPath = ''
        $old = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $vvPath = [string](& $vpy -c "import sys; sys.path.insert(0, sys.argv[1]); import core; print(core.find_voicevox() or '')" $Dest 2>$null |
                Select-Object -Last 1)
        } catch {
            $vvPath = ''
        } finally {
            $ErrorActionPreference = $old
        }

        Write-Host ''
        Write-Host '  できました！' -ForegroundColor Green
        Say '  これからは、デスクトップ（またはスタートメニュー）の「TextToVoicevox」から起動できます。'
        Say '  新しい版にしたいときも、同じ1行をもう一度貼り付けるだけです（設定や辞書は残ります）。'
        if ($vvPath.Trim()) {
            Say "  VOICEVOX: 見つかりました（$($vvPath.Trim())）。アプリの「VOICEVOX起動」からつなげます。"
        } else {
            Write-Host ''
            Write-Host '  ※ VOICEVOX（読み上げの声を作る無料ソフト）が、このPCに見つかりませんでした。' -ForegroundColor Yellow
            Say '     文字を取り出すことはできますが、声にするには VOICEVOX が必要です。'
            Say '     https://voicevox.hiroshiba.jp/ から入れてください（無料）。'
            if (-not $env:T2V_NO_LAUNCH -and (Ask '  VOICEVOX のダウンロードページを開きますか？')) {
                try { Start-Process 'https://voicevox.hiroshiba.jp/' } catch {}
            }
        }

        if (-not $env:T2V_NO_LAUNCH) {
            Start-Process -FilePath $vpyw -ArgumentList ('"' + (Join-Path $Dest 'main.py') + '"') -WorkingDirectory $Dest
        }
    } catch {
        Write-Host ''
        Write-Host "  [!] うまくいきませんでした: $($_.Exception.Message)" -ForegroundColor Yellow
        Say '      ネット接続を確かめて、もう一度この1行を貼り付けてみてください。'
        Say '      直らないときは、この画面の文字をそのまま作者に送ってください:'
        Say "      https://github.com/$Repo/issues"
        if ($env:T2V_NO_LAUNCH) { throw }   # CI では失敗として止める
    }
}
