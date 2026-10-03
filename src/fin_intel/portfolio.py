"""Portfolio: tax lots, gains, harvesting candidates and position context.

Lots are never stored: they're rebuilt from the broker's transactions on every request, so
a correction to the transactions or to this logic never leaves stale lots behind.

Rules (US individual taxpayer):
- Lots are matched first-in, first-out (FIFO), the default at most brokers.
- A lot is long-term only if sold more than one year after it was acquired.
- Wash sale: a loss is disallowed if the same security was bought within 30 days before
  or after the sale, in ANY account (IRAs included). Harvesting checks this across all
  accounts; "substantially identical" beyond the same security is a judgment call.
- Splits come from the broker's own split records when it has any for a symbol, otherwise
  from market corporate actions, never both.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.models import (
    Account,
    CorporateAction,
    DailyBar,
    PortfolioTransaction,
    PositionSnapshot,
    Security,
)
from fin_intel.prices import adjustments

WASH_SALE_DAYS = 30
EXCHANGE_TRADED_TYPES = ("ETF", "ETV", "ETN", "ETS")  # funds, vehicles, notes, single-stock
LOT_ACTIONS = {"buy", "reinvest", "transfer_in"}


# --- lot engine (pure) -----------------------------------------------------------------


@dataclass(frozen=True)
class Tx:
    id: int
    account_id: int
    trade_date: date
    action: str
    symbol: str
    quantity: float | None = None
    price: float | None = None
    amount: float | None = None
    fees: float = 0.0


@dataclass
class Lot:
    account_id: int
    symbol: str
    acquired: date
    quantity: float
    cost_basis: float | None  # total; None when unknown (e.g. transfers in without basis)
    tx_id: int

    @property
    def cost_per_share(self) -> float | None:
        return None if self.cost_basis is None else self.cost_basis / self.quantity


@dataclass(frozen=True)
class Realized:
    account_id: int
    symbol: str
    acquired: date | None  # None: shares sold that the history doesn't show being bought
    sold: date
    quantity: float
    proceeds: float
    cost_basis: float | None
    term: str | None  # "short" | "long"
    wash_sale_risk: bool = False

    @property
    def gain(self) -> float | None:
        return None if self.cost_basis is None else self.proceeds - self.cost_basis


@dataclass
class LotBook:
    open: list[Lot] = field(default_factory=list)
    realized: list[Realized] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def holding_term(acquired: date, sold: date) -> str:
    """Long-term means held more than one year: sold after the acquisition anniversary."""
    try:
        anniversary = acquired.replace(year=acquired.year + 1)
    except ValueError:  # acquired on Feb 29
        anniversary = date(acquired.year + 1, 3, 1)
    return "long" if sold > anniversary else "short"


def build_lots(
    transactions: list[Tx], market_splits: dict[str, list[tuple[date, float]]] | None = None
) -> LotBook:
    """Replay transactions into open lots and realized gains (FIFO per account and symbol)."""
    book = LotBook()
    market_splits = market_splits or {}
    broker_split_symbols = {t.symbol for t in transactions if t.action == "split"}
    events: list[tuple[date, int, object]] = [(t.trade_date, 1, t) for t in transactions]
    for symbol, splits in market_splits.items():
        if symbol in broker_split_symbols:
            continue  # the broker's own split records win
        events += [(ex_date, 0, (symbol, ratio)) for ex_date, ratio in splits]
    # Splits apply at the start of their ex-date; same-day transactions are post-split.
    events.sort(key=lambda e: (e[0], e[1], getattr(e[2], "id", 0)))

    lots: dict[tuple[int, str], list[Lot]] = defaultdict(list)
    for _day, _, event in events:
        if isinstance(event, tuple):
            symbol, ratio = event
            for (_, sym), held in lots.items():
                if sym == symbol:
                    for lot in held:
                        lot.quantity *= ratio
            continue
        assert isinstance(event, Tx)
        tx = event
        held = lots[(tx.account_id, tx.symbol)]
        qty = abs(tx.quantity or 0.0)
        if tx.action in LOT_ACTIONS and qty:
            if tx.amount is not None:
                cost = abs(tx.amount)
            elif tx.price is not None:
                cost = qty * tx.price + (tx.fees or 0.0)
            else:
                cost = None
            if tx.action == "transfer_in" and tx.amount is None:
                book.warnings.append(
                    f"{tx.symbol}: transfer in on {tx.trade_date} has no cost basis or "
                    "original acquisition date"
                )
            held.append(Lot(tx.account_id, tx.symbol, tx.trade_date, qty, cost, tx.id))
        elif tx.action == "split" and tx.quantity:
            total = sum(lot.quantity for lot in held)
            if total > 0:
                ratio = (total + tx.quantity) / total
                for lot in held:
                    lot.quantity *= ratio
        elif tx.action in ("sell", "transfer_out") and qty:
            consumed = _consume(held, qty)
            if tx.action == "sell":
                book.realized += _realize(tx, qty, consumed)
            if (missing := qty - sum(q for _, q in consumed)) > 1e-6:
                book.warnings.append(
                    f"{tx.symbol}: {tx.action} of {qty:g} on {tx.trade_date} exceeds the "
                    f"{qty - missing:g} shares the history shows; is the history complete?"
                )
            lots[(tx.account_id, tx.symbol)] = [lot for lot in held if lot.quantity > 1e-9]

    book.open = [lot for held in lots.values() for lot in held if lot.quantity > 1e-9]
    book.realized = _flag_wash_sales(book.realized, transactions)
    return book


def _consume(held: list[Lot], quantity: float) -> list[tuple[Lot, float]]:
    """Take shares from the oldest lots first; returns (lot snapshot, shares taken)."""
    taken = []
    for lot in held:
        if quantity <= 1e-9:
            break
        portion = min(lot.quantity, quantity)
        cost = None if lot.cost_basis is None else lot.cost_basis * portion / lot.quantity
        taken.append((replace(lot, quantity=portion, cost_basis=cost), portion))
        if lot.cost_basis is not None and cost is not None:
            lot.cost_basis -= cost
        lot.quantity -= portion
        quantity -= portion
    return taken


def _realize(tx: Tx, quantity: float, consumed: list[tuple[Lot, float]]) -> list[Realized]:
    if tx.amount is not None:
        proceeds = abs(tx.amount)
    else:
        proceeds = quantity * (tx.price or 0.0) - (tx.fees or 0.0)
    out = [
        Realized(
            account_id=tx.account_id,
            symbol=tx.symbol,
            acquired=lot.acquired,
            sold=tx.trade_date,
            quantity=portion,
            proceeds=proceeds * portion / quantity,
            cost_basis=lot.cost_basis,
            term=holding_term(lot.acquired, tx.trade_date),
        )
        for lot, portion in consumed
    ]
    if (missing := quantity - sum(q for _, q in consumed)) > 1e-6:
        out.append(
            Realized(
                tx.account_id,
                tx.symbol,
                None,
                tx.trade_date,
                missing,
                proceeds * missing / quantity,
                None,
                None,
            )
        )
    return out


def _flag_wash_sales(realized: list[Realized], transactions: list[Tx]) -> list[Realized]:
    """Mark losses with a purchase of the same symbol, in any account, within 30 days
    before or after the sale (other than the shares sold). Basis isn't adjusted here."""
    buys = defaultdict(list)
    for t in transactions:
        if t.action in ("buy", "reinvest"):
            buys[t.symbol].append(t.trade_date)
    window = timedelta(days=WASH_SALE_DAYS)
    out = []
    for r in realized:
        risky = (
            r.gain is not None
            and r.gain < 0
            and any(abs(day - r.sold) <= window and day != r.acquired for day in buys[r.symbol])
        )
        out.append(replace(r, wash_sale_risk=risky))
    return out


