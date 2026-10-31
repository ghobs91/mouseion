# Re-calibration: Dynamic MCP Routing Engine

**Date:** 2026-10-29
**Status:** Accepted for Phase 0–2. Phase 3 breadth and the auto-routing MCP surface
remain gated on the Phase 2 evaluation.
**Progress:** Phase 0 complete: git baseline, README and `.env.example` corrected,
`candidates_auto/` removed and ignored as regenerable output. Phase 1 complete: schema
fields, 36-manifest backfill, `mouseion audit`, audit tests, and CI. Phase 2 complete:
canonical query, cluster-only failover, attempt tracing, rate limiting, circuit breaker,
single-flight, and `mouseion lookup`. Phase 3 in progress: `financial_sovereign`,
`academic_scientific`, `developer_infrastructure`, `geo_earth_observation`,
`governance_legislative`, and `archives_web_state` have met their targets; transport
remains. Remote MCP/OAuth support was added for CourtListener; the read-only Shopify
Global Catalog is live. The local-election track now has a jurisdiction registry
(96 jurisdictions), a parallel discovery pass over 182 authority URLs, and a Socrata
catalog search that promoted five campaign-finance providers in wave 1. IMF, BoJ, OECD, BIS, and on-chain nodes remain
as Cat 1 stretch.
**Basis:** the 2026-10-29 specification plus the repository as it actually stands:
79 providers, 105 capabilities, 408 passing offline tests, 103 live contract tests.

---

## 0. Decisions

**Accepted by the operator on 2026-10-29:**

| Decision | Choice |
|---|---|
| Stack | Python 3.11 + YAML manifests. No TypeScript rewrite. |
| First milestone | Phase 0–2: docs truth, schema/audit, canonical-query failover engine. |
| Secondary indexes | Documented `aggregator_exception`; excluded from Tier 1 failover and never described as primary. |

**Plan defaults (revisit if the implementation proves them wrong):**

- Taxonomy is additive: required `domain` + `sub_category`; `category` stays as a
  compatibility tag until the router text and docs no longer need it.
- The auto-routing engine ships as a library and `mouseion lookup` CLI first. It becomes
  an MCP tool only after the Phase 2 failover eval passes.

---

## 1. Verdict

Adopt the specification's **sourcing policy**. Reject most of its **implementation
contract**.

The policy — primary originators only, keyless first, free-key second, no credit card,
no trial, no metered cliff — is exactly the right constraint for this project. The
repository already follows it informally: 33 of 36 providers are keyless, two use a
free key (FRED, CourtListener), and one requires a contact User-Agent (SEC).

The implementation section, however, describes a greenfield TypeScript service with a
per-source `execute()` function. This repository is a working Python engine whose
reliability comes from the opposite design: declarative YAML manifests, two generic
adapters, fixture replay, and per-capability projection. Rewriting it to match the
spec's file path would discard the parts that make the answers citable.

**Recommendation:** treat the spec as product requirements, not as an architecture
mandate. Translate its contract into the existing manifest model. Reject four specific
directives that would make the engine less correct: per-source `execute`, category-wide
failover, failover on empty results, and an in-memory-only cache.

---

## 2. What the specification gets right

1. **Primary-originator rule.** The distinction between the register and the article
   about the register is the whole product. The repo already says this in the README;
   the spec makes it enforceable.
2. **Two-tier auth policy.** Tier 1 keyless, Tier 2 permanently free. This is a better
   constraint than "free tier" because it excludes trial credits and metered cliffs.
3. **Seven-domain taxonomy.** The current 11 free-form categories (`finance`, `places`,
   `biomedical`, `government`, ...) are topic tags, not routing domains. A fixed domain
   enum is useful for coverage accounting and for ordering candidates.
4. **Machine-readable search fallback.** `{ "web_search_required": true }` is a better
   contract than a prose hint, provided it is emitted only when the engine has actually
   exhausted semantically equivalent sources.
5. **Explicit User-Agent and pruning requirements.** Both already exist, but the spec is
   right to make them non-optional and centrally enforced.
6. **Per-source rate limits.** The manifest has no rate-limit field today. That is a real
   gap.

