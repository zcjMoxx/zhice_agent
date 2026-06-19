"""Command-line entrypoint for the no-tool ZhiCe-Agent runtime."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime

from agent.config import (
    DotenvConfigurationError,
    InitConfigurationError,
    MissingWorkspaceError,
    bootstrap_dotenv,
    init_runtime_files,
    load_config,
    load_llm_endpoints,
    resolve_llm_endpoint_alias,
)
from agent.console import Spinner, console
from agent.context import ContextBuilder
from agent.gateway import format_gateway_check, run_gateway
from agent.llm import LLMConfigurationError, create_llm_provider_chain
from agent.loop import AgentLoop
from agent.message import Message
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.session import JsonlSessionStore
from agent.tools import create_default_tool_registry

DEFAULT_PROMPTS = ["identity", "tool_use_policy", "skills_intro"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the minimal interactive CLI."""

    argv_list = list(argv) if argv is not None else sys.argv[1:]
    env_file, argv_list = _extract_env_file(argv_list)
    try:
        bootstrap_dotenv(env_file)
    except DotenvConfigurationError as exc:
        print(console.error(str(exc)))
        print(console.warning("Save config/.env as UTF-8, or recreate it from config/.env.example."))
        return 1
    if argv_list and argv_list[0] == "init":
        return _run_init(argv_list[1:])
    if argv_list and argv_list[0] == "gateway":
        return _run_gateway(argv_list[1:])
    return _run_chat(argv_list)


def _run_chat(argv: Sequence[str]) -> int:
    """Run the default interactive chat command."""

    parser = argparse.ArgumentParser(prog="zcagent")
    parser.add_argument(
        "--session",
        default="default",
        help="Session id to resume. Defaults to the stable local session: default.",
    )
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    parser.add_argument(
        "--endpoint",
        default="auto",
        help="Preferred LLM endpoint name. Defaults to config default alias or priority order.",
    )
    args = parser.parse_args(argv)
    session_id = args.session

    try:
        config = load_config(args.workspace)
    except MissingWorkspaceError as exc:
        _print_workspace_error(str(exc))
        return 1
    if not _ensure_runtime_dirs(config):
        return 1

    prompt_loader = PromptLoader(config.prompts_dir)
    session_store = JsonlSessionStore(config.sessions_dir)
    context_builder = ContextBuilder(prompt_loader)
    llm = _build_llm_provider(config.config_dir, args.endpoint)
    tool_registry = create_default_tool_registry(config.workspace)
    agent_loop = AgentLoop(
        llm=llm,
        sessions=session_store,
        context_builder=context_builder,
        workspace=config.workspace,
        tools=tool_registry,
    )
    if not _load_startup_prompts(prompt_loader):
        return 1

    print(console.bold("ZhiCe-Agent"))
    print(f"workspace: {console.path(config.workspace)}")
    print(f"session: {console.command(session_id)}")

    while True:
        try:
            user_text = input("> ").strip()
        except EOFError:
            print(console.warning("bye"))
            return 0

        if not user_text:
            continue
        if user_text == "/exit":
            print(console.warning("bye"))
            return 0
        if user_text == "/help":
            _print_help()
            continue
        if user_text == "/new":
            session_id = _new_session_id()
            print(f"{console.success('new session:')} {console.command(session_id)}")
            continue
        if user_text == "/reset":
            session_store.clear(session_id)
            print(f"{console.warning('session cleared:')} {console.command(session_id)}")
            continue
        if user_text == "/sessions":
            _print_sessions(session_store, session_id)
            continue
        if user_text == "/history":
            _print_history(session_store, session_id)
            continue
        if user_text == "/prompts":
            _print_prompts(prompt_loader)
            continue
        if user_text == "/tools":
            _print_tools(tool_registry)
            continue
        if user_text == "/model" or user_text.startswith("/model "):
            print(_handle_model_command(llm, user_text.removeprefix("/model").strip()))
            continue

        try:
            with Spinner("thinking"):
                result = agent_loop.run_turn(session_id, user_text)
            print(result)
        except KeyboardInterrupt:
            session_store.append(session_id, [
                Message(role="user", content=user_text),
                Message(role="assistant", content="[interrupted]",
                        metadata={"interrupted": True}),
            ])


