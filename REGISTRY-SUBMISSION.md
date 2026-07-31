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
| Map ID `curitiba` unused | ✅ free |
| City code `CWB` unused (260 registry codes + 3 vanilla) | ✅ free |

## Form values

**Map ID** — `curitiba` *(permanent, cannot be changed later)*

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

A **required** step: a maintainer must confirm the answers before the map can merge. Brazil has no
existing registry maps, so the quality floor that applies to countries with existing maps does not
bite here.

Draft answers, from what this pipeline actually does:

| Question | Answer | Why |
| --- | --- | --- |
| `workplace_count` | `registered_self_declared` | CEMPRE counts formally registered establishments |
| `workplace_granularity` | `adm5` | Municipal control totals distributed to census-tract level |
| `workplace_resolution` | `mesh_125_or_adm5` | Individual CNEFE establishment addresses |
| `workplace_intensity` | `fine_types_generic` | Keyword-classified establishment types, generic weights |
| `resident_count` | `total_population` | Census tract `v0001`, then reduced to the commuting subset |
| `resident_granularity` | `adm5` | Census tracts, Brazil's minimal statistical area |
| `resident_resolution` | `mesh_125_or_adm5` | Individual CNEFE dwelling addresses |
| `resident_intensity` | `measured_per_unit` | One CNEFE row per dwelling unit; ties to the census household count |
| `od_metric` | `prior_informed_synthetic` | Synthetic gravity matrix calibrated to observed census commute-time bands |
| `od_granularity` | `adm3` | Time-band and workplace-location marginals are published per municipality |

`od_metric` is the one to expect discussion on. There is no published Brazilian OD matrix at this
resolution, so the matrix is synthetic — but it is fitted to real observed marginals rather than
assumed, which is why `prior_informed_synthetic` rather than `none`. If a maintainer reads
`structured_marginals` as the better fit given table 10330 supplies genuine per-municipality
marginals, that is a reasonable reading and worth asking about rather than arguing.
