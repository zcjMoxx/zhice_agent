from __future__ import annotations

import base64
import sqlite3

import pytest

from agent.connections.crypto import CredentialCipher, connection_aad, load_master_key
from agent.connections.protocols import ConnectionError, EmailMessage, EmailSendResult
from agent.connections.runtime import ConnectionRuntime
from agent.connections.store import SQLiteConnectionStore
from agent.integrations.email.personal_smtp import PersonalSMTPEmailProvider


def test_aes_gcm_round_trip_and_aad_tampering() -> None:
    cipher = CredentialCipher(b"k" * 32)
    encrypted = cipher.encrypt({"app_password": "secret"}, aad=connection_aad("u1", "c1", "smtp_personal"))
    assert cipher.decrypt(encrypted, aad=connection_aad("u1", "c1", "smtp_personal"))["app_password"] == "secret"
    with pytest.raises(ConnectionError, match="could not be decrypted"):
        cipher.decrypt(encrypted, aad=connection_aad("u2", "c1", "smtp_personal"))


def test_master_key_is_strict_urlsafe_base64() -> None:
    assert load_master_key(base64.urlsafe_b64encode(b"x" * 32).decode()) == b"x" * 32
    with pytest.raises(ConnectionError) as exc:
        load_master_key("short")
    assert exc.value.code == "CONNECTION_CREDENTIAL_KEY_INVALID"


def test_store_is_owner_scoped_and_encrypted(tmp_path) -> None:
    store = SQLiteConnectionStore(tmp_path / "connections.sqlite3", CredentialCipher(b"x" * 32))
    item = store.create(owner_user_id="u1", provider="smtp_personal", account_display="a@example.com",
                        credential={"app_password": "plain-secret"})
    assert item.account_display == "a@example.com"
    assert store.credential(item.id, owner_user_id="u1") == {"app_password": "plain-secret"}
    with pytest.raises(ConnectionError) as exc:
        store.credential(item.id, owner_user_id="u2")
    assert exc.value.code == "CONNECTION_ACCESS_DENIED"
    with sqlite3.connect(store.path) as db:
        raw = db.execute("SELECT credential_ciphertext FROM external_connections").fetchone()[0]
    assert b"plain-secret" not in raw


@pytest.mark.parametrize("port,security", [(25, "starttls"), (465, "starttls"), (587, "plain")])
def test_personal_smtp_rejects_insecure_configuration(port: int, security: str) -> None:
    with pytest.raises(ConnectionError) as exc:
        PersonalSMTPEmailProvider(host="smtp.example.com", port=port, security=security,
                                  username="u", app_password="p", from_address="u@example.com")
    assert exc.value.code == "CONNECTION_SMTP_INSECURE"


def test_connection_runtime_rejects_removed_provider(tmp_path) -> None:
    store = SQLiteConnectionStore(tmp_path / "connections.sqlite3", CredentialCipher(b"x" * 32))
    item = store.create(
        owner_user_id="u1",
        provider="removed_provider",
        account_display="legacy@example.com",
        credential={"refresh_token": "legacy"},
    )
    runtime = ConnectionRuntime(store)
    actor = type("Actor", (), {"user_id": "u1"})()

    with pytest.raises(ConnectionError) as exc:
        runtime.personal_email_provider(actor, item.id)

    assert exc.value.code == "CONNECTION_PROVIDER_UNSUPPORTED"


def test_connection_runtime_uses_mailbox_account_as_sender(monkeypatch, tmp_path) -> None:
    store = SQLiteConnectionStore(tmp_path / "connections.sqlite3", CredentialCipher(b"x" * 32))
    runtime = ConnectionRuntime(store)
    provider_options: dict = {}

    class Provider:
        def __init__(self, **options) -> None:
            provider_options.update(options)

        def verify(self) -> None:
            return None

    monkeypatch.setattr("agent.connections.runtime.PersonalSMTPEmailProvider", Provider)
    actor = type("Actor", (), {"user_id": "u1"})()

    item = runtime.create_personal_smtp(
        actor,
        host="smtp.qq.com",
        port=465,
        security="tls",
        username=" 849534549@qq.com ",
        app_password="authorization-code",
    )

    assert item["account_display"] == "849534549@qq.com"
    assert provider_options["username"] == "849534549@qq.com"
    assert provider_options["from_address"] == "849534549@qq.com"
    credential = store.credential(item["id"], owner_user_id="u1")
    assert credential["username"] == "849534549@qq.com"
    assert credential["from_address"] == "849534549@qq.com"


def test_connection_runtime_sends_explicit_test_email(monkeypatch, tmp_path) -> None:
    store = SQLiteConnectionStore(tmp_path / "connections.sqlite3", CredentialCipher(b"x" * 32))
    runtime = ConnectionRuntime(store)
    sent: list[EmailMessage] = []

    class Provider:
        def send(self, message: EmailMessage) -> EmailSendResult:
            sent.append(message)
            return EmailSendResult("accepted", provider_message_id="message-1")

    monkeypatch.setattr(runtime, "personal_email_provider", lambda actor, connection_id: Provider())
    actor = type("Actor", (), {"user_id": "u1"})()
    result = runtime.send_test_email(actor, "connection-1", recipient="me@example.com")
    assert result == {"status": "accepted", "provider_message_id": "message-1", "message": ""}
    assert sent[0].recipients == ("me@example.com",)
    assert "连接测试" in sent[0].subject
