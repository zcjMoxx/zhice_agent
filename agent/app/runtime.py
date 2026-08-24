"""Runtime dependency assembly for the local Web app."""

from __future__ import annotations

import inspect
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event, Lock
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yaml

from agent.app.auth import AuthService, local_operator_actor
from agent.applications.travel import (
    TravelApplicationService,
    TravelRequirementExtractor,
    load_travel_config,
)
from agent.applications.travel.config import TravelConfig, TravelConfigurationError
from agent.applications.travel.history import project_travel_progress
from agent.applications.travel.hotel_accounts import HotelAccountSupervisor
from agent.applications.travel.progress import TravelProgressHookRuntime
from agent.applications.travel.requirements import TravelRequirementDraft
from agent.applications.travel.service import TravelApplicationError
from agent.applications.travel.source_ledger import (
    guard_travel_tools,
    preferred_travel_tool_names,
    require_travel_finalization_before_saving,
    require_travel_research_before_solving,
    source_category,
)
from agent.applications.travel.subagents import (
    TRAVEL_FINAL_ROUTE_PROFILE,
    TRAVEL_FINAL_STAY_PROFILE,
    TRAVEL_FINAL_WEATHER_PROFILE,
    compact_travel_final_route_results,
    require_exact_travel_delegation,
    travel_subagent_config_for_stage,
)
from agent.applications.travel.tools import (
    TRAVEL_INTAKE_DRAFT_VERSION,
    recover_intake_draft,
)
from agent.applications.travel.xhs_sidecar import LocalXhsSidecarSupervisor
from agent.auth.activity import SqliteRuntimeActivitySink
from agent.auth.audit import SqliteAuditSink
from agent.auth.confirmation import SQLiteToolConfirmationBroker
from agent.auth.diagnostics import RecentActivityDiagnostics, SystemDiagnosticsService
from agent.auth.session_access import SessionAccessError, SessionAccessService
from agent.auth.store import SQLiteAuthStore
from agent.auth.tool_policy import RbacToolExecutionPolicy
from agent.auth.user_context import FilesystemUserContextResolver
from agent.config import AppConfig, sync_managed_application_prompts
from agent.connections.crypto import CredentialCipher, load_master_key
from agent.connections.protocols import ConnectionError
from agent.connections.runtime import ConnectionRuntime
from agent.connections.store import SQLiteConnectionStore
from agent.context.config import load_context_config
from agent.context.startup import check_context_engineering_startup
from agent.core.context import DEFAULT_CONTEXT_PROMPTS, ContextBuilder
from agent.core.loop import AgentLoop, CancellationToken
from agent.core.turns import new_turn_id
from agent.hooks import create_hook_runtime
from agent.integrations.email.official_smtp import OfficialSMTPEmailProvider
from agent.llm.runtime import (
    create_configured_llm_provider,
    create_optional_aliased_llm_provider,
)
from agent.llm.selection import ConfiguredLLMProviderResolver
from agent.logging_utils import log_event
from agent.mcp import McpRuntime, check_mcp_startup
from agent.memory import MemoryStoreError
from agent.memory.context import build_memory_context
from agent.memory.extraction import MemoryExtractionService, pop_memory_notification
from agent.memory.markdown_store import MarkdownMemoryStore
from agent.memory.presentation import format_memory_list
from agent.memory.safety import MemorySafetyPolicy
from agent.memory.scheduler import MemoryExtractionJob, MemoryExtractionScheduler
from agent.memory.startup import check_memory_extraction_startup
from agent.message import Message
from agent.prompt_loader import PromptLoader
from agent.protocols.auth import ActorContext, AuditEvent
from agent.protocols.capability import CapabilityStatus
from agent.protocols.channel import ChannelExecutionContext
from agent.protocols.diagnostics import DiagnosticContext
from agent.protocols.errors import ErrorCode
from agent.protocols.llm import (
    LLMConfigurationError,
    LLMEndpoint,
    LLMProvider,
    ModelSelection,
)
from agent.protocols.mcp import McpInteractionResponse
from agent.protocols.session import (
    SessionContext,
    SessionModelPreference,
    SessionState,
    SessionStore,
    SessionSummary,
)
from agent.protocols.subagent import SubagentProfile
from agent.protocols.tool import ToolProvider, ToolResult
from agent.session import (
    JsonlSessionStore,
    JsonSessionModelPreferenceStore,
    JsonSessionSubagentPreferenceStore,
    SessionSubagentPreference,
)
from agent.skills import SkillLoader, SkillSourceSync
from agent.skills.status import SkillSourceStatusStore
from agent.skills.sync import SkillSyncError
from agent.subagents.config import SubagentConfig
from agent.subagents.presentation import can_view_subagent_details, format_subagent_unavailable
from agent.subagents.runtime import (
    build_turn_subagent_provider,
    build_unavailable_subagent_provider,
)
from agent.subagents.startup import check_subagent_startup
from agent.tools import (
    FilteredToolProvider,
    UserScopedToolProvider,
    create_default_tool_registry,
    with_tool_discovery,
)
from agent.tools.registry import ToolRegistry
from agent.workflows import (
    WorkflowAuthorizationPolicy,
    WorkflowExecutor,
    WorkflowRuntime,
    WorkflowScheduler,
    WorkflowStore,
)
from agent.workflows.nodes import NodeHandlers
from agent.workflows.tool_inputs import with_required_query_helpers

DEFAULT_WEB_HISTORY_MESSAGES = 60
TRAVEL_INTAKE_PHASE = "intake"
TRAVEL_PLANNING_PHASE = "planning"
RuntimeEventCallback = Callable[[dict[str, Any]], None]
_TRAVEL_CANDIDATE_RESEARCH_PROFILES = frozenset(
    {"travel-transport-weather", "travel-stay-poi", "travel-guides"}
)
_TRAVEL_FINAL_PROFILE_CATEGORIES = {
    TRAVEL_FINAL_STAY_PROFILE: "lodging",
    TRAVEL_FINAL_ROUTE_PROFILE: "maps",
    TRAVEL_FINAL_WEATHER_PROFILE: "weather",
}


def _travel_message_timestamp(message: Message) -> float:
    try:
        return float(message.metadata.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def _persisted_travel_child_messages(
    sessions_dir: object,
    session_id: str,
    parent_messages: list[Message] | None = None,
) -> tuple[list[Message], list[Message]]:
    """Return persisted candidate and finalization Child messages separately.

    A selected Session may be resumed after process restart.  The source ledger must
    replay candidate research before opening the finalization budget, and replay only
    ``travel-final-*`` children afterwards.  Mixing both phases makes old candidate
    hotel/POI calls look like the required selected-itinerary detail batch.
    """

    if sessions_dir is None:
        return [], []
    child_dir = Path(str(sessions_dir)) / "_subagents" / session_id
    if not child_dir.is_dir():
        return [], []
    final_child_ids = _persisted_finalization_child_ids(parent_messages or [])
    child_store = JsonlSessionStore(child_dir)
    candidate_messages: list[Message] = []
    finalization_messages: list[Message] = []
    for path in sorted(child_dir.glob("*.jsonl"))[:24]:
        target = (
            finalization_messages
            if path.stem in final_child_ids
            else candidate_messages
        )
        target.extend(child_store.load(path.stem).messages)
    return candidate_messages, finalization_messages


def _persisted_finalization_child_ids(messages: list[Message]) -> set[str]:
    calls: dict[str, set[str]] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        for raw in message.tool_calls:
            if not isinstance(raw, dict):
                continue
            call_id = str(raw.get("id") or "").strip()
            function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
            if str(function.get("name") or "").casefold() != "delegate_tasks":
                continue
            try:
                arguments = function.get("arguments")
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            tasks = parsed.get("tasks") if isinstance(parsed, dict) else None
            if not isinstance(tasks, list):
                continue
            final_task_ids = {
                str(task.get("id") or "").strip()
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("profile") or "").strip()
                in {"travel-final-stay", "travel-final-route", "travel-final-weather"}
                and str(task.get("id") or "").strip()
            }
            if final_task_ids:
                calls[call_id] = final_task_ids

    child_ids: set[str] = set()
    for message in messages:
        if message.role != "tool" or str(message.tool_call_id or "") not in calls:
            continue
        payload = _stored_tool_payload(message.content)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            if str(result.get("id") or "").strip() not in calls[str(message.tool_call_id)]:
                continue
            child_id = str(result.get("child_session_id") or "").strip()
            if child_id:
                child_ids.add(child_id)
    return child_ids


def _persisted_finalization_completed_categories(
    messages: list[Message],
) -> frozenset[str]:
    """Recover successful finalization lanes from the durable parent fan-in."""

    calls: dict[str, dict[str, str]] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        for raw in message.tool_calls:
            if not isinstance(raw, dict):
                continue
            call_id = str(raw.get("id") or "").strip()
            function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
            if not call_id or str(function.get("name") or "").casefold() != "delegate_tasks":
                continue
            try:
                raw_arguments = function.get("arguments")
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            tasks = arguments.get("tasks") if isinstance(arguments, dict) else None
            if not isinstance(tasks, list):
                continue
            task_profiles = {
                str(task.get("id") or "").strip(): str(task.get("profile") or "").strip()
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("id") or "").strip()
                and str(task.get("profile") or "").strip() in _TRAVEL_FINAL_PROFILE_CATEGORIES
            }
            if task_profiles:
                calls[call_id] = task_profiles

    completed: set[str] = set()
    for message in messages:
        call_id = str(message.tool_call_id or "").strip()
        task_profiles = calls.get(call_id)
        if message.role != "tool" or task_profiles is None:
            continue
        payload = _stored_tool_payload(message.content)
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            task_id = str(row.get("id") or "").strip()
            profile = task_profiles.get(task_id)
            if (
                profile
                and str(row.get("status") or "").casefold() == "completed"
                and str(row.get("code") or "").upper() == "OK"
            ):
                completed.add(_TRAVEL_FINAL_PROFILE_CATEGORIES[profile])
    return frozenset(completed)


def _travel_finalization_categories_for_profiles(
    profiles: frozenset[str],
) -> set[str]:
    return {
        category
        for profile in profiles
        if (category := _TRAVEL_FINAL_PROFILE_CATEGORIES.get(profile))
    }


def _persisted_candidate_research_profiles(
    messages: list[Message],
) -> frozenset[str]:
    """Return every successful candidate lane persisted across delegation Turns."""

    candidate_calls: dict[str, dict[str, str]] = {}
    for message in messages:
        if message.role != "assistant":
            continue
        for raw in message.tool_calls:
            if not isinstance(raw, dict):
                continue
            call_id = str(raw.get("id") or "").strip()
            function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
            if not call_id or str(function.get("name") or "").casefold() != "delegate_tasks":
                continue
            try:
                raw_arguments = function.get("arguments")
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            tasks = arguments.get("tasks") if isinstance(arguments, dict) else None
            if not isinstance(tasks, list):
                continue
            task_profiles = {
                str(task.get("id") or "").strip(): str(task.get("profile") or "").strip()
                for task in tasks
                if isinstance(task, dict)
                and str(task.get("id") or "").strip()
                and str(task.get("profile") or "").strip()
            }
            profiles = frozenset(task_profiles.values())
            if (
                profiles
                and len(profiles) == len(task_profiles)
                and profiles.issubset(_TRAVEL_CANDIDATE_RESEARCH_PROFILES)
            ):
                candidate_calls[call_id] = task_profiles

    completed_profiles: set[str] = set()
    for message in messages:
        call_id = str(message.tool_call_id or "").strip()
        expected = candidate_calls.get(call_id)
        if message.role != "tool" or expected is None:
            continue
        payload = _stored_tool_payload(message.content)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        completed = {
            str(result.get("id") or "").strip()
            for result in results
            if isinstance(result, dict)
            and str(result.get("id") or "").strip() in expected
            and str(result.get("status") or "").casefold() == "completed"
            and str(result.get("code") or "").upper() == "OK"
        }
        completed_profiles.update(expected[task_id] for task_id in completed)
    return frozenset(completed_profiles)


def _persisted_candidate_research_complete(messages: list[Message]) -> bool:
    """Recognize one fully successful candidate fan-in from parent Session history."""

    return _TRAVEL_CANDIDATE_RESEARCH_PROFILES.issubset(
        _persisted_candidate_research_profiles(messages)
    )


def _latest_travel_finalizer_error_code(messages: list[Message]) -> str:
    """Return only the newest persisted finalizer error code for recovery routing."""

    for message in reversed(messages):
        if message.role != "tool" or str(message.name or "").casefold() != "finalize_travel_plan":
            continue
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        code = str(metadata.get("code") or "").strip().upper()
        return code if metadata.get("is_error") is True or code not in {"", "OK"} else ""
    return ""


