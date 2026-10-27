# AniCal setup: installs Git for Windows, ani-cli and mpv.
#
# AniCal's setup wizard opens this in a PowerShell window. It says what it will
# do and asks before each step. Git for Windows comes from winget; ani-cli and
# mpv come from scoop, as ani-cli's Windows instructions describe.
# Usage: install-windows.ps1 [git] [ani-cli] [mpv]
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Programs)

function Say($text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Ask($question) {
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}
function Finish($code) {
    Read-Host "`nPress Enter to close this window. AniCal checks again by itself"
    exit $code
}
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

if (-not $Programs) { $Programs = @('git', 'ani-cli', 'mpv') }
Say "AniCal setup: $($Programs -join ', ')"
$failed = $false

function Ensure-Scoop {
    if (Get-Command scoop -ErrorAction SilentlyContinue) { return $true }
    Say "scoop isn't installed. ani-cli and mpv are installed with it."
    Write-Host "Its official installer comes from https://scoop.sh and installs into your user folder."
    if (-not (Ask "Install scoop now?")) { return $false }
    try {
        Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force
        Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
        Refresh-Path
    } catch {
        Write-Host $_ -ForegroundColor Red
        return $false
    }
    return [bool](Get-Command scoop -ErrorAction SilentlyContinue)
}

foreach ($program in $Programs) {
    switch ($program) {
        'git' {
            $bash = @("$env:ProgramFiles\Git\bin\bash.exe", "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe") | Where-Object { Test-Path $_ }
            if ($bash) { Say "Git for Windows is already installed: $($bash[0])"; continue }
            Say "Installing Git for Windows (AniCal runs ani-cli with its Git Bash)"
            Write-Host "Will run: winget install --id Git.Git -e --source winget"
            if (-not (Ask "Continue?")) { $failed = $true; continue }
            winget install --id Git.Git -e --source winget
            if ($LASTEXITCODE) { $failed = $true }
        }
        'ani-cli' {
            if (Test-Path "$HOME\scoop\apps\ani-cli\current\ani-cli") { Say "ani-cli is already installed."; continue }
            Say "Installing ani-cli"
            if (-not (Ensure-Scoop)) { $failed = $true; continue }
            Write-Host "Will run: scoop bucket add extras; scoop install ani-cli"
            if (-not (Ask "Continue?")) { $failed = $true; continue }
            scoop bucket add extras
            scoop install ani-cli
            if ($LASTEXITCODE) { $failed = $true }
        }
        'mpv' {
            if (Test-Path "$HOME\scoop\apps\mpv\current\mpv.exe") { Say "mpv is already installed."; continue }
            Say "Installing mpv"
            if (-not (Ensure-Scoop)) { $failed = $true; continue }
            Write-Host "Will run: scoop bucket add extras; scoop install mpv"
            if (-not (Ask "Continue?")) { $failed = $true; continue }
            scoop bucket add extras
            scoop install mpv
            if ($LASTEXITCODE) { $failed = $true }
        }
        default { Write-Host "Don't know how to install $program."; $failed = $true }
    }
}

if ($failed) {
    Say "Something wasn't installed. See the messages above, or ani-cli's instructions:"
    Write-Host "https://github.com/pystardust/ani-cli"
    Finish 1
}
Say "Done."
Finish 0
