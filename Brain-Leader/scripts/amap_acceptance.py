"""Opt-in real map acceptance, using public landmarks only; no housing requests."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from javis_core.models import now
from rental_team.budget import DailyQuota
from rental_team.commute import Amap
from rental_team.config import Settings


async def verify(settings):
    quota = DailyQuota(settings.quota_path)
    before = quota.used()
    maps = Amap(settings.provider_config("amap"))
    lookup = await maps.lookup("北京站", "北京", authorized=True)
    # Fixed public-landmark GCJ-02 fixtures, not a rental listing or a user location.
    origin, destination = "116.397428,39.909230", "116.427287,39.904983"
    route = await maps.route(
        SimpleNamespace(gcj02=origin),
        SimpleNamespace(
            destination_gcj02=destination, city="北京", commute_mode="transit",
        ),
        authorized=True,
    )
    after = quota.used()
    checks = {
        "real_geocode_candidates": (
            lookup.get("status") == "awaiting_confirmation"
            and bool(lookup.get("candidates"))
            and any("北京站" in row["formatted_address"] for row in lookup["candidates"])
        ),
        "manual_choice_still_required": lookup.get("status") == "awaiting_confirmation",
        "real_transit_route": route.get("status") == "queried" and route.get("seconds", 0) > 0,
        "vigo_quota_unchanged": before == after,
    }
    return {
        "queried_at": now(), "passed": all(checks.values()), "checks": checks,
        "scope": "Public landmark map adapter only; not housing or personal commute acceptance",
        "fixtures": {
            "origin": "天安门公共地标", "origin_gcj02": origin,
            "destination": "北京站公共地标", "destination_gcj02": destination,
        },
        "maximum_map_requests": 2, "housing_requests": 0,
        "vigo_quota_before": before, "vigo_quota_after": after,
        "geocode": lookup, "route": route,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--live-map", action="store_true", required=True)
    args = parser.parse_args()
    settings = Settings()
    if not settings.amap_api_key.get_secret_value():
        parser.error("Configure the project Amap key first")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = asyncio.run(verify(settings))
    descriptor = os.open(args.output / "report.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
