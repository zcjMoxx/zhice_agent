from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.app.auth import AuthService
from agent.app.gateway import create_app
from agent.app.runtime import ModelState
from agent.auth.audit import SqliteAuditSink
from agent.auth.store import SQLiteAuthStore
from agent.channels.config import ChannelConfiguration, QQAccountConfig, QQChannelConfig
from agent.channels.identity import ExternalIdentityService
from agent.config import AppConfig
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.auth import AuditEvent
from agent.protocols.capability import CapabilityStatus
from agent.protocols.session import SessionState


def test_web_bootstrap_creates_owner_sets_cookie_and_logs_in(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store), setup_token="setup-secret")
    client = _client(tmp_path, _AuthRuntime(auth))

    bootstrapped = client.post(
        "/api/auth/bootstrap",
        json={
            "username": "spoofed-owner-name",
            "password": "password-123",
            "setup_token": "setup-secret",
            "display_name": "Spoofed Owner",
        },
    )
    me = client.get("/api/auth/me")

    assert bootstrapped.status_code == 200
    assert bootstrapped.json()["status"] == "authenticated"
    assert bootstrapped.json()["user"]["username"] == "owner"
    assert bootstrapped.json()["user"]["roles"] == ["owner"]
    assert "HttpOnly" in bootstrapped.headers["set-cookie"]
    assert me.status_code == 200
    assert me.json()["user"]["display_name"] == "owner"
    assert any(
        event["action"] == "auth.bootstrap_completed"
        and event["route"] == "/api/auth/bootstrap"
        for event in store.list_audit_events(limit=20)
    )


def test_web_bootstrap_allows_existing_viewer_and_closes_after_owner_exists(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_schema()
    store.create_user("alice", "Alice", "alice-password")
    client = _client(tmp_path, _AuthRuntime(AuthService(store, setup_token="setup-secret")))

    created = client.post(
        "/api/auth/bootstrap",
        json={"password": "password-123", "setup_token": "setup-secret"},
    )

    rejected = client.post(
        "/api/auth/bootstrap",
        json={
            "password": "password-456",
            "setup_token": "setup-secret",
        },
    )

    assert created.status_code == 200
    assert created.json()["user"]["roles"] == ["owner"]
    assert rejected.status_code == 409
    _assert_error(rejected, 409, "AUTH_OWNER_ALREADY_INITIALIZED", "Owner is already initialized")
    assert [user.username for user in store.list_users()] == ["alice", "owner"]


def test_web_owner_bootstrap_requires_deployment_setup_credential(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    disabled = _client(tmp_path, _AuthRuntime(AuthService(store)))
    disabled_response = disabled.post(
        "/api/auth/bootstrap",
        json={"setup_token": "guess", "password": "password-123"},
    )

    protected = _client(
        tmp_path,
        _AuthRuntime(AuthService(store, setup_token="real-secret")),
    )
    invalid = protected.post(
        "/api/auth/bootstrap",
        json={"setup_token": "guess", "password": "password-123"},
    )

    assert disabled_response.status_code == 503
    assert disabled_response.json()["error"]["code"] == "AUTH_OWNER_SETUP_DISABLED"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "AUTH_INVALID_SETUP_CREDENTIAL"
    assert store.has_owner() is False


@pytest.mark.parametrize(
    "payload",
    [
        {
            "password": "short",
        },
    ],
)
def test_invalid_web_bootstrap_keeps_setup_available(tmp_path, payload):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    client = _client(tmp_path, _AuthRuntime(AuthService(store, setup_token="setup-secret")))

    rejected = client.post(
        "/api/auth/bootstrap", json={**payload, "setup_token": "setup-secret"}
    )
    setup_state = client.get("/api/auth/me")
    health = client.get("/api/health")

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert setup_state.status_code == 503
    assert setup_state.json()["error"]["code"] == "AUTH_SETUP_REQUIRED"
    assert health.json()["auth_initialized"] == "false"
    assert store.get_user_by_username("owner") is None


def test_public_registration_creates_viewer_sets_cookie_and_logs_in(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    client = _client(tmp_path, _AuthRuntime(auth))

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "alice-password",
            "roles": ["admin"],
            "display_name": "Spoofed Alice",
        },
    )
    me = client.get("/api/auth/me")

    assert registered.status_code == 200
    assert registered.json()["status"] == "authenticated"
    assert registered.json()["user"]["roles"] == ["viewer"]
    assert "HttpOnly" in registered.headers["set-cookie"]
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "alice"
    assert me.json()["user"]["display_name"] == "alice"
    assert store.get_user_by_username("alice").role_keys == ("viewer",)
    assert any(
        event["action"] == "auth.user_registered"
        and event["route"] == "/api/auth/register"
        for event in store.list_audit_events(limit=20)
    )


def test_public_registration_is_allowed_before_owner_setup(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "alice-password",
        },
    )

    assert registered.status_code == 200
    assert registered.json()["user"]["roles"] == ["viewer"]
    assert store.get_user_by_username("alice") is not None


