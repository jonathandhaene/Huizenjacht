"""
Immovlan portal scraper.

Immovlan only honours postal-code filtering through the path-style URL
``/nl/vastgoed/<type>/te-koop/<postal>-<locality>``.  We therefore iterate over
the configured Vlaamse Ardennen postal codes (with a hard-coded slug map),
paginate via ``?page=N`` and parse the standard ``<article class="list-view-item">``
cards that carry ``itemtype="http://schema.org/House"`` micro-data.
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from agents.scrapers.base import BaseScraper
from agents.scrapers.nlp_normalizer import (
    classify_property_type,
    extract_land_area,
    extract_price,
)
from config.settings import settings
from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

_IMMOVLAN_BASE = "https://immovlan.be"
_IMMOVLAN_SEARCH_TEMPLATE = _IMMOVLAN_BASE + "/nl/vastgoed/{ptype}/te-koop/{slug}"

# Property-type path segments.  Immovlan uses singular Dutch nouns.
_PROPERTY_TYPE_PATHS = ["huis", "villa"]

# Stop after this many pages per (postal, type) combination.
_MAX_PAGES = 25

# Postal-code → list of locality slugs to query.  Multiple slugs cover the
# different sub-municipalities Immovlan groups under one postcode.
_LOCALITY_SLUGS: dict[str, list[str]] = {
    "9600": ["ronse"],
    "9620": ["zottegem"],
    "9630": ["zwalm", "munkzwalm"],
    "9660": ["brakel"],
    "9680": ["maarkedal"],
    "9688": ["schorisse"],
    "9690": ["kluisbergen"],
    "9700": ["oudenaarde"],
    "9750": ["zingem", "kruisem"],
    "9770": ["kruishoutem", "kruisem"],
    "9790": ["wortegem-petegem"],
}

_LISTING_ID_RE = re.compile(
    r"/nl/detail/(?P<subtype>[^/]+)/te-koop/(?P<postal>\d{4})/(?P<locality>[^/]+)/(?P<id>[a-z0-9]+)",
    re.IGNORECASE,
)

_SUBTYPE_TO_ENUM: dict[str, PropertyType] = {
    "huis": PropertyType.HOUSE,
    "herenhuis": PropertyType.HOUSE,
    "huis-gemengd-gebruik": PropertyType.HOUSE,
    "villa": PropertyType.VILLA,
    "boerderij": PropertyType.FARM,
    "hoeve": PropertyType.FARM,
    "landhuis": PropertyType.FARM,
    "grond": PropertyType.LAND,
    "bouwgrond": PropertyType.LAND,
}


class ImmovlanScraper(BaseScraper):
    """Scrapes Immovlan for house listings in the configured postal codes."""

    name = "immovlan"

    def scrape(self) -> List[Property]:
        results: List[Property] = []
        seen_ids: set[str] = set()

        for postal in settings.postal_code_list:
            slugs = _LOCALITY_SLUGS.get(postal)
            if not slugs:
                logger.debug("[immovlan] No slug mapping for postal %s, skipping", postal)
                continue

            for slug in slugs:
                for ptype in _PROPERTY_TYPE_PATHS:
                    new_count = self._scrape_postal_type(postal, slug, ptype, seen_ids, results)
                    logger.debug(
                        "[immovlan] %s/%s/%s → %d new", postal, slug, ptype, new_count,
                    )

        logger.info("[immovlan] Found %d listings", len(results))
        return results

    # ------------------------------------------------------------------

    def _scrape_postal_type(
        self,
        postal: str,
        slug: str,
        ptype: str,
        seen_ids: set[str],
        results: List[Property],
    ) -> int:
        added = 0
        base = _IMMOVLAN_SEARCH_TEMPLATE.format(ptype=ptype, slug=f"{postal}-{slug}")
        for page in range(1, _MAX_PAGES + 1):
            url = base if page == 1 else f"{base}?{urlencode({'page': page})}"
            try:
                resp = self._get(url)
                html = resp.text
            except Exception as exc:
                logger.warning("[immovlan] Failed to fetch %s: %s", url, exc)
                break

            cards = self._parse_search_page(html)
            if not cards:
                break

            page_new = 0
            for prop in cards:
                if prop.id in seen_ids:
                    continue
                if prop.postal_code and prop.postal_code != postal:
                    # Immovlan sometimes mixes nearby municipalities — keep them
                    # only when the postcode is in our configured set.
                    if prop.postal_code not in settings.postal_code_list:
                        continue
                if not self._matches_filters(prop):
                    continue
                seen_ids.add(prop.id)
                results.append(prop)
                page_new += 1
                added += 1

            if page_new == 0:
                break

        return added

    # ------------------------------------------------------------------

    def _parse_search_page(self, html: str) -> List[Property]:
        soup = BeautifulSoup(html, "lxml")
        articles = soup.select('article.list-view-item, article[itemtype*="schema.org/House"]')
        properties: List[Property] = []
        for art in articles:
            prop = self._parse_card(art)
            if prop:
                properties.append(prop)
        return properties

    def _parse_card(self, art) -> Property | None:
        try:
            url = art.get("data-url")
            if not url:
                anchor = art.select_one("a[href*='/nl/detail/']")
                if anchor:
                    url = anchor.get("href")
            if not url:
                return None

            match = _LISTING_ID_RE.search(url)
            if not match:
                return None
            listing_id = match.group("id").lower()
            subtype = match.group("subtype").lower()
            postal = match.group("postal")
            locality = match.group("locality").replace("-", " ").title()

            title_el = art.select_one("[itemprop='name']")
            title = (title_el.get_text(strip=True) if title_el else "Woning") or "Woning"

            price = None
            price_el = art.select_one(".list-item-price")
            if price_el:
                price = extract_price(price_el.get_text(" ", strip=True))

            description_el = art.select_one("[itemprop='description']")
            description = description_el.get_text(" ", strip=True) if description_el else None

            postal_el = art.select_one("[itemprop='postalCode']")
            if postal_el and postal_el.get_text(strip=True).isdigit():
                postal = postal_el.get_text(strip=True)
            locality_el = art.select_one("[itemprop='addressLocality']")
            if locality_el:
                locality = locality_el.get_text(strip=True)

            bedrooms = None
            bedrooms_meta = art.select_one("meta[itemprop='numberOfBedrooms']")
            if bedrooms_meta and bedrooms_meta.get("content", "").isdigit():
                bedrooms = int(bedrooms_meta["content"])

            bathrooms = None
            bathrooms_meta = art.select_one("meta[itemprop='numberOfBathroomsTotal']")
            if bathrooms_meta and bathrooms_meta.get("content", "").isdigit():
                bathrooms = int(bathrooms_meta["content"])

            living_area = None
            land_area = None
            for highlight in art.select(".property-highlight"):
                text = highlight.get_text(" ", strip=True)
                lower = text.lower()
                strong = highlight.find("strong")
                value_text = strong.get_text(strip=True) if strong else ""
                value = self._safe_float(value_text)
                if value is None:
                    continue
                if "slaapkamer" in lower and bedrooms is None:
                    bedrooms = int(value)
                elif "badkamer" in lower and bathrooms is None:
                    bathrooms = int(value)
                elif "m²" in lower and "are" not in lower and living_area is None:
                    living_area = value
                elif ("are" in lower or "ha" in lower or "perceel" in lower) and land_area is None:
                    land_area = extract_land_area(text) or value

            images: list[str] = []
            for img in art.select("img[itemprop='photo'], .media-pic img"):
                src = img.get("data-src") or img.get("content") or img.get("src")
                if src and "nopic" not in src:
                    images.append(src)

            property_type = _SUBTYPE_TO_ENUM.get(subtype) or classify_property_type(
                f"{title} {description or ''}"
            )

            return Property(
                id=f"immovlan-{listing_id}",
                source=self.name,
                source_url=url,
                title=title,
                description=description,
                property_type=property_type,
                price=price,
                postal_code=postal,
                municipality=locality,
                land_area=land_area,
                living_area=living_area,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                images=images,
            )
        except Exception as exc:
            logger.debug("[immovlan] Could not parse card: %s", exc)
            return None

    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(text: str) -> float | None:
        if not text:
            return None
        cleaned = text.replace("\xa0", " ").replace(".", "").replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", cleaned)
        if not match:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    def _matches_filters(self, prop: Property) -> bool:
        if prop.price is not None and prop.price > settings.max_price:
            return False
        if (
            prop.bedrooms is not None
            and settings.min_bedrooms
            and prop.bedrooms < settings.min_bedrooms
        ):
            return False
        if (
            prop.land_area is not None
            and settings.min_land_area
            and prop.land_area < settings.min_land_area
        ):
            return False
        return True
