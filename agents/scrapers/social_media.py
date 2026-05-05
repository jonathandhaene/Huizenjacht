"""
Social-media scraper agent.

Monitors public Facebook Marketplace listings and relevant public Facebook
groups for house listings in the Vlaamse Ardennen.

Note: Facebook's terms of service prohibit automated scraping of profile/
personal data.  This agent **only** reads publicly available Marketplace
listings and public group posts — the same data a logged-out browser sees.

Because Facebook blocks most automated access, this agent uses a
configurable list of public Facebook group URLs and scrapes only their
publicly visible post excerpts via plain HTTP (no login required).

If no listings are found (Facebook blocks the request), the agent logs a
warning and returns an empty list — it never crashes the pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import quote

from bs4 import BeautifulSoup

from agents.scrapers.base import BaseScraper
from agents.scrapers.nlp_normalizer import (
    classify_property_type,
    extract_bedrooms,
    extract_land_area,
    extract_living_area,
    extract_price,
)
from config.settings import settings
from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

# Public Facebook Marketplace search URL (no login required to view)
_FB_MARKETPLACE_SEARCH = (
    "https://www.facebook.com/marketplace/category/propertyrentals"
    "?sortBy=creation_time_descend"
    "&exact=false"
)

# Public Facebook groups relevant to Vlaamse Ardennen immobiliën
_PUBLIC_FB_GROUPS: List[str] = [
    "https://www.facebook.com/groups/immovlaamseardennen/",
    "https://www.facebook.com/groups/koopenverkoopvlaamseardennen/",
    "https://www.facebook.com/groups/hoevesenvastgoedoostVlaanderen/",
]

# Keywords to match in post text
_KEYWORDS = settings.keyword_list + ["te koop", "huis", "hoeve", "boerderij"]


class SocialMediaScraper(BaseScraper):
    """
    Scrapes publicly accessible Facebook Marketplace and group posts.
    Returns Property objects for any listings that match the search criteria.
    """

    name = "social_media"

    def scrape(self) -> List[Property]:
        results: List[Property] = []
        results.extend(self._scrape_marketplace())
        for group_url in _PUBLIC_FB_GROUPS:
            results.extend(self._scrape_public_group(group_url))
        logger.info("[social_media] Found %d listings", len(results))
        return results

    # ------------------------------------------------------------------

    def _scrape_marketplace(self) -> List[Property]:
        """
        Attempt to fetch publicly visible Marketplace listings.
        Facebook typically returns a login wall, so this will usually yield
        zero results — but it's worth trying and is totally harmless.
        """
        try:
            resp = self._get(_FB_MARKETPLACE_SEARCH)
            return self._parse_marketplace_html(resp.text)
        except Exception as exc:
            logger.warning("[social_media] Marketplace scrape failed (expected): %s", exc)
            return []

    def _parse_marketplace_html(self, html: str) -> List[Property]:
        """Parse Marketplace listing cards from HTML."""
        soup = BeautifulSoup(html, "lxml")
        props: List[Property] = []
        # Marketplace cards use data-testid="marketplace_feed_story" or similar
        cards = soup.select("[data-testid='marketplace_feed_story'], div[aria-label]")
        for idx, card in enumerate(cards[:50]):
            text = card.get_text(" ", strip=True)
            if not self._is_relevant(text):
                continue
            link = card.find("a", href=True)
            url = link["href"] if link else ""
            if url and not url.startswith("http"):
                url = "https://www.facebook.com" + url
            props.append(self._build_property(
                text=text,
                url=url or _FB_MARKETPLACE_SEARCH,
                prop_id=f"fb_marketplace_{idx}_{hash(text) & 0xFFFFFFFF}",
            ))
        return props

    def _scrape_public_group(self, group_url: str) -> List[Property]:
        """Fetch and parse a public Facebook group page."""
        try:
            resp = self._get(group_url)
            return self._parse_group_html(resp.text, group_url)
        except Exception as exc:
            logger.warning("[social_media] Group scrape failed for %s: %s", group_url, exc)
            return []

    def _parse_group_html(self, html: str, base_url: str) -> List[Property]:
        soup = BeautifulSoup(html, "lxml")
        props: List[Property] = []
        # Group posts typically live in <div role="article">
        posts = soup.select("div[role='article'], div[data-pagelet*='FeedUnit']")
        for idx, post in enumerate(posts[:50]):
            text = post.get_text(" ", strip=True)
            if not self._is_relevant(text):
                continue
            link = post.find("a", href=True)
            url = link["href"] if link else base_url
            if url and not url.startswith("http"):
                url = "https://www.facebook.com" + url
            props.append(self._build_property(
                text=text,
                url=url,
                prop_id=f"fb_group_{idx}_{hash(text) & 0xFFFFFFFF}",
            ))
        return props

    # ------------------------------------------------------------------

    def _build_property(self, text: str, url: str, prop_id: str) -> Property:
        """Construct a Property from post text using NLP normalization."""
        price = extract_price(text)
        bedrooms = extract_bedrooms(text)
        land_area = extract_land_area(text)
        living_area = extract_living_area(text)
        prop_type = classify_property_type(text)
        return Property(
            id=prop_id,
            source=self.name,
            source_url=url,
            title=text[:120],
            description=text,
            property_type=prop_type,
            price=price,
            bedrooms=bedrooms,
            land_area=land_area,
            living_area=living_area,
        )

    @staticmethod
    def _is_relevant(text: str) -> bool:
        """Return True if the text mentions at least one relevant keyword."""
        lower = text.lower()
        return any(kw.lower() in lower for kw in _KEYWORDS)

    @staticmethod
    def _extract_price(text: str) -> float | None:
        """Try to parse a price like '€ 450.000' or '450000 €' from text.

        Delegates to the shared NLP normalizer for consistent behaviour.
        """
        return extract_price(text)
