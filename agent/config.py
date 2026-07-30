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


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ENDPOINT_ROUTE_KEYS = {
    "protocol",
    "provider",
    "base_url",
    "model",
    "supported_models",
    "api_key",
    "context_window",
    "max_tokens",
    "pricing",
}


@dataclass(frozen=True)
class AppConfig:
    """Resolved filesystem layout for a ZhiCe-Agent process."""

    workspace: Path
    config_dir: Path
    prompts_dir: Path
    contexts_dir: Path
    sessions_dir: Path
    extends_dir: Path
    logs_dir: Path

    @property
    def state_dir(self) -> Path:
        """Runtime state directory for auth and other local databases."""

        return self.workspace / "state"

    @property
    def auth_db_path(self) -> Path:
        """SQLite path for the Part 9 local auth and audit store."""

        return self.state_dir / "auth.sqlite3"

    @property
    def mcp_config_path(self) -> Path:
        """Workspace runtime MCP configuration path."""

        return self.config_dir / "config.yml"

    @property
    def hooks_config_path(self) -> Path:
        """Workspace runtime Tool Hook configuration path."""

        return self.config_dir / "config.yml"

    @property
    def channels_config_path(self) -> Path:
        """Workspace runtime external-channel configuration path."""

        return self.config_dir / "config.yml"

    @property
    def mcp_runtime_dir(self) -> Path:
        """Workspace-shared MCP process and temporary state root."""

        return self.state_dir / "mcp_runtime"

    @property
    def users_contexts_dir(self) -> Path:
        """Root of per-user Web/external-channel contexts."""

        return self.contexts_dir / "users"

    @property
    def shared_readonly_dir(self) -> Path:
        """Shared context directory exposed read-only to ordinary users."""

        return self.contexts_dir / "shared" / "readonly"

    @property
    def local_memory_dir(self) -> Path:
        """Workspace-operator Memory shared by CLI and Owner Web."""

        return self.contexts_dir / "memory"

    def ensure_dirs(self) -> None:
        """Create runtime directories that must exist before the CLI runs."""

        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.prompts_dir.mkdir(parents=True, exist_ok=True)
        self.contexts_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.local_memory_dir.mkdir(parents=True, exist_ok=True)
        self.extends_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def load_config(workspace: str | Path | None = None) -> AppConfig:
    """Load configuration from explicit input, environment variables, and defaults."""

    workspace_value = workspace or os.getenv("ZHICE_AGENT_WORKSPACE") or _default_runtime_workspace()
    workspace_path = _resolve_path(workspace_value)
    config_dir = _resolve_path(os.getenv("ZHICE_AGENT_CONFIG_DIR"), workspace_path / "config")
    prompts_dir = _resolve_path(os.getenv("ZHICE_AGENT_PROMPTS_DIR"), workspace_path / "prompts")
    contexts_dir = _resolve_path(
        os.getenv("ZHICE_AGENT_CONTEXTS_DIR"), workspace_path / "contexts"
    )
    extends_dir = _resolve_path(os.getenv("ZHICE_AGENT_EXTENDS_DIR"), workspace_path / "extends")
    logs_dir = _resolve_path(os.getenv("ZHICE_AGENT_LOGS_DIR"), workspace_path / "logs")

    return AppConfig(
        workspace=workspace_path,
        config_dir=config_dir,
        prompts_dir=prompts_dir,
        contexts_dir=contexts_dir,
        sessions_dir=contexts_dir / "sessions",
        extends_dir=extends_dir,
        logs_dir=logs_dir,
    )


def _project_root() -> Path:
    """Return the installed/source project root containing bundled templates."""

    return Path(__file__).resolve().parents[1]


def _default_runtime_workspace() -> Path:
    """Return the shared local/Linux/Docker default runtime workspace."""

    return Path.home() / ".zhice"


