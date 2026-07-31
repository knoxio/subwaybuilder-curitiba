"""Step 7 — roads, runways/taxiways and the buildings index, straight from the OSM extract.

These three files are pure geometry, so they come from OSM rather than IBGE. `osmium` does the
filtering locally: the extract is already trimmed to the map area by step 6, and running tag
filters over a local PBF is far faster and more reliable than the same query against a public
Overpass instance.

Schemas are fixed by the game — see ../../docs/01-map-pack-format.md. `roads.geojson` needs
`roadClass` in {highway, major, minor}, `structure` in {normal, bridge, tunnel} and a `name`;
`runways_taxiways.geojson` needs polygons with `roadType`.

    python3 src/step7_geodata.py [--skip-buildings]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import cwb

# OSM highway value -> the game's three road classes.
ROAD_CLASS = {
    "motorway": "highway",
    "motorway_link": "highway",
    "trunk": "major",
    "trunk_link": "major",
    "primary": "major",
    "primary_link": "major",
    "secondary": "minor",
    "secondary_link": "minor",
    "tertiary": "minor",
    "tertiary_link": "minor",
    "unclassified": "minor",
    "residential": "minor",
}

# Buildings below this footprint are dropped from collision detection. depot defaults to 40 m2.
MIN_BUILDING_AREA_M2 = 40
# Grid cell size in degrees of latitude, matching the base game.
CELL_SIZE = 0.0009
DEFAULT_FOUNDATION_M = 10.0
MAX_FOUNDATION_M = 80.0

# Foundation depth model.
#
# A uniform depth is useless in play: if every building bottoms out at 10 m then a tunnel at 11 m
# is unconstrained under the entire city, and the buildings index stops being a constraint at all.
# Depth has to vary with the structure.
#
# Where a real height is known, depth scales with height and slenderness — a tall narrow tower
# needs to go deeper than a squat one of the same height:
#
#     depth = HEIGHT_ALPHA * h * (h / width) ** 0.25
#
# Overture's Brazilian footprints almost never carry a height (0.1%), so for the rest the
# *footprint area* stands in. That is measured geometry rather than an assumption about the
# building, and it separates the cases that matter: a 20,000 m2 warehouse or shopping centre is
# founded far deeper than a 60 m2 house whatever their heights. Documented as a proxy, not a claim.
HEIGHT_ALPHA = 0.22
AREA_DEPTH_TIERS = [
    (100, 10.0),
    (500, 12.0),
    (2_000, 16.0),
    (10_000, 22.0),
    (40_000, 30.0),
]
AREA_DEPTH_MAX = 38.0

RUNWAY_WIDTH_M = {"runway": 30.0, "taxiway": 10.0}


def trimmed_pbf() -> Path:
    path = cwb.DATA / "osrm" / "cwb.osm.pbf"
    if path.exists():
        return path
    source = cwb.RAW / "osm" / "sul-latest.osm.pbf"
    path.parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = cwb.BBOX
    print(f"  trimming extract to the map bbox")
    subprocess.run(
        [
            "osmium", "extract", "--bbox",
            f"{west - 0.05},{south - 0.05},{east + 0.05},{north + 0.05}",
            "--overwrite", "-o", str(path), str(source),
        ],
        check=True,
    )
    return path


def osmium_geojson(pbf: Path, expressions: list[str], out: Path, object_type: str = "w") -> Path:
    """Filter the PBF by tag and export GeoJSON sequence text, caching the result."""
    if out.exists() and out.stat().st_size > 0:
        print(f"  cached {out.name}")
        return out
    filtered = out.with_suffix(".osm.pbf")
    subprocess.run(
        ["osmium", "tags-filter", "--overwrite", "-o", str(filtered), str(pbf), *[f"{object_type}/{e}" for e in expressions]],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["osmium", "export", "--overwrite", "-f", "geojsonseq", "-o", str(out), str(filtered)],
        check=True,
        capture_output=True,
    )
    filtered.unlink(missing_ok=True)
    print(f"  {out.name}: {out.stat().st_size / 1e6:,.1f} MB")
    return out


def iter_features(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def build_roads(pbf: Path) -> None:
    cwb.banner("roads.geojson")
    raw = osmium_geojson(pbf, ["highway"], cwb.INTERIM / "osm_roads.geojsonseq")
    features = []
    counts = {"highway": 0, "major": 0, "minor": 0}
    structures = {"normal": 0, "bridge": 0, "tunnel": 0}
    for feature in iter_features(raw):
        tags = feature.get("properties") or {}
        road_class = ROAD_CLASS.get(tags.get("highway"))
        if not road_class:
            continue
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("LineString", "MultiLineString"):
            continue
        if tags.get("bridge") and tags["bridge"] != "no":
            structure = "bridge"
        elif tags.get("tunnel") and tags["tunnel"] != "no":
            structure = "tunnel"
        else:
            structure = "normal"
        name = tags.get("name") or tags.get("ref") or ""
        if tags.get("noname") == "yes":
            name = ""
        counts[road_class] += 1
        structures[structure] += 1
        features.append(
            {
                "type": "Feature",
                "properties": {"roadClass": road_class, "structure": structure, "name": name},
                "geometry": geometry,
            }
        )
    print(f"  features: {len(features):,}  {counts}  {structures}")
    cwb.write_json(cwb.OUT / "roads.geojson", {"type": "FeatureCollection", "features": features})


def build_runways(pbf: Path) -> None:
    cwb.banner("runways_taxiways.geojson")
    raw = osmium_geojson(pbf, ["aeroway"], cwb.INTERIM / "osm_aeroway.geojsonseq")
    features = []
    kinds = {}
    for feature in iter_features(raw):
        tags = feature.get("properties") or {}
        aeroway = tags.get("aeroway")
        if aeroway not in ("runway", "taxiway", "apron"):
            continue
        geometry = feature.get("geometry") or {}
        kinds[aeroway] = kinds.get(aeroway, 0) + 1
        if geometry.get("type") == "LineString" and aeroway in RUNWAY_WIDTH_M:
            polygon = buffer_line(geometry["coordinates"], RUNWAY_WIDTH_M[aeroway] / 2)
            if not polygon:
                continue
            geometry = {"type": "Polygon", "coordinates": [polygon]}
        elif geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "roadType": "runway" if aeroway != "taxiway" else "taxiway",
                    "z_order": 0,
                    "osm_way_id": str(tags.get("id") or feature.get("id") or ""),
                    "area": 0,
                },
                "geometry": geometry,
            }
        )
    print(f"  features: {len(features):,}  {kinds}")
    cwb.write_json(
        cwb.OUT / "runways_taxiways.geojson", {"type": "FeatureCollection", "features": features}
    )


def buffer_line(coords: list, half_width_m: float) -> list | None:
    """Rectangle-ish buffer around a centreline, as a closed ring."""
    if len(coords) < 2:
        return None
    lat0 = sum(c[1] for c in coords) / len(coords)
    mx = 111_320.0 * math.cos(math.radians(lat0))
    my = 110_574.0
    left, right = [], []
    for index, (lon, lat) in enumerate(coords):
        if index == 0:
            nxt = coords[1]
            dx, dy = (nxt[0] - lon) * mx, (nxt[1] - lat) * my
        else:
            prv = coords[index - 1]
            dx, dy = (lon - prv[0]) * mx, (lat - prv[1]) * my
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        left.append([lon + nx * half_width_m / mx, lat + ny * half_width_m / my])
        right.append([lon - nx * half_width_m / mx, lat - ny * half_width_m / my])
    ring = left + list(reversed(right))
    ring.append(ring[0])
    return ring


def ring_area_m2(ring: list, lat0: float) -> float:
    mx = 111_320.0 * math.cos(math.radians(lat0))
    my = 110_574.0
    total = 0.0
    for i in range(len(ring) - 1):
        x1, y1 = ring[i][0] * mx, ring[i][1] * my
        x2, y2 = ring[i + 1][0] * mx, ring[i + 1][1] * my
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def foundation_depth(height: float | None, area_m2: float, min_width_m: float) -> tuple[float, str]:
    """Foundation depth in metres, and which model produced it."""
    if height and height > 4.0:
        width = max(min_width_m, 4.0)
        depth = HEIGHT_ALPHA * height * (height / width) ** 0.25
        if depth > DEFAULT_FOUNDATION_M:
            return min(depth, MAX_FOUNDATION_M), "height"
    for limit, depth in AREA_DEPTH_TIERS:
        if area_m2 < limit:
            return depth, "area"
    return AREA_DEPTH_MAX, "area"


def osm_height_index(pbf: Path) -> list[tuple[list[float], float]]:
    """(bounds, height) for OSM buildings that carry usable height information.

    Overture's Brazilian footprints are mostly ML-derived and almost never have a height (0.1%),
    while OSM has few buildings but tags them better. Taking geometry from one and heights from the
    other gives 3D extrusions where they matter — the centre, where OSM coverage is good — instead
    of a uniformly flat city.
    """
    raw = osmium_geojson(pbf, ["building"], cwb.INTERIM / "osm_buildings.geojsonseq")
    out = []
    for feature in iter_features(raw):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        tags = feature.get("properties") or {}
        height = None
        for key in ("height", "building:height"):
            if tags.get(key):
                try:
                    height = float(str(tags[key]).split()[0])
                    break
                except ValueError:
                    pass
        if height is None and tags.get("building:levels"):
            try:
                height = float(tags["building:levels"]) * 3.2
            except ValueError:
                height = None
        if height is None or not (1.0 <= height <= 400.0):
            continue
        rings = [geometry["coordinates"][0]] if geometry["type"] == "Polygon" else [
            p[0] for p in geometry["coordinates"]
        ]
        for ring in rings:
            lons = [c[0] for c in ring]
            lats = [c[1] for c in ring]
            out.append(([min(lons), min(lats), max(lons), max(lats)], height))
    return out


def build_buildings_overture(pbf: Path) -> None:
    """Buildings index from Overture footprints, with heights borrowed from OSM."""
    cwb.banner("buildings_index.{bin,json} — Overture footprints")
    parquet = cwb.INTERIM / "overture_buildings.parquet"
    if not parquet.exists():
        print("  ! run step7b_overture.py first")
        return

    import duckdb
    from shapely import STRtree, box
    from shapely.wkb import loads as wkb_loads

    heights = osm_height_index(pbf)
    print(f"  OSM buildings with height: {len(heights):,}")
    tree = STRtree([box(*bounds) for bounds, _h in heights]) if heights else None
    height_values = [h for _b, h in heights]

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    rows = con.execute(
        f"""SELECT wkb, xmin, ymin, xmax, ymax, height, num_floors, num_floors_underground
            FROM read_parquet('{parquet}')"""
    )

    buildings = []
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    skipped_small = skipped_shape = 0
    borrowed = 0
    depth_models = {"height": 0, "area": 0}

    while True:
        batch = rows.fetchmany(50_000)
        if not batch:
            break
        for wkb, xmin, ymin, xmax, ymax, height, floors, under in batch:
            if not (xmax >= cwb.BBOX[0] and xmin <= cwb.BBOX[2] and ymax >= cwb.BBOX[1] and ymin <= cwb.BBOX[3]):
                continue
            try:
                geometry = wkb_loads(bytes(wkb))
            except Exception:
                skipped_shape += 1
                continue
            polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
            for polygon in polygons:
                if polygon.is_empty or polygon.geom_type != "Polygon":
                    skipped_shape += 1
                    continue
                # Equal-area check via the shapely area in square degrees is latitude-dependent;
                # -25.4 deg gives ~1 deg^2 = 1.113e5 * 1.005e5 m^2.
                if polygon.area * 1.113e5 * 1.005e5 < MIN_BUILDING_AREA_M2:
                    skipped_small += 1
                    continue
                outer = [[round(x, 7), round(y, 7)] for x, y in polygon.exterior.coords]
                if len(outer) < 4:
                    skipped_shape += 1
                    continue
                lons = [c[0] for c in outer]
                lats = [c[1] for c in outer]
                bounds = [min(lons), min(lats), max(lons), max(lats)]
                min_lon = min(min_lon, bounds[0])
                min_lat = min(min_lat, bounds[1])
                max_lon = max(max_lon, bounds[2])
                max_lat = max(max_lat, bounds[3])

                metres = None
                if height:
                    metres = float(height)
                elif floors:
                    metres = float(floors) * 3.2
                elif tree is not None:
                    hits = tree.query(polygon, predicate="intersects")
                    if len(hits):
                        metres = height_values[int(hits[0])]
                        borrowed += 1
                known_height = metres if metres and 1.0 <= metres <= 400.0 else None
                if known_height is None:
                    metres = 3.2

                # metres-per-degree at this latitude, for area and width in real units
                span_x = (bounds[2] - bounds[0]) * 111_320.0 * 0.902
                span_y = (bounds[3] - bounds[1]) * 110_574.0
                area_m2 = polygon.area * 1.113e5 * 1.005e5
                depth, model = foundation_depth(known_height, area_m2, min(span_x, span_y))
                depth_models[model] += 1
                if under:
                    try:
                        depth = max(depth, float(under) * 3.5)
                    except (TypeError, ValueError):
                        pass
                buildings.append(
                    {
                        "bounds": bounds,
                        "foundationDepth": round(depth, 2),
                        "polygon": [outer],
                        "height": round(metres, 2),
                    }
                )

    print(f"  buildings kept: {len(buildings):,}")
    print(f"  dropped: {skipped_small:,} under {MIN_BUILDING_AREA_M2} m2, {skipped_shape:,} bad geometry")
    print(f"  heights borrowed from OSM: {borrowed:,}")
    print(f"  foundation depth from height: {depth_models['height']:,}  from footprint area: {depth_models['area']:,}")
    depths = [b["foundationDepth"] for b in buildings]
    import statistics
    print(f"  depth: min {min(depths)} median {statistics.median(depths)} max {max(depths)} "
          f"({len(set(depths)):,} distinct)")
    _write_index(buildings, min_lon, min_lat, max_lon, max_lat)


def _write_index(buildings, min_lon, min_lat, max_lon, max_lat) -> None:
    sys.path.insert(0, str(cwb.TOOLS))
    try:
        import buildings_index as bi
    except ImportError as error:  # pragma: no cover - explained in tools/README.md
        raise ImportError(
            "buildings_index.py is not bundled with this repository — see tools/README.md"
        ) from error

    cols = math.ceil((max_lon - min_lon) / (CELL_SIZE / math.cos(math.radians((min_lat + max_lat) / 2)))) + 1
    rows_n = math.ceil((max_lat - min_lat) / CELL_SIZE) + 1
    cells = bi.build_cells(
        buildings, cols=cols, rows=rows_n, cell_size=CELL_SIZE,
        min_lon=min_lon, min_lat=min_lat, max_lat=max_lat,
    )
    total_refs = sum(len(c["buildingIds"]) for c in cells)
    print(f"  grid {cols} x {rows_n}, {len(cells):,} non-empty cells, {total_refs:,} refs")
    if total_refs <= len(buildings):
        print("  ! fewer cell refs than buildings — straddling buildings would be missed")

    packed = bi.encode(
        buildings=buildings, cells=cells, cols=cols, rows=rows_n, cell_size=CELL_SIZE,
        max_foundation_depth=max(b["foundationDepth"] for b in buildings),
        min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat,
    )
    header = bi.validate(packed)
    (cwb.OUT / "buildings_index.bin").write_bytes(packed)
    print(f"  wrote out/buildings_index.bin ({len(packed) / 1e6:,.1f} MB, heights={'yes' if header.has_heights else 'no'})")

    legacy = {
        "cs": CELL_SIZE,
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "grid": [cols, rows_n],
        "cells": [[c["col"], c["row"], *c["buildingIds"]] for c in cells],
        "buildings": [{"b": b["bounds"], "f": b["foundationDepth"], "p": b["polygon"]} for b in buildings],
        "stats": {"count": len(buildings), "maxDepth": max(b["foundationDepth"] for b in buildings)},
    }
    cwb.write_json(cwb.OUT / "buildings_index.json", legacy)


def build_buildings(pbf: Path) -> None:
    cwb.banner("buildings_index.{bin,json}")
    raw = osmium_geojson(pbf, ["building"], cwb.INTERIM / "osm_buildings.geojsonseq")

    buildings = []
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    skipped_small = skipped_shape = 0

    for feature in iter_features(raw):
        geometry = feature.get("geometry") or {}
        kind = geometry.get("type")
        if kind == "Polygon":
            rings = [geometry["coordinates"]]
        elif kind == "MultiPolygon":
            rings = geometry["coordinates"]
        else:
            skipped_shape += 1
            continue
        tags = feature.get("properties") or {}
        depth = DEFAULT_FOUNDATION_M
        levels = tags.get("building:levels:underground")
        if levels:
            try:
                depth = max(DEFAULT_FOUNDATION_M, float(levels) * 3.5)
            except ValueError:
                pass
        height = None
        for key in ("height", "building:height"):
            if tags.get(key):
                try:
                    height = float(str(tags[key]).split()[0])
                    break
                except ValueError:
                    pass
        if height is None and tags.get("building:levels"):
            try:
                height = float(tags["building:levels"]) * 3.2
            except ValueError:
                height = None

        for polygon in rings:
            outer = [list(map(float, c[:2])) for c in polygon[0]]
            if len(outer) < 4:
                skipped_shape += 1
                continue
            if outer[0] != outer[-1]:
                outer.append(outer[0])
            lat0 = sum(c[1] for c in outer) / len(outer)
            if ring_area_m2(outer, lat0) < MIN_BUILDING_AREA_M2:
                skipped_small += 1
                continue
            lons = [c[0] for c in outer]
            lats = [c[1] for c in outer]
            bounds = [min(lons), min(lats), max(lons), max(lats)]
            if not (
                bounds[2] >= cwb.BBOX[0]
                and bounds[0] <= cwb.BBOX[2]
                and bounds[3] >= cwb.BBOX[1]
                and bounds[1] <= cwb.BBOX[3]
            ):
                continue
            min_lon = min(min_lon, bounds[0])
            min_lat = min(min_lat, bounds[1])
            max_lon = max(max_lon, bounds[2])
            max_lat = max(max_lat, bounds[3])
            entry = {
                "bounds": bounds,
                "foundationDepth": depth,
                "polygon": [outer],
            }
            if height is not None and 1.0 <= height <= 400.0:
                entry["height"] = height
            buildings.append(entry)

    print(f"  buildings kept: {len(buildings):,}")
    print(f"  dropped: {skipped_small:,} under {MIN_BUILDING_AREA_M2} m2, {skipped_shape:,} bad geometry")
    with_height = sum(1 for b in buildings if "height" in b)
    print(f"  with height: {with_height:,} ({with_height / max(len(buildings), 1):.1%})")

    # The heights section of the binary is only written when *every* building has one, so a
    # partially-tagged extract would silently lose the whole section. Fill the gaps with a
    # single-storey default so the layer exists.
    if 0 < with_height < len(buildings):
        for entry in buildings:
            entry.setdefault("height", 3.2)
        print("  filled missing heights with 3.2 m so the heights section is emitted")

    sys.path.insert(0, str(cwb.TOOLS))
    try:
        import buildings_index as bi
    except ImportError as error:  # pragma: no cover - explained in tools/README.md
        raise ImportError(
            "buildings_index.py is not bundled with this repository — see tools/README.md"
        ) from error

    cols = math.ceil((max_lon - min_lon) / (CELL_SIZE / math.cos(math.radians((min_lat + max_lat) / 2)))) + 1
    rows = math.ceil((max_lat - min_lat) / CELL_SIZE) + 1
    cells = bi.build_cells(
        buildings,
        cols=cols,
        rows=rows,
        cell_size=CELL_SIZE,
        min_lon=min_lon,
        min_lat=min_lat,
        max_lat=max_lat,
    )
    total_refs = sum(len(c["buildingIds"]) for c in cells)
    print(f"  grid {cols} x {rows}, {len(cells):,} non-empty cells, {total_refs:,} refs")
    if total_refs <= len(buildings):
        print("  ! fewer cell refs than buildings — straddling buildings would be missed")

    packed = bi.encode(
        buildings=buildings,
        cells=cells,
        cols=cols,
        rows=rows,
        cell_size=CELL_SIZE,
        max_foundation_depth=max(b["foundationDepth"] for b in buildings),
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
    )
    header = bi.validate(packed)
    (cwb.OUT / "buildings_index.bin").write_bytes(packed)
    print(f"  wrote out/buildings_index.bin ({len(packed) / 1e6:,.1f} MB, heights={'yes' if header.has_heights else 'no'})")

    # Legacy JSON form keeps the pack installable on game <=1.3.0.
    legacy = {
        "cs": CELL_SIZE,
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "grid": [cols, rows],
        "cells": [[c["col"], c["row"], *c["buildingIds"]] for c in cells],
        "buildings": [
            {"b": b["bounds"], "f": b["foundationDepth"], "p": b["polygon"]} for b in buildings
        ],
        "stats": {
            "count": len(buildings),
            "maxDepth": max(b["foundationDepth"] for b in buildings),
        },
    }
    cwb.write_json(cwb.OUT / "buildings_index.json", legacy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-buildings", action="store_true")
    parser.add_argument("--skip-roads", action="store_true")
    parser.add_argument("--osm-buildings", action="store_true", help="use OSM instead of Overture")
    args = parser.parse_args()

    pbf = trimmed_pbf()
    print(f"  source: {pbf} ({pbf.stat().st_size / 1e6:,.0f} MB)")
    if not args.skip_roads:
        build_roads(pbf)
        build_runways(pbf)
    if not args.skip_buildings:
        if args.osm_buildings:
            build_buildings(pbf)
        else:
            build_buildings_overture(pbf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
