from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.app.auth import AuthService
from agent.app.gateway import create_app
from agent.app.runtime import ModelState
from agent.auth.audit import SqliteAuditSink
from agent.auth.store import SQLiteAuthStore
from agent.auth.user_context import FilesystemUserContextResolver
from agent.channels.config import ChannelConfiguration, QQAccountConfig, QQChannelConfig
from agent.channels.identity import ExternalIdentityService
from agent.config import AppConfig
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.auth import AuditEvent
from agent.protocols.capability import CapabilityStatus
from agent.protocols.mcp import (
    McpCatalogSnapshot,
    McpConnectionEvent,
    McpOAuthStatus,
    McpRuntimeStatsSnapshot,
    McpServerStatus,
    McpToolStats,
)
from agent.protocols.session import SessionState
from agent.protocols.tool import ToolResult


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
    owner = store.initialize_owner("admin", "Admin", "password-123")
    store.set_registration_enabled(True, actor_user_id=owner.id)
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


def test_public_registration_is_disabled_before_owner_setup(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "alice-password",
        },
    )

    assert registered.status_code == 403
    assert registered.json()["error"]["code"] == "AUTH_REGISTRATION_DISABLED"
    assert store.get_user_by_username("alice") is None


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
    owner = store.initialize_owner("admin", "Admin", "password-123")
    store.set_registration_enabled(True, actor_user_id=owner.id)
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


def test_public_username_availability_is_anonymous_and_returns_only_boolean(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("alice", "Existing Alice", "alice-password")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    available = client.get("/api/auth/username-availability", params={"username": "new-user"})
    occupied = client.get("/api/auth/username-availability", params={"username": "ALICE"})
    invalid = client.get("/api/auth/username-availability", params={"username": "bad account"})

    assert available.status_code == 200
    assert available.json() == {"available": True}
    assert occupied.json() == {"available": False}
    assert invalid.json() == {"available": False}


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
    owner = store.initialize_owner("admin", "Admin", "password-123")
    store.set_registration_enabled(True, actor_user_id=owner.id)
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))

    rejected = client.post("/api/auth/register", json=payload)

    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert len(store.list_users()) == 1


def test_owner_controls_public_registration_and_admin_cannot_override(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("alice", "Alice", "alice-password", role_keys=["admin"])
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))

    anonymous = _client(tmp_path, _AuthRuntime(auth))
    initial = anonymous.get("/api/auth/registration-policy")
    blocked = anonymous.post(
        "/api/auth/register",
        json={"username": "new-user", "password": "new-user-password"},
    )

    admin_client = _client(tmp_path, _AuthRuntime(auth))
    admin_client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "alice-password"},
    )
    admin_rejected = admin_client.patch(
        "/api/admin/auth/registration-policy",
        json={"registration_enabled": True},
    )

    owner_client = _client(tmp_path, _AuthRuntime(auth))
    owner_client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "password-123"},
    )
    owner_read = owner_client.get("/api/admin/auth/registration-policy")
    enabled = owner_client.patch(
        "/api/admin/auth/registration-policy",
        json={"registration_enabled": True},
    )
    public_enabled = anonymous.get("/api/auth/registration-policy")
    registered = anonymous.post(
        "/api/auth/register",
        json={"username": "new-user", "password": "new-user-password"},
    )

    assert initial.json() == {"registration_enabled": False}
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "AUTH_REGISTRATION_DISABLED"
    assert admin_rejected.status_code == 403
    assert admin_rejected.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"
    assert owner_read.json() == {"registration_enabled": False}
    assert enabled.json() == {"registration_enabled": True}
    assert public_enabled.json() == {"registration_enabled": True}
    assert registered.status_code == 200
    assert store.get_user_by_username("new-user") is not None
    audit = store.list_audit_events(limit=50)
    assert any(
        event["action"] == "auth.registration_failed"
        and event["reason_code"] == "AUTH_REGISTRATION_DISABLED"
        for event in audit
    )
    assert any(
        event["action"] == "auth.registration_policy_updated"
        and event["decision"] == "allow"
        and event["metadata"]["registration_enabled"] is True
        for event in audit
    )
    assert any(
        event["action"] == "auth.registration_policy_updated"
        and event["decision"] == "deny"
        for event in audit
    )
    assert owner.id


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


