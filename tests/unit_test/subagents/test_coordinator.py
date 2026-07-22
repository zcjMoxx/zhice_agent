from __future__ import annotations

import logging
import time
from threading import Event, Lock

from agent.core.loop import CancellationToken
from agent.prompt_loader import PromptNotFoundError
from agent.protocols.auth import ActorContext
from agent.protocols.subagent import SubagentBatchRequest, SubagentProfile, SubagentTask
from agent.protocols.tool import ToolExecutionContext
from agent.subagents.config import SubagentConfig
from agent.subagents.coordinator import BoundedSubagentCoordinator
from agent.subagents.workspace import WorkspaceManager


class _SleepingChildFactory:
    def __init__(
        self,
        *,
        failing: set[str] | None = None,
        delay: float = 0.2,
        expected_concurrency: int = 0,
    ):
        self.failing = failing or set()
        self.delay = delay
        self._lock = Lock()
        self.active = 0
        self.max_active = 0
        self.expected_concurrency = expected_concurrency
        self.concurrent_started = Event()

    def run_child(
        self,
        task,
        profile,
        parent_context,
        identity,
        workspace,
        *,
        cancellation_token,
        on_event=None,
    ):
        del profile, parent_context, identity, workspace, cancellation_token, on_event
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= self.expected_concurrency > 0:
                self.concurrent_started.set()
        try:
            if self.expected_concurrency:
                assert self.concurrent_started.wait(5)
            time.sleep(self.delay)
            if task.task_id in self.failing:
                raise RuntimeError("child failed")
            return f"result:{task.task_id}"
        finally:
            with self._lock:
                self.active -= 1


def _actor() -> ActorContext:
    return ActorContext(
        actor_type="local_operator",
        user_id=None,
        username="owner",
        display_name="Owner",
        role_keys=frozenset({"owner"}),
        permission_keys=frozenset(),
        channel="cli",
    )


def _context() -> ToolExecutionContext:
    return ToolExecutionContext(
        actor=_actor(),
        session_id="parent-session",
        turn_id="parent-turn",
        turn_index=1,
        channel="cli",
        tool_name="delegate_tasks",
        root_session_id="parent-session",
        root_turn_id="parent-turn",
    )


def _config() -> SubagentConfig:
    profile = SubagentProfile(
        name="explorer",
        description="inspect",
        tools=("read_file",),
        workspace_mode="shared_readonly",
        timeout_seconds=2,
    )
    return SubagentConfig(enabled=True, max_parallel=3, profiles={profile.name: profile})


def _request(count: int = 3) -> SubagentBatchRequest:
    return SubagentBatchRequest(
        reason="parallel_independent",
        tasks=tuple(
            SubagentTask(f"task-{index}", f"inspect {index}", "explorer")
            for index in range(count)
        ),
    )


def test_coordinator_runs_three_children_in_parallel_and_preserves_input_order(tmp_path):
    child_factory = _SleepingChildFactory(expected_concurrency=3)
    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=child_factory,  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )

    results = coordinator.run_batch(_request(), _context())

    assert child_factory.max_active == 3
    assert [result.task_id for result in results] == ["task-0", "task-1", "task-2"]
    assert [result.output for result in results] == [
        "result:task-0",
        "result:task-1",
        "result:task-2",
    ]


def test_coordinator_uses_model_task_id_for_workspace_lease(tmp_path):
    class _RecordingWorkspaceManager:
        def __init__(self):
            self.delegate = WorkspaceManager(tmp_path)
            self.task_ids = []

        def acquire(self, mode, *, batch_id, task_id):
            self.task_ids.append(task_id)
            return self.delegate.acquire(mode, batch_id=batch_id, task_id=task_id)

    workspace_manager = _RecordingWorkspaceManager()
    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=_SleepingChildFactory(delay=0),  # type: ignore[arg-type]
        workspace_manager=workspace_manager,  # type: ignore[arg-type]
    )

    results = coordinator.run_batch(_request(2), _context())

    assert all(result.status == "completed" for result in results)
    assert set(workspace_manager.task_ids) == {"task-0", "task-1"}


