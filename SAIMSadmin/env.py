"""Small, dependency-free helpers for reading configuration from the environment.

A ``.env`` file in the project root is loaded automatically. Real environment
variables always win over the file, so Docker / CI / production can override
anything without editing files.

``.env`` format: one ``KEY=value`` per line, ``#`` comment lines, optional
single or double quotes around the value. Inline comments are not supported.
"""

import os
from pathlib import Path

_TRUE_VALUES = {"1", "true", "yes", "on"}


def load_dotenv(path: Path) -> None:
    """Load ``KEY=value`` pairs from *path* into ``os.environ`` (if the file exists)."""
    if not path.is_file():
        return
    # utf-8-sig tolerates the BOM that Windows Notepad likes to add.
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in _TRUE_VALUES


def env_list(key: str, default: str = "") -> list[str]:
    """Comma-separated list, e.g. ``DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1``."""
    return [item.strip() for item in os.environ.get(key, default).split(",") if item.strip()]
