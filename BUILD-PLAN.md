# CWB build plan

**Built.** The pipeline below has been run end to end and the pack validates with zero
blockers; see [README.md](README.md) for the resulting numbers. What follows is the plan as
designed, with the deviations that turned up during the build marked **[changed]**.

Pipeline for the Curitiba map pack. Format rules come from [`../docs/`](../docs/); Brazilian
source detail from [`../docs/09-brazil-data-sources.md`](../docs/09-brazil-data-sources.md).
This document records the decisions specific to this city.

## Extent

Four candidate boxes were tested against tract centroids from the census mesh. Capture is the
share of the 3,382,210 people in IBGE's Curitiba *concentração urbana*:

| | Box | Size | Area | Tracts | Residents | Capture |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | `-49.50, -25.70, -49.05, -25.20` | 45 × 55 km | 2,498 km² | 5,363 | 3,135,399 | 92.7% |
| **T2** | **`-49.55, -25.72, -48.95, -25.15`** | **60 × 63 km** | **3,797 km²** | **5,623** | **3,271,037** | **96.7%** |
| T3 | `-49.65, -25.78, -48.85, -25.05` | 80 × 81 km | 6,484 km² | 5,815 | 3,348,195 | 99.0% |
| T4 | `-49.70, -25.82, -48.80, -25.00` | 90 × 91 km | 8,194 km² | 5,853 | 3,362,485 | 99.4% |

**Decision: T2.** It captures 96.7% of the conurbation across 17 municipalities for 3,797 km²,
which sits between Amsterdam (1,775 km²) and Dubai (5,167 km²) — both shipping maps, so the
tile and index sizes are known-survivable. T3 and T4 buy 2.3 and 2.7 further percentage points
for 1.7× and 2.2× the area, almost all of it Serra do Mar forest and the rural west; the
registry's detail score is per playable area, so empty hectares actively cost you.

```json
"bbox": [-49.55, -25.72, -48.95, -25.15],
"initialViewState": { "latitude": -25.4284, "longitude": -49.2733, "zoom": 11, "bearing": 0 }
```

`code` `CWB` (the IATA code for Afonso Pena, and unused in the registry). `country` `BR`.

Municipalities inside T2: Curitiba, São José dos Pinhais, Colombo, Araucária, Fazenda Rio
Grande, Campo Largo, Pinhais, Almirante Tamandaré, Piraquara, Campina Grande do Sul, Rio
Branco do Sul, Itaperuçu, Campo Magro, Mandirituba, Quatro Barras, Contenda, Bocaiúva do Sul.

## Inputs

| # | Source | File | Size |
| --- | --- | --- | --- |
| 1 | Census tracts + population | `PR_setores_CD2022.gpkg` | 54 MB |
| 2 | Addresses | CNEFE per municipality, ×17 | 24 MB for Curitiba alone |
| 3 | Jobs total | CEMPRE table 9509 var 707, ×17 | API |
| 4 | Commute calibration | Censo table 10330 var 13376, ×17 | API |
| 5 | Zoning, bairros, parks, water, terminals, schools, hospitals | IPPUC SIRGAS shapefiles | <1 MB each |
| 6 | Buildings, roads, aeroways, labels | OSM/Overture — Geofabrik `sul-latest.osm.pbf` | ~700 MB |

1, 2 and 5 are already downloaded and verified under this session's scratch directory; 3 and 4
were queried live. Only 6 is untouched.

## Steps

### 1 · Residents to addresses

Read tracts inside T2 (`gpkg_layer`, `columns=['CD_SETOR','v0001','CD_SIT','CD_TIPO','NM_MUN','NM_BAIRRO']`).
Read CNEFE for each municipality; keep `COD_ESPECIE` 1 and 2 as dwellings. Distribute each
tract's `v0001` across its dwellings, joined on `COD_SETOR`, weighted by building footprint
area where an OSM/Overture footprint contains the address point and uniformly otherwise.

