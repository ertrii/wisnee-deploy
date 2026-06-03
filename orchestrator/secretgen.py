"""Autogeneración de secrets. Nadie (ni el operador) los elige ni los ve: se
generan acá y se escriben a env/*.env (chmod 600)."""

import base64
import secrets
import string


def token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def alnum(length: int = 28) -> str:
    """Alfanumérico (sin símbolos que compliquen URLs de conexión/.env)."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def b64key(nbytes: int = 32) -> str:
    """32 bytes en base64 — formato que espera FISCAL_ENCRYPTION_KEY."""
    return base64.b64encode(secrets.token_bytes(nbytes)).decode()


def generate() -> dict:
    return {
        "DB_PASSWORD": alnum(28),
        "SECRET_KEY": token(48),
        "INIT_TOKEN": token(24),
        "FISCAL_ENCRYPTION_KEY": b64key(32),
        "WA_BRIDGE_SECRET": token(32),
        "MK_BRIDGE_SECRET": token(32),
    }
