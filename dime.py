#!/usr/bin/env python3
import os
import sys
import subprocess
import re
import json
import argparse
from pathlib import Path

# ==========================================
# DEPENDENCY CHECK
# ==========================================
# These are third-party packages. If they're missing (e.g. someone ran
# `python dime.py` outside the installed environment), fail with a clear
# instruction instead of a raw ModuleNotFoundError traceback.
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.syntax import Syntax
    from rich.markdown import Markdown
    from groq import Groq
    import groq as groq_sdk
except ImportError as e:
    missing = getattr(e, "name", None) or str(e)
    print(f"❌ dime is missing a required package ({missing}).", file=sys.stderr)
    print("   If you're running from source: uv sync", file=sys.stderr)
    print("   Otherwise, reinstall with:", file=sys.stderr)
    print("   curl -fsSL https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/main/install.sh | bash", file=sys.stderr)
    sys.exit(1)

CACHE_DIR = Path.home() / ".cache" / "dime"
SESSION_FILE = CACHE_DIR / "last_session.json"

def save_session(messages):
    """Saves the chat history to a JSON file for persistence."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            json.dump(messages, f, indent=2)
    except Exception:
        pass  # Session cache is a convenience, never worth interrupting the user for

def load_session():
    """Loads previous chat history if it exists."""
    if SESSION_FILE.exists():
        try:
            with open(SESSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            # Corrupted or unreadable cache — just start fresh rather than erroring out
            return None
    return None

# 1. Define sensitive pattern matchers
REDACTION_PATTERNS = [
    # Groq API Keys
    (r"gsk_[a-zA-Z0-9]{40,}", "[REDACTED_GROQ_KEY]"),
    # GitHub Tokens
    (r"gh[pousr]_[a-zA-Z0-9]{36}", "[REDACTED_GITHUB_TOKEN]"),
    # OpenAI API Keys
    (r"sk-[a-zA-Z0-9]{40,}", "[REDACTED_OPENAI_KEY]"),
    # AWS Access Keys
    (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    # Bearer tokens in headers or curls
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer [REDACTED_TOKEN]"),
    # Generic password/secret assignments (e.g. password="mysecret")
    (r"(?i)(password|secret|token|api_key)\s*[:=]\s*['\"][^'\"]+['\"]", r"\1: [REDACTED]"),
    # RSA/Ed25519 Private Keys
    (r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
]

# 2. Define dangerous command signatures (Blast Radius Check)
DANGEROUS_PATTERNS = [
    r"\brm\s+.*-r[fF]?\b",                  # recursive remove (rm -rf, rm -r)
    r"\bmkfs\b",                          # formatting a filesystem
    r"\bdd\s+if=.*of=.*",                 # block level copy
    r">\s*/dev/(sd|nvme|vd)[a-z0-9]*",    # overwriting disk devices directly
    r"\bdrop\s+(database|table)\b",       # SQL drops
    r"\btruncate\s+table\b",              # SQL truncate
    r"kubectl\s+delete\s+(namespace|ns)\b", # K8s namespace nuke
    r"systemctl\s+(stop|disable)\s+(sshd|network)", # Network suicide
    r"\bchmod\s+-R\s+777\b",              # recursive 777 permissions
    r"\bchown\s+-R\s+root:root\s+/",      # recursive root ownership
]

def is_dangerous_command(cmd):
    """Checks if a command hits any dangerous regex patterns."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False

def sanitize_text(text):
    """Strips sensitive credentials from text using regex."""
    if not text:
        return text
    sanitized = text
    for pattern, replacement in REDACTION_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized

console = Console()
client = None  # Initialized in main(), after we've confirmed GROQ_API_KEY exists

