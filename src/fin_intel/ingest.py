"""Load provider payloads into the normalized tables.

Live syncs fetch everything an item needs first and only then load it, because the raw
store commits each response in its own transaction (so it survives a failed load).

Each (provider, dataset) has one loader taking the raw payload. Live syncs fetch (which
records the raw response) and then call the loader; `rebuild.py` replays stored raw
responses through the same loaders. The API only ever reads what these have stored.
"""

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel import (
    congress,
    cot,
    crosslist,
    dart,
    derive,
    energy,
    esef,
    insiders,
    releases,
    taiwan,
    thirteenf,
    world,
    xbrl,
)
from fin_intel.db import upsert
from fin_intel.models import (
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    EconomicVintage,
    Fact,
    Filing,
    InsiderTransaction,
    Issuer,
    Security,
    SyncState,
    TickerHistory,
)
from fin_intel.providers import (
    CftcProvider,
    DartProvider,
    EdinetProvider,
    EiaProvider,
    FedProvider,
    FredProvider,
    HouseProvider,
    MassiveProvider,
    NotFoundError,
    OpenFigiProvider,
    ProviderError,
    SecProvider,
    SenateProvider,
    TiingoProvider,
    fred,
    massive,
    sec,
    tiingo,
)
from fin_intel.raw import RawStore

log = logging.getLogger(__name__)


# --- securities ------------------------------------------------------------------------


def get_security(session: Session, ticker: str) -> Security | None:
    """The security currently using `ticker`, else the one that used it most recently."""
    ticker = ticker.upper()
    security = session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        security = session.scalar(
            select(Security)
            .join(TickerHistory, TickerHistory.security_id == Security.id)
            .where(TickerHistory.ticker == ticker)
            .order_by(TickerHistory.last_seen.desc())
        )
    return security


def record_tickers(session: Session, securities: list[Security], today: date) -> None:
    rows = [
        {"security_id": s.id, "ticker": s.ticker, "first_seen": today, "last_seen": today}
        for s in securities
        if s.ticker
    ]
    upsert(session, TickerHistory, rows, key=["security_id", "ticker"], update=["last_seen"])


def load_company_tickers(session: Session, payload: Any, today: date) -> int:
    """Reconcile our securities with SEC's current ticker list.

    - Rename: a CIK's old ticker vanished and exactly one new ticker appeared for it
      (FB -> META); the existing security takes the new ticker and keeps its history.
    - Reuse: a ticker now belongs to a different CIK; the old holder gives it up and a new
      security is created, so the two companies' price histories never merge.
    - Delisting: an SEC-origin security missing from the list is marked inactive but keeps
      its ticker until someone else takes it.
    Securities other sources created are left alone unless SEC lists their ticker, in which
    case SEC claims them.
    """
    incoming: dict[str, dict] = {}
    for row in sec.parse_company_tickers(payload):
        if row["ticker"]:
            incoming[row["ticker"].upper()] = row  # the SEC file can repeat a ticker
    issuers = {r["cik"]: {"cik": r["cik"], "name": r["name"]} for r in incoming.values()}
    upsert(session, Issuer, issuers.values(), key=["cik"])

    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    # Securities with a CIK that no longer hold a listed ticker: candidates for a rename.
    unlisted: dict[int, list[Security]] = defaultdict(list)

    for ticker, row in incoming.items():
        holder = by_ticker.get(ticker)
        if holder is not None and holder.cik is not None and holder.cik != row["cik"]:
            log.warning("%s moved from CIK %s to CIK %s", ticker, holder.cik, row["cik"])
            holder.ticker = None
            del by_ticker[ticker]
            unlisted[holder.cik].append(holder)
    session.flush()  # release reused tickers before anyone takes them
    for s in by_ticker.values():
        if s.origin == "sec" and s.cik is not None and s.ticker not in incoming:
            unlisted[s.cik].append(s)

    new_tickers_per_cik = Counter(r["cik"] for t, r in incoming.items() if t not in by_ticker)
    for ticker, row in incoming.items():
        security = by_ticker.get(ticker)
        if security is None:
            candidates = unlisted.get(row["cik"], [])
            if len(candidates) == 1 and new_tickers_per_cik[row["cik"]] == 1:
                security = candidates.pop()
                log.info("%s renamed to %s", security.ticker, ticker)
                by_ticker.pop(security.ticker, None)
                security.ticker = ticker
            else:
                security = Security(ticker=ticker)
                session.add(security)
            by_ticker[ticker] = security
        security.name, security.exchange, security.cik = row["name"], row["exchange"], row["cik"]
        security.active = True
        security.origin = "sec"  # SEC's list is the authority for what it lists

    # Securities other sources created (ETFs from Massive, ...) are theirs to deactivate.
    for s in securities:
        if s.origin == "sec" and s.cik is not None and s.ticker not in incoming:
            s.active = False
    session.flush()
    record_tickers(session, [by_ticker[t] for t in incoming], today)
    return len(incoming)


def load_tiingo_metadata(session: Session, ticker: str, payload: Any, today: date) -> int:
    """Create a security for a symbol no reference list has given us yet."""
    if get_security(session, ticker) is not None:
        return 0
    meta = tiingo.parse_metadata(payload)
    security = Security(
        ticker=meta["ticker"], name=meta["name"], exchange=meta["exchange"], origin="tiingo"
    )
    session.add(security)
    session.flush()
    record_tickers(session, [security], today)
    return 1


MASSIVE_ORIGINS = {"stocks": "massive", "otc": "massive-otc"}


def load_massive_tickers(session: Session, market: str, payload: Any, today: date) -> int:
    """Add and enrich securities from one page of Massive's reference tickers.

    - Every listed security gets Massive's type, FIGIs and primary exchange.
    - Unknown symbols (ETFs, funds, notes SEC doesn't list) become securities with
      origin "massive"; known ones keep their SEC name and CIK.
    - Identity is the composite FIGI, which survives ticker changes. For securities Massive
      created, a known FIGI under a new symbol is a rename, and a held symbol arriving with
      a different FIGI is a reuse (the old holder gives the symbol up). Securities SEC lists
      are never renamed or released by Massive: another symbol for their FIGI becomes an
      alias in their ticker history (VSEE -> VSEED, CSAN -> CSANY).
    Deactivation needs the whole list, so it is a separate step (deactivate_unseen_massive).
    Securities created from the OTC list have origin "massive-otc"; the main list promotes
    them to "massive" if they uplist.
    """
    origin = MASSIVE_ORIGINS[market]
    rows = massive.parse_tickers(payload)
    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    by_figi = {s.figi: s for s in securities if s.figi}
    seen, aliases = [], []
    for row in rows:
        symbol, figi = row["symbol"], row["figi"]
        security = by_ticker.get(symbol)
        if security is not None and figi and security.figi and security.figi != figi:
            if not _massive_owned(security):
                # SEC lists this symbol for another instrument; SEC decides.
                log.info(
                    "%s: Massive FIGI %s differs from %s; skipped", symbol, figi, security.figi
                )
                continue
            log.warning("%s moved from FIGI %s to %s", symbol, security.figi, figi)
            security.ticker, security.active = None, False
            del by_ticker[symbol]
            session.flush()  # release the symbol before anyone takes it
            security = None
        if security is not None and row["cik"] and security.cik and security.cik != row["cik"]:
            # Same symbol, issuer attributed differently (e.g. a preferred issued by a
            # subsidiary). SEC is the authority on CIKs; the type and FIGIs still apply.
            log.info("%s: Massive CIK %s, SEC CIK %s; kept SEC's", symbol, row["cik"], security.cik)
        if security is None and figi and (same := by_figi.get(figi)) is not None:
            if not _massive_owned(same):
                # A security SEC lists, shown by Massive under another symbol: a temporary
                # one (VSEE -> VSEED after a reverse split), an OTC line (CSAN -> CSANY) or
                # a rename SEC hasn't caught up with. Record an alias; SEC keeps the ticker.
                aliases.append((same, symbol))
                continue
            log.info("%s renamed to %s (FIGI %s)", same.ticker, symbol, figi)
            by_ticker.pop(same.ticker, None)
            same.ticker = symbol
            security = same
        if security is None:
            security = Security(ticker=symbol, name=row["name"], origin=origin)
            session.add(security)
        by_ticker[symbol] = security
        if figi:
            by_figi[figi] = security

        if row["cik"] and security.cik is None:
            upsert(session, Issuer, [{"cik": row["cik"], "name": None}], key=["cik"], update=[])
            security.cik = row["cik"]
        security.name = security.name or row["name"]
        security.security_type = row["security_type"]
        security.figi = figi or security.figi
        security.share_class_figi = row["share_class_figi"] or security.share_class_figi
        security.mic = row["mic"]
        if security.origin in MASSIVE_ORIGINS.values():
            if origin == "massive":
                security.origin = origin
            security.active = True
        seen.append(security)
    session.flush()
    record_tickers(session, seen, today)
    upsert(
        session,
        TickerHistory,
        [
            {"security_id": sec.id, "ticker": sym, "first_seen": today, "last_seen": today}
            for sec, sym in aliases
        ],
        key=["security_id", "ticker"],
        update=["last_seen"],
    )
    return len(seen)


