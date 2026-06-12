"""Application configuration and path resolution.

The first-stage runtime derives every important directory from a workspace root.
This keeps CLI usage simple and gives later modules one place to read paths.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent.protocols.llm import LLMConfigurationError, LLMEndpoint


class InitConfigurationError(RuntimeError):
    """Raised when local runtime config files cannot be initialized safely."""


class DotenvConfigurationError(RuntimeError):
    """Raised when a dotenv file exists but cannot be read."""


class MissingWorkspaceError(RuntimeError):
    """Raised when no runtime workspace was provided."""


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class AppConfig:
    """Resolved filesystem layout for a ZhiCe-Agent process."""

    workspace: Path
    config_dir: Path
    prompts_dir: Path
    contexts_dir: Path
    sessions_dir: Path
    skills_dir: Path
    logs_dir: Path

    def ensure_dirs(self) -> None:
        """Create runtime directories that must exist before the CLI runs."""

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.contexts_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def load_config(workspace: str | Path | None = None) -> AppConfig:
    """Load configuration from explicit input, environment variables, and defaults."""

    workspace_value = workspace or os.getenv("ZHICE_AGENT_WORKSPACE")
    if not workspace_value:
        raise MissingWorkspaceError(
            "ZHICE_AGENT_WORKSPACE is not set. Create config/.env under the project config directory "
            "with ZHICE_AGENT_WORKSPACE=<runtime-workspace>, or set the environment variable "
            "before running zcagent."
        )
    workspace_path = _resolve_path(workspace_value)
    config_dir = _resolve_path(os.getenv("ZHICE_AGENT_CONFIG_DIR"), workspace_path / "config")
    prompts_dir = _resolve_path(os.getenv("ZHICE_AGENT_PROMPTS_DIR"), workspace_path / "prompts")
    contexts_dir = _resolve_path(
        os.getenv("ZHICE_AGENT_CONTEXTS_DIR"), workspace_path / "contexts"
    )
    skills_dir = _resolve_path(os.getenv("ZHICE_AGENT_SKILLS_DIR"), workspace_path / "skills")
    logs_dir = _resolve_path(os.getenv("ZHICE_AGENT_LOGS_DIR"), workspace_path / "logs")

    return AppConfig(
        workspace=workspace_path,
        config_dir=config_dir,
        prompts_dir=prompts_dir,
        contexts_dir=contexts_dir,
        sessions_dir=contexts_dir / "sessions",
        skills_dir=skills_dir,
        logs_dir=logs_dir,
    )


def _default_workspace() -> Path:
    """Return the project root inferred from this module location."""

    return Path(__file__).resolve().parents[1]


def _resolve_path(value: str | Path | None, default: Path | None = None) -> Path:
    """Resolve a path-like value, falling back to a provided default."""

    selected = Path(value) if value else default
    if selected is None:
        raise ValueError("path value is required")
    return selected.expanduser().resolve()


def load_llm_endpoint(config_dir: Path, name: str = "default") -> LLMEndpoint:
    """Load one LLM endpoint from config/llm_endpoints.json."""

    path = config_dir / "llm_endpoints.json"
    if not path.exists():
        raise LLMConfigurationError(
            "LLM endpoint config is missing. Create config/llm_endpoints.json from "
            "config/llm_endpoints.example.json."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMConfigurationError(f"Invalid LLM endpoint config JSON: {path}") from exc

    if not isinstance(raw, dict):
        raise LLMConfigurationError("LLM endpoint config must be a JSON object")
    if name not in raw:
        raise LLMConfigurationError(f"LLM endpoint is not configured: {name}")

    endpoint_data = raw[name]
    if not isinstance(endpoint_data, dict):
        raise LLMConfigurationError(f"LLM endpoint must be an object: {name}")
    return _endpoint_from_mapping(name, endpoint_data)


def init_runtime_files(
    config: AppConfig,
    *,
    create_env: bool = False,
    create_llm_config: bool = True,
    create_prompts: bool = True,
    endpoint_name: str = "default",
    protocol: str = "openai",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "",
    model: str = "gpt-5",
    max_tokens: int = 4096,
    temperature: float = 0.7,
    force: bool = False,
) -> list[Path]:
    """Create local runtime config templates for the second-stage runnable setup.

    The generated files are local working copies, not committed secrets. Existing
    files are preserved unless force=True so a later init cannot silently replace
    a user's real endpoint configuration. Endpoint values such as protocol,
    base_url, and model are scaffold defaults only; runtime calls always use the
    generated workspace config file.
    """

    written: list[Path] = []
    config.ensure_dirs()
    targets: list[Path] = []
    if create_env:
        targets.append(config.workspace / ".env")
    if create_llm_config:
        targets.append(config.config_dir / "llm_endpoints.json")
    if create_prompts:
        source_prompts = _default_workspace() / "prompts"
        for source in source_prompts.glob("*.md"):
            targets.append(config.prompts_dir / source.name)
    _ensure_can_write(targets, force=force)

    if create_env:
        env_path = config.workspace / ".env"
        _write_text_once(env_path, _build_env_template(config), force=force)
        written.append(env_path)
    if create_llm_config:
        llm_path = config.config_dir / "llm_endpoints.json"
        payload = {
            endpoint_name: {
                "name": endpoint_name,
                "protocol": protocol,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        }
        _write_text_once(
            llm_path,
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            force=force,
        )
        written.append(llm_path)
    if create_prompts:
        source_prompts = _default_workspace() / "prompts"
        if not source_prompts.is_dir():
            raise InitConfigurationError(f"Source prompts directory is missing: {source_prompts}")
        for source in sorted(source_prompts.glob("*.md")):
            target = config.prompts_dir / source.name
            _write_text_once(target, source.read_text(encoding="utf-8"), force=force)
            written.append(target)
    return written


def load_dotenv_file(path: Path) -> None:
    """Load KEY=VALUE pairs from a dotenv file without overriding process env."""

    for line in _read_dotenv_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = _strip_dotenv_quotes(value.strip())


def bootstrap_dotenv(
    env_file: str | Path | None = None,
    *,
    project_root: Path | None = None,
) -> Path | None:
    """Load an explicit env file or project config/.env, preserving existing env values."""

    root = project_root or _default_workspace()
    candidates = [Path(env_file).expanduser()] if env_file else [root / "config" / ".env"]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            load_dotenv_file(resolved)
            return resolved
    return None


def _read_dotenv_text(path: Path) -> str:
    """Read dotenv text saved by common Windows and UTF-8 editors."""

    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DotenvConfigurationError(
            f"Cannot read dotenv file as UTF-8 or UTF-16: {path}"
        ) from exc


def _strip_dotenv_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _ensure_can_write(paths: list[Path], *, force: bool) -> None:
    """Preflight local init writes so defaults cannot create partial config."""

    if force:
        return
    existing = [path for path in paths if path.exists()]
    if existing:
        raise InitConfigurationError(f"Refusing to overwrite existing file: {existing[0]}")


def _write_text_once(path: Path, content: str, *, force: bool) -> None:
    """Write a text file while protecting user-created local config."""

    if path.exists() and not force:
        raise InitConfigurationError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_env_template(config: AppConfig) -> str:
    """Build the minimal local .env template."""

    return f"ZHICE_AGENT_WORKSPACE={config.workspace}\n"


def _endpoint_from_mapping(name: str, data: dict[str, object]) -> LLMEndpoint:
    protocol = _resolve_endpoint_text(
        name,
        data.get("protocol") or data.get("provider") or data.get("openai_protocol"),
        "protocol",
    )
    base_url = _resolve_endpoint_text(name, data.get("base_url"), "base_url", required=False)
    if not base_url and protocol == "openai":
        base_url = _resolve_endpoint_text(name, data.get("openai_base_url"), "openai_base_url")
    if not base_url and protocol == "litellm":
        base_url = _resolve_endpoint_text(
            name,
            data.get("litellm_base_url") or data.get("llmlite_base_url"),
            "litellm_base_url",
            required=False,
        )

    endpoint = {
        "name": _resolve_endpoint_text(name, data.get("name") or name, "name"),
        "protocol": protocol,
        "base_url": base_url,
        "model": _resolve_endpoint_text(name, data.get("model"), "model"),
    }
    missing = [key for key, value in endpoint.items() if not value]
    if missing:
        raise LLMConfigurationError(
            f"LLM endpoint {name!r} is missing required fields: {', '.join(missing)}"
        )

    protocol_text = str(endpoint["protocol"])
    if protocol_text not in {"openai", "litellm"}:
        raise LLMConfigurationError(
            f"LLM endpoint {name!r} has unsupported protocol: {protocol_text}"
        )

    return LLMEndpoint(
        name=str(endpoint["name"]),
        protocol=protocol_text,
        base_url=str(endpoint["base_url"]),
        model=str(endpoint["model"]),
        api_key=_resolve_endpoint_text(name, data.get("api_key"), "api_key"),
        max_tokens=_coerce_int(data.get("max_tokens"), 4096, "max_tokens"),
        temperature=_coerce_float(data.get("temperature"), 0.7, "temperature"),
    )


def _resolve_endpoint_text(
    endpoint_name: str,
    value: object,
    field_name: str,
    *,
    required: bool = True,
) -> str:
    text = _required_text(value, field_name) if required else _optional_text(value)
    if not text:
        return text
    return _expand_env_placeholders(
        text,
        endpoint_name=endpoint_name,
        field_name=field_name,
    )


def _expand_env_placeholders(
    value: str,
    *,
    endpoint_name: str,
    field_name: str,
) -> str:
    def replace(match: re.Match[str]) -> str:
        env_name = match.group(1)
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
        raise LLMConfigurationError(
            f"LLM endpoint {endpoint_name!r} references missing environment variable "
            f"{env_name!r} in field {field_name}"
        )

    return _ENV_PATTERN.sub(replace, value)


def _optional_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise LLMConfigurationError(f"LLM endpoint is missing required field: {field_name}")
    return text


def _coerce_int(value: object, default: int, field_name: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(f"LLM endpoint field must be an integer: {field_name}") from exc


def _coerce_float(value: object, default: float, field_name: str) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(f"LLM endpoint field must be a number: {field_name}") from exc

