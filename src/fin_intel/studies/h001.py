"""H001: public distress signals precede blowups (docs/research/H001-distress-alerts.md).

One row per company and quarter end: members of the research population (population.py,
alive or dead) with an annual report in the prior 15 months and a public float of at
least $75 million in their latest annual report. Signals are events and figures public
in the 12 months before the quarter end; the outcome is a blowup in the 12 months after:
a bankruptcy 8-K, or a deregistration or delisting without takeover evidence in the year
before it. Measured by lift, recall and the share of rows flagged, against Altman's
price-free Z'' at the same alert rate.

Quarters are split so no outcome reaches the holdout: design 2012-03 to 2020-12,
validation 2021-03 to 2022-06 (outcomes end 2023-06), holdout from 2022-09.
"""

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from fin_intel import population, research
from fin_intel.models import CorporateEvent, StatementItem, XbrlFiling

HYPOTHESIS = "H001"
MIN_FLOAT = 75e6
YEAR = timedelta(days=365)
ANNUAL_FORMS = ("10-K", "20-F", "40-F", "10-KT")
PERIODS = {
    "design": (date(2012, 3, 31), date(2020, 12, 31)),
    "validation": (date(2021, 3, 31), date(2022, 6, 30)),
    "holdout": (date(2022, 9, 30), date(2025, 9, 30)),
}
EVENT_SIGNALS = {
    "late_filing": lambda form, item: form in ("NT 10-K", "NT 10-Q", "NT 20-F"),
    "auditor_change": lambda form, item: item == "4.01",
    "restatement": lambda form, item: item == "4.02",
    "debt_acceleration": lambda form, item: item == "2.04",
    "delisting_notice": lambda form, item: item == "3.01",
    "impairment": lambda form, item: item == "2.06",
}
FINANCIAL_SIGNALS = ("weak_z", "short_runway")
INSIDER_THRESHOLDS = (0.005, 0.01, 0.02)  # net sales over 12 months, share of float
EXITS = ("15-12B", "15-12G", "15-15D", "25-NSE")
TAKEOVER_FORMS = ("DEFM14A", "PREM14A", "DEFM14C", "425", "SC 13E3", "SC 14D9", "SC TO-T")
# Exit forms often cover one class of securities (preferred shares, notes, a secondary
# listing) while the company keeps reporting: an exit counts only if the company files no
# periodic report after it (a straggling report within this grace period is allowed).
STOPPED_GRACE = timedelta(days=120)
TAKEOVER_ITEMS = ("2.01", "5.01")
Z_DISTRESS = 1.1
ALERT_RATE = 0.02


def quarter_ends(first: date, last: date) -> list[date]:
    out, y, q = [], first.year, (first.month - 1) // 3 + 1
    while True:
        end = date(y + (q == 4), 1 if q == 4 else q * 3 + 1, 1) - timedelta(1)
        if end > last:
            return out
        if end >= first:
            out.append(end)
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)


@dataclass
class Row:
    cik: int
    quarter: date
    float_usd: float
    signals: dict[str, bool] = field(default_factory=dict)
    z: float | None = None
    net_selling: float = 0.0  # share of float
    blowup: bool = False
    blowup_on: date | None = None


# --- outcomes ---


def blowups(events: list[tuple[str, str, date]], reports: list[date] | None = None) -> list[date]:
    """Dates a company failed: a bankruptcy 8-K, or an exit form (deregistration,
    delisting) after which it stopped filing periodic reports, without takeover evidence in
    the year before. `events` are (form, item, filed) for one company; `reports` the dates
    of its periodic reports (None: don't check, every exit form counts)."""
    last_report = max(reports) if reports else None
    takeovers = sorted(
        filed
        for form, item, filed in events
        if form in TAKEOVER_FORMS or (form.startswith("8-K") and item in TAKEOVER_ITEMS)
    )
    out = []
    for form, item, filed in events:
        if form.startswith("8-K") and item == "1.03":
            out.append(filed)
        elif form in EXITS:
            if reports is not None and last_report and last_report > filed + STOPPED_GRACE:
                continue  # it kept reporting: one class of securities, not the company
            recent = [t for t in takeovers if filed - YEAR <= t <= filed]
            if not recent:
                out.append(filed)
    return sorted(out)


