from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


def hash_password(password: str) -> str:
    """Hash password using Argon2id with 64MB memory, 2 iterations, 4 parallelism."""
    salt = os.urandom(16)
    kdf = Argon2id(salt=salt, length=32, iterations=2, lanes=4, memory_cost=65536)
    derived = kdf.derive(password.encode("utf-8"))
    b64_salt = base64.b64encode(salt).decode("ascii")
    b64_derived = base64.b64encode(derived).decode("ascii")
    return f"$argon2id$m=65536,t=2,p=4${b64_salt}${b64_derived}"


# Precomputed valid Argon2id hash for constant-time unknown email verification
DUMMY_ARGON2_HASH = hash_password("MCSS_DUMMY_TIMING_MITIGATION_PASSWORD")


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against Argon2id hash in constant time."""
    try:
        parts = hashed.split("$")
        if len(parts) != 5 or parts[1] != "argon2id":
            return False
        b64_salt = parts[3]
        b64_derived = parts[4]
        salt = base64.b64decode(b64_salt)
        expected = base64.b64decode(b64_derived)
        kdf = Argon2id(salt=salt, length=len(expected), iterations=2, lanes=4, memory_cost=65536)
        kdf.verify(password.encode("utf-8"), expected)
        return True
    except Exception:
        return False
