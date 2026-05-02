"""
Immoweb scraper agent.

Uses the Immoweb public search API (the same JSON feed that powers their website)
to retrieve listings matching our search criteria.

API endpoint (reverse-engineered from browser traffic):
  https://www.immoweb.be/en/search/house/for-sale?
      countries=BE&
      postalCodes[]=9600&…&
      maxPrice=600000&
      minBedroomsCount=3&
      minLandSurface=5000&
      orderBy=newest&
      page=1
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from agents.scrapers.base import BaseScraper
from config.settings import settings
from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

_IMMOWEB_SEARCH_URL = "https://www.immoweb.be/nl/zoeken/huis/te-koop"
_IMMOWEB_API_URL = (
    "https://www.immoweb.be/en/search/house/for-sale"
)


class ImmowebScraper(BaseScraper):
    """Scrapes Immoweb for house listings in the Vlaamse Ardennen."""

    name = "immoweb"

    # Property-type slugs that are relevant for a rural/farm search
    _PROPERTY_TYPES = ["house", "villa", "farmhouse", "country-cottage", "exceptional-property"]

    def scrape(self) -> List[Property]:
        properties: List[Property] = []
        for prop_type in self._PROPERTY_TYPES:
            properties.extend(self._scrape_type(prop_type))
        logger.info("[immoweb] Found %d listings total", len(properties))
        return properties

    # ------------------------------------------------------------------

    def _scrape_type(self, prop_type: str) -> List[Property]:
        results: List[Property] = []
        page = 1
        while True:
            params = self._build_params(prop_type, page)
            url = f"{_IMMOWEB_API_URL}?{urlencode(params, doseq=True)}"
            try:
                resp = self._get(url, headers={"Accept": "application/json"})
                data = resp.json()
            except Exception as exc:
                logger.warning("[immoweb] Failed to fetch page %d for %s: %s", page, prop_type, exc)
                break

            items = data.get("results", [])
            if not items:
                break

            for item in items:
                prop = self._parse_item(item)
                if prop:
                    results.append(prop)

            # Immoweb paginates in sets of 30; stop when last page reached
            total = data.get("total", 0)
            if page * 30 >= total:
                break
            page += 1

        return results

    def _build_params(self, prop_type: str, page: int) -> dict:
        params: dict = {
            "countries": "BE",
            "maxPrice": settings.max_price,
            "minBedroomsCount": settings.min_bedrooms,
            "minLandSurface": settings.min_land_area,
            "orderBy": "newest",
            "page": page,
            "propertyTypes[]": prop_type,
        }
        for postal_code in settings.postal_code_list:
            params.setdefault("postalCodes[]", [])
            params["postalCodes[]"].append(postal_code)  # type: ignore[attr-defined]
        return params

    def _parse_item(self, item: dict) -> Property | None:
        try:
            prop_id = f"immoweb-{item['id']}"
            cluster = item.get("property", {})
            location = cluster.get("location", {})
            building = cluster.get("building", {})
            land = cluster.get("land", {})
            transaction = item.get("transaction", {})

            price_raw = transaction.get("sale", {}).get("price")
            price = float(price_raw) if price_raw else None

            images = [
                media["url"]
                for media in item.get("media", {}).get("pictures", [])
                if media.get("url")
            ]

            return Property(
                id=prop_id,
                source=self.name,
                source_url=item.get("url") or f"https://www.immoweb.be/nl/zoekertje/{item['id']}",
                title=item.get("property", {}).get("title") or cluster.get("subtype", "Woning"),
                description=cluster.get("description"),
                property_type=self._map_type(cluster.get("subtype", "")),
                price=price,
                address=location.get("street"),
                postal_code=str(location.get("postalCode", "")),
                municipality=location.get("locality"),
                land_area=land.get("surface"),
                living_area=building.get("netHabitableSurface"),
                bedrooms=cluster.get("bedroomCount"),
                bathrooms=cluster.get("bathroomCount"),
                images=images,
                features=cluster.get("equipments", []) or [],
            )
        except Exception as exc:
            logger.warning("[immoweb] Could not parse item %s: %s", item.get("id"), exc)
            return None

    @staticmethod
    def _map_type(subtype: str) -> PropertyType:
        subtype = subtype.lower()
        if "farm" in subtype or "hoeve" in subtype or "boerderij" in subtype:
            return PropertyType.FARM
        if "villa" in subtype:
            return PropertyType.VILLA
        if "land" in subtype or "grond" in subtype:
            return PropertyType.LAND
        return PropertyType.HOUSE
