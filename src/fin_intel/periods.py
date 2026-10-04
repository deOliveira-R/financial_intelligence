"""Classify XBRL facts by their own reporting period.

SEC's `fy`/`fp` fields describe the filing a fact appeared in, not the fact: a FY2025
10-K also reports FY2024 comparatives and quarterly breakdowns, all tagged "2025 FY".
Here each fact gets its own period type (from its duration) and fiscal year/period
(from where its end date falls relative to the company's fiscal year end).
"""

from collections import Counter
from datetime import date, timedelta
from typing import Any

# "annual": annual reports from other regulators (DART, EDINET, ESEF; see world.py).
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "annual"}
# Transition reports cover the stub period when a company changes its fiscal year end;
# their period end marks the new fiscal year end.
TRANSITION_FORMS = {"10-KT", "10-KT/A"}

# Inclusive duration ranges in days. 52/53-week years and 13/14-week quarters
# make durations drift a few days around the nominal value.
DURATIONS = [
    ("quarter", 80, 100),
    ("half", 170, 195),
    ("nine_months", 260, 285),
    ("annual", 350, 380),
]

# A period ending up to this many days after the nominal fiscal year end still
# belongs to that fiscal year (52/53-week calendars drift around the nominal date).
FYE_TOLERANCE = timedelta(days=10)


def period_type(start: date, end: date, instant: bool) -> str:
    if instant:
        return "instant"
    days = (end - start).days + 1
    for name, low, high in DURATIONS:
        if low <= days <= high:
            return name
    return "other"


class FiscalCalendar:
    """Maps a date to (fiscal_year, quarter) given a nominal fiscal year end."""

    def __init__(self, year_end_month: int, year_end_day: int, year_offset: int = 0):
        self.month = year_end_month
        self.day = year_end_day
        self.year_offset = year_offset

    def _nominal_end(self, year: int) -> date:
        # Clamp Feb 29 (and similar) to the month's last valid day.
        day = self.day
        while True:
            try:
                return date(year, self.month, day)
            except ValueError:
                day -= 1

    def fiscal_year_end(self, d: date) -> date:
        """The nominal end of the fiscal year containing d."""
        floor = d - FYE_TOLERANCE
        for year in (d.year - 1, d.year, d.year + 1):
            end = self._nominal_end(year)
            if end >= floor:
                return end
        raise AssertionError("unreachable")

    def locate(self, d: date) -> tuple[int, int]:
        fye = self.fiscal_year_end(d)
        quarter = 4 - round((fye - d).days / 91.31)
        return fye.year + self.year_offset, min(4, max(1, quarter))


def _day_distance(a: date, b: date) -> int:
    """Days between two dates' month/day positions, wrapping around New Year."""

    def day_of_year(d: date) -> int:
        return d.replace(year=2000).timetuple().tm_yday  # leap year, so Feb 29 is valid

    diff = abs(day_of_year(a) - day_of_year(b))
    return min(diff, 366 - diff)


class _Segment:
    def __init__(self, ends: list[tuple[date, int]]):
        self.ends = ends  # (fiscal year end, SEC fy label), sorted
        last = ends[-1][0]
        self.calendar = FiscalCalendar(last.month, last.day)
        # Most companies name the fiscal year after the calendar year it ends in;
        # some (e.g. retailers ending in late January) use the prior year.
        offsets = Counter(label - self.calendar.fiscal_year_end(end).year for end, label in ends)
        self.calendar.year_offset = offsets.most_common(1)[0][0]

    @property
    def last_end(self) -> date:
        return self.ends[-1][0]


