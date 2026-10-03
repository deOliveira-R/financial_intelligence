"""Standard financial statements mapped from raw XBRL facts.

Companies report the same figure under different concepts: revenue alone appears as
RevenueFromContractWithCustomerExcludingAssessedTax (since ASC 606, 2018), SalesRevenueNet
(before), Revenues, or ifrs-full:Revenue for IFRS filers such as ZIM. Each line item lists
candidates in priority order; for every period the first candidate with a value wins, so a
company switching concepts over the years still yields one continuous series. The chosen
concept is kept with each value, so every number traces back to its filing.

Values are the latest filed per period (restatements win), with a derived Q4 (FY minus
nine months) for flow items when a company only reports the full year.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import Concept, Fact, Filing, StatementItem
from fin_intel.periods import period_type

# line item -> (statement, kind, [(taxonomy, concept), ...] in priority order)
# kind: "flow" (summed over a period), "stock" (balance at a date), "per_share", "shares"
LINE_ITEMS: dict[str, tuple[str, str, list[tuple[str, str]]]] = {
    # Income statement
    "revenue": (
        "income",
        "flow",
        [
            # Total revenue first: contract revenue excludes e.g. Walmart's membership income.
            ("us-gaap", "Revenues"),
            ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
            ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
            ("us-gaap", "SalesRevenueNet"),
            ("us-gaap", "SalesRevenueGoodsNet"),
            ("us-gaap", "RevenuesNetOfInterestExpense"),
            ("ifrs-full", "Revenue"),
            ("ifrs-full", "RevenueFromContractsWithCustomers"),
        ],
    ),
    "cost_of_revenue": (
        "income",
        "flow",
        [
            ("us-gaap", "CostOfRevenue"),
            ("us-gaap", "CostOfGoodsAndServicesSold"),
            ("us-gaap", "CostOfGoodsSold"),
            ("ifrs-full", "CostOfSales"),
        ],
    ),
    "gross_profit": ("income", "flow", [("us-gaap", "GrossProfit"), ("ifrs-full", "GrossProfit")]),
    "operating_income": (
        "income",
        "flow",
        [
            ("us-gaap", "OperatingIncomeLoss"),
            ("ifrs-full", "ProfitLossFromOperatingActivities"),
        ],
    ),
    "interest_expense": (
        "income",
        "flow",
        [
            ("us-gaap", "InterestExpense"),
            ("us-gaap", "InterestExpenseNonoperating"),
            ("ifrs-full", "FinanceCosts"),
            ("ifrs-full", "InterestExpense"),
        ],
    ),
    "pretax_income": (
        "income",
        "flow",
        [
            (
                "us-gaap",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            ),
            (
                "us-gaap",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            ),
            ("ifrs-full", "ProfitLossBeforeTax"),
        ],
    ),
    "income_tax": (
        "income",
        "flow",
        [
            ("us-gaap", "IncomeTaxExpenseBenefit"),
            ("ifrs-full", "IncomeTaxExpenseContinuingOperations"),
        ],
    ),
    "net_income": (
        "income",
        "flow",
        [
            ("us-gaap", "NetIncomeLoss"),  # attributable to the parent
            ("us-gaap", "ProfitLoss"),
            ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
            ("ifrs-full", "ProfitLoss"),
        ],
    ),
    "eps_diluted": (
        "income",
        "per_share",
        [
            ("us-gaap", "EarningsPerShareDiluted"),
            ("ifrs-full", "DilutedEarningsLossPerShare"),
        ],
    ),
    "eps_basic": (
        "income",
        "per_share",
        [
            ("us-gaap", "EarningsPerShareBasic"),
            ("ifrs-full", "BasicEarningsLossPerShare"),
        ],
    ),
    "shares_diluted": (
        "income",
        "shares",
        [
            ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
            ("ifrs-full", "WeightedAverageShares"),
        ],
    ),
    # Cash flow statement
    "operating_cash_flow": (
        "cash_flow",
        "flow",
        [
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
            ("us-gaap", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
            ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
        ],
    ),
    "capex": (
        "cash_flow",
        "flow",
        [
            ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
            ("us-gaap", "PaymentsToAcquireProductiveAssets"),
            ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
            ("ifrs-full", "PurchaseOfPropertyPlantAndEquipment"),
        ],
    ),
    "depreciation": (
        "cash_flow",
        "flow",
        [
            ("us-gaap", "DepreciationDepletionAndAmortization"),
            ("us-gaap", "DepreciationAndAmortization"),
            ("us-gaap", "DepreciationAmortizationAndAccretionNet"),
            ("ifrs-full", "DepreciationAndAmortisationExpense"),
            ("ifrs-full", "AdjustmentsForDepreciationAndAmortisationExpense"),
        ],
    ),
    "dividends_paid": (
        "cash_flow",
        "flow",
        [
            ("us-gaap", "PaymentsOfDividends"),
            ("us-gaap", "PaymentsOfDividendsCommonStock"),
            ("ifrs-full", "DividendsPaidClassifiedAsFinancingActivities"),
            ("ifrs-full", "DividendsPaid"),
        ],
    ),
    "buybacks": (
        "cash_flow",
        "flow",
        [
            ("us-gaap", "PaymentsForRepurchaseOfCommonStock"),
            ("ifrs-full", "PaymentsToAcquireOrRedeemEntitysShares"),
        ],
    ),
    # Balance sheet
    "cash": (
        "balance",
        "stock",
        [
            ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
            ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
            ("us-gaap", "Cash"),
            ("ifrs-full", "CashAndCashEquivalents"),
        ],
    ),
    "short_term_investments": (
        "balance",
        "stock",
        [
            ("us-gaap", "ShortTermInvestments"),
            ("us-gaap", "MarketableSecuritiesCurrent"),
            ("us-gaap", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"),
            ("ifrs-full", "ShorttermDepositsNotClassifiedAsCashEquivalents"),
        ],
    ),
    "current_assets": (
        "balance",
        "stock",
        [("us-gaap", "AssetsCurrent"), ("ifrs-full", "CurrentAssets")],
    ),
    "total_assets": ("balance", "stock", [("us-gaap", "Assets"), ("ifrs-full", "Assets")]),
    "current_liabilities": (
        "balance",
        "stock",
        [
            ("us-gaap", "LiabilitiesCurrent"),
            ("ifrs-full", "CurrentLiabilities"),
        ],
    ),
    "total_liabilities": (
        "balance",
        "stock",
        [("us-gaap", "Liabilities"), ("ifrs-full", "Liabilities")],
    ),
    "long_term_debt": (
        "balance",
        "stock",
        [  # including its current portion where reported
            ("us-gaap", "LongTermDebt"),
            ("us-gaap", "DebtLongtermAndShorttermCombinedAmount"),
            ("us-gaap", "LongTermDebtNoncurrent"),
            ("ifrs-full", "Borrowings"),
            ("ifrs-full", "NoncurrentPortionOfNoncurrentBorrowings"),
        ],
    ),
    "short_term_debt": (
        "balance",
        "stock",
        [
            ("us-gaap", "ShortTermBorrowings"),
            ("us-gaap", "CommercialPaper"),
            ("ifrs-full", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"),
        ],
    ),
    "lease_liabilities": (
        "balance",
        "stock",
        [
            ("us-gaap", "OperatingLeaseLiability"),
            ("ifrs-full", "LeaseLiabilities"),
        ],
    ),
    "equity": (
        "balance",
        "stock",
        [
            ("us-gaap", "StockholdersEquity"),
            ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
            ("ifrs-full", "EquityAttributableToOwnersOfParent"),
            ("ifrs-full", "Equity"),
        ],
    ),
    "retained_earnings": (
        "balance",
        "stock",
        [
            ("us-gaap", "RetainedEarningsAccumulatedDeficit"),
            ("ifrs-full", "RetainedEarnings"),
        ],
    ),
    "shares_outstanding": (
        "balance",
        "shares",
        [
            ("dei", "EntityCommonStockSharesOutstanding"),  # cover page, near the filing date
            ("us-gaap", "CommonStockSharesOutstanding"),
        ],
    ),
}

CANDIDATES = {key: item for item, (_, _, keys) in LINE_ITEMS.items() for key in keys}


@dataclass(frozen=True)
class _Value:
    period_start: date
    period_end: date
    period_type: str
    fiscal_year: int | None
    fiscal_period: str | None
    unit: str
    value: float
    filed: date | None
    concept_id: int


def build_issuer(session: Session, cik: int) -> int:
    """Recompute an issuer's statement items from its facts; returns rows written."""
    wanted = {
        (t, n): i
        for t, n, i in session.execute(select(Concept.taxonomy, Concept.name, Concept.id))
        if (t, n) in CANDIDATES
    }
    by_concept: dict[int, tuple[str, str]] = {i: key for key, i in wanted.items()}
    rows = session.execute(
        select(
            Fact.concept_id,
            Fact.unit,
            Fact.period_start,
            Fact.period_end,
            Fact.instant,
            Fact.value,
            Fact.fiscal_year,
            Fact.fiscal_period,
            Filing.filed,
        )
        .join(Filing, Filing.id == Fact.filing_id)
        .where(Fact.cik == cik, Fact.concept_id.in_(list(by_concept)))
    ).all()

    # Latest filed value per (concept, unit, period): restatements win.
    latest: dict[tuple, _Value] = {}
    for r in sorted(rows, key=lambda r: r.filed or date.min):
        ptype = period_type(r.period_start, r.period_end, r.instant)
        latest[(r.concept_id, r.unit, r.period_start, r.period_end)] = _Value(
            r.period_start,
            r.period_end,
            ptype,
            r.fiscal_year,
            r.fiscal_period,
            r.unit,
            r.value,
            r.filed,
            r.concept_id,
        )

    # Per line item and period, the highest-priority concept with a value.
    chosen: dict[tuple[str, date, date], tuple[int, _Value]] = {}
    for v in latest.values():
        item = CANDIDATES[by_concept[v.concept_id]]
        if not _belongs(item, v):
            continue
        rank = LINE_ITEMS[item][2].index(by_concept[v.concept_id])
        key = (item, v.period_start, v.period_end)
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, v)

    values = defaultdict(list)
    for (item, _, _), (_, v) in chosen.items():
        values[item].append(v)
    for item, (_, kind, _) in LINE_ITEMS.items():
        if kind == "flow":
            values[item] += _derived_q4(values[item])

    records = [
        {
            "cik": cik,
            "line_item": item,
            "period_start": v.period_start,
            "period_end": v.period_end,
            "period_type": v.period_type,
            "fiscal_year": v.fiscal_year,
            "fiscal_period": v.fiscal_period,
            "unit": v.unit,
            "value": v.value,
            "filed": v.filed,
            "concept_id": v.concept_id,
        }
        for item, vs in values.items()
        for v in vs
    ]
    session.execute(delete(StatementItem).where(StatementItem.cik == cik))
    upsert(session, StatementItem, records, key=["cik", "line_item", "period_start", "period_end"])
    return len(records)


