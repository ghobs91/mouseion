"""Runtime settings. Everything is env-overridable so the same code runs in tests."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env support so .env.example is real. Existing env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    providers_dir: Path = field(
        default_factory=lambda: Path(os.getenv("MOUSEION_PROVIDERS", ROOT / "providers"))
    )
    db_path: Path = field(
        default_factory=lambda: Path(os.getenv("MOUSEION_DB", ROOT / ".mouseion" / "cache.db"))
    )
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "MOUSEION_UA",
            "mouseion/0.1 (local provider registry; +https://github.com/local/mouseion)",
        )
    )
    timeout_s: float = field(default_factory=lambda: float(os.getenv("MOUSEION_TIMEOUT", "30")))
    max_body_bytes: int = field(default_factory=lambda: int(os.getenv("MOUSEION_MAX_BODY", "8000000")))
    # fetch_url refuses private/link-local targets unless explicitly allowed.
    allow_private_urls: bool = field(default_factory=lambda: _bool("MOUSEION_ALLOW_PRIVATE_URLS"))
    default_ttl_s: float = field(default_factory=lambda: float(os.getenv("MOUSEION_TTL", "300")))
    #: Empty means no embedding backend: the router stays lexical + patterns.
    embed_url: str = field(default_factory=lambda: os.getenv("MOUSEION_EMBED_URL", ""))
    embed_model: str = field(default_factory=lambda: os.getenv("MOUSEION_EMBED_MODEL", ""))
    embed_key: str = field(default_factory=lambda: os.getenv("MOUSEION_EMBED_KEY", ""))
    #: "fastembed" loads a local ONNX model; empty means lexically-only unless
    #: an embed_url is configured. Never downloads a model implicitly.
    embed_backend: str = field(default_factory=lambda: os.getenv("MOUSEION_EMBED_BACKEND", ""))
    # Router calibration. Cosine baselines differ per model: re-tune after switching.
    router_alpha: float = field(
        default_factory=lambda: float(os.getenv("MOUSEION_ROUTER_ALPHA", "0.8"))
    )
    cos_floor: float = field(default_factory=lambda: float(os.getenv("MOUSEION_COS_FLOOR", "0.55")))
    lex_floor: float = field(default_factory=lambda: float(os.getenv("MOUSEION_LEX_FLOOR", "0.25")))
    #: Some models (nomic, e5) require asymmetric prefixes.
    query_prefix: str = field(default_factory=lambda: os.getenv("MOUSEION_QUERY_PREFIX", ""))
    doc_prefix: str = field(default_factory=lambda: os.getenv("MOUSEION_DOC_PREFIX", ""))


settings = Settings()
