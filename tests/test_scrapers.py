"""
Tests for the scraper agents using mocked HTTP responses.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from models.property import Property, PropertyType


# ---------------------------------------------------------------------------
# Immoweb scraper — HTML parsing
# ---------------------------------------------------------------------------

# Minimal `__NEXT_DATA__` payload that mimics the Next.js state shipped on the
# real Immoweb search page.  We only encode the fields the parser actually
# reads.
_IMMOWEB_NEXT_STATE = {
    "props": {
        "pageProps": {
            "dehydratedState": {
                "queries": [
                    {
                        "state": {
                            "data": {
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
                                            "equipments": ["stal", "schuur"],
                                        },
                                        "transaction": {"sale": {"price": 480000}},
                                        "media": {
                                            "pictures": [
                                                {"url": "https://img.immoweb.be/1.jpg"}
                                            ]
                                        },
                                    }
                                ],
                                "total": 1,
                            }
                        }
                    }
                ]
            }
        }
    }
}


_IMMOWEB_HTML_WITH_NEXT_DATA = (
    "<!doctype html><html><body>"
    '<script id="__NEXT_DATA__" type="application/json">'
    + json.dumps(_IMMOWEB_NEXT_STATE)
    + "</script></body></html>"
)

# Card-style fallback HTML (no __NEXT_DATA__ blob) — Immoweb sometimes serves
# this stripped-down variant for bot-suspicious clients.
_IMMOWEB_HTML_CARDS = """
<!doctype html><html><body>
<article class="card card--result">
  <a class="card__title-link"
     href="/en/classified/farmhouse/for-sale/brakel/9660/12345">
    Hoeve met weiland — 4 bedrooms
  </a>
  <p class="card--result__price">€ 480.000</p>
  <img src="https://img.immoweb.be/1.jpg" />
</article>
<article class="card card--result">
  <a class="card__title-link"
     href="/en/classified/villa/for-sale/oudenaarde/9700/67890">
    Villa met tuin
  </a>
  <p class="card__price">€ 525,000</p>
</article>
</body></html>
"""


def _mk_response(text: str):
    resp = MagicMock()
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def test_immoweb_scraper_parse_next_data():
    """ImmowebScraper extracts listings from the embedded __NEXT_DATA__ blob."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()

    with patch.object(scraper, "_get", return_value=_mk_response(_IMMOWEB_HTML_WITH_NEXT_DATA)):
        props = scraper._scrape_type("farmhouse")

    assert len(props) == 1
    p = props[0]
    assert p.id == "immoweb-12345"
    assert p.source == "immoweb"
    assert p.price == 480_000
    assert p.bedrooms == 4
    assert p.land_area == 12_000
    assert p.property_type == PropertyType.FARM
    assert "https://img.immoweb.be/1.jpg" in p.images


def test_immoweb_scraper_parse_cards_fallback():
    """When __NEXT_DATA__ is missing, ImmowebScraper falls back to card markup."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()

    with patch.object(scraper, "_get", return_value=_mk_response(_IMMOWEB_HTML_CARDS)):
        props = scraper._scrape_type("house")

    ids = sorted(p.id for p in props)
    assert ids == ["immoweb-12345", "immoweb-67890"]

    farm = next(p for p in props if p.id == "immoweb-12345")
    assert farm.postal_code == "9660"
    assert farm.municipality == "Brakel"
    assert farm.price == 480_000
    assert farm.property_type == PropertyType.FARM
    assert farm.images == ["https://img.immoweb.be/1.jpg"]

    villa = next(p for p in props if p.id == "immoweb-67890")
    assert villa.postal_code == "9700"
    assert villa.property_type == PropertyType.VILLA
    assert villa.price == 525_000


def test_immoweb_scraper_handles_empty_response():
    """ImmowebScraper returns empty list when the page has no listings."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()
    with patch.object(scraper, "_get", return_value=_mk_response("<html><body></body></html>")):
        props = scraper._scrape_type("house")

    assert props == []


