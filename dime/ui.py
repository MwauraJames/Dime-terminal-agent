"""Shared terminal UI plumbing: the one Rich console every module prints through, and the friendly
missing-dependency check.

Importing this module first (every UI-using module does) means a broken install produces a clear
message instead of a raw ModuleNotFoundError traceback."""
import platform
import sys


def print_missing_dependency(missing):
    print(f"\u274c dime is missing a required package ({missing}).", file=sys.stderr)
    print("   If you're running from source: uv sync", file=sys.stderr)
    print("   Otherwise, reinstall with:", file=sys.stderr)
    if platform.system() == "Windows":
        print("   irm https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/HEAD/install.ps1 | iex", file=sys.stderr)
    else:
        print("   curl -fsSL https://raw.githubusercontent.com/MwauraJames/Dime-terminal-agent/HEAD/install.sh | bash", file=sys.stderr)


# These are third-party packages. If they're missing (e.g. someone ran the code outside the
# installed environment), fail with a clear instruction instead of a raw traceback.
try:
    from rich.console import Console
    import prompt_toolkit  # noqa: F401  (imported only so a missing package fails here, with a clear message)
except ImportError as e:
    print_missing_dependency(getattr(e, "name", None) or str(e))
    sys.exit(1)

console = Console()
