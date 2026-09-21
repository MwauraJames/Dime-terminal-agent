# dime 🪙

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)
![Models](https://img.shields.io/badge/models-Groq%20%7C%20Anthropic%20%7C%20Gemini%20%7C%20OpenAI%20%7C%20Ollama-orange.svg)

> An intelligent, context-aware terminal debugging agent. Bring your own model: Groq, Anthropic, Gemini, OpenAI, or a local Ollama model.

`dime` acts as an interactive debugging copilot right in your shell. Paste a stack trace, pipe in failing build logs, or use auto-context slash commands to inspect Docker, Kubernetes, Git, or configuration files — then inspect, edit, or execute fixes with a single keystroke.

---

## ✨ Features

- **Bring Your Own Model:** Use your own API key with Groq (default: `openai/gpt-oss-120b`, with native reasoning and autonomous web search), Anthropic, Gemini, or OpenAI — or run fully local with Ollama. Switch any time with `--model` or `/model`.
- **Linux, macOS, Windows 11:** Runs natively in PowerShell on Windows, and in WSL like any Linux shell. Suggested commands match the shell they will run in.
- **Interactive Execution Loop:** Proposes remediations that can be run (`y`), edited inline (`e`), or copied (`c`) without leaving the terminal.
- **Blast-Radius Safety Guardrails:** Intercepts potentially destructive commands (`rm -rf`, `mkfs`, `drop database`, `kubectl delete ns`, and on Windows `Remove-Item -Recurse`, `rd /s`, `Format-Volume`, `reg delete`, …) and enforces explicit uppercase confirmation (`YES`).
- **Standard Input Piping:** Seamlessly accept piped logs: `cat build.log | dime` or `npm test 2>&1 | dime`.
- **Zero-Paste Context Commands:**
  - `/git` — Injects `git status` and `git diff --stat`.
  - `/docker` — Grabs active containers and tail logs of the most recently created container.
  - `/k8s` — Pulls pod status (completed pods hidden) and recent cluster events.
  - `/last` — Fetches the last executed command from your shell history.
  - `/read <file>` — Safely loads local scripts or manifests into context.
- **Automated Privacy Redaction:** Strips API keys (Groq, OpenAI, Anthropic, Google, GitHub, AWS, LangSmith), bearer tokens, password/secret assignments, and private keys before payloads leave your machine.
- **Optional LangSmith Tracing:** Set a LangSmith key and every request is traced (redacted first) — no LangChain required.
- **Session State Persistence:** Pick up right where you left off with `dime -r`.

---

## 🚀 Quickstart

### Prerequisites

- **An API key for one model provider** (Groq has a free tier: [console.groq.com/keys](https://console.groq.com/keys)) — *or* a local [Ollama](https://ollama.com) install, which needs no key.
- `git` (dime is installed straight from this repo), plus:
  - **macOS / Linux / WSL:** `curl` and `bash`
  - **Windows 11:** PowerShell

### Quick Install (recommended)

**macOS / Linux / WSL**

```bash
curl -fsSL https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/HEAD/install.sh | bash
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/HEAD/install.ps1 | iex
```

Both install [`uv`](https://github.com/astral-sh/uv) if it isn't already on your system, then install `dime` globally via `uv tool install`.

Then set the API key for the provider you want to use (see [Choosing a model](#-choosing-a-model)):

```bash
# macOS / Linux / WSL
export GROQ_API_KEY="gsk_..."
```

```powershell
# Windows (persists for new terminals)
setx GROQ_API_KEY "gsk_..."
```

On macOS/Linux, add the `export` line to your `~/.zshrc` or `~/.bashrc` so it persists, and make sure `~/.local/bin` is on your `PATH`. Then run `dime --help` to confirm it installed correctly.

### Manual Install (from source)

Prefer to run from a local clone — for example, to modify the code:

1. **Clone the repository**

   ```bash
   git clone https://github.com/MwauraJames/Dime-terminal-agent.git
   cd Dime-terminal-agent
   ```

2. **Install dependencies**

   ```bash
   uv sync
   ```

3. **Export your API key** (see [Choosing a model](#-choosing-a-model))

   ```bash
   export GROQ_API_KEY="gsk_..."
   ```

4. **Run it**

   ```bash
   uv run dime
   ```

   Or install it globally from your clone, so the `dime` command picks up your edits as you make them:

   ```bash
   uv tool install --editable .
   ```

---

## 🧠 Choosing a model

dime uses **your** API key and talks to the provider directly. Set the key for the provider you want:

| Provider | Environment variable | Example `--model` |
|---|---|---|
| Groq *(default)* | `GROQ_API_KEY` | `groq/openai/gpt-oss-120b` |
| Anthropic | `ANTHROPIC_API_KEY` | `anthropic/claude-sonnet-5` |
| Google Gemini | `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) | `gemini/gemini-2.5-flash` |
| OpenAI | `OPENAI_API_KEY` | `openai/gpt-5-mini` |
| Ollama (local) | none | `ollama_chat/llama3.1` |

```bash
dime --model anthropic/claude-sonnet-5      # pick a model for this run
export DIME_MODEL=gemini/gemini-2.5-flash   # or set a default
dime > /model openai/gpt-5-mini             # or switch mid-session
```

- **Which model is used:** `--model`, then `DIME_MODEL`, then the first provider whose key is set (Groq → Anthropic → Gemini → OpenAI).
- **Any model works:** dime accepts any [LiteLLM](https://docs.litellm.ai/docs/providers) model string. Bare names like `claude-sonnet-5`, `gemini-2.5-flash` and `gpt-5-mini` are filled in for you.
- **Feature differences:** autonomous web search is available on Groq's `gpt-oss` models only, and `/effort` applies to Groq `gpt-oss` and OpenAI `gpt-5`/o-series models. Everything else works with every model.
- **Ollama:** start it with `ollama serve` and pull a model first (`ollama pull llama3.1`). Point dime at a remote server with `OLLAMA_API_BASE`. dime asks Ollama for an 8192-token context window so long logs aren't silently truncated; change it with `DIME_NUM_CTX`.

### LangSmith tracing (optional)

```bash
export LANGSMITH_API_KEY="lsv2_..."
export LANGSMITH_PROJECT="dime"      # optional, defaults to "dime"
```

With a LangSmith key in your environment, dime traces every request to that project and says so at startup. Prompts are redacted before they are sent (see [Privacy & Safety](#-privacy--safety)). Use `--no-trace` (or `DIME_TRACE=0`) to switch it off for a run. The older `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` and `LANGCHAIN_ENDPOINT` names are accepted too; for EU workspaces set `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`.

### Windows notes

- **Native Windows:** suggested commands run in PowerShell (PowerShell 7 `pwsh` if installed, otherwise Windows PowerShell 5.1), and the model is told which one, so it suggests cmdlets rather than `ls`/`grep`. `/last` reads your PSReadLine history. Pipe logs with `Get-Content error.log | dime`. `cmd.exe` keeps no persistent history.
- **WSL:** behaves like Linux. The model knows `/mnt/c/` is your Windows drive, and the clipboard Copy action falls back to `clip.exe`.

---

## 🛠 Usage

### 1. Interactive session

```bash
dime
```

Inside the session, paste error logs or use slash commands:

```
dime > /read docker-compose.yml why is the redis service failing?
dime > /git why is my branch diverged?
dime > /effort high
```

### 2. Direct one-off queries

```bash
dime -d how do I extract a tar.xz archive to /opt?
```

### 3. Stdin piping

```bash
docker logs my-api 2>&1 | dime -d "Why is this crashing on startup?"
```

### Interactive slash commands

| Command | Description |
|---|---|
| `/read <file> [query]` | Inject a local file into context (e.g. `/read compose.yml why is it failing?`) |
| `/last` | Fetch the last executed shell command and evaluate it |
| `/git` | Inject `git status` and `git diff` into context |
| `/docker` | Inject `docker ps` and the most recent container's logs |
| `/k8s` | Inject recent pod status and cluster events |
| `/model [name]` | Show the current model, or switch to another one mid-session |
| `/think` | Toggle visibility of the AI's internal reasoning |
| `/effort <level>` | Set reasoning effort (`low`, `medium`, `high`) |
| `exit`, `quit` | Close the session |

### Execution actions (after a fix is suggested)

| Key | Action |
|---|---|
| `y` | Run — execute the command directly in your shell |
| `e` | Edit — load the command into your prompt to tweak flags before running |
| `c` | Copy — copy the exact command string to clipboard |
| `Enter` | Skip execution and return to chat |

> ⚠️ Dangerous commands (`rm -rf`, `drop database`, `kubectl delete ns`, `Remove-Item -Recurse`, etc.) are intercepted and require typing `YES` in full to run.

### More examples

```bash
dime                                  # Start a fresh interactive session
dime -r                               # Resume your previous session state
dime -t -d "untar a file"             # Direct one-off query with thinking visible
dime -m ollama_chat/llama3.1          # Use a local model
dime -d /last                         # Directly evaluate the command you just ran and exit
dime -d /read main.py "fix"           # Evaluate a file and exit immediately
cat error.log | dime                  # Pipe logs directly into dime for analysis
```

### Environment variables

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY` | Provider API keys (set the one you use) |
| `DIME_MODEL` | Default model, e.g. `anthropic/claude-sonnet-5` |
| `OLLAMA_API_BASE`, `DIME_NUM_CTX` | Ollama server address / context window (default 8192) |
| `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Enable LangSmith tracing |
| `DIME_TRACE=0` | Disable tracing even if a LangSmith key is set |
| `DIME_DEBUG=1` | Show the full Python traceback if dime crashes |

---

## 🔒 Privacy & Safety

- **Your provider sees your prompts.** What you paste, plus recent shell history and directory/git context, goes to whichever model provider you choose. To keep everything on your machine, use Ollama.
- **Redaction first:** every payload is scrubbed for Groq/OpenAI/Anthropic/Google/GitHub/AWS/LangSmith keys, bearer tokens, password/secret assignments, and PEM private keys before it is sent. This is pattern-based and best-effort — it can't catch every secret, so glance at what you paste.
- **Tracing is switched on by your key:** with `LANGSMITH_API_KEY` set, the same redacted prompts and the model's replies are also sent to LangSmith. `--no-trace` or `DIME_TRACE=0` turns that off.
- **Blast-radius checks:** destructive shell/SQL/Kubernetes/PowerShell patterns are flagged and require explicit uppercase `YES` confirmation before execution. These are pattern-based safeguards, not a sandbox; read commands before you run them.
- **Local session cache:** conversation history is cached at `~/.cache/dime/last_session.json` (in your user folder on Windows) so `-r` can resume it — delete this file to clear saved context.
- **No raw crashes:** missing API keys, network drops, rate limits, bad file paths, and interrupted commands all surface as a clear, specific message instead of a Python traceback. Set `DIME_DEBUG=1` before running if you ever need the full traceback for a bug report.

---

## 📦 Dependencies

- [`litellm`](https://pypi.org/project/litellm/) — one interface to every model provider (pinned to an exact version on purpose)
- [`prompt_toolkit`](https://pypi.org/project/prompt-toolkit/) — interactive prompt/session handling
- [`rich`](https://pypi.org/project/rich/) — terminal rendering (panels, syntax highlighting, Markdown)
- [`pyperclip`](https://pypi.org/project/pyperclip/) — enables the clipboard **Copy** action

---

## 🧱 Project layout

```
dime/
├── cli.py        # argument parsing, prompt loop, slash-command dispatch
├── llm.py        # model selection (LiteLLM), streaming, tracing, system prompt
├── executor.py   # running commands, clipboard, the [y/e/c] action prompt
├── context.py    # shell history, system info, /git /docker /k8s /last /read
├── security.py   # secret redaction and destructive-command patterns
├── session.py    # saving/loading the conversation
├── osinfo.py     # Windows / WSL / POSIX detection and shell choice
└── ui.py         # shared Rich console
tests/            # pytest suite (no network or API keys needed)
```

Run the tests with `uv run pytest`.

---

## 🤝 Contributing

Issues and pull requests are welcome. If you're proposing a new auto-context command (e.g. `/npm`, `/aws`) or a new safety pattern, please include a short rationale and, where relevant, a test case. New slash commands go in `dime/context.py`; safety patterns go in `dime/security.py`.

`litellm` is pinned deliberately (LiteLLM releases 1.82.7 and 1.82.8 were compromised on PyPI in March 2026). Please don't loosen the pin in a PR; bump it as its own change.

## 📄 License

Released under the [MIT License](LICENSE).