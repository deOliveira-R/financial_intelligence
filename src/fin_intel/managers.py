"""13F managers' books by quarter, derived from institutional_positions (`manager_quarters`:
positions, value, turnover) and their best ideas (`manager_best_ideas`: a position among
the ten largest that is new or grew by half since the quarter before). The screen for
concentrated, patient managers and their biggest new bets (research H010), and descriptors
for manager pages. One pass per quarter over the equity positions (shares, not options or
principal amounts), committed per quarter; `derive-managers` recomputes the quarters without
rows plus the latest two (late and amended filings); `rebuild holdings` wipes both tables.

History before 2025-06 holds only filers with $1 billion or more, so turnover and
concentration are known for those; a filer's first stored quarter has no turnover.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from fin_intel.models import InstitutionalPosition, ManagerBestIdea, ManagerQuarter

log = logging.getLogger(__name__)

TOP = 10
INCREASE = 0.5  # shares up by half or more counts as a substantial addition
RECENT = 2

_QUARTERS = text(
    """
    INSERT INTO manager_quarters (filer_cik, period, positions, value, previous_positions, turnover)
    WITH now AS (
        SELECT filer_cik, cusip, value FROM institutional_positions
        WHERE period = :period AND share_type = 'SH' AND put_call = '' AND value > 0 AND shares > 0
    ), before AS (
        SELECT filer_cik, cusip FROM institutional_positions
        WHERE period = :previous AND share_type = 'SH' AND put_call = ''
          AND value > 0 AND shares > 0
    ), gone AS (
        SELECT b.filer_cik, count(*) AS n,
               count(*) FILTER (WHERE n.cusip IS NULL) AS gone
        FROM before b LEFT JOIN now n ON n.filer_cik = b.filer_cik AND n.cusip = b.cusip
        GROUP BY b.filer_cik
    )
    SELECT now.filer_cik, CAST(:period AS date), count(*), sum(now.value), gone.n,
           CASE WHEN gone.n > 0 THEN gone.gone::float / gone.n END
    FROM now LEFT JOIN gone ON gone.filer_cik = now.filer_cik
    GROUP BY now.filer_cik, gone.n, gone.gone
    """
)
_IDEAS = text(
    """
    INSERT INTO manager_best_ideas (filer_cik, period, cusip, rank, shares, previous_shares, value)
    WITH now AS (
        SELECT filer_cik, cusip, shares, value,
               rank() OVER (PARTITION BY filer_cik ORDER BY value DESC, cusip) AS rk
        FROM institutional_positions
        WHERE period = :period AND share_type = 'SH' AND put_call = '' AND value > 0 AND shares > 0
    ), before AS (
        SELECT filer_cik, cusip, shares FROM institutional_positions
        WHERE period = :previous AND share_type = 'SH' AND put_call = ''
          AND value > 0 AND shares > 0
    )
    SELECT n.filer_cik, CAST(:period AS date), n.cusip, n.rk, n.shares, b.shares, n.value
    FROM now n LEFT JOIN before b ON b.filer_cik = n.filer_cik AND b.cusip = n.cusip
    WHERE n.rk <= :top AND (b.shares IS NULL OR n.shares >= (1 + :increase) * b.shares)
    """
)


def quarter_before(period: date) -> date:
    """The quarter end before `period`'s quarter."""
    start_month = 3 * ((period.month - 1) // 3) + 1
    return date(period.year, start_month, 1) - timedelta(days=1)


def periods_to_do(session: Session) -> list[date]:
    available = sorted(session.scalars(select(InstitutionalPosition.period).distinct()))
    have = set(session.scalars(select(ManagerQuarter.period).distinct()))
    recent = set(available[-RECENT:])
    return [p for p in available if p not in have or p in recent]


def compute(session: Session, periods: list[date] | None = None) -> int:
    """Recompute the given quarters (default: those without rows plus the latest two),
    committing per quarter. The quarter before is the previous quarter end, whether or
    not the manager reported then (no report means no turnover and every position new)."""
    rows = 0
    for period in periods if periods is not None else periods_to_do(session):
        params = {"period": period, "previous": quarter_before(period)}
        session.execute(delete(ManagerBestIdea).where(ManagerBestIdea.period == period))
        session.execute(delete(ManagerQuarter).where(ManagerQuarter.period == period))
        managers = session.execute(_QUARTERS, params)
        ideas = session.execute(_IDEAS, params | {"top": TOP, "increase": INCREASE})
        session.commit()
        rows += managers.rowcount or 0
        log.info("managers %s: %s books, %s best ideas", period, managers.rowcount, ideas.rowcount)
    return rows
