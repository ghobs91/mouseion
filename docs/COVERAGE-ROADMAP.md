# Coverage Roadmap: Local Elections and Commerce

**Status:** Proposal updated 2026-10-30. The product is a general primary-source
router, not a public-records-only tool. Commerce is in scope. Local elections are
bulk-first: all states, DC, and major cities, not a single pilot.
**Basis:** 62 providers / 86 capabilities.

**Implemented since the first draft:**
- Shopify Global Catalog is live as `shopify_catalog`, read-only (`search_catalog`,
  `lookup_catalog`, `get_product`), with client-credentials auth and automatic UCP
  profile injection.
- Shopify Dev Dashboard credentials are confirmed permanently free, no card, no paid
  approval.
- `commerce` and `civic_local` are now domain values; `commercial_platform` is a
  provenance value.

---

## 1. What the current registry is good at

The current 61 providers are a **public-record spine**:

- sovereign finance and central-bank statistics
- academic literature, DOI metadata, chemistry, proteins, clinical trials
- package registries, vulnerability feeds, source repositories
- geocoding, weather, seismology, natural events, municipal spatial data
- web archives, national libraries, national archives
- legislation, registers, grants, health regulation, case law

It is not a general answer engine. Two common query families fall outside it:

1. **Local elections** — who is on my ballot, and who funded them.
2. **Commerce** — product discovery, price comparison, cart and checkout.

Both are legitimate demand. Neither is closed by adding two YAML files.

---

## 2. Why these two break the current model

### Local elections are fragmented by design

There is no national API for “who is running in my locality.” Election
administration is delegated:

- state election boards publish candidate filing lists and voter guides
- county election offices publish local candidate lists and sample ballots
- city and county clerks publish municipal filings
- state ethics commissions and city campaign-finance boards publish donors
- formats are a mix of JSON, CSV, HTML tables, and PDFs

The same question therefore resolves to a different authority depending on the
address. A deterministic engine can cover **named jurisdictions deeply**; it
cannot honestly claim national coverage from one endpoint.

### Commerce is commercial MCP infrastructure, not a public record

Shopify’s agent surface is now UCP-based:

- **Global Catalog MCP** — `https://catalog.shopify.com/api/ucp/mcp`
  - `search_catalog`, `lookup_catalog`, `get_product`
  - searches across Shopify merchants
  - requires Shopify Dev Dashboard client credentials as a bearer token and a
    `meta.ucp-agent.profile` URL on every request
- **Storefront Catalog MCP** — `https://{shop-domain}/api/ucp/mcp`
  - the same catalog tools scoped to one merchant
- **Cart MCP** — `https://{shop-domain}/api/ucp/mcp`
  - `create_cart`, `get_cart`, `update_cart`, `cancel_cart`
  - unauthenticated, but a **write path**
- **Checkout MCP** — authenticated/signed, payment and order completion
  - a high-risk write path; out of scope for this project

UCP is an open standard (`https://ucp.dev/`), co-developed by Shopify and Google.
Other UCP-compliant merchants can use the same transport. But a commerce catalog
is not a government register: it is a commercial platform’s own inventory, and
the Global Catalog is an aggregator of Shopify merchants.

The current `is_primary_source` rule is designed for originators of public data.
Commerce needs a separate provenance class, not a quiet exception.

---

## 3. Local elections plan

### 3.1 Add a `civic_local` domain

Proposed taxonomy:

| Domain | Sub-categories |
|---|---|
| `civic_local` | `election_administration`, `candidate_filings`, `campaign_finance`, `ballot_measures`, `election_results`, `municipal_offices` |

This is separate from `governance_legislative`, which covers federal/state
legislation and registers, not local election administration.

### 3.2 Add a jurisdiction registry

A new `jurisdictions.yaml` maps a resolved place to its authorities:

```yaml
US-NY-NYC:
  election_authority: nyc_board_of_elections
  campaign_finance: nyc_campaign_finance_board
  sources: [nyc_cfb_socrata, nyc_voter_guide]
US-CA:
  election_authority: california_sos
  campaign_finance: cal_access
  sources: [cal_access_bulk, cal_sos_candidate_lists]
```

