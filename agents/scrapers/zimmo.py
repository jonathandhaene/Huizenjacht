"""
Zimmo scraper agent.

Zimmo exposes a GraphQL-like search API.  We use the REST-style search endpoint
that their mobile app consumes.
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlencode

from agents.scrapers.base import BaseScraper
from config.settings import settings
from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

_ZIMMO_API = "https://www.zimmo.be/nl/te-koop/zoeken/"


class ZimmoScraper(BaseScraper):
    """Scrapes Zimmo for house listings."""

    name = "zimmo"

    def scrape(self) -> List[Property]:
        results: List[Property] = []
        page = 1
        while True:
            params = self._build_params(page)
            url = f"{_ZIMMO_API}?{urlencode(params, doseq=True)}"
            try:
                resp = self._get(url, headers={"Accept": "application/json, text/javascript, */*"})
                data = resp.json()
            except Exception as exc:
                logger.warning("[zimmo] Failed to fetch page %d: %s", page, exc)
                break

            items = data.get("properties") or data.get("results") or []
            if not items:
                break

            for item in items:
                prop = self._parse_item(item)
                if prop:
                    results.append(prop)

            total_pages = data.get("totalPages") or data.get("pages") or 1
            if page >= total_pages:
                break
            page += 1

        logger.info("[zimmo] Found %d listings", len(results))
        return results

    # ------------------------------------------------------------------

    def _build_params(self, page: int) -> dict:
        postal_codes = ",".join(settings.postal_code_list)
        return {
            "page": page,
            "maxPrice": settings.max_price,
            "minBedrooms": settings.min_bedrooms,
            "minPlotSurface": settings.min_land_area,
            "postalCode": postal_codes,
            "type": "house",
            "sort": "date-desc",
        }

    def _parse_item(self, item: dict) -> Property | None:
        try:
            prop_id = f"zimmo-{item.get('id') or item.get('reference')}"
            location = item.get("location") or {}
            details = item.get("details") or item.get("characteristics") or {}

            price_raw = item.get("price") or item.get("askingPrice")
            price = float(price_raw) if price_raw else None

            images = [
                img if isinstance(img, str) else img.get("url", "")
                for img in item.get("images") or item.get("photos") or []
            ]

            return Property(
                id=prop_id,
                source=self.name,
                source_url=item.get("url") or item.get("link") or f"https://www.zimmo.be/nl/{prop_id}",
                title=item.get("title") or item.get("name") or "Woning",
                description=item.get("description"),
                property_type=PropertyType.HOUSE,
                price=price,
                address=location.get("street") or location.get("address"),
                postal_code=str(location.get("postalCode") or location.get("zip") or ""),
                municipality=location.get("city") or location.get("municipality"),
                land_area=details.get("plotSurface") or details.get("landSurface"),
                living_area=details.get("livingSurface") or details.get("habitableSurface"),
                bedrooms=details.get("bedrooms") or details.get("bedroomCount"),
                bathrooms=details.get("bathrooms") or details.get("bathroomCount"),
                images=[i for i in images if i],
            )
        except Exception as exc:
            logger.warning("[zimmo] Could not parse item: %s", exc)
            return None
