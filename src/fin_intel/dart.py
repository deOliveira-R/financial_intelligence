"""Korean listed companies' financial statements from DART (via OpenDART).

DART returns each report's accounts with standard ids (`ifrs-full_Revenue`,
`dart_OperatingIncomeLoss`) but no period dates: they follow from the business year, the
report (Q1, half, Q3, annual) and the company's fiscal year end. Amounts per row:

- balance sheet: at the period end (and, as `frmtrm`, at the previous fiscal year end);
- income statement: the quarter's three months (`thstrm`) and the year to date
  (`thstrm_add`), or the full year in an annual report, plus the prior year's figures;
- cash flow statement: the year to date.

The receipt number (rcept_no) starts with the filing date.
"""

import io
import xml.etree.ElementTree as ET
import zipfile
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

REPORTS = {"11013": ("Q1", 3), "11012": ("H1", 6), "11014": ("Q3", 9), "11011": ("FY", 12)}
FORMS = {"Q1": "quarterly", "H1": "half-year", "Q3": "quarterly", "FY": "annual"}
REPORT_CODES = {fp: code for code, (fp, _) in REPORTS.items()}
STATEMENT_ORDER = {"BS": 0, "IS": 1, "CIS": 2, "CF": 3}  # SCE (equity changes) is skipped


def parse_corp_codes(data: bytes) -> list[dict[str, str]]:
    """Listed companies (with a stock code) from the corp code zip."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ET.fromstring(archive.read(archive.namelist()[0]))
    out = []
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        if stock:
            out.append(
                {
                    "corp_code": (item.findtext("corp_code") or "").strip(),
                    "name": (
                        item.findtext("corp_eng_name") or item.findtext("corp_name") or ""
                    ).strip(),
                    "stock_code": stock,
                }
            )
    return out


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    return date(d.year + month // 12, month % 12 + 1, 1)


def fiscal_year(year: int, fiscal_month: int) -> tuple[date, date]:
    """Start and end of business year `year` (a year ending in December is that calendar
    year; otherwise it starts the month after `fiscal_month` of `year`)."""
    if fiscal_month == 12:
        return date(year, 1, 1), date(year, 12, 31)
    start = date(year, fiscal_month + 1, 1)
    end = _add_months(start, 12) - timedelta(days=1)
    return start, end


def _amount(text: str | None) -> float | None:
    text = (text or "").replace(",", "").strip()
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _concept(account_id: str) -> tuple[str, str] | None:
    for prefix, taxonomy in (
        ("ifrs-full_", "ifrs-full"),
        ("ifrs_", "ifrs-full"),
        ("dart_", "dart"),
    ):
        if account_id.startswith(prefix):
            return taxonomy, account_id[len(prefix) :]
    return None  # company-specific or non-standard accounts


def parse_statements(
    payload: dict[str, Any], year: int, report: str, fiscal_month: int = 12
) -> list[dict[str, Any]]:
    """Flat facts (world.facts_payload shape) from one fnlttSinglAcntAll response."""
    fp, months = REPORTS[report]
    fy_start, fy_end = fiscal_year(year, fiscal_month)
    period_end = _add_months(fy_start, months) - timedelta(days=1)
    quarter_start = _add_months(fy_start, months - 3)
    prior_fy_start, prior_fy_end = fiscal_year(year - 1, fiscal_month)
    prior_end = _month_end(period_end.year - 1, period_end.month)
    prior_quarter_start = date(quarter_start.year - 1, quarter_start.month, 1)

    rows = sorted(
        (r for r in payload.get("list") or [] if r.get("sj_div") in STATEMENT_ORDER),
        key=lambda r: STATEMENT_ORDER[r["sj_div"]],
    )
    facts = []
    for r in rows:
        concept = _concept(r.get("account_id") or "")
        if concept is None:
            continue
        currency = (r.get("currency") or "KRW").strip()
        unit = f"{currency}/shares" if "PerShare" in concept[1] else currency
        receipt = r.get("rcept_no") or ""
        base = {
            "taxonomy": concept[0],
            "concept": concept[1],
            "unit": unit,
            "accession": f"dart:{receipt}",
            "form": FORMS[fp],
            "filed": _receipt_date(receipt),
            "fy": year,
            "fp": fp,
        }
        sj = r["sj_div"]
        if sj == "BS":
            periods = [(None, period_end, "thstrm_amount"), (None, prior_fy_end, "frmtrm_amount")]
        elif sj == "CF":
            periods = [
                (fy_start, period_end, "thstrm_amount"),
                (prior_fy_start, prior_end, "frmtrm_amount"),
            ]
        elif fp == "FY":
            periods = [
                (fy_start, fy_end, "thstrm_amount"),
                (prior_fy_start, prior_fy_end, "frmtrm_amount"),
            ]
        else:
            periods = [
                (quarter_start, period_end, "thstrm_amount"),
                (fy_start, period_end, "thstrm_add_amount"),
                (prior_quarter_start, prior_end, "frmtrm_amount"),
                (prior_fy_start, prior_end, "frmtrm_add_amount"),
            ]
        for start, end, field in periods:
            value = _amount(r.get(field))
            if value is not None:
                facts.append({**base, "start": start, "end": end, "value": value})
    return facts


def _receipt_date(receipt: str) -> date | None:
    try:
        return date(int(receipt[:4]), int(receipt[4:6]), int(receipt[6:8]))
    except ValueError:
        return None


def parse_share_counts(payload: dict[str, Any], year: int, report: str) -> list[dict[str, Any]]:
    """Common shares outstanding (issued minus treasury) at the report's settlement date,
    as the cover-page share count US filings use."""
    fp = REPORTS[report][0]
    facts = []
    for r in payload.get("list") or []:
        if (r.get("se") or "").strip() != "보통주":  # common shares
            continue
        shares = _amount(r.get("distb_stock_co"))
        settled = (r.get("stlm_dt") or "").strip()
        receipt = r.get("rcept_no") or ""
        if shares and settled:
            facts.append(
                {
                    "taxonomy": "dei",
                    "concept": "EntityCommonStockSharesOutstanding",
                    "unit": "shares",
                    "start": None,
                    "end": date.fromisoformat(settled),
                    "value": shares,
                    "accession": f"dart:{receipt}",
                    "form": FORMS[fp],
                    "filed": _receipt_date(receipt),
                    "fy": year,  # the filing's labels: without them its fiscal year is lost
                    "fp": fp,
                }
            )
    return facts
