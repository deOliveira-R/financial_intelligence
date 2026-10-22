"""Quarterly prices for every stock institutions held, derived from 13F values
(`quarterly_prices`): at each quarter end, the median of value/shares across the filers
reporting a CUSIP. Survivorship-free back to 2001 (a company that later died is in the
13Fs filed while it lived), which daily bars can't be before 2024-10, and exact where it
can be checked (Apple's median matches the close to the cent). Research step 1
(docs/research.md): the price history stock-level studies run on; H008 first.

Each row carries how many filers priced it, the total value and shares held (size, from
the same source) and the security and issuer it maps to through `cusip_mappings`. A
company that died before our listings begin has no security row; its issuer is found
through the ticker insiders filed under within a year of the quarter (`cik_source =
"insiders"`, only when one issuer used that ticker in the window), never by name. Prices
are as reported: unadjusted for later splits (`corporate_actions` adjust on read, as for
daily bars). The median is robust to the few filers who report in the wrong unit; a study
should still ask for several filers per price. `derive-quarterly-prices` recomputes the
quarters without prices plus the latest two (late and amended filings); `rebuild holdings`
wipes the table.
"""

import logging
from datetime import date

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from fin_intel.models import InstitutionalPosition, QuarterlyPrice

log = logging.getLogger(__name__)

RECENT = 2  # quarters recomputed every run: 13Fs trickle in for 45 days, amendments longer

_INSERT = text(
    """
    INSERT INTO quarterly_prices
        (cusip, period, price, filers, value_total, shares_total, security_id, cik, cik_source)
    SELECT p.cusip, p.period,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY p.value / p.shares),
           count(*), sum(p.value), sum(p.shares), m.security_id, s.cik,
           CASE WHEN s.cik IS NOT NULL THEN 'security' END
    FROM institutional_positions p
    LEFT JOIN cusip_mappings m ON m.cusip = p.cusip
    LEFT JOIN securities s ON s.id = m.security_id
    WHERE p.period = :period AND p.share_type = 'SH' AND p.put_call = ''
      AND p.value > 0 AND p.shares > 0
    GROUP BY p.cusip, p.period, m.security_id, s.cik
    """
)
# Dead companies: the CUSIP's US ticker (OpenFIGI) matched to the symbol insiders filed
# under within a year of the quarter, when exactly one issuer did.
_BRIDGE = text(
    """
    UPDATE quarterly_prices q SET cik = b.cik, cik_source = 'insiders'
    FROM (
        SELECT m.cusip, min(i.issuer_cik) AS cik
        FROM cusip_mappings m
        JOIN insider_transactions i ON i.issuer_symbol = replace(m.ticker, '/', '-')
        WHERE m.security_id IS NULL
          AND m.ticker ~ '^[A-Z]{1,5}([./][A-Z0-9]{1,2})?$'
          AND i.filing_date BETWEEN CAST(:period AS date) - 365 AND CAST(:period AS date) + 365
          AND m.cusip IN (SELECT cusip FROM quarterly_prices WHERE period = :period AND cik IS NULL)
        GROUP BY m.cusip
        HAVING count(DISTINCT i.issuer_cik) = 1
    ) b
    WHERE q.period = :period AND q.cusip = b.cusip AND q.cik IS NULL
    """
)


def periods_to_do(session: Session) -> list[date]:
    available = sorted(session.scalars(select(InstitutionalPosition.period).distinct()))
    have = set(session.scalars(select(QuarterlyPrice.period).distinct()))
    recent = set(available[-RECENT:])
    return [p for p in available if p not in have or p in recent]


def compute(session: Session, periods: list[date] | None = None) -> int:
    """Recompute the given quarters (default: those without prices plus the latest two),
    committing per quarter."""
    rows = 0
    for period in periods if periods is not None else periods_to_do(session):
        session.execute(delete(QuarterlyPrice).where(QuarterlyPrice.period == period))
        result = session.execute(_INSERT, {"period": period})
        bridged = session.execute(_BRIDGE, {"period": period})
        session.commit()
        rows += result.rowcount or 0
        log.info(
            "quarterly prices %s: %s CUSIPs, %s issuers found through insiders' tickers",
            period, result.rowcount, bridged.rowcount,
        )  # fmt: skip
    return rows
