# ocr_win.ps1 - Windows 標準OCR (Windows.Media.Ocr) を使ったオフラインOCRヘルパー
# 使い方: powershell -ExecutionPolicy Bypass -File ocr_win.ps1 -Manifest <list.txt> -Out <result.json> [-Lang ja]
#   Manifest: 1行1画像パス (UTF-8)
#   Out     : 結果JSON出力先 (UTF-8)  [{path, text, ok, error}, ...]
param(
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$Out,
    [string]$Lang = "ja"
)

$ErrorActionPreference = "Stop"
[System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# --- WinRT 非同期 (IAsyncOperation) を同期的に待つためのヘルパー ---
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and
    $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    [void]$netTask.Wait(-1)
    $netTask.Result
}

# --- 必要な WinRT 型をロード ---
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]

# --- OCRエンジン生成 (指定言語 -> 失敗時はユーザー既定言語) ---
$engine = $null
try {
    $langObj = New-Object Windows.Globalization.Language($Lang)
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($langObj)
} catch { $engine = $null }
if ($null -eq $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $engine) {
    $err = @{ fatal = "OCRエンジンを作成できません。Windowsの言語設定に日本語(OCR)が必要です。" }
    ($err | ConvertTo-Json) | Out-File -FilePath $Out -Encoding utf8
    exit 2
}

function Read-Image-Text([string]$path) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        # OCRが確実に受け付ける形式へ変換 (Bgra8 / Premultiplied)
        $fmt = [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8
        $alpha = [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
        $bmp2 = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bitmap, $fmt, $alpha)
        $result = Await ($engine.RecognizeAsync($bmp2)) ([Windows.Media.Ocr.OcrResult])
        # 行単位でテキスト取得 (行内の単語はスペース結合される)
        $lines = New-Object System.Collections.Generic.List[string]
        foreach ($line in $result.Lines) { $lines.Add($line.Text) }
        return ($lines -join "`n")
    } finally {
        if ($stream) { $stream.Dispose() }
    }
}

$results = New-Object System.Collections.Generic.List[object]
foreach ($line in [System.IO.File]::ReadAllLines($Manifest, [System.Text.Encoding]::UTF8)) {
    $p = $line.Trim()
    if ([string]::IsNullOrWhiteSpace($p)) { continue }
    $obj = [ordered]@{ path = $p; text = ""; ok = $false; error = "" }
    try {
        $obj.text = Read-Image-Text $p
        $obj.ok = $true
    } catch {
        $obj.error = $_.Exception.Message
    }
    $results.Add([pscustomobject]$obj)
}

# JSON出力 (UTF-8, BOMなし)
$json = $results | ConvertTo-Json -Depth 5 -Compress
[System.IO.File]::WriteAllText($Out, $json, (New-Object System.Text.UTF8Encoding($false)))
exit 0
