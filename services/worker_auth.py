"""Server-only credentials for internal crawler/worker endpoints.

Never export the worker token from a Jac `def:pub` or `def:protect` API.
The background worker and Jac service share this local file in colocated mode.
"""
import os
from pathlib import Path
import secrets
import threading


_TOKEN_PATH = Path(__file__).with_name(".worker_token")
_LOCK = threading.Lock()


def worker_token():
    """Return a stable, randomly generated token; create it with owner-only permissions."""
    with _LOCK:
        try:
            token = _TOKEN_PATH.read_text(encoding="utf-8").strip()
            if token:
                return token
        except FileNotFoundError:
            pass

        fresh = secrets.token_hex(24)
        try:
            fd = os.open(str(_TOKEN_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(fresh)
            return fresh
        except FileExistsError:
            # A second colocated process may have created it first.
            token = _TOKEN_PATH.read_text(encoding="utf-8").strip()
            if token:
                return token
            raise RuntimeError("Worker token file exists but is empty")


def check_worker_token(provided):
    return bool(provided) and secrets.compare_digest(str(provided), worker_token())
