"""Errors and retry helpers used by every provider."""

from __future__ import annotations

import logging
import random
import time
from typing import Callable, Optional, TypeVar

import requests

log = logging.getLogger("reelforge")
T = TypeVar("T")


class ProviderError(Exception):
    """A provider call failed.

    retryable: worth trying the same provider again (timeouts, 429, 5xx).
    refused:   the provider rejected the content (safety filter); don't retry or fall back blindly.
    """

    def __init__(self, message: str, *, retryable: bool = False, refused: bool = False,
                 retry_after: Optional[float] = None):
        super().__init__(message)
        self.retryable = retryable
        self.refused = refused
        self.retry_after = retry_after


class NotConfigured(ProviderError):
    """The provider has no API key or settings; skip to the next one."""


def http(method: str, url: str, *, timeout: float = 60, **kwargs) -> requests.Response:
    """requests call that converts failures into ProviderError with the right retry flags."""
    try:
        resp = requests.request(method, url, timeout=timeout, **kwargs)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise ProviderError(f"network error: {e}", retryable=True) from e

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        raise ProviderError(
            "rate limited (429)", retryable=True,
            retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    if resp.status_code >= 500:
        raise ProviderError(f"server error {resp.status_code}: {resp.text[:300]}", retryable=True)
    if resp.status_code >= 400:
        text = resp.text[:500]
        refused = any(w in text.lower() for w in ("safety", "moderation", "nsfw", "policy", "blocked"))
        raise ProviderError(f"request rejected {resp.status_code}: {text}", refused=refused)
    return resp


def with_retry(fn: Callable[[], T], *, attempts: int = 3, base_delay: float = 2.0, label: str = "") -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ProviderError as e:
            if not e.retryable or attempt == attempts:
                raise
            delay = e.retry_after or base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            log.warning("%s failed (%s); retry %d/%d in %.1fs", label, e, attempt, attempts - 1, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def with_fallback(providers: list, call: Callable[[object], T], *, label: str) -> T:
    """Try each provider in order (each with retries). Raise the last error if all fail."""
    if not providers:
        raise ProviderError(f"{label}: no providers configured")
    last: Optional[Exception] = None
    for provider in providers:
        name = getattr(provider, "name", type(provider).__name__)
        try:
            return with_retry(lambda: call(provider), label=f"{label} via {name}")
        except NotConfigured as e:
            log.info("%s: skipping %s (%s)", label, name, e)
            last = e
        except ProviderError as e:
            if e.refused:
                raise
            log.warning("%s: %s failed, trying next provider (%s)", label, name, e)
            last = e
    raise ProviderError(f"{label}: all providers failed; last error: {last}")