---

## 3. Where the specification conflicts with the repository

| # | Specification says | Repository does | Why the difference matters | Recommendation |
|---|---|---|---|---|
| 1 | TypeScript, `src/router/registry.ts` | Python 3.11, `mouseion/`, YAML `providers/` | A rewrite costs the 163 tests, 36 manifests, fixture corpus, adapters, cache, and MCP server. No runtime requirement justifies it. | **Keep Python.** If a TS client is needed later, generate types from the manifest schema. |
| 2 | `APISource.execute(params)` per source | `Capability` + `rest_json`/`socrata` adapters; `ProviderClient.call()` executes | Per-source functions turn a data registry into code, losing hot reload, fixture replay, and uniform contract tests. | **Translate the contract.** Keep `execute` in the adapter/client layer; manifests stay declarative. |
| 3 | Seven categories replace the registry taxonomy | 11 free-form `category` values | Existing tags are useful sub-topics; deleting them breaks routing text and docs. | **Add `domain` (7 enum) + `sub_category`.** Keep `category` as a compatibility tag, then deprecate. |
| 4 | Fail over on HTTP 429, timeout, **or empty response** | Empty is a valid success (`0 records`) | "No Class I recalls" is an answer. Failing over on empty turns a correct zero into a different source's non-zero, or into a false `web_search_required`. | **Never fail over on valid empty.** Fail over only on transport, HTTP, auth, or schema failure. |
| 5 | Fail over to the next source **in the category** | Router returns ranked capabilities; model chooses | Category membership is not semantic equivalence. If FRED is down, World Bank cannot answer a US unemployment series. Blind failover produces plausible, wrong data. | **Failover only within a capability cluster** with a canonical query contract (§6). |
| 6 | In-memory TTL cache, 1 hour default | SQLite raw-body cache, 300 s default, per-manifest override | In-memory loses provenance and snapshot-safe pagination across restarts; SQLite is already local and fast. | **Keep SQLite.** Add per-domain TTL defaults, single-flight, and short negative caching. No in-memory-only layer. |
| 7 | Deterministic intent classifier | Patterns → embeddings → lexical, with floors and abstention | Natural-language intent classification is not deterministic. The *policy* can be deterministic given the scores; the classifier cannot. | **Say "deterministic policy over a probabilistic retriever."** Keep patterns first, embeddings second, lexical third, abstention allowed. |
| 8 | Programmatically verify primary originator and "permanently free" | No provenance metadata at all | Domain ownership can be checked; "free forever" and "primary" are policy claims, not facts a script can prove. Pretending otherwise creates false confidence. | **Machine-checkable evidence + human sign-off + staleness gate** (`mouseion audit`, §7). |
| 9 | 1-hour cache default | 300 s default; some endpoints need 24 h, some need 60 s | One TTL for legal registers and seismic feeds is wrong in both directions. | **Per-domain TTL table** (§6.4). |
| 10 | "Standardized query params" shared by all sources | Each capability has its own params (`series_id`, `indicator`, `q`, ...) | This is the hidden assumption behind category-wide failover. Without a canonical query layer, failover cannot map params safely. | **Add `CanonicalQuery` + per-capability `query_map`** (§6.1). |

### Specification self-contradictions to resolve

- **Open-Meteo is named as a primary hydro-meteorological source.** It is a secondary
  aggregator of national weather models. Under the spec's own "primary originators only"
  rule it must be excluded or explicitly marked as an exception. Prefer NOAA/NWS, DWD,
  Météo-France, and ECMWF.
- **"Deterministic fallback bridge" vs. an LLM-supplied prompt.** The query is
  open-ended; the engine can be deterministic in *selection policy*, not in *intent
  inference*. State the narrower claim.
- **"On-chain protocol nodes" as a primary source.** There is no single authoritative
  node. A self-hosted Bitcoin Core or Erigon node is primary for the operator; a public
  RPC is a provider. Require `provenance: self_hosted_node` for Tier 1, and cross-check
  critical reads across two independent nodes.

---

## 4. What already exists and should not be rebuilt

