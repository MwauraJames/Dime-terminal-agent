"""Shared test helpers: load dime under a simulated OS, and a tiny local server that impersonates
LLM providers and LangSmith so the request/response paths can be tested without network or API keys."""
import contextlib
import importlib
import json
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"   # never send mock traffic through a proxy
_MODULES = ["osinfo", "security", "session", "context", "executor", "llm"]


def load_dime(system="Linux", release="6.5.0-generic", env=None):
    """Imports fresh copies of the dime modules while pretending to be `system` (Linux/Windows) with
    kernel `release` (contains 'microsoft' under WSL). Returns one namespace holding every public name."""
    for k in [k for k in sys.modules if k == "dime" or k.startswith("dime.")]:
        del sys.modules[k]
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("platform.system", return_value=system))
        stack.enter_context(mock.patch("platform.release", return_value=release))
        stack.enter_context(mock.patch.dict(os.environ, env or {}))
        for var in ("WSL_DISTRO_NAME", "WSL_INTEROP"):
            if var not in (env or {}):
                os.environ.pop(var, None)
        mods = {name: importlib.import_module(f"dime.{name}") for name in _MODULES}
    ns = types.SimpleNamespace(**{k: v for m in mods.values() for k, v in vars(m).items() if not k.startswith("__")})
    ns.mods = types.SimpleNamespace(**mods)
    return ns


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No developer API keys / tracing settings leak into tests."""
    for k in list(os.environ):
        if k.endswith("_API_KEY") or k.startswith(("LANGSMITH", "LANGCHAIN")) or k in ("DIME_MODEL", "DIME_TRACE", "DIME_NUM_CTX"):
            monkeypatch.delenv(k, raising=False)


@pytest.fixture
def linux():
    return load_dime()


@pytest.fixture
def wsl():
    return load_dime(release="5.15.90.1-microsoft-standard-WSL2")


@pytest.fixture
def windows():
    return load_dime(system="Windows")


# ---------------------------------------------------------------------------------------------
# Fake provider / LangSmith server
# ---------------------------------------------------------------------------------------------
def _sse(chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _oa_chunk(delta, finish=None):
    return {"id": "x", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class MockServer:
    def __init__(self):
        self.hits = []
        self.status = 200        # HTTP status returned for LLM calls
        self.kind = "openai"     # "openai" (SSE) or "ollama" (NDJSON)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                try:
                    body = json.loads(raw)
                except Exception:
                    body = None
                outer.hits.append({"path": self.path, "headers": dict(self.headers), "body": body, "raw": raw})
                if "/runs/batch" in self.path:                      # LangSmith
                    self.send_response(200); self.end_headers(); self.wfile.write(b"{}"); return
                if outer.status != 200:
                    self.send_response(outer.status); self.send_header("content-type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": f"mock {outer.status} error", "type": "x"}}).encode()); return
                if outer.kind == "ollama":
                    self.send_response(200); self.send_header("content-type", "application/x-ndjson"); self.end_headers()
                    for content, done in (("ollama ok", False), ("", True)):
                        self.wfile.write((json.dumps({"model": "m", "message": {"role": "assistant", "content": content},
                                                      "done": done, **({"done_reason": "stop"} if done else {})}) + "\n").encode())
                    return
                self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers()
                self.wfile.write(_sse([
                    _oa_chunk({"role": "assistant", "reasoning": "pondering..."}),
                    _oa_chunk({"content": "Root cause: typo.\n```bash\necho fixed\n```"}),
                    _oa_chunk({}, "stop")]).encode())

            def log_message(self, *a):
                pass

        self._srv = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self._srv.server_port}"
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def llm_hits(self):
        return [h for h in self.hits if "/runs/batch" not in h["path"]]

    def trace_hits(self):
        return [h for h in self.hits if "/runs/batch" in h["path"]]


@pytest.fixture(scope="session")
def _server():
    return MockServer()


@pytest.fixture
def server(_server):
    _server.hits.clear(); _server.status = 200; _server.kind = "openai"
    return _server


@pytest.fixture(scope="session")
def llm_ns():
    """dime modules with LiteLLM loaded (loading takes ~2s, so once per test session)."""
    ns = load_dime()
    ns.load_litellm()
    return ns
