from __future__ import annotations

from threading import Event, Thread
from types import SimpleNamespace

import pytest

from agent.app.instance_lock import WorkspaceGatewayLock, WorkspaceGatewayLockError
from agent.app.runtime import ActiveTurn, WebRuntime
from agent.config import AppConfig
from agent.core.loop import CancellationToken
from agent.protocols.auth import ActorContext


def test_workspace_gateway_lock_rejects_second_writer_and_releases(tmp_path):
    first = WorkspaceGatewayLock(tmp_path)
    second = WorkspaceGatewayLock(tmp_path)

    first.acquire()
    with pytest.raises(WorkspaceGatewayLockError, match="active Gateway"):
        second.acquire()
    first.release()

    second.acquire()
    second.release()


def test_runtime_startup_recovers_interrupted_turns(tmp_path):
    store = _RecoveryStore()
    runtime = _runtime(tmp_path)
    runtime.auth = SimpleNamespace(store=store)

    assert runtime.startup() == 2
    assert store.calls == 1


def test_shutdown_rejects_new_turns_and_cancels_active_and_mcp(tmp_path):
    runtime = _runtime(tmp_path)
    actor = _actor()
    token = CancellationToken()
    runtime._register_turn((actor.user_id or "", "session-a"), ActiveTurn("turn-a", token))
    mcp = _McpRuntime()
    runtime.mcp_runtime = mcp

    runtime.shutdown()

    assert token.is_cancelled() is True
    assert mcp.cancel_calls == [(None, None)]
    assert mcp.closed is True
    with pytest.raises(RuntimeError, match="not accepting new turns"):
        runtime.run_chat_events(actor, "session-b", "hello")


def test_cancel_session_propagates_actor_and_session_to_mcp(tmp_path):
    runtime = _runtime(tmp_path)
    actor = _actor()
    token = CancellationToken()
    runtime.mcp_runtime = _McpRuntime()
    runtime._register_turn((actor.user_id or "", "session-a"), ActiveTurn("turn-a", token))

    result = runtime.cancel_session(actor, "session-a")

    assert result["cancelled"] == 1
    assert runtime.mcp_runtime.cancel_calls == [(actor.user_id, "session-a")]


def test_delete_waits_for_active_turn_before_removing_session(tmp_path):
    runtime = _runtime(tmp_path)
    actor = _actor()
    active = ActiveTurn("turn-a", CancellationToken())
    runtime._register_turn((actor.user_id or "", "session-a"), active)
    access = _SessionAccess()
    runtime.session_access = access
    cancel_seen = Event()
    cancel = runtime.cancel_session

    def cancel_and_signal(*args, **kwargs):
        result = cancel(*args, **kwargs)
        cancel_seen.set()
        return result

    runtime.cancel_session = cancel_and_signal
    worker = Thread(target=runtime.delete_session, args=(actor, "session-a"))
    worker.start()

    assert cancel_seen.wait(1)
    assert active.token.is_cancelled() is True
    assert access.deleted is False
    assert worker.is_alive() is True

    runtime._unregister_turn((actor.user_id or "", "session-a"), "turn-a")
    worker.join(1)

    assert worker.is_alive() is False
    assert access.deleted is True


def _runtime(tmp_path) -> WebRuntime:
    return WebRuntime(
        config=AppConfig(
            workspace=tmp_path,
            config_dir=tmp_path / "config",
            prompts_dir=tmp_path / "prompts",
            contexts_dir=tmp_path / "contexts",
            sessions_dir=tmp_path / "contexts" / "sessions",
            extends_dir=tmp_path / "extends",
            logs_dir=tmp_path / "logs",
        ),
        sessions=SimpleNamespace(),
        agent_loop=SimpleNamespace(),
        llm=SimpleNamespace(),
    )


def _actor() -> ActorContext:
    return ActorContext(
        actor_type="user",
        user_id="user-a",
        username="user-a",
        display_name="User A",
        role_keys=frozenset({"viewer"}),
        permission_keys=frozenset(),
        channel="web",
    )


class _RecoveryStore:
    def __init__(self):
        self.calls = 0

    def recover_interrupted_turn_runs(self):
        self.calls += 1
        return 2


class _McpRuntime:
    def __init__(self):
        self.cancel_calls = []
        self.closed = False

    def cancel_active_calls(self, server_id=None, *, user_id=None, session_id=None):
        del server_id
        self.cancel_calls.append((user_id, session_id))
        return 1

    def close(self):
        self.closed = True


class _SessionAccess:
    def __init__(self):
        self.deleted = False
        self.resolved = SimpleNamespace(store=SimpleNamespace())

    def resolve_session(self, _actor, _session_id, **_kwargs):
        return self.resolved

    def delete_session(self, _actor, _session_id):
        self.deleted = True