Resolution path:

1. address or place name → Census Geocoder (US) or Nominatim (non-US)
2. geocode result → state/county/place FIPS or OSM relation
3. FIPS/relation → jurisdiction entry
4. jurisdiction entry → candidate, finance, ballot, and results sources

If the jurisdiction is not in the registry, the engine returns
`web_search_required` with a clear reason. It does not guess a nearby authority.

### 3.3 Source classes to build

| Data | Official source class | Typical format | Adapter |
|---|---|---|---|
| Candidate filings | state election board, county election office, city clerk | JSON, CSV, HTML, PDF | `rest_json`, `csv`, `html_table` |
| Campaign finance | state ethics commission, city campaign-finance board | Socrata JSON, CSV, bulk download | `socrata`, `csv` |
| Ballot measures | secretary of state voter guide | HTML, PDF | `html_table`, later PDF |
| Election results | state/county results portal | JSON, CSV | `rest_json`, `csv` |
| Polling places | county election office | JSON, CSV | `rest_json`, `csv` |

Candidate sources to verify before shipping (not yet integrations):

- **NYC Campaign Finance Board** — campaign finance data, likely via NYC Open Data
- **California CAL-ACCESS** — state campaign finance and lobbying disclosure
- **California Secretary of State** — candidate lists and voter guides
- **Washington Public Disclosure Commission** — state campaign finance
- **Florida Division of Elections** — candidate and campaign finance data
- **Texas Ethics Commission** — campaign finance filings

Each source must pass the same gate as every other provider: primary originator,
evidence URL, rate limit, fixture, live contract, audit entry, and eval cases.

### 3.4 New adapters

- **`csv`** — election filings and finance exports. Must handle header rows,
  delimiter variants, and quoted fields. The `cdx` adapter is a precedent for a
  shape-specific parser.
- **`html_table`** — only where the official page is the only machine-readable
  surface. This is fragile; prefer CSV/JSON. Do not build a general scraper.
- **PDF** — defer. PDF extraction is a different reliability class; use only for
  voter guides after the CSV/JSON path is exhausted.

### 3.5 Bulk-first milestone — registry and discovery implemented

The unit is the **jurisdiction registry**, not a hand-picked pilot. Steps 1–3 are
built:

1. **Seeded.** `jurisdictions.yaml` holds 96 entries: 50 states, DC, five
   territories, and 40 major cities. Every entry names its official election
   authority; 86 also name a campaign-finance authority. State election URLs come
   from the USA.gov state and local election office directory.
2. **Modelled.** Each authority is a URL plus a note, not a guessed format. Format
   is measured, not declared.
3. **Probed.** `mouseion civic discover` fetches every authority page in parallel,
   follows machine-readable links, and writes `eval/civic-coverage.json`.

The first full pass covers 182 authority URLs:

| Format | Count | Meaning |
|---|---|---|
| `html` | 123 | prose or a JS shell |
| `blocked` | 33 | HTTP 403 at an edge WAF |
| `html_tables` | 19 | tables, no export |
| `tls_error` | 3 | incomplete certificate chain |
| `unreachable` | 2 | timeout or dead DNS |
| `csv` | 1 | Wyoming candidate list |
| `error` | 1 | stale seed (El Paso County elections) |

Remaining work is promotion, not seeding:

4. Promote sources in waves, format-first:
   - **wave 1: JSON/Socrata — done.** `mouseion civic datasets` searches Socrata's
     national catalog and ranks official tables by row count. 89 datasets across 13
     portals have at least 1,000 rows; fourteen are live providers covering New York
     state and city, Washington, Hawaii, Los Angeles, Austin, Oakland, Dallas,
     Maryland, Fulton County (GA), Ramsey County (MN) and King County (WA). King
     County uses `datasets:` to map 73 election keys to per-election resources.
   - **wave 2: CSV/bulk downloads — done.** `colorado_campaign_finance` pages
     Colorado TRACER's annual ZIP archives (2023-2026). The CSV adapter now reads a
     ZIP member and stops parsing at the last row a page needs, so a 68MB export
     costs one page of memory.
   - wave 3: HTML tables where no machine-readable surface exists
