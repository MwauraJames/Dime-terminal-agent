"""The safety layer: secret redaction and the 'blast radius' check for destructive commands.

Deliberately free of UI and network code so it can be unit-tested in isolation."""

import re

from dime.osinfo import IS_WINDOWS, IS_WSL


# 1. Define sensitive pattern matchers
REDACTION_PATTERNS = [
    # Groq API Keys
    (r"gsk_[a-zA-Z0-9]{40,}", "[REDACTED_GROQ_KEY]"),
    # GitHub Tokens
    (r"gh[pousr]_[a-zA-Z0-9]{36}", "[REDACTED_GITHUB_TOKEN]"),
    # Anthropic API Keys (contain hyphens/underscores, so the generic sk- pattern below misses them)
    (r"sk-ant-[A-Za-z0-9_\-]{20,}", "[REDACTED_ANTHROPIC_KEY]"),
    # OpenAI project / service-account / admin keys (same reason)
    (r"sk-(?:proj|svcacct|admin)-[A-Za-z0-9_\-]{20,}", "[REDACTED_OPENAI_KEY]"),
    # OpenAI API Keys
    (r"sk-[a-zA-Z0-9]{40,}", "[REDACTED_OPENAI_KEY]"),
    # Google (Gemini / AI Studio) API Keys
    (r"AIza[0-9A-Za-z_\-]{35}", "[REDACTED_GOOGLE_KEY]"),
    # LangSmith API Keys (lsv2_pt_..., lsv2_sk_..., legacy ls__...)
    (r"lsv2_[a-z]{2}_[A-Za-z0-9_]{20,}", "[REDACTED_LANGSMITH_KEY]"),
    (r"\bls__[A-Za-z0-9]{20,}", "[REDACTED_LANGSMITH_KEY]"),
    # AWS Access Keys
    (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    # Bearer tokens in headers or curls
    (r"(?i)bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer [REDACTED_TOKEN]"),
    # Generic password/secret assignments (e.g. password="mysecret")
    (r"(?i)(password|secret|token|api_key)\s*[:=]\s*['\"][^'\"]+['\"]", r"\1: [REDACTED]"),
    # RSA/Ed25519 Private Keys
    (r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]+?-----END [A-Z ]+ PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
]

# 2. Define dangerous command signatures (Blast Radius Check)
DANGEROUS_PATTERNS = [
    r"\brm\s+.*-r[fF]?\b",                  # recursive remove (rm -rf, rm -r)
    r"\bmkfs\b",                          # formatting a filesystem
    r"\bdd\s+if=.*of=.*",                 # block level copy
    r">\s*/dev/(sd|nvme|vd)[a-z0-9]*",    # overwriting disk devices directly
    r"\bdrop\s+(database|table)\b",       # SQL drops
    r"\btruncate\s+table\b",              # SQL truncate
    r"kubectl\s+delete\s+(namespace|ns)\b", # K8s namespace nuke
    r"systemctl\s+(stop|disable)\s+(sshd|network)", # Network suicide
    r"\bchmod\s+-R\s+777\b",              # recursive 777 permissions
    r"\bchown\s+-R\s+root:root\s+/",      # recursive root ownership
]

# Windows equivalents. Applied on native Windows AND under WSL, because WSL can call
# powershell.exe / cmd.exe / reg.exe directly through interop (e.g. against /mnt/c).
# All patterns are matched case-insensitively.
WINDOWS_DANGEROUS_PATTERNS = [
    # PowerShell recursive delete, including aliases (ri, rm, del, rd...) and abbreviated flags (-r, -rec, -Recurse)
    r"\b(?:Remove-Item|ri|rm|rmdir|rd|del|erase)\b.*\s-r[a-z]*\b",
    # cmd.exe recursive delete: rd /s, rmdir /s /q, del /s /f /q
    r"\b(?:rd|rmdir|del|erase)\b.*\s/s\b",
    r"\bFormat-Volume\b",                 # formatting a drive (PowerShell)
    r"\bformat(?:\.com)?\s+[a-z]:",       # formatting a drive (cmd: format D:)
    r"\bClear-Disk\b",                    # wiping a disk
    r"\bRemove-Partition\b",              # deleting partitions
    r"\bInitialize-Disk\b",               # re-initialising a disk (destroys the partition table)
    r"\bdiskpart\b",                      # interactive disk partitioning tool
    r"\breg(?:\.exe)?\s+delete\b",        # registry key deletion
    # Stopping/disabling core management + network services
    r"\b(?:Stop-Service|Set-Service|sc(?:\.exe)?\s+(?:stop|config|delete)|net\s+stop)\b.*"
    r"\b(?:Winmgmt|LanmanServer|LanmanWorkstation|Dhcp|Dnscache|WinRM|sshd|mpssvc)\b",
    r"\btakeown\b.*\s/r\b",               # recursive ownership takeover
    r"\bicacls\b.*\b(?:everyone|users):\S*f\b.*\s/t\b",  # recursive full-control for everyone (chmod -R 777 equivalent)
]

if IS_WINDOWS or IS_WSL:
    DANGEROUS_PATTERNS = DANGEROUS_PATTERNS + WINDOWS_DANGEROUS_PATTERNS

def is_dangerous_command(cmd):
    """Checks if a command hits any dangerous regex patterns."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return True
    return False

def sanitize_text(text):
    """Strips sensitive credentials from text using regex."""
    if not text:
        return text
    sanitized = text
    for pattern, replacement in REDACTION_PATTERNS:
        sanitized = re.sub(pattern, replacement, sanitized)
    return sanitized
