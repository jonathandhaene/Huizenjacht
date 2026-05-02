"""
Base class for all scraper agents, plus a reusable HTTP client helper.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

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


class HttpClient:
    """
    Reusable HTTP client with automatic retries.
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
    def _get(self, url: str, **kwargs) -> httpx.Response:
        logger.debug("[%s] GET %s", self.name, url)
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

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
