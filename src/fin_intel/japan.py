"""Japanese rates and flows from the Ministry of Finance: inputs to the yen carry gauge.

- **JGB yield curve**: constant-maturity yields, 1 to 40 years, every business day since
  1974 (`JGB1Y` … `JGB40Y`, percent). Published the same Tokyo afternoon, before the US
  open, so a day's yields are known on that day.
- **Weekly portfolio flows** (designated major investors, 100 million yen, net purchases):
  Japanese residents buying foreign equity and bonds (outflows: what funds carry trades
  and US Treasuries) and foreigners buying Japanese securities. The week runs Sunday to
  Saturday and is published the following Thursday. Large resident net selling of foreign
  bonds is repatriation: the yen-strengthening side of an unwind.

Stored as economic series (source "mof"); timeseries reads them as `jp:<alias>`.
"""

import csv
import io
import re
import unicodedata
from datetime import date, timedelta
from typing import Any

TENORS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30, 40)
JGB_SERIES = {f"JGB{t}Y": f"JGB {t}-year yield" for t in TENORS}

# Column of each net figure in week.csv rows (the period is column 0) -> (series, title)
FLOW_COLUMNS = {
    3: ("MOF_OUT_EQUITY", "Residents' net purchases of foreign equity and fund shares"),
    6: ("MOF_OUT_BONDS", "Residents' net purchases of foreign long-term debt"),
    11: ("MOF_OUT_TOTAL", "Residents' net purchases of foreign securities, total"),
    14: ("MOF_IN_EQUITY", "Non-residents' net purchases of Japanese equity and fund shares"),
    17: ("MOF_IN_BONDS", "Non-residents' net purchases of Japanese long-term debt"),
    22: ("MOF_IN_TOTAL", "Non-residents' net purchases of Japanese securities, total"),
}
FLOWS_LAG = timedelta(days=5)  # Saturday's week out the next Thursday

ALIASES = {
    **{f"jgb{t}y": f"JGB{t}Y" for t in TENORS},
    "out_equity": "MOF_OUT_EQUITY",
    "out_bonds": "MOF_OUT_BONDS",
    "out_total": "MOF_OUT_TOTAL",
    "in_equity": "MOF_IN_EQUITY",
    "in_bonds": "MOF_IN_BONDS",
    "in_total": "MOF_IN_TOTAL",
}
SERIES_IDS = set(ALIASES.values())


def resolve(ident: str) -> str:
    """A series ID from an alias (jgb10y, out_bonds…) or ID, case-insensitive."""
    if ident.upper() in SERIES_IDS:
        return ident.upper()
    if ident.lower() in ALIASES:
        return ALIASES[ident.lower()]
    raise ValueError(f"unknown Japanese series {ident!r}; one of {', '.join(ALIASES)}")


def available_on(series_id: str, period: date) -> date:
    return period + FLOWS_LAG if series_id.startswith("MOF_") else period


def _series_row(series_id: str, title: str, units: str, frequency: str) -> dict[str, Any]:
    return {
        "id": series_id,
        "source": "mof",
        "title": title,
        "units": units,
        "frequency": frequency,
        "seasonal_adjustment": "NSA",
        "last_updated": None,
    }


def _number(text: str) -> float | None:
    text = text.strip().replace(",", "")
    try:
        return float(text)
    except ValueError:  # "-": no value (the 40-year bond only exists since 2007)
        return None


def parse_jgb(body: bytes) -> tuple[list[dict], list[dict]]:
    """(series rows, observations) from jgbcme_all.csv or jgbcme.csv."""
    text = body.decode("utf-8-sig", errors="replace")
    header: list[str] | None = None
    observations = []
    for row in csv.reader(io.StringIO(text)):
        if row and row[0] == "Date":
            header = row
            continue
        if header is None or not row or not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", row[0]):
            continue
        day = date(*map(int, row[0].split("/")))
        for column, value in zip(header[1:], row[1:], strict=False):
            series_id = f"JGB{column.strip()}"
            if series_id in JGB_SERIES and (v := _number(value)) is not None:
                observations.append({"series_id": series_id, "date": day, "value": v})
    series = [_series_row(s, t, "Percent", "D") for s, t in JGB_SERIES.items()]
    return series, observations


def _week_end(period: str) -> date | None:
    """`2005．1．2〜 1．8` -> 2005-01-08; a week spanning the new year names the end's year
    (`2025．12．28～2026．1．3`)."""
    text = unicodedata.normalize("NFKC", period).replace("〜", "~").replace(" ", "")
    m = re.fullmatch(r"(\d{4})\.\d{1,2}\.\d{1,2}~(?:(\d{4})\.)?(\d{1,2})\.(\d{1,2})", text)
    if not m:
        return None
    start_year, end_year, month, day = m.groups()
    return date(int(end_year or start_year), int(month), int(day))


def parse_flows(body: bytes) -> tuple[list[dict], list[dict]]:
    """(series rows, observations) from week.csv (Shift JIS), dated by each week's end."""
    text = body.decode("cp932", errors="replace")
    observations = []
    for row in csv.reader(io.StringIO(text)):
        if not row or (end := _week_end(row[0])) is None:
            continue
        for column, (series_id, _) in FLOW_COLUMNS.items():
            if column < len(row) and (v := _number(row[column])) is not None:
                observations.append({"series_id": series_id, "date": end, "value": v})
    series = [_series_row(s, t, "100 million yen", "W") for s, t in FLOW_COLUMNS.values()]
    return series, observations
