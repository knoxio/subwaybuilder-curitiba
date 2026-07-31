"""Readers for the file formats statistical agencies actually ship.

Census and municipal data arrives as Shapefiles and GeoPackages. Both can be read for
attributes and bounding boxes without GDAL, geopandas or any other wheel that needs compiling:

* a **GeoPackage** is a SQLite database, so `sqlite3` from the standard library opens it, and
  each geometry blob carries its own envelope in a small header;
* a **DBF** (the attribute half of a Shapefile) is a fixed-width binary table with a
  self-describing header.

That keeps a map build runnable on a bare Python install. Full geometry — rings, not just
envelopes — needs a real WKB parser; `shapely.wkb` handles it if you have shapely, and
`gpkg_geometry_wkb` hands you the bytes to feed it.

Encoding note: Brazilian, Portuguese and Spanish agency files are usually latin-1, not UTF-8.
`read_dbf` defaults to latin-1 for that reason; pass `encoding=` when you know better.
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

__all__ = [
    "DbfField",
    "read_dbf",
    "dbf_fields",
    "gpkg_layers",
    "gpkg_layer",
    "gpkg_envelope",
    "gpkg_geometry_wkb",
]


# --------------------------------------------------------------------------- DBF


@dataclass(frozen=True)
class DbfField:
    name: str
    type: str
    length: int
    decimals: int


_DBF_HEADER = struct.Struct("<BBBBIHH")
_TRUTHY = {"Y", "y", "T", "t"}
_FALSY = {"N", "n", "F", "f"}


def dbf_fields(path: str | Path, *, encoding: str = "latin-1") -> tuple[int, list[DbfField]]:
    """Return `(record_count, fields)` without reading any records."""
    with Path(path).open("rb") as handle:
        head = handle.read(32)
        if len(head) < 32:
            raise ValueError(f"{path} is too short to be a DBF")
        _, _, _, _, count, header_len, _ = _DBF_HEADER.unpack_from(head, 0)
        fields = []
        for _ in range((header_len - 33) // 32):
            raw = handle.read(32)
            if not raw or raw[0] == 0x0D:
                break
            name = raw[:11].split(b"\x00")[0].decode(encoding).strip()
            fields.append(DbfField(name, chr(raw[11]), raw[16], raw[17]))
    return count, fields


def read_dbf(
    path: str | Path,
    *,
    encoding: str = "latin-1",
    convert: bool = True,
) -> tuple[int, list[DbfField], Iterator[dict]]:
    """Read a DBF table, returning `(record_count, fields, records)`.

    `records` is a lazy generator of dicts keyed by field name — a shapefile's attribute rows
    come back in the same order as its geometries, so zipping the two is valid.

    With `convert` (the default) numeric, logical and date fields are coerced to `int`/`float`,
    `bool` and `YYYY-MM-DD` strings, and blank values become `None`. Character fields are
    always stripped. Pass `convert=False` to get raw stripped strings throughout, which is
    what you want if a "numeric" column turns out to hold codes with meaningful leading zeros.

    Deleted records (those flagged with `*`) are skipped.
    """
    path = Path(path)
    count, fields = dbf_fields(path, encoding=encoding)
    record_len = 1 + sum(field.length for field in fields)

    with path.open("rb") as handle:
        head = handle.read(32)
        _, _, _, _, _, header_len, declared_len = _DBF_HEADER.unpack_from(head, 0)
    if declared_len and declared_len != record_len:
        record_len = declared_len

    def records() -> Iterator[dict]:
        with path.open("rb") as handle:
            handle.seek(header_len)
            for _ in range(count):
                raw = handle.read(record_len)
                if not raw or raw[:1] in (b"\x1a", b""):
                    break
                if raw[:1] == b"*":
                    continue
                row = {}
                offset = 1
                for field in fields:
                    chunk = raw[offset : offset + field.length]
                    offset += field.length
                    text = chunk.decode(encoding, errors="replace").strip()
                    row[field.name] = _coerce(text, field) if convert else text
                yield row

    return count, fields, records()


def _coerce(text: str, field: DbfField):
    if text == "":
        return None
    kind = field.type
    if kind in ("N", "F", "B", "O"):
        try:
            return int(text) if (kind == "N" and field.decimals == 0) else float(text)
        except ValueError:
            return text
    if kind == "L":
        if text in _TRUTHY:
            return True
        if text in _FALSY:
            return False
        return None
    if kind == "D" and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


# ----------------------------------------------------------------------- GeoPackage

_GPKG_MAGIC = b"GP"
_ENVELOPE_DOUBLES = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}


def gpkg_layers(path: str | Path) -> list[tuple[str, str, str]]:
    """List `(table_name, data_type, geometry_column)` for each layer in a GeoPackage."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT table_name, data_type FROM gpkg_contents").fetchall()
        geom = dict(con.execute("SELECT table_name, column_name FROM gpkg_geometry_columns"))
        return [(name, kind, geom.get(name, "")) for name, kind in rows]
    finally:
        con.close()


