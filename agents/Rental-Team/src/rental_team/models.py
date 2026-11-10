from datetime import date
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from javis_core.models import BrainError, StrictModel, now, uid
from pydantic import Field, model_validator

Source = Literal["vigolive", "ziroom"]


class Requirements(StrictModel):
    city: str = Field(min_length=1, max_length=30)
    rent_min: int = Field(0, ge=0, le=1000000)
    rent_max: int = Field(gt=0, le=1000000)
    budget_kind: Literal["rent", "known_monthly"] = "rent"
    rent_type: Literal["entire", "shared"]
    move_in: date
    months: int = Field(12, ge=1, le=60)
    location: str = Field("", max_length=100)
    bedrooms: list[int] = Field(default_factory=list, max_length=6)
    hard_conditions: list[str] = Field(default_factory=list, max_length=12)
    sources: list[Source] = Field(default_factory=lambda: ["vigolive", "ziroom"], min_length=1)
    destination: str = Field("", max_length=150)
    destination_gcj02: str = ""
    commute_mode: Literal["transit", "driving", "walking", "cycling"] = "transit"
    max_commute_minutes: int | None = Field(None, ge=1, le=240)

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.rent_min > self.rent_max:
            raise ValueError("rent_min exceeds rent_max")
        if self.max_commute_minutes and not self.destination:
            raise ValueError("commute destination required")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("duplicate sources")
        return self


class Listing(StrictModel):
    source: Source
    house_id: str = Field(min_length=1, max_length=150)
    url: str | None = None
    snapshot_id: str = Field(default_factory=lambda: uid("snapshot_"))
    collected_at: str = Field(default_factory=now)
    freshness: Literal["live", "historical"] = "live"
    title: str = ""
    city: str | None = None
    district: str | None = None
    location: str | None = None
    location_precision: str = "unknown"
    gcj02: str | None = None
    rent_type: str | None = None
    area_sqm: Decimal | None = None
    layout: str | None = None
    rent: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    rent_unit: str | None = None
    monthly_fees: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    one_time_fees: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    deposit: Decimal | None = Field(None, ge=0, allow_inf_nan=False)
    first_rent_months: int | None = Field(None, ge=1, le=60)
    updated_at: str | None = None
    detail_status: str = "not_read"
    filters: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    unknowns: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    commute: dict = Field(default_factory=lambda: {"status": "not_queried"})


def platform_url(source, value):
    if not value:
        return None
    value = "https:" + value if str(value).startswith("//") else value
    url = urlsplit(str(value))
    host = url.hostname or ""
    suffix = "ziroom.com" if source == "ziroom" else "vigolive.cn"
    if (url.scheme != "https" or url.username or url.password
            or not (host == suffix or host.endswith("." + suffix))):
        raise BrainError("source_url_rejected")
    return str(value)
