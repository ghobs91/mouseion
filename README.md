# mouseion

One tool surface over many public-record APIs, for a model running on your own machine.

The premise: most factual questions do not need a search engine. They need the register,
the filing, the dataset, or the table that the search engine would have cited. If the model
can call the source directly, it gets the primary record, a citable URL, and no SERP
interpretation step.

Firecrawl's Alexandria solves this with a hosted proxy: one API key, credits, their
provider credentials. On a local machine those constraints disappear, so the proxy layer
shrinks to what actually mattered — a **provider registry**, a **uniform envelope**, and a
**routing loop**. This is that, minus the billing.

```
local model (llama.cpp)
      │  tool calls, grammar-constrained
      ▼
┌──────────────────────────────────────────┐
│ 4 tools: providers_search                │
│          provider_describe               │
│          provider_call                   │
│          fetch_url                       │
└──────────────────────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────┐
│ router: patterns → embeddings → lexical  │  ← query → top-k capabilities
└──────────────────────────────────────────┘
      │
      ▼
┌──────────────────────────────────────────┐
│ adapters: rest_json · socrata            │  ← shape-specific code, ~8 classes total
└──────────────────────────────────────────┘
      │
      ▼
providers/*.yaml   ← one file per upstream, no code
      │
      ▼
SQLite cache (raw bodies, snapshot-safe pagination)
      │
      ▼
upstream API (keyless, or a free key)
```

## Quickstart

```bash
uv sync --extra dev

# what's loaded
.venv/bin/mouseion providers

# search the index, then inspect a provider's exact params
.venv/bin/mouseion search "drug recall"
.venv/bin/mouseion describe openfda

# call it
.venv/bin/mouseion call openfda drug_recalls '{"search": "classification:\"Class I\"", "limit": 3}'
```

MCP server (stdio):

```bash
.venv/bin/python -m mouseion.server
```

Tests:

```bash
.venv/bin/pytest -q
```

## The four tools

| Tool | Purpose | Cost |
|---|---|---|
| `providers_search` | query → compact provider cards (id, title, category, auth, capability names) | ~200 tokens |
| `provider_describe` | one provider → full param schemas, pagination mode, licence, example | ~300 tokens |
| `provider_call` | execute → normalized envelope | records |
| `fetch_url` | one URL → readable text (HTML stripped) | page size |

Three of them are the routing loop. The fourth is the escape hatch that keeps a missing
adapter from becoming a dead end.

The split matters for a small model: 128 tool definitions would be ~20k tokens of prompt
before the question even arrives, and an 8B model picks badly from that many. Four tools
plus a registry keeps selection tractable.

## Routing

`providers_search` does not score provider descriptions. Three signals, in order:

1. **Patterns.** `NCT\d{8}` on ClinicalTrials `get` resolves an identifier exactly, for
   free. Regexes live in the manifest, so identifier schemes never depend on a model's
   taste in embeddings.
2. **Embeddings.** One vector per *capability*, not per provider — "recalled blood pressure
   pills" is `(openfda, drug_recalls)`, and that granularity is what lets the router point
   at a capability instead of a whole service. 80% of the blended score.
3. **Lexical.** Fraction of query tokens present in the capability text, plus an
   inverse-document-frequency bonus for provider-name tokens. Catches `CFTC`, `openFDA`,
   `NVD`, tickers and anything an embedding model blurs. The IDF weighting matters:
   a flat name bonus made the word "data" in `cms_provider`'s title count as much as
   "UK" in `gov_uk`'s, which put the wrong provider first on real queries. 20% of score.

A candidate survives if any signal clears its floor. The router is allowed to return
nothing: an index that always answers will let the model cite the wrong register.

Measured by `mouseion eval` over 48 held-out questions, 6 deliberately unanswerable:

| Router | top-1 | top-3 |
|---|---|---|
| lexical + patterns only | 42 / 48 | 43 |
| lexical, with cosine as a gate only | 43 / 48 | 45 |
| embeddings only (bge-small) | 47 / 48 | 48 |
| **default blend (0.8 cosine / 0.2 lexical)** | **47 / 48** | **48** |

