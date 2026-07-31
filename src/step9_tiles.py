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


# Ground-level polygon layers, in the order they win where they overlap.
#
# Every one of these is drawn by the game (or by Railyard's loader) as a fill or a fill-extrusion
# sitting at height 0. Two of them covering the same ground are coplanar, and coplanar surfaces
# z-fight — the flicker is a depth-buffer tie, not a data error, so it cannot be fixed by reordering
# layers or nudging opacity. The only fix is to make the surfaces disjoint.
#
# Measured overlap before flattening: water/park 16.11 km2, park/commercial 4.29, park/industrial
# 4.27, commercial/industrial 1.37, park/college 1.06, plus 6.75 km2 of self-overlap inside
# industrial alone (IPPUC zoning and OSM landuse=industrial stacked on each other).
#
# Priority reasoning: a lake inside a park should read as water; a campus inside a commercial
# district should read as a campus (which is what first-party tilesets do); parks beat the two
# generic employment zones because Curitiba's green space is the thing worth seeing.
GROUND_PRIORITY = ["water", "aerodrome", "college", "park", "industrial", "commercial"]

# Bed depth in metres below the surface, by water kind. Negative, matching the game's convention.
# Curitiba is inland so there is no bathymetry to sample — these are class defaults. The two
# drinking-water reservoirs (Passauna, Irai) are the deep bodies; rivers here are shallow.
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


def run(cmd: list[str], **kwargs) -> None:
    subprocess.run(cmd, check=True, **kwargs)


def flatten_ground(groups: dict[str, list]) -> dict[str, list]:
    """Dissolve each ground layer and subtract everything of higher priority.

    Returns the same keys with disjoint geometry: no point on the map is covered by two of them.
    Dissolving also removes each layer's overlap with itself, which z-fights identically but is
    easier to miss because it happens within one colour.
    """
    from shapely.ops import unary_union

    KM2 = 1.113e5 * 1.005e5 / 1e6
    claimed = None
    out: dict[str, list] = {}
    for name in GROUND_PRIORITY:
        geoms = [g for g in groups.get(name, []) if g.is_valid and not g.is_empty]
        if not geoms:
            out[name] = []
            continue
        raw_area = sum(g.area for g in geoms) * KM2
        merged = unary_union(geoms)
        if claimed is not None:
            merged = merged.difference(claimed)
        kept = list(_explode(merged))
        kept_area = sum(g.area for g in kept) * KM2
        print(
            f"    {name:<11} {len(geoms):>6,} -> {len(kept):>6,} polys   "
            f"{raw_area:>8.2f} -> {kept_area:>8.2f} km2"
        )
        out[name] = kept
        claimed = merged if claimed is None else unary_union([claimed, merged])
    return out


def _explode(geometry):
    if geometry is None or geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        for part in geometry.geoms:
            if part.geom_type == "Polygon" and not part.is_empty:
                yield part


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


def _collect_water():
    from shapely.geometry import shape

    seq = osmium_layer("water", OSM_WATER)
    out = []
    for feature in features_from_seq(seq):
        tags = feature.get("properties") or {}
        kind = tags.get("natural") or tags.get("landuse") or tags.get("waterway") or "water"
        try:
            out.append((shape(feature["geometry"]), kind))
        except Exception:  # noqa: BLE001
            pass
    for layer in ("HIDRO_RIOS_PG", "HIDRO_LAGOS_LAGOAS_REPRESAS", "HIDRO_AREA_UMIDA"):
        path = shapefile_to_geojson(layer, cwb.INTERIM / f"ippuc_{layer}.geojson")
        if not path:
            continue
        for feature in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") in ("Polygon", "MultiPolygon"):
                try:
                    out.append((shape(geometry), "water"))
                except Exception:  # noqa: BLE001
                    pass
    return out


def _collect_parks_and_aerodromes():
    from shapely.geometry import shape

    parks, aero = [], []
    for feature in features_from_seq(osmium_layer("park", OSM_PARK)):
        try:
            parks.append(shape(feature["geometry"]))
        except Exception:  # noqa: BLE001
            pass
    for layer in ("PARQUES_E_BOSQUES", "PRACAS_E_JARDINETES"):
        path = shapefile_to_geojson(layer, cwb.INTERIM / f"ippuc_{layer}.geojson")
        if not path:
            continue
        for feature in json.loads(path.read_text(encoding="utf-8")).get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") in ("Polygon", "MultiPolygon"):
                try:
                    parks.append(shape(geometry))
                except Exception:  # noqa: BLE001
                    pass
    for feature in features_from_seq(osmium_layer("aerodrome", OSM_AERODROME)):
        try:
            aero.append(shape(feature["geometry"]))
        except Exception:  # noqa: BLE001
            pass
    return parks, aero


