from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from agent.auth.store import (
    AuthSetupError,
    AuthStoreError,
    ExternalIdentityConflictError,
    NotificationEmailVerificationRateLimitError,
    SQLiteAuthStore,
)


def test_initialize_schema_adds_session_conversation_type_to_legacy_database(tmp_path):
    path = tmp_path / "auth.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE session_index (
              session_id TEXT PRIMARY KEY,
              owner_user_id TEXT NOT NULL,
              channel TEXT NOT NULL DEFAULT 'web',
              external_chat_id TEXT NOT NULL DEFAULT '',
              external_thread_id TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL DEFAULT '',
              preview TEXT NOT NULL DEFAULT '',
              message_count INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              archived_at TEXT
            )
            """
        )

    SQLiteAuthStore(path).initialize_schema()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(session_index)")}
    assert "conversation_type" in columns
    assert SQLiteAuthStore(path).registration_enabled() is False


def test_registration_policy_defaults_closed_and_persists_across_reinitialization(tmp_path):
    path = tmp_path / "auth.sqlite3"
    store = SQLiteAuthStore(path)
    owner = store.initialize_owner("owner", "Owner", "password-123")

    assert store.registration_enabled() is False
    assert store.set_registration_enabled(True, actor_user_id=owner.id) is True
    assert SQLiteAuthStore(path).registration_enabled() is True

    store.initialize_schema()

    assert store.registration_enabled() is True


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


def test_notification_email_verification_is_salted_expiring_and_single_use(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")

    challenge = store.begin_notification_email_verification(
        user.id, " Me@Example.com ", "12345678"
    )

    assert challenge["address"] == "me@example.com"
    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            "SELECT code_salt,code_hash FROM notification_email_verifications"
        ).fetchone()
    assert row is not None
    assert row[0] != "12345678"
    assert row[1] != "12345678"
    assert store.verify_notification_email(
        user.id, "me@example.com", "00000000"
    ) is False
    assert store.verify_notification_email(
        user.id, "me@example.com", "12345678"
    ) is True
    assert store.notification_email(user.id) == "me@example.com"
    assert store.notification_email_status(user.id) == {
        "address": "me@example.com",
        "status": "active",
        "verified": True,
    }
    assert store.verify_notification_email(
        user.id, "me@example.com", "12345678"
    ) is False


def test_expired_notification_email_code_does_not_activate_address(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("owner", "Owner", "password-123")
    store.begin_notification_email_verification(
        user.id,
        "me@example.com",
        "12345678",
        ttl=timedelta(seconds=-1),
    )

    assert store.verify_notification_email(
        user.id, "me@example.com", "12345678"
    ) is False
    assert store.notification_email(user.id) is None


def test_notification_email_verification_enforces_per_user_resend_cooldown(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    viewer = store.create_user("viewer", "Viewer", "password-456")

    challenge = store.begin_notification_email_verification(
        owner.id, "owner@example.com", "12345678"
    )
    assert challenge["retry_after_seconds"] == 60

    with pytest.raises(NotificationEmailVerificationRateLimitError) as exc_info:
        store.begin_notification_email_verification(
            owner.id, "other@example.com", "87654321"
        )
    assert 1 <= exc_info.value.retry_after_seconds <= 60

    other_user_challenge = store.begin_notification_email_verification(
        viewer.id, "viewer@example.com", "11223344"
    )
    assert other_user_challenge["retry_after_seconds"] == 60

    old_created_at = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE notification_email_verifications SET created_at=? WHERE user_id=?",
            (old_created_at, owner.id),
        )
    retried = store.begin_notification_email_verification(
        owner.id, "other@example.com", "87654321"
    )
    assert retried["address"] == "other@example.com"


def test_viewer_has_only_personal_workflow_privileges(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_schema()
    viewer = store.create_user("alice", "Alice", "alice-password")

    actor = store.actor_for_user(viewer.id, channel="web")

    assert actor.role_keys == frozenset({"viewer"})
    assert actor.permission_keys == frozenset(
        {
            "workflow.use",
            "workflow.schedule",
            "workflow.notify.self",
            "workflow.email.send",
        }
    )
    assert "workflow.external.action" not in actor.permission_keys
    assert "workflow.social.publish" not in actor.permission_keys


def test_admin_role_permissions_are_editable_but_owner_role_remains_protected(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_schema()

    admin = next(role for role in store.list_roles() if role["key"] == "admin")
    owner = next(role for role in store.list_roles() if role["key"] == "owner")
    updated = store.update_role_permissions(admin["id"], ["auth.roles.read", "auth.roles.manage"])

    assert updated["permission_keys"] == ["auth.roles.manage", "auth.roles.read"]
    with pytest.raises(AuthStoreError, match="owner role permissions are protected"):
        store.update_role_permissions(owner["id"], ["auth.roles.read"])


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
    assert actor.permission_keys == frozenset(
        {
            "workflow.use",
            "workflow.schedule",
            "workflow.notify.self",
            "workflow.email.send",
        }
    )


def test_one_user_can_only_have_one_active_qq_identity(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="qq-first",
    )

    with pytest.raises(ExternalIdentityConflictError) as exc_info:
        store.link_external_identity(
            user_id=user.id,
            channel="qq",
            external_tenant_id="main",
            external_user_id="qq-second",
        )

    assert exc_info.value.reason == "user_already_bound"
    binding = store.list_external_identities_for_user(user.id)[0]
    assert store.unlink_external_identity_for_user(
        identity_id=str(binding["id"]), user_id=user.id
    )
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="qq-second",
    )
    assert len(store.list_external_identities_for_user(user.id)) == 1


def test_qq_delivery_identity_is_available_only_through_server_only_lookup(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    store.link_external_identity(
        user_id=user.id,
        channel="qq",
        external_tenant_id="main",
        external_user_id="sensitive-openid",
        external_display_name="My QQ",
    )

    public_binding = store.list_external_identities_for_user(user.id)[0]
    delivery_binding = store.get_active_external_identity_for_user(
        user_id=user.id,
        channel="qq",
    )

    assert "external_user_id" not in public_binding
    assert "external_tenant_id" not in public_binding
    assert delivery_binding is not None
    assert delivery_binding["external_user_id"] == "sensitive-openid"
    assert delivery_binding["external_tenant_id"] == "main"


def test_schema_migration_keeps_newest_active_qq_identity(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP INDEX uq_external_identities_active_qq_user")
        for identity_id, external_user_id, linked_at in (
            ("external-old", "qq-old", "2026-01-01T00:00:00+00:00"),
            ("external-new", "qq-new", "2026-02-01T00:00:00+00:00"),
        ):
            connection.execute(
                """
                INSERT INTO external_identities(
                  id, user_id, channel, external_tenant_id, external_user_id,
                  external_display_name, status, linked_at, last_seen_at, metadata_json
                ) VALUES (?, ?, 'qq', 'main', ?, '', 'active', ?, ?, '{}')
                """,
                (identity_id, user.id, external_user_id, linked_at, linked_at),
            )

    store.initialize_schema()

    with sqlite3.connect(store.path) as connection:
        statuses = dict(
            connection.execute(
                "SELECT external_user_id, status FROM external_identities WHERE user_id=?",
                (user.id,),
            ).fetchall()
        )
        index = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' "
            "AND name='uq_external_identities_active_qq_user'"
        ).fetchone()
    assert statuses == {"qq-old": "disabled", "qq-new": "active"}
    assert index is not None


def test_delete_user_removes_disabled_account_relations(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("owner", "Owner", "password-123")
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

    deleted = store.delete_user(target.id, expected_username="delete-me")

    assert deleted.id == target.id
    assert store.get_user_by_username("delete-me") is None
    assert store.session_index_get("session-delete-me") is None
    assert store.resolve_external_identity(
        channel="qq", external_tenant_id="main", external_user_id="openid-delete-me"
    ) is None