# --- database-backed views -------------------------------------------------------------


def load_transactions(session: Session) -> list[Tx]:
    rows = session.scalars(
        select(PortfolioTransaction).order_by(
            PortfolioTransaction.trade_date, PortfolioTransaction.id
        )
    )
    return [
        Tx(
            r.id,
            r.account_id,
            r.trade_date,
            r.action,
            (r.symbol or "").upper(),
            r.quantity,
            r.price,
            r.amount,
            r.fees or 0.0,
        )
        for r in rows
        if r.symbol
    ]


def lot_book(session: Session) -> LotBook:
    transactions = load_transactions(session)
    return build_lots(transactions, _market_splits(session, {t.symbol for t in transactions}))


def _security_ids(session: Session, symbols: set[str]) -> dict[str, int]:
    from fin_intel.ingest import get_security

    out = {}
    for symbol in symbols:
        if (security := get_security(session, symbol)) is not None:
            out[symbol] = security.id
    return out


def _market_splits(session: Session, symbols: set[str]) -> dict[str, list[tuple[date, float]]]:
    ids = _security_ids(session, symbols)
    by_id = {v: k for k, v in ids.items()}
    rows = session.execute(
        select(CorporateAction.security_id, CorporateAction.ex_date, CorporateAction.value)
        .where(
            CorporateAction.security_id.in_(ids.values()),
            CorporateAction.action == "split",
            CorporateAction.ex_date <= date.today(),
        )
        .distinct()
    )
    splits: dict[str, list[tuple[date, float]]] = defaultdict(list)
    seen = set()
    for security_id, ex_date, ratio in rows:
        if (security_id, ex_date) not in seen:  # one split may be reported by two sources
            seen.add((security_id, ex_date))
            splits[by_id[security_id]].append((ex_date, ratio))
    return dict(splits)


