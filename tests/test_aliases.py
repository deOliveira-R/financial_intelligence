from datetime import date

import respx
from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import Account, DailyBar, PortfolioTransaction, Security, TickerHistory
from fin_intel.providers import MassiveProvider
from fin_intel.rebuild import rebuild

API = "https://api.massive.com"
SEC = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [[1864531, "VSee Health", "VSEE", "Nasdaq"]],
}
DAY1, DAY2 = date(2026, 8, 3), date(2026, 8, 10)


def grouped(day, results):
    respx.get(f"{API}/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}").respond(
        json={"results": [{"T": t, "o": c, "h": c, "l": c, "c": c, "v": 100} for t, c in results]}
    )


def reference(results):
    respx.get(f"{API}/v3/reference/tickers", params={"active": "true"}).respond(
        json={"results": results}
    )
    respx.get(f"{API}/v3/reference/tickers", params={"active": "false"}).respond(
        json={"results": []}
    )


def row(symbol, figi, cik=1864531):
    return {
        "ticker": symbol,
        "name": "VSee",
        "type": "CS",
        "primary_exchange": "XNAS",
        "cik": f"{cik:010d}",
        "composite_figi": figi,
    }


@respx.mock
def test_variant_symbol_becomes_an_alias_not_a_second_security(session, raw_store):
    ingest.load_company_tickers(session, SEC, date(2026, 8, 1))
    session.commit()  # syncs commit; the raw store's connection would roll this back
    massive = MassiveProvider(raw_store=raw_store)
    reference([row("VSEE", "BBG0130V90C0")])
    ingest.sync_reference_tickers(session, massive)  # enriches VSEE with its FIGI
    grouped(DAY1, [("VSEE", 1.0)])
    ingest.sync_market_daily(session, massive, DAY1)
    # After a reverse split Nasdaq lists it as VSEED for a few weeks; SEC still says VSEE.
    reference([row("VSEED", "BBG0130V90C0")])
    ingest.sync_reference_tickers(session, massive)
    grouped(DAY2, [("VSEED", 10.0)])
    ingest.sync_market_daily(session, massive, DAY2)

    (security,) = session.scalars(select(Security)).all()
    assert (security.ticker, security.origin) == ("VSEE", "sec")
    assert set(session.scalars(select(TickerHistory.ticker))) == {"VSEE", "VSEED"}
    bars = session.execute(select(DailyBar.date, DailyBar.close).order_by(DailyBar.date)).all()
    assert bars == [(DAY1, 1.0), (DAY2, 10.0)]  # both under the one security


@respx.mock
def test_conflicting_figi_never_releases_an_sec_symbol(session, raw_store):
    ingest.load_company_tickers(session, SEC, date(2026, 8, 1))
    session.commit()  # syncs commit; the raw store's connection would roll this back
    massive = MassiveProvider(raw_store=raw_store)
    reference([row("VSEE", "BBG0130V90C0")])
    ingest.sync_reference_tickers(session, massive)
    reference([row("VSEE", "BBG000OTHER1", cik=999)])  # another instrument under VSEE
    ingest.sync_reference_tickers(session, massive)
    (security,) = session.scalars(select(Security)).all()
    assert (security.ticker, security.figi, security.active) == ("VSEE", "BBG0130V90C0", True)


def test_resolver_falls_back_to_the_last_holder(session):
    security = Security(ticker="VSEE", origin="sec")
    session.add(security)
    session.flush()
    session.add(
        TickerHistory(security_id=security.id, ticker="VSEED", first_seen=DAY1, last_seen=DAY1)
    )
    session.commit()
    resolver = ingest.SymbolResolver(session)
    assert resolver.resolve("VSEED", DAY1) == security.id
    assert resolver.resolve("VSEED", DAY2) == security.id  # after the last reference sync
    assert resolver.resolve("NOPE", DAY2) is None


def test_renamed_holder_doesnt_own_its_new_symbol_before_the_rename(session):
    # BNY Mellon traded as BK until mid-2026; "BNY" was a municipal bond fund before that.
    mellon = Security(ticker="BNY", origin="sec")
    session.add(mellon)
    session.flush()
    session.add_all(
        [
            TickerHistory(
                security_id=mellon.id,
                ticker="BK",
                first_seen=date(2024, 10, 1),
                last_seen=date(2026, 5, 21),
            ),
            TickerHistory(
                security_id=mellon.id,
                ticker="BNY",
                first_seen=date(2026, 7, 1),
                last_seen=date(2026, 10, 4),
            ),
        ]
    )
    session.commit()
    resolver = ingest.SymbolResolver(session)
    assert resolver.resolve("BK", date(2025, 1, 23)) == mellon.id
    assert resolver.resolve("BNY", date(2025, 1, 23)) is None  # the fund's row, not Mellon's
    assert resolver.resolve("BNY", date(2026, 8, 3)) == mellon.id


@respx.mock
def test_market_rebuild_keeps_portfolio_links(session, raw_store):
    respx.get("https://www.sec.gov/files/company_tickers_exchange.json").respond(json=SEC)
    from fin_intel.providers import SecProvider

    ingest.sync_tickers(session, SecProvider(raw_store=raw_store))
    session.add(
        Account(id=1, name="Taxable", broker="fidelity", account_type="taxable", taxable=True)
    )
    session.commit()
    vsee = session.scalar(select(Security).where(Security.ticker == "VSEE"))
    session.add(
        PortfolioTransaction(
            account_id=1,
            trade_date=DAY1,
            action="buy",
            symbol="VSEE",
            security_id=vsee.id,
            quantity=1,
            price=1,
            source="manual",
            source_ref="x",
            imported_at=DAY1,
        )
    )
    session.commit()
    with respx.mock:
        rebuild(session, raw_store, "market")
    tx = session.scalar(select(PortfolioTransaction))
    assert tx.security_id == session.scalar(select(Security.id).where(Security.ticker == "VSEE"))
