"""
Tests for the scraper agents using mocked HTTP responses.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from models.property import Property, PropertyType


# ---------------------------------------------------------------------------
# Immoweb scraper
# ---------------------------------------------------------------------------

IMMOWEB_RESPONSE = {
    "results": [
        {
            "id": 12345,
            "url": "https://www.immoweb.be/nl/zoekertje/12345",
            "property": {
                "title": "Hoeve met weiland",
                "subtype": "FARMHOUSE",
                "description": "Prachtige hoeve op groot perceel",
                "bedroomCount": 4,
                "bathroomCount": 2,
                "location": {
                    "street": "Dorpsstraat 1",
                    "postalCode": 9660,
                    "locality": "Brakel",
                },
                "building": {"netHabitableSurface": 280},
                "land": {"surface": 12000},
            },
            "transaction": {"sale": {"price": 480000}},
            "media": {"pictures": [{"url": "https://img.immoweb.be/1.jpg"}]},
        }
    ],
    "total": 1,
}


def test_immoweb_scraper_parse():
    """ImmowebScraper correctly parses a well-formed API response."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()

    mock_resp = MagicMock()
    mock_resp.json.return_value = IMMOWEB_RESPONSE
    mock_resp.raise_for_status.return_value = None

    with patch.object(scraper, "_get", return_value=mock_resp):
        props = scraper._scrape_type("farmhouse")

    assert len(props) == 1
    p = props[0]
    assert p.id == "immoweb-12345"
    assert p.source == "immoweb"
    assert p.price == 480_000
    assert p.bedrooms == 4
    assert p.land_area == 12_000
    assert p.property_type == "farm"
    assert "https://img.immoweb.be/1.jpg" in p.images


def test_immoweb_scraper_handles_empty_response():
    """ImmowebScraper returns empty list when API returns no results."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"results": [], "total": 0}

    with patch.object(scraper, "_get", return_value=mock_resp):
        props = scraper._scrape_type("house")

    assert props == []


def test_immoweb_scraper_handles_http_error():
    """ImmowebScraper returns empty list when HTTP request fails after retries."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()
    with patch.object(scraper, "_get", side_effect=Exception("Connection error")):
        props = scraper._scrape_type("house")

    assert props == []


# ---------------------------------------------------------------------------
# Zimmo scraper
# ---------------------------------------------------------------------------

ZIMMO_RESPONSE = {
    "properties": [
        {
            "id": "Z99999",
            "url": "https://www.zimmo.be/nl/Z99999",
            "title": "Landelijke woning",
            "price": 550000,
            "location": {"postalCode": "9700", "city": "Oudenaarde", "street": "Kerkweg 5"},
            "details": {
                "livingSurface": 200,
                "plotSurface": 8000,
                "bedrooms": 3,
                "bathrooms": 1,
            },
            "images": ["https://img.zimmo.be/1.jpg"],
        }
    ],
    "totalPages": 1,
}


def test_zimmo_scraper_parse():
    """ZimmoScraper correctly parses a well-formed response."""
    from agents.scrapers.zimmo import ZimmoScraper

    scraper = ZimmoScraper()
    mock_resp = MagicMock()
    mock_resp.json.return_value = ZIMMO_RESPONSE

    with patch.object(scraper, "_get", return_value=mock_resp):
        props = scraper.scrape()

    assert len(props) == 1
    p = props[0]
    assert p.id == "zimmo-Z99999"
    assert p.price == 550_000
    assert p.land_area == 8_000
    assert p.municipality == "Oudenaarde"


def test_zimmo_scraper_handles_error():
    from agents.scrapers.zimmo import ZimmoScraper

    scraper = ZimmoScraper()
    with patch.object(scraper, "_get", side_effect=Exception("timeout")):
        props = scraper.scrape()
    assert props == []


# ---------------------------------------------------------------------------
# Social media scraper
# ---------------------------------------------------------------------------

def test_social_media_is_relevant():
    from agents.scrapers.social_media import SocialMediaScraper

    assert SocialMediaScraper._is_relevant("Te koop: hoeve met weiland en schuur") is True
    assert SocialMediaScraper._is_relevant("Gevonden: hond in Gent") is False


def test_social_media_extract_price():
    from agents.scrapers.social_media import SocialMediaScraper

    price = SocialMediaScraper._extract_price("Vraagprijs: € 450.000")
    assert price == 450_000.0

    price2 = SocialMediaScraper._extract_price("geen prijs vermeld")
    assert price2 is None


def test_social_media_scraper_returns_list_on_failure():
    """SocialMediaScraper never raises — returns empty list on any failure."""
    from agents.scrapers.social_media import SocialMediaScraper

    scraper = SocialMediaScraper()
    with patch.object(scraper, "_get", side_effect=Exception("blocked")):
        props = scraper.scrape()
    assert isinstance(props, list)
