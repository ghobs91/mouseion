# Coverage Roadmap: Local Elections and Commerce

**Status:** Proposal. Two policy decisions and one scope decision are unresolved.
**Basis:** 61 providers / 85 capabilities. This document covers the two gaps that
motivated it: local election candidates and their funding, and product search /
shopping. It also lists the next high-demand topics.

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

### 3.5 First milestone

Pick **one city and one state** with machine-readable data:

- a city campaign-finance board (candidate + donor data)
- a state election authority (candidate filings + voter guide)

Ship 2–4 providers, a jurisdiction entry, fixtures, live contracts, and eval
cases. Then expand jurisdiction by jurisdiction. Do not claim national coverage.

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

### 4.5 First milestone

Ship a **read-only Shopify Global Catalog** provider:

1. `search_catalog` with a text query
2. `get_product` for one product/variant
3. a normalized envelope with merchant, price, currency, availability, URL
4. no cart, no checkout
5. audit entry with `provenance: commercial_platform`
6. eval cases for product discovery and price comparison

Then add a second UCP-compliant merchant to prove the transport is generic.

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

### Phase B — Local elections pilot (1–2 weeks)

- Build the jurisdiction registry and resolution path.
- Add `csv` (and `html_table` only if unavoidable).
- Ship 2–4 official sources for one city and one state.
- Add fixtures, live contracts, audit entries, and eval cases.

**Gate:** “who is running in <pilot city>” and “who funded <pilot candidate>”
return structured records with source URLs; an unregistered jurisdiction returns
`web_search_required`, not a guess.

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

1. **Is commerce in scope?** The project currently promises public records, not
   shopping. Adding commerce changes the product and the primary-source claim.
2. **Which local jurisdictions first?** Pick one city and one state with
   machine-readable data; national coverage is a multi-month data-engineering
   effort, not a provider count.
3. **Cart/checkout?** Read-only catalog only, or an explicit write mode? Checkout
   and payment should remain out of scope.
4. **Shopify auth tier.** Confirm the Dev Dashboard credentials are permanently
   free and do not require a card or paid approval. If they do, Shopify is a
   commercial exception, not Tier 2.