def _massive_owned(security: Security) -> bool:
    """Created from Massive's lists: Massive may rename it or give its symbol up."""
    return security.origin.startswith("massive")


def deactivate_unseen_massive(session: Session, market: str, as_of: date) -> int:
    """Deactivate securities a Massive list created but hasn't listed since `as_of`."""
    last_seen = (
        select(func.max(TickerHistory.last_seen))
        .where(TickerHistory.security_id == Security.id)
        .scalar_subquery()
    )
    stale = session.scalars(
        select(Security).where(
            Security.origin == MASSIVE_ORIGINS[market],
            Security.active,
            func.coalesce(last_seen, date.min) < as_of,
        )
    ).all()
    for security in stale:
        security.active = False
    return len(stale)


def load_massive_delisted(session: Session, payload: Any) -> int:
    """Record delisted securities, so history isn't limited to today's survivors.

    For each delisted listing (symbol S, last traded around date d), in order:
    1. Same composite FIGI as a security we know: it's that security, under an old symbol
       (a rename, recorded in its ticker history) or now delisted.
    2. S's current holder has the same CIK (or either identifier is missing): that
       company has just delisted; its data stays put.
    3. Otherwise S was reused: a separate delisted security takes S's history up to d,
       and rows already attached to the new holder for those dates move to it.
    Delisted securities keep `ticker` NULL; S is in their ticker history through d.
    """
    rows = massive.parse_delisted(payload)
    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    by_figi = {s.figi: s for s in securities if s.figi}
    known = set(
        session.execute(
            select(TickerHistory.ticker, Security.delisted_on)
            .join(Security, Security.id == TickerHistory.security_id)
            .where(Security.ticker.is_(None), Security.delisted_on.is_not(None))
        ).all()
    )
    history, repairs, count = [], [], 0
    for row in rows:
        symbol, delisted_on, figi, cik = row["symbol"], row["delisted_on"], row["figi"], row["cik"]
        if (symbol, delisted_on) in known:
            continue  # imported on an earlier sync
        if figi and (same := by_figi.get(figi)) is not None:
            if same.ticker == symbol:
                _mark_delisted(same, delisted_on)
            else:  # an old symbol of a renamed security
                history.append((same, symbol, delisted_on))
            continue
        holder = by_ticker.get(symbol)
        if holder is not None and _same_company(holder, cik, figi):
            _mark_delisted(holder, delisted_on)
            holder.figi = holder.figi or figi
            continue
        if cik:
            upsert(session, Issuer, [{"cik": cik, "name": None}], key=["cik"], update=[])
        security = Security(
            ticker=None,
            name=row["name"],
            origin="massive-delisted",
            active=False,
            security_type=row["security_type"],
            mic=row["mic"],
            cik=cik,
            figi=figi,
            share_class_figi=row["share_class_figi"],
            delisted_on=delisted_on,
        )
        session.add(security)
        history.append((security, symbol, delisted_on))
        known.add((symbol, delisted_on))
        if figi:
            by_figi[figi] = security
        if holder is not None:
            repairs.append((holder.id, delisted_on))
        count += 1
    session.flush()
    upsert(
        session,
        TickerHistory,
        [
            {"security_id": s.id, "ticker": t, "first_seen": d, "last_seen": d}
            for s, t, d in history
        ],
        key=["security_id", "ticker"],
        update=["last_seen"],
    )
    # Market rows for a reused symbol were attached to its current holder; those dated up
    # to the old listing's delisting belong to the delisted security (backfilled from raw).
    for holder_id, until in repairs:
        session.execute(
            delete(DailyBar).where(
                DailyBar.security_id == holder_id,
                DailyBar.source == "massive",
                DailyBar.date <= until,
            )
        )
        session.execute(
            delete(CorporateAction).where(
                CorporateAction.security_id == holder_id,
                CorporateAction.source == "massive",
                CorporateAction.ex_date <= until,
            )
        )
    return count


def _mark_delisted(security: Security, delisted_on: date) -> None:
    security.delisted_on = delisted_on
    if security.origin.startswith("massive"):  # SEC decides for securities it lists
        security.active = False


def load_filing_xbrl(session: Session, key: str, body: bytes) -> int:
    """Facts from one filing's XBRL instance (where SEC's company facts lack them)."""
    cik, accession, filed, form = key.split("|")
    payload = xbrl.parse_instance(body, accession, form, date.fromisoformat(filed))
    if not payload["facts"]:
        return 0
    return load_company_facts(session, int(cik), payload, supplement=True)


# --- Korea (DART) --------------------------------------------------------------------------


def load_dart_corp_codes(session: Session, body: bytes) -> int:
    corps = dart.parse_corp_codes(body)
    for c in corps:
        world.ensure_issuer(
            session, "dart", c["corp_code"], c["name"] or None, home_ticker=c["stock_code"]
        )
    return len(corps)


def load_dart_company(session: Session, corp_code: str, payload: Any) -> int:
    if (payload or {}).get("status") != "000":
        return 0
    month = str(payload.get("acc_mt") or "").strip()
    world.ensure_issuer(
        session,
        "dart",
        corp_code,
        payload.get("corp_name_eng") or payload.get("corp_name"),
        home_ticker=(payload.get("stock_code") or "").strip() or None,
        fiscal_month=int(month) if month.isdigit() else None,
    )
    return 1


def _dart_issuer(session: Session, corp_code: str) -> tuple[int, int]:
    cik = world.ensure_issuer(session, "dart", corp_code)
    issuer = session.get(Issuer, cik)
    return cik, (issuer.fiscal_month if issuer and issuer.fiscal_month else 12)


def load_dart_statements(session: Session, key: str, payload: Any) -> int:
    if (payload or {}).get("status") != "000":
        return 0
    corp, year, report, _ = key.split("|")
    cik, fiscal_month = _dart_issuer(session, corp)
    facts = dart.parse_statements(payload, int(year), report, fiscal_month)
    if not facts:
        return 0
    return load_company_facts(session, cik, world.facts_payload(facts), supplement=True)


def load_dart_share_counts(session: Session, key: str, payload: Any) -> int:
    if (payload or {}).get("status") != "000":
        return 0
    corp, year, report = key.split("|")
    cik, _ = _dart_issuer(session, corp)
    facts = dart.parse_share_counts(payload, int(year), report)
    if not facts:
        return 0
    return load_company_facts(session, cik, world.facts_payload(facts), supplement=True)


def sync_dart_corps(session: Session, provider: DartProvider) -> int:
    with tracked(session, "dart", "corp_codes", "all") as result:
        session.commit()
        result["rows"] = load_dart_corp_codes(session, provider.fetch_corp_codes())
    return result["rows"]