def test_immoweb_scraper_handles_http_error():
    """ImmowebScraper returns empty list when HTTP request fails after retries."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()
    with patch.object(scraper, "_get", side_effect=Exception("Connection error")):
        props = scraper._scrape_type("house")

    assert props == []


def test_immoweb_scraper_dedupes_across_property_types():
    """The same classified id appearing under multiple subtype searches yields one Property."""
    from agents.scrapers.immoweb import ImmowebScraper

    scraper = ImmowebScraper()

    with patch.object(scraper, "_get", return_value=_mk_response(_IMMOWEB_HTML_WITH_NEXT_DATA)):
        props = scraper.scrape()

    assert len(props) == 1
    assert props[0].id == "immoweb-12345"


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


# ---------------------------------------------------------------------------
# Playwright fallback (_PlaywrightResponse + _get fallback logic)
# ---------------------------------------------------------------------------

def test_playwright_response_json():
    """_PlaywrightResponse.json() returns pre-parsed data when available."""
    from agents.scrapers.base import _PlaywrightResponse

    resp = _PlaywrightResponse(json_data={"results": [1, 2]})
    assert resp.json() == {"results": [1, 2]}
    resp.raise_for_status()  # should not raise


def test_playwright_response_text_json():
    """_PlaywrightResponse.json() parses .text when no pre-parsed data given."""
    from agents.scrapers.base import _PlaywrightResponse

    resp = _PlaywrightResponse(text='{"key": "value"}')
    assert resp.json() == {"key": "value"}
    assert resp.text == '{"key": "value"}'


def test_get_uses_cloudscraper_then_playwright_on_http_error():
    """HttpClient._get() escalates httpx → cloudscraper → Playwright."""
    from agents.scrapers.base import HttpClient, _PlaywrightResponse

    client = HttpClient()
    fake_pw_response = _PlaywrightResponse(text="<html></html>")

    with patch.object(client, "_get_http", side_effect=Exception("403 Forbidden")):
        with patch.object(client, "_get_cloudscraper", side_effect=Exception("cf challenge")) as mock_cs:
            with patch.object(client, "_get_playwright", return_value=fake_pw_response) as mock_pw:
                result = client._get("https://example.com")

    mock_cs.assert_called_once()
    mock_pw.assert_called_once()
    assert result is fake_pw_response


def test_get_uses_cloudscraper_when_httpx_fails():
    """HttpClient._get() returns the cloudscraper response when cloudscraper succeeds."""
    from agents.scrapers.base import HttpClient, _PlaywrightResponse

    client = HttpClient()
    fake_cs_response = _PlaywrightResponse(text="<html>cloud</html>")

    with patch.object(client, "_get_http", side_effect=Exception("403 Forbidden")):
        with patch.object(client, "_get_cloudscraper", return_value=fake_cs_response) as mock_cs:
            with patch.object(client, "_get_playwright") as mock_pw:
                result = client._get("https://example.com")

    mock_cs.assert_called_once()
    mock_pw.assert_not_called()
    assert result is fake_cs_response


def test_get_returns_http_response_when_successful():
    """HttpClient._get() returns the httpx response when HTTP succeeds."""
    from agents.scrapers.base import HttpClient

    client = HttpClient()
    mock_resp = MagicMock()

    with patch.object(client, "_get_http", return_value=mock_resp) as mock_http:
        with patch.object(client, "_get_cloudscraper") as mock_cs:
            with patch.object(client, "_get_playwright") as mock_pw:
                result = client._get("https://example.com")

    mock_http.assert_called_once()
    mock_cs.assert_not_called()
    mock_pw.assert_not_called()
    assert result is mock_resp


def test_immoweb_scraper_uses_playwright_fallback():
    """ImmowebScraper returns listings when HTTP/cloudscraper fail but Playwright succeeds."""
    from agents.scrapers.immoweb import ImmowebScraper
    from agents.scrapers.base import _PlaywrightResponse

    scraper = ImmowebScraper()
    pw_resp = _PlaywrightResponse(text=_IMMOWEB_HTML_WITH_NEXT_DATA)

    with patch.object(scraper, "_get_http", side_effect=Exception("403")):
        with patch.object(scraper, "_get_cloudscraper", side_effect=Exception("cf")):
            with patch.object(scraper, "_get_playwright", return_value=pw_resp):
                props = scraper._scrape_type("farmhouse")

    assert len(props) == 1
    assert props[0].id == "immoweb-12345"