At this operating point the blend and pure embeddings tie. Lexical is kept for two
reasons and neither is accuracy on this set: it is the fallback when no model is
configured, and it is the tiebreaker on provider-name queries that the eval set
under-samples. The honest statement is "tying, and cheap to keep", not "worth +N".

### Geographic coverage

The one case embeddings cannot fix is `"what is the weather in Paris tomorrow"` → NWS.
That is not a similarity failure: NWS is the right kind of provider and the wrong
jurisdiction. Manifests now declare `regions:`, and the router drops a provider only when
its declared regions are **disjoint from every region named in the query**:

```yaml
regions: [US]      # nws, sec_edgar, cms_provider, nyc_open_data, ...
regions: [GB]      # gov_uk
regions: [AU]      # data_gov_au
# omitted = global, never filtered
```

The "every" matters. `"US hospitals operating in France"` names both, so US providers
survive; `"hospital ratings in Germany"` names only Germany, so US-only CMS is dropped and
the router correctly returns nothing. Two deliberate details: bare `us` is not a place
token (it is a pronoun — only uppercase `US` counts), and ambiguous names like Georgia map
to both readings, which makes them harmless rather than wrong.

It is a ~150-entry gazetteer, not a geocoder. It only ever removes providers that
declared a narrower region than the query asks about, and an undeclared provider is never
touched.

### The calibration trap

Swapping in a real model made routing *worse* at first — 35/44, with every negative query
matching something. The blend weight was fine; the **cosine floor was not**. bge-small
scores roughly 0.45–0.55 cosine for unrelated short text, so a floor of 0.34 (sensible for
the hashed-vector stand-in used during development) admitted everything.

The floor is not portable between models. Same eval, same blend, three models:

| Model | dims | best top-1 | floor at best | behaviour near it |
|---|---|---|---|---|
| `all-MiniLM-L6-v2` | 384 | 46 / 48 | **0.30** | 0.35 → 43, 0.40+ → 42 |
| `bge-small-en-v1.5` | 384 | 47 / 48 | **0.55** | 0.50 → 45, 0.60 → 47 |
| `bge-base-en-v1.5` | 768 | 47 / 48 | **0.50** | 0.45 → 46, 0.55 → 47 |

Running bge-small's floor (0.55) against MiniLM costs 4 cases; running MiniLM's floor
(0.30) against bge-small costs 6. A model three times larger (`bge-base`) buys nothing but
a *different* floor. So the numbers are calibrated constants, not universal ones — exposed
as `MOUSEION_COS_FLOOR`, `MOUSEION_LEX_FLOOR`, `MOUSEION_ROUTER_ALPHA`, and printed by
`mouseion eval` so the active values are never a mystery.

`bge-small-en-v1.5` stays the default: it ties the larger model on accuracy, is a third of
the size, and its plateau is as wide. Models that need asymmetric prefixes (nomic, e5) are
supported through `MOUSEION_QUERY_PREFIX` / `MOUSEION_DOC_PREFIX`.

### Enabling it

Two local options, both offline:

```bash
# A. llama.cpp already running — no Python dependency
llama-server -m bge-small-en-v1.5-q8_0.gguf --embeddings -b 512 -ub 512 --port 8081
export MOUSEION_EMBED_URL=http://127.0.0.1:8081

# B. no model server — ONNX via fastembed
uv sync --extra dev --extra embeddings
export MOUSEION_EMBED_BACKEND=fastembed

mouseion index    # embed every capability, ~2s for 29 docs
mouseion eval     # score the router against the held-out set
mouseion route "which pills got pulled off shelves"
```

Nothing downloads a model implicitly: without one of those two settings the router stays
lexical, at 42/48 rather than 47/48.

Vectors are content-addressed *and* model-addressed in SQLite, so editing one manifest
re-embeds one capability, and switching models does not silently reuse stale vectors. If
the backend is down the router logs once, degrades to lexical, and keeps answering —
routing must never take the tool surface down with it.

`eval/routing.jsonl` is held out on purpose: none of those questions appear as `exemplars`
in the manifests. When a real question has no provider, add it there with `"expect": null`.
That file is the roadmap.

