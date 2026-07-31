"""Step 4 — turn address-level residents and jobs into game demand points.

Three things happen here, and the order matters.

**Commuting subset.** `points[].residents` must sum to exactly `sum(pops[].size)`, so it cannot
be total population — it has to be the people who actually make a daily commute. Censo 2022
table 10330 counts, per municipality, residents who work away from home and return at least
three days a week. That rate (41% across this extent) is applied to the census residents.

The rate is computed against each municipality's *whole* population and then applied to the part
inside the extent, rather than using the commuter count directly. Several municipalities are cut
by the bbox: Mandirituba contributes 637 residents to the map but its municipal commuter total is
8,633, and using that directly would put thirteen times more commuters in a place than it has
people.

**Balancing.** A doubly-constrained gravity model needs origin and destination totals to match.
CEMPRE reports 1.31 jobs per census commuter across this extent — CEMPRE counts formal posts at
local units, including people registered at a head-office address who work elsewhere, while the
census counts bodies physically travelling. The census measures the thing the game simulates, so
jobs are scaled down to the commuter total with their spatial distribution preserved.

**Aggregation.** `aggregate_to_grid`, not `merge_within`: see BUILD-PLAN.md. Grid snapping cannot
chain, so it is safe on data this dense.

    python3 src/step4_points.py [--grid 200]
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict

import cwb
try:
    from sources.demand import aggregate_to_grid, summarise
except ImportError as error:  # pragma: no cover - explained in tools/README.md
    raise ImportError(
        "sources/demand.py is not bundled with this repository (aggregate_to_grid/summarise) "
        "— see tools/README.md"
    ) from error
from sources.formats import gpkg_layer
from step2_residents import largest_remainder

csv.field_size_limit(1 << 24)


def municipality_totals() -> dict[str, int]:
    """Whole-municipality resident totals, needed to derive an unbiased commuting rate."""
    totals: dict[str, int] = defaultdict(int)
    for attrs, _ in gpkg_layer(
        cwb.census_gpkg(), columns=["CD_MUN", "v0001"], where="NM_CONCURB LIKE '%Curitiba%'"
    ):
        totals[str(attrs["CD_MUN"])] += int(attrs["v0001"] or 0)
    return dict(totals)


def load_points(path, value_column: str) -> list[tuple[float, float, int, str]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            value = int(row[value_column])
            if value:
                rows.append((float(row["lon"]), float(row["lat"]), value, row["setor"]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grid", type=int, default=200, help="grid size in metres")
    args = parser.parse_args()

    cwb.banner(f"Step 4 — demand points (grid {args.grid} m)")

    tracts = cwb.read_json(cwb.INTERIM / "tracts.json")
    commute = cwb.read_json(cwb.INTERIM / "commute.json")
    mun_full = municipality_totals()

    # ---- commuting rate per municipality ----
    rates: dict[str, float] = {}
    print(f"  {'municipality':<24}{'rate':>8}{'basis':>28}")
    for code, name in cwb.MUNICIPALITIES:
        commuters = (commute.get(code, {}).get("time") or {}).get("Total") or 0
        full = mun_full.get(code, 0)
        rate = commuters / full if full else 0.0
        rates[code] = rate
        print(f"  {name:<24}{rate:>7.1%}   {commuters:>9,} / {full:>9,}")

    # ---- residents -> commuters ----
    residents = load_points(cwb.INTERIM / "residents.csv", "residents")
    census_total = sum(r[2] for r in residents)
    print()
    print(f"  address records      : {len(residents):,}")
    print(f"  census residents     : {census_total:,}")

    excluded = {k for k, v in tracts.items() if v["tipo"] in cwb.NON_COMMUTING_TRACT_TYPES}

    by_mun: dict[str, list[int]] = defaultdict(list)
    index_of: dict[str, list[int]] = defaultdict(list)
    for i, (_lon, _lat, value, setor) in enumerate(residents):
        if setor in excluded:
            continue
        mun = tracts.get(setor, {}).get("mun")
        if not mun:
            continue
        by_mun[mun].append(value)
        index_of[mun].append(i)

    commuters = [0] * len(residents)
    for mun, values in by_mun.items():
        target = int(round(sum(values) * rates.get(mun, 0.0)))
        for i, share in zip(index_of[mun], largest_remainder(target, values)):
            commuters[i] = share

    commuter_total = sum(commuters)
    excluded_residents = sum(r[2] for r in residents if r[3] in excluded)
    print(f"  excluded tracts      : {len(excluded)} ({excluded_residents:,} residents)")
    print(f"  modelled commuters   : {commuter_total:,} ({commuter_total / census_total:.1%})")

    # ---- jobs, rescaled to balance ----
    jobs_raw = load_points(cwb.INTERIM / "jobs.csv", "jobs")
    jobs_total_raw = sum(j[2] for j in jobs_raw)
    print()
    print(f"  job sites            : {len(jobs_raw):,}")
    print(f"  jobs from CEMPRE     : {jobs_total_raw:,} ({jobs_total_raw / commuter_total:.2f} per commuter)")
    job_values = [j[2] for j in jobs_raw]
    jobs_scaled = largest_remainder(commuter_total, job_values)
    print(f"  jobs after balancing : {sum(jobs_scaled):,}")

    # ---- grid aggregation ----
    resident_points = [
        {"id": f"r{i}", "location": [lon, lat], "residents": commuters[i], "jobs": 0}
        for i, (lon, lat, _value, _setor) in enumerate(residents)
        if commuters[i]
    ]
    job_points = [
        {"id": f"j{i}", "location": [lon, lat], "residents": 0, "jobs": jobs_scaled[i]}
        for i, (lon, lat, _value, _setor) in enumerate(jobs_raw)
        if jobs_scaled[i]
    ]
    print()
    print(f"  non-zero resident sites: {len(resident_points):,}")
    print(f"  non-zero job sites     : {len(job_points):,}")

    combined = aggregate_to_grid(resident_points + job_points, args.grid)
    for index, point in enumerate(combined):
        point["id"] = f"cwb_{index}"
        point.pop("mergedIds", None)
        point["location"] = [round(point["location"][0], 6), round(point["location"][1], 6)]

    phantom = [p for p in combined if not p["residents"] and not p["jobs"]]
    if phantom:
        print(f"  ! {len(phantom)} phantom points — dropping")
        combined = [p for p in combined if p["residents"] or p["jobs"]]

    print()
    print(f"  demand points        : {len(combined):,}")
    print(f"  residents            : {sum(p['residents'] for p in combined):,}")
    print(f"  jobs                 : {sum(p['jobs'] for p in combined):,}")
    with_both = sum(1 for p in combined if p["residents"] and p["jobs"])
    print(f"  points with both     : {with_both:,}")
    print(f"  residents only       : {sum(1 for p in combined if p['residents'] and not p['jobs']):,}")
    print(f"  jobs only            : {sum(1 for p in combined if p['jobs'] and not p['residents']):,}")

    stats = summarise(combined)
    print()
    print("  summarise():", {k: v for k, v in stats.items() if not isinstance(v, dict)})

    if sum(p["residents"] for p in combined) != commuter_total:
        print("  ! resident total changed during aggregation — aborting")
        return 1

    cwb.write_json(cwb.INTERIM / "points.json", combined)
    cwb.write_json(
        cwb.INTERIM / "points_meta.json",
        {
            "grid_m": args.grid,
            "census_residents": census_total,
            "modelled_commuters": commuter_total,
            "jobs_cempre_raw": jobs_total_raw,
            "commuting_rates": rates,
            "excluded_tracts": sorted(excluded),
        },
        indent=2,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
