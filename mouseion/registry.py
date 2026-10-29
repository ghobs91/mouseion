"""Provider index: manifests, routing documents, and lexical scoring.

Phase 0 searched providers. Phase 1 routes to *capabilities*, because that is the
unit the model actually calls: "recalled blood pressure pills" is
(openfda, drug_recalls), not "openfda" in the abstract.

Lexical scoring stays here and stays usable on its own. It is the fallback when
no embedding backend is configured, and it is the half that does not blur exact
identifiers such as `NCT04631731` or `CFTC`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import Manifest, load_manifests

_WORD = re.compile(r"[a-z0-9]+")

_STOP = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
    "is", "it", "of", "on", "or", "that", "the", "to", "was", "what", "when",
    "where", "which", "who", "with", "about", "me", "my", "i", "do", "does",
    "this", "these", "those", "there", "any", "all", "can", "you", "tell",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP and len(t) > 1]


@dataclass(frozen=True, slots=True)
class ProviderDoc:
    """One routable unit: a provider capability, plus everything that signals it."""

    provider: str
    capability: str
    text: str
    tokens: frozenset[str]
    id_tokens: frozenset[str]
    patterns: tuple[re.Pattern[str], ...]


def _doc_text(manifest: Manifest, capability: str, description: str, exemplars: list[str]) -> str:
    parts = [
        f"{manifest.title} ({manifest.category})",
        capability.replace("_", " "),
        description,
    ]
    if manifest.keywords:
        parts.append("Keywords: " + ", ".join(manifest.keywords))
    if exemplars:
        parts.append("Questions: " + " | ".join(exemplars))
    return ". ".join(parts)


class Registry:
    def __init__(self, manifests: dict[str, Manifest]):
        self.manifests = manifests
        self._docs: list[ProviderDoc] = []
        for manifest in manifests.values():
            id_tokens = frozenset(
                _tokens(manifest.id.replace("_", " ") + " " + manifest.title)
            )
            for name, cap in manifest.capabilities.items():
                text = _doc_text(manifest, name, cap.description, cap.exemplars)
                self._docs.append(
                    ProviderDoc(
                        provider=manifest.id,
                        capability=name,
                        text=text,
                        tokens=frozenset(_tokens(text)),
                        id_tokens=id_tokens,
                        patterns=tuple(re.compile(p, re.IGNORECASE) for p in cap.patterns),
                    )
                )

        # Document frequency, so a distinctive name token ("uk", "cms", "nvd") counts
        # far more than a common word that happens to appear in a title ("data").
        self._df: dict[str, int] = {}
        for doc in self._docs:
            for token in doc.tokens:
                self._df[token] = self._df.get(token, 0) + 1

    @classmethod
    def load(cls, directory: Path | str) -> Registry:
        return cls(load_manifests(Path(directory)))

    # -- lookup ---------------------------------------------------------

    def get(self, provider: str) -> Manifest:
        key = provider.strip().lower().replace("-", "_").replace(" ", "_")
        if key in self.manifests:
            return self.manifests[key]
        close = [pid for pid in self.manifests if key in pid or pid in key]
        hint = f"did you mean: {close}" if close else f"available: {sorted(self.manifests)}"
        raise KeyError(f"unknown provider '{provider}'. {hint}")

    def categories(self) -> list[str]:
        return sorted({m.category for m in self.manifests.values()})

    def documents(self, category: str | None = None) -> list[ProviderDoc]:
        if category is None:
            return self._docs
        wanted = category.lower()
        return [d for d in self._docs if self.manifests[d.provider].category.lower() == wanted]

    # -- lexical --------------------------------------------------------

    def lexical_scores(self, query: str) -> dict[tuple[str, str], float]:
        """0..1 per capability: fraction of query tokens present, plus a name bonus."""
        query_tokens = set(_tokens(query))
        if not query_tokens:
            return {}
        out: dict[tuple[str, str], float] = {}
        for doc in self._docs:
            hits = len(query_tokens & doc.tokens)
            if not hits:
                continue
            score = hits / len(query_tokens)
            name_hits = query_tokens & doc.id_tokens
            if name_hits:
                # Rare name tokens carry the signal; common ones barely move it.
                score += min(0.5, sum(1.0 / max(1, self._df.get(t, 1)) for t in name_hits))
            out[(doc.provider, doc.capability)] = min(1.0, score)
        return out

    def search(self, query: str = "", category: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Lexical-only search. Used as the fallback and by the CLI."""
        if not _tokens(query):
            return [
                m.compact()
                for m in sorted(self.manifests.values(), key=lambda m: m.title)
                if category is None or m.category.lower() == category.lower()
            ][:limit]
        scores = self.lexical_scores(query)
        ranked = [(scores.get((d.provider, d.capability), 0.0), d) for d in self.documents(category)]
        return self.cards([(score, doc) for score, doc in ranked if score > 0], limit)

    # -- presentation ---------------------------------------------------

    def cards(self, ranked: list[tuple[float, ProviderDoc]], limit: int) -> list[dict[str, Any]]:
        """Collapse capability hits to provider cards, best capability first."""
        best: dict[str, tuple[float, ProviderDoc]] = {}
        for score, doc in ranked:
            current = best.get(doc.provider)
            if current is None or score > current[0]:
                best[doc.provider] = (score, doc)

        ordered = sorted(
            best.values(), key=lambda item: (-item[0], self.manifests[item[1].provider].title)
        )
        out: list[dict[str, Any]] = []
        for _, doc in ordered[:limit]:
            card = self.manifests[doc.provider].compact()
            card["capabilities"] = sorted(
                card["capabilities"], key=lambda cap: cap["name"] != doc.capability
            )
            out.append(card)
        return out

    def describe(self, provider: str) -> dict[str, Any]:
        return self.get(provider).describe()

    def summary(self) -> dict[str, Any]:
        by_category: dict[str, int] = {}
        for manifest in self.manifests.values():
            by_category[manifest.category] = by_category.get(manifest.category, 0) + 1
        return {
            "providers": len(self.manifests),
            "capabilities": len(self._docs),
            "categories": by_category,
        }