def _persisted_travel_finalizer_plans(
    messages: list[Message],
) -> list[dict[str, Any]]:
    """Return Finalizer plan arguments in durable Session order."""

    plans: list[dict[str, Any]] = []
    for message in messages:
        if message.role != "assistant":
            continue
        for raw in message.tool_calls:
            if not isinstance(raw, dict):
                continue
            function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
            if str(function.get("name") or "").casefold() != "finalize_travel_plan":
                continue
            try:
                raw_arguments = function.get("arguments")
                arguments = (
                    json.loads(raw_arguments)
                    if isinstance(raw_arguments, str)
                    else raw_arguments
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            plan = arguments.get("plan") if isinstance(arguments, dict) else None
            if isinstance(plan, dict):
                plans.append(plan)
    return plans


def _persisted_travel_finalizer_attempts(
    messages: list[Message],
    *,
    weather_source_verified: bool,
    transit_source_verified: bool,
) -> list[dict[str, Any]]:
    """Recover Finalizer drafts with source verification available at that point."""

    delegate_calls: dict[str, dict[str, str]] = {}
    attempts: list[dict[str, Any]] = []
    weather_ready = False
    route_ready = False
    for message in messages:
        if message.role == "assistant":
            for raw in message.tool_calls:
                if not isinstance(raw, dict):
                    continue
                call_id = str(raw.get("id") or "").strip()
                function = raw.get("function") if isinstance(raw.get("function"), dict) else {}
                name = str(function.get("name") or "").casefold()
                try:
                    raw_arguments = function.get("arguments")
                    arguments = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if name == "delegate_tasks":
                    tasks = arguments.get("tasks") if isinstance(arguments, dict) else None
                    if call_id and isinstance(tasks, list):
                        delegate_calls[call_id] = {
                            str(task.get("id") or "").strip(): str(task.get("profile") or "").strip()
                            for task in tasks
                            if isinstance(task, dict) and str(task.get("id") or "").strip()
                        }
                elif name == "finalize_travel_plan":
                    plan = arguments.get("plan") if isinstance(arguments, dict) else None
                    if isinstance(plan, dict):
                        attempts.append(
                            {
                                "plan": plan,
                                "live_weather_verified": weather_ready,
                                "transit_verified": route_ready,
                            }
                        )
            continue
        call_profiles = delegate_calls.get(str(message.tool_call_id or "").strip())
        if message.role != "tool" or call_profiles is None:
            continue
        payload = _stored_tool_payload(message.content)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            continue
        completed_profiles = {
            call_profiles.get(str(result.get("id") or "").strip(), "")
            for result in results
            if isinstance(result, dict)
            and str(result.get("status") or "").casefold() == "completed"
            and str(result.get("code") or "").upper() == "OK"
        }
        weather_ready = weather_ready or (
            weather_source_verified
            and TRAVEL_FINAL_WEATHER_PROFILE in completed_profiles
        )
        route_ready = route_ready or (
            transit_source_verified
            and TRAVEL_FINAL_ROUTE_PROFILE in completed_profiles
        )
    return attempts


def _travel_finalization_repair_profiles(
    messages: list[Message],
    snapshot: Any,
) -> frozenset[str]:
    """Map a persisted, source-repairable finalizer error to one bounded Profile."""

    code = _latest_travel_finalizer_error_code(messages)
    if code in {
        "TRAVEL_WEATHER_FORECAST_REQUIRED",
        "TRAVEL_WEATHER_FORECAST_EVIDENCE_MISSING",
        "TRAVEL_WEATHER_EVIDENCE_MISSING",
    } and not bool(
        getattr(snapshot, "forecast_successful", False)
    ):
        return frozenset({TRAVEL_FINAL_WEATHER_PROFILE})
    if code in {
        "TRAVEL_ROUTE_EVIDENCE_MISSING",
        "TRAVEL_TRANSIT_EVIDENCE_MISSING",
    } and not bool(
        getattr(snapshot, "route_repair_attempted", False)
    ):
        return frozenset({TRAVEL_FINAL_ROUTE_PROFILE})
    return frozenset()


def _travel_forecast_window_context(draft: object) -> str:
    """Build deterministic date-window facts so the model never estimates them itself."""

    payload = draft if isinstance(draft, dict) else {}
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    window_end = today + timedelta(days=15)
    start_text = str(payload.get("start_date") or "")
    end_text = str(payload.get("end_date") or "")
    within = False
    try:
        start = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        within = today <= start <= end <= window_end
    except ValueError:
        pass
    return json.dumps(
        {
            "destination": next(iter(payload.get("destinations") or []), ""),
            "start_date": start_text,
            "end_date": end_text,
            "beijing_today": today.isoformat(),
            "forecast_window_end": window_end.isoformat(),
            "inside_forecast_window": within,
            "required_weather_tool": "get_forecast" if within else "get_historical_weather",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _stored_tool_payload(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("output"), str):
        try:
            nested = json.loads(payload["output"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return payload
        return nested if isinstance(nested, dict) else payload
    return payload if isinstance(payload, dict) else {}


def _rehydrate_travel_search_evidence(
    ledger: object,
    session_id: str,
    messages: list[Message],
    *,
    finalization: bool,
    only_categories: frozenset[str] | None = None,
) -> None:
    observe = getattr(ledger, "observe", None)
    if not callable(observe):
        return
    for message in messages:
        category = source_category(str(message.name or ""))
        if (
            message.role != "tool"
            or not category
            or (only_categories is not None and category not in only_categories)
        ):
            continue
        output = message.content
        metadata = dict(message.metadata)
        is_error = bool(metadata.get("is_error"))
        try:
            stored = json.loads(message.content)
        except (TypeError, ValueError, json.JSONDecodeError):
            stored = None
        if isinstance(stored, dict) and isinstance(stored.get("output"), str):
            output = stored["output"]
            stored_metadata = stored.get("metadata")
            if isinstance(stored_metadata, dict):
                metadata = {**stored_metadata, **metadata}
            is_error = bool(stored.get("status") == "error" or metadata.get("is_error"))
        observe(
            session_id,
            str(message.name or ""),
            ToolResult(
                output=output,
                is_error=is_error,
                metadata=metadata,
            ),
            record_finalization=finalization,
        )
web_logger = logging.getLogger("zcagent.agent.web")
session_logger = logging.getLogger("zcagent.agent.session")
memory_logger = logging.getLogger("zcagent.agent.memory")
skill_logger = logging.getLogger("zcagent.agent.skills")
WEB_COMMAND_PROFILE = "web"
EXTERNAL_COMMAND_PROFILE = "external"
QQ_C2C_COMMAND_PROFILE = "qq_c2c"
QQ_GROUP_COMMAND_PROFILE = "qq_group"
EXTERNAL_STOP_PROFILES = frozenset(
    {EXTERNAL_COMMAND_PROFILE, QQ_C2C_COMMAND_PROFILE, QQ_GROUP_COMMAND_PROFILE}
)


@dataclass
class ModelState:
    """Current endpoint model state exposed to the Web UI."""

    endpoint: str
    current_model: str
    models: list[str]


@dataclass
class ChatTurnResult:
    """Final result of one Web chat turn."""

    content: str
    stopped: bool = False
    turn_id: str = ""


@dataclass
class ActiveTurn:
    """Cancellation state for one active session turn."""

    turn_id: str
    token: CancellationToken
    subagent_force_once: bool = False
    completed: Event = field(default_factory=Event)


@dataclass
class WebRuntime:
    """Dependencies needed by HTTP routes without exposing construction details."""

    config: AppConfig
    sessions: SessionStore
    agent_loop: AgentLoop
    llm: LLMProvider
    auth: AuthService | None = None
    session_access: SessionAccessService | None = None
    model_preferences: JsonSessionModelPreferenceStore | None = None
    subagent_preferences: JsonSessionSubagentPreferenceStore | None = None
    subagent_profiles: tuple[tuple[str, str], ...] = ()
    subagent_config: SubagentConfig | None = None
    subagent_status: CapabilityStatus | None = None
    mcp_status: CapabilityStatus | None = None
    memory_extraction_status: CapabilityStatus | None = None
    context_engineering_status: CapabilityStatus | None = None
    travel_status: CapabilityStatus | None = None
    llm_resolver: ConfiguredLLMProviderResolver | None = None
    tool_policy: RbacToolExecutionPolicy | None = None
    confirmation_broker: SQLiteToolConfirmationBroker | None = None
    activity_sink: SqliteRuntimeActivitySink | None = None
    audit_sink: SqliteAuditSink | None = None
    diagnostics: RecentActivityDiagnostics | None = None
    system_diagnostics: SystemDiagnosticsService | None = None
    skill_loader: SkillLoader | None = None
    skill_sync: SkillSourceSync | None = None
    skill_status: SkillSourceStatusStore | None = None
    prompt_loader: PromptLoader | None = None
    memory_extraction_enabled: bool = False
    memory_idle_seconds: float = 300.0
    memory_extraction_max_workers: int = 2
    memory_extraction_max_pending_jobs: int = 1000
    memory_scheduler: MemoryExtractionScheduler | None = None
    mcp_runtime: McpRuntime | None = None
    channel_manager: Any | None = None
    channel_status: CapabilityStatus | None = None
    channel_statuses: dict[str, CapabilityStatus] | None = None
    channel_identity: Any | None = None
    channel_config: Any | None = None
    channel_weixin_binding: Any | None = None
    travel_service: TravelApplicationService | None = None
    travel_requirement_extractor: TravelRequirementExtractor | None = None
    xhs_sidecar: LocalXhsSidecarSupervisor | None = None
    hotel_accounts: HotelAccountSupervisor | None = None
    connection_runtime: ConnectionRuntime | None = None
    official_email_provider: Any | None = None
    workflow_runtime: WorkflowRuntime | None = None

    def __post_init__(self) -> None:
        self._active_turns: dict[tuple[str, str], ActiveTurn] = {}
        self._turns_lock = Lock()
        self._accepting_turns = True
        self._shutdown_complete = False

    def startup(self) -> int:
        """Recover process-local runtime facts before accepting Gateway traffic."""

        recovered = 0
        store = getattr(self.auth, "store", None)
        recover = getattr(store, "recover_interrupted_turn_runs", None)
        if callable(recover):
            recovered = int(recover())
        with self._turns_lock:
            self._accepting_turns = True
            self._shutdown_complete = False
        if recovered:
            log_event(
                web_logger,
                logging.WARNING,
                "gateway.turns_recovered",
                count=recovered,
                reason_code="GATEWAY_RESTART_INTERRUPTED",
            )
        return recovered

    def capability_statuses(self) -> dict[str, CapabilityStatus]:
        """Return transport-neutral optional capability state for health/UI."""

        statuses: dict[str, CapabilityStatus] = {}
        if self.subagent_status is not None:
            statuses["subagent"] = self.subagent_status
        statuses["mcp"] = self.mcp_status or CapabilityStatus(
            name="mcp",
            state="available" if self.mcp_runtime is not None else "disabled",
            code="MCP_AVAILABLE" if self.mcp_runtime is not None else "MCP_DISABLED",
            message=(
                "MCP runtime is available."
                if self.mcp_runtime is not None
                else "MCP runtime is not configured."
            ),
        )
        if self.memory_extraction_status is not None:
            statuses["memory_extraction"] = self.memory_extraction_status
        if self.context_engineering_status is not None:
            statuses["context_engineering"] = self.context_engineering_status
        if self.travel_status is not None:
            statuses["travel"] = self.travel_status
        if self.channel_status is not None:
            statuses["channel.qq"] = self.channel_status
        if self.channel_statuses is not None:
            statuses.update(self.channel_statuses)
        if self.channel_manager is not None:
            manager_statuses = self.channel_manager.statuses()
            qq_account_statuses = [
                status for key, status in manager_statuses.items() if key.startswith("qq.")
            ]
            if qq_account_statuses:
                statuses["channel.qq"] = _aggregate_qq_channel_status(qq_account_statuses)
            statuses.update(
                (key, status)
                for key, status in manager_statuses.items()
                if not key.startswith("qq.")
            )
        return statuses

    def list_sessions(
        self,
        actor: ActorContext | None = None,
        *,
        request_id: str = "",
    ) -> list[SessionSummary]:
        """Return known sessions for the Web sidebar."""

        if self.session_access is not None and actor is not None and actor.user_id is not None:
            return self.session_access.list_sessions(actor)
        return self.sessions.list_sessions()

    def load_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        *,
        request_id: str = "",
    ) -> SessionState:
        """Load one session for Web history rendering."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        if self.session_access is not None and actor.user_id is not None:
            return self.session_access.load_session(actor, session_id)
        return self.sessions.load(session_id)

    def create_session(self, actor: ActorContext, session_id: str, channel: str = "web") -> None:
        """Claim a new globally unique session id for one authenticated actor."""

        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.ensure_session(
                actor, session_id, channel=channel, write=True
            )
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=actor.user_id,
                    session_id=session_id,
                )
            if channel == "travel":
                state = resolved.store.load(session_id)
                if state.metadata.get("travel_phase") not in {
                    TRAVEL_INTAKE_PHASE,
                    TRAVEL_PLANNING_PHASE,
                }:
                    resolved.store.update_metadata(
                        session_id,
                        {
                            "travel_phase": TRAVEL_INTAKE_PHASE,
                            "travel_draft_version": TRAVEL_INTAKE_DRAFT_VERSION,
                        },
                    )
                    self.session_access.refresh_index(actor, session_id)

    def persist_travel_conversation(
        self,
        actor: ActorContext,
        session_id: str,
        messages: list[dict[str, object]],
        *,
        draft: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Create or update bounded requirement chat in one travel Session."""

        if self.session_access is None or actor.user_id is None:
            raise TravelApplicationError(
                "TRAVEL_CONVERSATION_UNAVAILABLE",
                "旅行需求对话暂时无法保存。",
                status_code=503,
            )
        try:
            resolved = self.session_access.resolve_session(actor, session_id, write=True)
        except SessionAccessError as exc:
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND",
                "旅行规划任务不存在。",
                status_code=404,
            ) from exc
        if resolved.channel != "travel":
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND",
                "旅行规划任务不存在。",
                status_code=404,
            )
        if str(resolved.owner_user_id) != str(actor.user_id):
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND",
                "旅行规划任务不存在。",
                status_code=404,
            )
        normalized: list[Message] = []
        total_chars = 0
        for item in messages:
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant"} or not content or len(content) > 2000:
                raise TravelApplicationError(
                    "TRAVEL_CONVERSATION_INVALID",
                    "旅行需求对话格式无效。",
                    status_code=422,
                )
            total_chars += len(content)
            normalized.append(
                Message(
                    role=role,
                    content=content,
                    metadata={
                        "travel_visibility": "conversation",
                        "travel_phase": "requirements",
                    },
                )
            )
        if not normalized or len(normalized) > 20 or total_chars > 8000:
            raise TravelApplicationError(
                "TRAVEL_CONVERSATION_INVALID",
                "旅行需求对话超出允许范围。",
                status_code=422,
            )
        normalized_draft: dict[str, Any] = {}
        if draft:
            try:
                parsed_draft = TravelRequirementDraft.from_dict(dict(draft))
            except (TravelApplicationError, TypeError, ValueError) as exc:
                raise TravelApplicationError(
                    "TRAVEL_DRAFT_INVALID",
                    "旅行草稿格式无效。",
                    status_code=422,
                ) from exc
            if parsed_draft.intent != "travel_requirement":
                raise TravelApplicationError(
                    "TRAVEL_DRAFT_INVALID",
                    "旅行草稿格式无效。",
                    status_code=422,
                )
            normalized_draft = parsed_draft.to_dict()
        state = resolved.store.load(session_id)
        existing_messages = [
            message
            for message in state.messages
            if message.metadata.get("travel_visibility") == "conversation"
            and message.metadata.get("travel_phase") == "requirements"
        ]
        existing = [(message.role, message.content) for message in existing_messages]
        requested = [(message.role, message.content) for message in normalized]
        store = getattr(self.auth, "store", None)
        list_turns = getattr(store, "list_turn_runs", None)
        turns = (
            list_turns(actor_user_id=str(actor.user_id), session_id=session_id, limit=1)
            if callable(list_turns)
            else []
        )
        if existing == requested and (
            not normalized_draft or state.metadata.get("travel_draft") == normalized_draft
        ):
            return {
                "session_id": session_id,
                "message_count": len(existing),
                "status": "unchanged",
            }
        if turns:
            if requested[: len(existing)] != existing:
                raise TravelApplicationError(
                    "TRAVEL_CONVERSATION_CONFLICT",
                    "已经开始的旅行需求记录不能被覆盖。",
                    status_code=409,
                )
            if self._travel_session_is_running(actor, session_id):
                raise TravelApplicationError(
                    "TRAVEL_CONVERSATION_CONFLICT",
                    "旅行规划正在进行，暂时不能修改需求。",
                    status_code=409,
                )
            if self._travel_plan_for_session(actor, session_id) or self._travel_review_pending(
                actor, session_id
            ):
                raise TravelApplicationError(
                    "TRAVEL_CONVERSATION_CONFLICT",
                    "旅行规划已经进入确认或完成阶段，不能覆盖需求对话。",
                    status_code=409,
                )
            suffix = normalized[len(existing) :]
            if suffix:
                resolved.store.append(session_id, suffix)
            status = "updated"
        else:
            resolved.store.replace(session_id, normalized)
            status = "saved" if not existing else "updated"
        metadata: dict[str, Any] = {
            "travel_draft_version": TRAVEL_INTAKE_DRAFT_VERSION
        }
        if normalized_draft:
            metadata["travel_draft"] = normalized_draft
        title = _travel_draft_title(normalized_draft, normalized)
        if title:
            metadata["title"] = title
        resolved.store.update_metadata(session_id, metadata)
        self.session_access.refresh_index(actor, session_id)
        return {
            "session_id": session_id,
            "message_count": len(normalized),
            "status": status,
        }

    def travel_draft(self, actor: ActorContext, session_id: str) -> dict[str, object]:
        """Return refresh-safe requirement messages and structured draft."""

        resolved = self._resolve_travel_session(actor, session_id)
        state = resolved.store.load(session_id)
        intake_turn_ids = {
            str(item)
            for item in state.metadata.get("travel_intake_turn_ids", [])
            if str(item).strip()
        }
        messages = [
            {"role": message.role, "content": message.content}
            for message in state.messages
            if message.role in {"user", "assistant"}
            and message.content.strip()
            and not message.tool_calls
            and (
                (
                    message.metadata.get("travel_visibility") == "conversation"
                    and message.metadata.get("travel_phase") == "requirements"
                )
                or (message.turn_id is not None and message.turn_id in intake_turn_ids)
            )
        ]
        draft = recover_intake_draft(state.metadata, state.messages)
        if state.metadata.get("travel_draft_version") != TRAVEL_INTAKE_DRAFT_VERSION:
            resolved.store.update_metadata(
                session_id,
                {
                    "travel_draft": draft,
                    "travel_draft_version": TRAVEL_INTAKE_DRAFT_VERSION,
                },
            )
        raw_location_clarifications = state.metadata.get(
            "travel_location_clarifications", []
        )
        if not isinstance(raw_location_clarifications, list):
            raw_location_clarifications = []
        return {
            "session_id": session_id,
            "messages": messages[-20:],
            "draft": dict(draft) if isinstance(draft, dict) else {},
            "phase": self._travel_phase(actor, session_id, resolved=resolved),
            "handoff_question": str(state.metadata.get("travel_handoff_question") or ""),
            "location_clarifications": [
                str(item).strip()
                for item in raw_location_clarifications
                if str(item).strip()
            ][:4],
        }

    def travel_progress_history(
        self, actor: ActorContext, session_id: str
    ) -> dict[str, object]:
        """Rebuild bounded user-facing progress from one actor-owned travel Session."""

        resolved = self._resolve_travel_session(actor, session_id)
        state = resolved.store.load(session_id)
        messages = list(state.messages)
        sessions_dir = getattr(resolved.store, "sessions_dir", None)
        candidate_messages, finalization_messages = _persisted_travel_child_messages(
            sessions_dir,
            session_id,
            messages,
        )
        messages.extend(candidate_messages)
        messages.extend(finalization_messages)
        messages.sort(key=_travel_message_timestamp)
        return {
            "session_id": session_id,
            "items": project_travel_progress(messages),
        }

    def confirm_travel_planning(
        self,
        actor: ActorContext,
        session_id: str,
        draft: dict[str, object],
    ) -> dict[str, str]:
        """Validate the reviewed draft and atomically open formal planning capabilities."""

        resolved = self._resolve_travel_session(actor, session_id)
        state = resolved.store.load(session_id)
        location_clarifications = state.metadata.get("travel_location_clarifications", [])
        if isinstance(location_clarifications, list) and any(
            str(item).strip() for item in location_clarifications
        ):
            raise TravelApplicationError(
                "TRAVEL_LOCATION_CLARIFICATION_REQUIRED",
                "开始规划前请先确认同名地点具体位于哪个省或市。",
                status_code=422,
            )
        try:
            parsed = TravelRequirementDraft.from_dict(dict(draft))
        except (TravelApplicationError, TypeError, ValueError) as exc:
            raise TravelApplicationError(
                "TRAVEL_DRAFT_INVALID",
                "旅行草稿格式无效。",
                status_code=422,
            ) from exc
        normalized = parsed.to_dict()
        missing = _travel_missing_fields(normalized)
        if missing:
            raise TravelApplicationError(
                "TRAVEL_REQUIREMENTS_INCOMPLETE",
                f"开始规划前还需确认：{'、'.join(missing)}。",
                status_code=422,
            )
        normalized = _apply_travel_tone_defaults(normalized)
        metadata: dict[str, Any] = {
            "travel_phase": TRAVEL_PLANNING_PHASE,
            "travel_draft": normalized,
            "travel_draft_version": TRAVEL_INTAKE_DRAFT_VERSION,
            "travel_planning_confirmed_at": datetime.now(UTC).isoformat(),
            "travel_handoff_question": "",
            "travel_handoff_topic": "",
        }
        title = _travel_draft_title(normalized, state.messages)
        if title:
            metadata["title"] = title
        resolved.store.update_metadata(session_id, metadata)
        if self.session_access is not None:
            self.session_access.refresh_index(actor, session_id)
        return {
            "session_id": session_id,
            "phase": TRAVEL_PLANNING_PHASE,
            "status": "confirmed",
        }

    def list_travel_work_items(
        self, actor: ActorContext, *, limit: int = 50
    ) -> list[dict[str, str]]:
        """Merge travel Sessions, Turns, reviews, and plans for the sidebar."""

        if self.session_access is None or actor.user_id is None:
            return []
        store = getattr(self.auth, "store", None)
        list_index = getattr(store, "session_index_list", None)
        if not callable(list_index):
            return []
        bounded = max(1, min(int(limit), 100))
        rows = [
            row
            for row in list_index(str(actor.user_id))
            if str(row.get("channel") or "") == "travel"
        ][:bounded]
        plans = self.travel_service.list_plans(actor, limit=bounded) if self.travel_service else []
        plan_by_session = {str(item.source_session_id): item for item in plans}
        list_turns = getattr(store, "list_turn_runs", None)
        all_turns = (
            list_turns(actor_user_id=str(actor.user_id), limit=500)
            if callable(list_turns)
            else []
        )
        turn_by_session: dict[str, dict[str, Any]] = {}
        for turn in all_turns:
            candidate = str(turn.get("session_id") or "")
            if candidate and candidate not in turn_by_session:
                turn_by_session[candidate] = turn
        actor_key = _active_turn_key(actor, "")[0]
        with self._turns_lock:
            active_sessions = {
                candidate_session_id
                for (candidate_actor, candidate_session_id), _active in self._active_turns.items()
                if candidate_actor == actor_key
            }
        items: list[dict[str, str]] = []
        for row in rows:
            candidate = str(row.get("session_id") or "")
            plan = plan_by_session.get(candidate)
            state = self.session_access.resolve_session(actor, candidate).store.load(candidate)
            phase = self._travel_phase(actor, candidate)
            draft = state.metadata.get("travel_draft")
            title = str(row.get("title") or state.metadata.get("title") or "").strip()
            if not title:
                title = _travel_draft_title(
                    dict(draft) if isinstance(draft, dict) else {},
                    [
                        message
                        for message in state.messages
                        if message.metadata.get("travel_phase") == "requirements"
                    ],
                ) or "旅行需求草稿"
            status, error_code = "collecting", ""
            plan_id = ""
            persisted_finalizer_error = _latest_travel_finalizer_error_code(state.messages)
            if plan is not None:
                status, plan_id = "completed", str(plan.plan_id)
                title = str(plan.title or title)
            elif phase == TRAVEL_INTAKE_PHASE:
                status = "collecting"
            elif candidate in active_sessions:
                status = "running"
            elif self._travel_review_pending(actor, candidate):
                status = "awaiting_candidate"
            elif persisted_finalizer_error:
                status = "failed"
                error_code = persisted_finalizer_error
            elif candidate in turn_by_session:
                status = "failed"
                error_code = str(
                    turn_by_session[candidate].get("error_code")
                    or "TRAVEL_PLAN_NOT_FINALIZED"
                )
            items.append(
                {
                    "session_id": candidate,
                    "plan_id": plan_id,
                    "status": status,
                    "title": title[:120],
                    "preview": str(row.get("preview") or "旅行需求")[:160],
                    "updated_at": str(row.get("updated_at") or ""),
                    "error_code": error_code,
                }
            )
        return items

    def delete_travel_work_item(self, actor: ActorContext, session_id: str) -> None:
        """Delete one unfinished travel Session and transient application state."""

        self._resolve_travel_session(actor, session_id)
        if self._travel_plan_for_session(actor, session_id):
            raise TravelApplicationError(
                "TRAVEL_WORK_ITEM_COMPLETED",
                "已完成计划请通过计划删除入口删除。",
                status_code=409,
            )
        if self.travel_service is not None:
            self.travel_service.clear_candidate_review(actor, session_id)
            self.travel_service.source_ledger.clear(session_id)
        self.delete_session(actor, session_id)

    def _resolve_travel_session(self, actor: ActorContext, session_id: str):
        if self.session_access is None or actor.user_id is None:
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404
            )
        try:
            resolved = self.session_access.resolve_session(actor, session_id, write=True)
        except SessionAccessError as exc:
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404
            ) from exc
        if resolved.channel != "travel":
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404
            )
        if str(resolved.owner_user_id) != str(actor.user_id):
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404
            )
        return resolved

    def _travel_phase(self, actor: ActorContext, session_id: str, *, resolved=None) -> str:
        """Return and backfill the bounded application phase for a travel Session."""

        current = resolved or self._resolve_travel_session(actor, session_id)
        state = current.store.load(session_id)
        phase = str(state.metadata.get("travel_phase") or "")
        if phase in {TRAVEL_INTAKE_PHASE, TRAVEL_PLANNING_PHASE}:
            return phase
        auth_store = getattr(self.auth, "store", None)
        list_turns = getattr(auth_store, "list_turn_runs", None)
        existing_turns = (
            list_turns(
                actor_user_id=str(actor.user_id),
                session_id=session_id,
                limit=1,
            )
            if callable(list_turns)
            else []
        )
        phase = (
            TRAVEL_PLANNING_PHASE
            if self._travel_plan_for_session(actor, session_id)
            or self._travel_review_pending(actor, session_id)
            or existing_turns
            else TRAVEL_INTAKE_PHASE
        )
        current.store.update_metadata(session_id, {"travel_phase": phase})
        if self.session_access is not None:
            self.session_access.refresh_index(actor, session_id)
        return phase

    def _travel_session_is_running(self, actor: ActorContext, session_id: str) -> bool:
        actor_key = _active_turn_key(actor, "")[0]
        with self._turns_lock:
            return (actor_key, session_id) in self._active_turns

    def _travel_plan_for_session(self, actor: ActorContext, session_id: str):
        if self.travel_service is None:
            return None
        return next(
            (
                item
                for item in self.travel_service.list_plans(actor, limit=100)
                if str(item.source_session_id) == session_id
            ),
            None,
        )

    def _travel_review_pending(self, actor: ActorContext, session_id: str) -> bool:
        if self.travel_service is None:
            return False
        review = self.travel_service.get_candidate_review(actor, session_id)
        return review is not None and review.status == "pending"

    def delete_travel_plan(self, actor: ActorContext, plan_id: str) -> None:
        """Delete one actor-owned travel plan and its associated travel Session."""

        if self.travel_service is None:
            raise TravelApplicationError(
                "TRAVEL_DISABLED", "Travel planning is not enabled.", status_code=503
            )
        source_session_id = self.travel_service.delete_plan(actor, plan_id)
        if not source_session_id or self.session_access is None or actor.user_id is None:
            return
        try:
            resolved = self.session_access.resolve_session(actor, source_session_id, delete=True)
        except SessionAccessError:
            return
        if resolved.channel != "travel":
            return
        self.session_access.delete_session(actor, source_session_id)

    def travel_generation_status(
        self,
        actor: ActorContext,
        *,
        session_id: str = "",
    ) -> dict[str, str]:
        """Return one actor-owned travel Turn state without exposing messages."""

        if self.session_access is None or actor.user_id is None:
            return {"status": "idle", "session_id": "", "turn_id": "", "plan_id": "", "error_code": ""}
        store = getattr(self.auth, "store", None)
        list_index = getattr(store, "session_index_list", None)
        if not callable(list_index):
            return {"status": "idle", "session_id": "", "turn_id": "", "plan_id": "", "error_code": ""}
        rows = [
            row
            for row in list_index(str(actor.user_id))
            if str(row.get("channel") or "") == "travel"
        ]
        owned_rows = {str(row.get("session_id") or ""): row for row in rows}
        owned_session_ids = set(owned_rows)
        requested = str(session_id or "").strip()
        if requested and requested not in owned_session_ids:
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND",
                "旅行规划任务不存在。",
                status_code=404,
            )

        actor_key = _active_turn_key(actor, "")[0]
        with self._turns_lock:
            active_items = [
                (candidate_session_id, active)
                for (candidate_actor, candidate_session_id), active in self._active_turns.items()
                if candidate_actor == actor_key
                and candidate_session_id in owned_session_ids
                and (not requested or candidate_session_id == requested)
            ]
        if active_items:
            candidate_session_id, active = active_items[0]
            persisted_error = ""
            if self.travel_service is not None:
                try:
                    resolved = self._resolve_travel_session(actor, candidate_session_id)
                    persisted_error = _latest_travel_finalizer_error_code(
                        resolved.store.load(candidate_session_id).messages
                    )
                except (TravelApplicationError, OSError):
                    persisted_error = ""
            return {
                "status": "running",
                "session_id": candidate_session_id,
                "turn_id": active.turn_id,
                "plan_id": "",
                "error_code": persisted_error,
            }
        if not requested:
            return {"status": "idle", "session_id": "", "turn_id": "", "plan_id": "", "error_code": ""}

        if self.travel_service is not None:
            for plan in self.travel_service.list_plans(actor, limit=50):
                if str(plan.source_session_id) == requested:
                    return {
                        "status": "completed",
                        "session_id": requested,
                        "turn_id": str(plan.source_turn_id),
                        "plan_id": str(plan.plan_id),
                        "error_code": "",
                    }
            review_reader = getattr(self.travel_service, "get_candidate_review", None)
            review = review_reader(actor, requested) if callable(review_reader) else None
            if review is not None and review.status == "pending":
                return {
                    "status": "awaiting_candidate",
                    "session_id": requested,
                    "turn_id": str(review.turn_id),
                    "plan_id": "",
                    "error_code": "",
                }
            if review is not None and review.status == "selected":
                resolved = self._resolve_travel_session(actor, requested)
                persisted_error = _latest_travel_finalizer_error_code(
                    resolved.store.load(requested).messages
                )
                if persisted_error:
                    return {
                        "status": "failed",
                        "session_id": requested,
                        "turn_id": str(review.turn_id),
                        "plan_id": "",
                        "error_code": persisted_error,
                    }

        list_turns = getattr(store, "list_turn_runs", None)
        turns = (
            list_turns(actor_user_id=str(actor.user_id), session_id=requested, limit=1)
            if callable(list_turns)
            else []
        )
        if not turns:
            updated_at = str(owned_rows[requested].get("updated_at") or "")
            status = "pending" if _is_recent_timestamp(updated_at, seconds=60) else "finished"
            turn_id, error_code = "", ""
        else:
            turn = turns[0]
            raw_status = str(turn.get("status") or "")
            status = {"error": "failed", "stopped": "stopped"}.get(raw_status, "failed")
            turn_id = str(turn.get("turn_id") or "")
            error_code = (
                str(turn.get("error_code") or "TRAVEL_PLAN_NOT_FINALIZED")
                if status == "failed"
                else ""
            )
        return {
            "status": status,
            "session_id": requested,
            "turn_id": turn_id,
            "plan_id": "",
            "error_code": error_code,
        }

    def travel_continuation_message(self, actor: ActorContext, session_id: str) -> str:
        """Return the runtime Prompt used for a bounded travel continuation Turn."""

        if self.session_access is None or actor.user_id is None:
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404
            )
        row = getattr(self.auth, "store", None)
        row = row.session_index_get(session_id) if row is not None else None
        if (
            row is None
            or str(row.get("owner_user_id") or "") != str(actor.user_id)
            or str(row.get("channel") or "") != "travel"
        ):
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404
            )
        resolved = self._resolve_travel_session(actor, session_id)
        if self._travel_phase(actor, session_id, resolved=resolved) != TRAVEL_PLANNING_PHASE:
            raise TravelApplicationError(
                "TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务尚未开始。", status_code=404
            )
        if self.prompt_loader is None:
            raise TravelApplicationError(
                "TRAVEL_PLAN_NOT_FINALIZED",
                "旅行规划未能完成，请稍后重试。",
                status_code=502,
            )
        review = (
            self.travel_service.get_candidate_review(actor, session_id)
            if self.travel_service is not None
            else None
        )
        if review is None:
            parent_messages = resolved.store.load(session_id).messages
            persisted_profiles = _persisted_candidate_research_profiles(
                parent_messages
            )
            ledger = getattr(self.travel_service, "source_ledger", None)
            mark_profiles = getattr(ledger, "mark_candidate_profiles_completed", None)
            if persisted_profiles and callable(mark_profiles):
                mark_profiles(session_id, persisted_profiles)
            snapshot = getattr(ledger, "snapshot", None)
            research = snapshot(session_id) if callable(snapshot) else None
            completed_profiles = frozenset(
                getattr(research, "candidate_completed_profiles", frozenset())
            ) | persisted_profiles
            missing_profiles = _TRAVEL_CANDIDATE_RESEARCH_PROFILES - completed_profiles
            if not missing_profiles or (
                research is not None
                and (
                    bool(getattr(research, "candidate_research_complete", False))
                    or not research.candidate_missing_attempts
                )
            ):
                return (
                    "三路旅行研究已经完成，不要重新查询外部来源，也不要再次调用 delegate_tasks。"
                    "当前只需复用本 Session 已有的交通天气、住宿景点和攻略结果，加载 travel-planner Skill，"
                    "按已确认天数与真实取舍构造一至三个都满足预算、每日总分钟和强度硬门槛的候选，"
                    "并立即运行 optimizer；短途取舍明显时最多三个，中等天数通常两个，"
                    "天数足以覆盖核心兴趣或差异不足时允许一个。"
                    "如果上一轮只有一个可行候选，只调整被拒候选对应的活动时长与安排一次；"
                    "不要用普通文字提前结束。"
                )
            if completed_profiles:
                task_by_profile = {
                    "travel-transport-weather": "travel-transport-weather 只补去返程交通或天气未完成部分",
                    "travel-stay-poi": "travel-stay-poi 只补住宿或景点未完成部分",
                    "travel-guides": "travel-guides 只补网页或小红书攻略未完成部分",
                }
                tasks = "；".join(task_by_profile[profile] for profile in sorted(missing_profiles))
                return (
                    "上一轮旅行研究已有成功子任务，禁止重跑这些已完成子任务，也禁止重新查询其外部来源。"
                    "本轮只调用一次 delegate_tasks，并且只创建以下失败或未完成的子任务："
                    f"{tasks}。等待这些补做结果后，与本 Session 已保存的成功子任务结果合并，"
                    "再加载 travel-planner Skill 并运行 optimizer；不得丢弃已有证据或从头规划，"
                    "也不得用普通文字提前结束。"
                )
            return (
                "候选行程尚未生成。本轮必须立即调用当前唯一的 delegate_tasks 入口，"
                "并在同一批次中并行创建恰好三个任务："
                "travel-transport-weather 查询去返程 12306 与天气；"
                "travel-stay-poi 查询指定日期住宿价格与目的地景点；"
                "travel-guides 查询 Tavily 与小红书攻略。"
                "等待三项结果 fan-in 后加载并运行 travel-planner optimizer；"
                "禁止用普通文字声称工具不可用，也不得跳过委派。"
            )
        if review.status == "selected":
            ledger = getattr(self.travel_service, "source_ledger", None)
            snapshot = getattr(ledger, "snapshot", None)
            research = snapshot(session_id) if callable(snapshot) else None
            parent_messages = resolved.store.load(session_id).messages
            repair_profiles = _travel_finalization_repair_profiles(
                parent_messages,
                research,
            )
            if TRAVEL_FINAL_WEATHER_PROFILE in repair_profiles:
                return (
                    "上一轮最终校验明确缺少当前日期范围的实时天气预报。"
                    "本轮只调用一次 delegate_tasks，并且只创建一个 "
                    "travel-final-weather 任务；按服务端日期窗口查询 get_forecast，"
                    "禁止查询历史天气、铁路、住宿、路线、网页或社区来源。"
                    "天气结果返回后复用已有完整计划草稿，更新 weather_summary 与 evidence，"
                    "并在同一轮立即再次调用 finalize_travel_plan；不得只返回文字说明。"
                )
            if TRAVEL_FINAL_ROUTE_PROFILE in repair_profiles:
                return (
                    "上一轮最终校验明确指出仍有不少于 2 公里的本地交通段缺少真实高德公交/地铁证据。"
                    "本轮只调用一次 delegate_tasks，并且只创建一个 travel-final-route 任务；"
                    "从上一轮 Finalizer 错误和已选候选骨架中列出所有缺口。公交首查为空时，"
                    "对远郊景点只改查同一景点的游客中心、主入口、售票处或景区接驳点，"
                    "再查一次公交；仍为空则查询一次高德驾车，作为出租车/网约车兜底，"
                    "保留真实距离和时长，禁止伪造公交线路或站点。公交有结果时逐条保留线路号、"
                    "上下车站、时长和距离；禁止查询天气、铁路、住宿、网页或社区来源。"
                    "路线结果返回后复用已有完整计划草稿，替换对应 planning estimate，"
                    "并在同一轮立即再次调用 finalize_travel_plan；不得只返回文字说明。"
                )
            if research is not None and not research.missing_attempts:
                return (
                    "所选方案的住宿与路线子任务已经完成，不要再次调用 delegate_tasks，"
                    "也不要重新查询外部来源。复用本 Session 历史中的 Child 结果和已有计划草稿，"
                    "修正上一轮 Finalizer 返回的具体字段或证据问题，然后立即再次调用 "
                    "finalize_travel_plan；禁止用普通文字提前结束。"
                )
        return self.prompt_loader.load("travel_planning_continuation")

    def travel_candidate_review(self, actor: ActorContext, session_id: str) -> dict[str, Any]:
        """Return an actor-owned pending or selected candidate review."""

        self._assert_travel_session_owner(actor, session_id)
        if self.travel_service is None:
            raise TravelApplicationError("TRAVEL_DISABLED", "旅行规划暂不可用。", status_code=503)
        review = self.travel_service.get_candidate_review(actor, session_id)
        if review is None:
            raise TravelApplicationError(
                "TRAVEL_CANDIDATE_REVIEW_NOT_FOUND", "候选行程不存在。", status_code=404
            )
        return review.to_dict()

    def select_travel_candidate(
        self, actor: ActorContext, session_id: str, candidate_id: str
    ) -> dict[str, Any]:
        """Record one actor-owned candidate decision for the continuation Turn."""

        self._assert_travel_session_owner(actor, session_id)
        if self.travel_service is None:
            raise TravelApplicationError("TRAVEL_DISABLED", "旅行规划暂不可用。", status_code=503)
        return self.travel_service.select_candidate(actor, session_id, candidate_id).to_dict()

    def travel_candidate_continuation_message(
        self, actor: ActorContext, session_id: str
    ) -> str:
        """Build a controlled message from a server-validated candidate selection."""

        review = self.travel_candidate_review(actor, session_id)
        selected = str(review.get("selected_candidate_id") or "")
        if review.get("status") != "selected" or not selected:
            raise TravelApplicationError(
                "TRAVEL_CANDIDATE_SELECTION_REQUIRED", "请先选择一个候选行程。", status_code=409
            )
        if self.travel_service is not None:
            resolved = self._resolve_travel_session(actor, session_id)
            snapshot = self.travel_service.source_ledger.snapshot(session_id)
            if _travel_finalization_repair_profiles(
                resolved.store.load(session_id).messages,
                snapshot,
            ):
                return self.travel_continuation_message(actor, session_id)
        return (
            "路线任务必须逐条覆盖所有不少于2公里的本地公交、地铁或未定交通段，包括首日车站到酒店、末日酒店到车站和远郊景点去返程；任务正文要附上候选研究中已有的地点坐标，只有缺坐标时才查询一次高德地点，并保留线路号、上下车站与途经站。远郊景点中心坐标公交为空时，改用同一景点的游客中心、主入口、售票处或景区接驳点再查一次；仍为空时用高德驾车距离和时长形成透明的出租车/网约车兜底，不得伪造公交线路。"
            f"用户已确认候选方案 {selected}。继续当前旅行规划，只能以该候选为最终 days、预算和路线的基础。"
            "本轮必须先且只调用一次 delegate_tasks，并在同一批次中并行创建恰好两个任务："
            "travel-final-stay 查询一处具体住宿身份和指定日期价格状态；"
            "travel-final-route 查询所选候选全部必要的高德公共交通路线，远郊景点同时覆盖去程和返程。"
            "等待两项结果 fan-in 后再一次构造 TravelPlanV1，禁止直接跳过委派试探 finalizer；"
            f"最后调用 finalize_travel_plan，并传 selected_candidate_id={selected}。"
        )

    def _assert_travel_session_owner(self, actor: ActorContext, session_id: str) -> None:
        if actor.user_id is None:
            raise TravelApplicationError("TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404)
        store = getattr(self.auth, "store", None)
        row = store.session_index_get(session_id) if store is not None else None
        if (
            row is None
            or str(row.get("owner_user_id") or "") != str(actor.user_id)
            or str(row.get("channel") or "") != "travel"
        ):
            raise TravelApplicationError("TRAVEL_GENERATION_NOT_FOUND", "旅行规划任务不存在。", status_code=404)

    def fork_session_to_web(
        self,
        actor: ActorContext,
        source_session_id: str,
        *,
        request_id: str = "",
    ) -> SessionSummary:
        """Create a private Web copy of one read-only external group session."""

        if self.session_access is None or actor.user_id is None:
            raise SessionAccessError(
                ErrorCode.AUTH_ACCOUNT_REQUIRED,
                "Database user is required",
                status_code=403,
            )
        return self.session_access.fork_to_web(actor, source_session_id)

    def run_chat_events(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        message: str | None = None,
        *,
        turn_id: str | None = None,
        on_event: RuntimeEventCallback | None = None,
        command_profile: str = WEB_COMMAND_PROFILE,
        request_id: str = "",
        channel_context: ChannelExecutionContext | None = None,
    ) -> ChatTurnResult:
        """Run one command-aware turn and emit text_delta events as they arrive."""

        actor, session_id, message = _normalize_actor_session_message(actor, session_id, message)
        with self._turns_lock:
            if not self._accepting_turns:
                raise RuntimeError("gateway is shutting down and is not accepting new turns")
        resolved_channel = channel_context.channel if channel_context is not None else (
            "external_ws" if command_profile == EXTERNAL_COMMAND_PROFILE else "web"
        )
        if self.session_access is not None and actor.user_id is not None:
            self.session_access.assert_chat_continuation_allowed(
                actor,
                session_id,
                request_channel=resolved_channel,
            )
        command_text = self.handle_command(
            actor,
            session_id,
            message,
            command_profile=command_profile,
            request_id=request_id,
            channel_context=channel_context,
        )
        if command_text is not None:
            _emit_runtime_event(on_event, {"type": "text_delta", "content": command_text})
            command_name = message.strip().split(maxsplit=1)[0].lower()
            stopped = command_profile in EXTERNAL_STOP_PROFILES and command_name == "/stop"
            return ChatTurnResult(content=command_text, stopped=stopped, turn_id=turn_id or "")

        subagent_preference = self.get_subagent_preference(actor, session_id)
        force_subagent_once = self.consume_subagent_force_once(actor, session_id)
        if force_subagent_once and not (
            self.subagent_status is not None and self.subagent_status.available
        ):
            text = _format_subagent_unavailable(self.subagent_status, actor)
            _emit_runtime_event(on_event, {"type": "text_delta", "content": text})
            return ChatTurnResult(content=text, stopped=False, turn_id=turn_id or "")
        turn_id = turn_id or new_turn_id()
        token = CancellationToken()
        active_key = _active_turn_key(actor, session_id)
        self._cancel_memory_extraction(actor, session_id)
        sessions = self.sessions
        workspace = self.config.workspace
        tools = getattr(self.agent_loop, "tools", None)
        visible_skills = self.skill_loader
        turn_llm = self.llm
        travel_child_parent_llm = turn_llm
        turn_context_budget = (
            self.llm_resolver.context_budget() if self.llm_resolver is not None else None
        )
        memory_notice: tuple[str, ...] = ()
        memory_store = None
        memory_safety = None
        travel_phase = ""
        travel_intake = False
        travel_research_delegation_active = False
        travel_candidate_review_state = None
        travel_finalization_active = False
        travel_candidate_research_complete = False
        travel_finalization_repair_profiles: frozenset[str] = frozenset()
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.ensure_session(
                actor,
                session_id,
                channel=resolved_channel,
                conversation_type=(
                    channel_context.conversation_type if channel_context is not None else ""
                ),
                external_chat_id=(
                    channel_context.external_conversation_id if channel_context is not None else ""
                ),
                external_thread_id=(
                    channel_context.external_thread_id if channel_context is not None else ""
                ),
                write=True,
            )
            resolved_channel = resolved.channel or resolved_channel
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=actor.user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    request_id=request_id,
                )
            sessions = resolved.store
            if resolved_channel == "travel":
                travel_phase = self._travel_phase(actor, session_id, resolved=resolved)
                travel_intake = travel_phase == TRAVEL_INTAKE_PHASE
                if (
                    travel_phase == TRAVEL_PLANNING_PHASE
                    and self.travel_service is not None
                ):
                    travel_candidate_review_state = (
                        self.travel_service.get_candidate_review(actor, session_id)
                    )
                    travel_finalization_active = (
                        travel_candidate_review_state is not None
                        and travel_candidate_review_state.status == "selected"
                    )
                    parent_messages = resolved.store.load(session_id).messages
                    persisted_candidate_profiles = (
                        _persisted_candidate_research_profiles(parent_messages)
                        if travel_candidate_review_state is None
                        else frozenset()
                    )
                    if persisted_candidate_profiles:
                        self.travel_service.source_ledger.mark_candidate_profiles_completed(
                            session_id,
                            persisted_candidate_profiles,
                        )
                    travel_candidate_research_complete = (
                        travel_candidate_review_state is None
                        and self.travel_service.source_ledger.snapshot(
                            session_id
                        ).candidate_research_complete
                    )
                    if travel_candidate_research_complete:
                        candidate_messages, _ = _persisted_travel_child_messages(
                            getattr(resolved.store, "sessions_dir", None),
                            session_id,
                            parent_messages,
                        )
                        _rehydrate_travel_search_evidence(
                            self.travel_service.source_ledger,
                            session_id,
                            candidate_messages,
                            finalization=False,
                        )
                    if travel_candidate_review_state is None:
                        try:
                            candidate_llm = create_optional_aliased_llm_provider(
                                self.config.config_dir,
                                "compaction",
                            )
                        except LLMConfigurationError as exc:
                            candidate_llm = None
                            log_event(
                                web_logger,
                                logging.WARNING,
                                "travel.candidate_model_degraded",
                                error_type=type(exc).__name__,
                            )
                        if candidate_llm is not None:
                            turn_llm = candidate_llm
                            candidate_endpoint_name = "custom"
                            candidate_model_name = "configured"
                            try:
                                candidate_endpoint = _current_endpoint(candidate_llm)
                                candidate_endpoint_name = candidate_endpoint.name
                                candidate_model_name = candidate_endpoint.model
                                turn_context_budget = ConfiguredLLMProviderResolver(
                                    list(candidate_llm.endpoints()),
                                    default_endpoint=candidate_endpoint.name,
                                ).context_budget()
                            except (AttributeError, LLMConfigurationError, ValueError):
                                # The turn keeps the already safe main-model budget if a
                                # custom provider cannot expose endpoint metadata.
                                pass
                            log_event(
                                web_logger,
                                logging.INFO,
                                "travel.candidate_model_selected",
                                endpoint=candidate_endpoint_name,
                                model=candidate_model_name,
                            )
                    if travel_finalization_active:
                        # Candidate selection normally opens this phase in the API service.
                        # Re-open it idempotently here as well so an already-selected plan
                        # resumes correctly after a process restart and still has to run the
                        # fresh stay + route detail batch before final saving.
                        ledger = self.travel_service.source_ledger
                        parent_messages = resolved.store.load(session_id).messages
                        candidate_messages, finalization_messages = (
                            _persisted_travel_child_messages(
                                getattr(resolved.store, "sessions_dir", None),
                                session_id,
                                parent_messages,
                            )
                        )
                        snapshot = ledger.snapshot(session_id)
                        if not snapshot.attempted:
                            _rehydrate_travel_search_evidence(
                                ledger,
                                session_id,
                                candidate_messages,
                                finalization=False,
                            )
                        ledger.begin_finalization_budget(session_id)
                        finalization_attempted = ledger.snapshot(
                            session_id
                        ).finalization_attempted
                        remaining_categories = frozenset(
                            {"lodging", "maps"} - set(finalization_attempted)
                        )
                        if finalization_messages and remaining_categories:
                            _rehydrate_travel_search_evidence(
                                ledger,
                                session_id,
                                finalization_messages,
                                finalization=True,
                                only_categories=remaining_categories,
                            )
                        if (
                            finalization_messages
                            and not ledger.snapshot(session_id).forecast_successful
                        ):
                            _rehydrate_travel_search_evidence(
                                ledger,
                                session_id,
                                finalization_messages,
                                finalization=True,
                                only_categories=frozenset({"weather"}),
                            )
                        ledger.mark_finalization_attempted(
                            session_id,
                            set(
                                _persisted_finalization_completed_categories(
                                    parent_messages
                                )
                            ),
                        )
                        restored_snapshot = ledger.snapshot(session_id)
                        ledger.restore_plan_attempts(
                            session_id,
                            _persisted_travel_finalizer_attempts(
                                parent_messages,
                                weather_source_verified=restored_snapshot.forecast_successful,
                                transit_source_verified=restored_snapshot.verified_transit_available,
                            ),
                        )
                        travel_finalization_repair_profiles = (
                            _travel_finalization_repair_profiles(
                                parent_messages,
                                ledger.snapshot(session_id),
                            )
                        )
                        if TRAVEL_FINAL_WEATHER_PROFILE in travel_finalization_repair_profiles:
                            ledger.begin_forecast_repair(session_id)
                        if TRAVEL_FINAL_ROUTE_PROFILE in travel_finalization_repair_profiles:
                            ledger.begin_route_repair(session_id)
            owner_has_workspace_scope = "owner" in actor.role_keys
            workspace = self.config.workspace if owner_has_workspace_scope else resolved.context.files_dir
            visible_skills = (
                self.skill_loader.for_actor(actor)
                if self.skill_loader is not None
                else None
            )
            memory_context = build_memory_context(
                resolved.context.memory_dir,
                scope="workspace" if owner_has_workspace_scope else "user",
                actor_user_id=None if owner_has_workspace_scope else actor.user_id,
            )
            memory_store = MarkdownMemoryStore(memory_context)
            memory_safety = MemorySafetyPolicy()
            memory_notice = pop_memory_notification(memory_context)
            if self.model_preferences is not None and self.llm_resolver is not None:
                preference = self.model_preferences.get(resolved.model_context(), session_id)
                selection = self.llm_resolver.resolve(preference)
                turn_llm = self.llm_resolver.bind(selection)
                travel_child_parent_llm = turn_llm
                turn_context_budget = selection.context_budget
            if travel_intake:
                intake_tools = (
                    self.travel_service.intake_tools_for_actor(
                        actor,
                        sessions,
                        confirm_planning=self.confirm_travel_planning,
                    )
                    if self.travel_service is not None
                    else []
                )
                tools = ToolRegistry(intake_tools)
                memory_notice = ()
            else:
                travel_domain_tools = (
                    self.travel_service.tools_for_actor(actor)
                    if self.travel_service is not None
                    and travel_phase == TRAVEL_PLANNING_PHASE
                    else []
                )
                mcp_tools = (
                    self.mcp_runtime.tools_for_actor(
                        actor,
                        workspace,
                        session_id=session_id,
                        interaction_notifier=lambda request: _emit_runtime_event(
                            on_event,
                            {"type": "mcp_elicitation_requested", **asdict(request)},
                        ),
                        # Travel source results are observed by guard_travel_tools after
                        # city filtering and payload compaction, with the effective args.
                        result_observer=None,
                    )
                    if self.mcp_runtime is not None
                    else []
                )
                if self.travel_service is not None and travel_phase == TRAVEL_PLANNING_PHASE:
                    expected_source_tools = [
                        tool.name for tool in [*travel_domain_tools, *mcp_tools]
                    ]
                    if travel_finalization_active:
                        expected_source_tools = [
                            name
                            for name in expected_source_tools
                            if source_category(name) in {"maps", "lodging", "weather"}
                        ]
                    self.travel_service.source_ledger.register_expected(
                        session_id,
                        expected_source_tools,
                    )
                    if (
                        travel_finalization_active
                        and (
                            self.travel_service.source_ledger.snapshot(
                                session_id
                            ).missing_attempts
                            or travel_finalization_repair_profiles
                        )
                    ):
                        force_subagent_once = True
                    if (
                        not travel_finalization_active
                        and not travel_candidate_research_complete
                        and self.travel_service.source_ledger.snapshot(
                            session_id
                        ).candidate_missing_attempts
                    ):
                        # Candidate research is a fixed three-lane product stage, not an
                        # optional model optimization. The force-once contract prevents a
                        # text-only refusal when delegate_tasks is the sole safe entrypoint.
                        force_subagent_once = True
                    mcp_tools = guard_travel_tools(
                        mcp_tools,
                        self.travel_service.source_ledger,
                        session_id,
                    )
                tools = UserScopedToolProvider(
                    files_dir=workspace,
                    shared_readonly_dir=resolved.context.shared_readonly_dir,
                    actor=actor,
                    skills=visible_skills,
                    skill_sync=self.skill_sync,
                    diagnostics=self.diagnostics,
                    system_diagnostics=self.system_diagnostics,
                    diagnostic_context=DiagnosticContext(
                        session_id=session_id,
                        current_turn_id=turn_id,
                        current_request_id=request_id,
                        channel=actor.channel,
                    ),
                    memory_store=memory_store,
                    memory_safety=memory_safety,
                    extra_tools=[
                        *travel_domain_tools,
                        *mcp_tools,
                    ],
                )
                if travel_phase == TRAVEL_PLANNING_PHASE:
                    tools = FilteredToolProvider(
                        tools,
                        allowed_tools=[
                            "load_skills",
                            "run_skill",
                            *[tool.name for tool in travel_domain_tools],
                            *[tool.name for tool in mcp_tools],
                        ],
                    )
        turn_subagent_config = self.subagent_config
        if (
            resolved_channel == "travel"
            and travel_phase == TRAVEL_PLANNING_PHASE
            and turn_subagent_config is not None
        ):
            turn_subagent_config = travel_subagent_config_for_stage(
                turn_subagent_config,
                finalization=travel_finalization_active,
                repair_profiles=travel_finalization_repair_profiles,
            )
        if (
            tools is not None
            and not travel_intake
            and turn_subagent_config is not None
            and turn_subagent_config.enabled
            and (subagent_preference.mode == "auto" or force_subagent_once)
            and self.prompt_loader is not None
        ):
            sessions_root = getattr(sessions, "sessions_dir", self.config.sessions_dir)

            def child_tools(
                child_workspace,
                profile: SubagentProfile,
                parent_context,
                child_on_event,
                child_identity,
                child_skills,
            ):
                del parent_context
                child_mcp_tools = (
                    self.mcp_runtime.tools_for_actor(
                        actor,
                        child_workspace,
                        session_id=session_id,
                        interaction_notifier=lambda request: _emit_runtime_event(
                            child_on_event,
                            {
                                "type": "mcp_elicitation_requested",
                                "subagent_id": child_identity.subagent_id,
                                "task_id": child_identity.task_id,
                                "batch_id": child_identity.batch_id,
                                **asdict(request),
                            },
                        ),
                        # The travel guard records the compact, city-scoped result once.
                        result_observer=None,
                    )
                    if self.mcp_runtime is not None
                    else []
                )
                if (
                    self.travel_service is not None
                    and resolved_channel == "travel"
                ):
                    child_mcp_tools = guard_travel_tools(
                        child_mcp_tools,
                        self.travel_service.source_ledger,
                        session_id,
                    )
                child_research_tools = (
                    self.travel_service.research_tools_for_actor(actor)
                    if self.travel_service is not None
                    and resolved_channel == "travel"
                    else []
                )
                child_provider: ToolProvider = UserScopedToolProvider(
                    files_dir=child_workspace,
                    shared_readonly_dir=(
                        resolved.context.shared_readonly_dir
                        if self.session_access is not None and actor.user_id is not None
                        else self.config.shared_readonly_dir
                    ),
                    actor=actor,
                    skills=child_skills,
                    skill_sync=self.skill_sync,
                    diagnostics=self.diagnostics,
                    system_diagnostics=self.system_diagnostics,
                    diagnostic_context=DiagnosticContext(
                        session_id=session_id,
                        current_turn_id=turn_id,
                        current_request_id=request_id,
                        channel=actor.channel,
                    ),
                    memory_store=memory_store,
                    memory_safety=memory_safety,
                    extra_tools=[*child_mcp_tools, *child_research_tools],
                )
                if profile.name == TRAVEL_FINAL_ROUTE_PROFILE:
                    child_provider = compact_travel_final_route_results(child_provider)
                return child_provider

            tools = build_turn_subagent_provider(
                base_tools=tools,
                config=turn_subagent_config,
                prompt_loader=self.prompt_loader,
                sessions_root=sessions_root,
                workspace=workspace,
                parent_llm=turn_llm,
                context_budget=turn_context_budget,
                tool_provider_factory=child_tools,
                skills=visible_skills,
                cancellation_token=token,
                on_event=on_event,
                force_once=force_subagent_once,
                tool_policy=self.tool_policy,
                confirmation_broker=self.confirmation_broker,
                activity_sink=self.activity_sink,
                audit_sink=self.audit_sink,
                hook_runtime=self.agent_loop.hook_runtime,
                llm_factory=(
                    _travel_child_llm_factory(travel_child_parent_llm)
                    if resolved_channel == "travel"
                    and travel_phase == TRAVEL_PLANNING_PHASE
                    else None
                ),
            )
            if (
                resolved_channel == "travel"
                and travel_phase == TRAVEL_PLANNING_PHASE
                and self.travel_service is not None
            ):
                final_stay_context = ""
                final_weather_context = ""
                if travel_finalization_active:
                    structured = self.travel_service.source_ledger.structured_results(
                        session_id
                    )
                    observations = structured.get("hotel_observations") or []
                    if observations:
                        final_stay_context = json.dumps(
                            {"candidate_hotel_observations": observations[-2:]},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )[:4_000]
                    if TRAVEL_FINAL_WEATHER_PROFILE in travel_finalization_repair_profiles:
                        final_weather_context = _travel_forecast_window_context(
                            sessions.load(session_id).metadata.get("travel_draft")
                        )
                tools = require_exact_travel_delegation(
                    tools,
                    finalization=travel_finalization_active,
                    final_stay_context=final_stay_context,
                    final_weather_context=final_weather_context,
                    expected_profiles=(
                        (travel_finalization_repair_profiles or None)
                        if travel_finalization_active
                        else (
                            _TRAVEL_CANDIDATE_RESEARCH_PROFILES
                            - self.travel_service.source_ledger.snapshot(
                                session_id
                            ).candidate_completed_profiles
                        )
                        or None
                    ),
                    on_profiles_completed=(
                        (
                            lambda profiles: self.travel_service.source_ledger.mark_finalization_attempted(
                                session_id,
                                _travel_finalization_categories_for_profiles(profiles),
                            )
                        )
                        if travel_finalization_active
                        else (
                            lambda profiles: self.travel_service.source_ledger.mark_candidate_profiles_completed(
                                session_id,
                                profiles,
                            )
                        )
                    ),
                )
                # The travel parent orchestrates bounded child research. ChildAgentFactory
                # already captured the full source surface above; hiding sources only from
                # the returned parent provider prevents slow serial re-query after fan-in.
                stage_tools = (
                    ["finalize_travel_plan"]
                    if travel_finalization_active
                    else [
                        "load_skills",
                        "run_skill",
                        "request_travel_clarification",
                        "request_travel_candidate_review",
                    ]
                )
                tools = FilteredToolProvider(
                    tools,
                    allowed_tools=[
                        *stage_tools,
                        "delegate_tasks",
                    ],
                    kernel_denied_tools=(),
                )
                if travel_finalization_active:
                    tools = require_travel_finalization_before_saving(
                        tools,
                        self.travel_service.source_ledger,
                        session_id,
                        repair_categories=(
                            frozenset(
                                {
                                    category
                                    for profile, category in (
                                        (TRAVEL_FINAL_WEATHER_PROFILE, "weather"),
                                        (TRAVEL_FINAL_ROUTE_PROFILE, "maps"),
                                    )
                                    if profile in travel_finalization_repair_profiles
                                }
                            )
                        ),
                    )
                else:
                    tools = require_travel_research_before_solving(
                        tools,
                        self.travel_service.source_ledger,
                        session_id,
                    )
                travel_research_delegation_active = True
        elif (
            tools is not None
            and not travel_intake
            and self.subagent_status is not None
            and self.subagent_status.state == "unavailable"
            and subagent_preference.mode == "auto"
        ):
            tools = build_unavailable_subagent_provider(tools, self.subagent_status)
        initial_tool_names: tuple[str, ...] = ()
        if travel_phase == TRAVEL_PLANNING_PHASE and tools is not None:
            available_names = [
                str(item.get("function", {}).get("name") or "")
                for item in tools.definitions()
                if isinstance(item, dict)
            ]
            initial_tool_names = (
                "load_skills",
                "run_skill",
                "delegate_tasks",
                "request_travel_candidate_review",
                "finalize_travel_plan",
                *preferred_travel_tool_names(available_names),
            )
        if not travel_intake and not travel_research_delegation_active:
            tools = with_tool_discovery(tools, initial_names=initial_tool_names)
        turn_requirements: list[str] = []
        if resolved_channel == "travel" and self.prompt_loader is not None:
            prompt_name = (
                "travel_intake"
                if travel_phase == TRAVEL_INTAKE_PHASE
                else "travel_planning"
            )
            turn_requirements.append(self.prompt_loader.load(prompt_name))
            if travel_phase == TRAVEL_INTAKE_PHASE:
                reference_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
                turn_requirements.append(
                    "# 日期理解基准\n"
                    f"当前北京时间日期为 {reference_date}。理解“国庆、下周、月底”等相对日期时"
                    "以此为基准；不确定年份或结束日期时应自然追问，不得臆造。"
                )
                intake_state = sessions.load(session_id)
                current_draft = recover_intake_draft(
                    intake_state.metadata,
                    intake_state.messages,
                )
                if intake_state.metadata.get(
                    "travel_draft_version"
                ) != TRAVEL_INTAKE_DRAFT_VERSION:
                    sessions.update_metadata(
                        session_id,
                        {
                            "travel_draft": current_draft,
                            "travel_draft_version": TRAVEL_INTAKE_DRAFT_VERSION,
                        },
                    )
                if current_draft:
                    turn_requirements.append(
                        "# 服务端当前旅行草稿\n"
                        "以下 JSON 是服务端已保存的累计条件，优先于历史对话中的旧值；"
                        "本轮只向 update_travel_draft 传用户明确新增或修正的非空字段。"
                        "空字符串、空数组和 null 只是占位，不得用来清除旧值；"
                        "用户明确要求删除条件时才使用 clear_fields。不得把 JSON 原样展示给用户。\n"
                        + json.dumps(current_draft, ensure_ascii=False, separators=(",", ":"))
                    )
            if travel_phase == TRAVEL_PLANNING_PHASE:
                reference_timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                turn_requirements.append(
                    "# 本轮服务端时间基准\n"
                    f"当前 RFC 3339 UTC 时间为 {reference_timestamp}。可直接把它用于 "
                    "generated_at，以及未返回独立查询时间的 ToolResult 的 retrieved_at；"
                    "禁止为取得当前时间调用 discover_tools、诊断 Tool 或外部来源。"
                )
                confirmed_draft = sessions.load(session_id).metadata.get("travel_draft")
                if isinstance(confirmed_draft, dict):
                    turn_requirements.append(
                        "# 服务端已确认的旅行草稿\n"
                        "以下 JSON 是服务端校验后的用户条件，优先于历史中的旧值；"
                        "不得把它原样展示给用户。\n"
                        + json.dumps(confirmed_draft, ensure_ascii=False, separators=(",", ":"))
                    )
                    turn_requirements.append(
                        "# 服务端天气日期窗口\n"
                        "以下 JSON 已由服务端按北京时间计算。inside_forecast_window=true 时，"
                        "天气任务必须调用 get_forecast，禁止由模型自行改判为历史天气；false 时"
                        "才允许把历史同期天气作为气候参考。不得把 JSON 原样展示给用户。\n"
                        + _travel_forecast_window_context(confirmed_draft)
                    )
                if (
                    travel_candidate_review_state is not None
                    and travel_candidate_review_state.status == "selected"
                ):
                    selected_candidate = next(
                        (
                            candidate
                            for candidate in travel_candidate_review_state.candidates
                            if str(candidate.get("candidate_id") or "")
                            == travel_candidate_review_state.selected_candidate_id
                        ),
                        None,
                    )
                    if isinstance(selected_candidate, dict):
                        turn_requirements.append(
                            "# 服务端已确认的候选方案\n"
                            "以下 JSON 是用户已经选择的候选骨架；只补充具体住宿、路线与证据，"
                            "不得改换候选或重新运行 optimizer，也不得把 JSON 原样展示给用户。\n"
                            + json.dumps(
                                selected_candidate,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        )
        if force_subagent_once and self.prompt_loader is not None and not travel_intake:
            turn_requirements.append(self.prompt_loader.load("subagent_once"))
        self._register_turn(
            active_key,
            ActiveTurn(
                turn_id=turn_id,
                token=token,
                subagent_force_once=force_subagent_once,
            ),
        )
        try:
            notice_text = _format_memory_notice(memory_notice)
            if notice_text:
                _emit_runtime_event(on_event, {"type": "text_delta", "content": notice_text + "\n\n"})
            content = _run_agent_turn(
                self.agent_loop,
                session_id,
                message,
                turn_id=turn_id,
                on_event=on_event,
                cancellation_token=token,
                actor=actor,
                llm_override=turn_llm,
                context_budget=turn_context_budget,
                sessions_override=sessions,
                tools_override=tools,
                workspace_override=workspace,
                skills_override=visible_skills,
                tool_policy=self.tool_policy,
                confirmation_broker=self.confirmation_broker,
                activity_sink=self.activity_sink,
                audit_sink=self.audit_sink,
                channel=resolved_channel,
                conversation_type=(
                    channel_context.conversation_type if channel_context is not None else ""
                ),
                request_id=request_id,
                system_prompt_addendum="\n\n".join(turn_requirements),
            )
            if travel_intake:
                state = sessions.load(session_id)
                turn_ids = state.metadata.get("travel_intake_turn_ids")
                normalized_turn_ids = (
                    [str(item) for item in turn_ids if str(item).strip()]
                    if isinstance(turn_ids, list)
                    else []
                )
                if turn_id not in normalized_turn_ids:
                    normalized_turn_ids.append(turn_id)
                    sessions.update_metadata(
                        session_id,
                        {"travel_intake_turn_ids": normalized_turn_ids[-40:]},
                    )
            if self.session_access is not None and actor.user_id is not None:
                self.session_access.refresh_index(actor, session_id)
            stopped = token.is_cancelled()
            if stopped:
                log_event(web_logger, logging.INFO, "chat.stopped", session_id=session_id, turn_id=turn_id)
            elif not travel_intake:
                self._schedule_memory_extraction(actor, session_id)
            visible_content = f"{notice_text}\n\n{content}" if notice_text else content
            return ChatTurnResult(content=visible_content, stopped=stopped, turn_id=turn_id)
        except Exception as exc:
            log_event(
                web_logger,
                logging.ERROR,
                "chat.error",
                session_id=session_id,
                turn_id=turn_id,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            self._unregister_turn(active_key, turn_id)

    def handle_command(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        message: str | None = None,
        *,
        command_profile: str = WEB_COMMAND_PROFILE,
        request_id: str = "",
        channel_context: ChannelExecutionContext | None = None,
    ) -> str | None:
        """Return a Web command response, or None for ordinary chat text."""

        actor, session_id, message = _normalize_actor_session_message(actor, session_id, message)
        stripped = message.strip()
        if not stripped.startswith("/"):
            return None

        command, _, target = stripped[1:].partition(" ")
        command = command.lower()
        target = target.strip()
        if command == "help":
            return _web_help_text(command_profile)
        if command_profile in {QQ_C2C_COMMAND_PROFILE, QQ_GROUP_COMMAND_PROFILE} and command == (
            "sessions"
        ):
            return "Session management is available in Web or CLI. Use `/new` here for a fresh QQ session."
        if command_profile == QQ_GROUP_COMMAND_PROFILE and command in {
            "model",
            "sessions",
            "history",
            "memory",
        }:
            return "This command is private. Please use QQ direct chat or Web."
        if command == "model":
            return self._handle_model_command(actor, session_id, target, request_id=request_id)
        if command == "subagent":
            return self._handle_subagent_command(actor, session_id, target)
        if command == "memory":
            return self._handle_memory_command(actor, session_id, target)
        if command == "mcp":
            if target:
                return "Usage: `/mcp`"
            return (
                self.mcp_runtime.format_capabilities()
                if self.mcp_runtime is not None
                else "当前没有可用的 MCP Server。"
            )
        if command == "stop":
            if command_profile not in EXTERNAL_STOP_PROFILES:
                return _command_not_supported_for_client(stripped)
            result = self.cancel_session(actor, session_id)
            return f"Stopped current turn. Cancelled: `{result['cancelled']}`"
        if command == "clear":
            self._cancel_memory_extraction(actor, session_id)
            self._clear_subagent_force_once(actor, session_id)
            self._invalidate_session_context(actor, session_id)
            if self.session_access is not None and actor.user_id is not None:
                self.session_access.clear_session(actor, session_id)
            else:
                self.sessions.clear(session_id)
            return f"Session cleared: `{session_id}`"
        if command == "new":
            return "Use the New chat button to start a fresh Web session."
        if command == "sessions":
            return self._handle_sessions_command(actor, session_id, target)
        if command == "history":
            if command_profile != EXTERNAL_COMMAND_PROFILE:
                return _command_not_supported_for_client(stripped)
            return _format_session_history(self.load_session(actor, session_id).messages)
        if command == "exit":
            if (
                command_profile != EXTERNAL_COMMAND_PROFILE
                or channel_context is not None
                and not channel_context.capabilities.can_close_conversation
            ):
                return _command_not_supported_for_client(stripped)
            return "Closing WebSocket connection."

        return _unsupported_command(stripped)

    def _handle_memory_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
    ) -> str:
        """Display the current actor's scoped durable Memory."""

        if target:
            return "Usage: `/memory`"
        target_session_id = session_id
        memory_dir = self.config.local_memory_dir
        scope = "workspace"
        actor_user_id = None
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.resolve_session(actor, target_session_id)
            if resolved.owner_user_id != actor.user_id:
                return "Memory is limited to your own sessions."
            is_owner = "owner" in actor.role_keys
            memory_dir = resolved.context.memory_dir
            scope = "workspace" if is_owner else "user"
            actor_user_id = None if is_owner else actor.user_id
        memory_store = MarkdownMemoryStore(
            build_memory_context(
                memory_dir,
                scope=scope,
                actor_user_id=actor_user_id,
            )
        )
        return format_memory_list(memory_store)

    def _handle_sessions_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
    ) -> str:
        """Handle shared /sessions subcommands for Web and WS channels."""

        if not target:
            return _format_session_list(self.list_sessions(actor), session_id)

        subcommand, _, rest = target.partition(" ")
        subcommand = subcommand.strip().lower()
        rest = rest.strip()
        if subcommand == "rename":
            target_session_id, _, title = rest.partition(" ")
            target_session_id = target_session_id.strip()
            title = title.strip()
            if not target_session_id or not title:
                return _sessions_usage_text()
            summary = self.rename_session(actor, target_session_id, title)
            return f"Session renamed: `{summary.session_id}`"
        if subcommand == "delete":
            target_session_id = rest or session_id
            if target_session_id == session_id:
                self._cancel_memory_extraction(actor, session_id)
                self._clear_subagent_force_once(actor, session_id)
                self._invalidate_session_context(actor, session_id)
                if self.session_access is not None and actor.user_id is not None:
                    self.session_access.clear_session(actor, session_id)
                else:
                    self.sessions.clear(session_id)
                return f"Session cleared: `{session_id}`"
            self.delete_session(actor, target_session_id)
            return f"Session deleted: `{target_session_id}`"
        return _sessions_usage_text()

    def get_subagent_preference(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
    ) -> SessionSubagentPreference:
        """Return the current session's Subagent mode and one-shot state."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        if self.subagent_preferences is None:
            return SessionSubagentPreference()
        return self.subagent_preferences.get(
            self._session_metadata_context(actor, session_id),
            session_id,
        )

    def consume_subagent_force_once(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
    ) -> bool:
        """Atomically consume one explicit next-message Subagent request."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        if self.subagent_preferences is None:
            return False
        return self.subagent_preferences.consume_force_once(
            self._session_metadata_context(actor, session_id),
            session_id,
        )

    def _handle_subagent_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
    ) -> str:
        """Handle session-scoped Web /subagent commands."""

        normalized = target.strip().lower()
        if normalized not in {"", "auto", "off", "once"}:
            return "Usage: `/subagent`"
        if self.subagent_status is not None and not self.subagent_status.available:
            return _format_subagent_unavailable(self.subagent_status, actor)
        if self.subagent_preferences is None:
            return "Subagent preferences are unavailable for this runtime."
        context = self._session_metadata_context(actor, session_id)
        if normalized in {"auto", "off"}:
            preference = self.subagent_preferences.set_mode(context, session_id, normalized)
        elif normalized == "once":
            preference = self.subagent_preferences.force_once(context, session_id)
        else:
            preference = self.subagent_preferences.get(context, session_id)
        return _format_subagent_status(preference, self.subagent_profiles)

    def _clear_subagent_force_once(self, actor: ActorContext, session_id: str) -> None:
        if self.subagent_preferences is None:
            return
        self.subagent_preferences.clear_force_once(
            self._session_metadata_context(actor, session_id),
            session_id,
        )

    def _session_metadata_context(
        self,
        actor: ActorContext,
        session_id: str,
    ) -> SessionContext:
        if self.session_access is not None and actor.user_id is not None:
            return self.session_access.ensure_session(
                actor,
                session_id,
                channel=actor.channel,
                write=True,
            ).model_context()
        metadata_dir = getattr(
            self.sessions,
            "metadata_dir",
            self.config.sessions_dir.parent / "sessions_meta",
        )
        return SessionContext(
            owner_user_id=None,
            sessions_dir=self.config.sessions_dir,
            sessions_meta_dir=metadata_dir,
            files_dir=self.config.workspace,
            shared_readonly_dir=self.config.shared_readonly_dir,
        )

    def rename_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        title: str | None = None,
        *,
        request_id: str = "",
    ) -> SessionSummary:
        """Rename a session title and return the updated summary."""

        actor, session_id, title = _normalize_actor_session_message(actor, session_id, title)
        if self.session_access is not None and actor.user_id is not None:
            summary = self.session_access.rename_session(actor, session_id, title)
        else:
            self.sessions.rename(session_id, title)
            summary = _find_session_summary(self.sessions.list_sessions(), session_id)
        log_event(session_logger, logging.INFO, "session.renamed", session_id=session_id)
        return summary

    def delete_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        *,
        request_id: str = "",
    ) -> None:
        """Cancel then delete one Web session."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        if self.session_access is not None and actor.user_id is not None:
            self.session_access.resolve_session(actor, session_id, delete=True)
        self._cancel_memory_extraction(actor, session_id)
        active = self._active_turn_for_session(actor, session_id)
        self.cancel_session(actor, session_id)
        if active is not None and not active.completed.wait(timeout=30.0):
            raise RuntimeError("active turn did not stop before session deletion")
        self._invalidate_session_context(actor, session_id)
        if self.session_access is not None and actor.user_id is not None:
            self.session_access.delete_session(actor, session_id)
        else:
            self.sessions.delete(session_id)
        log_event(session_logger, logging.INFO, "session.deleted", session_id=session_id)
        self._audit(
            actor,
            "session.deleted",
            "session",
            session_id=session_id,
            request_id=request_id,
        )

    def _invalidate_session_context(self, actor: ActorContext, session_id: str) -> None:
        """Delete rebuildable state within the actor-authorized Session store."""

        sessions = self.sessions
        if self.session_access is not None and actor.user_id is not None:
            try:
                sessions = self.session_access.resolve_session(actor, session_id).store
            except SessionAccessError:
                return
        builder = getattr(self.agent_loop, "context_builder", None)
        invalidate = getattr(builder, "delete_derived_session", None)
        if callable(invalidate):
            invalidate(sessions, session_id)

    def cancel_session(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Cancel the active turn for a session when one exists."""

        actor, session_id = _normalize_actor_session(actor, session_id)
        key = _active_turn_key(actor, session_id)
        with self._turns_lock:
            active = self._active_turns.get(key)
            if active is None and actor.has_permission("chat.stop.any"):
                match = next(
                    (
                        (candidate_key, candidate)
                        for candidate_key, candidate in self._active_turns.items()
                        if candidate_key[1] == session_id
                    ),
                    None,
                )
                if match is not None:
                    key, active = match
        if active is None:
            return {"session_id": session_id, "cancelled": 0}
        active.token.cancel()
        if self.mcp_runtime is not None:
            self.mcp_runtime.cancel_active_calls(
                user_id=actor.user_id,
                session_id=session_id,
            )
        log_event(
            web_logger,
            logging.INFO,
            "chat.cancel_requested",
            session_id=session_id,
            turn_id=active.turn_id,
        )
        return {"session_id": session_id, "turn_id": active.turn_id, "cancelled": 1}

    def _active_turn_for_session(
        self, actor: ActorContext, session_id: str
    ) -> ActiveTurn | None:
        key = _active_turn_key(actor, session_id)
        with self._turns_lock:
            active = self._active_turns.get(key)
            if active is not None or not actor.has_permission("chat.stop.any"):
                return active
            return next(
                (
                    candidate
                    for candidate_key, candidate in self._active_turns.items()
                    if candidate_key[1] == session_id
                ),
                None,
            )

    def current_model_label(self) -> str:
        """Return a compact endpoint/model label when the provider exposes it."""

        try:
            if self.llm_resolver is not None:
                selection = self.llm_resolver.resolve(None)
                return f"{selection.endpoint_name}/{selection.model_name}"
            model_state = self.model_state(local_operator_actor(channel="web"), "")
        except ValueError:
            return "auto"
        return f"{model_state.endpoint}/{model_state.current_model}"

    def model_state(
        self,
        actor: ActorContext | None = None,
        session_id: str = "",
        *,
        request_id: str = "",
    ) -> ModelState:
        """Return the current endpoint and selectable models for the Web UI."""

        actor = actor or local_operator_actor(channel="web")
        if self.session_access is not None and actor.user_id is not None:
            if not session_id:
                selection = self.llm_resolver.resolve(None) if self.llm_resolver else None
                if selection is None:
                    raise ValueError("LLM resolver is not configured")
                endpoint = _find_endpoint(self.llm_resolver.endpoints(), selection.endpoint_name)
                return ModelState(
                    endpoint=selection.endpoint_name,
                    current_model=selection.model_name,
                    models=_endpoint_model_names(endpoint),
                )
            resolved = self.session_access.ensure_session(actor, session_id, channel="web")
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=actor.user_id,
                    session_id=session_id,
                )
            preference = self.model_preferences.get(resolved.model_context(), session_id) if self.model_preferences else None
            selection = self.llm_resolver.resolve(preference) if self.llm_resolver else None
            if selection is None:
                raise ValueError("LLM resolver is not configured")
            endpoint = _find_endpoint(self.llm_resolver.endpoints(), selection.endpoint_name)
            return ModelState(
                endpoint=selection.endpoint_name,
                current_model=selection.model_name,
                models=_endpoint_model_names(endpoint),
            )
        current_endpoint = _current_endpoint(self.llm)
        configured_endpoint = _configured_endpoint(self.llm, current_endpoint.name) or current_endpoint
        models = _endpoint_model_names(configured_endpoint)
        if current_endpoint.model not in models:
            models.insert(0, current_endpoint.model)
        return ModelState(
            endpoint=current_endpoint.name,
            current_model=current_endpoint.model,
            models=models,
        )

    def set_model_preference(
        self,
        actor: ActorContext | str,
        session_id: str | None = None,
        model: str | None = None,
        *,
        request_id: str = "",
    ) -> ModelState:
        """Set the preferred model for the current endpoint and return new state."""

        if isinstance(actor, ActorContext):
            resolved_actor = actor
            resolved_session_id = str(session_id or "")
            selected_model = str(model or "").strip()
        else:
            resolved_actor = local_operator_actor(channel="web")
            resolved_session_id = ""
            selected_model = str(actor).strip()
        if not selected_model:
            raise ValueError("model is required")
        if self.session_access is not None and resolved_actor.user_id is not None:
            resolved = self.session_access.ensure_session(
                resolved_actor, resolved_session_id, channel="web", write=True
            )
            if resolved.created:
                log_event(
                    session_logger,
                    logging.INFO,
                    "session.created",
                    actor_user_id=resolved_actor.user_id,
                    session_id=resolved_session_id,
                    request_id=request_id,
                )
            state = self.model_state(
                resolved_actor,
                resolved_session_id,
                request_id=request_id,
            )
            selection = self.llm_resolver.select(state.endpoint, selected_model)
            if (
                selection.endpoint_name == state.endpoint
                and selection.model_name == state.current_model
            ):
                return state
            self.model_preferences.set(
                resolved.model_context(),
                resolved_session_id,
                SessionModelPreference(selection.endpoint_name, selection.model_name),
            )
            log_event(
                web_logger,
                logging.INFO,
                "model.switched",
                actor_user_id=resolved_actor.user_id,
                session_id=resolved_session_id,
                request_id=request_id,
                endpoint=selection.endpoint_name,
                model=selection.model_name,
            )
            return self.model_state(
                resolved_actor,
                resolved_session_id,
                request_id=request_id,
            )
        current_endpoint = _current_endpoint(self.llm)

        selected_endpoint, error = self.llm.match_endpoint(f"{current_endpoint.name}/{selected_model}")
        if selected_endpoint is None:
            raise ValueError(error or "model is not supported by the current endpoint")
        self.llm.set_preferred(selected_endpoint.name, selected_endpoint.model)
        return self.model_state()

    def reset_model_preference(
        self,
        actor: ActorContext,
        session_id: str,
        *,
        request_id: str = "",
    ) -> ModelState:
        """Clear only one session's model fields and restore the system default."""

        if self.session_access is None or self.model_preferences is None:
            self.llm.reset_preferred()
            return self.model_state()
        resolved = self.session_access.resolve_session(actor, session_id, write=True)
        existing = self.model_preferences.get(resolved.model_context(), session_id)
        self.model_preferences.reset(resolved.model_context(), session_id)
        if existing is not None:
            log_event(
                web_logger,
                logging.INFO,
                "model.reset",
                actor_user_id=actor.user_id or "",
                session_id=session_id,
                request_id=request_id,
            )
        return self.model_state(actor, session_id, request_id=request_id)

    def list_tool_confirmations(self, actor: ActorContext) -> list[dict[str, Any]]:
        if self.confirmation_broker is None:
            return []
        return self.confirmation_broker.list_for_actor(actor)

    def decide_tool_confirmation(
        self,
        actor: ActorContext,
        confirmation_id: str,
        approved: bool,
    ) -> str:
        if self.confirmation_broker is None:
            raise ValueError("confirmation broker is not configured")
        return self.confirmation_broker.decide(actor, confirmation_id, approved)

    def _handle_model_command(
        self,
        actor: ActorContext,
        session_id: str,
        target: str,
        *,
        request_id: str = "",
    ) -> str:
        """Handle session-scoped Web /model commands."""

        normalized = target.strip()
        if not normalized:
            state = self.model_state(actor, session_id, request_id=request_id)
            return f"Current model: `{state.endpoint}/{state.current_model}`"
        if normalized.lower() == "reset":
            state = self.reset_model_preference(actor, session_id, request_id=request_id)
            return f"Model preference reset.\n\nCurrent model: `{state.endpoint}/{state.current_model}`"
        if normalized.lower() == "list" or normalized.lower().startswith("list "):
            endpoint_name = normalized[4:].strip()
            if endpoint_name:
                return _format_endpoint_model_list(
                    self.llm_resolver or self.llm,
                    endpoint_name,
                )
            return _format_model_list(self.llm_resolver or self.llm)
        if self.session_access is None or self.model_preferences is None or self.llm_resolver is None:
            return _handle_model_command(self.llm, normalized)
        endpoint_name, separator, model_name = normalized.partition("/")
        selection = self.llm_resolver.select(endpoint_name, model_name if separator else None)
        resolved = self.session_access.ensure_session(
            actor, session_id, channel="web", write=True
        )
        if resolved.created:
            log_event(
                session_logger,
                logging.INFO,
                "session.created",
                actor_user_id=actor.user_id,
                session_id=session_id,
            )
        current = self.model_state(actor, session_id, request_id=request_id)
        if selection.endpoint_name == current.endpoint and selection.model_name == current.current_model:
            return f"Current model: `{selection.endpoint_name}/{selection.model_name}`"
        self.model_preferences.set(
            resolved.model_context(),
            session_id,
            SessionModelPreference(selection.endpoint_name, selection.model_name),
        )
        log_event(
            web_logger,
            logging.INFO,
            "model.switched",
            actor_user_id=actor.user_id or "",
            session_id=session_id,
            request_id=request_id,
            endpoint=selection.endpoint_name,
            model=selection.model_name,
        )
        return f"Model switched: `{selection.endpoint_name}/{selection.model_name}`"

    def _audit(
        self,
        actor: ActorContext,
        action: str,
        resource_type: str,
        *,
        session_id: str = "",
        turn_id: str = "",
        request_id: str = "",
        decision: str = "",
        reason_code: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_sink is None:
            return
        self.audit_sink.record(
            AuditEvent(
                action=action,
                resource_type=resource_type,
                actor=actor,
                session_id=session_id,
                turn_id=turn_id,
                request_id=request_id,
                channel=actor.channel,
                decision=decision,
                reason_code=reason_code,
                metadata=dict(metadata or {}),
            )
        )

    def _register_turn(self, key: tuple[str, str], active: ActiveTurn) -> None:
        """Register the active turn, cancelling any older turn for the session."""

        with self._turns_lock:
            if not self._accepting_turns:
                raise RuntimeError("gateway is shutting down and is not accepting new turns")
            old_turn = self._active_turns.get(key)
            if old_turn is not None:
                old_turn.token.cancel()
            self._active_turns[key] = active

    def _unregister_turn(self, key: tuple[str, str], turn_id: str) -> None:
        """Remove an active turn only if it is still the current one."""

        with self._turns_lock:
            active = self._active_turns.get(key)
            if active is not None and active.turn_id == turn_id:
                self._active_turns.pop(key, None)
                active.completed.set()

    def shutdown(self) -> None:
        """Cancel pending background work before the Gateway exits."""

        with self._turns_lock:
            if self._shutdown_complete:
                return
            self._accepting_turns = False
            active_turns = tuple(self._active_turns.values())
            self._active_turns.clear()
            self._shutdown_complete = True
        for active in active_turns:
            active.token.cancel()
            active.completed.set()
        if self.mcp_runtime is not None:
            self.mcp_runtime.cancel_active_calls()
        if self.channel_manager is not None:
            self.channel_manager.stop()
        if self.memory_scheduler is not None:
            self.memory_scheduler.shutdown()
        if self.mcp_runtime is not None:
            self.mcp_runtime.close()
        if self.xhs_sidecar is not None:
            self.xhs_sidecar.stop()
        if self.hotel_accounts is not None:
            self.hotel_accounts.stop()
        if self.workflow_runtime is not None:
            scheduler = getattr(self.workflow_runtime, "scheduler", None)
            if scheduler is not None:
                scheduler.shutdown()
            self.workflow_runtime.executor.shutdown()

    def submit_mcp_interaction(
        self,
        interaction_id: str,
        action: str,
        content: dict[str, Any] | None = None,
    ) -> bool:
        """Forward one Web/WS Elicitation response to the shared MCP Runtime."""

        if self.mcp_runtime is None or action not in {"accept", "decline", "cancel"}:
            return False
        return self.mcp_runtime.submit_interaction(
            interaction_id,
            McpInteractionResponse(action=action, content=content),  # type: ignore[arg-type]
        )

    def _schedule_memory_extraction(self, actor: ActorContext, session_id: str) -> None:
        if not self.memory_extraction_enabled or self.prompt_loader is None:
            return
        scheduler = self._ensure_memory_scheduler()
        scheduler.schedule(
            _memory_extraction_actor_key(actor),
            actor,
            session_id,
        )

    def _cancel_memory_extraction(self, actor: ActorContext, session_id: str) -> None:
        if self.memory_scheduler is not None:
            self.memory_scheduler.cancel(_memory_extraction_actor_key(actor), session_id)

    def _run_memory_extraction(self, job: MemoryExtractionJob) -> None:
        if self.prompt_loader is None:
            return
        actor = job.actor
        session_id = job.session_id
        sessions = self.sessions
        memory_dir = self.config.local_memory_dir
        scope = "workspace"
        actor_user_id = None
        llm = self.llm
        context_budget = self.llm_resolver.context_budget() if self.llm_resolver else None
        if self.session_access is not None and actor.user_id is not None:
            resolved = self.session_access.resolve_session(actor, session_id)
            sessions = resolved.store
            is_owner = "owner" in actor.role_keys
            memory_dir = resolved.context.memory_dir
            scope = "workspace" if is_owner else "user"
            actor_user_id = None if is_owner else actor.user_id
            if self.model_preferences is not None and self.llm_resolver is not None:
                preference = self.model_preferences.get(resolved.model_context(), session_id)
                selection = self.llm_resolver.resolve(preference)
                llm = self.llm_resolver.bind(selection)
                context_budget = selection.context_budget
        if job.cancelled.is_set():
            return
        context = build_memory_context(
            memory_dir,
            scope=scope,
            actor_user_id=actor_user_id,
        )
        result = MemoryExtractionService(
            context,
            MarkdownMemoryStore(context),
            self.prompt_loader,
            MemorySafetyPolicy(),
        ).extract(
            session_id,
            sessions.load(session_id).messages,
            llm,
            context_budget=context_budget,
            should_commit=lambda: not job.cancelled.is_set(),
        )
        if job.cancelled.is_set():
            return
        log_event(
            memory_logger,
            logging.INFO if result.added else logging.DEBUG,
            "memory.extraction.done",
            actor_user_id=actor.user_id or "",
            session_id=session_id,
            reviewed_through_turn_index=result.reviewed_through_turn_index,
            added_count=len(result.added),
        )

    def _ensure_memory_scheduler(self) -> MemoryExtractionScheduler:
        if self.memory_scheduler is None:
            self.memory_scheduler = MemoryExtractionScheduler(
                self._run_memory_extraction,
                idle_seconds=self.memory_idle_seconds,
                max_workers=self.memory_extraction_max_workers,
                max_pending_jobs=self.memory_extraction_max_pending_jobs,
                retryable=_is_retryable_memory_extraction_error,
            )
        return self.memory_scheduler


def _aggregate_qq_channel_status(
    account_statuses: list[CapabilityStatus],
) -> CapabilityStatus:
    """Collapse internal QQ account adapters into one public channel status."""

    states = {status.state for status in account_statuses}
    if states == {"available"}:
        state = "available"
    elif states == {"disabled"}:
        state = "disabled"
    elif states <= {"unavailable", "disabled"} and "unavailable" in states:
        state = "unavailable"
    else:
        state = "degraded"
    return CapabilityStatus(
        name="channel.qq",
        state=state,
        code=f"CHANNEL_QQ_{state.upper()}",
        message=f"QQ channel is {state}.",
        details={"account_count": len(account_statuses)},
    )


def _build_connection_runtime(
    config: AppConfig,
    *,
    auth_store: SQLiteAuthStore,
    official_email_provider: Any | None,
) -> ConnectionRuntime:
    """Always expose My email; enable optional SMTP only with a valid key."""

    try:
        cipher = CredentialCipher(load_master_key())
    except ConnectionError:
        return ConnectionRuntime(
            None,
            notification_store=auth_store,
            official_email_provider=official_email_provider,
        )
    return ConnectionRuntime(
        SQLiteConnectionStore(config.connections_db_path, cipher),
        notification_store=auth_store,
        official_email_provider=official_email_provider,
    )


def build_web_runtime(
    config: AppConfig,
    *,
    endpoint_name: str = "auto",
) -> WebRuntime:
    """Build the runtime graph used by the local FastAPI gateway."""

    config.ensure_dirs()
    skill_sync = SkillSourceSync(
        workspace=config.workspace,
        config_dir=config.config_dir,
        extends_dir=config.extends_dir,
    )
    skill_sync_error = _sync_startup_skills(skill_sync)

    sync_managed_application_prompts(config)
    prompt_loader = PromptLoader(config.prompts_dir)
    prompt_loader.load_many(DEFAULT_CONTEXT_PROMPTS)
    session_store = JsonlSessionStore(config.sessions_dir)
    subagent_preferences = JsonSessionSubagentPreferenceStore()
    skill_loader = _create_skill_loader(skill_sync, startup_error=skill_sync_error)
    subagent_startup = check_subagent_startup(config.config_dir, prompt_loader)
    mcp_startup = check_mcp_startup(config.config_dir)
    memory_extraction_startup = check_memory_extraction_startup(prompt_loader)
    subagent_config = subagent_startup.config
    context_startup = check_context_engineering_startup(config.config_dir, prompt_loader)
    context_config = load_context_config(config.config_dir)
    try:
        travel_config = load_travel_config(config.config_dir)
        travel_status = CapabilityStatus(
            name="travel",
            state="available" if travel_config.enabled else "disabled",
            code="TRAVEL_AVAILABLE" if travel_config.enabled else "TRAVEL_DISABLED",
            message=(
                "Travel planning is available."
                if travel_config.enabled
                else "Travel planning is not configured."
            ),
        )
    except TravelConfigurationError as exc:
        travel_config = TravelConfig()
        travel_status = CapabilityStatus(
            name="travel",
            state="unavailable",
            code="TRAVEL_CONFIG_INVALID",
            message="Travel planning configuration is invalid.",
            hint="Fix the travel section in config/config.yml and restart.",
            details={"error_type": type(exc).__name__},
        )
    llm = create_configured_llm_provider(config.config_dir, endpoint_name)
    try:
        compaction_llm = create_optional_aliased_llm_provider(
            config.config_dir,
            "compaction",
        )
    except LLMConfigurationError as exc:
        compaction_llm = None
        log_event(
            web_logger,
            logging.WARNING,
            "context.compaction.endpoint_degraded",
            error_type=type(exc).__name__,
        )
    extra_system_prompts = []
    if subagent_config.enabled:
        extra_system_prompts.append("subagent_orchestration")
    context_builder = ContextBuilder(
        prompt_loader,
        skills=skill_loader,
        max_history_messages=DEFAULT_WEB_HISTORY_MESSAGES,
        extra_system_prompts=tuple(extra_system_prompts),
        context_config=context_config,
        embedding_provider=context_startup.embedding_provider,
        compaction_llm_provider=compaction_llm,
    )
    default_endpoint = _current_endpoint(llm).name
    llm_resolver = ConfiguredLLMProviderResolver(
        list(llm.endpoints()),
        default_endpoint=default_endpoint,
    )
    auth_store = SQLiteAuthStore(config.auth_db_path)
    auth_store.initialize_schema()
    audit_sink = SqliteAuditSink(auth_store)
    activity_sink = SqliteRuntimeActivitySink(auth_store)
    auth = AuthService(
        auth_store,
        audit_sink=audit_sink,
        setup_token=os.getenv("ZHICE_AGENT_SETUP_TOKEN", ""),
    )
    user_contexts = FilesystemUserContextResolver(
        config.contexts_dir,
        workspace_dir=config.workspace,
    )
    session_access = SessionAccessService(auth_store, user_contexts)
    from agent.channels import ExternalIdentityService, load_channel_configuration
    from agent.channels.qq import QQNotificationProvider
    from agent.channels.weixin import WeixinNotificationProvider

    channel_config = load_channel_configuration(config.config_dir)
    identity = ExternalIdentityService(auth_store)
    qq_notification_provider = QQNotificationProvider(identity)
    weixin_notification_provider = WeixinNotificationProvider(identity)
    travel_service = TravelApplicationService(travel_config, user_contexts)
    travel_requirement_extractor = (
        TravelRequirementExtractor(llm, prompt_loader) if travel_config.enabled else None
    )
    model_preferences = JsonSessionModelPreferenceStore()
    confirmation_broker = SQLiteToolConfirmationBroker(auth_store)
    diagnostics = RecentActivityDiagnostics(auth_store, config.logs_dir)
    system_diagnostics = SystemDiagnosticsService(auth_store, config.logs_dir)
    tool_policy = RbacToolExecutionPolicy()
    xhs_sidecar = LocalXhsSidecarSupervisor.from_specs(config.workspace, mcp_startup.specs)
    xhs_sidecar.start()
    hotel_accounts = HotelAccountSupervisor(config.workspace)
    try:
        mcp_runtime = McpRuntime(
            mcp_startup.specs,
            workspace=config.workspace,
            activity_sink=activity_sink,
            audit_sink=audit_sink,
        )
    except Exception:
        xhs_sidecar.stop()
        raise
    hook_runtime = TravelProgressHookRuntime(
        create_hook_runtime(config.workspace, config.config_dir)
    )
    operator = local_operator_actor(channel="web")
    tool_registry = create_default_tool_registry(
        config.workspace,
        skills=skill_loader,
        skill_sync=skill_sync,
        extra_tools=[
            *travel_service.tools_for_actor(operator),
            *mcp_runtime.tools_for_actor(operator, config.workspace),
        ],
    )
    agent_loop = AgentLoop(
        llm=llm,
        sessions=session_store,
        context_builder=context_builder,
        workspace=config.workspace,
        tools=tool_registry,
        tool_policy=tool_policy,
        confirmation_broker=confirmation_broker,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
        hook_runtime=hook_runtime,
    )
    official_email_provider = None
    official_host = os.getenv("ZHICE_SMTP_HOST", "").strip()
    official_username = os.getenv("ZHICE_SMTP_USERNAME", "").strip()
    official_password = os.getenv("ZHICE_SMTP_PASSWORD", "")
    official_from = os.getenv("ZHICE_SMTP_FROM", "").strip()
    if official_host and official_username and official_password and official_from:
        official_port = int(os.getenv("ZHICE_SMTP_PORT", "587"))
        official_email_provider = OfficialSMTPEmailProvider(
            host=official_host,
            port=official_port,
            security="tls" if official_port == 465 else "starttls",
            username=official_username,
            app_password=official_password,
            from_address=official_from,
        )
    connection_runtime = _build_connection_runtime(
        config,
        auth_store=auth_store,
        official_email_provider=official_email_provider,
    )
    workflow_runtime = None
    workflow_config: dict[str, Any] = {}
    try:
        config_payload = yaml.safe_load(config.mcp_config_path.read_text(encoding="utf-8")) or {}
        workflow_config = dict(config_payload.get("workflows") or {})
    except (OSError, ValueError, yaml.YAMLError):
        workflow_config = {}
    if bool(workflow_config.get("enabled", False)):
        workflow_store = WorkflowStore(config.workflows_db_path)
        workflow_policy = WorkflowAuthorizationPolicy(
            query_tools=with_required_query_helpers(
                {str(item) for item in workflow_config.get("allowed_query_tools", [])}
            ),
            action_tools=frozenset(str(item) for item in workflow_config.get("allowed_action_tools", [])),
        )

        def workflow_executor_for(actor: ActorContext) -> WorkflowExecutor:
            user_context = user_contexts.resolve(str(actor.user_id))
            workflow_tools = UserScopedToolProvider(
                files_dir=user_context.files_dir,
                shared_readonly_dir=user_context.shared_readonly_dir,
                actor=actor,
                skills=skill_loader.for_actor(actor),
                skill_sync=skill_sync,
                diagnostics=diagnostics,
                system_diagnostics=system_diagnostics,
                extra_tools=mcp_runtime.tools_for_actor(actor, user_context.files_dir),
            )

            def personal_email(**values: Any) -> Any:
                if connection_runtime is None:
                    raise RuntimeError("CONNECTION_CREDENTIAL_KEY_MISSING")
                from agent.connections.protocols import EmailMessage

                connection_id = str(values.pop("connection_id"))
                values.pop("owner_user_id", None)
                provider = connection_runtime.personal_email_provider(actor, connection_id)
                raw_recipients = values.get("to") or values.get("recipients") or ()
                recipients = (
                    (raw_recipients,)
                    if isinstance(raw_recipients, str)
                    else tuple(raw_recipients)
                )
                return provider.send(EmailMessage(recipients, str(values.get("subject", "")), str(values.get("body") or values.get("text") or ""), values.get("html")))

            def official_email(**values: Any) -> Any:
                if official_email_provider is None:
                    raise RuntimeError("OFFICIAL_EMAIL_NOT_CONFIGURED")
                recipient = auth_store.notification_email(str(values.get("owner_user_id")))
                if not recipient:
                    raise RuntimeError("NOTIFICATION_EMAIL_NOT_VERIFIED")
                from agent.connections.protocols import EmailMessage

                return official_email_provider.send(EmailMessage((recipient,), str(values.get("subject", "")), str(values.get("body", ""))))

            def qq_notification(**values: Any) -> Any:
                return qq_notification_provider.send_to_user(
                    user_id=str(values.get("owner_user_id") or ""),
                    content=str(values.get("body") or values.get("text") or ""),
                )

            def weixin_notification(**values: Any) -> Any:
                return weixin_notification_provider.send_to_user(
                    user_id=str(values.get("owner_user_id") or ""),
                    content=str(values.get("body") or values.get("text") or ""),
                    delivery_key=str(values.get("delivery_key") or ""),
                )

            handlers = NodeHandlers(
                actor=actor,
                policy=workflow_policy,
                tools=workflow_tools,
                llm=llm,
                official_email=official_email,
                personal_email=personal_email,
                qq_notification=qq_notification,
                weixin_notification=weixin_notification,
            )
            return WorkflowExecutor(
                workflow_store,
                handlers,
                max_workers=int(workflow_config.get("max_global_workers", 4)),
            )

        baseline_executor = workflow_executor_for(operator)

        def workflow_capabilities(actor: ActorContext) -> dict[str, Any]:
            if connection_runtime.smtp_available:
                connection_runtime.list(actor)
            official_code = ""
            if official_email_provider is None:
                official_code = "OFFICIAL_EMAIL_NOT_CONFIGURED"
            elif not auth_store.notification_email(str(actor.user_id)):
                official_code = "NOTIFICATION_EMAIL_NOT_VERIFIED"
            capability_executor = workflow_executor_for(actor)
            try:
                live_tool_names = {
                    str(item.get("function", item).get("name", ""))
                    for item in (capability_executor.handlers.tools.definitions() if capability_executor.handlers.tools else [])
                }
            finally:
                capability_executor.shutdown()
            live_actions = workflow_policy.action_tools.intersection(live_tool_names)
            return {
                "official_notification": {
                    "available": not official_code,
                    "code": official_code,
                },
                "personal_email": {
                    "available": connection_runtime.smtp_available,
                    "code": "" if connection_runtime.smtp_available else "CONNECTION_CREDENTIAL_KEY_MISSING",
                },
                "qq_notification": qq_notification_provider.capability(actor),
                "weixin_notification": weixin_notification_provider.capability(actor),
                "external_actions": {
                    "available": bool(live_actions),
                    "count": len(live_actions),
                },
            }

        def validate_workflow_notification(
            actor: ActorContext, channel: str
        ) -> None:
            if channel == "qq":
                qq_notification_provider.validate(actor)
                return
            if channel == "weixin":
                weixin_notification_provider.validate(actor)
                return
            raise RuntimeError("WORKFLOW_NOTIFICATION_PROVIDER_UNSUPPORTED")

        workflow_runtime = WorkflowRuntime(
            workflow_store,
            baseline_executor,
            workflow_policy,
            executor_factory=workflow_executor_for,
            capability_provider=workflow_capabilities,
            connection_validator=(connection_runtime.validate_email_connection if connection_runtime.smtp_available else None),
            notification_validator=validate_workflow_notification,
        )

        def scheduled_workflow_run(workflow_id: str, scheduled_for: str) -> None:
            definition = workflow_store.get_published(workflow_id)
            if definition is None:
                return
            scheduled_actor = auth_store.actor_for_user(definition.owner_user_id, channel="workflow")
            workflow_runtime.run(scheduled_actor, workflow_id)

        scheduler = WorkflowScheduler(
            workflow_store,
            scheduled_workflow_run,
            workspace=config.workspace,
            max_workers=int(workflow_config.get("max_global_workers", 4)),
        )
        scheduler.start()
        workflow_runtime.scheduler = scheduler
    runtime = WebRuntime(
        config=config,
        sessions=session_store,
        agent_loop=agent_loop,
        llm=llm,
        auth=auth,
        session_access=session_access,
        model_preferences=model_preferences,
        subagent_preferences=subagent_preferences,
        subagent_profiles=_load_subagent_profile_summaries(config.config_dir),
        subagent_config=subagent_config,
        subagent_status=subagent_startup.status,
        mcp_status=mcp_startup.status,
        memory_extraction_status=memory_extraction_startup.status,
        context_engineering_status=context_startup.status,
        travel_status=travel_status,
        llm_resolver=llm_resolver,
        tool_policy=tool_policy,
        confirmation_broker=confirmation_broker,
        activity_sink=activity_sink,
        audit_sink=audit_sink,
        diagnostics=diagnostics,
        system_diagnostics=system_diagnostics,
        skill_loader=skill_loader,
        skill_sync=skill_sync,
        skill_status=skill_sync.status_store,
        prompt_loader=prompt_loader,
        travel_service=travel_service,
        travel_requirement_extractor=travel_requirement_extractor,
        xhs_sidecar=xhs_sidecar,
        hotel_accounts=hotel_accounts,
        connection_runtime=connection_runtime,
        official_email_provider=official_email_provider,
        workflow_runtime=workflow_runtime,
        memory_extraction_enabled=memory_extraction_startup.enabled,
        mcp_runtime=mcp_runtime,
    )
    from agent.channels import (
        ChannelConversationService,
        ChannelDedupService,
        ChannelManager,
        ChannelRuntimeAdapter,
    )
    from agent.channels.qq import build_qq_adapters
    from agent.channels.weixin import build_weixin_adapter

    conversations = ChannelConversationService(auth_store, session_access)
    dedup = ChannelDedupService(auth_store)
    channel_runtime = ChannelRuntimeAdapter(runtime, conversations)
    adapters, channel_status = build_qq_adapters(
        channel_config.qq,
        identity,
        conversations,
        dedup,
        channel_runtime,
    )
    qq_notification_provider.register_adapters(adapters)
    weixin_adapter, weixin_binding, weixin_status = build_weixin_adapter(
        channel_config.weixin,
        config.workspace,
        identity,
        conversations,
        dedup,
        channel_runtime,
    )
    weixin_notification_provider.register_adapter(weixin_adapter)
    adapters_by_channel = {
        "qq": tuple(adapters),
        "weixin": ((weixin_adapter,) if weixin_adapter is not None else ()),
    }
    all_adapters = tuple(
        adapter
        for channel in channel_config.order
        for adapter in adapters_by_channel[channel]
    )
    runtime.channel_manager = ChannelManager(all_adapters)
    runtime.channel_status = channel_status
    runtime.channel_statuses = {"channel.weixin": weixin_status}
    runtime.channel_identity = identity
    runtime.channel_config = channel_config
    runtime.channel_weixin_binding = weixin_binding
    return runtime


def _sync_startup_skills(skill_sync: SkillSourceSync) -> SkillSyncError | None:
    """Run best-effort startup sync and defer one structured warning to loader setup."""

    try:
        skill_sync.sync_on_startup()
    except SkillSyncError as exc:
        return exc
    return None


def _run_agent_turn(agent_loop, session_id: str, message: str, **kwargs) -> str:
    """Pass only kwargs supported by the concrete or fake AgentLoop."""

    method = agent_loop.run_turn
    parameters = inspect.signature(method).parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    filtered = kwargs if accepts_kwargs else {key: value for key, value in kwargs.items() if key in parameters}
    return method(session_id, message, **filtered)


def _normalize_actor_session(
    actor: ActorContext | str,
    session_id: str | None,
) -> tuple[ActorContext, str]:
    """Normalize new actor-aware and legacy direct runtime call forms."""

    if isinstance(actor, ActorContext):
        resolved_session_id = str(session_id or "").strip()
        if not resolved_session_id:
            raise ValueError("session_id is required")
        return actor, resolved_session_id
    resolved_session_id = str(actor).strip()
    if not resolved_session_id:
        raise ValueError("session_id is required")
    return local_operator_actor(channel="web"), resolved_session_id


def _normalize_actor_session_message(
    actor: ActorContext | str,
    session_id: str | None,
    message: str | None,
) -> tuple[ActorContext, str, str]:
    """Normalize actor-aware calls and the former (session_id, message) form."""

    if isinstance(actor, ActorContext):
        resolved_actor, resolved_session_id = _normalize_actor_session(actor, session_id)
        resolved_message = str(message or "")
    else:
        resolved_actor = local_operator_actor(channel="web")
        resolved_session_id = str(actor).strip()
        resolved_message = str(session_id if message is None else message)
    if not resolved_session_id:
        raise ValueError("session_id is required")
    return resolved_actor, resolved_session_id, resolved_message


def _active_turn_key(actor: ActorContext, session_id: str) -> tuple[str, str]:
    """Keep active turns isolated by stable actor identity and session id."""

    actor_key = actor.user_id or f"{actor.actor_type}:{actor.username}"
    return actor_key, session_id


def _is_recent_timestamp(value: str, *, seconds: int) -> bool:
    """Return whether an ISO timestamp is inside a small startup race window."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds()
        return 0 <= age <= seconds
    except ValueError:
        return False


def _memory_extraction_actor_key(actor: ActorContext) -> str:
    """Serialize Owner/CLI workspace Memory separately from ordinary users."""

    if "owner" in actor.role_keys or actor.actor_type == "local_operator":
        return "workspace-operator"
    return actor.user_id or f"{actor.actor_type}:{actor.username}"


def _is_retryable_memory_extraction_error(exc: BaseException) -> bool:
    return isinstance(exc, MemoryStoreError) and exc.code == "MEMORY_EXTRACTION_PROVIDER_FAILED"


def _find_endpoint(endpoints: list[LLMEndpoint], endpoint_name: str) -> LLMEndpoint:
    for endpoint in endpoints:
        if endpoint.name == endpoint_name:
            return endpoint
    raise ValueError(f"Unknown endpoint: {endpoint_name}")


def _emit_runtime_event(on_event: RuntimeEventCallback | None, event: dict[str, Any]) -> None:
    """Best-effort emit a legacy text or interaction event."""

    if on_event is not None:
        try:
            on_event(event)
        except Exception as exc:  # noqa: BLE001 - channel observation cannot break a turn.
            log_event(
                web_logger,
                logging.WARNING,
                "runtime_event.sink_failed",
                event_type=str(event.get("type") or "unknown"),
                error_type=type(exc).__name__,
            )


def _format_memory_notice(contents: tuple[str, ...]) -> str:
    if not contents:
        return ""
    if len(contents) == 1:
        return f"💾 根据上次对话，我记住了：{contents[0]}"
    rendered = "；".join(contents[:3])
    return f"💾 根据上次对话，我记住了：{rendered}"


def _find_session_summary(summaries: list[SessionSummary], session_id: str) -> SessionSummary:
    """Return the summary for a session after a metadata mutation."""

    for summary in summaries:
        if summary.session_id == session_id:
            return summary
    return SessionSummary(
        session_id=session_id,
        preview="(empty)",
        updated_at=0.0,
        message_count=0,
    )


def _create_skill_loader(
    skill_sync: SkillSourceSync,
    *,
    startup_error: SkillSyncError | None = None,
) -> SkillLoader:
    """Create a SkillLoader and emit at most one warning for one startup cause."""

    if not skill_sync.has_config():
        return SkillLoader([], cache_path=skill_sync.workspace / "state" / "skill_index.json")
    try:
        roots = skill_sync.skill_roots()
    except SkillSyncError as exc:
        log_event(
            skill_logger,
            logging.WARNING,
            "skills.runtime_unavailable",
            code="SKILL_SOURCE_CONFIG_INVALID",
            message="Skill source configuration is invalid.",
            hint="Fix the skills section in config/config.yml, then restart the process.",
            config_file="config.yml",
            error_type=type(exc).__name__,
        )
        return SkillLoader([], cache_path=skill_sync.workspace / "state" / "skill_index.json")
    if startup_error is not None:
        log_event(
            skill_logger,
            logging.WARNING,
            "skills.sync_degraded",
            code="SKILL_SYNC_FAILED",
            message="Configured Skill source synchronization failed.",
            hint="Run /skills sync --verbose to inspect and retry synchronization.",
            config_file="config.yml",
            error_type=type(startup_error).__name__,
        )
    return SkillLoader(
        roots,
        cache_path=skill_sync.workspace / "state" / "skill_index.json",
    )


def _current_endpoint(llm: LLMProvider) -> LLMEndpoint:
    """Read the current endpoint from the configured Web provider."""

    endpoint = llm.current_endpoint()
    if not isinstance(endpoint, LLMEndpoint):
        raise ValueError("current_endpoint must return an LLMEndpoint")
    return endpoint


def _configured_endpoint(llm: LLMProvider, endpoint_name: str) -> LLMEndpoint | None:
    """Return the configured endpoint before any temporary model override."""

    for endpoint in llm.endpoints():
        if isinstance(endpoint, LLMEndpoint) and endpoint.name == endpoint_name:
            return endpoint
    return None


def _endpoint_model_names(endpoint: LLMEndpoint) -> list[str]:
    """Return endpoint default model first, followed by unique supported models."""

    models = [endpoint.model]
    for model in endpoint.supported_models:
        if model not in models:
            models.append(model)
    return models


def _web_help_text(command_profile: str = WEB_COMMAND_PROFILE) -> str:
    """Return compact Web slash-command help."""

    if command_profile == QQ_GROUP_COMMAND_PROFILE:
        return "\n".join(
            [
                "Available QQ group commands:",
                "",
                "- `/help` - show commands",
                "- `/new` - start a new private agent session for you in this group",
                "- `/clear` - clear your current session history",
                "- `/stop` - stop your active turn",
            ]
        )
    title = "Available QQ commands:" if command_profile == QQ_C2C_COMMAND_PROFILE else "Available Web commands:"
    lines = [
        title,
        "",
        "- `/help` - show commands",
        "- `/model` - show or switch the preferred model",
        "- `/subagent` - show or control Subagent delegation",
        "- `/memory` - show current Memory",
        "- `/mcp` - show available MCP capabilities",
        "- `/clear` - clear this session history",
    ]
    if command_profile != QQ_C2C_COMMAND_PROFILE:
        lines.append("- `/sessions` - list or manage recent sessions")
    if command_profile in EXTERNAL_STOP_PROFILES:
        lines.append("- `/stop` - stop the active turn")
    if command_profile == EXTERNAL_COMMAND_PROFILE:
        lines.append("- `/history` - show recent messages")
        lines.append("- `/exit` - close this WebSocket connection")
    return "\n".join(lines)


def _format_subagent_status(
    preference: SessionSubagentPreference,
    profiles: tuple[tuple[str, str], ...],
) -> str:
    """Format compact /subagent state without expanding subcommands in /help."""

    lines = [
        f"Current subagent mode: `{preference.mode}`",
        f"Force once: `{'true' if preference.force_once else 'false'}`",
        "",
        "Available profiles:",
    ]
    if profiles:
        lines.extend(f"- `{name}` - {description}" for name, description in profiles)
    else:
        lines.append("- No model-callable profiles are currently available.")
    lines.extend(
        [
            "",
            "Tip: use `/subagent auto` to allow automatic delegation, "
            "`/subagent off` to disable it, or `/subagent once` to use Subagent "
            "for the next message.",
        ]
    )
    return "\n".join(lines)


def _format_subagent_unavailable(
    status: CapabilityStatus | None,
    actor: ActorContext,
) -> str:
    """Return human-facing Subagent capability guidance for commands and chat."""

    return format_subagent_unavailable(
        status,
        include_details=can_view_subagent_details(actor),
    )


def _load_subagent_profile_summaries(config_dir) -> tuple[tuple[str, str], ...]:
    """Load enabled model-callable Profile summaries through the Part 13 loader."""

    try:
        from agent.subagents.config import load_subagent_config

        config = load_subagent_config(config_dir)
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


def _command_not_supported_for_client(command_text: str) -> str:
    """Return the shared client capability rejection text."""

    return f"Command not supported for this client: `{command_text}`.\n\nUse `/help` to see available commands."


def _unsupported_command(command_text: str) -> str:
    """Return the shared unknown command text."""

    return f"Unsupported command: `{command_text}`.\n\nUse `/help` to see available commands."


def _sessions_usage_text() -> str:
    """Return compact /sessions subcommand help."""

    return "\n".join(
        [
            "Available sessions commands:",
            "",
            "- `/sessions` - list recent sessions",
            "- `/sessions rename <id> <title>` - rename a session",
            "- `/sessions delete [id]` - clear the current session when id is omitted; delete the named session otherwise",
        ]
    )


def _sessions_tip_text() -> str:
    """Return the short /sessions management tip."""

    return (
        "Tip: use `/sessions rename <id> <title>` to rename, or "
        "`/sessions delete [id]` to delete; omitting id clears the current session like `/clear`."
    )


def _handle_model_command(llm: LLMProvider, target: str) -> str:
    """Handle Web /model commands without sending them to the LLM."""

    normalized = target.strip()
    if not normalized:
        return _format_model_status(llm)
    if normalized.lower() == "list" or normalized.lower().startswith("list "):
        endpoint_name = normalized[4:].strip()
        if endpoint_name:
            return _format_endpoint_model_list(llm, endpoint_name)
        return _format_model_list(llm)
    if normalized.lower() == "reset":
        llm.reset_preferred()
        return "Model preference reset.\n\n" + _format_model_status(llm)

    endpoint, error = llm.match_endpoint(normalized)
    if endpoint is None:
        return f"{error}\n\n{_format_model_status(llm)}"

    model_override = endpoint.model if "/" in normalized else None
    llm.set_preferred(endpoint.name, model_override)
    return f"Model switched: `{endpoint.name}/{endpoint.model}`"


def _format_model_status(llm: LLMProvider) -> str:
    """Format current endpoint/model state for Web command output."""

    current = llm.current_endpoint()
    return "\n".join(
        [
            f"Current model: `{current.name}/{current.model}`",
            "",
            "Tip: use `/model list` to see available endpoints and models.",
        ]
    )


def _format_model_list(llm: LLMProvider) -> str:
    """Format all endpoints for Web /model list."""

    if isinstance(llm, ConfiguredLLMProviderResolver):
        selection = llm.resolve(None)
        current_name = selection.endpoint_name
        current_model = selection.model_name
    else:
        current = llm.current_endpoint()
        current_name = current.name
        current_model = current.model
    lines = [f"Current model: `{current_name}/{current_model}`", "", "Available endpoints:"]
    for endpoint in llm.endpoints():
        marker = "*" if endpoint.name == current_name else "-"
        lines.append(f"{marker} `{endpoint.name}` default model: `{endpoint.model}`")
    lines.append("")
    lines.append("Use `/model list <endpoint>` to see available models for one endpoint.")
    return "\n".join(lines)


def _format_endpoint_model_list(llm: LLMProvider, endpoint_name: str) -> str:
    """Format supported models for one endpoint."""

    for endpoint in llm.endpoints():
        if endpoint.name != endpoint_name:
            continue
        models = _endpoint_model_names(endpoint)
        lines = [f"Endpoint: `{endpoint.name}`", "", "Available models:"]
        for model in models:
            suffix = " (default)" if model == endpoint.model else ""
            lines.append(f"- `{model}`{suffix}")
        return "\n".join(lines)
    return f"Unknown endpoint: `{endpoint_name}`\n\n{_format_model_list(llm)}"


def _format_session_list(summaries: list[SessionSummary], current_session_id: str) -> str:
    """Return a compact Markdown session list."""

    if not summaries:
        return f"No recent sessions.\n\n{_sessions_tip_text()}"
    lines = ["Recent sessions:"]
    for summary in summaries[:10]:
        marker = "*" if summary.session_id == current_session_id else "-"
        preview = summary.preview or "(empty)"
        lines.append(f"{marker} `{summary.session_id}` [{summary.message_count}] {preview}")
    lines.extend(["", _sessions_tip_text()])
    return "\n".join(lines)


def _format_session_history(messages) -> str:
    """Return recent user/assistant messages for Web command output."""

    visible = [message for message in messages if message.role in {"user", "assistant"}]
    if not visible:
        return "This session has no visible history."
    lines = ["Recent history:"]
    for message in visible[-10:]:
        content = " ".join(message.content.split())
        if len(content) > 120:
            content = content[:117] + "..."
        lines.append(f"- **{message.role}**: {content}")
    return "\n".join(lines)


def _travel_draft_title(draft: dict[str, Any], messages: list[Message]) -> str:
    """Build a short user-facing travel work title from validated fields."""

    origin = " ".join(str(draft.get("origin") or "").split())
    destinations = draft.get("destinations")
    destination = ""
    if isinstance(destinations, list | tuple):
        destination = " / ".join(
            " ".join(str(item).split()) for item in destinations if str(item).strip()
        )
    if origin and destination:
        return f"{origin} → {destination}"[:120]
    if destination:
        return f"{destination}旅行计划"[:120]
    for message in messages:
        if message.role != "user":
            continue
        content = " ".join(message.content.split())
        if content:
            return content[:120]
    return ""


def _travel_missing_fields(draft: dict[str, Any]) -> list[str]:
    checks = (
        ("origin", "出发地"),
        ("destinations", "目的地"),
        ("start_date", "开始日期"),
        ("end_date", "结束日期"),
        ("traveller_count", "人数"),
        ("budget_level", "旅行基调"),
    )
    return [label for field, label in checks if not draft.get(field)]


def _apply_travel_tone_defaults(draft: dict[str, Any]) -> dict[str, Any]:
    """Project the reviewed tone into concrete and visible planning defaults."""

    normalized = dict(draft)
    defaults = {
        "economy": (
            "旅行基调：经济实惠；住宿每间每晚上限约250元",
            "公共交通优先，减少非必要打车",
            "balanced",
        ),
        "balanced": (
            "旅行基调：舒适均衡；住宿每间每晚上限约450元",
            "公共交通为主，必要时短途打车减少折返",
            "balanced",
        ),
        "comfortable": (
            "旅行基调：轻松品质；住宿每间每晚上限约700元",
            "优先减少换乘和长距离步行，必要时打车",
            "relaxed",
        ),
    }.get(str(normalized.get("budget_level") or ""))
    if defaults is None:
        return normalized
    stay_default, transport_default, pace_default = defaults
    stay = [str(item) for item in normalized.get("stay_preferences", [])]
    transport = [str(item) for item in normalized.get("transport_preferences", [])]
    if stay_default not in stay:
        stay.append(stay_default)
    if transport_default not in transport:
        transport.append(transport_default)
    normalized["stay_preferences"] = stay
    normalized["transport_preferences"] = transport
    if not normalized.get("pace"):
        normalized["pace"] = pace_default
    return normalized


def _travel_child_llm_factory(
    parent_llm: LLMProvider,
) -> Callable[[SubagentProfile], LLMProvider]:
    """Honor explicit Profile roles and otherwise inherit the current parent model."""

    endpoints_method = getattr(parent_llm, "endpoints", None)
    if not callable(endpoints_method):
        raise ValueError("Travel Subagent runtime requires configured LLM endpoints")
    endpoints = list(endpoints_method())
    current_method = getattr(parent_llm, "current_endpoint", None)
    inherited = current_method() if callable(current_method) else endpoints[0]
    resolver = ConfiguredLLMProviderResolver(endpoints, default_endpoint=inherited.name)

    def create(profile: SubagentProfile) -> LLMProvider:
        selected = inherited
        if profile.model_role != "inherit":
            selected = min(
                (endpoint for endpoint in endpoints if endpoint.role == profile.model_role),
                key=lambda endpoint: endpoint.priority,
                default=inherited,
            )
        return resolver.bind(
            ModelSelection(selected.name, selected.model, source="subagent_profile")
        )

    return create
