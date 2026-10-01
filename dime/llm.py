"""Talking to models: provider selection (LiteLLM, bring-your-own-key), streaming, optional LangSmith
tracing, system-prompt construction, and pulling the runnable command out of a reply."""

import os
import re
import sys
import threading

from dime.ui import console, print_missing_dependency  # keep first: verifies rich is installed before it's imported below
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule

from dime.osinfo import IS_WINDOWS, resolve_shell, get_os_guidance
from dime.security import sanitize_text
from dime.context import get_recent_shell_history, get_system_context


# ==========================================
# MODEL / PROVIDER LAYER (LiteLLM)
# ==========================================
litellm = None       # set once imported -- see load_litellm() / start_litellm_preload()
_preload_thread = None  # background import kicked off by start_litellm_preload(), if any
_preload_error = None   # ImportError the background thread hit, if any (re-raised on the main thread)

DEFAULT_GROQ_MODEL = "groq/openai/gpt-oss-120b"

# Env vars that hold each provider's API key (any one of them is enough).
KEY_ENV = {
    "groq":       ("GROQ_API_KEY",),
    "anthropic":  ("ANTHROPIC_API_KEY",),
    "gemini":     ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai":     ("OPENAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "mistral":    ("MISTRAL_API_KEY",),
    "deepseek":   ("DEEPSEEK_API_KEY",),
    "xai":        ("XAI_API_KEY",),
}

# Where users can get a key. Providers not listed here (Bedrock, Vertex, Azure...) can authenticate
# through files/roles instead of env vars, so dime doesn't pre-check them and lets the provider answer.
KEY_URLS = {
    "groq":      "https://console.groq.com/keys",
    "anthropic": "https://console.anthropic.com/settings/keys",
    "gemini":    "https://aistudio.google.com/apikey",
    "openai":    "https://platform.openai.com/api-keys",
}

KEYLESS_PROVIDERS = {"ollama", "ollama_chat"}

# With no --model / DIME_MODEL, the first provider whose key is in the environment wins.
# Groq is first, so existing users get exactly the behaviour they had before. These are only
# defaults -- edit freely; any LiteLLM model string works with --model.
DEFAULT_MODELS = [
    ("groq",      DEFAULT_GROQ_MODEL),
    ("anthropic", "anthropic/claude-sonnet-5"),
    ("gemini",    "gemini/gemini-3.6-flash"),
    ("openai",    "openai/gpt-5-mini"),
]

def _import_litellm():
    """The actual (slow, ~2s) import plus one-time config. Runs on whichever thread calls it --
    the background preload thread, or load_litellm() directly if there was no preload."""
    global litellm
    # Must be set BEFORE import: use the bundled model-price map instead of fetching one from
    # GitHub on every start (slow offline), and keep LiteLLM's own logging quiet.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    os.environ.setdefault("LITELLM_LOG", "ERROR")
    import litellm as _litellm
    _litellm.drop_params = True          # silently skip params a given model doesn't support (temperature on o-series, etc.)
    _litellm.suppress_debug_info = True  # no "Give Feedback / Get Help" banners on errors
    _litellm.telemetry = False
    litellm = _litellm

def start_litellm_preload():
    """Starts importing LiteLLM (~2s, almost entirely spent on provider code dime never uses --
    its proxy server, Bedrock, OpenTelemetry, guardrails...) on a background thread, so that cost
    overlaps with other startup work instead of blocking it. In an interactive session this mostly
    hides behind however long the user takes to read the banner and type their first message:
    load_litellm() is only called once dime is about to actually talk to a model, and by then the
    import has often already finished. Call this once, as early in startup as possible. Safe to
    call more than once (later calls are a no-op) and safe to skip (load_litellm() falls back to
    importing inline)."""
    global _preload_thread
    if litellm is not None or _preload_thread is not None:
        return

    def _target():
        global _preload_error
        try:
            _import_litellm()
        except ImportError as e:
            _preload_error = e  # re-raised on the main thread by load_litellm(); a background
                                 # thread crashing silently would otherwise look like a hang

    _preload_thread = threading.Thread(target=_target, daemon=True)
    _preload_thread.start()

