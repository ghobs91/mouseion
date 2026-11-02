# Source inventory

The full registry: **125 providers and 178 capabilities across ten domains**.
`none` means keyless; every other value names the credential the manifest
declares. The authoritative live view is `mouseion providers` or the dashboard's
Sources tab. This page is the stable inventory for reading and review.

A provider is only listed here after it has a manifest, an audit pass, a fixture
or live contract, and a routing case. That is why the count is smaller than a
directory of every API on the internet.

| Provider | Auth | Capabilities |
|---|---|---|
| arXiv | none | `search` |
| BLS | none | `series_observations` |
| Bank of England news | none | `announcements` |
| Census Geocoder | none | `geocode_address` |
| Chicago food inspections | none | `inspections` |
| CFTC Commitments of Traders | none | `legacy_futures_only` |
| CFTC press releases | none | `announcements` |
| California governor news | none | `announcements` |
| CISA KEV | none | `catalog` |
| ClinicalTrials.gov | none | `search`, `get` |
| CMS Care Compare | none | `hospitals` |
| Congress.gov | free key | `bills`, `bill` |
| CourtListener | free token | `search_opinions` |
| CourtListener MCP | OAuth | `tools` (runtime) |
| crates.io | none | `search`, `crate` |
| Crossref | none | `search_works` |
| data.gov.au | none | `search_datasets` |
| DataCite | none | `search_dois` |
| ECB | none | `yield_curve`, `exchange_rates`, `policy_rate` |
| ECB press releases | none | `announcements` |
| Federal Register | none | `search`, `get` |
| Federal Reserve Board press releases | none | `announcements` |
| FRED | free key | `search_series`, `series_observations` |
| GBIF | none | `search_occurrences` |
| Georgia governor news | none | `announcements` |
| GLEIF | none | `search_lei` |
| GitHub | free token | `search_repositories`, `repository`, `releases`, `commits` |
| Internet Archive | none | `search`, `metadata` |
| Library of Congress | none | `search` |
| FIFA | none | `matches`, `competitions` |
| Premier League | none | `fixtures`, `competitions` |
| Podcast Index | free key + secret | `search`, `episodes` |
| LALIGA | none | `matches`, `standings` |
| Bundesliga | none | `matches`, `standings` |
| Caltrain news | none | `announcements` |
| Serie A | none | `matches`, `standings` |
| Stack Exchange | none | `search`, `question` |
| Ligue 1 | none | `matches`, `standings`, `calendar` |
| FAA Airport Status | none | `status` |
| FDA press releases | none | `announcements` |
| FDIC press releases | none | `announcements` |
| Florida Department of Health news | none | `announcements` |
| football-data.org (fallback) | free key | `competitions`, `matches`, `standings` |
| Schiphol Flights | free app id + key | `flights`, `flight` |
| UEFA Match Centre | none | `matches`, `match` |
| FEMA Flood Hazard Zones | none | `flood_zone` |
| MLB StatsAPI | none | `teams`, `schedule`, `standings` |
| MusicBrainz | none | `search_artists`, `search_releases`, `artist` |
| OurAirports | none | `airports` |
| TMDB | free key | `search_movies`, `movie` |
| Texas governor news | none | `announcements` |
| Transport for London live status | none | `line_status`, `arrivals`, `stop_search` |
| USAJobs | free key + email | `search` |
| Companies House | free key (Basic) | `search`, `company` |
| GeoNames | free username | `search` |
| Hugging Face Hub | none | `search_models` |
| Open Food Facts | none | `search_products` |
| Maryland Lobbyist Contributions | none | `contributions` |
| Oakland Campaign Finance | none | `contributions` |
| Los Angeles Campaign Finance | none | `contributions` |
| Go module proxy | none | `latest` |
| GOV.UK | none | `search_content` |
| GovInfo | free key | `search`, `package` |
| Grants.gov | none | `search_opportunities` |
| Hawaii Campaign Spending | none | `ledger` |
| Maven Central | none | `search` |
| MBTA news | none | `announcements` |
| New York Campaign Finance | none | `disclosures` |
| New York State food inspections | none | `inspections` |
| New York Campaign Filers | none | `filers` |
| New York City Campaign Finance | none | `contributions` |
| NASA EONET | none | `events` |
| NHC | none | `active_storms` |
| NHTSA | none | `models_for_make` |
| NIH RePORTER | none | `search_projects` |
| NIST NVD | none | `search_cves` |
| NOAA Tides and Currents | none | `water_level` |
| Nominatim | none | `search`, `reverse` |
| npm | none | `search`, `package` |
| NWS alerting | none | `active_alerts`, `forecast` |
| North Carolina governor news | none | `announcements` |
| NYC Open Data | none | `parcels` |
| NYC restaurant inspections | none | `inspections` |
| OpenAlex | none | `search_works` |
| openFDA | none | `drug_recalls`, `drug_labels`, `device_recalls` |
| FEC (openFEC) | shared DEMO_KEY | `committees` |
| OpenFEMA | none | `disaster_declarations` |
| OpenLibrary | none | `search_books` |
| Overpass | none | `query` |
| PMC | none | `search`, `summaries` |
| Pennsylvania attorney general news | none | `announcements` |
| PubChem | none | `search_compounds`, `compound` |
| PubMed | none | `search`, `summaries` |
| PyPI | none | `package`, `release` |
| ROR | none | `search_organizations` |
| SEC EDGAR | contact UA | `full_text_search`, `filings_by_cik` |
| SEC press releases | contact UA | `announcements` |
| Shopify Global Catalog | free client credentials | `tools` (runtime, read-only) |
| DataSF | none | `police_incidents` |
| SFMTA news | none | `announcements` |
| US Treasury Fiscal Data | none | `debt_to_penny`, `avg_interest_rates` |
| Wikipedia | none | `search`, `summary`, `article`, `sections`, `links`, `coordinates` |
| Washington Campaign Expenditures | none | `expenditures` |
| Washington Campaign Finance | none | `contributions` |
| Austin Campaign Finance | none | `contributions` |
| Dallas Campaign Finance | none | `transactions` |
| Fulton County Election Results | none | `results` |
| King County Election Results | none | `results` (73 elections, 2004–2026) |
| King County food inspections | none | `inspections` |
| Colorado Campaign Finance | none | `contributions`, `expenditures` (bulk ZIP) |
| El Paso Elected Officials | none | `city_council`, `municipal_court` (HTML table) |
| Rhode Island Recent Filings | none | `recent` (HTML table) |
| Ramsey County Election Results | none | `results` |
| UK National Archives | none | `search` |
| UK Parliament | none | `bills` |
| UniProt | none | `search`, `entry` |
| USAspending | none | `spending_by_award` |
| USGS earthquakes | none | `query` |
| USGS water services | none | `instantaneous_values` |
| Wayback | none | `snapshots` |
| Wikidata | none | `search_entities`, `entity` |
| Washington attorney general news | none | `announcements` |
| Washington governor news | none | `announcements` |
| World Bank | none | `indicator` |
| Zenodo | none | `search_records` |
