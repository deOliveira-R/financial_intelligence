"""Import broker exports into accounts, transactions and position snapshots.

Every format is converted to the generic rows below, then loaded by one code path.

Generic transactions CSV (header required):
    account,date,action,symbol,quantity,price,amount,fees,description
    - date: YYYY-MM-DD; action: buy, sell, reinvest, dividend, interest, split,
      transfer_in, transfer_out, fee, other
    - quantity: shares (positive); for split, shares added (negative for reverse splits)
    - amount: cash effect on the account (purchases negative), optional if price is given

Generic positions CSV:
    account,as_of,symbol,quantity,price,market_value,cost_basis,description

Re-importing an overlapping export is safe: each row's identity is a hash of its content
and of how many identical rows came before it in the file, so the same trade appearing in
two exports is recognized, while two genuinely identical trades in one file stay distinct.
"""

import csv
import hashlib
import io
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import Account, PortfolioTransaction, PositionSnapshot

ACTIONS = {
    "buy",
    "sell",
    "reinvest",
    "dividend",
    "interest",
    "split",
    "transfer_in",
    "transfer_out",
    "fee",
    "other",
}
TX_FIELDS = ["account", "date", "action", "symbol", "quantity", "price", "amount", "fees"]


class PortfolioImportError(ValueError):
    pass


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    value = str(value)
    value = value.strip().replace(",", "").replace("$", "")
    if value in ("", "--", "n/a", "N/A"):
        return None
    if value.startswith("(") and value.endswith(")"):  # accounting negatives
        value = "-" + value[1:-1]
    return float(value.replace("+", "").rstrip("%"))


def _field(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return None if value is None else _number(str(value))


def _date(value: date | str) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value.strip())


def _accounts(session: Session) -> dict[str, Account]:
    return {a.name: a for a in session.scalars(select(Account))}


def load_transactions(session: Session, rows: Iterable[dict[str, Any]], source: str) -> int:
    """Insert generic transaction rows; returns how many were new."""
    from fin_intel.ingest import get_security

    accounts = _accounts(session)
    seen: Counter[str] = Counter()
    records = []
    for n, row in enumerate(rows, start=1):
        name = row["account"]
        if name not in accounts:
            raise PortfolioImportError(
                f"row {n}: unknown account {name!r}; add it with `portfolio add-account`"
            )
        action = row["action"].strip().lower()
        if action not in ACTIONS:
            raise PortfolioImportError(f"row {n}: unknown action {action!r}")
        symbol = (row.get("symbol") or "").strip().upper() or None
        content = "|".join(str(row.get(f) or "").strip() for f in TX_FIELDS)
        seen[content] += 1
        ref = hashlib.sha256(f"{source}|{content}|{seen[content]}".encode()).hexdigest()
        security = get_security(session, symbol) if symbol else None
        records.append(
            {
                "account_id": accounts[name].id,
                "trade_date": _date(row["date"]),
                "action": action,
                "symbol": symbol,
                "security_id": security.id if security else None,
                "quantity": _field(row, "quantity"),
                "price": _field(row, "price"),
                "amount": _field(row, "amount"),
                "fees": _field(row, "fees") or 0.0,
                "description": row.get("description"),
                "source": source,
                "source_ref": ref,
                "imported_at": datetime.now(UTC),
            }
        )
    existing = set(
        session.scalars(
            select(PortfolioTransaction.source_ref).where(
                PortfolioTransaction.source_ref.in_([r["source_ref"] for r in records])
            )
        )
    )
    new = [r for r in records if r["source_ref"] not in existing]
    session.add_all(PortfolioTransaction(**r) for r in new)
    session.commit()
    return len(new)


def load_positions(session: Session, rows: Iterable[dict[str, Any]]) -> int:
    accounts = _accounts(session)
    records = []
    for n, row in enumerate(rows, start=1):
        if row["account"] not in accounts:
            raise PortfolioImportError(f"row {n}: unknown account {row['account']!r}")
        records.append(
            {
                "account_id": accounts[row["account"]].id,
                "as_of": _date(row["as_of"]),
                "symbol": row["symbol"].strip().upper(),
                "description": row.get("description"),
                "quantity": _field(row, "quantity") or 0.0,
                "price": _field(row, "price"),
                "market_value": _field(row, "market_value"),
                "cost_basis": _field(row, "cost_basis"),
            }
        )
    count = upsert(session, PositionSnapshot, records, key=["account_id", "as_of", "symbol"])
    session.commit()
    return count


def read_generic(path: Path | str) -> list[dict[str, str]]:
    text = Path(path).read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


# --- Fidelity --------------------------------------------------------------------------


def account_type_from_name(name: str) -> str:
    upper = name.upper()
    for marker, account_type in (
        ("ROTH", "roth_ira"),
        ("401", "401k"),
        ("HSA", "hsa"),
        ("IRA", "ira"),
    ):
        if marker in upper:
            return account_type
    return "taxable"


def ensure_accounts(
    session: Session, accounts: dict[str, tuple[str, str]], broker: str
) -> list[str]:
    """Create accounts named in an export if missing: {name: (type, last4)}. Returns new names."""
    existing = _accounts(session)
    created = []
    for name, (account_type, last4) in accounts.items():
        if name not in existing:
            session.add(
                Account(
                    name=name,
                    broker=broker,
                    account_type=account_type,
                    taxable=account_type == "taxable",
                    number_last4=last4,
                )
            )
            created.append(name)
    session.commit()
    return created


def read_fidelity_positions(
    path: Path | str,
) -> tuple[list[dict[str, Any]], dict[str, tuple[str, str]]]:
    """Fidelity's Portfolio Positions CSV: generic position rows plus the accounts it names.

    The file has a byte-order mark, CRLF line endings, a trailing empty column, $ and +
    signs, money market funds (e.g. SPAXX**) with a value but no quantity, and disclaimer
    paragraphs ending in "Date downloaded Oct-03-2026 4:58 a.m ET", which dates the snapshot.
    """
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    table, _, footer = text.partition("\n\n")
    as_of = date.today()
    for line in footer.splitlines():
        if "Date downloaded" in line:
            stamp = line.split("Date downloaded", 1)[1].strip().strip('"').split()[0]
            as_of = datetime.strptime(stamp, "%b-%d-%Y").date()
    rows, accounts = [], {}
    for r in csv.DictReader(io.StringIO(table)):
        symbol = (r.get("Symbol") or "").strip()
        if not symbol:
            continue
        name = f"Fidelity {r['Account name'].strip()}"
        accounts[name] = (account_type_from_name(name), r["Account number"].strip()[-4:])
        value = _number(r.get("Current value"))
        quantity = _number(r.get("Quantity"))
        is_cash = symbol.endswith("**")  # money market sweep: $1.00 per share
        rows.append(
            {
                "account": name,
                "as_of": as_of,
                "symbol": symbol.rstrip("*"),
                "description": (r.get("Description") or "").strip(),
                "quantity": value if is_cash else quantity,
                "price": 1.0 if is_cash else _number(r.get("Last price")),
                "market_value": value,
                "cost_basis": value if is_cash else _number(r.get("Cost basis total")),
            }
        )
    return rows, accounts


def _fidelity_positions(path: Path | str) -> list[dict[str, Any]]:
    rows, _ = read_fidelity_positions(path)
    return rows


# Broker-specific readers convert their exports to generic rows.
TRANSACTION_FORMATS = {"generic": read_generic}
POSITION_FORMATS = {"generic": read_generic, "fidelity": _fidelity_positions}
