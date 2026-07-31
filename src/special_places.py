"""Curated special-demand places for Curitiba.

Coordinates are taken from OpenStreetMap and were checked against the extracted POI list, so
they are factual. **Capacities are not.** `daily` is the number of people modelled as travelling
to the place on a representative weekday, and its `basis` records where the figure comes from:

* `published` — a widely published, stable figure (stadium seating capacity).
* `estimate`  — a class-based estimate by this map's author. Treated as a tuning parameter, not
  a claim about the real world. Anyone with better numbers should replace them.

Sizes are deliberately conservative. Special demand competes with commuting for the simulation's
attention, and an over-sized stadium distorts a whole corridor.

## Why there are no BRT terminals here

Curitiba's 24 URBS terminals are the obvious candidates and they are deliberately excluded.
Two reasons:

1. **A terminal is not a trip end.** People pass through it on the way somewhere else. Its
   demand is already represented by the homes and workplaces at either end of those trips, so
   adding it as an attractor would count the same journey twice.
2. **The map is greenfield.** The premise is the metro Curitiba never built. Baking the existing
   BRT network's interchange points into the demand surface would pre-commit the player to the
   corridors URBS already chose, which is the opposite of the question the map asks.

The same argument excludes bus stations and park-and-ride sites. It does *not* exclude the
airport: an airport is a genuine origin and destination in its own right.
"""

from __future__ import annotations

