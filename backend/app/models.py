"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

Protocol = Literal["icmp", "udp", "tcp"]
IpVersion = Literal["auto", "4", "6"]
ProbeType = Literal["mtr", "ping", "http", "tcp", "dns", "globalping"]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
DnsRecordType = Literal["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "PTR", "SRV"]
GlobalpingMeasurement = Literal["ping", "traceroute", "mtr", "dns", "http"]
GlobalpingHttpMethod = Literal["GET", "HEAD", "OPTIONS"]
GlobalpingHttpProtocol = Literal["HTTPS", "HTTP", "HTTP2"]


def clean_status_spec(v: str) -> str:
    """'200', '200-299' or '200,301' style lists of acceptable HTTP status codes."""
    v = (v or "").strip() or "200-299"
    for token in v.split(","):
        if not re.match(r"^\s*\d{3}(\s*-\s*\d{3})?\s*$", token):
            raise ValueError(f"invalid status token '{token.strip()}'")
    return v


class HttpOptions(BaseModel):
    method: HttpMethod = "GET"
    expected_status: str = Field(default="200-299", max_length=100, description="e.g. 200, 200-299, 200,301")
    keyword: str = Field(default="", max_length=500)
    keyword_absent: bool = False
    json_path: str = Field(default="", max_length=300, description="dotted path, e.g. data.items[0].status")
    json_expected: str = Field(default="", max_length=500, description="value, or ==/!=/>/</>=/<= number, or ~substring")
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = Field(default="", max_length=20000)
    timeout_sec: float = Field(default=10.0, ge=1, le=120)
    verify_tls: bool = True
    follow_redirects: bool = True
    tls_warn_days: int = Field(default=14, ge=0, le=365, description="warn (degraded) when the certificate expires within N days; 0 disables")
    tls_info: bool = Field(default=True, description="record the certificate (subject, issuer, validity, names, protocol) with every run")

    @field_validator("expected_status")
    @classmethod
    def _status(cls, v: str) -> str:
        return clean_status_spec(v)


class PingOptions(BaseModel):
    timeout_sec: float = Field(default=2.0, ge=0.2, le=30)


class TcpOptions(BaseModel):
    timeout_sec: float = Field(default=5.0, ge=0.5, le=60)


class DnsOptions(BaseModel):
    record_type: DnsRecordType = "A"
    resolver: str = Field(default="", max_length=253, description="IP or hostname of the server to query; empty = system resolver")
    expected: str = Field(default="", max_length=500, description="substring that must appear in one of the answers")
    timeout_sec: float = Field(default=5.0, ge=0.5, le=60)
    random_prefix: bool = Field(
        default=False,
        description="query a random label under the name on every run so no cache can answer; the resolver has to ask the "
        "authoritative servers, and NXDOMAIN then counts as a successful lookup (the timing is what matters)",
    )


class GlobalpingOptions(BaseModel):
    measurement: GlobalpingMeasurement = "ping"
    location: str = Field(
        default="world",
        max_length=200,
        description="Globalping 'magic' location: country, city, continent, region, ASN, network or cloud region, "
        "e.g. 'Germany', 'Frankfurt', 'EU', 'AS3320', 'aws-eu-west-1'; 'world' picks any probe",
    )
    probes: int = Field(default=1, ge=1, le=10, description="probes to use at that location (ping, dns, http); traceroute and mtr always use one")
    # dns
    record_type: DnsRecordType = "A"
    resolver: str = Field(default="", max_length=253, description="dns: resolver the probe should query (IP or host name); empty = the probe's own")
    expected: str = Field(default="", max_length=500, description="dns: substring that must appear in one of the answers")
    # http
    path: str = Field(default="/", max_length=2048, description="http: request path (and query) on the target host")
    http_method: GlobalpingHttpMethod = "GET"
    http_protocol: GlobalpingHttpProtocol = "HTTPS"
    expected_status: str = Field(default="200-299", max_length=100, description="http: e.g. 200, 200-299, 200,301")
    keyword: str = Field(default="", max_length=500, description="http: text that must appear in the (first 10 kB of the) body")

    @field_validator("location")
    @classmethod
    def _location(cls, v: str) -> str:
        return v.strip() or "world"

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = v.strip() or "/"
        return v if v.startswith("/") else "/" + v

    @field_validator("expected_status")
    @classmethod
    def _status(cls, v: str) -> str:
        return clean_status_spec(v)

    @field_validator("resolver", "expected", "keyword")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


OPTION_MODELS: dict[str, type[BaseModel]] = {"http": HttpOptions, "ping": PingOptions, "tcp": TcpOptions, "dns": DnsOptions, "globalping": GlobalpingOptions}


def validate_options(kind: str, options: dict[str, Any] | None) -> dict[str, Any]:
    model = OPTION_MODELS.get(kind)
    if model is None:
        return {}
    return model.model_validate(options or {}).model_dump()


def _clean_host_value(v: str) -> str:
    v = v.strip()
    if not v or any(c.isspace() for c in v):
        raise ValueError("host must not contain whitespace")
    if v.startswith("-"):
        raise ValueError("host must not start with '-'")
    return v


def sort_tags(tags: list[str]) -> list[str]:
    """Alphabetical, case-insensitive; exact-case variants keep a deterministic order (uppercase first)."""
    return sorted(tags, key=lambda t: (t.casefold(), t))


def _clean_tags_value(v: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in v:
        t = tag.strip()[:40]
        if t and t not in cleaned:
            cleaned.append(t)
    return sort_tags(cleaned[:20])


_HEX_COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")
MAX_TAG_COLORS = 500


def clean_tag_colors(v: dict[str, str]) -> dict[str, str]:
    """Normalise a tag -> colour map: keys are trimmed tags, values lower-case #rrggbb; empty values mean 'automatic' and are dropped."""
    out: dict[str, str] = {}
    for tag, color in v.items():
        key = str(tag).strip()[:40]
        value = (color or "").strip().lower()
        if not key or not value:
            continue
        if not _HEX_COLOR_RE.match(value):
            raise ValueError(f"colour for tag '{key}' must be a hex value like #38bdf8")
        out[key] = value
    if len(out) > MAX_TAG_COLORS:
        raise ValueError(f"at most {MAX_TAG_COLORS} tag colours can be stored")
    return out


class _TargetValidators(BaseModel):
    @field_validator("host", check_fields=False)
    @classmethod
    def _clean_host(cls, v: str | None) -> str | None:
        return None if v is None else _clean_host_value(v)

    @field_validator("tags", check_fields=False)
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _clean_tags_value(v)


class TargetBase(_TargetValidators):
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=2048, description="hostname/IP, or a URL for http probes")
    type: ProbeType = "mtr"
    options: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list)
    interval_sec: int = Field(default=300, ge=10, le=86400, description="Seconds between MTR runs")
    count: int = Field(default=10, ge=1, le=200, description="Probes per hop per run")
    probe_interval: float = Field(default=1.0, ge=0.1, le=10.0, description="Seconds between probes")
    protocol: Protocol = "icmp"
    port: int | None = Field(default=None, ge=1, le=65535)
    packet_size: int = Field(default=64, ge=28, le=1500)
    ip_version: IpVersion = "auto"
    max_hops: int = Field(default=30, ge=1, le=64)
    enabled: bool = True
    notify: bool = Field(default=True, description="deliver this target's events to the notification channels; events are recorded either way")
    alert_loss_pct: float = Field(default=5.0, ge=0, le=100, description="0 disables")
    alert_latency_ms: float = Field(default=200.0, ge=0, description="0 disables")


