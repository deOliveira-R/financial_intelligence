import re
from datetime import date
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider
from fin_intel.providers.ratelimit import MINUTE, Limit

PAGE_SIZE = 5000  # the maximum the corporate action endpoints allow
TICKERS_PAGE_SIZE = 1000  # the maximum the tickers endpoint allows


class MassiveProvider(Provider):
    """Massive (formerly Polygon.io): whole-market daily bars, splits and dividends.

    Free (Basic) plan: 5 calls/minute, end-of-day data, two years of history.
    """

    name = "massive"
    base_url = "https://api.massive.com"
    limits = (Limit(5, MINUTE),)

    def headers(self) -> dict[str, str]:
        key = get_settings().massive_api_key
        if not key:
            raise NotConfiguredError("set FI_MASSIVE_API_KEY")
        # A header (not ?apiKey=) so next_url pagination links work as given.
        return {"Authorization": f"Bearer {key}"}

    def fetch_grouped_daily(self, day: date, include_otc: bool = False) -> Any:
        """Unadjusted OHLCV for every US stock on one trading day (empty on holidays)."""
        params = {"adjusted": "false"}
        if include_otc:
            params["include_otc"] = "true"
        return self.get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
            params,
            dataset="grouped_daily",
            key=day.isoformat(),
        )

    def fetch_tickers(self, market: str = "stocks") -> list[Any]:
        """Every active ticker in a market ("stocks" or "otc"), with type and identifiers."""
        return self._pages(
            "/v3/reference/tickers",
            {"market": market, "active": "true"},
            dataset="tickers",
            key=market,
            page_size=TICKERS_PAGE_SIZE,
        )

    def fetch_splits(self, since: date) -> list[Any]:
        return self._pages(
            "/stocks/v1/splits", {"execution_date.gte": since.isoformat()}, dataset="splits"
        )

    def fetch_dividends(self, since: date) -> list[Any]:
        return self._pages(
            "/stocks/v1/dividends", {"ex_dividend_date.gte": since.isoformat()}, dataset="dividends"
        )

    def _pages(
        self,
        path: str,
        params: dict[str, Any],
        dataset: str,
        key: str = "all",
        page_size: int = PAGE_SIZE,
    ) -> list[Any]:
        """Every page of a reference endpoint; each page is its own raw response."""
        pages = [self.get(path, {**params, "limit": page_size}, dataset=dataset, key=key)]
        while next_url := pages[-1].get("next_url"):
            pages.append(self.get(next_url, dataset=dataset, key=key))
        return pages


# Massive's symbol suffixes and SEC's equivalents (SEC_style = base + suffix).
_SUFFIXES = [
    (re.compile(r"^([A-Z]+)p([A-Z]?)$"), r"\1-P\2"),  # preferred: JPMpC -> JPM-PC
    (re.compile(r"^([A-Z]+)\.U$"), r"\1-UN"),  # unit: AAC.U -> AAC-UN
    (re.compile(r"^([A-Z]+)\.WS$"), r"\1-WT"),  # warrant: BBAI.WS -> BBAI-WT
    (re.compile(r"^([A-Z]+)rw$"), r"\1-RW"),  # right, when issued: BCATrw -> BCAT-RW
    (re.compile(r"^([A-Z]+)r$"), r"\1-RI"),  # right: AIIAr -> AIIA-RI
]


def normalize_symbol(symbol: str) -> str:
    """Massive's symbols in SEC's style; share classes use a dash (BRK.B -> BRK-B)."""
    for pattern, replacement in _SUFFIXES:
        if pattern.match(symbol):
            return pattern.sub(replacement, symbol)
    return symbol.replace(".", "-")


def parse_grouped_daily(day: date, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": normalize_symbol(r["T"]),
            "date": day,
            "source": MassiveProvider.name,
            "open": r.get("o"),
            "high": r.get("h"),
            "low": r.get("l"),
            "close": r.get("c"),
            "volume": int(r["v"]) if r.get("v") is not None else None,
        }
        for r in payload.get("results") or []
    ]


def parse_splits(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Splits, reverse splits and stock dividends, as new-shares-per-old-share ratios."""
    return [
        {
            "symbol": normalize_symbol(r["ticker"]),
            "ex_date": date.fromisoformat(r["execution_date"]),
            "action": "split",
            "source": MassiveProvider.name,
            "value": round(r["split_to"] / r["split_from"], 6),
        }
        for r in payload.get("results") or []
        if r.get("split_from") and r.get("split_to")
    ]


def parse_dividends(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Cash dividends as originally declared (not split-adjusted), on their ex-dates."""
    return [
        {
            "symbol": normalize_symbol(r["ticker"]),
            "ex_date": date.fromisoformat(r["ex_dividend_date"]),
            "action": "dividend",
            "source": MassiveProvider.name,
            "value": r["cash_amount"],
        }
        for r in payload.get("results") or []
        if r.get("cash_amount") and r.get("ex_dividend_date")
    ]


def parse_tickers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reference tickers in SEC-style symbols, with type and identifiers."""
    return [
        {
            "symbol": normalize_symbol(r["ticker"]),
            "name": r.get("name"),
            "security_type": r.get("type"),
            "cik": int(r["cik"]) if r.get("cik") else None,
            "figi": r.get("composite_figi"),
            "share_class_figi": r.get("share_class_figi"),
            "mic": r.get("primary_exchange"),
        }
        for r in payload.get("results") or []
        if r.get("ticker")
    ]
