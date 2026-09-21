"""Loading and saving the JSON conversation state (for `dime -r`)."""

import json
from pathlib import Path


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
