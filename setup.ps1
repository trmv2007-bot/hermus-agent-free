# HERMUS Agent Free — fresh-machine Windows installer
# Default Windows entry point. Detects/install missing host tooling, creates the
# project-local .venv, installs dependencies, then runs the live bootstrap.
[CmdletBinding()]
param(
    [switch]$Repair,
    [switch]$VerifyOnly,
    [switch]$SkipSystem,
    [switch]$SkipOptional
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
function Info($m) { Write-Host "[HERMUS] $m" -ForegroundColor Cyan }
function Good($m) { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[FAIL] $m" -ForegroundColor Red; exit 1 }
function Has($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }
Info "Windows fresh-machine installer"
Info "Repository: $Root"
if (-not (Test-Path (Join-Path $Root 'bootstrap.py'))) { Fail 'bootstrap.py is missing from the repository root.' }
if (-not (Test-Path (Join-Path $Root 'bootstrap_live.py'))) { Fail 'bootstrap_live.py is missing from the repository root.' }
if (-not (Test-Path (Join-Path $Root 'requirements.txt'))) { Fail 'requirements.txt is missing from the repository root.' }
function Install-WingetPackage($Id, $Name) {
    if (-not (Has 'winget')) { return $false }
    Info "Installing missing $Name with winget ($Id)"
    & winget install --id $Id --exact --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -ne 0) { Warn "winget could not install $Name automatically."; return $false }
    return $true
}
if (-not $SkipSystem -and -not $VerifyOnly) {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($machinePath -or $userPath) { $env:Path = "$machinePath;$userPath" }
    if (-not (Has 'git')) { [void](Install-WingetPackage 'Git.Git' 'Git') }
    if (-not (Has 'python')) { [void](Install-WingetPackage 'Python.Python.3.12' 'Python 3.12') }
    if (-not (Has 'node')) { [void](Install-WingetPackage 'OpenJS.NodeJS.LTS' 'Node.js LTS') }
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($machinePath -or $userPath) { $env:Path = "$machinePath;$userPath" }
}
if (-not (Has 'python')) { Fail 'Python 3.10+ is required. Install Python from python.org or enable winget, then run setup.ps1 again.' }
if (-not (Has 'git')) { Fail 'Git is required. Install Git for Windows or enable winget, then run setup.ps1 again.' }
$py = (Get-Command python).Source
$versionText = & $py --version 2>&1
Good "Host Python selected: $versionText"
Good "Git detected"
$venv = Join-Path $Root '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Info "Creating project-local .venv"
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail 'Could not create .venv. Reinstall a full Python distribution with venv support.' }
} else { Good "Project .venv already exists" }
if (-not $VerifyOnly) {
    Info "Upgrading pip in .venv"
    & $venvPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail 'Could not upgrade pip inside .venv.' }
    Info "Installing canonical Python dependencies (live pip progress below)"
    $pipArgs = @('-m','pip','install','--disable-pip-version-check','--progress-bar','on')
    if ($Repair) { $pipArgs += '--upgrade' }
    $pipArgs += @('-r',(Join-Path $Root 'requirements.txt'))
    & $venvPy @pipArgs
    if ($LASTEXITCODE -ne 0) { Fail 'Python dependency installation failed. See the pip error above.' }
    if (-not $SkipOptional -and (Test-Path (Join-Path $Root 'requirements-optional.txt'))) {
        Info "Installing optional capabilities independently (live progress below)"
        $optional = @(Get-Content (Join-Path $Root 'requirements-optional.txt') | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') -and -not $_.Trim().StartsWith('-') })
        $i = 0
        foreach ($line in $optional) {
            $i++
            $spec = $line.Trim()
            Info "Optional [$i/$($optional.Count)] $spec"
            & $venvPy -m pip install --disable-pip-version-check --progress-bar on $spec
            if ($LASTEXITCODE -ne 0) { Warn "Optional dependency unavailable: $spec" }
        }
    }
}
$packageJson = Join-Path $Root 'package.json'
if (Test-Path $packageJson) {
    if (-not (Has 'node')) { Fail 'Node.js is required because package.json is present.' }
    if (-not (Has 'npm')) { Fail 'npm is required because package.json is present.' }
    Info 'Installing JavaScript dependencies (live npm output below)'
    if (Test-Path (Join-Path $Root 'package-lock.json')) { & npm ci } else { & npm install }
    if ($LASTEXITCODE -ne 0) { Fail 'JavaScript dependency installation failed.' }
} else {
    if (Has 'node') { Good 'Node.js detected (optional; no package.json is present)' } else { Warn 'Node.js/npm not installed; no JS package manifest requires them.' }
    if (Has 'bun') { Good "Optional Bun detected: $(& bun --version)" } else { Warn 'Bun not installed; no Bun manifest is present, so it is not required.' }
}
if ($VerifyOnly) {
    Info 'Running canonical Doctor in verification-only mode'
    & $venvPy bootstrap_live.py --verify-only
} else {
    Info 'Running canonical HERMUS bootstrap/Doctor with live download progress'
    $args = @()
    if ($Repair) { $args += '--repair' }
    & $venvPy bootstrap_live.py @args
}
if ($LASTEXITCODE -ne 0) { Fail 'HERMUS Doctor reported an incomplete installation. Fix the reported item and re-run setup.ps1.' }
Good 'HERMUS fresh-machine setup completed.'
Good "Runtime: $venvPy"
Write-Host ''
Write-Host 'Next: .\.venv\Scripts\Activate.ps1' -ForegroundColor Cyan
Write-Host 'Then:  .\bin\hermus.ps1 status' -ForegroundColor Cyan
