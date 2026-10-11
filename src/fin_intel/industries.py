"""The US economy's input-output accounts (BEA): which industries buy from which, in dollars.

A use table says how much of each commodity every industry used as inputs (and every
final-demand column: households, investment, exports, government) in a year. BEA publishes
them at the detail level (~400 industries, coded close to 6-digit NAICS) for benchmark
years (2007, 2012, 2017) and at the summary level (71 industries) for every year since
1997. With the concordance from BEA codes to NAICS they connect every source keyed by
industry (contracts by NAICS, companies by SIC through NAICS, themes) into a supply chain:
graph.py builds the graph from these tables.

Companies come in through their SIC codes: SEC still assigns 1987 SIC codes, so Census's
concordances chain them to 2017 NAICS (1987 SIC -> 2002 -> 2007 -> 2012 -> 2017 NAICS),
stored as published, one row per code pair.

Stored as published (supply-use framework, millions of dollars). Suppressed cells ("...")
and empty cells are left out. A table describes its year's economy but is published years
later (the 2017 detail tables in 2024); the structure changes slowly, so the graph uses the
latest tables without point-in-time care, and nothing here feeds a backtest's prices.
"""

import io
import logging
import re
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import Concordance, IoCode, IoNaics, IoUse
from fin_intel.pipeline import synced_since, tracked
from fin_intel.providers import BeaProvider, CensusProvider
from fin_intel.providers.census import CONCORDANCES
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

DETAIL_FILE = "Use_SUT_Framework_2017_DET.xlsx"
SUMMARY_FILE = "Use_Tables_Supply-Use_Framework_1997-2023_Summary.xlsx"
REFRESH = timedelta(days=30)  # BEA revises the tables once a year


def _code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_naics(text: Any) -> list[str]:
    """BEA's NAICS column: "1122, 1124-5, 1129" means 1122, 1124, 1125, 1129; "23*" all of
    23; "n.a." none (government)."""
    out = []
    for token in str(text or "").replace("*", "").split(","):
        token = token.strip()
        if not token or not token[0].isdigit():
            continue
        first, _, last = token.partition("-")
        if not last:
            out.append(first)
            continue
        stem = first[: len(first) - len(last)]
        for n in range(int(first[len(stem) :]), int(last) + 1):
            out.append(f"{stem}{n:0{len(last)}d}")
    return out


def _kind(code: str) -> str:
    """The kind of a table code that isn't an industry (industries are tagged separately)."""
    if re.fullmatch(r"F\d.*", code):
        return "final_demand"
    if re.fullmatch(r"V.*", code):
        return "value_added"
    if re.fullmatch(r"T\d.*", code):
        return "total"
    return "commodity"


def parse_hierarchy(rows: list[tuple]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The "NAICS Codes" sheet: sectors (column A), summary (B), underlying summary (C) and
    detail industries (D) with their NAICS codes (G), nested by row order."""
    codes: dict[tuple[str, str], dict[str, Any]] = {}
    naics: list[dict[str, Any]] = []
    sector = summary = None
    for row in rows:
        row = tuple(row) + (None,) * (7 - len(row))
        a, b, c, d = (_code(v) for v in row[:4])
        if a and b and a[0].isdigit():
            sector = a
            codes[("sector", a)] = {"level": "sector", "code": a, "title": b, "sector": None}
        elif b and c and not a:
            summary = b
            codes[("summary", b)] = {"level": "summary", "code": b, "title": c, "sector": sector}
        elif d and _code(row[4]) and not (a or b or c):
            codes[("detail", d)] = {
                "level": "detail",
                "code": d,
                "title": _code(row[4]),
                "summary": summary,
                "sector": sector,
            }
            naics += [{"code": d, "naics": n} for n in parse_naics(row[6])]
    rows_out = [{"kind": "industry", "summary": None, **c} for c in codes.values()]
    unique = {(n["code"], n["naics"]): n for n in naics}
    return rows_out, list(unique.values())


def parse_use(
    rows: list[tuple], level: str, year: int, header: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A use table sheet: column codes on row `header`, titles on the row above (detail) or
    below (summary), then one row per commodity (code, title, values...)."""
    codes_row = rows[header]
    titles_row = rows[header - 1] if level == "detail" else rows[header + 1]
    columns = {j: _code(c) for j, c in enumerate(codes_row) if j >= 2 and _code(c)}
    titles = {_code(codes_row[j]): _code(titles_row[j]) for j in columns}
    # Industry columns come first, up to the total of intermediate uses (T001).
    first_total = min(j for j, c in columns.items() if c == "T001")
    industries = {c for j, c in columns.items() if j < first_total}
    cells = []
    for row in rows[header + (1 if level == "detail" else 2) :]:
        commodity = _code(row[0]) if row else None
        if not commodity or commodity.startswith("Note") or len(row) < 3:
            continue
        titles.setdefault(commodity, _code(row[1]))
        for j, buyer in columns.items():
            value = row[j] if j < len(row) else None
            if isinstance(value, (int, float)) and value:
                cells.append(
                    {
                        "level": level,
                        "year": year,
                        "commodity": commodity,
                        "buyer": buyer,
                        "value": float(value),
                    }
                )
    codes = [
        {
            "level": level,
            "code": code,
            "title": title or code,
            "kind": "industry" if code in industries else _kind(code),
        }
        for code, title in titles.items()
    ]
    return codes, cells


def _sheets(data: bytes, name: str) -> dict[str, list[tuple]]:
    import openpyxl

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        book = openpyxl.load_workbook(io.BytesIO(archive.read(name)), read_only=True)
    return {ws.title: list(ws.iter_rows(values_only=True)) for ws in book.worksheets}


def _header_row(rows: list[tuple]) -> int:
    """The row holding the column codes: the one whose cells include T001."""
    return next(i for i, row in enumerate(rows[:12]) if "T001" in row)


def parse_supply_use(data: bytes) -> dict[str, list[dict[str, Any]]]:
    detail = _sheets(data, DETAIL_FILE)
    hierarchy, naics = parse_hierarchy(detail.pop("NAICS Codes")[5:])
    codes: dict[tuple[str, str], dict[str, Any]] = {}
    cells: list[dict[str, Any]] = []
    tables = [("detail", detail), ("summary", _sheets(data, SUMMARY_FILE))]
    for level, sheets in tables:
        for name, rows in sheets.items():
            if not name.isdigit():
                continue
            sheet_codes, sheet_cells = parse_use(rows, level, int(name), _header_row(rows))
            cells += sheet_cells
            for c in sheet_codes:
                codes.setdefault((c["level"], c["code"]), c)
    # The hierarchy's titles and parents win for industries; table codes fill in the rest.
    for h in hierarchy:
        table = codes.get((h["level"], h["code"]))
        codes[(h["level"], h["code"])] = {**h, "kind": table["kind"] if table else "industry"}
    for c in codes.values():
        c.setdefault("summary", None)
        c.setdefault("sector", None)
    return {"codes": list(codes.values()), "naics": naics, "uses": cells}


def _rows(data: bytes) -> list[list[Any]]:
    """The first sheet of an .xls (Census's older files) or .xlsx workbook."""
    if data[:4] == b"PK\x03\x04":
        import openpyxl

        sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True).worksheets[0]
        return [list(r) for r in sheet.iter_rows(values_only=True)]
    import xlrd

    sheet = xlrd.open_workbook(file_contents=data).sheet_by_index(0)
    return [sheet.row_values(i) for i in range(sheet.nrows)]


