"""Command-line entrypoint for the no-tool ZhiCe-Agent runtime."""

from __future__ import annotations

import argparse
import atexit
import getpass
import hmac
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime

from agent.app.auth import local_operator_actor
from agent.app.gateway import format_gateway_check, run_gateway
from agent.app.logging import GatewayLogOptions
from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.audit import SqliteAuditSink
from agent.auth.confirmation import ConsoleConfirmationBroker
from agent.auth.store import AuthSetupError, AuthStoreError, SQLiteAuthStore
from agent.auth.tool_policy import RbacToolExecutionPolicy
from agent.channels.config import ChannelConfigurationError, load_channel_configuration
from agent.channels.identity import ExternalIdentityService
from agent.channels.qq.startup import check_qq_startup
from agent.channels.weixin.startup import check_weixin_startup
from agent.config import (
    DotenvConfigurationError,
    InitConfigurationError,
    MissingWorkspaceError,
    bootstrap_dotenv,
    init_runtime_files,
    load_config,
)
from agent.console import Spinner, console
from agent.core.context import ContextBuilder
from agent.core.loop import AgentLoop, CancellationToken
from agent.core.turns import assign_turn, new_turn_id, next_turn_index
from agent.hooks import HookConfigurationError, create_hook_runtime
from agent.llm import LLMConfigurationError
from agent.llm.failover_provider import EndpointFailoverProvider
from agent.llm.runtime import (
    create_configured_llm_provider,
    resolve_preferred_endpoint,
    validate_startup_llm_endpoints,
)
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.mcp import McpRuntime, check_mcp_startup
from agent.memory.context import build_memory_context
from agent.memory.markdown_store import MarkdownMemoryStore
from agent.memory.presentation import format_memory_list
from agent.memory.safety import MemorySafetyPolicy
from agent.message import Message
from agent.presentation import markdown_to_plain_text
from agent.prompt_loader import PromptLoader, PromptNotFoundError
from agent.protocols.auth import AuditEvent
from agent.protocols.capability import CapabilityStatus
from agent.protocols.mcp import McpInteractionRequest, McpInteractionResponse
from agent.protocols.runtime_event import is_runtime_event_payload
from agent.protocols.session import SessionContext, SessionModelPreference
from agent.protocols.subagent import SubagentProfile
from agent.session import (
    JsonlSessionStore,
    JsonSessionModelPreferenceStore,
    JsonSessionSubagentPreferenceStore,
    SessionSubagentPreference,
)
from agent.skills import SkillLoader, SkillSourceSync
from agent.skills.sync import SkillSyncError, SkillSyncResult
from agent.subagents.config import SubagentConfig
from agent.subagents.presentation import format_subagent_unavailable
from agent.subagents.runtime import (
    build_turn_subagent_provider,
    build_unavailable_subagent_provider,
)
from agent.subagents.startup import check_subagent_startup
from agent.tools import create_default_tool_registry, with_tool_discovery

DEFAULT_PROMPTS = ["identity", "tool_use_policy", "skills_intro"]
DEFAULT_CHAT_HISTORY_MESSAGES = 60
CHAT_BANNER = "\U0001F408 zcagent - Personal AI Assistant"


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
    if argv_list and argv_list[0] == "auth":
        return _run_auth(argv_list[1:])
    if argv_list and argv_list[0] == "channels":
        return _run_channels(argv_list[1:])
    return _run_chat(argv_list)


def _run_auth(argv: Sequence[str]) -> int:
    """Run local auth bootstrap and maintenance commands."""

    parser = argparse.ArgumentParser(prog="zcagent auth")
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    subparsers = parser.add_subparsers(dest="auth_command", required=True)

    init_owner = subparsers.add_parser("init-owner", help="Create the unique Owner user.")
    init_owner.add_argument("--username", default="owner")
    init_owner.add_argument("--display-name", default="Owner")
    subparsers.add_parser("users", help="List local DB users.")

    reset_password = subparsers.add_parser("reset-password", help="Reset one user's password.")
    reset_password.add_argument("username")

    args = parser.parse_args(argv)
    try:
        config = load_config(args.workspace)
    except MissingWorkspaceError as exc:
        _print_workspace_error(str(exc))
        return 1
    if not _ensure_runtime_dirs(config):
        return 1
    store = SQLiteAuthStore(config.auth_db_path)

    try:
        if args.auth_command == "init-owner":
            if store.has_owner():
                raise AuthSetupError("owner already exists; owner initialization is closed")
            expected_setup_token = os.getenv("ZHICE_AGENT_SETUP_TOKEN", "")
            if not expected_setup_token:
                raise AuthSetupError(
                    "Owner setup is disabled; configure ZHICE_AGENT_SETUP_TOKEN before initializing."
                )
            setup_token = getpass.getpass("Setup token: ")
            if not hmac.compare_digest(expected_setup_token, setup_token):
                raise AuthSetupError("Invalid setup credential")
            password = getpass.getpass("Owner password: ")
            user = store.initialize_owner(
                args.username,
                args.display_name,
                password,
            )
            print(f"{console.success('owner initialized:')} {console.command(user.username)}")
            print(f"user_id: {console.command(user.id)}")
            return 0
        if not store.is_initialized():
            raise AuthSetupError(
                "auth database is not initialized; register a user or run zcagent auth init-owner"
            )
        if args.auth_command == "users":
            for user in store.list_users():
                roles = ",".join(user.role_keys)
                print(f"{user.id}  {user.username}  {user.status}  roles={roles}")
            return 0
        if args.auth_command == "reset-password":
            password = _read_confirmed_password("New password: ")
            store.reset_password(args.username, password)
            print(f"{console.success('password reset:')} {console.command(args.username)}")
            return 0
    except (AuthSetupError, AuthStoreError, ValueError, OSError) as exc:
        print(console.error(str(exc)))
        return 1
    return 1


