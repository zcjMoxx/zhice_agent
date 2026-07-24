"""SQLite-backed local auth, RBAC, session index, and audit state."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from agent.auth.passwords import hash_password, verify_password
from agent.auth.schema import PERMISSIONS, ROLE_NAMES, ROLE_PERMISSIONS, SCHEMA_SQL
from agent.auth.tokens import generate_token, hash_token
from agent.protocols.activity import RuntimeActivityEvent
from agent.protocols.auth import ActorContext, AuditEvent, AuthLogin, UserAccount

DEFAULT_AUTH_TTL = timedelta(days=7)
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
_STATUS_VALUES = {"active", "disabled"}


class AuthSetupError(RuntimeError):
    """Raised when Owner bootstrap cannot proceed."""


class AuthStoreError(RuntimeError):
    """Raised for invalid auth store mutations."""


class SQLiteAuthStore:
    """Small SQLite store for the local Part 9 user system."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()

    def is_initialized(self) -> bool:
        """Return whether the auth database has a usable users table."""

        if not self.path.is_file():
            return False
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
                ).fetchone()
        except sqlite3.Error:
            return False
        return row is not None

    def has_users(self) -> bool:
        """Return whether auth setup has created at least one user."""

        if not self.is_initialized():
            return False
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def has_owner(self) -> bool:
        """Return whether the unique Owner account exists."""

        if not self.is_initialized():
            return False
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                WHERE r.key='owner' LIMIT 1
                """
            ).fetchone() is not None

    def initialize_schema(self) -> None:
        """Create the schema and idempotently seed built-in permissions and roles."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA_SQL)
            turn_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(turn_runs)").fetchall()
            }
            if "duration_ms" not in turn_columns:
                connection.execute("ALTER TABLE turn_runs ADD COLUMN duration_ms INTEGER")
            session_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(session_index)").fetchall()
            }
            if "conversation_type" not in session_columns:
                connection.execute(
                    "ALTER TABLE session_index "
                    "ADD COLUMN conversation_type TEXT NOT NULL DEFAULT ''"
                )
                connection.execute(
                    """
                    UPDATE session_index
                    SET conversation_type=COALESCE((
                      SELECT conversation_type FROM channel_conversations c
                      WHERE c.current_session_id=session_index.session_id
                      LIMIT 1
                    ), '')
                    """
                )
            for key, (description, category) in PERMISSIONS.items():
                connection.execute(
                    """
                    INSERT INTO permissions(key, description, category)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                      description=excluded.description,
                      category=excluded.category
                    """,
                    (key, description, category),
                )
            if PERMISSIONS:
                placeholders = ", ".join("?" for _ in PERMISSIONS)
                connection.execute(
                    f"DELETE FROM permissions WHERE key NOT IN ({placeholders})",
                    tuple(PERMISSIONS),
                )
            else:
                connection.execute("DELETE FROM permissions")
            for role_key, permission_keys in ROLE_PERMISSIONS.items():
                role_id = f"role-{role_key}"
                connection.execute(
                    """
                    INSERT INTO roles(id, key, name, description, is_builtin, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at
                    """,
                    (
                        role_id,
                        role_key,
                        ROLE_NAMES[role_key],
                        f"Built-in {role_key} role",
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "DELETE FROM role_permissions WHERE role_id=?",
                    (role_id,),
                )
                connection.executemany(
                    """
                    INSERT OR IGNORE INTO role_permissions(role_id, permission_key)
                    VALUES (?, ?)
                    """,
                    [(role_id, permission_key) for permission_key in permission_keys],
                )

    def initialize_owner(
        self,
        username: str,
        display_name: str,
        password: str,
    ) -> UserAccount:
        """Initialize schema and create the unique permanent Owner account."""

        username = _normalize_username(username)
        display_name = _normalize_display_name(display_name)
        password_hash, password_salt = hash_password(password)
        self.initialize_schema()
        user_id = "user-" + uuid.uuid4().hex
        now = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1 FROM user_roles ur JOIN roles r ON r.id=ur.role_id
                WHERE r.key='owner' LIMIT 1
                """
            ).fetchone()
            if existing is not None:
                raise AuthSetupError("owner already exists; owner initialization is closed")
            role = connection.execute("SELECT id FROM roles WHERE key='owner'").fetchone()
            if role is None:
                raise AuthSetupError("owner role is not initialized")
            connection.execute(
                """
                INSERT INTO users(
                  id, username, display_name, password_hash, password_salt,
                  status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    user_id,
                    username,
                    display_name,
                    password_hash,
                    password_salt,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO user_roles(user_id, role_id) VALUES (?, ?)",
                (user_id, str(role["id"])),
            )
        return self.get_user(user_id)

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        *,
        role_keys: Iterable[str] = ("viewer",),
        status: str = "active",
    ) -> UserAccount:
        """Create one user and assign validated role keys."""

        self._require_initialized()
        username = _normalize_username(username)
        display_name = _normalize_display_name(display_name)
        status = _normalize_status(status)
        password_hash, password_salt = hash_password(password)
        user_id = "user-" + uuid.uuid4().hex
        now = _utc_now()
        normalized_roles = tuple(dict.fromkeys(str(key).strip() for key in role_keys if str(key).strip()))
        if not normalized_roles:
            raise AuthStoreError("at least one role is required")
        if "owner" in normalized_roles:
            raise AuthStoreError("owner role can only be assigned during owner initialization")

        try:
            with self._connect() as connection:
                role_rows = connection.execute(
                    f"SELECT id, key FROM roles WHERE key IN ({_placeholders(normalized_roles)})",
                    normalized_roles,
                ).fetchall()
                found_roles = {str(row["key"]): str(row["id"]) for row in role_rows}
                missing = [key for key in normalized_roles if key not in found_roles]
                if missing:
                    raise AuthStoreError(f"unknown roles: {', '.join(missing)}")
                connection.execute(
                    """
                    INSERT INTO users(
                      id, username, display_name, password_hash, password_salt,
                      status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        username,
                        display_name,
                        password_hash,
                        password_salt,
                        status,
                        now,
                        now,
                    ),
                )
                connection.executemany(
                    "INSERT INTO user_roles(user_id, role_id) VALUES (?, ?)",
                    [(user_id, found_roles[key]) for key in normalized_roles],
                )
        except sqlite3.IntegrityError as exc:
            raise AuthStoreError("username already exists") from exc
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> UserAccount:
        """Return one public user record."""

        self._require_initialized()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthStoreError("user not found")
            roles = self._role_keys(connection, user_id)
        return _user_from_row(row, roles)

    def get_user_by_username(self, username: str) -> UserAccount | None:
        """Return a public user record by normalized username."""

        if not self.is_initialized():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (str(username).strip(),),
            ).fetchone()
            if row is None:
                return None
            roles = self._role_keys(connection, str(row["id"]))
        return _user_from_row(row, roles)

    def list_users(self) -> list[UserAccount]:
        """Return users ordered by username."""

        self._require_initialized()
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
            return [
                _user_from_row(row, self._role_keys(connection, str(row["id"])))
                for row in rows
            ]

    def update_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        status: str | None = None,
        role_keys: Iterable[str] | None = None,
        direct_permission: tuple[str, bool] | None = None,
    ) -> UserAccount:
        """Update public user fields and optionally replace role assignments."""

        self._require_initialized()
        current = self.get_user(user_id)
        if "owner" in current.role_keys and (status is not None or role_keys is not None):
            raise AuthStoreError("owner account cannot be disabled or have roles changed")
        updates: list[str] = []
        values: list[Any] = []
        if display_name is not None:
            updates.append("display_name = ?")
            values.append(_normalize_display_name(display_name))
        if status is not None:
            updates.append("status = ?")
            values.append(_normalize_status(status))
        updates.append("updated_at = ?")
        values.append(_utc_now())
        values.append(user_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE users SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            if cursor.rowcount == 0:
                raise AuthStoreError("user not found")
            if role_keys is not None:
                normalized_roles = tuple(
                    dict.fromkeys(str(key).strip() for key in role_keys if str(key).strip())
                )
                if not normalized_roles:
                    raise AuthStoreError("at least one role is required")
                if "owner" in normalized_roles:
                    raise AuthStoreError("owner role cannot be assigned through user management")
                rows = connection.execute(
                    f"SELECT id, key FROM roles WHERE key IN ({_placeholders(normalized_roles)})",
                    normalized_roles,
                ).fetchall()
                role_ids = {str(row["key"]): str(row["id"]) for row in rows}
                missing = [key for key in normalized_roles if key not in role_ids]
                if missing:
                    raise AuthStoreError(f"unknown roles: {', '.join(missing)}")
                connection.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
                connection.executemany(
                    "INSERT INTO user_roles(user_id, role_id) VALUES (?, ?)",
                    [(user_id, role_ids[key]) for key in normalized_roles],
                )
                if "admin" not in normalized_roles:
                    connection.execute(
                        "DELETE FROM user_permissions WHERE user_id=? AND permission_key='auth.admin.manage'",
                        (user_id,),
                    )
            if direct_permission is not None:
                permission_key, enabled = direct_permission
                permission_key = str(permission_key).strip()
                permission = connection.execute(
                    "SELECT key FROM permissions WHERE key=?", (permission_key,)
                ).fetchone()
                if permission is None:
                    raise AuthStoreError("unknown permission")
                effective_roles = set(self._role_keys(connection, user_id))
                if permission_key == "auth.admin.manage" and "admin" not in effective_roles:
                    raise AuthStoreError("admin management can only be delegated to an admin")
                if enabled:
                    connection.execute(
                        """
                        INSERT INTO user_permissions(user_id, permission_key, granted_at)
                        VALUES (?, ?, ?) ON CONFLICT(user_id, permission_key) DO NOTHING
                        """,
                        (user_id, permission_key, _utc_now()),
                    )
                else:
                    connection.execute(
                        "DELETE FROM user_permissions WHERE user_id=? AND permission_key=?",
                        (user_id, permission_key),
                    )
        return self.get_user(user_id)

    def set_user_permission(self, user_id: str, permission_key: str, *, enabled: bool) -> None:
        """Set one direct permission grant used for Owner-controlled delegation."""

        self._require_initialized()
        permission_key = str(permission_key).strip()
        with self._connect() as connection:
            user = connection.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
            if user is None:
                raise AuthStoreError("user not found")
            permission = connection.execute(
                "SELECT key FROM permissions WHERE key=?", (permission_key,)
            ).fetchone()
            if permission is None:
                raise AuthStoreError("unknown permission")
            roles = set(self._role_keys(connection, user_id))
            if permission_key == "auth.admin.manage" and "admin" not in roles:
                raise AuthStoreError("admin management can only be delegated to an admin")
            if enabled:
                connection.execute(
                    """
                    INSERT INTO user_permissions(user_id, permission_key, granted_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, permission_key) DO NOTHING
                    """,
                    (user_id, permission_key, _utc_now()),
                )
            else:
                connection.execute(
                    "DELETE FROM user_permissions WHERE user_id=? AND permission_key=?",
                    (user_id, permission_key),
                )

    def user_has_direct_permission(self, user_id: str, permission_key: str) -> bool:
        """Return whether one user has a direct, non-role permission grant."""

        self._require_initialized()
        with self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM user_permissions WHERE user_id=? AND permission_key=?",
                (user_id, permission_key),
            ).fetchone() is not None

    def reset_password(self, username: str, password: str) -> None:
        """Replace one user's password and revoke existing auth sessions."""

        password_hash, password_salt = hash_password(password)
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                (str(username).strip(),),
            ).fetchone()
            if row is None:
                raise AuthStoreError("user not found")
            user_id = str(row["id"])
            connection.execute(
                """
                UPDATE users SET password_hash=?, password_salt=?, updated_at=? WHERE id=?
                """,
                (password_hash, password_salt, now, user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        """Change an authenticated user's password and revoke all sessions."""

        new_hash, new_salt = hash_password(new_password)
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None or not verify_password(
                current_password,
                str(row["password_hash"]),
                str(row["password_salt"]),
            ):
                raise AuthStoreError("invalid current password")
            connection.execute(
                """
                UPDATE users SET password_hash=?, password_salt=?, updated_at=? WHERE id=?
                """,
                (new_hash, new_salt, now, user_id),
            )
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (now, user_id),
            )

    def authenticate(self, username: str, password: str, *, channel: str) -> ActorContext | None:
        """Verify credentials and return an actor without creating login state."""

        if not self.is_initialized():
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (str(username).strip(),),
            ).fetchone()
            if row is None or str(row["status"]) != "active":
                return None
            if not verify_password(password, str(row["password_hash"]), str(row["password_salt"])):
                return None
            return self._actor_from_user_row(connection, row, channel=channel)

    def login(
        self,
        username: str,
        password: str,
        *,
        channel: str,
        expires_at: datetime | None = None,
        user_agent_preview: str = "",
        remote_addr_preview: str = "",
    ) -> AuthLogin:
        """Authenticate and create a revocable opaque auth session."""

        actor = self.authenticate(username, password, channel=channel)
        if actor is None or actor.user_id is None:
            raise AuthStoreError("invalid credentials")
        token = generate_token()
        auth_session_id = "auth-" + uuid.uuid4().hex
        now_dt = datetime.now(UTC)
        expiry = (expires_at or (now_dt + DEFAULT_AUTH_TTL)).astimezone(UTC)
        now = now_dt.isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO auth_sessions(
                  id, user_id, token_hash, created_at, expires_at,
                  user_agent_preview, remote_addr_preview
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    auth_session_id,
                    actor.user_id,
                    hash_token(token),
                    now,
                    expiry.isoformat(timespec="seconds"),
                    str(user_agent_preview)[:160],
                    str(remote_addr_preview)[:80],
                ),
            )
            connection.execute(
                "UPDATE users SET last_login_at=?, updated_at=? WHERE id=?",
                (now, now, actor.user_id),
            )
        return AuthLogin(
            token=token,
            auth_session_id=auth_session_id,
            expires_at=expiry.isoformat(timespec="seconds"),
            actor=replace(actor, auth_session_id=auth_session_id),
        )

    def resolve_token(self, token: str, *, channel: str) -> ActorContext | None:
        """Resolve one active token without exposing its stored hash."""

        if not token or not self.is_initialized():
            return None
        now = datetime.now(UTC)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.id AS auth_session_id, s.expires_at, u.*
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.revoked_at IS NULL
                """,
                (hash_token(token),),
            ).fetchone()
            if row is None or str(row["status"]) != "active":
                return None
            try:
                expiry = datetime.fromisoformat(str(row["expires_at"]))
            except ValueError:
                return None
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= now:
                return None
            return self._actor_from_user_row(
                connection,
                row,
                channel=channel,
                auth_session_id=str(row["auth_session_id"]),
            )

    def revoke_token(self, token: str) -> None:
        """Revoke an opaque token if it exists."""

        if not token or not self.is_initialized():
            return
        with self._connect() as connection:
            connection.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (_utc_now(), hash_token(token)),
            )

    def actor_for_user(self, user_id: str, *, channel: str) -> ActorContext:
        """Build an actor from a user id for trusted internal flows."""

        self._require_initialized()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise AuthStoreError("user not found")
            return self._actor_from_user_row(connection, row, channel=channel)

    def list_roles(self) -> list[dict[str, Any]]:
        """Return role definitions and their current permission sets."""

        self._require_initialized()
        with self._connect() as connection:
            roles = connection.execute("SELECT * FROM roles ORDER BY key").fetchall()
            result = []
            for role in roles:
                permissions = connection.execute(
                    "SELECT permission_key FROM role_permissions WHERE role_id=? ORDER BY permission_key",
                    (role["id"],),
                ).fetchall()
                result.append(
                    {
                        "id": str(role["id"]),
                        "key": str(role["key"]),
                        "name": str(role["name"]),
                        "description": str(role["description"]),
                        "is_builtin": bool(role["is_builtin"]),
                        "permission_keys": [str(item[0]) for item in permissions],
                    }
                )
            return result

    def update_role_permissions(self, role_id: str, permission_keys: Iterable[str]) -> dict[str, Any]:
        """Replace one role's permission assignments."""

        keys = tuple(dict.fromkeys(str(key).strip() for key in permission_keys if str(key).strip()))
        with self._connect() as connection:
            role = connection.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
            if role is None:
                raise AuthStoreError("role not found")
            if str(role["key"]) in {"owner", "admin"}:
                raise AuthStoreError("owner and admin role permissions are protected")
            if keys:
                rows = connection.execute(
                    f"SELECT key FROM permissions WHERE key IN ({_placeholders(keys)})", keys
                ).fetchall()
                found = {str(row[0]) for row in rows}
                missing = [key for key in keys if key not in found]
                if missing:
                    raise AuthStoreError(f"unknown permissions: {', '.join(missing)}")
            connection.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
            connection.executemany(
                "INSERT INTO role_permissions(role_id, permission_key) VALUES (?, ?)",
                [(role_id, key) for key in keys],
            )
            connection.execute("UPDATE roles SET updated_at=? WHERE id=?", (_utc_now(), role_id))
        return next(role for role in self.list_roles() if role["id"] == role_id)

    def link_external_identity(
        self,
        *,
        user_id: str,
        channel: str,
        external_tenant_id: str,
        external_user_id: str,
        external_display_name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create or refresh one channel identity mapping."""

        now = _utc_now()
        identity_id = "external-" + uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO external_identities(
                  id, user_id, channel, external_tenant_id, external_user_id,
                  external_display_name, linked_at, last_seen_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, external_tenant_id, external_user_id) DO UPDATE SET
                  user_id=excluded.user_id,
                  external_display_name=excluded.external_display_name,
                  status='active',
                  last_seen_at=excluded.last_seen_at,
                  metadata_json=excluded.metadata_json
                """,
                (
                    identity_id,
                    user_id,
                    str(channel).strip(),
                    str(external_tenant_id).strip(),
                    str(external_user_id).strip(),
                    str(external_display_name).strip()[:120],
                    now,
                    now,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def create_channel_account(
        self,
        *,
        channel: str,
        account_key: str,
        owner_user_id: str,
        external_account_id: str,
        external_user_id: str,
        credential_ref: str,
        external_display_name: str = "",
    ) -> dict[str, Any]:
        """Atomically create account ownership and its external identity mapping."""

        now = _utc_now()
        account_id = "channel-account-" + uuid.uuid4().hex
        identity_id = "external-" + uuid.uuid4().hex
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO channel_accounts(
                      id, channel, account_key, owner_user_id, external_account_id,
                      external_user_id, credential_ref, status, linked_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        account_id,
                        channel,
                        account_key,
                        owner_user_id,
                        external_account_id,
                        external_user_id,
                        credential_ref,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO external_identities(
                      id, user_id, channel, external_tenant_id, external_user_id,
                      external_display_name, linked_at, last_seen_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
                    """,
                    (
                        identity_id,
                        owner_user_id,
                        channel,
                        account_key,
                        external_user_id,
                        external_display_name[:120],
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthStoreError("channel account conflicts with an existing binding") from exc
        return self.get_channel_account(channel=channel, account_key=account_key) or {}

    def get_channel_account(
        self, *, channel: str, account_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channel_accounts WHERE channel=? AND account_key=?",
                (channel, account_key),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_channel_account_for_user(
        self, *, channel: str, owner_user_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channel_accounts WHERE channel=? AND owner_user_id=?",
                (channel, owner_user_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_active_channel_accounts(self, channel: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM channel_accounts
                WHERE channel=? AND status IN ('active', 'reconnect_required')
                ORDER BY linked_at
                """,
                (channel,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_channel_account_status(
        self, *, channel: str, account_key: str, status: str
    ) -> bool:
        if status not in {"active", "reconnect_required", "disabled", "cleanup_pending"}:
            raise AuthStoreError("invalid channel account status")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE channel_accounts SET status=?, updated_at=?
                WHERE channel=? AND account_key=?
                """,
                (status, _utc_now(), channel, account_key),
            )
        return cursor.rowcount == 1

    def delete_channel_account_for_user(
        self, *, channel: str, owner_user_id: str
    ) -> dict[str, Any] | None:
        """Delete live ownership and identity; callers retain Session and Memory history."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM channel_accounts WHERE channel=? AND owner_user_id=?",
                (channel, owner_user_id),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE channel_accounts SET status='disabled', updated_at=? WHERE id=?",
                (_utc_now(), str(row["id"])),
            )
            connection.execute(
                """
                DELETE FROM external_identities
                WHERE channel=? AND external_tenant_id=? AND user_id=?
                """,
                (channel, str(row["account_key"]), owner_user_id),
            )
            connection.execute("DELETE FROM channel_accounts WHERE id=?", (str(row["id"]),))
        return dict(row)

    def resolve_external_identity(
        self,
        *,
        channel: str,
        external_tenant_id: str,
        external_user_id: str,
    ) -> ActorContext | None:
        """Resolve an active external identity into an internal actor."""

        if not self.is_initialized():
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.* FROM external_identities e
                JOIN users u ON u.id=e.user_id
                WHERE e.channel=? AND e.external_tenant_id=? AND e.external_user_id=?
                  AND e.status='active' AND u.status='active'
                """,
                (channel, external_tenant_id, external_user_id),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE external_identities SET last_seen_at=?
                WHERE channel=? AND external_tenant_id=? AND external_user_id=?
                """,
                (_utc_now(), channel, external_tenant_id, external_user_id),
            )
            return self._actor_from_user_row(connection, row, channel=channel)

    def unlink_external_identity(
        self,
        *,
        channel: str,
        external_tenant_id: str,
        external_user_id: str,
    ) -> bool:
        """Disable one external identity without deleting its audit history."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE external_identities SET status='disabled'
                WHERE channel=? AND external_tenant_id=? AND external_user_id=?
                  AND status='active'
                """,
                (channel, external_tenant_id, external_user_id),
            )
        return cursor.rowcount > 0

    def list_external_identities_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Return active external identities owned by one internal user."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, channel, external_display_name, linked_at
                FROM external_identities
                WHERE user_id=? AND status='active'
                ORDER BY linked_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def unlink_external_identity_for_user(self, *, identity_id: str, user_id: str) -> bool:
        """Disable one identity only when it belongs to the requesting user."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE external_identities SET status='disabled'
                WHERE id=? AND user_id=? AND status='active'
                """,
                (identity_id, user_id),
            )
        return cursor.rowcount > 0

    def create_external_link_token(
        self,
        *,
        token_hash: str,
        user_id: str,
        channel: str,
        account_key: str,
        expires_at: str,
    ) -> str:
        """Persist one hashed, account-scoped, single-use link token."""

        token_id = "link-" + uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO external_identity_link_tokens(
                  id, token_hash, user_id, channel, account_key, status,
                  created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (token_id, token_hash, user_id, channel, account_key, _utc_now(), expires_at),
            )
        return token_id

    def consume_external_link_token(
        self,
        *,
        token_hash: str,
        channel: str,
        account_key: str,
        consumed_at: str,
    ) -> str | None:
        """Atomically consume a valid link token and return its internal user id."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, user_id, expires_at FROM external_identity_link_tokens
                WHERE token_hash=? AND channel=? AND account_key=? AND status='pending'
                """,
                (token_hash, channel, account_key),
            ).fetchone()
            if row is None or str(row["expires_at"]) <= consumed_at:
                return None
            cursor = connection.execute(
                """
                UPDATE external_identity_link_tokens
                SET status='consumed', consumed_at=?
                WHERE id=? AND status='pending'
                """,
                (consumed_at, str(row["id"])),
            )
            return str(row["user_id"]) if cursor.rowcount == 1 else None

    def create_external_authorization_request(
        self,
        *,
        token_hash: str,
        channel: str,
        account_key: str,
        external_user_id: str,
        external_display_name: str,
        expires_at: str,
    ) -> str:
        """Persist one external-identity-bound Web authorization request."""

        request_id = "channel-auth-" + uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO external_identity_authorization_requests(
                  id, token_hash, channel, account_key, external_user_id,
                  external_display_name, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    request_id,
                    token_hash,
                    channel,
                    account_key,
                    external_user_id,
                    external_display_name[:120],
                    _utc_now(),
                    expires_at,
                ),
            )
        return request_id

    def consume_external_authorization_request(
        self,
        *,
        token_hash: str,
        user_id: str,
        consumed_at: str,
    ) -> dict[str, Any] | None:
        """Atomically bind one pending Web authorization request to a DB user."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM external_identity_authorization_requests
                WHERE token_hash=? AND status='pending'
                """,
                (token_hash,),
            ).fetchone()
            if row is None or str(row["expires_at"]) <= consumed_at:
                return None
            existing = connection.execute(
                """
                SELECT user_id FROM external_identities
                WHERE channel=? AND external_tenant_id=? AND external_user_id=?
                  AND status='active'
                """,
                (str(row["channel"]), str(row["account_key"]), str(row["external_user_id"])),
            ).fetchone()
            if existing is not None and str(existing["user_id"]) != user_id:
                connection.execute(
                    """
                    UPDATE external_identity_authorization_requests
                    SET status='conflict', consumed_at=? WHERE id=? AND status='pending'
                    """,
                    (consumed_at, str(row["id"])),
                )
                return None
            identity_id = "external-" + uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO external_identities(
                  id, user_id, channel, external_tenant_id, external_user_id,
                  external_display_name, linked_at, last_seen_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}')
                ON CONFLICT(channel, external_tenant_id, external_user_id) DO UPDATE SET
                  user_id=excluded.user_id,
                  external_display_name=excluded.external_display_name,
                  status='active',
                  last_seen_at=excluded.last_seen_at
                """,
                (
                    identity_id,
                    user_id,
                    str(row["channel"]),
                    str(row["account_key"]),
                    str(row["external_user_id"]),
                    str(row["external_display_name"]),
                    consumed_at,
                    consumed_at,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE external_identity_authorization_requests
                SET status='consumed', consumed_at=?, user_id=?
                WHERE id=? AND status='pending'
                """,
                (consumed_at, user_id, str(row["id"])),
            )
            if cursor.rowcount != 1:
                return None
            result = dict(row)
            result["user_id"] = user_id
            result["status"] = "consumed"
            return result

    def channel_conversation_get(
        self,
        *,
        channel: str,
        account_key: str,
        conversation_type: str,
        external_conversation_id: str,
        external_thread_id: str,
        owner_user_id: str,
    ) -> dict[str, Any] | None:
        """Return one persistent external-conversation route."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM channel_conversations
                WHERE channel=? AND account_key=? AND conversation_type=?
                  AND external_conversation_id=? AND external_thread_id=?
                  AND owner_user_id=?
                """,
                (
                    channel,
                    account_key,
                    conversation_type,
                    external_conversation_id,
                    external_thread_id,
                    owner_user_id,
                ),
            ).fetchone()
        return dict(row) if row is not None else None

    def channel_conversation_upsert(
        self,
        *,
        channel: str,
        account_key: str,
        conversation_type: str,
        external_conversation_id: str,
        external_thread_id: str,
        owner_user_id: str,
        current_session_id: str,
    ) -> dict[str, Any]:
        """Create a route or atomically replace its current session."""

        now = _utc_now()
        route_id = "route-" + uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO channel_conversations(
                  id, channel, account_key, conversation_type,
                  external_conversation_id, external_thread_id, owner_user_id,
                  current_session_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                  channel, account_key, conversation_type, external_conversation_id,
                  external_thread_id, owner_user_id
                ) DO UPDATE SET
                  current_session_id=excluded.current_session_id,
                  updated_at=excluded.updated_at
                """,
                (
                    route_id,
                    channel,
                    account_key,
                    conversation_type,
                    external_conversation_id,
                    external_thread_id,
                    owner_user_id,
                    current_session_id,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM channel_conversations
                WHERE channel=? AND account_key=? AND conversation_type=?
                  AND external_conversation_id=? AND external_thread_id=?
                  AND owner_user_id=?
                """,
                (
                    channel,
                    account_key,
                    conversation_type,
                    external_conversation_id,
                    external_thread_id,
                    owner_user_id,
                ),
            ).fetchone()
        return dict(row)

    def claim_channel_event(
        self,
        *,
        channel: str,
        account_key: str,
        event_id: str,
        message_id: str = "",
    ) -> bool:
        """Atomically claim an inbound event before any Agent work begins."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO channel_event_receipts(
                  channel, account_key, event_id, message_id, status, first_seen_at
                ) VALUES (?, ?, ?, ?, 'processing', ?)
                """,
                (channel, account_key, event_id, message_id, _utc_now()),
            )
        return cursor.rowcount == 1

    def finish_channel_event(
        self,
        *,
        channel: str,
        account_key: str,
        event_id: str,
        status: str,
        error_code: str = "",
    ) -> None:
        """Finish a previously claimed event without permitting replay."""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE channel_event_receipts
                SET status=?, finished_at=?, error_code=?
                WHERE channel=? AND account_key=? AND event_id=?
                """,
                (status, _utc_now(), error_code, channel, account_key, event_id),
            )

    def session_index_get(self, session_id: str) -> dict[str, Any] | None:
        """Return one session index row as a plain dict."""

        self._require_initialized()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_index WHERE session_id=?", (session_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def session_index_list(self, owner_user_id: str | None = None) -> list[dict[str, Any]]:
        """List active session index rows, optionally limited to one owner."""

        self._require_initialized()
        query = "SELECT * FROM session_index WHERE archived_at IS NULL"
        values: tuple[Any, ...] = ()
        if owner_user_id is not None:
            query += " AND owner_user_id=?"
            values = (owner_user_id,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def session_index_create(
        self,
        *,
        session_id: str,
        owner_user_id: str,
        channel: str,
        conversation_type: str = "",
        external_chat_id: str = "",
        external_thread_id: str = "",
    ) -> None:
        """Create a globally unique session owner row."""

        now = _utc_now()
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO session_index(
                      session_id, owner_user_id, channel, conversation_type, external_chat_id,
                      external_thread_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        owner_user_id,
                        channel,
                        conversation_type,
                        external_chat_id,
                        external_thread_id,
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthStoreError("session id is already owned") from exc

    def session_index_update(
        self,
        session_id: str,
        *,
        title: str,
        preview: str,
        message_count: int,
        updated_at: str | None = None,
    ) -> None:
        """Update denormalized list fields after a JSONL mutation."""

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE session_index SET title=?, preview=?, message_count=?, updated_at=?
                WHERE session_id=?
                """,
                (title, preview, int(message_count), updated_at or _utc_now(), session_id),
            )

    def session_index_delete(self, session_id: str) -> None:
        """Delete one index row after its files were removed."""

        with self._connect() as connection:
            connection.execute("DELETE FROM session_index WHERE session_id=?", (session_id,))

    def record_audit(self, event: AuditEvent) -> str:
        """Persist one generic audit event and return its id."""

        if not self.is_initialized():
            return ""
        event_id = "audit-" + uuid.uuid4().hex
        actor = event.actor
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(
                  id, ts, actor_user_id, auth_session_id, request_id, channel,
                  action, resource_type, resource_id, session_id, turn_id,
                  tool_call_record_id, route, status_code, decision, reason_code,
                  risk_category, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _utc_now(),
                    actor.user_id if actor else None,
                    (actor.auth_session_id or "") if actor else "",
                    event.request_id,
                    event.channel or (actor.channel if actor else ""),
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.session_id,
                    event.turn_id,
                    event.tool_call_record_id,
                    event.route,
                    event.status_code,
                    event.decision,
                    event.reason_code,
                    event.risk_category,
                    json.dumps(event.metadata, ensure_ascii=False, separators=(",", ":")),
                ),
            )
        return event_id

    def record_activity(self, event: RuntimeActivityEvent) -> None:
        """Maintain structured turn/tool runtime indexes without writing audit rows."""

        if not self.is_initialized():
            return
        with self._connect() as connection:
            self._record_turn_run(connection, event)
            self._record_tool_call(connection, event)

    def create_tool_confirmation(
        self,
        *,
        confirmation_id: str,
        tool_call_record_id: str,
        actor_user_id: str,
        session_id: str,
        turn_id: str,
        tool_name: str,
        risk_level: str,
        command_preview: str,
        args_hash: str,
        expires_at: str,
    ) -> dict[str, Any]:
        """Persist one pending exact-args confirmation."""

        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_confirmations(
                  id, tool_call_record_id, actor_user_id, session_id, turn_id,
                  tool_name, risk_level, command_preview, args_hash, status,
                  requested_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    confirmation_id,
                    tool_call_record_id,
                    actor_user_id,
                    session_id,
                    turn_id,
                    tool_name,
                    risk_level,
                    command_preview,
                    args_hash,
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                """
                UPDATE tool_call_records
                SET confirmation_status='pending', args_hash=?, risk_level=?
                WHERE id=?
                """,
                (args_hash, risk_level, tool_call_record_id),
            )
        return self.get_tool_confirmation(confirmation_id) or {}

    def get_tool_confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        """Return one confirmation row."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_confirmations WHERE id=?", (confirmation_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_tool_confirmations(
        self,
        *,
        actor_user_id: str | None = None,
        pending_only: bool = True,
    ) -> list[dict[str, Any]]:
        """List actor-owned or global pending confirmations."""

        clauses: list[str] = []
        values: list[Any] = []
        if actor_user_id is not None:
            clauses.append("actor_user_id=?")
            values.append(actor_user_id)
        if pending_only:
            clauses.append("status='pending'")
        query = "SELECT * FROM tool_confirmations"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY requested_at DESC"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def decide_tool_confirmation(
        self,
        confirmation_id: str,
        *,
        decision_actor_user_id: str,
        approved: bool,
        manage_any: bool = False,
    ) -> str:
        """Approve or deny a still-pending actor-owned confirmation."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tool_confirmations WHERE id=?", (confirmation_id,)
            ).fetchone()
            if row is None:
                raise AuthStoreError("confirmation not found")
            if not manage_any and str(row["actor_user_id"]) != decision_actor_user_id:
                raise AuthStoreError("confirmation not found")
            if str(row["status"]) != "pending":
                return str(row["status"])
            expiry = datetime.fromisoformat(str(row["expires_at"]))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            status = "expired" if expiry <= datetime.now(UTC) else ("approved" if approved else "denied")
            now = _utc_now()
            connection.execute(
                """
                UPDATE tool_confirmations
                SET status=?, decided_at=?, decision_actor_user_id=? WHERE id=?
                """,
                (status, now, decision_actor_user_id, confirmation_id),
            )
            connection.execute(
                "UPDATE tool_call_records SET confirmation_status=? WHERE id=?",
                (status, str(row["tool_call_record_id"])),
            )
        return status

    def expire_tool_confirmation(self, confirmation_id: str, *, status: str = "expired") -> str:
        """Mark a pending confirmation expired or cancelled."""

        if status not in {"expired", "cancelled"}:
            raise ValueError("confirmation terminal status must be expired or cancelled")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT tool_call_record_id, status FROM tool_confirmations WHERE id=?",
                (confirmation_id,),
            ).fetchone()
            if row is None:
                return status
            if str(row["status"]) == "pending":
                connection.execute(
                    "UPDATE tool_confirmations SET status=?, decided_at=? WHERE id=?",
                    (status, _utc_now(), confirmation_id),
                )
                connection.execute(
                    "UPDATE tool_call_records SET confirmation_status=? WHERE id=?",
                    (status, str(row["tool_call_record_id"])),
                )
            else:
                status = str(row["status"])
        return status

    def list_audit_events(
        self,
        *,
        actor_user_id: str | None = None,
        limit: int = 100,
        session_id: str = "",
        turn_id: str = "",
    ) -> list[dict[str, Any]]:
        """Read bounded audit events for admin and diagnostic views."""

        clauses: list[str] = []
        values: list[Any] = []
        if actor_user_id is not None:
            clauses.append("actor_user_id=?")
            values.append(actor_user_id)
        if session_id:
            clauses.append("session_id=?")
            values.append(session_id)
        if turn_id:
            clauses.append("turn_id=?")
            values.append(turn_id)
        query = "SELECT * FROM audit_events"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts DESC LIMIT ?"
        values.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(query, values).fetchall()]
        for row in rows:
            try:
                row["metadata"] = json.loads(row.pop("metadata_json"))
            except (json.JSONDecodeError, TypeError):
                row["metadata"] = {}
        return rows

    def list_turn_runs(
        self,
        *,
        actor_user_id: str,
        session_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent structured turn activity for one actor."""

        clauses = ["actor_user_id=?"]
        values: list[Any] = [actor_user_id]
        if session_id:
            clauses.append("session_id=?")
            values.append(session_id)
        values.append(max(1, min(int(limit), 500)))
        query = (
            "SELECT * FROM turn_runs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY started_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def list_tool_call_records(
        self,
        *,
        actor_user_id: str,
        session_id: str = "",
        turn_id: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent structured tool activity for one actor."""

        clauses = ["actor_user_id=?"]
        values: list[Any] = [actor_user_id]
        if session_id:
            clauses.append("session_id=?")
            values.append(session_id)
        if turn_id:
            clauses.append("turn_id=?")
            values.append(turn_id)
        values.append(max(1, min(int(limit), 500)))
        query = (
            "SELECT * FROM tool_call_records WHERE "
            + " AND ".join(clauses)
            + " ORDER BY started_at DESC LIMIT ?"
        )
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, values).fetchall()]

    def _record_turn_run(
        self,
        connection: sqlite3.Connection,
        event: RuntimeActivityEvent,
    ) -> None:
        """Maintain the structured turn runtime index."""

        actor = event.actor
        if actor is None or actor.user_id is None or not event.action.startswith("chat.turn_"):
            return
        if event.action == "chat.turn_started":
            connection.execute(
                """
                INSERT INTO turn_runs(
                  turn_id, session_id, turn_index, actor_user_id, auth_session_id,
                  request_id, channel, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'started', ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                  session_id=excluded.session_id,
                  turn_index=excluded.turn_index,
                  actor_user_id=excluded.actor_user_id,
                  auth_session_id=excluded.auth_session_id,
                  request_id=excluded.request_id,
                  channel=excluded.channel,
                  status='started'
                """,
                (
                    event.turn_id,
                    event.session_id,
                    event.metadata.get("turn_index"),
                    actor.user_id,
                    actor.auth_session_id or "",
                    event.request_id,
                    event.channel or actor.channel,
                    _utc_now(),
                ),
            )
            return
        status = event.action.removeprefix("chat.turn_")
        connection.execute(
            """
            UPDATE turn_runs SET status=?, finished_at=?, duration_ms=?, error_code=?
            WHERE turn_id=?
            """,
            (
                status,
                _utc_now(),
                event.metadata.get("duration_ms"),
                event.reason_code if status == "error" else "",
                event.turn_id,
            ),
        )

    def _record_tool_call(
        self,
        connection: sqlite3.Connection,
        event: RuntimeActivityEvent,
    ) -> None:
        """Maintain the structured tool-call runtime index."""

        actor = event.actor
        if actor is None or actor.user_id is None or not event.action.startswith("tool."):
            return
        metadata = event.metadata
        if event.action == "tool.call_requested":
            connection.execute(
                """
                INSERT OR REPLACE INTO tool_call_records(
                  id, session_id, turn_id, tool_call_id, tool_name, actor_user_id,
                  cwd, command_preview, args_preview, risk_level, risk_category,
                  decision, started_at, timeout_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'low', 'safe', 'requested', ?, ?)
                """,
                (
                    event.tool_call_record_id,
                    event.session_id,
                    event.turn_id,
                    event.resource_id,
                    str(metadata.get("tool_name") or ""),
                    actor.user_id,
                    str(metadata.get("cwd") or ""),
                    str(metadata.get("command_preview") or ""),
                    str(metadata.get("args_preview") or ""),
                    _utc_now(),
                    metadata.get("timeout_seconds"),
                ),
            )
            return
        if event.action in {"tool.call_allowed", "tool.call_denied", "tool.confirmation_requested"}:
            connection.execute(
                """
                UPDATE tool_call_records SET
                  decision=?, decision_code=?, permission_key=?, risk_level=?, risk_category=?
                WHERE id=?
                """,
                (
                    event.decision,
                    event.reason_code,
                    str(metadata.get("permission_key") or ""),
                    str(metadata.get("risk_level") or "low"),
                    event.risk_category or "safe",
                    event.tool_call_record_id,
                ),
            )
            return
        if event.action in {"tool.call_done", "tool.call_error"}:
            connection.execute(
                """
                UPDATE tool_call_records SET
                  finished_at=?, duration_seconds=?, is_error=?, result_code=?, exit_code=?,
                  timeout_seconds=COALESCE(?, timeout_seconds), stdout_tail=?, stderr_tail=?,
                  output_truncated=?, output_preview=?
                WHERE id=?
                """,
                (
                    _utc_now(),
                    metadata.get("duration_seconds")
                    or (
                        float(metadata["duration_ms"]) / 1000
                        if metadata.get("duration_ms") is not None
                        else None
                    ),
                    1 if event.action == "tool.call_error" else 0,
                    event.reason_code,
                    metadata.get("exit_code"),
                    metadata.get("timeout_seconds"),
                    str(metadata.get("stdout_tail") or "")[:500],
                    str(metadata.get("stderr_tail") or "")[:500],
                    1 if metadata.get("truncated") else 0,
                    str(metadata.get("output_preview") or ""),
                    event.tool_call_record_id,
                ),
            )

    def _actor_from_user_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        channel: str,
        auth_session_id: str | None = None,
    ) -> ActorContext:
        """Build an ActorContext with aggregated role and permission keys."""

        user_id = str(row["id"])
        role_keys = frozenset(self._role_keys(connection, user_id))
        permission_rows = connection.execute(
            """
            SELECT rp.permission_key
            FROM user_roles ur
            JOIN role_permissions rp ON rp.role_id=ur.role_id
            WHERE ur.user_id=?
            UNION
            SELECT permission_key FROM user_permissions WHERE user_id=?
            """,
            (user_id, user_id),
        ).fetchall()
        return ActorContext(
            actor_type="user",
            user_id=user_id,
            username=str(row["username"]),
            display_name=str(row["display_name"]),
            role_keys=role_keys,
            permission_keys=frozenset(str(item[0]) for item in permission_rows),
            channel=channel,
            auth_session_id=auth_session_id,
        )

    @staticmethod
    def _role_keys(connection: sqlite3.Connection, user_id: str) -> tuple[str, ...]:
        rows = connection.execute(
            """
            SELECT r.key FROM user_roles ur JOIN roles r ON r.id=ur.role_id
            WHERE ur.user_id=? ORDER BY r.key
            """,
            (user_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def _require_initialized(self) -> None:
        if not self.is_initialized():
            raise AuthSetupError("auth database is not initialized; run zcagent auth init-owner")

    def _connect(self):
        return _ManagedConnection(self.path)


class _ManagedConnection:
    """Commit/rollback and always close one short-lived SQLite connection."""

    def __init__(self, path: Path):
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        self.connection = connection
        return connection

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.connection is None:
            return
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


def _normalize_username(username: str) -> str:
    value = str(username).strip()
    if not _USERNAME_RE.fullmatch(value):
        raise AuthStoreError(
            "username must be 3-64 characters using letters, numbers, dot, underscore, or hyphen"
        )
    return value


def _normalize_display_name(display_name: str) -> str:
    value = " ".join(str(display_name).split())
    if not value:
        raise AuthStoreError("display_name is required")
    return value[:120]


def _normalize_status(status: str) -> str:
    value = str(status).strip().lower()
    if value not in _STATUS_VALUES:
        raise AuthStoreError("status must be active or disabled")
    return value


def _user_from_row(row: sqlite3.Row, roles: tuple[str, ...]) -> UserAccount:
    return UserAccount(
        id=str(row["id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"]),
        status=str(row["status"]),
        role_keys=roles,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        last_login_at=str(row["last_login_at"]) if row["last_login_at"] else None,
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _placeholders(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)
