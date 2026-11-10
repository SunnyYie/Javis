import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

from javis_core.models import BrainError

from .mcp_client import BLOCKED, MCP, failure_code
from .models import Listing, platform_url

VIGO_FIELDS = {
    "house_id", "city", "district", "address", "compound", "layout", "bedrooms",
    "rent_type", "rent_price_yuan", "area_sqm", "tags", "near_subway",
    "create_time", "updated_at", "electricity_fee", "water_fee", "url",
    "other_fee_desc",
    "fixed_monthly_fee_yuan", "one_time_fee_yuan", "deposit_yuan", "first_rent_months",
}
ZIROOM_FIELDS = {
    "listing_id", "url", "title", "city", "district", "area_name", "community",
    "rental_mode", "area", "layout", "available", "lease", "payments",
}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def vigo_listing(raw, filters):
    data = {k: v for k, v in raw.items() if k in VIGO_FIELDS}
    house_id = data.get("house_id")
    if not isinstance(house_id, str) or not house_id:
        raise BrainError("missing_house_id")
    rent = number(data.get("rent_price_yuan"))
    return Listing(
        source="vigolive", house_id=house_id, url=platform_url("vigolive", data.get("url")),
        title=" ".join(str(data.get(k, "")) for k in ("compound", "layout")).strip(),
        city=data.get("city"), district=data.get("district"),
        location=data.get("address") or data.get("compound"), location_precision="platform_address",
        rent_type={1: "entire", 2: "shared"}.get(data.get("rent_type")),
        area_sqm=number(data.get("area_sqm")), layout=data.get("layout"),
        rent=rent, rent_unit="CNY/month" if rent is not None else None,
        monthly_fees=number(data.get("fixed_monthly_fee_yuan")),
        one_time_fees=number(data.get("one_time_fee_yuan")),
        deposit=number(data.get("deposit_yuan")),
        first_rent_months=data.get("first_rent_months"),
        updated_at=str(data["updated_at"]) if data.get("updated_at") else None,
        filters=filters, evidence=data,
        unknowns=[k for k in ("updated_at", "fixed_monthly_fee_yuan", "one_time_fee_yuan",
                              "deposit_yuan", "first_rent_months") if data.get(k) is None],
    )


def ziroom_listing(raw, filters):
    url = platform_url("ziroom", raw.get("url"))
    if not url or not re.fullmatch(r"/(?:[a-z]+/)?\d+\.html", urlsplit(url).path):
        raise BrainError("source_url_rejected")
    return Listing(
        source="ziroom", house_id=urlsplit(url).path, url=url,
        title=str(raw.get("name", raw.get("title", "")))[:300], filters=filters,
        evidence={k: raw[k] for k in ("name", "url") if k in raw},
    )


class Vigo:
    source = "vigolive"

    def __init__(self, settings, factory=MCP):
        self.settings, self.factory = settings, factory

    async def search(self, requirements, budget):
        records, issues = [], []
        params = {
            "city": requirements.city, "price_min": requirements.rent_min,
            "price_max": requirements.rent_max,
            "rent_type": 1 if requirements.rent_type == "entire" else 2,
            "page": 1, "page_size": 10,
        }
        if requirements.location:
            params["location"] = requirements.location
        if requirements.bedrooms:
            params["bedrooms"] = requirements.bedrooms
        async with self.factory(self.source, self.settings, budget) as client:
            result = await client.call("search_houses", params)
            if (not isinstance(result.get("houses"), list)
                    or not isinstance(result.get("total"), int)
                    or result["total"] < 0
                    or (result["total"] == 0 and result["houses"])
                    or (result["total"] > 0 and not result["houses"])):
                raise BrainError("tool_response_invalid")
            for raw in result["houses"][:10]:
                records.append(vigo_listing(raw, params))
            budget.checkpoint(records, ["details_not_completed"])
            for index, listing in enumerate(records[:self.settings.detail_limit]):
                try:
                    detail = await client.call("get_house_detail", {"house_id": listing.house_id})
                    if detail.get("found") is False:
                        raise BrainError("listing_not_found")
                    raw = detail.get("house", detail)
                    if raw.get("house_id") != listing.house_id:
                        raise BrainError("house_id_mismatch")
                    merged = {**listing.evidence, **{
                        k: v for k, v in raw.items() if k in VIGO_FIELDS and v is not None
                    }}
                    expanded = vigo_listing(merged, params)
                    expanded.snapshot_id = listing.snapshot_id
                    expanded.detail_status = "read"
                    expanded.conflicts = [
                        k for k in ("rent_price_yuan", "city", "rent_type")
                        if k in raw and listing.evidence.get(k) is not None
                        and listing.evidence[k] != raw[k]
                    ]
                    records[index] = expanded
                except Exception as exc:
                    code = failure_code(exc)
                    listing.detail_status = code
                    issues.append(code)
                    if code in BLOCKED or code in ("task_budget_exhausted", "task_timeout"):
                        break
                budget.checkpoint(records, issues)
            return records, issues


