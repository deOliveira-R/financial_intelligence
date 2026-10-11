"""Themes: one taxonomy across every source's own classification.

Each source classifies industries its own way: companies by SIC code (SEC), federal
contracts by NAICS (USAspending), lobbying by issue code (Senate LDA), bills by policy area
and subject (CRS), presidential documents not at all (only text). A theme maps one
industry or policy area to its codes in each vocabulary, plus the market series that track
it (ETFs, CFTC markets, FRED and EIA series), so a question like "what is happening
around defense?" is one lookup rather than a dozen hand-made joins.

The six strategic industries of the deep-history universe come first (energy and critical
materials split into two themes); the rest give the other major sectors a home. Mappings
are judgment calls meant to be refined: tests check that every code exists in its
vocabulary, not that the choice is right.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    # Companies: a deep-history universe industry (universe.py), and/or SIC code ranges.
    universe_industry: str | None = None
    sic: tuple[tuple[int, int], ...] = ()
    # Policy: NAICS code prefixes (contracts), LDA issue codes (lobbying), CRS policy areas
    # and subjects (bills), and a full-text query for presidential documents.
    naics: tuple[str, ...] = ()
    # BEA detail industries the NAICS codes can't reach (graph.py): BEA splits construction
    # by type of structure, all under NAICS 23.
    bea: tuple[str, ...] = ()
    lobbying: tuple[str, ...] = ()
    policy_areas: tuple[str, ...] = ()
    bill_subjects: tuple[str, ...] = ()
    presidential_query: str | None = None
    # Markets: ETFs, CFTC markets (cot.py names), FRED and EIA series.
    etfs: tuple[str, ...] = ()
    cot: tuple[str, ...] = ()
    fred: tuple[str, ...] = ()
    eia: tuple[str, ...] = ()
    notes: str = field(default="", compare=False)


THEMES = (
    Theme(
        "semiconductors",
        "Semiconductors and equipment",
        universe_industry="semiconductors",
        naics=("3344", "333242"),
        lobbying=("TEC", "SCI", "TRD", "MAN"),
        policy_areas=(
            "Science, Technology, Communications",
            "Foreign Trade and International Finance",
        ),
        bill_subjects=("Computers and information technology", "Manufacturing"),
        presidential_query="semiconductor OR semiconductors OR chips OR polysilicon",
        etfs=("SMH", "SOXX"),
    ),
    Theme(
        "ai_power",
        "AI infrastructure and power",
        universe_industry="ai_infrastructure_power",
        naics=("2211", "518", "335", "237130"),
        bea=("233240",),
        lobbying=("UTI", "ENG", "CPI", "TEC"),
        policy_areas=("Energy", "Science, Technology, Communications"),
        bill_subjects=(
            "Electric power generation and transmission",
            "Computers and information technology",
        ),
        presidential_query='"artificial intelligence" OR "data center" OR grid OR nuclear',
        etfs=("XLU", "GRID", "URA"),
        eia=("natgas_storage",),
    ),
    Theme(
        "oil_gas",
        "Oil and gas",
        universe_industry="energy_materials",
        sic=((1311, 1311), (1381, 1389), (2911, 2911), (4610, 4619), (4922, 4925)),
        naics=("211", "324", "486", "213111"),
        lobbying=("ENG", "FUE", "ENV", "NAT"),
        policy_areas=("Energy", "Environmental Protection"),
        bill_subjects=("Oil and gas",),
        presidential_query='oil OR petroleum OR LNG OR "natural gas" OR pipeline',
        etfs=("XLE", "XOP", "OIH", "USO", "UNG"),
        cot=("crude", "natgas", "gasoline", "heatingoil"),
        fred=("DCOILWTICO", "DCOILBRENTEU", "DHHNGSP", "PNGASEUUSDM", "PNGASJPUSDM"),
        eia=("crude_stocks", "cushing_stocks", "crude_production", "refinery_utilization"),
    ),
    Theme(
        "critical_minerals",
        "Metals, mining and critical minerals",
        universe_industry="energy_materials",
        sic=((1000, 1099), (1400, 1499), (3330, 3339)),
        naics=("212", "331"),
        lobbying=("NAT", "MAN", "TRD"),
        policy_areas=("Public Lands and Natural Resources",),
        bill_subjects=("Mining", "Metals"),
        presidential_query='"critical minerals" OR "rare earth" OR copper OR uranium OR lithium',
        etfs=("COPX", "LIT", "URA", "GDX", "GLD", "SLV"),
        cot=("gold", "silver", "copper"),
        fred=("PCOPPUSDM", "PIORECRUSDM", "PNICKUSDM", "PALUMUSDM", "PURANUSDM"),
        notes="shares the energy_materials universe industry with oil_gas; split by SIC",
    ),
    Theme(
        "defense",
        "Defense and aerospace",
        universe_industry="defense_aerospace",
        naics=("3364", "3366", "332992", "332993", "332994", "334511", "541715"),
        lobbying=("DEF", "AER", "AVI", "HOM"),
        policy_areas=("Armed Forces and National Security",),
        bill_subjects=("Military procurement, research, weapons development",),
        presidential_query='"national security" OR "defense production act" OR "armed forces"',
        etfs=("ITA", "XAR"),
    ),
    Theme(
        "health",
        "Healthcare and biotech",
        universe_industry="healthcare_biotech",
        naics=("3254", "3391", "621", "622", "541714"),
        lobbying=("HCR", "MMM", "PHA", "MED"),
        policy_areas=("Health",),
        bill_subjects=("Prescription drugs", "Medicare", "Health technology, devices, supplies"),
        presidential_query='"drug pricing" OR pharmaceutical OR "most favored nation" OR medicare',
        etfs=("XLV", "XBI", "IBB"),
    ),
    Theme(
        "industrials",
        "Industrials, infrastructure and reshoring",
        universe_industry="industrials_reshoring",
        naics=("333", "237", "2373"),
        bea=("2332C0", "233230", "233240"),
        lobbying=("MAN", "TRA", "TAR", "ROD", "TRD"),
        policy_areas=("Transportation and Public Works", "Foreign Trade and International Finance"),
        bill_subjects=("Tariffs", "Manufacturing", "Infrastructure development"),
        presidential_query='tariff OR tariffs OR "section 232" OR reciprocal OR infrastructure',
        etfs=("XLI", "PAVE"),
        fred=("INDPRO", "TCU"),
    ),
    Theme(
        "shipping",
        "Shipping and logistics",
        sic=((4400, 4499), (4011, 4013), (4210, 4231), (4512, 4513), (4700, 4799)),
        naics=("483", "484", "482", "488", "336611"),
        lobbying=("MAR", "TRA", "TRU", "RRR", "POS"),
        policy_areas=("Transportation and Public Works",),
        bill_subjects=("Marine and inland water transportation", "Navigation, waterways, harbors"),
        presidential_query="maritime OR shipbuilding OR shipping OR ports OR vessels",
        fred=("TSIFRGHT", "PCU483111483111", "PCU4841214841212"),
        notes="no ETF in the deep-history universe; container rates aren't free",
    ),
    Theme(
        "financials",
        "Financials",
        sic=((6000, 6799),),
        naics=("52",),
        lobbying=("FIN", "BAN", "BNK", "INS", "ACC"),
        policy_areas=("Finance and Financial Sector",),
        presidential_query='banking OR "digital assets" OR cryptocurrency OR "capital markets"',
        etfs=("XLF",),
        cot=("ust2y", "ust10y", "fedfunds", "sofr"),
        fred=("DFF", "DGS2", "DGS10", "T10Y2Y", "BAMLH0A0HYM2", "BAMLC0A0CM"),
    ),
    Theme(
        "technology",
        "Technology, software and internet",
        sic=((7370, 7379), (3570, 3579), (3660, 3669)),
        naics=("5112", "5132", "518", "519", "517"),
        lobbying=("TEC", "CPI", "COM", "SCI", "CPT"),
        policy_areas=("Science, Technology, Communications", "Commerce"),
        bill_subjects=(
            "Internet, web applications, social media",
            "Computers and information technology",
        ),
        presidential_query='"artificial intelligence" OR cybersecurity OR broadband',
        etfs=("XLK", "QQQ", "XLC"),
    ),
    Theme(
        "agriculture",
        "Agriculture and food",
        sic=((100, 999), (2000, 2099)),
        naics=("111", "112", "311"),
        lobbying=("AGR", "FOO", "ANI"),
        policy_areas=("Agriculture and Food",),
        presidential_query="agriculture OR farmers OR food OR fertilizer",
        cot=("corn", "soybeans", "wheat"),
        fred=("PWHEAMTUSDM", "PMAIZMTUSDM", "PSOYBUSDM"),
    ),
    Theme(
        "real_estate",
        "Real estate and housing",
        sic=((6500, 6599), (6798, 6798), (1520, 1531)),
        naics=("531", "236"),
        bea=("233411", "233412", "2334A0", "230302", "2332A0"),
        lobbying=("HOU", "RES", "URB"),
        policy_areas=("Housing and Community Development",),
        presidential_query="housing OR mortgage OR homeownership",
        etfs=("XLRE",),
        fred=("HOUST", "MORTGAGE30US"),
    ),
    Theme(
        "consumer",
        "Consumer goods and retail",
        sic=((5000, 5999), (2100, 2199), (3940, 3949)),
        naics=("44", "45", "312"),
        lobbying=("RET", "CSP", "BEV", "ALC", "TOB"),
        policy_areas=("Commerce",),
        presidential_query='"consumer prices" OR retail OR "de minimis"',
        etfs=("XLY", "XLP"),
        fred=("RSAFS", "UMCSENT"),
    ),
)
BY_NAME = {t.name: t for t in THEMES}


def get(name: str) -> Theme:
    try:
        return BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown theme {name!r}; one of {', '.join(BY_NAME)}") from None
