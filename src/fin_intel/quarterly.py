"""Quarterly prices for every stock institutions held, derived from 13F values
(`quarterly_prices`): at each quarter end, the median of value/shares across the filers
reporting a CUSIP. Survivorship-free from 2013-Q2 (SEC's data sets begin there; a company
that later died is in the 13Fs filed while it lived), which daily bars can't be before
2024-10, and exact where it can be checked (Apple's median matches the close to the cent).
Research step 1 (docs/research.md): the price history stock-level studies run on; H008 first.

The medians come from `quarterly_price_parts`: one pass over each raw 13F data set file
(`load_parts`, run by the 13F loader and by `derive-quarterly-prices --from-raw`) over
*every* filer in it, not only the large ones `institutional_positions` keeps for history,
so the small companies that small managers hold get prices too. Files overlap on a quarter
(late and amended filings), so a quarter takes its part with the most filers.

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

import csv
import io
import logging
import zipfile
from collections import defaultdict
from datetime import date, datetime
from statistics import median

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import QuarterlyPrice, QuarterlyPricePart
from fin_intel.raw import RawStore

log = logging.getLogger(__name__)

RECENT = 2  # quarters recomputed every run: 13Fs trickle in for 45 days, amendments longer
VALUE_IN_DOLLARS_FROM = date(2023, 1, 3)  # reported in thousands before (thirteenf.py)

# The quarter's part with the most filers (ties: the latest file), mapped to our security
# and issuer where the CUSIP is known.
_INSERT = text(
    """
    INSERT INTO quarterly_prices
        (cusip, period, price, filers, value_total, shares_total, security_id, cik, cik_source)
    SELECT p.cusip, p.period, p.price, p.filers, p.value_total, p.shares_total,
           m.security_id, s.cik, CASE WHEN s.cik IS NOT NULL THEN 'security' END
    FROM (
        SELECT DISTINCT ON (cusip) cusip, period, price, filers, value_total, shares_total
        FROM quarterly_price_parts WHERE period = :period
        ORDER BY cusip, filers DESC, file_key DESC
    ) p
    LEFT JOIN cusip_mappings m ON m.cusip = p.cusip
    LEFT JOIN securities s ON s.id = m.security_id
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


def _date(value: str | None) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date() if value else None
    except ValueError:
        return None


def _num(value: str | None) -> float:
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


def parse_parts(file_key: str, body: bytes) -> list[dict]:
    """One pass over a 13F data set zip: per CUSIP and quarter end, the median of
    value/shares across every holdings report in the file (shares only: no options, no
    principal amounts), the number of reports and the totals. Streams the tables, so a
    2.4-million-row file needs memory for its ratios, not its rows."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = {n.rsplit("/", 1)[-1]: n for n in archive.namelist()}

        def rows(name: str):
            with archive.open(members[name]) as f:
                text_ = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
                yield from csv.DictReader(text_, delimiter="\t", quoting=csv.QUOTE_NONE)

        reports: dict[str, tuple[date, float]] = {}  # accession -> (period, value scale)
        for r in rows("SUBMISSION.tsv"):
            if not (r.get("SUBMISSIONTYPE") or "").startswith("13F-HR"):
                continue
            period = _date(r.get("PERIODOFREPORT"))
            if period is None:
                continue
            filed = _date(r.get("FILING_DATE"))
            scale = 1000.0 if filed and filed < VALUE_IN_DOLLARS_FROM else 1.0
            reports[r["ACCESSION_NUMBER"]] = (period, scale)
        ratios: dict[tuple[str, date], list[float]] = defaultdict(list)
        filers: dict[tuple[str, date], set[str]] = defaultdict(set)
        totals: dict[tuple[str, date], list[float]] = defaultdict(lambda: [0.0, 0.0])
        for r in rows("INFOTABLE.tsv"):
            report = reports.get(r["ACCESSION_NUMBER"])
            if report is None or (r.get("PUTCALL") or "").strip():
                continue
            if (r.get("SSHPRNAMTTYPE") or "").strip().upper() != "SH":
                continue
            cusip = (r.get("CUSIP") or "").strip().upper()
            value, shares = _num(r.get("VALUE")) * report[1], _num(r.get("SSHPRNAMT"))
            if len(cusip) != 9 or value <= 0 or shares <= 0:
                continue
            key = (cusip, report[0])
            ratios[key].append(value / shares)
            filers[key].add(r["ACCESSION_NUMBER"])
            totals[key][0] += value
            totals[key][1] += shares
    return [
        {
            "file_key": file_key,
            "cusip": cusip,
            "period": period,
            "price": median(values),
            "filers": len(filers[cusip, period]),
            "value_total": totals[cusip, period][0],
            "shares_total": totals[cusip, period][1],
        }
        for (cusip, period), values in ratios.items()
    ]


def load_parts(session: Session, file_key: str, body: bytes) -> int:
    """Replace the file's parts with a fresh pass over it."""
    session.execute(delete(QuarterlyPricePart).where(QuarterlyPricePart.file_key == file_key))
    return upsert(
        session,
        QuarterlyPricePart,
        parse_parts(file_key, body),
        key=["file_key", "cusip", "period"],
    )


def parts_from_raw(session: Session, store: RawStore, only_missing: bool = True) -> int:
    """Pass over the stored 13F data set files (the latest fetch of each), skipping files
    that already have parts unless told otherwise; one commit per file."""
    have = set(session.scalars(select(QuarterlyPricePart.file_key).distinct()))
    records = [
        r
        for r in store.records("sec", ["13f_dataset"], latest_per_key=True)
        if r.key and not (only_missing and r.key in have)
    ]
    rows = 0
    for record in records:
        rows += load_parts(session, record.key, record.body)
        session.commit()
        log.info("quarterly price parts %s: done", record.key)
    return rows


def periods_to_do(session: Session) -> list[date]:
    available = sorted(session.scalars(select(QuarterlyPricePart.period).distinct()))
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