def _read_confirmed_password(prompt: str) -> str:
    """Read a password twice without accepting it through command-line arguments."""

    password = getpass.getpass(prompt)
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("password confirmation does not match")
    return password


def _run_channels(argv: Sequence[str]) -> int:
    """Inspect channel status and create account-scoped user link codes."""

    parser = argparse.ArgumentParser(prog="zcagent channels")
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    subparsers = parser.add_subparsers(dest="channels_command")
    subparsers.add_parser("status", help="Show configured channel capability status.")
    link_code = subparsers.add_parser("link-code", help="Create a one-time identity link code.")
    link_code.add_argument("channel", choices=["qq"])
    link_code.add_argument("--user", required=True, help="Internal username receiving the link.")
    link_code.add_argument("--account", default="main", help="Configured channel account key.")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.workspace)
        channel_config = load_channel_configuration(config.config_dir)
        if args.channels_command in {None, "status"}:
            statuses = (
                check_qq_startup(channel_config.qq),
                check_weixin_startup(channel_config.weixin, config.workspace),
            )
            for status in statuses:
                print(f"{status.name}: {status.state} code={status.code}")
                if status.message:
                    print(status.message)
            return 0 if all(status.state != "unavailable" for status in statuses) else 1
        store = SQLiteAuthStore(config.auth_db_path)
        if not store.is_initialized():
            raise AuthSetupError("auth database is not initialized")
        account_keys = {account.key for account in channel_config.qq.accounts}
        if args.account not in account_keys:
            raise ChannelConfigurationError(f"QQ account is not configured: {args.account}")
        user = next((item for item in store.list_users() if item.username == args.user), None)
        if user is None:
            raise AuthStoreError("user not found")
        link = ExternalIdentityService(store).create_link_code(
            user.id,
            args.channel,
            args.account,
        )
        print(f"link code: {console.command(link.code)}")
        print(f"expires_at: {link.expires_at}")
        return 0
    except (
        MissingWorkspaceError,
        ChannelConfigurationError,
        AuthSetupError,
        AuthStoreError,
        OSError,
    ) as exc:
        print(console.error(str(exc)))
        return 1