def sync_dart_company(session: Session, provider: DartProvider, corp_code: str) -> int:
    with tracked(session, "dart", "company", corp_code) as result:
        session.commit()
        result["rows"] = load_dart_company(session, corp_code, provider.fetch_company(corp_code))
    return result["rows"]


def sync_dart_report(
    session: Session, provider: DartProvider, corp_code: str, year: int, report: str, shares: bool
) -> int:
    """One report's statements (consolidated, else separate) and, if asked, share counts.
    Everything is fetched before anything is loaded (fetch-then-load)."""
    with tracked(session, "dart", "report", f"{corp_code}|{year}|{report}") as result:
        session.commit()
        statements = None
        for fs_div in ("CFS", "OFS"):
            payload = provider.fetch_statements(corp_code, year, report, fs_div)
            if payload is not None:
                statements = (fs_div, payload)
                break
        counts = provider.fetch_share_counts(corp_code, year, report) if shares else None
        rows = 0
        if statements is not None:
            fs_div, payload = statements
            key = f"{corp_code}|{year}|{report}|{fs_div}"
            rows += load_dart_statements(session, key, payload)
        if counts is not None:
            rows += load_dart_share_counts(session, f"{corp_code}|{year}|{report}", counts)
        result["rows"] = rows
    return result["rows"]


# --- cross-listings ------------------------------------------------------------------------


def load_listings(session: Session, key: str, request: list[dict], response: list[dict]) -> int:
    """Apply an OpenFIGI mapping made for cross-listings. The key says what for:
    `home|...` (home tickers), `isin|<LEI>` (one European issuer's ISINs), `us|...`."""
    kind = key.split("|", 1)[0]
    if kind == "isin":
        cik = world.issuer_id("esef", key.split("|")[1])
        return crosslist.apply_home(session, [(job, cik) for job in request], response)
    targets: list[int | None] = []
    if kind == "us":
        tickers = [job["idValue"].replace("/", "-") for job in request]
        ids = dict(
            session.execute(
                select(Security.ticker, Security.id).where(Security.ticker.in_(tickers))
            ).all()
        )
        targets = [ids.get(t) for t in tickers]
    else:
        sources = {"JT": "edinet", "KS": "dart", "KQ": "dart", "TT": "twse"}
        for job in request:
            source = sources.get(job.get("exchCode", ""))
            home = _home_id(session, source, job["idValue"]) if source else None
            targets.append(world.issuer_id(source, home) if source and home else None)
    pairs = [(job, t) for job, t in zip(request, targets, strict=True) if t]
    results = [r for r, t in zip(response, targets, strict=False) if t]
    apply = crosslist.apply_us if kind == "us" else crosslist.apply_home
    return apply(session, pairs, results)


def _home_id(session: Session, source: str, ticker: str) -> str:
    """The regulator id of the issuer with this home ticker (DART and EDINET ids differ
    from tickers; TWSE's are the tickers)."""
    found = session.scalar(
        select(Issuer.source_id).where(Issuer.source == source, Issuer.home_ticker == ticker)
    )
    return found or ticker


def sync_crosslist(session: Session, openfigi: OpenFigiProvider, gleif: Any) -> dict[str, int]:
    """Find foreign issuers' share classes (home tickers; ISINs via GLEIF for European
    issuers), the share classes of US OTC lines and ADRs, then link them."""
    stats: dict[str, int] = defaultdict(int)

    def run(kind: str, pairs: list, apply: Callable) -> None:
        for i in range(0, len(pairs), openfigi.batch):
            batch = pairs[i : i + openfigi.batch]
            session.commit()  # fetch-then-load
            key = f"{kind}|{batch[0][0]['idValue']}"
            results = openfigi.map_jobs([job for job, _ in batch], key)
            stats[kind] += apply(session, batch, results)
            session.commit()

    run("home", crosslist.home_jobs(session), crosslist.apply_home)
    europeans = session.execute(
        select(Issuer.cik, Issuer.lei).where(
            Issuer.source == "esef", Issuer.lei.is_not(None), Issuer.share_class_figi.is_(None)
        )
    ).all()
    for cik, lei in europeans:
        session.commit()
        isins = [r["attributes"]["isin"] for r in (gleif.fetch_isins(lei) or {}).get("data") or []]
        if not isins:
            continue
        jobs = [({"idType": "ID_ISIN", "idValue": isin}, cik) for isin in isins[: openfigi.batch]]
        results = openfigi.map_jobs([job for job, _ in jobs], f"isin|{lei}")
        stats["isin"] += crosslist.apply_home(session, jobs, results)
        session.commit()
    run("us", crosslist.us_jobs(session), crosslist.apply_us)
    stats.update(crosslist.link(session))
    session.commit()
    return dict(stats)


# --- Europe (ESEF) -------------------------------------------------------------------------


def load_esef_report(session: Session, key: str, body: bytes) -> int:
    """One ESEF annual report. Key: `LEI|period end|country|date added|name`."""
    lei, _, country, added, name = key.split("|", 4)
    cik = world.ensure_issuer(session, "esef", lei, name or None, country=country, lei=lei)
    payload = esef.parse_report(body, f"esef:{lei}:{key.split('|')[1]}", date.fromisoformat(added))
    if not payload["facts"]:
        return 0
    return load_company_facts(session, cik, payload, supplement=True)


def sync_esef_report(session: Session, provider: Any, filing: dict[str, Any]) -> int:
    name = (filing["name"] or "").replace("|", " ")
    key = f"{filing['lei']}|{filing['period_end']}|{filing['country']}|{filing['added']}|{name}"
    with tracked(session, "esef", "report", f"{filing['lei']}|{filing['period_end']}") as result:
        session.commit()
        result["rows"] = load_esef_report(
            session, key, provider.fetch_report(filing["json_url"], key)
        )
    return result["rows"]


# --- Taiwan (TWSE / TPEx) --------------------------------------------------------------------


def load_twse_table(session: Session, key: str, payload: Any) -> int:
    """One exchange table: profiles (names), or a quarter's income statements or balance
    sheets for every company. Key: `twse|t187ap06_ci|2026-10-04`."""
    if not isinstance(payload, list):
        return 0
    _, table, snapshot = key.split("|")
    if table == "t187ap03":
        profiles = taiwan.parse_profiles(payload)
        for p in profiles:
            world.ensure_issuer(session, "twse", p["code"], p["name"], home_ticker=p["code"])
        return len(profiles)
    statement = "income" if table.startswith("t187ap06") else "balance"
    rows = 0
    for company, facts in taiwan.parse_table(
        payload, statement, date.fromisoformat(snapshot)
    ).items():
        cik = world.ensure_issuer(session, "twse", company, home_ticker=company)
        rows += load_company_facts(session, cik, world.facts_payload(facts), supplement=True)
        session.commit()  # one company at a time: a whole table would hold the write lock
    return rows


def load_tw_prices(session: Session, key: str, payload: Any) -> int:
    """One trading day's quotes on TWSE or TPEx, for companies we have filings for (ETFs and
    warrants are skipped). Each company's listing is created on first sight, priced in TWD.
    Key: `twse|2026-10-02`."""
    market, day = key.split("|")
    bars = taiwan.parse_prices(payload, market)
    if not bars:
        return 0
    issuers = dict(
        session.execute(select(Issuer.source_id, Issuer.name).where(Issuer.source == "twse")).all()
    )
    tickers = {f"{b['code']}.{taiwan.SUFFIX[market]}": b for b in bars if b["code"] in issuers}
    existing = dict(
        session.execute(
            select(Security.ticker, Security.id).where(Security.ticker.in_(list(tickers)))
        ).all()
    )
    for ticker, bar in tickers.items():
        if ticker not in existing:
            security = Security(
                ticker=ticker,
                name=issuers[bar["code"]],
                origin="twse",
                mic=taiwan.MICS[market],
                security_type="CS",
                cik=world.issuer_id("twse", bar["code"]),
                currency="TWD",
            )
            session.add(security)
            session.flush()
            existing[ticker] = security.id
    rows = [
        {
            "security_id": existing[t],
            "date": date.fromisoformat(day),
            "source": "twse",
            **{k: b[k] for k in ("open", "high", "low", "close", "volume")},
        }
        for t, b in tickers.items()
    ]
    return upsert(session, DailyBar, rows, key=["security_id", "date", "source"])


