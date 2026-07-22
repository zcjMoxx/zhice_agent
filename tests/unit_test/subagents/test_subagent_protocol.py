from agent.protocols.auth import ActorContext
from agent.protocols.subagent import (
    SubagentBatchRequest,
    SubagentTask,
    SubagentTaskResult,
)
from agent.protocols.tool import ToolExecutionContext


def test_subagent_protocol_records_parent_child_identity():
    task = SubagentTask(
        task_id="tests",
        task="Inspect the tests.",
        profile_name="explorer",
        expected_output="A short summary.",
    )
    request = SubagentBatchRequest(reason="parallel_independent", tasks=(task,))
    result = SubagentTaskResult(
        task_id="tests",
        status="completed",
        code="OK",
        output="Done",
        subagent_id="agent-child",
        child_session_id="session-child",
        child_turn_id="turn-child",
        duration_ms=12,
    )
    context = ToolExecutionContext(
        actor=ActorContext(
            actor_type="user",
            user_id="user-1",
            username="owner",
            display_name="Owner",
            role_keys=frozenset({"owner"}),
            permission_keys=frozenset(),
            channel="cli",
        ),
        session_id="session-parent",
        turn_id="turn-parent",
        turn_index=1,
        channel="cli",
        source="subagent",
        root_session_id="session-root",
        root_turn_id="turn-root",
        parent_session_id="session-parent",
        parent_turn_id="turn-parent",
        subagent_id="agent-child",
        task_id="tests",
    )

    assert request.tasks[0].profile_name == "explorer"
    assert result.status == "completed"
    assert context.root_session_id == "session-root"
    assert context.task_id == task.task_id