# (code, type, name, lon, lat, daily, basis, max_distance_m, residential_split)
PLACES: list[tuple[str, str, str, float, float, int, str, int, float]] = [
    # ---- airports -------------------------------------------------------------------------
    # Afonso Pena is Paraná's main airport. `daily` covers passengers plus staff movements.
    ("AIR_CWB", "airport", "Aeroporto Internacional Afonso Pena", -49.17419, -25.53327, 14000, "estimate", 45000, 0.0),
    ("AIR_BFH", "airport", "Aeroporto de Bacacheri", -49.23121, -25.40302, 600, "estimate", 25000, 0.0),

    # ---- universities ---------------------------------------------------------------------
    # UFPR is split across campuses; each is modelled separately so demand lands where the
    # students actually go rather than at a single notional centroid.
    ("UNI_UFPR_POLI", "university", "UFPR — Campus Centro Politécnico", -49.23415, -25.45086, 14000, "estimate", 40000, 0.02),
    ("UNI_UFPR_REIT", "university", "UFPR — Campus Reitoria", -49.26201, -25.42688, 7000, "estimate", 40000, 0.0),
    ("UNI_UFPR_SAUDE", "university", "UFPR — Setor de Ciências da Saúde", -49.26227, -25.42292, 4000, "estimate", 40000, 0.0),
    ("UNI_UFPR_JBOT", "university", "UFPR — Campus Jardim Botânico", -49.23804, -25.44645, 3500, "estimate", 40000, 0.0),
    ("UNI_UFPR_REB", "university", "UFPR — Campus Rebouças", -49.26432, -25.43740, 2500, "estimate", 40000, 0.0),
    ("UNI_PUCPR", "university", "Pontifícia Universidade Católica do Paraná", -49.25035, -25.45296, 20000, "estimate", 45000, 0.03),
    ("UNI_UTFPR_CT", "university", "UTFPR — Campus Curitiba (Ecoville)", -49.31774, -25.50596, 8000, "estimate", 40000, 0.0),
    ("UNI_POSITIVO", "university", "Universidade Positivo", -49.35988, -25.44506, 12000, "estimate", 40000, 0.02),
    ("UNI_TUIUTI", "university", "Universidade Tuiuti do Paraná", -49.31946, -25.42458, 7000, "estimate", 35000, 0.0),
    ("UNI_UNIBRASIL", "university", "UniBrasil", -49.21425, -25.42604, 5000, "estimate", 30000, 0.0),
    ("UNI_UNICURITIBA", "university", "Centro Universitário Curitiba", -49.26554, -25.44979, 5000, "estimate", 30000, 0.0),
    ("UNI_UNINTER", "university", "Uninter — Campus Centro", -49.27469, -25.43278, 4500, "estimate", 30000, 0.0),
    ("UNI_MACKENZIE", "university", "Faculdade Evangélica Mackenzie", -49.30610, -25.43528, 3000, "estimate", 30000, 0.0),
    ("UNI_FAE", "university", "FAE Business School", -49.27351, -25.43831, 3000, "estimate", 30000, 0.0),
    ("UNI_UNIANDRADE", "university", "Uniandrade", -49.31778, -25.45731, 2500, "estimate", 30000, 0.0),
    ("UNI_DOMBOSCO", "university", "Faculdades Dom Bosco", -49.26579, -25.47205, 2200, "estimate", 25000, 0.0),
    ("UNI_STACRUZ", "university", "Faculdades Santa Cruz", -49.30350, -25.49581, 1800, "estimate", 25000, 0.0),
    ("UNI_EMBAP", "university", "EMBAP / Unespar — Música e Belas Artes", -49.26810, -25.43355, 1500, "estimate", 25000, 0.0),

    # ---- stadiums -------------------------------------------------------------------------
    # Seating capacities are published and stable; `daily` is the capacity, and the game's own
    # event scheduling decides how often it fills.
    ("SPO_BAIXADA", "sports_facility", "Arena da Baixada (Estádio Mario Celso Petraglia)", -49.27651, -25.44839, 42000, "published", 40000, 0.0),
    ("SPO_COUTO", "sports_facility", "Estádio Major Antônio Couto Pereira", -49.25974, -25.42128, 37000, "published", 40000, 0.0),
    ("SPO_CAPANEMA", "sports_facility", "Estádio Durival Britto e Silva (Vila Capanema)", -49.25592, -25.43921, 17000, "published", 30000, 0.0),
    ("SPO_PINHEIRAO", "sports_facility", "Complexo Poliesportivo Pinheirão", -49.21951, -25.43157, 6000, "estimate", 30000, 0.0),

    # ---- shopping -------------------------------------------------------------------------
    ("SHP_PALLADIUM", "shopping_center", "Shopping Palladium", -49.29113, -25.47784, 18000, "estimate", 30000, 0.0),
    ("SHP_BARIGUI", "shopping_center", "ParkShopping Barigüi", -49.31656, -25.43483, 16000, "estimate", 30000, 0.0),
    ("SHP_MUELLER", "shopping_center", "Shopping Mueller", -49.27059, -25.42346, 14000, "estimate", 25000, 0.0),
    ("SHP_CURITIBA", "shopping_center", "Shopping Curitiba", -49.27738, -25.44099, 12000, "estimate", 25000, 0.0),
    ("SHP_ESTACAO", "shopping_center", "Shopping Estação", -49.26677, -25.43829, 12000, "estimate", 25000, 0.0),
    ("SHP_JOCKEY", "shopping_center", "Jockey Plaza Shopping", -49.21522, -25.42805, 12000, "estimate", 30000, 0.0),
    ("SHP_BATEL", "shopping_center", "Shopping Pátio Batel", -49.29086, -25.44304, 9000, "estimate", 25000, 0.0),
    ("SHP_CRYSTAL", "shopping_center", "Crystal Plaza Shopping", -49.28139, -25.43907, 6000, "estimate", 20000, 0.0),
    ("SHP_SAOJOSE", "shopping_center", "Shopping São José", -49.20457, -25.53832, 7000, "estimate", 25000, 0.0),
    ("SHP_COLOMBO", "shopping_center", "Colombo Park Shopping", -49.18440, -25.36087, 6000, "estimate", 25000, 0.0),

    # ---- hospitals ------------------------------------------------------------------------
    # Hospitals generate staff, outpatient and visitor trips. `daily` is all three.
    ("HOS_HCUFPR", "hospital", "Hospital de Clínicas da UFPR", -49.26202, -25.42404, 9000, "estimate", 45000, 0.0),
    ("HOS_TRABALHADOR", "hospital", "Complexo Hospitalar do Trabalhador", -49.29522, -25.48508, 3500, "estimate", 35000, 0.0),
    ("HOS_EVANGELICO", "hospital", "Hospital Universitário Evangélico Mackenzie", -49.29211, -25.43410, 3500, "estimate", 35000, 0.0),
    ("HOS_CAJURU", "hospital", "Hospital Universitário Cajuru", -49.24540, -25.43597, 2800, "estimate", 35000, 0.0),
    ("HOS_PEQPRINCIPE", "hospital", "Hospital Pequeno Príncipe", -49.27636, -25.44391, 2800, "estimate", 40000, 0.0),
    ("HOS_ERASTO", "hospital", "Hospital Erasto Gaertner", -49.23899, -25.45317, 2200, "estimate", 40000, 0.0),
    ("HOS_STACASA", "hospital", "Hospital Santa Casa de Misericórdia", -49.27270, -25.43724, 1800, "estimate", 30000, 0.0),
    ("HOS_ZILDAARNS", "hospital", "Hospital do Idoso Zilda Arns", -49.29371, -25.51080, 1500, "estimate", 30000, 0.0),
    ("HOS_SJP", "hospital", "Hospital e Maternidade São José dos Pinhais", -49.19709, -25.54334, 1400, "estimate", 25000, 0.0),
    ("HOS_ARAUCARIA", "hospital", "Hospital Municipal de Araucária", -49.39246, -25.57935, 1200, "estimate", 25000, 0.0),

    # ---- parks and attractions ------------------------------------------------------------
    # Curitiba's parks are genuinely major destinations — the city's identity is built on them.
    ("PRK_BARIGUI", "park", "Parque Barigui", -49.30800, -25.42500, 9000, "estimate", 30000, 0.0),
    ("PRK_JBOTANICO", "park", "Jardim Botânico de Curitiba", -49.24080, -25.44280, 8000, "estimate", 35000, 0.0),
    ("PRK_TANGUA", "park", "Parque Tanguá", -49.30560, -25.37870, 4500, "estimate", 30000, 0.0),
    ("PRK_TINGUI", "park", "Parque Tingui", -49.30200, -25.39800, 3000, "estimate", 25000, 0.0),
    ("PRK_PAPA", "park", "Bosque Estadual Papa João Paulo II", -49.26953, -25.41009, 2500, "estimate", 25000, 0.0),
    ("PRK_ALEMAO", "park", "Bosque Alemão", -49.28655, -25.40536, 1800, "estimate", 20000, 0.0),
    ("PRK_SAOLOURENCO", "park", "Parque São Lourenço", -49.26500, -25.39600, 1500, "estimate", 20000, 0.0),
    ("ZOO_CWB", "zoo", "Zoológico de Curitiba", -49.23371, -25.55767, 2500, "estimate", 30000, 0.0),

    # ---- culture --------------------------------------------------------------------------
    ("CUL_OPERAARAME", "cultural_center", "Ópera de Arame", -49.27600, -25.38400, 2000, "estimate", 30000, 0.0),
    ("MUS_MON", "museum", "Museu Oscar Niemeyer", -49.26400, -25.41400, 1800, "estimate", 30000, 0.0),
    ("CUL_GUAIRA", "cultural_center", "Teatro Guaíra", -49.26800, -25.43000, 1500, "estimate", 25000, 0.0),

    # ---- large single-site employment not captured well by establishment counts ------------
    # The CIC is a planned industrial district and REPAR is a refinery; both are large, spatially
    # concentrated employers that a per-establishment job model spreads too thinly.
    ("IND_REPAR", "government_facility", "Refinaria Presidente Getúlio Vargas (REPAR)", -49.39500, -25.55500, 4000, "estimate", 40000, 0.0),
]

MILITARY: list[tuple[str, str, str, float, float, int, str, int, float]] = [
    # Curitiba hosts the 5ª Região Militar and CINDACTA II.
    ("MIL_CINDACTA", "military_base", "CINDACTA II", -49.23360, -25.39780, 1500, "estimate", 30000, 0.15),
]

ALL_PLACES = PLACES + MILITARY