def _number(value: Any, width: int) -> str | None:
    """A code cell (Excel stores 0111 as 111.0) as a zero-padded string."""
    if isinstance(value, (int, float)) and value > 0:
        return f"{int(value):0{width}d}"
    text = str(value or "").strip()
    return text.zfill(width) if text.isdigit() else None


def parse_concordance(name: str, data: bytes) -> list[dict[str, Any]]:
    """Census concordance sheets: source code in column A, target code in column C, after
    title rows (skipped: their cells aren't codes)."""
    system, target = name.split("-")
    width = 4 if system.startswith("sic") else 6
    out = []
    for row in _rows(data):
        if len(row) < 3:
            continue
        code, target_code = _number(row[0], width), _number(row[2], 6)
        if code and target_code:
            out.append(
                {
                    "system": system,
                    "code": code,
                    "target_system": target,
                    "target_code": target_code,
                }
            )
    return out


# --- loading and syncs ---


def load_concordance(session: Session, key: str, payload: bytes, fetched_at: datetime) -> int:
    system, target = key.split("-")
    session.execute(
        delete(Concordance).where(Concordance.system == system, Concordance.target_system == target)
    )
    rows = parse_concordance(key, payload)
    return upsert(
        session, Concordance, rows, key=["system", "code", "target_system", "target_code"]
    )


def load_supply_use(session: Session, key: str, payload: bytes, fetched_at: datetime) -> int:
    """A complete release: replaces every table (BEA revises past years too)."""
    parsed = parse_supply_use(payload)
    for table in (IoUse, IoNaics, IoCode):
        session.execute(delete(table))
    upsert(session, IoCode, parsed["codes"], key=["level", "code"])
    upsert(session, IoNaics, parsed["naics"], key=["code", "naics"])
    return upsert(session, IoUse, parsed["uses"], key=["level", "year", "commodity", "buyer"])


def sync_industries(
    session: Session, bea: BeaProvider, census: CensusProvider, *, force: bool = False
) -> int:
    """BEA's supply-use tables and Census's concordances, checked monthly (both change at
    most yearly), then the graph when anything was loaded."""
    since = datetime.now(UTC) - REFRESH
    rows = 0
    if force or "AllTablesSUP" not in synced_since(session, "bea", "supply_use", since):
        with tracked(session, "bea", "supply_use", "AllTablesSUP") as result:
            session.commit()  # fetch-then-load
            data = bea.fetch_supply_use()
            result["rows"] = load_supply_use(session, "AllTablesSUP", data, datetime.now(UTC))
        rows += result["rows"]
    done = synced_since(session, "census", "concordance", since)
    for name in CONCORDANCES:
        if not force and name in done:
            continue
        with tracked(session, "census", "concordance", name) as result:
            session.commit()
            data = census.fetch_concordance(name)
            result["rows"] = load_concordance(session, name, data, datetime.now(UTC))
        rows += result["rows"]
    if rows:
        from fin_intel import graph

        graph.build(session)
        session.commit()
    return rows


registry.register(
    Dataset(
        "bea",
        "supply_use",
        load_supply_use,
        ("industries",),
        payload="bytes",
        snapshot=True,
    ),
    Dataset(
        "census",
        "concordance",
        load_concordance,
        ("industries",),
        payload="bytes",
        snapshot=True,
    ),
)
