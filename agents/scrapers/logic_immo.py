"""
Logic Immo scraper agent.

Logic Immo Belgium (logic-immo.be) has a JSON search API that mirrors
the search results shown on their website.
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlencode

from agents.scrapers.base import BaseScraper
from config.settings import settings
from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

_LOGIC_IMMO_API = "https://www.logic-immo.be/nl/te-koop/zoeken"


class LogicImmoScraper(BaseScraper):
    """Scrapes Logic Immo Belgium listings."""

    name = "logic_immo"

    def scrape(self) -> List[Property]:
        results: List[Property] = []
        page = 1
        while True:
            params = self._build_params(page)
            url = f"{_LOGIC_IMMO_API}?{urlencode(params, doseq=True)}"
            try:
                resp = self._get(url, headers={"Accept": "application/json"})
                data = resp.json()
            except Exception as exc:
                logger.warning("[logic_immo] Failed page %d: %s", page, exc)
                break

            items = data.get("results") or data.get("properties") or []
            if not items:
                break

            for item in items:
                prop = self._parse_item(item)
                if prop:
                    results.append(prop)

            total = data.get("total") or 0
            if page * 24 >= total:
                break
            page += 1

        logger.info("[logic_immo] Found %d listings", len(results))
        return results

    # ------------------------------------------------------------------

    def _build_params(self, page: int) -> dict:
        params: dict = {
            "transactionType": "SALE",
            "propertyType": "HOUSE",
            "maxPrice": settings.max_price,
            "minRooms": settings.min_bedrooms,
            "minGardenArea": settings.min_land_area,
            "page": page,
            "pageSize": 24,
            "orderBy": "PUBLICATION_DATE_DESC",
        }
        for pc in settings.postal_code_list:
            params.setdefault("postalCodes[]", [])
            params["postalCodes[]"].append(pc)  # type: ignore[attr-defined]
        return params

    def _parse_item(self, item: dict) -> Property | None:
        try:
            prop_id = f"logic_immo-{item.get('id') or item.get('reference')}"
            location = item.get("location") or {}
            characteristics = item.get("characteristics") or {}

            price_raw = item.get("price") or item.get("priceValue")
            price = float(price_raw) if price_raw else None

            images = [
                img if isinstance(img, str) else img.get("url", "")
                for img in item.get("images") or item.get("photos") or []
            ]

            return Property(
                id=prop_id,
                source=self.name,
                source_url=item.get("url") or f"https://www.logic-immo.be/nl/{prop_id}",
                title=item.get("title") or "Woning",
                description=item.get("description"),
                property_type=PropertyType.HOUSE,
                price=price,
                address=location.get("street"),
                postal_code=str(location.get("postalCode") or ""),
                municipality=location.get("city") or location.get("locality"),
                land_area=characteristics.get("gardenArea") or characteristics.get("landSurface"),
                living_area=characteristics.get("livingSurface") or characteristics.get("habitableSurface"),
                bedrooms=characteristics.get("bedroomCount") or characteristics.get("rooms"),
                bathrooms=characteristics.get("bathroomCount"),
                images=[i for i in images if i],
            )
        except Exception as exc:
            logger.warning("[logic_immo] Could not parse item: %s", exc)
            return None
