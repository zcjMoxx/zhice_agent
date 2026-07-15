from __future__ import annotations

from dataclasses import replace

import pytest

from agent.auth.session_access import SessionAccessError, SessionAccessService
from agent.auth.store import SQLiteAuthStore
from agent.auth.user_context import FilesystemUserContextResolver
from agent.message import Message
from agent.session.jsonl_store import JsonlSessionStore


def test_user_context_and_session_index_isolate_users(tmp_path):
    store = SQLiteAuthStore(tmp_path / "state" / "auth.sqlite3")
    admin = store.initialize_owner("admin", "Admin", "password-123")
    developer = store.create_user(
        username="dev",
        display_name="Developer",
        password="developer-password",
        role_keys=["developer"],
    )
    resolver = FilesystemUserContextResolver(tmp_path / "contexts")
    service = SessionAccessService(store, resolver)
    admin_actor = store.actor_for_user(admin.id, channel="web")
    developer_actor = store.actor_for_user(developer.id, channel="web")

    resolved = service.ensure_session(developer_actor, "session-dev", channel="web")
    resolved.store.append("session-dev", [Message(role="user", content="private")])
    service.refresh_index(developer_actor, "session-dev")

    assert resolved.context.files_dir.is_dir()
    assert resolved.context.sessions_dir.is_dir()
    assert resolved.context.sessions_meta_dir.is_dir()
    assert resolver.shared_readonly_dir.is_dir()
    assert [item.session_id for item in service.list_sessions(developer_actor)] == ["session-dev"]
    assert service.list_sessions(admin_actor) == []

    other = store.create_user(
        username="viewer",
        display_name="Viewer",
        password="viewer-password",
        role_keys=["viewer"],
    )
    other_actor = store.actor_for_user(other.id, channel="web")
    with pytest.raises(SessionAccessError) as exc_info:
        service.load_session(other_actor, "session-dev")
    assert exc_info.value.code == "SESSION_NOT_FOUND"


def test_session_id_is_globally_owned_and_owner_mutations_are_checked(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    store.initialize_owner("admin", "Admin", "password-123")
    first = store.create_user("first", "First", "first-password", role_keys=["developer"])
    second = store.create_user("second", "Second", "second-password", role_keys=["developer"])
    service = SessionAccessService(store, FilesystemUserContextResolver(tmp_path / "contexts"))
    first_actor = store.actor_for_user(first.id, channel="web")
    second_actor = store.actor_for_user(second.id, channel="web")
    service.ensure_session(first_actor, "shared-name", channel="web")

    with pytest.raises(SessionAccessError) as create_error:
        service.ensure_session(second_actor, "shared-name", channel="web")
    assert create_error.value.code == "SESSION_NOT_FOUND"

    with pytest.raises(SessionAccessError) as delete_error:
        service.delete_session(second_actor, "shared-name")
    assert delete_error.value.code == "SESSION_NOT_FOUND"


def test_ensure_session_reports_creation_only_for_the_first_resolution(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    service = SessionAccessService(store, FilesystemUserContextResolver(tmp_path / "contexts"))
    actor = store.actor_for_user(user.id, channel="web")

    created = service.ensure_session(actor, "session-created", channel="web", write=True)
    existing = service.ensure_session(actor, "session-created", channel="web", write=True)

    assert created.created is True
    assert existing.created is False


def test_owner_sessions_use_cli_storage_while_other_users_remain_isolated(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    viewer = store.create_user("viewer", "Viewer", "viewer-password", role_keys=["viewer"])
    resolver = FilesystemUserContextResolver(tmp_path / "contexts")
    service = SessionAccessService(store, resolver)

    owner_session = service.ensure_session(
        store.actor_for_user(owner.id, channel="web"),
        "owner-session",
        channel="web",
        write=True,
    )
    viewer_session = service.ensure_session(
        store.actor_for_user(viewer.id, channel="web"),
        "viewer-session",
        channel="web",
        write=True,
    )

    assert owner_session.context.sessions_dir == tmp_path / "contexts" / "sessions"
    assert owner_session.context.sessions_meta_dir == tmp_path / "contexts" / "sessions_meta"
    assert viewer_session.context.sessions_dir == (
        tmp_path / "contexts" / "users" / viewer.id / "sessions"
    )
    assert owner_session.context.files_dir == tmp_path / "contexts" / "users" / owner.id / "files"


def test_owner_listing_indexes_unowned_global_cli_sessions_without_copying(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    resolver = FilesystemUserContextResolver(tmp_path / "contexts")
    service = SessionAccessService(store, resolver)
    global_store = JsonlSessionStore(tmp_path / "contexts" / "sessions")
    global_store.append("cli-history", [Message(role="user", content="from cli")])

    summaries = service.list_sessions(store.actor_for_user(owner.id, channel="web"))

    assert [summary.session_id for summary in summaries] == ["cli-history"]
    assert store.session_index_get("cli-history")["owner_user_id"] == owner.id
    assert (tmp_path / "contexts" / "sessions" / "cli-history.jsonl").is_file()
    assert not (tmp_path / "contexts" / "users" / owner.id / "sessions" / "cli-history.jsonl").exists()


def test_owner_ignores_existing_user_directory_session(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    owner = store.initialize_owner("owner", "Owner", "password-123")
    resolver = FilesystemUserContextResolver(tmp_path / "contexts")
    service = SessionAccessService(store, resolver)
    legacy_context = resolver.resolve(owner.id)
    legacy_store = JsonlSessionStore(legacy_context.sessions_dir)
    legacy_store.append("legacy-owner-session", [Message(role="user", content="legacy")])
    store.session_index_create(
        session_id="legacy-owner-session",
        owner_user_id=owner.id,
        channel="web",
    )

    state = service.load_session(
        store.actor_for_user(owner.id, channel="web"),
        "legacy-owner-session",
    )

    assert state.messages == []


def test_ensure_session_checks_write_permission_before_creating_index(tmp_path):
    store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = store.initialize_owner("admin", "Admin", "password-123")
    service = SessionAccessService(store, FilesystemUserContextResolver(tmp_path / "contexts"))
    actor = store.actor_for_user(user.id, channel="web")
    create_without_write = replace(
        actor,
        permission_keys=frozenset({"session.create", "session.read.own"}),
    )

    with pytest.raises(SessionAccessError) as exc_info:
        service.ensure_session(
            create_without_write,
            "session-denied",
            channel="web",
            write=True,
        )

    assert exc_info.value.code == "AUTH_PERMISSION_DENIED"
    assert exc_info.value.details == {"required_permission": "session.write.own"}
    assert store.session_index_get("session-denied") is None
