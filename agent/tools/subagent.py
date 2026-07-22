"""Parent-facing batch delegation Tool and provider composition."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from agent.logging_utils import log_event
from agent.protocols.capability import CapabilityStatus
from agent.protocols.subagent import (
    SubagentBatchRequest,
    SubagentCoordinator,
    SubagentTask,
    SubagentTaskResult,
    subagent_unavailable_payload,
)
from agent.protocols.tool import ToolExecutionContext, ToolProvider, ToolResult
from agent.subagents.presentation import (
    GENERIC_SUBAGENT_UNAVAILABLE_TEXT,
    can_view_subagent_details,
)

subagent_tool_logger = logging.getLogger("zcagent.agent.subagent")

_REASONS = (
    "parallel_independent",
    "context_isolation",
    "specialist_capability",
    "independent_verification",
    "explicit_user_request",
)


class DelegateTasksTool:
    """Submit one bounded child batch to the turn-scoped Coordinator."""

    name = "delegate_tasks"
    parameters = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "enum": list(_REASONS)},
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1, "maxLength": 64},
                        "task": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "profile": {"type": "string", "minLength": 1, "maxLength": 64},
                        "expected_output": {"type": "string", "maxLength": 1000},
                    },
                    "required": ["id", "task", "profile"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["reason", "tasks"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        coordinator: SubagentCoordinator,
        *,
        profile_summaries: tuple[tuple[str, str], ...] = (),
        force_once: bool = False,
    ):
        self.coordinator = coordinator
        profiles = "; ".join(f"{name}: {description}" for name, description in profile_summaries)
        requirement = " This tool must be used once for the current user message." if force_once else ""
        self.description = (
            "Delegate independent or context-isolated tasks to configured child Agents and wait "
            "for structured partial-capable results. Prefer direct execution for simple tasks. "
            f"Available Profiles: {profiles or 'none'}.{requirement}"
        )

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Reject dispatch that omitted trusted parent context."""

        del args
        return ToolResult(
            output="Subagent delegation requires trusted turn context.",
            is_error=True,
            metadata={"code": "SUBAGENT_CONTEXT_REQUIRED", "tool_name": self.name},
        )

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Decode one validated batch and run the Coordinator."""

        try:
            request = _request_from_args(args)
        except ValueError as exc:
            return ToolResult(
                output=str(exc),
                is_error=True,
                metadata={"code": "SUBAGENT_INVALID_BATCH", "tool_name": self.name},
            )
        results = self.coordinator.run_batch(request, context)
        payload = {
            "status": _batch_status(results),
            "results": [_result_dict(result) for result in results],
        }
        error_code = _batch_error_code(results)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            is_error=payload["status"] in {"failed", "cancelled"},
            metadata={
                "code": "OK" if payload["status"] in {"completed", "partial"} else error_code,
                "batch_status": payload["status"],
                "task_count": len(results),
                "tool_name": self.name,
            },
        )


class UnavailableDelegateTasksTool:
    """Expose the capability failure without creating fake child executions."""

    name = DelegateTasksTool.name
    parameters = DelegateTasksTool.parameters

    def __init__(self, status: CapabilityStatus):
        self.status = status
        self.description = (
            "Subagent delegation is currently unavailable. You must call this tool when the user "
            "explicitly requests a Subagent so the exact capability error is returned. Do not "
            "substitute exec or another tool for the requested Subagent."
        )

    def execute(self, args: dict[str, Any]) -> ToolResult:
        del args
        return self._result()

    def execute_with_context(
        self,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        del args
        if can_view_subagent_details(context.actor):
            return self._result()
        status = self.status
        log_event(
            subagent_tool_logger,
            logging.WARNING,
            "subagent.runtime_unavailable",
            session_id=context.session_id,
            turn_id=context.turn_id,
            actor_user_id=context.actor.user_id or "",
            cause_code=status.code,
            capability_state=status.state,
        )
        return ToolResult(
            output=GENERIC_SUBAGENT_UNAVAILABLE_TEXT,
            is_error=True,
            metadata={
                "code": "SUBAGENT_UNAVAILABLE",
                "tool_name": self.name,
            },
        )

    def _result(self) -> ToolResult:
        payload = subagent_unavailable_payload(self.status)
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            is_error=True,
            metadata={
                "code": payload["cause_code"],
                "capability_code": payload["code"],
                "tool_name": self.name,
            },
        )


class AugmentedToolProvider:
    """Add contextual tools without exposing provider internals."""

    def __init__(
        self,
        base: ToolProvider,
        extra_tools: tuple[DelegateTasksTool | UnavailableDelegateTasksTool, ...],
    ):
        self.base = base
        self.extra_tools = {tool.name: tool for tool in extra_tools}

    def definitions(self) -> list[dict[str, Any]]:
        definitions = [copy.deepcopy(item) for item in self.base.definitions()]
        definitions.extend(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": copy.deepcopy(tool.parameters),
                },
            }
            for tool in self.extra_tools.values()
        )
        return definitions

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.extra_tools.get(name)
        if tool is not None:
            return tool.execute(args)
        return self.base.execute(name, args)

    def execute_with_context(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolExecutionContext,
    ) -> ToolResult:
        tool = self.extra_tools.get(name)
        if tool is not None:
            return tool.execute_with_context(args, context)
        contextual = getattr(self.base, "execute_with_context", None)
        if callable(contextual):
            return contextual(name, args, context)
        return self.base.execute(name, args)


def _request_from_args(args: dict[str, Any]) -> SubagentBatchRequest:
    if not isinstance(args, dict):
        raise ValueError("Subagent arguments must be an object.")
    reason = args.get("reason")
    if reason not in _REASONS:
        raise ValueError("Subagent reason is invalid.")
    raw_tasks = args.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("Subagent tasks must be a non-empty list.")
    tasks = []
    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise ValueError("Each Subagent task must be an object.")
        tasks.append(
            SubagentTask(
                task_id=str(raw.get("id") or ""),
                task=str(raw.get("task") or ""),
                profile_name=str(raw.get("profile") or ""),
                expected_output=str(raw.get("expected_output") or ""),
            )
        )
    return SubagentBatchRequest(reason=reason, tasks=tuple(tasks))  # type: ignore[arg-type]


def _batch_status(results: tuple[SubagentTaskResult, ...]) -> str:
    completed = sum(result.status == "completed" for result in results)
    if results and completed == len(results):
        return "completed"
    if completed:
        return "partial"
    if results and all(result.status == "cancelled" for result in results):
        return "cancelled"
    return "failed"


def _batch_error_code(results: tuple[SubagentTaskResult, ...]) -> str:
    """Keep a common terminal child cause on the parent Tool activity."""

    codes = {result.code for result in results if result.status != "completed" and result.code}
    if len(codes) == 1:
        return next(iter(codes))
    return "SUBAGENT_FAILED"


def _result_dict(result: SubagentTaskResult) -> dict[str, Any]:
    return {
        "id": result.task_id,
        "status": result.status,
        "code": result.code,
        "stage": result.stage,
        "output": result.output,
        "subagent_id": result.subagent_id,
        "child_session_id": result.child_session_id,
        "child_turn_id": result.child_turn_id,
        "duration_ms": result.duration_ms,
        "truncated": result.truncated,
    }