## Lookup and failover

`providers_search` is model-in-the-loop: the model picks a provider, reads its params,
then calls it. `mouseion lookup` is the automatic path. It takes a query, routes it,
translates the query through each capability's `query_map`, and tries sources in order.

```bash
mouseion lookup "CRISPR base editing" --domain academic_scientific
mouseion lookup "NCT04631731" --limit 1
mouseion lookup "drug recall" --provider openfda --capability drug_recalls
```

Four rules keep it honest:

1. **Failover only inside a cluster.** A cluster is a declared group of capabilities
   that can answer the same question (`scholarly_work_search`,
   `government_content_search`, `entity_name_search`). Category membership is not
   enough: FRED and the World Bank are not interchangeable.
2. **A valid empty result is a success.** If a source answers with zero records, the
   engine stops. "No Class I recalls" is an answer, not a reason to ask a different
   register.
3. **A region conflict is not a failover.** A US-only provider is dropped before the
   call when the query names another country; it is never used as a fallback.
4. **Aggregator exceptions are not automatic.** CourtListener, OpenAlex, GBIF, and
   Open Library are useful, but they are secondary indexes. They are used only with
   `--allow-aggregators` or an explicit `--provider`.

Every result carries an `attempts` trace and a machine-readable signal:

```json
{
  "web_search_required": false,
  "attempts": [
    {"provider": "openalex", "capability": "search_works", "outcome": "retryable", "http": 429},
    {"provider": "crossref", "capability": "search_works", "outcome": "ok"}
  ],
  "envelope": { "records": [] }
}
```

If every candidate fails, `web_search_required` is `true` and `reason` says why
(`no_candidates`, `no_primary_candidates`, `missing_credential`, `unmapped_query`,
`all_sources_failed`). `providers_search` emits the same signal when it finds nothing.

The engine is a library and CLI first. It is not a fifth MCP tool yet; the four-tool
surface stays stable until the failover eval is in place.

## The envelope

Every provider returns this shape, which is what makes heterogeneous upstreams
interchangeable:

```json
{
  "provider": "clinicaltrials_gov",
  "capability": "search",
  "source_status": {"url": "...", "http": 200, "fetched_at": "...", "cache_age_s": 0.0},
  "attribution": "ClinicalTrials.gov, U.S. National Library of Medicine",
  "cursor": {"snapshot": "2026-10-24T13:24:37+00:00", "next": "TOKEN-2"},
  "records": [
    {"source_record_id": "NCT04631731",
     "source_url": "https://clinicaltrials.gov/study/NCT04631731",
     "retrieved_at": "...",
     "data": {"nct_id": "...", "phases": ["PHASE1"], "enrollment": 200}}
  ],
  "missing": ["collaborators"],
  "truncated": true
}
```

Four rules hold this together:

1. **Never surface raw upstream JSON.** Project to the fields the model needs. Raw ClinicalTrials
   study objects are 10–50× the projected record.
2. **Always emit `source_url`** when the upstream has a human-viewable page. That is what makes
   an answer checkable instead of merely confident.
3. **`missing` lists projection gaps** once per response, so the model knows what it does not know.
4. **`cursor.next` is the only pagination token** the model ever handles. Upstream offsets, page
   numbers and opaque page tokens all collapse into it.

## Adding a provider

A provider is a YAML file in `providers/`. Only genuinely bespoke upstreams need Python.

```yaml
id: fred
title: FRED (Federal Reserve Economic Data)
category: finance
domain: financial_sovereign
sub_category: economic_series
base: https://api.stlouisfed.org/fred
auth: {type: api_key, env: FRED_API_KEY, param: api_key, location: query}
attribution: "Federal Reserve Bank of St. Louis"
source:
  is_primary: true
  originator: "Federal Reserve Bank of St. Louis"
  originator_domain: stlouisfed.org
  provenance: official
  tier: 2
  auth_policy:
    free: true
    credit_card_required: false
    trial: false
    evidence_url: https://fred.stlouisfed.org/docs/api/api_key.html
    verified_at: 2026-10-29
  rate_limit: {per_min: 300, burst: 10, concurrency: 4}
cache_ttl: 3600
capabilities:
  series_observations:
    description: Read the observations of one economic series.
    path: /series/observations
    params:
      series_id: {type: string, required: true, description: e.g. UNRATE}
      limit: {type: integer, default: 100}
    alias: {series_id: series_id}
    pagination: {mode: offset, param: offset, limit_param: limit, default_limit: 100, max_limit: 1000}
    record_path: observations
    id_field: date
    fields: {date: date, value: value}
```

