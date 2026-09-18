@echo off
chcp 65001 > nul
echo ╔════════════════════════════════════════════════════════════════╗
echo ║  HERMUS JARVIS - ONE-SHOT WINDOWS INSTALLER                   ║
echo ║  RTX 3050 Optimized | 100% Free | Local-First AI Assistant      ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.

:: Check if running as administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [WARNING] Please run as Administrator for best results
    echo Continuing anyway...
    echo.
)

:: Create installation directory
set "INSTALL_DIR=%USERPROFILE%\hermus-jarvis"
if not exist "%INSTALL_DIR%" (
    mkdir "%INSTALL_DIR%"
    echo [✓] Created installation directory: %INSTALL_DIR%
) else (
    echo [i] Using existing directory: %INSTALL_DIR%
)

:: Check Python
python --version >nul 2>&1
if %errorLevel% neq 0 (
    echo [✗] Python not found. Installing Python 3.11...
    echo This will open the Python installer. Please check "Add Python to PATH"
    echo.
    start "" https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe
    echo After installation, run this script again.
    pause
    exit /b 1
)

for /f "tokens=2 delims=: " %%V in ('python --version 2^>^&1') do set "PYTHON_VERSION=%%V"
echo [✓] Python %PYTHON_VERSION% detected

:: Check Python version
for /f "tokens=1,2,3 delims=. " %%A in ('echo %PYTHON_VERSION%') do (
    set MAJOR=%%A
    set MINOR=%%B
)
if %MAJOR% LSS 3 (
    echo [✗] Python 3.x required. Found Python %MAJOR%
    pause
    exit /b 1
)
if %MINOR% LSS 10 (
    echo [!] Warning: Python 3.10+ recommended for best performance
)

:: Install system dependencies
where git >nul 2>&1
if %errorLevel% neq 0 (
    echo [✓] Installing Git...
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
) else (
    echo [✓] Git already installed
)

where curl >nul 2>&1
if %errorLevel% neq 0 (
    echo [✓] Installing cURL...
    winget install --id curl.curl -e --source winget --accept-package-agreements --accept-source-agreements
) else (
    echo [✓] cURL already installed
)

:: Clone HERMUS repository
echo.
echo [✓] Cloning HERMUS JARVIS repository...
cd /d "%INSTALL_DIR%"
if exist "hermus-agent-free" (
    echo [i] Repository already exists. Pulling latest changes...
    cd hermus-agent-free
    git pull origin main
) else (
    git clone https://github.com/trmv2007-bot/hermus-agent-free.git
    cd hermus-agent-free
)
echo [✓] Repository ready

:: Install Ollama for local models (RTX 3050 optimized)
echo.
echo [✓] Installing Ollama for local AI models...
where ollama >nul 2>&1
if %errorLevel% neq 0 (
    curl -fsSL https://ollama.com/install.sh | sh
    echo [✓] Ollama installed
    echo [✓] Pulling mistral:7b (RTX 3050 optimized, ~4.5GB)...
    ollama pull mistral:7b
    echo [✓] Model ready
) else (
    echo [✓] Ollama already installed
    where ollama >nul 2>&1 && ollama pull mistral:7b
)

:: Create Python virtual environment
echo.
echo [✓] Setting up Python environment...
python -m venv venv
call venv\Scripts\activate

:: Install Python dependencies
echo [✓] Installing Python dependencies...
pip install --upgrade pip
pip install -e .

:: Configure RTX 3050 settings
echo.
echo [✓] Configuring for RTX 3050 (8GB VRAM, 7GB safe limit)...
setx HERMUS_VRAM_LIMIT 7
setx HERMUS_GPU_MEMORY 7GB
setx HERMUS_RECOMMENDED_MODEL "mistral:7b"

:: Create startup script
echo.
echo [✓] Creating startup script...
(
    echo @echo off
    echo chcp 65001 > nul
    echo cd /d "%INSTALL_DIR%\hermus-agent-free"
    echo call venv\Scripts\activate
    echo python -m gateway.gateway
) > "%USERPROFILE%\Desktop\Start HERMUS JARVIS.bat"

:: Start HERMUS JARVIS
echo.
echo ╔════════════════════════════════════════════════════════════════╗
echo ║  ✅ HERMUS JARVIS INSTALLATION COMPLETE!                     ║
echo ╚════════════════════════════════════════════════════════════════╝
echo.
echo Installation Directory: %INSTALL_DIR%\hermus-agent-free
echo.
echo To start HERMUS JARVIS:
echo   1. Double-click "Start HERMUS JARVIS" on your Desktop
   2. Or run: cd %INSTALL_DIR%\hermus-agent-free && venv\Scripts\activate && python -m gateway.gateway
echo.
echo Access the Control Room at: http://localhost:8000/control
echo.
echo RTX 3050 Configuration:
echo   - VRAM Safe Limit: 7GB (out of 8GB)
echo   - Recommended Model: mistral:7b
   - Quantization: 4bit
echo   - Context Window: 4096
   - Streaming: Enabled
echo.
echo Features:
echo   ✓ JARVIS Control Room with animated orb
echo   ✓ Spacegrid particle background
echo   ✓ Live VRAM monitor
echo   ✓ Multi-agent system (10 keys/provider max)
echo   ✓ Agent-to-agent communication
echo   ✓ Voice chat (type or speak)
echo   ✓ 100% Free & Local-First
echo.
pause
