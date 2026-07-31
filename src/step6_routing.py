"""Step 6 — route every commute pair through OSRM and assemble `demand_data.json`.

The modelled travel times from step 5 are a calibrated *estimate* from straight-line distance.
The game wants real road values, so each unique origin-destination pair is routed once through a
local OSRM instance built from the OSM extract.

Pairs are deduplicated before routing: 46,718 pops collapse to far fewer distinct point pairs
because a pair over MAXPOPSIZE is split into several pops that share one route.

OSRM setup (see ../../docs/04-demand-data.md) is handled by `--start-osrm`, which trims the
extract to the map bbox first — routing the whole of southern Brazil would need far more RAM and
time than the metro needs.

    python3 src/step6_routing.py --start-osrm
    python3 src/step6_routing.py --no-routes    # emit with estimated times, skip OSRM
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import cwb

OSRM_PORT = int(__import__("os").environ.get("OSRM_PORT", "5001"))
OSRM_IMAGE = "ghcr.io/project-osrm/osrm-backend"
OSRM_CONTAINER = "osrm_cwb"
# Trim margin around the map bbox so routes near the edge can still use roads just outside it.
BBOX_MARGIN_DEG = 0.25
# Floor for a home and workplace that land in the same demand-point cell. The grid is 300 m, so a
# typical internal trip is a couple of hundred metres on foot.
INTRA_CELL_SECONDS = 150
INTRA_CELL_METRES = 300


def osrm_healthy() -> bool:
    """True only if OSRM is up **and** serving road data that covers this map.

    Checking that *something* answers on the port is not enough. When two map builds run on the
    same machine and the second one's container fails to bind because the first already holds the
    port, the second build's health check happily passes against the *first* build's container —
    and every route then comes back as a 0.0 km trip through the wrong city's road network, with
    all the totals still balancing. That is a real failure that happened in a parallel build.

    So: ask OSRM to snap a coordinate at the centre of this map, and require the snapped point to
    come back nearby. Foreign road data either errors or snaps to somewhere far away.
    """
    lon, lat = cwb.INITIAL_VIEW["longitude"], cwb.INITIAL_VIEW["latitude"]
    try:
        url = f"http://localhost:{OSRM_PORT}/nearest/v1/driving/{lon},{lat}"
        with urllib.request.urlopen(url, timeout=5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read())
    except Exception:
        return False
    waypoints = payload.get("waypoints") or []
    if payload.get("code") != "Ok" or not waypoints:
        return False
    snapped = waypoints[0].get("location") or []
    if len(snapped) != 2:
        return False
    # A snap more than ~20 km from the map centre means this instance is serving another region.
    drift = math.hypot((snapped[0] - lon) * 111.32 * 0.9, (snapped[1] - lat) * 110.57)
    if drift > 20:
        print(f"  ! OSRM on port {OSRM_PORT} snapped {drift:,.0f} km away — it is serving another region")
        return False
    return True


def prepare_osrm(pbf, verbose: bool = True) -> None:
    """Trim the extract to the map area and run OSRM's three preprocessing stages."""
    work = cwb.DATA / "osrm"
    work.mkdir(parents=True, exist_ok=True)
    trimmed = work / "cwb.osm.pbf"

    if not trimmed.exists():
        west, south, east, north = cwb.BBOX
        box = (
            f"{west - BBOX_MARGIN_DEG},{south - BBOX_MARGIN_DEG},"
            f"{east + BBOX_MARGIN_DEG},{north + BBOX_MARGIN_DEG}"
        )
        print(f"  trimming extract to {box}")
        subprocess.run(
            ["osmium", "extract", "--bbox", box, "--overwrite", "-o", str(trimmed), str(pbf)],
            check=True,
        )
        print(f"  trimmed: {trimmed.stat().st_size / 1e6:,.0f} MB (from {pbf.stat().st_size / 1e6:,.0f} MB)")

    if not (work / "cwb.osrm.fileIndex").exists() and not (work / "cwb.osrm.mldgr").exists():
        mount = f"{work}:/data"
        for stage in (
            ["osrm-extract", "-p", "/opt/car.lua", "/data/cwb.osm.pbf"],
            ["osrm-partition", "/data/cwb.osrm"],
            ["osrm-customize", "/data/cwb.osrm"],
        ):
            print(f"  docker {stage[0]}")
            subprocess.run(
                ["docker", "run", "--rm", "-t", "-v", mount, OSRM_IMAGE, *stage],
                check=True,
                capture_output=not verbose,
            )

    subprocess.run(["docker", "rm", "-f", OSRM_CONTAINER], capture_output=True)
    subprocess.run(
        [
            "docker", "run", "-d", "--name", OSRM_CONTAINER,
            "-p", f"{OSRM_PORT}:5000", "-v", f"{work}:/data",
            OSRM_IMAGE, "osrm-routed", "--algorithm", "mld", "--max-table-size", "2000",
            "/data/cwb.osrm",
        ],
        check=True,
        capture_output=True,
    )
    print("  waiting for OSRM", end="", flush=True)
    for _ in range(120):
        time.sleep(1)
        print(".", end="", flush=True)
        if osrm_healthy():
            print(" ready")
            return
    print()
    raise RuntimeError(f"OSRM did not come up; check `docker logs {OSRM_CONTAINER}`")


