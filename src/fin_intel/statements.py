"""Standard financial statements mapped from raw XBRL facts.

Companies report the same figure under different concepts: revenue alone appears as
RevenueFromContractWithCustomerExcludingAssessedTax (since ASC 606, 2018), SalesRevenueNet
(before), Revenues, or ifrs-full:Revenue for IFRS filers such as ZIM. Each line item lists
candidates in priority order; for every period the first candidate with a value wins, so a
company switching concepts over the years still yields one continuous series. The chosen
concept is kept with each value, so every number traces back to its filing.

Values are the latest filed per period (restatements win). Flow items get one standalone
value per fiscal quarter, derived from year-to-date figures where a company reports only
those (10-Q cash flow statements); derived values are flagged.
"""

import re
from collections import Counter, defaultdict
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
            ("dart", "OperatingIncomeLoss"),  # Korea's standard operating profit line
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

# Japan (EDINET): J-GAAP (jppfs), Japan's IFRS taxonomy (jpigp) and the annual report's
# summary of business results (jpcrp, consolidated figures for five years). Each list is
# appended after the US GAAP and IFRS candidates, in priority order. `fin-intel` concepts
# are assembled from several lines at load time (edinet.py).
JAPAN: dict[str, list[tuple[str, str]]] = {
    "revenue": [
        ("jpigp", "RevenueIFRS"),
        ("jppfs", "NetSales"),
        ("jppfs", "OperatingRevenue1"),
        ("jppfs", "NetSalesOfCompletedConstructionContractsCNS"),
        ("jppfs", "OrdinaryIncomeBNK"),  # a bank's total revenue (not "ordinary profit")
        ("jpcrp", "RevenueIFRSSummaryOfBusinessResults"),
        ("jpcrp", "NetSalesSummaryOfBusinessResults"),
        ("jpcrp", "OperatingRevenue1SummaryOfBusinessResults"),
    ],
    "cost_of_revenue": [("jpigp", "CostOfSalesIFRS"), ("jppfs", "CostOfSales")],
    "gross_profit": [("jpigp", "GrossProfitIFRS"), ("jppfs", "GrossProfit")],
    "operating_income": [("jpigp", "OperatingProfitLossIFRS"), ("jppfs", "OperatingIncome")],
    "interest_expense": [("jpigp", "FinanceCostsIFRS"), ("jppfs", "InterestExpensesNOE")],
    "pretax_income": [("jpigp", "ProfitLossBeforeTaxIFRS"), ("jppfs", "IncomeBeforeIncomeTaxes")],
    "income_tax": [("jpigp", "IncomeTaxExpenseIFRS"), ("jppfs", "IncomeTaxes")],
    "net_income": [
        ("jpigp", "ProfitLossAttributableToOwnersOfParentIFRS"),
        ("jppfs", "ProfitLossAttributableToOwnersOfParent"),
        ("jpcrp", "ProfitLossAttributableToOwnersOfParentSummaryOfBusinessResults"),
        ("jpigp", "ProfitLossIFRS"),
        ("jppfs", "ProfitLoss"),
    ],
    "eps_diluted": [
        ("jpigp", "DilutedEarningsLossPerShareIFRS"),
        ("jpcrp", "DilutedEarningsLossPerShareIFRSSummaryOfBusinessResults"),
        ("jpcrp", "DilutedEarningsPerShareSummaryOfBusinessResults"),
    ],
    "eps_basic": [
        ("jpigp", "BasicEarningsLossPerShareIFRS"),
        ("jpcrp", "BasicEarningsLossPerShareIFRSSummaryOfBusinessResults"),
        ("jpcrp", "BasicEarningsLossPerShareSummaryOfBusinessResults"),
    ],
    "operating_cash_flow": [
        ("jpigp", "NetCashProvidedByUsedInOperatingActivitiesIFRS"),
        ("jppfs", "NetCashProvidedByUsedInOperatingActivities"),
    ],
    "capex": [
        ("jpigp", "PurchaseOfPropertyPlantAndEquipmentInvCFIFRS"),
        ("jppfs", "PurchaseOfPropertyPlantAndEquipmentInvCF"),
    ],
    "depreciation": [
        ("jpigp", "DepreciationAndAmortizationOpeCFIFRS"),
        ("jppfs", "DepreciationAndAmortizationOpeCF"),
    ],
    "dividends_paid": [("jpigp", "DividendsPaidFinCFIFRS"), ("jppfs", "CashDividendsPaidFinCF")],
    "buybacks": [
        ("jpigp", "PurchaseOfTreasurySharesFinCFIFRS"),
        ("jppfs", "PurchaseOfTreasuryStockFinCF"),
    ],
    "cash": [
        ("jpigp", "CashAndCashEquivalentsIFRS"),
        ("jppfs", "CashAndCashEquivalents"),
        ("jppfs", "CashAndDeposits"),
    ],
    "short_term_investments": [("jppfs", "ShortTermInvestmentSecurities")],
    "current_assets": [("jpigp", "CurrentAssetsIFRS"), ("jppfs", "CurrentAssets")],
    "total_assets": [("jpigp", "AssetsIFRS"), ("jppfs", "Assets")],
    "current_liabilities": [
        ("jpigp", "TotalCurrentLiabilitiesIFRS"),
        ("jppfs", "CurrentLiabilities"),
    ],
    "total_liabilities": [("jpigp", "LiabilitiesIFRS"), ("jppfs", "Liabilities")],
    "long_term_debt": [("fin-intel", "InterestBearingDebt")],  # including current portions
    "lease_liabilities": [("jpigp", "LeaseLiabilitiesNCLIFRS")],
    "equity": [
        ("jpigp", "EquityAttributableToOwnersOfParentIFRS"),
        ("jppfs", "ShareholdersEquity"),
        ("jpigp", "EquityIFRS"),
        ("jppfs", "NetAssets"),
    ],
    "retained_earnings": [("jpigp", "RetainedEarningsIFRS"), ("jppfs", "RetainedEarnings")],
    "shares_outstanding": [("fin-intel", "SharesOutstanding")],
}
for _item, _keys in JAPAN.items():
    LINE_ITEMS[_item][2].extend(_keys)

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
    derived: bool = False  # computed from other periods (e.g. Q2 = six months - Q1)
    first_filed: date | None = None  # when the period's figure first became public


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

    # Latest filed value per (concept, unit, period): restatements win. The first filing
    # date is kept too: later filings repeat a period as a comparative (each 10-K re-reports
    # the prior year), and a backtest must date the figure from when it was first public.
    latest: dict[tuple, _Value] = {}
    first: dict[tuple, date | None] = {}
    for r in sorted(rows, key=lambda r: r.filed or date.min):
        ptype = period_type(r.period_start, r.period_end, r.instant)
        key = (r.concept_id, r.unit, r.period_start, r.period_end)
        first.setdefault(key, r.filed)
        latest[key] = _Value(
            r.period_start,
            r.period_end,
            ptype,
            r.fiscal_year,
            r.fiscal_period,
            r.unit,
            r.value,
            r.filed,
            r.concept_id,
            first_filed=first[key],
        )

    # The reporting currency: the one most periods are in. Foreign filers often add a
    # convenience translation (TSMC's latest year in USD too), which mustn't displace it.
    currencies = Counter(
        (v.period_start, v.period_end, v.unit.split("/")[0])
        for v in latest.values()
        if re.fullmatch(r"[A-Z]{3}(/shares)?", v.unit)
    )
    by_currency = Counter(currency for _, _, currency in currencies)
    main = by_currency.most_common(1)[0][0] if by_currency else None

    # Per line item and period, the highest-priority concept with a value (in the
    # reporting currency when the period has several).
    chosen: dict[tuple[str, date, date], tuple[tuple[bool, int], _Value]] = {}
    for v in latest.values():
        item = CANDIDATES[by_concept[v.concept_id]]
        if not _belongs(item, v):
            continue
        base = v.unit.split("/")[0]
        foreign = main is not None and re.fullmatch(r"[A-Z]{3}", base) is not None and base != main
        rank = (foreign, LINE_ITEMS[item][2].index(by_concept[v.concept_id]))
        key = (item, v.period_start, v.period_end)
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, v)

    values = defaultdict(list)
    for (item, _, _), (_, v) in chosen.items():
        values[item].append(v)
    for item, (_, kind, _) in LINE_ITEMS.items():
        if kind == "flow":
            values[item] = _standalone_quarters(values[item])

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
            "first_filed": v.first_filed,
            "concept_id": v.concept_id,
            "derived": v.derived,
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
    if kind == "flow":  # year-to-date periods only feed the standalone-quarter derivation
        return v.period_type in ("annual", "quarter", "half", "nine_months")
    return v.period_type in ("annual", "quarter")


