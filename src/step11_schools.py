"""Step 11 — school demand from INEP's Censo Escolar, geocoded through CNEFE.

Commuting is a minority of urban travel — Sydney's household travel survey puts journey-to-work
at 14.8% of journeys — and a map built only from a journey-to-work matrix under-represents
exactly the destinations a player wants to serve. Education is the largest single category that
official data can supply in bulk rather than by hand-curation.

INEP's Censo Escolar gives **real per-school enrolment by level** for every school in Brazil:
2,138 active schools and 734,607 enrolments inside this extent. What it does not give is
coordinates — but it does give `CO_CEP`, and CNEFE gives coordinates for every address plus a
species code that flags education establishments. So each school is placed at a CNEFE address
sharing its postcode, preferring one already recorded as a school. Both halves are official.

Travel fractions by level, because a six-year-old does not travel independently:

| Level | Ages | Fraction | Reasoning |
| --- | --- | --- | --- |
| `INF` infantil | 0–5 | 0.20 | Accompanied; the trip is really the adult's |
| `FUND_AI` anos iniciais | 6–10 | 0.35 | Mostly car or school bus with a parent |
| `FUND_AF` anos finais | 11–14 | 0.60 | Increasingly independent |
| `MED` médio | 15–17 | 0.85 | Independent, and heavy transit users |

The 0.35 and 0.85 anchors come from the Sydney build's reading of the NSW household travel
survey; the two middle values interpolate. They are assumptions, not measurements.

    python3 src/step11_schools.py [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import zipfile
from collections import defaultdict

import cwb
import numpy as np
from step2_residents import cnefe_rows, largest_remainder

csv.field_size_limit(1 << 24)

CENSO = (
    cwb.RAW / "inep" / "microdados_censo_escolar_2024_defeso" / "dados" / "microdados_ed_basica_2024.csv"
)

# (column, travel fraction, catchment metres)
LEVELS = [
    ("QT_MAT_INF", 0.20, 2500),
    ("QT_MAT_FUND_AI", 0.35, 3500),
    ("QT_MAT_FUND_AF", 0.60, 5500),
    ("QT_MAT_MED", 0.85, 9000),
]

TARGET_POP_SIZE = 60
EARTH_KM = 6371.0088


def clean_cep(raw: str | None) -> str | None:
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    return digits.zfill(8) if 5 <= len(digits) <= 8 else None


def build_cep_index() -> tuple[dict, dict]:
    """CEP -> coordinate, from CNEFE. Two indexes: education addresses, and any address."""
    school_cep: dict[str, list[tuple[float, float]]] = defaultdict(list)
    any_cep: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for code, name in cwb.MUNICIPALITIES:
        for row in cnefe_rows(code):
            cep = clean_cep(row.get("CEP"))
            if not cep:
                continue
            try:
                lon, lat = float(row["LONGITUDE"]), float(row["LATITUDE"])
            except (TypeError, ValueError):
                continue
            if row["COD_ESPECIE"] == "4":
                school_cep[cep].append((lon, lat))
            elif len(any_cep[cep]) < 40:
                any_cep[cep].append((lon, lat))
    return school_cep, any_cep


def centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def haversine_km(lon1, lat1, lons, lats) -> np.ndarray:
    p1, p2 = math.radians(lat1), np.radians(lats)
    a = (
        np.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * np.cos(p2) * np.sin((np.radians(lons) - math.radians(lon1)) / 2) ** 2
    )
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def load_schools() -> list[dict]:
    if not CENSO.exists():
        print(f"  ! missing {CENSO}")
        return []
    muns = {code for code, _ in cwb.MUNICIPALITIES}
    out = []
    for row in csv.DictReader(CENSO.open(encoding="latin-1"), delimiter=";"):
        if row["CO_MUNICIPIO"] not in muns:
            continue
        if row.get("TP_SITUACAO_FUNCIONAMENTO") != "1":
            continue
        cep = clean_cep(row.get("CO_CEP"))
        if not cep:
            continue

        def count(key: str) -> int:
            value = (row.get(key) or "").strip()
            return int(value) if value.isdigit() else 0

        trips = 0.0
        weighted_catchment = 0.0
        enrolment = 0
        for column, fraction, catchment in LEVELS:
            students = count(column)
            if not students:
                continue
            enrolment += students
            contribution = students * fraction
            trips += contribution
            weighted_catchment += contribution * catchment
        if trips < 1:
            continue
        out.append(
            {
                "code": row["CO_ENTIDADE"],
                "name": (row.get("NO_ENTIDADE") or "").strip().title(),
                "cep": cep,
                "mun": row["CO_MUNICIPIO"],
                "enrolment": enrolment,
                "trips": int(round(trips)),
                "catchment": weighted_catchment / trips,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()

    cwb.banner("Step 11 — school demand (INEP Censo Escolar 2024)")

    schools = load_schools()
    print(f"  active schools with usable enrolment: {len(schools):,}")
    print(f"  enrolment {sum(s['enrolment'] for s in schools):,}  modelled trips {sum(s['trips'] for s in schools):,}")

    print("  building CEP -> coordinate index from CNEFE")
    school_cep, any_cep = build_cep_index()
    print(f"  CEPs with an education address: {len(school_cep):,}  with any address: {len(any_cep):,}")

    placed: dict[tuple[float, float], dict] = {}
    matched_school = matched_any = unmatched = 0
    for school in schools:
        options = school_cep.get(school["cep"])
        if options:
            matched_school += 1
        else:
            options = any_cep.get(school["cep"])
            if options:
                matched_any += 1
            else:
                unmatched += 1
                continue
        lon, lat = centroid(options)
        if not cwb.in_bbox(lon, lat):
            unmatched += 1
            continue
        key = (round(lon, 5), round(lat, 5))
        # Several schools commonly share one postcode; merging them at the shared coordinate is
        # de-duplication, not thinning, so it is safe.
        entry = placed.setdefault(
            key, {"trips": 0, "catchment": 0.0, "names": [], "enrolment": 0}
        )
        entry["trips"] += school["trips"]
        entry["catchment"] += school["catchment"] * school["trips"]
        entry["enrolment"] += school["enrolment"]
        if len(entry["names"]) < 4:
            entry["names"].append(school["name"])

    print(f"  geocoded via an education address: {matched_school:,}")
    print(f"  geocoded via any address at that CEP: {matched_any:,}")
    print(f"  unmatched or outside bbox: {unmatched:,}")
    print(f"  distinct school sites: {len(placed):,}")
    total_trips = sum(v["trips"] for v in placed.values())
    print(f"  school trips to place: {total_trips:,}")
    if not placed:
        return 1

    demand = cwb.read_json(cwb.OUT / "demand_data.json")
    points, pops = demand["points"], demand["pops"]
    if any(p["id"].startswith("SCH_") for p in points):
        print("  ! school points already present — re-run step 6 and 10 first")
        return 1
    calibration = cwb.read_json(cwb.INTERIM / "pairs.json")["calibration"]
    alpha, beta, floor = calibration["alpha"], calibration["beta"], calibration["floor_min"]
    terminal, v_max = calibration["terminal_min"], calibration["speed"][1]
    v_min, d_half = calibration["speed"][0], 6.0

    origins = [p for p in points if p["residents"] > 0 and p["id"].startswith("cwb_")]
    o_lon = np.array([p["location"][0] for p in origins])
    o_lat = np.array([p["location"][1] for p in origins])
    o_res = np.array([p["residents"] for p in origins], dtype=np.float64)
    print(f"  candidate origins: {len(origins):,}")
    sampler = np.random.default_rng(20260801)

    def minutes(km):
        road = km * 1.35
        speed = v_min + (v_max - v_min) * road / (road + d_half)
        return terminal + road / speed * 60.0

    added_points, added_pops = [], []
    skipped = 0
    for index, ((lon, lat), entry) in enumerate(sorted(placed.items())):
        trips = entry["trips"]
        catchment = entry["catchment"] / max(trips, 1)
        km = haversine_km(lon, lat, o_lon, o_lat)
        within = np.where(km * 1000 <= catchment)[0]
        if len(within) == 0:
            # Widen once before giving up — a rural school can sit beyond its nominal catchment
            # from any modelled residence.
            within = np.where(km * 1000 <= catchment * 3)[0]
        if len(within) == 0:
            skipped += 1
            continue
        mins = minutes(km[within])
        effective = np.maximum(mins, floor)
        weight = o_res[within] * np.power(effective, -alpha) * np.exp(-beta * effective)
        if weight.sum() <= 0:
            skipped += 1
            continue
        n_origins = int(min(len(within), max(6, round(trips / TARGET_POP_SIZE))))
        keys = np.log(np.maximum(weight, 1e-300)) + sampler.gumbel(size=len(weight))
        chosen = (
            np.argpartition(-keys, n_origins - 1)[:n_origins]
            if n_origins < len(weight)
            else np.arange(len(weight))
        )
        sel = within[chosen]
        shares = largest_remainder(trips, [max(1, int(w * 1e6)) for w in weight[chosen]])

        code = f"SCH_{index}"
        point = {
            "id": code,
            "location": [round(lon, 6), round(lat, 6)],
            "jobs": trips,
            "residents": 0,
            "popIds": [],
        }
        for local, size in zip(sel, shares):
            if size <= 0:
                continue
            origin = origins[int(local)]
            distance_km = float(km[int(local)])
            seconds = int(round(float(minutes(np.array([distance_km]))[0]) * 60))
            while size > 0:
                chunk = min(size, 200)
                pop_id = f"sc_{code}_{len(added_pops)}"
                added_pops.append(
                    {
                        "id": pop_id,
                        "size": int(chunk),
                        "residenceId": origin["id"],
                        "jobId": code,
                        "drivingSeconds": max(seconds, 150),
                        "drivingDistance": max(int(round(distance_km * 1350)), 300),
                    }
                )
                origin["popIds"].append(pop_id)
                origin["residents"] += int(chunk)
                size -= chunk
        added_points.append(point)

    print(f"  school points added: {len(added_points):,}  skipped: {skipped:,}")
    print(f"  school pops added  : {len(added_pops):,}")

    points.extend(added_points)
    pops.extend(added_pops)
    residents = sum(p["residents"] for p in points)
    pop_total = sum(p["size"] for p in pops)
    print()
    print(f"  points {len(points):,}  pops {len(pops):,}")
    print(f"  residents {residents:,}  sum(pops.size) {pop_total:,}")
    if residents != pop_total:
        print("  ! residents and pop sizes disagree — aborting")
        return 1
    if any(not p["residents"] and not p["jobs"] for p in points):
        print("  ! phantom point created — aborting")
        return 1
    print("  invariants hold")

    if args.dry_run:
        print("  dry run — not written")
        return 0

    from step6_routing import osrm_healthy, route_one

    if osrm_healthy():
        from concurrent.futures import ThreadPoolExecutor

        by_id = {p["id"]: p for p in points}
        pairs = {(q["residenceId"], q["jobId"]): None for q in added_pops}
        keys = list(pairs)
        coords = [(tuple(by_id[a]["location"]), tuple(by_id[b]["location"])) for a, b in keys]
        print(f"  routing {len(keys):,} school pairs through OSRM")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for key, result in zip(keys, pool.map(lambda c: route_one(c, False), coords)):
                if result:
                    pairs[key] = result
        applied = 0
        for pop in added_pops:
            route = pairs.get((pop["residenceId"], pop["jobId"]))
            if route and route["seconds"] > 0 and route["metres"] > 0:
                pop["drivingSeconds"] = route["seconds"]
                pop["drivingDistance"] = route["metres"]
                applied += 1
        print(f"  routed {applied:,} of {len(added_pops):,} school pops")
    else:
        print("  ! OSRM not responding — school pops keep estimated times")

    cwb.write_json(cwb.OUT / "demand_data.json", demand)
    cwb.write_json(
        cwb.INTERIM / "schools.json",
        [
            {"id": f"SCH_{i}", "names": v["names"], "enrolment": v["enrolment"], "trips": v["trips"]}
            for i, ((_lon, _lat), v) in enumerate(sorted(placed.items()))
        ],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
