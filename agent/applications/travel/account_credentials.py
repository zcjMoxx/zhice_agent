"""Cross-platform environment credentials for Owner-managed travel sources."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_ENV_KEYS = {
    "ctrip": ("ZHICE_CTRIP_USERNAME", "ZHICE_CTRIP_PASSWORD"),
}


class CredentialStoreError(RuntimeError):
    """Raised when environment credentials cannot be safely read or persisted."""


@dataclass(frozen=True)
class PlatformCredential:
    """One credential kept only for the duration of an operation."""

    username: str
    password: str


class EnvironmentPlatformCredentialStore:
    """Read platform Secrets from process env or workspace ``config/.env``."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        environ: MutableMapping[str, str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.path = (self.workspace / "config" / ".env").resolve()
        try:
            self.path.relative_to(self.workspace)
        except ValueError as exc:
            raise CredentialStoreError("Platform credential path is invalid.") from exc
        self.environ = os.environ if environ is None else environ

    @property
    def available(self) -> bool:
        return True

    def save(self, provider: str, username: str, password: str) -> None:
        account = _credential(username, password)
        username_key, password_key = _env_keys(provider)
        self._rewrite(
            {
                username_key: account.username,
                password_key: account.password,
            }
        )
        self.environ[username_key] = account.username
        self.environ[password_key] = account.password

    def load(self, provider: str) -> PlatformCredential:
        username_key, password_key = _env_keys(provider)
        environment = self._environment_credential(username_key, password_key)
        if environment is not None:
            return environment
        file_credential = self._file_credential(username_key, password_key)
        if file_credential is not None:
            return file_credential
        raise CredentialStoreError("Platform credentials are not configured.")

    def delete(self, provider: str) -> bool:
        username_key, password_key = _env_keys(provider)
        source = self.source(provider)
        if source == "environment":
            raise CredentialStoreError(
                "Platform credentials are managed by the deployment environment."
            )
        assignments = _read_dotenv_assignments(self.path)
        existed = username_key in assignments or password_key in assignments
        self._rewrite({username_key: None, password_key: None})
        self.environ.pop(username_key, None)
        self.environ.pop(password_key, None)
        return existed

    def configured(self, provider: str) -> bool:
        try:
            self.load(provider)
        except CredentialStoreError:
            return False
        return True

    def source(self, provider: str) -> str:
        username_key, password_key = _env_keys(provider)
        environment = self._environment_credential(username_key, password_key)
        file_credential = self._file_credential(username_key, password_key)
        if environment is not None:
            if file_credential is not None and environment == file_credential:
                return "workspace_env"
            return "environment"
        if file_credential is not None:
            return "workspace_env"
        return ""

    def account_hint(self, provider: str) -> str:
        try:
            return _account_hint(self.load(provider).username)
        except CredentialStoreError:
            return ""

    def updated_at(self, provider: str) -> str:
        if self.source(provider) != "workspace_env":
            return ""
        try:
            modified = self.path.stat().st_mtime
        except OSError:
            return ""
        return datetime.fromtimestamp(modified, UTC).isoformat(timespec="seconds")

    def _environment_credential(
        self,
        username_key: str,
        password_key: str,
    ) -> PlatformCredential | None:
        username = self.environ.get(username_key)
        password = self.environ.get(password_key)
        if not str(username or "").strip() or not str(password or ""):
            return None
        return _credential(username, password)

    def _file_credential(
        self,
        username_key: str,
        password_key: str,
    ) -> PlatformCredential | None:
        assignments = _read_dotenv_assignments(self.path)
        username = assignments.get(username_key)
        password = assignments.get(password_key)
        if not str(username or "").strip() or not str(password or ""):
            return None
        return _credential(username, password)

    def _rewrite(self, updates: dict[str, str | None]) -> None:
        try:
            original = _read_dotenv_text(self.path) if self.path.exists() else ""
        except OSError as exc:
            raise CredentialStoreError("Runtime .env could not be read.") from exc
        kept: list[str] = []
        for line in original.splitlines():
            key = _dotenv_key(line)
            if key not in updates:
                kept.append(line)
        for key, value in updates.items():
            if value is not None:
                kept.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
        rendered = "\n".join(kept).rstrip("\n") + "\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".env-",
            dir=self.path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CredentialStoreError("Runtime .env could not be updated.") from exc


def _env_keys(provider: object) -> tuple[str, str]:
    name = _provider(provider)
    try:
        return _ENV_KEYS[name]
    except KeyError as exc:
        raise CredentialStoreError("Platform provider is unsupported.") from exc


def _provider(value: object) -> str:
    provider = str(value or "").strip().casefold()
    if not _PROVIDER_RE.fullmatch(provider):
        raise CredentialStoreError("Platform provider is invalid.")
    return provider


def _credential(username: object, password: object) -> PlatformCredential:
    account = str(username or "").strip()
    secret = str(password or "")
    if not account or len(account) > 320 or "\n" in account or "\r" in account:
        raise CredentialStoreError("Platform username is invalid.")
    if not secret or len(secret) > 4096 or "\n" in secret or "\r" in secret:
        raise CredentialStoreError("Platform password is invalid.")
    return PlatformCredential(username=account, password=secret)


def _read_dotenv_assignments(path: Path) -> dict[str, str]:
    try:
        text = _read_dotenv_text(path)
    except OSError:
        return {}
    assignments: dict[str, str] = {}
    for line in text.splitlines():
        key = _dotenv_key(line)
        if not key:
            continue
        raw = line.split("=", 1)[1].strip()
        assignments[key] = _decode_dotenv_value(raw)
    return assignments


def _read_dotenv_text(path: Path) -> str:
    payload = path.read_bytes()
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    return payload.decode("utf-8-sig")


def _dotenv_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    key = stripped.split("=", 1)[0].strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return ""
    return key


def _decode_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return value[1:-1]
        return str(decoded)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _account_hint(value: str) -> str:
    if "@" in value:
        local, _, domain = value.partition("@")
        return f"{local[:2]}***@{domain}"
    if len(value) <= 4:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


__all__ = [
    "CredentialStoreError",
    "EnvironmentPlatformCredentialStore",
    "PlatformCredential",
]
