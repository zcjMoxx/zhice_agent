from __future__ import annotations

from agent.auth.tool_policy import RbacToolExecutionPolicy
from agent.protocols.auth import ActorContext
from agent.protocols.tool import ToolExecutionContext


def test_tool_policy_maps_permissions_and_exec_risk():
    policy = RbacToolExecutionPolicy()
    developer = _actor()
    context = _context(developer)

    assert policy.decide("read_file", {"path": "notes.txt"}, context).action == "allow"
    assert policy.decide("load_skills", {"name": "demo"}, context).action == "allow"
    assert policy.decide("sync_skills", {}, context).action == "deny"
    safe = policy.decide("exec", {"command": "python -m pytest"}, context)
    assert safe.action == "allow"
    assert safe.permission_key == ""

    network = policy.decide("exec", {"command": "pip install demo"}, context)
    assert network.action == "deny"
    assert network.permission_key == "tool.exec.dangerous"
    assert network.risk_category == "network"


def test_dangerous_exec_permission_only_enters_confirmation_and_env_dump_stays_denied():
    policy = RbacToolExecutionPolicy()
    admin = _actor("tool.exec.dangerous")
    context = _context(admin)

    network = policy.decide("exec", {"command": "pip install demo"}, context)
    env_dump = policy.decide("exec", {"command": "Get-ChildItem Env:"}, context)

    assert network.action == "confirm"
    assert network.risk_level == "high"
    assert network.risk_category == "network"
    assert env_dump.action == "deny"
    assert env_dump.code == "ENV_DUMP_BLOCKED"


def test_absolute_destructive_command_is_rejected_as_workspace_escape():
    policy = RbacToolExecutionPolicy()
    admin = _actor("tool.exec.dangerous")

    decision = policy.decide(
        "exec",
        {"command": r"Remove-Item -Recurse C:\\Users\\Public"},
        _context(admin),
    )

    assert decision.action == "deny"
    assert decision.code == "PATH_OUTSIDE_WORKSPACE"


def test_memory_tools_are_baseline_but_writes_still_require_user_authorization():
    policy = RbacToolExecutionPolicy()
    actor = _actor()

    read = policy.decide("memory_read", {"query": "style"}, _context(actor))
    write = policy.decide(
        "memory_write",
        {
            "operation": "add",
            "category": "preferences",
            "content": "Keep answers concise.",
            "authorization": "user_explicit",
        },
        _context(actor),
    )
    confirmed = policy.decide(
        "memory_write",
        {
            "operation": "add",
            "category": "preferences",
            "content": "Give the conclusion first.",
            "authorization": "user_confirmed",
        },
        _context(actor),
    )
    inferred = policy.decide(
        "memory_write",
        {
            "operation": "add",
            "category": "preferences",
            "content": "Keep answers concise.",
            "authorization": "assistant_inferred",
        },
        _context(actor),
    )
    missing_authorization = policy.decide("memory_write", {}, _context(actor))

    assert read.action == "allow"
    assert read.permission_key == ""
    assert write.action == "allow"
    assert write.permission_key == ""
    assert write.risk_category == "memory_write"
    assert confirmed.action == "allow"
    assert inferred.action == "deny"
    assert inferred.code == "MEMORY_USER_AUTHORIZATION_REQUIRED"
    assert missing_authorization.action == "deny"
    assert missing_authorization.code == "MEMORY_USER_AUTHORIZATION_REQUIRED"


def _actor(*permissions: str) -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id="user-1",
        username="tester",
        display_name="Tester",
        role_keys=frozenset({"developer"}),
        permission_keys=frozenset(permissions),
        channel="web",
        auth_session_id="auth-1",
    )


def _context(actor: ActorContext) -> ToolExecutionContext:
    return ToolExecutionContext(
        actor=actor,
        session_id="session-1",
        turn_id="turn-1",
        turn_index=1,
        channel="web",
        tool_call_id="call-1",
        tool_call_record_id="tool-record-1",
    )