Field reference (`domain`, `sub_category`, and `source` are required; the rest are
optional):

| Key | Meaning |
|---|---|
| `domain` | one of the seven routing domains: `financial_sovereign`, `academic_scientific`, `developer_infrastructure`, `geo_earth_observation`, `archives_web_state`, `transport_infrastructure`, `governance_legislative` |
| `sub_category` | finer topic within the domain, e.g. `economic_series` |
| `source` | primary-source policy: `is_primary`, `originator`, `originator_domain`, `provenance`, `tier`, `auth_policy`, `rate_limit`, optional `exception_reason` |
| `cluster` | failover group. Two capabilities may fail over only when they share a cluster |
| `query_map` | optional; enables `mouseion lookup`; required when `cluster` is set. Canonical field → source param, e.g. `{text: query, limit: limit}` |
| `local_filter` | post-parse filter for catalogs with no server-side search: `{field: cveID, param: cve_id}` |
| `category` | compatibility tag; `domain` is what the router uses |
| `adapter` | `rest_json` (default) or `socrata` |
| `path` | path under `base`; `{name}` segments are substituted from params |
| `params` | name → `{type, description, required, default, enum}`; a bare string is shorthand for the type |
| `alias` | friendly param name → upstream name (`status` → `filter.overallStatus`). Pagination params are not aliasable |
| `pagination` | `token` (next token from `next_field`), `offset`, or `page` |
| `record_path` | dotted path to the record list; omit if the body *is* the list |
| `fields` | target → dotted source path. A value may be a list of paths; the first non-null wins (language fallback). Omit to pass the raw object through |
| `pluck` | collapse list-of-object fields to one attribute (`interventions: name`) |
| `id_field` / `id_template` | stable record id; use a template for composite keys |
| `source_url_template` | format string over normalized fields → citable URL |
| `empty_on_404` | treat 404 as an empty result set (openFDA does this) |
| `terms_field` | dotted path to upstream licence text returned with every result |
| `exemplars` | questions this capability answers — the strongest routing signal per token |
| `patterns` | regexes that identify the capability outright (`NCT\d{8}`) |
| `body_template` | JSON body for POST APIs; all params go to the body, not the query |
| `query_template` | nested query filters (`conditions[0][property]=state`), flattened |
| `map_exclude` | keys to skip when the records live in an object keyed by id |
| `local_window` | upstream returns a fixed, unstable window — cache it and page locally |

Add a file, run `mouseion providers`, and it is live. Unknown caller params are dropped
rather than forwarded, so a hallucinated parameter is a no-op instead of a 400 the model
cannot interpret.

Before committing a manifest, run the policy audit:

```bash
mouseion audit          # static: tier/auth, originator domain, evidence age, blocklist, TTL
mouseion audit --live   # also checks that every evidence URL still resolves
```

The audit is the gate that keeps "primary source" from becoming a comment. It fails on
a stale evidence date, a Tier 1 source that needs a key, a Tier 2 source that requires a
card or trial, a secondary host, or a failover cluster without a query map.

## llama.cpp

llama.cpp has no MCP client, so pick one of three entry points.

**1. Grammar (recommended for small models).** No chat template needed:

```bash
.venv/bin/mouseion grammar > /tmp/mouseion.gbnf
.venv/bin/python examples/llamacpp_agent.py --question "Any Class I drug recalls?"
```

`grammar()` constrains output to `{"tool": <closed enum>, "arguments": <object>}`, so the
model cannot emit malformed JSON or invent a provider. This does more for reliability than
prompt tuning.

**2. Native tool calling.** If your model's template has tool tokens:

