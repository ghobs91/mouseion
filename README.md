# mouseion

**One tool surface over primary sources, for a model running on your own machine.**

Most questions an agent answers do not need a search engine. They need the
register, the filing, the dataset, the table, the catalogue, or the official
endpoint that a search engine would have cited. Mouseion is a local provider
registry that routes a question to the source that actually holds the record,
calls it, and returns a uniform, citable envelope.

It is not a search engine and not a hosted proxy. It is the routing and
provenance layer that sits between a model and a hundred heterogeneous official
APIs.

```
question
  → router (patterns, embeddings, lexical)
  → engine (cluster, rate limit, circuit breaker, cache)
  → adapter (REST, Socrata, CSV, ZIP, HTML table, XML, MCP)
  → envelope (records + where they came from + when)
```

## Why this exists

Three problems keep agents from using primary sources:

1. **Search is lossy by design.** A SERP is an index of pages about a record, not
   the record. The answer inherits the ranking, the snippet, and the aggregator's
   gaps, and the citation points at a summary rather than the source.
2. **Every source is its own project.** Auth, pagination, schema, rate limits,
   date formats, terms and quirks differ. Wiring ten sources into an agent is ten
   small integrations, each with its own failure modes.
3. **The easy fix reintroduces a middleman.** A hosted "one API for everything"
   proxy solves the wiring by taking custody of the credentials, the billing, and
   the coverage decisions. On a local machine those constraints disappear.

Mouseion keeps the useful part — a registry, a uniform envelope, deterministic
routing — and drops the rest.

## The thesis

1. **A source, not a search.** Route to the originating register or dataset
   first. A search engine is a fallback, not the default.
2. **Capabilities are the unit.** A question is answered by
   `(provider, capability)`, not by a provider in the abstract. "Recalled blood
   pressure pills" is `openfda/drug_recalls`.
3. **Provenance is part of the answer.** Every result says who originated it,
   when it was fetched, what it cost, and under what terms. A record without
   provenance is a rumour with a URL.
4. **Abstaining is a feature.** A router that always returns something teaches
   the model to answer from the wrong register. When no source covers a question,
   the correct answer is `web_search_required`, not a nearby dataset.
5. **Local-first.** Keys stay on your machine, requests come from your machine,
   and there is no billing layer between you and the source.

## What is in the box

| Piece | Responsibility |
|---|---|
| **Registry** | YAML manifests in `providers/`: source policy, auth, capabilities, params, projections |
| **Adapters** | The shapes: `rest_json`, `socrata`, `csv`, `zip`, `html_table`, `atom`, `sdmx_json`, `cdx`, `faa_nas`, `mcp_remote` |
| **Router** | Exact patterns first, then optional embeddings, blended with IDF-aware lexical scoring |
| **Engine** | Canonical query translation, cluster-only failover, rate limiting, circuit breaker, negative cache, single-flight |
| **Envelope** | One result shape: records, source status, attribution, terms, cursor, missing fields |
| **Surfaces** | MCP server (four tools), CLI, local dashboard |

## The four tools

| Tool | Purpose |
|---|---|
| `providers_search` | Rank providers by domain, capability, region and jurisdiction |
| `provider_describe` | Show a provider's capability schemas, params and examples |
| `provider_call` | Call one capability with validated params and a cursor |
| `fetch_url` | Fetch a URL as text when the model needs the page itself |

The MCP server is the same registry the CLI and dashboard read. Add it to any
MCP client:

```json
{
  "mcpServers": {
    "mouseion": {
      "command": "/absolute/path/to/mouseion/.venv/bin/python",
      "args": ["-m", "mouseion.server"]
    }
  }
}
```

## How routing works

A manifest declares one of ten domains and a finer `sub_category`:

