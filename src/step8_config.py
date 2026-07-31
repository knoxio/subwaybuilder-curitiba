"""Step 8 — write `config.json` and report on the pack.

`population` is the *census* figure for the extent, not the modelled commuter total: the schema
requires `sum(points[].residents)` to equal `sum(pops[].size)`, so `points[].residents` holds the
commuting subset while `config.json` states how many people actually live in the area. The
registry compares the two and warns when they differ, which is expected and correct here.

    python3 src/step8_config.py
"""

from __future__ import annotations

import argparse
import sys

import cwb

VERSION = "1.0.0"

DESCRIPTION = (
    "The city that invented bus rapid transit instead of building a metro. Curitiba's 1974 "
    "structural axes carry a quarter of the city to work by bus and exactly none by rail — the "
    "2022 census records 0.0% of commutes by train or metro. Coverage runs from Almirante "
    "Tamandaré and Colombo in the north through the centre and the Cidade Industrial to "
    "Araucária and Fazenda Rio Grande in the south, taking in São José dos Pinhais, Pinhais, "
    "Piraquara and Afonso Pena airport. Residents and jobs are built from IBGE's 2022 census, "
    "distributed to individual addresses using the national address register, with commutes "
    "calibrated against observed census travel times."
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", default=VERSION)
    args = parser.parse_args()

    cwb.banner("Step 8 — config.json")

    meta = cwb.read_json(cwb.INTERIM / "points_meta.json")
    demand_path = cwb.OUT / "demand_data.json"
    demand = cwb.read_json(demand_path) if demand_path.exists() else {"points": [], "pops": []}

    points, pops = demand["points"], demand["pops"]
    # Extra descriptive fields that shipped community maps carry and Railyard reads back — see an
    # installed map's config.json under metro-maker4/cities/data/<CODE>/. They are informational,
    # but they are what the launcher and the registry display, so it is worth filling them in.
    commuters = sum(q["size"] for q in pops if q["id"].startswith("pop_"))
    special = sum(q["size"] for q in pops if not q["id"].startswith("pop_"))

    special_types: dict[str, list[str]] = {}
    content_path = cwb.OUT / "special_demand_points.json"
    if content_path.exists():
        for entry in cwb.read_json(content_path)["points"]:
            special_types.setdefault(entry["type"], []).append(entry["name"]["__default__"])
    schools_path = cwb.INTERIM / "schools.json"
    if schools_path.exists():
        school_names = [
            (s["names"][0] if s.get("names") else s["id"]) for s in cwb.read_json(schools_path)
        ]
        if school_names:
            special_types["school"] = school_names

    config = {
        "code": cwb.CODE,
        "name": cwb.NAME,
        "country": cwb.COUNTRY,
        "description": DESCRIPTION,
        "population": meta["census_residents"],
        "bbox": cwb.BBOX,
        "thumbnailBbox": [-49.42, -25.60, -49.10, -25.32],
        "initialViewState": cwb.INITIAL_VIEW,
        "creator": cwb.CREATOR,
        "version": args.version,
        "commuterPopulation": commuters,
        "specialDemandPopulation": special,
        "totalPops": len(pops),
        "totalDemandPoints": len(points),
        "specialDemandTypes": [
            {
                "code": kind,
                "demandPointsCount": len(names),
                "demandPointNames": sorted(names)[:60],
            }
            for kind, names in sorted(special_types.items())
        ],
    }
    cwb.write_json(cwb.OUT / "config.json", config, indent=2)

    print()
    print(f"  census population    : {meta['census_residents']:,}")
    print(f"  modelled commuters   : {meta['modelled_commuters']:,}")
    print(f"  demand points        : {len(demand['points']):,}")
    print(f"  pops                 : {len(demand['pops']):,}")
    print()
    print("  pack contents:")
    required = [
        "config.json",
        f"{cwb.CODE}.pmtiles",
        "demand_data.json",
        "buildings_index.bin",
        "buildings_index.json",
        "roads.geojson",
        "runways_taxiways.geojson",
    ]
    for name in required:
        path = cwb.OUT / name
        if path.exists():
            print(f"    [x] {name:<32} {path.stat().st_size / 1e6:>9,.2f} MB")
        else:
            print(f"    [ ] {name:<32} {'MISSING':>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