def route_one(pair, with_path: bool):
    (o_lon, o_lat), (d_lon, d_lat) = pair
    overview = "full" if with_path else "false"
    url = (
        f"http://localhost:{OSRM_PORT}/route/v1/driving/"
        f"{o_lon},{o_lat};{d_lon},{d_lat}?overview={overview}&geometries=geojson"
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.loads(response.read())
            if payload.get("code") != "Ok" or not payload.get("routes"):
                return None
            route = payload["routes"][0]
            result = {
                "seconds": int(round(route["duration"])),
                "metres": int(round(route["distance"])),
            }
            if with_path:
                result["path"] = [
                    [round(c[0], 5), round(c[1], 5)] for c in route["geometry"]["coordinates"]
                ]
            return result
        except Exception:
            if attempt == 2:
                return None
            time.sleep(0.3 * (attempt + 1))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start-osrm", action="store_true", help="prepare and launch OSRM first")
    parser.add_argument("--no-routes", action="store_true", help="use estimated times, skip OSRM")
    parser.add_argument("--driving-path", action="store_true", help="include route geometry")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()

    cwb.banner("Step 6 — routing and demand_data.json")

    points = cwb.read_json(cwb.INTERIM / "points.json")
    pairs_payload = cwb.read_json(cwb.INTERIM / "pairs.json")
    origin_ids = pairs_payload["origins"]
    dest_ids = pairs_payload["dests"]
    pairs = pairs_payload["pairs"]
    by_id = {p["id"]: p for p in points}
    print(f"  points {len(points):,}  pops {len(pairs):,}")

    unique: dict[tuple[int, int], None] = {}
    for origin, dest, _size, _mins, _km in pairs:
        unique[(origin, dest)] = None
    print(f"  distinct point pairs to route: {len(unique):,}")

    routed: dict[tuple[int, int], dict] = {}
    if not args.no_routes:
        if args.start_osrm:
            pbf = cwb.RAW / "osm" / "sul-latest.osm.pbf"
            if not pbf.exists():
                print(f"  ! missing {pbf}")
                return 1
            prepare_osrm(pbf)
        elif not osrm_healthy():
            print("  ! OSRM is not responding on port 5000; pass --start-osrm or use --no-routes")
            return 1

        keys = list(unique)
        coords = [
            (
                tuple(by_id[origin_ids[o]]["location"]),
                tuple(by_id[dest_ids[d]]["location"]),
            )
            for o, d in keys
        ]
        print(f"  routing {len(keys):,} pairs with {args.workers} workers")
        done = failed = 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for key, result in zip(keys, pool.map(lambda c: route_one(c, args.driving_path), coords)):
                done += 1
                if result is None:
                    failed += 1
                else:
                    routed[key] = result
                if done % 5000 == 0:
                    rate = done / max(time.time() - started, 1e-6)
                    print(f"    {done:,}/{len(keys):,}  {rate:,.0f}/s  failed {failed:,}")
        print(f"  routed {len(routed):,}, failed {failed:,} ({failed / max(len(keys), 1):.2%})")

    # ---- assemble ----
    pops = []
    pop_ids_by_point: dict[str, list[str]] = {p["id"]: [] for p in points}
    fallbacks = 0
    intra_cell = 0
    for index, (origin, dest, size, mins, km) in enumerate(pairs):
        residence_id = origin_ids[origin]
        job_id = dest_ids[dest]
        route = routed.get((origin, dest))
        if route is None:
            # No road route (or routing skipped): fall back to the calibrated estimate so the pop
            # still carries plausible values rather than zeros.
            seconds = int(round(mins * 60))
            metres = int(round(km * 1350))  # straight-line km -> road metres via the detour factor
            fallbacks += 1
        else:
            seconds, metres = route["seconds"], route["metres"]
        # A pop whose home and work fall in the same grid cell routes to zero distance, which is
        # physically real — 6.25% of observed car commutes take under five minutes — but a literal
        # zero makes driving instant and free, so nothing would ever switch to transit, and it
        # invites division-by-zero downstream. Floor it at a plausible intra-cell trip instead.
        if seconds <= 0 or metres <= 0:
            seconds = max(seconds, INTRA_CELL_SECONDS)
            metres = max(metres, INTRA_CELL_METRES)
            intra_cell += 1
        pop = {
            "id": f"pop_{index}",
            "size": int(size),
            "residenceId": residence_id,
            "jobId": job_id,
            "drivingSeconds": seconds,
            "drivingDistance": metres,
        }
        if args.driving_path and route and route.get("path"):
            pop["drivingPath"] = route["path"]
        pops.append(pop)
        pop_ids_by_point[residence_id].append(pop["id"])

    for point in points:
        point["popIds"] = pop_ids_by_point.get(point["id"], [])

    demand = {"points": points, "pops": pops}
    residents_total = sum(p["residents"] for p in points)
    pop_total = sum(p["size"] for p in pops)
    print()
    print(f"  pops written        : {len(pops):,}")
    print(f"  estimate fallbacks  : {fallbacks:,}")
    print(f"  same-cell pops floored: {intra_cell:,}")
    print(f"  sum(points.residents): {residents_total:,}")
    print(f"  sum(pops.size)       : {pop_total:,}")
    if residents_total != pop_total:
        print("  ! residents and pop sizes disagree — the registry would fail this")
        return 1
    phantom = sum(1 for p in points if not p["residents"] and not p["jobs"])
    print(f"  phantom points       : {phantom}")
    if phantom:
        return 1

    seconds = [p["drivingSeconds"] for p in pops]
    metres = [p["drivingDistance"] for p in pops]
    sizes = [p["size"] for p in pops]
    weight = sum(sizes)
    print()
    print(f"  size-weighted mean commute: {sum(s * z for s, z in zip(seconds, sizes)) / weight / 60:.1f} min, "
          f"{sum(m * z for m, z in zip(metres, sizes)) / weight / 1000:.2f} km")
    ordered = sorted(zip(metres, sizes))
    half = weight / 2
    running = 0
    for value, size in ordered:
        running += size
        if running >= half:
            print(f"  size-weighted median distance: {value / 1000:.2f} km")
            break

    cwb.write_json(cwb.OUT / "demand_data.json", demand)
    return 0


if __name__ == "__main__":
    sys.exit(main())
