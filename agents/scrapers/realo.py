"""
Realo scraper agent.

Realo provides a public search page (HTML) as well as a JSON endpoint
used by their front-end SPA.
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlencode

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

_REALO_SEARCH = "https://www.realo.be/en/search/for-sale/country-be"


class RealoScraper(BaseScraper):
    """Scrapes Realo listings."""

    name = "realo"

    def scrape(self) -> List[Property]:
        results: List[Property] = []
        page = 1
        while True:
            params = self._build_params(page)
            url = f"{_REALO_SEARCH}?{urlencode(params, doseq=True)}"
            try:
                resp = self._get(url)
                props = self._parse_html(resp.text, url)
                # AI fallback: if static parsing yields nothing, try GPT extraction
                if not props:
                    props = self._try_ai_extract(resp.text, url)
            except Exception as exc:
                logger.warning("[realo] Failed page %d: %s", page, exc)
                break

            if not props:
                break
            results.extend(props)

            # Realo paginates with ?page=N
            if len(props) < 20:
                break
            page += 1

        logger.info("[realo] Found %d listings", len(results))
        return results

    # ------------------------------------------------------------------

    def _build_params(self, page: int) -> dict:
        params: dict = {
            "transaction": "sale",
            "type": "house",
            "maxPrice": settings.max_price,
            "minBedrooms": settings.min_bedrooms,
            "minLandArea": settings.min_land_area,
            "page": page,
            "sort": "date",
        }
        for pc in settings.postal_code_list:
            params.setdefault("postalCodes[]", [])
            params["postalCodes[]"].append(pc)  # type: ignore[attr-defined]
        return params

    def _parse_html(self, html: str, base_url: str) -> List[Property]:
        soup = BeautifulSoup(html, "lxml")
        # Realo's current grid markup uses `component-estate-grid-item`
        # nested inside `component-estate-list-grid-item`.  Fall back to the
        # older selectors as well in case the layout is A/B-tested.
        cards = soup.select(
            "div.component-estate-grid-item[data-href], "
            "article.property-card, "
            "div[data-testid='property-card']"
        )
        props: List[Property] = []
        for card in cards:
            prop = self._parse_card(card)
            if prop:
                props.append(prop)
        return props

    def _parse_card(self, card) -> Property | None:
        try:
            # Newer markup exposes the canonical detail URL on `data-href`
            # and the numeric estate id on `data-id`.  Older markup only
            # has a nested <a href>.
            href = card.get("data-href") or ""
            if not href:
                link_tag = card.find("a", href=True)
                href = link_tag["href"] if link_tag else ""
            url = href.strip()
            if url and not url.startswith("http"):
                url = "https://www.realo.be" + url

            # Prefer `data-listingid` (unique per listing) over `data-id`
            # (which is the underlying address/estate id and can repeat
            # across multiple active listings on the same property).
            estate_id = (
                card.get("data-listingid")
                or card.get("data-id")
                or card.get("id")
            )
            if estate_id:
                prop_id = f"realo-{estate_id}"
            else:
                prop_id = f"realo-{url.rstrip('/').split('/')[-1]}" if url else "realo-unknown"

            title_tag = card.find(["h2", "h3", "span"], class_=lambda c: c and "title" in c.lower())
            title = title_tag.get_text(strip=True) if title_tag else ""

            # Full card text for NLP extraction
            card_text = card.get_text(" ", strip=True)

            price_tag = card.find(class_=lambda c: c and "price" in c.lower())
            price: float | None = None
            if price_tag:
                raw = price_tag.get_text(strip=True)
                # Try structured price tag first, fall back to NLP
                price = extract_price(raw) or extract_price(card_text)
            else:
                price = extract_price(card_text)

            location_tag = card.find(
                class_=lambda c: c and ("location" in c.lower() or "address" in c.lower())
            )
            address = location_tag.get_text(strip=True) if location_tag else None

            # Use the address as the title when the markup doesn't expose
            # a dedicated heading (Realo's grid view is image-first).
            if not title:
                title = address or "Woning"

            # Realo's carousel embeds the full image list as a JSON blob on
            # the carousel <ul data-images=...>.  Fall back to the first
            # <img src> if the JSON is missing or malformed.
            images: list[str] = []
            carousel = card.find(attrs={"data-images": True})
            if carousel:
                import json as _json

                try:
                    raw = carousel.get("data-images") or "[]"
                    for entry in _json.loads(raw):
                        src = entry.get("srcAt2x") or entry.get("src")
                        if src:
                            images.append(src)
                except Exception:
                    pass
            if not images:
                img_tag = card.find("img")
                if img_tag and img_tag.get("src"):
                    images = [img_tag["src"]]

            # Use NLP to enrich fields missing from structured markup
            bedrooms = extract_bedrooms(card_text)
            land_area = extract_land_area(card_text)
            living_area = extract_living_area(card_text)
            prop_type = classify_property_type(title + " " + card_text)

            return Property(
                id=prop_id,
                source=self.name,
                source_url=url or f"https://www.realo.be/nl/{prop_id}",
                title=title,
                price=price,
                address=address,
                images=images,
                property_type=prop_type,
                bedrooms=bedrooms,
                land_area=land_area,
                living_area=living_area,
            )
        except Exception as exc:
            logger.warning("[realo] Could not parse card: %s", exc)
            return None
