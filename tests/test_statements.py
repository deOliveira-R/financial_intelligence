from datetime import date

import pytest
from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import Concept, StatementItem


def fact(start, end, val, accn, fy, fp, form="10-K", filed="2025-11-01"):
    out = {"end": end, "val": val, "accn": accn, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        out["start"] = start
    return out


def payload(cik, facts_by_concept):
    """facts_by_concept: {(taxonomy, concept): (unit, [facts])}"""
    out: dict = {"cik": cik, "entityName": f"Co {cik}", "facts": {}}
    for (taxonomy, concept), (unit, facts) in facts_by_concept.items():
        out["facts"].setdefault(taxonomy, {})[concept] = {"label": concept, "units": {unit: facts}}
    return out


def items(session, cik, line_item):
    rows = session.execute(
        select(
            StatementItem.fiscal_year,
            StatementItem.fiscal_period,
            StatementItem.value,
            Concept.name,
        )
        .join(Concept, Concept.id == StatementItem.concept_id)
        .where(StatementItem.cik == cik, StatementItem.line_item == line_item)
        .order_by(StatementItem.period_end, StatementItem.period_start)
    )
    return [tuple(r) for r in rows]


def test_total_revenue_wins_and_concept_switches_are_continuous(session):
    ingest.load_company_facts(
        session,
        1,
        payload(
            1,
            {
                # FY2016 only as SalesRevenueNet (pre-ASC 606); FY2025 has total and contract.
                ("us-gaap", "SalesRevenueNet"): (
                    "USD",
                    [
                        fact(
                            "2015-02-01", "2016-01-31", 482.0, "A16", 2016, "FY", filed="2016-03-30"
                        )
                    ],
                ),
                ("us-gaap", "Revenues"): (
                    "USD",
                    [fact("2024-02-01", "2025-01-31", 681.0, "A25", 2025, "FY")],
                ),
                ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"): (
                    "USD",
                    [fact("2024-02-01", "2025-01-31", 674.5, "A25", 2025, "FY")],
                ),
            },
        ),
    )
    assert items(session, 1, "revenue") == [
        (2016, "FY", 482.0, "SalesRevenueNet"),
        (2025, "FY", 681.0, "Revenues"),
    ]


def test_ifrs_filer_maps_to_the_same_items(session):
    ingest.load_company_facts(
        session,
        2,
        payload(
            2,
            {
                ("ifrs-full", "Revenue"): (
                    "USD",
                    [
                        fact(
                            "2024-01-01",
                            "2024-12-31",
                            8.4e9,
                            "Z24",
                            2024,
                            "FY",
                            form="20-F",
                            filed="2025-03-12",
                        )
                    ],
                ),
                ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"): (
                    "USD",
                    [
                        fact(
                            "2024-01-01",
                            "2024-12-31",
                            2.1e9,
                            "Z24",
                            2024,
                            "FY",
                            form="20-F",
                            filed="2025-03-12",
                        )
                    ],
                ),
                ("ifrs-full", "LeaseLiabilities"): (
                    "USD",
                    [
                        fact(
                            None,
                            "2024-12-31",
                            5.0e9,
                            "Z24",
                            2024,
                            "FY",
                            form="20-F",
                            filed="2025-03-12",
                        )
                    ],
                ),
            },
        ),
    )
    assert items(session, 2, "revenue")[0][2:] == (8.4e9, "Revenue")
    assert items(session, 2, "net_income")[0][2:] == (
        2.1e9,
        "ProfitLossAttributableToOwnersOfParent",
    )
    assert items(session, 2, "lease_liabilities")[0][1:] == ("FY", 5.0e9, "LeaseLiabilities")


def test_balance_items_take_instants_and_flows_take_durations(session):
    ingest.load_company_facts(
        session,
        3,
        payload(
            3,
            {
                ("us-gaap", "Assets"): (
                    "USD",
                    [fact(None, "2025-12-31", 100.0, "K25", 2025, "FY")],
                ),
                ("us-gaap", "Revenues"): (
                    "USD",
                    [
                        fact("2025-01-01", "2025-12-31", 50.0, "K25", 2025, "FY"),
                        fact(
                            "2025-01-01", "2025-01-31", 4.0, "K25", 2025, "FY"
                        ),  # a month: neither
                    ],
                ),
            },
        ),
    )
    assert [r[2] for r in items(session, 3, "total_assets")] == [100.0]
    assert [r[2] for r in items(session, 3, "revenue")] == [50.0]


def test_q4_derived_for_flows_not_for_eps(session):
    quarters = [
        ("2025-01-01", "2025-03-31", 10.0, "Q1"),
        ("2025-04-01", "2025-06-30", 11.0, "Q2"),
        ("2025-07-01", "2025-09-30", 12.0, "Q3"),
    ]
    revenue = [
        fact(s, e, v, f"Q-{q}", 2025, q, form="10-Q", filed="2025-10-30") for s, e, v, q in quarters
    ]
    revenue.append(fact("2025-01-01", "2025-12-31", 50.0, "K25", 2025, "FY", filed="2026-02-20"))
    eps = [
        fact(s, e, 1.0, f"Q-{q}", 2025, q, form="10-Q", filed="2025-10-30")
        for s, e, _, q in quarters
    ]
    eps.append(fact("2025-01-01", "2025-12-31", 4.5, "K25", 2025, "FY", filed="2026-02-20"))
    ingest.load_company_facts(
        session,
        4,
        payload(
            4,
            {
                ("us-gaap", "Revenues"): ("USD", revenue),
                ("us-gaap", "EarningsPerShareDiluted"): ("USD/shares", eps),
            },
        ),
    )
    q4 = [r for r in items(session, 4, "revenue") if r[1] == "Q4"]
    assert q4 == [(2025, "Q4", pytest.approx(17.0), "Revenues")]
    assert not [r for r in items(session, 4, "eps_diluted") if r[1] == "Q4"]


def test_restatement_wins(session):
    ingest.load_company_facts(
        session,
        5,
        payload(
            5,
            {
                ("us-gaap", "NetIncomeLoss"): (
                    "USD",
                    [
                        fact(
                            "2024-01-01", "2024-12-31", 10.0, "K24", 2024, "FY", filed="2025-02-01"
                        ),
                        fact(
                            "2024-01-01", "2024-12-31", 9.0, "K25", 2025, "FY", filed="2026-02-01"
                        ),
                    ],
                ),
            },
        ),
    )
    assert [r[2] for r in items(session, 5, "net_income")] == [9.0]


def test_rederiving_replaces_rows(session):
    data = payload(
        6, {("us-gaap", "Assets"): ("USD", [fact(None, "2025-12-31", 1.0, "K", 2025, "FY")])}
    )
    ingest.load_company_facts(session, 6, data)
    ingest.load_company_facts(session, 6, data)
    assert len(items(session, 6, "total_assets")) == 1
    assert session.scalar(select(StatementItem.period_end).where(StatementItem.cik == 6)) == date(
        2025, 12, 31
    )


def test_company_without_facts_loads_cleanly(session):
    # Some filers' company facts contain no facts at all; this crashed the bulk load.
    assert (
        ingest.load_company_facts(session, 7, {"cik": 7, "entityName": "Empty", "facts": {}}) == 0
    )
    assert items(session, 7, "revenue") == []