def gpkg_envelope(blob: bytes | None) -> tuple[float, float, float, float] | None:
    """Bounding box `(min_lon, min_lat, max_lon, max_lat)` from a GeoPackage geometry blob.

    Returns `None` when the blob is absent or carries no envelope, which is legal — an
    envelope is optional in the spec and writers omit it for points.

    The header stores the envelope as `min_x, max_x, min_y, max_y` — x-range then y-range, not
    the interleaved order a bbox is usually written in. Getting that wrong silently produces
    boxes that look plausible and are wrong, so this function exists to be the only place that
    has to remember.
    """
    if not blob or len(blob) < 8 or blob[:2] != _GPKG_MAGIC:
        return None
    flags = blob[3]
    doubles = _ENVELOPE_DOUBLES.get((flags >> 1) & 0x07, 0)
    if doubles == 0 or len(blob) < 8 + doubles * 8:
        return None
    # Flags bit 0 is the byte order of the header and envelope: 1 little, 0 big.
    order = "<" if flags & 0x01 else ">"
    min_x, max_x, min_y, max_y = struct.unpack_from(order + "4d", blob, 8)
    return min_x, min_y, max_x, max_y


def gpkg_geometry_wkb(blob: bytes | None) -> bytes | None:
    """Strip the GeoPackage header and return the bare WKB, for `shapely.wkb.loads`."""
    if not blob or len(blob) < 8 or blob[:2] != _GPKG_MAGIC:
        return None
    flags = blob[3]
    doubles = _ENVELOPE_DOUBLES.get((flags >> 1) & 0x07, 0)
    return blob[8 + doubles * 8 :]


def gpkg_layer(
    path: str | Path,
    layer: str | None = None,
    *,
    where: str | None = None,
    columns: list[str] | None = None,
    geometry: bool = False,
) -> Iterator[tuple[dict, tuple[float, float, float, float] | None]]:
    """Iterate a GeoPackage layer as `(attributes, envelope)` pairs.

    `layer` defaults to the file's only layer, erroring if there is more than one. `where` is
    raw SQL appended as a WHERE clause — this is a local read-only file, but do not build it
    from untrusted input. `columns` restricts the attributes fetched, which is worth doing on a
    wide census table. With `geometry=True` each attribute dict gains a `_wkb` key holding the
    bare WKB for the row.

    Rows stream from SQLite, so this is safe on files far larger than memory.
    """
    path = Path(path)
    available = gpkg_layers(path)
    if layer is None:
        features = [entry for entry in available if entry[1] == "features"] or available
        if len(features) != 1:
            names = ", ".join(name for name, _, _ in available)
            raise ValueError(f"{path.name} has multiple layers ({names}); pass layer=")
        layer = features[0][0]

    geom_column = next((geom for name, _, geom in available if name == layer), "")
    if not geom_column:
        raise ValueError(f"{path.name} has no geometry column for layer {layer!r}")

    if columns is None:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            info = con.execute(f'PRAGMA table_info("{layer}")').fetchall()
        finally:
            con.close()
        columns = [row[1] for row in info if row[1] != geom_column]

    selected = ", ".join(f'"{name}"' for name in columns)
    sql = f'SELECT "{geom_column}"{", " + selected if selected else ""} FROM "{layer}"'
    if where:
        sql += f" WHERE {where}"

    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for row in con.execute(sql):
            blob = row[0]
            attrs = dict(zip(columns, row[1:]))
            if geometry:
                attrs["_wkb"] = gpkg_geometry_wkb(blob)
            yield attrs, gpkg_envelope(blob)
    finally:
        con.close()
