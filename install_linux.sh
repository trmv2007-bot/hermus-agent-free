#!/bin/bash

# HERMUS JARVIS - ONE-SHOT LINUX INSTALLER
# RTX 3050 Optimized | 100% Free | Local-First AI Assistant
# ============================================================

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
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

install_package() {
    echo -e "${BLUE}[→]${NC} Installing $1..."
    
    # Try apt first (Debian/Ubuntu)
    if command -v apt &> /dev/null; then
        sudo apt update > /dev/null 2>&1
        if sudo apt install -y "$1" 2>/dev/null; then
            echo -e "${GREEN}[✓]${NC} $1 installed via apt"
            return 0
        fi
    fi
    
    # Try dnf (Fedora/RHEL)
    if command -v dnf &> /dev/null; then
        if sudo dnf install -y "$1" 2>/dev/null; then
            echo -e "${GREEN}[✓]${NC} $1 installed via dnf"
            return 0
        fi
    fi
    
    # Try pacman (Arch)
    if command -v pacman &> /dev/null; then
        if sudo pacman -S --noconfirm "$1" 2>/dev/null; then
            echo -e "${GREEN}[✓]${NC} $1 installed via pacman"
            return 0
        fi
    fi
    
    # Try zypper (openSUSE)
    if command -v zypper &> /dev/null; then
        if sudo zypper install -y "$1" 2>/dev/null; then
            echo -e "${GREEN}[✓]${NC} $1 installed via zypper"
            return 0
        fi
    fi
    
    echo -e "${RED}[✗]${NC} Failed to install $1"
    return 1
}

# Check if running as root
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}[✗]${NC} Please do NOT run as root. Run as regular user with sudo privileges."
    exit 1
fi

# Detect distribution
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS="$NAME"
    VER="$VERSION_ID"
else
    OS="Unknown"
    VER="Unknown"
fi

# Banner
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  HERMUS JARVIS - ONE-SHOT LINUX INSTALLER                      ║"
echo "║  RTX 3050 Optimized | 100% Free | Local-First AI Assistant      ║"
echo "║  Distribution: $OS $VER                                          ║"
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

# Install system dependencies
echo -e "${CYAN}=== Installing System Dependencies ===${NC}"

# Git
check_command git || install_package git

# cURL
check_command curl || install_package curl

# wget
check_command wget || install_package wget

# Python 3 and pip
if ! check_command python3; then
    echo -e "${BLUE}[→]${NC} Installing Python 3..."
    install_package python3
    install_package python3-pip
    install_package python3-venv
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

# Additional development tools
check_command gcc || install_package gcc
check_command g++ || install_package g++
check_command make || install_package make

# For NVIDIA GPU support (RTX 3050)
if lspci | grep -i nvidia &> /dev/null; then
    echo -e "${CYAN}=== NVIDIA GPU Detected ===${NC}"
    
    # Check for NVIDIA drivers
    if ! nvidia-smi &> /dev/null; then
        echo -e "${YELLOW}[!]${NC} NVIDIA drivers not detected"
        echo "Please install NVIDIA drivers for your RTX 3050:"
        echo "  Ubuntu/Debian: sudo apt install nvidia-driver-535"
        echo "  Fedora: sudo dnf install akmod-nvidia"
        echo "  Arch: sudo pacman -S nvidia nvidia-utils"
        echo
        read -p "Continue without GPU acceleration? [Y/n] " -n 1 -r
        if [[ ! $REPLY =~ ^[Yy]$ ]] && [[ -n $REPLY ]]; then
            exit 1
        fi
        echo
    else
        echo -e "${GREEN}[✓]${NC} NVIDIA drivers detected"
        nvidia-smi | head -n 1
    fi
    
    # Install CUDA (optional but recommended for RTX 3050)
    if ! nvcc --version &> /dev/null; then
        echo -e "${YELLOW}[!]${NC} CUDA not detected (optional for GPU acceleration)"
        echo "Install CUDA 12.x for best RTX 3050 performance:"
        echo "  https://developer.nvidia.com/cuda-downloads"
        echo
    else
        echo -e "${GREEN}[✓]${NC} CUDA detected"
    fi
fi

# Clone HERMUS repository
echo
echo -e "${CYAN}=== Cloning HERMUS JARVIS ===${NC}"
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
echo -e "${CYAN}=== Installing Ollama ===${NC}"
if ! check_command ollama; then
    curl -fsSL https://ollama.com/install.sh | sh
    check_command ollama || exit 1
    echo -e "${GREEN}[✓]${NC} Ollama installed"
    
    echo -e "${BLUE}[→]${NC} Pulling mistral:7b (RTX 3050 optimized, ~4.5GB)..."
    echo "This may take a few minutes depending on your internet speed..."
    ollama pull mistral:7b
    echo -e "${GREEN}[✓]${NC} Model ready"
