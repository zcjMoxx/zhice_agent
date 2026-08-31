from __future__ import annotations

import base64
import sqlite3

import pytest

from agent.auth.store import SQLiteAuthStore
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
        username=" user@example.com ",
        app_password="authorization-code",
    )

    assert item["account_display"] == "user@example.com"
    assert provider_options["username"] == "user@example.com"
    assert provider_options["from_address"] == "user@example.com"
    credential = store.credential(item["id"], owner_user_id="u1")
    assert credential["username"] == "user@example.com"
    assert credential["from_address"] == "user@example.com"


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


def test_smtp_mailbox_becomes_notification_email_without_overriding_existing(monkeypatch, tmp_path) -> None:
    auth_store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    first_user = auth_store.initialize_owner("owner", "Owner", "password-123")
    store = SQLiteConnectionStore(tmp_path / "connections.sqlite3", CredentialCipher(b"x" * 32))
    runtime = ConnectionRuntime(store, notification_store=auth_store)

    class Provider:
        def __init__(self, **_options) -> None:
            pass

        def verify(self) -> None:
            return None

    monkeypatch.setattr("agent.connections.runtime.PersonalSMTPEmailProvider", Provider)
    actor = type("Actor", (), {"user_id": first_user.id})()

    runtime.create_personal_smtp(
        actor,
        host="smtp.qq.com",
        port=465,
        security="tls",
        username="first@example.com",
        app_password="authorization-code",
    )
    assert auth_store.notification_email(first_user.id) == "first@example.com"

    auth_store.upsert_notification_email(
        first_user.id, "chosen@example.com", verified=True, is_default=True
    )
    runtime.create_personal_smtp(
        actor,
        host="smtp.qq.com",
        port=465,
        security="tls",
        username="second@example.com",
        app_password="authorization-code",
    )
    assert auth_store.notification_email(first_user.id) == "chosen@example.com"


def test_notification_email_verification_and_official_test_use_only_self(monkeypatch, tmp_path) -> None:
    auth_store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = auth_store.initialize_owner("owner", "Owner", "password-123")
    store = SQLiteConnectionStore(tmp_path / "connections.sqlite3", CredentialCipher(b"x" * 32))
    sent: list[EmailMessage] = []

    class OfficialProvider:
        def send(self, message: EmailMessage) -> EmailSendResult:
            sent.append(message)
            return EmailSendResult("accepted", provider_message_id="official-1")

    runtime = ConnectionRuntime(
        store,
        notification_store=auth_store,
        official_email_provider=OfficialProvider(),
    )
    actor = type("Actor", (), {"user_id": user.id})()

    challenge = runtime.request_notification_email_verification(
        actor, address="me@example.com"
    )
    assert challenge["address"] == "me@example.com"
    assert sent[0].recipients == ("me@example.com",)
    code = sent[0].text.split("：", 1)[1].splitlines()[0]
    assert runtime.verify_notification_email(
        actor, address="me@example.com", code=code
    )["verified"] is True

    result = runtime.send_notification_test(actor)
    assert result["status"] == "accepted"
    assert sent[-1].recipients == ("me@example.com",)


def test_notification_email_verification_rate_limit_is_structured(tmp_path) -> None:
    auth_store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = auth_store.initialize_owner("owner", "Owner", "password-123")
    sent: list[EmailMessage] = []

    class OfficialProvider:
        def send(self, message: EmailMessage) -> EmailSendResult:
            sent.append(message)
            return EmailSendResult("accepted")

    runtime = ConnectionRuntime(
        None,
        notification_store=auth_store,
        official_email_provider=OfficialProvider(),
    )
    actor = type("Actor", (), {"user_id": user.id})()

    first = runtime.request_notification_email_verification(
        actor, address="me@example.com"
    )
    with pytest.raises(ConnectionError) as exc_info:
        runtime.request_notification_email_verification(
            actor, address="other@example.com"
        )

    assert first["retry_after_seconds"] == 60
    assert exc_info.value.code == "NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED"
    assert 1 <= exc_info.value.details["retry_after_seconds"] <= 60
    assert len(sent) == 1


def test_notification_email_remains_available_without_optional_smtp_store(tmp_path) -> None:
    auth_store = SQLiteAuthStore(tmp_path / "auth.sqlite3")
    user = auth_store.initialize_owner("owner", "Owner", "password-123")
    auth_store.upsert_notification_email(
        user.id, "me@example.com", verified=True, is_default=True
    )
    runtime = ConnectionRuntime(None, notification_store=auth_store)
    actor = type("Actor", (), {"user_id": user.id})()

    assert runtime.smtp_available is False
    assert runtime.notification_email(actor)["address"] == "me@example.com"
    with pytest.raises(ConnectionError) as exc:
        runtime.list(actor)
    assert exc.value.code == "CONNECTION_CREDENTIAL_KEY_MISSING"
