# dime 🪙

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Powered by Groq](https://img.shields.io/badge/powered%20by-Groq-orange.svg)

> An intelligent, context-aware terminal debugging agent powered by `openai/gpt-oss-120b` on Groq.

`dime` acts as an interactive debugging copilot right in your shell. Paste a stack trace, pipe in failing build logs, or use auto-context slash commands to inspect Docker, Kubernetes, Git, or configuration files — then inspect, edit, or execute fixes with a single keystroke.

---

## ✨ Features

- **Blazing Fast Inference:** Powered by Groq's LPU inference with `openai/gpt-oss-120b`, featuring native reasoning and autonomous web search.
- **Interactive Execution Loop:** Proposes remediations that can be run (`y`), edited inline (`e`), or copied (`c`) without leaving the terminal.
- **Blast-Radius Safety Guardrails:** Intercepts potentially destructive commands (`rm -rf`, `mkfs`, `drop database`, `kubectl delete ns`) and enforces explicit uppercase confirmation (`YES`).
- **Standard Input Piping:** Seamlessly accept piped logs: `cat build.log | dime` or `npm test 2>&1 | dime`.
- **Zero-Paste Context Commands:**
  - `/git` — Injects `git status` and `git diff --stat`.
  - `/docker` — Grabs active containers and tail logs of the last exited container.
  - `/k8s` — Pulls failing pod statuses and recent cluster events.
  - `/last` — Fetches the last executed command from your shell history.
  - `/read <file>` — Safely loads local scripts or manifests into context.
- **Automated Privacy Redaction:** Strips API keys (Groq, OpenAI, GitHub, AWS), bearer tokens, and private keys before payloads leave your machine.
- **Session State Persistence:** Pick up right where you left off with `dime -r`.

---

## 🚀 Quickstart

### Prerequisites

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) package manager
- A [Groq API Key](https://console.groq.com/keys)

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/JameZMw/dime-terminal-agent.git
   cd dime-terminal-agent
   ```

2. **Install dependencies**

   ```bash
   uv sync
   ```

3. **Export your API key**

   ```bash
   export GROQ_API_KEY="gsk_..."
   ```

   Add this line to your `~/.zshrc` or `~/.bashrc` to persist it across sessions.

4. **Install globally to `~/.local/bin`**

   ```bash
   mkdir -p ~/.local/bin
   cat << 'EOF' > ~/.local/bin/dime
   #!/usr/bin/env bash
   set -e
   APP_DIR="$HOME/path/to/dime-terminal-agent"
   exec "$APP_DIR/.venv/bin/python" "$APP_DIR/dime.py" "$@"
   EOF
   chmod +x ~/.local/bin/dime
   ```

   Replace `APP_DIR` with wherever you cloned the repo, and make sure `~/.local/bin` is on your `PATH`.

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
| `/docker` | Inject `docker ps` and the last exited container's logs |
| `/k8s` | Inject recent pod status and cluster events |
| `/think` | Toggle visibility of the AI's internal reasoning |
| `/effort <level>` | Set reasoning effort (`low`, `medium`, `high`) |
| `exit`, `quit` | Close the session (auto-copies the last suggestion to clipboard) |

### Execution actions (after a fix is suggested)

| Key | Action |
|---|---|
| `y` | Run — execute the command directly in your shell |
| `e` | Edit — load the command into your prompt to tweak flags before running |
| `c` | Copy — copy the exact command string to clipboard |
| `Enter` | Skip execution and return to chat |

> ⚠️ Dangerous commands (`rm -rf`, `drop database`, `kubectl delete ns`, etc.) are intercepted and require typing `YES` in full to run.

### More examples

```bash
dime                          # Start a fresh interactive session
dime -r                       # Resume your previous session state
dime -t -d "untar a file"     # Direct one-off query with thinking visible
dime -d /last                 # Directly evaluate the command you just ran and exit
dime -d /read main.py "fix"   # Evaluate a file and exit immediately
cat error.log | dime          # Pipe logs directly into dime for analysis
```

---

## 🔒 Privacy & Safety

- **Redaction first:** every payload is scrubbed for Groq/OpenAI/GitHub/AWS keys, bearer tokens, and PEM private keys before it's sent to the model.
- **Blast-radius checks:** destructive shell/SQL/Kubernetes patterns are flagged and require explicit uppercase `YES` confirmation before execution.
- **Local session cache:** conversation history is cached at `~/.cache/dime/last_session.json` so `-r` can resume it — delete this file to clear saved context.

---

## 📦 Dependencies

- [`groq`](https://pypi.org/project/groq/) — Groq API client
- [`prompt_toolkit`](https://pypi.org/project/prompt-toolkit/) — interactive prompt/session handling
- [`rich`](https://pypi.org/project/rich/) — terminal rendering (panels, syntax highlighting, Markdown)
- [`pyperclip`](https://pypi.org/project/pyperclip/) — optional, enables the clipboard **Copy** action

Make sure these are declared in your `pyproject.toml` so `uv sync` installs them.

---

## 🤝 Contributing

Issues and pull requests are welcome. If you're proposing a new auto-context command (e.g. `/npm`, `/aws`) or a new safety pattern, please include a short rationale and, where relevant, a test case.

## 📄 License

Released under the [MIT License](LICENSE).