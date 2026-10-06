"""The deep-history universe: strategic industries that get full daily price history.

Massive's free plan gives two years of bars for the whole market; Tiingo gives decades, but
only for 500 symbols a month. So deep history goes to a focused set chosen with the user
(2026-10-06): semiconductors, AI infrastructure and power, energy and critical materials,
defense and aerospace, healthcare and biotech, industrials and reshoring, plus sector and
macro ETFs for benchmarks.

Each industry is a set of SIC codes plus companies named explicitly because their SIC code
sits elsewhere (Synopsys is "software", Equinix a REIT, the hyperscalers are the demand
side of AI infrastructure). Within an industry, the largest NYSE/Nasdaq listings by market
cap are kept. The selection is written to MEMBERS_FILE and committed: it's chosen once
and stays stable (a history only has to be fetched once), and rebuilding it is explicit.

Bias to keep in mind: members are today's large companies, so their history is
survivorship-biased. Fine for studying how signals behave on a focused universe; not for
claiming a strategy would have picked these names in 2010.
"""

import csv
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.models import CompanyMetrics, Issuer, Security

MEMBERS_FILE = Path(__file__).with_name("deep_history.csv")
MAJOR_MICS = ("XNYS", "XNAS", "XASE", "ARCX", "BATS")
MIN_MARKET_CAP = 1e9
EXCLUDE = {"VLTO"}  # SIC places it in an industry it isn't in (Veralto: water testing)


@dataclass(frozen=True)
class Industry:
    name: str
    sics: frozenset[int]
    limit: int
    include: tuple[str, ...] = field(default=())  # tickers, regardless of SIC


def _industry(name: str, limit: int, sics: str, include: str = "") -> Industry:
    return Industry(name, frozenset(map(int, sics.split())), limit, tuple(include.split()))


INDUSTRIES = (
    _industry(
        "semiconductors", 80,
        "3674 3559 3825 3827 3672 3572",
        "SNPS CDNS ENTG MKSI ONTO KLIC APH QCOM ARM",
    ),
    _industry(
        "ai_infrastructure_power", 80,
        "4911 4931 4932 4991 3679 3612 3613 3620 3621 1731 1623 3669 3661 3576 3571 3585",
        "MSFT GOOGL AMZN META ORCL CRWV NBIS APLD EQIX DLR IRM AMT CCI GEV ETN ANET TT HUBB "
        "BWXT OKLO SMR ITRI FSLR ENPH",
    ),
    _industry(
        "energy_materials", 80,
        "1311 2911 1389 1381 1382 3533 4610 4922 4923 4924 1221 1000 1040 1090 1400 3334",
        "ALB MP LEU USAR LAC SQM UUUU LIN APD",
    ),
    _industry(
        "defense_aerospace", 55,
        "3760 3812 3728 3721 3724 3730 3480 3720 3795",
        "GE HWM HEI BAH CACI LDOS SAIC PLTR PSN ASTS RKLB",
    ),
    _industry(
        "healthcare_biotech", 85,
        "2834 2836 2835 3841 3842 3844 3845 3826 8071 6324 8062 5122 8731",
        "TMO DHR A ISRG IQV",
    ),
    _industry(
        "industrials_reshoring", 75,
        "3531 3523 3510 3490 3561 3562 3537 3540 3541 3560 3590 3822 3743 1600 1700 3312 3317 "
        "7359 5084 5085 3420 3290 3640 3630",
        "CAT DE URI PH EMR HON ITW WAB NUE STLD FAST GWW MLM VMC PWR ROK AME XYL MMM",
    ),
)  # fmt: skip

# Benchmarks and macro: broad and equal-weight indices (SPY is dominated by a few
# megacaps), sectors, industries above, commodities, rates, credit, currencies, countries.
ETFS = (
    "SPY", "RSP", "QQQ", "IWM", "DIA", "XLK", "XLE", "XLF", "XLV", "XLI", "XLU", "XLB", "XLP",
    "XLY", "XLC", "XLRE", "SMH", "SOXX", "ITA", "XAR", "XBI", "IBB", "XOP", "OIH", "URA",
    "COPX", "LIT", "GDX", "GLD", "SLV", "USO", "UNG", "DBC", "TLT", "IEF", "SHY", "HYG",
    "LQD", "UUP", "FXY", "EWJ", "EWT", "EWY", "EEM", "EFA", "FXI", "PAVE", "GRID",
)  # fmt: skip


@dataclass
class Member:
    ticker: str
    industry: str
    cik: int | None = None
    figi: str | None = None
    market_cap: float | None = None


def build(session: Session) -> list[Member]:
    """Choose members from the latest company metrics: per industry, its named companies
    first, then the largest by market cap up to the limit. A named company belongs to the
    industry naming it; otherwise to the first industry whose SIC codes claim it."""
    as_of = session.scalar(select(func.max(CompanyMetrics.as_of)))
    rows = session.execute(
        select(Security.ticker, Security.figi, Issuer.cik, Issuer.sic, CompanyMetrics.market_cap)
        .join(Security, Security.id == CompanyMetrics.security_id)
        .join(Issuer, Issuer.cik == CompanyMetrics.cik)
        .where(
            CompanyMetrics.as_of == as_of,
            Security.ticker.is_not(None),
            Security.mic.in_(MAJOR_MICS),
            CompanyMetrics.market_cap >= MIN_MARKET_CAP,
        )
        .order_by(CompanyMetrics.market_cap.desc())
    ).all()
    by_ticker = {r.ticker: r for r in rows}
    named = {by_ticker[t].cik: i.name for i in INDUSTRIES for t in i.include if t in by_ticker}
    taken: set[int] = set()
    members: list[Member] = []
    for industry in INDUSTRIES:
        pool = [by_ticker[t] for t in industry.include if t in by_ticker]
        pool += [r for r in rows if r.sic in industry.sics]
        chosen = 0
        for r in pool:
            if chosen >= industry.limit:
                break
            if (
                r.cik in taken
                or r.ticker in EXCLUDE
                or named.get(r.cik, industry.name) != industry.name
            ):
                continue
            taken.add(r.cik)
            members.append(Member(r.ticker, industry.name, r.cik, r.figi, r.market_cap))
            chosen += 1
    members += [Member(t, "etf") for t in ETFS]
    return members


def write(members: list[Member], path: Path = MEMBERS_FILE) -> None:
    with path.open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["ticker", "industry", "cik", "figi", "market_cap_bn"])
        for m in members:
            cap = f"{m.market_cap / 1e9:.1f}" if m.market_cap else ""
            out.writerow([m.ticker, m.industry, m.cik or "", m.figi or "", cap])


def read(path: Path = MEMBERS_FILE) -> list[Member]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [
            Member(r["ticker"], r["industry"], int(r["cik"]) if r["cik"] else None, r["figi"])
            for r in csv.DictReader(f)
        ]
