"""Local credential store for provider API keys.

Precedence is deliberate: **environment first, then this store**. A `.env` file or
a shell export stays authoritative for a process, which is what an operator
expects; the store is the convenience path for a key entered in the dashboard.

Values are never returned to a caller. The dashboard sees only whether a
credential is configured and where it came from, so a key cannot leak through the
snapshot endpoint or a page reload.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / ".mouseion" / "credentials.json"

#: Environment variable names only: no paths, no shell metacharacters.
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
MAX_VALUE = 4096

Source = Literal["env", "store"] | None


class CredentialStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_PATH

    # -- read -----------------------------------------------------------

    def _read(self) -> dict[str, str]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            key: value
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def stored(self, name: str) -> str | None:
        return self._read().get(name)

    def source(self, name: str | None) -> Source:
        if not name:
            return None
        if os.getenv(name):
            return "env"
        if self._read().get(name):
            return "store"
        return None

    def resolve(self, name: str | None) -> str | None:
        if not name:
            return None
        return os.getenv(name) or self._read().get(name)

    def configured(self, name: str | None) -> bool:
        return self.source(name) is not None

    # -- write ----------------------------------------------------------

    def set(self, name: str, value: str) -> None:
        if not _NAME.match(name):
            raise ValueError(f"invalid credential name '{name}'")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("credential value is empty")
        if len(cleaned) > MAX_VALUE:
            raise ValueError(f"credential value is over {MAX_VALUE} characters")
        data = self._read()
        data[name] = cleaned
        self._write(data)

    def delete(self, name: str) -> bool:
        data = self._read()
        if name not in data:
            return False
        del data[name]
        self._write(data)
        return True

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError:
            pass
        handle_fd, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=".credentials-", suffix=".tmp"
        )
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


_default = CredentialStore()


def default() -> CredentialStore:
    return _default


def resolve(name: str | None) -> str | None:
    """Environment first, then the local store."""
    return _default.resolve(name)
