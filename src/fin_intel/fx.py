"""Exchange rates to US dollars from FRED's H.10 daily series, for converting foreign
filers' financials (e.g. a 20-F in DKK or CNY) into the currency their US listing trades in.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.models import EconomicObservation

# currency -> (FRED series, whether it's quoted as US dollars per unit)
SERIES: dict[str, tuple[str, bool]] = {
    "EUR": ("DEXUSEU", True),
    "GBP": ("DEXUSUK", True),
    "AUD": ("DEXUSAL", True),
    "NZD": ("DEXUSNZ", True),
    "CNY": ("DEXCHUS", False),
    "CAD": ("DEXCAUS", False),
    "HKD": ("DEXHKUS", False),
    "BRL": ("DEXBZUS", False),
    "JPY": ("DEXJPUS", False),
    "SGD": ("DEXSIUS", False),
    "MXN": ("DEXMXUS", False),
    "CHF": ("DEXSZUS", False),
    "TWD": ("DEXTAUS", False),
    "KRW": ("DEXKOUS", False),
    "MYR": ("DEXMAUS", False),
    "INR": ("DEXINUS", False),
    "ZAR": ("DEXSFUS", False),
    "SEK": ("DEXSDUS", False),
    "DKK": ("DEXDNUS", False),
    "NOK": ("DEXNOUS", False),
    "THB": ("DEXTHUS", False),
}
PEGGED = {"USD": 1.0, "AED": 1 / 3.6725, "SAR": 1 / 3.75, "BHD": 1 / 0.376}


def usd_rates(session: Session) -> dict[str, float]:
    """US dollars per unit of each currency, at the latest available rate."""
    rates = dict(PEGGED)
    ids = {series_id: (currency, direct) for currency, (series_id, direct) in SERIES.items()}
    latest = (
        select(EconomicObservation.series_id, func.max(EconomicObservation.date).label("d"))
        .where(
            EconomicObservation.series_id.in_(list(ids)),
            EconomicObservation.value.is_not(None),
        )
        .group_by(EconomicObservation.series_id)
        .subquery()
    )
    for series_id, value in session.execute(
        select(EconomicObservation.series_id, EconomicObservation.value).join(
            latest,
            (latest.c.series_id == EconomicObservation.series_id)
            & (latest.c.d == EconomicObservation.date),
        )
    ):
        currency, direct = ids[series_id]
        if value:
            rates[currency] = value if direct else 1 / value
    return rates