def test_coordinator_keeps_completed_siblings_when_one_child_fails(tmp_path):
    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=_SleepingChildFactory(failing={"task-1"}),  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )

    results = coordinator.run_batch(_request(), _context())

    assert [result.status for result in results] == ["completed", "failed", "completed"]
    assert results[1].code == "SUBAGENT_INTERNAL_ERROR"
    assert results[1].stage == "runtime"


def test_coordinator_trace_keeps_safe_internal_error_message(tmp_path, caplog):
    class _TypeErrorChildFactory(_SleepingChildFactory):
        def run_child(self, *args, **kwargs):
            del args, kwargs
            raise TypeError(
                "SubagentContextBuilder.build() got an unexpected keyword argument "
                "'context_budget'; API_KEY=private-value"
            )

    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=_TypeErrorChildFactory(),  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )
    request = SubagentBatchRequest(
        reason="context_isolation",
        tasks=(SubagentTask("type-error", "inspect", "explorer"),),
    )

    with caplog.at_level(logging.WARNING, logger="zcagent.agent.subagent"):
        result = coordinator.run_batch(request, _context())[0]

    record = next(record for record in caplog.records if record.message == "subagent.task_failed")
    assert result.code == "SUBAGENT_INTERNAL_ERROR"
    assert record.fields["error_type"] == "TypeError"
    assert "unexpected keyword argument 'context_budget'" in record.fields["error_message"]
    assert "private-value" not in record.fields["error_message"]
    assert "API_KEY=<redacted>" in record.fields["error_message"]


def test_coordinator_preserves_missing_prompt_as_terminal_child_cause(tmp_path):
    class _MissingPromptChildFactory(_SleepingChildFactory):
        def run_child(self, *args, **kwargs):
            del args, kwargs
            raise PromptNotFoundError("prompt not found: subagent")

    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=_MissingPromptChildFactory(),  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )
    request = SubagentBatchRequest(
        reason="context_isolation",
        tasks=(SubagentTask("missing", "inspect", "explorer"),),
    )

    result = coordinator.run_batch(request, _context())[0]

    assert result.status == "failed"
    assert result.code == "SUBAGENT_PROMPT_NOT_FOUND"
    assert result.stage == "context"
    assert result.output == "Required Subagent runtime prompt is missing: subagent.md"


def test_parallel_reason_rejects_a_single_task_without_starting_child(tmp_path):
    child_factory = _SleepingChildFactory()
    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=child_factory,  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )
    request = SubagentBatchRequest(
        reason="parallel_independent",
        tasks=(SubagentTask("only", "inspect", "explorer"),),
    )

    result = coordinator.run_batch(request, _context())

    assert result[0].status == "failed"
    assert result[0].code == "SUBAGENT_INVALID_BATCH"


def test_max_parallel_queues_the_fourth_child(tmp_path):
    child_factory = _SleepingChildFactory(expected_concurrency=3)
    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=child_factory,  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )

    results = coordinator.run_batch(_request(4), _context())

    assert child_factory.max_active == 3
    assert all(result.status == "completed" for result in results)


def test_child_timeout_keeps_a_bounded_partial_result(tmp_path):
    config = _config()
    profile = config.get_profile("explorer")
    assert profile is not None
    timed_profile = SubagentProfile(
        name=profile.name,
        description=profile.description,
        tools=profile.tools,
        timeout_seconds=1,
    )
    coordinator = BoundedSubagentCoordinator(
        config=SubagentConfig(enabled=True, profiles={"explorer": timed_profile}),
        child_factory=_SleepingChildFactory(delay=1.3),  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
    )
    request = SubagentBatchRequest(
        reason="context_isolation",
        tasks=(SubagentTask("slow", "inspect", "explorer"),),
    )

    result = coordinator.run_batch(request, _context())

    assert result[0].status == "timed_out"
    assert result[0].code == "SUBAGENT_TIMEOUT"


def test_parent_cancellation_marks_unfinished_children_cancelled(tmp_path):
    token = CancellationToken()
    token.cancel()
    coordinator = BoundedSubagentCoordinator(
        config=_config(),
        child_factory=_SleepingChildFactory(delay=0.5),  # type: ignore[arg-type]
        workspace_manager=WorkspaceManager(tmp_path),
        parent_cancellation_token=token,
    )

    result = coordinator.run_batch(_request(2), _context())

    assert [item.status for item in result] == ["cancelled", "cancelled"]
