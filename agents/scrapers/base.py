"""
Base class for all scraper agents, plus a reusable HTTP client helper.
"""
from __future__ import annotations

import json as _json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, List

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from models.property import Property

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "nl-BE,nl;q=0.9,fr;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Timeout (ms) for Playwright page navigations
PLAYWRIGHT_TIMEOUT_MS = 30_000

# Persistent storage-state path.  Cookies acquired from a successful
# DataDome / Cloudflare warm-up are cached here so the next run starts
# from an already-trusted session instead of a cold browser.
PLAYWRIGHT_STORAGE_STATE = Path(
    os.environ.get(
        "PLAYWRIGHT_STORAGE_STATE",
        str(Path(__file__).resolve().parents[2] / ".playwright" / "storage_state.json"),
    )
)


def _proxy_config() -> dict | None:
    """Return a Playwright-style proxy dict from env, or ``None``.

    Recognised env vars (in order of precedence):

    * ``PLAYWRIGHT_PROXY_SERVER`` / ``PLAYWRIGHT_PROXY_USERNAME`` /
      ``PLAYWRIGHT_PROXY_PASSWORD``
    * ``HTTPS_PROXY`` / ``HTTP_PROXY`` (parsed for embedded credentials)

    Use this with a residential or mobile proxy to defeat DataDome on
    Immoweb / Zimmo / Logic-immo.  Datacenter proxies do **not** help —
    DataDome flags them just like the bare datacenter IP.
    """
    server = os.environ.get("PLAYWRIGHT_PROXY_SERVER")
    if server:
        cfg: dict = {"server": server}
        if os.environ.get("PLAYWRIGHT_PROXY_USERNAME"):
            cfg["username"] = os.environ["PLAYWRIGHT_PROXY_USERNAME"]
        if os.environ.get("PLAYWRIGHT_PROXY_PASSWORD"):
            cfg["password"] = os.environ["PLAYWRIGHT_PROXY_PASSWORD"]
        return cfg

    https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if https_proxy:
        from urllib.parse import urlparse

        parsed = urlparse(https_proxy)
        cfg = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 8080}"}
        if parsed.username:
            cfg["username"] = parsed.username
        if parsed.password:
            cfg["password"] = parsed.password
        return cfg
    return None

