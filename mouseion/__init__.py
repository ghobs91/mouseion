"""Mouseion: one tool surface over many public-record APIs, for a local model."""

from .bootstrap import App, build
from .envelope import Envelope, ProviderError, Record, SourceStatus
from .registry import Registry
from .tools import Toolkit

__version__ = "0.1.0"

__all__ = [
    "App",
    "Envelope",
    "ProviderError",
    "Record",
    "Registry",
    "SourceStatus",
    "Toolkit",
    "__version__",
    "build",
]
