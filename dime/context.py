"""Everything dime knows about the user's environment: shell history, system info, and the
auto-context slash commands (/git, /docker, /k8s, /last, /read). New slash commands go in process_auto_context()."""

import os
import subprocess
from pathlib import Path

from dime.osinfo import IS_WINDOWS, describe_os
from dime.ui import console


def get_powershell_history_path():
    """Path to PSReadLine's history file (shared by Windows PowerShell 5.1 and PowerShell 7).
    cmd.exe keeps no persistent history, so there's nothing to read for it."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    path = Path(appdata) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine" / "ConsoleHost_history.txt"
    return path if path.exists() else None

def get_recent_shell_history(limit=25):
    """Reads the last N commands from PowerShell, zsh or bash history files."""
    home = Path.home()
    zsh_hist = home / ".zsh_history"
    bash_hist = home / ".bash_history"
    ps_hist = get_powershell_history_path() if IS_WINDOWS else None

    commands = []
    if IS_WINDOWS:
        if ps_hist:
            try:
                with open(ps_hist, "r", encoding="utf-8", errors="replace") as f:
                    commands = [line.strip() for line in f.readlines()[-limit * 2:]]
            except Exception:
                pass
    elif zsh_hist.exists():
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

    return f"Working Directory: {cwd}\nGit Branch: {git_branch}\nOS: {describe_os()}"

def _head(text, n):
    return "\n".join(text.splitlines()[:n])

def _tail(text, n):
    return "\n".join(text.splitlines()[-n:])

def run_silent(cmd, timeout=5):
    """Runs a command silently and returns stdout or stderr.

    Pass an argv list (preferred) to run without any shell; the /git, /docker and /k8s
    helpers do their own head/tail/grep filtering in Python, so they behave the same on
    Linux, macOS and Windows. A plain string still runs through the system shell."""
    try:
        result = subprocess.run(
            cmd, shell=isinstance(cmd, str), capture_output=True, text=True,
            errors="replace", timeout=timeout
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
        status = run_silent(["git", "status"])
        diff = run_silent(["git", "diff", "--stat"])
        context_data = f"[Git Context]\nStatus:\n{status}\n\nDiff Stat:\n{diff}"

    elif command == "/docker":
        ps = _head(run_silent(["docker", "ps", "-a"]), 15)
        last_id = run_silent(["docker", "ps", "-lq"])
        if last_id and not last_id.startswith("Error"):
            logs = run_silent(["docker", "logs", last_id.splitlines()[0], "--tail", "30"])
        else:
            logs = last_id or "No containers found."
        context_data = f"[Docker Context]\nContainers:\n{ps}\n\nLast Container Logs:\n{logs}"

    elif command == "/k8s":
        pods_raw = run_silent(["kubectl", "get", "pods", "-A"])
        pods = _head("\n".join(l for l in pods_raw.splitlines() if "Completed" not in l), 15)
        events = _tail(run_silent(["kubectl", "get", "events", "--sort-by=.metadata.creationTimestamp"]), 15)
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
