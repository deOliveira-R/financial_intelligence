"""H001's benchmark, corrected after the holdout (docs/research/H001-distress-alerts.md,
errata): Altman's Z'' from the latest annual statements, at the same 2% alert rate.

The holdout compared the frozen alert against Z'' computed from the latest balance sheet,
which in three quarters out of four is a 10-Q's without annual income, so Z'' was missing
and the benchmark rarely flagged. This study measures the benchmark properly in every
period; the frozen model isn't changed (it is what was tested), so its holdout run stands
and only the comparison is corrected. Logged as its own hypothesis, frozen before its one
holdout run like any other.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from fin_intel import research
from fin_intel.studies import h001

HYPOTHESIS = "H001_BENCHMARK"
FROZEN = {"benchmark": "altman_z2_annual", "alert_rate": h001.ALERT_RATE}


def evaluate(rows: list[h001.Row]) -> dict[str, Any]:
    def worst(values: list[float | None]) -> list[bool]:
        return h001.top_share([-(v if v is not None else 99.0) for v in values], h001.ALERT_RATE)

    def covered(values: list[float | None]) -> float:
        return sum(v is not None for v in values) / len(values) if values else float("nan")

    as_tested = [r.z for r in rows]
    annual = [r.z_annual for r in rows]
    weak = [r.z_annual is not None and r.z_annual < h001.Z_DISTRESS for r in rows]
    frozen = h001.top_share([h001.frozen_score(r) for r in rows], h001.ALERT_RATE)
    return {
        "rows": len(rows),
        "blowups": sum(r.blowup for r in rows),
        "z_coverage_as_tested": covered(as_tested),
        "z_coverage_annual": covered(annual),
        "altman_z2_as_tested": h001.measure(rows, worst(as_tested)),
        "altman_z2_annual": h001.measure(rows, worst(annual)),
        "weak_z_annual_signal": h001.measure(rows, weak),
        "frozen_logit": h001.measure(rows, frozen),
        "frozen_logit_warning_days": h001.warning_days(rows, frozen),
        "altman_z2_annual_warning_days": h001.warning_days(rows, worst(annual)),
    }


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in research.PERIODS:
        raise ValueError(f"period is one of {research.PERIODS}")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze first (study H001_BENCHMARK --freeze), once")
    first, last = h001.PERIODS[period]
    research.check_period(period, last + h001.YEAR - timedelta(days=1))
    result = {"period": period, **evaluate(h001.build_rows(session, first, last))}
    if log:
        research.log(session, HYPOTHESIS, FROZEN, period, result)
    return [result]
