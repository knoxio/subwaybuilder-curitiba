"""Step 7b — pull building footprints from Overture instead of OSM.

OSM's building coverage in Brazil is thin: 87,133 footprints inside this extent against
1,323,335 in Overture, a factor of 15. The census counts 1,389,625 households here, so Overture
is the only source in the right order of magnitude — with OSM alone the collision layer and the
3D extrusions would be almost empty outside the centre of Curitiba.

The trade-off is attributes. Overture's Brazilian footprints are largely ML-derived (Google Open
Buildings, Microsoft) and carry almost no height: 0.1% have `height`, 0.3% have `num_floors`. OSM
is the opposite — few buildings, better tagged. So geometry comes from Overture and heights are
taken from OSM where a footprint overlaps one.

    python3 src/step7b_overture.py [--release 2026-07-22.0]
"""

from __future__ import annotations

import argparse
import sys

import cwb

RELEASE = "2026-07-22.0"
S3 = "s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", default=RELEASE)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    cwb.banner(f"Step 7b — Overture buildings ({args.release})")
    out = cwb.INTERIM / "overture_buildings.parquet"
    if out.exists() and not args.refresh:
        print(f"  cached {out.name} ({out.stat().st_size / 1e6:,.1f} MB)")
    else:
        import duckdb

        con = duckdb.connect()
        con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_region='us-west-2';")
        west, south, east, north = cwb.BBOX
        source = S3.format(release=args.release)
        # Filter on the bbox struct first — it is a top-level column, so the predicate prunes row
        # groups before any geometry is decoded. Filtering on the geometry instead reads everything.
        print("  querying Overture (this reads remote parquet, expect a few minutes)")
        con.execute(
            f"""
            COPY (
                SELECT
                    -- In the 2026 releases `geometry` is already a GEOMETRY column, not a WKB
                    -- BLOB, so wrapping it in ST_GeomFromWKB is a binder error.
                    ST_AsWKB(geometry) AS wkb,
                    bbox.xmin AS xmin, bbox.ymin AS ymin, bbox.xmax AS xmax, bbox.ymax AS ymax,
                    height,
                    num_floors,
                    num_floors_underground,
                    subtype,
                    class
                FROM read_parquet('{source}', filename=false, hive_partitioning=1)
                WHERE bbox.xmin BETWEEN {west} AND {east}
                  AND bbox.ymin BETWEEN {south} AND {north}
            ) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        print(f"  wrote {out.name} ({out.stat().st_size / 1e6:,.1f} MB)")

    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    stats = con.execute(
        f"""
        SELECT count(*), count(height), count(num_floors), count(num_floors_underground)
        FROM read_parquet('{out}')
        """
    ).fetchone()
    print(f"  buildings {stats[0]:,}  height {stats[1]:,}  floors {stats[2]:,}  underground {stats[3]:,}")
    classes = con.execute(
        f"SELECT coalesce(class,'(none)') c, count(*) n FROM read_parquet('{out}') GROUP BY 1 ORDER BY n DESC LIMIT 12"
    ).fetchall()
    print("  top classes:")
    for name, count in classes:
        print(f"    {name:<24} {count:>9,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