def test_owner_can_delegate_admin_management_and_delegated_admin_can_promote_without_propagating(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    first_admin = store.create_user("alice", "Alice", "alice-password", role_keys=["admin"])
    target = store.create_user("bob", "Bob", "bob-password")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    owner_client = _client(tmp_path, _AuthRuntime(auth))
    owner_client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    delegated = owner_client.patch(
        f"/api/admin/users/{first_admin.id}",
        json={"can_manage_admins": True},
    )

    admin_client = _client(tmp_path, _AuthRuntime(auth))
    admin_client.post("/api/auth/login", json={"username": "alice", "password": "alice-password"})
    promoted = admin_client.patch(
        f"/api/admin/users/{target.id}",
        json={"roles": ["admin"]},
    )
    target_actor = store.actor_for_user(target.id, channel="web")

    assert delegated.status_code == 200
    assert delegated.json()["can_manage_admins"] is True
    assert promoted.status_code == 200
    assert promoted.json()["roles"] == ["admin"]
    assert not target_actor.has_permission("auth.admin.manage")


def test_plain_admin_cannot_promote_admin_or_modify_owner(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("alice", "Alice", "alice-password", role_keys=["admin"])
    target = store.create_user("bob", "Bob", "bob-password")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))
    client.post("/api/auth/login", json={"username": "alice", "password": "alice-password"})

    promote = client.patch(f"/api/admin/users/{target.id}", json={"roles": ["admin"]})
    disable_owner = client.patch(f"/api/admin/users/{owner.id}", json={"status": "disabled"})

    assert promote.status_code == 403
    _assert_error(
        promote,
        403,
        "AUTH_ADMIN_MANAGEMENT_NOT_DELEGATED",
        "Administrator management is not delegated",
        details={"required_permission": "auth.admin.manage"},
    )
    assert disable_owner.status_code == 403
    assert disable_owner.json()["error"]["code"] == "AUTH_OWNER_ACCOUNT_PROTECTED"
    assert store.get_user(owner.id).status == "active"


def test_only_owner_can_update_administrator_role_permissions(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("alice", "Alice", "alice-password", role_keys=["admin"])
    auth = AuthService(store)
    admin_role = next(role for role in store.list_roles() if role["key"] == "admin")
    permission_keys = ["auth.roles.manage", "auth.roles.read"]

    owner_client = _client(tmp_path, _AuthRuntime(auth))
    owner_client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})
    updated = owner_client.patch(
        f"/api/admin/roles/{admin_role['id']}", json={"permission_keys": permission_keys}
    )

    admin_client = _client(tmp_path, _AuthRuntime(auth))
    admin_client.post("/api/auth/login", json={"username": "alice", "password": "alice-password"})
    rejected = admin_client.patch(
        f"/api/admin/roles/{admin_role['id']}", json={"permission_keys": ["auth.roles.read"]}
    )

    assert updated.status_code == 200
    assert updated.json()["permission_keys"] == permission_keys
    _assert_error(
        rejected,
        403,
        "AUTH_PERMISSION_DENIED",
        "Only Owner can update administrator role permissions",
        details={"required_role": "owner"},
    )


def test_public_registration_rejects_duplicate_username(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    store.create_user("alice", "Existing Alice", "alice-password")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    rejected = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "another-password",
        },
    )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "USER_USERNAME_ALREADY_EXISTS"
    assert len(store.list_users()) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "new-user", "password": "short"},
        {
            "username": "bad username",
            "password": "valid-password",
        },
    ],
)
def test_public_registration_rejects_invalid_fields(tmp_path, payload):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    rejected = client.post("/api/auth/register", json=payload)

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert len(store.list_users()) == 1