| Requirement | Existing mechanism | Gap |
|---|---|---|
| Primary-only sourcing | 33/36 keyless; 2 free key; 1 contact UA | No `tier`, `is_primary_source`, originator, or evidence metadata |
| Key tier enforcement | `auth.type` + `env` + structured missing-credential errors | No free/no-card/no-trial evidence, no audit |
| User-Agent compliance | `Settings.user_agent`; per-manifest `headers`; SEC-specific UA | No per-source UA policy or rotation; no central audit |
| Response pruning | `fields`, `pluck`, `drop`, per-capability projection | No generic prune fallback for auto-discovered manifests |
| Caching | SQLite raw-body TTL, per-manifest `cache_ttl`, snapshot cursors | No per-domain defaults, single-flight, or negative cache |
| Routing | Patterns + embeddings + lexical, floors, region filter, abstention | No domain prior, no execution/failover |
| Fallback | `fetch_url` + no-match hint | No machine-readable `web_search_required` |
| Provenance | Envelope carries URL, HTTP status, `fetched_at`, `cache_age_s`, attribution, terms | No attempt trace across failover |
| Tests | 163 offline fixture/unit tests; 44 live contract tests | No failover, rate-limit, circuit-breaker, or audit tests |
| MCP surface | Four stable tools: search, describe, call, fetch | No auto-routing tool (deliberate; see §6.5) |

**Do not rebuild these.** The spec's requirements are mostly additions to this spine,
not replacements for it.

---

## 5. Documentation drift (fix before planning further)

The README is behind the code and will mislead the next planning session:

- README says **24 providers, 29 capabilities, 127 offline tests, 27 live tests**.
  Actual: **36 providers, 44 capabilities, 163 offline tests, 44 live tests**.
- README's "What's deliberately not here" lists Census, FDA device/food, and SEC
  submissions by CIK. All three now exist (`census_geocoder`, `openfda.device_recalls`,
  `sec_edgar.filings_by_cik`).
- `.env.example` lists `BLS_API_KEY` and `BEA_API_KEY` as if they were required. BLS is
  keyless in this registry; BEA is not integrated.
- `candidates_auto/` contains 21 unreviewed generated manifests. They are outside
  `providers/`, so they are invisible to the registry. Either promote them through the
  audit or delete them; leaving them implies coverage that does not exist.
- The repository is **not under version control**. That is a planning risk, not a code
  defect. `git init` before Phase 1 is strongly recommended.

---

## 6. Recommended architecture delta

### 6.1 Canonical query and capability clusters

The spec's failover design needs one missing layer. Add:

```python
class CanonicalQuery(BaseModel):
    text: str                      # original user text
    identifier: str | None = None  # DOI, NCT, CVE, CIK, LEI, ...
    entity: str | None = None      # company, country, series, drug, ...
    geo: str | None = None         # ISO country/region
    time_range: tuple[str, str] | None = None
    limit: int = 10
```

Each capability declares:

```yaml
cluster: us_unemployment_rate
query_map:
  entity: series_id
  time_range: start_date
  limit: limit
```

`cluster` is the unit of failover, not `category`. Two capabilities may fail over only
when they declare the same cluster and can accept the canonical fields. Free-text
search capabilities can share a cluster such as `web_text_search`; a FRED series and a
World Bank indicator cannot.

**Rule:** no cluster, no failover. The engine returns `web_search_required` instead of
guessing that two sources are interchangeable.

### 6.2 Manifest schema additions

Add to `Manifest`:

```yaml
domain: financial_sovereign        # one of the 7
sub_category: central_banking
source:
  is_primary: true
  originator: "European Central Bank"
  originator_domain: ecb.europa.eu
  provenance: official             # official | intergovernmental | registry |
                                   # self_hosted_node | aggregator_exception
  tier: 1
  auth_policy:
    free: true
    credit_card_required: false
    trial: false
    evidence_url: https://www.ecb.europa.eu/stats/...
    verified_at: 2026-10-29
  rate_limit:
    per_min: 60
    burst: 10
    concurrency: 4
cache_ttl: 3600
```

Python model additions:

