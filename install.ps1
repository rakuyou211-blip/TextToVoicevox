# TextToVoicevox かんたん導入（Windows）
#
# 使い方: PowerShell を開いて、次の1行を貼り付けて Enter。
#   irm https://raw.githubusercontent.com/rakuyou211-blip/TextToVoicevox/main/install.ps1 | iex
#
# なぜこの方法か:
#   ブラウザで落とした zip には「インターネットから来た」という印が付き、
#   そこから出したファイルを開くと Windows が「PCが保護されました」（SmartScreen）で止める。
#   さらに「スマート アプリ コントロール」が有効な機械では、通すボタンの無い窓で完全に
#   止まる（v1.23 までの zip に入っていた .bat は、印が付いていると拡張子だけで弾かれた）。
#   この1行は PowerShell 自身がファイルを取ってくるので印が付かず、
#   できたデスクトップのアイコンからは、警告なしで起動できる。
#
# やること（ぜんぶ自分のユーザーの中だけ。管理者権限は使わない）:
#   1. 最新版の「Python 入りの版」（TextToVoicevox_OneLine_Win64.zip。約200MB）を取ってくる。
#      Python・アプリの部品・画像の文字を読む部品が全部入っているので、
#      このパソコンに Python は要らない（ネット接続が要るのはこのときだけ）
#   2. %LOCALAPPDATA%\TextToVoicevox に置く
#      （上書き更新。設定・辞書・本文の自動保存・立ち絵はそのまま残る）
#   3. デスクトップとスタートメニューにアイコンを作り、Windows の「設定 → アプリ」に載せて、起動する
#
#   Python 入りの版が無いとき（その版の Release に無い・T2V_REF / T2V_SRC を使うとき）は、
#   前からの入れ方になる: Python を探し（無ければ、許可をもらって winget で入れる）、
#   本体を置き、必要な部品を入れる（初回だけネット接続が必要。以降はオフラインで動く）。
#
# Python 入りの版を、ブラウザで落とす zip にしない理由:
#   ブラウザで落とした zip を Explorer で展開すると、Windows の Defender が python.exe と
#   python3xx.dll の組で固まり、約4分たって諦めるまでアプリを開けない（2026-11-06 実測）。
#   この1行なら印が付かないので、展開は数秒で終わる。
#
# やめたいとき（アンインストール）:
#   Windows の「設定 → アプリ」で TextToVoicevox を「アンインストール」するか、次の1行を貼る
#   （どちらも uninstall.ps1 が動く）:
#     irm https://raw.githubusercontent.com/rakuyou211-blip/TextToVoicevox/main/uninstall.ps1 | iex
#   消すのは %LOCALAPPDATA%\TextToVoicevox フォルダと、デスクトップ・スタートメニューの
#   「TextToVoicevox」アイコンと、「設定 → アプリ」の登録だけ。ほかには何も残さない。
#
# 上級者・テスト用の環境変数:
#   T2V_REF=ブランチ名   … 最新版の代わりに、そのブランチを入れる（前からの入れ方）
#   T2V_SRC=フォルダ     … ネットから取らず、手元のフォルダを入れる（CI用。前からの入れ方）
#   T2V_BUNDLE=zip       … ネットから取らず、手元の Python 入りの版を入れる（CI用）
#   T2V_NO_BUNDLE=1      … Python 入りの版を使わず、前からの入れ方にする
#   T2V_DIR=フォルダ     … 入れる場所を変える
#   T2V_NO_LAUNCH=1      … 入れ終わっても起動しない
#   T2V_YES=1            … 確認を全部「はい」で進める
#   T2V_FROM_APP=1       … アプリの「更新する」から動いた（アプリが閉じるのを待つ・失敗したら窓を残す）
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
    # Python 入りの版（tools/make_bundle.py が作り、Release に添付する）
    $BundleName = 'TextToVoicevox_OneLine_Win64.zip'
    $Dest = if ($env:T2V_DIR) { $env:T2V_DIR.TrimEnd('\') } else { Join-Path $env:LOCALAPPDATA $AppName }
    # 入れ終えたら、アプリを動かす Python をここに入れる（関数の戻り値は表示と混ざるので使わない）
    $App = @{ py = $null; pyw = $null }

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

    function Get-LatestRelease {
        # 最新版の情報（取れなければ $null）
        try {
            return Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/$Repo/releases/latest"
        } catch {
            return $null
        }
    }

    function Get-BundleUrl($rel) {
        # 最新版に Python 入りの版が添付されていれば、その URL
        if (-not $rel -or $env:T2V_NO_BUNDLE) { return $null }
        $asset = @($rel.assets | Where-Object { $_.name -eq $BundleName }) | Select-Object -First 1
        if (-not $asset) { return $null }
        Say "  最新版: $($rel.tag_name)"
        return $asset.browser_download_url
    }

    function Get-SourceZipUrl($rel) {
        if ($env:T2V_REF) {
            return "https://github.com/$Repo/archive/refs/heads/$($env:T2V_REF).zip"
        }
        if ($rel) {
            # Windows 用の zip（TextToVoicevox_Windows.zip）を選ぶ。分ける前の版は zip が1つだけ。
            # Python 入りの版は、ここでは使わない
            $zips = @($rel.assets | Where-Object { $_.name -like '*.zip' -and $_.name -ne $BundleName })
            $asset = $zips | Where-Object { $_.name -like '*Windows*' } | Select-Object -First 1
            if (-not $asset) {
                $asset = $zips | Where-Object { $_.name -notlike '*Mac*' } | Select-Object -First 1
            }
            if ($asset) {
                Say "  最新版: $($rel.tag_name)"
                return $asset.browser_download_url
            }
        } else {
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

    function Wait-AppClosed {
        # 開いたまま部品を入れ替えると失敗したり、書きかけの本文を失ったりする。
        # アプリの「更新する」から来たときは、アプリが自分で閉じるのを少し待つ
        if ($env:T2V_FROM_APP) {
            for ($i = 0; $i -lt 40 -and @(Get-RunningApp).Count -gt 0; $i++) { Start-Sleep -Milliseconds 500 }
        }
        if (@(Get-RunningApp).Count -eq 0) { return $true }
        Say '  TextToVoicevox が起動中です。新しい版に入れ替えるので、アプリを閉じてください。'
        if (-not $env:T2V_YES) { [void](Read-Host '  閉じたら Enter を押してください') }
        if (@(Get-RunningApp).Count -eq 0) { return $true }
        Say ''
        Say '  [!] まだ起動中なので、入れ替えをやめました（何も変えていません）。'
        Say '      アプリを閉じてから、もう一度この1行を貼り付けてください。'
        return $false
    }

    function Stop-Reader {
        # 画像の文字を読む係（ocr_helper.py）は、アプリを閉じたあとも少し残ることがある。
        # 残っていると部品を入れ替えられないので止める（次に読むとき、アプリがまた起こす）
        try {
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
                Where-Object {
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf($Dest, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                    $_.CommandLine -match 'ocr_helper\.py'
                } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        } catch {}
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

    function Remove-Legacy($src) {
        # 上書きコピーは消さないので、v1.23 までの .bat が残る。今は使わない上に、
        # 「起動.pyw」と並んで「起動」が2つに見えるので消す。
        # 置いた中身が新しい形（winsetup.py がある）のときだけ。古い版を入れたときに
        # その版が頼りにしている .bat まで消さないように
        if (Test-Path -LiteralPath (Join-Path $src 'winsetup.py')) {
            foreach ($old in @('起動.bat', 'setup.bat', 'デバッグ起動.bat', '英語OCRを入れる.bat', '英語OCRを入れる.pyw', 'requirements-english-ocr.txt')) {
                Remove-Item -LiteralPath (Join-Path $Dest $old) -Force -ErrorAction SilentlyContinue
            }
        }
    }

    function Install-Bundle($bundle) {
        # Python 入りの版を入れる。済んだら $App に、アプリを動かす Python を入れる
        Step '1/3 アプリを取ってきています（Python も入った版。約200MB）'
        if (-not (Wait-AppClosed)) { return }
        # 入れる場所の隣で広げる（同じドライブなので、入れ替えは名前を変えるだけで済む。
        # 道のりも短く保てる。Windows は 260 文字を超える場所を扱えないことがある）
        $stage = "$Dest.new"
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
        try {
            if (Test-Path -LiteralPath $bundle) {
                $zip = $bundle
                Say "  手元の zip から: $zip"
            } else {
                $zip = Join-Path $stage 'app.zip'
                Say "  ダウンロード中… $bundle"
                Invoke-WebRequest -UseBasicParsing -Uri $bundle -OutFile $zip
            }
            Say '  広げています…'
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $stage)
            $src = Join-Path $stage $AppName
            foreach ($need in @('main.py', 'runtime\pythonw.exe')) {
                if (-not (Test-Path -LiteralPath (Join-Path $src $need))) { throw "ダウンロードした中に $need がありません。" }
            }

            Step '2/3 アプリを置いています'
            New-Item -ItemType Directory -Path $Dest -Force | Out-Null
            Stop-Reader
            # Python と、画像の文字を読む部品は、フォルダごと入れ替える（前の版の物を残さない）
            foreach ($part in @('runtime', 'ocr_engine')) {
                $new = Join-Path $src $part
                if (-not (Test-Path -LiteralPath $new)) { continue }
                $cur = Join-Path $Dest $part
                $old = Join-Path $stage "$part.old"
                if (Test-Path -LiteralPath $cur) { Move-Item -LiteralPath $cur -Destination $old }
                try {
                    Move-Item -LiteralPath $new -Destination $cur
                } catch {
                    if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $cur }
                    throw
                }
            }
            # 本体は上書きコピー（消しはしない）。設定・辞書・自動保存・立ち絵は残る
            & robocopy $src $Dest /E /XD runtime ocr_engine /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "ファイルのコピーに失敗しました（robocopy $LASTEXITCODE）。" }
            Remove-Legacy $src
        } finally {
            Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
        }
        $py = Join-Path $Dest 'runtime\python.exe'
        if (-not (Test-Python $py @())) { throw 'アプリを動かす Python が動きませんでした。' }
        # 前の入れ方の部品（venv）は使わなくなったので片付ける（数百MB）
        $venv = Join-Path $Dest 'venv'
        if (Test-Path -LiteralPath (Join-Path $venv 'pyvenv.cfg')) {
            Say '  前の入れ方で入れた部品（venv フォルダ）を片付けています…'
            Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction SilentlyContinue
        }
        # Windows の圧縮機能で小さくする（中身はそのまま。約600MB → 約250MB）。できなくても動く
        Say '  小さくしています…（1分ほど）'
        foreach ($part in @('runtime', 'ocr_engine')) {
            $p = Join-Path $Dest $part
            if (Test-Path -LiteralPath $p) {
                try { & compact.exe /c "/s:$p" /i /q /exe:lzx | Out-Null } catch {}
            }
        }
        $App.py = $py
        $App.pyw = Join-Path $Dest 'runtime\pythonw.exe'
    }

    function Install-WithPython($rel) {
        # 前からの入れ方（このパソコンの Python で venv を作り、部品を入れる）。
        # 済んだら $App に、アプリを動かす Python を入れる

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
        if (-not (Wait-AppClosed)) { return }
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ("t2v_install_" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        $topFiles = @()
        try {
            if ($env:T2V_SRC) {
                $src = $env:T2V_SRC
                Say "  手元のフォルダから: $src"
            } else {
                $url = Get-SourceZipUrl $rel
                $zip = Join-Path $tmp 'app.zip'
                Say "  ダウンロード中… $url"
                Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
                Expand-Archive -LiteralPath $zip -DestinationPath (Join-Path $tmp 'x') -Force
                $main = Get-ChildItem -LiteralPath (Join-Path $tmp 'x') -Recurse -Filter 'main.py' |
                    Select-Object -First 1
                if (-not $main) { throw 'ダウンロードした中に main.py がありません。' }
                $src = $main.DirectoryName
                # 新しい zip は、プログラムや部品を app フォルダに入れ、いちばん上には 起動.pyw と
                # 説明だけを置く（開いたときに見える物を減らすため）。ここでは今までどおり1つの
                # フォルダに並べるので、いちばん上の物も一緒に置く
                $up = Split-Path $src -Parent
                if ((Split-Path $src -Leaf) -eq 'app' -and (Test-Path -LiteralPath (Join-Path $up '起動.pyw'))) {
                    $topFiles = @(Get-ChildItem -LiteralPath $up -File)
                }
            }
            New-Item -ItemType Directory -Path $Dest -Force | Out-Null
            # 上書きコピー（消しはしない）。設定・辞書・自動保存・立ち絵・venv は残る
            & robocopy $src $Dest /E /XD .git venv runtime __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { throw "ファイルのコピーに失敗しました（robocopy $LASTEXITCODE）。" }
            foreach ($f in $topFiles) { Copy-Item -LiteralPath $f.FullName -Destination $Dest -Force }
            Remove-Legacy $src
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
        # 部品（ふりがなの辞書 Janome の約250MB を含めて約300MB）を、Windows の圧縮機能で
        # 小さくする（約90MB）。winsetup.py の shrink_venv と同じ。中身はそのまま・読み込みの
        # 速さも同じ。圧縮できない場所でも動作には影響しないので、できなくても止めない
        try {
            $parts = Join-Path $venv 'Lib\site-packages'
            if (Test-Path -LiteralPath $parts) { & compact.exe /c "/s:$parts" /i /q /exe:lzx | Out-Null }
        } catch {}

        # 画像の文字を読む部品（約122MB）も一緒に入れる。winsetup.py の ensure_engine と同じで、
        # 入っていれば何もしない（更新は遅くならない）・入らなくても止めない（Windows の読み取りで動く）
        if (Test-Path -LiteralPath (Join-Path $Dest 'ocr_helper.py')) {
            try {
                Invoke-Native '画像の文字を読む部品' $vpy @((Join-Path $Dest 'winsetup.py'), 'engine')
            } catch {
                Say '  （画像の文字を読む部品は入りませんでした。Windows の読み取りで読みます）'
            }
        }

        # 部品を入れ終えた印（winlaunch.py と同じ: requirements.txt の SHA-256・小文字・BOM 無し）。
        # これが無いと、入れたフォルダの 起動.pyw を開いたときにセットアップをやり直す
        try {
            $stamp = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $Dest 'requirements.txt')).Hash.ToLowerInvariant()
            [IO.File]::WriteAllText((Join-Path $venv '.t2v_setup'), $stamp)
        } catch {}
        # Python 入りの版から、こちらの入れ方に戻したとき: その版の Python は使わなくなるので片付ける
        # （残っていると、起動.pyw がそちらを使ってしまう）
        $rt = Join-Path $Dest 'runtime'
        if (Test-Path -LiteralPath (Join-Path $rt 'pythonw.exe')) {
            Remove-Item -LiteralPath $rt -Recurse -Force -ErrorAction SilentlyContinue
        }
        $App.py = $vpy
        $App.pyw = $vpyw
    }

    function Register-Uninstall($ico) {
        # Windows の「設定 → アプリ」に載せる（そこからアンインストールできるように）。自分の
        # ユーザーの中（HKCU）だけなので、管理者権限は要らない。消すのは uninstall.ps1 なので、
        # それが無い版（v1.26.2 まで。T2V_REF で古いブランチを入れたときなど）では載せない
        $script = Join-Path $Dest 'uninstall.ps1'
        if (-not (Test-Path -LiteralPath $script)) { return $false }
        $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$AppName"
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        # uninstall.ps1 は irm | iex 用に BOM を付けていない。-File で開くと日本語が化けるので、
        # UTF-8 として読んでから動かす。フォルダを手で消したあとでも登録を片付けられるよう、
        # 台本が無ければネットの同じ物を使う（T2V_UNINSTALL_FROM: 終わっても窓をすぐ閉じない）
        $lit = $script.Replace("'", "''")
        $dir = $Dest.Replace("'", "''")
        $url = "https://raw.githubusercontent.com/$Repo/main/uninstall.ps1"
        $cmd = "`"$ps`" -NoProfile -Command `"`$env:T2V_UNINSTALL_FROM='settings'; `$env:T2V_DIR='$dir'; " +
               "`$f='$lit'; `$s = if (Test-Path -LiteralPath `$f) { [IO.File]::ReadAllText(`$f, [Text.Encoding]::UTF8) } " +
               "else { irm '$url' }; & ([ScriptBlock]::Create(`$s))`""
        $version = ''
        try {
            $m = Select-String -LiteralPath (Join-Path $Dest 'core.py') -Pattern '^APP_VERSION\s*=\s*"([^"]+)"' |
                Select-Object -First 1
            if ($m) { $version = $m.Matches[0].Groups[1].Value }
        } catch {}
        $kb = 0
        try {
            $sum = (Get-ChildItem -LiteralPath $Dest -Recurse -File -Force -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum
            $kb = [int][Math]::Min([double]$sum / 1KB, [int]::MaxValue)
        } catch {}
        New-Item -Path $key -Force | Out-Null
        $text = [ordered]@{
            DisplayName = $AppName
            DisplayVersion = $version
            Publisher = 'RAKUYOU211'
            InstallLocation = $Dest
            InstallDate = (Get-Date -Format 'yyyyMMdd')
            UninstallString = $cmd
            URLInfoAbout = "https://github.com/$Repo"
            HelpLink = 'https://rakuyou211-blip.github.io/TextToVoicevox/manual/'
        }
        if ($ico -and (Test-Path -LiteralPath $ico)) { $text['DisplayIcon'] = $ico }
        foreach ($name in $text.Keys) {
            New-ItemProperty -LiteralPath $key -Name $name -Value $text[$name] -PropertyType String -Force | Out-Null
        }
        $num = [ordered]@{ NoModify = 1; NoRepair = 1 }
        if ($kb -gt 0) { $num['EstimatedSize'] = $kb }
        foreach ($name in $num.Keys) {
            New-ItemProperty -LiteralPath $key -Name $name -Value $num[$name] -PropertyType DWord -Force | Out-Null
        }
        return $true
    }

    try {
        Write-Host ''
        Write-Host "  TextToVoicevox をこのパソコンに入れます" -ForegroundColor Green
        Write-Host "  入れる場所: $Dest"

        $rel = $null
        if (-not $env:T2V_SRC -and -not $env:T2V_REF -and -not $env:T2V_BUNDLE) { $rel = Get-LatestRelease }
        $bundle = if ($env:T2V_BUNDLE) { $env:T2V_BUNDLE } else { Get-BundleUrl $rel }
        if ($bundle) {
            Install-Bundle $bundle
            $steps = 3
        } else {
            Install-WithPython $rel
            $steps = 4
        }
        if (-not $App.pyw) { return }   # 途中でやめた（理由は表示済み）
        $vpy = $App.py
        $vpyw = $App.pyw

        # ---- アイコン ----
        Step "$steps/$steps デスクトップとスタートメニューにアイコンを作っています"
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
            $folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
            foreach ($round in 1, 2) {
                foreach ($folder in $folders) {
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
                if ($made.Count -gt 0) { break }
                # どちらにも作れなかったときは、入れたフォルダの中に作る（そこから開ける）
                $folders = @($Dest)
            }
        } catch {
            # アイコンの仕組み（COM）自体が使えない制限つきの環境。本体は入っているので続ける
        }
        foreach ($m in $made) { Say "  作成: $m" }
        if ($made.Count -eq 0 -or $made[0].StartsWith($Dest, [StringComparison]::OrdinalIgnoreCase)) {
            Say '  （このPCの設定で、デスクトップとスタートメニューにはアイコンを作れませんでした。アプリは入っています）'
            if ($made.Count -gt 0) {
                Say "  起動するときは、次のフォルダの「$AppName」をダブルクリックしてください:"
            } else {
                Say '  起動するときは、次のフォルダの「起動.pyw」をダブルクリックしてください:'
            }
            Say "    $Dest"
            try { Start-Process explorer.exe -ArgumentList ('"' + $Dest + '"') } catch {}
        }
        $listed = $false
        try {
            $listed = Register-Uninstall $ico
        } catch {
            Say '  （Windows の「設定 → アプリ」には載せられませんでした。アプリは入っています）'
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
        Say '  新しい版のお知らせのため、開いたときに1日1回だけ GitHub に最新の版を聞きます（送るのはアプリの名前と版だけ。「接続設定…」で切れます）。'
        if ($listed) {
            Say '  使わなくなったら、Windows の「設定 → アプリ」から TextToVoicevox をアンインストールできます。'
        }
        if ($vvPath.Trim()) {
            Say "  VOICEVOX: 見つかりました（$($vvPath.Trim())）。アプリの「VOICEVOX起動」からつなげます。"
        } else {
            Write-Host ''
            Write-Host '  ※ VOICEVOX（読み上げの声を作る無料ソフト）が、このPCに見つかりませんでした。' -ForegroundColor Yellow
            Say '     文字を取り出すことはできますが、声にするには VOICEVOX が必要です。'
            Say '     https://voicevox.hiroshiba.jp/ から入れてください（無料）。'
            if (-not $env:T2V_NO_LAUNCH -and -not $env:T2V_FROM_APP -and (Ask '  VOICEVOX のダウンロードページを開きますか？')) {
                try { Start-Process 'https://voicevox.hiroshiba.jp/' } catch {}
            }
        }

        if (-not $env:T2V_NO_LAUNCH) {
            Start-Process -FilePath $vpyw -ArgumentList ('"' + (Join-Path $Dest 'main.py') + '"') -WorkingDirectory $Dest
        }
        # アプリの「更新する」から来たときは、終わりの一言を読めるくらいは窓を残して閉じる
        if ($env:T2V_FROM_APP) { Start-Sleep -Seconds 3 }
    } catch {
        Write-Host ''
        Write-Host "  [!] うまくいきませんでした: $($_.Exception.Message)" -ForegroundColor Yellow
        Say '      ネット接続を確かめて、もう一度この1行を貼り付けてみてください。'
        Say '      直らないときは、この画面の文字をそのまま作者に送ってください:'
        Say "      https://github.com/$Repo/issues"
        # アプリの「更新する」から来たときは、窓がすぐ消えて読めないので、閉じるのを待つ
        if ($env:T2V_FROM_APP) { [void](Read-Host '  Enter キーを押すと閉じます') }
        if ($env:T2V_NO_LAUNCH) { throw }   # CI では失敗として止める
    }
}