def _collect_zoning():
    """commercial / college / industrial from IPPUC statutory zoning, with OSM outside Curitiba."""
    from shapely.geometry import shape

    commercial, college, industrial = [], [], []
    zoning = shapefile_to_geojson("ZONEAMENTO", cwb.INTERIM / "ippuc_ZONEAMENTO.geojson")
    if zoning:
        for feature in json.loads(zoning.read_text(encoding="utf-8")).get("features", []):
            geometry = feature.get("geometry") or {}
            if geometry.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            code = ((feature.get("properties") or {}).get("SG_ZONA") or "").strip().upper()
            bucket = (
                college if code in ZONE_COLLEGE
                else industrial if code in ZONE_INDUSTRIAL
                else commercial if code in ZONE_COMMERCIAL
                else None
            )
            if bucket is not None:
                try:
                    bucket.append(shape(geometry))
                except Exception:  # noqa: BLE001
                    pass
    for feature in features_from_seq(osmium_layer("commercial", OSM_COMMERCIAL)):
        try:
            commercial.append(shape(feature["geometry"]))
        except Exception:  # noqa: BLE001
            pass
    for feature in features_from_seq(osmium_layer("industrial", OSM_INDUSTRIAL)):
        try:
            industrial.append(shape(feature["geometry"]))
        except Exception:  # noqa: BLE001
            pass
    # Campuses are not reliably a zoning class — Curitiba's ZE covers 2 polygons — so they come
    # from OSM and fold into `commercial` with type=college, which is what the 1.5 style reads.
    for feature in features_from_seq(osmium_layer("campus", ["amenity=university", "amenity=college"])):
        try:
            college.append(shape(feature["geometry"]))
        except Exception:  # noqa: BLE001
            pass
    return commercial, college, industrial


def build_ocean_depth(polygons_in, water_kind) -> Path | None:
    """`ocean_depth_index.json` plus the `ocean_foundations` tile layer.

    Without these two, water is unconstrained: tracks can be laid straight across a reservoir at
    any elevation because nothing tells the game where the bed is. The index is the rule, the tile
    layer is the visual — shipping only one gets you either an invisible restriction or shading
    that restricts nothing.
    """
    print("  ocean depth index + ocean_foundations")
    from shapely import STRtree
    from shapely.geometry import Point, mapping

    import bathymetry

    KM2 = 1.113e5 * 1.005e5

    # Real per-body depths from HydroLAKES where the water body is covered, split into concentric
    # bands so the bed has a profile instead of being flat. Anything unmatched keeps the class
    # default for its water kind.
    base = cwb.RAW / "hydrolakes" / "HydroLAKES_points_v10_shp" / "HydroLAKES_points_v10"
    lakes = bathymetry.load_hydrolakes(base, cwb.BBOX) if base.with_suffix(".shp").exists() else []
    print(f"    HydroLAKES bodies in bbox: {len(lakes)}")
    lake_tree = STRtree([Point(l["lon"], l["lat"]) for l in lakes]) if lakes else None

    polygons: list[tuple[object, float]] = []
    matched = banded = 0
    for polygon in polygons_in:
        kind = water_kind.get(id(polygon), "water")
        lake = None
        if lake_tree is not None:
            for i in lake_tree.query(polygon, predicate="contains"):
                candidate = lakes[int(i)]
                if lake is None or candidate["depth_max"] > lake["depth_max"]:
                    lake = candidate
        if lake is None:
            polygons.append((polygon, WATER_DEPTH_M.get(kind, DEFAULT_WATER_DEPTH_M)))
            continue
        matched += 1
        bands = bathymetry.depth_bands(polygon, lake["depth_max"], polygon.area * KM2)
        if len(bands) > 1:
            banded += 1
        polygons.extend(bands)
    print(f"    polygons matched to a HydroLAKES body: {matched}  profiled into bands: {banded}")
    print(f"    depth polygons after banding: {len(polygons):,} (from {len(polygons_in):,})")
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
    cwb.write_json(
        cwb.OUT / "ocean_depth_index.json",
        {
            "cs": cs_y,
            "bbox": [round(min_lon, 6), round(min_lat, 6), round(max_lon, 6), round(max_lat, 6)],
            "grid": [cols, rows],
            "cells": [
                [col, row, *ids]
                for (col, row), ids in sorted(cells.items(), key=lambda kv: kv[0][::-1])
            ],
            "depths": depths,
            "stats": {"count": len(depths), "minDepth": min(all_depths), "maxDepth": max(all_depths)},
        },
    )
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


