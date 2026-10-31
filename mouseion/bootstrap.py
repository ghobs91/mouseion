"""Wire the object graph once, for the server, the CLI and the tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from .cache import Cache
from .civic import JurisdictionIndex, load_jurisdictions
from .client import ProviderClient
from .config import Settings
from .config import settings as default_settings
from .embedders import FastEmbedder, OpenAIEmbedder
from .engine import Engine
from .registry import Registry
from .router import Router
from .tools import Toolkit


@dataclass(slots=True)
class App:
    registry: Registry
    cache: Cache
    client: ProviderClient
    router: Router
    engine: Engine
    toolkit: Toolkit

    async def aclose(self) -> None:
        await self.toolkit.aclose()
        self.cache.close()


def build(
    config: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> App:
    cfg = config or default_settings
    registry = Registry.load(cfg.providers_dir)
    cache = Cache(cfg.db_path)
    client = ProviderClient(registry, cache, config=cfg, transport=transport)
    embedder = _embedder(cfg)
    router = Router(
        registry,
        cache,
        embedder,
        alpha=cfg.router_alpha,
        cos_floor=cfg.cos_floor,
        lex_floor=cfg.lex_floor,
        query_prefix=cfg.query_prefix,
        doc_prefix=cfg.doc_prefix,
        jurisdictions=_jurisdictions(cfg),
    )
    engine = Engine(registry, client, router, config=cfg)
    toolkit = Toolkit(registry, client, cache, router=router, config=cfg, transport=transport)
    return App(
        registry=registry,
        cache=cache,
        client=client,
        router=router,
        engine=engine,
        toolkit=toolkit,
    )


def _embedder(cfg: Settings):
    if cfg.embed_url:
        return OpenAIEmbedder(
            cfg.embed_url,
            cfg.embed_model,
            cfg.embed_key,
            timeout_s=cfg.timeout_s,
        )
    if cfg.embed_backend == "fastembed":
        return FastEmbedder(cfg.embed_model or "BAAI/bge-small-en-v1.5")
    return None


def _jurisdictions(cfg: Settings) -> JurisdictionIndex | None:
    """Load the civic registry when it sits beside the providers directory."""
    path = Path(cfg.providers_dir).parent / "jurisdictions.yaml"
    if not path.exists():
        return None
    return JurisdictionIndex(load_jurisdictions(path))
