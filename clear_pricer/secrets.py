"""Credential hygiene: load connection strings from the environment / .env and never print them.

Drivers (libpq via DuckDB's postgres extension) echo the full connection string -- password included -- in their
error messages. Every path that touches a DSN goes through `redact` / `guard` so a failed connection can never leak a
secret into logs, CI output, or a terminal session.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path

_URL_PASSWORD = re.compile(r"(postgres(?:ql)?://[^:/@\s]+:)([^@\s]*)(@)", re.I)
_KV_PASSWORD = re.compile(r"(password\s*=\s*)('[^']*'|\S+)", re.I)


def redact(text: str, *secrets: str) -> str:
    """Mask passwords in URL-style and key=value DSNs, plus any explicitly given secret values."""
    out = _URL_PASSWORD.sub(r"\1***\3", text)
    out = _KV_PASSWORD.sub(r"\1***", out)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out


def dsn_password(dsn: str) -> str | None:
    m = _URL_PASSWORD.search(dsn) or None
    if m:
        return m.group(2)
    m = _KV_PASSWORD.search(dsn)
    return m.group(2).strip("'") if m else None


class RedactedError(RuntimeError):
    pass


@contextmanager
def guard(dsn: str):
    """Re-raise any exception with the DSN's password removed from its message (and drop the original chain)."""
    try:
        yield
    except Exception as e:  # noqa: BLE001 -- we re-raise, sanitised
        raise RedactedError(redact(f"{type(e).__name__}: {e}", dsn_password(dsn) or "")) from None


def load_env(repo: Path) -> None:
    """Populate os.environ from repo/.env (without overriding real environment variables)."""
    env = repo / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
