"""Step 9 — build `CWB.pmtiles` with the source-layer names the game's map style reads.

The 1.5 style names its source-layers directly and a map pack ships no JavaScript, so it cannot
remap them (see ../../docs/03-pmtiles-layers.md). Two exceptions Railyard's generated loader mod
handles: parks and airports are read from a `landuse` layer filtered on `kind`.

Layers emitted here:

| layer                 | source                                            |
| --------------------- | ------------------------------------------------- |
| `water`               | OSM water areas + IPPUC hydrography               |
| `buildings`           | Overture footprints, with `height`                |
| `landuse`             | parks (`kind=park`) and airports (`kind=aerodrome`)|
| `commercial`          | IPPUC statutory zoning, `type` commercial/college |
| `industrial`          | IPPUC `ZI` zones + OSM industrial landuse         |
| `city_labels`         | OSM `place` for the 17 municipalities             |
| `suburb_labels`       | OSM `place=suburb|town|village`                   |
| `neighborhood_labels` | IPPUC's 75 official bairros + OSM neighbourhoods  |

`neighborhood_labels` is US spelling; the British form renders nothing and gives no warning.

    python3 src/step9_tiles.py [--skip-buildings]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path

import cwb

MAXZOOM = 15
TILE_DIR = cwb.DATA / "tiles"

# Zoom range per layer, mirroring what a first-party tileset ships.
LAYER_ZOOM = {
    "water": (4, MAXZOOM),
    "ocean_foundations": (8, MAXZOOM),
    "buildings": (12, MAXZOOM),
    "landuse": (6, MAXZOOM),
    "commercial": (10, MAXZOOM),
    "industrial": (9, MAXZOOM),
    "city_labels": (6, MAXZOOM),
    "suburb_labels": (6, MAXZOOM),
    "neighborhood_labels": (6, MAXZOOM),
}

OSM_WATER = ["natural=water", "waterway=riverbank", "landuse=reservoir", "natural=wetland"]
OSM_PARK = [
    "leisure=park", "leisure=garden", "leisure=nature_reserve", "leisure=pitch",
    "landuse=forest", "landuse=grass", "landuse=cemetery", "landuse=recreation_ground",
    "landuse=village_green", "natural=wood", "natural=scrub", "tourism=zoo",
]
OSM_AERODROME = ["aeroway=aerodrome"]
OSM_INDUSTRIAL = ["landuse=industrial"]
OSM_COMMERCIAL = ["landuse=commercial", "landuse=retail"]

# IPPUC zoning code -> game layer. Codes come from LEI 15511/2019; see docs/09.
ZONE_COMMERCIAL = {"ZC", "ZUM-1", "ZUM-2", "ZUM-3", "ZS-1", "ZS-2", "ZS-2-LV",
                   "EE", "EMF", "ENC", "EAC", "ECO-1", "ECO-2", "ECL-1", "ECL-2", "ECL-3",
                   "POLO-LV", "SE-LV", "ZT-LV"}
ZONE_COLLEGE = {"ZE"}
ZONE_INDUSTRIAL = {"ZI", "ZI-LV"}


def run(cmd: list[str], **kwargs) -> None:
    subprocess.run(cmd, check=True, **kwargs)


def shapefile_to_geojson(layer: str, out: Path, *, where: str | None = None) -> Path | None:
    """Unpack an IPPUC shapefile and convert it to WGS84 GeoJSON with mapshaper."""
    archive = cwb.RAW / "ippuc" / f"{layer}.zip"
    if not archive.exists():
        print(f"    ! {layer}.zip not downloaded")
        return None
    work = cwb.INTERIM / "ippuc" / layer
    work.mkdir(parents=True, exist_ok=True)
    if not any(work.glob("*.shp")):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(work)
    shp = next((p for p in work.glob("*.shp") if p.stem.upper() == layer), None) or next(
        iter(work.glob("*.shp")), None
    )
    if shp is None:
        print(f"    ! no .shp inside {layer}.zip")
        return None
    if out.exists() and out.stat().st_size > 0:
        return out
    cmd = ["mapshaper", "-i", str(shp), "encoding=latin1"]
    if where:
        cmd += ["-filter", where]
    cmd += ["-proj", "wgs84", "-o", "format=geojson", str(out)]
    run(cmd, capture_output=True)
    return out


def osmium_layer(name: str, filters: list[str], types: str = "wr") -> Path:
    """Filter the trimmed PBF by tag and export a GeoJSON sequence."""
    out = cwb.INTERIM / f"tile_{name}.geojsonseq"
    if out.exists() and out.stat().st_size > 0:
        return out
    pbf = cwb.DATA / "osrm" / "cwb.osm.pbf"
    filtered = out.with_suffix(".osm.pbf")
    expressions = [f"{t}/{f}" for f in filters for t in types]
    run(
        ["osmium", "tags-filter", "--overwrite", "-o", str(filtered), str(pbf), *expressions],
        capture_output=True,
    )
    run(
        ["osmium", "export", "--overwrite", "-f", "geojsonseq", "-o", str(out), str(filtered)],
        capture_output=True,
    )
    filtered.unlink(missing_ok=True)
    return out


def features_from_seq(path: Path, wanted_types=("Polygon", "MultiPolygon")):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (feature.get("geometry") or {}).get("type") in wanted_types:
                yield feature


def write_seq(path: Path, features) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for feature in features:
            handle.write(json.dumps(feature, ensure_ascii=False) + "\n")
            count += 1
    return count


def area_m2(geometry) -> float:
    """Rough planar area, enough for the style's parks-large/parks-small split."""
    rings = []
    if geometry["type"] == "Polygon":
        rings = [geometry["coordinates"][0]]
    elif geometry["type"] == "MultiPolygon":
        rings = [poly[0] for poly in geometry["coordinates"]]
    total = 0.0
    for ring in rings:
        if len(ring) < 4:
            continue
        lat0 = sum(c[1] for c in ring) / len(ring)
        mx = 111_320.0 * math.cos(math.radians(lat0))
        my = 110_574.0
        acc = 0.0
        for i in range(len(ring) - 1):
            acc += ring[i][0] * mx * ring[i + 1][1] * my - ring[i + 1][0] * mx * ring[i][1] * my
        total += abs(acc) / 2
    return total