```bash
llama-server -m model.gguf --jinja -c 8192
.venv/bin/python examples/llamacpp_agent.py --question "..." --mode tools
```

**3. MCP.** `python -m mouseion.server` works with any MCP client (OpenCode, Claude Desktop,
etc.) if you would rather the outer harness own the loop.

### Unsloth Studio

Unsloth Studio's API-only server is OpenAI-compatible but listens on **8889**, not
llama.cpp's 8080, and requires a `model` field. The example auto-detects the loaded
model from `/v1/models`:

```bash
.venv/bin/python examples/llamacpp_agent.py \
  --question "Were there any Class I drug recalls recently?" \
  --base-url http://127.0.0.1:8889 --mode tools
```

Qwen3-style reasoning models spend the token budget on `reasoning_content` before
emitting a tool call. The example sends `chat_template_kwargs.enable_thinking=false`
by default; pass `--thinking` to leave it on. It also caps each capability at one page
so the loop terminates; pass `--max-pages 0` for exhaustive pagination.

Unsloth's `/v1/embeddings` needs a loaded GGUF embedding model. The chat model alone
does not provide embeddings, so the router stays lexical. For the calibrated 47/48
router, use `MOUSEION_EMBED_BACKEND=fastembed` or point `MOUSEION_EMBED_URL` at a
separate embedding server.

## What's here

49 providers, 69 capabilities. The contract suite verifies every upstream live except the
four that need credentials this machine does not have (FRED, CourtListener, GitHub, and
SEC's contact User-Agent is set but FRED's key is not). Their auth paths are verified.

| Provider | Auth | Capabilities |
|---|---|---|
| arXiv | none | `search` |
| BLS | none | `series_observations` |
| Census Geocoder | none | `geocode_address` |
| CFTC Commitments of Traders | none | `legacy_futures_only` |
| CISA KEV | none | `catalog` |
| ClinicalTrials.gov | none | `search`, `get` |
| CMS Care Compare | none | `hospitals` |
| CourtListener | free token | `search_opinions` |
| crates.io | none | `search`, `crate` |
| Crossref | none | `search_works` |
| data.gov.au | none | `search_datasets` |
| DataCite | none | `search_dois` |
| ECB | none | `yield_curve`, `exchange_rates`, `policy_rate` |
| Federal Register | none | `search`, `get` |
| FRED | free key | `search_series`, `series_observations` |
| GBIF | none | `search_occurrences` |
| GLEIF | none | `search_lei` |
| GitHub | free token | `search_repositories`, `repository`, `releases`, `commits` |
| Go module proxy | none | `latest` |
| GOV.UK | none | `search_content` |
| Grants.gov | none | `search_opportunities` |
| Maven Central | none | `search` |
| NHC | none | `active_storms` |
| NHTSA | none | `models_for_make` |
| NIH RePORTER | none | `search_projects` |
| NIST NVD | none | `search_cves` |
| npm | none | `search`, `package` |
| NWS alerting | none | `active_alerts` |
| NYC Open Data | none | `parcels` |
| OpenAlex | none | `search_works` |
| openFDA | none | `drug_recalls`, `drug_labels`, `device_recalls` |
| FEC (openFEC) | shared DEMO_KEY | `committees` |
| OpenFEMA | none | `disaster_declarations` |
| OpenLibrary | none | `search_books` |
| PMC | none | `search`, `summaries` |
| PubChem | none | `search_compounds`, `compound` |
| PubMed | none | `search`, `summaries` |
| PyPI | none | `package`, `release` |
| ROR | none | `search_organizations` |
| SEC EDGAR | contact UA | `full_text_search`, `filings_by_cik` |
| DataSF | none | `police_incidents` |
| US Treasury Fiscal Data | none | `debt_to_penny`, `avg_interest_rates` |
| UniProt | none | `search`, `entry` |
| USAspending | none | `spending_by_award` |
| USGS earthquakes | none | `query` |
| USGS water services | none | `instantaneous_values` |
| Wikidata | none | `search_entities`, `entity` |
| World Bank | none | `indicator` |
| Zenodo | none | `search_records` |

Credentials, before the first call:

