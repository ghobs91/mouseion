# Coverage register

The reference every "what should we build next" decision starts from. The goal is a
**general primary-source router**: for any question people actually ask, return a
record from the authority that originates it, with a citation.

This page exists so that "next" is chosen against the whole map, not the most
recently discussed source. Update the status columns when a provider lands.

## How to read this

- **Domain** is the internal routing unit (one of ten in `mouseion/manifest.py`).
- **Topic** is what a user actually asks about, drawn from Alexandria's demand
  catalogue, the Gemini search-intent taxonomy, and the repo's own
  `eval/coverage-gaps.jsonl` signals.
- **Status**: `covered` (a primary provider answers it), `partial` (a provider
  touches it but cannot answer the common phrasing), `gap` (no primary provider),
  or `excluded` (cannot be served without breaking the source policy).

## Current state

**131 providers / 191 capabilities.** Domain distribution is deliberately uneven:
depth where the sources are good, not an even split.

| Domain | Providers | Strong at | Weak at |
|---|---:|---|---|
| `civic_local` | 23 | campaign finance, election results, restaurant inspections, property owners | candidates, ballots, most county assessors |
| `financial_sovereign` | 20 | central banks, regulators, filings, entity ownership, market quotes, statistics | sanctions, primary NYSE feeds |
| `academic_scientific` | 14 | literature, biology, chemistry, clinical trials | global health, structures, genomes |
| `geo_earth_observation` | 14 | geocoding, hazards, hydrology, alerts | **forecast**, real-time conditions |
| `reference` | 15 | sports, music, film, jobs, occupations, encyclopedia, podcasts | games, people |
| `governance_legislative` | 20 | legislation, courts, federal agencies, state governors and attorneys general | — |
| `developer_infrastructure` | 10 | package registries, vulnerabilities, code, Q&A | containers |
| `transport_infrastructure` | 8 | airports, flight status, transit news, London live status | US real-time transit (GTFS-RT) |
| `archives_web_state` | 5 | web and national archives | — |
| `commerce` | 2 | Shopify catalogue, food products | barcodes, non-food products |

## Topic status

| Topic (user phrasing) | Status | Authority in place / missing |
|---|---|---|
| Weather warnings and alerts | covered | NWS |
| Weather forecast | covered | NWS `forecast` (shipped) |
| Earthquakes, tides, storms, floods | covered | USGS, NOAA, NHC, FEMA |
| Places, addresses, geography | covered | Nominatim, GeoNames, Census, Overpass |
| Academic papers and citations | covered | arXiv, Crossref, OpenAlex, PubMed, PMC |
| Clinical trials, drugs, devices | covered | ClinicalTrials.gov, openFDA, CMS |
| Global health statistics | gap | WHO GHO |
| Protein structures, genomes | gap | RCSB PDB, Ensembl |
| Federal law, rules, court opinions | covered | Congress, GovInfo, Federal Register, CourtListener |
| State government announcements | covered | 7 states: CA, GA, WA, NC, TX, PA, FL |
| Local election results and money | covered | 17 civic providers, 101 jurisdictions |
| Candidates and ballots | gap | 5 explicit demand signals in `eval/coverage-gaps.jsonl` |
| Restaurant inspections | covered | NYC, Chicago, King County, NY State |
| Company registration and officers | partial | SEC, GLEIF ownership chains, ROR, Companies House, NY registry; most state registries and officers remain |
| Who owns this building | partial | NYC parcels, NY assessment rolls, Collin County TX; most county assessors remain |
| Who owns this business | partial | SEC and GLEIF ownership, Companies House, NY registry; most state registries remain |
| Sanctions and people screening | gap | OFAC SDN, EU and UK lists |
| Product barcodes and non-food goods | partial | Open Food Facts; Open Products Facts missing |
| Air travel status | covered | FAA, Schiphol, OurAirports |
| Transit service announcements | partial | MBTA, SFMTA, Caltrain news |
| Real-time transit | partial | TfL line status and arrivals; US GTFS-RT remains |
| Jobs and occupations | covered | USAJobs postings, O*NET occupation profiles |
| Packages, vulnerabilities, source code | covered | npm, PyPI, crates, Go, Maven, GitHub, NVD |
| Developer Q&A | covered | Stack Exchange |
| Market quotes | covered | Coinbase crypto; Yahoo Finance equities/ETFs/indices via aggregator exception; no primary NYSE feed |
| Macroeconomic statistics | covered | FRED, BLS, ECB, World Bank, Treasury |
| IMF / OECD / BIS series | gap | SDMX endpoints, adapter already exists |
| Sports results | covered | MLB, UEFA, FIFA, five leagues |
| Music, film, TV | partial | MusicBrainz, TMDB |
| Podcasts | partial | Podcast Index scaffolded; data calls need a free key and secret |
| Video games | gap | IGDB |
| Breaking news headlines | partial | 10 agency press feeds; no cross-source index |
| Consumer reviews, coupons, brand pages, social profiles | excluded | commercial or navigational, not primary records |

## Scoring

Every "next" decision scores the candidate 1-5 on five axes, then records the
result here:

