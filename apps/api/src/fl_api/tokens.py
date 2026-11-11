"""Capability tokens (ADR-012, docs/11 §2).

128 random bits, URL-safe. Only the SHA-256 hex digest is stored. A fast hash is
appropriate here: the token is high-entropy random data, not a user-chosen password,
so offline guessing is infeasible regardless of hash cost.
"""

import hashlib
import secrets

TOKEN_BYTES = 16  # 128 bits


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def looks_like_token(token: str) -> bool:
    # token_urlsafe(16) → 22 chars of [A-Za-z0-9_-]
    return len(token) == 22 and all(c.isalnum() or c in "-_" for c in token)