# --- financial signals ---


def altman_z2(s: dict[str, float]) -> float | None:
    """Altman's Z'' (1995): price-free, so it works for companies without stored prices."""
    try:
        assets, liabilities = s["total_assets"], s["total_liabilities"]
        if assets <= 0 or liabilities <= 0:
            return None
        return (
            6.56 * (s["current_assets"] - s["current_liabilities"]) / assets
            + 3.26 * s["retained_earnings"] / assets
            + 6.72 * s["operating_income"] / assets
            + 1.05 * s["equity"] / liabilities
        )
    except KeyError:
        return None


def short_runway(s: dict[str, float]) -> bool | None:
    """Cash covers less than a year of negative free cash flow."""
    if "cash" not in s or "operating_cash_flow" not in s:
        return None
    burn = -(s["operating_cash_flow"] - abs(s.get("capex", 0.0)))
    return burn > 0 and s["cash"] / burn < 1


ANNUAL_ITEMS = (
    "current_assets",
    "current_liabilities",
    "total_assets",
    "total_liabilities",
    "retained_earnings",
    "operating_income",
    "equity",
    "cash",
    "operating_cash_flow",
    "capex",
)


def annual_snapshots(
    items: list[tuple[str, date, date, str, float, date | None]],
) -> list[tuple[date, dict[str, float]]]:
    """(public date, figures) per fiscal year end for one company: annual flows and
    year-end balances, public when the last of them was first filed."""
    by_end: dict[date, dict[str, tuple[float, date]]] = defaultdict(dict)
    for line, start, end, ptype, value, first in items:
        if first is None:
            continue
        if ptype == "annual" or (ptype == "instant" and start == end):
            current = by_end[end].get(line)
            if current is None or first < current[1]:
                by_end[end][line] = (value, first)
    out = []
    for figures in by_end.values():
        if "total_assets" not in figures:
            continue
        public = max(f for _, f in figures.values())
        out.append((public, {k: v for k, (v, _) in figures.items()}))
    return sorted(out, key=lambda x: x[0])


# --- assembling rows ---


def _float_history(session: Session, ciks: set[int]) -> dict[int, list[tuple[date, float]]]:
    rows = session.execute(
        text(
            "SELECT f.cik, fi.filed, f.value FROM facts f "
            "JOIN concepts c ON c.id = f.concept_id "
            "JOIN filings fi ON fi.id = f.filing_id "
            "WHERE c.taxonomy = 'dei' AND c.name = 'EntityPublicFloat' AND f.unit = 'USD'"
        )
    )
    out: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for cik, filed, value in rows:
        if cik in ciks and filed is not None:
            out[cik].append((filed, value))
    for v in out.values():
        v.sort()
    return out


def _latest(history: list[tuple[date, Any]], day: date) -> Any:
    i = bisect_right([d for d, _ in history], day)
    return history[i - 1][1] if i else None


