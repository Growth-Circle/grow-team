"""Secret storage helpers for the agent connection service."""

import base64
import json
import secrets
from hashlib import sha256
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings
from django.utils.crypto import constant_time_compare


def hash_agent_credential(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def credential_matches(value: str, digest: str) -> bool:
    return constant_time_compare(hash_agent_credential(value), digest)


def _keyring() -> tuple[str, dict[str, bytes]]:
    path = getattr(settings, "AGENT_SECRET_MASTER_KEY_FILE", "")
    if not path:
        raise ValueError("Agent secret storage is not configured.")
    try:
        payload = json.loads(Path(path).read_text())
        current = payload["current"]
        keys = {
            key_id: base64.b64decode(value, validate=True)
            for key_id, value in payload["keys"].items()
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Agent secret storage is not configured.") from error
    if current not in keys or any(len(key) != 32 for key in keys.values()):
        raise ValueError("Agent secret storage is not configured.")
    return current, keys


def _associated_data(*, realm_id: int, owner_id: int, version: int, key_id: str) -> bytes:
    return f"agent-secret:{realm_id}:{owner_id}:{version}:{key_id}".encode()


def encrypt_agent_secret(
    value: str, *, realm_id: int, owner_id: int, version: int
) -> tuple[bytes, bytes, str]:
    key_id, keys = _keyring()
    data_key = AESGCM.generate_key(bit_length=256)
    associated_data = _associated_data(
        realm_id=realm_id, owner_id=owner_id, version=version, key_id=key_id
    )
    data_nonce = secrets.token_bytes(12)
    wrap_nonce = secrets.token_bytes(12)
    ciphertext = data_nonce + AESGCM(data_key).encrypt(data_nonce, value.encode(), associated_data)
    wrapped_key = wrap_nonce + AESGCM(keys[key_id]).encrypt(wrap_nonce, data_key, associated_data)
    return ciphertext, wrapped_key, key_id


def decrypt_agent_secret(
    ciphertext: bytes,
    wrapped_key: bytes,
    *,
    key_id: str,
    realm_id: int,
    owner_id: int,
    version: int,
) -> str:
    _, keys = _keyring()
    if key_id not in keys or len(ciphertext) < 13 or len(wrapped_key) < 13:
        raise ValueError("Agent secret cannot be decrypted.")
    associated_data = _associated_data(
        realm_id=realm_id, owner_id=owner_id, version=version, key_id=key_id
    )
    try:
        data_key = AESGCM(keys[key_id]).decrypt(wrapped_key[:12], wrapped_key[12:], associated_data)
        return AESGCM(data_key).decrypt(ciphertext[:12], ciphertext[12:], associated_data).decode()
    except Exception as error:
        raise ValueError("Agent secret cannot be decrypted.") from error