def build_ground_layers() -> dict[str, Path]:
    """Emit water / landuse / commercial / industrial as mutually disjoint surfaces."""
    print("  ground layers (water, landuse, commercial, industrial)")
    from shapely.geometry import mapping

    water = _collect_water()
    water_kind = {}
    parks, aero = _collect_parks_and_aerodromes()
    commercial, college, industrial = _collect_zoning()

    groups = {
        "water": [g for g, _k in water],
        "aerodrome": aero,
        "college": college,
        "park": parks,
        "industrial": industrial,
        "commercial": commercial,
    }
    print("    flattening by priority " + " > ".join(GROUND_PRIORITY))
    flat = flatten_ground(groups)

    # Water kind drives bed depth, and dissolving loses the per-polygon tag. Re-attach it by
    # testing each flattened piece against the original kinded geometry, deepest kind wins.
    from shapely import STRtree

    kinded = [(g, k) for g, k in water if g.is_valid and not g.is_empty]
    tree = STRtree([g for g, _k in kinded]) if kinded else None

    def kind_for(polygon):
        if tree is None:
            return "water"
        hits = tree.query(polygon, predicate="intersects")
        best, best_depth = "water", DEFAULT_WATER_DEPTH_M
        for i in hits:
            k = kinded[int(i)][1]
            d = WATER_DEPTH_M.get(k, DEFAULT_WATER_DEPTH_M)
            if d < best_depth:
                best, best_depth = k, d
        return best

    out: dict[str, Path] = {}

    water_path = cwb.INTERIM / "layer_water.geojsonseq"
    water_features = []
    for polygon in flat["water"]:
        kind = kind_for(polygon)
        water_kind[id(polygon)] = kind
        water_features.append(
            {
                "type": "Feature",
                "properties": {"kind": kind, "sort_rank": 200},
                "geometry": mapping(polygon),
            }
        )
    write_seq(water_path, water_features)
    out["water"] = water_path

    landuse_path = cwb.INTERIM / "layer_landuse.geojsonseq"
    landuse = []
    for polygon in flat["park"]:
        geometry = mapping(polygon)
        landuse.append({"type": "Feature", "properties": {"kind": "park", "area": round(area_m2(geometry), 1), "sort_rank": 300}, "geometry": geometry})
    for polygon in flat["aerodrome"]:
        landuse.append({"type": "Feature", "properties": {"kind": "aerodrome", "sort_rank": 250}, "geometry": mapping(polygon)})
    write_seq(landuse_path, landuse)
    out["landuse"] = landuse_path

    commercial_path = cwb.INTERIM / "layer_commercial.geojsonseq"
    commercial_features = []
    for kind, polys in (("commercial", flat["commercial"]), ("college", flat["college"])):
        for polygon in polys:
            geometry = mapping(polygon)
            commercial_features.append({"type": "Feature", "properties": {"type": kind, "area": round(area_m2(geometry), 1), "sort_rank": 320}, "geometry": geometry})
    write_seq(commercial_path, commercial_features)
    out["commercial"] = commercial_path

    industrial_path = cwb.INTERIM / "layer_industrial.geojsonseq"
    write_seq(
        industrial_path,
        (
            {"type": "Feature", "properties": {"kind": "industrial", "sort_rank": 310}, "geometry": mapping(p)}
            for p in flat["industrial"]
        ),
    )
    out["industrial"] = industrial_path

    ocean = build_ocean_depth(flat["water"], water_kind)
    if ocean:
        out["ocean_foundations"] = ocean
    return out


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
    sources.update(build_ground_layers())
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