def build_water() -> Path:
    print("  water")
    out = cwb.INTERIM / "layer_water.geojsonseq"
    seq = osmium_layer("water", OSM_WATER)

    def gen():
        for feature in features_from_seq(seq):
            tags = feature.get("properties") or {}
            kind = tags.get("natural") or tags.get("landuse") or tags.get("waterway") or "water"
            yield {
                "type": "Feature",
                "properties": {"kind": kind, "sort_rank": 200},
                "geometry": feature["geometry"],
            }
        for layer in ("HIDRO_RIOS_PG", "HIDRO_LAGOS_LAGOAS_REPRESAS", "HIDRO_AREA_UMIDA"):
            path = shapefile_to_geojson(layer, cwb.INTERIM / f"ippuc_{layer}.geojson")
            if not path:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for feature in data.get("features", []):
                if (feature.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon"):
                    yield {
                        "type": "Feature",
                        "properties": {"kind": "water", "sort_rank": 200},
                        "geometry": feature["geometry"],
                    }

    print(f"    features: {write_seq(out, gen()):,}")
    return out


# Bed depth in metres below the surface, by water kind. Negative, matching the game's convention.
# Curitiba is inland, so there is no bathymetry to sample — these are class defaults. The two
# drinking-water reservoirs (Passaúna, Iraí) are the deep bodies; rivers here are shallow.
WATER_DEPTH_M = {
    "reservoir": -9.0,
    "basin": -6.0,
    "lake": -6.0,
    "water": -4.0,
    "river": -3.0,
    "canal": -3.0,
    "dock": -4.0,
    "wetland": -1.0,
}
DEFAULT_WATER_DEPTH_M = -4.0
# Grid cell size for the depth index, in degrees of latitude — the value the foundry uses.
OCEAN_CELL_SIZE = 0.0027


def build_ocean_depth(water_seq: Path) -> Path | None:
    """`ocean_depth_index.json` plus the `ocean_foundations` tile layer.

    Without these two, water is unconstrained: tracks can be laid straight across a reservoir at
    any elevation because nothing tells the game where the bed is. The index is the rule, the tile
    layer is the visual — shipping only one of them gets you either an invisible restriction or a
    shaded surface that restricts nothing.

    The index format mirrors the buildings index: a uniform grid over the water bbox, each
    non-empty cell listing the polygons overlapping it.
    """
    print("  ocean depth index + ocean_foundations")
    from shapely.geometry import mapping, shape

    polygons = []
    for feature in features_from_seq(water_seq):
        geometry = feature.get("geometry") or {}
        kind = (feature.get("properties") or {}).get("kind") or "water"
        depth = WATER_DEPTH_M.get(kind, DEFAULT_WATER_DEPTH_M)
        try:
            geom = shape(geometry)
        except Exception:  # noqa: BLE001
            continue
        if geom.is_empty:
            continue
        parts = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for part in parts:
            if part.geom_type != "Polygon" or part.is_empty:
                continue
            polygons.append((part, depth))
    if not polygons:
        print("    ! no water polygons — skipping")
        return None

    min_lon = min(p.bounds[0] for p, _ in polygons)
    min_lat = min(p.bounds[1] for p, _ in polygons)
    max_lon = max(p.bounds[2] for p, _ in polygons)
    max_lat = max(p.bounds[3] for p, _ in polygons)
    cs_y = OCEAN_CELL_SIZE
    cs_x = cs_y / math.cos(math.radians((min_lat + max_lat) / 2))
    cols = max(1, math.ceil((max_lon - min_lon) / cs_x))
    rows = max(1, math.ceil((max_lat - min_lat) / cs_y))

    depths = []
    cells: dict[tuple[int, int], list[int]] = {}
    for index, (polygon, depth) in enumerate(polygons):
        west, south, east, north = polygon.bounds
        depths.append(
            {
                "b": [round(v, 6) for v in polygon.bounds],
                "d": depth,
                "p": [[[round(x, 6), round(y, 6)] for x, y in polygon.exterior.coords]],
            }
        )
        c0 = max(0, int((west - min_lon) // cs_x))
        c1 = min(cols - 1, int((east - min_lon) // cs_x))
        r0 = max(0, int((south - min_lat) // cs_y))
        r1 = min(rows - 1, int((north - min_lat) // cs_y))
        for col in range(c0, c1 + 1):
            for row in range(r0, r1 + 1):
                cells.setdefault((col, row), []).append(index)

    all_depths = [d["d"] for d in depths]
    index_payload = {
        "cs": cs_y,
        "bbox": [round(min_lon, 6), round(min_lat, 6), round(max_lon, 6), round(max_lat, 6)],
        "grid": [cols, rows],
        "cells": [[col, row, *ids] for (col, row), ids in sorted(cells.items(), key=lambda kv: kv[0][::-1])],
        "depths": depths,
        "stats": {"count": len(depths), "minDepth": min(all_depths), "maxDepth": max(all_depths)},
    }
    cwb.write_json(cwb.OUT / "ocean_depth_index.json", index_payload)
    print(f"    {len(depths):,} water polygons, grid {cols} x {rows}, {len(cells):,} cells")
    print(f"    depth range {min(all_depths)} to {max(all_depths)} m")

    layer = cwb.INTERIM / "layer_ocean_foundations.geojsonseq"
    written = write_seq(
        layer,
        (
            {
                "type": "Feature",
                "properties": {"depth_min": depth, "kind": "ocean"},
                "geometry": mapping(polygon),
            }
            for polygon, depth in polygons
        ),
    )
    print(f"    ocean_foundations features: {written:,}")
    return layer


def build_landuse() -> Path:
    """Parks and aerodromes in one `landuse` layer, with the two made geometrically disjoint.

    Railyard renders both from this layer as `fill-extrusion` with height 0 and base 0
    (`parks-modded` filters `kind=park`, `airports-modded` filters `kind=aerodrome`). Two
    coplanar extrusions at the same height z-fight: Afonso Pena's apron flickered against the
    grass and woodland polygons OSM maps *inside* the airport perimeter — 75 park polygons
    overlapping 4.63 km2 of aerodrome.

    Dissolving the parks and subtracting the aerodromes fixes both that and park-on-park overlap
    (a wood inside a park is mapped as both), which z-fights the same way but less visibly.
    """
    print("  landuse (parks + aerodromes)")
    out = cwb.INTERIM / "layer_landuse.geojsonseq"
    parks = osmium_layer("park", OSM_PARK)
    aero = osmium_layer("aerodrome", OSM_AERODROME)

    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    park_geoms = [shape(f["geometry"]) for f in features_from_seq(parks)]
    for layer in ("PARQUES_E_BOSQUES", "PRACAS_E_JARDINETES"):
        path = shapefile_to_geojson(layer, cwb.INTERIM / f"ippuc_{layer}.geojson")
        if not path:
            continue
        for feature in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") in ("Polygon", "MultiPolygon"):
                park_geoms.append(shape(geometry))
    aero_geoms = [shape(f["geometry"]) for f in features_from_seq(aero)]
    print(f"    inputs: {len(park_geoms):,} park, {len(aero_geoms):,} aerodrome")

    park_geoms = [g for g in park_geoms if g.is_valid and not g.is_empty]
    aero_geoms = [g for g in aero_geoms if g.is_valid and not g.is_empty]
    merged_parks = unary_union(park_geoms) if park_geoms else None
    merged_aero = unary_union(aero_geoms) if aero_geoms else None
    if merged_parks is not None and merged_aero is not None:
        merged_parks = merged_parks.difference(merged_aero)

    def explode(geometry):
        if geometry is None or geometry.is_empty:
            return
        if geometry.geom_type == "Polygon":
            yield geometry
        elif geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
            for part in geometry.geoms:
                if part.geom_type == "Polygon" and not part.is_empty:
                    yield part

    def gen():
        for polygon in explode(merged_parks):
            geometry = mapping(polygon)
            yield {
                "type": "Feature",
                "properties": {"kind": "park", "area": round(area_m2(geometry), 1), "sort_rank": 300},
                "geometry": geometry,
            }
        for polygon in explode(merged_aero):
            yield {
                "type": "Feature",
                "properties": {"kind": "aerodrome", "sort_rank": 250},
                "geometry": mapping(polygon),
            }

    print(f"    features after dissolve + difference: {write_seq(out, gen()):,}")
    return out


def build_zoning() -> tuple[Path, Path]:
    """`commercial` (with the college distinction) and `industrial`, from statutory zoning."""
    print("  commercial + industrial (IPPUC zoning, OSM fallback outside Curitiba)")
    commercial_out = cwb.INTERIM / "layer_commercial.geojsonseq"
    industrial_out = cwb.INTERIM / "layer_industrial.geojsonseq"
    zoning = shapefile_to_geojson("ZONEAMENTO", cwb.INTERIM / "ippuc_ZONEAMENTO.geojson")

    commercial, industrial = [], []
    counts = {"commercial": 0, "college": 0, "industrial": 0}
    if zoning:
        for feature in json.loads(zoning.read_text(encoding="utf-8")).get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            tags = feature.get("properties") or {}
            code = (tags.get("SG_ZONA") or "").strip().upper()
            if code in ZONE_COLLEGE:
                counts["college"] += 1
                commercial.append({"type": "Feature", "properties": {"type": "college", "area": round(area_m2(geometry), 1), "sort_rank": 320}, "geometry": geometry})
            elif code in ZONE_COMMERCIAL:
                counts["commercial"] += 1
                commercial.append({"type": "Feature", "properties": {"type": "commercial", "area": round(area_m2(geometry), 1), "sort_rank": 320}, "geometry": geometry})
            elif code in ZONE_INDUSTRIAL:
                counts["industrial"] += 1
                industrial.append({"type": "Feature", "properties": {"kind": "industrial", "sort_rank": 310}, "geometry": geometry})

    # Zoning only covers the Curitiba municipality; OSM carries the other 16.
    for feature in features_from_seq(osmium_layer("commercial", OSM_COMMERCIAL)):
        counts["commercial"] += 1
        commercial.append({"type": "Feature", "properties": {"type": "commercial", "area": round(area_m2(feature["geometry"]), 1), "sort_rank": 320}, "geometry": feature["geometry"]})
    for feature in features_from_seq(osmium_layer("industrial", OSM_INDUSTRIAL)):
        counts["industrial"] += 1
        industrial.append({"type": "Feature", "properties": {"kind": "industrial", "sort_rank": 310}, "geometry": feature["geometry"]})

    # University and college campuses are not reliably a zoning class anywhere — Curitiba's ZE
    # covers only two polygons — so campuses come from OSM and are folded into `commercial` with
    # type=college, which is what the 1.5 style actually reads.
    campuses = osmium_layer("campus", ["amenity=university", "amenity=college"])
    for feature in features_from_seq(campuses):
        counts["college"] += 1
        commercial.append({"type": "Feature", "properties": {"type": "college", "area": round(area_m2(feature["geometry"]), 1), "sort_rank": 320}, "geometry": feature["geometry"]})

    print(f"    commercial {counts['commercial']:,}  college {counts['college']:,}  industrial {counts['industrial']:,}")
    write_seq(commercial_out, commercial)
    write_seq(industrial_out, industrial)
    return commercial_out, industrial_out


def build_labels() -> dict[str, Path]:
    print("  labels")
    places = osmium_layer("places", ["place"], types="n")
    tiers = {
        "city_labels": {"city", "borough", "municipality"},
        "suburb_labels": {"town", "village", "suburb"},
        "neighborhood_labels": {"neighbourhood", "quarter", "hamlet", "locality"},
    }
    buckets: dict[str, list] = {name: [] for name in tiers}
    for feature in features_from_seq(places, wanted_types=("Point",)):
        tags = feature.get("properties") or {}
        place = tags.get("place")
        name = tags.get("name")
        if not place or not name:
            continue
        for layer, kinds in tiers.items():
            if place in kinds:
                buckets[layer].append(
                    {
                        "type": "Feature",
                        "properties": {
                            "name": name,
                            "place": place,
                            "label_type": layer.replace("_labels", ""),
                        },
                        "geometry": feature["geometry"],
                    }
                )
                break

    # IPPUC's 75 official bairros are better than OSM's neighbourhood coverage; label them at
    # their centroid.
    bairros = shapefile_to_geojson("DIVISA_DE_BAIRROS", cwb.INTERIM / "ippuc_DIVISA_DE_BAIRROS.geojson")
    if bairros:
        added = 0
        for feature in json.loads(bairros.read_text(encoding="utf-8")).get("features", []):
            geometry = feature.get("geometry") or {}
            name = (feature.get("properties") or {}).get("NOME")
            if not name or geometry.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            rings = geometry["coordinates"][0] if geometry["type"] == "Polygon" else geometry["coordinates"][0][0]
            lon = sum(c[0] for c in rings) / len(rings)
            lat = sum(c[1] for c in rings) / len(rings)
            buckets["neighborhood_labels"].append(
                {
                    "type": "Feature",
                    "properties": {
                        "name": name.title(),
                        "place": "neighbourhood",
                        "label_type": "neighborhood",
                    },
                    "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
                }
            )
            added += 1
        print(f"    IPPUC bairros added: {added}")

    out = {}
    for layer, features in buckets.items():
        path = cwb.INTERIM / f"layer_{layer}.geojsonseq"
        print(f"    {layer}: {write_seq(path, features):,}")
        out[layer] = path
    return out


def build_buildings() -> Path | None:
    print("  buildings (Overture)")
    out = cwb.INTERIM / "layer_buildings.geojsonseq"
    if out.exists() and out.stat().st_size > 0:
        print(f"    cached ({out.stat().st_size / 1e6:,.0f} MB)")
        return out
    parquet = cwb.INTERIM / "overture_buildings.parquet"
    if not parquet.exists():
        print("    ! run step7b_overture.py first")
        return None
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    # Emit GeoJSON straight from duckdb; 1.3M features through Python would be needlessly slow.
    con.execute(
        f"""
        COPY (
            SELECT json_object(
                'type', 'Feature',
                'properties', json_object('height', coalesce(height, num_floors * 3.2, 3.2)),
                'geometry', ST_AsGeoJSON(ST_GeomFromWKB(wkb))::JSON
            ) AS feature
            FROM read_parquet('{parquet}')
        ) TO '{out}' (FORMAT CSV, HEADER false, QUOTE '', ESCAPE '', DELIMITER E'\\x01')
        """
    )
    print(f"    wrote {out.stat().st_size / 1e6:,.0f} MB")
    return out


def tile(layer: str, source: Path, extra: list[str] | None = None) -> Path:
    minzoom, maxzoom = LAYER_ZOOM.get(layer, (6, MAXZOOM))
    out = TILE_DIR / f"{layer}.pmtiles"
    if out.exists():
        out.unlink()
    cmd = [
        "tippecanoe", "-o", str(out), "-l", layer,
        "-Z", str(minzoom), "-z", str(maxzoom),
        "--drop-densest-as-needed", "--extend-zooms-if-still-dropping",
        "--no-tile-compression" if False else "--force",
        *(extra or []),
        str(source),
    ]
    print(f"    tippecanoe {layer} z{minzoom}-{maxzoom}")
    run(cmd, capture_output=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-buildings", action="store_true")
    args = parser.parse_args()

    cwb.banner("Step 9 — PMTiles")
    TILE_DIR.mkdir(parents=True, exist_ok=True)

    sources: dict[str, Path] = {}
    water_seq = build_water()
    sources["water"] = water_seq
    ocean = build_ocean_depth(water_seq)
    if ocean:
        sources["ocean_foundations"] = ocean
    sources["landuse"] = build_landuse()
    commercial, industrial = build_zoning()
    sources["commercial"] = commercial
    sources["industrial"] = industrial
    sources.update(build_labels())
    if not args.skip_buildings:
        buildings = build_buildings()
        if buildings:
            sources["buildings"] = buildings

    print()
    print("  tiling")
    archives = []
    for layer, path in sources.items():
        if path.stat().st_size == 0:
            print(f"    skipping empty {layer}")
            continue
        extra = ["-y", "height", "--maximum-tile-bytes", "450000"] if layer == "buildings" else []
        archives.append(tile(layer, path, extra))

    final = cwb.OUT / f"{cwb.CODE}.pmtiles"
    if final.exists():
        final.unlink()
    print(f"  tile-join -> {final.name}")
    run(["tile-join", "-o", str(final), "--force", *[str(a) for a in archives]], capture_output=True)
    print(f"  wrote {final.relative_to(cwb.ROOT)} ({final.stat().st_size / 1e6:,.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
