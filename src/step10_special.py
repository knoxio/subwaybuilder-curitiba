"""Step 10 — add special demand: airports, universities, stadiums, hospitals, shopping, parks.

Residence-and-workplace statistics do not capture places whose demand is not employment. A
stadium's 42,000 attendees are not jobs; nor are a university's students or an airport's
passengers. Those trips are added here, drawn from the surrounding residential points using the
same deterrence function step 5 fitted, so a special place attracts from a realistic catchment
rather than uniformly.

**BRT terminals are deliberately excluded** — see `special_places.py` for the reasoning. In
short: a terminal is a place people pass through, not a trip end, and the map is greenfield.

Balancing: each added trip needs an origin, so the allocated size is added to the origin point's
`residents` as well as appearing in a new pop. That keeps `sum(points[].residents) ==
sum(pops[].size)`, which the registry enforces. It does mean `residents` totals more than the
commuting subset — correct, because a student or a match-goer is not a census commuter.

    python3 src/step10_special.py [--dry-run]
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict

import cwb
import numpy as np
from special_places import ALL_PLACES
from step2_residents import largest_remainder
from step6_routing import osrm_healthy, route_one

EARTH_KM = 6371.0088
# People per pop to aim for when spreading a special place's demand over its catchment.
TARGET_POP_SIZE = 60


def haversine_km(lon1, lat1, lons, lats) -> np.ndarray:
    p1, p2 = math.radians(lat1), np.radians(lats)
    dlat = p2 - p1
    dlon = np.radians(lons) - math.radians(lon1)
    a = np.sin(dlat / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-routes", action="store_true", help="keep estimated times")
    parser.add_argument("--workers", type=int, default=24)
    args = parser.parse_args()

    cwb.banner(f"Step 10 — special demand ({len(ALL_PLACES)} places)")

    demand = cwb.read_json(cwb.OUT / "demand_data.json")
    points = demand["points"]
    pops = demand["pops"]
    calibration = cwb.read_json(cwb.INTERIM / "pairs.json")["calibration"]
    alpha = calibration["alpha"]
    beta = calibration["beta"]
    floor = calibration["floor_min"]
    terminal = calibration["terminal_min"]
    v_max = calibration["speed"][1]
    v_min, d_half = calibration["speed"][0], 6.0
    print(f"  reusing fitted deterrence: alpha={alpha} beta={beta} floor={floor} min")

    residents_before = sum(p["residents"] for p in points)
    pops_before = len(pops)

    # Candidate origins: existing points that already house someone.
    origins = [p for p in points if p["residents"] > 0]
    o_lon = np.array([p["location"][0] for p in origins])
    o_lat = np.array([p["location"][1] for p in origins])
    o_res = np.array([p["residents"] for p in origins], dtype=np.float64)
    index_of = {id(p): i for i, p in enumerate(origins)}
    print(f"  candidate origins: {len(origins):,}")
    sampler = np.random.default_rng(20260801)

    def minutes(km: np.ndarray) -> np.ndarray:
        road = km * 1.35
        speed = v_min + (v_max - v_min) * road / (road + d_half)
        return terminal + road / speed * 60.0

    added_points = []
    added_pops = []
    by_type: dict[str, int] = defaultdict(int)
    skipped = []

    for code, kind, name, lon, lat, daily, basis, max_dist, split in ALL_PLACES:
        if not cwb.in_bbox(lon, lat):
            skipped.append((code, "outside bbox"))
            continue
        on_site = int(round(daily * split))
        visiting = daily - on_site

        km = haversine_km(lon, lat, o_lon, o_lat)
        within = np.where(km * 1000 <= max_dist)[0]
        if len(within) == 0:
            skipped.append((code, "no origin in catchment"))
            continue

        mins = minutes(km[within])
        weight = o_res[within] * np.power(np.maximum(mins, floor), -alpha) * np.exp(
            -beta * np.maximum(mins, floor)
        )
        if weight.sum() <= 0:
            skipped.append((code, "zero catchment weight"))
            continue

        # Allocating across every origin in the catchment produces tens of thousands of pops of
        # two or three people each — for 57 places that tripled the whole map's pop count while
        # adding a quarter of its people. Sample a bounded number of origins instead, weighted by
        # the same catchment weight, so pop sizes stay around TARGET_POP_SIZE. Sampling keeps the
        # spatial distribution unbiased in expectation; truncating to the nearest origins would
        # not.
        n_origins = int(min(len(within), max(12, round(visiting / TARGET_POP_SIZE))))
        keys = np.log(np.maximum(weight, 1e-300)) + sampler.gumbel(size=len(weight))
        chosen = np.argpartition(-keys, n_origins - 1)[:n_origins] if n_origins < len(weight) else np.arange(len(weight))
        within = within[chosen]
        weight = weight[chosen]
        km_sel = km[within]
        shares = largest_remainder(visiting, [max(1, int(w * 1e6)) for w in weight])
        point = {
            "id": code,
            "location": [round(lon, 6), round(lat, 6)],
            "jobs": visiting,
            "residents": on_site,
            "popIds": [],
        }
        placed = 0
        for local, size in zip(within, shares):
            if size <= 0:
                continue
            origin = origins[int(local)]
            while size > 0:
                chunk = min(size, 200)
                pop_id = f"sp_{code}_{len(added_pops)}"
                road_km = float(km[int(local)]) * 1.35
                added_pops.append(
                    {
                        "id": pop_id,
                        "size": int(chunk),
                        "residenceId": origin["id"],
                        "jobId": code,
                        "drivingSeconds": int(round(float(minutes(km[int(local)] * np.ones(1))[0]) * 60)),
                        "drivingDistance": int(round(road_km * 1000)),
                    }
                )
                origin["popIds"].append(pop_id)
                origin["residents"] += int(chunk)
                placed += int(chunk)
                size -= chunk

        # On-site residents (dorms, barracks) travel to the place itself, so the point is both
        # their residence and their destination.
        if on_site > 0:
            remaining = on_site
            while remaining > 0:
                chunk = min(remaining, 200)
                pop_id = f"sp_{code}_on{len(added_pops)}"
                added_pops.append(
                    {
                        "id": pop_id,
                        "size": int(chunk),
                        "residenceId": code,
                        "jobId": code,
                        "drivingSeconds": 120,
                        "drivingDistance": 400,
                    }
                )
                point["popIds"].append(pop_id)
                remaining -= chunk
            point["jobs"] += on_site

        added_points.append(point)
        by_type[kind] += 1
        print(f"  {code:<18} {kind:<20} daily {daily:>6,}  placed {placed:>6,}  origins {len(within):>5,}  [{basis}]")

    print()
    print(f"  places added: {len(added_points)}  by type: {dict(by_type)}")
    if skipped:
        print(f"  skipped: {skipped}")

    points.extend(added_points)
    pops.extend(added_pops)

    residents_after = sum(p["residents"] for p in points)
    pop_total = sum(p["size"] for p in pops)
    print()
    print(f"  points {len(points):,} (was {len(points) - len(added_points):,})")
    print(f"  pops   {len(pops):,} (was {pops_before:,})")
    print(f"  residents {residents_after:,} (was {residents_before:,}, +{residents_after - residents_before:,})")
    print(f"  sum(pops.size) {pop_total:,}")
    if residents_after != pop_total:
        print("  ! residents and pop sizes disagree — aborting")
        return 1
    phantom = sum(1 for p in points if not p["residents"] and not p["jobs"])
    if phantom:
        print(f"  ! {phantom} phantom points — aborting")
        return 1
    print("  invariants hold")

    if args.dry_run:
        print("  dry run — not written")
        return 0

    # Replace the estimated times on the new pops with real road routes, as step 6 did for the
    # commute pops. Routes are deduplicated per (origin, place) pair.
    if not args.no_routes and osrm_healthy():
        from concurrent.futures import ThreadPoolExecutor

        by_id = {p["id"]: p for p in points}
        pairs = {}
        for pop in added_pops:
            pairs.setdefault((pop["residenceId"], pop["jobId"]), None)
        keys = [k for k in pairs if k[0] != k[1]]
        coords = [
            (tuple(by_id[a]["location"]), tuple(by_id[b]["location"])) for a, b in keys
        ]
        print(f"  routing {len(keys):,} special pairs through OSRM")
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for key, result in zip(keys, pool.map(lambda c: route_one(c, False), coords)):
                if result:
                    pairs[key] = result
        applied = 0
        for pop in added_pops:
            route = pairs.get((pop["residenceId"], pop["jobId"]))
            if route:
                pop["drivingSeconds"] = route["seconds"]
                pop["drivingDistance"] = route["metres"]
                applied += 1
        print(f"  routed {applied:,} of {len(added_pops):,} special pops")
    elif not args.no_routes:
        print("  ! OSRM not responding — special pops keep their estimated times")

    cwb.write_json(cwb.OUT / "demand_data.json", demand)

    # Companion content file for the Special Demand mod, per
    # foundry/schemas/special_demand_points.schema.json.
    content = {
        "$schema": "special_demand_points.schema.json",
        "version": 1,
        "map_code": cwb.CODE,
        "points": [
            {
                "point_id": code,
                "type": kind,
                "name": {"__default__": name, "pt": name},
                "pop_ids": [p["id"] for p in added_pops if p["jobId"] == code],
                "metadata": {"daily_modelled": daily, "capacity_basis": basis},
            }
            for code, kind, name, lon, lat, daily, basis, _md, _rs in ALL_PLACES
            if any(ap["id"] == code for ap in added_points)
        ],
    }
    cwb.write_json(cwb.OUT / "special_demand_points.json", content, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