```python
class AuthPolicy(BaseModel):
    free: bool
    credit_card_required: bool
    trial: bool
    evidence_url: str
    verified_at: date

class RateLimit(BaseModel):
    per_min: int
    burst: int = 10
    concurrency: int = 4

class SourcePolicy(BaseModel):
    is_primary: bool
    originator: str
    originator_domain: str
    provenance: Literal["official", "intergovernmental", "registry",
                        "self_hosted_node", "aggregator_exception"]
    tier: Literal[1, 2]
    auth_policy: AuthPolicy
    rate_limit: RateLimit
```

`is_primary` must be `true` for every source in `providers/`. The only way to ship a
secondary source is `provenance: aggregator_exception` plus an `exception_reason`, and
it must not be used for Tier 1 failover.

**Keep `execute` out of the manifest.** The executor is `ProviderClient.call()` plus the
adapter named by `adapter:`. A bespoke upstream gets a registered adapter, not a lambda
in a data file.

### 6.3 Router pipeline (deterministic policy, probabilistic signals)

```
query
  → normalize (tokens, identifiers, regions)
  → retrieve candidates (patterns → embeddings → lexical)
  → apply domain prior (soft boost, never a hard gate)
  → filter: region conflict, missing credential, open circuit
  → order: Tier 1 before Tier 2; then score; then cache freshness
  → execute with timeout, rate limit, one retry on retryable errors
  → classify outcome:
        success (including 0 records)
        retryable failure (429, 408, 5xx, timeout, connection)
        terminal failure (400, 401, 403, 404 unless empty_on_404, 422)
        schema failure (record path missing, no id on any record)
  → failover only on retryable/schema, only within the same cluster
  → return envelope with attempts[] or web_search_required
```

`attempts[]` must be returned even on success, so the host can see what was tried:

```json
{
  "query": "...",
  "attempts": [
    {"provider": "fred", "capability": "series_observations", "outcome": "http_429", "ms": 120},
    {"provider": "bls", "capability": "series_observations", "outcome": "ok", "ms": 240}
  ],
  "web_search_required": false
}
```

**Do not fail over on:** valid empty results, region conflicts, terminal 4xx caused by
caller params, or a cluster mismatch. **Do fail over on:** 429, 5xx, timeout, connection
reset, missing credential for a Tier 2 source (skip before execution), and schema
breakage.

### 6.4 Cache policy

Keep SQLite. Add these defaults, overridable per manifest:

| Domain | Default TTL | Rationale |
|---|---:|---|
| `governance_legislative` | 24 h | Statutes and registers change slowly |
| `archives_web_state` | 7 d | Historical snapshots are immutable |
| `academic_scientific` | 24 h | Citation metadata is stable |
| `developer_infrastructure` | 15 min | Package versions and advisories move |
| `financial_sovereign` | 1 h | Series update daily/weekly; some intraday |
| `transport_infrastructure` | 30 s (RT) / 24 h (static GTFS) | Realtime vs. schedule |
| `geo_earth_observation` | 60 s (weather/seismic) / 24 h (boundaries) | Mixed freshness |

Add:
- **Single-flight** per cache key, so ten concurrent identical calls make one upstream request.
- **Negative cache** for 429/5xx: 30–60 s, so failover does not hammer a down source.
- **No in-memory-only cache.** If profiling later shows SQLite is a bottleneck, add an
  LRU in front of it; do not replace the durable layer.

### 6.5 Tool surface

The README promises four tools "stable forever". Keep that promise.

- Keep `providers_search`, `provider_describe`, `provider_call`, `fetch_url`.
- Add the auto-routing engine as a **library and CLI first** (`mouseion lookup`).
- Promote it to an MCP tool (`primary_lookup`) only after the failover eval passes and
  the four-tool surface has been versioned. The model-in-the-loop path stays available.

This avoids a breaking change to every existing MCP client while still building the
engine the spec asks for.

### 6.6 Response contract

Keep the existing envelope. Add two fields:

```json
{
  "attempts": [ ... ],
  "web_search_required": false,
  "search_reason": null
}
```

`web_search_required` is `true` only when:
1. no candidate clears the routing floors, or
2. every candidate in the matching cluster failed with a retryable/schema error, or
3. the only candidates are region-conflicting or credential-blocked.