def load_litellm():
    """Ensures LiteLLM is imported and ready, returning it. If start_litellm_preload() was called
    first, this blocks only for whatever time is left on that background import (often nothing);
    otherwise it imports inline here, exactly as before. Always safe to call -- every function
    below that touches `litellm` calls this first rather than assuming someone else already did."""
    if litellm is not None:
        return litellm
    if _preload_thread is not None:
        with console.status("[dim]Finishing startup...[/dim]"):
            _preload_thread.join()
        if litellm is not None:
            return litellm
        if _preload_error is not None:
            print_missing_dependency(getattr(_preload_error, "name", None) or str(_preload_error))
            sys.exit(1)
    try:
        with console.status("[dim]Loading...[/dim]"):
            _import_litellm()
    except ImportError as e:
        print_missing_dependency(getattr(e, "name", None) or str(e))
        sys.exit(1)
    return litellm

def normalize_model(name):
    """Turns whatever the user typed into a LiteLLM model string.

    - provider-prefixed names (anthropic/..., groq/..., ollama_chat/...) pass through
    - bare 'gemini-*' -> 'gemini/gemini-*': unprefixed, LiteLLM routes Gemini to *Vertex AI*
      (needs GCP credentials) instead of using your GEMINI_API_KEY
    - bare 'claude-*' -> 'anthropic/claude-*'; bare 'gpt-*' / 'o1'-style -> 'openai/...'
    - 'openai/gpt-oss-*' and bare 'gpt-oss-*' -> 'groq/openai/gpt-oss-*': that's the id dime has
      always used with Groq (OpenAI's own API doesn't serve gpt-oss)
    - 'ollama/x' -> 'ollama_chat/x': the chat endpoint handles system prompts and history properly
    """
    name = name.strip()
    lower = name.lower()
    if lower.startswith("openai/gpt-oss"):
        return "groq/" + name
    if lower.startswith("gpt-oss"):
        return "groq/openai/" + name
    if lower.startswith("ollama/"):
        return "ollama_chat/" + name.split("/", 1)[1]
    if "/" in name:
        return name
    if lower.startswith("claude"):
        return "anthropic/" + name
    if lower.startswith("gemini"):
        return "gemini/" + name
    if re.match(r"(gpt-|o\d)", lower):
        return "openai/" + name
    return name

def get_provider(model):
    """LiteLLM's provider name for a model string, or None if LiteLLM doesn't recognise it."""
    load_litellm()  # no-op if already loaded; otherwise blocks here rather than assuming it's ready
    try:
        return litellm.get_llm_provider(model)[1]
    except Exception:
        return None

def uses_browser_search(model):
    """Groq-hosted gpt-oss models have a server-side `browser_search` tool. No other provider does."""
    return model.startswith("groq/") and "gpt-oss" in model.lower()

def supports_effort(model):
    """Models where dime's /effort setting is actually sent (Groq gpt-oss, OpenAI gpt-5 / o-series).
    It is deliberately NOT sent to other providers: e.g. on Claude it would switch on extended
    thinking, which requires temperature=1 and a token budget dime doesn't manage."""
    m = model.lower()
    if uses_browser_search(m):
        return True
    return bool(re.match(r"openai/(gpt-5|o\d)", m))

def build_completion_kwargs(model, reasoning_effort):
    """Per-model request options. Groq gpt-oss gets exactly what dime always sent."""
    kwargs = {}
    if uses_browser_search(model):
        kwargs["tools"] = [{"type": "browser_search"}]
        kwargs["tool_choice"] = "auto"
    if supports_effort(model):
        kwargs["reasoning_effort"] = reasoning_effort
    openai_reasoning = supports_effort(model) and not uses_browser_search(model)
    if not openai_reasoning:  # OpenAI reasoning models only accept the default temperature
        kwargs["temperature"] = 0.2
    if get_provider(model) in KEYLESS_PROVIDERS:
        # Ollama silently truncates input beyond its default context window (often 2-4k tokens),
        # which would cut off exactly the log/trace being debugged.
        try:
            kwargs["num_ctx"] = int(os.environ.get("DIME_NUM_CTX", "8192"))
        except ValueError:
            kwargs["num_ctx"] = 8192
    return kwargs

def set_env_hint(var):
    """How to set an environment variable on this OS (Rich markup)."""
    if IS_WINDOWS:
        return (f"[bold]setx {var} \"...\"[/bold]  (persists; open a new terminal afterwards)\n"
                f"   or for this session only: [bold]$env:{var} = \"...\"[/bold]")
    return (f"[bold]export {var}=\"...\"[/bold]\n"
            f"   (add that line to your ~/.zshrc or ~/.bashrc so it persists)")