def _run_chat(argv: Sequence[str]) -> int:
    """Run the default interactive chat command."""

    parser = argparse.ArgumentParser(prog="zcagent")
    parser.add_argument(
        "--session",
        default=None,
        help="Session id to resume. Defaults to today's local chat session.",
    )
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    parser.add_argument(
        "--endpoint",
        default="auto",
        help="Preferred LLM endpoint name. Defaults to config default alias or priority order.",
    )
    args = parser.parse_args(argv)
    session_id = args.session or _default_session_id()

    try:
        config = load_config(args.workspace)
    except MissingWorkspaceError as exc:
        _print_workspace_error(str(exc))
        return 1
    if not _ensure_runtime_dirs(config):
        return 1
    try:
        hook_runtime = create_hook_runtime(config.workspace, config.config_dir)
    except HookConfigurationError as exc:
        print(console.error(f"Hook configuration is invalid: {exc}"))
        return 1

    skill_sync = SkillSourceSync(
        workspace=config.workspace,
        config_dir=config.config_dir,
        extends_dir=config.extends_dir,
    )
    skill_sync_error = _sync_startup_skills(skill_sync)

    prompt_loader = PromptLoader(config.prompts_dir)
    session_store = JsonlSessionStore(config.sessions_dir)
    skill_loader = _create_skill_loader(skill_sync, startup_error=skill_sync_error)
    context_builder = ContextBuilder(
        prompt_loader,
        skills=skill_loader,
        max_history_messages=DEFAULT_CHAT_HISTORY_MESSAGES,
    )
    if not _load_startup_prompts(prompt_loader):
        return 1
    try:
        llm = _build_llm_provider(config.config_dir, args.endpoint)
    except LLMConfigurationError as exc:
        _print_llm_configuration_error(exc, config)
        return 1
    model_runtime = _build_cli_model_runtime(llm, config, session_store)
    subagent_runtime = _build_cli_subagent_runtime(config, session_store)
    if subagent_runtime.status.state == "unavailable":
        print(console.warning(_format_cli_subagent_unavailable(subagent_runtime.status)))
    mcp_startup = check_mcp_startup(config.config_dir)
    if mcp_startup.status.state == "unavailable":
        print(console.warning(_format_cli_capability_status(mcp_startup.status)))
    if subagent_runtime.config.enabled:
        context_builder.extra_system_prompts = ("subagent_orchestration",)
    cli_actor = local_operator_actor(channel="cli")
    tool_policy = RbacToolExecutionPolicy()
    confirmation_broker = ConsoleConfirmationBroker()
    auth_store = SQLiteAuthStore(config.auth_db_path)
    audit_sink = SqliteAuditSink(auth_store) if auth_store.is_initialized() else None
    activity_sink = (
        SqliteRuntimeActivitySink(auth_store) if auth_store.is_initialized() else None
    )
    mcp_runtime = McpRuntime(
        mcp_startup.specs,
        workspace=config.workspace,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
    )
    atexit.register(mcp_runtime.close)
    memory_store = MarkdownMemoryStore(
        build_memory_context(
            config.local_memory_dir,
            scope="workspace",
            actor_user_id=None,
        )
    )
    memory_safety = MemorySafetyPolicy()
    tool_registry = create_default_tool_registry(
        config.workspace,
        skills=skill_loader,
        skill_sync=skill_sync,
        allow_confirmable_exec=True,
        memory_store=memory_store,
        memory_safety=memory_safety,
        extra_tools=mcp_runtime.tools_for_actor(
            cli_actor,
            config.workspace,
            interaction_notifier=lambda request: _handle_cli_mcp_interaction(
                mcp_runtime, request
            ),
        ),
    )
    agent_loop = AgentLoop(
        llm=llm,
        sessions=session_store,
        context_builder=context_builder,
        workspace=config.workspace,
        tools=tool_registry,
        tool_policy=tool_policy,
        confirmation_broker=confirmation_broker,
        audit_sink=audit_sink,
        hook_runtime=hook_runtime,
    )
    print(console.bold(CHAT_BANNER))

    while True:
        try:
            user_text = input("> ").strip()
        except EOFError:
            print(console.warning("bye"))
            mcp_runtime.close()
            return 0

        if not user_text:
            continue
        if user_text == "/exit":
            print(console.warning("bye"))
            mcp_runtime.close()
            return 0
        if user_text == "/help":
            _print_help()
            continue
        if user_text == "/new":
            session_id = _new_session_id()
            print(f"{console.success('new session:')} {console.command(session_id)}")
            continue
        if _cli_session_is_read_only(auth_store, session_id) and not (
            user_text == "/history" or user_text == "/sessions" or user_text.startswith("/sessions ")
        ):
            print(
                console.warning(
                    "This QQ group session is read-only in CLI. "
                    "Use '/new' to continue privately."
                )
            )
            continue
        if user_text == "/clear":
            session_store.clear(session_id)
            subagent_runtime.preferences.clear_force_once(subagent_runtime.context, session_id)
            print(f"{console.warning('session cleared:')} {console.command(session_id)}")
            continue
        if user_text == "/reset":
            print(console.warning("Unsupported command: /reset. Use /clear."))
            continue
        if user_text == "/sessions" or user_text.startswith("/sessions "):
            _handle_sessions_command(
                session_store,
                session_id,
                user_text.removeprefix("/sessions").strip(),
                subagent_runtime,
                auth_store,
            )
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
        if user_text == "/mcp":
            print(mcp_runtime.format_capabilities())
            continue
        if user_text == "/skills" or user_text.startswith("/skills "):
            _handle_skills_command(
                skill_loader,
                skill_sync,
                user_text.removeprefix("/skills").strip(),
            )
            continue
        if user_text == "/memory" or user_text.startswith("/memory "):
            target = user_text.removeprefix("/memory").strip()
            if target:
                print("Usage: /memory")
                continue
            print(format_memory_list(memory_store))
            continue
        if user_text == "/model" or user_text.startswith("/model "):
            target = user_text.removeprefix("/model").strip()
            if model_runtime is None:
                print(_handle_model_command(llm, target))
            else:
                output = _handle_session_model_command(model_runtime, session_id, target)
                print(output)
                if audit_sink is not None:
                    normalized_target = target.lower()
                    action = (
                        "model.reset"
                        if normalized_target == "reset"
                        else "model.viewed"
                        if not normalized_target or normalized_target.startswith("list")
                        else "model.switched"
                    )
                    audit_sink.record(
                        AuditEvent(
                            action=action,
                            resource_type="model",
                            actor=cli_actor,
                            channel="cli",
                            session_id=session_id,
                            decision="allow",
                        )
                    )
            continue
        if user_text == "/subagent" or user_text.startswith("/subagent "):
            print(
                _handle_session_subagent_command(
                    subagent_runtime,
                    session_id,
                    user_text.removeprefix("/subagent").strip(),
                )
            )
            continue

        subagent_preference = subagent_runtime.preferences.get(
            subagent_runtime.context,
            session_id,
        )
        force_subagent_once = subagent_runtime.preferences.consume_force_once(
            subagent_runtime.context,
            session_id,
        )
        if force_subagent_once and not subagent_runtime.config.enabled:
            print(console.warning(_format_cli_subagent_unavailable(subagent_runtime.status)))
            continue
        turn_id = new_turn_id()
        turn_index = next_turn_index(session_store.load(session_id).messages)
        turn_tools = create_default_tool_registry(
            config.workspace,
            skills=skill_loader,
            skill_sync=skill_sync,
            allow_confirmable_exec=True,
            memory_store=memory_store,
            memory_safety=memory_safety,
            extra_tools=mcp_runtime.tools_for_actor(
                cli_actor,
                config.workspace,
                interaction_notifier=lambda request: _handle_cli_mcp_interaction(
                    mcp_runtime, request
                ),
            ),
        )
        turn_token = CancellationToken()
        try:
            with Spinner("已接收问题") as spinner:
                turn_context_budget = None
                if model_runtime is not None:
                    turn_llm, turn_context_budget = _cli_turn_provider(
                        model_runtime,
                        session_id,
                    )
                else:
                    turn_llm = llm

                def turn_event_callback(event):
                    _update_cli_runtime_status(spinner, event)

                if subagent_runtime.config.enabled and (
                    subagent_preference.mode == "auto" or force_subagent_once
                ):

                    def child_tools(
                        child_workspace,
                        profile: SubagentProfile,
                        parent_context,
                        child_on_event,
                        child_identity,
                        child_skills,
                    ):
                        del profile, parent_context, child_on_event

                        def child_mcp_interaction(request):
                            print(
                                console.muted(
                                    f"Subagent task {child_identity.task_id} requested MCP input."
                                )
                            )
                            return _handle_cli_mcp_interaction(mcp_runtime, request)

                        return create_default_tool_registry(
                            child_workspace,
                            skills=child_skills,
                            skill_sync=skill_sync,
                            allow_confirmable_exec=True,
                            memory_store=memory_store,
                            memory_safety=memory_safety,
                            extra_tools=mcp_runtime.tools_for_actor(
                                cli_actor,
                                child_workspace,
                                interaction_notifier=child_mcp_interaction,
                            ),
                        )

                    turn_tools = build_turn_subagent_provider(
                        base_tools=turn_tools,
                        config=subagent_runtime.config,
                        prompt_loader=prompt_loader,
                        sessions_root=config.sessions_dir,
                        workspace=config.workspace,
                        parent_llm=turn_llm,
                        context_budget=turn_context_budget,
                        tool_provider_factory=child_tools,
                        skills=skill_loader,
                        cancellation_token=turn_token,
                        on_event=turn_event_callback,
                        force_once=force_subagent_once,
                        tool_policy=tool_policy,
                        confirmation_broker=confirmation_broker,
                        activity_sink=activity_sink,
                        audit_sink=audit_sink,
                        hook_runtime=hook_runtime,
                    )
                elif (
                    subagent_runtime.status.state == "unavailable"
                    and subagent_preference.mode == "auto"
                ):
                    turn_tools = build_unavailable_subagent_provider(
                        turn_tools,
                        subagent_runtime.status,
                    )
                turn_tools = with_tool_discovery(turn_tools)
                result = agent_loop.run_turn(
                    session_id,
                    user_text,
                    turn_id=turn_id,
                    actor=cli_actor,
                    llm_override=turn_llm,
                    context_budget=turn_context_budget,
                    tools_override=turn_tools,
                    cancellation_token=turn_token,
                    tool_policy=tool_policy,
                    confirmation_broker=confirmation_broker,
                    channel="cli",
                    on_event=turn_event_callback,
                    system_prompt_addendum=(
                        prompt_loader.load("subagent_once") if force_subagent_once else ""
                    ),
                )
            print(markdown_to_plain_text(result))
        except KeyboardInterrupt:
            session_store.append(
                session_id,
                [
                    assign_turn(
                        Message(role="user", content=user_text),
                        turn_id=turn_id,
                        turn_index=turn_index,
                    ),
                    assign_turn(
                        Message(
                            role="assistant",
                            content="[interrupted]",
                            metadata={"interrupted": True},
                        ),
                        turn_id=turn_id,
                        turn_index=turn_index,
                    ),
                ],
            )