def sync_tw_prices(session: Session, provider: Any, market: str, day: date) -> int:
    with tracked(session, "twse", "prices", f"{market}|{day.isoformat()}") as result:
        session.commit()
        payload = provider.fetch_prices(market, day)
        result["rows"] = load_tw_prices(session, f"{market}|{day.isoformat()}", payload)
    return result["rows"]


def sync_twse_table(
    session: Session, provider: Any, market: str, table: str, snapshot: date
) -> int:
    with tracked(session, "twse", "table", f"{market}|{table}|{snapshot.isoformat()}") as result:
        session.commit()
        payload = provider.fetch(market, table, snapshot.isoformat())
        result["rows"] = load_twse_table(
            session, f"{market}|{table}|{snapshot.isoformat()}", payload
        )
    return result["rows"]


# --- Japan (EDINET) -------------------------------------------------------------------------


def load_edinet_instance(session: Session, key: str, body: bytes) -> int:
    """One EDINET report's facts. The raw key carries the document id, filer, type and
    filing date: `S100YE9I|E00776|120|2026-06-19`."""
    from fin_intel.providers.edinet import DOC_TYPES

    doc_id, edinet_code, doc_type, filed = key.split("|")
    payload = xbrl.parse_instance(
        body, f"edinet:{doc_id}", DOC_TYPES.get(doc_type), date.fromisoformat(filed)
    )
    from fin_intel import edinet

    dei = payload.pop("dei", {})
    edinet.add_derived(payload)
    year_end = dei.get("CurrentFiscalYearEndDateDEI") or ""
    code = (dei.get("SecurityCodeDEI") or "").strip()
    cik = world.ensure_issuer(
        session,
        "edinet",
        edinet_code,
        payload.get("entityName"),
        home_ticker=code[:4] or None,
        fiscal_month=int(year_end[5:7]) if len(year_end) >= 7 else None,
    )
    if not payload["facts"]:
        return 0
    return load_company_facts(session, cik, payload, supplement=True)


def edinet_reports(documents: Any) -> list[dict[str, Any]]:
    """Listed companies' annual, quarterly and half-year reports with XBRL in a day's list."""
    from fin_intel.providers.edinet import DOC_TYPES

    return [
        r
        for r in (documents or {}).get("results") or []
        if r.get("docTypeCode") in DOC_TYPES and r.get("secCode") and r.get("xbrlFlag") == "1"
    ]


def sync_edinet_day(session: Session, provider: EdinetProvider, day: date) -> int:
    """Every report a day's list names that isn't loaded yet."""
    with tracked(session, "edinet", "documents", day.isoformat()) as result:
        session.commit()
        reports = edinet_reports(provider.fetch_documents(day))
        store = provider.raw_store
        done = (
            {k.split("|")[0] for k in store.latest_hashes("edinet", "instance") if k}
            if store
            else set()
        )
        rows = 0
        for r in reports:
            if r["docID"] in done:
                continue
            filed = (r.get("submitDateTime") or day.isoformat())[:10]
            key = f"{r['docID']}|{r['edinetCode']}|{r['docTypeCode']}|{filed}"
            session.commit()  # fetch-then-load
            body = provider.fetch_instance(r["docID"], key)
            if body is not None:
                rows += load_edinet_instance(session, key, body)
        result["rows"] = rows
    return result["rows"]


def dart_periods(today: date, first_year: int = 2015) -> list[tuple[int, str]]:
    """(business year, report code) newest first, for reports past their filing deadline
    (December year ends: Q1 by May 15, half by Aug 14, Q3 by Nov 14, annual by Mar 31)."""
    deadlines = {"11013": (5, 16), "11012": (8, 15), "11014": (11, 15), "11011": (4, 1)}
    out = []
    for year in range(today.year, first_year - 1, -1):
        for report in ("11014", "11012", "11013"):
            month, day = deadlines[report]
            if date(year, month, day) <= today:
                out.append((year, report))
        if date(year + 1, 4, 1) <= today:
            out.insert(len(out) - sum(1 for y, _ in out if y == year), (year, "11011"))
    return out


def load_submissions(session: Session, cik: int, payload: Any) -> int:
    """A filer's SIC code and category (SEC submissions)."""
    issuer = session.get(Issuer, cik)
    if issuer is None:
        return 0
    sic = str(payload.get("sic") or "").strip()
    issuer.sic = int(sic) if sic.isdigit() else None
    issuer.sic_description = payload.get("sicDescription") or None
    issuer.filer_category = payload.get("category") or ""  # "" marks it looked up
    return 1


def load_massive_ticker_details(session: Session, ticker: str, payload: Any, today: date) -> int:
    """A listing's shares outstanding (for an ADR, in depositary shares)."""
    info = payload.get("results") or {}
    shares = info.get("share_class_shares_outstanding")
    figi = info.get("composite_figi")
    security = (
        session.scalar(select(Security).where(Security.figi == figi)) if figi else None
    ) or get_security(session, massive.normalize_symbol(ticker))
    if security is None or not shares:
        return 0
    security.shares_outstanding = float(shares)
    security.shares_as_of = today
    return 1


def _same_company(holder: Security, cik: int | None, figi: str | None) -> bool:
    """No identifier contradicts it. Missing identifiers count as a match: this never
    moves data on a guess."""
    return not (cik and holder.cik and holder.cik != cik) and not (
        figi and holder.figi and holder.figi != figi
    )


# --- prices ----------------------------------------------------------------------------


def load_tiingo_daily(session: Session, ticker: str, payload: Any) -> int:
    security = get_security(session, ticker)
    if security is None:
        raise ProviderError(f"{ticker}: unknown security; load its metadata first")
    bars, actions = tiingo.parse_daily(payload)
    for row in bars + actions:
        row["security_id"] = security.id
    upsert(session, CorporateAction, actions, key=["security_id", "ex_date", "action", "source"])
    return upsert(session, DailyBar, bars, key=["security_id", "date", "source"])


class SymbolResolver:
    """Which security a symbol meant on a given date.

    A symbol can pass from one company to another, and market-wide feeds identify rows by
    symbol only. Past holders are known from ticker_history entries for symbols a security
    no longer uses (delisted securities, renames), valid through their last_seen date; on
    later dates the symbol means its current holder.
    """

    def __init__(self, session: Session):
        self.current = dict(
            session.execute(
                select(Security.ticker, Security.id).where(Security.ticker.is_not(None))
            ).all()
        )
        self.past: dict[str, list[tuple[date, int]]] = defaultdict(list)
        rows = session.execute(
            select(TickerHistory.ticker, TickerHistory.last_seen, TickerHistory.security_id)
            .join(Security, Security.id == TickerHistory.security_id)
            .where((Security.ticker.is_(None)) | (Security.ticker != TickerHistory.ticker))
        )
        for symbol, last_seen, security_id in rows:
            self.past[symbol].append((last_seen, security_id))
        for listings in self.past.values():
            listings.sort()

    def resolve(self, symbol: str, on: date) -> int | None:
        past = self.past.get(symbol, ())
        for last_seen, security_id in past:
            if on <= last_seen:
                return security_id
        if symbol in self.current:
            return self.current[symbol]
        # No current holder: its most recent past holder (an alias still in use since the
        # last reference sync, or a delisted listing).
        return past[-1][1] if past else None


def _by_symbol(
    session: Session, rows: list[dict[str, Any]], date_field: str
) -> list[dict[str, Any]]:
    """Attach security_id to market-wide rows by symbol as of each row's date; drop
    unknown symbols (warrants, units, OTC names we don't track)."""
    resolver = SymbolResolver(session)
    out = []
    for row in rows:
        security_id = resolver.resolve(row.pop("symbol"), row[date_field])
        if security_id is not None:
            out.append({**row, "security_id": security_id})
    if skipped := len(rows) - len(out):
        log.info("skipped %d of %d rows with unknown symbols", skipped, len(rows))
    return out


