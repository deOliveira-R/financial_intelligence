"""Taiwanese listed companies (TWSE and TPEx open data).

The exchanges publish the latest quarter for every company: income statements are year
to date (Q2 covers January to June) in thousands of TWD, balance sheets at the quarter
end. Company profiles give English short names and issued common shares. Each snapshot is
kept, so a history builds up quarter by quarter (a backfill from MOPS is in the backlog).

Field names differ between the exchanges (公司代號 vs SecuritiesCompanyCode) and between
industry formats (banks report net interest income and other net revenue, not revenue).
"""

from datetime import date
from typing import Any

THOUSANDS = 1000.0
PAR = 10.0

INCOME = {
    "CostOfRevenue": ["營業成本"],
    "GrossProfit": ["營業毛利（毛損）淨額", "營業毛利（毛損）"],
    "OperatingIncome": ["營業利益（損失）", "營業利益"],
    "PretaxIncome": ["稅前淨利（淨損）", "繼續營業單位稅前淨利（淨損）", "繼續營業單位稅前損益"],
    "IncomeTax": ["所得稅費用（利益）"],
    "NetIncome": ["本期淨利（淨損）", "本期稅後淨利（淨損）"],
    "NetIncomeParent": ["淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主"],
}
BALANCE = {
    "CurrentAssets": ["流動資產"],
    "Assets": ["資產總計", "資產合計"],
    "CurrentLiabilities": ["流動負債"],
    "Liabilities": ["負債總計", "負債合計"],
    "EquityParent": ["歸屬於母公司業主之權益合計"],
    "Equity": ["權益總計", "權益合計"],
    "RetainedEarnings": ["保留盈餘"],
    "ShareCapital": ["股本"],
}
TREASURY_SHARES = "母公司暨子公司所持有之母公司庫藏股股數（單位：股）"


def _num(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first(row: dict[str, Any], names: list[str]) -> float | None:
    return next((v for n in names if (v := _num(row.get(n))) is not None), None)


def code(row: dict[str, Any]) -> str:
    return str(row.get("公司代號") or row.get("SecuritiesCompanyCode") or "").strip()


def period(row: dict[str, Any]) -> tuple[int, int] | None:
    """(calendar year, quarter) from the ROC year (115 = 2026) and quarter."""
    year, quarter = row.get("年度") or row.get("Year"), row.get("季別") or row.get("Season")
    try:
        return int(year) + 1911, int(quarter)
    except TypeError, ValueError:
        return None


def _quarter_end(year: int, quarter: int) -> date:
    return {
        1: date(year, 3, 31),
        2: date(year, 6, 30),
        3: date(year, 9, 30),
        4: date(year, 12, 31),
    }[quarter]


def _revenue(row: dict[str, Any]) -> float | None:
    direct = _first(row, ["營業收入", "收益", "淨收益"])
    if direct is not None:
        return direct
    parts = [_num(row.get("利息淨收益")), _num(row.get("利息以外淨損益"))]
    return sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None


def parse_table(rows: list[dict[str, Any]], statement: str, filed: date) -> dict[str, list[dict]]:
    """Flat facts (world.facts_payload shape) per company code, from one income statement
    ("income") or balance sheet ("balance") table."""
    out: dict[str, list[dict]] = {}
    for row in rows:
        c, p = code(row), period(row)
        if not c or p is None:
            continue
        year, quarter = p
        end = _quarter_end(year, quarter)
        base = {
            "taxonomy": "twse",
            "unit": "TWD",
            "accession": f"twse:{c}:{year}Q{quarter}",
            "form": "annual" if quarter == 4 else "quarterly",
            "filed": filed,
            "fy": year,
            "fp": "FY" if quarter == 4 else f"Q{quarter}",
        }
        facts = []
        if statement == "income":
            start = date(year, 1, 1)
            values = {"Revenue": _revenue(row)} | {k: _first(row, v) for k, v in INCOME.items()}
            for concept, value in values.items():
                if value is not None:
                    facts.append(
                        {
                            **base,
                            "concept": concept,
                            "start": start,
                            "end": end,
                            "value": value * THOUSANDS,
                        }
                    )
            eps = _num(row.get("基本每股盈餘（元）"))
            if eps is not None:
                facts.append(
                    {
                        **base,
                        "concept": "EPS",
                        "unit": "TWD/shares",
                        "start": start,
                        "end": end,
                        "value": eps,
                    }
                )
        else:
            for concept, names in BALANCE.items():
                value = _first(row, names)
                if value is not None:
                    facts.append(
                        {
                            **base,
                            "concept": concept,
                            "start": None,
                            "end": end,
                            "value": value * THOUSANDS,
                        }
                    )
            # Shares outstanding: share capital at the standard TWD 10 par, less treasury
            # shares (a company with another par fails the EPS consistency check in metrics).
            capital = _first(row, BALANCE["ShareCapital"])
            if capital:
                treasury = _num(row.get(TREASURY_SHARES)) or 0.0
                facts.append(
                    {
                        **base,
                        "concept": "SharesOutstanding",
                        "unit": "shares",
                        "start": None,
                        "end": end,
                        "value": capital * THOUSANDS / PAR - treasury,
                    }
                )
        if facts:
            out.setdefault(c, []).extend(facts)
    return out


def parse_profiles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Company code, English short name and issued common shares from a profile table."""
    out = []
    for row in rows:
        c = code(row)
        if not c:
            continue
        name = (
            row.get("英文簡稱")
            or row.get("Symbol")
            or row.get("公司簡稱")
            or row.get("CompanyAbbreviation")
            or ""
        )
        shares = _num(row.get("已發行普通股數或TDR原股發行股數") or row.get("IssueShares"))
        out.append(
            {
                "code": c,
                "name": str(name).replace("　", " ").strip() or None,
                "shares": shares,
                "date": row.get("出表日期") or row.get("Date"),
            }
        )
    return out
