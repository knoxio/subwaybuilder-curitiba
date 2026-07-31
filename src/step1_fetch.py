"""Step 1 — fetch every source the CWB build needs.

Idempotent: cached files are left alone, so re-running is cheap. The SIDRA queries are cached
to `data/raw/sidra/` as JSON because they are per-municipality and the multi-locality form of
the API returns HTTP 500 on these tables.

    python3 src/step1_fetch.py [--skip-cnefe] [--skip-ippuc] [--refresh]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

import cwb
from sources.fetch import fetch, ssl_context

UA = {"User-Agent": "SubwayBuilder-MapTools/1.0 (+https://github.com/Subway-Builder-Modded)"}


def sidra_get(url: str, cache_path, *, refresh: bool = False, tries: int = 3):
    """GET a SIDRA URL with retries, caching the parsed JSON on disk."""
    if cache_path.exists() and not refresh:
        return cwb.read_json(cache_path)
    last = None
    for attempt in range(1, tries + 1):
        try:
            payload = cwb.http_json(url)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            return payload
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
            last = error
            if attempt < tries:
                time.sleep(2 * attempt)
    print(f"    ! failed after {tries} tries: {last}")
    return None


def fetch_census(refresh: bool) -> None:
    cwb.banner("Census tract mesh with attributes (IBGE Censo 2022)")
    fetch(cwb.CENSUS_MESH_URL, cwb.census_gpkg(), refresh=refresh)


def fetch_cnefe(refresh: bool) -> None:
    cwb.banner(f"CNEFE 2022 addresses — {len(cwb.MUNICIPALITIES)} municipalities")
    # Filenames are <IBGE7>_<UPPERCASE_NAME>.zip with accents stripped; list the directory over
    # FTP once and match on the code prefix rather than trying to reproduce the transliteration.
    from sources.fetch import ftp_list

    listing_cache = cwb.RAW / "cnefe" / "_listing.json"
    if listing_cache.exists() and not refresh:
        names = cwb.read_json(listing_cache)
    else:
        ftp_url = cwb.CNEFE_BASE.replace(cwb.IBGE_HTTP, cwb.IBGE_FTP) + "/"
        names = [n.rsplit("/", 1)[-1] for n in ftp_list(ftp_url)]
        cwb.write_json(listing_cache, names)
    by_code = {n.split("_", 1)[0]: n for n in names if n.endswith(".zip")}

    missing = []
    for code, name in cwb.MUNICIPALITIES:
        remote = by_code.get(code)
        if not remote:
            missing.append((code, name))
            continue
        print(f"  {name} ({code})")
        fetch(f"{cwb.CNEFE_BASE}/{remote}", cwb.cnefe_zip(code), refresh=refresh, quiet=False)
    if missing:
        print(f"  ! no CNEFE file found for: {missing}")


def fetch_cempre(refresh: bool) -> dict:
    cwb.banner(f"CEMPRE jobs (table {cwb.CEMPRE_TABLE}, {cwb.CEMPRE_YEAR})")
    out = {}
    for code, name in cwb.MUNICIPALITIES:
        url = (
            f"{cwb.SIDRA}/{cwb.CEMPRE_TABLE}/periodos/{cwb.CEMPRE_YEAR}"
            f"/variaveis/{cwb.CEMPRE_JOBS_VAR}?localidades=N6%5B{code}%5D"
        )
        payload = sidra_get(url, cwb.RAW / "sidra" / f"cempre_{code}.json", refresh=refresh)
        jobs = None
        if payload:
            try:
                serie = payload[0]["resultados"][0]["series"][0]["serie"]
                jobs = cwb.sidra_value(next(iter(serie.values())))
            except (KeyError, IndexError, StopIteration):
                jobs = None
        out[code] = jobs
        print(f"  {name:<24} {jobs if jobs is not None else 'unavailable':>12}")
    known = {k: v for k, v in out.items() if v}
    print(f"  total jobs across {len(known)}/{len(out)} municipalities: {sum(known.values()):,}")
    cwb.write_json(cwb.INTERIM / "cempre_jobs.json", out, indent=2)
    return out


def fetch_commute(refresh: bool) -> dict:
    """Commute-time bands, mode share and workplace location, per municipality."""
    cwb.banner(f"Commute calibration (table {cwb.COMMUTE_TABLE}, {cwb.COMMUTE_YEAR})")
    axes = {
        "time": (cwb.CLS_TIME, [cwb.CLS_MODE, cwb.CLS_RACE, cwb.CLS_WORKPLACE]),
        "mode": (cwb.CLS_MODE, [cwb.CLS_TIME, cwb.CLS_RACE, cwb.CLS_WORKPLACE]),
        "workplace": (cwb.CLS_WORKPLACE, [cwb.CLS_TIME, cwb.CLS_MODE, cwb.CLS_RACE]),
    }
    out: dict[str, dict] = {}
    for code, name in cwb.MUNICIPALITIES:
        out[code] = {}
        for axis, (varying, fixed) in axes.items():
            parts = [f"{varying}%5Ball%5D"] + [f"{c}%5B{cwb.CAT_TOTAL[c]}%5D" for c in fixed]
            url = (
                f"{cwb.SIDRA}/{cwb.COMMUTE_TABLE}/periodos/{cwb.COMMUTE_YEAR}"
                f"/variaveis/{cwb.COMMUTE_VAR}?localidades=N6%5B{code}%5D"
                f"&classificacao={'%7C'.join(parts)}"
            )
            payload = sidra_get(
                url, cwb.RAW / "sidra" / f"commute_{axis}_{code}.json", refresh=refresh
            )
            out[code][axis] = _flatten_commute(payload, varying)
        total = (out[code]["time"] or {}).get("Total")
        print(f"  {name:<24} out-of-home commuters: {total if total else 'n/a':>10}")
    cwb.write_json(cwb.INTERIM / "commute.json", out, indent=2)
    return out


def _flatten_commute(payload, varying: str) -> dict:
    """Reduce a SIDRA v3 response to {category_name: value} for the varying classification."""
    if not payload or isinstance(payload, dict):
        return {}
    result: dict[str, int | None] = {}
    for variable in payload:
        for block in variable.get("resultados", []):
            label = None
            for classification in block.get("classificacoes", []):
                if classification.get("id") == varying:
                    label = next(iter(classification["categoria"].values()))
            if label is None:
                continue
            for series in block.get("series", []):
                value = next(iter(series["serie"].values()))
                result[label] = cwb.sidra_value(value)
    return result


def fetch_ippuc(refresh: bool) -> None:
    cwb.banner(f"IPPUC municipal geodata — {len(cwb.IPPUC_LAYERS)} layers")
    for layer in cwb.IPPUC_LAYERS:
        try:
            fetch(
                f"{cwb.IPPUC_SHAPES}/{layer}_SIRGAS.zip",
                cwb.RAW / "ippuc" / f"{layer}.zip",
                refresh=refresh,
            )
        except Exception as error:  # noqa: BLE001 — one dead layer must not stop the build
            print(f"  ! {layer}: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-cnefe", action="store_true")
    parser.add_argument("--skip-ippuc", action="store_true")
    parser.add_argument("--refresh", action="store_true", help="ignore cached copies")
    args = parser.parse_args()

    fetch_census(args.refresh)
    if not args.skip_cnefe:
        fetch_cnefe(args.refresh)
    fetch_cempre(args.refresh)
    fetch_commute(args.refresh)
    if not args.skip_ippuc:
        fetch_ippuc(args.refresh)

    cwb.banner("Step 1 complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
