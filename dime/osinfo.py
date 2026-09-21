"""Platform detection: native Windows vs WSL vs POSIX, and which shell dime runs commands in.

A leaf module (stdlib only) so every other module can import it without import cycles."""

import os
import platform
import shutil
import sys


# ==========================================
# PLATFORM DETECTION
# ==========================================
# Three environments matter:
#   - native Windows (PowerShell / cmd)  -> IS_WINDOWS
#   - Linux under WSL on a Windows host  -> IS_WSL (behaves like Linux, plus a few quirks)
#   - everything else (Linux / macOS)    -> POSIX defaults
IS_WINDOWS = platform.system() == "Windows"

def _detect_wsl():
    if IS_WINDOWS:
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        # WSL1 reports "Microsoft", WSL2 reports "microsoft-standard-WSL2"
        return "microsoft" in platform.release().lower()
    except Exception:
        return False

IS_WSL = _detect_wsl()

def resolve_shell():
    """Decides which shell dime uses to execute suggested commands.

    Returns (family, argv_prefix) where the command string is appended to argv_prefix.
    family is 'powershell', 'cmd' or 'posix'. The system prompt tells the model the
    same family, so what it suggests always matches what will actually run.
    """
    if IS_WINDOWS:
        # Prefer PowerShell 7 (pwsh), then Windows PowerShell 5.1, then cmd.exe.
        # Note: PSModulePath is set in *every* Windows process (even cmd.exe), so it
        # can't be used to tell which shell the user launched dime from.
        for exe in ("pwsh", "powershell"):
            path = shutil.which(exe)
            if path:
                return "powershell", [path, "-NoLogo", "-Command"]
        return "cmd", [os.environ.get("COMSPEC", "cmd.exe"), "/c"]
    # -i loads the user's rc file so aliases/functions/builtins work
    return "posix", [os.environ.get("SHELL", "/bin/bash"), "-i", "-c"]

def describe_os():
    """Human-readable OS string for the model's context."""
    if IS_WINDOWS:
        try:
            build = int(platform.version().split(".")[-1])
        except Exception:
            build = 0
        # Older Python builds report Windows 11 as release "10", so go by build number.
        name = "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
        return f"{name} (native, build {build})" if build else f"{name} (native)"
    if IS_WSL:
        distro = os.environ.get("WSL_DISTRO_NAME", "Linux")
        return f"Linux ({distro}) under WSL on a Windows host"
    return sys.platform

def get_os_guidance():
    """Returns (guidance_text, code_fence_language) for the system prompt."""
    if IS_WINDOWS:
        family, prefix = resolve_shell()
        if family == "powershell":
            is_core = prefix[0].replace("\\", "/").rsplit("/", 1)[-1].lower().startswith("pwsh")
            version = "PowerShell 7 (pwsh)" if is_core else "Windows PowerShell 5.1"
            chaining = ("Use ';' to chain commands (&& and || are available in PowerShell 7)."
                        if is_core else
                        "Use ';' to chain commands (&& and || do NOT exist in Windows PowerShell 5.1).")
            return (
                f"The user is on native Windows, and every command you suggest is executed by {version}.\n"
                "   Suggest PowerShell cmdlets or Windows binaries only. Do NOT suggest POSIX-only tools "
                "or syntax (ls -la, grep, cat, sed, awk, export VAR=..., $(...), /dev/null, ~/.bashrc).\n"
                "   Set env vars with $env:NAME = 'value'. Use Select-String instead of grep, "
                "Get-Content instead of cat, Get-ChildItem instead of ls, Get-Process / Stop-Process for processes. "
                f"{chaining}\n"
                "   Paths use backslashes (C:\\Users\\...), and quote any path containing spaces.",
                "powershell",
            )
        return (
            "The user is on native Windows, and every command you suggest is executed by cmd.exe.\n"
            "   Suggest cmd.exe syntax and Windows binaries only (dir, findstr, type, set VAR=value, %VAR%). "
            "Do NOT suggest POSIX-only tools or syntax (ls, grep, cat, export, $(...)).",
            "cmd",
        )
    if IS_WSL:
        return (
            "The user is running Linux via WSL on a Windows host. Suggest normal Linux (POSIX shell) commands.\n"
            "   Be aware that /mnt/c/ is the Windows C: drive (so /mnt/c/Users/<name> is a Windows profile folder); "
            "files there are slow and Linux permissions (chmod/chown) may not apply. Windows executables such as "
            "explorer.exe, clip.exe and powershell.exe can be called from WSL through interop.",
            "bash",
        )
    return ("The user is on a POSIX system; suggest POSIX shell commands.", "bash")