def build_rows(session: Session, first: date, last: date) -> list[Row]:
    ciks = population.members(session)
    annual = defaultdict(list)
    periodic = defaultdict(list)
    for cik, form, filed in session.execute(
        select(XbrlFiling.cik, XbrlFiling.form, XbrlFiling.filed).where(
            func.replace(XbrlFiling.form, "/A", "").in_(population.PERIODIC_FORMS)
        )
    ):
        periodic[cik].append(filed)
        if form.removesuffix("/A") in ANNUAL_FORMS:
            annual[cik].append(filed)
    floats = _float_history(session, ciks)
    events: dict[int, list[tuple[str, str, date]]] = defaultdict(list)
    wanted_items = ("1.03", "2.01", "5.01", "4.01", "4.02", "2.04", "3.01", "2.06")
    wanted_forms = ("NT 10-K", "NT 10-Q", "NT 20-F", *EXITS, *TAKEOVER_FORMS)
    for cik, form, item, filed in session.execute(
        select(
            CorporateEvent.cik, CorporateEvent.form, CorporateEvent.item, CorporateEvent.filed
        ).where(CorporateEvent.form.in_(wanted_forms) | CorporateEvent.item.in_(wanted_items))
    ):
        events[cik].append((form, item, filed))
    statements: dict[int, list] = defaultdict(list)
    for row in session.execute(
        select(
            StatementItem.cik,
            StatementItem.line_item,
            StatementItem.period_start,
            StatementItem.period_end,
            StatementItem.period_type,
            StatementItem.value,
            StatementItem.first_filed,
        ).where(StatementItem.line_item.in_(ANNUAL_ITEMS))
    ):
        statements[row[0]].append(tuple(row[1:]))
    selling: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for cik, filed, dollars in session.execute(
        text(
            "SELECT issuer_cik, filing_date, "
            "SUM(CASE WHEN trans_code = 'S' THEN shares * price ELSE -shares * price END) "
            "FROM insider_transactions "
            "WHERE trans_code IN ('S', 'P') AND NOT plan_10b5_1 AND price > 0 "
            "AND (relationship ILIKE '%officer%' OR relationship ILIKE '%director%') "
            "GROUP BY issuer_cik, filing_date"
        )
    ):
        if filed is not None:
            selling[cik].append((filed, float(dollars or 0)))

    rows = []
    for cik in ciks:
        reports = sorted(annual.get(cik, []))
        floats_c = floats.get(cik, [])
        if not reports or not floats_c:
            continue
        failed = blowups(events.get(cik, []), periodic.get(cik, []))
        snapshots = annual_snapshots(statements.get(cik, []))
        sells = sorted(selling.get(cik, []))
        sell_days = [d for d, _ in sells]
        sell_cum = np.concatenate([[0.0], np.cumsum([v for _, v in sells])]) if sells else None
        evs = events.get(cik, [])
        for q in quarter_ends(first, last):
            if not any(q - timedelta(days=456) < r <= q for r in reports):
                continue
            if any(f <= q for f in failed):
                continue  # already failed
            float_usd = _latest(floats_c, q)
            if not float_usd or float_usd < MIN_FLOAT:
                continue
            row = Row(cik, q, float_usd)
            for name, test in EVENT_SIGNALS.items():
                row.signals[name] = any(q - YEAR < d <= q and test(f, i) for f, i, d in evs)
            snap = _latest(snapshots, q)
            if snap:
                row.z = altman_z2(snap)
                row.signals["weak_z"] = row.z is not None and row.z < Z_DISTRESS
                row.signals["short_runway"] = bool(short_runway(snap))
            else:
                row.signals["weak_z"] = row.signals["short_runway"] = False
            if sell_cum is not None:
                lo, hi = bisect_right(sell_days, q - YEAR), bisect_right(sell_days, q)
                row.net_selling = float(sell_cum[hi] - sell_cum[lo]) / float_usd
            ahead = [f for f in failed if q < f <= q + YEAR]
            row.blowup = bool(ahead)
            row.blowup_on = ahead[0] if ahead else None
            rows.append(row)
    return rows


# --- measures ---


def signal_names(threshold: float) -> list[str]:
    return [*EVENT_SIGNALS, *FINANCIAL_SIGNALS, f"insider_selling_{threshold}"]


def flags(row: Row, threshold: float) -> dict[str, bool]:
    return {**row.signals, f"insider_selling_{threshold}": row.net_selling > threshold}