def latest_prices(session: Session, symbols: set[str]) -> dict[str, tuple[float, date, str]]:
    """Latest close per symbol: market data if we have it, else the latest broker snapshot
    (e.g. 401(k) trusts with no public price). Returns (price, as_of, source)."""
    ids = _security_ids(session, symbols)
    out: dict[str, tuple[float, date, str]] = {}
    if ids:
        latest = (
            select(DailyBar.security_id, func.max(DailyBar.date).label("d"))
            .where(DailyBar.security_id.in_(ids.values()))
            .group_by(DailyBar.security_id)
            .subquery()
        )
        rows = session.execute(
            select(DailyBar.security_id, DailyBar.date, DailyBar.close, DailyBar.source).join(
                latest,
                (latest.c.security_id == DailyBar.security_id) & (latest.c.d == DailyBar.date),
            )
        ).all()
        by_id = {v: k for k, v in ids.items()}
        for security_id, day, close, source in sorted(rows, key=lambda r: r.source != "massive"):
            if close is not None:
                out.setdefault(by_id[security_id], (close, day, source))
    missing = symbols - out.keys()
    if missing:
        snapshots = session.execute(
            select(PositionSnapshot.symbol, PositionSnapshot.price, PositionSnapshot.as_of)
            .where(PositionSnapshot.symbol.in_(missing), PositionSnapshot.price.is_not(None))
            .order_by(PositionSnapshot.as_of)
        )
        for symbol, price, as_of in snapshots:
            out[symbol] = (price, as_of, "broker")
    return out


@dataclass(frozen=True)
class LotView:
    account: str
    taxable: bool
    symbol: str
    acquired: date | None  # None for broker positions (no transaction history imported)
    quantity: float
    cost_basis: float | None
    price: float | None
    market_value: float | None
    unrealized: float | None
    term: str  # "short", "long", or "unknown" without an acquisition date
    basis_source: str = "lots"  # "lots": from transactions; "broker": the broker's snapshot


def lot_views(session: Session, as_of: date | None = None) -> list[LotView]:
    """Open lots from transaction history, plus one pseudo-lot per broker position in
    accounts with no history yet (cost basis from the broker, acquisition date unknown)."""
    as_of = as_of or date.today()
    book = lot_book(session)
    accounts = {a.id: a for a in session.scalars(select(Account))}
    with_history = {t.account_id for t in load_transactions(session)}
    broker = [r for r in _latest_snapshot_rows(session) if r.account_id not in with_history]
    prices = latest_prices(session, {lot.symbol for lot in book.open} | {r.symbol for r in broker})
    views = []
    for r in broker:
        price = prices.get(r.symbol, (r.price,))[0]
        value = None if price is None else price * r.quantity
        views.append(
            LotView(
                account=accounts[r.account_id].name,
                taxable=accounts[r.account_id].taxable,
                symbol=r.symbol,
                acquired=None,
                quantity=r.quantity,
                cost_basis=r.cost_basis,
                price=price,
                market_value=value,
                unrealized=None if value is None or r.cost_basis is None else value - r.cost_basis,
                term="unknown",
                basis_source="broker",
            )
        )
    for lot in sorted(book.open, key=lambda x: (x.symbol, x.acquired)):
        price = prices.get(lot.symbol, (None,))[0]
        value = None if price is None else price * lot.quantity
        views.append(
            LotView(
                account=accounts[lot.account_id].name,
                taxable=accounts[lot.account_id].taxable,
                symbol=lot.symbol,
                acquired=lot.acquired,
                quantity=lot.quantity,
                cost_basis=lot.cost_basis,
                price=price,
                market_value=value,
                unrealized=None
                if value is None or lot.cost_basis is None
                else value - lot.cost_basis,
                term=holding_term(lot.acquired, as_of),
            )
        )
    return views


