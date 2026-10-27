<#
Builds AniCal for Windows x64 on this PC: the AniCal folder, a portable zip and
the per-user installer, then starts the built app in self-test mode. The same
steps as the windows build job in .github/workflows/windows.yml. From any PowerShell:

  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 [-SkipSelfTest] [-Screenshots] [-Msys2 C:\msys64]

-Screenshots  also opens each setup wizard step and saves a screenshot and
              report of each.
-Msys2        where MSYS2 is installed (default C:\msys64).

Needs, installed once (the script says what's missing):
  MSYS2                    winget install MSYS2.MSYS2
  Python from python.org   winget install Python.Python.3.13
  Inno Setup 6             winget install JRSoftware.InnoSetup
It installs the GTK stack into MSYS2 (UCRT64) itself, with pacman --needed.
It doesn't install the result: the self-test runs the built folder, so an
AniCal you already installed is left alone. Writes:
  dist\AniCal\                                  the app folder
  out\AniCal-VERSION-windows-x64-portable.zip
  out\AniCal-VERSION-windows-x64-setup.exe
  out\windows-selftest\                         report.json, report.log, screenshot.png
#>
param(
    [string]$Msys2 = "C:\msys64",
    [switch]$SkipSelfTest,
    [switch]$Screenshots
)
$ErrorActionPreference = "Stop"

function Say($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nError: $text" -ForegroundColor Red; exit 1 }
function Check($what) { if ($LASTEXITCODE) { Fail "$what failed (exit $LASTEXITCODE)" } }

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
$work = Join-Path $root "build\windows"
New-Item -ItemType Directory -Force $work | Out-Null
$version = (Select-String -Path anical\__init__.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value

# Prerequisites: find them all first, so one run lists everything missing.
$missing = @()
$bash = Join-Path $Msys2 "usr\bin\bash.exe"
if (-not (Test-Path $bash)) { $missing += "MSYS2 (not in $Msys2):  winget install MSYS2.MSYS2" }
$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($want in @("-3.13", "-3")) {
        # Windows PowerShell turns py's "not found" message into an error under Stop.
        try { $found = & py $want -c "import sys; print(sys.executable)" 2>$null } catch { $found = $null }
        if (-not $LASTEXITCODE -and $found) { $python = "$found".Trim(); break }
    }
}
if (-not $python) { $missing += "Python from python.org:  winget install Python.Python.3.13" }
$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { $missing += "Inno Setup 6:  winget install JRSoftware.InnoSetup" }
if ($missing) {
    Write-Host "`nInstall these first, then run this again:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  $_" }
    exit 1
}

# MSYS2's UCRT64 shell, started in the repository folder.
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
function Msys($command) {
    & $bash -lc $command
    Check "MSYS2: $command"
}

Say "GTK stack in MSYS2 (UCRT64)"
$packages = "gtk4 libadwaita python-gobject pyinstaller adwaita-icon-theme python-certifi python-tzdata python-tzlocal librsvg" -split " " |
    ForEach-Object { "mingw-w64-ucrt-x86_64-$_" }
Msys "pacman -S --needed --noconfirm $($packages -join ' ')"

Say "Icon"
Msys "rsvg-convert -w 256 -h 256 anical/data/icons/hicolor/scalable/apps/io.github.dhopson.AniCal.svg -o build/windows/icon-256.png"
$venv = Join-Path $work "venv-notify"
if (-not (Test-Path "$venv\Scripts\python.exe")) {
    & $python -m venv $venv
    Check "Creating $venv"
}
& "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip pillow pyinstaller desktop-notifier
Check "pip install"
$ico = Join-Path $work "AniCal.ico"
& "$venv\Scripts\python.exe" packaging\windows\make-ico.py build\windows\icon-256.png $ico
Check "make-ico.py"

Say "Freezing AniCal $version (MSYS2)"
$env:ANICAL_ICO = $ico
Msys "pyinstaller --noconfirm --log-level WARN --distpath dist --workpath build/windows/pyinstaller packaging/pyinstaller/anical.spec"

Say "Freezing anical-notify (python.org Python)"
& "$venv\Scripts\pyinstaller.exe" --noconfirm --log-level WARN --distpath "$work\dist-notify" --workpath "$work\notify" packaging\windows\anical-notify.spec
Check "pyinstaller anical-notify"
if (Test-Path dist\AniCal\anical-notify) { Remove-Item -Recurse -Force dist\AniCal\anical-notify }
Copy-Item -Recurse "$work\dist-notify\anical-notify" dist\AniCal\anical-notify

Say "Portable zip and installer"
New-Item -ItemType Directory -Force out | Out-Null
$zip = "out\AniCal-$version-windows-x64-portable.zip"
Compress-Archive -Force -Path dist\AniCal -DestinationPath $zip
& $iscc /Q "/DAppVersion=$version" "/DIconFile=$ico" packaging\windows\anical.iss
Check "Inno Setup"
$setup = "out\AniCal-$version-windows-x64-setup.exe"

if (-not $SkipSelfTest) {
    # Without MSYS2 on PATH, so the app has to stand on its own, as on a user's PC.
    $env:PATH = ($env:PATH -split ";" | Where-Object { $_ -and -not $_.StartsWith($Msys2, "OrdinalIgnoreCase") }) -join ";"
    Remove-Item Env:MSYSTEM, Env:CHERE_INVOKING, Env:ANICAL_ICO
    $app = Join-Path $root "dist\AniCal\AniCal.exe"
    Say "Self-test (a window opens for a few seconds)"
    & $python packaging\ci\run_selftest.py out\windows-selftest "--" $app
    Check "Self-test"
    if ($Screenshots) {
        Say "Setup wizard screenshots"
        foreach ($step in 1..4) {
            $env:ANICAL_SELFTEST_WIZARD = "$step"
            & $python packaging\ci\run_selftest.py "out\windows-wizard-$step" "--" $app
            if ($LASTEXITCODE) { Write-Host "Step $step's self-test failed; carrying on." -ForegroundColor Yellow }
        }
        Remove-Item Env:ANICAL_SELFTEST_WIZARD
    }
}

Say "Done"
Write-Host "App folder:   dist\AniCal\"
Write-Host "Portable zip: $zip"
Write-Host "Installer:    $setup"
if (-not $SkipSelfTest) { Write-Host "Self-test:    out\windows-selftest\" }
