"""Running the suggested command: the subprocess call, clipboard handling, and the [y]/[e]/[c] action prompt."""

import shutil
import subprocess
import sys

from dime.ui import console  # keep first: verifies rich/prompt_toolkit are installed before they're imported below
from prompt_toolkit.formatted_text import HTML
from rich.panel import Panel
from rich.syntax import Syntax

from dime.osinfo import IS_WINDOWS, IS_WSL, resolve_shell
from dime.security import is_dangerous_command, sanitize_text


def execute_command(cmd, messages):
    """Runs the command in the user's active shell with builtins enabled."""
    console.print(f"\n[bold green]Running:[/bold green] [dim]{cmd}[/dim]\n")
    _family, shell_prefix = resolve_shell()
    user_shell = shell_prefix[0]

    try:
        process = subprocess.run(
            [*shell_prefix, cmd],
            text=True,
            capture_output=True,
            errors="replace",  # never crash on output the console codepage can't decode
        )
    except FileNotFoundError:
        if IS_WINDOWS:
            console.print(f"[bold red]Couldn't find a shell to run that ('{user_shell}').[/bold red] "
                          f"Make sure PowerShell (pwsh or powershell.exe) is on your PATH and try again.\n")
        else:
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

class ClipboardUnavailable(Exception):
    """Raised when no clipboard route worked. The message is safe to show the user."""

def copy_to_clipboard(text):
    """Copies text to the system clipboard.

    Order: pyperclip first (works natively on Windows/macOS and on Linux with xclip/xsel/wl-clipboard),
    then, under WSL only, the Windows host's clip.exe. Raises ClipboardUnavailable on failure."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return
    except ImportError:
        reason = "pyperclip isn't installed (run 'uv add pyperclip' to enable the Copy action)"
    except Exception as e:  # pyperclip.PyperclipException: no xclip/xsel/wl-copy, headless session, ...
        reason = str(e)

    if IS_WSL and shutil.which("clip.exe"):
        try:
            # "utf-16" (not "utf-16le") writes a BOM, which is how clip.exe knows the input is Unicode
            subprocess.run(["clip.exe"], input=text.encode("utf-16"), check=True, timeout=5)
            return
        except Exception as e:
            raise ClipboardUnavailable(f"clip.exe failed too ({type(e).__name__}: {e})")

    raise ClipboardUnavailable(reason)

def handle_suggested_command(cmd, session, messages):
    """Displays action options, enforcing strict checks on dangerous commands."""
    dangerous = is_dangerous_command(cmd)

    # Highlight using the lexer that matches the shell the command will run in
    lexer = {"powershell": "powershell", "cmd": "bat"}.get(resolve_shell()[0], "bash")
    syntax_cmd = Syntax(cmd, lexer, theme="monokai", line_numbers=False, word_wrap=True, background_color="default")

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
                    copy_to_clipboard(cmd)
                    console.print("[dim green]✓ Copied to clipboard.[/dim green]\n")
                except ClipboardUnavailable as e:
                    console.print(f"[dim red]Couldn't copy to clipboard:[/dim red] [dim]{e}[/dim]\n")
                break

            elif choice.lower() in ("n", "no", ""):
                console.print("[dim]Skipped.[/dim]\n")
                break

    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Skipped.[/dim]\n")
