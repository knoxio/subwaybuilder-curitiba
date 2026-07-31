"""Install the built pack into the game the way Railyard installs a local import.

Railyard normally does this itself. Doing it by hand is useful when the pack has not been
published yet, and it is what Railyard's own `isLocal` install path produces:

    ~/Library/Application Support/metro-maker4/cities/data/CWB/
        .railyard_asset                     zero-byte marker: Railyard owns this folder
        config.json                         verbatim from the pack
        buildings_index.{bin,json}.gz       gzipped
        demand_data.json.gz
        roads.geojson.gz
        runways_taxiways.geojson.gz
        .railyard_map/special_demand_points.json
    ~/Library/Application Support/railyard/tiles/CWB.pmtiles
    ~/Library/Application Support/railyard/installed_maps.json   <- entry appended

The state entry matters: Railyard builds the game's loader mod from `GetInstalledMaps()`, which
reads that file — **not** from scanning the filesystem. Files alone would sit there unregistered
and the city would never appear in the game.

**Railyard must not be running.** It holds this state in memory and would overwrite the entry on
exit. The script refuses to run otherwise.

    python3 src/install_local.py [--uninstall]
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import cwb

GAME_DATA = Path.home() / "Library/Application Support/metro-maker4/cities/data"
RAILYARD = Path.home() / "Library/Application Support/railyard"
# For a LOCAL install the registry id must equal the city code. Railyard's bootstrap does
# `cityCode := mapID` for local maps and then looks for the install folder and the asset marker at
# `<maps root>/<cityCode>`, so an id of "curitiba" makes it hunt for cities/data/curitiba/, fail
# validation, and silently drop the entry on next startup — the files stay on disk and the city
# never appears. Registry-installed maps are different: they carry a manifest that supplies the
# city code separately, which is why published ids like "jelegend-tokyo" work.
MAP_ID = cwb.CODE

GZIP_FILES = [
    "buildings_index.bin",
    "buildings_index.json",
    "demand_data.json",
    "roads.geojson",
    "runways_taxiways.geojson",
    # Presence of ocean_depth_index.json.gz is what makes Railyard set hasOceanDepth and turn the
    # oceanFoundations layer on by default — without it, water is unconstrained.
    "ocean_depth_index.json",
]


def railyard_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "/Applications/railyard.app"], capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def load_state() -> list:
    path = RAILYARD / "installed_maps.json"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def subscribe(version: str | None) -> None:
    """Add (or remove) the map in the active profile's `subscriptions.localMaps`.

    This is the list Railyard actually iterates at startup. `installed_maps.json` only supplies
    config for maps that are already subscribed — an entry there alone is ignored and silently
    disappears on the next launch, with the data files left sitting on disk.

    Only `subscriptions.localMaps` is touched; the rest of the profile (including credentials
    stored in the same tree) is read and written back unchanged.
    """
    path = RAILYARD / "user_profiles.json"
    if not path.exists():
        print(f"  ! {path} not found — cannot subscribe")
        return
    backup = path.with_suffix(".json.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    with path.open(encoding="utf-8") as handle:
        profiles = json.load(handle)
    active = profiles.get("activeProfileId") or "__default__"
    profile = (profiles.get("profiles") or {}).get(active)
    if profile is None:
        print(f"  ! active profile {active!r} not found")
        return
    local_maps = profile.setdefault("subscriptions", {}).setdefault("localMaps", {})
    if version is None:
        local_maps.pop(MAP_ID, None)
        print(f"  unsubscribed {MAP_ID} from profile {active!r}")
    else:
        local_maps[MAP_ID] = version
        print(f"  subscribed {MAP_ID} {version} in profile {active!r} (localMaps)")
    with path.open("w", encoding="utf-8") as handle:
        json.dump(profiles, handle, indent=2, ensure_ascii=False)


def save_state(entries: list) -> None:
    path = RAILYARD / "installed_maps.json"
    backup = path.with_suffix(".json.bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2, ensure_ascii=False)


def uninstall() -> int:
    target = GAME_DATA / cwb.CODE
    if target.exists():
        if not (target / ".railyard_asset").exists():
            print(f"  ! {target} has no .railyard_asset marker — refusing to delete, it may be official")
            return 1
        shutil.rmtree(target)
        print(f"  removed {target}")
    tiles = RAILYARD / "tiles" / f"{cwb.CODE}.pmtiles"
    if tiles.exists():
        tiles.unlink()
        print(f"  removed {tiles}")
    entries = [e for e in load_state() if e.get("id") != MAP_ID]
    save_state(entries)
    subscribe(None)
    print(f"  registry entries now: {len(entries)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    cwb.banner(f"Install {cwb.CODE} locally")

    if railyard_running():
        print("  ! Railyard is running. Quit it first — it would overwrite the registry entry.")
        return 1
    if not GAME_DATA.parent.exists():
        print(f"  ! game data directory not found at {GAME_DATA}")
        return 1

    if args.uninstall:
        return uninstall()

    config = cwb.read_json(cwb.OUT / "config.json")
    target = GAME_DATA / cwb.CODE
    target.mkdir(parents=True, exist_ok=True)
    (target / ".railyard_asset").write_bytes(b"")

    shutil.copy2(cwb.OUT / "config.json", target / "config.json")
    print(f"  config.json -> {target.name}/")

    for name in GZIP_FILES:
        source = cwb.OUT / name
        if not source.exists():
            print(f"  ! missing {name}")
            return 1
        destination = target / f"{name}.gz"
        with source.open("rb") as raw, gzip.open(destination, "wb", compresslevel=6) as out:
            shutil.copyfileobj(raw, out, length=1 << 22)
        print(f"  {name} -> {destination.name}  ({destination.stat().st_size / 1e6:,.1f} MB)")

    content = cwb.OUT / "special_demand_points.json"
    if content.exists():
        (target / ".railyard_map").mkdir(exist_ok=True)
        shutil.copy2(content, target / ".railyard_map" / "special_demand_points.json")
        print("  special_demand_points.json -> .railyard_map/")

    tiles_dir = RAILYARD / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cwb.OUT / f"{cwb.CODE}.pmtiles", tiles_dir / f"{cwb.CODE}.pmtiles")
    print(f"  {cwb.CODE}.pmtiles -> railyard/tiles/  ({(tiles_dir / f'{cwb.CODE}.pmtiles').stat().st_size / 1e6:,.1f} MB)")

    entries = [e for e in load_state() if e.get("id") != MAP_ID]
    entries.append(
        {
            "id": MAP_ID,
            "version": config["version"],
            "isLocal": True,
            "config": config,
        }
    )
    save_state(entries)
    print(f"  registered in installed_maps.json as isLocal (entries: {len(entries)})")
    subscribe(config["version"])

    print()
    print("  Installed. Launch the game through Railyard — it writes the loader mod on launch,")
    print("  and Curitiba should appear in the city selector.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
