# ocr_win.ps1 - Windows 標準OCR (Windows.Media.Ocr) を使ったオフラインOCRヘルパー
# 使い方: powershell -ExecutionPolicy Bypass -File ocr_win.ps1 -Manifest <list.txt> -Out <result.json> [-Lang ja]
#   Manifest: 1行1画像パス (UTF-8)
#   Out     : 結果JSON出力先 (UTF-8)  [{path, text, ok, error, lines}, ...]
#             lines は行の外接矩形 [{text,x0,x1,y0,y1}] (画像サイズで正規化・原点は左上)。
#             Python側 (core._parse_windows_ocr_result) が折り返し連結・ラベル除去に使う。
#             文字が傾いて検出された画像 (TextAngle) では省略される。
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
[void][Windows.Graphics.Imaging.BitmapTransform, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
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
    # BOMなしUTF-8で書く (PS5.1の -Encoding utf8 はBOM付きになり、Python側の
    # json.load が失敗して本来の案内メッセージがユーザーに届かない)
    $err = @{ fatal = "OCRエンジンを作成できません。Windowsの言語設定に日本語(OCR)が必要です。" }
    [System.IO.File]::WriteAllText($Out, ($err | ConvertTo-Json),
        (New-Object System.Text.UTF8Encoding($false)))
    exit 2
}

function Read-Image-Text([string]$path) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $fmt = [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8
        $alpha = [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied
        # OCRエンジンの上限 (MaxImageDimension, 通常2600) を超える画像は RecognizeAsync が
        # 例外を投げ「無言で空振り」になるため、デコード時に縮小して収める
        # (Python側 preprocess_image も上限を適用するが、前処理OFF時への二重の保険)。
        $maxDim = [int][Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        $pw = [int]$decoder.PixelWidth
        $ph = [int]$decoder.PixelHeight
        if (($pw -gt $maxDim) -or ($ph -gt $maxDim)) {
            $scale = [double]$maxDim / [Math]::Max($pw, $ph)
            $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
            $transform.ScaledWidth = [uint32][Math]::Max(1, [Math]::Floor($pw * $scale))
            $transform.ScaledHeight = [uint32][Math]::Max(1, [Math]::Floor($ph * $scale))
            $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Fant
            $exif = [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation
            $cmm = [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
            $bmp2 = Await ($decoder.GetSoftwareBitmapAsync($fmt, $alpha, $transform, $exif, $cmm)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        } else {
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            # OCRが確実に受け付ける形式へ変換 (Bgra8 / Premultiplied)
            $bmp2 = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bitmap, $fmt, $alpha)
        }
        $result = Await ($engine.RecognizeAsync($bmp2)) ([Windows.Media.Ocr.OcrResult])
        # 行単位でテキスト取得 (行内の単語はスペース結合される。従来互換の text)
        $lines = New-Object System.Collections.Generic.List[string]
        foreach ($line in $result.Lines) { $lines.Add($line.Text) }
        $text = ($lines -join "`n")
        # 行の外接矩形 (OcrLine自体は矩形を持たないため単語BoundingRectの和集合)。
        # 画像サイズで正規化 (原点は左上なのでy反転不要・Python側の期待形式のまま)。
        # 文字が傾いて検出された画像 (TextAngle) は座標系が回転しているため出力しない。
        $rects = @()
        $angle = $result.TextAngle
        $w = [double]$bmp2.PixelWidth
        $h = [double]$bmp2.PixelHeight
        $angleOk = ($null -eq $angle) -or ([Math]::Abs([double]$angle) -le 3.0)
        if (($w -gt 0) -and ($h -gt 0) -and $angleOk) {
            foreach ($line in $result.Lines) {
                $words = @($line.Words)
                if ($words.Count -eq 0) { continue }
                $x0 = ($words | ForEach-Object { $_.BoundingRect.X } | Measure-Object -Minimum).Minimum
                $y0 = ($words | ForEach-Object { $_.BoundingRect.Y } | Measure-Object -Minimum).Minimum
                $x1 = ($words | ForEach-Object { $_.BoundingRect.X + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum
                $y1 = ($words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum
                $rects += [pscustomobject]@{
                    text = $line.Text
                    x0 = [Math]::Round([double]$x0 / $w, 4)
                    x1 = [Math]::Round([double]$x1 / $w, 4)
                    y0 = [Math]::Round([double]$y0 / $h, 4)
                    y1 = [Math]::Round([double]$y1 / $h, 4)
                }
            }
        }
        return @{ text = $text; lines = $rects }
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
        $r = Read-Image-Text $p
        $obj["text"] = $r.text
        if ($r.lines -and @($r.lines).Count -gt 0) { $obj["lines"] = @($r.lines) }
        $obj["ok"] = $true
    } catch {
        $obj["error"] = $_.Exception.Message
    }
    $results.Add([pscustomobject]$obj)
}

# JSON出力 (UTF-8, BOMなし)。lines のネストが深さ4になるため -Depth は余裕を持たせる
$json = $results | ConvertTo-Json -Depth 8 -Compress
[System.IO.File]::WriteAllText($Out, $json, (New-Object System.Text.UTF8Encoding($false)))
exit 0