def load_massive_grouped_daily(session: Session, day: str, payload: Any) -> int:
    rows = _by_symbol(
        session, massive.parse_grouped_daily(date.fromisoformat(day), payload), "date"
    )
    return upsert(session, DailyBar, rows, key=["security_id", "date", "source"])


def load_massive_actions(session: Session, dataset: str, payload: Any) -> int:
    parse = massive.parse_splits if dataset == "splits" else massive.parse_dividends
    rows = _by_symbol(session, parse(payload), "ex_date")
    return upsert(
        session, CorporateAction, rows, key=["security_id", "ex_date", "action", "source"]
    )


# --- fundamentals ----------------------------------------------------------------------


def load_company_facts(session: Session, cik: int, payload: Any, supplement: bool = False) -> int:
    """SEC company facts for one filer. With `supplement` (facts read from one filing's
    XBRL), names and concept labels already known aren't overwritten."""
    filings, concepts, facts = sec.parse_company_facts(cik, payload)
    keep = [] if supplement else None
    upsert(
        session, Issuer, [{"cik": cik, "name": payload.get("entityName")}], key=["cik"], update=keep
    )
    upsert(session, Filing, filings, key=["accession"])
    upsert(session, Concept, concepts, key=["taxonomy", "name"], update=keep)

    filing_ids = dict(
        session.execute(select(Filing.accession, Filing.id).where(Filing.cik == cik)).all()
    )
    concept_ids = {
        (t, n): i for t, n, i in session.execute(select(Concept.taxonomy, Concept.name, Concept.id))
    }
    rows = [
        {
            "filing_id": filing_ids[f["accession"]],
            "concept_id": concept_ids[f["concept"]],
            "unit": f["unit"],
            "period_start": f["period_start"],
            "period_end": f["period_end"],
            "instant": f["instant"],
            "value": f["value"],
            "frame": f["frame"],
            "cik": cik,
        }
        for f in facts
    ]
    count = upsert(
        session,
        Fact,
        rows,
        key=["filing_id", "concept_id", "unit", "period_start", "period_end"],
    )
    derive.derive_issuer(session, cik)
    return count


# --- economic data ---------------------------------------------------------------------


def load_fred_series(session: Session, payload: Any) -> int:
    return upsert(session, EconomicSeries, [fred.parse_series(payload)], key=["id"])


def load_fred_observations(session: Session, series_id: str, payload: Any) -> int:
    rows = fred.parse_observations(series_id, payload)
    return upsert(session, EconomicObservation, rows, key=["series_id", "date"])


def load_eia_series(session: Session, series_id: str, payload: Any) -> int:
    series, observations = energy.parse(series_id, payload)
    upsert(session, EconomicSeries, [series], key=["id"])
    return upsert(session, EconomicObservation, observations, key=["series_id", "date"])


def load_fred_vintages(session: Session, series_id: str, payload: Any) -> int:
    rows = fred.parse_vintages(series_id, payload)
    return upsert(session, EconomicVintage, rows, key=["series_id", "date", "realtime_start"])


# --- loader registry (used by rebuild) -------------------------------------------------

# (provider, dataset) -> loader(session, key, payload, fetched_at)
Loader = Callable[[Session, Any, Any, datetime], int]
LOADERS: dict[tuple[str, str], Loader] = {
    ("sec", "company_tickers"): lambda s, k, p, t: load_company_tickers(s, p, t.date()),
    ("sec", "companyfacts"): lambda s, k, p, t: load_company_facts(s, int(k), p),
    ("sec", "submissions"): lambda s, k, p, t: load_submissions(s, int(k), p),
    ("sec", "filing_xbrl"): lambda s, k, p, t: load_filing_xbrl(s, k, p),
    ("dart", "corp_codes"): lambda s, k, p, t: load_dart_corp_codes(s, p),
    ("dart", "company"): lambda s, k, p, t: load_dart_company(s, k, p),
    ("dart", "statements"): lambda s, k, p, t: load_dart_statements(s, k, p),
    ("dart", "share_counts"): lambda s, k, p, t: load_dart_share_counts(s, k, p),
    ("edinet", "documents"): lambda s, k, p, t: len(edinet_reports(p)),
    ("edinet", "instance"): lambda s, k, p, t: load_edinet_instance(s, k, p),
    ("twse", "table"): lambda s, k, p, t: load_twse_table(s, k, p),
    ("twse", "prices"): lambda s, k, p, t: load_tw_prices(s, k, p),
    ("esef", "index"): lambda s, k, p, t: len(esef.filings(p)),
    ("esef", "report"): lambda s, k, p, t: load_esef_report(s, k, p),
    ("tiingo", "metadata"): lambda s, k, p, t: load_tiingo_metadata(s, k, p, t.date()),
    ("tiingo", "daily_prices"): lambda s, k, p, t: load_tiingo_daily(s, k, p),
    ("massive", "tickers"): lambda s, k, p, t: load_massive_tickers(s, k, p, t.date()),
    ("massive", "delisted"): lambda s, k, p, t: load_massive_delisted(s, p),
    ("massive", "grouped_daily"): lambda s, k, p, t: load_massive_grouped_daily(s, k, p),
    ("massive", "splits"): lambda s, k, p, t: load_massive_actions(s, "splits", p),
    ("massive", "dividends"): lambda s, k, p, t: load_massive_actions(s, "dividends", p),
    ("massive", "ticker_details"): lambda s, k, p, t: load_massive_ticker_details(
        s, k, p, t.date()
    ),
    ("sec", "insider_dataset"): lambda s, k, p, t: load_insider_dataset(s, p),
    ("sec", "form4"): lambda s, k, p, t: load_form4(s, k, p),
    ("sec", "13f_dataset"): lambda s, k, p, t: load_13f_dataset(s, p),
    ("openfigi", "listings"): lambda s, k, p, t: load_listings(s, k, p["request"], p["response"]),
    ("openfigi", "mapping"): lambda s, k, p, t: load_openfigi_mapping(
        s, p["request"], p["response"]
    ),
    ("fred", "series"): lambda s, k, p, t: load_fred_series(s, p),
    ("fred", "observations"): lambda s, k, p, t: load_fred_observations(s, k, p),
    ("fred", "vintages"): lambda s, k, p, t: load_fred_vintages(s, k, p),
    ("house", "fd_index"): lambda s, k, p, t: congress.load_index(s, congress.parse_house_index(p)),
    ("house", "ptr"): lambda s, k, p, t: congress.load_report(s, *congress.parse_house_ptr(k, p)),
    ("senate", "search"): lambda s, k, p, t: congress.load_index(
        s, congress.parse_senate_search(p)
    ),
    ("senate", "ptr"): lambda s, k, p, t: congress.load_report(s, *congress.parse_senate_ptr(k, p)),
    ("eia", "series"): lambda s, k, p, t: load_eia_series(s, k, p),
    ("fred", "series_release"): lambda s, k, p, t: releases.load_series_release(s, k, p),
    ("fred", "release_dates"): lambda s, k, p, t: releases.load_release_dates(s, int(k), p),
    ("fed", "fomc_calendar"): lambda s, k, p, t: releases.load_fomc(s, p),
    **{
        ("cftc", report): (lambda r: lambda s, k, p, t: cot.load(s, r, p))(report)
        for report in cot.REPORTS
    },
}
# Datasets that create or rename securities. Rebuilds replay them before everything else,
# so symbol-keyed market data always resolves against the full security list.
REFERENCE_DATASETS = [
    ("sec", "company_tickers"),
    ("massive", "tickers"),
    ("massive", "delisted"),
    ("tiingo", "metadata"),
]
# Market-wide datasets keyed by symbol: rows for unknown symbols are skipped at load time,
# so they are replayed for securities discovered later (backfill_from_raw).
SYMBOL_KEYED_DATASETS = [
    ("massive", "grouped_daily"),
    ("massive", "splits"),
    ("massive", "dividends"),
]
# Datasets whose loaders take the raw body (bytes) rather than parsed JSON.
BINARY_DATASETS = {
    ("sec", "filing_xbrl"),
    ("dart", "corp_codes"),
    ("edinet", "instance"),
    ("esef", "report"),
    ("sec", "insider_dataset"),
    ("sec", "form4"),
    ("sec", "13f_dataset"),
    ("house", "fd_index"),
    ("house", "ptr"),
    ("senate", "ptr"),
    ("fed", "fomc_calendar"),
}
# Datasets whose response only means something with its request (recorded as params).
REQUEST_DATASETS = {("openfigi", "mapping"), ("openfigi", "listings")}
# Datasets where each response is a full snapshot, so only the latest one matters.
SNAPSHOT_DATASETS = {
    ("sec", "companyfacts"),
    ("massive", "grouped_daily"),  # one complete response per trading day
    ("massive", "ticker_details"),
    ("sec", "submissions"),
    ("dart", "corp_codes"),
    ("dart", "company"),
    ("fred", "series"),
    ("fred", "observations"),
    ("house", "fd_index"),  # the year's complete index
    ("eia", "series"),  # full history in every response
    ("fred", "series_release"),
    ("fred", "release_dates"),
    ("fed", "fomc_calendar"),
}