def _run_gateway(argv: Sequence[str]) -> int:
    """Start the local HTTP gateway."""

    parser = argparse.ArgumentParser(prog="zcagent gateway")
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway bind host.")
    parser.add_argument("--port", type=int, default=18791, help="Gateway bind port.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate gateway configuration and exit without serving.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.workspace)
    except MissingWorkspaceError as exc:
        _print_workspace_error(str(exc))
        return 1
    if args.check:
        if not _ensure_runtime_dirs(config):
            return 1
        print(format_gateway_check(config, host=args.host, port=args.port))
        return 0
    if not _ensure_runtime_dirs(config):
        return 1
    run_gateway(config, host=args.host, port=args.port)
    return 0


def _run_init(argv: Sequence[str]) -> int:
    """Generate local runtime config files for the current workspace."""

    parser = argparse.ArgumentParser(prog="zcagent init")
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing local files.")
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Also generate a .env template inside the runtime workspace.",
    )
    parser.add_argument(
        "--skip-llm-config",
        action="store_true",
        help="Do not generate config/llm_endpoints.json.",
    )
    parser.add_argument("--skip-prompts", action="store_true", help="Do not copy prompt files.")
    parser.add_argument("--endpoint", default="default", help="Endpoint name to generate.")
    parser.add_argument("--protocol", default="openai", choices=["openai", "litellm"])
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument(
        "--api-key",
        default="",
        help="api_key value written to workspace config. Supports a direct key or ${ENV_VAR}.",
    )
    parser.add_argument("--model", default="gpt-5")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--temperature", type=float, default=0.7)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.workspace)
    except MissingWorkspaceError as exc:
        _print_workspace_error(str(exc))
        return 1
    try:
        written = init_runtime_files(
            config,
            create_env=args.write_env,
            create_llm_config=not args.skip_llm_config,
            create_prompts=not args.skip_prompts,
            endpoint_name=args.endpoint,
            protocol=args.protocol,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            force=args.force,
        )
    except InitConfigurationError as exc:
        print(console.error(str(exc)))
        print(console.warning("Use --force only if you intentionally want to replace the local file."))
        return 1

    if not written:
        print(console.warning("No runtime files requested."))
        return 0
    for path in written:
        print(f"{console.success('created:')} {console.path(path)}")
    print(
        console.warning(
            "Before calling a real LLM, set workspace api_key to a direct value or ${ENV_VAR}."
        )
    )
    return 0


def _ensure_runtime_dirs(config) -> bool:
    """Create runtime directories and print a friendly setup error on failure."""

    try:
        config.ensure_dirs()
    except OSError as exc:
        print(console.error(f"Cannot create runtime workspace directories: {exc}"))
        print(console.warning("Check ZHICE_AGENT_WORKSPACE in config/.env, or choose a writable directory."))
        return False
    return True


def _extract_env_file(argv: list[str]) -> tuple[str | None, list[str]]:
    """Extract a process-wide --env-file option before normal command parsing."""

    cleaned: list[str] = []
    env_file: str | None = None
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--env-file":
            if index + 1 >= len(argv):
                raise SystemExit("--env-file requires a path")
            env_file = argv[index + 1]
            index += 2
            continue
        if item.startswith("--env-file="):
            env_file = item.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(item)
        index += 1
    return env_file, cleaned


def _new_session_id() -> str:
    """Return a new session id for an explicit /new command."""

    return "session-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _print_workspace_error(message: str) -> None:
    """Print first-run workspace setup guidance after config discovery fails."""

    env_example = "ZHICE_AGENT_WORKSPACE=C:\\Users\\you\\ZhiCe-Agent-Workspace"
    powershell_override = '$env:ZHICE_AGENT_WORKSPACE="C:\\Users\\you\\ZhiCe-Agent-Workspace"'

    print(console.error(message))
    print()
    print(f"Create {console.bold('config/.env')} under the project config directory, for example:")
    print("  " + console.command(env_example))
    print()
    print("Then run:")
    print(f"  {console.command('zcagent init')}")
    print(f"  {console.command('zcagent')}")
    print(f"  {console.command('zcagent gateway')}")
    print()
    print("PowerShell override:")
    print("  " + console.command(powershell_override))