It is `false` for a valid zero-record envelope.

---

## 7. Source audit: the honest version of "programmatically verify"

`mouseion audit` should be a CI gate with two layers.

### Static checks (every commit)

- `domain` is one of the seven; `sub_category` is non-empty.
- `source.is_primary == true` unless `provenance == aggregator_exception`.
- `tier == 1` implies `auth.type in {none, user_agent}` and no `env` key.
- `tier == 2` implies `auth.type == api_key`, `env` is set, and
  `auth_policy.free == true`, `credit_card_required == false`, `trial == false`.
- `auth_policy.evidence_url` resolves to the originator's domain or an official
  documentation host; `verified_at` is less than 180 days old.
- `originator_domain` matches the manifest `base` host, or is in the official-domain
  allowlist for that originator.
- `rate_limit.per_min > 0` and `concurrency > 0`.
- `cache_ttl` is within the domain's allowed range.
- Every capability that declares `cluster` has a `query_map`.
- Blocklist check: no `news`, `blog`, `medium.com`, `substack.com`, `reddit.com`,
  `x.com`, `twitter.com`, `facebook.com`, `linkedin.com`, `yelp`, `tripadvisor`,
  `bloomberg`, `reuters`, `yahoo`, `marketwatch`, `seekingalpha`, `coindesk`,
  `cointelegraph` in `base` or `docs_url`.

### Live checks (nightly)

- `evidence_url` returns 200 (or a documented 403 that still proves the policy page exists).
- Tier 1 base endpoint returns non-401/403 without credentials.
- Tier 2 base endpoint returns 401/403 without the key and 200 with it.
- The manifest `example` still produces a valid envelope.

### What the audit cannot prove

"Permanently free" and "primary originator" are claims about the world, not properties
of a response. The audit can enforce **evidence, consistency, and freshness**. A human
must sign the evidence note. That is the correct division of labor; pretending a script
can prove the claim is how stale assumptions ship.

### Known classification problems to resolve now

| Provider | Current label | Problem | Recommendation |
|---|---|---|---|
| `courtlistener` | `legal`, free key | Free Law Project is a nonprofit aggregator of court opinions, not a court. | `provenance: aggregator_exception`, not primary. Consider removing from Tier 1. |
| `openalex` | `research` | Secondary index of Crossref/PubMed/arXiv, not an originator. | `aggregator_exception` or drop; prefer Crossref/DataCite/arXiv for primary. |
| `gbif` | `life-sciences` | Aggregates occurrence records from institutions. | `aggregator_exception`; do not count toward primary coverage. |
| `openlibrary` | `reference` | Internet Archive catalog, aggregated bibliographic metadata. | `aggregator_exception`; ISBN primary source is national agencies, not a single API. |
| `open-meteo` (proposed) | — | Secondary aggregator of national weather models; the spec names it as primary. | Reject as primary; use NOAA/NWS/DWD/Météo-France/ECMWF. |
| `world_bank` | `finance` | Intergovernmental, but some series are sourced from national statistics. | Accept as `intergovernmental`; cite the originating series where known. |
| `fred` | `finance` | Federal Reserve Bank of St. Louis; some series are third-party. | Accept as `official` for Fed-owned series; mark third-party series in metadata. |

---

## 8. Coverage matrix and build order

Current counts are from `Registry.load(providers).summary()`. "Named gaps" are the
spec's own examples plus the obvious adjacent authorities.

