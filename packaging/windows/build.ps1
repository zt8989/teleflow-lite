#!/usr/bin/env pwsh
#
# Build the TeleFlow Windows installer.
#
# Usage:  .\packaging\windows\build.ps1
#
# Steps:
#   1. Freeze the app with PyInstaller into an onedir TeleFlow directory.
#   2. Run NSIS to wrap the onedir into a single .exe installer.
#
# The output is left UNSIGNED — distributing may trigger Windows SmartScreen.
# To distribute widely, you would need a code signing certificate.
#
# Phone-report feature: report audio is synthesized with edge-tts and
# transcoded with ffmpeg. ffmpeg is NOT bundled — it must be present on the
# target machine's PATH or configured via TeleFlow's `ffmpeg_path` setting.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent (Split-Path -Parent $ScriptDir)
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Spec = Join-Path $ScriptDir "teleflow.spec"
$OutDir = Join-Path $ScriptDir "dist"
$BuildDir = Join-Path $ScriptDir "build"
$NsisScript = Join-Path $ScriptDir "installer.nsi"
$NsisExe = "C:\Program Files (x86)\NSIS\makensis.exe"

# ─── 1. Freeze the app ───────────────────────────────────────────────────────

Write-Host "`n=== Step 1: PyInstaller freeze ===" -ForegroundColor Cyan
& $VenvPython -m PyInstaller --clean --noconfirm --distpath $OutDir --workpath $BuildDir $Spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$ExePath = Join-Path $OutDir "TeleFlow\TeleFlow.exe"
if (-not (Test-Path $ExePath)) {
    throw "Build artifact not found: $ExePath"
}
Write-Host "Frozen app: $ExePath" -ForegroundColor Green

# ─── 2. Build installer ──────────────────────────────────────────────────────

Write-Host "`n=== Step 2: NSIS installer ===" -ForegroundColor Cyan

if (-not (Test-Path $NsisExe)) {
    Write-Host "NSIS not found at $NsisExe" -ForegroundColor Yellow
    Write-Host "Install with: winget install NSIS.NSIS" -ForegroundColor Yellow
    throw "NSIS not found"
}

Push-Location $ScriptDir
& $NsisExe /INPUTCHARSET UTF8 /V3 $NsisScript
Pop-Location
if ($LASTEXITCODE -ne 0) { throw "NSIS failed with exit code $LASTEXITCODE" }

$InstallerPath = Join-Path $Root "TeleFlow-windows-0.1.0-setup.exe"
if (Test-Path $InstallerPath) {
    $size = (Get-Item $InstallerPath).Length
    $sizeMB = "{0:N1}" -f ($size / 1MB)
    Write-Host "`nInstaller built successfully!" -ForegroundColor Green
    Write-Host "  File: $InstallerPath"
    Write-Host "  Size: $sizeMB MB"
} else {
    throw "Installer not found at $InstallerPath"
}