| Domain | Providers | Examples |
|---|---:|---|
| `civic_local` | 17 | campaign finance, election results, elected officials |
| `academic_scientific` | 14 | arXiv, Crossref, OpenAlex, PubMed, PMC, UniProt, GBIF |
| `geo_earth_observation` | 14 | Nominatim, Overpass, USGS, NOAA, NWS, GeoNames, FEMA |
| `reference` | 13 | Wikipedia, MLB, MusicBrainz, TMDB, USAJobs, UEFA, FIFA, five soccer leagues |
| `financial_sovereign` | 11 | ECB, FRED, BLS, World Bank, Treasury, CFTC, SEC, FEC, GLEIF, Companies House |
| `governance_legislative` | 11 | Congress, GovInfo, Federal Register, UK Parliament, CourtListener |
| `developer_infrastructure` | 9 | npm, PyPI, crates.io, Go, Maven, GitHub, NVD, Hugging Face |
| `archives_web_state` | 5 | Wayback, Internet Archive, Library of Congress, UK National Archives |
| `transport_infrastructure` | 4 | OurAirports, FAA airport status, Schiphol flights |
| `commerce` | 2 | Shopify Global Catalog, Open Food Facts |

Routing runs in three passes:

1. **Patterns.** `NCT04631731`, a DOI, a CVE or a CIK is exact and cheap. A regex
   beats cosine similarity for an identifier.
2. **Embeddings (optional).** A local or OpenAI-compatible embedder handles
   paraphrase. Without one, the router degrades to lexical scoring rather than
   failing.
3. **Lexical.** IDF-weighted token overlap, with a small name bonus. Generic
   tokens such as `new`, `city` and `results` do not earn a bonus, and time words
   such as `year` and `recent` are ignored.

The engine then translates a canonical query into source params through each
capability's `query_map`, which is what makes failover possible without inventing
a shared schema.

### Failover is a contract, not a hope

Capabilities join a `cluster` only when they can accept the same canonical
fields. Failover happens inside a cluster, never across categories, and never on
a valid empty result. "No Class I recalls" is an answer, not a reason to query a
different register.

State and local sources add a second constraint: **jurisdiction**. Every civic
manifest declares the jurisdiction ids it covers (`US-CA`, `US-CA-LOS-ANGELES`,
`US-WA-KING`). The router resolves place names in the query and drops providers
outside that coverage. When a query names a local office in a place no provider
covers, the engine returns `no_jurisdiction_coverage` with the uncovered ids
instead of substituting a federal or other-state source.

### The router may say nothing

If nothing clears the floors, `lookup` returns `web_search_required` with a
reason (`no_candidates`, `no_jurisdiction_coverage`, `missing_required_param`,
`all_sources_failed`). That is deliberately better than a confident wrong answer.

## The envelope

Every call returns the same shape, whichever adapter produced it:

```json
{
  "provider": "openfda",
  "capability": "drug_recalls",
  "source_status": {
    "url": "https://api.fda.gov/drug/enforcement.json",
    "http": 200,
    "fetched_at": "2026-11-01T09:00:00Z",
    "cache_age_s": 0.0
  },
  "attribution": "U.S. Food and Drug Administration",
  "terms": null,
  "cursor": null,
  "records": [
    {
      "source_record_id": "D-1234-2026",
      "source_url": "https://www.fda.gov/...",
      "retrieved_at": "2026-11-01T09:00:00Z",
      "data": { "product": "...", "reason": "..." }
    }
  ],
  "missing": [],
  "truncated": false
}
```

`missing` lists projected fields the upstream did not return; `truncated` and
`cursor.next` say whether there is another page. The model can cite the source
without guessing at the shape.

## Source policy

Two tiers, enforced by `mouseion audit`:

- **Tier 1** — no account or API key. A contact User-Agent may be required
  where the source asks for one (SEC EDGAR).
- **Tier 2** — permanently free, no credit card, no trial. Free keys and free
  OAuth are allowed; "free tier with a card on file" is not.

The audit also checks that the evidence for "free" was verified recently, that
the originator domain matches the host, that Tier 1 carries no key, and that
a failover cluster has a query map the engine can use.

Some sources are genuinely secondary. They are marked
`provenance: aggregator_exception`, `is_primary: false`, with a reason, and the
engine never uses them automatically:

