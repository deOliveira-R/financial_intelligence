from datetime import date

from fin_intel.fundamentals import Fact, derive_q4, latest_per_period, split_adjust

D = date.fromisoformat


def fact(start, end, value, unit="USD", fy=2025, fp="FY", ptype="annual", filed="2025-11-01"):
    return Fact(
        concept="X",
        unit=unit,
        period_start=D(start),
        period_end=D(end),
        period_type=ptype,
        value=value,
        fiscal_year=fy,
        fiscal_period=fp,
        form="10-K",
        filed=D(filed),
        accession="A",
    )


def test_split_adjust_only_values_filed_before_split():
    splits = [(D("2024-06-10"), 10.0), (D("2021-07-20"), 4.0)]
    eps_old = fact("2020-01-27", "2021-01-31", 6.90, unit="USD/shares", filed="2021-02-26")
    eps_mid = fact("2022-01-31", "2023-01-29", 1.74, unit="USD/shares", filed="2023-02-24")
    eps_new = fact("2024-01-29", "2025-01-26", 2.94, unit="USD/shares", filed="2025-02-26")
    shares = fact("2023-01-30", "2023-01-30", 2.47e9, unit="shares", filed="2023-02-24")
    revenue = fact("2022-01-31", "2023-01-29", 27e9, filed="2023-02-24")

    out = split_adjust([eps_old, eps_mid, eps_new, shares, revenue], splits)
    assert [round(f.value, 4) for f in out[:3]] == [0.1725, 0.174, 2.94]
    assert [f.split_adjustment for f in out] == [40.0, 10.0, 1.0, 10.0, 1.0]
    assert out[3].value == 2.47e10
    assert out[4].value == 27e9


def test_latest_per_period_prefers_restatement():
    original = fact("2024-01-01", "2024-12-31", 100, filed="2025-02-01")
    restated = fact("2024-01-01", "2024-12-31", 90, filed="2026-02-01")
    assert [f.value for f in latest_per_period([restated, original])] == [90]


def test_derive_q4_from_nine_months():
    facts = [
        fact("2024-09-29", "2025-09-27", 416, fp="FY"),
        fact("2024-09-29", "2025-06-28", 313.5, fp="9M", ptype="nine_months"),
    ]
    q4 = [f for f in derive_q4(facts) if f.derived]
    assert len(q4) == 1
    assert (q4[0].fiscal_period, q4[0].value) == ("Q4", 102.5)
    assert (q4[0].period_start, q4[0].period_end) == (D("2025-06-29"), D("2025-09-27"))


def test_derive_q4_from_three_quarters():
    facts = [
        fact("2025-01-01", "2025-12-31", 100, fp="FY"),
        fact("2025-01-01", "2025-03-31", 20, fp="Q1", ptype="quarter"),
        fact("2025-04-01", "2025-06-30", 25, fp="Q2", ptype="quarter"),
        fact("2025-07-01", "2025-09-30", 30, fp="Q3", ptype="quarter"),
    ]
    (q4,) = [f for f in derive_q4(facts) if f.derived]
    assert q4.value == 25


def test_no_q4_for_per_share_or_existing_q4_or_transition():
    eps = [
        fact("2025-01-01", "2025-12-31", 4.0, unit="USD/shares"),
        fact("2025-01-01", "2025-09-30", 3.0, unit="USD/shares", fp="9M", ptype="nine_months"),
    ]
    reported = [
        fact("2025-01-01", "2025-12-31", 100),
        fact("2025-01-01", "2025-09-30", 75, fp="9M", ptype="nine_months"),
        fact("2025-10-01", "2025-12-31", 26, fp="Q4", ptype="quarter"),
    ]
    mismatched_start = [  # 9M doesn't start with the fiscal year
        fact("2025-01-01", "2025-12-31", 100),
        fact("2024-12-01", "2025-08-31", 75, fp="9M", ptype="nine_months"),
    ]
    for facts in (eps, reported, mismatched_start):
        assert not any(f.derived for f in derive_q4(facts))


def test_split_adjust_ignores_announced_future_splits():
    eps = fact("2025-01-01", "2025-12-31", 4.0, unit="USD/shares", filed="2026-02-01")
    (out,) = split_adjust([eps], [(D("2026-12-01"), 2.0)], as_of=D("2026-10-01"))
    assert (out.value, out.split_adjustment) == (4.0, 1.0)
