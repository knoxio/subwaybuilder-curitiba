# Registry submission

How CWB gets into Railyard's map browser, and the exact values to paste.

Railyard hosts nothing itself — it reads the
[Subway-Builder-Modded/registry](https://github.com/Subway-Builder-Modded/registry) repo.
Publishing means filing one issue there, which auto-creates a PR for a maintainer to review.

**Submit at:** registry repo → Issues → New Issue → **Publish New Map**
(<https://github.com/Subway-Builder-Modded/registry/issues/new?template=publish-map.yml>)

If automated validation fails, edit the issue and comment `revalidate`.

## Pre-flight — all passing

| Requirement | Status |
| --- | --- |
| Repo exists with ≥1 release | ✅ `knoxio/subwaybuilder-curitiba` |
| Latest release has a `.zip` asset | ✅ `CWB.zip` |
| `manifest.json` exposed *outside* the archive | ✅ separate release asset |
| Tag is semver and matches `config.json` version | ✅ `v1.0.0` / `1.0.0` |
| `manifest.dependencies` includes `subway-builder` | ✅ `>=1.0.0` |
| Required files at zip top level, no wrapper dir | ✅ |
| `check_map_pack.py` blockers | ✅ 0 |
| Map ID `curitiba-detailed` unused | ✅ free |
| City code `CWB` unused (260 registry codes + 3 vanilla) | ✅ free |

## Form values

**Map ID** — `curitiba-detailed` *(permanent, cannot be changed later; must match `manifest.json`'s `id`)*

**City Name** — `Curitiba`

**Additional Cities** — `São José dos Pinhais, Colombo, Araucária, Pinhais, Fazenda Rio Grande, Almirante Tamandaré, Piraquara, Campo Largo`

**City Code** — `CWB`

**Country Code** — `BR` *(location tag `south-america` is derived automatically)*

**Description** — use the `description` field from [`manifest.json`](manifest.json) verbatim; it is
already formatted for the map browser.

**Data Source** — `IBGE/INEP/IPPUC`

**Methodology**

> Demand is built entirely from Brazilian government sources; no OSM population estimation.
>
> **Residents.** IBGE Censo 2022 census tracts (5,623 tracts, 3,271,037 people) disaggregated onto
> individual dwelling addresses from CNEFE, the national address register. Each CNEFE row is one
> dwelling *unit* — an apartment tower appears as up to 540 rows sharing a coordinate — so the row
> count per municipality equals the census household count and distributing a tract's residents
> uniformly across its rows reproduces the census mean household size by construction. 1,388,241
> dwelling units placed, 99.90% of the census household count. Addresses are assigned to tracts by
> point-in-polygon rather than by CNEFE's own tract code, because CNEFE carries the 2022
> *preliminary* mesh while the population mesh was corrected in April 2025; the two disagree on 11%
> of addresses.
>
> **Jobs.** IBGE CEMPRE 2024 municipal employment totals distributed across 148,690 establishment
> addresses from CNEFE, weighted by establishment type inferred from each record's free-text
> description. Municipalities that the bounding box cuts are prorated by the share of their
> establishments inside the extent.
>
> **Commutes.** Doubly-constrained gravity model over a sparse candidate matrix. Destinations are
> sampled per origin with probability proportional to the gravity weight (Gumbel top-k) rather than
> truncated to the top-K, because truncation discards the long tail the model was fitted to. The
> deterrence function *and* the travel-time model are fitted jointly by grid search against the
> observed car-commute time distribution from Censo 2022 table 10330, crossed by mode so the target
> is car trips rather than all modes (χ² 0.0071; modelled 4.8/26.8/35.3/28.5/4.5% against observed
> 6.3/26.3/38.9/23.7/4.9% across the five populated time bands). All 46,709 distinct pairs routed
> through a local OSRM instance, zero failures.
>
> **Education.** INEP Censo Escolar 2024 per-school enrolment by level for 2,051 active schools
> (680,403 enrolments), geocoded by joining each school's postcode to CNEFE and preferring an
> address already recorded as an education establishment (79% matched that way). Travel fractions by
> level (infantil 0.20, anos iniciais 0.35, anos finais 0.60, médio 0.85) because young children do
> not travel independently.
>
> **Special demand.** 57 hand-curated places. Locations are from OpenStreetMap; stadium capacities
> are published figures, other visitor and enrolment counts are class-based estimates and are
> labelled as such in the source. BRT terminals are deliberately excluded — a terminal is somewhere
> people pass through rather than a trip end, so including it would double-count journeys already
> represented by their home and workplace, and the map's premise is the metro Curitiba never built.
>
> **Limitations, stated plainly.** Commuting is a minority of real urban travel, so at 64.6% commute
> / 20.0% special destinations / 15.3% education this map is still commute-weighted; no Brazilian
> purpose-split survey was located to calibrate against. Special-demand visitor counts are
> estimates. OSRM free-flow times are roughly 1.8× faster than the census-implied mean car commute,
> which biases mode choice against transit. Building footprints come from Overture rather than OSM
> (87k OSM footprints in the extent against Overture's 1.32M) and only 0.1% carry a height, so
> heights are borrowed from overlapping OSM buildings where possible and defaulted otherwise.

**Special Demand** — tick: `airports`, `entertainment`, `hospitals`, `parks`, `schools`,
`universities`. Leave `ferries` unticked — Curitiba is inland.

**Gallery Images** — at least one is **required**. See the note below.

**Source** — `https://github.com/knoxio/subwaybuilder-curitiba`

**Update Type** — `GitHub Releases`

**GitHub Repository** — `knoxio/subwaybuilder-curitiba`

**Collaborators** — leave blank unless crediting someone.

**Terms** — both boxes must be ticked by the author. These are personal attestations: that you are
the map's author or have the author's permission, and that you have read the platform Terms of
Service.

## Gallery — worth getting right before submitting

The cover image currently in the repo is a stock photograph of the Jardim Botânico. It is fine for
the README, but the gallery is what people see when deciding whether to download a *map*, and
reviewers look at it first. In-game screenshots are much stronger. Suggested set:

1. The Centro at moderate zoom, showing the commercial/college district tint from the statutory
   zoning.
2. A wide view showing demand-point density across the metro.
3. Afonso Pena airport — now that the park/aerodrome z-fighting is fixed.
4. One of the reservoirs, showing the new water depth shading.

## After validation — the data-quality questionnaire

A **required** step; a maintainer must confirm the answers before the map can merge. Filed as its
own issue (`data-quality.yml`), prefilled with the map id. Brazil has no other registry maps, so the
quality floor that applies to countries with existing maps does not bite here.

Answers, taken from the live form's wording rather than the underlying schema enums:

| Question | Answer |
| --- | --- |
| Map ID | `curitiba-detailed` |
| Same methodology as your other maps in this country? | No — new or different methodology |
| Where do your job numbers come from? | A government census or survey that counts where people actually work |
| Smallest area job numbers are reported for | Whole cities or municipalities |
| How did you place the jobs? | Building footprints, split by workplace type using standard assumptions |
| What do your population numbers count? | Total population, including children and retirees |
| Smallest area population is reported for | Individual buildings or census blocks |
| How did you place the people? | Spread evenly across each area *(see note)* |
| Does the census publish commute data? | Partial — per-area totals plus how far or where trips tend to go |
| Smallest area commute-flow data covers | Whole cities or municipalities |

Verified against what the pipeline actually queried: CEMPRE returns one value per municipality
(`N6 = Município`, 17 values); the census mesh returns 5,623 tracts at a median of 567 residents
each; table 10330 returns one set of marginals per municipality.

**Two answers to flag rather than assert.** The form separates *reporting* granularity from
*placement* method, which is why "whole cities" is the correct answer for jobs even though the
finished surface is address-level — the address precision is credited in the placement question. But:

- *"How did you place the people?"* has no option that fits. Every choice assumes a split by building
  footprint; this map distributes tract population across individual dwelling *units* from a
  government address register, which is finer than footprints. "Spread evenly across each area" is
  the nearest wording and undersells it badly. Say so in the methodology box and let the maintainer
  decide.
- *"Where do your job numbers come from?"* — CEMPRE is an administrative business register where
  employers self-declare employment per local unit, not a survey of workers. The suggested answer is
  the closest available and slightly flatters it.

The `EFFECTIVE RESOLUTION` section of [METHODOLOGY.txt](METHODOLOGY.txt) exists to carry this
distinction in prose, where a dropdown cannot.

## Data source links

For the questionnaire's Sources field:

```
IBGE Censo 2022, Agregados por Setores Censitários:
https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/

IBGE CNEFE 2022 (national address register):
https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/

IBGE CEMPRE 2024 (SIDRA table 9509, variable 707):
https://sidra.ibge.gov.br/tabela/9509

IBGE Censo 2022 commuting (SIDRA table 10330, variable 13376):
https://sidra.ibge.gov.br/tabela/10330

INEP Censo Escolar 2024:
https://www.gov.br/inep/pt-br/acesso-a-informacao/dados-abertos/microdados/censo-escolar

IPPUC geodata (zoning, neighbourhoods, parks, hydrography):
https://ippuc.org.br/geodownloads/geo.htm

Overture Maps 2026-07-22.0 (building footprints):
https://overturemaps.org/

OpenStreetMap (roads, aeroways, labels), © OpenStreetMap contributors, ODbL:
https://www.openstreetmap.org/copyright

HydroLAKES v1.0 (water depth), Messager et al. 2016, CC BY 4.0:
https://www.hydrosheds.org/products/hydrolakes
```