def _resolve_path(value: str | Path | None, default: Path | None = None) -> Path:
    """Resolve a path-like value, falling back to a provided default."""

    selected = Path(value) if value else default
    if selected is None:
        raise ValueError("path value is required")
    return selected.expanduser().resolve()


def load_llm_endpoint(config_dir: Path, name: str = "default") -> LLMEndpoint:
    """Load one endpoint by name, resolving aliases such as ``default`` first."""

    name = resolve_llm_endpoint_alias(config_dir, name)
    endpoints = load_llm_endpoints(config_dir)
    for endpoint in endpoints:
        if endpoint.name == name:
            return endpoint
    raise LLMConfigurationError(f"LLM endpoint is not configured: {name}")


def resolve_llm_endpoint_alias(config_dir: Path, name: str | None) -> str:
    """Resolve a user-facing endpoint name to the real endpoint key.

    ``auto`` means the caller did not choose a concrete endpoint yet. A top-level
    alias such as ``"default": "openai_gpt5"`` resolves to ``openai_gpt5``.
    """

    endpoint_name, _ = resolve_model_route(config_dir, name)
    return endpoint_name


def resolve_model_route(config_dir: Path, name: str | None) -> tuple[str, str]:
    """Resolve a route or endpoint/model string into its two explicit parts."""

    endpoint_name = (name or "").strip()
    if not endpoint_name or endpoint_name == "auto":
        return "", ""

    raw = _load_llm_endpoint_config(config_dir)
    aliases = _endpoint_aliases(raw)
    seen: set[str] = set()
    while endpoint_name in aliases:
        # Follow alias chains like default -> primary -> openai_gpt5.
        if endpoint_name in seen:
            raise LLMConfigurationError(f"LLM endpoint alias cycle detected: {endpoint_name}")
        seen.add(endpoint_name)
        endpoint_name = aliases[endpoint_name]
    endpoint, separator, model = endpoint_name.partition("/")
    if not endpoint.strip() or (separator and not model.strip()):
        raise LLMConfigurationError(f"Invalid model route: {endpoint_name!r}")
    return endpoint.strip(), model.strip()


def load_llm_endpoints(config_dir: Path) -> list[LLMEndpoint]:
    """Load all chat endpoint objects from config/models.json."""

    raw = _load_llm_endpoint_config(config_dir)
    endpoints = [
        _endpoint_from_mapping(name, data)
        for name, data in _iter_endpoint_mappings(raw)
    ]
    if not endpoints:
        raise LLMConfigurationError("LLM endpoint config does not contain any endpoints")
    return endpoints


