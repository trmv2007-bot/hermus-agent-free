"""Secure pairing + transport integrity for the Android bridge (§18).

Uses only the standard library (``hmac``/``hashlib``/``secrets``) so the subsystem
has no extra runtime dependency. Provides:

* :func:`new_pairing_secret` — cryptographically random 32-byte session key.
* :func:`sign` / :func:`verify` — HMAC-SHA256 request/response integrity (the
  *encrypted/integrity* channel for the local bridge; the bridge must additionally
  speak TLS for confidentiality when not on loopback).
* :func:`pairing_challenge` / :func:`pairing_response` — a simple challenge/response
  handshake so a device proves knowledge of the shared secret before it is trusted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional


def new_pairing_secret() -> bytes:
    """Generate a fresh 32-byte pairing secret."""
    return secrets.token_bytes(32)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s.encode("ascii"))


def sign(secret: bytes, data: bytes) -> str:
    """Return the HMAC-SHA256 base64 signature of ``data`` under ``secret``."""
    return _b64(hmac.new(secret, data, hashlib.sha256).digest())


def verify(secret: bytes, data: bytes, mac: str) -> bool:
    """Constant-time verify a base64 HMAC-SHA256 signature."""
    try:
        expected = hmac.new(secret, data, hashlib.sha256).digest()
        provided = _unb64(mac)
    except Exception:
        return False
    return hmac.compare_digest(expected, provided)


def pairing_challenge() -> tuple[bytes, str]:
    """Return (nonce, base64 of HMAC(nonce)) — the controller's challenge."""
    nonce = secrets.token_bytes(16)
    return nonce, _b64(hmac.new(nonce, b"challenge", hashlib.sha256).digest())


def pairing_response(secret: bytes, nonce: bytes) -> str:
    """Device proves it holds ``secret`` by signing the controller's nonce."""
    return sign(secret, nonce)


def verify_pairing(secret: bytes, nonce: bytes, response: str) -> bool:
    """Verify the device's pairing response against the shared secret."""
    return verify(secret, nonce, response)


def load_or_create_secret(path: Optional[str]) -> bytes:
    """Load the persistent pairing secret from ``path``, creating + storing a new
    one with 0600 perms if absent. Keeping the secret on disk (outside the repo)
    is what allows the bridge to re-pair across restarts."""
    import os
    from pathlib import Path
    if path:
        p = Path(path)
        if p.exists():
            raw = _unb64(p.read_text(encoding="utf-8").strip())
            if len(raw) == 32:
                return raw
        s = new_pairing_secret()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_b64(s), encoding="utf-8")
        os.chmod(p, 0o600)
        return s
    return new_pairing_secret()