| Domain | Current | Target | Named gaps | Priority |
|---|---:|---:|---|---|
| `financial_sovereign` | 10 | 10 | Target met: ECB; existing BLS, CFTC, FRED, GLEIF, openFEC, SEC, Treasury, USAspending, World Bank. Stretch: IMF, BoJ, OECD, BIS, on-chain nodes | P1 |
| `academic_scientific` | 14 | 14 | Complete: arXiv, PMC, PubChem, UniProt, Wikidata; existing Crossref, DataCite, OpenAlex, ROR, Zenodo, PubMed, ClinicalTrials, NIH RePORTER, GBIF | P1 |
| `developer_infrastructure` | 8 | 8 | Complete: npm, PyPI, crates.io, Go proxy, Maven Central, CISA KEV, GitHub REST | P1 |
| `geo_earth_observation` | 12 | 12 | Complete: Nominatim, Overpass, NASA EONET, NOAA Tides; existing Census, NHC, NWS, NYC Open Data, OpenFEMA, DataSF, USGS earthquakes, USGS water | P2 |
| `archives_web_state` | 5 | 5 | Complete: Internet Archive, Wayback, Library of Congress, UK National Archives; existing OpenLibrary | P2 |
| `transport_infrastructure` | 1 | 8 | 5–8 transit agencies with GTFS/GTFS-RT; State Dept travel; ISBN via OpenLibrary (exception) | P3 |
| `governance_legislative` | 11 | 10 | Target met: Congress.gov, GovInfo, UK Parliament, CourtListener MCP (OAuth); existing CMS, CourtListener REST, data.gov.au, Federal Register, GOV.UK, Grants.gov, openFDA | P2 |
| `commerce` | 1 | 1 | Shopify Global Catalog (read-only UCP MCP) | P2 |
| `civic_local` | 17 | 50 states + DC + major cities | Registry + discovery built: 101 jurisdictions, 182 authority URLs probed; routing is jurisdiction-aware and abstains when uncovered. Wave 1 promoted 14 Socrata providers across 11 jurisdictions, including a consolidated King County results provider spanning 73 elections and a ZIP bulk provider for Colorado; 89 candidate datasets catalogued | P2 |

Notes on specific targets:

- **UPC/GS1** is likely infeasible under the policy: GS1 membership and product APIs are
  not permanently free. Narrow this to ISBN/ISSN via library and DOI registries, and
  document the exclusion.
- **On-chain**: a public RPC is not a primary source. Build a `self_hosted_node` adapter
  for Bitcoin Core/Erigon; treat public RPCs as Tier 2 `node_operator` with cross-check.
- **Transit**: list agencies, not aggregators. Transitland and OpenMobilityData are
  secondary. Start with MBTA, BART, MTA, TfL, SFMTA, and one GTFS-RT feed.
- **Archives**: Wayback is primary for its own captures; NARA and LoC are primary for
  their holdings. Europeana is an aggregator; mark it as an exception if added.

Target total: roughly **60 providers**, but coverage per domain and eval cases matter
more than the number. Do not add a provider without a fixture, a live contract, an audit
entry, and at least two routing eval cases.

---

## 9. Execution plan

### Phase 0 — Truth and guardrails (0.5–1 day)

- Fix README counts and the stale "not here" list.
- Fix `.env.example`; remove keys for providers that are keyless or absent.
- Triage `candidates_auto/`: promote, or delete.
- `git init` and a first commit (needs approval; no repo exists today).
- Add this document to `docs/`.

**Gate:** `mouseion providers` output matches the README; 163 tests pass; a fresh clone
can run the quickstart.

### Phase 1 — Schema and audit (1–2 days)

- Add `domain`, `sub_category`, `source`, `cluster`, `query_map`, `rate_limit` to
  `manifest.py`.
- Backfill all 36 manifests. This is the bulk of the work; do it in one pass so the
  audit has a clean baseline.
- Implement `mouseion audit` (static) and `mouseion audit --live`.
- Add tests that seed each violation and assert a non-zero exit.
- Wire the audit into a pre-commit hook or CI.

**Gate:** 36/36 manifests pass; five seeded violations fail; no routing behavior changes;
163 tests still pass.

### Phase 2 — Canonical query and failover engine (2–3 days)

- Add `mouseion/engine.py` with `CanonicalQuery`, cluster selection, execution, failover,
  and `attempts[]`.
- Add a token-bucket rate limiter, circuit breaker, negative cache, and single-flight.
- Add `mouseion lookup` CLI.
- Fault-injection tests: 429, timeout, 500, schema break, valid empty, region conflict,
  missing Tier 2 key.
- Add `web_search_required` to the engine result and to `providers_search`'s no-match path.

**Gate:** valid empty never fails over; cross-cluster never fails over; region conflict
never fails over; every failure produces an attempt trace; p95 overhead under 50 ms
excluding upstream time.

