"""Pydantic schemas for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Protocol = Literal["icmp", "udp", "tcp"]
IpVersion = Literal["auto", "4", "6"]


def _clean_host_value(v: str) -> str:
    v = v.strip()
    if not v or any(c.isspace() for c in v):
        raise ValueError("host must not contain whitespace")
    if v.startswith("-"):
        raise ValueError("host must not start with '-'")
    return v


def _clean_tags_value(v: list[str]) -> list[str]:
    cleaned: list[str] = []
    for tag in v:
        t = tag.strip()
        if t and t not in cleaned:
            cleaned.append(t[:40])
    return cleaned[:20]


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
    host: str = Field(min_length=1, max_length=253)
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
    alert_loss_pct: float = Field(default=5.0, ge=0, le=100, description="0 disables")
    alert_latency_ms: float = Field(default=200.0, ge=0, description="0 disables")


class TargetCreate(TargetBase):
    pass


class TargetUpdate(_TargetValidators):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    host: str | None = Field(default=None, min_length=1, max_length=253)
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
    alert_loss_pct: float | None = Field(default=None, ge=0, le=100)
    alert_latency_ms: float | None = Field(default=None, ge=0)


class SettingsUpdate(BaseModel):
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    asn_lookup: bool | None = None
    reverse_dns: bool | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)
    webhook_events: list[str] | None = None
    site_name: str | None = Field(default=None, max_length=60)

    @field_validator("webhook_url")
    @classmethod
    def _check_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("webhook_url must start with http:// or https://")
        return v


class ProbeRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    count: int = Field(default=5, ge=1, le=50)
    protocol: Protocol = "icmp"
    port: int | None = Field(default=None, ge=1, le=65535)
    ip_version: IpVersion = "auto"
    max_hops: int = Field(default=30, ge=1, le=64)


def apply_update(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for k, v in patch.items():
        merged[k] = v
    return merged