@dataclass(frozen=True)
class Position:
    account: str
    symbol: str
    quantity: float
    cost_basis: float | None
    market_value: float | None
    unrealized: float | None
    unrealized_short: float | None  # from lots held one year or less
    unrealized_long: float | None
    broker_quantity: float | None  # from the latest position snapshot, if imported
    reconciled: bool | None  # derived quantity matches the broker's; None if nothing to compare
    basis_source: str  # "lots" (from transactions) or "broker" (snapshot only, no history)


def positions(session: Session, as_of: date | None = None) -> list[Position]:
    """Lots rolled up per account and symbol, checked against the broker's own snapshot."""
    groups: dict[tuple[str, str], list[LotView]] = defaultdict(list)
    for lot in lot_views(session, as_of):
        groups[(lot.account, lot.symbol)].append(lot)
    snapshots = _latest_snapshots(session)
    for key in snapshots.keys() - groups.keys():
        groups[key] = []  # held per the broker, but no lots derived: history is missing

    def total(values: list[float | None]) -> float | None:
        """Sum, or None if any part is unknown (e.g. a lot without cost basis)."""
        return None if not values or any(v is None for v in values) else sum(values)

    def by_term(lots: list[LotView], term: str) -> float | None:
        if any(lot.term == "unknown" for lot in lots):
            return None  # no acquisition dates: the split can't be known
        matching = [lot.unrealized for lot in lots if lot.term == term]
        return total(matching) if matching else (0.0 if lots else None)

    out = []
    for (account, symbol), lots in sorted(groups.items()):
        quantity = sum(lot.quantity for lot in lots)
        from_broker = bool(lots) and all(lot.basis_source == "broker" for lot in lots)
        broker = snapshots.get((account, symbol))
        out.append(
            Position(
                account=account,
                symbol=symbol,
                quantity=quantity,
                cost_basis=total([lot.cost_basis for lot in lots]),
                market_value=total([lot.market_value for lot in lots]),
                unrealized=total([lot.unrealized for lot in lots]),
                unrealized_short=by_term(lots, "short"),
                unrealized_long=by_term(lots, "long"),
                broker_quantity=broker,
                reconciled=None if broker is None or from_broker else abs(broker - quantity) < 1e-4,
                basis_source="broker" if from_broker else "lots",
            )
        )
    return out


def _latest_snapshots(session: Session) -> dict[tuple[str, str], float]:
    latest = (
        select(PositionSnapshot.account_id, func.max(PositionSnapshot.as_of).label("d"))
        .group_by(PositionSnapshot.account_id)
        .subquery()
    )
    rows = session.execute(
        select(Account.name, PositionSnapshot.symbol, PositionSnapshot.quantity)
        .join(Account, Account.id == PositionSnapshot.account_id)
        .join(
            latest,
            (latest.c.account_id == PositionSnapshot.account_id)
            & (latest.c.d == PositionSnapshot.as_of),
        )
    )
    return {(name, symbol): quantity for name, symbol, quantity in rows}


def _latest_snapshot_rows(session: Session) -> list[PositionSnapshot]:
    latest = (
        select(PositionSnapshot.account_id, func.max(PositionSnapshot.as_of).label("d"))
        .group_by(PositionSnapshot.account_id)
        .subquery()
    )
    return list(
        session.scalars(
            select(PositionSnapshot).join(
                latest,
                (latest.c.account_id == PositionSnapshot.account_id)
                & (latest.c.d == PositionSnapshot.as_of),
            )
        )
    )


