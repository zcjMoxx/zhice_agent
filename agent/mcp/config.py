"""Strict loader for common JSON ``mcpServers`` configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from agent.protocols.mcp import McpOAuthSpec, McpProxyMode, McpServerSpec
from agent.runtime_config import load_runtime_section

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-)?\}")
_SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_ROOT_FIELDS = {"mcpServers"}
_SERVER_FIELDS = {
    "command",
    "args",
    "cwd",
    "env",
    "url",
    "headers",
    "proxy_mode",
    "transport",
    "type",
    "oauth",
    "startup_timeout_seconds",
    "connect_timeout_seconds",
    "call_timeout_seconds",
}
_OAUTH_FIELDS = {
    "token_url",
    "access_token",
    "refresh_token",
    "client_id",
    "client_secret",
    "scope",
    "expires_at",
}


class McpConfigError(RuntimeError):
    """Structured MCP configuration failure."""

    code = "MCP_CONFIG_INVALID"


@dataclass(frozen=True)
class McpConfigLoadResult:
    """Valid MCP specs plus safely identified invalid server entries."""

    specs: tuple[McpServerSpec, ...]
    invalid_server_ids: tuple[str, ...]


def load_mcp_server_specs(config_dir: Path | str) -> tuple[McpServerSpec, ...]:
    """Load config.yml mcp.servers; a missing section disables MCP."""

    servers = _load_server_mapping(config_dir)
    return tuple(_parse_server(str(server_id), value) for server_id, value in servers.items())


def load_mcp_server_specs_isolated(config_dir: Path | str) -> McpConfigLoadResult:
    """Load valid servers while isolating failures in individual entries."""

    servers = _load_server_mapping(config_dir)
    specs: list[McpServerSpec] = []
    invalid_server_ids: list[str] = []
    for server_id, value in servers.items():
        normalized_id = str(server_id)
        try:
            specs.append(_parse_server(normalized_id, value))
        except McpConfigError:
            invalid_server_ids.append(
                normalized_id if _SERVER_ID_RE.fullmatch(normalized_id) else "<invalid>"
            )
    return McpConfigLoadResult(tuple(specs), tuple(invalid_server_ids))


def _load_server_mapping(config_dir: Path | str) -> dict[Any, Any]:
    """Validate the MCP root and return its server mapping."""

    path = Path(config_dir) / "config.yml"
    try:
        section = load_runtime_section(config_dir, "mcp", default={})
    except ValueError as exc:
        raise McpConfigError(f"Cannot read MCP config: {path.name}") from exc
    if not isinstance(section, dict):
        raise McpConfigError("MCP config root must be an object")
    if not section:
        return {}
    raw = {"mcpServers": section.get("servers", {})}
    _reject_unknown(raw, _ROOT_FIELDS, "root")
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        raise McpConfigError("mcpServers must be an object")
    if len(servers) > 16:
        raise McpConfigError("mcpServers exceeds max_servers=16")
    return servers


def _parse_server(server_id: str, raw: Any) -> McpServerSpec:
    if not _SERVER_ID_RE.fullmatch(server_id):
        raise McpConfigError(f"Invalid MCP server id: {server_id!r}")
    if not isinstance(raw, dict):
        raise McpConfigError(f"MCP server {server_id!r} must be an object")
    _reject_unknown(raw, _SERVER_FIELDS, f"mcpServers.{server_id}")
    command = _text(raw.get("command"))
    url = _text(raw.get("url"))
    explicit_transport = _text(raw.get("transport") or raw.get("type")).lower()
    if command and url:
        raise McpConfigError(f"MCP server {server_id!r} cannot define command and url together")
    if explicit_transport in {"http", "streamable-http", "streamable_http"}:
        transport = "streamable_http"
    elif explicit_transport in {"stdio", "sse"}:
        transport = explicit_transport
    elif explicit_transport:
        raise McpConfigError(f"Unsupported MCP transport for {server_id!r}: {explicit_transport}")
    elif command:
        transport = "stdio"
    elif url:
        transport = "streamable_http"
    else:
        raise McpConfigError(f"MCP server {server_id!r} requires command or url")
    if transport == "stdio" and not command:
        raise McpConfigError(f"stdio MCP server {server_id!r} requires command")
    if transport != "stdio" and not _valid_http_url(url):
        raise McpConfigError(f"Remote MCP server {server_id!r} requires an http(s) URL")
    proxy_mode = _proxy_mode(raw.get("proxy_mode"), server_id)
    if transport == "stdio" and "proxy_mode" in raw:
        raise McpConfigError(
            f"MCP server {server_id!r} proxy_mode is only valid for remote transports"
        )
    args = raw.get("args", [])
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise McpConfigError(f"MCP server {server_id!r} args must be a string array")
    cwd = _expand(_text(raw.get("cwd")), f"{server_id}.cwd")
    if cwd and (Path(cwd).is_absolute() or ".." in Path(cwd).parts):
        raise McpConfigError(
            f"MCP server {server_id!r} cwd must be relative to its temp sandbox"
        )
    return McpServerSpec(
        server_id=server_id,
        transport=transport,  # type: ignore[arg-type]
        command=_expand(command, f"{server_id}.command"),
        args=tuple(_expand(item, f"{server_id}.args") for item in args),
        cwd=cwd,
        env=_string_map(raw.get("env"), f"{server_id}.env"),
        url=_expand(url, f"{server_id}.url"),
        headers=_string_map(raw.get("headers"), f"{server_id}.headers"),
        proxy_mode=proxy_mode,
        oauth=_parse_oauth(server_id, raw.get("oauth")),
        startup_timeout_seconds=_positive_number(raw.get("startup_timeout_seconds"), 15, server_id),
        connect_timeout_seconds=_positive_number(raw.get("connect_timeout_seconds"), 15, server_id),
        call_timeout_seconds=_positive_number(raw.get("call_timeout_seconds"), 60, server_id),
    )


def _parse_oauth(server_id: str, raw: Any) -> McpOAuthSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise McpConfigError(f"MCP server {server_id!r} oauth must be an object")
    _reject_unknown(raw, _OAUTH_FIELDS, f"mcpServers.{server_id}.oauth")
    token_url = _expand(_text(raw.get("token_url")), f"{server_id}.oauth.token_url")
    if not _valid_http_url(token_url):
        raise McpConfigError(f"MCP server {server_id!r} oauth.token_url must be http(s)")
    expires_at_raw = raw.get("expires_at")
    try:
        expires_at = float(expires_at_raw) if expires_at_raw is not None else None
    except (TypeError, ValueError) as exc:
        raise McpConfigError(f"MCP server {server_id!r} oauth.expires_at must be numeric") from exc
    values = {
        key: _expand(_text(raw.get(key)), f"{server_id}.oauth.{key}")
        for key in _OAUTH_FIELDS - {"expires_at"}
    }
    if not values["access_token"] and not values["refresh_token"]:
        raise McpConfigError(f"MCP server {server_id!r} oauth requires access_token or refresh_token")
    return McpOAuthSpec(expires_at=expires_at, **values)


def _string_map(raw: Any, field: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise McpConfigError(f"{field} must be a string map")
    return {key: _expand(value, f"{field}.{key}") for key, value in raw.items()}


def _expand(value: str, field: str) -> str:
    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.getenv(env_name)
        if match.group(2) == ":-" and not env_value:
            return ""
        if env_value is None or env_value == "":
            raise McpConfigError(f"Missing environment variable {env_name!r} for {field}")
        return env_value

    return _ENV_PATTERN.sub(replace, value)


def _positive_number(raw: Any, default: float, server_id: str) -> float:
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise McpConfigError(f"MCP timeout for {server_id!r} must be numeric") from exc
    if value <= 0 or value > 3600:
        raise McpConfigError(f"MCP timeout for {server_id!r} must be within (0, 3600]")
    return value


def _proxy_mode(raw: Any, server_id: str) -> McpProxyMode:
    if raw is None:
        return "direct"
    if not isinstance(raw, str) or raw.strip().lower() not in {"direct", "environment"}:
        raise McpConfigError(
            f"MCP proxy_mode for {server_id!r} must be 'direct' or 'environment'"
        )
    return cast(McpProxyMode, raw.strip().lower())


def _reject_unknown(raw: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise McpConfigError(f"Unknown MCP config fields at {field}: {', '.join(unknown)}")


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