def measure(rows: list[Row], flagged: list[bool], reps: int = 500) -> dict[str, Any]:
    outcome = [r.blowup for r in rows]
    by_company: dict[int, list[tuple[bool, bool]]] = defaultdict(list)
    for r, f in zip(rows, flagged, strict=True):
        by_company[r.cik].append((f, r.blowup))

    def lift_of(pairs: list[tuple[bool, bool]]) -> float:
        return research.lift([p[0] for p in pairs], [p[1] for p in pairs])

    est = research.cluster_bootstrap(by_company, lift_of, reps=reps)
    hits = sum(outcome)
    caught = sum(1 for f, o in zip(flagged, outcome, strict=True) if f and o)
    return {
        "flagged_share": sum(flagged) / len(rows) if rows else math.nan,
        "lift": est.value,
        "lift_low": est.low,
        "lift_high": est.high,
        "recall": caught / hits if hits else math.nan,
        "precision": caught / sum(flagged) if sum(flagged) else math.nan,
    }


def top_share(scores: list[float], share: float) -> list[bool]:
    cut = np.quantile(scores, 1 - share)
    return [s >= cut for s in scores]


def fit_logit(rows: list[Row], names: list[str], threshold: float):
    import statsmodels.api as sm

    X = np.asarray([[float(flags(r, threshold)[n]) for n in names] for r in rows])
    y = np.asarray([float(r.blowup) for r in rows])
    fit = sm.Logit(y, sm.add_constant(X, has_constant="add")).fit(
        disp=0, maxiter=200, method="bfgs"
    )
    return fit


def warning_days(rows: list[Row], flagged: list[bool]) -> float | None:
    """Median days from a company's first flagged quarter to its blowup."""
    first_flag: dict[int, date] = {}
    blow: dict[int, date] = {}
    for r, f in zip(rows, flagged, strict=True):
        if f and r.cik not in first_flag:
            first_flag[r.cik] = r.quarter
        if r.blowup_on:
            blow[r.cik] = r.blowup_on
    days = [(blow[c] - d).days for c, d in first_flag.items() if c in blow and blow[c] > d]
    return float(np.median(days)) if days else None


def evaluate(rows: list[Row], threshold: float, model=None) -> dict[str, Any]:
    names = signal_names(threshold)
    out: dict[str, Any] = {
        "rows": len(rows),
        "companies": len({r.cik for r in rows}),
        "blowups": sum(r.blowup for r in rows),
        "base_rate": sum(r.blowup for r in rows) / len(rows) if rows else math.nan,
        "signals": {},
    }
    for n in names:
        out["signals"][n] = measure(rows, [flags(r, threshold)[n] for r in rows])
    count = [float(sum(flags(r, threshold).values())) for r in rows]
    out["count_at_alert_rate"] = measure(rows, top_share(count, ALERT_RATE))
    z_scores = [-(r.z if r.z is not None else 99.0) for r in rows]
    out["altman_z2_at_alert_rate"] = measure(rows, top_share(z_scores, ALERT_RATE))
    if model is not None:
        import statsmodels.api as sm

        X = np.asarray([[float(flags(r, threshold)[n]) for n in names] for r in rows])
        scores = list(model.predict(sm.add_constant(X, has_constant="add")))
        chosen = top_share(scores, ALERT_RATE)
        out["logit_at_alert_rate"] = measure(rows, chosen)
        out["logit_warning_days"] = warning_days(rows, chosen)
        out["logit_coef"] = dict(
            zip(["const", *names], np.round(model.params, 3).tolist(), strict=True)
        )
    return out


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in ("design", "validation"):
        raise ValueError("H001 runs design or validation here; the holdout needs a frozen variant")
    first, last = PERIODS[period]
    research.check_period(period, last + YEAR - timedelta(days=1))
    rows = build_rows(session, first, last)
    design_rows = rows if period == "design" else build_rows(session, *PERIODS["design"])
    results = []
    for threshold in INSIDER_THRESHOLDS:
        model = fit_logit(design_rows, signal_names(threshold), threshold)
        result = {
            "period": period,
            "insider_threshold": threshold,
            **evaluate(rows, threshold, model),
        }
        results.append(result)
        if log:
            research.log(session, HYPOTHESIS, {"insider_threshold": threshold}, period, result)
    return results
