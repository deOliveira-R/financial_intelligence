"""The curated macro pack: FRED series for trading research, grouped by theme.

`fin-intel sync-economic` (and the daily sync) loads these unless FI_FRED_SERIES names an
explicit list. Every series is stored with its full revision history (ALFRED vintages),
so research can ask what was known on any past date (see timeseries.py).
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import EconomicObservation, EconomicSeries, EconomicVintage
from fin_intel.pipeline import tracked
from fin_intel.providers import FredProvider, fred
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

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
    "AMTMNO": ("activity", "Manufacturers' new orders, total"),
    # Steel: iron is the present, copper the outlook (theses.md, thesis 1)
    "IPG3311A2S": ("industry", "Industrial production: iron and steel products"),
    "WPU1017": ("industry", "PPI: steel mill products"),
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
    "SAHMREALTIME": ("labor", "Real-time Sahm rule recession indicator"),
    "LNS14000036": ("labor", "Unemployment rate, ages 20-24"),
    "JTSJOL": ("labor", "Job openings (JOLTS)"),
    # Productivity and who gets it (theses 7-8)
    "OPHNFB": ("productivity", "Output per hour, nonfarm business"),
    "HOANBS": ("productivity", "Hours worked, nonfarm business"),
    "PRS85006173": ("productivity", "Labor share, nonfarm business"),
    # The sovereign (thesis 2): the interest bill, debt, who holds it
    "A091RC1Q027SBEA": ("sovereign", "Federal interest payments, quarterly"),
    "FGRECPT": ("sovereign", "Federal current receipts, quarterly"),
    "GFDEGDQ188S": ("sovereign", "Federal debt, % of GDP"),
    "TREAST": ("sovereign", "Fed holdings of Treasuries, weekly"),
    "FDHBFIN": ("sovereign", "Federal debt held by foreign investors, quarterly"),
    # Private credit (thesis 3)
    "TOTBKCR": ("credit", "Bank credit, all commercial banks, weekly"),
    "DRTSCILM": ("credit", "Banks tightening C&I loan standards (SLOOS), quarterly"),
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

# The cross-country panel for the credit-cycle research (credit.py, H003): OECD share
# prices (monthly) and OECD recession indicators (1 in recession; monthly, through 2022)
# for the BIS economies FRED covers. ISO alpha-2 -> alpha-3.
PANEL_COUNTRIES = {
    "AU": "AUS", "AT": "AUT", "BE": "BEL", "BR": "BRA", "CA": "CAN", "CH": "CHE", "CL": "CHL",
    "CN": "CHN", "CZ": "CZE", "DE": "DEU", "DK": "DNK", "ES": "ESP", "FI": "FIN", "FR": "FRA",
    "GB": "GBR", "GR": "GRC", "HU": "HUN", "ID": "IDN", "IE": "IRL", "IL": "ISR", "IN": "IND",
    "IT": "ITA", "JP": "JPN", "KR": "KOR", "LU": "LUX", "MX": "MEX", "NL": "NLD", "NO": "NOR",
    "NZ": "NZL", "PL": "POL", "PT": "PRT", "RU": "RUS", "SE": "SWE", "TR": "TUR", "US": "USA",
    "ZA": "ZAF",
}  # fmt: skip
NO_RECESSION_SERIES = {"NL"}  # FRED has no OECD recession indicator for the Netherlands


def share_price_series(country: str) -> str:
    return f"SPASTT01{country}M661N"


def recession_series(country: str) -> str:
    return f"{PANEL_COUNTRIES[country]}RECM"


def panel_series() -> list[str]:
    out = [share_price_series(c) for c in PANEL_COUNTRIES]
    out += [recession_series(c) for c in PANEL_COUNTRIES if c not in NO_RECESSION_SERIES]
    return out


# --- loading and syncs ---


def load_fred_series(session: Session, payload: Any) -> int:
    return upsert(session, EconomicSeries, [fred.parse_series(payload)], key=["id"])


def load_fred_observations(session: Session, series_id: str, payload: Any) -> int:
    rows = fred.parse_observations(series_id, payload)
    return upsert(session, EconomicObservation, rows, key=["series_id", "date"])


def load_fred_vintages(session: Session, series_id: str, payload: Any) -> int:
    rows = fred.parse_vintages(series_id, payload)
    return upsert(session, EconomicVintage, rows, key=["series_id", "date", "realtime_start"])


def sync_economic(session: Session, fred_provider: FredProvider, series_id: str) -> int:
    """Latest values (one call returns the whole series) plus every revision published
    since the last sync (ALFRED vintages), for point-in-time research."""
    with tracked(session, "fred", "observations", series_id) as result:
        last_vintage = session.scalar(
            select(func.max(EconomicVintage.realtime_start)).where(
                EconomicVintage.series_id == series_id
            )
        )
        series = fred_provider.fetch_series(series_id)
        observations = fred_provider.fetch_observations(series_id)
        # From the last stored vintage, not the day after: FRED answers HTTP 500 to a
        # realtime_start past its own date (which stays on the last business day over
        # weekends), and the last vintage is one it has published.
        vintage_dates = [
            d
            for d in fred_provider.fetch_vintage_dates(series_id, last_vintage)
            if last_vintage is None or d > last_vintage
        ]
        vintage_pages = []
        for i in range(0, len(vintage_dates), fred.MAX_VINTAGES):
            window = vintage_dates[i : i + fred.MAX_VINTAGES]
            vintage_pages += fred_provider.fetch_vintages(series_id, window[0], window[-1])

        load_fred_series(session, series)
        result["rows"] = load_fred_observations(session, series_id, observations)
        for page in vintage_pages:
            load_fred_vintages(session, series_id, page)
    return result["rows"]


registry.register(
    Dataset(
        "fred", "series", lambda s, k, p, t: load_fred_series(s, p), ("economic",), snapshot=True
    ),
    Dataset(
        "fred",
        "observations",
        lambda s, k, p, t: load_fred_observations(s, k, p),
        ("economic",),
        snapshot=True,
    ),
    Dataset("fred", "vintages", lambda s, k, p, t: load_fred_vintages(s, k, p), ("economic",)),
)
