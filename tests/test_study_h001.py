from datetime import date

from fin_intel import edgar, population, world
from fin_intel.models import CorporateEvent, InsiderTransaction, Issuer
from fin_intel.studies import h001


def test_blowups_exclude_takeovers():
    failed = h001.blowups(
        [
            ("8-K", "1.03", date(2015, 5, 1)),  # bankruptcy
            ("15-12G", "", date(2016, 1, 1)),  # deregistration after the bankruptcy: failure
        ]
    )
    assert failed == [date(2015, 5, 1), date(2016, 1, 1)]
    bought = h001.blowups([("DEFM14A", "", date(2018, 3, 1)), ("25-NSE", "", date(2018, 9, 1))])
    assert bought == []  # a merger proxy in the year before: a takeover
    assert h001.blowups([("25-NSE", "", date(2019, 9, 1))]) == [date(2019, 9, 1)]
    # Delisting one class of securities while still reporting isn't a failure; stopping is.
    still_reporting = [date(2019, 11, 1), date(2020, 3, 1)]
    assert h001.blowups([("25-NSE", "", date(2019, 9, 1))], still_reporting) == []
    stopped = [date(2019, 5, 1), date(2019, 10, 15)]  # a straggling 10-Q within the grace
    assert h001.blowups([("25-NSE", "", date(2019, 9, 1))], stopped) == [date(2019, 9, 1)]
    assert (
        h001.blowups([("425", "", date(2019, 6, 1)), ("15-12B", "", date(2019, 9, 1))], stopped)
        == []
    )


def test_financial_signals():
    healthy = {
        "current_assets": 50.0, "current_liabilities": 20.0, "total_assets": 100.0,
        "total_liabilities": 40.0, "retained_earnings": 30.0, "operating_income": 10.0,
        "equity": 60.0, "cash": 30.0, "operating_cash_flow": 15.0, "capex": 5.0,
    }  # fmt: skip
    assert h001.altman_z2(healthy) > 2.6
    weak = {**healthy, "retained_earnings": -60.0, "operating_income": -10.0, "equity": 5.0,
            "total_liabilities": 95.0, "current_assets": 15.0}  # fmt: skip
    assert h001.altman_z2(weak) < h001.Z_DISTRESS
    assert h001.short_runway({"cash": 5.0, "operating_cash_flow": -8.0, "capex": 2.0}) is True
    assert h001.short_runway(healthy) is False
    assert h001.quarter_ends(date(2012, 2, 1), date(2012, 12, 31)) == [
        date(2012, 3, 31), date(2012, 6, 30), date(2012, 9, 30), date(2012, 12, 31)
    ]  # fmt: skip


def annual_facts(accession, fy, filed, assets, float_usd, retained, healthy=False):
    end = date(fy, 12, 31)
    cur_a, cur_l, liab, eq, income = (
        (0.6, 0.2, 0.4, 0.6, 100.0) if healthy else (0.3, 0.4, 0.9, 0.1, -20.0)
    )
    base = {"accession": accession, "form": "10-K", "filed": filed, "fy": fy, "fp": "FY"}
    gaap = {
        "Assets": assets, "AssetsCurrent": assets * cur_a, "LiabilitiesCurrent": assets * cur_l,
        "Liabilities": assets * liab, "RetainedEarningsAccumulatedDeficit": retained,
        "StockholdersEquity": assets * eq, "CashAndCashEquivalentsAtCarryingValue": 5.0,
    }  # fmt: skip
    facts = [
        {**base, "taxonomy": "us-gaap", "concept": c, "unit": "USD", "start": None, "end": end,
         "value": v}
        for c, v in gaap.items()
    ]  # fmt: skip
    facts += [
        {**base, "taxonomy": "us-gaap", "concept": "OperatingIncomeLoss", "unit": "USD",
         "start": date(fy, 1, 1), "end": end, "value": income},
        {**base, "taxonomy": "us-gaap", "concept": "NetCashProvidedByUsedInOperatingActivities",
         "unit": "USD", "start": date(fy, 1, 1), "end": end, "value": income * 1.5},
        {**base, "taxonomy": "dei", "concept": "EntityPublicFloat", "unit": "USD",
         "start": None, "end": date(fy, 6, 30), "value": float_usd},
    ]  # fmt: skip
    return facts


