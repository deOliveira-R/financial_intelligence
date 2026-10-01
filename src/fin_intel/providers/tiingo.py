from datetime import UTC, date, datetime, timedelta
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider, QuotaExceededError
from fin_intel.providers.ratelimit import DAY, HOUR, Limit

MONTHLY_SYMBOLS = 500
SYMBOL_DATASETS = ["metadata", "daily_prices"]


class TiingoProvider(Provider):
    """Tiingo: unadjusted end-of-day prices with splits and dividends, 30+ years of history."""

    name = "tiingo"
    base_url = "https://api.tiingo.com/tiingo"
    limits = (Limit(50, HOUR), Limit(1000, DAY))  # free tier

    def headers(self) -> dict[str, str]:
        key = get_settings().tiingo_api_key
        if not key:
            raise NotConfiguredError("set FI_TIINGO_API_KEY")
        return {"Authorization": f"Token {key}"}

    def check_text_error(self, text: str) -> None:
        # Tiingo answers HTTP 200 with plain text when a limit is hit, e.g. "You have run
        # over your 500 symbol look up for this month. Please upgrade at ..."
        if "run over" in text or "allocation" in text:
            raise QuotaExceededError(f"tiingo: {text.strip()[:200]}")

    def check_symbol_quota(self, ticker: str) -> None:
        """Fail before spending a call when a new symbol would exceed the monthly cap."""
        if self.raw_store is None:
            return
        since = datetime.now(UTC) - timedelta(days=30)
        used = self.raw_store.distinct_keys(self.name, SYMBOL_DATASETS, since)
        if ticker not in used and len(used) >= MONTHLY_SYMBOLS:
            raise QuotaExceededError(
                f"tiingo: {len(used)} distinct symbols in the last 30 days (cap {MONTHLY_SYMBOLS})"
            )

    def fetch_metadata(self, ticker: str) -> Any:
        self.check_symbol_quota(ticker)
        return self.get(f"/daily/{ticker}", dataset="metadata", key=ticker)

    def fetch_daily(self, ticker: str, start: date | None = None) -> Any:
        self.check_symbol_quota(ticker)
        params = {"startDate": (start or date(1900, 1, 1)).isoformat()}
        return self.get(f"/daily/{ticker}/prices", params, dataset="daily_prices", key=ticker)


def parse_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": payload["ticker"].upper(),
        "name": payload.get("name"),
        "exchange": payload.get("exchangeCode"),
    }


def parse_daily(payload: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Unadjusted bars, plus the splits and dividends Tiingo reports on their ex-dates."""
    bars, actions = [], []
    for row in payload:
        day = date.fromisoformat(row["date"][:10])
        bars.append(
            {
                "date": day,
                "source": TiingoProvider.name,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
        )
        if split := row.get("splitFactor", 1.0):
            if split != 1.0:
                # Tiingo stores ratios like 7.000007 for a 7-for-1 split.
                actions.append(_action(day, "split", round(split, 4)))
        if dividend := row.get("divCash", 0.0):
            actions.append(_action(day, "dividend", dividend))
    return bars, actions


def _action(day: date, action: str, value: float) -> dict[str, Any]:
    return {"ex_date": day, "action": action, "source": TiingoProvider.name, "value": value}