def _belongs(item: str, v: _Value) -> bool:
    """Flows are annual or quarterly durations; balances are instants; per-share and share
    counts follow their statement (EPS and weighted shares are durations)."""
    statement, kind, _ = LINE_ITEMS[item]
    if kind == "stock" or (kind == "shares" and statement == "balance"):
        return v.period_type == "instant"
    return v.period_type in ("annual", "quarter")


def _derived_q4(values: list[_Value]) -> list[_Value]:
    """FY minus the first three quarters (or the nine-month YTD) when Q4 isn't reported."""
    by_year: dict[tuple, dict[str, _Value]] = defaultdict(dict)
    for v in values:
        if v.fiscal_year is not None and v.fiscal_period:
            by_year[(v.unit, v.fiscal_year)][v.fiscal_period] = v
    out = []
    for periods in by_year.values():
        fy, q1, q2, q3 = (periods.get(p) for p in ("FY", "Q1", "Q2", "Q3"))
        if fy is None or q1 is None or q2 is None or q3 is None or "Q4" in periods:
            continue
        quarters = [q1, q2, q3]
        if q1.period_start != fy.period_start:
            continue
        start = q3.period_end + timedelta(days=1)
        if period_type(start, fy.period_end, instant=False) != "quarter":
            continue
        out.append(
            replace(
                fy,
                period_start=start,
                period_type="quarter",
                fiscal_period="Q4",
                value=fy.value - sum(q.value for q in quarters),
            )
        )
    return out
