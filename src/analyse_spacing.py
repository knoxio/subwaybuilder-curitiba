"""Measure the spacing statistics the registry publishes, so a grid size can be chosen on the
metric that is actually scored rather than on file size.

The registry computes `residentWeightedNearestNeighborKm` and `workerWeightedNearestNeighborKm`
percentiles per map. Weighted means each point contributes once per resident (or worker), so a
dense downtown point counts far more than a lone rural one — the statistic describes the spacing
an average *person* experiences, not an average point.

Reference values read off shipped registry manifests: Amsterdam p50 0.69 km with 373 points;
the existing Sydney map p50 0.295 km with 9,687 points and a detail score of 0.724.

    python3 src/analyse_spacing.py --grid 200 300 400 600
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
from bisect import insort

import cwb


def nearest_neighbour_km(points: list[dict], key: str) -> list[tuple[float, int]]:
    """(distance to nearest other point, weight) for every point carrying weight `key`."""
    if not points:
        return []
    mid_lat = sum(p["location"][1] for p in points) / len(points)
    scale_x = 111.320 * math.cos(math.radians(mid_lat))
    scale_y = 110.574

    # bucket into ~1 km cells so each lookup only scans neighbouring cells
    cell = 1.0
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    xy = []
    for point in points:
        lon, lat = point["location"]
        x, y = lon * scale_x, lat * scale_y
        xy.append((x, y))
        buckets.setdefault((int(x // cell), int(y // cell)), []).append((x, y))

    out = []
    for point, (x, y) in zip(points, xy):
        weight = point.get(key) or 0
        if weight <= 0:
            continue
        best = float("inf")
        radius = 1
        while True:
            cx, cy = int(x // cell), int(y // cell)
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    for ox, oy in buckets.get((cx + dx, cy + dy), ()):
                        if ox == x and oy == y:
                            continue
                        d = math.hypot(ox - x, oy - y)
                        if d < best:
                            best = d
            if best < radius * cell or radius > 6:
                break
            radius += 1
        if best < float("inf"):
            out.append((best, weight))
    return out


def weighted_percentiles(samples: list[tuple[float, int]], probs=(10, 25, 50, 75, 90)) -> dict:
    if not samples:
        return {}
    ordered = sorted(samples)
    total = sum(w for _, w in ordered)
    result = {}
    target = {p: total * p / 100 for p in probs}
    cumulative = 0
    remaining = sorted(probs)
    for distance, weight in ordered:
        cumulative += weight
        while remaining and cumulative >= target[remaining[0]]:
            result[f"p{remaining[0]}"] = distance
            remaining.pop(0)
        if not remaining:
            break
    for p in remaining:
        result[f"p{p}"] = ordered[-1][0]
    result["mean"] = sum(d * w for d, w in ordered) / total
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grid", type=int, nargs="+", default=[200, 300, 400, 600])
    parser.add_argument("--no-rebuild", action="store_true", help="analyse the current points.json only")
    args = parser.parse_args()

    cwb.banner("Spacing analysis")
    print("  reference: Amsterdam p50 0.690 km / 373 pts;  Sydney p50 0.295 km / 9,687 pts (detail 0.724)")
    print()
    header = f"  {'grid':>6}{'points':>9}{'res p10':>9}{'p25':>8}{'p50':>8}{'p75':>8}{'p90':>8}{'job p50':>9}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for grid in args.grid:
        if not args.no_rebuild:
            subprocess.run(
                [sys.executable, str(cwb.ROOT / "src" / "step4_points.py"), "--grid", str(grid)],
                check=True,
                capture_output=True,
            )
        points = cwb.read_json(cwb.INTERIM / "points.json")
        res = weighted_percentiles(nearest_neighbour_km(points, "residents"))
        job = weighted_percentiles(nearest_neighbour_km(points, "jobs"))
        print(
            f"  {grid:>5}m{len(points):>9,}"
            f"{res.get('p10', 0):>9.3f}{res.get('p25', 0):>8.3f}{res.get('p50', 0):>8.3f}"
            f"{res.get('p75', 0):>8.3f}{res.get('p90', 0):>8.3f}{job.get('p50', 0):>9.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
