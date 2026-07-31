# tools/

`sources/fetch.py` and `sources/formats.py` are part of this project.

Three further helpers used by the pipeline are **not redistributed here**, because they were
written by another author in the shared workspace this map was built in and their consent to
republish has not been given:

| File | Used by | What it does |
| --- | --- | --- |
| `sources/demand.py` | `src/step4_points.py` | `aggregate_to_grid`, `summarise`, `to_demand_points`, `merge_within` |
| `buildings_index.py` | `src/step7_geodata.py` | Reads/writes Subway Builder's packed binary buildings index |
| `check_map_pack.py` | validation | Lints a map pack against the game's and the registry's requirements |

Every other step runs without them. `step4_points.py` and `step7_geodata.py` will raise an
`ImportError` naming the missing file.

The published release artifacts (`CWB.zip`, `manifest.json`) are complete and do not depend on
any of this — the source tree is here for reproducibility, not to build the release from scratch.
