"""Optional UI authentication: password hashing and signed session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

_PBKDF2_ROUNDS = 240_000


def get_secret_key(config_dir: Path) -> bytes:
    """Read (or create) the server secret used to sign session tokens."""
    path = config_dir / "secret.key"
    try:
        if path.exists():
            return bytes.fromhex(path.read_text().strip())
    except (OSError, ValueError):
        pass
    key = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable
        with os.fdopen(fd, "w") as fh:
            fh.write(key.hex())
    except OSError:
        pass
    return key


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def make_token(secret: bytes) -> str:
    payload = str(int(time.time())).encode()
    sig = hmac.new(secret, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def verify_token(secret: bytes, token: str, max_age_seconds: int = 30 * 24 * 3600) -> bool:
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(sig_b64), expected):
            return False
        return (int(time.time()) - int(payload.decode())) <= max_age_seconds
    except (ValueError, TypeError):
        return False
