from datetime import date

import respx
from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import Issuer, Security
from fin_intel.providers import MassiveProvider

D1, D2, D3 = date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 15)


def ref(ticker, type_="CS", cik=None, figi=None, name=None, mic="XNAS"):
    return {
        "ticker": ticker,
        "name": name or f"{ticker} name",
        "type": type_,
        "cik": f"{cik:010d}" if cik else None,
        "composite_figi": figi,
        "share_class_figi": figi and figi.replace("BBG", "SCF"),
        "primary_exchange": mic,
        "active": True,
    }


def sec_sync(session, rows, today=D1):
    payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}
    ingest.load_company_tickers(session, payload, today)
    session.commit()


def massive_sync(session, results, today=D1, market="stocks"):
    ingest.load_massive_tickers(session, market, {"results": results}, today)
    ingest.deactivate_unseen_massive(session, market, today)
    session.commit()


def sec(session, ticker):
    return ingest.get_security(session, ticker)


def test_enriches_sec_securities_but_keeps_sec_name_and_cik(session):
    sec_sync(session, [[320193, "Apple Inc.", "AAPL", "Nasdaq"]])
    massive_sync(session, [ref("AAPL", cik=320193, figi="BBG000B9XRY4", name="Apple Inc")])
    aapl = sec(session, "AAPL")
    assert (aapl.name, aapl.cik, aapl.origin) == ("Apple Inc.", 320193, "sec")
    assert (aapl.security_type, aapl.figi, aapl.mic) == ("CS", "BBG000B9XRY4", "XNAS")


def test_creates_etfs_with_their_issuer(session):
    massive_sync(session, [ref("XLV", "ETF", cik=1064641, figi="BBG000BJ7007", mic="ARCX")])
    xlv = sec(session, "XLV")
    assert (xlv.origin, xlv.security_type, xlv.cik, xlv.active) == ("massive", "ETF", 1064641, True)
    assert session.get(Issuer, 1064641) is not None


def test_figi_identifies_renames(session):
    massive_sync(session, [ref("OLDX", "ETF", figi="BBG0000000A1")])
    original_id = sec(session, "OLDX").id
    massive_sync(session, [ref("NEWX", "ETF", figi="BBG0000000A1")], today=D2)
    assert sec(session, "NEWX").id == original_id
    assert sec(session, "OLDX").id == original_id  # old symbol resolves through history


def test_symbol_with_a_new_figi_is_a_reuse(session):
    massive_sync(session, [ref("ABCD", "ETF", figi="BBG0000000A1")])
    old_id = sec(session, "ABCD").id
    massive_sync(session, [ref("ABCD", "ETF", figi="BBG0000000B2")], today=D2)
    assert sec(session, "ABCD").id != old_id
    old = session.get(Security, old_id)
    assert old.ticker is None and not old.active


def test_cik_conflict_keeps_sec_cik_but_takes_type_and_figi(session):
    sec_sync(session, [[1, "Real Co", "RCO", "NYSE"]])
    massive_sync(session, [ref("RCO", cik=2, figi="BBG0000000C3")])
    rco = sec(session, "RCO")
    assert (rco.cik, rco.figi, rco.security_type) == (1, "BBG0000000C3", "CS")


def test_sec_sync_leaves_massive_securities_alone(session):
    # Same trust CIK: SEC must neither deactivate the ETF nor treat it as renamed.
    massive_sync(session, [ref("XLV", "ETF", cik=1064641, figi="BBG000BJ7007")])
    sec_sync(session, [[1064641, "Select Sector SPDR Trust", "XLK", "NYSE"]], today=D2)
    xlv = sec(session, "XLV")
    assert (xlv.ticker, xlv.active, xlv.origin) == ("XLV", True, "massive")
    assert sec(session, "XLK").id != xlv.id


def test_sec_claims_a_massive_security_it_lists(session):
    massive_sync(session, [ref("NEWCO", cik=99, figi="BBG0000000D4")])
    sec_sync(session, [[99, "NewCo Inc.", "NEWCO", "Nasdaq"]], today=D2)
    assert sec(session, "NEWCO").origin == "sec"