# --- live syncs ------------------------------------------------------------------------


def _today() -> date:
    """UTC, like raw fetched_at: a rebuild must stamp the same dates a live sync did."""
    return datetime.now(UTC).date()


@contextmanager
def tracked(session: Session, provider: str, dataset: str, key: str) -> Generator[dict[str, Any]]:
    """Record the outcome of one sync item in sync_state, committing or rolling back."""
    now = datetime.now(UTC)
    result: dict[str, Any] = {"rows": None}
    try:
        yield result
    except Exception as exc:
        session.rollback()
        state = {"last_attempt": now, "last_error": str(exc)[:1000]}
        upsert(
            session,
            SyncState,
            [{"provider": provider, "dataset": dataset, "key": key, **state}],
            key=["provider", "dataset", "key"],
        )
        session.commit()
        raise
    state = {"last_attempt": now, "last_success": now, "last_error": None, "rows": result["rows"]}
    upsert(
        session,
        SyncState,
        [{"provider": provider, "dataset": dataset, "key": key, **state}],
        key=["provider", "dataset", "key"],
    )
    session.commit()


def backfill_from_raw(session: Session, store: RawStore | None, symbols: set[str]) -> int:
    """Reload stored market-wide rows for newly discovered symbols, without the network.

    Grouped daily bars, splits and dividends were filtered to known symbols when first
    loaded. When a symbol gains a (security, symbol) pairing (a new security, a delisted
    listing, a rename) its rows are replayed from those same raw responses, and the
    date-aware resolver assigns each to the right security, so the live database matches
    what a rebuild would produce.
    """
    if store is None or not symbols:
        return 0
    records = list(
        store.records(
            provider="massive",
            datasets=[d for _, d in SYMBOL_KEYED_DATASETS],
            connection=session.connection(),
        )
    )
    latest = {(r.dataset, r.key): r.id for r in records}
    loaded = 0
    for r in records:
        if (r.provider, r.dataset) in SNAPSHOT_DATASETS and latest[(r.dataset, r.key)] != r.id:
            continue
        payload = r.json()
        rows = [
            row
            for row in payload.get("results") or []
            if massive.normalize_symbol(row.get("T") or row.get("ticker") or "") in symbols
        ]
        if rows:
            loaded += LOADERS[(r.provider, r.dataset)](
                session, r.key, {**payload, "results": rows}, r.fetched_at
            )
    log.info("backfilled %d rows for %d newly paired symbols", loaded, len(symbols))
    return loaded


@contextmanager
def discovering(session: Session, store: RawStore | None) -> Generator[None]:
    """Backfill stored market data for symbols that gained a (security, symbol) pairing
    inside the block: new securities, delisted listings and renames."""

    def pairs() -> set[tuple[int, str]]:
        return set(session.execute(select(TickerHistory.security_id, TickerHistory.ticker)).all())

    before = pairs()
    yield
    session.flush()
    backfill_from_raw(session, store, {symbol for _, symbol in pairs() - before})


def sync_tickers(session: Session, sec_provider: SecProvider) -> int:
    with tracked(session, "sec", "company_tickers", "all") as result:
        payload = sec_provider.fetch_company_tickers()
        with discovering(session, sec_provider.raw_store):
            result["rows"] = load_company_tickers(session, payload, _today())
    return result["rows"]


def sync_fundamentals(session: Session, sec_provider: SecProvider, ticker: str) -> int:
    security = get_security(session, ticker)
    if security is None or security.cik is None:
        raise ProviderError(f"{ticker}: no CIK known; run sync-tickers first")
    with tracked(session, "sec", "companyfacts", str(security.cik)) as result:
        payload = sec_provider.fetch_company_facts(security.cik)
        result["rows"] = load_company_facts(session, security.cik, payload)
    return result["rows"]


