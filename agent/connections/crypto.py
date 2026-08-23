"""AES-GCM envelope encryption for per-user connection credentials."""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent.connections.protocols import ConnectionError


def connection_aad(owner_user_id: str, connection_id: str, provider: str) -> bytes:
    if not owner_user_id or not connection_id or not provider:
        raise ValueError("connection credential AAD fields must be non-empty")
    return f"{owner_user_id}|{connection_id}|{provider}".encode()


def load_master_key(value: str | None = None) -> bytes:
    encoded = value if value is not None else os.getenv("ZHICE_AGENT_CREDENTIAL_ENCRYPTION_KEY")
    if not encoded:
        raise ConnectionError("CONNECTION_CREDENTIAL_KEY_MISSING", "credential encryption key is unavailable")
    try:
        key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ConnectionError("CONNECTION_CREDENTIAL_KEY_INVALID", "credential encryption key is invalid") from exc
    if len(key) != 32:
        raise ConnectionError("CONNECTION_CREDENTIAL_KEY_INVALID", "credential encryption key must decode to 32 bytes")
    return key


@dataclass(frozen=True)
class EncryptedCredential:
    ciphertext: bytes
    nonce: bytes
    key_version: int = 1


class CredentialCipher:
    def __init__(self, key: bytes, *, key_version: int = 1):
        if len(key) != 32:
            raise ValueError("AES-256-GCM requires a 32-byte key")
        self._cipher = AESGCM(key)
        self.key_version = key_version

    @classmethod
    def from_environment(cls) -> "CredentialCipher":
        return cls(load_master_key())

    def encrypt(self, credential: Mapping[str, Any], *, aad: bytes) -> EncryptedCredential:
        nonce = os.urandom(12)
        plaintext = json.dumps(dict(credential), separators=(",", ":"), sort_keys=True).encode()
        return EncryptedCredential(self._cipher.encrypt(nonce, plaintext, aad), nonce, self.key_version)

    def decrypt(self, encrypted: EncryptedCredential, *, aad: bytes) -> dict[str, Any]:
        if encrypted.key_version != self.key_version:
            raise ConnectionError("CONNECTION_CREDENTIAL_KEY_INVALID", "credential key version is unavailable")
        try:
            plaintext = self._cipher.decrypt(encrypted.nonce, encrypted.ciphertext, aad)
            value = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectionError("CONNECTION_CREDENTIAL_DECRYPT_FAILED", "credential could not be decrypted") from exc
        if not isinstance(value, dict):
            raise ConnectionError("CONNECTION_CREDENTIAL_DECRYPT_FAILED", "credential payload is invalid")
        return value