Exclude non-commuting tracts before pairing: `CD_TIPO` `2` (barracks), `6` (prison), `7`
(hospital/care institution). Their residents are real and belong in `config.json`'s
`population`, but they do not commute, and left in they generate pops travelling to work from
a jail.

Read `CD_SETOR` with `convert=False` — it is a zero-padded geocode and integer coercion breaks
the join.

### 2 · Jobs to establishments

Keep CNEFE `COD_ESPECIE` 4, 5, 6, 8 as workplace candidates. Drop `COD_ESPECIE` 7 (under
construction) and the junk `DSC_ESTABELECIMENTO` values — `VAGO`, `SEM NOME`, `LOJA VAGA`,
`VAZIO`, `ESTABELECIMENTO VAGO`, `SEM IDENTIFICACAO` (3,206 `VAGO` alone in Curitiba).

Classify the remainder by keyword against `DSC_ESTABELECIMENTO` into rough employment
densities — office/professional, retail, food service, workshop/industrial, warehouse,
education, health, religious — and scale so each municipality's total matches its CEMPRE 707
figure. Curitiba's is 1,262,165 (2024).

Weight by footprint area × floors where available; a 40-storey tower in the Centro should not
receive the same job count as a corner bakery holding the same address species.

### 3 · Demand points

Aggregate to points. The registry's detail score rewards tight nearest-neighbour spacing, so
merge conservatively — Amsterdam ships 373 points for 821 k modelled residents and scores
0.148.

**Use `aggregate_to_grid` for thinning, not `merge_within`.** They are different operations
and the shared helper deliberately separates them:

* `merge_within(points, m)` is *de-duplication* by leader clustering — the same stadium
  arriving from two sources, a campus split across parcels. Cluster diameter is bounded at
  2 m so it cannot chain.
* `aggregate_to_grid(points, m)` is *thinning* — a fixed grid snap, one output point per cell.
  Cell membership depends only on a point's own coordinates, so it is deterministic,
  order-independent, and cannot chain.

The distinction matters more here than almost anywhere. CNEFE addresses sit roughly 10–20 m
apart, so any single-link ("join a cluster if it holds a neighbour within m") implementation
chains the entire city into a handful of points at any threshold you would naturally reach
for — and residents and jobs stay perfectly conserved while it happens, so every total you
would think to check still balances. Verify point *counts* and cluster diameters, not just
sums.

Measured on the 5,623 T2 tracts (3,271,037 residents), residents conserved exactly throughout:

| Operation | Points out |
| --- | --- |
| `to_demand_points` | 5,591 (32 dropped as empty — the phantom-point guard) |
| `aggregate_to_grid(200 m)` | 5,133 |
| `aggregate_to_grid(400 m)` | 3,536 |
| `aggregate_to_grid(800 m)` | 1,635 |

**Err fine, not coarse.** The detail score rewards tight nearest-neighbour spacing, so
aggregating hard to keep the file small is the wrong instinct — it trades score for bytes.
Comparative measurements from the Sydney build: the existing shipped Sydney map has 9,687
points at a resident-weighted median spacing of 295 m and scores 0.724, while a 250 m grid
over ABS mesh blocks reaches 180 m spacing at a *similar* point count. Amsterdam's 0.148 at
373 points shows how much of that score is point count.

Starting target: `aggregate_to_grid` at **200 m** (5,133 points from tracts), and finer once
step 1 has pushed residents down to addresses — grid the addresses, not the tract centroids,
so the output reflects where people actually live rather than where a centroid happens to
fall. Treat pathfinding cost and pack size as the constraints that pull the threshold back up,
not as reasons to start coarse. Check the spacing percentiles against a shipped map before
committing.

`points[].residents` must be the **working-age commuting subset**, not total population,
because `sum(points[].residents)` has to equal `sum(pops[].size)` exactly. Derive the ratio
from table 10330's 716,001 out-of-home commuters against Curitiba's 1,773,718 residents
(≈40%), computed per municipality rather than applied globally. Keep the true census figure
for `config.json`'s `population`.

Every point needs `residents > 0 or jobs > 0` — the registry fails a release for a single
phantom point.