- **Wikipedia** — tertiary reference, useful for orientation and citations.
- **football-data.org** — aggregates league feeds and does not disclose upstreams.
- **CourtListener, OpenAlex, GBIF, OpenLibrary** — indexes with an explicit
  exception where the primary endpoint is impractical.

Commercial platforms are labelled `commercial_platform`: Shopify is primary for
its own catalogue, not a public-record originator.

## Coverage

**100 providers, 148 capabilities, ten domains.** The full inventory is in
[`docs/PROVIDERS.md`](docs/PROVIDERS.md). The demand-shaped gap analysis, using
Firecrawl Alexandria's categories as a reference, is in
[`docs/PROVIDER-TARGETS.md`](docs/PROVIDER-TARGETS.md).

Highlights:

- **Local elections** — 17 providers across 18 jurisdictions, a registry of 101
  state, county and city jurisdictions, and a parallel discovery pipeline. A
  Chicago query abstains rather than reaching New York's disclosures.
- **Commerce** — Shopify's read-only UCP catalogue and Open Food Facts.
- **Soccer** — the Premier League, UEFA, FIFA, LALIGA, Bundesliga, Serie A and
  Ligue 1 read from their own site backends, with football-data.org kept as an
  explicit fallback.
- **Flight status** — FAA airport status and Schiphol per-flight status, from the
  operator rather than a tracker.
- **Science and health** — arXiv, Crossref, OpenAlex, PubMed, PMC, UniProt,
  PubChem, ClinicalTrials.gov, openFDA, CMS.
- **Money and law** — ECB, FRED, BLS, Treasury, SEC, FEC, Congress, GovInfo,
  Federal Register, UK Parliament.

## Quickstart

Requires Python 3.11+ and `uv`.

```bash
uv sync --extra dev

.venv/bin/mouseion audit          # source policy, free-access evidence, TTLs
.venv/bin/mouseion doctor         # local readiness
.venv/bin/mouseion providers      # live counts and the registry

.venv/bin/mouseion lookup "recalled blood pressure medication" --limit 3
.venv/bin/mouseion lookup "who funded a New York campaign" --domain civic_local
.venv/bin/mouseion call ecb yield_curve '{"limit": 2}'
```

Credentials for providers that need them live in `.env` or in the dashboard.
Environment variables win over the dashboard store; the dashboard labels which
one is in use.

### Dashboard

```bash
.venv/bin/mouseion dashboard --open
# http://127.0.0.1:8765/
```

Five views: Sources, Domains, Jurisdictions, Credentials, Audit. It is one static
page plus `/api/snapshot`, served by the standard library. Favicons are fetched
server-side through an allowlist and cached locally; the browser never talks to a
third-party icon service. Credential writes are loopback-only and require a
same-origin request with a custom header.

### Local elections

```bash
.venv/bin/mouseion civic list
.venv/bin/mouseion civic discover --concurrency 16 --out eval/civic-coverage.json
.venv/bin/mouseion civic datasets --min-rows 1000 --out eval/civic-datasets.json
```

`civic discover` probes every authority page in parallel and records what it
actually serves: `json`, `csv`, `html_tables`, `html`, `blocked`, `tls_error`.
`civic datasets` searches Socrata's national catalogue for structured tables on
official portals and ranks them by row count. A jurisdiction becomes a provider
only after its surface passes the probe.

## Adding a source

A provider is a YAML file. The schema is [`mouseion/manifest.py`](mouseion/manifest.py); the short
version:

```yaml
id: example
title: Example Registry
category: government
domain: governance_legislative
sub_category: filings
base: https://api.example.gov
adapter: rest_json
source:
  is_primary: true
  originator: "Example Agency"
  originator_domain: example.gov
  provenance: official
  tier: 1
  auth_policy:
    free: true
    credit_card_required: false
    trial: false
    evidence_url: https://api.example.gov/terms
    verified_at: 2026-11-01
cache_ttl: 3600
capabilities:
  search:
    description: Search filings by keyword.
    path: /v1/filings
    params:
      query: {type: string, required: true}
      limit: {type: integer, default: 20}
    record_path: results
    id_field: id
    fields: {id: id, title: title, date: filed_at}
    exemplars: ["find a filing by keyword"]
    example: {query: "example", limit: 3}
```

