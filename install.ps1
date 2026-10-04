# HERMUS Agent Free — one-shot repository installer for Windows PowerShell.
# Downloads the repository when needed, then delegates the full environment
# setup to the canonical setup.ps1.
[CmdletBinding()]
param(
  [string]$InstallDir = (Join-Path $env:USERPROFILE "hermus-agent-free"),
  [string]$Ref = "main",
  [switch]$Repair,
  [switch]$VerifyOnly,
  [switch]$SkipSystem,
  [switch]$SkipOptional
)

$ErrorActionPreference = "Stop"
$repo = "https://github.com/trmv2007-bot/hermus-agent-free"
$zipUrl = "$repo/archive/refs/heads/$Ref.zip"
$temp = Join-Path $env:TEMP ("hermus-" + [guid]::NewGuid().ToString("N"))
$zip = Join-Path $temp "hermus.zip"

function Info($m) { Write-Host "[HERMUS] $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "[HERMUS][FAIL] $m" -ForegroundColor Red; exit 1 }

New-Item -ItemType Directory -Force -Path $temp | Out-Null
try {
  $git = Get-Command git -ErrorAction SilentlyContinue
  $gitCheckout = Test-Path (Join-Path $InstallDir ".git")

  if ($gitCheckout -and $git) {
    Info "Updating existing checkout: $InstallDir"
    & git -C $InstallDir fetch --depth 1 origin $Ref
    if ($LASTEXITCODE -ne 0) { Fail "Git fetch failed." }
    & git -C $InstallDir checkout -q $Ref 2>$null
    if ($LASTEXITCODE -ne 0) {
      & git -C $InstallDir checkout -q -B $Ref "origin/$Ref"
      if ($LASTEXITCODE -ne 0) { Fail "Could not select ref $Ref." }
    }
    & git -C $InstallDir pull --ff-only origin $Ref
    if ($LASTEXITCODE -ne 0) { Fail "Existing checkout has local/diverged changes. Resolve them, then re-run." }
  } elseif (Test-Path $InstallDir) {
    Fail "Install directory already exists but is not a Git checkout: $InstallDir. Use setup.ps1 there, or choose another InstallDir."
  } else {
    Info "Downloading HERMUS $Ref"
    Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $temp -Force
    $extracted = Get-ChildItem -Path $temp -Directory | Where-Object { $_.Name -like "hermus-agent-free-*" } | Select-Object -First 1
    if (-not $extracted) { Fail "Could not locate the extracted HERMUS repository." }
    Move-Item -Path $extracted.FullName -Destination $InstallDir
  }

  $setup = Join-Path $InstallDir "setup.ps1"
  if (-not (Test-Path $setup)) { Fail "setup.ps1 is missing from the repository." }

  $args = @()
  if ($Repair) { $args += "-Repair" }
  if ($VerifyOnly) { $args += "-VerifyOnly" }
  if ($SkipSystem) { $args += "-SkipSystem" }
  if ($SkipOptional) { $args += "-SkipOptional" }

  Info "Starting canonical full installation"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup @args
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
  Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
}
