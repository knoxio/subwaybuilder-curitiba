"""Reusable open-data source helpers.

Standard library only — no requests, geopandas or GDAL. Map building already needs a heavy CLI
toolchain and adding a Python geo stack on top makes these helpers unusable where they are most
useful.
"""

from .fetch import CacheError, cache_dir, fetch, ftp_list, ssl_context, unpack
from .formats import (
    DbfField,
    dbf_fields,
    gpkg_envelope,
    gpkg_geometry_wkb,
    gpkg_layer,
    gpkg_layers,
    read_dbf,
)

__all__ = [
    "CacheError",
    "cache_dir",
    "fetch",
    "ftp_list",
    "ssl_context",
    "unpack",
    "DbfField",
    "dbf_fields",
    "gpkg_envelope",
    "gpkg_geometry_wkb",
    "gpkg_layer",
    "gpkg_layers",
    "read_dbf",
]
