"""Routing eval: the only way to know whether a router change helped.

Thirty hand-written questions, including paraphrases that share no vocabulary with
the provider description (those are what embeddings buy) and negatives (those are
what keeps the router honest when no source fits).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .router import Router

EVAL_PATH = Path(__file__).resolve().parent.parent / "eval" / "routing.jsonl"


@dataclass(frozen=True, slots=True)
class Case:
    query: str
    expect: str | None


def load_cases(path: Path | str = EVAL_PATH) -> list[Case]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"eval set not found: {path}")
    cases: list[Case] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        cases.append(Case(query=row["query"], expect=row.get("expect")))
    return cases


async def evaluate(router: Router, cases: list[Case] | None = None) -> dict[str, Any]:
    cases = cases if cases is not None else load_cases()
    top1 = top3 = 0
    misses: list[dict[str, Any]] = []

    for case in cases:
        candidates = await router.route(case.query)
        providers = [candidate.provider for candidate in candidates]

        if case.expect is None:
            hit1 = hit3 = not providers
        else:
            hit1 = bool(providers) and providers[0] == case.expect
            hit3 = case.expect in providers[:3]

        top1 += hit1
        top3 += hit3
        if not hit3:
            misses.append(
                {
                    "query": case.query,
                    "expect": case.expect,
                    "got": providers[:3],
                    "scores": [candidate.as_dict() for candidate in candidates[:3]],
                }
            )

    total = len(cases)
    return {
        "total": total,
        "top1": top1,
        "top3": top3,
        "top1_accuracy": round(top1 / total, 3) if total else 0.0,
        "top3_accuracy": round(top3 / total, 3) if total else 0.0,
        "misses": misses,
    }