def test_admin_create_user_defaults_blank_display_name_to_username(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    created = client.post(
        "/api/admin/users",
        json={"username": "user002", "password": "user002-password", "roles": ["viewer"]},
    )

    assert created.status_code == 200
    assert created.json()["display_name"] == "user002"


def test_api_requires_login_and_login_cookie_unlocks_me_and_sessions(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    runtime = _AuthRuntime(AuthService(store))
    client = _client(tmp_path, runtime)

    unauthenticated = client.get("/api/sessions")
    malformed_unauthenticated = client.post("/api/chat", json={})
    login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password-123"},
    )
    me = client.get("/api/auth/me")
    sessions = client.get("/api/sessions")

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "AUTH_REQUIRED"
    assert malformed_unauthenticated.status_code == 401
    assert malformed_unauthenticated.json()["error"]["code"] == "AUTH_REQUIRED"
    assert login.status_code == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=lax" in login.headers["set-cookie"]
    assert me.json()["user"]["username"] == "admin"
    assert "auth.users.manage" in me.json()["permissions"]
    assert sessions.status_code == 200
    assert runtime.session_actors[-1].username == "admin"


def test_login_failure_identifies_missing_user_wrong_password_and_disabled_account(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    disabled = store.create_user("disabled", "Disabled", "password-123")
    store.update_user(disabled.id, status="disabled")
    client = _client(tmp_path, _AuthRuntime(AuthService(store, audit_sink=SqliteAuditSink(store))))

    missing = client.post(
        "/api/auth/login", json={"username": "missing", "password": "wrong"}
    )
    wrong = client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"}
    )
    disabled_login = client.post(
        "/api/auth/login", json={"username": "disabled", "password": "password-123"}
    )
    client.post(
        "/api/auth/login", json={"username": "admin", "password": "password-123"}
    )
    logout = client.post("/api/auth/logout")
    me = client.get("/api/auth/me")

    _assert_error(missing, 401, "AUTH_INVALID_CREDENTIALS", "Invalid username or password")
    _assert_error(wrong, 401, "AUTH_INVALID_CREDENTIALS", "Invalid username or password")
    _assert_error(disabled_login, 403, "AUTH_ACCOUNT_DISABLED", "Account is disabled")
    failure_reasons = {
        event["reason_code"]
        for event in store.list_audit_events(limit=20)
        if event["action"] == "auth.login_failed"
    }
    assert {"AUTH_USER_NOT_FOUND", "AUTH_INVALID_PASSWORD"} <= failure_reasons
    assert logout.status_code == 200
    assert me.status_code == 401


def test_login_with_empty_user_database_reports_missing_user_not_setup_state(tmp_path):
    client = _client(tmp_path, _AuthRuntime(AuthService(SQLiteAuthStore(tmp_path / "auth.sqlite3"))))

    response = client.post("/api/auth/login", json={"username": "missing", "password": "wrong"})

    _assert_error(response, 401, "AUTH_INVALID_CREDENTIALS", "Invalid username or password")


def test_current_user_can_update_own_display_name_and_audit_change(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    client = _client(tmp_path, _AuthRuntime(auth))
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password-123"},
    )

    updated = client.patch(
        "/api/auth/profile",
        json={"display_name": "Updated Admin"},
    )
    me = client.get("/api/auth/me")

    assert updated.status_code == 200
    assert updated.json()["user"]["display_name"] == "Updated Admin"
    assert me.json()["user"]["display_name"] == "Updated Admin"
    assert store.get_user(user.id).display_name == "Updated Admin"
    assert any(
        event["action"] == "auth.profile_updated"
        and event["resource_id"] == user.id
        and event["route"] == "/api/auth/profile"
        for event in store.list_audit_events(limit=20)
    )


def test_current_user_password_change_revokes_all_sessions_and_requires_login(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    client = _client(tmp_path, _AuthRuntime(auth))
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password-123"},
    )
    other_login = store.login("admin", "password-123", channel="web")

    changed = client.post(
        "/api/auth/password",
        json={
            "current_password": "password-123",
            "new_password": "password-456",
        },
    )

    assert changed.status_code == 200
    assert changed.json()["status"] == "reauthentication_required"
    assert "zcagent_session=\"\"" in changed.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 401
    assert store.resolve_token(other_login.token, channel="web") is None
    assert store.authenticate("admin", "password-123", channel="web") is None
    assert store.authenticate("admin", "password-456", channel="web") is not None
    assert any(
        event["action"] == "auth.password_changed"
        and event["route"] == "/api/auth/password"
        for event in store.list_audit_events(limit=20)
    )


def test_current_user_can_generate_qq_code_and_consume_web_authorization(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    runtime = _AuthRuntime(auth)
    runtime.channel_identity = ExternalIdentityService(store)
    runtime.channel_config = ChannelConfiguration(
        qq=QQChannelConfig(
            enabled=True,
            accounts=(QQAccountConfig("main", "app", "secret"),),
        )
    )
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "admin", "password": "password-123"})

    code_response = client.post("/api/channels/qq/link-code")
    authorization = runtime.channel_identity.create_authorization_request(
        channel="qq",
        account_key="main",
        external_user_id="openid-web",
    )
    bound = client.post(
        "/api/channels/qq/authorize",
        json={"token": authorization.token},
    )
    replay = client.post(
        "/api/channels/qq/authorize",
        json={"token": authorization.token},
    )

    assert code_response.status_code == 200
    assert code_response.json()["command"].startswith("/bind ")
    assert bound.status_code == 200
    assert bound.json() == {"status": "bound", "channel": "qq"}
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "CHANNEL_BIND_TOKEN_INVALID"
    assert store.resolve_external_identity(
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-web",
    ).user_id == user.id
    actions = {event["action"] for event in store.list_audit_events(limit=20)}
    assert "external_identity.link_code_created" in actions
    assert "external_identity.linked" in actions


