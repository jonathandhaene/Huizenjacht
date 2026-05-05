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
    Minimal response-like wrapper around content fetched via Playwright or
    cloudscraper.  Provides the same `.text`, `.json()`, and
    `.raise_for_status()` interface as an `httpx.Response` so scrapers can
    use it transparently.
    """

    def __init__(self, text: str = "", json_data: Any = None) -> None:
        self.text = text
        self._json_data = json_data

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return _json.loads(self.text)

    def raise_for_status(self) -> None:
        pass  # Already validated upstream


class HttpClient:
    """
    Reusable HTTP client with automatic retries and two escalating fallbacks.

    Fetch order:
        1. plain httpx (fast, default)
        2. cloudscraper — bypasses Cloudflare bot-detection without a browser
        3. Playwright — full headless Chromium (slowest, last resort)

    Used directly by non-scraper agents (e.g. GovernmentEnrichmentAgent).
    """

    name: str = "http_client"

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30,
        )
        # Lazily initialised — cloudscraper imports a JS interpreter the first
        # time it is used, which is unnecessary for clients that never hit a
        # Cloudflare-protected site.
        self._cloudscraper = None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _get_http(self, url: str, **kwargs) -> httpx.Response:
        logger.debug("[%s] GET %s", self.name, url)
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def _get_cloudscraper(self, url: str, **kwargs) -> _PlaywrightResponse:
        """
        Fetch *url* via cloudscraper, which mimics Chrome's TLS handshake and
        solves Cloudflare's basic JS challenges.  This is much cheaper than
        spinning up a full Playwright browser session.
        """
        import cloudscraper  # lazy import — heavy dependency

        if self._cloudscraper is None:
            self._cloudscraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows"}
            )

        headers = {**HEADERS, **(kwargs.get("headers") or {})}
        logger.info("[%s] cloudscraper fallback: GET %s", self.name, url)
        resp = self._cloudscraper.get(url, headers=headers, timeout=30, allow_redirects=True)
        resp.raise_for_status()

        accept = headers.get("Accept", "text/html").lower()
        if "json" in accept:
            try:
                return _PlaywrightResponse(json_data=resp.json())
            except ValueError as exc:
                # Body wasn't JSON — keep the text so the caller can still
                # inspect it (e.g. parse embedded JSON in an HTML page).
                logger.debug(
                    "[%s] cloudscraper response was not JSON, returning text: %s",
                    self.name,
                    exc,
                )
        return _PlaywrightResponse(text=resp.text)

    def _get_playwright(self, url: str, **kwargs) -> _PlaywrightResponse:
        """
        Fetch *url* using a headless Chromium browser.

        For JSON-accepting requests the browser navigates to the URL and the
        raw JSON body is extracted from the page; for HTML requests the full
        rendered page content is returned.  Using a real browser avoids most
        bot-detection mechanisms (TLS fingerprinting, JS challenges, etc.).
        """
        from playwright.sync_api import sync_playwright  # lazy import – not always needed

        headers_extra: dict = kwargs.get("headers") or {}
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
                # `domcontentloaded` is far more reliable than `networkidle`
                # for modern SPAs that keep long-running websockets / trackers
                # open — the latter routinely times out at 30 s on real
                # estate sites (see Actions run #25398130280).
                page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)

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
        Fetch *url* with automatic retries and two escalating fallbacks.

        On HTTP errors (e.g. 403 bot-detection) the request is retried up to
        three times via `httpx`, then via `cloudscraper` (TLS-spoofing
        requests session), then finally via a real Chromium browser session.
        """
        try:
            return self._get_http(url, **kwargs)
        except Exception as http_exc:
            logger.warning(
                "[%s] HTTP fetch failed (%s); trying cloudscraper: %s",
                self.name,
                type(http_exc).__name__,
                url,
            )
            try:
                return self._get_cloudscraper(url, **kwargs)
            except Exception as cs_exc:
                logger.warning(
                    "[%s] cloudscraper fetch failed (%s); falling back to Playwright: %s",
                    self.name,
                    type(cs_exc).__name__,
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

    def __init__(self) -> None:
        super().__init__()
        # Lazily initialised — only active when OPENAI_API_KEY is set.
        self._ai_extractor = None

    def _get_ai_extractor(self):
        """Return a cached AIPropertyExtractor instance (lazy init)."""
        if self._ai_extractor is None:
            from agents.scrapers.ai_extractor import AIPropertyExtractor  # avoid circular import

            self._ai_extractor = AIPropertyExtractor()
        return self._ai_extractor

    def _try_ai_extract(self, html: str, url: str) -> List[Property]:
        """Try to extract properties from *html* using the AI extractor.

        This is the last-resort fallback: it is only called when static
        parsing produces zero results and an ``OPENAI_API_KEY`` is
        configured.  Returns an empty list when the API is unavailable.
        """
        extractor = self._get_ai_extractor()
        if not extractor.available:
            return []
        return extractor.extract_from_html(html, url, self.name)

    def _log_http_error(self, url: str, status_code: int, response_text: str = "") -> None:
        """Log a structured diagnosis of an HTTP scraping failure.

        Uses ``AIPropertyExtractor.analyze_error`` when available, otherwise
        falls back to a static rule-based analysis so that meaningful
        information is always surfaced in the logs.
        """
        extractor = self._get_ai_extractor()
        analysis = extractor.analyze_error(url, status_code, response_text)
        logger.warning(
            "[%s] HTTP %d on %s — %s: %s. Suggestions: %s. Retry: %s",
            self.name,
            status_code,
            url,
            analysis.get("error_type", "unknown"),
            analysis.get("likely_cause", ""),
            "; ".join(analysis.get("suggestions", [])),
            analysis.get("retry_strategy", "none"),
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @abstractmethod
    def scrape(self) -> List[Property]:
        """Return a list of Property objects found today."""