@dataclass(frozen=True)
class HarvestCandidate:
    lot: LotView
    loss: float
    loss_pct: float
    blocking_purchases: list[tuple[str, date, float]]  # (account, date, shares) in last 30 days
    rebuy_after: date  # earliest repurchase date that won't wash the loss
    # False for broker positions without transaction history: the holding period and any
    # purchases in the last 30 days (including reinvested dividends) can't be checked.
    history_known: bool = True


def harvest_candidates(
    session: Session, min_loss: float = 0.0, min_loss_pct: float = 0.0, as_of: date | None = None
) -> list[HarvestCandidate]:
    """Lots in taxable accounts with an unrealized loss, and what would block harvesting it.

    A purchase of the same security in any account during the 30 days before the sale
    would wash the loss; so would buying it back within 30 days after.
    """
    as_of = as_of or date.today()
    accounts = {a.id: a.name for a in session.scalars(select(Account))}
    recent = defaultdict(list)
    for t in load_transactions(session):
        if t.action in ("buy", "reinvest") and as_of - t.trade_date <= timedelta(WASH_SALE_DAYS):
            recent[t.symbol].append((accounts[t.account_id], t.trade_date, abs(t.quantity or 0)))
    out = []
    for lot in lot_views(session, as_of):
        if not lot.taxable or lot.unrealized is None or lot.unrealized >= 0 or not lot.cost_basis:
            continue
        loss, pct = -lot.unrealized, -lot.unrealized / lot.cost_basis
        if loss < min_loss or pct < min_loss_pct:
            continue
        blockers = [b for b in recent[lot.symbol] if b[1] != lot.acquired]
        out.append(
            HarvestCandidate(
                lot,
                loss,
                pct,
                blockers,
                as_of + timedelta(WASH_SALE_DAYS + 1),
                history_known=lot.basis_source == "lots",
            )
        )
    return sorted(out, key=lambda c: -c.loss)


# --- position context ("buy more?") ----------------------------------------------------


@dataclass(frozen=True)
class Context:
    symbol: str
    as_of: date
    price: float
    high_52w: float
    low_52w: float
    drawdown_from_high: float  # e.g. -0.25 = 25% below the 52-week high
    vs_200d_avg: float | None  # e.g. 0.08 = 8% above the 200-day average
    returns: dict[str, float | None]  # total return over 1m/3m/6m/1y
    spy_returns: dict[str, float | None]


WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "1y": 252}


def _adjusted_closes(session: Session, security_id: int) -> list[tuple[date, float]]:
    """Total-return closes (splits and dividends) from the source with the latest data."""
    source = session.scalar(
        select(DailyBar.source)
        .where(DailyBar.security_id == security_id)
        .order_by(DailyBar.date.desc(), DailyBar.source != "massive")
        .limit(1)
    )
    if source is None:
        return []
    bars = list(
        session.scalars(
            select(DailyBar)
            .where(DailyBar.security_id == security_id, DailyBar.source == source)
            .where(DailyBar.date >= date.today() - timedelta(days=450))
            .order_by(DailyBar.date)
        )
    )
    actions = session.execute(
        select(CorporateAction.ex_date, CorporateAction.action, CorporateAction.value).where(
            CorporateAction.security_id == security_id, CorporateAction.source == source
        )
    ).all()
    factors = adjustments(bars, [tuple(a) for a in actions])
    return [(b.date, b.close * factors[b.date].price) for b in bars if b.close]


def _returns(closes: list[tuple[date, float]]) -> dict[str, float | None]:
    last = closes[-1][1] if closes else None
    return {
        name: (last / closes[-1 - n][1] - 1) if last and len(closes) > n else None
        for name, n in WINDOWS.items()
    }


def context(session: Session, symbol: str) -> Context | None:
    from fin_intel.ingest import get_security

    security, spy = get_security(session, symbol), get_security(session, "SPY")
    closes = _adjusted_closes(session, security.id) if security else []
    if len(closes) < 2:
        return None
    year = [c for _, c in closes[-252:]]
    price = closes[-1][1]
    high, low = max(year), min(year)
    avg200 = sum(c for _, c in closes[-200:]) / 200 if len(closes) >= 200 else None
    return Context(
        symbol=symbol.upper(),
        as_of=closes[-1][0],
        price=price,
        high_52w=high,
        low_52w=low,
        drawdown_from_high=price / high - 1,
        vs_200d_avg=None if avg200 is None else price / avg200 - 1,
        returns=_returns(closes),
        spy_returns=_returns(_adjusted_closes(session, spy.id)) if spy else {},
    )


