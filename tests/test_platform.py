"""OS handling (native Windows / WSL / Linux): shell selection, prompts, history, clipboard, fence parsing."""
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pytest


def test_linux_defaults(linux):
    assert not linux.IS_WINDOWS and not linux.IS_WSL
    family, prefix = linux.resolve_shell()
    assert family == "posix" and prefix[1:] == ["-i", "-c"]


def test_wsl_detected_from_kernel_release(wsl):
    assert wsl.IS_WSL and not wsl.IS_WINDOWS
    guidance, fence = wsl.get_os_guidance()
    assert "/mnt/c/" in guidance and fence == "bash"
    assert "WSL" in wsl.describe_os()


def test_windows_shell_preference_order(windows):
    with mock.patch("shutil.which", side_effect=lambda e: {"powershell": r"C:\WINDOWS\powershell.exe"}.get(e)):
        family, prefix = windows.resolve_shell()
        assert family == "powershell" and prefix[1:] == ["-NoLogo", "-Command"]
        guidance, fence = windows.get_os_guidance()
        assert fence == "powershell" and "5.1" in guidance and "$env:NAME" in guidance
    with mock.patch("shutil.which", side_effect=lambda e: {"pwsh": r"C:\pwsh.exe"}.get(e)):
        assert windows.resolve_shell()[1][0].endswith("pwsh.exe")
        assert "PowerShell 7" in windows.get_os_guidance()[0]
    with mock.patch("shutil.which", return_value=None):
        family, prefix = windows.resolve_shell()
        assert family == "cmd" and prefix[-1] == "/c" and windows.get_os_guidance()[1] == "cmd"


def test_windows_11_detected_by_build_number(windows):
    with mock.patch("platform.version", return_value="10.0.22631"), mock.patch("platform.release", return_value="10"):
        assert windows.describe_os().startswith("Windows 11")


@pytest.mark.parametrize("reply, expected", [
    ("Fix:\n```powershell\nGet-Process\n```", "Get-Process"),
    ("```pwsh\nA\n```\ntext\n```cmd\nB\n```", "B"),
    ("```bash\nls\n```", "ls"),
    ("```\nplain\n```", "plain"),
    ("```python\nprint(1)\n```\nthen:\n```bash\nls -la\n```", "ls -la"),
    ("```python\nprint(1)\n```", None),
    ("no blocks", None),
    ("```PowerShell\nX\n```", "X"),
    ("```bash\nunterminated", None),
    ("```bash\nline1\nline2\n```", "line1\nline2"),
])
def test_extract_command(linux, reply, expected):
    assert linux.extract_command(reply) == expected


def test_powershell_history(windows):
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "Microsoft" / "Windows" / "PowerShell" / "PSReadLine"
        p.mkdir(parents=True)
        (p / "ConsoleHost_history.txt").write_text("a\nb\nb\nc\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {"APPDATA": t}):
            assert windows.get_recent_shell_history(10) == ["a", "b", "c"]     # de-duplicated, order kept


def test_run_silent_and_helpers(linux):
    assert linux.run_silent([sys.executable, "-c", "print('hi')"]) == "hi"
    assert linux.run_silent(["definitely-not-a-binary"]).startswith("Error")
    assert linux._head("a\nb\nc", 2) == "a\nb" and linux._tail("a\nb\nc", 2) == "b\nc"


def test_clipboard_paths(linux, wsl):
    boom = types.ModuleType("pyperclip")

    class PyperclipException(Exception):
        pass

    def fail(_):
        raise PyperclipException("no clipboard mechanism")
    boom.copy = fail
    with mock.patch.dict(sys.modules, {"pyperclip": boom}):
        with pytest.raises(linux.ClipboardUnavailable, match="no clipboard"):
            linux.copy_to_clipboard("x")
        calls = {}
        with mock.patch("shutil.which", return_value="/mnt/c/Windows/system32/clip.exe"), \
                mock.patch("subprocess.run", side_effect=lambda argv, **kw: calls.update(argv=argv, input=kw["input"])):
            wsl.copy_to_clipboard("héllo")
        assert calls["argv"] == ["clip.exe"] and calls["input"][:2] == b"\xff\xfe"    # UTF-16 with BOM
        assert calls["input"].decode("utf-16") == "héllo"
        with mock.patch("shutil.which", return_value=None), pytest.raises(wsl.ClipboardUnavailable):
            wsl.copy_to_clipboard("x")
    with mock.patch.dict(sys.modules, {"pyperclip": None}):
        with pytest.raises(linux.ClipboardUnavailable, match="uv add pyperclip"):
            linux.copy_to_clipboard("x")
