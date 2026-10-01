import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx

from fin_intel.config import get_settings
from fin_intel.providers.errors import NotConfiguredError, ProviderError, QuotaExceededError
from fin_intel.providers.ratelimit import Limit, RateLimiter
from fin_intel.raw import RawStore

log = logging.getLogger(__name__)

__all__ = ["NotConfiguredError", "Provider", "ProviderError", "QuotaExceededError"]

_limiters: dict[str, RateLimiter] = {}

RETRY_STATUSES = (429, 500, 502, 503, 504)


class Provider:
    """Base for HTTP data providers: rate limiting, retries, raw capture and JSON decoding.

    With a RawStore, every response (errors included) is recorded before it is interpreted,
    and the rate limiter is seeded from recorded calls so limits hold across processes.
    """

    name: ClassVar[str]
    base_url: ClassVar[str]
    limits: ClassVar[tuple[Limit, ...]]
    max_retries: ClassVar[int] = 3

    def __init__(self, client: httpx.Client | None = None, raw_store: RawStore | None = None):
        self.client = client or httpx.Client(timeout=get_settings().http_timeout)
        self.raw_store = raw_store
        if self.name not in _limiters:
            limiter = RateLimiter(self.limits)
            if raw_store and limiter.longest_period:
                now = datetime.now(UTC)
                since = now - timedelta(seconds=limiter.longest_period)
                limiter.record_past(
                    (now - t).total_seconds() for t in raw_store.call_times(self.name, since)
                )
            _limiters[self.name] = limiter
        self.limiter = _limiters[self.name]

    def headers(self) -> dict[str, str]:
        return {}

    def auth_params(self) -> dict[str, str]:
        return {}

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        dataset: str,
        key: str | None = None,
    ) -> Any:
        if not url.startswith("http"):
            url = self.base_url + url
        public_params = params or {}
        all_params = {**public_params, **self.auth_params()}
        headers = self.headers()
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            try:
                resp = self.client.get(url, params=all_params, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise ProviderError(f"{self.name}: {exc}") from exc
            else:
                if self.raw_store:
                    self.raw_store.save(
                        self.name, dataset, key, public_params, resp.status_code, resp.content
                    )
                if resp.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                    break
            delay = 2**attempt
            log.warning("%s: retrying %s in %ss", self.name, url, delay)
            time.sleep(delay)
        if resp.status_code == 429:
            raise QuotaExceededError(f"{self.name}: rate limited after {self.max_retries} retries")
        if resp.status_code == 404:
            raise ProviderError(f"{self.name}: not found: {url}")
        if resp.is_error:
            raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:
            self.check_text_error(resp.text)
            raise ProviderError(f"{self.name}: non-JSON response: {resp.text[:200]}") from None

    def check_text_error(self, text: str) -> None:
        """Some providers report errors as plain text with HTTP 200; raise a typed error."""
