#!/bin/bash

# HERMUS JARVIS - ONE-SHOT MACOS INSTALLER
# RTX 3050 Optimized | 100% Free | Local-First AI Assistant
# ============================================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
check_command() {
    if command -v "$1" &> /dev/null; then
        echo -e "${GREEN}[✓]${NC} $1 already installed"
        return 0
    else
        echo -e "${YELLOW}[!]${NC} $1 not found"
        return 1
    fi
}

install_command() {
    echo -e "${BLUE}[→]${NC} Installing $1..."
    if brew install "$1" 2>/dev/null; then
        echo -e "${GREEN}[✓]${NC} $1 installed"
        return 0
    else
        echo -e "${RED}[✗]${NC} Failed to install $1"
        return 1
    fi
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}[✗]${NC} Please do NOT run as root. Run as regular user."
    exit 1
fi

# Banner
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  HERMUS JARVIS - ONE-SHOT MACOS INSTALLER                     ║"
echo "║  RTX 3050 Optimized | 100% Free | Local-First AI Assistant      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo

# Create installation directory
INSTALL_DIR="$HOME/hermus-jarvis"
if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    echo -e "${GREEN}[✓]${NC} Created installation directory: $INSTALL_DIR"
else
    echo -e "${BLUE}[i]${NC} Using existing directory: $INSTALL_DIR"
fi

# Check Homebrew
if ! check_command brew; then
    echo -e "${BLUE}[→]${NC} Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> "$HOME/.zprofile"
    eval "$(/opt/homebrew/bin/brew shellenv)"
    check_command brew || exit 1
fi

# Install system dependencies
check_command git || install_command git
check_command curl || install_command curl
check_command wget || install_command wget

# Check Python
if ! check_command python3; then
    echo -e "${BLUE}[→]${NC} Installing Python 3.11..."
    brew install python@3.11
    check_command python3 || exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1)
echo -e "${GREEN}[✓]${NC} $PYTHON_VERSION detected"

# Verify Python version
MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ]; then
    echo -e "${RED}[✗]${NC} Python 3.x required. Found Python $MAJOR"
    exit 1
fi

if [ "$MINOR" -lt 10 ]; then
    echo -e "${YELLOW}[!]${NC} Warning: Python 3.10+ recommended for best performance"
fi

# Clone HERMUS repository
echo
echo -e "${BLUE}[→]${NC} Cloning HERMUS JARVIS repository..."
cd "$INSTALL_DIR" || exit 1

if [ -d "hermus-agent-free" ]; then
    echo -e "${BLUE}[i]${NC} Repository already exists. Pulling latest changes..."
    cd hermus-agent-free
    git pull origin main
else
    git clone https://github.com/trmv2007-bot/hermus-agent-free.git
    cd hermus-agent-free
fi
echo -e "${GREEN}[✓]${NC} Repository ready"

# Install Ollama for local models (RTX 3050 optimized)
echo
echo -e "${BLUE}[→]${NC} Installing Ollama for local AI models..."
if ! check_command ollama; then
    curl -fsSL https://ollama.com/install.sh | sh
    check_command ollama || exit 1
    echo -e "${GREEN}[✓]${NC} Ollama installed"
    echo -e "${BLUE}[→]${NC} Pulling mistral:7b (RTX 3050 optimized, ~4.5GB)..."
    ollama pull mistral:7b
    echo -e "${GREEN}[✓]${NC} Model ready"
else
    echo -e "${GREEN}[✓]${NC} Ollama already installed"
    ollama pull mistral:7b 2>/dev/null
fi

# Create Python virtual environment
echo
echo -e "${BLUE}[→]${NC} Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo -e "${BLUE}[→]${NC} Installing Python dependencies..."
pip install --upgrade pip
pip install -e .

# Configure RTX 3050 settings
echo
echo -e "${BLUE}[→]${NC} Configuring for RTX 3050 (8GB VRAM, 7GB safe limit)..."
echo "export HERMUS_VRAM_LIMIT=7" >> "$HOME/.zshrc"
echo "export HERMUS_GPU_MEMORY=7GB" >> "$HOME/.zshrc"
echo "export HERMUS_RECOMMENDED_MODEL='mistral:7b'" >> "$HOME/.zshrc"

# For bash users
if [ -f "$HOME/.bash_profile" ]; then
    echo "export HERMUS_VRAM_LIMIT=7" >> "$HOME/.bash_profile"
    echo "export HERMUS_GPU_MEMORY=7GB" >> "$HOME/.bash_profile"
    echo "export HERMUS_RECOMMENDED_MODEL='mistral:7b'" >> "$HOME/.bash_profile"
fi

# Create startup script
echo
echo -e "${BLUE}[→]${NC} Creating startup script..."
cat > "$HOME/Desktop/Start HERMUS JARVIS.command" << 'EOF'
#!/bin/bash
cd "$HOME/hermus-jarvis/hermus-agent-free"
source venv/bin/activate
python3 -m gateway.gateway
EOF
chmod +x "$HOME/Desktop/Start HERMUS JARVIS.command"

# Create launch script in installation directory
cat > "$INSTALL_DIR/hermus-agent-free/start.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python3 -m gateway.gateway
EOF
chmod +x "$INSTALL_DIR/hermus-agent-free/start.sh"

# Completion banner
echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  ✅ HERMUS JARVIS INSTALLATION COMPLETE!                     ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "Installation Directory: $INSTALL_DIR/hermus-agent-free"
echo
echo "To start HERMUS JARVIS:"
echo "  1. Double-click 'Start HERMUS JARVIS' on your Desktop"
echo "  2. Or run: cd $INSTALL_DIR/hermus-agent-free && source venv/bin/activate && python3 -m gateway.gateway"
echo
echo "Access the Control Room at: http://localhost:8000/control"
echo
echo "RTX 3050 Configuration:"
echo "  - VRAM Safe Limit: 7GB (out of 8GB)"
echo "  - Recommended Model: mistral:7b"
echo "  - Quantization: 4bit"
echo "  - Context Window: 4096"
echo "  - Streaming: Enabled"
echo
echo "Features:"
echo "  ✓ JARVIS Control Room with animated orb"
echo "  ✓ Spacegrid particle background"
echo "  ✓ Live VRAM monitor"
echo "  ✓ Multi-agent system (10 keys/provider max)"
echo "  ✓ Agent-to-agent communication"
echo "  ✓ Voice chat (type or speak)"
echo "  ✓ 100% Free & Local-First"
echo

# Start HERMUS JARVIS (optional)
read -p "Start HERMUS JARVIS now? [Y/n] " -n 1 -r
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    echo
echo -e "${BLUE}[→]${NC} Starting HERMUS JARVIS..."
    cd "$INSTALL_DIR/hermus-agent-free"
    source venv/bin/activate
    python3 -m gateway.gateway
fi
