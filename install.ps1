# HERMUS JARVIS - ONE-SHOT WINDOWS INSTALLER (PowerShell)
# RTX 3050 Optimized | 100% Free | Local-First AI Assistant
# ============================================================

Write-Host "`n╔════════════════════════════════════════════════════════════════╗"
Write-Host "║  HERMUS JARVIS - ONE-SHOT WINDOWS INSTALLER                   ║"
Write-Host "║  RTX 3050 Optimized | 100% Free | Local-First AI Assistant      ║"
Write-Host "╚════════════════════════════════════════════════════════════════╝"

# Check if running as administrator
if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warning "Please run as Administrator for best results"
    Write-Host "Continuing anyway...`n"
}

# Installation directory
$INSTALL_DIR = "$HOME\hermus-jarvis"
if (-not (Test-Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR | Out-Null
    Write-Host "[✓] Created installation directory: $INSTALL_DIR" -ForegroundColor Green
} else {
    Write-Host "[i] Using existing directory: $INSTALL_DIR" -ForegroundColor Cyan
}

# Check Python
try {
    $pythonVersion = python --version 2>$null
    if ($LASTEXITCODE -ne 0) {
        $pythonVersion = python3 --version 2>$null
    }
    
    if ($LASTEXITCODE -ne 0) {
        throw "Python not found"
    }
    
    Write-Host "[✓] $pythonVersion detected" -ForegroundColor Green
    
    # Check Python version
    $versionMatch = [regex]::Match($pythonVersion, "(\d+)\.(\d+)")
    if ($versionMatch.Success) {
        $major = [int]$versionMatch.Groups[1].Value
        $minor = [int]$versionMatch.Groups[2].Value
        
        if ($major -lt 3) {
            Write-Error "Python 3.x required. Found Python $major"
            exit 1
        }
        
        if ($minor -lt 10) {
            Write-Warning "Python 3.10+ recommended for best performance"
        }
    }
} catch {
    Write-Host "[✗] Python not found. Installing Python 3.11..." -ForegroundColor Red
    Write-Host "This will open the Python installer. Please check 'Add Python to PATH'" -ForegroundColor Yellow
    Start-Process "https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe"
    Write-Host "After installation, run this script again.`n" -ForegroundColor Yellow
    exit 1
}

# Check Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[→] Installing Git..." -ForegroundColor Blue
    try {
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
        Write-Host "[✓] Git installed" -ForegroundColor Green
    } catch {
        Write-Host "[!] Git installation via winget failed. Please install manually from https://git-scm.com/" -ForegroundColor Yellow
    }
} else {
    Write-Host "[✓] Git already installed" -ForegroundColor Green
}

# Check cURL
if (-not (Get-Command curl -ErrorAction SilentlyContinue)) {
    Write-Host "[→] Installing cURL..." -ForegroundColor Blue
    try {
        winget install --id curl.curl -e --source winget --accept-package-agreements --accept-source-agreements
        Write-Host "[✓] cURL installed" -ForegroundColor Green
    } catch {
        Write-Host "[!] cURL installation via winget failed. Please install manually" -ForegroundColor Yellow
    }
} else {
    Write-Host "[✓] cURL already installed" -ForegroundColor Green
}

# Clone HERMUS repository
Write-Host "`n[→] Cloning HERMUS JARVIS repository..." -ForegroundColor Blue
Set-Location $INSTALL_DIR

if (Test-Path "hermus-agent-free") {
    Write-Host "[i] Repository already exists. Pulling latest changes..." -ForegroundColor Cyan
    Set-Location hermus-agent-free
    git pull origin main
} else {
    git clone https://github.com/trmv2007-bot/hermus-agent-free.git
    Set-Location hermus-agent-free
}
Write-Host "[✓] Repository ready" -ForegroundColor Green

