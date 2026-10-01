"""Turn stored XBRL facts into consistent series: latest values, split-adjusted, with Q4 filled.

- Latest per period: later filings (restatements, comparatives) supersede earlier ones.
- Split adjustment: a filing reports share counts and per-share values in the share terms
  of its filing date. Values filed before a split are put in today's terms using the split
  history from price data, so EPS and share-count series don't jump at each split.
- Q4: many companies never report a standalone fourth quarter, only the full year. For
  additive (monetary flow) values it is derived as FY minus the first nine months.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta

from fin_intel.periods import period_type


@dataclass(frozen=True)
class Fact:
    concept: str
    unit: str
    period_start: date
    period_end: date
    period_type: str | None
    value: float
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed: date | None
    accession: str | None
    split_adjustment: float = 1.0  # factor applied to the reported value's share basis
    derived: bool = False

    @classmethod
    def from_row(cls, row) -> Fact:
        """From a facts/filings/concepts join (see api.fundamentals)."""
        return cls(
            concept=row.name,
            unit=row.unit,
            period_start=row.period_start,
            period_end=row.period_end,
            period_type=row.period_type,
            value=row.value,
            fiscal_year=row.fiscal_year,
            fiscal_period=row.fiscal_period,
            form=row.form,
            filed=row.filed,
            accession=row.accession,
        )


def latest_per_period(facts: Iterable[Fact]) -> list[Fact]:
    """Keep the most recently filed value for each (unit, period)."""
    latest: dict[tuple, Fact] = {}
    for f in sorted(facts, key=lambda f: f.filed or date.min):
        latest[(f.unit, f.period_start, f.period_end)] = f
    return sorted(latest.values(), key=lambda f: (f.unit, f.period_end, f.period_start))


def is_share_count(unit: str) -> bool:
    return unit == "shares"


def is_per_share(unit: str) -> bool:
    return unit.endswith("/shares")


def split_adjust(facts: Iterable[Fact], splits: list[tuple[date, float]]) -> list[Fact]:
    """Restate share counts and per-share values filed before each split in post-split terms.

    `splits` holds (ex-date, factor), e.g. (2024-06-10, 10.0) for a 10-for-1 split. A filing
    made on or after the ex-date is assumed to already reflect the split.
    """
    out = []
    for f in facts:
        if f.filed is None or not (is_share_count(f.unit) or is_per_share(f.unit)):
            out.append(f)
            continue
        factor = math.prod(ratio for ex_date, ratio in splits if ex_date > f.filed)
        if factor == 1.0:
            out.append(f)
            continue
        value = f.value * factor if is_share_count(f.unit) else f.value / factor
        out.append(replace(f, value=value, split_adjustment=factor))
    return out


def is_additive(f: Fact) -> bool:
    """Flow values that sum across quarters. Share counts are averages; per-share and
    ratio values don't add up either."""
    return f.period_type != "instant" and "shares" not in f.unit and f.unit != "pure"


def derive_q4(facts: list[Fact]) -> list[Fact]:
    """Add a derived Q4 (FY minus 9M, or FY minus Q1-Q3) where none was reported.

    Expects latest_per_period output. Derived facts are marked `derived=True`.
    """
    by_year: dict[tuple, dict[str, Fact]] = {}
    for f in facts:
        if f.fiscal_year is not None and f.fiscal_period and is_additive(f):
            by_year.setdefault((f.unit, f.fiscal_year), {})[f.fiscal_period] = f

    derived = []
    for periods in by_year.values():
        fy = periods.get("FY")
        if fy is None or "Q4" in periods:
            continue
        if (nine := periods.get("9M")) and nine.period_start == fy.period_start:
            year_to_q3, sources = nine.value, [nine]
        elif all(q in periods for q in ("Q1", "Q2", "Q3")):
            sources = [periods[q] for q in ("Q1", "Q2", "Q3")]
            if sources[0].period_start != fy.period_start:
                continue
            year_to_q3 = sum(s.value for s in sources)
        else:
            continue
        start = sources[-1].period_end + timedelta(days=1)
        # Guard against odd years (transition periods, 53-week quirks gone wrong).
        if period_type(start, fy.period_end, instant=False) != "quarter":
            continue
        derived.append(
            replace(
                fy,
                period_start=start,
                period_type="quarter",
                fiscal_period="Q4",
                value=fy.value - year_to_q3,
                filed=max(s.filed or date.min for s in [fy, *sources]),
                accession=None,
                derived=True,
            )
        )
    return sorted(facts + derived, key=lambda f: (f.unit, f.period_end, f.period_start))
