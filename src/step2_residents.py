"""Step 2 — push census-tract population down onto individual dwelling addresses.

IBGE gives residents per census tract (`v0001`) and, separately, every address in the country
with coordinates and a species code. Each CNEFE dwelling row is one dwelling *unit*, not one
building: an apartment tower appears as up to ~540 rows sharing a coordinate. That is why the
row count per municipality equals the census household count exactly — verified for Curitiba,
789,846 rows against `v0002` = 789,846 — and why distributing a tract's residents uniformly
across its dwelling rows reproduces the census mean household size by construction rather than
by assumption.

Output is one record per distinct coordinate, carrying the summed residents of the units there.

    python3 src/step2_residents.py [--municipality 4106902]
"""

from __future__ import annotations

import argparse
import csv
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import cwb
from sources.formats import gpkg_layer

csv.field_size_limit(1 << 24)


def load_tracts(with_geometry: bool = False) -> tuple[dict[str, dict], list, list[str]]:
    """Census tracts whose centroid falls inside BBOX, keyed by tract geocode.

    Returns `(tracts, polygons, polygon_codes)`; the last two are empty unless
    `with_geometry`, in which case they are parallel lists suitable for an STRtree.
    """
    tracts: dict[str, dict] = {}
    polygons: list = []
    codes: list[str] = []
    columns = ["CD_SETOR", "CD_MUN", "NM_MUN", "NM_BAIRRO", "CD_SIT", "CD_TIPO", "v0001", "v0002"]
    for attrs, envelope in gpkg_layer(
        cwb.census_gpkg(),
        columns=columns,
        where="NM_CONCURB LIKE '%Curitiba%'",
        geometry=with_geometry,
    ):
        if not envelope:
            continue
        lon = (envelope[0] + envelope[2]) / 2
        lat = (envelope[1] + envelope[3]) / 2
        if not cwb.in_bbox(lon, lat):
            continue
        # CD_SETOR comes back as text; keep it as text or the zero-padded geocode stops joining.
        code = str(attrs["CD_SETOR"])
        tracts[code] = {
            "mun": str(attrs["CD_MUN"]),
            "mun_name": attrs["NM_MUN"],
            "bairro": attrs["NM_BAIRRO"],
            "sit": str(attrs["CD_SIT"] or ""),
            "tipo": str(attrs["CD_TIPO"] or "0"),
            "residents": int(attrs["v0001"] or 0),
            "households": int(attrs["v0002"] or 0),
            "lon": lon,
            "lat": lat,
        }
        if with_geometry and attrs.get("_wkb"):
            from shapely.wkb import loads

            try:
                polygons.append(loads(attrs["_wkb"]))
                codes.append(code)
            except Exception:  # noqa: BLE001 — a single unreadable tract must not stop the build
                pass
    return tracts, polygons, codes


def build_tract_index(polygons: list, codes: list[str]):
    """An STRtree over tract polygons plus the code lookup, for assigning addresses spatially.

    A spatial join is used rather than CNEFE's own `COD_SETOR` because the two products are
    different mesh vintages. CNEFE 2022 carries the *preliminary* tract codes — every row ends
    in a `P` — while `malha_com_atributos` ships the mesh corrected in April 2025. Stripping the
    suffix joins about 97% of Curitiba's tracts and leaves 100 CNEFE-only and 129 mesh-only
    codes, so a code join silently drops ~3% of addresses. Point-in-polygon has no such skew.
    """
    from shapely import STRtree

    return STRtree(polygons), codes


def cnefe_rows(code: str):
    """Stream the CNEFE CSV for one municipality out of its zip without extracting it."""
    archive = cwb.cnefe_zip(code)
    if not archive.exists():
        print(f"    ! missing {archive.name}")
        return
    with zipfile.ZipFile(archive) as zf:
        name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if not name:
            print(f"    ! no CSV inside {archive.name}")
            return
        with zf.open(name) as raw:
            # IBGE ships latin-1, semicolon-delimited. Decoding as UTF-8 dies on the first "ã".
            text = (line.decode("latin-1") for line in raw)
            yield from csv.DictReader(text, delimiter=";")


def assign_tracts(tree, codes: list[str], points: list[tuple[float, float]]) -> list[str | None]:
    """Point-in-polygon assign a batch of coordinates to tract codes.

    Uses one vectorised STRtree query for the whole batch. `predicate="within"` is exact, not
    just a bounding-box test, so a point in the gap between two tracts returns nothing rather
    than being attached to a neighbour — those are counted and reported, not silently absorbed.
    """
    from shapely import points as make_points

    geoms = make_points([[lon, lat] for lon, lat in points])
    result: list[str | None] = [None] * len(points)
    pairs = tree.query(geoms, predicate="within")
    for point_index, tract_index in zip(pairs[0], pairs[1]):
        if result[point_index] is None:
            result[point_index] = codes[tract_index]
    return result


