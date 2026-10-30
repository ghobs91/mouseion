"""SQLite cache over raw upstream bodies.

We cache the *raw* body, not the normalized envelope, so a manifest change
(projection, field mapping) applies retroactively to everything already cached.
It also leaves us a corpus for the retrieval layer later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    key         TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    capability  TEXT NOT NULL,
    params      TEXT NOT NULL,
    status      INTEGER NOT NULL,
    body        TEXT NOT NULL,
    fetched_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS responses_provider ON responses (provider, capability);
CREATE TABLE IF NOT EXISTS vectors (
    key        TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth (
    provider    TEXT PRIMARY KEY,
    client_info TEXT,
    tokens      TEXT,
    expires_at  REAL,
    updated_at  REAL NOT NULL
);
"""


@dataclass(slots=True)
class CachedResponse:
    status: int
    body: Any
    fetched_at: float

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.fetched_at)


def cache_key(provider: str, capability: str, params: dict[str, Any], cursor: str | None) -> str:
    payload = json.dumps(
        {"provider": provider, "capability": capability, "params": params, "cursor": cursor},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def vector_key(model: str, text: str) -> str:
    """Content-addressed, model-addressed: a manifest edit re-embeds only what changed."""
    return hashlib.sha256(f"{model}\x00{text}".encode()).hexdigest()


class Cache:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def get(self, key: str) -> CachedResponse | None:
        row = self._conn.execute(
            "SELECT status, body, fetched_at, expires_at FROM responses WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        status, body, fetched_at, expires_at = row
        if expires_at < time.time():
            return None
        try:
            return CachedResponse(status=status, body=json.loads(body), fetched_at=fetched_at)
        except json.JSONDecodeError:  # pragma: no cover - corrupt row
            return None

    def put(
        self,
        key: str,
        *,
        provider: str,
        capability: str,
        params: dict[str, Any],
        status: int,
        body: Any,
        ttl: float,
    ) -> None:
        now = time.time()
        self._conn.execute(
            "INSERT OR REPLACE INTO responses"
            " (key, provider, capability, params, status, body, fetched_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                provider,
                capability,
                json.dumps(params, sort_keys=True, default=str),
                status,
                json.dumps(body, default=str),
                now,
                now + ttl,
            ),
        )
        self._conn.commit()

    def stats(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT provider, COUNT(*) FROM responses GROUP BY provider"
        ).fetchall()
        return {provider: count for provider, count in rows}

    # -- embedding vectors ---------------------------------------------

    def get_vectors(self, model: str, keys: list[str]) -> dict[str, list[float]]:
        """Return cached vectors for the keys that have one under this model id."""
        out: dict[str, list[float]] = {}
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT key, vector FROM vectors WHERE model = ? AND key IN ({placeholders})",
                (model, *chunk),
            ).fetchall()
            for key, blob in rows:
                values = array("f")
                values.frombytes(blob)
                out[key] = values.tolist()
        return out

    def put_vectors(self, model: str, vectors: dict[str, list[float]]) -> None:
        if not vectors:
            return
        now = time.time()
        self._conn.executemany(
            "INSERT OR REPLACE INTO vectors (key, model, dim, vector, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (key, model, len(vector), array("f", vector).tobytes(), now)
                for key, vector in vectors.items()
            ],
        )
        self._conn.commit()

    def vector_count(self, model: str | None = None) -> int:
        if model is None:
            row = self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM vectors WHERE model = ?", (model,)
            ).fetchone()
        return int(row[0]) if row else 0

    # -- OAuth credentials ---------------------------------------------

    def get_oauth(self, provider: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT client_info, tokens, expires_at FROM oauth WHERE provider = ?",
            (provider,),
        ).fetchone()
        if row is None:
            return None
        client_info, tokens, expires_at = row
        return {
            "client_info": json.loads(client_info) if client_info else None,
            "tokens": json.loads(tokens) if tokens else None,
            "expires_at": expires_at,
        }

    def put_oauth(
        self,
        provider: str,
        *,
        client_info: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
        expires_at: float | None = None,
    ) -> None:
        existing = self.get_oauth(provider) or {}
        self._conn.execute(
            "INSERT OR REPLACE INTO oauth"
            " (provider, client_info, tokens, expires_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                provider,
                json.dumps(
                    client_info if client_info is not None else existing.get("client_info"),
                    default=str,
                )
                if (client_info is not None or existing.get("client_info") is not None)
                else None,
                json.dumps(
                    tokens if tokens is not None else existing.get("tokens"),
                    default=str,
                )
                if (tokens is not None or existing.get("tokens") is not None)
                else None,
                expires_at if expires_at is not None else existing.get("expires_at"),
                time.time(),
            ),
        )
        self._conn.commit()

    def clear_oauth(self, provider: str) -> None:
        self._conn.execute("DELETE FROM oauth WHERE provider = ?", (provider,))
        self._conn.commit()

    def oauth_status(self) -> dict[str, dict[str, bool]]:
        rows = self._conn.execute(
            "SELECT provider, client_info IS NOT NULL, tokens IS NOT NULL FROM oauth"
        ).fetchall()
        return {
            provider: {"client": bool(has_client), "tokens": bool(has_tokens)}
            for provider, has_client, has_tokens in rows
        }

    def close(self) -> None:
        self._conn.close()