else
    echo -e "${GREEN}[✓]${NC} Ollama already installed"
    ollama pull mistral:7b 2>/dev/null
fi

# Create Python virtual environment
echo
echo -e "${CYAN}=== Setting Up Python Environment ===${NC}"
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo -e "${BLUE}[→]${NC} Installing Python dependencies..."
pip install --upgrade pip
pip install -e .

# Configure RTX 3050 settings
echo
echo -e "${CYAN}=== Configuring RTX 3050 Settings ===${NC}"

# Add to bashrc
if [ -f "$HOME/.bashrc" ]; then
    if ! grep -q "HERMUS_VRAM_LIMIT" "$HOME/.bashrc"; then
        echo "" >> "$HOME/.bashrc"
        echo "# HERMUS JARVIS Configuration" >> "$HOME/.bashrc"
        echo "export HERMUS_VRAM_LIMIT=7" >> "$HOME/.bashrc"
        echo "export HERMUS_GPU_MEMORY=7GB" >> "$HOME/.bashrc"
        echo "export HERMUS_RECOMMENDED_MODEL='mistral:7b'" >> "$HOME/.bashrc"
        echo -e "${GREEN}[✓]${NC} Added configuration to ~/.bashrc"
    fi
fi

# Add to zsh config
if [ -f "$HOME/.zshrc" ]; then
    if ! grep -q "HERMUS_VRAM_LIMIT" "$HOME/.zshrc"; then
        echo "" >> "$HOME/.zshrc"
        echo "# HERMUS JARVIS Configuration" >> "$HOME/.zshrc"
        echo "export HERMUS_VRAM_LIMIT=7" >> "$HOME/.zshrc"
        echo "export HERMUS_GPU_MEMORY=7GB" >> "$HOME/.zshrc"
        echo "export HERMUS_RECOMMENDED_MODEL='mistral:7b'" >> "$HOME/.zshrc"
        echo -e "${GREEN}[✓]${NC} Added configuration to ~/.zshrc"
    fi
fi

# Create startup scripts
echo
echo -e "${CYAN}=== Creating Startup Scripts ===${NC}"

# Desktop shortcut (for GNOME/KDE/XFCE)
if [ -d "$HOME/Desktop" ]; then
    cat > "$HOME/Desktop/Start HERMUS JARVIS.desktop" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=HERMUS JARVIS
Comment=Local-First AI Assistant
Exec=bash -c "cd $INSTALL_DIR/hermus-agent-free && source venv/bin/activate && python3 -m gateway.gateway"
Icon=utilities-terminal
Terminal=true
Categories=Development;AI;
Path=$INSTALL_DIR/hermus-agent-free
EOF
    chmod +x "$HOME/Desktop/Start HERMUS JARVIS.desktop"
    echo -e "${GREEN}[✓]${NC} Created desktop shortcut"
fi

# Launch script
cat > "$INSTALL_DIR/hermus-agent-free/start.sh" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python3 -m gateway.gateway
EOF
chmod +x "$INSTALL_DIR/hermus-agent-free/start.sh"
echo -e "${GREEN}[✓]${NC} Created launch script"

# Create systemd service (optional)
read -p "Create systemd service for auto-start? [Y/n] " -n 1 -r
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    echo
echo -e "${BLUE}[→]${NC} Creating systemd service..."
    
    cat > /tmp/hermus-jarvis.service << EOF
[Unit]
Description=HERMUS JARVIS AI Assistant
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR/hermus-agent-free
Environment="PATH=$INSTALL_DIR/hermus-agent-free/venv/bin:$PATH"
ExecStart=$INSTALL_DIR/hermus-agent-free/venv/bin/python3 -m gateway.gateway
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    sudo mv /tmp/hermus-jarvis.service /etc/systemd/system/hermus-jarvis.service
    sudo systemctl daemon-reload
    sudo systemctl enable hermus-jarvis.service
    echo -e "${GREEN}[✓]${NC} Systemd service created"
    echo "To start: sudo systemctl start hermus-jarvis"
    echo "To stop: sudo systemctl stop hermus-jarvis"
    echo "To check status: sudo systemctl status hermus-jarvis"
fi

# Completion banner
echo
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  ✅ HERMUS JARVIS INSTALLATION COMPLETE!                     ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo
echo "Installation Directory: $INSTALL_DIR/hermus-agent-free"
echo
echo "To start HERMUS JARVIS:"
echo "  1. Run: cd $INSTALL_DIR/hermus-agent-free && source venv/bin/activate && python3 -m gateway.gateway"
echo "  2. Or use the desktop shortcut (if created)"
echo "  3. Or use: $INSTALL_DIR/hermus-agent-free/start.sh"
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
echo "Environment Variables Set:"
echo "  HERMUS_VRAM_LIMIT=7"
echo "  HERMUS_GPU_MEMORY=7GB"
echo "  HERMUS_RECOMMENDED_MODEL=mistral:7b"
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