# Cached Playwright browser/context shared across requests within a single
# scraper run.  Re-using the context preserves cookies set by anti-bot
# providers (DataDome / Cloudflare) when warming up via the homepage,
# which dramatically improves success rates on subsequent search calls.


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
        # Proxy for httpx / cloudscraper.  Re-uses the same env vars as
        # `_proxy_config` so a single configuration drives all three tiers.
        proxy_url: str | None = None
        if os.environ.get("PLAYWRIGHT_PROXY_SERVER"):
            server = os.environ["PLAYWRIGHT_PROXY_SERVER"]
            user = os.environ.get("PLAYWRIGHT_PROXY_USERNAME")
            pwd = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD")
            if user and pwd:
                from urllib.parse import urlparse, urlunparse

                parsed = urlparse(server)
                netloc = f"{user}:{pwd}@{parsed.netloc or parsed.path}"
                proxy_url = urlunparse(parsed._replace(netloc=netloc, path=""))
            else:
                proxy_url = server
        else:
            proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")

        client_kwargs: dict = {
            "headers": HEADERS,
            "follow_redirects": True,
            "timeout": 30,
        }
        if proxy_url:
            # httpx >=0.28 takes a single ``proxy`` argument; older releases
            # used ``proxies``.  Try the new keyword first.
            try:
                self._client = httpx.Client(proxy=proxy_url, **client_kwargs)
            except TypeError:
                self._client = httpx.Client(proxies=proxy_url, **client_kwargs)
        else:
            self._client = httpx.Client(**client_kwargs)
        self._proxy_url = proxy_url
        # Lazily initialised — cloudscraper imports a JS interpreter the first
        # time it is used, which is unnecessary for clients that never hit a
        # Cloudflare-protected site.
        self._cloudscraper = None
        # Lazily initialised Playwright session (playwright instance, browser,
        # context).  Re-used across calls so that cookies acquired during a
        # warm-up navigation persist for subsequent requests.
        self._pw = None  # type: ignore[var-annotated]
        self._pw_browser = None  # type: ignore[var-annotated]
        self._pw_context = None  # type: ignore[var-annotated]
        self._pw_warmed: set[str] = set()

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
            if self._proxy_url:
                self._cloudscraper.proxies = {
                    "http": self._proxy_url,
                    "https": self._proxy_url,
                }
            # Re-use any session cookies the Playwright tier captured.
            self._sync_pw_cookies_to(self._cloudscraper)

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

    def _ensure_playwright_context(self):
        """Lazily start Playwright and return a stealthy persistent context.

        The returned context is reused across all calls in this client's
        lifetime so cookies (notably DataDome / Cloudflare session cookies
        obtained during a homepage warm-up) persist between requests.
        """
        if self._pw_context is not None:
            return self._pw_context

        from playwright.sync_api import sync_playwright  # lazy import

        self._pw = sync_playwright().start()
        launch_kwargs: dict = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        }
        proxy = _proxy_config()
        if proxy:
            launch_kwargs["proxy"] = proxy
            logger.info("[%s] Playwright using proxy: %s", self.name, proxy["server"])
        self._pw_browser = self._pw.chromium.launch(**launch_kwargs)

        context_kwargs: dict = {
            "user_agent": HEADERS["User-Agent"],
            "locale": "nl-BE",
            "timezone_id": "Europe/Brussels",
            "viewport": {"width": 1366, "height": 768},
            "extra_http_headers": {
                "Accept-Language": HEADERS["Accept-Language"],
                "Sec-Ch-Ua": HEADERS["Sec-Ch-Ua"],
                "Sec-Ch-Ua-Mobile": HEADERS["Sec-Ch-Ua-Mobile"],
                "Sec-Ch-Ua-Platform": HEADERS["Sec-Ch-Ua-Platform"],
            },
        }
        # Reload a previously-trusted session if available — DataDome
        # cookies are valid for hours and re-using them avoids tripping
        # the bot challenge on every run.
        if PLAYWRIGHT_STORAGE_STATE.is_file():
            try:
                context_kwargs["storage_state"] = str(PLAYWRIGHT_STORAGE_STATE)
                logger.info(
                    "[%s] Loaded Playwright storage state from %s",
                    self.name,
                    PLAYWRIGHT_STORAGE_STATE,
                )
            except Exception:
                pass
        self._pw_context = self._pw_browser.new_context(**context_kwargs)

        # Apply playwright-stealth patches if the package is available
        try:
            from playwright_stealth import Stealth  # type: ignore

            Stealth().apply_stealth_sync(self._pw_context)
        except Exception as exc:  # pragma: no cover — best-effort
            logger.debug("[%s] playwright-stealth unavailable: %s", self.name, exc)

        # Mask the most common automation tells.  These complement (or stand
        # in for) playwright-stealth on systems where it is not installed.
        self._pw_context.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['nl-BE','nl','en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            window.chrome = window.chrome || { runtime: {} };
            const _q = window.navigator.permissions && window.navigator.permissions.query;
            if (_q) {
                window.navigator.permissions.query = (p) =>
                    p && p.name === 'notifications'
                        ? Promise.resolve({ state: Notification.permission })
                        : _q(p);
            }
            """
        )
        return self._pw_context

    def _close_playwright(self) -> None:
        try:
            if self._pw_context is not None:
                # Persist cookies so the next process starts already
                # warmed up.  The directory may not exist on a first run.
                try:
                    PLAYWRIGHT_STORAGE_STATE.parent.mkdir(parents=True, exist_ok=True)
                    self._pw_context.storage_state(path=str(PLAYWRIGHT_STORAGE_STATE))
                    logger.debug(
                        "[%s] Saved Playwright storage state to %s",
                        self.name,
                        PLAYWRIGHT_STORAGE_STATE,
                    )
                except Exception as exc:
                    logger.debug("[%s] storage_state save failed: %s", self.name, exc)
                self._pw_context.close()
            if self._pw_browser is not None:
                self._pw_browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # pragma: no cover
            pass
        finally:
            self._pw_context = None
            self._pw_browser = None
            self._pw = None
            self._pw_warmed.clear()

    def _sync_pw_cookies_to(self, target) -> None:
        """Copy current Playwright cookies into an httpx / requests session."""
        if self._pw_context is None:
            return
        try:
            for c in self._pw_context.cookies():
                domain = c.get("domain") or ""
                name = c.get("name")
                value = c.get("value")
                if not name or value is None:
                    continue
                if hasattr(target, "cookies") and hasattr(target.cookies, "set"):
                    try:
                        target.cookies.set(name, value, domain=domain.lstrip("."))
                    except Exception:
                        pass
        except Exception:
            pass

    def _warm_up_playwright(self, context, url: str) -> None:
        """Visit the site's homepage with human-like behaviour.

        DataDome and Akamai score requests on more than just cookies — mouse
        movement, scrolling and dwell time all feed their behavioural model.
        Doing a brief mouse/scroll dance during warm-up substantially
        improves the chances that the issued cookie is "trusted" rather than
        "challenged".
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._pw_warmed:
            return
        self._pw_warmed.add(origin)
        try:
            page = context.new_page()
            try:
                logger.info("[%s] Playwright warm-up: GET %s", self.name, origin)
                page.goto(origin, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
                # Human-like behaviour: small mouse moves, scroll, dwell.
                try:
                    page.mouse.move(200, 200)
                    page.wait_for_timeout(400)
                    page.mouse.move(640, 480, steps=12)
                    page.wait_for_timeout(300)
                    page.mouse.wheel(0, 600)
                    page.wait_for_timeout(700)
                    page.mouse.wheel(0, 400)
                    page.wait_for_timeout(800)
                except Exception:
                    pass
                # Final dwell — DataDome's challenge JS needs ~2s to finish.
                page.wait_for_timeout(2000)
            finally:
                page.close()
        except Exception as exc:
            logger.debug("[%s] warm-up failed for %s: %s", self.name, origin, exc)

    def _get_playwright(self, url: str, **kwargs) -> _PlaywrightResponse:
        """
        Fetch *url* using a headless Chromium browser with stealth patches
        and an upfront homepage warm-up.

        For JSON-accepting requests the browser navigates to the URL and the
        raw JSON body is extracted from the page; for HTML requests the full
        rendered page content is returned.  Using a real browser avoids most
        bot-detection mechanisms (TLS fingerprinting, JS challenges, etc.).
        """
        headers_extra: dict = kwargs.get("headers") or {}
        want_json = "json" in headers_extra.get("Accept", "text/html").lower()

        logger.info("[%s] Playwright fallback: GET %s", self.name, url)
        context = self._ensure_playwright_context()
        self._warm_up_playwright(context, url)

        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
            # Give client-side anti-bot scripts a moment to settle and any
            # late-rendered listings to mount before we grab the DOM.
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

            if want_json:
                body_text = page.inner_text("body")
                try:
                    return _PlaywrightResponse(json_data=_json.loads(body_text))
                except _json.JSONDecodeError as exc:
                    raise ValueError(
                        f"[{self.name}] Playwright fetched {url} but the page body "
                        f"did not contain valid JSON: {exc}"
                    ) from exc
            content = page.content()
            # Push cookies into the httpx client so the next cheap request
            # to the same origin starts already-trusted.
            self._sync_pw_cookies_to(self._client)
            if self._cloudscraper is not None:
                self._sync_pw_cookies_to(self._cloudscraper)
            return _PlaywrightResponse(text=content)
        finally:
            page.close()

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
        self._close_playwright()

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