**Implemented:** `mouseion/engine.py`, `mouseion lookup`, and 18 engine tests. The
shipped clusters are `scholarly_work_search`, `government_content_search`, and
`entity_name_search`. `CanonicalQuery` supports `text`, `identifier`, `entity`, `geo`,
and `limit`; `time_range` mapping is deferred until a cluster needs it. Identifier
extraction covers NCT, DOI, CVE, CIK, PMID, and bare numeric ids. An explicit
`--provider` is exact and does not fail over; source-specific `--params` require an
explicit provider.

### Phase 3 — Category expansion (1–2 weeks, parallelizable)

Build in priority order: Cat 1/2/3 first (existing spines and the biggest spec gaps),
then Cat 4/7, then Cat 5/6.

For each provider:

1. Manifest with `domain`, `source`, `cluster`, `query_map`, `example`.
2. Fixture recorded with `mouseion record`.
3. Live contract test (`pytest -m live`).
4. Two routing eval cases (one positive, one negative).
5. Audit pass.

**Gate:** each domain meets its target; every new provider has a fixture and a live
contract; the audit is clean; the eval set grows to at least 100 cases.

### Phase 4 — Evaluation and hardening (2–3 days)

- Expand `eval/routing.jsonl` to 100+ cases across all seven domains, including
  negatives and cross-domain ambiguity.
- Measure top-1, top-3, MRR, abstention precision/recall, false-positive rate on
  negatives, failover success rate, p50/p95 latency, cache hit rate, and 429 rate.
- Re-calibrate `MOUSEION_COS_FLOOR`, `MOUSEION_LEX_FLOOR`, and `MOUSEION_ROUTER_ALPHA`
  per embedding model; print them in `mouseion eval` (already done).
- Add structured logs for routing and failover; never log credentials or full bodies.
- Document the final operating point and its known failure modes.

**Gate:** routing accuracy is not worse than the current 47/48 baseline on the original
set; the new domain cases meet an agreed threshold; failover never changes a valid zero
into a non-zero; the live contract suite passes nightly for two consecutive runs.

### Effort estimate

Roughly **3–4 weeks for one engineer** for Phases 0–2 plus a meaningful slice of Phase 3.
The full 60-provider target is longer; the provider work is parallelizable and is the
right place to use multiple agents. Do not start Phase 3 before Phase 1's audit exists,
or every new manifest will need to be revisited.

---

## 10. Remaining decisions

Resolved on 2026-10-29: stack (Python/YAML), first milestone (Phase 0–2), and secondary
indexes (documented exceptions). The following are still open:

1. **Breadth target.** Is 60 providers the goal, or is 40–45 with deeper eval coverage
   per domain better? The spec says "breadth", but the current eval says depth is what
   moves accuracy.
2. **Version control.** Initialize git and a CI workflow before Phase 1? The plan assumes
   yes; nothing in Phase 0–2 depends on a particular remote.
3. **Auto-routing promotion.** After Phase 2 passes, promote `mouseion lookup` to a fifth
   MCP tool, or keep the four-tool surface and expose the engine only through the CLI?
4. **`category` retirement.** Keep the compatibility tag indefinitely, or remove it once
   `domain` + `sub_category` have replaced every use in the router, docs, and tests?

### Immediate next actions

1. Fix README counts and `.env.example`; triage `candidates_auto/`.
2. `git init` and commit the current baseline plus this document.
3. Add the manifest schema fields and backfill all 36 providers.
4. Implement `mouseion audit` and its violation tests.
5. Build `mouseion/engine.py` and `mouseion lookup` behind the Phase 2 gate.

---

## 11. What not to do

- Do not rewrite the engine in TypeScript to satisfy `src/router/registry.ts`.
- Do not put `execute` functions in the registry.
- Do not fail over on empty results.
- Do not fail over across categories without a shared cluster and query map.
- Do not replace SQLite with an in-memory cache.
- Do not claim a script can prove "permanently free" or "primary originator".
- Do not add 20 manifests before the audit exists; unreviewed breadth is how the
  primary-source guarantee dies.
