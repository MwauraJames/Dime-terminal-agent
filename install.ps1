# dime installer for Windows (Windows PowerShell 5.1 or PowerShell 7+)
#
#   irm https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/HEAD/install.ps1 | iex
#
# What it does:
#   1. checks that git is available (uv installs dime straight from the GitHub repo)
#   2. installs uv if it's missing (official Astral installer: puts uv.exe in
#      %USERPROFILE%\.local\bin and adds that folder to your user PATH)
#   3. runs `uv tool install` so `dime` is available as a normal command
#
# Keep this file ASCII-only: Windows PowerShell 5.1 misreads UTF-8 without a BOM.

$RepoOwner = "MwauraJames"
$RepoName  = "Dime-terminal-agent"
$RepoSpec  = "git+https://github.com/$RepoOwner/$RepoName"

function Write-Step($Message) { Write-Host "==> $Message" -ForegroundColor Cyan }

function Add-ToSessionPath($Dir) {
    # Makes a folder usable in THIS terminal right away (the installers only edit the
    # persistent user PATH, which new terminals pick up but the current one doesn't).
    if ($Dir -and (Test-Path $Dir) -and (($env:Path -split ';') -notcontains $Dir)) {
        $env:Path = "$Dir;$env:Path"
    }
}

# Windows PowerShell 5.1 can default to old TLS versions that GitHub/Astral reject.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

# --- 1. git ------------------------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "git is required to install dime from GitHub, but it wasn't found on your PATH." -ForegroundColor Red
    Write-Host "Install it with:  winget install --id Git.Git -e" -ForegroundColor Yellow
    Write-Host "Then open a NEW terminal and re-run this installer." -ForegroundColor Yellow
    throw "git not found"
}

# --- 2. uv -------------------------------------------------------------------
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Step "uv is already installed ($(& uv --version))"
} else {
    Write-Step "Installing uv..."
    try {
        Invoke-RestMethod -Uri "https://astral.sh/uv/install.ps1" -ErrorAction Stop | Invoke-Expression
    } catch {
        Write-Host "Couldn't download/run the uv installer: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Install uv manually (https://docs.astral.sh/uv/) and re-run this script." -ForegroundColor Yellow
        throw
    }
    Add-ToSessionPath (Join-Path $env:USERPROFILE ".local\bin")
    Add-ToSessionPath (Join-Path $env:USERPROFILE ".cargo\bin")

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "uv was installed but isn't on PATH yet. Open a NEW terminal and re-run this script." -ForegroundColor Yellow
        throw "uv not on PATH"
    }
}

# --- 3. dime -----------------------------------------------------------------
Write-Step "Installing dime from github.com/$RepoOwner/$RepoName ..."
& uv tool install --force $RepoSpec
if ($LASTEXITCODE -ne 0) {
    Write-Host "uv tool install failed (exit code $LASTEXITCODE). Check the repo URL and your network connection." -ForegroundColor Red
    throw "uv tool install failed"
}

# Make sure uv's tool bin folder is on the persistent user PATH, and usable right now.
try { & uv tool update-shell *> $null } catch {}
try { Add-ToSessionPath ((& uv tool dir --bin 2>$null) | Select-Object -First 1) } catch {}

# --- done --------------------------------------------------------------------
Write-Host ""
if (Get-Command dime -ErrorAction SilentlyContinue) {
    Write-Host "dime installed successfully." -ForegroundColor Green
} else {
    Write-Host "dime installed. Open a NEW terminal so the updated PATH takes effect." -ForegroundColor Green
}
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Get an API key for the provider you want (Groq has a free tier: https://console.groq.com/keys)"
Write-Host '  2. Save it:   setx GROQ_API_KEY "gsk_..."     (or ANTHROPIC_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY; then open a new terminal)'
Write-Host "     No key? Use a local model: ollama pull llama3.1  ;  dime --model ollama_chat/llama3.1"
Write-Host "  3. Run:       dime"