def _update_cli_runtime_status(spinner: Spinner, event: dict[str, object]) -> None:
    """Render only active RuntimeEvent states on the existing CLI status line."""

    if not is_runtime_event_payload(event) or event.get("status") not in {"started", "waiting"}:
        return
    display = event.get("display")
    title = display.get("title") if isinstance(display, dict) else ""
    if isinstance(title, str) and title.strip():
        spinner.set_label(title)


def _run_gateway(argv: Sequence[str]) -> int:
    """Start the local HTTP gateway."""

    parser = argparse.ArgumentParser(prog="zcagent gateway")
    parser.add_argument("--workspace", default=None, help="Workspace root override.")
    parser.add_argument("--host", default="127.0.0.1", help="Gateway bind host.")
    parser.add_argument("--port", type=int, default=10086, help="Gateway bind port.")
    parser.add_argument(
        "--agent-log",
        choices=["on", "off"],
        default="on",
        help="Print Agent lifecycle logs. Defaults to on.",
    )
    parser.add_argument(
        "--agent-log-level",
        choices=["debug", "info", "warning", "error", "critical"],
        default="info",
        help="Agent lifecycle log level. Defaults to info.",
    )
    parser.add_argument(
        "--trace-log",
        choices=["on", "off"],
        default="on",
        help="Write workspace JSONL trace logs. Defaults to on.",
    )
    parser.add_argument(
        "--http-access-log",
        choices=["on", "off"],
        default=None,
        help="Print HTTP request access logs. Defaults to on.",
    )
    parser.add_argument(
        "--http-server-log",
        choices=["on", "off"],
        default="on",
        help="Print HTTP server lifecycle logs. Defaults to on.",
    )
    parser.add_argument(
        "--http-server-log-level",
        choices=["debug", "info", "warning", "error", "critical"],
        default=None,
        help="HTTP server log level. Defaults to info.",
    )
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
    try:
        run_gateway(
            config,
            host=args.host,
            port=args.port,
            log_options=_gateway_log_options(args),
        )
    except PromptNotFoundError as exc:
        print(console.error(str(exc)))
        print(console.warning("Runtime prompt files are missing from the workspace. Run zcagent init."))
        return 1
    except LLMConfigurationError as exc:
        _print_llm_configuration_error(exc, config)
        return 1
    return 0


