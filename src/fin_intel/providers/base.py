import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import httpx

from fin_intel.config import get_settings
from fin_intel.providers.errors import (
    NotConfiguredError,
    NotFoundError,
    ProviderError,
    QuotaExceededError,
)
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
        """GET a JSON resource (recorded in the raw store)."""
        resp = self._request(url, params, dataset=dataset, key=key)
        try:
            return resp.json()
        except ValueError:
            self.check_text_error(resp.text)
            raise ProviderError(f"{self.name}: non-JSON response: {resp.text[:200]}") from None

    def get_bytes(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        dataset: str,
        key: str | None = None,
    ) -> bytes:
        """GET a non-JSON resource (zip, HTML, text), recorded in the raw store."""
        return self._request(url, params, dataset=dataset, key=key).content

    def post_json(self, url: str, body: Any, *, dataset: str, key: str | None = None) -> Any:
        """POST a JSON body and return the JSON response. The body is recorded with the
        raw response (as its params), since the request defines what the answer means."""
        resp = self._request(url, None, dataset=dataset, key=key, json_body=body)
        return resp.json()

    def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        dataset: str,
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """POST a form and return the JSON response (the form is recorded like a body)."""
        resp = self._request(
            url, None, dataset=dataset, key=key, form_body=form, extra_headers=headers
        )
        return resp.json()

    def _request(
        self,
        url: str,
        params: dict[str, Any] | None,
        *,
        dataset: str,
        key: str | None,
        json_body: Any = None,
        form_body: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not url.startswith("http"):
            url = self.base_url + url
        public_params = params or {}
        # Merge into any query already in the URL (pagination links carry a cursor):
        # httpx's `params=` would replace it instead.
        request_url = httpx.URL(url).copy_merge_params({**public_params, **self.auth_params()})
        headers = self.headers() | (extra_headers or {})
        body = json_body if form_body is None else form_body
        for attempt in range(self.max_retries + 1):
            self.limiter.acquire()
            try:
                if form_body is not None:
                    resp = self.client.post(request_url, headers=headers, data=form_body)
                elif json_body is not None:
                    resp = self.client.post(request_url, headers=headers, json=json_body)
                else:
                    resp = self.client.get(request_url, headers=headers)
            except httpx.TransportError as exc:
                if attempt == self.max_retries:
                    raise ProviderError(f"{self.name}: {exc}") from exc
            else:
                if self.raw_store:
                    recorded = public_params if body is None else {"body": body}
                    self.raw_store.save(
                        self.name, dataset, key, recorded, resp.status_code, resp.content
                    )
                if resp.status_code not in RETRY_STATUSES or attempt == self.max_retries:
                    break
            delay = 2**attempt
            log.warning("%s: retrying %s in %ss", self.name, url, delay)
            time.sleep(delay)
        if resp.status_code == 429:
            raise QuotaExceededError(f"{self.name}: rate limited after {self.max_retries} retries")
        if resp.status_code == 404:
            raise NotFoundError(f"{self.name}: not found: {url}")
        if resp.is_error:
            raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def check_text_error(self, text: str) -> None:
        """Some providers report errors as plain text with HTTP 200; raise a typed error."""
