"""
Immoweb scraper agent.

Immoweb's public website (www.immoweb.be) is protected by Cloudflare which
returns HTTP 403 for plain `httpx` requests and serves a regular HTML search
page (not a JSON feed) once unblocked.  We therefore:

1. Fetch the HTML search page through `BaseScraper._get` — that helper
   transparently escalates from `httpx` → `cloudscraper` → Playwright on
   bot-detection errors.
2. Look for an embedded `<script id="__NEXT_DATA__">` blob (Next.js apps
   ship the full server state inside it).  When present this gives us
   richly-typed listing data without further requests.
3. Fall back to parsing the visible listing cards (`a.card__title-link`)
   when the Next.js blob is not available — for example when the page is
   server-rendered or when Cloudflare returns a stripped-down version.

The canonical classified URL is

    https://www.immoweb.be/<lang>/classified/<type>/for-sale/<locality>/<postal>/<id>

so we can always recover an id, postal code and locality even when the rest
of the card cannot be parsed.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urljoin

from bs4 import BeautifulSoup

from agents.scrapers.base import BaseScraper
from config.settings import settings
from models.property import Property, PropertyType

logger = logging.getLogger(__name__)

_IMMOWEB_BASE = "https://www.immoweb.be"
_IMMOWEB_SEARCH_URL = f"{_IMMOWEB_BASE}/en/search/house/for-sale"

# Matches /<lang>/classified/<subtype>/for-sale/<locality>/<postal>/<id>
_CLASSIFIED_RE = re.compile(
    r"/(?P<lang>[a-z]{2})/classified/(?P<subtype>[^/]+)/for-sale/"
    r"(?P<locality>[^/]+)/(?P<postal>\d{4})/(?P<id>\d+)"
)


class ImmowebScraper(BaseScraper):
    """Scrapes Immoweb for house listings in the Vlaamse Ardennen."""

    name = "immoweb"

    # Property-type slugs that are relevant for a rural/farm search
    _PROPERTY_TYPES = ["house", "villa", "farmhouse", "country-cottage", "exceptional-property"]

    # Hard cap to avoid runaway pagination on a malformed response
    _MAX_PAGES = 25

    def scrape(self) -> List[Property]:
        properties: List[Property] = []
        seen_ids: set[str] = set()
        for prop_type in self._PROPERTY_TYPES:
            for prop in self._scrape_type(prop_type):
                if prop.id in seen_ids:
                    continue
                seen_ids.add(prop.id)
                properties.append(prop)
        logger.info("[immoweb] Found %d unique listings total", len(properties))
        return properties

    # ------------------------------------------------------------------
    # Per-type pagination
    # ------------------------------------------------------------------

    def _scrape_type(self, prop_type: str) -> List[Property]:
        results: List[Property] = []
        page = 1
        while page <= self._MAX_PAGES:
            params = self._build_params(prop_type, page)
            url = f"{_IMMOWEB_SEARCH_URL}?{urlencode(params, doseq=True)}"
            try:
                resp = self._get(url)
                page_props = self._parse_search_page(resp.text)
            except Exception as exc:
                logger.warning(
                    "[immoweb] Failed to fetch page %d for %s: %s",
                    page,
                    prop_type,
                    exc,
                )
                break

            if not page_props:
                # Either we're past the last page or the page changed shape.
                break

            results.extend(page_props)

            # Stop once a page returns fewer than a typical "full" page worth
            # of cards — Immoweb shows 30 per page.
            if len(page_props) < 30:
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

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    def _parse_search_page(self, html: str) -> List[Property]:
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")

        # Preferred path: embedded Next.js state.
        next_props = self._parse_next_data(soup)
        if next_props:
            return next_props

        # Fallback: visible card markup.
        return self._parse_cards(soup)

    # -- __NEXT_DATA__ ---------------------------------------------------

    def _parse_next_data(self, soup: BeautifulSoup) -> List[Property]:
        node = soup.find("script", id="__NEXT_DATA__")
        if not node or not node.string:
            return []
        try:
            payload = json.loads(node.string)
        except (ValueError, TypeError) as exc:
            logger.debug("[immoweb] __NEXT_DATA__ was not valid JSON: %s", exc)
            return []

        items = self._extract_results_from_state(payload)
        results: List[Property] = []
        for item in items:
            prop = self._parse_state_item(item)
            if prop:
                results.append(prop)
        return results

    @staticmethod
    def _extract_results_from_state(payload: Any) -> List[dict]:
        """
        The Next.js page state nests the search results under several
        possible paths depending on Immoweb's current frontend version.
        Walk the structure defensively and return the first list of
        listing-shaped dicts we find.
        """
        candidates: List[dict] = []

        def _walk(node: Any, depth: int = 0) -> None:
            if depth > 8 or candidates:
                return
            if isinstance(node, dict):
                # Direct hit on the canonical key.
                results = node.get("results") if "results" in node else None
                if isinstance(results, list) and results and isinstance(results[0], dict):
                    # Heuristic: results items have an "id" or "property" field.
                    sample = results[0]
                    if "id" in sample or "property" in sample:
                        candidates.extend(results)
                        return
                for value in node.values():
                    _walk(value, depth + 1)
            elif isinstance(node, list):
                for value in node:
                    _walk(value, depth + 1)

        _walk(payload)
        return candidates

    def _parse_state_item(self, item: dict) -> Optional[Property]:
        try:
            raw_id = item.get("id") or item.get("classifiedId")
            if not raw_id:
                return None
            prop_id = f"immoweb-{raw_id}"
            cluster = item.get("property") or {}
            location = cluster.get("location") or {}
            building = cluster.get("building") or {}
            land = cluster.get("land") or {}
            transaction = item.get("transaction") or {}

            price_raw = (
                (transaction.get("sale") or {}).get("price")
                or cluster.get("price")
                or item.get("price")
            )
            price = float(price_raw) if price_raw else None

            images_raw = (
                (item.get("media") or {}).get("pictures")
                or cluster.get("pictures")
                or []
            )
            images = [
                m.get("url") if isinstance(m, dict) else str(m)
                for m in images_raw
                if (isinstance(m, dict) and m.get("url")) or isinstance(m, str)
            ]

            url = item.get("url")
            if not url:
                # Reconstruct from the canonical pattern when missing.
                lang = "en"
                subtype = (cluster.get("subtype") or "house").lower()
                locality = (location.get("locality") or "unknown").lower().replace(" ", "-")
                postal = location.get("postalCode") or ""
                url = f"{_IMMOWEB_BASE}/{lang}/classified/{subtype}/for-sale/{locality}/{postal}/{raw_id}"
            elif url.startswith("/"):
                url = urljoin(_IMMOWEB_BASE, url)

            return Property(
                id=prop_id,
                source=self.name,
                source_url=url,
                title=cluster.get("title") or cluster.get("subtype") or "Woning",
                description=cluster.get("description"),
                property_type=self._map_type(cluster.get("subtype", "")),
                price=price,
                address=location.get("street"),
                postal_code=str(location.get("postalCode") or ""),
                municipality=location.get("locality"),
                land_area=land.get("surface"),
                living_area=building.get("netHabitableSurface"),
                bedrooms=cluster.get("bedroomCount"),
                bathrooms=cluster.get("bathroomCount"),
                images=[i for i in images if i],
                features=cluster.get("equipments") or [],
            )
        except Exception as exc:
            logger.warning("[immoweb] Could not parse __NEXT_DATA__ item %s: %s", item.get("id"), exc)
            return None

    # -- Card markup -----------------------------------------------------

    def _parse_cards(self, soup: BeautifulSoup) -> List[Property]:
        """
        Parse the visible listing cards.  Selectors are intentionally broad
        — Immoweb regularly tweaks class names but always exposes a
        ``card__title-link`` anchor pointing at the canonical classified URL.
        """
        results: List[Property] = []
        seen: set[str] = set()

        for anchor in soup.select("a.card__title-link, a[class*='card__title']"):
            href = anchor.get("href") or ""
            if not href:
                continue
            full_url = urljoin(_IMMOWEB_BASE, href)
            match = _CLASSIFIED_RE.search(full_url)
            if not match:
                continue
            raw_id = match.group("id")
            if raw_id in seen:
                continue
            seen.add(raw_id)

            card = anchor.find_parent(
                lambda tag: tag.name in {"article", "li", "div"}
                and any("card" in c for c in (tag.get("class") or []))
            )

            prop = self._parse_card(anchor, card, full_url, match)
            if prop:
                results.append(prop)

        return results

    def _parse_card(
        self,
        anchor,
        card,
        full_url: str,
        match: re.Match,
    ) -> Optional[Property]:
        try:
            raw_id = match.group("id")
            postal = match.group("postal")
            locality = match.group("locality").replace("-", " ").title()
            subtype = match.group("subtype")

            title = anchor.get_text(strip=True) or f"{subtype.title()} te koop"

            scope = card or anchor.parent

            price = self._extract_price(scope)
            bedrooms = self._extract_int(scope, ["bedroom", "slaapkamer", "chambre"])
            living = self._extract_int(scope, ["habitable", "bewoonbare", "habitable surface"])
            land = self._extract_int(scope, ["land", "surface du terrain", "perceel"])

            image = None
            img_tag = scope.find("img") if scope else None
            if img_tag:
                image = img_tag.get("src") or img_tag.get("data-src")

            return Property(
                id=f"immoweb-{raw_id}",
                source=self.name,
                source_url=full_url,
                title=title,
                property_type=self._map_type(subtype),
                price=price,
                postal_code=postal,
                municipality=locality,
                land_area=land,
                living_area=living,
                bedrooms=bedrooms,
                images=[image] if image else [],
            )
        except Exception as exc:
            logger.warning("[immoweb] Could not parse card %s: %s", full_url, exc)
            return None

    # ------------------------------------------------------------------
    # Field extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(scope) -> Optional[float]:
        if not scope:
            return None
        price_el = scope.find(
            lambda tag: any("price" in c.lower() for c in (tag.get("class") or []))
        )
        if not price_el:
            return None
        text = price_el.get_text(" ", strip=True)
        # Capture the longest digit run — handles "€ 450 000", "€450,000", etc.
        digits = re.findall(r"\d[\d.,\s]*", text)
        if not digits:
            return None
        cleaned = re.sub(r"[^0-9]", "", max(digits, key=len))
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None

    @staticmethod
    def _extract_int(scope, keywords: List[str]) -> Optional[int]:
        if not scope:
            return None
        text = scope.get_text(" ", strip=True).lower()
        for kw in keywords:
            m = re.search(r"(\d[\d\s.,]*)\s*[^\s]*\s*" + re.escape(kw), text)
            if m:
                cleaned = re.sub(r"[^0-9]", "", m.group(1))
                if cleaned:
                    try:
                        return int(cleaned)
                    except ValueError:
                        continue
        return None

    @staticmethod
    def _map_type(subtype: str) -> PropertyType:
        subtype = (subtype or "").lower()
        if any(k in subtype for k in ("farm", "hoeve", "boerderij", "country-cottage", "country_cottage")):
            return PropertyType.FARM
        if "villa" in subtype or "exceptional" in subtype:
            return PropertyType.VILLA
        if "land" in subtype or "grond" in subtype:
            return PropertyType.LAND
        return PropertyType.HOUSE
