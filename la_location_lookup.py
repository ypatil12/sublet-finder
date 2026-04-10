#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LA_LOOKUP_PATH = Path("data/la_location_lookup.csv")
TARGET_ADDRESS = "3213 S La Cienega Blvd, Los Angeles, CA 90016"
# Approximate coordinate used for modeled travel ranking around the target address.
TARGET_COORDS = (34.0254, -118.3766)

ZIP_RE = re.compile(r"\b9\d{4}\b")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

LA_LOCATION_ALIASES = {
    "dtla": "Downtown Los Angeles",
    "downtown la": "Downtown Los Angeles",
    "downtown los angeles": "Downtown Los Angeles",
    "k town": "Koreatown",
    "k-town": "Koreatown",
    "ktown": "Koreatown",
    "koreatown": "Koreatown",
    "weho": "West Hollywood",
    "west hollywood": "West Hollywood",
    "usc": "USC/University Park",
    "usc area": "USC/University Park",
    "university park": "USC/University Park",
    "university park usc": "USC/University Park",
    "mid city": "Mid-City",
    "mid-city": "Mid-City",
    "mid wilshire": "Mid-Wilshire",
    "mid-wilshire": "Mid-Wilshire",
    "miracle mile": "Miracle Mile",
    "fairfax district": "Fairfax",
    "fairfax": "Fairfax",
    "sawtelle": "Sawtelle",
    "culver": "Culver City",
    "culver city": "Culver City",
    "downtown culver city": "Culver City",
    "palms": "Palms",
    "mar vista": "Mar Vista",
    "west adams": "West Adams",
    "jefferson park": "Jefferson Park",
    "silverlake": "Silver Lake",
    "silver lake": "Silver Lake",
    "echo park": "Echo Park",
    "los feliz": "Los Feliz",
    "hollywood": "Hollywood",
    "east hollywood": "East Hollywood",
    "westwood": "Westwood",
    "santa monica": "Santa Monica",
    "venice": "Venice",
    "playa vista": "Playa Vista",
    "beverly hills": "Beverly Hills",
    "century city": "Century City",
    "sherman oaks": "Sherman Oaks",
    "studio city": "Studio City",
    "north hollywood": "North Hollywood",
    "glendale": "Glendale",
    "burbank": "Burbank",
    "pasadena": "Pasadena",
    "inglewood": "Inglewood",
}


@dataclass(frozen=True)
class LocationCommuteRow:
    location_key: str
    canonical_location: str
    location_type: str
    commute_minutes: int
    source_type: str
    source_query: str
    source_url: str
    is_fallback: bool


def normalize_la_location_token(value: str | None) -> str | None:
    if value is None:
        return None

    compact = " ".join(value.strip().split())
    if not compact:
        return None

    zip_match = ZIP_RE.fullmatch(compact)
    if zip_match:
        return zip_match.group(0)

    lowered = compact.lower()
    if lowered in LA_LOCATION_ALIASES:
        return LA_LOCATION_ALIASES[lowered]

    simplified = NON_ALNUM_RE.sub(" ", lowered).strip()
    if simplified in LA_LOCATION_ALIASES:
        return LA_LOCATION_ALIASES[simplified]

    return compact


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius * c


def modeled_transit_minutes(lat: float, lon: float) -> int:
    km = haversine_km(lat, lon, TARGET_COORDS[0], TARGET_COORDS[1])
    return max(12, min(150, int(round(10 + (km * 5.2)))))


def build_la_location_commutes(path: Path = DEFAULT_LA_LOOKUP_PATH) -> list[LocationCommuteRow]:
    rows: dict[str, LocationCommuteRow] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for record in reader:
            raw_key = (record.get("location_key") or "").strip()
            normalized_key = normalize_la_location_token(raw_key)
            canonical = (record.get("canonical_location") or "").strip() or normalized_key
            location_type = (record.get("location_type") or "").strip() or "neighborhood"
            lat = float(record["latitude"])
            lon = float(record["longitude"])
            if not normalized_key:
                continue

            rows[normalized_key.lower()] = LocationCommuteRow(
                location_key=normalized_key,
                canonical_location=canonical,
                location_type=location_type,
                commute_minutes=modeled_transit_minutes(lat, lon),
                source_type="fallback_model",
                source_query=f"{canonical} to {TARGET_ADDRESS} transit time",
                source_url="local:data/la_location_lookup.csv",
                is_fallback=True,
            )

    return sorted(rows.values(), key=lambda row: row.location_key.lower())
