"""Big players and policy: insiders, 13F holdings, congressional trades, presidential
documents."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from fin_intel import (
    congress,
    insiders,
    presidential,
    thirteenf,
)
from fin_intel.api.app import Orm, SessionDep, issuer_cik_or_404, security_or_404
from fin_intel.models import (
    CusipMapping,
    InsiderTransaction,
    InstitutionalFiler,
    InstitutionalPosition,
)

router = APIRouter()


class InsiderOut(Orm):
    filing_date: date | None
    trans_date: date
    owner_name: str | None
    relationship: str | None
    owner_title: str | None
    trans_code: str | None
    acquired_disposed: str | None
    shares: float | None
    price: float | None
    shares_after: float | None
    direct_indirect: str | None
    plan_10b5_1: bool
    accession: str


@router.get("/insiders/clusters")
def insider_clusters(
    session: SessionDep,
    days: int = Query(30, le=365),
    min_insiders: int = Query(3, ge=2),
) -> list[insiders.ClusterBuy]:
    """Companies where several insiders bought on the open market (not under 10b5-1 plans)
    within the window."""
    return insiders.cluster_buys(session, days, min_insiders)


@router.get("/insiders/{ticker}", response_model=list[InsiderOut])
def insider_transactions(
    session: SessionDep,
    ticker: str,
    code: str | None = Query(None, description="e.g. P (purchases), S (sales)"),
    limit: int = Query(100, le=1000),
) -> list[InsiderTransaction]:
    """A company's insider transactions, newest first."""
    cik = issuer_cik_or_404(session, ticker)
    stmt = (
        select(InsiderTransaction)
        .where(InsiderTransaction.issuer_cik == cik)
        .order_by(InsiderTransaction.trans_date.desc())
        .limit(limit)
    )
    if code:
        stmt = stmt.where(InsiderTransaction.trans_code == code.upper())
    return list(session.scalars(stmt))


@router.get("/holdings/managers")
def holdings_managers(session: SessionDep, q: str, limit: int = Query(20, le=100)) -> list[dict]:
    """Find 13F filers by name, e.g. q=berkshire, q=pershing, q=scion."""
    return [{"cik": f.cik, "name": f.name} for f in thirteenf.find_filers(session, q, limit)]


@router.get("/holdings/managers/{cik}")
def holdings_manager(session: SessionDep, cik: int, period: date | None = None) -> dict:
    """A manager's positions at a quarter end, with the change since its previous report
    (new, added, reduced, sold, unchanged), largest first."""
    current, changes = thirteenf.manager_changes(session, cik, period)
    if current is None:
        raise HTTPException(404, f"no 13F holdings for CIK {cik}")
    return {"cik": cik, "period": current, "positions": changes}


@router.get("/holdings/security/{ticker}")
def holdings_security(session: SessionDep, ticker: str, limit: int = Query(25, le=200)) -> dict:
    """Institutions holding a security at the latest quarter end, largest first."""
    security = security_or_404(session, ticker)
    cusips = list(
        session.scalars(select(CusipMapping.cusip).where(CusipMapping.security_id == security.id))
    )
    if not cusips:
        raise HTTPException(404, f"no 13F holdings mapped to {ticker}")
    period = session.scalar(
        select(func.max(InstitutionalPosition.period)).where(
            InstitutionalPosition.cusip.in_(cusips)
        )
    )
    rows = session.execute(
        select(InstitutionalPosition, InstitutionalFiler.name)
        .join(InstitutionalFiler, InstitutionalFiler.cik == InstitutionalPosition.filer_cik)
        .where(
            InstitutionalPosition.cusip.in_(cusips),
            InstitutionalPosition.period == period,
            InstitutionalPosition.put_call == "",
        )
        .order_by(InstitutionalPosition.value.desc())
        .limit(limit)
    ).all()
    return {
        "ticker": ticker.upper(),
        "period": period,
        "holders": [
            {
                "cik": p.filer_cik,
                "name": name,
                "shares": p.shares,
                "value": p.value,
                "filed": p.filed,
            }
            for p, name in rows
        ],
    }


@router.get("/congress/trades")
def congress_trades(
    session: SessionDep,
    member: str | None = Query(None, description="Part of a name, e.g. pelosi"),
    ticker: str | None = None,
    since: Annotated[date | None, Query(description="Transaction date on or after")] = None,
    type: str | None = Query(None, description="purchase, sale or exchange"),
    party: str | None = Query(None, description="Democrat, Republican, Independent"),
    committee: str | None = Query(
        None, description="Current committee, e.g. SSAS (Senate Armed Services), HSIF"
    ),
    limit: int = Query(200, le=2000),
) -> list[congress.Trade]:
    """Members of Congress's reported trades (PTRs), newest transaction first. Amounts are
    the reported range; `filed` minus `trans_date` is the disclosure lag (up to 45 days).
    Committee filters use today's rosters (a look-ahead for past trades)."""
    return congress.trades(
        session,
        member=member,
        ticker=ticker,
        since=since,
        trans_type=type,
        party=party,
        committee=committee,
        limit=limit,
    )


@router.get("/congress/popular")
def congress_popular(
    session: SessionDep, days: int = Query(90, le=730), limit: int = Query(50, le=500)
) -> list[congress.Popular]:
    """Tickers traded by the most distinct members within the window."""
    return congress.most_traded(session, days)[:limit]


@router.get("/presidential/search")
def presidential_search(
    session: SessionDep,
    q: str,
    kind: str | None = Query(None, description="executive_order, proclamation, memorandum..."),
    since: date | None = None,
    limit: int = Query(20, le=200),
) -> list[presidential.Hit]:
    """Full-text search over executive orders, proclamations (tariffs), memoranda,
    determinations and notices since 1994 (web-search syntax)."""
    return presidential.search(session, q, kind, since, limit)