def _load_llm_endpoint_config(config_dir: Path) -> dict[str, object]:
    """Read config/models.json and expose its chat endpoints plus routing aliases."""

    path = config_dir / "models.json"
    if not path.exists():
        raise LLMConfigurationError(
            "Model config is missing. Create config/models.json from config/models.example.json."
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMConfigurationError(f"Invalid model config JSON: {path}") from exc

    if not isinstance(raw, dict):
        raise LLMConfigurationError("models.json must be a JSON object")
    if raw.get("schema_version", 1) != 1:
        raise LLMConfigurationError("models.json schema_version must be 1")
    chat = raw.get("chat", {})
    routing = raw.get("routing", {})
    if not isinstance(chat, dict) or not isinstance(routing, dict):
        raise LLMConfigurationError("models.json chat and routing must be objects")
    normalized: dict[str, object] = dict(chat)
    for alias in ("chat", "compaction"):
        value = routing.get(alias)
        if isinstance(value, str) and value.strip():
            normalized["default" if alias == "chat" else alias] = value.strip()
    return normalized


def init_runtime_files(
    config: AppConfig,
    *,
    create_env: bool = True,
    create_llm_config: bool = True,
    create_skill_sources_config: bool = True,
    create_channels_config: bool = True,
    create_context_config: bool = True,
    create_embedding_config: bool = True,
    create_prompts: bool = True,
    endpoint_name: str = "default",
    protocol: str = "openai",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "",
    model: str = "gpt-5.5",
    max_tokens: int = 16384,
    context_window: int = 131072,
    temperature: float = 0.7,
    force: bool = False,
) -> list[Path]:
    """Create local runtime config templates for the second-stage runnable setup.

    The generated files are local working copies, not committed secrets. Existing
    files are skipped unless force=True so rerunning init can fill missing files
    without replacing a user's real endpoint configuration. Endpoint values such
    as protocol, base_url, and model are scaffold defaults only; runtime calls
    always use the generated workspace config file.
    """

    written: list[Path] = []
    del create_env  # Retained only for source compatibility; env initialization is standard.
    config.ensure_dirs()
    _validate_init_token_budget(
        context_window=context_window,
        max_tokens=max_tokens,
    )

    env_path = config.config_dir / ".env"
    if _write_text_once(env_path, _build_env_template(), force=force):
        written.append(env_path)
    if create_llm_config or create_embedding_config:
        llm_path = config.config_dir / "models.json"
        endpoint_payload = {
            "protocol": protocol,
            "provider": "",
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "supported_models": [model],
            "max_tokens": max_tokens,
            "context_window": context_window,
            "temperature": temperature,
            "priority": 1,
            "enabled": True,
            "role": "default",
            "pricing": {
                "input_per_million": 0,
                "output_per_million": 0,
            },
        }
        route = f"{endpoint_name}/{model}"
        payload = {
            "schema_version": 1,
            "routing": {
                "chat": route,
                "compaction": route,
                "embedding": "default_embedding",
            },
            "chat": {endpoint_name: endpoint_payload},
            "embedding": {
                "default_embedding": {
                    "protocol": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "api_key": "${ZHICE_EMBEDDING_API_KEY}",
                    "model": "text-embedding-3-small",
                    "supported_models": ["text-embedding-3-small"],
                    "dimensions": 1536,
                    "timeout": 30,
                    "batch_size": 16,
                    "enabled": False,
                }
            },
        }
        if _write_text_once(
            llm_path,
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            force=force,
        ):
            written.append(llm_path)
    if any(
        (
            create_skill_sources_config,
            create_channels_config,
            create_context_config,
        )
    ):
        source = _project_root() / "config" / "config.example.yml"
        if not source.is_file():
            raise InitConfigurationError(f"Unified config template is missing: {source}")
        target = config.config_dir / "config.yml"
        if _write_text_once(target, source.read_text(encoding="utf-8"), force=force):
            written.append(target)
    if create_prompts:
        source_prompts = _project_root() / "prompts"
        if not source_prompts.is_dir():
            raise InitConfigurationError(f"Source prompts directory is missing: {source_prompts}")
        for source in sorted(source_prompts.glob("*.md")):
            target = config.prompts_dir / source.name
            if _write_text_once(target, source.read_text(encoding="utf-8"), force=force):
                written.append(target)
    return written


def load_dotenv_file(path: Path, *, excluded_keys: set[str] | None = None) -> None:
    """Load KEY=VALUE pairs without overriding process env or excluded bootstrap keys."""

    excluded = excluded_keys or set()
    for line in _read_dotenv_text(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key and key not in excluded and key not in os.environ:
            os.environ[key] = _strip_dotenv_quotes(value.strip())


def bootstrap_dotenv(
    env_file: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
    project_root: Path | None = None,
) -> Path | None:
    """Load explicit or workspace runtime env before the remaining config files."""

    if env_file is not None:
        resolved = Path(env_file).expanduser().resolve()
        if resolved.exists():
            load_dotenv_file(resolved)
            return resolved
        return None

    workspace_root = _resolve_path(
        workspace or os.getenv("ZHICE_AGENT_WORKSPACE") or _default_runtime_workspace()
    )
    runtime_env = workspace_root / "config" / ".env"
    if runtime_env.exists():
        load_dotenv_file(runtime_env, excluded_keys={"ZHICE_AGENT_WORKSPACE"})
        return runtime_env

    # Migration-only fallback for existing source checkouts. New installations
    # and initialized workspaces no longer depend on a project-local real env.
    legacy_env = (project_root or _project_root()) / "config" / ".env"
    if legacy_env.exists():
        load_dotenv_file(legacy_env)
        return legacy_env.resolve()
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
    """Remove matching single or double quotes around one dotenv value."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _write_text_once(path: Path, content: str, *, force: bool) -> bool:
    """Write a text file, returning False when an existing file is preserved."""

    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _build_env_template() -> str:
    """Read the repository's single public runtime env template as UTF-8."""

    source = _project_root() / "config" / ".env.example"
    if not source.is_file():
        raise InitConfigurationError(f"Runtime env template is missing: {source}")
    try:
        return source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise InitConfigurationError(
            f"Runtime env template is not valid UTF-8: {source}"
        ) from exc


def _validate_init_token_budget(
    *,
    context_window: int,
    max_tokens: int,
) -> None:
    """Reject init arguments that would generate an unusable endpoint config."""

    if context_window < 1:
        raise InitConfigurationError("context_window must be >= 1")
    if max_tokens < 1:
        raise InitConfigurationError("max_tokens must be >= 1")
    if max_tokens >= context_window:
        raise InitConfigurationError("max_tokens must be less than context_window")


def _endpoint_from_mapping(name: str, data: dict[str, object]) -> LLMEndpoint:
    """Convert one JSON endpoint object into the shared LLMEndpoint shape.

    For keyed configs, ``name`` is the outer JSON key. The endpoint body should
    not repeat it. LiteLLM keeps ``provider`` and plain ``model`` separate here;
    the provider adapter joins them only when it calls the LiteLLM SDK.
    """

    protocol = _resolve_endpoint_protocol(name, data)
    base_url = _resolve_endpoint_text(name, data.get("base_url"), "base_url", required=False)
    provider = _resolve_endpoint_text(name, data.get("provider"), "provider", required=False)
    model = _resolve_endpoint_text(name, data.get("model"), "model")
    supported_models = _resolve_supported_models(name, data.get("supported_models"))
    protocol_text = str(protocol)

    endpoint = {
        "name": _resolve_endpoint_text(name, name, "name"),
        "protocol": protocol,
        "base_url": base_url,
        "provider": provider,
        "model": model,
    }
    required_endpoint_keys = ["name", "protocol", "model"]
    # OpenAI-compatible endpoints need a concrete API base URL. LiteLLM direct
    # SDK endpoints need a provider prefix such as "anthropic".
    if protocol_text == "openai":
        required_endpoint_keys.append("base_url")
    if protocol_text == "litellm":
        required_endpoint_keys.append("provider")
    missing = [key for key in required_endpoint_keys if not endpoint[key]]
    if missing:
        raise LLMConfigurationError(
            f"LLM endpoint {name!r} is missing required fields: {', '.join(missing)}"
        )

    if protocol_text not in {"openai", "litellm"}:
        raise LLMConfigurationError(
            f"LLM endpoint {name!r} has unsupported protocol: {protocol_text}"
        )
    if "/" in model:
        raise LLMConfigurationError(
            f"LLM endpoint {name!r} model should be an unprefixed model name"
        )
    priority = _coerce_int(data.get("priority"), 1, "priority")
    if priority < 1:
        raise LLMConfigurationError("LLM endpoint field must be >= 1: priority")
    max_tokens = _coerce_int(data.get("max_tokens"), 4096, "max_tokens")
    if max_tokens < 1:
        raise LLMConfigurationError("LLM endpoint field must be >= 1: max_tokens")
    context_window = _coerce_int(data.get("context_window"), 131072, "context_window")
    if context_window < 1:
        raise LLMConfigurationError("LLM endpoint field must be >= 1: context_window")
    if max_tokens >= context_window:
        raise LLMConfigurationError(
            "LLM endpoint max_tokens must be less than context_window"
        )
    request_timeout_seconds = _positive_float(
        data.get("request_timeout_seconds"), 60.0, "request_timeout_seconds"
    )
    max_attempts = _coerce_int(data.get("max_attempts"), 2, "max_attempts")
    if max_attempts < 1:
        raise LLMConfigurationError("LLM endpoint field must be >= 1: max_attempts")
    if max_attempts > 5:
        raise LLMConfigurationError("LLM endpoint field must be <= 5: max_attempts")
    total_deadline_seconds = _positive_float(
        data.get("total_deadline_seconds"), 120.0, "total_deadline_seconds"
    )
    retry_backoff_seconds = _positive_float(
        data.get("retry_backoff_seconds"), 0.5, "retry_backoff_seconds", allow_zero=True
    )
    retry_backoff_max_seconds = _positive_float(
        data.get("retry_backoff_max_seconds"),
        8.0,
        "retry_backoff_max_seconds",
        allow_zero=True,
    )
    if retry_backoff_max_seconds < retry_backoff_seconds:
        raise LLMConfigurationError(
            "LLM endpoint retry_backoff_max_seconds must be >= retry_backoff_seconds"
        )
    retry_jitter_ratio = _coerce_float(
        data.get("retry_jitter_ratio"), 0.1, "retry_jitter_ratio"
    )
    if not 0 <= retry_jitter_ratio <= 1:
        raise LLMConfigurationError(
            "LLM endpoint retry_jitter_ratio must be between 0 and 1"
        )
    cooldown_seconds = _positive_float(
        data.get("cooldown_seconds"), 30.0, "cooldown_seconds", allow_zero=True
    )

    return LLMEndpoint(
        name=str(endpoint["name"]),
        protocol=protocol_text,
        base_url=str(endpoint["base_url"]),
        model=str(endpoint["model"]),
        api_key=_resolve_endpoint_text(name, data.get("api_key"), "api_key"),
        context_window=context_window,
        provider=str(endpoint["provider"]),
        max_tokens=max_tokens,
        temperature=_coerce_float(data.get("temperature"), 0.7, "temperature"),
        priority=priority,
        enabled=_coerce_bool(data.get("enabled"), True, "enabled"),
        role=_resolve_endpoint_text(name, data.get("role") or "default", "role"),
        supported_models=supported_models,
        input_price_per_million=_price(data, "input_per_million"),
        output_price_per_million=_price(data, "output_per_million"),
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
        total_deadline_seconds=total_deadline_seconds,
        retry_backoff_seconds=retry_backoff_seconds,
        retry_backoff_max_seconds=retry_backoff_max_seconds,
        retry_jitter_ratio=retry_jitter_ratio,
        cooldown_seconds=cooldown_seconds,
    )


def _positive_float(
    value: object,
    default: float,
    field: str,
    *,
    allow_zero: bool = False,
) -> float:
    result = _coerce_float(value, default, field)
    minimum_ok = result >= 0 if allow_zero else result > 0
    if not minimum_ok:
        comparator = ">= 0" if allow_zero else "> 0"
        raise LLMConfigurationError(f"LLM endpoint field must be {comparator}: {field}")
    return result


def _price(data: dict[str, object], field: str) -> float:
    pricing = data.get("pricing", {})
    if not isinstance(pricing, dict):
        raise LLMConfigurationError("LLM endpoint pricing must be an object")
    value = _coerce_float(pricing.get(field), 0.0, f"pricing.{field}")
    if value < 0:
        raise LLMConfigurationError(f"LLM endpoint price must be non-negative: {field}")
    return value


def _resolve_supported_models(endpoint_name: str, value: object) -> tuple[str, ...]:
    """Parse optional model-switch allowlist for one endpoint.

    These names stay local and unprefixed. For LiteLLM, ``provider/model`` is
    built later by LiteLLMProvider when a request is made.
    """

    if value is None:
        return ()
    if not isinstance(value, list):
        raise LLMConfigurationError(
            f"LLM endpoint {endpoint_name!r} field must be a list: supported_models"
        )
    models: list[str] = []
    for index, item in enumerate(value):
        model = _resolve_endpoint_text(endpoint_name, item, f"supported_models[{index}]")
        if model:
            if "/" in model:
                raise LLMConfigurationError(
                    f"LLM endpoint {endpoint_name!r} supported model should be unprefixed: "
                    f"supported_models[{index}]"
                )
            models.append(model)
    return tuple(models)


def _iter_endpoint_mappings(raw: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Normalize the two supported JSON layouts into ``(name, data)`` pairs.

    Keyed object layout infers the endpoint name from the outer key. List layout
    has no outer key, so each item must carry its own ``name``.
    """

    if "endpoints" in raw:
        entries = raw["endpoints"]
        if not isinstance(entries, list):
            raise LLMConfigurationError("LLM endpoint config field must be a list: endpoints")
        mappings: list[tuple[str, dict[str, object]]] = []
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                raise LLMConfigurationError(f"LLM endpoint must be an object: endpoints[{index}]")
            name = _required_text(item.get("name"), f"endpoints[{index}].name")
            mappings.append((name, item))
        return mappings

    mappings = []
    for key, value in raw.items():
        # Skip comments, aliases such as "default": "openai_gpt5", and any
        # metadata objects that do not look like endpoint definitions.
        if key.startswith("_") or not isinstance(value, dict):
            continue
        if not _looks_like_endpoint(value):
            continue
        mappings.append((key, value))
    return mappings


def _endpoint_aliases(raw: dict[str, object]) -> dict[str, str]:
    """Collect top-level aliases, for example ``default -> openai_gpt5``."""

    aliases: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            target = value.strip()
            if target:
                aliases[key] = target
            continue
        if isinstance(value, dict) and not _looks_like_endpoint(value):
            target = _optional_text(value.get("ref") or value.get("alias") or value.get("endpoint"))
            if target:
                aliases[key] = target
    return aliases


def _looks_like_endpoint(data: dict[str, object]) -> bool:
    """Return True when a dict has fields that identify an endpoint body."""

    return any(key in data for key in _ENDPOINT_ROUTE_KEYS)


def _resolve_endpoint_protocol(name: str, data: dict[str, object]) -> str:
    """Read the required local adapter name: ``openai`` or ``litellm``."""

    return _resolve_endpoint_text(name, data.get("protocol"), "protocol")


def _resolve_endpoint_text(
    endpoint_name: str,
    value: object,
    field_name: str,
    *,
    required: bool = True,
) -> str:
    """Read a text field, enforce required values, and expand ${ENV_VAR}."""

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
    """Replace ${ENV_VAR} placeholders inside one endpoint text value."""

    def replace(match: re.Match[str]) -> str:
        """Return the environment value for one regex placeholder match."""

        # re.sub calls this function for each ${ENV_VAR} match and passes the
        # match object; group(1) is the variable name inside the braces.
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
    """Convert an optional config value to stripped text, or empty string."""

    if value is None:
        return ""
    return str(value).strip()


def _required_text(value: object, field_name: str) -> str:
    """Convert a required config value to text, raising when it is empty."""

    text = _optional_text(value)
    if not text:
        raise LLMConfigurationError(f"LLM endpoint is missing required field: {field_name}")
    return text


def _coerce_int(value: object, default: int, field_name: str) -> int:
    """Convert an optional endpoint field to int with a default."""

    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(f"LLM endpoint field must be an integer: {field_name}") from exc


def _coerce_float(value: object, default: float, field_name: str) -> float:
    """Convert an optional endpoint field to float with a default."""

    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise LLMConfigurationError(f"LLM endpoint field must be a number: {field_name}") from exc


def _coerce_bool(value: object, default: bool, field_name: str) -> bool:
    """Convert an optional endpoint field to bool with common string aliases."""

    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise LLMConfigurationError(f"LLM endpoint field must be a boolean: {field_name}")
