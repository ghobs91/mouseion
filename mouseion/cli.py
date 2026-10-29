"""Small CLI so the registry is usable (and debuggable) without a model in the loop."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .bootstrap import build
from .envelope import ProviderError


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


async def _run(args: argparse.Namespace) -> int:
    if args.command == "discover":
        from .discovery import discover, manifest_from

        report = await discover(args.catalog_limit, args.probe_limit)
        if args.write:
            import yaml

            from .discovery import Candidate, ProbeResult

            out = Path(args.write)
            out.mkdir(parents=True, exist_ok=True)
            written = 0
            for result in report["results"]:
                if result["verdict"] != "ok":
                    continue
                stub = ProbeResult(
                    candidate=Candidate(result["url"], "", result["agency"], result["shape"]),
                    status=result["status"],
                    record_path=result["record_path"],
                    records=result["records"],
                    verdict="ok",
                )
                manifest = manifest_from(stub)
                (out / f"{manifest['id']}.yaml").write_text(
                    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
                )
                written += 1
            report["written"] = written
        else:
            report.pop("results", None)
        _print(report)
        return 0

    if args.command == "record":
        from .recorder import record

        written = await record(args.provider, args.capability)
        _print({"count": len(written), "fixtures": written})
        return 0

    app = build()
    try:
        if args.command == "lookup":
            params = json.loads(args.params) if args.params else None
            result = await app.engine.lookup(
                args.query,
                category=args.category,
                domain=args.domain,
                provider=args.provider,
                capability=args.capability,
                params=params,
                limit=args.limit,
                refresh=args.refresh,
                allow_aggregators=args.allow_aggregators,
            )
            _print(result.to_dict())
            return 0
        if args.command == "audit":
            from .audit import audit_live, audit_registry, summarize

            findings = audit_registry(app.registry)
            if args.provider:
                findings = [f for f in findings if f.provider == args.provider]
            if args.live:
                live = await audit_live(app.registry, config=app.toolkit.config)
                if args.provider:
                    live = [f for f in live if f.provider == args.provider]
                findings += live
            report = summarize(findings)
            if args.strict and report["warnings"]:
                report["ok"] = False
            _print(report)
            return 0 if report["ok"] else 1
        if args.command == "providers":
            _print(app.registry.summary())
            for manifest in app.registry.manifests.values():
                caps = ", ".join(manifest.capabilities)
                print(f"  {manifest.id:<24} {manifest.category:<16} {caps}")
            return 0
        if args.command == "search":
            _print(await app.toolkit.providers_search(args.query, args.category, args.limit))
            return 0
        if args.command == "describe":
            _print(await app.toolkit.provider_describe(args.provider))
            return 0
        if args.command == "call":
            params = json.loads(args.params) if args.params else {}
            _print(
                await app.toolkit.provider_call(
                    args.provider, args.capability, params, args.cursor, args.refresh
                )
            )
            return 0
        if args.command == "fetch":
            _print(await app.toolkit.fetch_url(args.url))
            return 0
        if args.command == "tools":
            from .llamacpp import openai_tools

            _print(openai_tools())
            return 0
        if args.command == "grammar":
            from .llamacpp import grammar

            print(grammar())
            return 0
        if args.command == "route":
            candidates = await app.router.route(args.query, args.category, args.limit)
            from .regions import detect_regions

            _print(
                {
                    "query": args.query,
                    "router": app.router.status(),
                    "query_regions": sorted(detect_regions(args.query)),
                    "candidates": [candidate.as_dict() for candidate in candidates],
                }
            )
            return 0
        if args.command == "eval":
            from .evaluate import evaluate, load_cases

            report = await evaluate(app.router, load_cases(args.path) if args.path else None)
            _print({"router": app.router.status(), **report})
            return 0
        if args.command == "index":
            count = await app.router.prime()
            _print({"embedded": count, **app.router.status()})
            return 0
        if args.command == "cache":
            _print(app.cache.stats())
            return 0
    finally:
        await app.aclose()
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="mouseion", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("providers", help="list loaded providers")
    sub.add_parser("cache", help="cache row counts per provider")
    sub.add_parser("tools", help="emit OpenAI-format tool definitions")
    sub.add_parser("grammar", help="emit the GBNF grammar for a tool call")
    sub.add_parser("index", help="embed every provider document (needs MOUSEION_EMBED_URL)")

    p = sub.add_parser("audit", help="validate source policy and manifest invariants")
    p.add_argument("--live", action="store_true", help="also check evidence URLs")
    p.add_argument("--provider", help="audit one provider")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures")

    p = sub.add_parser("lookup", help="route, execute, and fail over within a capability cluster")
    p.add_argument("query")
    p.add_argument("--category")
    p.add_argument("--domain")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--provider")
    p.add_argument("--capability")
    p.add_argument("--params", default=None, help="JSON object; bypasses query mapping")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--allow-aggregators", action="store_true")

    p = sub.add_parser("route", help="show ranked routing candidates")
    p.add_argument("query")
    p.add_argument("--category")
    p.add_argument("--limit", type=int, default=8)

    p = sub.add_parser("eval", help="score the router against eval/routing.jsonl")
    p.add_argument("--path", default=None)

    p = sub.add_parser("record", help="record upstream fixtures into tests/fixtures")
    p.add_argument("--provider", default=None)
    p.add_argument("--capability", default=None)

    p = sub.add_parser("discover", help="harvest agency data.json catalogues and probe endpoints")
    p.add_argument("--catalog-limit", type=int, default=None)
    p.add_argument("--probe-limit", type=int, default=None)
    p.add_argument("--write", default=None, help="directory for generated manifests")

    p = sub.add_parser("search", help="search the provider index")
    p.add_argument("query")
    p.add_argument("--category")
    p.add_argument("--limit", type=int, default=8)

    p = sub.add_parser("describe", help="show a provider's capability schemas")
    p.add_argument("provider")

    p = sub.add_parser("call", help="call a provider capability")
    p.add_argument("provider")
    p.add_argument("capability")
    p.add_argument("params", nargs="?", default="{}", help="JSON object of params")
    p.add_argument("--cursor")
    p.add_argument("--refresh", action="store_true")

    p = sub.add_parser("fetch", help="fetch a URL as text")
    p.add_argument("url")

    args = parser.parse_args()
    try:
        sys.exit(asyncio.run(_run(args)))
    except ProviderError as exc:
        _print(exc.to_dict())
        sys.exit(1)


if __name__ == "__main__":
    main()