def _standalone_quarters(values: list[_Value]) -> list[_Value]:
    """Annual values plus one standalone value per fiscal quarter.

    10-Q cash flow statements (and some income statements) are year-to-date: three, six
    and nine months. Quarters not reported on their own are derived from cumulative values
    starting at the fiscal year start: Q2 = six months - Q1, Q3 = nine months - six months,
    Q4 = full year - nine months (or minus the sum of the earlier quarters). Year-to-date
    values themselves aren't kept.
    """
    out = [v for v in values if v.period_type == "annual"]
    out += [v for v in values if v.period_type == "quarter" and v.fiscal_year is None]
    by_year: dict[tuple, dict[str, _Value]] = defaultdict(dict)
    for v in values:
        if v.fiscal_year is not None and v.fiscal_period:
            by_year[(v.unit, v.fiscal_year)][v.fiscal_period] = v
    for periods in by_year.values():
        reported = {n: periods.get(f"Q{n}") for n in (1, 2, 3, 4)}
        reported = {
            n: v for n, v in reported.items() if v is not None and v.period_type == "quarter"
        }
        starts = [v.period_start for k in ("FY", "9M", "H1") if (v := periods.get(k))]
        if 1 in reported:
            starts.append(reported[1].period_start)
        if not starts:
            out += reported.values()
            continue
        year_start = min(starts)
        cumulative: dict[int, _Value] = {}
        for n, key in ((2, "H1"), (3, "9M"), (4, "FY")):
            v = periods.get(key)
            if v is not None and v.period_start == year_start:
                cumulative[n] = v
        if 1 in reported and reported[1].period_start == year_start:
            cumulative[1] = reported[1]

        quarters: dict[int, _Value] = {}
        for n in (1, 2, 3, 4):
            if n in reported:
                quarters[n] = reported[n]
                continue
            ytd = cumulative.get(n)
            if ytd is None or n == 1:
                continue
            if n - 1 in cumulative:
                before, prev_end = cumulative[n - 1].value, cumulative[n - 1].period_end
                parts = [cumulative[n - 1]]
            elif all(k in quarters for k in range(1, n)):
                before = sum(quarters[k].value for k in range(1, n))
                prev_end = quarters[n - 1].period_end
                parts = [quarters[k] for k in range(1, n)]
            else:
                continue
            start = prev_end + timedelta(days=1)
            if period_type(start, ytd.period_end, instant=False) != "quarter":
                continue
            quarters[n] = replace(
                ytd,
                period_start=start,
                period_type="quarter",
                fiscal_period=f"Q{n}",
                value=ytd.value - before,
                derived=True,
                first_filed=max(
                    (p.first_filed for p in (ytd, *parts) if p.first_filed), default=None
                ),
            )
        out += quarters.values()
    return out
