# HERMUS Agent Free — fresh-machine Windows installer
[CmdletBinding()]
param([switch]$Repair,[switch]$VerifyOnly,[switch]$SkipSystem,[switch]$SkipOptional)
$ErrorActionPreference='Stop'
$Root=Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$MinPython=if($env:HERMUS_MIN_PYTHON){[version]$env:HERMUS_MIN_PYTHON}else{[version]'3.10'}
$InstallNodeWhenMissing=$env:HERMUS_INSTALL_NODE -ne '0'
function Info($m){Write-Host "[HERMUS] $m" -ForegroundColor Cyan}
function Good($m){Write-Host "[ OK ] $m" -ForegroundColor Green}
function Warn($m){Write-Host "[WARN] $m" -ForegroundColor Yellow}
function Fail($m){Write-Host "[FAIL] $m" -ForegroundColor Red;exit 1}
function Has($name){return [bool](Get-Command $name -ErrorAction SilentlyContinue)}
function PythonVersion($exe){try{return [version]((& $exe -c 'import platform;print(platform.python_version())') -join '').Trim()}catch{return $null}}
function FindPython{
  foreach($candidate in @('python','python3')){if(Has $candidate){$v=PythonVersion $candidate;if($v -and $v -ge $MinPython){return(Get-Command $candidate).Source}}}
  if(Has 'py'){try{$v=PythonVersion 'py';if($v -and $v -ge $MinPython){return(Get-Command py).Source}}catch{}}
  return $null
}
function Install-WingetPackage($Id,$Name){if(-not(Has 'winget')){return $false};Info "Installing missing $Name with winget ($Id)";& winget install --id $Id --exact --accept-source-agreements --accept-package-agreements --silent;if($LASTEXITCODE -ne 0){Warn "winget could not install $Name automatically.";return $false};return $true}
Info 'Windows fresh-machine installer'
Info "Repository: $Root"
foreach($required in @('bootstrap.py','bootstrap_live.py','requirements.txt')){if(-not(Test-Path(Join-Path $Root $required))){Fail "$required is missing from the repository root."}}
$py=FindPython
if(-not $SkipSystem -and -not $VerifyOnly){
  if(-not $py){[void](Install-WingetPackage 'Python.Python.3.13' 'Python')}
  if(-not(Has 'git')){[void](Install-WingetPackage 'Git.Git' 'Git')}
  if($InstallNodeWhenMissing -and -not(Has 'node')){[void](Install-WingetPackage 'OpenJS.NodeJS.LTS' 'Node.js LTS')}
  $py=FindPython
}
if(-not $py){Fail "Python $MinPython or newer is required. Install Python and re-run setup.ps1."}
if(-not(Has 'git')){Fail 'Git is required. Install Git for Windows and re-run setup.ps1.'}
Good "Host Python selected: $(& $py --version 2>&1)"
Good 'Git detected'
$venv=Join-Path $Root '.venv';$venvPy=Join-Path $venv 'Scripts\python.exe'
if(-not(Test-Path $venvPy)){Info 'Creating project-local .venv';& $py -m venv $venv;if($LASTEXITCODE -ne 0 -or -not(Test-Path $venvPy)){Fail 'Could not create .venv. Reinstall Python with venv support.'}}else{Good 'Project .venv already exists'}
if(-not $VerifyOnly){
  Info 'Upgrading pip in .venv';& $venvPy -m pip install --upgrade pip;if($LASTEXITCODE -ne 0){Fail 'Could not upgrade pip inside .venv.'}
  Info 'Installing canonical Python dependencies (live progress below)';$pipArgs=@('-m','pip','install','--disable-pip-version-check','--progress-bar','on');if($Repair){$pipArgs+='--upgrade'};$pipArgs+=@('-r',(Join-Path $Root 'requirements.txt'));& $venvPy @pipArgs;if($LASTEXITCODE -ne 0){Fail 'Python dependency installation failed. See the pip error above.'}
  if(-not $SkipOptional -and(Test-Path(Join-Path $Root 'requirements-optional.txt'))){$optional=@(Get-Content(Join-Path $Root 'requirements-optional.txt')|Where-Object{$_.Trim() -and -not $_.Trim().StartsWith('#') -and -not $_.Trim().StartsWith('-')});$i=0;foreach($line in $optional){$i++;$spec=$line.Trim();Info "Optional [$i/$($optional.Count)] $spec";& $venvPy -m pip install --disable-pip-version-check --progress-bar on $spec;if($LASTEXITCODE -ne 0){Warn "Optional dependency unavailable: $spec"}}}
}
$packageJson=Join-Path $Root 'package.json'
if(Test-Path $packageJson){if(-not(Has 'node')){Fail 'Node.js is required because package.json is present.'};if(-not(Has 'npm')){Fail 'npm is required because package.json is present.'};Info 'Installing JavaScript dependencies (live npm output below)';if(Test-Path(Join-Path $Root 'package-lock.json')){& npm ci}else{& npm install};if($LASTEXITCODE -ne 0){Fail 'JavaScript dependency installation failed.'}}else{if(Has 'node'){Good 'Node.js detected; no JS package manifest requires it.'}else{Info 'Node.js/npm not required: no JS package manifest is present.'};if(Has 'bun'){Good "Optional Bun detected: $(& bun --version)"}else{Info 'Bun not required: no Bun manifest is present.'}}
if($VerifyOnly){Info 'Running canonical Doctor in verification-only mode';& $venvPy bootstrap_live.py --verify-only}else{Info 'Running canonical HERMUS bootstrap/Doctor with live download progress';$args=@();if($Repair){$args+='--repair'};& $venvPy bootstrap_live.py @args}
if($LASTEXITCODE -ne 0){Fail 'HERMUS Doctor reported an incomplete installation. Fix the reported item and re-run setup.ps1.'}
Good 'HERMUS fresh-machine setup completed.'
Good "Runtime: $venvPy"