def _load_startup_prompts(prompt_loader: PromptLoader) -> bool:
    """Fail early when required first-stage prompt files are missing."""

    try:
        prompt_loader.load_many(DEFAULT_PROMPTS)
    except PromptNotFoundError as exc:
        print(console.error(str(exc)))
        print(console.warning("Runtime prompt files are missing from the workspace."))
        print()
        print("Choose one:")
        print(
            f"  {console.success('Recommended:')} run {console.command('zcagent init')} "
            "to copy default prompts."
        )
        print("  Manual: create these files under the workspace prompts directory:")
        for name in DEFAULT_PROMPTS:
            print(f"    {console.path(f'prompts/{name}.md')}")
        print()
        print("If the workspace was partially initialized and you want to refresh defaults:")
        print(f"  {console.command('zcagent init --force')}")
        print()
        print("Current command:")
        print(f"  {console.command('zcagent init')}")
        return False
    return True


def _print_history(session_store: JsonlSessionStore, session_id: str) -> None:
    """Print recent session messages for local debugging."""

    state = session_store.load(session_id)
    if not state.messages:
        print(console.warning("(empty history)"))
        return
    for message in state.messages[-10:]:
        print(f"{console.command(message.role)}: {message.content}")


def _print_sessions(session_store: JsonlSessionStore, current_session_id: str) -> None:
    """Print stored sessions with a short preview of the first user message."""

    from agent.protocols.session import SessionSummary

    summaries = session_store.list_sessions()
    if current_session_id not in {summary.session_id for summary in summaries}:
        summaries.insert(
            0,
            SessionSummary(
                session_id=current_session_id,
                preview="(empty)",
                updated_at=0.0,
                message_count=0,
            ),
        )

    if not summaries:
        print(console.warning("(no sessions)"))
        return

    for summary in summaries:
        marker = "*" if summary.session_id == current_session_id else " "
        updated_at = _format_session_time(summary.updated_at)
        print(
            f"{marker} {console.command(summary.session_id)}"
            f"  [{summary.message_count}]  {updated_at}"
        )
        print(f"    {summary.preview}")


def _print_prompts(prompt_loader: PromptLoader) -> None:
    """Print known prompt files."""

    names = prompt_loader.available_names()
    if not names:
        print(console.warning("(no prompts)"))
        return
    for name in names:
        print(console.path(name))


def _print_tools(tool_registry) -> None:
    """Print registered tool names for local debugging."""

    definitions = tool_registry.definitions()
    if not definitions:
        print(console.warning("(no tools)"))
        return
    for definition in definitions:
        function = definition.get("function", {})
        name = function.get("name", "")
        description = function.get("description", "")
        print(f"{console.command(str(name)):<18}{description}")


def _handle_model_command(llm, target: str) -> str:
    """Handle the local /model command without sending it to the LLM.

    Supported forms:
    - /model
    - /model list
    - /model list <endpoint>
    - /model <endpoint>
    - /model <endpoint>/<model>
    - /model reset
    """

    required = (
        "endpoints",
        "current_endpoint",
        "match_endpoint",
        "reset_preferred",
        "set_preferred",
    )
    if not all(hasattr(llm, name) for name in required):
        return console.warning("Model switching is unavailable for this provider.")

    normalized_target = target.strip()
    if not normalized_target:
        return _format_model_status(llm)
    if normalized_target.lower() == "list" or normalized_target.lower().startswith("list "):
        # "list" shows all endpoints; "list claude" shows one endpoint's model allowlist.
        endpoint_name = normalized_target[4:].strip()
        if endpoint_name:
            return _format_endpoint_model_list(llm, endpoint_name)
        return _format_model_list(llm)
    if normalized_target.lower() == "reset":
        llm.reset_preferred()
        current = llm.current_endpoint()
        return (
            f"{console.success('model preference reset.')}\n"
            f"{_format_current_model(current)}"
        )

    endpoint, error = llm.match_endpoint(normalized_target)
    if endpoint is None:
        return f"{console.error(error)}\n{_format_model_status(llm)}"

    # A slash means the user chose endpoint/model, so remember a temporary
    # model override for that endpoint. Plain endpoint switches keep its default model.
    model_override = endpoint.model if "/" in normalized_target else None
    llm.set_preferred(endpoint.name, model_override)
    return (
        f"{console.success('model switched:')} {console.command(endpoint.model)}\n"
        f"endpoint: {console.command(endpoint.name)}"
    )


