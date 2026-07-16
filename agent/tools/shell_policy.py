"""Safety policy helpers for the local exec tool."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import PurePath, PureWindowsPath

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
_NEVER_CONFIRM_DESTRUCTIVE = {
    "format",
    "mkfs",
    "shutdown",
    "stop-computer",
    "restart-computer",
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
_WINDOWS_PATH_REFERENCE_RE = re.compile(r"(?i)(?:^|[\s\"'(=])(?:[A-Z]:[\\/]|\\\\)")
_POSIX_PATH_REFERENCE_RE = re.compile(r"(?:^|[\s\"'(=])/(?!/)")


@dataclass(frozen=True)
class CommandPolicyResult:
    """Result of validating a model-supplied shell command."""

    allowed: bool
    code: str = "OK"
    message: str = ""
    category: str = "local"
    risk_level: str = "low"
    risk_category: str = "safe"
    required_permission: str = ""
    requires_confirmation: bool = False


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
    if _has_unscoped_path_reference(command, tokens):
        return _blocked(
            "PATH_OUTSIDE_WORKSPACE",
            "Absolute or environment-expanded paths are not supported by exec.",
            "path",
        )
    normalized_tokens = [_normalize_token(token) for token in tokens]
    normalized_text = " ".join(normalized_tokens)
    first = normalized_tokens[0] if normalized_tokens else ""

    if first in _NEVER_CONFIRM_DESTRUCTIVE:
        return _blocked("DESTRUCTIVE_COMMAND_BLOCKED", "Destructive command blocked.", "destructive")
    if first in _DESTRUCTIVE_FIRST_TOKENS or _matches_destructive_git(normalized_tokens):
        if _is_confirmable_destructive(tokens, normalized_tokens):
            return _confirmable(
                "DESTRUCTIVE_CONFIRMATION_REQUIRED",
                "Destructive workspace command requires confirmation.",
                "destructive",
            )
        return _blocked("DESTRUCTIVE_COMMAND_BLOCKED", "Destructive command blocked.", "destructive")
    if _contains_any(normalized_tokens, _DESTRUCTIVE_FIRST_TOKENS):
        return _blocked("DESTRUCTIVE_COMMAND_BLOCKED", "Destructive command blocked.", "destructive")

    if (
        first in _NETWORK_FIRST_TOKENS
        or _matches_install_or_network_command(normalized_tokens)
        or _contains_any(normalized_tokens, _NETWORK_FIRST_TOKENS)
    ):
        return _confirmable(
            "NETWORK_CONFIRMATION_REQUIRED",
            "Network or install command requires confirmation.",
            "network",
        )

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
    """Create a consistent denied policy result."""

    return CommandPolicyResult(
        allowed=False,
        code=code,
        message=message,
        category=category,
        risk_level="critical" if category in {"destructive", "environment"} else "high",
        risk_category=category,
        required_permission=(
            "tool.exec.dangerous" if category in {"destructive", "network"} else ""
        ),
    )


def _confirmable(code: str, message: str, category: str) -> CommandPolicyResult:
    """Create a high-risk result that may run only after policy confirmation."""

    return CommandPolicyResult(
        allowed=True,
        code=code,
        message=message,
        category=category,
        risk_level="high",
        risk_category=category,
        required_permission="tool.exec.dangerous",
        requires_confirmation=True,
    )


def _command_tokens(command: str) -> list[str]:
    """Split a simple command into tokens for policy checks."""

    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return re.findall(r"[^\s\"']+", command)


def _normalize_token(token: str) -> str:
    """Lowercase a command token and remove common executable suffixes."""

    normalized = token.strip().strip("\"'").lower()
    if normalized.endswith(".exe") or normalized.endswith(".cmd") or normalized.endswith(".bat"):
        normalized = normalized.rsplit(".", 1)[0]
    return normalized


def _contains_any(tokens: list[str], blocked: set[str]) -> bool:
    """Return True when any normalized token is in a blocked token set."""

    return any(token in blocked for token in tokens)


def _matches_destructive_git(tokens: list[str]) -> bool:
    """Detect git commands that can discard local work."""

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
    """Detect dependency install, clone, pull, fetch, and network commands."""

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


def _is_confirmable_destructive(raw_tokens: list[str], normalized_tokens: list[str]) -> bool:
    """Allow confirmation only when destructive effects stay rooted in the command cwd."""

    if _matches_destructive_git(normalized_tokens):
        return True
    if not normalized_tokens or normalized_tokens[0] not in _DESTRUCTIVE_FIRST_TOKENS:
        return False
    if normalized_tokens[0] in _NEVER_CONFIRM_DESTRUCTIVE:
        return False
    operands = [
        token
        for token in raw_tokens[1:]
        if token and not token.lstrip().startswith("-") and not token.lstrip().startswith("/")
    ]
    if not operands:
        return False
    return all(_is_workspace_relative_operand(token) for token in operands)


def _is_workspace_relative_operand(token: str) -> bool:
    """Reject absolute, parent-traversing, drive-qualified, and home-expanded paths."""

    value = token.strip().strip("\"'")
    if not value or value.startswith(("~", "\\\\", "//")):
        return False
    windows_path = PureWindowsPath(value)
    posix_path = PurePath(value.replace("\\", "/"))
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        return False
    return ".." not in windows_path.parts and ".." not in posix_path.parts


def _contains_unquoted_shell_syntax(command: str) -> bool:
    """Detect unsupported shell operators outside quoted strings."""

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


def _has_unscoped_path_reference(command: str, tokens: list[str]) -> bool:
    """Reject filesystem paths that cannot be proven relative to the guarded cwd."""

    lowered = command.lower()
    if "$env:" in lowered or "${env:" in lowered or re.search(r"%[A-Za-z_][A-Za-z0-9_]*%", command):
        return True
    for index, token in enumerate(tokens):
        value = token.strip().strip("\"'")
        if not value or "://" in value:
            continue
        if index == 0:
            # An absolute executable path is allowed; subprocess still runs with a guarded cwd.
            continue
        if value.startswith("~"):
            return True
        if _WINDOWS_PATH_REFERENCE_RE.search(value):
            return True
        if _POSIX_PATH_REFERENCE_RE.search(value):
            if value.lower() not in {"/s", "/q", "/f"}:
                return True
    return False
