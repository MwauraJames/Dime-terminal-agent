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
        echo "   On Windows, either run this installer inside WSL, or use the native PowerShell installer:"
        echo "     irm https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/HEAD/install.ps1 | iex"
        echo "   More options in the README: ${REPO_URL}"
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
# 4. Automatically add ~/.local/bin to PATH (for future shells), and work out whether THIS
#    shell -- the one the user is actually sitting in right now -- already has it too.
#
#    These are two different questions. Editing ~/.bashrc only affects shells started AFTER
#    this script runs; it can never change a shell that's already open. And on a stock Ubuntu
#    box, ~/.bashrc already has a stanza like `if [ -d "$HOME/.local/bin" ]; then PATH=...`
#    -- which only takes effect once that directory actually exists. The first time dime (or
#    uv itself) creates it, that stanza was already evaluated against its *absence* when this
#    terminal started, so the current shell is stuck without it regardless of what's in the
#    rc file. So: check the CURRENT shell's PATH (inherited from whatever invoked this script)
#    directly, rather than inferring it from whether we just edited a config file.
# ------------------------------------------------------------------
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ALREADY_ON_PATH=1 ;;
    *) ALREADY_ON_PATH=0 ;;
esac

RC_FILE=""
if [ -n "${ZSH_VERSION:-}" ] || [ -f "$HOME/.zshrc" ]; then
    RC_FILE="$HOME/.zshrc"
elif [ -f "$HOME/.bashrc" ]; then
    RC_FILE="$HOME/.bashrc"
fi

if [ -n "$RC_FILE" ]; then
    touch "$RC_FILE" 2>/dev/null || true
    if [ -w "$RC_FILE" ] && ! grep -q '\$HOME/.local/bin' "$RC_FILE" 2>/dev/null; then
        printf '\n# Added by dime installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC_FILE"
        echo "✅ Added ~/.local/bin to $RC_FILE (takes effect in new terminals)"
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
if [ "$ALREADY_ON_PATH" -eq 0 ]; then
    echo "⚠️  Your current terminal doesn't have ~/.local/bin on its PATH yet (new terminals will)."
    echo "   To use dime right now, in THIS session, run:"
    echo '   export PATH="$HOME/.local/bin:$PATH"'
fi
echo ""
echo "🔑 Before running dime, set an API key for the model provider you want to use:"
echo '    export GROQ_API_KEY="gsk_..."          # free key: https://console.groq.com/keys'
echo '    export ANTHROPIC_API_KEY="sk-ant-..."  # or ANTHROPIC / GEMINI / OPENAI_API_KEY'
echo "   (add that export line to your shell's startup file so it persists across sessions)"
echo "   No key? Run a local model instead:  ollama pull llama3.1  &&  dime --model ollama_chat/llama3.1"
echo ""
echo "Run 'dime --help' to get started."