def model_problem(model):
    """Returns (title, body) describing why `model` can't be used right now, or None if it looks fine."""
    provider = get_provider(model)
    if provider is None:
        return ("❓ Unknown Model",
                f"LiteLLM doesn't recognise [bold]{model}[/bold].\n\n"
                "Use the form [bold]provider/model[/bold], for example:\n"
                "  groq/llama-3.3-70b-versatile\n"
                "  anthropic/claude-sonnet-5\n"
                "  gemini/gemini-3.6-flash\n"
                "  openai/gpt-5-mini\n"
                "  ollama_chat/llama3.1   (local, no key needed)")
    env_vars = KEY_ENV.get(provider)
    if env_vars and not any(os.environ.get(v) for v in env_vars):
        var = env_vars[0]
        url = KEY_URLS.get(provider)
        body = f"[bold]{model}[/bold] needs an API key, but [bold]{var}[/bold] isn't set.\n\n"
        if url:
            body += f"1. Get a key at [cyan]{url}[/cyan]\n2. Then run: {set_env_hint(var)}"
        else:
            body += f"Set it with: {set_env_hint(var)}"
        return (f"🔑 {provider.capitalize()} API Key Missing", body)
    return None

def resolve_model(cli_model):
    """Picks the model: --model, then $DIME_MODEL, then the first provider whose key is set.
    Prints a helpful panel and exits if nothing usable is configured."""
    requested = cli_model or os.environ.get("DIME_MODEL")
    if requested:
        model = normalize_model(requested)
        problem = model_problem(model)
        if problem:
            console.print(Panel(problem[1], title=problem[0], border_style="yellow"))
            sys.exit(1)
        return model

    for provider, model in DEFAULT_MODELS:
        if any(os.environ.get(v) for v in KEY_ENV[provider]):
            return model

    lines = ["dime needs an API key for at least one provider (or a local Ollama model).\n",
             "Set one of these, then run dime again:"]
    for provider, _ in DEFAULT_MODELS:
        lines.append(f"  [bold]{KEY_ENV[provider][0]:<18}[/bold] [cyan]{KEY_URLS[provider]}[/cyan]")
    lines.append(f"\nHow to set one: {set_env_hint('GROQ_API_KEY')}")
    lines.append("\nNo key? Run a local model instead: [bold]ollama pull llama3.1[/bold], then "
                 "[bold]dime --model ollama_chat/llama3.1[/bold]")
    console.print(Panel("\n".join(lines), title="🔑 No API Key Found", border_style="yellow"))
    sys.exit(1)

# ==========================================
# OPTIONAL LANGSMITH TRACING (via LiteLLM's built-in callback -- no LangChain)
# ==========================================
def setup_tracing(disabled=False):
    """Turns on LangSmith tracing if a LangSmith API key is in the environment.
    Returns the project name when enabled, else None.

    Notes:
    - LiteLLM only reads LANGSMITH_* variables, so the older LANGCHAIN_* names are mapped across.
    - batch size 1: LiteLLM normally queues traces and flushes them from a background asyncio task,
      which a short-lived CLI process never runs (and it only sends after 100 queued traces). With
      batch size 1 each trace is sent as soon as its request finishes, before dime exits.
    - Everything sent has already been through sanitize_text() (keys/tokens redacted)."""
    if disabled or os.environ.get("DIME_TRACE", "").strip().lower() in ("0", "false", "off", "no"):
        return None
    key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    if not key:
        return None
    load_litellm()  # only needed once we know tracing will actually be configured
    os.environ["LANGSMITH_API_KEY"] = key
    project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT") or "dime"
    os.environ["LANGSMITH_PROJECT"] = project
    endpoint = os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get("LANGCHAIN_ENDPOINT")
    if endpoint and not os.environ.get("LANGSMITH_BASE_URL"):
        os.environ["LANGSMITH_BASE_URL"] = endpoint
    workspace = os.environ.get("LANGSMITH_WORKSPACE_ID")
    if workspace and not os.environ.get("LANGSMITH_TENANT_ID"):
        os.environ["LANGSMITH_TENANT_ID"] = workspace
    os.environ.setdefault("LANGSMITH_DEFAULT_RUN_NAME", "dime")
    litellm.success_callback = ["langsmith"]
    litellm.langsmith_batch_size = 1
    return project

def _short(e, limit=600):
    """A provider error message, secrets redacted and length-capped."""
    return escape(sanitize_text(str(e)).strip()[:limit])

_CONNECTION_HINTS = ("connection refused", "connection error", "connecterror", "name or service not known",
                     "temporary failure in name resolution", "getaddrinfo", "network is unreachable",
                     "nodename nor servname", "no route to host")

