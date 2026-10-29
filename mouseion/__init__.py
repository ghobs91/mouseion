"""Mouseion: one tool surface over many public-record APIs, for a local model."""

from .bootstrap import App, build
from .engine import CanonicalQuery, Engine, LookupResult
from .envelope import Envelope, ProviderError, Record, SourceStatus
from .registry import Registry
from .tools import Toolkit

__version__ = "0.1.0"

__all__ = [
    "App",
    "CanonicalQuery",
    "Engine",
    "Envelope",
    "LookupResult",
    "ProviderError",
    "Record",
    "Registry",
    "SourceStatus",
    "Toolkit",
    "__version__",
    "build",
]