class FiscalSchedule:
    """The company's fiscal calendars over time, one per fiscal-year-end regime.

    Most companies have one. A company that changes its fiscal year end (e.g. June to
    December) gets a new segment from that point; the stub period in between is a
    transition period, labelled fiscal_period "T".
    """

    def __init__(self, segments: list[_Segment]):
        self.segments = segments

    def _segment(self, d: date) -> int:
        for i, seg in enumerate(self.segments[:-1]):
            if d <= seg.last_end + FYE_TOLERANCE:
                return i
        return len(self.segments) - 1

    def calendar_for(self, d: date) -> FiscalCalendar:
        return self.segments[self._segment(d)].calendar

    def locate(self, d: date) -> tuple[int, int]:
        return self.calendar_for(d).locate(d)

    def is_transition(self, start: date, end: date) -> bool:
        """True for the stub period from the old fiscal year end to the first new one."""
        i = self._segment(end)
        if i == 0:
            return False
        prev_end = self.segments[i - 1].last_end
        calendar = self.segments[i].calendar
        first_new_end = calendar.fiscal_year_end(prev_end + FYE_TOLERANCE + timedelta(days=1))
        return (
            abs((start - prev_end).days - 1) <= FYE_TOLERANCE.days
            and abs((end - first_new_end).days) <= FYE_TOLERANCE.days
        )

    @classmethod
    def from_facts(cls, facts: list[dict[str, Any]]) -> FiscalSchedule | None:
        """Infer fiscal year ends and their fy labels from annual and transition filings.

        A filing's period end is the latest end among its duration facts of the right
        length (annual for 10-K/20-F/40-F, any for 10-KT). Instants are skipped (cover-page
        facts like shares outstanding are dated after the period), as are facts ending after
        the filing date (forward-looking or mis-tagged).
        """
        report_ends: dict[str, date] = {}
        labels: dict[str, int] = {}
        for f in facts:
            transition = f["form"] in TRANSITION_FORMS
            if not transition and (
                f["form"] not in ANNUAL_FORMS or f["filing_fiscal_period"] != "FY"
            ):
                continue
            if f["taxonomy"] == "dei" or f["filing_fiscal_year"] is None or f["instant"]:
                continue
            ptype = period_type(f["period_start"], f["period_end"], f["instant"])
            if not transition and ptype != "annual":
                continue
            if f.get("filed") and f["period_end"] > f["filed"]:
                continue
            accn = f["accession"]
            if accn not in report_ends or f["period_end"] > report_ends[accn]:
                report_ends[accn] = f["period_end"]
            labels[accn] = f["filing_fiscal_year"]
        if not report_ends:
            return None

        # One entry per fiscal year end (amendments repeat it); most common label wins.
        by_end: dict[date, Counter[int]] = {}
        for accn, end in report_ends.items():
            by_end.setdefault(end, Counter())[labels[accn]] += 1
        ends = sorted((end, c.most_common(1)[0][0]) for end, c in by_end.items())

        groups = _group_by_year_end(ends)
        # A lone year end between two regimes is more likely a tagging error than two
        # fiscal-year changes in a row; drop it and regroup. The latest group may be a
        # single year (the change just happened).
        while len(groups) > 1 and any(len(g) == 1 for g in groups[:-1]):
            ends = [e for g in groups[:-1] if len(g) > 1 for e in g] + groups[-1]
            groups = _group_by_year_end(sorted(ends))
        return cls([_Segment(g) for g in groups])


def _group_by_year_end(ends: list[tuple[date, int]]) -> list[list[tuple[date, int]]]:
    groups: list[list[tuple[date, int]]] = []
    for end, label in ends:
        if groups and _day_distance(groups[-1][-1][0], end) <= FYE_TOLERANCE.days:
            groups[-1].append((end, label))
        else:
            groups.append([(end, label)])
    return groups


def fiscal_period(ptype: str, quarter: int) -> str | None:
    match ptype:
        case "annual":
            return "FY"
        case "quarter":
            return f"Q{quarter}"
        case "half":
            return "H1" if quarter == 2 else "H2"
        case "nine_months":
            return "9M"
        case "instant":
            return "FY" if quarter == 4 else f"Q{quarter}"
    return None


def classify(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add period_type, fiscal_year and fiscal_period to each fact (in place)."""
    return label(facts, FiscalSchedule.from_facts(facts))


def label(facts: list[dict[str, Any]], schedule: FiscalSchedule | None) -> list[dict[str, Any]]:
    """Add period_type, fiscal_year and fiscal_period using a given schedule (in place)."""
    for f in facts:
        start, end = f["period_start"], f["period_end"]
        ptype = period_type(start, end, f["instant"])
        f["period_type"] = ptype
        f["fiscal_year"] = f["fiscal_period"] = None
        if schedule is None:
            continue
        year, quarter = schedule.locate(end)
        if ptype not in ("instant", "annual") and schedule.is_transition(start, end):
            f["fiscal_year"], f["fiscal_period"] = year, "T"
        elif ptype != "other":
            f["fiscal_year"], f["fiscal_period"] = year, fiscal_period(ptype, quarter)
    return facts