class Ziroom:
    source = "ziroom"

    def __init__(self, settings, factory=MCP):
        self.settings, self.factory = settings, factory

    async def search(self, requirements, budget):
        records, issues = [], []
        async with self.factory(self.source, self.settings, budget) as client:
            sid = None
            try:
                created = await client.call("create_search_session", {"city": requirements.city})
                sid, version = created["session_id"], created["state_version"]
                schema = await client.call("get_filter_schema", {"session_id": sid})
                fields = {f["field_id"]: f for f in schema["fields"]}
                expected = {}

                async def modify(name, arguments, field, value):
                    nonlocal version
                    response = await client.call(name, {
                        "session_id": sid, "expected_version": version, **arguments,
                    })
                    version = response["state_version"]
                    # Auto-restored filters no longer match this request; never reuse them.
                    if not response.get("applied"):
                        raise BrainError("filters_not_applied")
                    expected[field] = value

                label = "整租" if requirements.rent_type == "entire" else "合租"
                options = fields.get("rental_type", {}).get("options", [])
                option = next((o for o in options if isinstance(o, str) and label in o), None)
                if not option:
                    raise BrainError("unsupported_filter")
                await modify("select_filter_option", {
                    "field_id": "rental_type", "option": option,
                }, "rental_type", [option])
                if requirements.location:
                    await modify("search_location", {"keyword": requirements.location},
                                 "location", requirements.location)
                if fields.get("rent", {}).get("control") != "range":
                    raise BrainError("unsupported_filter")
                await modify("set_filter_range", {
                    "field_id": "rent", "minimum": requirements.rent_min,
                    "maximum": requirements.rent_max,
                }, "rent", {"min": requirements.rent_min, "max": requirements.rent_max})
                result = await client.call("get_results", {
                    "session_id": sid, "limit": 10, "expected_filters": expected,
                })
                if result.get("filters_verified") is not True:
                    raise BrainError("filters_not_verified")
                if (not isinstance(result.get("items"), list)
                        or not isinstance(result.get("has_results"), bool)):
                    raise BrainError("tool_response_invalid")
                if not result["items"] and result["has_results"]:
                    raise BrainError("parse_error")
                for raw in result["items"][:10]:
                    if raw.get("url") and result.get("current_url"):
                        base = platform_url("ziroom", result["current_url"])
                        raw = {**raw, "url": urljoin(base, raw["url"])}
                    records.append(ziroom_listing(raw, expected))
                budget.checkpoint(records, ["details_not_completed"])
                for listing in records[:self.settings.detail_limit]:
                    try:
                        detail = await client.call("get_listing_detail", {
                            "url": listing.url, "timeout_seconds": 20,
                            "retries": 1, "include_report": False,
                        })
                        raw = detail.get("listing")
                        if not isinstance(raw, dict):
                            raise BrainError("detail_response_invalid")
                        listing.detail_status = "read"
                        listing.evidence.update({k: v for k, v in raw.items() if k in ZIROOM_FIELDS})
                        listing.city = raw.get("city")
                        listing.district = raw.get("district")
                        listing.location = raw.get("community")
                        listing.location_precision = "community"
                        listing.rent_type = {"合租": "shared", "整租": "entire"}.get(
                            raw.get("rental_mode"),
                        )
                        listing.layout = raw.get("layout")
                        payments = raw.get("payments", [])
                        # Multiple payment plans are not silently collapsed into one.
                        if len(payments) == 1:
                            listing.rent = number(payments[0].get("rent"))
                            listing.monthly_fees = number(payments[0].get("service"))
                            listing.deposit = number(payments[0].get("deposit"))
                            listing.rent_unit = "CNY/month" if listing.rent is not None else None
                        else:
                            listing.unknowns.append("payment_plan_selection_required")
                    except Exception as exc:
                        code = failure_code(exc)
                        listing.detail_status = code
                        issues.append(code)
                        if code in BLOCKED:
                            break
                    budget.checkpoint(records, issues)
                return records, issues
            finally:
                if sid:
                    try:
                        await client.call("close_search_session", {"session_id": sid}, cleanup=True)
                    except Exception:
                        issues.append("cleanup_failed")
