# Brazil data sources

Everything here was probed on 2026-07-31 and the byte counts are from files actually
downloaded, not from documentation. Where a number is quoted it came out of the file.

Brazil is unusually good territory for a high-quality map, for one reason: **IBGE publishes
both the population counts and an address-level register to distribute them against.** Most
countries give you one or the other. That means a Brazilian map can reach
`high-data-quality` + `high-detail` (see [the registry's rubric](06-railyard-and-registry.md))
using only government sources, with OSM needed for geometry but not for demand.

The registry currently lists **272 maps and zero for Brazil** — 9 Peru, 1 Argentina, nothing
else in South America. Verified by tallying `country` across `maps/*/manifest.json` in
`Subway-Builder-Modded/registry`.

## The three pillars

| Need | Source | Granularity | Vintage |
| --- | --- | --- | --- |
| Residents | Censo 2022 *malha com atributos* | Census tract (~300 households) | 2022 |
| Spatial distribution | CNEFE 2022 | Individual address, with coordinates | 2022 |
| Jobs control total | CEMPRE via SIDRA | Municipality | 2024 |
| Commute calibration | Censo 2022 table 10330 | Municipality | 2022 |

### 1. Residents — census tracts with population attached

IBGE ships the census-tract mesh with the census variables already joined as attributes, one
file per state, in GeoPackage / Shapefile / CSV:

```
ftp://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/
    malha_com_atributos/setores/{gpkg,shp,csv}/UF/<UF>/<UF>_setores_CD2022.gpkg
```

Sizes: `PR` 54 MB, `RS` 55 MB, `RJ` 72 MB, `SC` 116 MB, `SP` 174 MB.

The variables you want, from `malha_com_atributos/Dicionario_de_dados_malha_agregados.ods`:

| Column | Meaning |
| --- | --- |
| `v0001` | Total persons — **this is your `residents`** |
| `v0002` | Total households |
| `v0003` / `v0004` | Private / collective households |
| `v0005` | Mean residents per occupied private household |
| `v0007` | Total occupied private households |

Plus the full administrative hierarchy on every row — `CD_MUN`/`NM_MUN`, `NM_BAIRRO`,
`NM_SUBDIST`, and two useful metro-area groupings: `NM_CONCURB` (*concentração urbana*, IBGE's
functional conurbation) and `NM_RGI` (*região geográfica imediata*). `CD_SIT` distinguishes
urban density classes from rural, and `CD_TIPO` flags special tracts — `1` favela/urban
community, `2` barracks, `6` prison, `7` hospital/care institution. `CD_TIPO` matters: a
prison or barracks tract has residents who do not commute, and left alone it will generate
phantom commuters out of a jail.

A tract-level `residents` figure is already enough for `high-detail`, since the census tract
is Brazil's minimal official statistical area.

### 2. CNEFE — the part that makes Brazil special

The *Cadastro Nacional de Endereços para Fins Estatísticos* is every address in the country
from the 2022 census, **with coordinates**, one CSV per municipality:

```
ftp://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/
    Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/<UF#>_<UF>/<IBGE7>_<NAME>.zip
```

There is a GeoJSON tree beside the CSV one (`Arquivos_CNEFE/GeoJSON/`) and a separate
coordinates-only tree (`Coordenadas_enderecos/`). The CSV is the most compact way in.

Curitiba (`4106902_CURITIBA.zip`, 24 MB → 152 MB, 900,459 rows):

| `COD_ESPECIE` | Meaning | Curitiba count |
| --- | --- | --- |
| 1 | Private dwelling | 788,684 (87.6%) |
| 6 | Establishment, other purposes | 87,930 (9.8%) |
| 7 | Building under construction | 16,123 (1.8%) |
| 5 | Health establishment | 2,820 |
| 8 | Religious establishment | 2,094 |
| 4 | Education establishment | 1,574 |
| 2 | Collective dwelling | 1,162 |
| 3 | Agricultural establishment | 72 |

**100% of rows carry coordinates**, and `NV_GEO_COORD` grades their precision — 86.6% at
level 1 (the address itself), 11.3% at 2, 1.7% at 3, 0.5% at 4. Filter on it if you care.

Other columns worth knowing: `COD_SETOR` is the census-tract geocode, so CNEFE joins straight
onto the mesh above — that is the whole trick. `DSC_ESTABELECIMENTO` is a free-text
establishment name (`PADARIA`, `OFICINA MECANICA`, `ESCRITORIO DE ADVOCACIA`) — 61,924
distinct values in Curitiba, usable for typing establishments by keyword.
`COD_INDICADOR_ESTAB_ENDERECO`, `NUM_QUADRA` and `NUM_FACE` give block/face identity.

Gotchas: the file is **`;`-delimited and latin-1 encoded**, not UTF-8. `DSC_ESTABELECIMENTO`
is dirty — `VAGO` (3,206), `SEM NOME` (500), `LOJA VAGA`, `VAZIO`, `SEM IDENTIFICACAO` all
mean "nothing here", and must be dropped before you treat a row as a workplace.

What this buys you: instead of scattering a tract's population over its polygon, you can
place it on the actual dwellings, and put jobs on the actual establishments. That is finer
than US LODES, which stops at the census block.

### 3. Jobs — CEMPRE via the SIDRA API

IBGE's *Cadastro Central de Empresas* gives formal employment per municipality. No key, no
auth, JSON:

```
https://servicodados.ibge.gov.br/api/v3/agregados/9509/periodos/2024/variaveis/707?localidades=N6%5B<IBGE7>%5D
```

Table **9509** covers 2022–2024. Variable `707` is *pessoal ocupado total*; `708` is the
salaried subset, `706` counts local units. Curitiba: 1,186,645 (2022) → 1,227,816 (2023) →
**1,262,165 (2024)**.

Two traps. **Table 1685 is the superseded series and ends at 2021** — it is what most search
results and tutorials point at. And the multi-locality form `N6[a,b,c]` returns
**HTTP 500** on this table; request one municipality at a time. `apisidra.ibge.gov.br` is an
alternative front end with a flatter response shape if you prefer it.

CEMPRE is municipality-level only, so the job *count* is coarse even though the job
*locations* from CNEFE are not. The pairing is the point: official total, official
distribution.

### 4. Commute calibration — the LODES substitute

[04-demand-data.md](04-demand-data.md) notes that since 1.5 the official cities pair homes to
workplaces so the commute-distance distribution matches reality, and that a naive uniform
pairing behaves very differently. For Brazil the calibration target is Censo 2022 table
**10330**, which crosses commute time × mode × workplace location per municipality:

```
https://servicodados.ibge.gov.br/api/v3/agregados/10330/periodos/2022/variaveis/13376
    ?localidades=N6%5B<IBGE7>%5D&classificacao=537%5Ball%5D%7C2088%5B79488%5D%7C86%5B95251%5D%7C469%5B79176%5D
```

Classifications: `537` commute time (8 bands), `2088` main mode (15 categories), `469`
workplace location, `86` colour/race. Pass the `Total` category id for the axes you are not
slicing — `2088[79488]`, `86[95251]`, `469[79176]`, `537[31609]`.

Curitiba, 716,001 residents who work away from home:

| Commute time | Share |     | Main mode | Share |
| --- | --- | --- | --- | --- |
| ≤ 5 min | 6.5% | | Car | 49.8% |
| 6–15 min | 21.2% | | Bus | 26.5% |
| 15–30 min | 33.7% | | On foot | 10.9% |
| 30–60 min | 27.2% | | Motorcycle | 4.9% |
| 1–2 h | 10.0% | | Bicycle | 2.7% |
| 2–4 h | 0.3% | | BRT | 0.4% |
| > 4 h | 0.0% | | **Train or metro** | **0.0%** |

And `469` gives the intermunicipal split: 94.4% work inside Curitiba, 5.6% in another
municipality. Use it to constrain how much demand crosses a municipal boundary instead of
guessing.

Three uses: fit the gravity deterrence function to the time bands rather than inventing a
distance floor; sanity-check in-game walking mode share against the real one; and bound
cross-boundary flows. Note the bands are *reported* times across all modes, so compare them
against a mode-weighted mix, not against `drivingSeconds` alone.

**If your city already has rail, there is a stronger calibration loop than this.** Where an
operator publishes per-station boarding counts, build the real network in the finished map,
simulate, and compare modelled boardings against observed — that turns "the demand looks
plausible" into a number you can be measurably wrong about. Brazilian metro and CPTM operators
publish ridership at varying granularity, so it is worth checking for São Paulo, Rio, Belo
Horizonte, Brasília, Recife, Salvador, Porto Alegre and Fortaleza before falling back to the
commute-time distribution. The distribution is the right substitute only for cities with no
rail to calibrate against — Curitiba, where the census records 0.0% rail mode share, being the
clearest case.

## Municipal geoportals

Above IBGE, most large Brazilian cities run their own geoportal, and the good ones ship things
OSM simply does not have. Curitiba's IPPUC is the strongest example found:

```
https://ippuc.org.br/geodownloads/SHAPES_SIRGAS/<NAME>_SIRGAS.zip
```

Index at <https://ippuc.org.br/geodownloads/geo.htm>. Both SAD-69 (`SHAPES/`) and SIRGAS-2000
(`SHAPES_SIRGAS/`) versions are published — **always take `SHAPES_SIRGAS`**, since SAD-69
needs a datum shift (the page gives dX −66.163 m, dY 2.028 m, dZ −33.718 m).

| Dataset | Records | Feeds |
| --- | --- | --- |
| `ZONEAMENTO` | 223 | `commercial` layer, incl. `type: college` |
| `DIVISA_DE_BAIRROS` | 75 | `neighborhood_labels` / `suburb_labels` |
| `TERMINAL_DE_TRANSPORTE` | 24 | Special demand, with lat/lon and served lines |
| `PARQUES_E_BOSQUES`, `PRACAS_E_JARDINETES` | | `landuse` `kind: park` |
| `HIDRO_RIOS_PG`, `HIDRO_LAGOS_LAGOAS_REPRESAS`, `HIDRO_AREA_UMIDA` | | `water` |
| `HOSPITAL`, `UNIDADE_DE_SAUDE`, `UPA`, `ESCOLA_MUNICIPAL`, `CMEI` | | Special demand |
| `EIXO_RUA`, `SIST_VIARIO_CLASSIFICADO` | | Road cross-check |
| `LOTES` | | Cadastral parcels |

### Zoning beats OSM landuse for the `commercial` layer

[03-pmtiles-layers.md](03-pmtiles-layers.md) explains that 1.5 tints districts from a
`commercial` source-layer with a `type` of `commercial` or `college`, that depot does *not*
emit this, and that Protomaps basemaps do not either. A municipal zoning layer is a better
input than OSM landuse, because it is the legally-defined land use rather than whatever a
mapper tagged.

IPPUC's `ZONEAMENTO` carries `SG_ZONA` / `NM_GRUPO`, which map cleanly onto what the game
wants:

| `SG_ZONA` | Zone | Destination |
| --- | --- | --- |
| `ZONA CENTRAL`, `ZUM-1/2/3`, `ZS-1`, `ZS-2` | Central, mixed use, services | `commercial`, `type: commercial` |
| `EE`, `EMF`, `ENC`, `ECO-*`, `ECL-*`, `EAC` | Structural / connector axes (the BRT corridors) | `commercial`, `type: commercial` |
| `ZE` | *Zona educacional* | `commercial`, `type: college` |
| `ZI`, `ZI-LV` | Industrial | `industrial` |
| `ZR1`–`ZR4`, `ZR3-T`, `ZROC`, `ZROI`, `SEHIS` | Residential | `residential` |
| `UC`, `NM_ZONA = PARQUES E BOSQUES` | Conservation units, parks | `landuse`, `kind: park` |
| `ZM` | Military | `landuse`, `kind: aerodrome` if following depot's `color_military_like_aerodrome` |

Keep the polygons disjoint and give campuses precedence over commercial where they overlap,
as first-party tilesets do. Worth checking whether your city publishes the equivalent —
GeoSampa, data.rio, DataPOA and Florianópolis all do to varying depth.

## Practical notes

**The FTP host needs the FTP protocol.** `https://ftp.ibge.gov.br/<path>/` returns an HTTP 200
portal page with no file list, so directory listing over HTTPS looks empty. Individual file
URLs over HTTPS work fine. To list a directory, use FTP:

```bash
curl -s --list-only "ftp://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/"
```

Download over HTTPS, list over FTP.

**GeoPackage is SQLite.** No GDAL needed for attributes:

```bash
sqlite3 PR_setores_CD2022.gpkg "SELECT NM_MUN, COUNT(*), SUM(v0001) FROM PR_setores_CD2022 GROUP BY NM_MUN;"
```

Geometry is WKB behind a small GeoPackage header. If all you need is a bounding box, the
header carries one: bytes 0–1 are `GP`, byte 3 is flags, and when bits 1–3 of the flags are
`1` the next 32 bytes from offset 8 are the envelope. Two ways to get this wrong, both of
which produce numbers rather than errors:

* the order is `min_x, max_x, min_y, max_y` — x-range then y-range, **not** the interleaved
  `min_x, min_y, max_x, max_y` a bbox is normally written as;
* **flags bit 0 is the byte order, and `1` means little-endian.** Reading it backwards yields
  values like `5.8e+157` that are obviously wrong, but only if you look — aggregate them into
  a bbox first and you get a plausible-looking box that is silently garbage.

`tools/sources/formats.py` implements both correctly; use `gpkg_envelope` rather than
re-deriving it. For full rings, `gpkg_geometry_wkb` strips the header so `shapely.wkb.loads`
can take over.

**Shapefile DBF is parseable with `struct`.** 32-byte header, then 32 bytes per field
descriptor, then fixed-width records. Also latin-1. `tools/sources/formats.py` has
`read_dbf`.

**CRS.** IBGE and IPPUC SIRGAS files are EPSG:4674 (SIRGAS 2000). It is within a metre of
WGS84 for these latitudes — pass the coordinates through unchanged. IPPUC's non-SIRGAS
`SHAPES/` files are SAD-69 / UTM-22S and do need converting; just take the SIRGAS ones.

**Encoding and delimiters.** IBGE CSVs are `;`-delimited latin-1. DBFs are latin-1. Decoding
as UTF-8 will fail on the first `ã`.

**Municipality codes** are the 7-digit IBGE code (Curitiba `4106902`, São Paulo `3550308`, Rio
`3304557`, Porto Alegre `4314902`, Florianópolis `4205407`). The first two digits are the
state. Filenames in CNEFE's per-municipality trees are `<IBGE7>_<UPPERCASE_NAME>.zip` with
accents stripped.

## Choosing a city

The IBGE tier is identical for every Brazilian city, so all five obvious candidates can reach
`high-data-quality` / `high-detail`. What differs is the municipal layer, the map's size, and
whether the city has an OD survey.

| City | Metro pop | Municipal geodata | OD survey | Map size | Notes |
| --- | --- | --- | --- | --- | --- |
| **Curitiba** | 3.4 M | IPPUC, excellent | IPPUC POD, recent | Tractable | Zoning + official bairros; no rail today |
| São Paulo | 21 M | GeoSampa, excellent | **Metrô OD 2023**, best in Latin America | Very large | Best OD data anywhere in BR; huge buildings index |
| Rio de Janeiro | 13 M | data.rio, strong | Yes | Large | Terrain and bay make interesting geometry |
| Porto Alegre | 4.4 M | DataPOA | EDOM, dated | Tractable | — |
| Florianópolis | 1.6 M | Geoportal | PLAMUS 2015 | Small | Island geography; smallest demand base |

Curitiba is the best first Brazilian map: the municipal data is the strongest of the five
relative to city size, 3.4 M metro keeps the buildings index and tile archive manageable, and
the premise is unusually clean — the 0.0% train/metro mode share above is real. Curitiba is
the city that invented BRT *instead of* building a metro, so "build the metro Curitiba never
built" is the actual counterfactual rather than a retread of an existing network.

São Paulo is the better *second* map, and the only Brazilian city where a genuine
survey-derived OD matrix is available; budget for the size.

## Recipe

1. Pull the state GeoPackage, filter to your municipalities, read `v0001` per tract.
   `NM_CONCURB` is a good default metro definition.
2. Pull CNEFE for each municipality. Split on `COD_ESPECIE`: 1/2 are dwelling candidates,
   4/5/6/8 are workplace candidates. Drop `COD_ESPECIE = 7` and the junk
   `DSC_ESTABELECIMENTO` values.
3. Distribute each tract's `v0001` across its dwellings by `COD_SETOR`. Weight by OSM or
   Overture building footprint area where you have it, uniformly otherwise.
4. Pull CEMPRE 707 per municipality for the jobs total; distribute across workplace addresses,
   weighted by type inferred from `DSC_ESTABELECIMENTO` and by footprint.
5. Aggregate to demand points at whatever spacing you want — the registry's detail metric
   rewards tight nearest-neighbour spacing, and merging is what keeps the pop count sane.
6. Pair homes to jobs with a doubly-constrained gravity model, fitting the deterrence function
   to table 10330's time bands and bounding cross-municipal flows with classification `469`.
7. Route each pair through OSRM for `drivingSeconds` / `drivingDistance`
   ([04-demand-data.md](04-demand-data.md)).
8. Zero out or exclude non-commuting `CD_TIPO` tracts (prisons, barracks) before pairing.
9. Check `sum(points[].residents) == sum(pops[].size)`, no phantom points, then
   `python3 tools/check_map_pack.py CODE.zip --tag <tag>`.

Residents in `points[].residents` should be the working-age commuting subset, not total
population, because the schema requires it to equal the pop total — see the DXB write-up
referenced in [04-demand-data.md](04-demand-data.md). Keep the true census figure for
`config.json`'s `population`.

## Licensing and attribution

IBGE data is public and free to redistribute with attribution; cite "IBGE, Censo Demográfico
2022" and name the specific product (Agregados por Setores Censitários, CNEFE, CEMPRE). IPPUC
publishes its shapefiles for free use with the caveat that it accepts no liability, and asks
to be credited — cite "IPPUC / Prefeitura Municipal de Curitiba" and the layer date. Neither
is ODbL, so keep their attribution separate from the OpenStreetMap notice your tiles and roads
need.

## Source index

| What | Where |
| --- | --- |
| Census tracts + attributes | `ftp://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/malha_com_atributos/` |
| CNEFE 2022 | `ftp://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/` |
| SIDRA aggregate index | <https://servicodados.ibge.gov.br/api/v3/agregados> |
| CEMPRE 2022–2024 | table `9509`, variable `707` |
| Commute time / mode / workplace | table `10330`, variable `13376` |
| Panorama do Censo 2022 | <https://censo2022.ibge.gov.br/panorama/> |
| IPPUC downloads | <https://ippuc.org.br/geodownloads/geo.htm> |
| Curitiba open data | <https://dadosabertos.curitiba.pr.gov.br/> |
| GeoSampa (SP) | <https://geosampa.prefeitura.sp.gov.br/> |
| data.rio | <https://www.data.rio/> |
| Metrô SP OD 2023 | <https://www.metro.sp.gov.br/pt_BR/pesquisa-od/> |
| Geofabrik Brazil extracts | <https://download.geofabrik.de/south-america/brazil.html> |