5. Every promoted source still gets a manifest, fixture, live contract, audit
   entry, and eval cases.

Two findings shape the waves. First, most state portals expose no export link on
the landing page; the data sits behind an ASP.NET form or a JS app, so wave 3
needs a table scraper rather than a URL probe. Second, a third of the authorities
reject a plain HTTP client at the edge (HTTP 403 even with a browser User-Agent),
so those need a browser-capable probe before they can be classified at all.

This is how “all states and major cities” is approached without pretending every
jurisdiction has an API. A jurisdiction with no machine-readable source is marked
`manual`/`unavailable` and returns `web_search_required`, not a guess.

### 3.6 What not to do

- Do not use Ballotpedia, OpenStates, or similar secondary aggregators as primary
  sources. They are the same class of exception as CourtListener, and local
  election coverage is exactly where their gaps hide.
- Do not scrape a search engine result page.
- Do not infer a candidate’s party, district, or funding from a name.
- Do not answer “who is on my ballot” without a resolved jurisdiction.

---

## 4. Commerce plan

### 4.1 Add a `commerce` domain with a distinct policy

Proposed taxonomy:

| Domain | Sub-categories |
|---|---|
| `commerce` | `product_catalog`, `offers_pricing`, `cart`, `checkout`, `order_tracking` |

Proposed provenance:

```yaml
provenance: commercial_platform
is_primary: true          # primary for that platform's own inventory
primary_scope: platform   # not a universal public-record source
```

The audit must not treat `commercial_platform` as a public-record originator. It
is opt-in, labeled in cards, and excluded from public-record failover.

### 4.2 Read-only catalog first

Integrate **Global Catalog MCP** first:

- endpoint: `https://catalog.shopify.com/api/ucp/mcp`
- tools: `search_catalog`, `lookup_catalog`, `get_product`
- auth: Shopify Dev Dashboard client credentials (bearer token)
- required metadata: `meta.ucp-agent.profile` on every request
- use `read_only_only: true` so cart/checkout tools are never exposed

The existing `mcp_remote` transport and OAuth/token store are the right base.
The missing pieces are:

- a UCP agent profile URL and a Shopify bearer-token auth path
- a `commerce` source policy and audit rule
- tool-result normalization for UCP `structuredContent` (`products`, `product`,
  `variants`, `offers`)
- a product-specific envelope projection: title, merchant, variant, price,
  currency, availability, product URL, image URL

### 4.3 Cart and checkout are a different product

**Cart MCP is a write path.** It accepts unauthenticated requests, but it mutates
state (`create_cart`, `update_cart`, `cancel_cart`). It must not be part of the
automatic failover engine or the default tool surface.

If cart support is wanted, it needs:

- a separate `commerce_write` mode, explicitly enabled
- a per-action confirmation from the user
- no automatic “add to cart” from a search result
- an idempotency key for mutations
- a clear handoff of `continue_url` to the merchant checkout

**Checkout MCP is out of scope.** It handles payment and order completion.
Mouseion should never hold payment authority. The correct behavior is to hand the
buyer to the merchant’s checkout URL.

### 4.4 UPC/GS1 remains excluded

GS1 membership and product APIs are not permanently free. UPC/GS1 lookup cannot
meet Tier 1 or Tier 2 as written. Shopify uses its own product and variant IDs
(and UCP identifiers), not a universal UPC registry. Document this exclusion
rather than smuggling in a paid identifier service.

### 4.5 First milestone — implemented

The read-only Shopify Global Catalog is live as `shopify_catalog`:

- `search_catalog`, `lookup_catalog`, `get_product`
- client-credentials auth at `https://api.shopify.com/auth/access_token`
- UCP profile injected automatically
- no cart, no checkout
- `provenance: commercial_platform`
- audit entry and unit tests

