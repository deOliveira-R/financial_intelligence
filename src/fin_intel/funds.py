"""Index ETF holdings from SEC Form N-PORT: point-in-time index membership and weights.

An index fund's holdings are its index: IVV's are the S&P 500, IWM's the Russell 2000. N-PORT
reports (quarterly since late 2019, monthly in recent years) give survivorship-free
membership with weights, so screens and benchmarks can use the index as it was rather
than today's list. A report counts as known from its filing date (about 60 days after the
period).

SPY and DIA are unit investment trusts and don't file N-PORT; IVV stands in for the S&P 500.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import CusipMapping, FundHolding

FUNDS = {
    "IVV": "S&P 500",
    "IJH": "S&P MidCap 400",
    "IJR": "S&P SmallCap 600",
    "IWB": "Russell 1000",
    "IWM": "Russell 2000",
    "IWV": "Russell 3000",
    "QQQ": "Nasdaq-100",
    "QQQM": "Nasdaq-100",
    "RSP": "S&P 500 Equal Weight",
    "XLK": "Technology Select Sector",
    "XLE": "Energy Select Sector",
    "XLF": "Financial Select Sector",
    "XLV": "Health Care Select Sector",
    "XLI": "Industrial Select Sector",
    "XLU": "Utilities Select Sector",
    "XLB": "Materials Select Sector",
    "XLP": "Consumer Staples Select Sector",
    "XLY": "Consumer Discretionary Select Sector",
    "XLC": "Communication Services Select Sector",
    "XLRE": "Real Estate Select Sector",
    "SMH": "MVIS US Listed Semiconductor 25",
    "SOXX": "NYSE Semiconductor",
    "ITA": "Dow Jones US Select Aerospace & Defense",
    "XBI": "S&P Biotechnology Select Industry",
    "IBB": "Nasdaq Biotechnology",
    "URA": "Solactive Global Uranium & Nuclear Components",
}
FORMS = ("NPORT-P", "NPORT-P/A")


def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: ET.Element, name: str) -> str | None:
    for child in node.iter():
        if _strip(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def _float(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def parse(body: bytes) -> tuple[date | None, list[dict[str, Any]]]:
    """(report period, holdings) from an N-PORT primary document."""
    root = ET.fromstring(body)
    period_text = next(
        (n.text for n in root.iter() if _strip(n.tag) == "repPdDate" and n.text), None
    )
    period = date.fromisoformat(period_text.strip()) if period_text else None
    rows = []
    for node in root.iter():
        if _strip(node.tag) != "invstOrSec":
            continue
        cusip = _text(node, "cusip")
        cusip = cusip.upper() if cusip and re.fullmatch(r"[0-9A-Za-z]{9}", cusip) else None
        isin = next(
            (c.get("value") for c in node.iter() if _strip(c.tag) == "isin" and c.get("value")),
            None,
        )
        name = _text(node, "name")
        identifier = cusip or isin or name
        if not identifier:
            continue
        rows.append(
            {
                "identifier": identifier[:128],
                "cusip": cusip,
                "isin": isin,
                "name": name,
                "shares": _float(_text(node, "balance")),
                "value_usd": _float(_text(node, "valUSD")),
                "weight": _float(_text(node, "pctVal")),
                "asset_category": _text(node, "assetCat"),
            }
        )
    return period, rows


def load(session: Session, key: str, body: bytes) -> int:
    """One report (key `IVV|S000004310|0002071691-26-019760|2026-08-25`). A period's holdings
    come from its latest filing (an older one, replayed in another order, is ignored), so an
    amended period counts as known from the amendment's filing date."""
    fund, series_id, accession, filed_text = key.split("|")
    filed = date.fromisoformat(filed_text)
    period, rows = parse(body)
    if period is None:
        return 0
    newest = session.scalar(
        select(func.max(FundHolding.filed)).where(
            FundHolding.fund == fund, FundHolding.period == period
        )
    )
    if newest is not None and newest > filed:
        return 0
    session.execute(
        delete(FundHolding).where(FundHolding.fund == fund, FundHolding.period == period)
    )
    merged: dict[str, dict[str, Any]] = {}
    for r in rows:  # a security can appear in several lots: sum them
        entry = merged.setdefault(
            r["identifier"], {**r, "shares": 0.0, "value_usd": 0.0, "weight": 0.0}
        )
        for field in ("shares", "value_usd", "weight"):
            entry[field] += r[field] or 0.0
    records = [
        {
            **r,
            "fund": fund,
            "period": period,
            "series_id": series_id,
            "accession": accession,
            "filed": filed,
        }
        for r in merged.values()
    ]
    return upsert(session, FundHolding, records, key=["fund", "period", "identifier"])


def members(session: Session, fund: str, as_of: date | None = None) -> list[dict[str, Any]]:
    """The fund's holdings in its latest report public on `as_of` (default: latest), with
    the security each CUSIP maps to, largest weight first."""
    stmt = select(func.max(FundHolding.period)).where(FundHolding.fund == fund.upper())
    if as_of is not None:
        stmt = stmt.where(FundHolding.filed <= as_of)
    period = session.scalar(stmt)
    if period is None:
        return []
    rows = session.execute(
        select(FundHolding, CusipMapping.security_id)
        .outerjoin(CusipMapping, CusipMapping.cusip == FundHolding.cusip)
        .where(FundHolding.fund == fund.upper(), FundHolding.period == period)
        .order_by(FundHolding.weight.desc())
    ).all()
    return [
        {
            "period": h.period,
            "filed": h.filed,
            "cusip": h.cusip,
            "isin": h.isin,
            "name": h.name,
            "security_id": security_id,
            "shares": h.shares,
            "value_usd": h.value_usd,
            "weight": h.weight,
        }
        for h, security_id in rows
    ]