def test_deactivation_only_touches_the_list_that_created_a_security(session):
    sec_sync(session, [[1, "Co", "CO", "NYSE"]])
    massive_sync(session, [ref("ETFA", "ETF", figi="BBG0000000E5"), ref("CO", cik=1)])
    massive_sync(session, [ref("OTCX", "OS", figi="BBG0000000F6", mic=None)], market="otc")
    # The next main-list sync no longer lists ETFA or CO.
    massive_sync(session, [ref("ETFB", "ETF", figi="BBG0000000G7")], today=D2)
    assert not sec(session, "ETFA").active  # Massive-created, gone from Massive's list
    assert sec(session, "CO").active  # SEC-created: SEC decides
    assert sec(session, "OTCX").active  # created by the OTC list, not the main one
    # An OTC name that uplists moves to the main list's care.
    massive_sync(session, [ref("OTCX", "CS", figi="BBG0000000F6")], today=D3)
    assert sec(session, "OTCX").origin == "massive"


@respx.mock
def test_reference_sync_reads_every_page(session, raw_store):
    first = respx.get(
        "https://api.massive.com/v3/reference/tickers",
        params={"market": "stocks", "active": "true", "limit": "1000"},
    ).respond(
        json={
            "results": [ref("XLV", "ETF", figi="BBG000BJ7007")],
            "next_url": "https://api.massive.com/v3/reference/tickers?cursor=p2",
        }
    )
    respx.get("https://api.massive.com/v3/reference/tickers", params={"cursor": "p2"}).respond(
        json={"results": [ref("VYM", "ETF", figi="BBG000Q3DFH4")]}
    )
    respx.get("https://api.massive.com/v3/reference/tickers", params={"active": "false"}).respond(
        json={"results": []}
    )
    rows = ingest.sync_reference_tickers(session, MassiveProvider(raw_store=raw_store))
    assert rows == 2 and first.called
    types = session.execute(select(Security.ticker, Security.security_type)).all()
    assert sorted(types) == [("VYM", "ETF"), ("XLV", "ETF")]


@respx.mock
def test_market_data_synced_before_discovery_is_backfilled(session, raw_store):
    """Order independence: bars and dividends fetched before a security is known are
    loaded from raw when it's discovered, and a rebuild produces the same rows."""
    from fin_intel.models import CorporateAction, DailyBar
    from fin_intel.rebuild import rebuild

    api = "https://api.massive.com"
    respx.get(f"{api}/v2/aggs/grouped/locale/us/market/stocks/2026-09-29").respond(
        json={"results": [{"T": "XLV", "o": 1, "h": 1, "l": 1, "c": 150.0, "v": 10}]}
    )
    respx.get(f"{api}/stocks/v1/dividends").respond(
        json={"results": [{"ticker": "XLV", "ex_dividend_date": "2026-09-22", "cash_amount": 0.5}]}
    )
    respx.get(f"{api}/v3/reference/tickers").respond(
        json={"results": [ref("XLV", "ETF", figi="BBG000BJ7007")]}
    )
    massive = MassiveProvider(raw_store=raw_store)
    # Market data first: XLV is unknown, so its rows are skipped...
    assert ingest.sync_market_daily(session, massive, date(2026, 9, 29)) == 0
    assert ingest.sync_market_actions(session, massive, "dividends", date(2026, 1, 1)) == 0
    # ...until the reference sync discovers it.
    ingest.sync_reference_tickers(session, massive)

    def rows():
        return (
            session.execute(select(DailyBar.date, DailyBar.close)).all(),
            session.execute(select(CorporateAction.ex_date, CorporateAction.value)).all(),
        )

    live = rows()
    assert live == ([(date(2026, 9, 29), 150.0)], [(date(2026, 9, 22), 0.5)])
    rebuild(session, raw_store, "all")
    assert rows() == live


@respx.mock
def test_submissions_set_sic_codes(session, raw_store):
    from fin_intel.models import Issuer
    from fin_intel.providers import SecProvider

    session.add(Issuer(cik=1109357, name="Exelon"))
    session.commit()
    respx.get("https://data.sec.gov/submissions/CIK0001109357.json").respond(
        json={
            "sic": "4931",
            "sicDescription": "Electric & Other Services Combined",
            "category": "Large accelerated filer",
            "filings": {},
        }
    )
    assert ingest.sync_submissions(session, SecProvider(raw_store=raw_store), 1109357) == 1
    exelon = session.get(Issuer, 1109357)
    assert (exelon.sic, exelon.filer_category) == (4931, "Large accelerated filer")