Next commerce steps: a second UCP-compliant merchant to prove the transport is
generic, then the cart write-mode decision.

---

## 5. Other high-demand gaps

These are not commitments; they are the next candidates after local elections and
commerce. Each needs the same primary-source gate.

| Topic | Primary source class | Notes |
|---|---|---|
| Health symptoms and drug info | MedlinePlus, NIH, FDA, PubMed | Not diagnosis; cite the source |
| Company registry | SEC, GLEIF, Companies House | Companies House is a free key |
| Patents and trademarks | USPTO | Check key/free-tier terms |
| Crime statistics | FBI Crime Data Explorer, BJS | Agency-level, not incident-level everywhere |
| Schools and districts | NCES | Keyless data files/API |
| Real estate / parcels | county assessors | Fragmented like local elections |
| Immigration case status | USCIS | Requires a receipt number; no bulk API |
| Flight status | FAA/NOAA aviation weather, airline APIs | Airline APIs are commercial |
| Nutrition and food | USDA FoodData Central | Free key; Open Food Facts is crowdsourced |
| Standards and specifications | IETF, W3C, Unicode | Keyless documents, not search |
| Patents / trademarks | USPTO | Free but rate-limited |
| Legal codes | official statute sites | Often HTML/PDF; CourtListener covers opinions |

The demand signal should come from real queries, not a wish list. Add unmatched
queries to `eval/coverage-gaps.jsonl` with `"expect": null`; that file is the
backlog.

---

## 6. Phased roadmap

### Phase A — Demand ledger and policy (1–2 days)

- Add `eval/coverage-gaps.jsonl` with local-election and commerce queries.
- Decide whether `commerce` is in scope for this product.
- Decide whether local elections are one pilot jurisdiction or a national push.
- Add `civic_local` and `commerce` to the domain enum only after the decisions.

**Gate:** the roadmap has named owners, jurisdictions, and a read-only/write
boundary.

### Phase B — Local elections bulk build (multi-week, parallelizable)

- Seed `jurisdictions.yaml` for all 50 states, DC, and major cities.
- Build `mouseion civic discover` to probe official election and campaign-finance
  pages and write a coverage matrix.
- Add `csv` and, only where unavoidable, `html_table`.
- Promote sources in waves: JSON/Socrata, then CSV, then HTML.
- Add fixtures, live contracts, audit entries, and eval cases per source.

**Gate:** every state and major city has a registry entry and a status; every
machine-readable source in the registry has a manifest and a live contract; an
unregistered or `manual` jurisdiction returns `web_search_required`, not a guess.

### Phase C — Commerce read-only catalog (3–5 days)

- Add the `commerce` domain and `commercial_platform` provenance.
- Add Shopify Global Catalog MCP with bearer auth and agent profile.
- Normalize UCP product results into the envelope.
- Keep `read_only_only: true`; no cart/checkout.

**Gate:** product search returns merchant, title, variant, price, currency,
availability, and URL; cart/checkout tools are absent from `provider_describe`.

### Phase D — Commerce write path, only if explicitly wanted (1–2 weeks)

- Separate mode, explicit confirmation, idempotency keys.
- Cart only; no payment authority.
- Handoff to merchant checkout.

**Gate:** no automatic cart mutation; every write is user-confirmed and audited.

---

## 7. Decisions needed

Resolved on 2026-10-30:

- **Product scope:** general primary-source router, not public-records-only.
- **Commerce:** in scope; read-only catalog first.
- **Shopify auth:** permanently free, no card, no paid approval.
- **Local elections:** all states, DC, and major cities; bulk-first.

Still open:

1. **Cart/checkout:** read-only catalog only, or an explicit write mode? Checkout
   and payment should remain out of scope.
2. **Jurisdiction seed authority:** which source list seeds the state and major-city
   registry, and who signs off on each entry?
3. **HTML/PDF tolerance:** are HTML-table and PDF sources acceptable when a
   jurisdiction has no API/CSV, or should those stay `manual`?
