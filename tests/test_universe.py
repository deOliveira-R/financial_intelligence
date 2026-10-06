from dataclasses import replace
from datetime import date

from fin_intel import universe
from fin_intel.models import CompanyMetrics, Issuer, Security

TODAY = date(2026, 10, 2)


def company(session, ticker, sic, cap, mic="XNAS"):
    cik = sum(ord(c) * 31**i for i, c in enumerate(ticker)) % 10**9
    session.add(Issuer(cik=cik, name=ticker, sic=sic))
    session.flush()
    security = Security(ticker=ticker, cik=cik, origin="sec", mic=mic, security_type="CS")
    session.add(security)
    session.flush()
    session.add(
        CompanyMetrics(
            security_id=security.id,
            as_of=TODAY,
            cik=cik,
            price=10.0,
            period_end=date(2026, 6, 30),
            market_cap=cap,
        )
    )


def test_build_ranks_by_market_cap_within_industries(session, monkeypatch, tmp_path):
    semis, power, *_, industrials = universe.INDUSTRIES
    monkeypatch.setattr(
        universe,
        "INDUSTRIES",
        (
            replace(semis, limit=2, include=("SNPS",)),
            replace(power, limit=5, include=()),
            replace(industrials, sics=frozenset({3531}), include=("PWR",)),
        ),
    )
    monkeypatch.setattr(universe, "ETFS", ("SPY",))
    company(session, "NVDA", 3674, 5e12)
    company(session, "AMD", 3674, 1e12)
    company(session, "SNPS", 7372, 9e10)  # software SIC, named explicitly
    company(session, "SMALL", 3674, 5e8)  # under the size floor
    company(session, "OTCX", 3674, 9e9, mic="OTCM")  # not a major exchange
    company(session, "CEG", 4911, 9e10)
    company(session, "PWR", 1731, 1e11)  # power's SIC, but named by industrials
    company(session, "CAT", 3531, 4e11)
    session.commit()

    members = universe.build(session)
    by_industry = {}
    for m in members:
        by_industry.setdefault(m.industry, []).append(m.ticker)
    assert by_industry == {
        "semiconductors": ["SNPS", "NVDA"],  # named first, then the largest; limit 2
        "ai_infrastructure_power": ["CEG"],
        "industrials_reshoring": ["PWR", "CAT"],
        "etf": ["SPY"],
    }

    path = tmp_path / "members.csv"
    universe.write(members, path)
    assert [(m.ticker, m.industry, m.cik) for m in universe.read(path)] == [
        (m.ticker, m.industry, m.cik) for m in members
    ]
