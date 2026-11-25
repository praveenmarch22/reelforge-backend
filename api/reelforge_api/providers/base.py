"""Errors, the HTTP helper, retries and fallbacks shared by every provider (from the prototype)."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import requests

log = logging.getLogger(__name__)
T = TypeVar("T")


class ProviderError(Exception):
    """A provider call failed.

    retryable: worth trying the same provider again (timeouts, 429, 5xx, unusable output).
    refused:   the provider rejected the content (safety filter); never retried or sent elsewhere.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        refused: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.refused = refused
        self.retry_after = retry_after


class NotConfigured(ProviderError):
    """The provider has no key, or the admin switched it off; skip to the next one."""


class AllProvidersFailed(ProviderError):
    """Every provider in the list failed or was skipped."""

    def __init__(self, message: str, *, none_available: bool):
        super().__init__(message)
        self.none_available = none_available


def json_of(resp: requests.Response) -> dict:
    """The JSON body of a provider's answer; an HTML error page or broken body is retryable."""
    try:
        data = resp.json()
    except ValueError:
        raise ProviderError("the provider's answer was not JSON", retryable=True) from None
    if not isinstance(data, dict):
        raise ProviderError("the provider's answer was not a JSON object", retryable=True)
    return data


def http(method: str, url: str, *, timeout: float = 60, **kwargs) -> requests.Response:
    """A requests call that turns failures into ProviderError with the right retry flags.

    Error messages keep the provider's status and a short part of its answer, never the
    request (which carries the API key in a header, and the creator's text in the body).
    """
    try:
        resp = requests.request(method, url, timeout=timeout, **kwargs)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise ProviderError(f"network error: {type(e).__name__}", retryable=True) from None

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "")
        raise ProviderError(
            "rate limited (429)",
            retryable=True,
            retry_after=min(float(retry_after), 60.0) if retry_after.isdigit() else None,
        )
    if resp.status_code >= 500:
        raise ProviderError(f"server error {resp.status_code}", retryable=True)
    if resp.status_code >= 400:
        text = resp.text[:300]
        # Only a 400 that names the content filter is a refusal; a 401 or 403 (a bad key, a
        # billing "policy") is a provider problem, so the next provider is tried.
        refused = resp.status_code == 400 and any(w in text.lower() for w in ("safety", "moderation", "nsfw"))
        raise ProviderError(f"request rejected {resp.status_code}: {text}", refused=refused)
    return resp


def with_retry(
    fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 2.0, label: str = "", sleep=time.sleep
) -> T:
    """Up to `attempts` tries for retryable errors, waiting longer each time (G-1, G-2)."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError as e:
            if not e.retryable or attempt == attempts:
                raise
            delay = e.retry_after or base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)  # noqa: S311
            log.warning("provider call failed, retrying", extra={"call": label, "attempt": attempt})
            sleep(delay)
    raise AssertionError("unreachable")


def with_fallback(
    providers: list, call: Callable[[object], T], *, label: str, sleep=time.sleep
) -> tuple[T, str]:
    """Tries each provider in order. Returns (result, name of the provider that served it)."""
    last: ProviderError | None = None
    tried_any = False
    for provider in providers:
        name = provider.name
        try:
            return with_retry(lambda p=provider: call(p), label=f"{label} via {name}", sleep=sleep), name
        except NotConfigured as e:
            log.info("provider skipped", extra={"call": label, "provider": name, "reason": str(e)})
            last = last or e
        except ProviderError as e:
            if e.refused:
                raise
            tried_any = True
            log.warning("provider failed, trying the next one", extra={"call": label, "provider": name})
            last = e
    raise AllProvidersFailed(f"{label}: no provider succeeded ({last})", none_available=not tried_any)
