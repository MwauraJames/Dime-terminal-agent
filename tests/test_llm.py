"""Provider layer: model selection, per-provider request shape, error handling, LangSmith tracing, and a
full end-to-end `python -m dime` run -- all against a local mock server (no network, no real keys)."""
import os
import subprocess
import sys
import time

import pytest

from conftest import ROOT
from test_security import SECRETS


def call(ns, server, model, effort="high", think=False):
    server.hits.clear()
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "why?"}]
    with ns.mods.llm.console.capture() as cap:
        out = ns.stream_completion(msgs, model, reasoning_effort=effort, show_thinking=think)
    return out, cap.get()


# ---------------- model names and selection ----------------
@pytest.mark.parametrize("typed, expected", {
    "claude-sonnet-5": "anthropic/claude-sonnet-5", "gemini-2.5-flash": "gemini/gemini-2.5-flash",
    "gpt-5-mini": "openai/gpt-5-mini", "o3": "openai/o3", "openai/gpt-oss-120b": "groq/openai/gpt-oss-120b",
    "gpt-oss-120b": "groq/openai/gpt-oss-120b", "ollama/llama3.1": "ollama_chat/llama3.1",
    "ollama_chat/llama3.1": "ollama_chat/llama3.1", "groq/llama-3.3-70b-versatile": "groq/llama-3.3-70b-versatile",
    "anthropic/claude-sonnet-5": "anthropic/claude-sonnet-5", "  claude-haiku-4-5-20251001 ": "anthropic/claude-haiku-4-5-20251001",
}.items())
def test_normalize_model(linux, typed, expected):
    assert linux.normalize_model(typed) == expected


def test_bare_gemini_does_not_route_to_vertex(llm_ns):
    assert llm_ns.mods.llm.litellm.get_llm_provider(llm_ns.normalize_model("gemini-2.5-flash"))[1] == "gemini"


def test_model_problem_messages(llm_ns):
    p = llm_ns.model_problem("groq/openai/gpt-oss-120b")
    assert p[0].startswith("🔑") and "GROQ_API_KEY" in p[1] and "console.groq.com" in p[1]
    assert llm_ns.model_problem("ollama_chat/llama3.1") is None          # local models need no key
    assert llm_ns.model_problem("totally-unknown-model")[0].startswith("❓")


