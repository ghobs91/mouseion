# Provider targets

Firecrawl's [Alexandria](https://www.firecrawl.dev/alexandria) is the reference
point: 428 providers sorted into demand-shaped categories. Its catalogue is
mostly commercial wrappers, so the counts are not directly comparable to a
primary-source registry. They are still the best public signal for *which topics
people actually ask agents about*, which is what this document uses them for.

Captured 2026-10-31 from the Alexandria directory page.

## The benchmark

| Alexandria category | Providers |
|---|---|
| Public records | 199 |
| Company | 90 |
| Retail | 66 |
| Finance | 39 |
| Real estate | 34 |
| Health | 32 |
| Life sciences | 31 |
| Places | 23 |
| Developer | 21 |
| Travel | 21 |
| News | 16 |
| Research | 16 |
| Sports | 14 |
| Jobs | 13 |
| AI models | 12 |
| People | 12 |
| Restaurant | 10 |
| Entertainment | 8 |
| Marketing | 8 |
| Apps | 6 |
| Social | 6 |
| Tools | 3 |
| Other | 1 |
| Podcasts | 1 |
| Web analytics & marketing | 1 |

## Where mouseion stands

110 providers across ten routing domains. The counts below are providers, not
capabilities; one provider often covers several capabilities.

| Domain | Providers | What is already strong |
|---|---|---|
| `civic_local` | 17 | campaign finance and election results across 11 jurisdictions |
| `academic_scientific` | 14 | arXiv, Crossref, OpenAlex, DataCite, PMC, PubMed, UniProt, GBIF |
| `geo_earth_observation` | 14 | Nominatim, Overpass, USGS, NOAA, NWS, EONET, GeoNames |
| `governance_legislative` | 12 | Congress, GovInfo, Federal Register, UK Parliament, CourtListener, FDA press |
| `financial_sovereign` | 17 | ECB, FRED, BLS, World Bank, Treasury, CFTC, SEC, FEC, GLEIF; central-bank and regulator press |
| `developer_infrastructure` | 9 | npm, PyPI, crates, Go, Maven, GitHub, NVD, Hugging Face |
| `archives_web_state` | 5 | Wayback, Internet Archive, LoC, UK National Archives |
| `commerce` | 2 | Shopify catalog, Open Food Facts |
| `transport_infrastructure` | 7 | OurAirports, FAA status, Schiphol flights, MBTA, SFMTA, Caltrain news |
| `reference` | 13 | Wikipedia, MLB, MusicBrainz, TMDB, USAJobs, UEFA, FIFA, Premier League, LALIGA, Bundesliga, Serie A, Ligue 1, football-data |

## Gap matrix

"Targets" are primary sources or originating registries, not wrappers. Tier
follows the project rules: Tier 1 keyless, Tier 2 permanently free without a
card.

| Category | Now | Gap | Primary targets | Tier |
|---|---|---|---|---|
| Public records (199) | ~45 government/legal/civic providers | Depth, not breadth: state and local coverage | more Socrata/state portals; county recorders | 1 |
| Company (90) | SEC, GLEIF, ROR, Companies House | UK/EU registries, sanctions, US federal awards | **Companies House** ✅, OpenSanctions, SAM.gov, state business registries | 2 / 1 |
| Retail (66) | Shopify, Open Food Facts | barcodes, recalls, non-food products | **Open Food Facts** ✅, Open Products Facts, openFDA recalls ✅, GS1 (paid) | 1 |
| Finance (39) | 11 providers | IMF, OECD, BIS, exchange data | IMF SDMX, OECD SDMX, BIS SDMX, Nasdaq (commercial) | 1 |
| Real estate (34) | Census, Nominatim, Overpass, city parcels, **FEMA NFHL** | housing, assessors | **FEMA NFHL** ✅, HUD, county assessor portals | 1 / 2 |
| Health (32) | openFDA, CMS, ClinicalTrials, NIH, PubMed | WHO, NHS, state health | WHO GHO (OData), NHS Digital, state health Socrata | 1 |
| Life sciences (31) | UniProt, PubChem, PMC, GBIF | structures, genomes | RCSB PDB, Ensembl, bioRxiv, KEGG (academic) | 1 |
| Places (23) | Nominatim, Overpass, USGS, **GeoNames** | hierarchy, timezone, population | **GeoNames** ✅, Who's On First, USGS GNIS | 2 / 1 |
| Developer (21) | 9 providers | package registries, Q&A | **Hugging Face** ✅, Docker Hub, Stack Exchange | 1 |
| Travel (21) | OurAirports, FAA, Schiphol | routes, rail | **OurAirports** ✅, **FAA NAS** ✅, **Schiphol** ✅, OpenFlights | 1 / 2 |
| News (16) | ten agency and transit press feeds, plus official registers | global news index | more agency press releases via RSS; GDELT (aggregator exception), Media Cloud | 1 / 2 |
| Research (16) | 14 providers | citation graph, preprints | Semantic Scholar, CORE, bioRxiv/medRxiv | 2 / 1 |
| Sports (14) | MLB, UEFA, Premier League, FIFA, LALIGA, Bundesliga, Serie A, Ligue 1, football-data | other leagues | **MLB** ✅, **UEFA** ✅, **Premier League** ✅, **FIFA** ✅, **LALIGA** ✅, **Bundesliga** ✅, **Serie A** ✅, **Ligue 1** ✅; football-data.org retained as an explicit-use fallback | 1 / 2 |
| Jobs (13) | USAJobs | occupations | **USAJobs** ✅, O*NET, Arbeitnow | 2 / 1 |
| AI models (12) | **Hugging Face** | model catalogues | **Hugging Face** ✅, OpenRouter (commercial) | 1 |
| People (12) | Wikidata, GLEIF, SEC insiders | sanctions, officers | OpenSanctions, Companies House officers | 2 |
| Restaurant (10) | Overpass (OSM) | inspections, menus | city health-inspection Socrata datasets | 1 |
| Entertainment (8) | MusicBrainz, TMDB | podcasts | **MusicBrainz** ✅, **TMDB** ✅, Podcast Index | 1 / 2 |
| Marketing (8) | none | — | **do not target**: commercial only | — |
| Apps (6) | none | — | **do not target**: no official store API | — |
| Social (6) | none | — | **do not target**: commercial platforms | — |
| Tools (3) | none | — | skip unless a primary API exists | — |
| Podcasts (1) | none | podcast index | Podcast Index | 2 |
| Web analytics (1) | none | — | **do not target** | — |

