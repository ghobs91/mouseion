"""Provider manifests: data, not code.

A provider is a YAML file describing an upstream API surface. Only genuinely
bespoke upstreams (ASP.NET session dances, JS-rendered portals) need Python.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal, get_args

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

ParamType = Literal["string", "integer", "number", "boolean", "array", "object"]

#: The seven routing domains. A manifest declares exactly one; `sub_category`
#: carries the finer topic that used to live in the free-form `category` tag.
Domain = Literal[
    "financial_sovereign",
    "academic_scientific",
    "developer_infrastructure",
    "geo_earth_observation",
    "archives_web_state",
    "transport_infrastructure",
    "governance_legislative",
]
DOMAINS: tuple[str, ...] = get_args(Domain)

Provenance = Literal[
    "official",
    "intergovernmental",
    "registry",
    "self_hosted_node",
    "aggregator_exception",
]

_TYPE_ALIASES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


class ParamSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ParamType = "string"
    description: str = ""
    required: bool = False
    default: Any = None
    enum: list[Any] | None = None

    @field_validator("type", mode="before")
    @classmethod
    def _alias(cls, value: Any) -> Any:
        return _TYPE_ALIASES.get(value, value) if isinstance(value, str) else value


class Pagination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["offset", "page", "token"]
    #: Query param carrying the page/offset (offset and page modes).
    param: str | None = None
    #: Query param carrying the page size.
    limit_param: str | None = None
    #: Where the token mode reads the next token from in the response body.
    next_field: str | None = None
    #: When next_field is a full URL, read the cursor from this query parameter.
    next_query_param: str | None = None
    #: Where the page mode reads total pages from in the response body.
    total_pages_field: str | None = None
    #: Boolean field that says whether another page exists (USASpending).
    has_next_field: str | None = None
    default_limit: int = 25
    max_limit: int = 100
    #: Some APIs silently ignore a too-small page size (Federal Register drops per_page=1).
    min_limit: int = 1
    #: 0 for offset mode, 1 for page mode.
    start: int = 0


class Auth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["none", "api_key", "user_agent", "oauth"] = "none"
    #: Environment variable holding the credential (api_key/user_agent).
    env: str | None = None
    #: Query param or header name receiving the credential.
    param: str | None = None
    location: Literal["query", "header"] = "header"
    prefix: str = ""
    #: OAuth settings, used by remote MCP providers.
    scopes: list[str] = Field(default_factory=list)
    redirect_uri: str | None = None
    client_name: str = "mouseion"


class AuthPolicy(BaseModel):
    """The free-access claim, with the evidence and the date it was checked.

    A script cannot prove "permanently free"; it can enforce that a human recorded
    why they believed it and when. The audit fails stale evidence.
    """

    model_config = ConfigDict(extra="forbid")

    free: bool = True
    credit_card_required: bool = False
    trial: bool = False
    evidence_url: str
    verified_at: date


class RateLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_min: int = Field(default=60, gt=0)
    burst: int = Field(default=10, gt=0)
    concurrency: int = Field(default=4, gt=0)


class LocalFilter(BaseModel):
    """Post-parse filter for catalogs with no server-side search.

    Keep records where `field` (a dotted path in the raw record) equals the
    caller's `param`. CISA KEV is the motivating case: the feed is one large
    catalog, and the only useful question is "is this CVE in it?".
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    param: str


