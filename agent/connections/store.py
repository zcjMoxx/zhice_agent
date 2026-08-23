"""SQLite truth for encrypted, user-owned external connections."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from agent.connections.crypto import CredentialCipher, EncryptedCredential, connection_aad
from agent.connections.protocols import ConnectionError, ExternalConnection

_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS external_connections (
 id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, provider TEXT NOT NULL,
 account_display TEXT NOT NULL, credential_ciphertext BLOB NOT NULL, credential_nonce BLOB NOT NULL,
 key_version INTEGER NOT NULL, scopes_json TEXT NOT NULL, expires_at TEXT, status TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_external_connections_owner ON external_connections(owner_user_id, provider);
DROP TABLE IF EXISTS oauth_authorization_states;
CREATE TABLE IF NOT EXISTS connection_audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, connection_id TEXT, owner_user_id TEXT NOT NULL,
 action TEXT NOT NULL, result TEXT NOT NULL, reason_code TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteConnectionStore:
    def __init__(self, path: Path | str, cipher: CredentialCipher):
        self.path = Path(path).expanduser().resolve()
        self.cipher = cipher
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def create(self, *, owner_user_id: str, provider: str, account_display: str,
               credential: Mapping[str, Any], scopes: tuple[str, ...] = (),
               expires_at: str | None = None, status: str = "active") -> ExternalConnection:
        connection_id = "conn-" + uuid.uuid4().hex
        encrypted = self.cipher.encrypt(credential, aad=connection_aad(owner_user_id, connection_id, provider))
        now = _now()
        with self._connect() as db:
            db.execute("""INSERT INTO external_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                connection_id, owner_user_id, provider, account_display, encrypted.ciphertext,
                encrypted.nonce, encrypted.key_version, json.dumps(sorted(set(scopes))), expires_at,
                status, now, now,
            ))
            self._audit(db, connection_id, owner_user_id, "connection.created", "success")
        return self.get(connection_id, owner_user_id=owner_user_id)

    def get(self, connection_id: str, *, owner_user_id: str | None = None) -> ExternalConnection:
        with self._connect() as db:
            row = db.execute("SELECT * FROM external_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise ConnectionError("CONNECTION_NOT_FOUND", "connection was not found")
        if owner_user_id is not None and row["owner_user_id"] != owner_user_id:
            raise ConnectionError("CONNECTION_ACCESS_DENIED", "connection belongs to another user")
        return self._public(row)

    def list_for_owner(self, owner_user_id: str) -> tuple[ExternalConnection, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM external_connections WHERE owner_user_id=? ORDER BY created_at", (owner_user_id,)).fetchall()
        return tuple(self._public(row) for row in rows)

    def credential(self, connection_id: str, *, owner_user_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM external_connections WHERE id=?", (connection_id,)).fetchone()
        if row is None:
            raise ConnectionError("CONNECTION_NOT_FOUND", "connection was not found")
        if row["owner_user_id"] != owner_user_id:
            raise ConnectionError("CONNECTION_ACCESS_DENIED", "connection belongs to another user")
        encrypted = EncryptedCredential(row["credential_ciphertext"], row["credential_nonce"], row["key_version"])
        return self.cipher.decrypt(encrypted, aad=connection_aad(owner_user_id, connection_id, row["provider"]))

    def delete(self, connection_id: str, *, owner_user_id: str) -> None:
        self.get(connection_id, owner_user_id=owner_user_id)
        with self._connect() as db:
            self._audit(db, connection_id, owner_user_id, "connection.deleted", "success")
            db.execute("DELETE FROM external_connections WHERE id=?", (connection_id,))

    @staticmethod
    def _public(row: sqlite3.Row) -> ExternalConnection:
        return ExternalConnection(row["id"], row["owner_user_id"], row["provider"], row["account_display"],
                                  tuple(json.loads(row["scopes_json"])), row["expires_at"], row["status"], row["created_at"], row["updated_at"])

    @staticmethod
    def _audit(db: sqlite3.Connection, connection_id: str, owner_user_id: str, action: str,
               result: str, reason_code: str = "") -> None:
        db.execute("INSERT INTO connection_audit_events(connection_id,owner_user_id,action,result,reason_code,created_at) VALUES(?,?,?,?,?,?)",
                   (connection_id, owner_user_id, action, result, reason_code, _now()))