def _looks_like_connection_error(e):
    """LiteLLM reports some network failures (notably on OpenAI-compatible providers like Groq)
    as a generic InternalServerError / HTTP 500. Recognise those so users aren't told the
    provider is broken when it's really their network."""
    text = str(e).lower()
    return any(h in text for h in _CONNECTION_HINTS)

def _connection_panel(provider):
    if provider in KEYLESS_PROVIDERS:
        msg = ("Couldn't reach Ollama. Is it running? Start it with [bold]ollama serve[/bold] "
               "(default address http://localhost:11434, override with OLLAMA_API_BASE).")
    else:
        msg = f"Couldn't reach {provider or 'the provider'}'s servers. Check your internet connection and try again."
    console.print()
    console.print(Panel(msg, title="📡 Connection Error", border_style="red"))

def stream_completion(messages, model, reasoning_effort="medium", show_thinking=False):
    """Streams a completion from any LiteLLM-supported provider, with optional reasoning visibility.
    Never lets a provider/network error surface as a raw traceback -- always prints a clear,
    specific explanation and returns whatever partial text (if any) was generated before the failure."""
    full_content = []
    is_thinking = False
    provider = get_provider(model)

    try:
        stream = litellm.completion(
            model=model,
            messages=messages,
            stream=True,
            **build_completion_kwargs(model, reasoning_effort),
        )

        for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Capture reasoning tokens but only print if show_thinking is True.
            # (LiteLLM normalises providers' reasoning fields to `reasoning_content`.)
            reasoning_token = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if reasoning_token and show_thinking:
                if not is_thinking:
                    is_thinking = True
                    console.print()
                    console.print(Rule(title="Thinking Process", style="dim cyan"))
                console.print(reasoning_token, end="", style="dim italic", markup=False)
                sys.stdout.flush()

            # Capture standard response tokens. These are NOT printed live: rendering the reply as
            # Markdown (below, once the full text is in) needs the whole thing, and printing each raw
            # token as it arrives *and then* re-rendering the finished text would show every answer
            # twice -- once raw (literal ```fences```), once formatted. If show_thinking is on, the
            # reasoning tokens above still stream live, so there's visible progress before the answer
            # itself appears all at once.
            content_token = getattr(delta, "content", None)
            if content_token:
                full_content.append(content_token)

    except litellm.AuthenticationError:
        env_vars = KEY_ENV.get(provider)
        which = f"[bold]{env_vars[0]}[/bold]" if env_vars else "your API key"
        url = KEY_URLS.get(provider)
        console.print()
        console.print(Panel(
            f"Your {provider or 'provider'} API key was rejected.\n\n"
            f"Double-check {which} is set correctly"
            + (f", or grab a fresh key at [cyan]{url}[/cyan]" if url else "."),
            title="🔑 Authentication Failed", border_style="red"
        ))
        return "".join(full_content)
    except litellm.RateLimitError:
        console.print()
        console.print(Panel(
            f"You've hit {provider or 'the provider'}'s rate limit (or you're out of quota). "
            "Wait a moment and try again.",
            title="⏳ Rate Limited", border_style="yellow"
        ))
        return "".join(full_content)
    except litellm.Timeout:  # must come before APIConnectionError (Timeout is a subclass)
        console.print()
        console.print(Panel(
            "The request timed out.\n\nTry again, or lower the reasoning effort with [bold]/effort low[/bold].",
            title="⏱ Request Timed Out", border_style="yellow"
        ))
        return "".join(full_content)
    except litellm.APIConnectionError:
        _connection_panel(provider)
        return "".join(full_content)
    except litellm.ContextWindowExceededError:  # must come before BadRequestError (subclass)
        console.print()
        console.print(Panel(
            "That input is too large for this model's context window.\n\n"
            "Try piping less (e.g. [bold]tail -n 200 app.log | dime[/bold]) or pick a larger-context model "
            "with [bold]/model[/bold].",
            title="📏 Context Too Large", border_style="yellow"
        ))
        return "".join(full_content)
    except litellm.NotFoundError as e:
        hint = (f"Pull it first: [bold]ollama pull {model.split('/', 1)[-1]}[/bold]"
                if provider in KEYLESS_PROVIDERS else "Check the spelling, and that your account has access to it.")
        console.print()
        console.print(Panel(
            f"[bold]{model}[/bold] wasn't found.\n\n{hint}\n\n[dim]{_short(e, 300)}[/dim]",
            title="❓ Model Not Found", border_style="red"
        ))
        return "".join(full_content)
    except litellm.BadRequestError as e:
        console.print()
        console.print(Panel(
            f"The provider rejected the request:\n\n[dim]{_short(e)}[/dim]",
            title="⚠️ Bad Request", border_style="red"
        ))
        return "".join(full_content)
    except (KeyboardInterrupt, EOFError):
        console.print("\n\n[yellow]Generation interrupted.[/yellow]\n")
        return "".join(full_content)
    except Exception as e:
        if _looks_like_connection_error(e):
            _connection_panel(provider)
            return "".join(full_content)
        status = getattr(e, "status_code", None)
        what = f"The provider returned an error (HTTP {status})" if status else "Something unexpected went wrong"
        console.print()
        console.print(Panel(
            f"{what}:\n{type(e).__name__}: {_short(e)}",
            title="⚠️ API Error" if status else "⚠️ Unexpected Error", border_style="red"
        ))
        return "".join(full_content)

    if is_thinking:
        console.print("\n")
        console.print(Rule(title="Remediation", style="green"))

    full_text = "".join(full_content)
    if full_text:
        try:
            console.print(Markdown(full_text, code_theme="monokai"))
        except Exception:
            # Markdown rendering is cosmetic -- never let a rendering hiccup swallow the answer.
            console.print(full_text, markup=False)
    console.print()
    return full_text

