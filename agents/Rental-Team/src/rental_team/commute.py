import re

import httpx
from javis_core.models import BrainError, now


def coordinates(value):
    if not re.fullmatch(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?", value or ""):
        raise BrainError("location_confirmation_required")
    lng, lat = map(float, value.split(","))
    if not -180 <= lng <= 180 or not -90 <= lat <= 90:
        raise BrainError("invalid_coordinates")
    return value


class Amap:
    def __init__(self, settings):
        self.key = settings.amap_api_key.get_secret_value()
        self.timeout = settings.request_timeout

    async def lookup(self, address, city, *, authorized=False):
        if not authorized:
            return {"status": "not_queried", "pending_action": "authorize_location_sharing"}
        if not self.key:
            return {"status": "not_queried", "pending_action": "configure_amap_key"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.get(
                    "https://restapi.amap.com/v3/geocode/geo",
                    params={"key": self.key, "address": address, "city": city},
                )
                response.raise_for_status()
                data = response.json()
            if data.get("status") != "1":
                raise BrainError("geocode_provider_error")
            return {
                "status": "awaiting_confirmation", "coordinate_system": "GCJ-02",
                "queried_at": now(), "candidates": [{
                    "formatted_address": str(row.get("formatted_address", ""))[:300],
                    "district": row.get("district"), "gcj02": coordinates(row.get("location")),
                    "level": row.get("level"),
                } for row in data.get("geocodes", [])[:5]],
                "pending_action": "用户选择并确认坐标；不会自动选第一个地点",
            }
        except Exception:
            return {"status": "failed", "error": "geocode_query_failed", "queried_at": now()}

    async def route(self, listing, requirements, *, authorized=False):
        if not authorized:
            return {"status": "not_queried", "pending_action": "authorize_location_sharing"}
        if not self.key:
            return {"status": "not_queried", "pending_action": "configure_amap_key"}
        if not listing.gcj02 or not requirements.destination_gcj02:
            return {"status": "not_queried", "pending_action": "confirm_gcj02_locations"}
        origin = coordinates(listing.gcj02)
        destination = coordinates(requirements.destination_gcj02)
        mode = requirements.commute_mode
        endpoint = {
            "transit": "transit/integrated", "driving": "driving",
            "walking": "walking", "cycling": None,
        }[mode]
        if endpoint is None:
            return {"status": "not_queried", "pending_action": "cycling_not_supported"}
        params = {"key": self.key, "origin": origin, "destination": destination}
        if mode == "transit":
            params.update(city=requirements.city, cityd=requirements.city)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.get(
                    "https://restapi.amap.com/v3/direction/" + endpoint, params=params,
                )
                response.raise_for_status()
                data = response.json()
            if data.get("status") != "1":
                raise BrainError("route_provider_error")
            route = data.get("route", {})
            options = route.get("transits" if mode == "transit" else "paths", [])
            if not options:
                return {"status": "no_route", "queried_at": now(), "mode": mode}
            first = options[0]
            seconds = int(first["duration"])
            if seconds < 0:
                raise BrainError("invalid_route")
            segments = first.get("segments", [])
            legs = []
            for segment in segments:
                lines = segment.get("bus", {}).get("buslines", [])
                if lines:
                    legs.append(str(lines[0].get("name", ""))[:100])
                railway = segment.get("railway", {})
                if railway.get("id"):
                    legs.append(str(railway.get("name", ""))[:100])
            return {
                "status": "queried", "provider": "amap", "queried_at": now(),
                "mode": mode, "coordinate_system": "GCJ-02", "seconds": seconds,
                "minutes": (seconds + 59) // 60, "distance_m": first.get("distance"),
                "transfers": max(0, len(legs) - 1) if mode == "transit" and legs
                else None if mode == "transit" else 0,
                "transit_legs": legs,
                "uncertainty": "路线服务估算；不承诺实际高峰耗时",
            }
        except Exception:
            return {"status": "failed", "error": "route_query_failed", "queried_at": now()}