### 4 · Home→work pairing

Doubly-constrained gravity model with IPF, following the approach documented for the Dubai map
(see [`../docs/04-demand-data.md`](../docs/04-demand-data.md)). Two calibration targets, both
official, both per municipality:

* **Table 10330 classification 537** — commute-time bands. Fit the deterrence function to
  these rather than inventing a distance floor: 6.5% ≤5 min, 21.2% 6–15, 33.7% 15–30, 27.2%
  30–60, 10.0% 1–2 h. Compare against a mode-weighted travel time, not `drivingSeconds` alone,
  since the census figure is reported time across all modes.
* **Table 10330 classification 469** — intermunicipal split. 94.4% of Curitiba residents work
  inside Curitiba, 5.6% outside. Bound cross-boundary flows to match, per municipality; the
  ring municipalities will show the opposite sign, sending large shares into Curitiba.

Curitiba's 1.26 M jobs against 1.77 M residents means the city is a net importer of commuters
from the ring. Do not let the model quietly balance that away — the asymmetry is the reason
the network is interesting.

### 5 · Routing

OSRM in Docker over `sul-latest.osm.pbf`, per
[`../docs/04-demand-data.md`](../docs/04-demand-data.md), for `drivingSeconds` and
`drivingDistance`. Decide on `drivingPath` after measuring: it multiplies demand file size
several times over, and the payoff is a road-following line in the pop-details panel.

### 6 · Tiles

depot's `MapGen` over the same PBF, then override the two things depot gets wrong for this
game (both noted in [`../docs/03-pmtiles-layers.md`](../docs/03-pmtiles-layers.md)):

* depot emits `college`/`university` as their own layers, which the vanilla style does not
  read. The 1.5 district tint needs a **`commercial`** source-layer with a `type` property of
  `commercial` or `college`.
* Build that layer from **IPPUC `ZONEAMENTO`** rather than OSM landuse — legally-defined land
  use instead of mapper tagging. `ZONA CENTRAL`, `ZUM-1/2/3`, `ZS-1`, `ZS-2` and the
  *eixos* (`EE`, `EMF`, `ENC`, `ECO-*`, `ECL-*`, `EAC`) → `type: commercial`; `ZE`
  (*zona educacional*) → `type: college`; `ZI` → `industrial`; `ZR*`/`SEHIS` → `residential`;
  `UC` and `PARQUES E BOSQUES` → `landuse` `kind: park`. Keep polygons disjoint, campus wins
  over commercial on overlap.

Zoning covers the Curitiba municipality only; fall back to OSM landuse for the other 16.

