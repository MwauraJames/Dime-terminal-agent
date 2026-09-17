#!/usr/bin/env bash
set -e

echo "🪙 Installing dime - Terminal Debugging Assistant..."

# 1. Check if uv is installed. If not, install it.
if ! command -v uv &> /dev/null; then
    echo "Installing 'uv' package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Temporarily source the env so we can use uv in this script
    source $HOME/.local/bin/env 2>/dev/null || true
fi

# 2. Install dime directly from the github repo
echo "Installing dime..."
uv tool install git+https://github.com/MwauraJames/dime-terminal-agent.git --force

echo ""
echo "✅ dime installed successfully!"
echo "Make sure ~/.local/bin is in your PATH."
echo "Run 'dime --help' to get started."