# Code-fence languages that count as "a command the user can run".
# An untagged fence counts too. (Without the Windows tags, a ```powershell reply
# would never produce a Run/Edit/Copy prompt at all.)
SHELL_FENCE_LANGS = {"", "bash", "sh", "zsh", "shell", "console",
                     "powershell", "pwsh", "ps1", "cmd", "bat", "batch"}

def extract_command(text):
    """Returns the last executable shell block (bash/sh/PowerShell/cmd) in the reply, or None.

    Walks the text line by line so that a non-shell block earlier in the reply
    (e.g. ```python) can't be mistaken for the opening of a shell block."""
    blocks = []
    lang = None
    buf = []
    for line in text.splitlines():
        stripped = line.strip()
        if lang is None:
            if stripped.startswith("```"):
                tag = stripped[3:].strip().lower()
                lang = tag.split()[0] if tag else ""
                buf = []
        elif stripped == "```":
            if lang in SHELL_FENCE_LANGS:
                block = "\n".join(buf).strip()
                if block:
                    blocks.append(block)
            lang = None
        else:
            buf.append(line)
    return blocks[-1] if blocks else None

def build_system_prompt(model):
    """Builds the system prompt for the current model, OS and shell history."""
    recent_cmds = get_recent_shell_history(20)
    sys_context = get_system_context()
    history_block = sanitize_text("\n".join(f"- {cmd}" for cmd in recent_cmds))
    os_guidance, code_fence = get_os_guidance()

    if IS_WINDOWS and resolve_shell()[0] == "powershell":
        history_hint = ("If the user asks to search command history, suggest "
                        "`Select-String -Path (Get-PSReadLineOption).HistorySavePath -Pattern <term>` "
                        "instead of using the `history` cmdlet.")
    elif IS_WINDOWS:
        history_hint = "cmd.exe keeps no persistent history; if asked to search history, say so and suggest `doskey /history`."
    else:
        history_hint = ("If the user asks to search command history, suggest "
                        "`grep <term> ~/.bash_history ~/.zsh_history` instead of using the `history` builtin.")

    if uses_browser_search(model):
        web_search_line = ("You have the `browser_search` tool enabled. Use it autonomously if an error involves an "
                           "obscure flag, recent release change, or vendor-specific issue you need to verify.")
    else:
        web_search_line = ("You do not have a web search tool in this session. If an error depends on something you "
                           "can't be sure about (a recent release change, an obscure flag), say so instead of guessing.")

    return f"""You are 'dime', an advanced terminal debugging assistant running on {model}.
The user is working in an interactive terminal and will paste error traces, logs, or questions.

Context:
{sys_context}

Operating environment:
   {os_guidance}

Recent shell command history (oldest to newest):
{history_block}

Guidelines:
1. Deep Reasoning: Analyze ambiguous stack traces, conflicting library versions, or multi-step errors carefully.
2. Web Search: {web_search_line}
3. Output Format:
   - 1-2 sentence diagnosis of the root cause.
   - The exact remediation command inside a markdown code block (```{code_fence} ... ```).
   - Keep prose minimal and action-focused.
4. {history_hint}
"""