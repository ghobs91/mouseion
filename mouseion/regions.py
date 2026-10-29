"""Very small place gazetteer, used only to catch provably impossible matches.

"weather in Paris" should not reach a US-only provider. That is a coverage
constraint, not a similarity problem, so no embedding model fixes it.

This is deliberately a heuristic and deliberately conservative:

* It only ever *constrains* providers that declare `regions` in their manifest.
  Undeclared means global and is never filtered.
* A provider is filtered only when its regions are disjoint from **every** region
  named in the query. "US companies operating in France" names both, so US
  providers survive.
* Multi-word places are matched as bigrams. Ambiguous names (Georgia the state,
  Georgia the country) map to several regions, which makes them harmless.

It is not a geocoder and should not become one.
"""

from __future__ import annotations

import re

_WORD = re.compile(r"[a-z']+")

PLACES: dict[str, tuple[str, ...]] = {
    # United States. Deliberately no bare "us": as a pronoun it appears in half of
    # all queries and would swamp the signal.
    "usa": ("US",), "america": ("US",), "american": ("US",),
    "united states": ("US",),
    "alabama": ("US",), "alaska": ("US",), "arizona": ("US",), "arkansas": ("US",),
    "california": ("US",), "colorado": ("US",), "connecticut": ("US",), "delaware": ("US",),
    "florida": ("US",), "hawaii": ("US",), "idaho": ("US",), "illinois": ("US",),
    "indiana": ("US",), "iowa": ("US",), "kansas": ("US",), "kentucky": ("US",),
    "louisiana": ("US",), "maine": ("US",), "maryland": ("US",), "massachusetts": ("US",),
    "michigan": ("US",), "minnesota": ("US",), "mississippi": ("US",), "missouri": ("US",),
    "montana": ("US",), "nebraska": ("US",), "nevada": ("US",), "ohio": ("US",),
    "oklahoma": ("US",), "oregon": ("US",), "pennsylvania": ("US",), "tennessee": ("US",),
    "texas": ("US",), "utah": ("US",), "vermont": ("US",), "virginia": ("US",),
    "washington": ("US",), "wisconsin": ("US",), "wyoming": ("US",),
    "georgia": ("US", "GE"),
    "new york": ("US",), "los angeles": ("US",), "chicago": ("US",),
    "san francisco": ("US",), "boston": ("US",), "seattle": ("US",), "miami": ("US",),
    "houston": ("US",), "denver": ("US",), "atlanta": ("US",), "phoenix": ("US",),
    # United Kingdom
    "uk": ("GB",), "gb": ("GB",), "britain": ("GB",), "british": ("GB",),
    "united kingdom": ("GB",),
    "england": ("GB",), "scotland": ("GB",), "wales": ("GB",), "london": ("GB",),
    "manchester": ("GB",), "edinburgh": ("GB",),
    # Europe
    "france": ("FR",), "french": ("FR",), "paris": ("FR",), "lyon": ("FR",),
    "germany": ("DE",), "german": ("DE",), "berlin": ("DE",), "munich": ("DE",),
    "spain": ("ES",), "spanish": ("ES",), "madrid": ("ES",), "barcelona": ("ES",),
    "italy": ("IT",), "italian": ("IT",), "rome": ("IT",), "milan": ("IT",),
    "netherlands": ("NL",), "dutch": ("NL",), "amsterdam": ("NL",),
    "belgium": ("BE",), "brussels": ("BE",), "ireland": ("IE",), "dublin": ("IE",),
    "portugal": ("PT",), "lisbon": ("PT",), "greece": ("GR",), "athens": ("GR",),
    "poland": ("PL",), "warsaw": ("PL",), "sweden": ("SE",), "stockholm": ("SE",),
    "norway": ("NO",), "oslo": ("NO",), "denmark": ("DK",), "copenhagen": ("DK",),
    "finland": ("FI",), "helsinki": ("FI",), "switzerland": ("CH",), "zurich": ("CH",),
    "austria": ("AT",), "vienna": ("AT",), "czech": ("CZ",), "prague": ("CZ",),
    "russia": ("RU",), "moscow": ("RU",), "ukraine": ("UA",), "kyiv": ("UA",),
    "turkey": ("TR",), "istanbul": ("TR",),
    # Asia-Pacific
    "australia": ("AU",), "australian": ("AU",), "sydney": ("AU",), "melbourne": ("AU",),
    "canberra": ("AU",), "new zealand": ("NZ",), "auckland": ("NZ",),
    "japan": ("JP",), "japanese": ("JP",), "tokyo": ("JP",), "osaka": ("JP",),
    "china": ("CN",), "chinese": ("CN",), "beijing": ("CN",), "shanghai": ("CN",),
    "india": ("IN",), "indian": ("IN",), "delhi": ("IN",), "mumbai": ("IN",),
    "korea": ("KR",), "seoul": ("KR",), "singapore": ("SG",),
    "indonesia": ("ID",), "jakarta": ("ID",), "philippines": ("PH",), "manila": ("PH",),
    "vietnam": ("VN",), "hanoi": ("VN",), "thailand": ("TH",), "bangkok": ("TH",),
    "malaysia": ("MY",), "kuala lumpur": ("MY",), "pakistan": ("PK",),
    "bangladesh": ("BD",), "sri lanka": ("LK",), "hong kong": ("HK",),
    # Americas, Africa, Middle East
    "canada": ("CA",), "canadian": ("CA",), "toronto": ("CA",), "vancouver": ("CA",),
    "montreal": ("CA",), "mexico": ("MX",), "mexican": ("MX",), "brazil": ("BR",),
    "brazilian": ("BR",), "argentina": ("AR",), "chile": ("CL",), "colombia": ("CO",),
    "peru": ("PE",), "south africa": ("ZA",), "nigeria": ("NG",), "kenya": ("KE",),
    "egypt": ("EG",), "morocco": ("MA",), "israel": ("IL",), "tel aviv": ("IL",),
    "saudi arabia": ("SA",), "riyadh": ("SA",), "emirates": ("AE",), "dubai": ("AE",),
    "qatar": ("QA",),
}


def detect_regions(text: str) -> set[str]:
    """Regions named in a query. Empty means "no geographic constraint detected"."""
    found: set[str] = set()
    # "US" in caps is the country; "us" is a pronoun. Casing is the disambiguator.
    if re.search(r"\bUS\b|\bU\.S\.", text):
        found.add("US")

    tokens = _WORD.findall(text.lower())
    for index, token in enumerate(tokens):
        found.update(PLACES.get(token, ()))
        if index + 1 < len(tokens):
            found.update(PLACES.get(f"{token} {tokens[index + 1]}", ()))
    return found


def region_conflict(provider_regions: list[str], query_regions: set[str]) -> bool:
    """True when a provider provably cannot cover anywhere the query names."""
    if not provider_regions or not query_regions:
        return False
    declared = {region.upper() for region in provider_regions}
    if "GLOBAL" in declared:
        return False
    return not (declared & query_regions)