def index(rows):
    head = "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 40
    lines = [f"{c}|CO {c}|{f}|{d}|edgar/data/{c}/{a}.txt" for c, f, d, a in rows]
    return ("\n".join([head, *lines]) + "\n").encode("latin-1")


def test_rows_from_the_database(session):
    # Company 1001 fails in 2016; company 1002 is healthy; 1003 is too small to count.
    population.load(
        session,
        index(
            [
                (1001, "10-K", "2014-03-01", "0001001-14-000001"),
                (1001, "10-K", "2015-03-01", "0001001-15-000001"),
                (1002, "10-K", "2015-03-01", "0001002-15-000001"),
                (1003, "10-K", "2015-03-01", "0001003-15-000001"),
            ]
        ),
    )
    session.commit()
    edgar.load_company_facts(
        session,
        1001,
        world.facts_payload(
            annual_facts("0001001-15-000001", 2014, "2015-03-01", 1000.0, 200e6, -500.0)
            + annual_facts("0001001-14-000001", 2013, "2014-03-01", 1100.0, 300e6, -400.0)
        ),
    )
    edgar.load_company_facts(
        session,
        1002,
        world.facts_payload(
            annual_facts("0001002-15-000001", 2014, "2015-03-01", 900.0, 500e6, 400.0, True)
        ),
    )
    edgar.load_company_facts(
        session,
        1003,
        world.facts_payload(annual_facts("0001003-15-000001", 2014, "2015-03-01", 90.0, 10e6, 1.0)),
    )
    session.add_all(
        [
            CorporateEvent(
                accession="a1", item="", cik=1001, form="NT 10-Q", filed=date(2015, 5, 15)
            ),
            CorporateEvent(
                accession="a2", item="1.03", cik=1001, form="8-K", filed=date(2016, 2, 1)
            ),
            InsiderTransaction(
                key="s1", accession="i1", filing_date=date(2015, 4, 1), issuer_cik=1001,
                relationship="Officer", trans_date=date(2015, 3, 30), trans_code="S",
                shares=1_000_000, price=5.0,
            ),
        ]
    )  # fmt: skip
    session.commit()
    rows = h001.build_rows(session, date(2015, 3, 31), date(2015, 12, 31))
    by_key = {(r.cik, r.quarter): r for r in rows}
    assert {c for c, _ in by_key} == {1001, 1002}  # 1003's float is under $75 million
    june = by_key[(1001, date(2015, 6, 30))]
    assert june.signals["late_filing"] and june.signals["weak_z"] and june.signals["short_runway"]
    assert abs(june.net_selling - 5e6 / 200e6) < 1e-9
    assert june.blowup and june.blowup_on == date(2016, 2, 1)
    assert not by_key[(1001, date(2015, 3, 31))].signals["late_filing"]  # not public yet
    healthy = by_key[(1002, date(2015, 6, 30))]
    assert not healthy.blowup and not healthy.signals["weak_z"]
    assert session.get(Issuer, 1001) is not None

    result = h001.evaluate(rows, 0.01)
    assert result["blowups"] == 4 and result["signals"]["late_filing"]["recall"] == 0.75
    from fin_intel.studies import h001_benchmark

    bench = h001_benchmark.evaluate(rows)
    assert bench["z_coverage_annual"] == 1.0 and bench["blowups"] == 4


def test_z_from_annual_statements_only():
    """Quarterly balance sheets lack annual income: the as-tested Z'' goes missing, the
    annual one keeps the last full year."""
    year = ("total_assets", "total_liabilities", "current_assets", "current_liabilities",
            "retained_earnings", "equity")  # fmt: skip
    items = [(n, date(2014, 12, 31), date(2014, 12, 31), "instant", 10.0, date(2015, 3, 1))
             for n in year]  # fmt: skip
    items.append(
        ("operating_income", date(2014, 1, 1), date(2014, 12, 31), "annual", 1.0, date(2015, 3, 1))
    )
    items += [(n, date(2015, 3, 31), date(2015, 3, 31), "instant", 10.0, date(2015, 5, 1))
              for n in year]  # fmt: skip
    snaps = h001.annual_snapshots(items)
    assert h001.altman_z2(h001._latest(snaps, date(2015, 6, 30))) is None  # the 10-Q's
    full = [(d, f) for d, f in snaps if "operating_income" in f]
    assert h001.altman_z2(h001._latest(full, date(2015, 6, 30))) is not None