def get_recent_shell_history(limit=25):
    """Reads the last N commands from zsh or bash history files."""
    home = Path.home()
    zsh_hist = home / ".zsh_history"
    bash_hist = home / ".bash_history"

    commands = []
    if zsh_hist.exists():
        try:
            with open(zsh_hist, "r", encoding="utf-8", errors="replace") as f:
                for line in f.readlines()[-limit * 2:]:
                    if ";" in line:
                        commands.append(line.split(";", 1)[1].strip())
                    else:
                        commands.append(line.strip())
        except Exception:
            pass
    elif bash_hist.exists():
        try:
            with open(bash_hist, "r", encoding="utf-8", errors="replace") as f:
                commands = [line.strip() for line in f.readlines() if not line.startswith("#")]
        except Exception:
            pass

    filtered = []
    for cmd in commands:
        if cmd and (not filtered or filtered[-1] != cmd):
            filtered.append(cmd)

    return filtered[-limit:]

def get_system_context():
    """Captures the local terminal environment."""
    cwd = os.getcwd()
    git_branch = "None"
    try:
        git_branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        pass

    return f"Working Directory: {cwd}\nGit Branch: {git_branch}\nOS: {sys.platform}"

def stream_groq_completion(messages, reasoning_effort="medium", show_thinking=False):
    """Streams completion with browser search and optional reasoning visibility.
    Never lets a Groq/network error surface as a raw traceback — always prints
    a clear, specific explanation and returns whatever partial text (if any)
    was generated before the failure."""
    tools = [{"type": "browser_search"}]
    full_content = []
    is_thinking = False

    try:
        stream = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            tools=tools,
            tool_choice="auto",
            reasoning_effort=reasoning_effort,
            stream=True,
            temperature=0.2
        )

        for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Capture reasoning tokens but only print if show_thinking is True
            reasoning_token = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
            if reasoning_token and show_thinking:
                if not is_thinking:
                    is_thinking = True
                    console.print()
                    console.print(Rule(title="Thinking Process", style="dim cyan"))
                console.print(reasoning_token, end="", style="dim italic", markup=False)
                sys.stdout.flush()

            # Capture and stream standard response tokens
            content_token = getattr(delta, "content", None)
            if content_token:
                if is_thinking:
                    is_thinking = False
                    console.print("\n")
                    console.print(Rule(title="Remediation", style="green"))
                console.print(content_token, end="", markup=False)
                sys.stdout.flush()
                full_content.append(content_token)

    except groq_sdk.AuthenticationError:
        console.print()
        console.print(Panel(
            "Your Groq API key was rejected.\n\n"
            "Double-check [bold]GROQ_API_KEY[/bold] is set correctly, or grab a fresh key at "
            "[cyan]https://console.groq.com/keys[/cyan]",
            title="🔑 Authentication Failed", border_style="red"
        ))
        return "".join(full_content)
    except groq_sdk.RateLimitError:
        console.print()
        console.print(Panel(
            "You've hit Groq's rate limit. Wait a moment and try again.",
            title="⏳ Rate Limited", border_style="yellow"
        ))
        return "".join(full_content)
    except groq_sdk.APITimeoutError:
        console.print()
        console.print(Panel(
            "The request to Groq timed out.\n\nTry again, or lower the reasoning effort with [bold]/effort low[/bold].",
            title="⏱ Request Timed Out", border_style="yellow"
        ))
        return "".join(full_content)
    except groq_sdk.APIConnectionError:
        console.print()
        console.print(Panel(
            "Couldn't reach Groq's servers. Check your internet connection and try again.",
            title="📡 Connection Error", border_style="red"
        ))
        return "".join(full_content)
    except groq_sdk.APIStatusError as e:
        console.print()
        console.print(Panel(
            f"Groq returned an error (HTTP {e.status_code}): {e.message}",
            title="⚠️ API Error", border_style="red"
        ))
        return "".join(full_content)
    except (KeyboardInterrupt, EOFError):
        console.print("\n\n[yellow]Generation interrupted.[/yellow]\n")
        return "".join(full_content)
    except Exception as e:
        console.print()
        console.print(Panel(
            f"Something unexpected went wrong talking to Groq:\n{type(e).__name__}: {e}",
            title="⚠️ Unexpected Error", border_style="red"
        ))
        return "".join(full_content)

    console.print("\n")

    # ---------------------------------------------------------
    # Re-render the final output as highlighted Markdown
    # ---------------------------------------------------------
    full_text = "".join(full_content)
    if full_text:
        try:
            console.print(Rule(style="dim"))
            md = Markdown(full_text, code_theme="monokai")
            console.print(md)
            console.print("\n")
        except Exception:
            # Markdown rendering is cosmetic — the raw text already streamed above,
            # so a rendering hiccup shouldn't be treated as a failure.
            pass

    return full_text