def _format_model_status(llm) -> str:
    """Format the compact /model status view for the current endpoint/model."""

    current = llm.current_endpoint()
    lines = [
        _format_current_model(current),
        (
            "Tip: use '/model <endpoint>' or '/model <endpoint>/<model>' to switch. "
            "Use '/model list' to see available, '/model reset' to restore default."
        ),
    ]
    return "\n".join(lines)


def _format_model_list(llm) -> str:
    """Format all configured endpoints for /model list."""

    current = llm.current_endpoint()
    lines = [_format_current_model(current), "available endpoints:"]
    for endpoint in llm.endpoints():
        marker = "*" if endpoint.name == current.name else " "
        lines.append(f"{marker} {endpoint.name:<18} default model: {endpoint.model}")
    lines.append("")
    lines.append("Tip: use '/model list <endpoint>' to see available models for an endpoint.")
    return "\n".join(lines)


def _format_endpoint_model_list(llm, endpoint_name: str) -> str:
    """Format the model allowlist for one endpoint."""

    for endpoint in llm.endpoints():
        if endpoint.name == endpoint_name:
            models = _endpoint_model_names(endpoint)
            lines = [
                f"endpoint: {console.command(endpoint.name)}",
                "available models:",
            ]
            for model in models:
                marker = "*" if model == endpoint.model else " "
                suffix = " (default)" if model == endpoint.model else ""
                lines.append(f"{marker} {model}{suffix}")
            return "\n".join(lines)
    return f"{console.error(f'Unknown endpoint: {endpoint_name}')}\n{_format_model_list(llm)}"


def _endpoint_model_names(endpoint) -> list[str]:
    """Return endpoint default model first, followed by unique supported models."""

    models = [endpoint.model]
    for model in endpoint.supported_models:
        if model not in models:
            models.append(model)
    return models


def _format_current_model(endpoint) -> str:
    """Render the current endpoint/model pair shown by /model."""

    return f"current: {console.command(f'{endpoint.name}/{endpoint.model}')}"


def _print_help() -> None:
    """Print available slash commands for the local CLI."""

    commands = [
        ("/help", "show available commands"),
        ("/new", "create and switch to a new session"),
        ("/reset", "clear the current session history"),
        ("/sessions", "list stored sessions and previews"),
        ("/history", "print recent messages from the current session"),
        ("/prompts", "list loaded prompt files"),
        ("/tools", "list registered tools"),
        ("/model", "show or switch the preferred LLM endpoint"),
        ("/exit", "leave the CLI"),
    ]
    for name, description in commands:
        print(f"{console.command(name):<18}{description}")


def _format_session_time(timestamp: float) -> str:
    """Render a readable local timestamp for session listings."""

    if timestamp <= 0:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


class _UnavailableLLMProvider:
    """Provider placeholder that delays configuration errors until first chat."""

    def __init__(self, error: LLMConfigurationError):
        """Store the startup error so normal CLI rendering can continue."""

        self.error = error

    def chat(self, messages, tools=None):
        """Raise the stored configuration error through the normal loop path."""

        raise self.error


def _build_llm_provider(config_dir, endpoint_name):
    """Build the runtime LLM provider chain from endpoint config."""

    try:
        endpoints = load_llm_endpoints(config_dir)
        preferred_endpoint = _resolve_preferred_endpoint(config_dir, endpoint_name, endpoints)
        return create_llm_provider_chain(endpoints, preferred_endpoint=preferred_endpoint)
    except LLMConfigurationError as exc:
        return _UnavailableLLMProvider(exc)


def _resolve_preferred_endpoint(config_dir, endpoint_name, endpoints):
    """Resolve CLI --endpoint into the concrete startup preference.

    ``auto`` means: use a configured default alias if present, else a real
    endpoint named "default", else let the failover provider choose by priority.
    """

    resolved = resolve_llm_endpoint_alias(config_dir, endpoint_name)
    if resolved:
        return resolved
    default_alias = resolve_llm_endpoint_alias(config_dir, "default")
    endpoint_names = {endpoint.name for endpoint in endpoints if endpoint.enabled}
    if default_alias and default_alias in endpoint_names:
        return default_alias
    if "default" in endpoint_names:
        return "default"
    return None


if __name__ == "__main__":
    raise SystemExit(main())