def largest_remainder(total: int, weights: list[int]) -> list[int]:
    """Split `total` across `weights` so the parts are integers summing to exactly `total`."""
    if total <= 0 or not weights:
        return [0] * len(weights)
    weight_sum = sum(weights)
    if weight_sum <= 0:
        base = total // len(weights)
        parts = [base] * len(weights)
        for i in range(total - base * len(weights)):
            parts[i] += 1
        return parts
    exact = [total * w / weight_sum for w in weights]
    parts = [int(v) for v in exact]
    shortfall = total - sum(parts)
    if shortfall:
        order = sorted(range(len(weights)), key=lambda i: exact[i] - parts[i], reverse=True)
        for i in order[:shortfall]:
            parts[i] += 1
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--municipality", help="only process one IBGE code (for testing)")
    args = parser.parse_args()

    cwb.banner("Step 2 — residents onto dwelling addresses")

    tracts, polygons, poly_codes = load_tracts(with_geometry=True)
    tree, tree_codes = build_tract_index(polygons, poly_codes)
    print(f"  tract polygons indexed: {len(polygons):,}")
    print(f"  tracts in bbox: {len(tracts):,}")
    print(f"  residents      : {sum(t['residents'] for t in tracts.values()):,}")
    print(f"  households     : {sum(t['households'] for t in tracts.values()):,}")

    non_commuting = {k: v for k, v in tracts.items() if v["tipo"] in cwb.NON_COMMUTING_TRACT_TYPES}
    if non_commuting:
        print(
            f"  non-commuting tracts (barracks/prison/care): {len(non_commuting)}, "
            f"{sum(t['residents'] for t in non_commuting.values()):,} residents"
        )

    municipalities = (
        [(args.municipality, cwb.MUN_NAME.get(args.municipality, "?"))]
        if args.municipality
        else cwb.MUNICIPALITIES
    )

    # coordinate-level accumulation, keyed per tract so distribution stays inside the tract
    units: dict[str, dict[tuple[float, float], int]] = defaultdict(lambda: defaultdict(int))
    stats = {
        "rows": 0,
        "dwellings": 0,
        "kept": 0,
        "out_of_bbox": 0,
        "no_tract": 0,
        "code_agrees": 0,
        "code_differs": 0,
    }

    for code, name in municipalities:
        before = stats["kept"]
        pending: list[tuple[float, float, str]] = []

        def flush() -> None:
            if not pending:
                return
            assigned = assign_tracts(tree, tree_codes, [(p[0], p[1]) for p in pending])
            for (lon, lat, cnefe_code), tract_code in zip(pending, assigned):
                if tract_code is None:
                    stats["no_tract"] += 1
                    continue
                if cnefe_code == tract_code:
                    stats["code_agrees"] += 1
                else:
                    stats["code_differs"] += 1
                units[tract_code][(round(lon, 6), round(lat, 6))] += 1
                stats["kept"] += 1
            pending.clear()

        for row in cnefe_rows(code):
            stats["rows"] += 1
            if row["COD_ESPECIE"] not in cwb.DWELLING_SPECIES:
                continue
            stats["dwellings"] += 1
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
            except (TypeError, ValueError):
                continue
            if not cwb.in_bbox(lon, lat):
                stats["out_of_bbox"] += 1
                continue
            raw_code = row["COD_SETOR"] or ""
            pending.append((lon, lat, raw_code[:-1] if raw_code[-1:].isalpha() else raw_code))
            if len(pending) >= 200_000:
                flush()
        flush()
        print(f"  {name:<24} dwelling units kept: {stats['kept'] - before:>8,}")

    print()
    print(f"  CNEFE rows read      : {stats['rows']:,}")
    print(f"  dwelling rows        : {stats['dwellings']:,}")
    print(f"  kept (in a tract)    : {stats['kept']:,}")
    print(f"  dropped, outside box : {stats['out_of_bbox']:,}")
    print(f"  dropped, no tract    : {stats['no_tract']:,}")
    print(f"  distinct coordinates : {sum(len(v) for v in units.values()):,}")
    decided = stats["code_agrees"] + stats["code_differs"]
    if decided:
        share = stats["code_agrees"] / decided * 100
        print(
            f"  spatial vs CNEFE code: {share:.2f}% agree "
            f"({stats['code_differs']:,} differ — mesh vintage skew, spatial wins)"
        )

    # ---- distribute each tract's residents across its dwelling coordinates ----
    records: list[tuple[float, float, int, str]] = []
    fallback_tracts = 0
    fallback_residents = 0
    for setor, tract in tracts.items():
        residents = tract["residents"]
        if residents <= 0:
            continue
        coords = units.get(setor)
        if not coords:
            # No addressed dwelling inside the box for this tract — usually rural, or the tract
            # straddles the boundary. Put its people at the tract centroid so none are lost.
            records.append((round(tract["lon"], 6), round(tract["lat"], 6), residents, setor))
            fallback_tracts += 1
            fallback_residents += residents
            continue
        keys = list(coords.keys())
        weights = [coords[k] for k in keys]
        for (lon, lat), share in zip(keys, largest_remainder(residents, weights)):
            if share:
                records.append((lon, lat, share, setor))

    total = sum(r[2] for r in records)
    expected = sum(t["residents"] for t in tracts.values())
    print()
    print(f"  address-level records: {len(records):,}")
    print(f"  residents placed     : {total:,} (expected {expected:,})")
    print(f"  centroid fallback    : {fallback_tracts} tracts, {fallback_residents:,} residents")
    if total != expected:
        print("  ! resident total does not match — aborting rather than shipping a mismatch")
        return 1

    out = cwb.INTERIM / "residents.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lon", "lat", "residents", "setor"])
        writer.writerows(records)
    print(f"  wrote {out.relative_to(cwb.ROOT)} ({out.stat().st_size / 1e6:,.1f} MB)")

    cwb.write_json(
        cwb.INTERIM / "tracts.json",
        {k: v for k, v in tracts.items()},
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
