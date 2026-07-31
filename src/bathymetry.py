"""Depth profiles for inland water, from HydroLAKES.

Curitiba is inland, so the usual bathymetry sources are no help: GEBCO is ocean-only, and none of
IBGE, IPPUC or ANA publish reservoir soundings. A flat class default per water type is the fallback,
and it produces a bathtub — you either can or cannot tunnel under a whole reservoir.

**HydroLAKES** (Messager et al. 2016) covers 1.43 million water bodies globally with a modelled
mean depth, 13 of them inside this extent, and that is enough for two real improvements:

1. **Per-body depth instead of per-class.** The largest body here has `Depth_avg` 8.8 m, another
   11.3 m, another 4.3 m — a single reservoir constant would flatten all of that.
2. **A profile instead of a flat bed.** `Depth_avg` plus a basin-shape assumption gives a maximum
   depth, and successive inward buffers turn that into concentric bands: shallow at the bank,
   deepest in the middle. In play that is the difference that matters, because it makes a crossing
   near the shore feasible and a crossing through the centre not.

`Depth_avg` is a *mean*, so a maximum is derived from it by assuming a basin shape. For a parabolic
basin `V = ½·A·Dmax`, giving `Dmax = 2·Depth_avg`; a cone would give 3×. The parabolic assumption is
the conservative of the two and is what is used here. That step is a model, not a measurement, and
is flagged as such.

The band depth follows `d(r) = Dmax · r^0.5` where `r` is the relative distance from the bank. The
square root makes the sides steep and the middle flat, which is the right shape for a drowned river
valley — which is what Passaúna, Iraí and Piraquara are.

HydroLAKES is CC BY 4.0 and must be cited.
"""

from __future__ import annotations

import struct
from pathlib import Path

# Relative distance from the bank for each band edge, and the depth fraction at that band.
BANDS = [0.18, 0.42, 0.70, 1.00]
# Only band bodies at least this large (m2); smaller ones get a single flat depth. Banding a 2,000 m2
# pond produces four slivers and no useful gameplay distinction.
MIN_BAND_AREA_M2 = 40_000
# Parabolic basin: V = 0.5 * A * Dmax, so Dmax = 2 * Depth_avg. A cone would be 3x.
DMAX_OVER_DAVG = 2.0


def load_hydrolakes(base: Path, bbox: list[float]) -> list[dict]:
    """HydroLAKES pour points inside `bbox`, with their attributes.

    The points distribution is 75 MB against 782 MB for the polygons, and since each water body is
    matched by containment against geometry we already have, the polygons add nothing.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from sources.formats import read_dbf

    shp = base.with_suffix(".shp")
    coords = []
    with shp.open("rb") as handle:
        handle.seek(100)
        while True:
            header = handle.read(8)
            if len(header) < 8:
                break
            _number, word_length = struct.unpack(">II", header)
            body = handle.read(word_length * 2)
            if len(body) < 20:
                continue
            _shape_type, x, y = struct.unpack("<Idd", body[:20])
            coords.append((x, y))

    _count, _fields, records = read_dbf(base.with_suffix(".dbf"))
    west, south, east, north = bbox
    out = []
    for (x, y), record in zip(coords, records):
        if not (west <= x <= east and south <= y <= north):
            continue
        depth_avg = record.get("Depth_avg") or 0
        if depth_avg <= 0:
            continue
        out.append(
            {
                "lon": x,
                "lat": y,
                "hylak_id": record.get("Hylak_id"),
                "name": (record.get("Lake_name") or "").strip() or None,
                "area_km2": record.get("Lake_area") or 0.0,
                "depth_avg": float(depth_avg),
                "depth_max": float(depth_avg) * DMAX_OVER_DAVG,
                "lake_type": record.get("Lake_type"),
            }
        )
    return out


def _inscribed_radius_deg(polygon, ceiling: float) -> float:
    """Largest inward offset that leaves anything, i.e. roughly the distance bank-to-centre."""
    low, high = 0.0, ceiling
    for _ in range(14):
        mid = (low + high) / 2
        if mid <= 0:
            break
        if polygon.buffer(-mid).is_empty:
            high = mid
        else:
            low = mid
    return low


def depth_bands(polygon, depth_max: float, area_m2: float) -> list[tuple[object, float]]:
    """Split a water polygon into concentric bands, each with its own depth.

    Returns `[(geometry, depth_negative_metres), ...]` covering the polygon exactly once. Falls back
    to a single flat band when the body is too small to be worth banding or too thin to buffer.
    """
    if area_m2 < MIN_BAND_AREA_M2:
        return [(polygon, -round(depth_max * 0.5, 2))]

    # Work in degrees; at this latitude 1e-5 deg is about 1 m.
    radius = _inscribed_radius_deg(polygon, ceiling=0.02)
    if radius <= 1e-6:
        return [(polygon, -round(depth_max * 0.5, 2))]

    bands: list[tuple[object, float]] = []
    previous = polygon
    for index, fraction in enumerate(BANDS):
        depth = depth_max * (fraction ** 0.5)
        if index == len(BANDS) - 1:
            ring = previous
        else:
            inner = polygon.buffer(-radius * fraction)
            if inner.is_empty:
                ring = previous
            else:
                ring = previous.difference(inner)
                previous = inner
        for part in _explode(ring):
            bands.append((part, -round(depth, 2)))
        if index < len(BANDS) - 1 and previous.is_empty:
            break
    return bands or [(polygon, -round(depth_max * 0.5, 2))]


def _explode(geometry):
    if geometry is None or geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        for part in geometry.geoms:
            if part.geom_type == "Polygon" and not part.is_empty:
                yield part
