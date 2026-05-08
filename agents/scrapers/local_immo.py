"""
Local Vlaamse Ardennen real-estate agency scraper.

These agencies often publish only on their own site before listings propagate to
the bigger portals.  The scraper therefore visits a curated set of local
agency search pages and extracts whatever structured data or card markup is
available.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, List
from urllib.parse import urljoin, urlparse

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


@dataclass(frozen=True)
class LocalImmoSource:
    slug: str
    search_url: str


_LOCAL_SOURCES = [
    LocalImmoSource("immoroman", "https://immoroman.be/fr/a-vendre"),
    LocalImmoSource("immodhondt", "https://www.immodhondt.be/nl/te-koop"),
    LocalImmoSource("vastgoedlietaer", "https://www.vastgoedlietaer.be/nl/te-koop"),
    LocalImmoSource(
        "immofrancois_oudenaarde",
        "https://www.immofrancois.be/nl/te-koop?office=oudenaarde-velden",
    ),
    LocalImmoSource(
        "axellenaerts_vlaamse_ardennen",
        "https://www.axellenaerts.be/nl/te-koop?location=oudenaarde",
    ),
]

_LISTING_TYPES = {
    "realestatelisting",
    "offer",
    "house",
    "singlefamilyresidence",
    "residence",
    "apartment",
    "accommodation",
    "product",
}

_CARD_SELECTOR = (
    "article, "
    "div[class*='property'], div[class*='Property'], "
    "div[class*='listing'], div[class*='Listing'], "
    "div[class*='result'], div[class*='Result'], "
    "li[class*='property'], li[class*='listing']"
)

_MIN_PLAUSIBLE_LIVING_AREA = 50.0
_MAX_PLAUSIBLE_LIVING_AREA = 500.0


class LocalImmoScraper(BaseScraper):
    """Scrape local agency websites in the Vlaamse Ardennen."""

    name = "local_immo"

    def scrape(self) -> List[Property]:
        results: List[Property] = []
        seen_ids: set[str] = set()
        for source in _LOCAL_SOURCES:
            listings = self._scrape_source(source)
            logger.info("[local_immo:%s] Found %d listings", source.slug, len(listings))
            for prop in listings:
                if prop.id in seen_ids:
                    continue
                seen_ids.add(prop.id)
                results.append(prop)
        logger.info("[local_immo] Found %d listings total", len(results))
        return results

    def _scrape_source(self, source: LocalImmoSource) -> List[Property]:
        try:
            resp = self._get(source.search_url)
            props = self._parse_search_page(resp.text, source)
            if not props:
                props = [self._with_source_context(p, source) for p in self._try_ai_extract(resp.text, source.search_url)]
            return [p for p in props if self._matches_search_criteria(p)]
        except Exception as exc:
            logger.warning("[local_immo:%s] Failed to scrape %s: %s", source.slug, source.search_url, exc)
            return []

    def _parse_search_page(self, html: str, source: LocalImmoSource) -> List[Property]:
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        props = self._parse_json_ld(soup, source)
        props.extend(self._parse_cards(soup, source))
        deduped: dict[str, Property] = {}
        for prop in props:
            deduped[prop.id] = prop
        return list(deduped.values())

    def _parse_json_ld(self, soup: BeautifulSoup, source: LocalImmoSource) -> List[Property]:
        props: List[Property] = []
        for node in soup.find_all("script", type=lambda value: value and "ld+json" in value):
            raw = node.string or node.get_text()
            if not raw or not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            for candidate in self._iter_json_ld_candidates(payload):
                prop = self._property_from_json_ld(candidate, source)
                if prop:
                    props.append(prop)
        return props

    def _iter_json_ld_candidates(self, node: Any) -> Iterable[dict]:
        if isinstance(node, list):
            for item in node:
                yield from self._iter_json_ld_candidates(item)
            return
        if not isinstance(node, dict):
            return

        node_types = {str(t).lower() for t in _as_list(node.get("@type")) if t}
        if "itemlist" in node_types:
            yield from self._iter_json_ld_candidates(node.get("itemListElement"))
        if "listitem" in node_types:
            yield from self._iter_json_ld_candidates(node.get("item"))

        if self._looks_like_listing_node(node, node_types):
            yield node

        for value in node.values():
            yield from self._iter_json_ld_candidates(value)

    def _looks_like_listing_node(self, node: dict, node_types: set[str]) -> bool:
        if node_types & _LISTING_TYPES:
            return True
        if not (node.get("name") or node.get("headline")):
            return False
        if node.get("offers") and (node.get("address") or node.get("url") or node.get("image")):
            return True
        return bool(node.get("price") and (node.get("address") or node.get("url")))

    def _property_from_json_ld(self, node: dict, source: LocalImmoSource) -> Property | None:
        title = _first_text(node.get("name"), node.get("headline"))
        url = self._absolute_url(source.search_url, _first_text(node.get("url"), node.get("@id")))
        offers = _first_dict(node.get("offers"), node.get("offer"))
        address = _first_dict(node.get("address"), node.get("location"))
        description = _first_text(node.get("description"))
        address_text, postal_code, municipality = self._extract_address(address)

        text_blob = " ".join(filter(None, [title, description, address_text, municipality]))
        price = _extract_price_from_node(offers) or _extract_price_from_node(node)
        living_area = _extract_area_from_node(
            _first_dict(node.get("floorSize"), node.get("livingSize"), node.get("size"))
        )
        land_area = _extract_area_from_node(_first_dict(node.get("lotSize"), node.get("landArea")))
        bedrooms = _extract_bedrooms_from_node(node) or extract_bedrooms(text_blob)
        images = _extract_images(node.get("image"))

        if not self._looks_like_listing(url, title, price, text_blob):
            return None

        prop_type = self._property_type_from_node(node, text_blob)
        return Property(
            id=self._make_property_id(source, url, _first_text(node.get("identifier"), node.get("sku"), title)),
            source=source.slug,
            source_url=url or source.search_url,
            title=title or "Woning",
            description=description,
            property_type=prop_type,
            price=price,
            address=address_text,
            postal_code=postal_code,
            municipality=municipality,
            living_area=living_area or extract_living_area(text_blob),
            land_area=land_area or extract_land_area(text_blob),
            bedrooms=bedrooms,
            images=images,
        )

    def _parse_cards(self, soup: BeautifulSoup, source: LocalImmoSource) -> List[Property]:
        props: List[Property] = []
        for card in soup.select(_CARD_SELECTOR):
            prop = self._property_from_card(card, source)
            if prop:
                props.append(prop)
        return props

    def _property_from_card(self, card, source: LocalImmoSource) -> Property | None:
        link = self._extract_card_link(card, source)
        if not link:
            return None

        card_text = card.get_text(" ", strip=True)
        title = self._extract_card_title(card) or (link.rstrip("/").split("/")[-1].replace("-", " ").title())
        if not self._looks_like_listing(link, title, extract_price(card_text), card_text):
            return None

        price = extract_price(card_text)
        address_text = self._find_text_by_class(card, ("address", "location", "city", "municipality"))
        postal_code = _extract_postal_code(card_text)
        municipality = self._extract_municipality(address_text, card_text)
        land_area = extract_land_area(card_text)
        living_area = extract_living_area(card_text)
        if land_area is not None and living_area is not None and land_area < living_area:
            all_areas = _extract_all_area_values(card_text)
            if all_areas:
                logger.debug(
                    "[local_immo:%s] correcting swapped card areas: land=%s living=%s text=%s",
                    source.slug,
                    land_area,
                    living_area,
                    card_text[:200],
                )
                land_area = max(all_areas)
                plausible_living = [
                    area
                    for area in all_areas
                    if _MIN_PLAUSIBLE_LIVING_AREA <= area <= _MAX_PLAUSIBLE_LIVING_AREA
                ]
                if plausible_living:
                    living_area = min(plausible_living)

        images: list[str] = []
        img = card.find("img")
        if img:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy")
            if src:
                images = [self._absolute_url(source.search_url, src)]

        return Property(
            id=self._make_property_id(source, link, title),
            source=source.slug,
            source_url=link,
            title=title or "Woning",
            description=None,
            property_type=classify_property_type(f"{title} {card_text}"),
            price=price,
            address=address_text,
            postal_code=postal_code,
            municipality=municipality,
            land_area=land_area,
            living_area=living_area,
            bedrooms=extract_bedrooms(card_text),
            images=[i for i in images if i],
        )

    def _extract_card_link(self, card, source: LocalImmoSource) -> str | None:
        for tag in card.find_all("a", href=True):
            href = tag.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = self._absolute_url(source.search_url, href)
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.path.rstrip("/") == urlparse(source.search_url).path.rstrip("/"):
                continue
            return url
        return None

    def _extract_card_title(self, card) -> str:
        for tag_name in ("h1", "h2", "h3", "h4", "strong"):
            tag = card.find(tag_name)
            if tag:
                text = tag.get_text(" ", strip=True)
                if text:
                    return text
        link = card.find("a")
        return link.get_text(" ", strip=True) if link else ""

    def _find_text_by_class(self, card, class_fragments: tuple[str, ...]) -> str | None:
        for fragment in class_fragments:
            node = card.find(class_=lambda value: value and fragment in str(value).lower())
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return None

    def _property_type_from_node(self, node: dict, text_blob: str) -> PropertyType:
        node_types = {str(t).lower() for t in _as_list(node.get("@type")) if t}
        if "house" in node_types or "singlefamilyresidence" in node_types:
            return classify_property_type(text_blob)
        if "apartment" in node_types:
            # The shared PropertyType enum has no apartment-specific value.
            return PropertyType.OTHER
        return classify_property_type(text_blob)

    def _extract_address(self, address: dict | str | None) -> tuple[str | None, str | None, str | None]:
        if not address:
            return None, None, None
        if isinstance(address, str):
            text = address.strip()
            return text or None, _extract_postal_code(text), self._extract_municipality(text, "")
        street = _first_text(address.get("streetAddress"), address.get("name"))
        postal = _first_text(address.get("postalCode"), address.get("zip"))
        municipality = _first_text(address.get("addressLocality"), address.get("city"))
        pieces = [piece for piece in [street, postal, municipality] if piece]
        return ", ".join(pieces) if pieces else None, postal, municipality

    def _extract_municipality(self, preferred_text: str | None, fallback_text: str) -> str | None:
        for text in filter(None, [preferred_text, fallback_text]):
            if not text:
                continue
            postal = _extract_postal_code(text)
            if postal:
                _, _, suffix = text.partition(postal)
                remainder = suffix.lstrip(" ,-/")
                if remainder:
                    return remainder.split(" | ")[0].strip()
        return None

    def _looks_like_listing(self, url: str | None, title: str | None, price: float | None, text_blob: str) -> bool:
        if not url or not title:
            return False
        if len(text_blob.strip()) < 20:
            return False
        return bool(
            price
            or extract_bedrooms(text_blob)
            or extract_land_area(text_blob)
            or extract_living_area(text_blob)
            or _extract_postal_code(text_blob)
        )

    def _make_property_id(self, source: LocalImmoSource, url: str | None, fallback: str | None) -> str:
        seed = url or fallback or source.search_url
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return f"{source.slug}-{digest}"

    def _absolute_url(self, base_url: str, raw_url: str | None) -> str | None:
        if not raw_url:
            return None
        return urljoin(base_url, str(raw_url).strip())

    def _with_source_context(self, prop: Property, source: LocalImmoSource) -> Property:
        seed = f"{prop.source_url}|{prop.title}|{prop.id}"
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]
        return prop.model_copy(
            update={
                "id": f"{source.slug}-{digest}",
                "source": source.slug,
            }
        )

    def _matches_search_criteria(self, prop: Property) -> bool:
        if prop.price is not None and prop.price > settings.max_price:
            return False
        if prop.postal_code and prop.postal_code not in settings.postal_code_list:
            return False
        if prop.bedrooms is not None and prop.bedrooms < settings.min_bedrooms:
            return False
        if prop.land_area is not None and prop.land_area < settings.min_land_area:
            return False
        return True


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        elif isinstance(value, dict):
            text = _first_text(value.get("@id"), value.get("url"), value.get("name"))
            if text:
                return text
    return None


def _first_dict(*values: Any) -> dict | None:
    for value in values:
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    return item
    return None


def _extract_images(value: Any) -> list[str]:
    images: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            images.append(item.strip())
        elif isinstance(item, dict):
            image = _first_text(item.get("url"), item.get("@id"), item.get("contentUrl"))
            if image:
                images.append(image)
    return images


def _extract_price_from_node(node: dict | None) -> float | None:
    if not node:
        return None
    raw = node.get("price")
    if raw is None:
        price_spec = _first_dict(node.get("priceSpecification"))
        raw = price_spec.get("price") if price_spec else None
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return extract_price(str(raw))


def _extract_area_from_node(node: dict | None) -> float | None:
    if not node:
        return None
    raw = node.get("value") or node.get("maxValue") or node.get("minValue") or node.get("size")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        text = f"{raw} {node.get('unitCode') or ''}"
        return extract_land_area(text) or extract_living_area(text)


def _extract_bedrooms_from_node(node: dict) -> int | None:
    for key in ("numberOfBedrooms", "numberOfRooms"):
        value = node.get(key)
        if value is None:
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _extract_postal_code(text: str) -> str | None:
    match = re.search(r"\b(\d{4})\b", text or "")
    return match.group(1) if match else None


def _extract_all_area_values(text: str) -> list[float]:
    values: list[float] = []
    for raw in re.findall(r"(\d[\d.,\s]*)\s*m\s?[²2]", text.lower()):
        cleaned = _normalize_area_numeric(raw)
        try:
            values.append(float(cleaned))
        except ValueError:
            continue
    return values


def _normalize_area_numeric(raw: str) -> str:
    cleaned = re.sub(r"\s", "", raw)
    # Belgian-style values commonly look like 8.000 or 12.345,67:
    # - 1-2 trailing digits => decimal suffix ("8,5" -> "8.5")
    # - otherwise separators are thousands separators ("8.000" -> "8000")
    decimal_match = re.search(r"([,.])(\d{1,2})$", cleaned)
    if decimal_match:
        integer_part = re.sub(r"[,.]", "", cleaned[: decimal_match.start()])
        return f"{integer_part}.{decimal_match.group(2)}"
    return re.sub(r"[,.]", "", cleaned)