Labels: `neighborhood_labels` from IPPUC `DIVISA_DE_BAIRROS` (75 official bairros, better than
OSM's coverage), `city_labels` from the 17 municipality names, `suburb_labels` from OSM
`place=suburb` in the ring municipalities. Spelling is **`neighborhood_labels`**, US — the
British spelling renders nothing, silently.

Water from IPPUC `HIDRO_RIOS_PG` / `HIDRO_LAGOS_LAGOAS_REPRESAS` / `HIDRO_AREA_UMIDA` inside
Curitiba, OSM elsewhere. Curitiba is inland with no coast, so `ocean_foundations` and
`ocean_depth_index.json` are not applicable — but the Passaúna and Iraí reservoirs are large
enough that unrestricted tunnelling under them is worth a second look.

### 7 · Buildings index

depot `process_buildings`, or Overture direct. Ship **both** `buildings_index.bin` and
`buildings_index.json` so the pack installs on 1.3.x and 1.4+ alike — a `.bin`-only pack is
constrained to `>1.3.0`, and a `.json`-only pack is skipped entirely by Railyard on current
builds ([`../docs/06-railyard-and-registry.md`](../docs/06-railyard-and-registry.md)).

Verify with `python3 ../tools/buildings_index.py buildings_index.bin`, and if
`total_cell_refs <= building_count`, rebuild with `--reindex` — that means buildings straddling
a cell boundary were registered in one cell only and collision detection misses them from the
neighbouring cell.

Expect a large index. Amsterdam's 609,587 buildings make a 111 MB `.bin` and a 159 MB `.json`;
Greater Curitiba at ~1 M buildings implies a pack in AMS's 226 MB class. Tune
`building_index_filter_size` if that proves unwieldy.

### 8 · Special demand

Candidates, all with coordinates already available:

| Type | Source | Notes |
| --- | --- | --- |
| Airport | Afonso Pena (CWB), Bacacheri | Passenger figures needed |
| Universities | UFPR (Reitoria, Politécnico, Jardim Botânico), PUCPR, UTFPR, Positivo, Tuiuti | `students`, `perc_oncampus` |
| Stadiums | Arena da Baixada (Athletico), Couto Pereira (Coritiba), Vila Capanema | Capacities are published |
| Parks | Barigui, Tanguá, Tingui, Jardim Botânico, Passeio Público, Bosque do Papa | IPPUC `PARQUES_E_BOSQUES` |
| Hospitals | IPPUC `HOSPITAL`, `UNIDADE_DE_SAUDE`, `UPA` | Beds where published |
| Shopping | Shopping Curitiba, Palladium, Pátio Batel, ParkShoppingBarigüi, Estação | — |
| Bus terminals | IPPUC `TERMINAL_DE_TRANSPORTE`, 24 records | Existing BRT interchange demand |
| Industry | CIC (Cidade Industrial de Curitiba), Araucária refinery (REPAR) | Large single-site employers |

Use depot's `DemandData.add_points` with the type vocabulary from
`foundry/schemas/special_demand_types.json` — `airport`, `university`, `sports_facility`,
`park`, `hospital`, `shopping_center`, `military_base`. Declare the categories in the registry
manifest's `special_demand` so they become searchable tags.

The 24 bus terminals are the interesting case: they are where surface demand already
concentrates, so they are the natural competitor to a metro station and worth representing
even though no vanilla category fits exactly.

### 9 · Validate and publish

```bash
python3 ../tools/check_map_pack.py CWB.zip --tag 1.0.0
```

Zero blockers required. Then the checklist in
[`../docs/06-railyard-and-registry.md`](../docs/06-railyard-and-registry.md): `config.json`
`version` equals the tag, every file at the zip's top level with no wrapper directory,
`CWB.pmtiles` matching `code`.

Registry submission: `country: "BR"`, `source_quality: "high-quality"`,
`level_of_detail: "high-detail"`, `data_source: "IBGE/IPPUC"`, and `search_aliases` for
Curitiba (`クリチバ`, `库里蒂巴`, `쿠리치바`).

## Open questions

1. **Ring-municipality zoning.** Do São José dos Pinhais, Colombo, Pinhais or Araucária
   publish zoning shapefiles? COMEC (the metropolitan coordination body) may hold a
   metropolitan land-use layer that would cover all 17 at once.
2. **IPPUC POD.** An origin–destination survey for Curitiba and the metropolitan region has
   been run. If a zone-level matrix is published it replaces the gravity model with observed
   flows — a material upgrade. Not located yet; worth asking IPPUC directly.
3. **Reverse-commute mode share.** Table 10330 was pulled for Curitiba only. The ring
   municipalities need their own figures, and their bus shares will be higher.
4. **Building floors.** How much of the Curitiba OSM/Overture extract carries
   `building:levels`? It drives both job weighting and the tile extrusion heights.
5. **`drivingPath` size.** Measure the demand file with and without before deciding.
6. **Reservoir tunnelling.** Passaúna and Iraí are drinking-water reservoirs. Whether to
   restrict building under them without a coastline to hang `ocean_foundations` on.
7. **GTFS.** URBS publishes the network through Curitiba's CKAN portal. Not needed for the
   pack, but useful to validate that modelled demand near the *eixos* matches observed
   boardings — the closest available substitute for the ridership calibration a rail city
   would have.


---

## Deviations found during the build

Recorded because each one produced plausible-looking wrong output rather than an error.

**[changed] Step 1 — CNEFE tract codes are a different mesh vintage.** CNEFE's `COD_SETOR` is
16 characters ending in a literal `P`; the population mesh's `CD_SETOR` is 15 digits. Stripping
the suffix matches 97% of Curitiba's tracts, which looks like a rounding problem and is not —
CNEFE carries the 2022 *preliminary* mesh while `malha_com_atributos` carries the mesh corrected
in April 2025. Switching to point-in-polygon changed the tract assignment for **11% of
addresses**. Never join two government products on a same-looking geocode without checking.

**[changed] Step 3 — the classifier leaves a long tail on the default weight.** 40% of
establishments match no keyword. Inspection showed the unmatched tail is genuinely tiny
businesses (`BARRACAO`, `BRECHO`, `CHAVEIRO`), so the default weight is about right; the
patterns that were worth adding came from reading the 30 most frequent misses.

**[changed] Step 4 — CEMPRE totals need prorating.** CEMPRE reports whole municipalities, and
the bbox cuts through several. Mandirituba contributes 637 residents to the map against a
municipal commuter total of 8,633. Both jobs and the commuting rate are now prorated by the
share of the municipality inside the extent — jobs by establishment share, commuters by applying
a municipality-wide rate to the residents actually inside.

**[changed] Step 5 — top-K by gravity weight discards the calibrated tail.** With a good fit
(χ² 0.106) in hand, keeping each origin's top-14 destinations by weight produced 63.5% of trips
in the 6–15 min band against 26.3% observed, because the top of a gravity ordering is dominated
by nearby jobs. It also left most destinations with no candidate origin, so the job constraint
was unsatisfiable — column error 79.6%. Replaced with probability-proportional sampling without
replacement (Gumbel top-k) and an equal share per drawn destination: unbiased in expectation,
column error fell to **4.7%**.

**[changed] Step 5 — the travel-time model must be fitted, not assumed.** A hardcoded 4-minute
terminal time swallowed the entire "up to 5 min" band (0.35% modelled against 6.25% observed)
and a 52 km/h free-flow maximum compressed everything out of the "1–2 h" band. Fitting terminal
time and `v_max` alongside the deterrence parameters cut χ² from 0.106 to **0.0071**. Fitted
values: 1.0 min terminal, 32 km/h effective peak speed. Histogramming each origin's destination
jobs by *distance* makes this cheap — the speed model then becomes a transform of the bin
midpoints instead of a reason to rebuild the matrix.

**[changed] Step 7 — OSM buildings are unusable in Brazil.** 87,133 footprints in the extent
against 1,323,335 in Overture, for 1,389,625 census households. Geometry now comes from
Overture; heights are borrowed from the 4,825 OSM buildings that carry one, via STRtree
intersection (2,746 donated). Note `tools/buildings_index.py` only writes the heights section
when *every* building has a height, so gaps must be filled or the section vanishes silently.

**[changed] Step 7 — `osmium export` emits each closed building way twice**, once as
MultiPolygon and once as LineString. Filter on geometry type; a raw feature count out of
`osmium export` is roughly double the truth.

**[changed] Step 9 — campuses come from OSM, not zoning.** Curitiba's `ZE` (*zona educacional*)
covers only two polygons, so university land is mostly zoned as whatever surrounds it. Campus
extents come from OSM `amenity=university|college`, folded into `commercial` with
`type: college` — which is what the 1.5 style actually reads.

**Environment.** Port 5000 is held by ControlCenter (AirPlay Receiver) on macOS and Docker
reports the collision as a bare exit 125; `OSRM_PORT` now defaults to 5001. A mise/pyenv Python
can also resolve to a CA bundle missing current roots — `ftp.ibge.gov.br` chains to Sectigo R46,
which macOS `/etc/ssl/cert.pem` does not carry — so `tools/sources/fetch.py` now tries
`SSL_CERT_FILE`, then `certifi`, then the platform bundles, retrying on a verification failure
without ever disabling verification.
