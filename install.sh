#!/usr/bin/env bash
set -e

echo "🪙 Installing dime - Terminal Debugging Assistant..."

# 1. Check if uv is installed. If not, install it.
if ! command -v uv &> /dev/null; then
    echo "Installing 'uv' package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env 2>/dev/null || true
fi

# 2. Install dime directly from the github repo
echo "Installing dime..."
uv tool install git+https://github.com/MwauraJames/dime-terminal-agent.git --force

# 3. Automatically add ~/.local/bin to PATH if missing
RC_FILE=""
if [ -f "$HOME/.zshrc" ]; then
    RC_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    RC_FILE="$HOME/.bashrc"
fi

if [ -n "$RC_FILE" ]; then
    # Check if the path is already in the file to avoid duplicates
    if ! grep -q "$HOME/.local/bin" "$RC_FILE"; then
        echo -e '\n# Added by dime installer\nexport PATH="$HOME/.local/bin:$PATH"' >> "$RC_FILE"
        echo "✅ Added ~/.local/bin to $RC_FILE"
    fi
fi

echo ""
echo "✅ dime installed successfully!"
echo "⚠️  To start using dime immediately, run:"
echo "    source $RC_FILE"