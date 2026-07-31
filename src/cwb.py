"""Shared configuration and helpers for the Curitiba (CWB) map build.

Every constant that describes *this map* lives here so the pipeline steps stay generic. See
`../BUILD-PLAN.md` for why the extent is what it is, and
`../../docs/09-brazil-data-sources.md` for the sources.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Shared, map-agnostic source helpers live alongside this project in tools/sources/.
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

CODE = "CWB"
NAME = "Curitiba"
COUNTRY = "BR"
CREATOR = "joao"

# Extent "T2" — see BUILD-PLAN.md. 60 x 63 km, 3,797 km2, 96.7% of IBGE's Curitiba
# concentracao urbana.
BBOX = [-49.55, -25.72, -48.95, -25.15]

INITIAL_VIEW = {"latitude": -25.4284, "longitude": -49.2733, "zoom": 11, "bearing": 0}

# IBGE 7-digit municipality codes inside BBOX, with the tract counts and resident totals the
# census mesh reports for the part that falls inside. Ordered by population.
MUNICIPALITIES: list[tuple[str, str]] = [
    ("4106902", "Curitiba"),
    ("4125506", "São José dos Pinhais"),
    ("4105805", "Colombo"),
    ("4101804", "Araucária"),
    ("4107652", "Fazenda Rio Grande"),
    ("4119152", "Pinhais"),
    ("4100400", "Almirante Tamandaré"),
    ("4119509", "Piraquara"),
    ("4104204", "Campo Largo"),
    ("4104006", "Campina Grande do Sul"),
    ("4122206", "Rio Branco do Sul"),
    ("4104253", "Campo Magro"),
    ("4111258", "Itaperuçu"),
    ("4120804", "Quatro Barras"),
    ("4106209", "Contenda"),
    ("4103107", "Bocaiúva do Sul"),
    ("4114302", "Mandirituba"),
]

MUN_NAME = dict(MUNICIPALITIES)
UF = "PR"
UF_NUM = "41"

# Census tract types whose residents do not commute to work. Leaving them in generates pops
# travelling to a job from a prison. Codes from the IBGE data dictionary (CD_TIPO).
NON_COMMUTING_TRACT_TYPES = {"2", "6", "7"}  # barracks, prison, hospital/care institution

# CNEFE address species. 1/2 are dwellings, 4/5/6/8 are workplaces, 3 is agricultural and 7 is
# a building site.
DWELLING_SPECIES = {"1", "2"}
WORKPLACE_SPECIES = {"4", "5", "6", "8"}

# CNEFE DSC_ESTABELECIMENTO values that mean "nothing here". Matched case-insensitively after
# stripping; these are exact matches, not substrings, because "LOJA" alone is a real shop.
VACANT_ESTABLISHMENT = {
    "VAGO",
    "VAGA",
    "VAZIO",
    "VAZIA",
    "SEM NOME",
    "SEM IDENTIFICACAO",
    "SEM IDENTIFICAÇÃO",
    "SEM INFORMACAO",
    "NAO IDENTIFICADO",
    "LOJA VAGA",
    "SALA VAGA",
    "ESTABELECIMENTO VAGO",
    "IMOVEL VAGO",
    "PREDIO VAGO",
    "TERRENO VAGO",
    "EM CONSTRUCAO",
    "EM REFORMA",
    "DESOCUPADO",
    "FECHADO",
    "PARA ALUGAR",
    "ALUGA-SE",
    "ALUGA SE",
    "A VENDA",
    "SALA FECHADA",
    "LOJA FECHADA",
    "CASA FECHADA",
    "SEM ESTABELECIMENTO",
    "-",
    ".",
}

# --------------------------------------------------------------------------- paths

DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
OUT = ROOT / "out"

for _d in (RAW, INTERIM, OUT):
    _d.mkdir(parents=True, exist_ok=True)


def census_gpkg() -> Path:
    return RAW / f"{UF}_setores_CD2022.gpkg"


def cnefe_zip(code: str) -> Path:
    return RAW / "cnefe" / f"{code}.zip"


# --------------------------------------------------------------------------- source URLs

IBGE_FTP = "ftp://ftp.ibge.gov.br"
IBGE_HTTP = "https://ftp.ibge.gov.br"

CENSUS_MESH_URL = (
    f"{IBGE_HTTP}/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios"
    f"/malha_com_atributos/setores/gpkg/UF/{UF}/{UF}_setores_CD2022.gpkg"
)

CNEFE_BASE = (
    f"{IBGE_HTTP}/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos"
    f"/Censo_Demografico_2022/Arquivos_CNEFE/CSV/Municipio/{UF_NUM}_{UF}"
)

SIDRA = "https://servicodados.ibge.gov.br/api/v3/agregados"

# CEMPRE: local units and employment per municipality. Table 9509 covers 2022-2024; table 1685
# is the superseded series and stops at 2021.
CEMPRE_TABLE, CEMPRE_JOBS_VAR, CEMPRE_YEAR = "9509", "707", "2024"

# Censo 2022: workers commuting out of home, by commute time / mode / workplace location.
COMMUTE_TABLE, COMMUTE_VAR, COMMUTE_YEAR = "10330", "13376", "2022"
CLS_TIME, CLS_MODE, CLS_RACE, CLS_WORKPLACE = "537", "2088", "86", "469"
CAT_TOTAL = {CLS_TIME: "31609", CLS_MODE: "79488", CLS_RACE: "95251", CLS_WORKPLACE: "79176"}

# Commute-time band ids in classification 537, in ascending order, with the minute range each
# covers. The open-ended top band is capped at 300 for fitting purposes.
TIME_BANDS: list[tuple[str, str, float, float]] = [
    ("19429", "up to 5 min", 0, 5),
    ("79189", "6-15 min", 5, 15),
    ("79190", "15-30 min", 15, 30),
    ("19431", "30-60 min", 30, 60),
    ("19432", "1-2 h", 60, 120),
    ("79191", "2-4 h", 120, 240),
    ("79192", "over 4 h", 240, 300),
]

IPPUC_SHAPES = "https://ippuc.org.br/geodownloads/SHAPES_SIRGAS"
IPPUC_LAYERS = [
    "ZONEAMENTO",
    "DIVISA_DE_BAIRROS",
    "TERMINAL_DE_TRANSPORTE",
    "PARQUES_E_BOSQUES",
    "PRACAS_E_JARDINETES",
    "HIDRO_RIOS_PG",
    "HIDRO_LAGOS_LAGOAS_REPRESAS",
    "HIDRO_AREA_UMIDA",
    "HOSPITAL",
    "UNIDADE_DE_SAUDE",
    "ESCOLA_MUNICIPAL",
    "EIXO_RUA",
]

# --------------------------------------------------------------------------- helpers


def in_bbox(lon: float, lat: float, bbox: list[float] | None = None) -> bool:
    b = bbox or BBOX
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def write_json(path: Path, payload, *, indent: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=indent)
    size = path.stat().st_size
    print(f"  wrote {path.relative_to(ROOT)} ({size / 1e6:,.2f} MB)")
    return path


def read_json(path: Path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def banner(text: str) -> None:
    print()
    print("=" * 72)
    print(f"  {text}")
    print("=" * 72)


def http_json(url: str, *, timeout: int = 60):
    """GET a JSON document, tolerating a gzipped response.

    SIDRA sometimes returns gzip even without an `Accept-Encoding: gzip` request header, so
    decoding the body as text directly fails on the magic bytes. Checking `Content-Encoding`
    alone is not enough either — sniff the magic.
    """
    import gzip
    import urllib.request

    from sources.fetch import ssl_context

    request = urllib.request.Request(
        url, headers={"User-Agent": "SubwayBuilder-MapTools/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        body = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
    if encoding == "gzip" or body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return json.loads(body)


def sidra_value(raw) -> int | None:
    """Coerce a SIDRA value cell to an int.

    SIDRA returns sentinels as strings in the value field and they mean different things:
    `...` not available, `-` zero by convention, `X` suppressed for confidentiality. Only `-`
    is safely zero; the others must stay unknown or they bias every total that includes them.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "-":
        return 0
    if text in ("...", "X", "..", ""):
        return None
    try:
        return int(float(text))
    except ValueError:
        return None