def test_current_user_can_list_and_unlink_only_own_qq_binding(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("admin", "Admin", "password-123")
    other = store.create_user("other", "Other", "other-password")
    store.link_external_identity(
        user_id=owner.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="owner-openid",
        external_display_name="Owner QQ",
    )
    store.link_external_identity(
        user_id=other.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="other-openid",
    )
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    runtime = _AuthRuntime(auth)
    runtime.channel_identity = ExternalIdentityService(store)
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "admin", "password": "password-123"})

    listed = client.get("/api/channels/bindings")
    binding = listed.json()["bindings"][0]
    other_binding = store.list_external_identities_for_user(other.id)[0]
    denied = client.delete(f"/api/channels/bindings/{other_binding['id']}")
    removed = client.delete(f"/api/channels/bindings/{binding['binding_id']}")

    assert listed.status_code == 200
    assert binding["channel"] == "qq"
    assert binding["display_name"] == "Owner QQ"
    assert "external_user_id" not in binding
    assert denied.status_code == 404
    assert removed.status_code == 200
    assert removed.json()["status"] == "unbound"
    assert store.resolve_external_identity(
        channel="qq",
        external_tenant_id="main",
        external_user_id="owner-openid",
    ) is None
    assert store.resolve_external_identity(
        channel="qq",
        external_tenant_id="main",
        external_user_id="other-openid",
    ).user_id == other.id


def test_wrong_current_password_does_not_change_password_or_revoke_session(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))
    client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password-123"},
    )

    rejected = client.post(
        "/api/auth/password",
        json={
            "current_password": "wrong-password",
            "new_password": "password-456",
        },
    )

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "AUTH_INVALID_CURRENT_PASSWORD"
    assert client.get("/api/auth/me").status_code == 200
    assert store.authenticate("admin", "password-123", channel="web") is not None
    assert store.authenticate("admin", "password-456", channel="web") is None


def test_websocket_without_auth_is_rejected_with_policy_violation(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    with client.websocket_connect("/ws") as websocket:
        event = websocket.receive_json()
        assert event["event"] == "channel_status"
        assert event["data"]["error"]["code"] == "AUTH_REQUIRED"


def test_model_api_is_session_aware_and_permission_checked(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    store.create_user("viewer", "Viewer", "viewer-password")
    runtime = _AuthRuntime(AuthService(store))
    client = _client(tmp_path, runtime)
    client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "viewer-password"},
    )

    viewed = client.get("/api/models?session_id=session-a")
    switched = client.post(
        "/api/model/preference",
        json={"session_id": "session-a", "model": "model-b"},
    )
    reset = client.delete("/api/model/preference?session_id=session-a")

    assert viewed.status_code == switched.status_code == reset.status_code == 200
    assert runtime.model_calls == [
        ("view", "viewer", "session-a", ""),
        ("set", "viewer", "session-a", "model-b"),
        ("reset", "viewer", "session-a", ""),
    ]