## Priority

**P0 — biggest gaps that fit the source policy (shipped in this batch):**

1. **Company**: Companies House. The UK register is free, official and covers
   the largest non-US company category.
2. **Places**: GeoNames. Adds hierarchy, timezone and population that
   Nominatim/Overpass do not carry.
3. **AI models**: Hugging Face Hub. The originating registry for open models.
4. **Retail**: Open Food Facts. Barcodes and nutrition, keyless.

**Soccer sourcing note:** football-data.org does not publish its upstreams. The
Premier League, UEFA and FIFA expose public official feeds, so those are used
directly. The four remaining domestic leagues are now read from the same
browser-facing backends their sites use: LALIGA's public-service API, the
Bundesliga's bapi feed, Lega Serie A's data service and LFP's match API for
Ligue 1. Those endpoints need browser headers (and LALIGA's public web
subscription key) and are more brittle than a published developer API, so the
season keys are manifest parameters and football-data.org remains as an
explicit-use fallback.

**P1 — shipped:**

5. **Jobs**: USAJobs (free key plus the registered email header) ✅. O*NET
   still needs a key or its bulk database; it is the remaining P1 item.
6. **Sports**: MLB StatsAPI (official, keyless) ✅.
7. **Entertainment**: MusicBrainz (keyless) and TMDB (free key) ✅.
8. **Travel**: OurAirports (public-domain CSV) ✅.
9. **Real estate**: FEMA NFHL via ArcGIS REST (keyless) ✅.

**Agency announcements — started:** the Federal Reserve, SEC, FDIC, CFTC, ECB,
Bank of England, FDA, MBTA, SFMTA and Caltrain publish their own press feeds, now
read directly as RSS with a local keyword filter. This is the in-policy
alternative to a news aggregator: the feed is the originator's own publication,
so no `aggregator_exception` is needed. The remaining work is breadth — roughly
230 federal and state agency feeds are available — not new plumbing.

**P2 — depth and long tail:**

10. **Finance**: IMF, OECD and BIS SDMX. These are the remaining Cat 1 stretch
    targets from the original specification.
11. **Health**: WHO Global Health Observatory.
12. **Life sciences**: RCSB PDB and Ensembl.
13. **Research**: Semantic Scholar and CORE.
14. **People**: OpenSanctions.
15. **Restaurant**: city health-inspection datasets.
16. **News**: GDELT as an `aggregator_exception`, never automatic.

**Do not target:** marketing, apps, social, web analytics. They are commercial
platforms or have no official API; adding them would break the primary-source
claim for no real coverage gain.

## How a target becomes a provider

1. Check the source is the originator, not a wrapper.
2. Write the manifest in `providers/`, with `domain`, `sub_category`, `source`
   policy and an `auth.signup_url` when a key is needed.
3. Run `mouseion audit` and fix every error.
4. Record a fixture, add a routing case, and confirm the live contract.
5. If the source needs a key, save it in the dashboard's Credentials tab; the
   adapter resolves it without a restart.

A target is not "covered" until it passes all five. Counts in this document are
therefore deliberately smaller than Alexandria's.
