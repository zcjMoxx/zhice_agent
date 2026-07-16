from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agent.auth.store import AuthSetupError, AuthStoreError, SQLiteAuthStore


def test_init_owner_seeds_roles_permissions_and_authenticates(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")

    user = store.initialize_owner(
        username="owner",
        display_name="Local Owner",
        password="correct horse battery staple",
    )

    actor = store.authenticate("owner", "correct horse battery staple", channel="web")

    assert user.username == "owner"
    assert actor is not None
    assert actor.user_id == user.id
    assert "owner" in actor.role_keys
    assert "auth.admin.manage" in actor.permission_keys
    assert "auth.users.manage" in actor.permission_keys
    assert "tool.exec.dangerous" in actor.permission_keys
    assert store.is_initialized() is True


def test_viewer_has_no_extra_privileges(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_schema()
    viewer = store.create_user("alice", "Alice", "alice-password")

    actor = store.actor_for_user(viewer.id, channel="web")

    assert actor.role_keys == frozenset({"viewer"})
    assert actor.permission_keys == frozenset()


def test_reinitializing_schema_removes_obsolete_baseline_permissions(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_schema()
    viewer = store.create_user("alice", "Alice", "alice-password")

    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO permissions(key, description, category) VALUES (?, ?, ?)",
            ("model.switch", "obsolete baseline capability", "model"),
        )
        connection.execute(
            "INSERT INTO role_permissions(role_id, permission_key) VALUES (?, ?)",
            ("role-viewer", "model.switch"),
        )
        connection.execute(
            "INSERT INTO user_permissions(user_id, permission_key, granted_at) VALUES (?, ?, ?)",
            (viewer.id, "model.switch", "2026-07-16T00:00:00+00:00"),
        )

    assert store.actor_for_user(viewer.id, channel="web").has_permission("model.switch")

    store.initialize_schema()

    assert not store.actor_for_user(viewer.id, channel="web").has_permission("model.switch")
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT 1 FROM permissions WHERE key='model.switch'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM role_permissions WHERE permission_key='model.switch'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM user_permissions WHERE permission_key='model.switch'"
        ).fetchone() is None


def test_init_owner_allows_existing_viewer_but_refuses_second_owner(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_schema()
    store.create_user("alice", "Alice", "alice-password")
    owner = store.initialize_owner("owner", "Owner", "password-123")

    with pytest.raises(AuthSetupError, match="already"):
        store.initialize_owner("second", "Second", "password-456")

    assert owner.role_keys == ("owner",)
    assert [user.username for user in store.list_users()] == ["alice", "owner"]


def test_direct_admin_management_permission_is_owner_controlled_and_does_not_propagate(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    admin = store.create_user("alice", "Alice", "alice-password", role_keys=["admin"])
    promoted = store.create_user("bob", "Bob", "bob-password", role_keys=["admin"])

    store.set_user_permission(admin.id, "auth.admin.manage", enabled=True)

    admin_actor = store.actor_for_user(admin.id, channel="web")
    promoted_actor = store.actor_for_user(promoted.id, channel="web")
    owner_actor = store.actor_for_user(owner.id, channel="web")
    assert admin_actor.has_permission("auth.admin.manage")
    assert not promoted_actor.has_permission("auth.admin.manage")
    assert owner_actor.has_permission("auth.admin.manage")


def test_user_update_rolls_back_role_change_when_direct_permission_write_fails(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
    user = store.create_user("alice", "Alice", "alice-password", role_keys=["viewer"])

    with pytest.raises(AuthStoreError, match="unknown permission"):
        store.update_user(
            user.id,
            role_keys=["admin"],
            direct_permission=("missing.permission", True),
        )

    assert store.get_user(user.id).role_keys == ("viewer",)


def test_auth_session_stores_only_token_hash_and_can_be_revoked(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    login = store.login(
        "admin",
        "password-123",
        channel="web",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT token_hash FROM auth_sessions WHERE id = ?",
            (login.auth_session_id,),
        ).fetchone()

    assert row is not None
    assert row[0] != login.token
    assert login.token not in row[0]
    assert store.resolve_token(login.token, channel="web") is not None

    store.revoke_token(login.token)

    assert store.resolve_token(login.token, channel="web") is None


def test_change_password_revokes_all_sessions(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    current_login = store.login("admin", "password-123", channel="web")
    other_login = store.login("admin", "password-123", channel="web")

    store.change_password(
        user.id,
        "password-123",
        "password-456",
    )

    assert store.resolve_token(current_login.token, channel="web") is None
    assert store.resolve_token(other_login.token, channel="web") is None
    assert store.authenticate("admin", "password-123", channel="web") is None
    assert store.authenticate("admin", "password-456", channel="web") is not None


def test_change_password_with_wrong_current_password_preserves_credentials_and_sessions(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    current_login = store.login("admin", "password-123", channel="web")
    other_login = store.login("admin", "password-123", channel="web")

    with pytest.raises(AuthStoreError, match="invalid current password"):
        store.change_password(
            user.id,
            "wrong-password",
            "password-456",
        )

    assert store.resolve_token(current_login.token, channel="web") is not None
    assert store.resolve_token(other_login.token, channel="web") is not None
    assert store.authenticate("admin", "password-123", channel="web") is not None
    assert store.authenticate("admin", "password-456", channel="web") is None


def test_authentication_uses_uniform_failure_for_wrong_password_and_disabled_user(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    developer = store.create_user(
        username="dev",
        display_name="Developer",
        password="developer-password",
        role_keys=["developer"],
    )

    assert store.authenticate("missing", "developer-password", channel="web") is None
    assert store.authenticate("dev", "wrong-password", channel="web") is None

    store.update_user(developer.id, status="disabled")

    assert store.authenticate("dev", "developer-password", channel="web") is None


def test_external_identity_resolves_to_internal_actor(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    user = store.create_user(
        username="dev",
        display_name="Developer",
        password="developer-password",
        role_keys=["developer"],
    )
    store.link_external_identity(
        user_id=user.id,
        channel="feishu",
        external_tenant_id="tenant-a",
        external_user_id="ou_123",
        external_display_name="Feishu Dev",
    )

    actor = store.resolve_external_identity(
        channel="feishu",
        external_tenant_id="tenant-a",
        external_user_id="ou_123",
    )

    assert actor is not None
    assert actor.user_id == user.id
    assert actor.channel == "feishu"
    assert actor.role_keys == frozenset({"developer"})
    assert actor.permission_keys == frozenset()
