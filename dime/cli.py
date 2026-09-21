"""The traffic controller: argument parsing, the interactive prompt loop and slash-command dispatch."""

import argparse
import os
import sys

from dime.ui import console  # keep first: verifies rich/prompt_toolkit are installed before they're imported below
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from rich.panel import Panel

from dime.osinfo import IS_WINDOWS
from dime.security import sanitize_text
from dime.context import process_auto_context
from dime.executor import handle_suggested_command
from dime.llm import (
    build_system_prompt,
    extract_command,
    load_litellm,
    model_problem,
    normalize_model,
    resolve_model,
    setup_tracing,
    stream_completion,
    supports_effort,
)
from dime.session import load_session, save_session


def _run():
    if IS_WINDOWS:
        # dime prints ✓ ⚠️ 🔑 etc. If output is redirected to a legacy codepage (cp1252/cp437),
        # a plain print() would raise UnicodeEncodeError; substitute '?' instead of crashing.
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass

    description = "dime - Advanced Terminal Debugging Assistant (bring your own key: Groq, Anthropic, Gemini, OpenAI, Ollama)"

    epilog = """
Interactive Slash Commands (Inside the dime > prompt):
  /read <file> [query]  Inject a local file into context (e.g., /read compose.yml why is it failing?)
  /last                 Fetch the last executed shell command and evaluate it
  /git                  Inject 'git status' and 'git diff' into context
  /docker               Inject 'docker ps' and the last exited container's logs
  /k8s                  Inject recent pod status and cluster events
  /think                Toggle visibility of the AI's internal reasoning
  /effort <level>       Set reasoning effort (low, medium, high) - applies to Groq gpt-oss and OpenAI gpt-5/o-series
  /model [name]         Show the current model, or switch to another one mid-session
  exit, quit            Close the session

Execution Actions (After a fix is suggested):
  [y] Run               Execute the command directly in your shell
  [e] Edit              Load the command into your prompt to tweak flags before running
  [c] Copy              Copy the exact command string to clipboard
  [Enter]               Skip execution and return to chat
  (Dangerous commands like 'rm -rf' or 'drop' are intercepted and require typing 'YES')

Models (bring your own key; dime uses the first key it finds unless you pick with --model or $DIME_MODEL):
  GROQ_API_KEY       groq/openai/gpt-oss-120b   (default; also: groq/llama-3.3-70b-versatile, ...)
  ANTHROPIC_API_KEY  anthropic/claude-sonnet-5
  GEMINI_API_KEY     gemini/gemini-2.5-flash
  OPENAI_API_KEY     openai/gpt-5-mini
  (none)             ollama_chat/llama3.1        local Ollama, no key needed (set OLLAMA_API_BASE for a remote one)
  Bare names work too: --model claude-sonnet-5, gemini-2.5-flash, gpt-5-mini. Any LiteLLM model string is accepted.

Tracing (optional): set LANGSMITH_API_KEY (and optionally LANGSMITH_PROJECT) to send every request to LangSmith.
  Prompts are sent after secret redaction. Opt out per run with --no-trace, or set DIME_TRACE=0.

Examples:
  dime                          Start a fresh interactive session
  dime -m anthropic/claude-sonnet-5   Use Claude (needs ANTHROPIC_API_KEY)
  dime -m ollama_chat/llama3.1  Use a local model via Ollama
  dime -r                       Resume your previous session state
  dime -t -d "untar a file"     Direct one-off query with thinking visible
  dime -d /last                 Directly evaluate the command you just ran and exit
  dime -d /read main.py "fix"   Evaluate a file and exit immediately
  cat error.log | dime          Pipe logs directly into dime for analysis
  Get-Content error.log | dime  Same thing from PowerShell on Windows
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
    parser.add_argument("-m", "--model", type=str, metavar="MODEL",
                        help="Model to use, e.g. anthropic/claude-sonnet-5 or ollama_chat/llama3.1 "
                             "(default: $DIME_MODEL, else the first provider whose API key is set)")
    parser.add_argument("--no-trace", action="store_true",
                        help="Don't send traces to LangSmith even if LANGSMITH_API_KEY is set")
    args = parser.parse_args()

    # ==========================================
    # MODEL / PROVIDER SETUP (LiteLLM, bring-your-own-key)
    # ==========================================
    load_litellm()
    model = resolve_model(args.model)
    trace_project = setup_tracing(disabled=args.no_trace)
    if trace_project:
        console.print(f"[dim cyan]✓ LangSmith tracing on (project: {trace_project}). Prompts are sent after "
                      f"secret redaction; disable with --no-trace.[/dim cyan]")

    # ==========================================
    # STDIN PIPING LOGIC
    # ==========================================
    piped_data = ""
    if not sys.stdin.isatty():
        try:
            # Piped bytes aren't always valid in the console's codepage (common on Windows) —
            # replace bad bytes instead of dying with a UnicodeDecodeError.
            sys.stdin.reconfigure(errors="replace")
        except Exception:
            pass
        try:
            piped_data = sys.stdin.read().strip()
        except Exception as e:
            console.print(f"[yellow]Couldn't read piped input: {e}[/yellow]")
            piped_data = ""
        # Reconnect stdin to the terminal so interactive prompts (like [y] Run) still work
        if not IS_WINDOWS:
            try:
                sys.stdin = open('/dev/tty', 'r')
            except Exception:
                pass  # No controlling TTY available (e.g. running in CI) — interactive prompts just won't appear
        # On Windows there is no /dev/tty, and we deliberately do NOT swap sys.stdin for open('CONIN$').
        # prompt_toolkit already detects a non-tty sys.stdin and opens CONIN$ itself; replacing
        # sys.stdin with a console file makes isatty() return True, which sends prompt_toolkit to
        # the original (piped) stdin handle instead and breaks the interactive prompts.

    system_prompt = build_system_prompt(model)

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

        assistant_reply = stream_completion(messages, model, reasoning_effort=current_effort, show_thinking=show_thinking)

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
    console.print(f"[bold green]dime session active[/bold green] [dim]({model} | thinking {think_status})[/dim]")
    console.print("[dim]Paste errors below. Type 'exit' to quit, '/effort \\[low|medium|high]', '/model <name>' to switch models, or '/think' to toggle reasoning.[/dim]")
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
                    console.print(f"[dim]Reasoning effort set to: [bold]{current_effort}[/bold][/dim]")
                    if not supports_effort(model):
                        console.print(f"[dim](Not applied to {model}: dime only sends it to Groq gpt-oss and OpenAI gpt-5/o-series models.)[/dim]")
                    console.print()
                    continue
                else:
                    console.print("[dim red]Usage: /effort low | medium | high[/dim red]\n")
                    continue

            if clean_input.startswith("/model"):
                parts = clean_input.split(maxsplit=1)
                if len(parts) == 1:
                    console.print(f"[dim]Current model: [bold]{model}[/bold]. Switch with /model <name>.[/dim]\n")
                    continue
                candidate = normalize_model(parts[1])
                problem = model_problem(candidate)
                if problem:
                    console.print(Panel(problem[1], title=problem[0], border_style="yellow"))
                    continue
                model = candidate
                # The system prompt depends on the model (e.g. whether web search is available)
                messages[0]["content"] = build_system_prompt(model)
                save_session(messages)
                console.print(f"[dim]Switched to [bold]{model}[/bold][/dim]\n")
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

            assistant_reply = stream_completion(messages, model, reasoning_effort=current_effort, show_thinking=show_thinking)

            if assistant_reply:
                messages.append({"role": "assistant", "content": assistant_reply})
                cmd = extract_command(assistant_reply)

                if cmd:
                    handle_suggested_command(cmd, session, messages)

                save_session(messages)

        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Session closed. Run 'dime -r' to resume.[/dim]")
            break


def main():
    """Console-script entry point (`dime = "dime.cli:main"`).

    Wraps _run() in a last-resort safety net: dime should never hand the user a raw Python
    traceback. (This lives here rather than under `if __name__ == "__main__"` because the
    installed `dime` command calls main() directly and would bypass that.)"""
    try:
        _run()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Interrupted. Bye![/dim]")
        sys.exit(130)
    except Exception as e:
        # If something truly unexpected slips through, explain it plainly and point at how
        # to get more detail.
        console.print()
        console.print(Panel(
            f"dime hit an unexpected problem and had to stop.\n\n"
            f"[dim]{type(e).__name__}: {e}[/dim]\n\n"
            f"This shouldn't happen \u2014 please open an issue at:\n"
            f"[cyan]https://github.com/MwauraJames/Dime-terminal-agent/issues[/cyan]\n\n"
            f"[dim]Tip: set DIME_DEBUG=1 and re-run to see the full traceback.[/dim]",
            title="\U0001F4A5 Unexpected Error", border_style="red"
        ))
        if os.environ.get("DIME_DEBUG"):
            console.print_exception(show_locals=False)
        sys.exit(1)


if __name__ == "__main__":
    main()