| Axis | What a 5 means |
|---|---|
| **Demand** | a question people ask constantly (weather, local services) |
| **Gap** | nothing in the registry answers the common phrasing |
| **Policy fit** | the originator publishes it keyless and free |
| **Leverage** | an existing adapter or workflow carries most of the work |
| **Effort** | 5 = days; 1 = one manifest and a fixture |

`impact = 0.35·demand + 0.25·gap + 0.20·policy + 0.20·leverage`, reported with
effort alongside. A lower-impact item can still win on cost, but the trade is
stated, not hidden.

## Ranked backlog

| Rank | Step | Demand | Gap | Policy | Leverage | Impact | Effort |
|---:|---|---:|---:|---:|---:|---:|---|
| ~~1~~ | ~~NWS forecast capability~~ — shipped | 5 | 4 | 5 | 4 | **4.55** | medium |
| ~~2~~ | ~~State government announcement tranche~~ — shipped | 4 | 4 | 5 | 5 | 4.45 | low |
| ~~3~~ | ~~Restaurant inspection capabilities~~ — shipped | 4 | 5 | 5 | 4 | 4.45 | medium |
| 4 | WHO Global Health Observatory | 4 | 5 | 5 | 4 | 4.45 | low |
| ~~5~~ | ~~Real-time transit~~ — partially shipped (TfL); US GTFS-RT remains | 4 | 5 | 5 | 2 | 4.10 | high |
| 6 | OFAC sanctions screening | 4 | 4 | 5 | 3 | 3.95 | high |
| ~~7~~ | ~~O*NET occupations~~ — shipped | 3 | 4 | 5 | 4 | 3.95 | low |
| 8 | IMF / OECD / BIS SDMX | 3 | 3 | 5 | 5 | 3.85 | low |
| ~~9~~ | ~~Podcast Index and IGDB~~ — Podcast Index scaffolded; IGDB blocked | 3 | 5 | 4 | 3 | 3.80 | low |
| ~~10~~ | ~~Stack Exchange~~ — shipped | 4 | 4 | 3 | 4 | 3.75 | low |

**Shipped: #1 (NWS forecast), #2 (state government announcements), #3
(restaurant inspections) and #5 (real-time transit for London).** #5 used TfL's
JSON Unified API rather than a protobuf GTFS-RT feed, so it closed the topic for
one authority without a new binary adapter; US agencies still need GTFS-RT.

**Blocked: #4, the WHO Global Health Observatory.** Its documented OData endpoint
(`ghoapi.azureedge.net`) returned 500/502 on every path on 2026-11-01, including
root and `$metadata`. Re-check before scheduling it; do not ship a manifest
against a dead endpoint.

**Blocked: IGDB.** It needs Twitch client credentials, and its OAuth token flow
does not fit the current auth model (`client_credentials` is restricted to
remote-MCP transports). Its Apicalypse query is a raw POST body, which the client
does not send today. Treat it as an auth-model project, not a manifest.

**Next by the same rule: #6, OFAC sanctions screening.** It is the highest-impact
unshipped item (3.95). It is also the most expensive remaining one: the SDN list
is a bulk file that needs local search, and the other sanctions lists (EU, UK)
have different schemas. If a cheaper win is preferred, #8 (IMF/OECD/BIS SDMX) is
keyless and reuses the existing SDMX adapter.

## LLM web-search demand

The backlog above weighs general demand. When the goal is specifically the
questions an agent reaches for a search engine to answer, recency, prices and
official documentation dominate, while compliance and macro series are rarer.
Scored on the same axes, with demand read as "how often an LLM searches this":

| Candidate | Demand | Gap | Policy | Leverage | Impact | Note |
|---|---:|---:|---:|---:|---:|---|
| ~~**Market quotes: Coinbase + Yahoo Finance**~~ — shipped | 5 | 5 | 3 | 4 | **4.40** | Coinbase documented and keyless; Yahoo is an explicit aggregator exception covering NYSE and Nasdaq names |
| Official docs: MDN | 5 | 4 | 4 | 4 | 4.35 | highest coding-agent demand; covers the web platform only |
| General news: GDELT | 5 | 4 | 2 | 5 | 4.15 | aggregator exception; one API covers every topic |
| US real-time transit (GTFS-RT) | 4 | 5 | 5 | 2 | 4.05 | high effort |
| OFAC sanctions | 3 | 4 | 5 | 3 | 3.65 | compliance, not volume |

**Shipped: market quotes.** Coinbase covers crypto spot and rates; Yahoo Finance
covers NYSE- and Nasdaq-listed equities, ETFs and indices through its public chart
and search endpoints, as an explicit aggregator exception. NYSE has no public API
of its own, so a primary NYSE feed remains a gap; Yahoo is the working substitute,
never automatic.

**Next by the same rule: MDN official docs (4.35).** It is policy-clean and
keyless, and official documentation is the most common unshipped category for
coding agents. General news via GDELT (4.15) remains the runner-up, penalized for
the aggregator exception.

## Maintenance

- When a provider lands, move its row from `gap`/`partial` to `covered` and add it
  to `docs/PROVIDERS.md`.
- When a candidate is started, record the score here first; when it ships, record
  what the score got wrong.
- Re-score after every batch: a covered topic lowers the gap score of everything
  adjacent to it.
