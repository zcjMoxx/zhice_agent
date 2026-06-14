"""Safety policy helpers for the local exec tool."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

MAX_COMMAND_CHARS = 2000

_COMPLEX_SHELL_MARKERS = ("&&", "||", "|", ";", ">", "<", "`", "$(", "\n", "\r")
_DESTRUCTIVE_FIRST_TOKENS = {
    "del",
    "erase",
    "format",
    "mkfs",
    "rd",
    "rm",
    "rmdir",
    "shutdown",
    "stop-computer",
    "restart-computer",
    "remove-item",
    "ri",
}
_NETWORK_FIRST_TOKENS = {
    "curl",
    "wget",
    "invoke-webrequest",
    "iwr",
    "invoke-restmethod",
    "irm",
}
_ENV_DUMP_FIRST_TOKENS = {"env", "printenv", "set"}

_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASS|PWD)[A-Z0-9_]*)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


@dataclass(frozen=True)
class CommandPolicyResult:
    """Result of validating a model-supplied shell command."""

    allowed: bool
    code: str = "OK"
    message: str = ""
    category: str = "local"


def validate_command(command: str) -> CommandPolicyResult:
    """Return whether a command is safe enough for first-stage local exec."""

    if not isinstance(command, str) or not command.strip():
        return _blocked("MISSING_PARAM", "Missing required parameter: command")
    if len(command) > MAX_COMMAND_CHARS:
        return _blocked("COMMAND_TOO_LONG", "Command is too long.")
    if _contains_unquoted_shell_syntax(command):
        return _blocked(
            "UNSUPPORTED_SHELL_SYNTAX",
            "Complex shell syntax is not supported by exec.",
        )

    tokens = _command_tokens(command)
    normalized_tokens = [_normalize_token(token) for token in tokens]
    normalized_text = " ".join(normalized_tokens)
    first = normalized_tokens[0] if normalized_tokens else ""

    if first in _DESTRUCTIVE_FIRST_TOKENS or _matches_destructive_git(normalized_tokens):
        return _blocked("DESTRUCTIVE_COMMAND_BLOCKED", "Destructive command blocked.", "destructive")
    if _contains_any(normalized_tokens, _DESTRUCTIVE_FIRST_TOKENS):
        return _blocked("DESTRUCTIVE_COMMAND_BLOCKED", "Destructive command blocked.", "destructive")

    if (
        first in _NETWORK_FIRST_TOKENS
        or _matches_install_or_network_command(normalized_tokens)
        or _contains_any(normalized_tokens, _NETWORK_FIRST_TOKENS)
    ):
        return _blocked("NETWORK_COMMAND_BLOCKED", "Network or install command blocked.", "network")

    if (
        first in _ENV_DUMP_FIRST_TOKENS
        or "get-childitem env:" in normalized_text
        or "gci env:" in normalized_text
        or "dir env:" in normalized_text
        or "ls env:" in normalized_text
    ):
        return _blocked("ENV_DUMP_BLOCKED", "Environment dump command blocked.", "environment")

    return CommandPolicyResult(allowed=True)


def redact_secrets(text: str) -> str:
    """Redact common secret shapes in command output."""

    redacted = _ASSIGNMENT_SECRET_RE.sub(r"\1=<redacted>", text)
    redacted = _BEARER_RE.sub(r"\1<redacted>", redacted)
    return _OPENAI_KEY_RE.sub("sk-<redacted>", redacted)


def _blocked(code: str, message: str, category: str = "local") -> CommandPolicyResult:
    return CommandPolicyResult(allowed=False, code=code, message=message, category=category)


def _command_tokens(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return re.findall(r"[^\s\"']+", command)


def _normalize_token(token: str) -> str:
    normalized = token.strip().strip("\"'").lower()
    if normalized.endswith(".exe") or normalized.endswith(".cmd") or normalized.endswith(".bat"):
        normalized = normalized.rsplit(".", 1)[0]
    return normalized


def _contains_any(tokens: list[str], blocked: set[str]) -> bool:
    return any(token in blocked for token in tokens)


def _matches_destructive_git(tokens: list[str]) -> bool:
    if not tokens or tokens[0] != "git":
        return False
    if len(tokens) >= 3 and tokens[1] == "reset" and "--hard" in tokens[2:]:
        return True
    if len(tokens) >= 2 and tokens[1] == "clean":
        return True
    if len(tokens) >= 3 and tokens[1] in {"checkout", "restore"} and "--" in tokens[2:]:
        return True
    return False


def _matches_install_or_network_command(tokens: list[str]) -> bool:
    if not tokens:
        return False
    first = tokens[0]
    if first in {"pip", "pip3"} and len(tokens) >= 2 and tokens[1] == "install":
        return True
    if first in {"python", "python3", "py"} and len(tokens) >= 4:
        if tokens[1:4] == ["-m", "pip", "install"]:
            return True
    if first in {"npm", "pnpm"} and len(tokens) >= 2 and tokens[1] in {"install", "i", "add"}:
        return True
    if first == "yarn" and len(tokens) >= 2 and tokens[1] in {"add", "install"}:
        return True
    if first == "git" and len(tokens) >= 2 and tokens[1] in {"clone", "pull", "fetch"}:
        return True
    return False


def _contains_unquoted_shell_syntax(command: str) -> bool:
    in_single = False
    in_double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "'" and not in_double:
            in_single = not in_single
            index += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if not in_single and not in_double:
            for marker in _COMPLEX_SHELL_MARKERS:
                if command.startswith(marker, index):
                    return True
            if char == "&":
                return True
        index += 1
    return False