def test_gemini_accepts_either_key_variable(llm_ns, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert llm_ns.model_problem("gemini/gemini-2.5-flash") is None


def test_default_model_is_first_provider_with_a_key(llm_ns, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert llm_ns.resolve_model(None) == "anthropic/claude-sonnet-5"
    monkeypatch.setenv("GROQ_API_KEY", "g")
    assert llm_ns.resolve_model(None) == "groq/openai/gpt-oss-120b"     # Groq first: existing users unchanged


def test_flag_beats_env_var_beats_default(llm_ns, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "g"); monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("DIME_MODEL", "gpt-oss-120b")
    assert llm_ns.resolve_model(None) == "groq/openai/gpt-oss-120b"
    assert llm_ns.resolve_model("claude-sonnet-5") == "anthropic/claude-sonnet-5"


def test_missing_key_exits_with_help(llm_ns, monkeypatch):
    monkeypatch.setenv("DIME_MODEL", "gemini-2.5-flash")
    with pytest.raises(SystemExit) as e:
        llm_ns.resolve_model(None)
    assert e.value.code == 1
    monkeypatch.delenv("DIME_MODEL")
    with pytest.raises(SystemExit):
        llm_ns.resolve_model(None)                                       # no keys at all


# ---------------- what gets sent to each provider ----------------
def test_completion_kwargs(llm_ns, monkeypatch):
    k = llm_ns.build_completion_kwargs
    assert k("groq/openai/gpt-oss-120b", "high") == {"tools": [{"type": "browser_search"}], "tool_choice": "auto",
                                                     "reasoning_effort": "high", "temperature": 0.2}
    assert k("groq/llama-3.3-70b-versatile", "high") == {"temperature": 0.2}       # no Groq-only tool elsewhere
    assert k("anthropic/claude-sonnet-5", "high") == {"temperature": 0.2}          # effort would enable extended thinking
    assert k("openai/gpt-5-mini", "low") == {"reasoning_effort": "low"}            # reasoning models reject temperature
    assert k("ollama_chat/llama3.1", "high") == {"temperature": 0.2, "num_ctx": 8192}
    monkeypatch.setenv("DIME_NUM_CTX", "32768"); assert k("ollama_chat/x", "m")["num_ctx"] == 32768
    monkeypatch.setenv("DIME_NUM_CTX", "junk"); assert k("ollama_chat/x", "m")["num_ctx"] == 8192


def test_groq_default_path_over_the_wire(llm_ns, server, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test"); monkeypatch.setenv("GROQ_API_BASE", server.base)
    out, shown = call(llm_ns, server, "groq/openai/gpt-oss-120b", "high", think=True)
    h = server.hits[0]; b = h["body"]
    assert b["model"] == "openai/gpt-oss-120b" and b["stream"] is True
    assert b["tools"] == [{"type": "browser_search"}] and b["tool_choice"] == "auto"
    assert b["reasoning_effort"] == "high" and b["temperature"] == 0.2
    assert h["headers"]["Authorization"] == "Bearer gsk_test"
    assert llm_ns.extract_command(out) == "echo fixed"
    assert "pondering" in shown and "Thinking Process" in shown          # reasoning tokens surface with --think
    _, shown2 = call(llm_ns, server, "groq/openai/gpt-oss-120b", "medium", think=False)
    assert "pondering" not in shown2 and "Root cause" in shown2


def test_openai_reasoning_model_shape(llm_ns, server, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test"); monkeypatch.setenv("OPENAI_API_BASE", server.base)
    out, _ = call(llm_ns, server, "openai/gpt-5-mini", "low")
    b = server.hits[0]["body"]
    assert b["reasoning_effort"] == "low" and "temperature" not in b and "tools" not in b
    assert "Root cause" in out


def test_anthropic_request_shape(llm_ns, server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test"); monkeypatch.setenv("ANTHROPIC_API_BASE", server.base)
    call(llm_ns, server, "anthropic/claude-sonnet-5")
    h = server.hits[0]; b = h["body"]
    assert h["path"].endswith("/v1/messages") and h["headers"]["x-api-key"] == "sk-ant-test"
    assert b["system"] and any(m["role"] == "user" for m in b["messages"])           # system prompt translated
    # Newer Claude models (sonnet-5 / opus-5) reject `temperature`; drop_params makes LiteLLM omit it
    # instead of raising client-side. Older ones (haiku 4.5) still get it.
    assert "temperature" not in b and "thinking" not in b and "tools" not in b
    call(llm_ns, server, "anthropic/claude-haiku-4-5-20251001")
    assert server.hits[0]["body"]["temperature"] == 0.2


def test_gemini_request_shape(llm_ns, server, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gem-test"); monkeypatch.setenv("GEMINI_API_BASE", server.base)
    call(llm_ns, server, "gemini/gemini-2.5-flash")
    h = server.hits[0]
    assert "gemini-2.5-flash" in h["path"] and "vertex" not in h["path"].lower()
    assert h["body"]["generationConfig"]["temperature"] == 0.2


def test_ollama_request_shape(llm_ns, server, monkeypatch):
    server.kind = "ollama"; monkeypatch.setenv("OLLAMA_API_BASE", server.base)
    out, _ = call(llm_ns, server, "ollama_chat/llama3.1")
    h = server.hits[0]
    assert h["path"].endswith("/api/chat") and h["body"]["options"] == {"temperature": 0.2, "num_ctx": 8192}
    assert out == "ollama ok"


# ---------------- errors become panels, never tracebacks ----------------
@pytest.mark.parametrize("model, status, title, extras", [
    ("groq/openai/gpt-oss-120b", 401, "Authentication Failed", ["GROQ_API_KEY"]),
    ("anthropic/claude-sonnet-5", 401, "Authentication Failed", ["ANTHROPIC_API_KEY", "console.anthropic.com"]),
    ("groq/openai/gpt-oss-120b", 429, "Rate Limited", []),
    ("groq/openai/gpt-oss-120b", 404, "Model Not Found", []),
    ("groq/openai/gpt-oss-120b", 400, "Bad Request", ["mock 400"]),
    ("groq/openai/gpt-oss-120b", 500, "API Error", []),
])
def test_http_errors(llm_ns, server, monkeypatch, model, status, title, extras):
    for var in ("GROQ", "ANTHROPIC"):
        monkeypatch.setenv(f"{var}_API_KEY", "k"); monkeypatch.setenv(f"{var}_API_BASE", server.base)
    server.status = status
    out, shown = call(llm_ns, server, model)
    assert out == "" and title in shown and "Traceback" not in shown
    for s in extras:
        assert s in shown


def test_connection_failures_are_friendly(llm_ns, server, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_BASE", "http://127.0.0.1:9")
    _, shown = call(llm_ns, server, "ollama_chat/llama3.1")
    assert "Connection Error" in shown and "ollama serve" in shown
    monkeypatch.setenv("GROQ_API_KEY", "k"); monkeypatch.setenv("GROQ_API_BASE", "http://127.0.0.1:9")
    _, shown = call(llm_ns, server, "groq/openai/gpt-oss-120b")          # LiteLLM reports this one as HTTP 500
    assert "Connection Error" in shown and "HTTP 500" not in shown


# ---------------- LangSmith tracing ----------------
@pytest.fixture
def tracing(llm_ns):
    lt = llm_ns.mods.llm.litellm
    lt.success_callback = []; lt.langsmith_batch_size = None
    yield llm_ns
    lt.success_callback = []; lt.langsmith_batch_size = None


def test_tracing_off_without_key_or_when_disabled(tracing, monkeypatch):
    lt = tracing.mods.llm.litellm
    assert tracing.setup_tracing() is None and "langsmith" not in lt.success_callback
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_x")
    assert tracing.setup_tracing(disabled=True) is None                  # --no-trace
    monkeypatch.setenv("DIME_TRACE", "0")
    assert tracing.setup_tracing() is None
    assert "langsmith" not in lt.success_callback


def test_legacy_langchain_names_are_mapped(tracing, server, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_pt_legacy"); monkeypatch.setenv("LANGCHAIN_PROJECT", "myproj")
    monkeypatch.setenv("LANGCHAIN_ENDPOINT", server.base)
    for var in ("LANGSMITH_API_KEY", "LANGSMITH_PROJECT", "LANGSMITH_BASE_URL"):
        monkeypatch.delenv(var, raising=False)                           # setup_tracing() writes these; undo after the test
    assert tracing.setup_tracing() == "myproj"
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_pt_legacy" and os.environ["LANGSMITH_BASE_URL"] == server.base
    lt = tracing.mods.llm.litellm
    # batch size 1 is what makes a short-lived CLI actually deliver its traces
    assert lt.success_callback == ["langsmith"] and lt.langsmith_batch_size == 1


def test_default_project_name(tracing, monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_x")
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert tracing.setup_tracing() == "dime"
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)


# ---------------- end to end: the real CLI, as installed users run it ----------------
def _cli_env(server, **extra):
    env = {k: v for k, v in os.environ.items() if not (k.endswith("_API_KEY") or k.startswith(("LANGSMITH", "LANGCHAIN")))}
    env.update({"GROQ_API_KEY": "gsk_e2e", "GROQ_API_BASE": server.base, "TERM": "dumb", "NO_COLOR": "1",
                "SHELL": "/bin/bash", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)})
    env.update(extra)
    return env


def test_cli_end_to_end_redacts_secrets_and_delivers_trace(server):
    ant, ls = SECRETS["anthropic"], SECRETS["langsmith"]
    env = _cli_env(server, LANGSMITH_API_KEY="lsv2_pt_e2e", LANGSMITH_BASE_URL=server.base, LANGSMITH_PROJECT="e2e")
    r = subprocess.run([sys.executable, "-m", "dime", "-d", "why did this fail?"],
                       input=f"boom: auth failed using {ant}\nAPI key {ls}\n", env=env, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr
    assert "Suggested Fix" in r.stdout and "echo fixed" in r.stdout
    llm_hits, traces = server.llm_hits(), server.trace_hits()
    assert llm_hits, "no request reached the provider"
    assert traces, "LangSmith trace was not delivered before the process exited"
    for h in llm_hits + traces:
        assert ant.encode() not in h["raw"] and ls.encode() not in h["raw"], f"secret leaked to {h['path']}"
    assert b"REDACTED_ANTHROPIC_KEY" in llm_hits[0]["raw"]
    assert b"boom: auth failed" in traces[0]["raw"]                      # the (redacted) prompt is in the trace
    assert traces[0]["headers"]["x-api-key"] == "lsv2_pt_e2e"
    assert "LangSmith tracing on (project: e2e)" in r.stdout


def test_cli_no_trace_flag_sends_nothing(server):
    env = _cli_env(server, LANGSMITH_API_KEY="lsv2_pt_e2e", LANGSMITH_BASE_URL=server.base)
    r = subprocess.run([sys.executable, "-m", "dime", "--no-trace", "-d", "hello"], input="x\n", env=env,
                       capture_output=True, text=True, timeout=90)
    assert r.returncode == 0 and server.llm_hits() and not server.trace_hits()
    assert "tracing on" not in r.stdout


def test_help_is_fast_and_skips_litellm():
    r = subprocess.run([sys.executable, "-X", "importtime", "-m", "dime", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "usage: dime" in r.stdout
    assert "litellm" not in r.stderr                                     # lazy import: --help never pays the ~2s