# Install Ollama for local models (RTX 3050 optimized)
Write-Host "`n[→] Installing Ollama for local AI models..." -ForegroundColor Blue
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    try {
        # Download and install Ollama
        $ollamaUrl = "https://ollama.com/install.sh"
        $tempFile = "$env:TEMP\install.sh"
        Invoke-WebRequest -Uri $ollamaUrl -OutFile $tempFile
        
        # Use Git Bash to run the installer if available
        if (Get-Command bash -ErrorAction SilentlyContinue) {
            bash -c "curl -fsSL https://ollama.com/install.sh | sh"
        } else {
            Write-Host "[!] Git Bash not found. Please install Ollama manually from https://ollama.com/" -ForegroundColor Yellow
        }
        
        if (Get-Command ollama -ErrorAction SilentlyContinue) {
            Write-Host "[✓] Ollama installed" -ForegroundColor Green
            Write-Host "[→] Pulling mistral:7b (RTX 3050 optimized, ~4.5GB)..." -ForegroundColor Blue
            ollama pull mistral:7b
            Write-Host "[✓] Model ready" -ForegroundColor Green
        } else {
            Write-Host "[!] Ollama installation may have failed. Please install manually" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "[!] Failed to install Ollama: $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "[✓] Ollama already installed" -ForegroundColor Green
    ollama pull mistral:7b 2>$null
}

# Create Python virtual environment
Write-Host "`n[→] Setting up Python environment..." -ForegroundColor Blue
python -m venv .venv

# Activate virtual environment and install dependencies
Write-Host "[→] Installing Python dependencies..." -ForegroundColor Blue
& "$PWD\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$PWD\.venv\Scripts\python.exe" -m pip install -e .

# Configure RTX 3050 settings
Write-Host "`n[→] Configuring for RTX 3050 (8GB VRAM, 7GB safe limit)..." -ForegroundColor Blue
[System.Environment]::SetEnvironmentVariable("HERMUS_VRAM_LIMIT", "7", "User")
[System.Environment]::SetEnvironmentVariable("HERMUS_GPU_MEMORY", "7GB", "User")
[System.Environment]::SetEnvironmentVariable("HERMUS_RECOMMENDED_MODEL", "mistral:7b", "User")

# Create startup script
Write-Host "`n[→] Creating startup script..." -ForegroundColor Blue
$startupScript = @'
@echo off
chcp 65001 > nul
cd /d "%~dp0"
call .venv\Scripts\activate
python -m gateway.gateway
'@
$startupScript | Out-File -FilePath "$INSTALL_DIR\hermus-agent-free\start.bat" -Encoding ASCII

# Create desktop shortcut
$desktopPath = [Environment]::GetFolderPath("Desktop")
$shortcutPath = "$desktopPath\Start HERMUS JARVIS.bat"
$startupScript | Out-File -FilePath $shortcutPath -Encoding ASCII
Write-Host "[✓] Created startup script and desktop shortcut" -ForegroundColor Green

# Completion banner
Write-Host "`n╔════════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  ✅ HERMUS JARVIS INSTALLATION COMPLETE!                     ║" -ForegroundColor Green
Write-Host "╚════════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host "`nInstallation Directory: $INSTALL_DIR\hermus-agent-free`n"

Write-Host "To start HERMUS JARVIS:" -ForegroundColor Cyan
Write-Host "  1. Double-click 'Start HERMUS JARVIS' on your Desktop" -ForegroundColor Yellow
Write-Host "  2. Or run in PowerShell: cd $INSTALL_DIR\hermus-agent-free; .\.venv\Scripts\Activate.ps1; python -m gateway.gateway" -ForegroundColor Yellow
Write-Host "  3. Or run in CMD: cd $INSTALL_DIR\hermus-agent-free && .\.venv\Scripts\activate && python -m gateway.gateway" -ForegroundColor Yellow

Write-Host "`nAccess the Control Room at: http://localhost:8000/control`n" -ForegroundColor Cyan

Write-Host "RTX 3050 Configuration:" -ForegroundColor Cyan
Write-Host "  - VRAM Safe Limit: 7GB (out of 8GB)" -ForegroundColor Yellow
Write-Host "  - Recommended Model: mistral:7b" -ForegroundColor Yellow
Write-Host "  - Quantization: 4bit" -ForegroundColor Yellow
Write-Host "  - Context Window: 4096" -ForegroundColor Yellow
Write-Host "  - Streaming: Enabled" -ForegroundColor Yellow

Write-Host "`nFeatures:" -ForegroundColor Cyan
Write-Host "  ✓ JARVIS Control Room with animated orb" -ForegroundColor Green
Write-Host "  ✓ Spacegrid particle background" -ForegroundColor Green
Write-Host "  ✓ Live VRAM monitor" -ForegroundColor Green
Write-Host "  ✓ Multi-agent system (10 keys/provider max)" -ForegroundColor Green
Write-Host "  ✓ Agent-to-agent communication" -ForegroundColor Green
Write-Host "  ✓ Voice chat (type or speak)" -ForegroundColor Green
Write-Host "  ✓ 100% Free & Local-First" -ForegroundColor Green

Write-Host "`nEnvironment Variables Set:" -ForegroundColor Cyan
Write-Host "  HERMUS_VRAM_LIMIT=7" -ForegroundColor Yellow
Write-Host "  HERMUS_GPU_MEMORY=7GB" -ForegroundColor Yellow
Write-Host "  HERMUS_RECOMMENDED_MODEL=mistral:7b" -ForegroundColor Yellow

# Start HERMUS JARVIS (optional)
$startNow = Read-Host "`nStart HERMUS JARVIS now? [Y/n]"
if ($startNow -eq "" -or $startNow -eq "Y" -or $startNow -eq "y") {
    Write-Host "`n[→] Starting HERMUS JARVIS..." -ForegroundColor Blue
    cd $INSTALL_DIR\hermus-agent-free
    & "$PWD\.venv\Scripts\python.exe" -m gateway.gateway
}
