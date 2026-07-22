"""Bounded synchronous fan-out/fan-in Subagent coordination."""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any

from agent.core.loop import CancellationToken, TurnCancelledError
from agent.logging_utils import log_event, preview_text
from agent.prompt_loader import PromptNotFoundError
from agent.protocols.activity import RuntimeActivityEvent, RuntimeActivitySink
from agent.protocols.auth import AuditEvent, AuditSink
from agent.protocols.llm import LLMConfigurationError, LLMProviderError
from agent.protocols.skill import SkillError
from agent.protocols.subagent import (
    SubagentBatchRequest,
    SubagentProfile,
    SubagentTask,
    SubagentTaskResult,
)
from agent.protocols.tool import ToolExecutionContext
from agent.subagents.config import SubagentConfig
from agent.subagents.factory import ChildAgentFactory, ChildRunIdentity
from agent.subagents.workspace import WorkspaceIsolationError, WorkspaceManager

subagent_logger = logging.getLogger("zcagent.agent.subagent")


@dataclass
class _PendingChild:
    task: SubagentTask
    profile: SubagentProfile
    identity: ChildRunIdentity
    token: CancellationToken
    future: Future[SubagentTaskResult] | None = None
    submitted_at: float = 0.0


class BoundedSubagentCoordinator:
    """Run a single bounded child batch while preserving partial results."""

    def __init__(
        self,
        *,
        config: SubagentConfig,
        child_factory: ChildAgentFactory,
        workspace_manager: WorkspaceManager,
        parent_cancellation_token: CancellationToken | None = None,
        activity_sink: RuntimeActivitySink | None = None,
        audit_sink: AuditSink | None = None,
        on_event=None,
    ):
        self.config = config
        self.child_factory = child_factory
        self.workspace_manager = workspace_manager
        self.parent_cancellation_token = parent_cancellation_token
        self.activity_sink = activity_sink
        self.audit_sink = audit_sink
        self.on_event = on_event
        self._batch_count = 0
        self._child_count = 0

    def run_batch(
        self,
        request: SubagentBatchRequest,
        context: ToolExecutionContext,
    ) -> tuple[SubagentTaskResult, ...]:
        """Validate, run children concurrently, and return input-ordered results."""

        validation = self._validate_request(request, context)
        if validation is not None:
            return validation
        self._batch_count += 1
        self._child_count += len(request.tasks)
        batch_id = "batch-" + uuid.uuid4().hex[:16]
        batch_started = time.perf_counter()
        self._record(
            "subagent.batch_started",
            context,
            resource_id=batch_id,
            metadata={"task_count": len(request.tasks), "delegation_reason": request.reason},
        )
        pending: list[_PendingChild] = []
        for task in request.tasks:
            profile = self.config.get_profile(task.profile_name)
            assert profile is not None
            subagent_id = "subagent-" + uuid.uuid4().hex[:16]
            pending.append(
                _PendingChild(
                    task=task,
                    profile=profile,
                    identity=ChildRunIdentity(
                        batch_id=batch_id,
                        task_id=task.task_id,
                        subagent_id=subagent_id,
                        child_session_id="child-" + uuid.uuid4().hex,
                        child_turn_id="turn-" + uuid.uuid4().hex,
                    ),
                    token=CancellationToken(),
                )
            )

        executor = ThreadPoolExecutor(
            max_workers=min(self.config.max_parallel, len(pending)),
            thread_name_prefix="zcagent-subagent",
        )
        future_to_child: dict[Future[SubagentTaskResult], _PendingChild] = {}
        for child in pending:
            child.submitted_at = time.perf_counter()
            child.future = executor.submit(self._run_child, child, context)
            future_to_child[child.future] = child

        results: dict[str, SubagentTaskResult] = {}
        batch_timeout = max(child.profile.timeout_seconds for child in pending)
        batch_deadline = time.perf_counter() + batch_timeout
        unfinished = set(future_to_child)
        try:
            while unfinished:
                if self._parent_cancelled():
                    self._cancel_children(pending)
                    for future in tuple(unfinished):
                        child = future_to_child[future]
                        future.cancel()
                        results[child.task.task_id] = self._failed_result(
                            child,
                            "SUBAGENT_CANCELLED",
                            "Subagent was cancelled with the parent turn.",
                            status="cancelled",
                            stage="cancelled",
                        )
                        unfinished.remove(future)
                    break
                now = time.perf_counter()
                for future in tuple(unfinished):
                    child = future_to_child[future]
                    if now - child.submitted_at < child.profile.timeout_seconds:
                        continue
                    child.token.cancel()
                    future.cancel()
                    results[child.task.task_id] = self._failed_result(
                        child,
                        "SUBAGENT_TIMEOUT",
                        "Subagent timed out before producing a complete result.",
                        status="timed_out",
                        stage="timeout",
                    )
                    unfinished.remove(future)
                if not unfinished:
                    break
                if now >= batch_deadline:
                    for future in tuple(unfinished):
                        child = future_to_child[future]
                        child.token.cancel()
                        future.cancel()
                        results[child.task.task_id] = self._failed_result(
                            child,
                            "SUBAGENT_TIMEOUT",
                            "Subagent batch deadline was reached.",
                            status="timed_out",
                            stage="timeout",
                        )
                        unfinished.remove(future)
                    break
                done, _ = wait(
                    unfinished,
                    timeout=min(0.05, max(0.0, batch_deadline - now)),
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    unfinished.remove(future)
                    child = future_to_child[future]
                    try:
                        results[child.task.task_id] = future.result()
                    except Exception as exc:  # noqa: BLE001 - child failures are isolated.
                        code, stage, message = _classify_child_exception(exc)
                        results[child.task.task_id] = self._failed_result(
                            child,
                            code,
                            message,
                            stage=stage,
                        )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        ordered = tuple(results[task.task_id] for task in request.tasks)
        ordered = _bound_batch_results(ordered, self.config.max_batch_result_chars)
        status = _batch_status(ordered)
        self._record(
            f"subagent.batch_{status}",
            context,
            resource_id=batch_id,
            decision=status,
            metadata={
                "task_count": len(ordered),
                "completed_count": sum(item.status == "completed" for item in ordered),
                "duration_ms": _duration_ms(batch_started),
            },
        )
        return ordered

    def _run_child(
        self,
        child: _PendingChild,
        context: ToolExecutionContext,
    ) -> SubagentTaskResult:
        started = time.perf_counter()
        error_type = ""
        error_message = ""
        if self._parent_cancelled():
            child.token.cancel()
        self._record(
            "subagent.task_started",
            context,
            resource_id=child.identity.subagent_id,
            metadata={"task_id": child.task.task_id, "profile": child.profile.name},
        )
        try:
            with self.workspace_manager.acquire(
                child.profile.workspace_mode,
                batch_id=child.identity.batch_id,
                task_id=child.task.task_id,
            ) as lease:
                output = self.child_factory.run_child(
                    child.task,
                    child.profile,
                    context,
                    child.identity,
                    lease.workspace,
                    cancellation_token=child.token,
                    on_event=self.on_event,
                )
                summary = lease.summarize_changes()
                if summary.changed_files:
                    output = _append_change_summary(
                        output,
                        summary.changed_files,
                        summary.diff_summary,
                        lease.worktree_id or "",
                    )
            bounded, truncated = _truncate(output, child.profile.max_result_chars)
            result = SubagentTaskResult(
                task_id=child.task.task_id,
                status="completed",
                code="OK",
                output=bounded,
                subagent_id=child.identity.subagent_id,
                child_session_id=child.identity.child_session_id,
                child_turn_id=child.identity.child_turn_id,
                duration_ms=_duration_ms(started),
                truncated=truncated,
                stage="completed",
            )
        except TurnCancelledError:
            error_type = "TurnCancelledError"
            error_message = "Subagent was cancelled."
            result = self._failed_result(
                child,
                "SUBAGENT_CANCELLED",
                "Subagent was cancelled.",
                status="cancelled",
                started=started,
                stage="cancelled",
            )
        except WorkspaceIsolationError as exc:
            error_type = type(exc).__name__
            error_message = preview_text(exc, limit=500)
            result = self._failed_result(
                child,
                "SUBAGENT_WORKTREE_FAILED"
                if child.profile.workspace_mode == "worktree"
                else "SUBAGENT_WORKSPACE_BUSY",
                str(exc),
                started=started,
                stage="workspace",
            )
        except Exception as exc:  # noqa: BLE001 - sibling children must keep running.
            error_type = type(exc).__name__
            error_message = preview_text(exc, limit=500)
            code, stage, message = _classify_child_exception(exc)
            result = self._failed_result(
                child,
                code,
                message,
                started=started,
                stage=stage,
            )
        log_event(
            subagent_logger,
            logging.INFO if result.status == "completed" else logging.WARNING,
            f"subagent.task_{result.status}",
            actor_user_id=context.actor.user_id or "",
            actor_username=context.actor.username,
            session_id=child.identity.child_session_id,
            turn_id=child.identity.child_turn_id,
            root_session_id=context.root_session_id,
            root_turn_id=context.root_turn_id,
            parent_session_id=context.session_id,
            parent_turn_id=context.turn_id,
            batch_id=child.identity.batch_id,
            task_id=child.task.task_id,
            subagent_id=child.identity.subagent_id,
            profile=child.profile.name,
            workspace_mode=child.profile.workspace_mode,
            status=result.status,
            stage=result.stage,
            code=result.code,
            error_type=error_type,
            error_message=error_message,
            duration_ms=result.duration_ms,
        )
        self._record(
            f"subagent.task_{result.status}",
            context,
            resource_id=child.identity.subagent_id,
            decision=result.status,
            reason_code=result.code,
            metadata={
                "task_id": child.task.task_id,
                "profile": child.profile.name,
                "duration_ms": result.duration_ms,
                "stage": result.stage,
            },
        )
        return result

    def _validate_request(
        self,
        request: SubagentBatchRequest,
        context: ToolExecutionContext,
    ) -> tuple[SubagentTaskResult, ...] | None:
        code = ""
        message = ""
        if not self.config.enabled:
            code, message = "SUBAGENT_PROFILE_DISABLED", "Subagent runtime is disabled."
        elif context.subagent_id or (context.root_turn_id and context.root_turn_id != context.turn_id):
            code, message = "SUBAGENT_DEPTH_EXCEEDED", "Child Agents cannot delegate another batch."
        elif self._batch_count >= self.config.max_batches_per_parent_turn:
            code, message = "SUBAGENT_LIMIT_REACHED", "Subagent batch limit reached for this turn."
        elif not request.tasks or len(request.tasks) > self.config.max_tasks_per_call:
            code, message = "SUBAGENT_INVALID_BATCH", "Subagent task count is outside the allowed range."
        elif request.reason == "parallel_independent" and len(request.tasks) < 2:
            code, message = "SUBAGENT_INVALID_BATCH", "Parallel delegation requires at least two tasks."
        elif self._child_count + len(request.tasks) > self.config.max_subagents_per_parent_turn:
            code, message = "SUBAGENT_LIMIT_REACHED", "Subagent child limit reached for this turn."
        elif len({task.task_id for task in request.tasks}) != len(request.tasks):
            code, message = "SUBAGENT_INVALID_TASK", "Subagent task ids must be unique."
        else:
            for task in request.tasks:
                profile = self.config.get_profile(task.profile_name)
                if profile is None:
                    code, message = "SUBAGENT_UNKNOWN_PROFILE", "Unknown Subagent Profile."
                    break
                if not profile.allow_model_invocation:
                    code, message = "SUBAGENT_PROFILE_DISABLED", "Subagent Profile is disabled."
                    break
        if not code:
            return None
        if self.audit_sink is not None:
            try:
                self.audit_sink.record(
                    AuditEvent(
                        action="subagent.request_denied",
                        resource_type="subagent_batch",
                        actor=context.actor,
                        resource_id=context.tool_call_id,
                        request_id=context.request_id,
                        channel=context.channel,
                        session_id=context.session_id,
                        turn_id=context.turn_id,
                        tool_call_record_id=context.tool_call_record_id,
                        decision="deny",
                        reason_code=code,
                        risk_category="subagent_capability",
                        metadata={"task_count": len(request.tasks)},
                    )
                )
            except Exception:  # noqa: BLE001 - audit remains best effort.
                pass
        return tuple(
            SubagentTaskResult(
                task_id=task.task_id,
                status="failed",
                code=code,
                output=message,
                subagent_id="",
                child_session_id="",
                child_turn_id="",
                duration_ms=0,
                stage="validation",
            )
            for task in request.tasks
        )

    def _failed_result(
        self,
        child: _PendingChild,
        code: str,
        output: str,
        *,
        status: str = "failed",
        started: float | None = None,
        stage: str = "runtime",
    ) -> SubagentTaskResult:
        bounded, truncated = _truncate(output, child.profile.max_result_chars)
        return SubagentTaskResult(
            task_id=child.task.task_id,
            status=status,  # type: ignore[arg-type]
            code=code,
            output=bounded,
            subagent_id=child.identity.subagent_id,
            child_session_id=child.identity.child_session_id,
            child_turn_id=child.identity.child_turn_id,
            duration_ms=_duration_ms(started) if started is not None else 0,
            truncated=truncated,
            stage=stage,
        )

    def _parent_cancelled(self) -> bool:
        return bool(
            self.parent_cancellation_token is not None
            and self.parent_cancellation_token.is_cancelled()
        )

    @staticmethod
    def _cancel_children(children: list[_PendingChild]) -> None:
        for child in children:
            child.token.cancel()

    def _record(
        self,
        action: str,
        context: ToolExecutionContext,
        *,
        resource_id: str,
        decision: str = "",
        reason_code: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.activity_sink is None:
            return
        try:
            self.activity_sink.record(
                RuntimeActivityEvent(
                    action=action,
                    actor=context.actor,
                    resource_id=resource_id,
                    request_id=context.request_id,
                    channel=context.channel,
                    session_id=context.session_id,
                    turn_id=context.turn_id,
                    decision=decision,
                    reason_code=reason_code,
                    metadata=dict(metadata or {}),
                )
            )
        except Exception:  # noqa: BLE001 - activity is best effort.
            return


def _duration_ms(started: float | None) -> int:
    if started is None:
        return 0
    return max(0, int((time.perf_counter() - started) * 1000))


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    text = str(value or "")
    if len(text) <= limit:
        return text, False
    return text[:limit] + "[truncated]", True


def _classify_child_exception(exc: Exception) -> tuple[str, str, str]:
    if isinstance(exc, PromptNotFoundError):
        prompt_name = str(exc).partition(":")[2].strip() or "unknown"
        filename = prompt_name if prompt_name.endswith(".md") else f"{prompt_name}.md"
        return (
            "SUBAGENT_PROMPT_NOT_FOUND",
            "context",
            f"Required Subagent runtime prompt is missing: {filename}",
        )
    if isinstance(exc, LLMConfigurationError):
        return "SUBAGENT_LLM_CONFIG_FAILED", "llm", str(exc)[:500]
    if isinstance(exc, LLMProviderError):
        return "SUBAGENT_LLM_FAILED", "llm", "Subagent model request failed."
    if isinstance(exc, SkillError):
        return "SUBAGENT_SKILL_NOT_ALLOWED", "skill", exc.output[:500]
    if isinstance(exc, ValueError) and str(exc):
        return "SUBAGENT_CONTEXT_FAILED", "context", str(exc)[:500]
    return "SUBAGENT_INTERNAL_ERROR", "runtime", "Subagent execution failed."


def _append_change_summary(
    output: str,
    changed_files: tuple[str, ...],
    diff_summary: str,
    worktree_id: str,
) -> str:
    files = ", ".join(changed_files)
    suffix = f"\n\nWorktree {worktree_id or 'changes'}: {files}"
    if diff_summary:
        suffix += f"\n{diff_summary}"
    return str(output or "") + suffix


def _batch_status(results: tuple[SubagentTaskResult, ...]) -> str:
    completed = sum(result.status == "completed" for result in results)
    if completed == len(results):
        return "completed"
    if completed:
        return "partial"
    if results and all(result.status == "cancelled" for result in results):
        return "cancelled"
    return "failed"


def _bound_batch_results(
    results: tuple[SubagentTaskResult, ...],
    limit: int,
) -> tuple[SubagentTaskResult, ...]:
    remaining = limit
    bounded: list[SubagentTaskResult] = []
    for result in results:
        output, truncated = _truncate(result.output, max(0, remaining))
        remaining = max(0, remaining - len(output))
        bounded.append(
            SubagentTaskResult(
                task_id=result.task_id,
                status=result.status,
                code=result.code,
                output=output,
                subagent_id=result.subagent_id,
                child_session_id=result.child_session_id,
                child_turn_id=result.child_turn_id,
                duration_ms=result.duration_ms,
                truncated=result.truncated or truncated,
                stage=result.stage,
            )
        )
    return tuple(bounded)
