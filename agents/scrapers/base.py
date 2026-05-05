"""
Base class for all scraper agents, plus a reusable HTTP client helper.
"""
from __future__ import annotations

import json as _json
import logging
from abc import ABC, abstractmethod
from typing import Any, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from models.property import Property

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-BE,nl;q=0.9,fr;q=0.8,en;q=0.7",
}

# Timeout (ms) for Playwright page navigations
PLAYWRIGHT_TIMEOUT_MS = 30_000


class _PlaywrightResponse:
    """
    Minimal response-like wrapper around content fetched via Playwright.
    Provides the same `.text`, `.json()`, and `.raise_for_status()` interface
    as an `httpx.Response` so scrapers can use it transparently.
    """

    def __init__(self, text: str = "", json_data: Any = None) -> None:
        self.text = text
        self._json_data = json_data

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return _json.loads(self.text)

    def raise_for_status(self) -> None:
        pass  # Playwright already validated the response


class HttpClient:
    """
    Reusable HTTP client with automatic retries and a Playwright fallback.
    Used directly by non-scraper agents (e.g. GovernmentEnrichmentAgent).
    """

    name: str = "http_client"

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get_http(self, url: str, **kwargs) -> httpx.Response:
        logger.debug("[%s] GET %s", self.name, url)
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def _get_playwright(self, url: str, **kwargs) -> _PlaywrightResponse:
        """
        Fetch *url* using a headless Chromium browser.

        For JSON-accepting requests the browser navigates to the URL and the
        raw JSON body is extracted from the page; for HTML requests the full
        rendered page content is returned.  Using a real browser avoids most
        bot-detection mechanisms (TLS fingerprinting, JS challenges, etc.).
        """
        from playwright.sync_api import sync_playwright  # lazy import – not always needed

        headers_extra: dict = kwargs.get("headers", {})
        want_json = "json" in headers_extra.get("Accept", "text/html").lower()

        logger.info("[%s] Playwright fallback: GET %s", self.name, url)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    user_agent=HEADERS["User-Agent"],
                    locale="nl-BE",
                    extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)

                if want_json:
                    body_text = page.inner_text("body")
                    try:
                        return _PlaywrightResponse(json_data=_json.loads(body_text))
                    except _json.JSONDecodeError as exc:
                        raise ValueError(
                            f"[{self.name}] Playwright fetched {url} but the page body "
                            f"did not contain valid JSON: {exc}"
                        ) from exc
                else:
                    return _PlaywrightResponse(text=page.content())
            finally:
                browser.close()

    def _get(self, url: str, **kwargs) -> "httpx.Response | _PlaywrightResponse":
        """
        Fetch *url* with automatic retries (via httpx) and a Playwright fallback.

        On HTTP errors (e.g. 403 bot-detection) the request is retried up to
        three times, then transparently retried once more via a real Chromium
        browser session.
        """
        try:
            return self._get_http(url, **kwargs)
        except Exception as exc:
            logger.warning(
                "[%s] HTTP fetch failed (%s); retrying with Playwright: %s",
                self.name,
                type(exc).__name__,
                url,
            )
            return self._get_playwright(url, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class BaseScraper(HttpClient, ABC):
    """Abstract base for all scrapers.  Each sub-class implements `scrape()`."""

    name: str = "base"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @abstractmethod
    def scrape(self) -> List[Property]:
        """Return a list of Property objects found today."""