LATENCY_ALERT_DEFAULT: dict[str, float] = {"mtr": 200.0, "ping": 200.0, "http": 1500.0, "tcp": 500.0, "dns": 500.0, "globalping": 200.0}


class TargetCreate(TargetBase):
    @model_validator(mode="after")
    def _check_options(self) -> "TargetCreate":
        self.options = validate_options(self.type, self.options)
        if self.type == "tcp" and not self.port:
            raise ValueError("tcp probes need a port")
        # A 200 ms threshold suits paths and pings, not HTTP; apply a per-type default when the caller did not choose one.
        if "alert_latency_ms" not in self.model_fields_set:
            self.alert_latency_ms = LATENCY_ALERT_DEFAULT.get(self.type, 200.0)
        return self


class TargetUpdate(_TargetValidators):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=2048)
    type: ProbeType | None = None
    options: dict[str, Any] | None = None
    description: str | None = None
    tags: list[str] | None = None
    interval_sec: int | None = Field(default=None, ge=10, le=86400)
    count: int | None = Field(default=None, ge=1, le=200)
    probe_interval: float | None = Field(default=None, ge=0.1, le=10.0)
    protocol: Protocol | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    packet_size: int | None = Field(default=None, ge=28, le=1500)
    ip_version: IpVersion | None = None
    max_hops: int | None = Field(default=None, ge=1, le=64)
    enabled: bool | None = None
    notify: bool | None = None
    alert_loss_pct: float | None = Field(default=None, ge=0, le=100)
    alert_latency_ms: float | None = Field(default=None, ge=0)