def extract_command(text):
    """Extracts the first or last executable bash/sh command block."""
    matches = re.findall(r"```(?:bash|sh)?\n(.*?)\n```", text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    return None

def execute_command(cmd, messages):
    """Runs the command in the user's active shell with builtins enabled."""
    console.print(f"\n[bold green]Running:[/bold green] [dim]{cmd}[/dim]\n")
    user_shell = os.environ.get("SHELL", "/bin/bash")

    try:
        process = subprocess.run(
            [user_shell, "-i", "-c", cmd],
            text=True,
            capture_output=True,
        )
    except FileNotFoundError:
        console.print(f"[bold red]Couldn't find your shell ('{user_shell}').[/bold red] "
                      f"Set the $SHELL environment variable to a valid shell path and try again.\n")
        return
    except PermissionError:
        console.print(f"[bold red]Permission denied[/bold red] trying to run '{user_shell}'.\n")
        return
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Command interrupted — nothing further was run.[/yellow]\n")
        return
    except Exception as e:
        console.print(f"[bold red]Couldn't run that command:[/bold red] {type(e).__name__}: {e}\n")
        return

    if process.stdout:
        print(process.stdout, end="")
    if process.stderr:
        print(process.stderr, end="", file=sys.stderr)

    status_msg = f"Command executed: `{cmd}`\nExit Code: {process.returncode}"
    if process.stdout:
        status_msg += f"\nStdout:\n{process.stdout.strip()}"
    if process.stderr:
        status_msg += f"\nStderr:\n{process.stderr.strip()}"

    # SANITIZE BEFORE APPENDING TO CONTEXT
    safe_status_msg = sanitize_text(status_msg)
    messages.append({"role": "user", "content": f"[Command Output]\n{safe_status_msg}"})

    if process.returncode == 0:
        console.print("\n[bold green]✓ Command succeeded (exit code 0).[/bold green]\n")
    else:
        console.print(f"\n[bold red]✗ Command failed (exit code {process.returncode}).[/bold red] Output added to context.\n")

def handle_suggested_command(cmd, session, messages):
    """Displays action options, enforcing strict checks on dangerous commands."""
    dangerous = is_dangerous_command(cmd)

    # Apply Bash syntax highlighting to the raw command string
    syntax_cmd = Syntax(cmd, "bash", theme="monokai", line_numbers=False, word_wrap=True, background_color="default")

    if dangerous:
        console.print(Panel(
            syntax_cmd,
            title="⚠️ DANGEROUS FIX SUGGESTED ⚠️",
            border_style="red"
        ))
    else:
        console.print(Panel(
            syntax_cmd,
            title="Suggested Fix",
            border_style="cyan"
        ))

    try:
        while True:
            if dangerous:
                choice = session.prompt(
                    HTML("<b>Action:</b> [<b><ansired>type YES to run</ansired></b>] | "
                         "[<b><ansiyellow>e</ansiyellow></b>] Edit | "
                         "[<b><ansiblue>c</ansiblue></b>] Copy | "
                         "[<b><ansigreen>n</ansigreen></b>/Enter] Skip &gt; ")
                ).strip()

                if choice == "YES":
                    execute_command(cmd, messages)
                    break
                elif choice.lower() in ("y", "yes"):
                    console.print("[bold red]Action cancelled. You must type exactly 'YES' (all caps) to execute this command.[/bold red]")
                    continue

            else:
                choice = session.prompt(
                    HTML("<b>Action:</b> [<b><ansigreen>y</ansigreen></b>] Run | "
                         "[<b><ansiyellow>e</ansiyellow></b>] Edit | "
                         "[<b><ansiblue>c</ansiblue></b>] Copy | "
                         "[<b><ansired>n</ansired></b>/Enter] Skip &gt; ")
                ).strip().lower()

                if choice in ("y", "yes"):
                    execute_command(cmd, messages)
                    break

            # Common options for both safe and dangerous commands
            if choice.lower() in ("e", "edit"):
                edited_cmd = session.prompt(
                    HTML("<b><ansiyellow>edit &gt; </ansiyellow></b>"),
                    default=cmd
                ).strip()
                if edited_cmd:
                    execute_command(edited_cmd, messages)
                break

            elif choice.lower() in ("c", "copy"):
                try:
                    import pyperclip
                    pyperclip.copy(cmd)
                    console.print("[dim green]✓ Copied to clipboard.[/dim green]\n")
                except ImportError:
                    console.print("[dim red]pyperclip isn't installed, so dime can't reach the clipboard.[/dim red] "
                                  "[dim]Run 'uv add pyperclip' to enable the Copy action.[/dim]\n")
                except Exception as e:
                    console.print(f"[dim red]Couldn't copy to clipboard: {e}[/dim red]\n")
                break

            elif choice.lower() in ("n", "no", ""):
                console.print("[dim]Skipped.[/dim]\n")
                break

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Skipped.[/dim]\n")

def run_silent(cmd, timeout=5):
    """Runs a shell command silently and returns stdout or stderr."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return f"Error ({result.returncode}): {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"

def process_auto_context(user_input):
    """Intercepts slash commands and injects shell context or file contents.

    Returns (query, used_auto_context).
    If a slash command fails in a way the user needs to know about immediately
    (bad usage, missing file, etc.), the explanation is printed directly here
    and this returns (None, True) so the caller skips the API call entirely —
    no point spending a request just to relay a local, already-known problem.
    """
    parts = user_input.split(maxsplit=1)
    command = parts[0].lower()
    query = parts[1] if len(parts) > 1 else ""
    context_data = ""

    if command == "/git":
        status = run_silent("git status")
        diff = run_silent("git diff --stat")
        context_data = f"[Git Context]\nStatus:\n{status}\n\nDiff Stat:\n{diff}"

    elif command == "/docker":
        ps = run_silent("docker ps -a | head -n 15")
        logs = run_silent("docker logs $(docker ps -lq) --tail 30")
        context_data = f"[Docker Context]\nContainers:\n{ps}\n\nLast Container Logs:\n{logs}"

    elif command == "/k8s":
        pods = run_silent("kubectl get pods -A | grep -v 'Completed' | head -n 15")
        events = run_silent("kubectl get events --sort-by='.metadata.creationTimestamp' | tail -n 15")
        context_data = f"[Kubernetes Context]\nPods:\n{pods}\n\nRecent Events:\n{events}"

    elif command == "/last":
        cmds = get_recent_shell_history(1)
        last_cmd = cmds[0] if cmds else "No history found."
        context_data = f"[Last Command]\n{last_cmd}"

    elif command == "/read":
        if not query:
            console.print("[yellow]Usage:[/yellow] /read <filepath> [optional question]\n")
            return None, True

        # Split the query into the file path and the actual question
        read_parts = query.split(maxsplit=1)
        filepath = read_parts[0]
        actual_query = read_parts[1] if len(read_parts) > 1 else ""

        try:
            # Resolve the path (handles ~ and relative paths properly)
            path_obj = Path(filepath).expanduser().resolve()
        except Exception as e:
            console.print(f"[yellow]Couldn't resolve path '{filepath}': {e}[/yellow]\n")
            return None, True

        if not path_obj.exists():
            console.print(f"[yellow]Can't find '{filepath}'.[/yellow] Check the path and try again.\n")
            return None, True
        if not path_obj.is_file():
            console.print(f"[yellow]'{filepath}' is a directory, not a file[/yellow] — point /read at a specific file.\n")
            return None, True

        try:
            size = path_obj.stat().st_size
        except Exception as e:
            console.print(f"[yellow]Couldn't check '{filepath}': {e}[/yellow]\n")
            return None, True

        # Prevent token bloat: limit to 100KB (approx 25k-30k tokens)
        if size > 100 * 1024:
            console.print(f"[yellow]'{filepath}' is larger than 100KB[/yellow] — dime only reads smaller files to stay within context limits.\n")
            return None, True

        try:
            content = path_obj.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            console.print(f"[yellow]'{filepath}' looks like a binary file[/yellow] — dime can only read plain text.\n")
            return None, True
        except PermissionError:
            console.print(f"[yellow]Permission denied[/yellow] reading '{filepath}'.\n")
            return None, True
        except Exception as e:
            console.print(f"[yellow]Couldn't read '{filepath}': {e}[/yellow]\n")
            return None, True

        context_data = f"[File Contents of {filepath}]\n```\n{content}\n```"

        # Override the query variable so it appends the user's actual question
        query = actual_query

    else:
        return user_input, False  # Not an auto-context command

    # Combine the gathered context with the user's specific question
    fallback_query = "Analyze the above context and identify any errors, misconfigurations, or required next steps."
    full_query = f"{context_data}\n\nUser Query: {query if query else fallback_query}"

    return full_query, True

def main():
    description = "dime - Advanced Terminal Debugging Assistant (Powered by Groq)"

    epilog = """
Interactive Slash Commands (Inside the dime > prompt):
  /read <file> [query]  Inject a local file into context (e.g., /read compose.yml why is it failing?)
  /last                 Fetch the last executed shell command and evaluate it
  /git                  Inject 'git status' and 'git diff' into context
  /docker               Inject 'docker ps' and the last exited container's logs
  /k8s                  Inject recent pod status and cluster events
  /think                Toggle visibility of the AI's internal reasoning
  /effort <level>       Set reasoning effort (low, medium, high)
  exit, quit            Close the session (auto-copies the last suggestion to clipboard)

Execution Actions (After a fix is suggested):
  [y] Run               Execute the command directly in your shell
  [e] Edit              Load the command into your prompt to tweak flags before running
  [c] Copy              Copy the exact command string to clipboard
  [Enter]               Skip execution and return to chat
  (Dangerous commands like 'rm -rf' or 'drop' are intercepted and require typing 'YES')

Examples:
  dime                          Start a fresh interactive session
  dime -r                       Resume your previous session state
  dime -t -d "untar a file"     Direct one-off query with thinking visible
  dime -d /last                 Directly evaluate the command you just ran and exit
  dime -d /read main.py "fix"   Evaluate a file and exit immediately
  cat error.log | dime          Pipe logs directly into dime for analysis
"""

    from argparse import RawTextHelpFormatter
    parser = argparse.ArgumentParser(
        prog="dime",
        description=description,
        epilog=epilog,
        formatter_class=RawTextHelpFormatter
    )
    parser.add_argument("-r", "--resume", action="store_true", help="Resume previous debugging session")
    parser.add_argument("-t", "--think", action="store_true", help="Display the AI's internal thinking process")
    parser.add_argument("-d", "--direct", type=str, nargs='+', help="Run a direct query and exit immediately")
    args = parser.parse_args()

    # ==========================================
    # API KEY / CLIENT SETUP
    # ==========================================
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        console.print(Panel(
            "dime needs a Groq API key to talk to the model.\n\n"
            "1. Grab a free key at [cyan]https://console.groq.com/keys[/cyan]\n"
            "2. Then run: [bold]export GROQ_API_KEY=\"gsk_...\"[/bold]\n"
            "   (add that line to your ~/.zshrc or ~/.bashrc so it persists)",
            title="🔑 Groq API Key Missing", border_style="yellow"
        ))
        sys.exit(1)

    global client
    try:
        client = Groq(api_key=api_key)
    except groq_sdk.GroqError as e:
        console.print(Panel(f"Couldn't initialize the Groq client: {e}", title="⚠️ Startup Error", border_style="red"))
        sys.exit(1)
    except Exception as e:
        console.print(Panel(f"Unexpected error setting up Groq: {type(e).__name__}: {e}", title="⚠️ Startup Error", border_style="red"))
        sys.exit(1)

    # ==========================================
    # STDIN PIPING LOGIC
    # ==========================================
    piped_data = ""
    if not sys.stdin.isatty():
        try:
            piped_data = sys.stdin.read().strip()
        except Exception as e:
            console.print(f"[yellow]Couldn't read piped input: {e}[/yellow]")
            piped_data = ""
        # Reconnect stdin to the terminal so interactive prompts (like [y] Run) still work
        try:
            sys.stdin = open('/dev/tty', 'r')
        except Exception:
            pass  # No controlling TTY available (e.g. running in CI) — interactive prompts just won't appear

    recent_cmds = get_recent_shell_history(20)
    sys_context = get_system_context()
    history_block = sanitize_text("\n".join(f"- {cmd}" for cmd in recent_cmds))

    system_prompt = f"""You are 'dime', an advanced terminal debugging assistant running on openai/gpt-oss-120b.
The user is working in an interactive terminal and will paste error traces, logs, or questions.

Context:
{sys_context}

Recent shell command history (oldest to newest):
{history_block}

Guidelines:
1. Deep Reasoning: Analyze ambiguous stack traces, conflicting library versions, or multi-step errors carefully.
2. Web Search: You have the `browser_search` tool enabled. Use it autonomously if an error involves an obscure flag, recent release change, or vendor-specific issue you need to verify.
3. Output Format:
   - 1-2 sentence diagnosis of the root cause.
   - The exact remediation command inside a markdown code block (```bash ... ```).
   - Keep prose minimal and action-focused.
4. If the user asks to search command history, suggest `grep <term> ~/.bash_history ~/.zsh_history` instead of using the `history` builtin.
"""

    messages = []
    if args.resume:
        loaded_messages = load_session()
        if loaded_messages:
            messages = loaded_messages
            messages[0]["content"] = system_prompt
            if not args.direct and not piped_data:
                console.print("[bold green]✓ Resumed previous session[/bold green]")
        else:
            if not args.direct and not piped_data:
                console.print("[yellow]No previous session found. Starting fresh.[/yellow]")
            messages = [{"role": "system", "content": system_prompt}]
    else:
        messages = [{"role": "system", "content": system_prompt}]

    session = PromptSession()
    current_effort = "medium"
    show_thinking = args.think

    # ==========================================
    # DIRECT MODE OR PIPED MODE
    # ==========================================
    if args.direct or piped_data:
        # Default query if they just pipe data without a -d flag
        raw_query = " ".join(args.direct) if args.direct else "Analyze this piped input and identify any errors, warnings, or necessary fixes."

        if piped_data:
            # Truncate to the last 100,000 characters to prevent API limits on massive logs
            truncated_pipe = piped_data[-100000:]
            raw_query = f"{raw_query}\n\n[Piped Input]\n```\n{truncated_pipe}\n```"

        processed_query, is_auto = process_auto_context(raw_query)
        if processed_query is None:
            # A local error was already shown by process_auto_context — nothing to send.
            save_session(messages)
            sys.exit(1)

        if is_auto:
            console.print(f"[dim cyan]✓ Injected background context[/dim cyan]")
        elif piped_data:
            console.print(f"[dim cyan]✓ Injected {len(piped_data)} bytes of piped data[/dim cyan]")

        safe_input = sanitize_text(processed_query)
        messages.append({"role": "user", "content": safe_input})

        if show_thinking:
            console.print(f"[dim]Executing Query (Thinking Enabled)[/dim]\n")
        else:
            console.print(f"[dim]Executing Query...[/dim]\n")

        assistant_reply = stream_groq_completion(messages, reasoning_effort=current_effort, show_thinking=show_thinking)

        if assistant_reply:
            messages.append({"role": "assistant", "content": assistant_reply})
            cmd = extract_command(assistant_reply)
            if cmd:
                handle_suggested_command(cmd, session, messages)

        save_session(messages)
        sys.exit(0)

    # ==========================================
    # INTERACTIVE MODE
    # ==========================================
    think_status = "visible" if show_thinking else "hidden"
    console.print(f"[bold green]dime session active[/bold green] [dim](openai/gpt-oss-120b | thinking {think_status})[/dim]")
    console.print("[dim]Paste errors below. Type 'exit' to quit, '/effort [low|medium|high]', or '/think' to toggle reasoning.[/dim]")
    console.print("[dim]Auto-context commands: /git, /docker, /k8s, /last, /read <file>[/dim]\n")

    save_session(messages)

    while True:
        try:
            user_input = session.prompt(HTML("<b><ansigreen>dime &gt; </ansigreen></b>"))

            clean_input = user_input.strip()
            if not clean_input:
                continue

            if clean_input.lower() in ("exit", "quit", ":q"):
                console.print("[dim]Exiting dime.[/dim]")
                break

            if clean_input.startswith("/effort"):
                parts = clean_input.split()
                if len(parts) == 2 and parts[1] in ("low", "medium", "high"):
                    current_effort = parts[1]
                    console.print(f"[dim]Reasoning effort set to: [bold]{current_effort}[/bold][/dim]\n")
                    continue
                else:
                    console.print("[dim red]Usage: /effort low | medium | high[/dim red]\n")
                    continue

            if clean_input.startswith("/think"):
                show_thinking = not show_thinking
                status = "VISIBLE" if show_thinking else "HIDDEN"
                console.print(f"[dim]Thinking process is now: [bold]{status}[/bold][/dim]\n")
                continue

            processed_query, is_auto = process_auto_context(clean_input)
            if processed_query is None:
                # A local error was already shown by process_auto_context — skip the API call.
                continue

            if is_auto:
                console.print(f"[dim cyan]✓ Injected background context for {clean_input.split()[0]}[/dim cyan]")

            safe_input = sanitize_text(processed_query)
            messages.append({"role": "user", "content": safe_input})
            save_session(messages)

            assistant_reply = stream_groq_completion(messages, reasoning_effort=current_effort, show_thinking=show_thinking)

            if assistant_reply:
                messages.append({"role": "assistant", "content": assistant_reply})
                cmd = extract_command(assistant_reply)

                if cmd:
                    handle_suggested_command(cmd, session, messages)

                save_session(messages)

        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session closed. Run 'dime -r' to resume.[/dim]")
            break

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Interrupted. Bye![/dim]")
        sys.exit(130)
    except Exception as e:
        # Last-resort safety net: dime should never hand the user a raw Python
        # traceback. If something truly unexpected slips through, explain it
        # plainly and point at how to get more detail.
        console.print()
        console.print(Panel(
            f"dime hit an unexpected problem and had to stop.\n\n"
            f"[dim]{type(e).__name__}: {e}[/dim]\n\n"
            f"This shouldn't happen — please open an issue at:\n"
            f"[cyan]https://github.com/MwauraJames/Dime-terminal-agent/issues[/cyan]\n\n"
            f"[dim]Tip: set DIME_DEBUG=1 and re-run to see the full traceback.[/dim]",
            title="💥 Unexpected Error", border_style="red"
        ))
        if os.environ.get("DIME_DEBUG"):
            console.print_exception(show_locals=False)
        sys.exit(1)