def test_web_authorization_rejects_second_qq_until_current_binding_is_unlinked(tmp_path):
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
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-first",
    )
    authorization = runtime.channel_identity.create_authorization_request(
        channel="qq",
        account_key="main",
        external_user_id="openid-second",
    )
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "admin", "password": "password-123"})

    response = client.post(
        "/api/channels/qq/authorize",
        json={"token": authorization.token},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CHANNEL_QQ_USER_ALREADY_BOUND"
    assert response.json()["error"]["message"] == "当前账号已经绑定其他 QQ，请先在渠道连接中解绑。"
    assert store.resolve_external_identity(
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-first",
    ).user_id == user.id
    assert store.resolve_external_identity(
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-second",
    ) is None


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

    draft_viewed = client.get("/api/models")
    viewed = client.get("/api/models?session_id=session-a")
    switched = client.post(
        "/api/model/preference",
        json={"session_id": "session-a", "model": "model-b"},
    )
    missing_session = client.post(
        "/api/model/preference",
        json={"model": "model-b"},
    )
    reset = client.delete("/api/model/preference?session_id=session-a")

    assert draft_viewed.status_code == viewed.status_code == switched.status_code == reset.status_code == 200
    assert missing_session.status_code == 400
    assert runtime.model_calls == [
        ("view", "viewer", "", ""),
        ("view", "viewer", "session-a", ""),
        ("set", "viewer", "session-a", "model-b"),
        ("reset", "viewer", "session-a", ""),
    ]


def test_admin_monitor_reports_existing_health_capability_and_activity_truth(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(owner.id, channel="web")
    store.session_index_create(
        session_id="session-a",
        owner_user_id=owner.id,
        channel="web",
    )
    store.session_index_update(
        "session-a",
        title="Diagnose model failure",
        preview="Why did the model fail?",
        message_count=2,
    )
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
    recent_turn = payload["activity"]["recent_turns"][0]
    assert recent_turn["error_code"] == "LLM_ERROR"
    assert recent_turn["request_id"] == ""
    assert recent_turn["actor_user_id"] == owner.id
    assert recent_turn["actor_username"] == "owner"
    assert recent_turn["actor_display_name"] == "Owner"
    assert recent_turn["session_title"] == "Diagnose model failure"
    assert "diagnosis" not in payload

    filtered = client.get("/api/admin/monitor?status=completed")
    assert filtered.status_code == 200
    assert filtered.json()["activity"]["recent_turns"] == []


def test_monitor_requires_turn_read_any_permission(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("viewer", "Viewer", "viewer-password")
    client = _client(tmp_path, _AuthRuntime(AuthService(store)))
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})

    response = client.get("/api/admin/monitor")

    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_permission"] == "turn.read.any"


def test_system_diagnostics_requires_explicit_permission_and_owner_can_query(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("viewer", "Viewer", "viewer-password")
    runtime = _AuthRuntime(AuthService(store))
    runtime.system_diagnostics = _FakeSystemDiagnostics()
    viewer_client = _client(tmp_path, runtime)
    viewer_client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-password"})

    denied = viewer_client.get("/api/admin/diagnostics")
    viewer_client.post("/api/auth/logout")
    viewer_client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})
    allowed = viewer_client.get("/api/admin/diagnostics?component=llm&error_code=RATE_LIMITED")

    assert denied.status_code == 403
    assert denied.json()["error"]["details"]["required_permission"] == "diagnostics.system.use"
    assert allowed.status_code == 200
    assert allowed.json()["summary"]["incidents"] == 1
    assert runtime.system_diagnostics.filters["component"] == "llm"
    assert runtime.system_diagnostics.filters["error_code"] == "RATE_LIMITED"