def load_bulk_company_facts(
    session: Session,
    store: RawStore,
    zip_path: Path,
    ciks: set[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Load SEC's bulk companyfacts.zip, one company at a time, skipping unchanged ones.

    Each entry is stored as its own raw response (dataset companyfacts, key = CIK), exactly
    like a per-company fetch, so rebuilds and retention treat both the same. Entries whose
    content matches the latest stored response for that company are skipped: on a normal
    night only companies that filed something are reloaded. Returns (loaded, unchanged).
    """
    import hashlib
    import json
    import zipfile

    latest = store.latest_hashes("sec", "companyfacts")
    loaded = unchanged = 0
    with zipfile.ZipFile(zip_path) as archive:
        entries = [
            (int(info.filename[3:13]), info)
            for info in archive.infolist()
            if info.filename.startswith("CIK") and info.filename.endswith(".json")
        ]
        entries = [(cik, info) for cik, info in entries if ciks is None or cik in ciks]
        for n, (cik, info) in enumerate(entries, start=1):
            body = archive.read(info)
            if latest.get(str(cik)) == hashlib.sha256(body).hexdigest():
                unchanged += 1
            else:
                # Fetch-then-load per company: commit first so the raw store's own
                # transaction never waits on this session's write lock.
                session.commit()
                store.save("sec", "companyfacts", str(cik), None, 200, body)
                load_company_facts(session, cik, json.loads(body))
                session.commit()
                loaded += 1
            if progress:
                progress(n, len(entries))
    return loaded, unchanged


def tracked_ciks(session: Session) -> set[int]:
    """Issuers we follow: those with at least one security, active or delisted."""
    return set(session.scalars(select(Security.cik).where(Security.cik.is_not(None)).distinct()))


def sync_prices(
    session: Session, tiingo_provider: TiingoProvider, ticker: str, start: date | None = None
) -> int:
    """Incrementally sync unadjusted bars and corporate actions. Stored history never needs
    refetching: adjustments are computed on read."""
    ticker = ticker.upper()
    with tracked(session, "tiingo", "daily_prices", ticker) as result:
        # Fetch everything before writing anything: the raw store commits each response in
        # its own transaction, which must not interleave with this session's writes.
        security = get_security(session, ticker)
        metadata = None
        if security is None:
            metadata = tiingo_provider.fetch_metadata(ticker)
            symbol, last_stored = tiingo.parse_metadata(metadata)["ticker"], None
        elif security.ticker is None:
            raise ProviderError(f"{ticker}: symbol now belongs to another security")
        else:
            symbol = security.ticker
            last_stored = session.scalar(
                select(func.max(DailyBar.date)).where(
                    DailyBar.security_id == security.id, DailyBar.source == tiingo_provider.name
                )
            )
        # Refetch the last stored day too, so a late correction to it is picked up.
        payload = tiingo_provider.fetch_daily(symbol, start=start or last_stored)

        if metadata is not None:
            load_tiingo_metadata(session, symbol, metadata, _today())
        result["rows"] = load_tiingo_daily(session, symbol, payload)
    return result["rows"]


def sync_reference_tickers(
    session: Session, massive_provider: MassiveProvider, market: str = "stocks"
) -> int:
    """Massive's full active ticker list for a market: add, enrich, then deactivate the
    Massive-origin securities it no longer lists. For stocks, also record securities
    delisted within our market history, and backfill their stored bars."""
    with tracked(session, "massive", "tickers", market) as result:
        pages = massive_provider.fetch_tickers(market)
        delisted = []
        if market == "stocks":
            # Delistings since our market history begins (Massive's free plan: 2 years).
            first_bar = session.scalar(
                select(func.min(DailyBar.date)).where(DailyBar.source == "massive")
            )
            delisted = massive_provider.fetch_delisted(first_bar or _today() - timedelta(days=730))
        today = _today()
        with discovering(session, massive_provider.raw_store):
            result["rows"] = sum(load_massive_tickers(session, market, p, today) for p in pages)
            for page in delisted:
                load_massive_delisted(session, page)
        deactivate_unseen_massive(session, market, today)
    return result["rows"]


THIN_FILING = 50  # a periodic report with fewer facts than this lacks its financials
QUARTERLY_STALE, ANNUAL_STALE = timedelta(days=200), timedelta(days=430)


def stale_issuers(session: Session) -> list[int]:
    """Issuers of primary listings whose latest financials are older than their filing
    rhythm allows (a quarter for 10-Q filers, a year for 20-F filers): a periodic report
    is probably missing from SEC's company facts."""
    from fin_intel import metrics
    from fin_intel.models import StatementItem

    ciks = list(metrics.primary_securities(session))
    latest = dict(
        session.execute(
            select(StatementItem.cik, func.max(StatementItem.period_end))
            .where(
                StatementItem.cik.in_(ciks),
                StatementItem.line_item.in_(("revenue", "net_income", "total_assets")),
            )
            .group_by(StatementItem.cik)
        ).all()
    )
    quarterly = set(
        session.scalars(
            select(Filing.cik)
            .where(Filing.form == "10-Q", Filing.filed >= _today() - timedelta(days=730))
            .distinct()
        )
    )
    today = _today()
    return sorted(
        cik
        for cik in ciks
        if latest.get(cik) is None
        or today - latest[cik] > (QUARTERLY_STALE if cik in quarterly else ANNUAL_STALE)
    )


def sync_xbrl_gaps(session: Session, sec_provider: SecProvider, cik: int) -> int:
    """Read the XBRL of an issuer's recent periodic reports that SEC's company facts are
    missing (or nearly empty for), straight from each filing."""
    with tracked(session, "sec", "xbrl_gaps", str(cik)) as result:
        session.commit()  # fetch-then-load
        submissions = sec_provider.fetch_submissions(cik)
        load_submissions(session, cik, submissions)
        recent = (submissions.get("filings") or {}).get("recent") or {}
        since = (_today() - timedelta(days=730)).isoformat()
        fact_counts = dict(
            session.execute(
                select(Filing.accession, func.count(Fact.filing_id))
                .outerjoin(Fact, Fact.filing_id == Filing.id)
                .where(Filing.cik == cik)
                .group_by(Filing.accession)
            ).all()
        )
        store = sec_provider.raw_store
        read = (
            {key.split("|")[1] for key in store.latest_hashes("sec", "filing_xbrl") if key}
            if store
            else set()
        )
        rows = 0
        for accession, form, filed, is_xbrl in zip(
            recent.get("accessionNumber", []),
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("isXBRL", []),
            strict=False,
        ):
            if form not in xbrl.PERIODIC_FORMS or not is_xbrl or filed < since:
                continue
            if fact_counts.get(accession, 0) >= THIN_FILING or accession in read:
                continue
            session.commit()
            index = sec_provider.fetch_filing_index(cik, accession)
            names = [i["name"] for i in (index.get("directory") or {}).get("item", [])]
            name = xbrl.instance_file(names)
            if name is None:
                continue
            body = sec_provider.fetch_xbrl_instance(
                cik, accession, name, form, date.fromisoformat(filed)
            )
            rows += load_filing_xbrl(session, f"{cik}|{accession}|{filed}|{form}", body)
        result["rows"] = rows
    return result["rows"]


def issuers_without_sic(session: Session) -> list[int]:
    """Issuers of primary listings whose SEC profile hasn't been looked up yet."""
    from fin_intel import metrics

    looked_up = set(session.scalars(select(Issuer.cik).where(Issuer.filer_category.is_not(None))))
    return sorted(cik for cik in metrics.primary_securities(session) if cik not in looked_up)


def sync_submissions(session: Session, sec_provider: SecProvider, cik: int) -> int:
    with tracked(session, "sec", "submissions", str(cik)) as result:
        session.commit()  # fetch-then-load
        result["rows"] = load_submissions(session, cik, sec_provider.fetch_submissions(cik))
    return result["rows"]


def due_listing_shares(session: Session, max_age_days: int = 28, limit: int = 150) -> list[str]:
    """ADRs (primary listings) whose depositary share count is missing or stale, oldest
    first. Each run refreshes at most `limit` (Massive's free plan: 5 calls a minute)."""
    from fin_intel import metrics

    cutoff = _today() - timedelta(days=max_age_days)
    due = [
        s
        for s in metrics.primary_securities(session).values()
        if s.security_type == "ADRC" and s.ticker and (s.shares_as_of or date.min) < cutoff
    ]
    due.sort(key=lambda s: s.shares_as_of or date.min)
    return [s.ticker for s in due[:limit] if s.ticker]


def sync_listing_shares(session: Session, massive_provider: MassiveProvider, ticker: str) -> int:
    with tracked(session, "massive", "ticker_details", ticker) as result:
        session.commit()  # fetch-then-load
        payload = massive_provider.fetch_ticker_details(ticker.replace("-", "."))
        result["rows"] = load_massive_ticker_details(session, ticker, payload, _today())
    return result["rows"]


def sync_market_daily(
    session: Session, massive_provider: MassiveProvider, day: date, include_otc: bool = False
) -> int:
    """Unadjusted bars for every known security on one trading day, in one call."""
    with tracked(session, "massive", "grouped_daily", day.isoformat()) as result:
        payload = massive_provider.fetch_grouped_daily(day, include_otc=include_otc)
        result["rows"] = load_massive_grouped_daily(session, day.isoformat(), payload)
    return result["rows"]


def sync_market_actions(
    session: Session, massive_provider: MassiveProvider, dataset: str, since: date
) -> int:
    """Market-wide splits or dividends with ex-dates on or after `since`."""
    fetch = (
        massive_provider.fetch_splits if dataset == "splits" else massive_provider.fetch_dividends
    )
    with tracked(session, "massive", dataset, "all") as result:
        pages = fetch(since)
        result["rows"] = sum(load_massive_actions(session, dataset, page) for page in pages)
    return result["rows"]


# --- insiders -----------------------------------------------------------------------------

INSIDER_FORMS = ("4", "4/A")
_FILED = re.compile(rb"FILED AS OF DATE:\s*(\d{8})")


def load_insider_dataset(session: Session, body: bytes) -> int:
    return insiders.load(session, insiders.parse_dataset(body))


def load_form4(session: Session, accession: str, body: bytes) -> int:
    match = _FILED.search(body)
    filed = datetime.strptime(match.group(1).decode(), "%Y%m%d").date() if match else None
    return insiders.load(session, insiders.parse_form4(accession, filed, body))


def sync_insider_dataset(
    session: Session, sec_provider: SecProvider, quarter: str, url: str
) -> int:
    with tracked(session, "sec", "insider_dataset", quarter) as result:
        body = sec_provider.fetch_insider_dataset(quarter, url)
        result["rows"] = load_insider_dataset(session, body)
    return result["rows"]


def sync_insider_day(session: Session, sec_provider: SecProvider, day: date) -> int:
    """Every Form 4 filed on a day that isn't loaded yet (each fetched individually)."""
    with tracked(session, "sec", "insider_day", day.isoformat()) as result:
        filings = [
            (a, p) for form, a, p in sec_provider.fetch_daily_index(day) if form in INSIDER_FORMS
        ]
        have = set(
            session.scalars(
                select(InsiderTransaction.accession).where(
                    InsiderTransaction.accession.in_([a for a, _ in filings])
                )
            )
        )
        bodies = [(a, sec_provider.fetch_submission(a, p)) for a, p in filings if a not in have]
        result["rows"] = sum(load_form4(session, a, body) for a, body in bodies)
    return result["rows"]


# --- institutional holdings (13F) -------------------------------------------------------------


def load_13f_dataset(session: Session, body: bytes) -> int:
    count = thirteenf.load(session, body)
    thirteenf.link_securities(session)
    return count


def load_openfigi_mapping(session: Session, request: list[dict], response: list[dict]) -> int:
    count = thirteenf.load_openfigi(session, [job["idValue"] for job in request], response)
    thirteenf.link_securities(session)
    return count


def sync_13f_dataset(session: Session, sec_provider: SecProvider, period: str, url: str) -> int:
    with tracked(session, "sec", "13f_dataset", period) as result:
        body = sec_provider.fetch_13f_dataset(period, url)
        result["rows"] = load_13f_dataset(session, body)
    return result["rows"]


def sync_cusip_mappings(
    session: Session, openfigi: OpenFigiProvider, progress: Callable[[int, int], None] | None = None
) -> int:
    """Look up every unmapped CUSIP on OpenFIGI, one committed batch at a time (a first
    run without an API key takes hours; progress survives interruptions)."""
    cusips = thirteenf.unmapped_cusips(session)
    done = 0
    for i in range(0, len(cusips), openfigi.batch):
        batch = cusips[i : i + openfigi.batch]
        session.commit()  # fetch-then-load: no write lock held during the request
        results = openfigi.map_cusips(batch)
        thirteenf.load_openfigi(session, batch, results)
        session.commit()
        done += len(batch)
        if progress:
            progress(done, len(cusips))
    thirteenf.link_securities(session)
    session.commit()
    return done


# --- congressional trades -------------------------------------------------------------------


def sync_house_index(session: Session, house: HouseProvider, year: int) -> int:
    """The year's filing index: which PTRs exist (each is then fetched on its own)."""
    with tracked(session, "house", "fd_index", str(year)) as result:
        session.commit()  # fetch-then-load
        result["rows"] = LOADERS[("house", "fd_index")](
            session, str(year), house.fetch_index(year), datetime.now(UTC)
        )
    return result["rows"]


def sync_house_ptr(session: Session, house: HouseProvider, doc_id: str, year: int) -> int:
    with tracked(session, "house", "ptr", doc_id) as result:
        session.commit()
        try:
            body = house.fetch_ptr(doc_id, year)
        except NotFoundError:
            # Listed but never published (withdrawn): don't retry it every day.
            congress.load_report(session, {"doc_id": doc_id, "chamber": "house"}, [])
            result["rows"] = 0
        else:
            result["rows"] = LOADERS[("house", "ptr")](session, doc_id, body, datetime.now(UTC))
    return result["rows"]


def sync_senate_index(session: Session, senate: SenateProvider, since: date) -> int:
    """Every PTR submitted since `since`, page by page."""
    with tracked(session, "senate", "search", since.isoformat()) as result:
        rows, start = 0, 0
        while True:
            session.commit()
            page = senate.search_ptrs(since.strftime("%m/%d/%Y"), start)
            rows += congress.load_index(session, congress.parse_senate_search(page))
            start += senate.page_size
            if start >= int(page.get("recordsFiltered") or 0):
                break
        result["rows"] = rows
    return result["rows"]


def sync_senate_ptr(session: Session, senate: SenateProvider, doc_id: str) -> int:
    with tracked(session, "senate", "ptr", doc_id) as result:
        session.commit()
        result["rows"] = LOADERS[("senate", "ptr")](
            session, doc_id, senate.fetch_ptr(doc_id), datetime.now(UTC)
        )
    return result["rows"]


def sync_release_calendar(
    session: Session, fred_provider: FredProvider, fed_provider: FedProvider
) -> int:
    """Which release each tracked FRED series comes out in (looked up once per series),
    then every such release's dates (past year plus the published schedule), and the
    FOMC meeting calendar."""
    with tracked(session, "fred", "release_dates", "all") as result:
        session.commit()
        releases.load_fomc(session, fed_provider.fetch_fomc_calendar())
        unmapped = session.scalars(
            select(EconomicSeries.id).where(
                EconomicSeries.source == "fred", EconomicSeries.release_id.is_(None)
            )
        ).all()
        for series_id in unmapped:
            session.commit()  # fetch-then-load
            releases.load_series_release(
                session, series_id, fred_provider.fetch_series_release(series_id)
            )
        ids = set(
            session.scalars(
                select(EconomicSeries.release_id).where(EconomicSeries.release_id.is_not(None))
            )
        )
        since = _today() - timedelta(days=releases.HISTORY_DAYS)
        rows = 0
        for release_id in sorted(ids):
            session.commit()
            rows += releases.load_release_dates(
                session, release_id, fred_provider.fetch_release_dates(release_id, since)
            )
        result["rows"] = rows
    return result["rows"]


# --- EIA energy data -------------------------------------------------------------------------


def sync_eia(session: Session, eia_provider: EiaProvider, series_id: str) -> int:
    route = energy.SERIES[series_id][0]
    with tracked(session, "eia", "series", series_id) as result:
        session.commit()  # fetch-then-load
        result["rows"] = load_eia_series(
            session, series_id, eia_provider.fetch_series(route, series_id)
        )
    return result["rows"]


# --- CFTC positioning -----------------------------------------------------------------------


def sync_cot(session: Session, cftc: CftcProvider, report: str, since: date) -> int:
    """A COT report's rows for the curated markets since `since`, page by page."""
    if report == "legacy":
        codes = [code for code, _, _ in cot.MARKETS.values()]
    else:
        family = next(f for f, r in cot.FAMILY_REPORT.items() if r == report)
        codes = [code for code, fam, _ in cot.MARKETS.values() if fam == family]
    with tracked(session, "cftc", report, since.isoformat()) as result:
        rows, offset = 0, 0
        while True:
            session.commit()  # fetch-then-load
            page = cftc.fetch_reports(report, cot.REPORTS[report], codes, since, offset)
            rows += cot.load(session, report, page)
            if len(page) < cftc.page_size:
                break
            offset += cftc.page_size
        result["rows"] = rows
    return result["rows"]


def sync_economic(session: Session, fred_provider: FredProvider, series_id: str) -> int:
    """Latest values (one call returns the whole series) plus every revision published
    since the last sync (ALFRED vintages), for point-in-time research."""
    with tracked(session, "fred", "observations", series_id) as result:
        last_vintage = session.scalar(
            select(func.max(EconomicVintage.realtime_start)).where(
                EconomicVintage.series_id == series_id
            )
        )
        series = fred_provider.fetch_series(series_id)
        observations = fred_provider.fetch_observations(series_id)
        since = last_vintage + timedelta(days=1) if last_vintage else None
        vintage_dates = fred_provider.fetch_vintage_dates(series_id, since)
        vintage_pages = []
        for i in range(0, len(vintage_dates), fred.MAX_VINTAGES):
            window = vintage_dates[i : i + fred.MAX_VINTAGES]
            vintage_pages += fred_provider.fetch_vintages(series_id, window[0], window[-1])

        load_fred_series(session, series)
        result["rows"] = load_fred_observations(session, series_id, observations)
        for page in vintage_pages:
            load_fred_vintages(session, series_id, page)
    return result["rows"]
