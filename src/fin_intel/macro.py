"""The curated macro pack: FRED series for trading research, grouped by theme.

`fin-intel sync-economic` (and the daily sync) loads these unless FI_FRED_SERIES names an
explicit list. Every series is stored with its full revision history (ALFRED vintages),
so research can ask what was known on any past date (see timeseries.py).
"""

MACRO_SERIES: dict[str, tuple[str, str]] = {
    # Oil and energy
    "DCOILWTICO": ("energy", "WTI crude oil spot, daily"),
    "DCOILBRENTEU": ("energy", "Brent crude oil spot, daily"),
    "DHHNGSP": ("energy", "Henry Hub natural gas spot, daily"),
    "GASREGW": ("energy", "US regular gasoline retail price, weekly"),
    # Inflation, realized and expected
    "CPIAUCSL": ("inflation", "CPI, all items"),
    "CPILFESL": ("inflation", "CPI, core (ex food and energy)"),
    "PCEPI": ("inflation", "PCE price index"),
    "PCEPILFE": ("inflation", "PCE price index, core"),
    "PPIACO": ("inflation", "PPI, all commodities"),
    "T5YIE": ("inflation", "5-year breakeven inflation, daily"),
    "T10YIE": ("inflation", "10-year breakeven inflation, daily"),
    "T5YIFR": ("inflation", "5-year, 5-year forward inflation expectation, daily"),
    "MICH": ("inflation", "University of Michigan 1-year inflation expectations"),
    # Interest rates and the curve
    "DFF": ("rates", "Effective federal funds rate, daily"),
    "SOFR": ("rates", "Secured overnight financing rate, daily"),
    "DGS3MO": ("rates", "3-month Treasury yield, daily"),
    "DGS2": ("rates", "2-year Treasury yield, daily"),
    "DGS5": ("rates", "5-year Treasury yield, daily"),
    "DGS10": ("rates", "10-year Treasury yield, daily"),
    "DGS30": ("rates", "30-year Treasury yield, daily"),
    "T10Y2Y": ("rates", "10-year minus 2-year Treasury spread, daily"),
    "T10Y3M": ("rates", "10-year minus 3-month Treasury spread, daily"),
    "DFII10": ("rates", "10-year TIPS real yield, daily"),
    "MORTGAGE30US": ("rates", "30-year mortgage rate, weekly"),
    "THREEFYTP10": ("rates", "10-year term premium (Kim-Wright)"),
    # Growth and industrial activity
    "GDPC1": ("activity", "Real GDP, quarterly"),
    "GDPNOW": ("activity", "Atlanta Fed GDPNow nowcast"),
    "INDPRO": ("activity", "Industrial production"),
    "TCU": ("activity", "Capacity utilization"),
    "CFNAI": ("activity", "Chicago Fed National Activity Index"),
    "GACDFSA066MSFRBPHI": ("activity", "Philadelphia Fed manufacturing, current activity"),
    "GACDISA066MSFRBNY": ("activity", "Empire State manufacturing, current activity"),
    "PCOPPUSDM": ("activity", "Copper price, monthly (daily proxy: CPER ETF)"),
    "RSAFS": ("activity", "Retail sales"),
    "HOUST": ("activity", "Housing starts"),
    "UMCSENT": ("activity", "University of Michigan consumer sentiment"),
    # Commodities (IMF primary commodity prices, monthly) and freight. Strategic materials
    # and energy for the deep-history industries; lithium and cobalt aren't on FRED (LIT
    # and the miners' prices stand in).
    "PIORECRUSDM": ("commodities", "Iron ore price, monthly"),
    "PNICKUSDM": ("commodities", "Nickel price, monthly"),
    "PALUMUSDM": ("commodities", "Aluminum price, monthly"),
    "PZINCUSDM": ("commodities", "Zinc price, monthly"),
    "PTINUSDM": ("commodities", "Tin price, monthly"),
    "PURANUSDM": ("commodities", "Uranium price, monthly"),
    "PCOALAUUSDM": ("commodities", "Coal price (Australia), monthly"),
    "PNGASEUUSDM": ("commodities", "Natural gas price (Europe), monthly"),
    "PNGASJPUSDM": ("commodities", "LNG price (Asia), monthly"),
    "PWHEAMTUSDM": ("commodities", "Wheat price, monthly"),
    "PMAIZMTUSDM": ("commodities", "Corn price, monthly"),
    "PSOYBUSDM": ("commodities", "Soybean price, monthly"),
    "TSIFRGHT": ("freight", "Freight Transportation Services Index (BTS), monthly"),
    "PCU483111483111": ("freight", "PPI: deep sea freight transportation, monthly"),
    "PCU4841214841212": ("freight", "PPI: long-distance general freight trucking, monthly"),
    # Labor
    "PAYEMS": ("labor", "Nonfarm payrolls"),
    "UNRATE": ("labor", "Unemployment rate"),
    "ICSA": ("labor", "Initial jobless claims, weekly"),
    # Dollar and currencies
    "DTWEXBGS": ("currency", "Broad trade-weighted US dollar index, daily"),
    "DEXUSEU": ("currency", "US dollars per euro, daily"),
    "DEXJPUS": ("currency", "Japanese yen per US dollar, daily"),
    "DEXCHUS": ("currency", "Chinese yuan per US dollar, daily"),
    # Japan (yen carry trade; the JGB curve and flows come from MoF, see japan.py)
    "IRSTCI01JPM156N": ("japan", "Japan call money rate, monthly"),
    "IR3TIB01JPM156N": ("japan", "Japan 3-month interbank rate, monthly"),
    # Credit, volatility and financial conditions
    "BAMLH0A0HYM2": ("risk", "High-yield credit spread (option-adjusted), daily"),
    "BAMLC0A0CM": ("risk", "Investment-grade credit spread (option-adjusted), daily"),
    "VIXCLS": ("risk", "VIX, daily"),
    "VXVCLS": ("risk", "3-month VIX, daily"),
    "NFCI": ("risk", "Chicago Fed National Financial Conditions Index, weekly"),
    "STLFSI4": ("risk", "St. Louis Fed Financial Stress Index, weekly"),
    # Liquidity
    "WALCL": ("liquidity", "Fed total assets, weekly"),
    "RRPONTSYD": ("liquidity", "Overnight reverse repo, daily"),
    "WTREGEN": ("liquidity", "Treasury General Account, weekly"),
    "M2SL": ("liquidity", "M2 money supply"),
}