class SettingsUpdate(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    asn_lookup: bool | None = None
    reverse_dns: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_events: list[str] | None = None
    pushover_enabled: bool | None = None
    pushover_user_key: str | None = Field(default=None, max_length=64)
    pushover_api_token: str | None = Field(default=None, max_length=64)
    pushover_device: str | None = Field(default=None, max_length=64)
    pushover_sound: str | None = Field(default=None, max_length=32)
    pushover_priority: str | None = Field(default=None, max_length=8)
    pushover_events: list[str] | None = None
    base_url: str | None = Field(default=None, max_length=2048)
    site_name: str | None = Field(default=None, max_length=60)
    tag_colors: dict[str, str] | None = Field(default=None, description="tag -> #rrggbb; tags without an entry get an automatic colour")
    globalping_token: str | None = Field(default=None, max_length=200, description="optional Globalping API token for higher rate limits")
    maxmind_account_id: str | None = Field(default=None, max_length=32, description="MaxMind account ID (optional; digits)")
    maxmind_license_key: str | None = Field(default=None, max_length=200, description="MaxMind licence key; enables the GeoLite2 download and the map on target pages")

    @field_validator("maxmind_account_id")
    @classmethod
    def _check_account_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not v.isdigit():
            raise ValueError("maxmind_account_id must be the numeric account ID shown on maxmind.com")
        return v

    @field_validator("tag_colors")
    @classmethod
    def _check_tag_colors(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        return None if v is None else clean_tag_colors(v)

    @field_validator("webhook_url", "base_url")
    @classmethod
    def _check_url(cls, v: str | None, info: ValidationInfo) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"{info.field_name} must start with http:// or https://")
        return v.rstrip("/") if info.field_name == "base_url" else v

    @field_validator("pushover_user_key", "pushover_api_token", "pushover_device", "pushover_sound", "globalping_token", "maxmind_license_key")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return None if v is None else v.strip()

    @field_validator("pushover_priority")
    @classmethod
    def _check_priority(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v not in {"auto", "-2", "-1", "0", "1", "2"}:
            raise ValueError("pushover_priority must be auto or an integer from -2 to 2")
        return v

    @field_validator("webhook_events", "pushover_events")
    @classmethod
    def _check_events(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        allowed = {"down", "recovered", "degraded", "route_change"}
        bad = [e for e in v if e not in allowed]
        if bad:
            raise ValueError(f"unknown event kinds: {', '.join(bad)}")
        return list(dict.fromkeys(v))


class NotificationTest(BaseModel):
    channel: Literal["webhook", "pushover"]
    settings: SettingsUpdate | None = None


class ProbeRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    count: int = Field(default=5, ge=1, le=50)
    protocol: Protocol = "icmp"
    port: int | None = Field(default=None, ge=1, le=65535)
    ip_version: IpVersion = "auto"
    max_hops: int = Field(default=30, ge=1, le=64)


class BulkAction(BaseModel):
    action: Literal["pause", "resume", "mute", "unmute", "run", "delete"]
    ids: list[int] = Field(min_length=1, max_length=1000)


class TargetImport(BaseModel):
    targets: list[TargetCreate] = Field(min_length=1, max_length=1000)
    mode: Literal["upsert", "create", "replace"] = "upsert"
