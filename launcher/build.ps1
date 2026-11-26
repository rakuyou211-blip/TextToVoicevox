# Build launcher\TextToVoicevox.exe (Windows only). Used by CI and the release workflow.
#   powershell -File launcher\build.ps1 [-Out dist\TextToVoicevox.exe]
# Uses the C# compiler that ships with .NET Framework 4, so nothing extra is installed.
param([string]$Out = "dist\TextToVoicevox.exe")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
$outDir = Split-Path -Parent $Out
if ($outDir) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
# Icon from the same PNG as the app window (needs Pillow)
$ico = Join-Path $env:TEMP 't2v-launcher.ico'
python -c "import sys; from PIL import Image; Image.open(sys.argv[1]).save(sys.argv[2], sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])" (Join-Path $root 'assets\app-icon.png') $ico
if ($LASTEXITCODE) { throw 'icon conversion failed' }
# Product name and version in the file's properties, from core.py's APP_VERSION.
# SignPath checks them when it signs (docs/CODE_SIGNING.md)
$m = Select-String -LiteralPath (Join-Path $root 'core.py') -Pattern '^APP_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | Select-Object -First 1
if (-not $m) { throw 'APP_VERSION not found in core.py' }
$ver = $m.Matches[0].Groups[1].Value
$info = Join-Path $env:TEMP 't2v-launcher-info.cs'
@"
using System.Reflection;
[assembly: AssemblyTitle("TextToVoicevox")]
[assembly: AssemblyDescription("TextToVoicevox launcher")]
[assembly: AssemblyProduct("TextToVoicevox")]
[assembly: AssemblyCompany("RAKUYOU211")]
[assembly: AssemblyCopyright("Copyright (c) 2026 RAKUYOU211. MIT License.")]
[assembly: AssemblyVersion("$ver.0")]
[assembly: AssemblyFileVersion("$ver.0")]
[assembly: AssemblyInformationalVersion("$ver")]
"@ | Set-Content -LiteralPath $info -Encoding ASCII
# /codepage:65001 - the source is UTF-8 without BOM (Japanese messages)
& $csc /nologo /codepage:65001 /optimize+ /target:winexe "/out:$Out" "/win32icon:$ico" /r:System.Windows.Forms.dll (Join-Path $PSScriptRoot 'TextToVoicevox.cs') $info
if ($LASTEXITCODE) { throw "csc failed ($LASTEXITCODE)" }
Get-Item $Out | Format-List Name, Length
(Get-Item $Out).VersionInfo | Format-List ProductName, ProductVersion, FileVersion, FileDescription
