"""dime.security in isolation: no rich, no prompt_toolkit, no network, no LiteLLM."""
import subprocess
import sys

import pytest

from conftest import ROOT, load_dime

SECRETS = {
    "groq":          "gsk_" + "A1b2C3d4E5" * 5,
    "github":        "ghp_" + "a1B2c3D4e5" * 3 + "a1B2c3",
    "anthropic":     "sk-ant-api03-" + "AbC_dEf-123456789012345678901234567890",
    "openai-proj":   "sk-proj-" + "Ab_Cd-1234567890123456789012345678",
    "openai-legacy": "sk-" + "a1B2c3D4e5" * 5,
    "google":        "AIza" + "SyA1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6x",   # AIza + 35 chars
    "langsmith":     "lsv2_pt_" + "0123456789abcdef0123456789abcdef_0123456789",
    "langsmith-old": "ls__" + "0123456789abcdef0123456789abcdef",
    "aws":           "AKIA" + "IOSFODNN7EXAMPLE",
}


@pytest.mark.parametrize("name", SECRETS)
def test_api_keys_are_redacted(name):
    sec = load_dime().mods.security
    secret = SECRETS[name]
    cleaned = sec.sanitize_text(f"export KEY={secret} && run")
    assert secret not in cleaned
    assert "REDACTED" in cleaned


def test_other_secret_shapes_are_redacted():
    sec = load_dime().mods.security
    assert "hunter2" not in sec.sanitize_text('password="hunter2"')
    assert "abc.def-123" not in sec.sanitize_text("curl -H 'Authorization: Bearer abc.def-123' https://x")
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert "MIIEow" not in sec.sanitize_text(key)


@pytest.mark.parametrize("text", ["ls -la /home", "git commit -m 'skip ci'", "pip install requests", "", None])
def test_ordinary_text_is_untouched(text):
    sec = load_dime().mods.security
    assert sec.sanitize_text(text) == text


DANGEROUS_ANYWHERE = ["rm -rf /", "sudo rm -r /var", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda",
                      "DROP TABLE users;", "truncate table logs", "kubectl delete namespace prod",
                      "systemctl stop sshd", "chmod -R 777 /", "chown -R root:root /"]
SAFE = ["ls -la", "git status", "npm run format", "formatter --check .", "Get-Service Winmgmt",
        "Get-ChildItem -Recurse -Filter *.log", "Remove-Item C:\\temp\\one.txt", "Stop-Service Spooler", "dir /s"]
WINDOWS_DANGEROUS = [
    r"Remove-Item C:\data -Recurse -Force", r"Remove-Item -Recurse -Force C:\data", r"remove-item C:\data -r",
    r"rm -Recurse C:\data", r"ri -Rec C:\data", r"rd /s /q C:\data", r"rmdir /S C:\data", r"del /f /s /q C:\*",
    r"Format-Volume -DriveLetter D", r"format D: /fs:ntfs", r"Clear-Disk -Number 1 -RemoveData",
    r"Remove-Partition -DiskNumber 0 -PartitionNumber 2", "diskpart", r"reg delete HKLM\Software\Foo /f",
    "Stop-Service Winmgmt -Force", "Stop-Service -Name lanmanserver", "Set-Service -Name WinRM -StartupType Disabled",
    "sc stop Dhcp", "net stop dnscache", r"takeown /f C:\Windows /r", r"icacls C:\ /grant Everyone:(OI)(CI)F /t",
    r"icacls C:\data /grant Everyone:F /T",
]


@pytest.mark.parametrize("cmd", DANGEROUS_ANYWHERE)
def test_unix_destructive_commands_flagged_everywhere(cmd, linux, windows):
    assert linux.is_dangerous_command(cmd)
    assert windows.is_dangerous_command(cmd)


@pytest.mark.parametrize("cmd", WINDOWS_DANGEROUS)
def test_windows_destructive_commands_flagged_on_windows_and_wsl(cmd, windows, wsl):
    assert windows.is_dangerous_command(cmd)
    assert wsl.is_dangerous_command(cmd)      # WSL can call powershell.exe / cmd.exe through interop


@pytest.mark.parametrize("cmd", SAFE)
def test_no_false_positives(cmd, linux, windows):
    assert not linux.is_dangerous_command(cmd)
    assert not windows.is_dangerous_command(cmd)


def test_windows_patterns_not_applied_on_plain_linux(linux):
    assert len(linux.DANGEROUS_PATTERNS) == 10
    assert not linux.is_dangerous_command("Stop-Service Winmgmt -Force")


def test_security_module_has_no_heavy_dependencies():
    """The safety layer must stay importable (and testable) without UI or LLM libraries."""
    code = ("import sys; import dime.security; "
            "bad = [m for m in ('rich', 'prompt_toolkit', 'litellm') if m in sys.modules]; "
            "sys.exit(1 if bad else 0)")
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 0
