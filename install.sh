#!/usr/bin/env bash
# dime installer — see https://github.com/MwauraJames/Dime-terminal-agent
set -u
set -o pipefail

REPO_URL="https://github.com/MwauraJames/Dime-terminal-agent.git"

# ------------------------------------------------------------------
# Friendly error trap — if anything below fails unexpectedly, explain
# what happened instead of dumping a raw bash error and stopping dead.
# ------------------------------------------------------------------
on_error() {
    local line="$1"
    echo ""
    echo "❌ Installation stopped (line $line)."
    echo ""
    echo "This usually means one of:"
    echo "  • No internet connection right now"
    echo "  • git isn't installed (dime is installed via 'uv tool install git+...')"
    echo "  • GitHub or astral.sh couldn't be reached from this network"
    echo ""
    echo "You can install manually instead — see the README:"
    echo "  git clone ${REPO_URL}"
    echo "  cd Dime-terminal-agent && uv sync"
    echo ""
    echo "If this keeps happening, please open an issue:"
    echo "  https://github.com/MwauraJames/Dime-terminal-agent/issues"
    exit 1
}
trap 'on_error $LINENO' ERR

echo "🪙 Installing dime - Terminal Debugging Assistant..."
echo ""

# ------------------------------------------------------------------
# 0. Platform check — this installer targets macOS/Linux with bash.
# ------------------------------------------------------------------
OS_NAME="$(uname -s 2>/dev/null || echo unknown)"
case "$OS_NAME" in
    Linux|Darwin) ;;
    *)
        echo "⚠️  This installer supports macOS and Linux (detected: $OS_NAME)."
        echo "   On Windows, run it inside WSL, or install manually — see the README:"
        echo "   ${REPO_URL}"
        exit 1
        ;;
esac

# ------------------------------------------------------------------
# 1. Make sure curl and git are available before doing anything else.
# ------------------------------------------------------------------
if ! command -v curl &> /dev/null; then
    echo "❌ 'curl' isn't installed, but the installer needs it to fetch dime and uv."
    echo "   Install it first, e.g.:"
    echo "     Debian/Ubuntu:  sudo apt install curl"
    echo "     macOS:          brew install curl"
    exit 1
fi

if ! command -v git &> /dev/null; then
    echo "❌ 'git' isn't installed, but dime is installed straight from its GitHub repo."
    echo "   Install it first, e.g.:"
    echo "     Debian/Ubuntu:  sudo apt install git"
    echo "     macOS:          brew install git  (or install Xcode Command Line Tools)"
    exit 1
fi

# ------------------------------------------------------------------
# 2. Check if uv is installed. If not, install it.
# ------------------------------------------------------------------
if ! command -v uv &> /dev/null; then
    echo "Installing 'uv' package manager..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        echo ""
        echo "❌ Couldn't download/install 'uv' (astral.sh unreachable or the install script failed)."
        echo "   Check your internet connection and try again, or install uv manually:"
        echo "   https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi

    # Pick up uv on PATH for the rest of this script, whichever way it installed.
    export PATH="$HOME/.local/bin:$PATH"
    source "$HOME/.local/bin/env" 2>/dev/null || true

    if ! command -v uv &> /dev/null; then
        echo ""
        echo "❌ uv finished installing but isn't on PATH yet in this shell."
        echo '   Open a new terminal (or run: export PATH="$HOME/.local/bin:$PATH") and re-run this installer.'
        exit 1
    fi
fi

# ------------------------------------------------------------------
# 3. Install dime directly from the GitHub repo.
# ------------------------------------------------------------------
echo "Installing dime..."
if ! uv tool install "git+${REPO_URL}" --force; then
    echo ""
    echo "❌ Couldn't install dime from ${REPO_URL}."
    echo "   This is usually a network issue or a temporary GitHub outage — try again in a moment."
    echo "   You can also install from source instead:"
    echo "     git clone ${REPO_URL}"
    echo "     cd Dime-terminal-agent && uv sync"
    exit 1
fi

# ------------------------------------------------------------------
# 4. Automatically add ~/.local/bin to PATH if missing.
# ------------------------------------------------------------------
RC_FILE=""
if [ -n "${ZSH_VERSION:-}" ] || [ -f "$HOME/.zshrc" ]; then
    RC_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    RC_FILE="$HOME/.bashrc"
fi

PATH_NOTE=""
if [ -n "$RC_FILE" ]; then
    touch "$RC_FILE" 2>/dev/null || true
    if [ -w "$RC_FILE" ] && ! grep -q '\$HOME/.local/bin' "$RC_FILE" 2>/dev/null; then
        printf '\n# Added by dime installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC_FILE"
        echo "✅ Added ~/.local/bin to $RC_FILE"
        PATH_NOTE="    source $RC_FILE"
    fi
else
    echo "⚠️  Couldn't find a ~/.zshrc or ~/.bashrc to update automatically."
    echo "   Add this line to your shell's startup file yourself:"
    echo '   export PATH="$HOME/.local/bin:$PATH"'
fi

# ------------------------------------------------------------------
# 5. Verify the install actually worked before declaring victory.
# ------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v dime &> /dev/null; then
    echo ""
    echo "⚠️  dime installed, but isn't runnable yet in this shell session."
    echo "   Make sure ~/.local/bin is on your PATH, then open a new terminal."
    exit 1
fi

echo ""
echo "✅ dime installed successfully!"
if [ -n "$PATH_NOTE" ]; then
    echo "⚠️  To start using dime immediately in this session, run:"
    echo "$PATH_NOTE"
fi
echo ""
echo "🔑 Before running dime, set your Groq API key:"
echo '    export GROQ_API_KEY="gsk_..."'
echo "   (get one for free at https://console.groq.com/keys, and add that export"
echo "    line to your shell's startup file so it persists across sessions)"
echo ""
echo "Run 'dime --help' to get started."