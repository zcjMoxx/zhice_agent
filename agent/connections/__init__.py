"""User-owned external connections and encrypted credential storage."""

from agent.connections.crypto import CredentialCipher, connection_aad, load_master_key
from agent.connections.protocols import ConnectionError, ExternalConnection
from agent.connections.store import SQLiteConnectionStore

__all__ = ["ConnectionError", "CredentialCipher", "ExternalConnection", "SQLiteConnectionStore", "connection_aad", "load_master_key"]
