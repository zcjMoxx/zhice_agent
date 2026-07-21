"""Configured Tool Hook Runtime with stage-specific failure policies."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from typing import Any

from agent.hooks.config import HookRegistry, HookSpec
from agent.hooks.runner import HookExecutionError, HookProcessRunner
from agent.logging_utils import log_event
from agent.protocols.hook import (
    PostToolHookRequest,
    PostToolHookResult,
    PreToolHookRequest,
    PreToolHookResult,
)
from agent.protocols.runtime_event import validate_runtime_event_presentation

hook_logger = logging.getLogger("zcagent.agent.hook")
_HOOK_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SAFE_RESULT_METADATA = frozenset(
    {
        "category",
        "code",
        "duration_ms",
        "duration_seconds",
        "exit_code",
        "match_count",
        "operation",
        "path",
        "timed_out",
        "total",
        "truncated",
    }
)


class ConfiguredHookRuntime:
    """Run ordered configured Hooks without changing core Tool security facts."""

    def __init__(self, registry: HookRegistry, runner: HookProcessRunner):
        self.registry = registry
        self.runner = runner

    def run_pre_tooluse(self, request: PreToolHookRequest) -> PreToolHookResult:
        current_arguments = dict(request.arguments)
        modified = False
        for spec in self.registry.select("pre_tooluse", request.tool_name):
            exemption = _resolve_exemption(spec, request.role_keys, request.permission_keys)
            if exemption is not None:
                _log_hook_skipped(spec, *exemption)
                continue
            started = time.perf_counter()
            try:
                output = self.runner.run(spec, _pre_payload(spec, request, current_arguments))
                result = _parse_pre_output(output)
            except (HookExecutionError, ValueError) as exc:
                code = exc.code if isinstance(exc, HookExecutionError) else "HOOK_INVALID_OUTPUT"
                _log_hook_result(spec, code=code, duration_ms=_duration_ms(started), failed=True)
                return PreToolHookResult(
                    action="block",
                    arguments=current_arguments,
                    code=code,
                    message="Tool execution was blocked because a pre-tool Hook failed safely.",
                )
            _log_hook_result(spec, code=result.action.upper(), duration_ms=_duration_ms(started))
            if result.action == "block":
                return replace(result, arguments=current_arguments)
            if result.action == "modify":
                current_arguments = dict(result.arguments)
                modified = True
        return PreToolHookResult(
            action="modify" if modified else "continue",
            arguments=current_arguments,
        )

    def run_post_tooluse(self, request: PostToolHookRequest) -> PostToolHookResult:
        display: dict[str, Any] = {}
        ui_metadata: dict[str, Any] = {}
        for spec in self.registry.select("post_tooluse", request.tool_name):
            exemption = _resolve_exemption(spec, request.role_keys, request.permission_keys)
            if exemption is not None:
                _log_hook_skipped(spec, *exemption)
                continue
            started = time.perf_counter()
            try:
                output = self.runner.run(spec, _post_payload(spec, request))
                patch = _parse_post_output(output)
                display.update(patch.display)
                if patch.ui_metadata:
                    ui_metadata = dict(patch.ui_metadata)
                display, ui_metadata = validate_runtime_event_presentation(display, ui_metadata)
            except (HookExecutionError, ValueError) as exc:
                code = exc.code if isinstance(exc, HookExecutionError) else "HOOK_INVALID_OUTPUT"
                _log_hook_result(spec, code=code, duration_ms=_duration_ms(started), failed=True)
                continue
            _log_hook_result(spec, code="ENRICHED" if patch.display or patch.ui_metadata else "CONTINUE", duration_ms=_duration_ms(started))
        return PostToolHookResult(display=display, ui_metadata=ui_metadata)


def _pre_payload(
    spec: HookSpec,
    request: PreToolHookRequest,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "stage": "pre_tooluse",
        "hook_name": spec.name,
        "tool_name": request.tool_name,
        "arguments": arguments,
        "context": {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "request_id": request.request_id,
            "channel": request.channel,
            "actor_type": request.actor_type,
            "role_keys": list(request.role_keys),
            "permission_keys": list(request.permission_keys),
        },
    }


def _post_payload(spec: HookSpec, request: PostToolHookRequest) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "stage": "post_tooluse",
        "hook_name": spec.name,
        "tool_name": request.tool_name,
        "arguments": request.arguments,
        "result": {
            "output": request.output[:12000],
            "is_error": request.is_error,
            "metadata": {
                key: value
                for key, value in request.result_metadata.items()
                if key in _SAFE_RESULT_METADATA
            },
        },
        "context": {
            "session_id": request.session_id,
            "turn_id": request.turn_id,
            "request_id": request.request_id,
            "channel": request.channel,
            "actor_type": request.actor_type,
            "role_keys": list(request.role_keys),
            "permission_keys": list(request.permission_keys),
        },
    }


def _parse_pre_output(output: dict[str, Any]) -> PreToolHookResult:
    action = output.get("action")
    allowed_by_action = {
        "continue": {"action"},
        "modify": {"action", "arguments"},
        "block": {"action", "code", "message"},
    }
    allowed = allowed_by_action.get(action)
    if allowed is None or set(output) != allowed:
        raise ValueError("invalid pre-tool Hook result")
    if action == "continue":
        return PreToolHookResult(action="continue")
    if action == "modify":
        arguments = output.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("modified Hook arguments must be an object")
        return PreToolHookResult(action="modify", arguments=dict(arguments))
    code = output.get("code")
    message = output.get("message")
    if not isinstance(code, str) or not _HOOK_CODE_RE.fullmatch(code):
        raise ValueError("Hook block code is invalid")
    if not isinstance(message, str) or not message.strip() or len(message) > 300:
        raise ValueError("Hook block message is invalid")
    return PreToolHookResult(action="block", code=code, message=message.strip())


def _parse_post_output(output: dict[str, Any]) -> PostToolHookResult:
    action = output.get("action")
    if action == "continue" and set(output) == {"action"}:
        return PostToolHookResult()
    if action != "enrich" or not set(output).issubset({"action", "display", "ui_metadata"}):
        raise ValueError("invalid post-tool Hook result")
    if set(output) == {"action"}:
        raise ValueError("post-tool enrich result must contain a presentation patch")
    display = output.get("display", {})
    ui_metadata = output.get("ui_metadata", {})
    if not isinstance(display, dict) or not isinstance(ui_metadata, dict):
        raise ValueError("post-tool presentation fields must be objects")
    validated_display, validated_ui = validate_runtime_event_presentation(display, ui_metadata)
    return PostToolHookResult(display=validated_display, ui_metadata=validated_ui)


def _log_hook_result(
    spec: HookSpec,
    *,
    code: str,
    duration_ms: int,
    failed: bool = False,
) -> None:
    log_event(
        hook_logger,
        logging.WARNING if failed else logging.DEBUG,
        "hook.failed" if failed else "hook.done",
        hook_name=spec.name,
        stage=spec.stage,
        code=code,
        duration_ms=duration_ms,
    )


def _resolve_exemption(
    spec: HookSpec,
    role_keys: tuple[str, ...],
    permission_keys: tuple[str, ...],
) -> tuple[str, str] | None:
    exempted_role = spec.exempted_role(role_keys)
    if exempted_role:
        return "role_exempted", exempted_role
    exempted_permission = spec.exempted_permission(permission_keys)
    if exempted_permission:
        return "permission_exempted", exempted_permission
    return None


def _log_hook_skipped(spec: HookSpec, reason: str, matched_key: str) -> None:
    log_event(
        hook_logger,
        logging.DEBUG,
        "hook.skipped",
        hook_name=spec.name,
        stage=spec.stage,
        reason=reason,
        matched_key=matched_key,
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