class SourcePolicy(BaseModel):
    """Who originated the data, under what access terms, at what rate.

    `is_primary` is a claim about the originator, not about usefulness. A secondary
    index that is still worth shipping must say so: `provenance: aggregator_exception`
    plus `exception_reason`, and it is excluded from Tier 1 failover.
    """

    model_config = ConfigDict(extra="forbid")

    is_primary: bool = True
    originator: str
    originator_domain: str
    provenance: Provenance = "official"
    tier: Literal[1, 2] = 1
    auth_policy: AuthPolicy
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    exception_reason: str | None = None


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    path: str
    #: Override the manifest base when a provider spreads one API across hosts
    #: (SEC serves search from efts.sec.gov and submissions from data.sec.gov).
    base: str | None = None
    method: Literal["GET", "POST"] = "GET"
    adapter: str | None = None
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)
    pagination: Pagination | None = None
    #: Dotted path to the record list in the response. None means the body is one record.
    record_path: str | None = None
    #: Dotted path (against the normalized record) holding the stable upstream id.
    id_field: str | None = None
    #: Composite id built from normalized fields, e.g. "{report_date}:{market}".
    #: Takes precedence over id_field when both are set.
    id_template: str | None = None
    #: Dotted paths to pull from the raw record. When present the record is projected.
    #: A value may be a list of paths; the first non-null path wins (language fallback).
    fields: dict[str, str | list[str]] | None = None
    #: Collapse list-of-object fields to one attribute, e.g. {interventions: name}.
    pluck: dict[str, str] = Field(default_factory=dict)
    #: Keys to strip when no projection is declared.
    drop: list[str] = Field(default_factory=list)
    #: Format string applied to the normalized record, e.g. ".../study/{nct_id}".
    source_url_template: str | None = None
    #: Dotted path in the body holding licence text shown with every result.
    terms_field: str | None = None
    #: Dotted path in the body holding an upstream version/snapshot marker.
    version_field: str | None = None
    #: Some APIs signal "no matches" as 404 (openFDA). Treat that as an empty page.
    empty_on_404: bool = False
    #: Friendly param name -> upstream param name, e.g. status -> filter.overallStatus.
    #: Pagination params (limit/page/offset) are owned by `pagination`, not by this map.
    alias: dict[str, str] = Field(default_factory=dict)
    #: Questions this capability answers. The strongest routing signal per token.
    exemplars: list[str] = Field(default_factory=list)
    #: Regexes that identify this capability outright, e.g. an NCT number or a DOI.
    patterns: list[str] = Field(default_factory=list)
    #: Failover group. Two capabilities may fail over only when they share a cluster
    #: and can accept the same canonical fields. No cluster means no failover.
    cluster: str | None = None
    #: Canonical field -> source param, e.g. {text: query, limit: limit}. The engine
    #: uses this to translate a CanonicalQuery into a source-specific request.
    query_map: dict[str, str] = Field(default_factory=dict)
    #: Post-parse filter for catalogs with no server-side search.
    local_filter: LocalFilter | None = None
    #: JSON body template for POST APIs. All params go to the body, not the query.
    #: ``"{name}"`` is replaced by the raw value; keys with no value are dropped.
    body_template: dict[str, Any] | None = None
    #: Query template for APIs whose filters are nested params
    #: (`conditions[0][property]=state`). Rendered like body_template, then flattened.
    query_template: dict[str, Any] | None = None
    #: When the record container is an object keyed by id, skip these keys
    #: (PubMed's esummary mixes a "uids" list into its result map).
    map_exclude: list[str] = Field(default_factory=list)
    #: Upstream returns a fixed, unstably ordered window (SEC: 100 hits, re-ranked per
    #: request). Fetch the window once, cache it, and page through it locally so
    #: pages cannot overlap.
    local_window: bool = False
    #: Some responses are columnar: {field: [v1, v2, ...], ...}. Zip them into rows.
    transpose: bool = False
    example: dict[str, Any] | None = None

    @field_validator("params", mode="before")
    @classmethod
    def _shorthand(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        out: dict[str, Any] = {}
        for name, spec in value.items():
            out[name] = {"type": spec} if isinstance(spec, str) else spec
        return out

    @field_validator("alias")
    @classmethod
    def _no_pagination_alias(
        cls, value: dict[str, str], info: ValidationInfo
    ) -> dict[str, str]:
        # A capability with no pagination may use `limit` as an ordinary param
        # (UniProt maps it to `size`). Only pagination-owned params are reserved.
        if info.data.get("pagination") is None:
            return value
        reserved = {"limit", "page", "offset", "cursor"}
        if reserved & set(value):
            raise ValueError(f"pagination params are not aliasable: {reserved & set(value)}")
        return value

    @field_validator("patterns")
    @classmethod
    def _compile_patterns(cls, value: list[str]) -> list[str]:
        import re

        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid pattern {pattern!r}: {exc}") from None
        return value

    def required_params(self) -> list[str]:
        return [name for name, spec in self.params.items() if spec.required]

    def coerce(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate caller params and drop unknowns, so a hallucinated key is a no-op."""
        out: dict[str, Any] = {}
        for name, spec in self.params.items():
            if name in params:
                out[name] = _coerce_value(name, params[name], spec)
            elif spec.default is not None:
                out[name] = spec.default
        return out


def _coerce_value(name: str, value: Any, spec: ParamSpec) -> Any:
    if value is None:
        return None
    if spec.type == "integer" and not isinstance(value, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"param '{name}' must be an integer, got {value!r}") from None
    if spec.type == "number":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError(f"param '{name}' must be a number, got {value!r}") from None
    if spec.type == "boolean":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if spec.type == "array" and isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    category: str = "public-records"
    #: HTTP REST is the default; mcp_remote proxies a remote MCP server.
    transport: Literal["http", "mcp_remote"] = "http"
    #: For remote MCP, expose only tools annotated readOnlyHint=true.
    read_only_only: bool = True
    #: Explicit remote tool allowlist; empty means use read_only_only.
    allow_tools: list[str] = Field(default_factory=list)
    #: Required routing domain (one of the seven) and its finer sub-category.
    domain: Domain
    sub_category: str = Field(min_length=1)
    #: Primary-source and access policy. Required so the audit has something to check.
    source: SourcePolicy
    description: str = ""
    docs_url: str | None = None
    keywords: list[str] = Field(default_factory=list)
    #: Geographic coverage as ISO-ish codes ("US", "GB", "AU"). Empty means global
    #: and is never filtered. Used only to reject provably impossible matches.
    regions: list[str] = Field(default_factory=list)
    base: str
    adapter: str = "rest_json"
    auth: Auth = Field(default_factory=Auth)
    attribution: str | None = None
    terms: str | None = None
    cache_ttl: float | None = None
    #: Per-provider request timeout; falls back to MOUSEION_TIMEOUT.
    timeout_s: float | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Capability]

    @field_validator("regions")
    @classmethod
    def _normalise_regions(cls, value: list[str]) -> list[str]:
        return [region.strip().upper() for region in value]

    def ttl(self, default: float) -> float:
        return self.cache_ttl if self.cache_ttl is not None else default

    @property
    def requires_key(self) -> bool:
        return self.auth.type == "api_key"

    @property
    def requires_oauth(self) -> bool:
        return self.auth.type == "oauth"

    @property
    def env_key_name(self) -> str | None:
        return self.auth.env if self.requires_key else None

    def compact(self) -> dict[str, Any]:
        """The cheap form shown by providers_search. Keep it small."""
        return {
            "id": self.id,
            "title": self.title,
            "transport": self.transport,
            "domain": self.domain,
            "sub_category": self.sub_category,
            "tier": self.source.tier,
            "is_primary": self.source.is_primary,
            "category": self.category,
            "auth": self.auth.type,
            "capabilities": [
                {"name": name, "description": _clip(cap.description, 110)}
                for name, cap in self.capabilities.items()
            ],
        }

    def describe(self) -> dict[str, Any]:
        """The expensive form, shown only for the provider the model picked."""
        caps = {}
        for name, cap in self.capabilities.items():
            caps[name] = {
                "description": cap.description,
                "required_params": cap.required_params(),
                "params": {
                    pname: {
                        k: v
                        for k, v in {
                            "type": pspec.type,
                            "description": pspec.description,
                            "required": pspec.required or None,
                            "default": pspec.default,
                            "enum": pspec.enum,
                        }.items()
                        if v is not None
                    }
                    for pname, pspec in cap.params.items()
                },
                "pagination": cap.pagination.mode if cap.pagination else None,
                "example": cap.example,
            }
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "transport": self.transport,
            "domain": self.domain,
            "sub_category": self.sub_category,
            "description": self.description,
            "docs_url": self.docs_url,
            "auth": self.auth.type,
            "auth_env": self.auth.env,
            "source": {
                "is_primary": self.source.is_primary,
                "originator": self.source.originator,
                "originator_domain": self.source.originator_domain,
                "provenance": self.source.provenance,
                "tier": self.source.tier,
                "requires_key": self.requires_key,
                "env_key_name": self.env_key_name,
                "auth_policy": {
                    "free": self.source.auth_policy.free,
                    "credit_card_required": self.source.auth_policy.credit_card_required,
                    "trial": self.source.auth_policy.trial,
                    "evidence_url": self.source.auth_policy.evidence_url,
                    "verified_at": self.source.auth_policy.verified_at.isoformat(),
                },
                "rate_limit": self.source.rate_limit.model_dump(),
                **(
                    {"exception_reason": self.source.exception_reason}
                    if self.source.exception_reason
                    else {}
                ),
            },
            "attribution": self.attribution,
            "terms": self.terms,
            "regions": self.regions or "global",
            "limits": self.limits,
            "capabilities": caps,
        }


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def load_manifest(path: Path) -> Manifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: manifest must be a mapping")
    try:
        return Manifest.model_validate(raw)
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        raise ValueError(f"{path.name}: {exc}") from exc


def load_manifests(directory: Path) -> dict[str, Manifest]:
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"providers directory not found: {directory}")
    out: dict[str, Manifest] = {}
    for path in sorted(directory.glob("*.y*ml")):
        manifest = load_manifest(path)
        if manifest.id in out:
            raise ValueError(f"duplicate provider id '{manifest.id}' in {path}")
        out[manifest.id] = manifest
    return out