def test_admin_monitor_reports_existing_health_capability_and_activity_truth(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(owner.id, channel="web")
    store.record_activity(
        RuntimeActivityEvent(
            action="chat.turn_started",
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            channel="web",
        )
    )
    store.record_activity(
        RuntimeActivityEvent(
            action="chat.turn_error",
            actor=actor,
            session_id="session-a",
            turn_id="turn-a",
            channel="web",
            reason_code="LLM_ERROR",
        )
    )
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    response = client.get("/api/admin/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["gateway"]["status"] == "ok"
    assert payload["capabilities"]["mcp"]["state"] == "disabled"
    assert payload["activity"]["summary"]["failed"] == 1
    assert payload["activity"]["recent_turns"][0]["error_code"] == "LLM_ERROR"
    assert "diagnosis" not in payload


def test_monitor_requires_turn_read_any_permission(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("viewer", "Viewer", "viewer-password")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})

    response = client.get("/api/admin/monitor")

    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "turn.read.any"


def test_audit_filter_cursor_pagination_and_csv_export_are_backward_compatible(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(owner.id, channel="web")
    sink = SqliteAuditSink(store)
    sink.record(AuditEvent(action="role.updated", resource_type="role", actor=actor, decision="allow"))
    sink.record(AuditEvent(action="user.disabled", resource_type="user", actor=actor, decision="allow"))
    client = _client(tmp_path, _AuthRuntime(AuthService(store, audit_sink=sink)))
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    first = client.get("/api/audit/events?limit=1&decision=allow")
    second = client.get(
        "/api/audit/events",
        params={"limit": 1, "decision": "allow", "cursor": first.json()["next_cursor"]},
    )
    filtered = client.get("/api/audit/events?action=role.updated")
    exported = client.get("/api/audit/events/export?decision=allow")

    assert first.status_code == second.status_code == filtered.status_code == 200
    assert first.json()["has_more"] is True
    assert first.json()["events"][0]["id"] != second.json()["events"][0]["id"]
    assert [event["action"] for event in filtered.json()["events"]] == ["role.updated"]
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "zhice-security-audit.csv" in exported.headers["content-disposition"]
    assert exported.content.startswith(b"\xef\xbb\xbf")


class _AuthRuntime:
    def __init__(self, auth):
        self.auth = auth
        self.session_actors = []
        self.model_calls = []

    def list_sessions(self, actor):
        self.session_actors.append(actor)
        return []

    def load_session(self, actor, session_id):
        return SessionState(session_id=session_id, messages=[])

    def current_model_label(self):
        return "default/model-a"

    def capability_statuses(self):
        return {
            "mcp": CapabilityStatus("mcp", "disabled", "MCP_DISABLED"),
            "context_engineering": CapabilityStatus(
                "context_engineering", "available", "CONTEXT_AVAILABLE"
            ),
        }

    def model_state(self, actor, session_id):
        self.model_calls.append(("view", actor.username, session_id, ""))
        return ModelState("default", "model-a", ["model-a", "model-b"])

    def set_model_preference(self, actor, session_id, model):
        self.model_calls.append(("set", actor.username, session_id, model))
        return ModelState("default", model, ["model-a", "model-b"])

    def reset_model_preference(self, actor, session_id):
        self.model_calls.append(("reset", actor.username, session_id, ""))
        return ModelState("default", "model-a", ["model-a", "model-b"])


def _client(tmp_path: Path, runtime: _AuthRuntime) -> TestClient:
    static_dir = tmp_path / "static"
    static_dir.mkdir(exist_ok=True)
    static_dir.joinpath("index.html").write_text("<html>ZhiCe-Agent</html>", encoding="utf-8")
    return TestClient(create_app(config=_config(tmp_path), runtime=runtime, static_dir=static_dir))


def _assert_error(response, status: int, code: str, message: str, *, details=None) -> None:
    payload = response.json()["error"]
    assert response.status_code == status
    assert payload["status"] == status
    assert payload["code"] == code
    assert payload["message"] == message
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert payload["details"] == (details or {})


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        workspace=tmp_path,
        config_dir=tmp_path / "config",
        prompts_dir=tmp_path / "prompts",
        contexts_dir=tmp_path / "contexts",
        sessions_dir=tmp_path / "contexts" / "sessions",
        extends_dir=tmp_path / "extends",
        logs_dir=tmp_path / "logs",
    )