Then:

1. `mouseion audit` — fix every error.
2. `mouseion record --provider example` — commit the fixture.
3. Add a routing case to `eval/routing.jsonl`.
4. `pytest` and `pytest -m live` — the fixture replay and the upstream contract.
5. Update the inventory in `docs/PROVIDERS.md`.

A source is not "covered" until all five pass. That is why the count is smaller
than a directory of every API.

## Design principles

- **Deterministic over clever.** Patterns and manifests before similarity; a
  regex for an identifier, a cluster for a failover.
- **No silent substitution.** Failover only inside a cluster. No cross-region
  fallback. No aggregator in automatic routing.
- **Empty is an answer.** A valid zero-result response is a success; a weak match
  is not.
- **Provenance travels.** Attribution, terms and fetch time are in every
  envelope, not in a side document.
- **Local-first.** No hosted credential custody, no credits, no billing.
- **The registry is the product.** Code implements shapes; the knowledge lives in
  reviewable YAML.

## What this is not

- Not a search engine or a crawler.
- Not an aggregator of aggregators; secondary sources are explicit exceptions.
- Not a hosted service or a billing proxy.
- Not a knowledge graph, an embedding index, or a model.
- Not a promise of exhaustive coverage. It is a spine you can extend.

## Honest limits

- **Coverage is uneven.** Civic is deepest in 18 jurisdictions; the other 83
  registry entries are seeded and probed, not all promoted. Transport has four
  providers.
- **Some feeds are undocumented.** The Premier League and the four domestic
  leagues use the same browser-facing backends their sites call. They need
  browser headers and can change without notice; season keys are manifest
  defaults, so a new season is a small edit.
- **Keys still exist.** FRED, Congress, GovInfo, GitHub, CourtListener, SEC,
  Shopify, Companies House, GeoNames, TMDB, USAJobs, Schiphol and
  football-data.org need credentials or a contact header. The dashboard makes
  them easier to manage; it does not remove them.
- **Embeddings are optional.** Without a backend the router is lexical, which is
  fine for identifiers and weaker for paraphrase.
- **The eval is small.** It measures routing, not answer correctness.

## Roadmap

- **Civic depth** — promote the remaining jurisdictions from the coverage matrix;
  add a browser-capable probe for the 30+ authorities behind edge WAFs.
- **Finance stretch** — IMF, OECD, BIS SDMX.
- **People and entities** — OpenSanctions, state business registries.
- **News** — GDELT as an explicit aggregator exception.
- **Transport** — more official airport and rail feeds.
- **Documentation** — keep the gap matrix current as sources are promoted.

## Repository map

```
providers/        100 YAML manifests, one per source
mouseion/
  manifest.py     schema: domain, source policy, auth, capabilities, projection
  registry.py     loading, lexical scoring, provider cards
  router.py       patterns → embeddings → lexical, jurisdiction filtering
  engine.py       canonical query, cluster failover, rate limits, breaker
  client.py       prepare → cache → HTTP → normalize → envelope
  adapters/       rest_json, socrata, csv (+ZIP), html_table, atom, sdmx_json, cdx, faa_nas
  remote_mcp.py   OAuth and client-credentials MCP, UCP profile injection
  civic.py        jurisdiction registry, coverage discovery, dataset catalogue
  dashboard.py    local dashboard and snapshot API
  web/            static dashboard
  credentials.py  local credential store
tests/            488 offline tests; live contracts behind -m live
eval/             routing cases, coverage matrix, dataset catalogue
docs/             architecture, roadmap, targets, inventory
```

## Tests

```bash
.venv/bin/pytest              # 488 offline: fixture replay
.venv/bin/pytest -m live      # 146 live contracts: every upstream, manifest example
.venv/bin/mouseion audit      # source policy, evidence age, tier/auth, TTLs
```

The contract suite never asserts data values. It asserts that each manifest still
describes the response: the record path resolves, the projection returns fields,
the cursor behaves, and the source status is attached.

## License

MIT.
