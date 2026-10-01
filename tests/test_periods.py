from datetime import date

import pytest

from fin_intel.periods import FiscalCalendar, FiscalSchedule, classify, period_type

D = date.fromisoformat


def fact(start, end, accn="A1", form="10-K", fy=2025, fp="FY", instant=False):
    return {
        "taxonomy": "us-gaap",
        "period_start": D(start or end),
        "period_end": D(end),
        "instant": instant,
        "accession": accn,
        "form": form,
        "filing_fiscal_year": fy,
        "filing_fiscal_period": fp,
    }


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2024-09-29", "2025-09-27", "annual"),  # 52 weeks
        ("2022-09-25", "2023-09-30", "annual"),  # 53 weeks
        ("2025-06-29", "2025-09-27", "quarter"),  # 13 weeks
        ("2025-01-01", "2025-06-30", "half"),
        ("2025-01-01", "2025-09-30", "nine_months"),
        ("2025-01-01", "2025-01-31", "other"),
    ],
)
def test_period_type(start, end, expected):
    assert period_type(D(start), D(end), instant=False) == expected


def test_comparatives_in_a_10k_get_their_own_fiscal_year():
    # Apple-style FY2025 10-K: current year, prior-year comparative, and a Q4 breakdown,
    # all tagged fy=2025 fp=FY by SEC.
    facts = classify(
        [
            fact("2024-09-29", "2025-09-27"),
            fact("2023-10-01", "2024-09-28"),
            fact("2025-06-29", "2025-09-27"),
            fact(None, "2024-09-28", instant=True),
        ]
    )
    assert [(f["fiscal_year"], f["fiscal_period"]) for f in facts] == [
        (2025, "FY"),
        (2024, "FY"),
        (2025, "Q4"),
        (2024, "FY"),
    ]


def test_june_year_end_quarters():
    calendar = FiscalCalendar(6, 30)
    assert calendar.locate(D("2025-09-30")) == (2026, 1)
    assert calendar.locate(D("2025-12-31")) == (2026, 2)
    assert calendar.locate(D("2026-03-31")) == (2026, 3)
    assert calendar.locate(D("2026-06-30")) == (2026, 4)


def test_retailer_naming_fiscal_year_after_prior_calendar_year():
    # Fiscal 2025 ends 2026-01-31; Q1 of fiscal 2025 ends 2025-05-03.
    facts = classify(
        [
            fact("2025-02-02", "2026-01-31", fy=2025),
            fact("2025-02-02", "2025-05-03", accn="Q", form="10-Q", fy=2025, fp="Q1"),
        ]
    )
    assert [(f["fiscal_year"], f["fiscal_period"]) for f in facts] == [(2025, "FY"), (2025, "Q1")]


def test_52_53_week_year_end_drifting_across_new_year():
    calendar = FiscalCalendar(1, 1)  # nominal end Jan 1; some years end in late December
    assert calendar.locate(D("2026-12-27")) == (2027, 4)
    assert calendar.locate(D("2027-01-03")) == (2027, 4)
    assert calendar.locate(D("2026-04-04")) == (2027, 1)


def test_no_annual_filings_leaves_fiscal_fields_empty():
    (f,) = classify([fact("2025-01-01", "2025-03-31", form="10-Q", fp="Q1")])
    assert f["period_type"] == "quarter"
    assert f["fiscal_year"] is None


def test_calendar_ignores_facts_dated_after_filing():
    # A 10-K filed 2025-11-01 for FY ending Sep 27 also carries a mis-tagged annual fact
    # ending after the filing date; it must not move the inferred fiscal year end.
    real = fact("2024-09-29", "2025-09-27")
    bogus = fact("2025-01-01", "2025-12-31")
    real["filed"] = bogus["filed"] = D("2025-11-01")
    (segment,) = FiscalSchedule.from_facts([real, bogus]).segments
    assert (segment.calendar.month, segment.calendar.day) == (9, 27)


def labels(facts):
    return [(f["fiscal_year"], f["fiscal_period"]) for f in classify(facts)]


def test_fiscal_year_end_change_june_to_december():
    # Sphere-style: June fiscal years, a Jul-Dec 2024 transition period, then calendar years.
    facts = [
        fact("2022-07-01", "2023-06-30", accn="K23", fy=2023),
        fact("2023-07-01", "2024-06-30", accn="K24", fy=2024),
        fact("2024-07-01", "2024-12-31", accn="KT", form="10-KT", fy=2024),
        fact("2025-01-01", "2025-12-31", accn="K25", fy=2025),
        # Quarters under the old calendar, the transition and the new calendar.
        fact("2023-07-01", "2023-09-30", accn="Q", form="10-Q", fy=2024, fp="Q1"),
        fact("2024-07-01", "2024-09-30", accn="Q", form="10-Q", fy=2025, fp="Q1"),
        fact("2025-01-01", "2025-03-31", accn="Q", form="10-Q", fy=2025, fp="Q1"),
    ]
    assert labels(facts) == [
        (2023, "FY"),
        (2024, "FY"),
        (2024, "T"),
        (2025, "FY"),
        (2024, "Q1"),
        (2024, "Q3"),
        (2025, "Q1"),
    ]


def test_transition_report_marks_new_year_end_before_first_new_10k():
    # Greif-style: October years, then an 11-month transition to September; no 10-K yet
    # under the new calendar.
    facts = [
        fact("2022-11-01", "2023-10-31", accn="K23", fy=2023),
        fact("2023-11-01", "2024-10-31", accn="K24", fy=2024),
        fact("2024-11-01", "2025-09-30", accn="KT", form="10-KT", fy=2025),
        fact("2025-10-01", "2025-12-31", accn="Q", form="10-Q", fy=2026, fp="Q1"),
    ]
    assert labels(facts) == [(2023, "FY"), (2024, "FY"), (2025, "T"), (2026, "Q1")]


def test_lone_odd_year_end_is_treated_as_tagging_error():
    facts = [
        fact("2021-01-01", "2021-12-31", accn="K21", fy=2021),
        fact("2021-07-01", "2022-06-30", accn="KX", fy=2022),  # mis-tagged
        fact("2022-01-01", "2022-12-31", accn="K22", fy=2022),
        fact("2023-01-01", "2023-12-31", accn="K23", fy=2023),
    ]
    assert len(FiscalSchedule.from_facts(facts).segments) == 1