def _gateway_log_options(args) -> GatewayLogOptions:
    """Build split gateway logging options from explicit gateway flags."""

    return GatewayLogOptions(
        agent_log=args.agent_log == "on",
        agent_log_level=args.agent_log_level,
        trace_log=args.trace_log == "on",
        http_access_log=(args.http_access_log or "on") == "on",
        http_server_log=args.http_server_log == "on",
        http_server_log_level=args.http_server_log_level or "info",
    )


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
    parser.add_argument("--endpoint", default="default", help="Endpoint name to generate.")
    parser.add_argument("--protocol", default="openai", choices=["openai", "litellm"])
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument(
        "--api-key",
        default="",
        help="api_key value written to workspace config. Supports a direct key or ${ENV_VAR}.",
    )
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=16384,
        help="Maximum output tokens requested for one model call.",
    )
    parser.add_argument(
        "--context-window",
        type=int,
        default=131072,
        help="Total model context window including input and generated output.",
    )
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
            endpoint_name=args.endpoint,
            protocol=args.protocol,
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
            max_tokens=args.max_tokens,
            context_window=args.context_window,
            temperature=args.temperature,
            force=args.force,
        )
    except InitConfigurationError as exc:
        print(console.error(str(exc)))
        print(console.warning("Use --force only if you intentionally want to replace the local file."))
        return 1

    if not written:
        print(console.warning("All requested runtime files already exist. Nothing changed."))
        return 0
    for path in written:
        print(f"{console.success('created:')} {console.path(path)}")
    print(
        console.warning(
            "Runtime files created. Configure an enabled LLM endpoint before chatting. "
            "Extension capabilities are optional."
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


def _sync_startup_skills(skill_sync: SkillSourceSync) -> SkillSyncError | None:
    """Run best-effort startup Skill sync and return one optional failure cause."""

    try:
        skill_sync.sync_on_startup()
    except SkillSyncError as exc:
        return exc
    return None


def _create_skill_loader(
    skill_sync: SkillSourceSync,
    *,
    startup_error: SkillSyncError | None = None,
) -> SkillLoader:
    """Create a SkillLoader and print at most one accurate optional-capability warning."""

    if not skill_sync.has_config():
        return SkillLoader([])
    try:
        roots = skill_sync.skill_roots()
    except SkillSyncError as exc:
        print(
            console.warning(
                "Skill capability unavailable: invalid config/skill_sources.yml "
                f"({exc}). Fix the file, then restart."
            )
        )
        return SkillLoader([])
    if startup_error is not None:
        print(
            console.warning(
                "Skill sync degraded: configured source synchronization failed "
                f"({startup_error}). Run /skills sync --verbose to inspect and retry."
            )
        )
    return SkillLoader(roots)


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


def _default_session_id() -> str:
    """Return the implicit local chat session for today's date."""

    return "chat-" + datetime.now().strftime("%Y%m%d")


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


def _print_llm_configuration_error(exc: LLMConfigurationError, config) -> None:
    """Print a startup-blocking LLM configuration error with setup guidance."""

    print(console.error(f"LLM configuration is invalid: {exc}"))
    print(console.warning("Chat cannot start until an enabled LLM endpoint is configured."))
    print()
    endpoint_path = config.config_dir / "llm_endpoints.json"
    if endpoint_path.exists():
        print(
            f"  {console.success('Recommended:')} edit {console.path(endpoint_path)} and fix "
            "the enabled endpoint. Use protocol-specific base_url/provider, model, and api_key "
            "values that match the actual service."
        )
        print(
            f"  Replace template intentionally: {console.command('zcagent init --force')}"
        )
        return
    print(
        f"  {console.success('Recommended:')} run {console.command('zcagent init')} "
        "to create the missing runtime files."
    )
    print(f"  Then review: {console.path(endpoint_path)}")


def _print_history(session_store: JsonlSessionStore, session_id: str) -> None:
    """Print recent session messages for local debugging."""

    state = session_store.load(session_id)
    if not state.messages:
        print(console.warning("(empty history)"))
        return
    for message in state.messages[-10:]:
        content = (
            markdown_to_plain_text(message.content)
            if message.role == "assistant"
            else message.content
        )
        print(f"{console.command(message.role)}: {content}")


def _print_sessions(
    session_store: JsonlSessionStore,
    current_session_id: str,
    auth_store: SQLiteAuthStore | None = None,
) -> None:
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
        title = summary.title or summary.preview
        source = _cli_session_source_label(auth_store, summary.session_id)
        print(
            f"{marker} {console.command(summary.session_id)}"
            f"  [{summary.message_count}]  {updated_at}  {source}"
        )
        print(f"    {title}")
    print()
    print(
        console.warning(
            "Tip: use '/sessions rename <id> <title>' to rename, "
            "'/sessions delete (<id>)' to delete."
        )
    )


def _handle_sessions_command(
    session_store: JsonlSessionStore,
    current_session_id: str,
    target: str,
    subagent_runtime=None,
    auth_store: SQLiteAuthStore | None = None,
) -> None:
    """Handle local /sessions management commands."""

    if not target:
        _print_sessions(session_store, current_session_id, auth_store)
        return

    command, _, rest = target.partition(" ")
    command = command.strip().lower()
    rest = rest.strip()
    try:
        if command == "rename":
            session_id, _, title = rest.partition(" ")
            session_id = session_id.strip()
            title = title.strip()
            if not session_id or not title:
                print(console.warning("Usage: /sessions rename <id> <title>"))
                return
            session_store.rename(session_id, title)
            print(f"{console.success('session renamed:')} {console.command(session_id)}")
            return
        if command == "delete":
            session_id = rest or current_session_id
            if _cli_session_is_external(auth_store, session_id):
                print(
                    console.warning(
                        "External-channel sessions cannot be deleted from CLI; "
                        "their conversation route must be preserved."
                    )
                )
                return
            if session_id == current_session_id:
                session_store.clear(current_session_id)
                if subagent_runtime is not None:
                    subagent_runtime.preferences.clear_force_once(
                        subagent_runtime.context,
                        current_session_id,
                    )
                print(f"{console.warning('session cleared:')} {console.command(current_session_id)}")
                return
            session_store.delete(session_id)
            print(f"{console.warning('session deleted:')} {console.command(session_id)}")
            return
    except ValueError as exc:
        print(console.error(str(exc)))
        return
    print(
        console.warning(
            "Tip: use '/sessions rename <id> <title>' to rename, "
            "'/sessions delete (<id>)' to delete."
        )
    )


def _cli_session_is_read_only(
    auth_store: SQLiteAuthStore | None,
    session_id: str,
) -> bool:
    row = _cli_session_index_row(auth_store, session_id)
    return bool(
        row
        and str(row.get("channel") or "") == "qq"
        and str(row.get("conversation_type") or "") == "group"
    )


def _cli_session_is_external(
    auth_store: SQLiteAuthStore | None,
    session_id: str,
) -> bool:
    row = _cli_session_index_row(auth_store, session_id)
    return bool(row and str(row.get("channel") or "") not in {"", "web", "cli", "cli_legacy"})


def _cli_session_source_label(
    auth_store: SQLiteAuthStore | None,
    session_id: str,
) -> str:
    row = _cli_session_index_row(auth_store, session_id)
    if row is None:
        return "CLI"
    channel = str(row.get("channel") or "")
    conversation_type = str(row.get("conversation_type") or "")
    if channel == "qq" and conversation_type == "group":
        return "QQ group (read-only)"
    if channel == "qq":
        return "QQ direct"
    if channel in {"cli", "cli_legacy"}:
        return "CLI"
    return "Web" if channel == "web" else channel or "CLI"


def _cli_session_index_row(
    auth_store: SQLiteAuthStore | None,
    session_id: str,
):
    if auth_store is None or not auth_store.is_initialized():
        return None
    return auth_store.session_index_get(session_id)


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


def _handle_skills_command(
    skill_loader: SkillLoader,
    skill_sync: SkillSourceSync,
    target: str,
) -> None:
    """Handle /skills and /skills sync debug commands."""

    if not target:
        _print_skills(skill_loader)
        return
    parts = target.split()
    if parts[0] != "sync":
        print(console.warning("Usage: /skills or /skills sync [--verbose] [source_name]"))
        return
    verbose = "--verbose" in parts[1:]
    unknown_options = [part for part in parts[1:] if part.startswith("--") and part != "--verbose"]
    if unknown_options:
        print(console.warning("Usage: /skills or /skills sync [--verbose] [source_name]"))
        return
    source_names = [part for part in parts[1:] if part != "--verbose"]
    try:
        result = skill_sync.sync(source_names=source_names or None)
    except SkillSyncError as exc:
        print(console.error(f"skills sync failed: {exc}"))
        return
    print(_format_skill_sync_result(result, verbose=verbose))


def _format_skill_sync_result(result: SkillSyncResult, *, verbose: bool = False) -> str:
    """Return a compact human-readable Skill sync summary."""

    if not result.sources:
        return console.warning("skills sync: no configured sources")
    lines: list[str] = []
    for source in result.sources:
        if source.status == "synced":
            lines.append(
                f"{console.success('skills synced:')} {source.name} "
                f"({_format_skill_sync_counts(source)})"
            )
        elif source.status == "up_to_date":
            lines.append(
                f"{console.success('skills up to date:')} {source.name} "
                f"({source.skills} skills)"
            )
        elif source.status == "skipped":
            suffix = f" ({source.message})" if source.message else ""
            lines.append(console.warning(f"skills skipped: {source.name}{suffix}"))
        elif source.status == "failed":
            message = source.error or source.message or "unknown error"
            lines.append(console.error(f"skills failed: {source.name} ({message})"))
        else:
            lines.append(console.warning(f"skills {source.status}: {source.name}"))
        if verbose:
            lines.extend(_format_skill_sync_details(source))
    return "\n".join(lines)


def _format_skill_sync_counts(source) -> str:
    """Return compact source-level change counts."""

    parts = [f"{source.skills} skills"]
    if source.new:
        parts.append(f"{len(source.new)} new")
    if source.changed:
        parts.append(f"{len(source.changed)} changed")
    if source.removed:
        parts.append(f"{len(source.removed)} removed")
    if len(parts) == 1:
        parts.append("changed")
    return ", ".join(parts)


def _format_skill_sync_details(source) -> list[str]:
    """Return optional verbose sync details for one source."""

    lines: list[str] = []
    details = [
        ("new", source.new),
        ("changed", source.changed),
        ("removed", source.removed),
    ]
    for label, names in details:
        if names:
            lines.append(f"  {label}: {', '.join(names)}")
    if source.unchanged:
        lines.append(f"  unchanged: {len(source.unchanged)}")
    return lines


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


class _CliModelRuntime:
    """Session metadata dependencies used by the production CLI model commands."""

    def __init__(self, resolver, preferences, context):
        self.resolver = resolver
        self.preferences = preferences
        self.context = context


class _CliSubagentRuntime:
    """Session metadata dependencies used by production CLI Subagent commands."""

    def __init__(self, preferences, context, profiles, config=None, status=None):
        self.preferences = preferences
        self.context = context
        self.profiles = profiles
        self.config = config or SubagentConfig()
        self.status = status or CapabilityStatus(
            name="subagent",
            state="disabled",
            code="SUBAGENT_DISABLED",
            message="Subagent is not enabled for this workspace.",
            hint="Copy config/subagents.example.yml to the runtime config directory to enable it.",
        )


def _build_cli_model_runtime(llm, config, session_store) -> _CliModelRuntime | None:
    """Enable session-scoped model state for the configured production provider."""

    if not isinstance(llm, EndpointFailoverProvider):
        return None
    resolver = ConfiguredLLMProviderResolver(
        llm.endpoints(),
        default_endpoint=llm.current_endpoint().name,
    )
    context = SessionContext(
        owner_user_id=None,
        sessions_dir=config.sessions_dir,
        sessions_meta_dir=session_store.metadata_dir,
        files_dir=config.workspace,
        shared_readonly_dir=config.shared_readonly_dir,
    )
    return _CliModelRuntime(resolver, JsonSessionModelPreferenceStore(), context)


def _build_cli_subagent_runtime(config, session_store) -> _CliSubagentRuntime:
    """Bind session-scoped Subagent state and configured Profile summaries."""

    context = SessionContext(
        owner_user_id=None,
        sessions_dir=config.sessions_dir,
        sessions_meta_dir=session_store.metadata_dir,
        files_dir=config.workspace,
        shared_readonly_dir=config.shared_readonly_dir,
    )
    startup = check_subagent_startup(config.config_dir, PromptLoader(config.prompts_dir))
    subagent_config = startup.config
    return _CliSubagentRuntime(
        JsonSessionSubagentPreferenceStore(),
        context,
        tuple(
            (profile.name, profile.description)
            for profile in subagent_config.list_profiles()
            if profile.allow_model_invocation
        ),
        subagent_config,
        startup.status,
    )


def _handle_session_model_command(
    runtime: _CliModelRuntime,
    session_id: str,
    target: str,
) -> str:
    """Handle CLI /model using only the current session sidecar metadata."""

    normalized = target.strip()
    current = runtime.resolver.resolve(runtime.preferences.get(runtime.context, session_id))
    if not normalized:
        return "\n".join(
            [
                f"current: {console.command(f'{current.endpoint_name}/{current.model_name}')}",
                (
                    "Tip: use '/model <endpoint>' or '/model <endpoint>/<model>' to switch. "
                    "Use '/model list' to see available, '/model reset' to restore default."
                ),
            ]
        )
    if normalized.lower() == "reset":
        runtime.preferences.reset(runtime.context, session_id)
        default = runtime.resolver.resolve(None)
        return (
            f"{console.success('model preference reset.')}\n"
            f"current: {console.command(f'{default.endpoint_name}/{default.model_name}')}"
        )
    if normalized.lower() == "list" or normalized.lower().startswith("list "):
        endpoint_name = normalized[4:].strip()
        if endpoint_name:
            endpoint = next(
                (item for item in runtime.resolver.endpoints() if item.name == endpoint_name),
                None,
            )
            if endpoint is None:
                return f"{console.error(f'Unknown endpoint: {endpoint_name}')}"
            lines = [f"endpoint: {console.command(endpoint.name)}", "available models:"]
            for model in _endpoint_model_names(endpoint):
                suffix = " (default)" if model == endpoint.model else ""
                lines.append(f"  {model}{suffix}")
            return "\n".join(lines)
        lines = [
            f"current: {console.command(f'{current.endpoint_name}/{current.model_name}')}",
            "available endpoints:",
        ]
        for endpoint in runtime.resolver.endpoints():
            marker = "*" if endpoint.name == current.endpoint_name else " "
            lines.append(f"{marker} {endpoint.name:<18} default model: {endpoint.model}")
        return "\n".join(lines)

    endpoint_name, separator, model_name = normalized.partition("/")
    selection = runtime.resolver.select(endpoint_name, model_name if separator else None)
    runtime.preferences.set(
        runtime.context,
        session_id,
        SessionModelPreference(selection.endpoint_name, selection.model_name),
    )
    return (
        f"{console.success('model switched:')} {console.command(selection.model_name)}\n"
        f"endpoint: {console.command(selection.endpoint_name)}"
    )


def _handle_session_subagent_command(
    runtime: _CliSubagentRuntime,
    session_id: str,
    target: str,
) -> str:
    """Handle CLI /subagent using only the current session sidecar metadata."""

    normalized = target.strip().lower()
    if normalized not in {"", "auto", "off", "once"}:
        return console.warning("Usage: /subagent")
    if not runtime.status.available:
        return console.warning(_format_cli_subagent_unavailable(runtime.status))
    if normalized in {"auto", "off"}:
        preference = runtime.preferences.set_mode(runtime.context, session_id, normalized)
    elif normalized == "once":
        preference = runtime.preferences.force_once(runtime.context, session_id)
    else:
        preference = runtime.preferences.get(runtime.context, session_id)
    return _format_cli_subagent_status(preference, runtime.profiles)


def _format_cli_subagent_unavailable(status: CapabilityStatus | None) -> str:
    """Return the same human-facing capability guidance used by Web commands."""

    return format_subagent_unavailable(status, include_details=True)


def _format_cli_capability_status(status: CapabilityStatus) -> str:
    """Format a generic optional-capability startup warning."""

    return json.dumps(status.to_dict(), ensure_ascii=False, indent=2)


def _format_cli_subagent_status(
    preference: SessionSubagentPreference,
    profiles: tuple[tuple[str, str], ...],
) -> str:
    lines = [
        f"current subagent mode: {console.command(preference.mode)}",
        f"force once: {'true' if preference.force_once else 'false'}",
        "",
        "available profiles:",
    ]
    if profiles:
        lines.extend(f"  {name:<18} {description}" for name, description in profiles)
    else:
        lines.append("  (no model-callable profiles available)")
    lines.extend(
        [
            "",
            "Tip: use '/subagent auto' to allow automatic delegation, "
            "'/subagent off' to disable it, or '/subagent once' to use Subagent "
            "for the next message.",
        ]
    )
    return "\n".join(lines)


def _load_subagent_profile_summaries(config_dir) -> tuple[tuple[str, str], ...]:
    """Load enabled model-callable Profile summaries through the Part 13 loader."""

    try:
        from agent.subagents.config import load_subagent_config

        config = load_subagent_config(config_dir / "subagents.yml")
    except (ImportError, OSError, ValueError):
        return ()
    summaries: list[tuple[str, str]] = []
    if not config.enabled:
        return ()
    for profile in config.list_profiles():
        if not getattr(profile, "enabled", True):
            continue
        if not getattr(profile, "allow_model_invocation", True):
            continue
        name = str(profile.name).strip()
        description = str(getattr(profile, "description", "")).strip()
        if name:
            summaries.append((name, description or "Available Subagent profile"))
    return tuple(summaries)


def _cli_turn_provider(runtime: _CliModelRuntime, session_id: str):
    """Bind one independent provider and its failover-safe input budget."""

    preference = runtime.preferences.get(runtime.context, session_id)
    selection = runtime.resolver.resolve(preference)
    return runtime.resolver.bind(selection), selection.context_budget


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
        ("/clear", "clear the current session history"),
        ("/sessions", "list stored sessions and previews"),
        ("/history", "print recent messages from the current session"),
        ("/prompts", "list loaded prompt files"),
        ("/tools", "list registered tools"),
        ("/skills", "list discovered local Skills"),
        ("/model", "show or switch the preferred LLM endpoint"),
        ("/subagent", "show or control Subagent delegation"),
        ("/memory", "show current Memory"),
        ("/mcp", "show available MCP capabilities"),
        ("/exit", "leave the CLI"),
    ]
    for name, description in commands:
        print(f"{console.command(name):<18}{description}")


def _handle_cli_mcp_interaction(
    runtime: McpRuntime,
    request: McpInteractionRequest,
) -> None:
    """Prompt for one Server-originated Elicitation and submit the answer."""

    print(f"\nMCP {request.server_id}: {request.message}")
    if request.url:
        print(f"URL: {request.url}")
    if request.requested_schema:
        print("Expected JSON schema:")
        print(json.dumps(request.requested_schema, indent=2, ensure_ascii=False))
    try:
        raw = input("MCP response JSON (blank=accept {}, /decline, /cancel): ").strip()
    except EOFError:
        raw = "/cancel"
    if raw == "/decline":
        response = McpInteractionResponse(action="decline")
    elif raw == "/cancel":
        response = McpInteractionResponse(action="cancel")
    else:
        try:
            content = json.loads(raw) if raw else {}
            if not isinstance(content, dict):
                raise ValueError("response must be a JSON object")
            response = McpInteractionResponse(action="accept", content=content)
        except (ValueError, json.JSONDecodeError) as exc:
            print(console.error(f"Invalid MCP response: {exc}"))
            response = McpInteractionResponse(action="cancel")
    runtime.submit_interaction(request.interaction_id, response)


def _print_skills(skill_loader: SkillLoader) -> None:
    """Print discovered local Skill summaries for debugging."""

    skills = skill_loader.list_skills()
    if not skills:
        print(console.warning("(no skills)"))
    for skill in skills:
        print(f"{console.command(skill.qualified_name):<24}{skill.description}")
    for error in skill_loader.load_errors:
        path = error.get("path", "")
        code = error.get("code", "SKILL_ERROR")
        message = error.get("message", "")
        print(console.warning(f"skipped skill [{code}] {path}: {message}"))
    print()
    print(
        console.warning(
            "Tip: use '/skills sync [--verbose] [source_name]' to sync configured sources."
        )
    )
    print(
        console.warning(
            "Optional args: --verbose prints details, source_name syncs one source."
        )
    )


def _format_session_time(timestamp: float) -> str:
    """Render a readable local timestamp for session listings."""

    if timestamp <= 0:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _build_llm_provider(config_dir, endpoint_name):
    """Build the runtime LLM provider chain from endpoint config."""

    return create_configured_llm_provider(config_dir, endpoint_name)


def _validate_startup_llm_endpoints(endpoints, preferred_endpoint: str | None) -> None:
    """Reject endpoint sets that cannot produce any chat response."""

    validate_startup_llm_endpoints(endpoints, preferred_endpoint)


def _resolve_preferred_endpoint(config_dir, endpoint_name, endpoints):
    """Resolve CLI --endpoint into the concrete startup preference.

    ``auto`` means: use a configured default alias if present, else a real
    endpoint named "default", else let the failover provider choose by priority.
    """

    return resolve_preferred_endpoint(config_dir, endpoint_name, endpoints)


if __name__ == "__main__":
    raise SystemExit(main())