class _FakeSystemDiagnostics:
    def __init__(self):
        self.filters = {}

    def diagnose(self, filters):
        self.filters = filters
        return {
            "status": "ok",
            "window_minutes": 60,
            "filters": {"component": filters.get("component", "")},
            "summary": {"incidents": 1},
            "incidents": [{"incident_id": "inc-1", "code": "RATE_LIMITED"}],
            "timeline": [],
            "limitations": [],
        }


def test_audit_filter_cursor_pagination_and_csv_export_are_backward_compatible(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    actor = store.actor_for_user(owner.id, channel="web")
    sink = SqliteAuditSink(store)
    sink.record(AuditEvent(action="role.updated", resource_type="role", actor=actor, decision="allow"))
    sink.record(AuditEvent(action="user.disabled", resource_type="user", actor=actor, decision="allow"))
    sink.record(
        AuditEvent(
            action="auth.login_success",
            resource_type="auth_session",
            actor=actor,
            status_code=200,
            decision="allow",
        )
    )
    sink.record(
        AuditEvent(
            action="auth.login_failed",
            resource_type="auth_session",
            status_code=401,
            decision="deny",
        )
    )
    client = _client(tmp_path, _AuthRuntime(AuthService(store, audit_sink=sink)))
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    first = client.get("/api/audit/events?limit=1&decision=allow")
    second = client.get(
        "/api/audit/events",
        params={"limit": 1, "decision": "allow", "cursor": first.json()["next_cursor"]},
    )
    filtered = client.get("/api/audit/events?action=role.updated")
    failed_logins = client.get(
        "/api/audit/events", params={"event_type": "login", "outcome": "failure"}
    )
    exported = client.get("/api/audit/events/export?event_type=login&outcome=success")

    assert first.status_code == second.status_code == filtered.status_code == 200
    assert first.json()["has_more"] is True
    assert first.json()["events"][0]["id"] != second.json()["events"][0]["id"]
    assert [event["action"] for event in filtered.json()["events"]] == ["role.updated"]
    assert [event["action"] for event in failed_logins.json()["events"]] == [
        "auth.login_failed"
    ]
    assert exported.status_code == 200
    assert "text/csv" in exported.headers["content-type"]
    assert "zhice-security-audit.csv" in exported.headers["content-disposition"]
    assert exported.content.startswith(b"\xef\xbb\xbf")


def test_owner_can_permanently_delete_disabled_user_and_isolated_context(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    target = store.create_user("delete-me", "Delete Me", "password-456")
    store.link_external_identity(
        user_id=target.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="openid-delete-me",
    )
    store.session_index_create(
        session_id="session-delete-me", owner_user_id=target.id, channel="web"
    )
    store.update_user(target.id, status="disabled")
    runtime = _AuthRuntime(AuthService(store, audit_sink=SqliteAuditSink(store)), tmp_path)
    context = runtime.session_access.user_contexts.resolve(target.id)
    context.files_dir.joinpath("private.txt").write_text("private", encoding="utf-8")
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": owner.username, "password": "password-123"})

    response = client.request(
        "DELETE",
        f"/api/admin/users/{target.id}",
        json={"confirmation": target.username},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert [user.username for user in store.list_users()] == ["owner"]
    assert store.session_index_get("session-delete-me") is None
    assert store.resolve_external_identity(
        channel="qq", external_tenant_id="main", external_user_id="openid-delete-me"
    ) is None
    assert not context.root_dir.exists()
    assert any(event["action"] == "user.deleted" for event in store.list_audit_events(limit=20))


def test_user_deletion_requires_disabled_state_and_restores_context(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    target = store.create_user("still-active", "Still Active", "password-456")
    runtime = _AuthRuntime(AuthService(store), tmp_path)
    context = runtime.session_access.user_contexts.resolve(target.id)
    marker = context.files_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": owner.username, "password": "password-123"})

    response = client.request(
        "DELETE",
        f"/api/admin/users/{target.id}",
        json={"confirmation": target.username},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "AUTH_USER_DELETE_REQUIRES_DISABLED"
    assert store.get_user(target.id).username == target.username
    assert marker.read_text(encoding="utf-8") == "keep"


def test_user_deletion_rejects_wrong_confirmation_owner_and_bound_weixin(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    target = store.create_user("bound-user", "Bound User", "password-456")
    store.create_channel_account(
        channel="weixin",
        account_key="wx-bound-user",
        owner_user_id=target.id,
        external_account_id="wx-account-bound-user",
        external_user_id="wx-user-bound-user",
        credential_ref="channels/weixin/accounts/wx-bound-user.json",
    )
    store.update_user(target.id, status="disabled")
    client = _client(tmp_path, _AuthRuntime(AuthService(store), tmp_path))
    client.post("/api/auth/login", json={"username": owner.username, "password": "password-123"})

    wrong_confirmation = client.request(
        "DELETE",
        f"/api/admin/users/{target.id}",
        json={"confirmation": "wrong-user"},
    )
    owner_delete = client.request(
        "DELETE",
        f"/api/admin/users/{owner.id}",
        json={"confirmation": owner.username},
    )
    bound_weixin = client.request(
        "DELETE",
        f"/api/admin/users/{target.id}",
        json={"confirmation": target.username},
    )

    assert wrong_confirmation.status_code == 400
    assert wrong_confirmation.json()["error"]["code"] == "AUTH_USER_DELETE_CONFIRMATION_INVALID"
    assert owner_delete.status_code == 403
    assert owner_delete.json()["error"]["code"] == "AUTH_OWNER_ACCOUNT_PROTECTED"
    assert bound_weixin.status_code == 409
    assert bound_weixin.json()["error"]["code"] == "AUTH_USER_DELETE_CHANNELS_BOUND"
    assert store.get_user(target.id).username == target.username


def test_non_owner_admin_cannot_permanently_delete_user(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    admin = store.create_user(
        "admin-user", "Admin User", "password-456", role_keys=("admin",)
    )
    target = store.create_user("delete-me", "Delete Me", "password-789")
    store.update_user(target.id, status="disabled")
    client = _client(tmp_path, _AuthRuntime(AuthService(store), tmp_path))
    client.post(
        "/api/auth/login", json={"username": admin.username, "password": "password-456"}
    )

    response = client.request(
        "DELETE",
        f"/api/admin/users/{target.id}",
        json={"confirmation": target.username},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_PERMISSION_DENIED"
    assert store.get_user(target.id).username == target.username


def test_skill_source_admin_api_filters_fields_syncs_and_audits_safely(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    auth = AuthService(store, audit_sink=SqliteAuditSink(store))
    runtime = _AuthRuntime(auth, tmp_path)
    runtime.skill_status = _FakeSkillStatus()
    runtime.skill_loader = _FakeSkillLoader()
    runtime.skill_sync = _FakeSkillSync()
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    listed = client.get("/api/admin/skills/sources")
    synced = client.post("/api/admin/skills/sources/official/sync")
    refreshed = client.post("/api/admin/skills/sources/official/refresh-index")
    unknown = client.post("/api/admin/skills/sources/not-configured/sync")

    assert listed.status_code == 200
    assert listed.json()["sources"] == [
        {
            "source": "official",
            "enabled": True,
            "sync_enabled": True,
            "configured_target": "master",
            "current_commit": "abc123",
            "last_sync_started_at": "2026-08-09T01:00:00Z",
            "last_sync_finished_at": "2026-08-09T01:00:01Z",
            "last_success_at": "2026-08-09T01:00:01Z",
            "last_status": "up_to_date",
            "health": "healthy",
            "skill_count": 1,
            "load_error_count": 0,
            "last_error_code": "",
            "last_error_message_safe": "",
        }
    ]
    assert listed.json()["skills"][0]["qualified_name"] == "official/weather"
    serialized = listed.text
    assert "/srv/private" not in serialized
    assert "https://credential" not in serialized
    assert "raw secret stderr" not in serialized
    assert synced.json() == {"status": "synchronized", "user": None}
    assert refreshed.json() == {"status": "refreshed", "user": None}
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "SKILL_SOURCE_NOT_CONFIGURED"
    assert runtime.skill_sync.calls == [["official"]]
    assert runtime.skill_loader.invalidated == ["official", "official"]
    audit = store.list_audit_events(limit=20)
    completed = next(event for event in audit if event["action"] == "skill.source.sync_completed")
    assert completed["metadata"] == {"source": "official"}
    assert "url" not in completed["metadata"]
    assert "path" not in completed["metadata"]


def test_mcp_admin_status_aggregates_safe_runtime_monitoring(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    runtime = _AuthRuntime(AuthService(store), tmp_path)
    runtime.mcp_runtime = _FakeMcpRuntime()
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    response = client.get("/api/admin/mcp/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["reconnect_count"] == 3
    assert payload["servers"] == [
        {
            "server_id": "tavily",
            "state": "degraded",
            "tool_count": 2,
            "error_code": "MCP_TRANSPORT_ERROR",
            "call_count": 5,
            "success_count": 3,
            "failure_count": 1,
            "cancelled_count": 1,
            "last_tool_error_code": "MCP_TOOL_TIMEOUT",
            "last_connection_state": "degraded",
            "last_connection_at": 10.0,
            "last_connection_reason_code": "MCP_TRANSPORT_ERROR",
            "oauth_state": "ready",
        }
    ]
    assert "secret" not in response.text.lower()


def test_xhs_platform_login_is_owner_only_and_mcp_restart_stays_separate(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("ops-admin", "Ops Admin", "password-456", role_keys=("admin",))
    admin_role = next(role for role in store.list_roles() if role["key"] == "admin")
    store.update_role_permissions(
        admin_role["id"],
        [*admin_role["permission_keys"], "skill.sources.read"],
    )
    runtime = _AuthRuntime(AuthService(store, audit_sink=SqliteAuditSink(store)), tmp_path)
    runtime.config = _config(tmp_path)
    runtime.xhs_sidecar = _FakeXhsSupervisor()
    runtime.mcp_runtime = _FakeXhsMcpRuntime()
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    status = client.get("/api/admin/external-platforms/xhs/status")
    checked = client.post("/api/admin/external-platforms/xhs/check-login")
    persisted = client.get("/api/admin/external-platforms/xhs/status")
    started = client.post("/api/admin/external-platforms/xhs/login")
    restarted = client.post("/api/admin/mcp/xhs-readonly/restart")

    assert status.status_code == 200
    assert status.json()["platform_id"] == "xhs"
    assert "server_id" not in status.json()
    assert checked.json()["state"] == "authenticated"
    assert checked.json()["code"] == "OK"
    assert persisted.json()["state"] == "authenticated"
    assert started.json()["code"] == "XHS_LOGIN_STARTED"
    assert started.json()["login_in_progress"] is True
    assert restarted.json()["code"] == "XHS_RESTARTED"
    serialized = status.text + checked.text + started.text + restarted.text
    assert "cookies.json" not in serialized
    assert str(tmp_path) not in serialized
    assert "process" not in serialized.casefold()

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"username": "ops-admin", "password": "password-456"},
    )
    denied = client.post("/api/admin/external-platforms/xhs/login")

    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"required_role": "owner"}
    actions = {item["action"] for item in store.list_audit_events(limit=50)}
    assert {
        "external_platform.xhs.login_checked",
        "external_platform.xhs.login_started",
        "mcp.xhs.restarted",
    }.issubset(actions)


def test_hotel_browser_credentials_are_owner_only_and_never_returned(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    store.create_user("ops-admin", "Ops Admin", "password-456", role_keys=("admin",))
    admin_role = next(role for role in store.list_roles() if role["key"] == "admin")
    store.update_role_permissions(
        admin_role["id"],
        [*admin_role["permission_keys"], "skill.sources.read"],
    )
    runtime = _AuthRuntime(AuthService(store, audit_sink=SqliteAuditSink(store)), tmp_path)
    runtime.config = _config(tmp_path)
    runtime.hotel_accounts = _FakeHotelAccountSupervisor()
    client = _client(tmp_path, runtime)
    client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})

    initial = client.get("/api/admin/external-platforms/ctrip/status")
    saved = client.put(
        "/api/admin/external-platforms/ctrip/credentials",
        json={"username": "traveller@example.com", "password": "secret-password"},
    )
    runtime.hotel_accounts.login_in_progress = False
    runtime.hotel_accounts.state = "unknown"
    runtime.hotel_accounts.code = "HOTEL_AUTH_NOT_CHECKED"
    checked = client.post("/api/admin/external-platforms/ctrip/check-login")
    started = client.post("/api/admin/external-platforms/ctrip/login")
    deleted = client.delete("/api/admin/external-platforms/ctrip/credentials")

    assert initial.status_code == 200
    assert initial.json()["platform_id"] == "ctrip"
    assert "server_id" not in initial.json()
    assert saved.json()["credential_configured"] is True
    assert saved.json()["account_hint"] == "tr***@example.com"
    assert checked.json()["state"] == "authenticated"
    assert checked.json()["code"] == "OK"
    assert started.json()["login_in_progress"] is True
    assert deleted.json()["credential_configured"] is False
    serialized = initial.text + saved.text + checked.text + started.text + deleted.text
    assert "traveller@example.com" not in serialized
    assert "secret-password" not in serialized
    assert str(tmp_path) not in serialized

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/login",
        json={"username": "ops-admin", "password": "password-456"},
    )
    denied = client.put(
        "/api/admin/external-platforms/ctrip/credentials",
        json={"username": "other", "password": "another-secret"},
    )
    check_denied = client.post("/api/admin/external-platforms/ctrip/check-login")

    assert denied.status_code == 403
    assert denied.json()["error"]["details"] == {"required_role": "owner"}
    assert check_denied.status_code == 403
    actions = {item["action"] for item in store.list_audit_events(limit=50)}
    assert {
        "external_platform.ctrip.credentials_saved",
        "external_platform.ctrip.login_checked",
        "external_platform.ctrip.login_started",
        "external_platform.ctrip.credentials_deleted",
    }.issubset(actions)


class _FakeMcpRuntime:
    def snapshot(self):
        return McpCatalogSnapshot(
            servers=(McpServerStatus("tavily", "degraded", 2, "MCP_TRANSPORT_ERROR"),),
            version=4,
            generated_at=9.0,
        )

    def stats_snapshot(self):
        return McpRuntimeStatsSnapshot(
            active_calls=1,
            reconnect_count=3,
            connection_history=(McpConnectionEvent("tavily", "degraded", 10.0, "MCP_TRANSPORT_ERROR"),),
            tools=(McpToolStats("tavily", "search", 5, 3, 1, 1, last_error_code="MCP_TOOL_TIMEOUT"),),
            oauth=(McpOAuthStatus("tavily", "ready"),),
        )


class _FakeXhsTool:
    name = "mcp__xhs-readonly__check_login_status"

    def execute(self, args):
        assert args == {}
        return ToolResult(
            output=(
                '{"status":"success","code":"OK"}\n\n'
                '{"status":"success","code":"OK"}'
            )
        )


class _FakeXhsMcpRuntime:
    def tools_for_actor(self, actor, files_dir):
        assert actor.username == "owner"
        assert files_dir
        return [_FakeXhsTool()]


class _FakeXhsSupervisor:
    def __init__(self):
        self.login_in_progress = False
        self.state = "unknown"
        self.code = "XHS_AUTH_NOT_CHECKED"
        self.message = "not checked"

    def admin_snapshot(self):
        return {
            "enabled": True,
            "login_supported": True,
            "login_in_progress": self.login_in_progress,
            "restart_supported": True,
            "cookie_updated_at": "2026-08-14T04:42:18+00:00",
            "state": self.state,
            "code": self.code,
            "message": self.message,
        }

    def record_login_status(self, state, code, message):
        self.state = state
        self.code = code
        self.message = message

    def start_login(self):
        self.login_in_progress = True
        self.state = "login_pending"
        self.code = "XHS_LOGIN_STARTED"
        return "XHS_LOGIN_STARTED"

    def restart(self):
        self.state = "unknown"
        self.code = "XHS_AUTH_RECHECK_PENDING"
        return "XHS_RESTARTED"


class _FakeHotelAccountSupervisor:
    def __init__(self):
        self.configured = False
        self.hint = ""
        self.login_in_progress = False
        self.state = "not_configured"
        self.code = "HOTEL_CREDENTIALS_NOT_CONFIGURED"

    def admin_snapshot(self):
        return {
            "provider": "ctrip",
            "state": (
                "login_pending"
                if self.login_in_progress
                else self.state
            ),
            "code": (
                "HOTEL_LOGIN_STARTED"
                if self.login_in_progress
                else self.code
            ),
            "message": "safe status",
            "credential_store_supported": True,
            "credential_configured": self.configured,
            "account_hint": self.hint,
            "credentials_updated_at": "2026-08-14T05:00:00+00:00" if self.configured else "",
            "browser_supported": True,
            "check_in_progress": False,
            "login_supported": True,
            "login_in_progress": self.login_in_progress,
            "login_mode": "password_with_manual_verification_fallback",
            "last_checked_at": "",
        }

    def save_credentials(self, username, password):
        assert username == "traveller@example.com"
        assert password == "secret-password"
        self.configured = True
        self.hint = "tr***@example.com"
        self.state = "unknown"
        self.code = "HOTEL_AUTH_RECHECK_PENDING"
        return "HOTEL_CREDENTIALS_SAVED"

    def check_login(self):
        if not self.configured:
            return "HOTEL_CREDENTIALS_NOT_CONFIGURED"
        self.state = "authenticated"
        self.code = "OK"
        return "OK"

    def start_login(self):
        if not self.configured:
            return "HOTEL_CREDENTIALS_NOT_CONFIGURED"
        self.login_in_progress = True
        self.state = "login_pending"
        self.code = "HOTEL_LOGIN_STARTED"
        return "HOTEL_LOGIN_STARTED"

    def delete_credentials(self):
        self.configured = False
        self.hint = ""
        self.login_in_progress = False
        self.state = "not_configured"
        self.code = "HOTEL_CREDENTIALS_NOT_CONFIGURED"
        return "HOTEL_CREDENTIALS_DELETED"


def test_skill_source_permissions_and_owner_only_operations_projection(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    admin = store.create_user("ops-admin", "Ops Admin", "password-456", role_keys=("admin",))
    viewer = store.create_user("viewer", "Viewer", "password-789")
    admin_role = next(role for role in store.list_roles() if role["key"] == "admin")
    store.update_role_permissions(
        admin_role["id"],
        [*admin_role["permission_keys"], "skill.sources.read", "skill.sync"],
    )
    runtime = _AuthRuntime(AuthService(store, audit_sink=SqliteAuditSink(store)), tmp_path)
    runtime.skill_status = _FakeSkillStatus()
    runtime.skill_loader = _FakeSkillLoader()
    runtime.skill_sync = _FakeSkillSync()
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    config_dir.joinpath("config.yml").write_text(
        """schema_version: 1
operations:
  terminal:
    enabled: true
    url: https://ops.example.test
    presentation: both
""",
        encoding="utf-8",
    )

    viewer_client = _client(tmp_path, runtime)
    viewer_client.post(
        "/api/auth/login", json={"username": viewer.username, "password": "password-789"}
    )
    viewer_sources = viewer_client.get("/api/admin/skills/sources")
    assert viewer_sources.status_code == 403
    assert viewer_sources.json()["error"]["details"] == {
        "required_permission": "skill.sources.read"
    }

    admin_client = _client(tmp_path, runtime)
    admin_client.post(
        "/api/auth/login", json={"username": admin.username, "password": "password-456"}
    )
    assert admin_client.get("/api/admin/skills/sources").status_code == 200
    assert admin_client.post("/api/admin/skills/sources/official/sync").status_code == 200
    denied_ops = admin_client.get("/api/admin/operations/terminal")
    assert denied_ops.status_code == 403
    assert denied_ops.json()["error"]["details"] == {"required_role": "owner"}

    owner_client = _client(tmp_path, runtime)
    owner_client.post("/api/auth/login", json={"username": "owner", "password": "password-123"})
    projected = owner_client.get("/api/admin/operations/terminal")
    assert projected.status_code == 200
    assert projected.json() == {
        "enabled": True,
        "configured": True,
        "url": "https://ops.example.test",
        "presentation": "both",
        "mode": "server_docker",
        "target_type": "container",
        "target_name": "zhice-agent",
    }
    assert "credential" not in projected.text.lower()
    assert "password" not in projected.text.lower()
    assert "socket" not in projected.text.lower()
    from agent.operations.runtime import OperationsRuntimeState, write_operations_runtime_state

    write_operations_runtime_state(
        tmp_path / "state",
        OperationsRuntimeState(
            mode="local_process",
            target_type="process",
            target_name="zcagent-gateway",
            url="http://127.0.0.1:17681",
            instance_id="test-owner",
            supervisor_pid=__import__("os").getpid(),
        ),
    )
    runtime_projection = owner_client.get("/api/admin/operations/terminal")
    assert runtime_projection.status_code == 200
    assert runtime_projection.json()["mode"] == "local_process"
    assert runtime_projection.json()["url"] == "http://127.0.0.1:17681"
    operations_audit = next(
        event
        for event in store.list_audit_events(limit=20)
        if event["action"] == "server.operations.entry_read"
    )
    assert operations_audit["metadata"] == {}


class _FakeSkillStatus:
    def list_statuses(self, *, skill_loader, skill_sync):
        assert skill_loader is not None
        assert skill_sync is not None
        return [
            {
                "name": "official",
                "enabled": True,
                "sync": True,
                "target": "master",
                "commit": "abc123",
                "last_sync_started_at": "2026-08-09T01:00:00Z",
                "last_sync_finished_at": "2026-08-09T01:00:01Z",
                "last_success_at": "2026-08-09T01:00:01Z",
                "status": "up_to_date",
                "health": "healthy",
                "skills": 1,
                "load_error_count": 0,
                "materialized_root": "/srv/private/extends/official",
                "git_url": "https://credential@example.test/repo.git",
                "stderr": "raw secret stderr",
            }
        ]


class _FakeSkillLoader:
    def __init__(self):
        self.invalidated = []

    def list_skills_for_actor(self, actor):
        assert actor.user_id
        return [
            SimpleNamespace(
                qualified_name="official/weather",
                source="official",
                name="weather",
                description="Weather report",
                runtime=SimpleNamespace(type="python"),
            )
        ]

    def invalidate(self, source=None):
        self.invalidated.append(source)


class _FakeSkillSync:
    def __init__(self):
        self.calls = []

    def sync(self, *, source_names=None):
        self.calls.append(source_names)
        return SimpleNamespace(errors=[])

    def load(self):
        return SimpleNamespace(), [SimpleNamespace(name="official")]


class _AuthRuntime:
    def __init__(self, auth, tmp_path: Path | None = None):
        self.auth = auth
        self.session_actors = []
        self.model_calls = []
        contexts_root = (tmp_path or Path.cwd()) / "contexts"
        self.session_access = SimpleNamespace(
            user_contexts=FilesystemUserContextResolver(contexts_root, workspace_dir=tmp_path)
        )

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
