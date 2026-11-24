# Run from the extracted project folder using Windows PowerShell.
[CmdletBinding()]
param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not [Environment]::Is64BitOperatingSystem -or
    ($env:PROCESSOR_ARCHITEW6432, $env:PROCESSOR_ARCHITECTURE -notcontains 'AMD64')) {
    throw 'This installer requires x64 Windows.'
}

function Install-Requirement([string]$Package) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Install Microsoft App Installer (WinGet), or install $Package manually, then rerun this script."
    }
    Write-Host "Installing $Package with WinGet..."
    & winget.exe install --id $Package --exact --source winget --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "Could not install $Package. Install it manually and rerun this script." }
    $env:Path = @($env:Path, [Environment]::GetEnvironmentVariable('Path','Machine'),
        [Environment]::GetEnvironmentVariable('Path','User')) -join ';'
}

function Test-Python311 {
    if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) { return $false }
    try {
        & py.exe -3.11 -c "import sys; sys.exit(sys.maxsize <= 2**32)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

if (-not (Test-Python311)) {
    Install-Requirement 'Python.Python.3.11'
    if (-not (Test-Python311)) { throw 'Install 64-bit Python 3.11 with the Python Launcher, reopen PowerShell and retry.' }
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    Install-Requirement 'OpenJS.NodeJS.LTS'
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Reopen PowerShell so it can find npm, then retry.' }
}

& (Join-Path $PSScriptRoot 'start.ps1') -Setup -InstallModels -OpenBrowser:(-not $NoBrowser)