# --- replacement candidates for harvesting --------------------------------------------


@dataclass(frozen=True)
class Replacement:
    ticker: str
    name: str | None
    correlation: float
    beta: float  # 1.0 moves like the target; 3.0 is a 3x leveraged fund
    tracking_error: float  # annualized; lower means closer


def replacements(
    session: Session, symbol: str, top: int = 10, min_dollar_volume: float = 5e6
) -> list[Replacement]:
    """Liquid ETFs that track `symbol` most closely over the past year, ranked by tracking
    error (annualized volatility of daily return differences).

    Candidates to keep market exposure while a harvested loss waits out the wash-sale
    window. Tracking error, unlike correlation, ranks leveraged funds (2x, 3x) far down:
    they move in the same direction but by a multiple. An ETF tracking the very same index
    may be considered "substantially identical"; that call is yours (or your tax adviser's).
    """
    from fin_intel.ingest import get_security

    target = get_security(session, symbol)
    if target is None:
        return []
    since = date.today() - timedelta(days=380)
    # Commodity trusts (GLD, IAU) are "ETV"s and notes "ETN"s in Massive's types, not "ETF"s.
    etf_ids = select(Security.id).where(
        Security.security_type.in_(EXCHANGE_TRADED_TYPES), Security.active
    )
    liquid = (
        select(DailyBar.security_id)
        .where(DailyBar.source == "massive", DailyBar.date >= date.today() - timedelta(days=90))
        .where(DailyBar.security_id.in_(etf_ids))
        .group_by(DailyBar.security_id)
        .having(func.avg(DailyBar.close * DailyBar.volume) >= min_dollar_volume)
    )
    rows = session.execute(
        select(DailyBar.security_id, DailyBar.date, DailyBar.close)
        .where(DailyBar.source == "massive", DailyBar.date >= since)
        .where(DailyBar.security_id.in_(liquid) | (DailyBar.security_id == target.id))
        .order_by(DailyBar.security_id, DailyBar.date)
    ).all()
    series: dict[int, dict[date, float]] = defaultdict(dict)
    for security_id, day, close in rows:
        if close:
            series[security_id][day] = close
    if target.id not in series:
        return []
    days = sorted(series[target.id])
    base = _daily_returns(series[target.id], days)
    tickers = dict(
        session.execute(select(Security.id, Security.ticker).where(Security.id.in_(series))).all()
    )
    names = dict(
        session.execute(select(Security.id, Security.name).where(Security.id.in_(series))).all()
    )
    scored = []
    for security_id, closes in series.items():
        if security_id == target.id or len(closes) < len(days) * 0.9:
            continue
        stats = _tracking(base, _daily_returns(closes, days))
        if stats is not None:
            scored.append(Replacement(tickers[security_id], names[security_id], *stats))
    return sorted(scored, key=lambda r: r.tracking_error)[:top]


def _daily_returns(closes: dict[date, float], days: list[date]) -> list[float | None]:
    out: list[float | None] = [None]
    for prev, day in zip(days, days[1:], strict=False):
        a, b = closes.get(prev), closes.get(day)
        out.append(b / a - 1 if a and b else None)
    return out


def _tracking(xs: list[float | None], ys: list[float | None]) -> tuple[float, float, float] | None:
    """(correlation, beta, annualized tracking error) of ys against xs."""
    pairs = [(x, y) for x, y in zip(xs, ys, strict=True) if x is not None and y is not None]
    # Splits and dividends aren't adjusted here; skip the rare outsized one-day moves.
    pairs = [(x, y) for x, y in pairs if abs(x) < 0.25 and abs(y) < 0.25]
    if len(pairs) < 60:
        return None
    n = len(pairs)
    mx, my = sum(p[0] for p in pairs) / n, sum(p[1] for p in pairs) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x, _ in pairs)
    vy = sum((y - my) ** 2 for _, y in pairs)
    if not vx or not vy:
        return None
    diffs = [y - x for x, y in pairs]
    md = sum(diffs) / n
    tracking_error = math.sqrt(sum((d - md) ** 2 for d in diffs) / (n - 1) * 252)
    return cov / math.sqrt(vx * vy), cov / vx, tracking_error