```bash
export SEC_USER_AGENT="mouseion/0.1 (Your Name you@example.com)"  # SEC refuses anonymous clients
export FRED_API_KEY="..."                                        # free at fred.stlouisfed.org
export COURTLISTENER_TOKEN="..."                                 # free; anonymous search now 401s
export GITHUB_TOKEN="..."                                        # free; GitHub REST needs 5,000/hour
```

openFEC ships with the shared `DEMO_KEY`, which works but is rate-limited to the point of
being unusable for more than a couple of calls; register a free key and pass it as
`api_key`. Missing credentials surface as a structured error naming the exact variable,
never as an upstream 403.

Four adapters (`rest_json`, `socrata`, `atom`, `sdmx_json`), SQLite cache with
snapshot-safe cursors, rule + embedding + lexical router with a 48-question held-out
eval, MCP server, CLI, GBNF export, 261 offline tests and 69 live contract tests.

Eight upstream behaviours the adapter contract had to grow for, all now generic:

- **POST + JSON body** (USAspending, NIH RePORTER, Grants.gov, BLS). `body_template`
  renders a nested body, keeps integer types intact, and drops optional fields instead of
  sending `"{start_date}"` as literal text.
- **Nested query filters** (CMS `conditions[0][property]=state`). `query_template`
  flattens a nested structure into bracketed query keys, and drops the whole clause when
  the optional value is missing rather than sending half a filter.
- **Keyed maps instead of lists** (PubMed `esummary` returns `{uids: [...], "<pmid>": {...}}`).
  `map_exclude` turns the object into records and skips the metadata keys.
- **Fixed, unstable result windows** (SEC returns 100 hits per request whatever you ask,
  re-ranked each call, so offset paging duplicates records). `local_window` fetches the
  window once, caches it, and pages through it locally, which is why SEC pages cannot
  overlap.
- **Unpaginated endpoints** (NWS alerts). The same `local_window` mechanism slices a
  single response instead of inventing a page parameter the API does not have.
- **Catalogs with no server-side search** (CISA KEV). `local_filter` narrows the parsed
  records to an exact identifier after fetching the catalog once.
- **Atom XML feeds** (arXiv). The `atom` adapter parses `<entry>` elements and the
  OpenSearch total, while the rest of the pipeline stays unchanged.
- **SDMX-JSON series** (ECB). The `sdmx_json` adapter flattens dimension-indexed
  series and observations into one row per time period.

## Contract tests

Two layers, and they test different things:

```bash
pytest                    # 261 offline: fixture replay
pytest -m live            # 69 live: every upstream, with the manifest example
mouseion record           # re-record fixtures after an intentional change
```

Fixtures are the raw upstream body plus the params that produced it, so replay exercises
the real adapter path rather than a hand-written sample. Neither layer asserts data
values — upstream facts change daily and that is not a bug. They assert that the manifest
still *describes* the response:

- the record path resolves
- the record count matches what parsing produced
- every `id_field` resolves on every record
- every `source_url_template` resolves
- `truncated` always comes with a usable cursor
- projected fields that vanished upstream are reported as warnings, not failures, because
  plenty are legitimately optional (DOIs, abstracts, close dates)

Run `pytest -m live` nightly. It is the thing that notices a renamed field or a dropped
page token before a user does.

## What's deliberately not here

- **Coverage.** 49 providers is a spine, not an index. The current gap matrix is in
  `docs/RECALIBRATION.md` §8: central banks and fiscal authorities, preprint and
  molecular repositories, archives, transit, and legislative registers. Most are
  keyless or free-key; each needs a manifest, a fixture, a live contract, and an
  audit entry before it counts.
- **Write paths.** Everything here reads. No filings, no comments, no purchases.

## Honest ceiling

Direct-API coverage is strong for lookups with a known authority: a drug, a statute, a
company, a statistic, a trial, a filing, a tariff line. It is near-zero for news, opinion,
and "what happened this week". Expect to serve roughly 40–60% of factual lookup queries
directly and 20–30% of open-ended research, with `fetch_url` handling the remainder.

That gap is not a defect. Log every query no provider matched — that log is the roadmap.
