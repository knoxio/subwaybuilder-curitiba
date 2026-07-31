"""Step 3 — place jobs on individual establishment addresses.

CNEFE says *where* every establishment is and gives it a free-text description; CEMPRE says how
many people are formally employed in each municipality. Neither alone is enough. This step
classifies each establishment from its description into a relative employment weight, then
scales the weights so each municipality's modelled jobs match its CEMPRE total — official count,
official locations.

Establishment classes are relative to each other, not absolute headcounts; the per-municipality
rescale sets the level. What the weights have to get right is that a hospital employs far more
people than a bakery at the same address species.

    python3 src/step3_jobs.py
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict

import cwb
from step2_residents import assign_tracts, build_tract_index, cnefe_rows, largest_remainder, load_tracts

# Keyword -> relative employment weight. Order matters: the first pattern that matches a
# description wins, so the large specific employers are listed before the generic retail terms
# that would otherwise swallow them ("HOSPITAL DO TRABALHADOR" must not match "TRABALHO").
JOB_CLASSES: list[tuple[str, str, float]] = [
    # (label, regex, weight)
    ("hospital", r"\bHOSPITAL|\bMATERNIDADE|PRONTO ?SOCORRO|\bUPA\b|SANTA CASA", 220),
    ("university", r"UNIVERSIDAD|FACULDADE|\bUFPR\b|\bPUC\b|UTFPR|CAMPUS|FATEC|INSTITUTO FEDERAL", 180),
    ("shopping_centre", r"SHOPPING|CENTRO COMERCIAL|GALERIA COMERCIAL|OUTLET", 160),
    ("industry", r"IND[UÚ]STRIA|INDUSTRIAL|F[AÁ]BRICA|FRIGORIFICO|REFINARIA|METAL[UÚ]RGICA|USINA|MONTADORA", 55),
    ("government", r"PREFEITURA|SECRETARIA|MINIST[EÉ]RIO|F[OÓ]RUM|TRIBUNAL|C[AÂ]MARA MUNICIPAL|RECEITA FEDERAL|DETRAN|IBGE|CART[OÓ]RIO", 40),
    ("supermarket", r"SUPERMERCADO|HIPERMERCADO|ATACAD|MERCADO MUNICIPAL", 45),
    ("school", r"\bESCOLA|COL[EÉ]GIO|\bCMEI\b|CRECHE|CENTRO EDUCACION|ENSINO", 32),
    ("hotel", r"\bHOTEL|POUSADA|MOTEL|HOSTEL|APART ?HOTEL", 25),
    ("warehouse", r"DEP[OÓ]SITO|ARMAZ[EÉ]M|LOG[IÍ]STICA|TRANSPORTADORA|DISTRIBUIDORA|CENTRO DE DISTRIBUI", 18),
    ("clinic", r"CL[IÍ]NICA|CONSULT[OÓ]RIO|LABORAT[OÓ]RIO|ODONTOL[OÓ]G|FISIOTERAPIA|UNIDADE DE SA[UÚ]DE|POSTO DE SA[UÚ]DE", 10),
    ("bank", r"\bBANCO\b|BRADESCO|ITA[UÚ]|CAIXA ECON|SANTANDER|COOPERATIVA DE CR[EÉ]DITO|SICREDI|SICOOB", 14),
    ("office", r"ESCRIT[OÓ]RIO|ADVOCACIA|ADVOGAD|CONTABIL|CONTADOR|ENGENHARIA|ARQUITETURA|CONSULTORIA|CORRETORA|IMOBILI[AÁ]RIA|SEGURADORA|EMPRESA|COM[EÉ]RCIO E SERVI", 9),
    ("restaurant", r"RESTAURANTE|LANCHONETE|PIZZARIA|CHURRASCARIA|CAFETERIA|\bCAF[EÉ]\b|SORVETERIA|FAST ?FOOD|BUFFET", 8),
    ("church", r"IGREJA|TEMPLO|CAPELA|PAR[OÓ]QUIA|CENTRO ESP[IÍ]RITA|SALÃO DO REINO|MESQUITA|SINAGOGA|TERREIRO", 4),
    ("gym", r"ACADEMIA|GIN[AÁ]SIO|EST[UÚ]DIO DE PILATES|CROSSFIT", 7),
    ("bakery", r"PANIFICADORA|PADARIA|CONFEITARIA", 7),
    ("pharmacy", r"FARM[AÁ]CIA|DROGARIA", 6),
    ("workshop", r"OFICINA|MEC[AÂ]NICA|SERRALHERIA|MARCENARIA|BORRACHARIA|FUNILARIA|LANTERNAGEM|TORNEARIA|CHAPEA", 4),
    ("petrol", r"POSTO DE COMBUST|POSTO DE GASOLINA|AUTO ?POSTO", 8),
    ("event_venue", r"SAL[AÃ]O DE FESTAS|BUFFET INFANTIL|CASA DE FESTAS|CHACARA DE FESTAS", 6),
    ("retail", r"\bLOJA|COM[EÉ]RCIO|COMERCIAL|MERCEARIA|MINIMERCADO|\bMERCADO\b|BOUTIQUE|MAGAZINE|PAPELARIA|PET ?SHOP|[OÓ]TICA|JOALHERIA|FLORICULTURA|MATERIAIS DE CONSTRU|BRE[CX]H?[OÓ]|LOT[EÉ]RICA|TABACARIA|BAZAR", 5),
    ("personal_services", r"SAL[AÃ]O|BARBEARIA|CABELE?[IE]?REIRO|EST[EÉ]TICA|MANICURE|LAVANDERIA|LAVA ?CAR|LAVA ?RAPIDO|COSTUR|ATELI[EÊ]R?", 3),
    ("bar", r"\bBAR\b|BOTECO|CHOPERIA|DISTRIBUIDORA DE BEBIDAS|ADEGA", 3),
    ("parking", r"ESTACIONAMENTO|GARAGEM", 2),
]

# Extra patterns appended after the main table so they never shadow a more specific class above.
# These come from inspecting the most frequent descriptions the first pass failed to classify.
JOB_CLASSES += [
    ("clinic", r"DENTISTA|PSIC[OÓ]LOG|NUTRICIONISTA|VETERIN[AÁ]RI|FONOAUDI", 8),
    ("church", r"ASSEMBLEIA DE DEUS|CONGREGA[CÇ][AÃ]O|BATISTA|UNIVERSAL DO REINO|QUADRANGULAR|ADVENTISTA|METODISTA|PRESBITERIAN|SEM DENOMINA", 4),
    ("workshop", r"AUTO ?PE[CÇ]AS|AUTO ?EL[EÉ]TRICA|LATARIA|ESTOFARIA|VIDRA[CÇ]ARIA|CHAVEIRO|BICICLETARIA|FERRO ?VELHO|RECICLAGEM|GR[AÁ]FICA|TAPE[CÇ]ARIA|RETIFICA", 4),
    ("warehouse", r"BARRAC[AÃ]O|GALP[AÃ]O|SUCATA", 8),
    ("hotel", r"AIRBNB|ALUGUEL POR TEMPORADA|KITNET", 3),
]

COMPILED = [(label, re.compile(pattern), weight) for label, pattern, weight in JOB_CLASSES]

# Fallback weight by address species when the description matches nothing, which is common —
# most rows carry a proper name ("PADARIA SÃO JORGE" matches, "CASA DO JOÃO" does not).
SPECIES_DEFAULT = {"4": 30.0, "5": 10.0, "6": 4.0, "8": 4.0}


def classify(description: str, species: str) -> tuple[str, float]:
    text = (description or "").upper()
    for label, pattern, weight in COMPILED:
        if pattern.search(text):
            return label, weight
    return f"unclassified_{species}", SPECIES_DEFAULT.get(species, 4.0)


def is_vacant(description: str) -> bool:
    text = (description or "").strip().upper()
    if not text:
        return False  # blank is normal; a real establishment often has no recorded name
    return text in cwb.VACANT_ESTABLISHMENT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()

    cwb.banner("Step 3 — jobs onto establishment addresses")

    tracts, polygons, poly_codes = load_tracts(with_geometry=True)
    tree, tree_codes = build_tract_index(polygons, poly_codes)
    cempre = cwb.read_json(cwb.INTERIM / "cempre_jobs.json")
    print(f"  tracts: {len(tracts):,}  |  CEMPRE municipalities: {len(cempre)}")

    # weight accumulation per municipality, at coordinate level
    weights: dict[str, dict[tuple[float, float], float]] = defaultdict(lambda: defaultdict(float))
    coord_tract: dict[tuple[float, float], str] = {}
    classes = Counter()
    stats = {"establishments": 0, "vacant": 0, "in_box": 0, "no_tract": 0, "total_in_file": 0}

    # Per-municipality establishment counts, so a municipality the bbox cuts through can have its
    # whole-municipality CEMPRE total prorated to the part that is actually in the map.
    per_mun: dict[str, dict[str, int]] = {c: {"in_file": 0, "in_box": 0} for c, _ in cwb.MUNICIPALITIES}

    for code, name in cwb.MUNICIPALITIES:
        pending: list[tuple[float, float, str, float]] = []
        kept_before = stats["in_box"]

        def flush() -> None:
            if not pending:
                return
            assigned = assign_tracts(tree, tree_codes, [(p[0], p[1]) for p in pending])
            for (lon, lat, _label, weight), tract_code in zip(pending, assigned):
                if tract_code is None:
                    stats["no_tract"] += 1
                    continue
                key = (round(lon, 6), round(lat, 6))
                weights[code][key] += weight
                coord_tract[key] = tract_code
                stats["in_box"] += 1
            pending.clear()

        for row in cnefe_rows(code):
            species = row["COD_ESPECIE"]
            if species not in cwb.WORKPLACE_SPECIES:
                continue
            stats["total_in_file"] += 1
            description = row.get("DSC_ESTABELECIMENTO") or ""
            if is_vacant(description):
                stats["vacant"] += 1
                continue
            stats["establishments"] += 1
            per_mun[code]["in_file"] += 1
            try:
                lat = float(row["LATITUDE"])
                lon = float(row["LONGITUDE"])
            except (TypeError, ValueError):
                continue
            if not cwb.in_bbox(lon, lat):
                continue
            per_mun[code]["in_box"] += 1
            label, weight = classify(description, species)
            classes[label] += 1
            pending.append((lon, lat, label, weight))
            if len(pending) >= 200_000:
                flush()
        flush()
        print(f"  {name:<24} establishments in box: {stats['in_box'] - kept_before:>7,}")

    print()
    print(f"  workplace-species rows : {stats['total_in_file']:,}")
    print(f"  dropped as vacant      : {stats['vacant']:,}")
    print(f"  usable establishments  : {stats['establishments']:,}")
    print(f"  placed in a tract      : {stats['in_box']:,}")
    print(f"  dropped, no tract      : {stats['no_tract']:,}")
    print(f"  distinct coordinates   : {sum(len(v) for v in weights.values()):,}")
    print()
    print("  top classes:")
    for label, count in classes.most_common(14):
        print(f"    {label:<22} {count:>8,}")

    # ---- scale to CEMPRE, prorating for municipalities that straddle the extent ----
    print()
    records: list[tuple[float, float, int, str]] = []
    grand_total = 0
    for code, name in cwb.MUNICIPALITIES:
        target = cempre.get(code)
        coords = weights.get(code)
        if not target or not coords:
            print(f"  {name:<24} skipped (jobs={target}, coords={len(coords or {})})")
            continue
        # CEMPRE is a whole-municipality figure, but the bbox cuts through several
        # municipalities. Prorate by the share of that municipality's establishments that fell
        # inside the extent — establishments track jobs far better than area or residents do.
        counts = per_mun[code]
        share = counts["in_box"] / counts["in_file"] if counts["in_file"] else 0.0
        modelled = int(round(target * share))
        keys = list(coords.keys())
        parts = largest_remainder(modelled, [max(1, int(coords[k] * 100)) for k in keys])
        for (lon, lat), jobs in zip(keys, parts):
            if jobs:
                records.append((lon, lat, jobs, coord_tract[(lon, lat)]))
        grand_total += modelled
        flag = "" if share > 0.995 else f"  [{share * 100:.1f}% of municipality in extent]"
        print(
            f"  {name:<24} jobs {modelled:>9,} over {len(coords):>7,} sites"
            f"  (CEMPRE {target:,}){flag}"
        )

    print()
    print(f"  job records : {len(records):,}")
    print(f"  jobs placed : {grand_total:,}")

    out = cwb.INTERIM / "jobs.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["lon", "lat", "jobs", "setor"])
        writer.writerows(records)
    print(f"  wrote {out.relative_to(cwb.ROOT)} ({out.stat().st_size / 1e6:,.1f} MB)")
    return 0




if __name__ == "__main__":
    sys.exit(main())
