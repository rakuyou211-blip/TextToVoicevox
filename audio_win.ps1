# WAV -> M4A / MP3 using the encoder built into Windows (Media Foundation via WinRT).
# No ffmpeg needed. Called by core.encode_audio_file. Windows 10 or later.
# usage: audio_win.ps1 -In <wav> -Out <m4a|mp3 path> -Fmt m4a|mp3
param([string]$In, [string]$Out, [string]$Fmt = "m4a")
$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $null = [Windows.Media.Transcoding.MediaTranscoder, Windows.Media.Transcoding, ContentType = WindowsRuntime]
    $null = [Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime]
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }

    function Await($op, [Type]$type) {
        $m = $asTask | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
        $t = $m.MakeGenericMethod($type).Invoke($null, @($op))
        $t.Wait()
        $t.Result
    }

    function AwaitProgress($op, [Type]$p) {
        $m = $asTask | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncActionWithProgress`1' } | Select-Object -First 1
        $t = $m.MakeGenericMethod($p).Invoke($null, @($op))
        $t.Wait()
    }

    if ($In -eq "--probe") { Write-Output "ok"; exit 0 }
    $src = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($In)) ([Windows.Storage.StorageFile])
    $dir = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync([System.IO.Path]::GetDirectoryName($Out))) ([Windows.Storage.StorageFolder])
    $dst = Await ($dir.CreateFileAsync([System.IO.Path]::GetFileName($Out), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
    # Speech from VOICEVOX is mono. Medium (128 kbps) is plenty and keeps a 10-hour book small.
    $q = [Windows.Media.MediaProperties.AudioEncodingQuality]::Medium
    if ($Fmt -eq "mp3") { $prof = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateMp3($q) }
    else { $prof = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateM4a($q) }
    $tr = New-Object Windows.Media.Transcoding.MediaTranscoder
    $prep = Await ($tr.PrepareFileTranscodeAsync($src, $dst, $prof)) ([Windows.Media.Transcoding.PrepareTranscodeResult])
    if (-not $prep.CanTranscode) {
        [Console]::Error.WriteLine("cannot transcode: " + $prep.FailureReason)
        exit 2
    }
    AwaitProgress ($prep.TranscodeAsync()) ([double])
    Write-Output "ok"
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
