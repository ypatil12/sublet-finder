#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

NTA_CSV_URL = "https://data.cityofnewyork.us/api/views/9nt8-h7nd/rows.csv?accessType=DOWNLOAD"
DEFAULT_NTA_PATH = Path("data/nyc_nta_2020.csv")
WORK_COORDS = (40.7295, -73.9904)


@dataclass(frozen=True)
class CommuteRow:
    neighborhood: str
    borough: str
    commute_minutes: int
    source_type: str
    source_query: str
    source_url: str
    is_fallback: bool


@dataclass(frozen=True)
class CommuteSeed:
    canonical: str
    commute_minutes: int


COMMUTE_SEEDS = [
    CommuteSeed("NoHo", 6),
    CommuteSeed("SoHo", 12),
    CommuteSeed("NoLIta", 11),
    CommuteSeed("East Village", 10),
    CommuteSeed("West Village", 18),
    CommuteSeed("Lower East Side", 15),
    CommuteSeed("Gramercy", 12),
    CommuteSeed("Flatiron", 14),
    CommuteSeed("Kips Bay", 17),
    CommuteSeed("Murray Hill", 18),
    CommuteSeed("Chelsea", 18),
    CommuteSeed("Midtown East", 24),
    CommuteSeed("Midtown West", 26),
    CommuteSeed("Financial District", 24),
    CommuteSeed("Williamsburg", 22),
    CommuteSeed("East Williamsburg", 26),
    CommuteSeed("Greenpoint", 28),
    CommuteSeed("Bushwick", 32),
    CommuteSeed("Bed-Stuy", 29),
    CommuteSeed("Clinton Hill", 28),
    CommuteSeed("Fort Greene", 26),
    CommuteSeed("Prospect Heights", 32),
    CommuteSeed("Park Slope", 34),
    CommuteSeed("Crown Heights", 36),
    CommuteSeed("DUMBO", 24),
    CommuteSeed("Long Island City", 26),
    CommuteSeed("Astoria", 34),
    CommuteSeed("Upper East Side", 26),
    CommuteSeed("Upper West Side", 30),
    CommuteSeed("Harlem", 35),
    CommuteSeed("Washington Heights", 45),
    CommuteSeed("Downtown Brooklyn", 26),
    CommuteSeed("Jersey City", 42),
    CommuteSeed("Hoboken", 38),
    CommuteSeed("New Rochelle", 60),
]


def ensure_nta_csv(path: Path = DEFAULT_NTA_PATH) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(NTA_CSV_URL, timeout=60) as response:
        payload = response.read()
    path.write_bytes(payload)
    return path


def centroid_from_wkt(wkt: str) -> tuple[float, float] | None:
    points = re.findall(r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)", wkt or "")
    if not points:
        return None
    lons = [float(p[0]) for p in points]
    lats = [float(p[1]) for p in points]
    return sum(lats) / len(lats), sum(lons) / len(lons)


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
    # Simple NYC transit proxy calibrated to downtown Manhattan commute patterns.
    km = haversine_km(lat, lon, WORK_COORDS[0], WORK_COORDS[1])
    return max(5, min(120, int(round(7 + (km * 4.3)))))


def build_neighborhood_commutes(path: Path = DEFAULT_NTA_PATH) -> list[CommuteRow]:
    csv_path = ensure_nta_csv(path)
    csv.field_size_limit(10_000_000)

    # Seed values from existing, manually tuned listing neighborhoods.
    seed_minutes = {hood.canonical.lower(): hood.commute_minutes for hood in COMMUTE_SEEDS}

    rows: dict[str, CommuteRow] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for record in reader:
            name = (record.get("NTAName") or "").strip()
            borough = (record.get("BoroName") or "").strip()
            geom = record.get("the_geom") or ""
            if not name:
                continue

            key = name.lower()
            if key in seed_minutes:
                commute = seed_minutes[key]
                source_type = "seed_existing"
                is_fallback = False
            else:
                center = centroid_from_wkt(geom)
                if center is None:
                    continue
                commute = modeled_transit_minutes(center[0], center[1])
                source_type = "fallback_model"
                is_fallback = True

            rows[key] = CommuteRow(
                neighborhood=name,
                borough=borough,
                commute_minutes=commute,
                source_type=source_type,
                source_query=f"{name}, NYC to 36 Cooper Square transit time",
                source_url=NTA_CSV_URL if source_type == "fallback_model" else "",
                is_fallback=is_fallback,
            )

    # Ensure common listing aliases remain directly joinable from post classifications.
    for hood in COMMUTE_SEEDS:
        key = hood.canonical.lower()
        rows[key] = CommuteRow(
            neighborhood=hood.canonical,
            borough="",
            commute_minutes=hood.commute_minutes,
            source_type="seed_existing",
            source_query=f"{hood.canonical}, NYC to 36 Cooper Square transit time",
            source_url="",
            is_fallback=False,
        )

    return sorted(rows.values(), key=lambda row: row.neighborhood.lower())
