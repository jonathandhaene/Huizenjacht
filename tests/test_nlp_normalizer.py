"""
Tests for agents.scrapers.nlp_normalizer.
"""
from __future__ import annotations

import pytest

from agents.scrapers.nlp_normalizer import (
    classify_property_type,
    deduplicate_properties,
    extract_bedrooms,
    extract_land_area,
    extract_living_area,
    extract_price,
)
from models.property import Property, PropertyType


# ── extract_bedrooms ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Ruime woning met 4 slaapkamers en grote tuin", 4),
    ("3 bedrooms house with garden", 3),
    ("Maison avec 5 chambres", 5),
    ("slaapkamers: 2", 2),
    ("bedrooms: 3", 3),
    ("2 slpk.", 2),
    ("No bedroom info here", None),
    ("1 br appartement", 1),
    ("4 bed house", 4),
])
def test_extract_bedrooms(text, expected):
    assert extract_bedrooms(text) == expected


def test_extract_bedrooms_ignores_implausible_values():
    """Values >= 20 are rejected as implausible for real-estate listings."""
    assert extract_bedrooms("25 slaapkamers") is None


# ── extract_price ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Vraagprijs: € 450.000", 450_000.0),
    ("450,000 €", 450_000.0),
    ("450 000 EUR", 450_000.0),
    ("€450000", 450_000.0),
    ("€ 525,000", 525_000.0),
    ("Budget: 600K", 600_000.0),
    ("geen prijs vermeld", None),
    # Sanity check: values ≤ 1000 are not prices
    ("€ 500", None),
])
def test_extract_price(text, expected):
    result = extract_price(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, rel=0.01)


def test_extract_price_handles_spaces_as_thousands_separator():
    assert extract_price("480 000 €") == pytest.approx(480_000.0, rel=0.01)


# ── extract_land_area ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Perceel: 12.000 m²", 12_000.0),
    ("groot perceel van 8000 m2", 8_000.0),
    ("terrain: 1,5 ha", 15_000.0),
    # Generic fallback — largest area found
    ("Woonoppervlakte 180 m² op perceel van 6000 m²", 6_000.0),
])
def test_extract_land_area(text, expected):
    result = extract_land_area(text)
    assert result == pytest.approx(expected, rel=0.01)


def test_extract_land_area_returns_none_when_absent():
    assert extract_land_area("Geen informatie beschikbaar") is None


# ── extract_living_area ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Bewoonbare oppervlakte: 250 m²", 250.0),
    ("surface habitable: 180 m2", 180.0),
    ("living area: 200 m²", 200.0),
])
def test_extract_living_area(text, expected):
    result = extract_living_area(text)
    assert result == pytest.approx(expected, rel=0.01)


def test_extract_living_area_returns_none_when_absent():
    assert extract_living_area("Geen woonoppervlakte info") is None


# ── classify_property_type ────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Prachtige hoeve met weiland en stal", PropertyType.FARM),
    ("Boerderij te koop in Brakel", PropertyType.FARM),
    ("Luxueuze villa met zwembad", PropertyType.VILLA),
    ("Bouwgrond te koop", PropertyType.LAND),
    ("Moderne woning in Oudenaarde", PropertyType.HOUSE),
    ("", PropertyType.HOUSE),
])
def test_classify_property_type(text, expected):
    assert classify_property_type(text) == expected


# ── deduplicate_properties ────────────────────────────────────────────────────

def _make_prop(
    prop_id: str,
    source: str,
    postal: str = "9660",
    price: float = 450_000,
    address: str = "Dorpsstraat 1",
    title: str = "Hoeve met weiland",
) -> Property:
    return Property(
        id=prop_id,
        source=source,
        source_url=f"https://example.com/{prop_id}",
        title=title,
        price=price,
        postal_code=postal,
        address=address,
    )


def test_deduplicate_removes_cross_source_duplicate():
    """Same listing on two different sources → keep only the first."""
    a = _make_prop("iw-1", "immoweb")
    b = _make_prop("z-1", "zimmo")  # Same postal + price + address

    result = deduplicate_properties([a, b])
    assert len(result) == 1
    assert result[0].id == "iw-1"


def test_deduplicate_keeps_different_prices():
    """Properties with different prices at the same address are not duplicates."""
    a = _make_prop("iw-1", "immoweb", price=450_000)
    b = _make_prop("z-1", "zimmo", price=500_000)

    result = deduplicate_properties([a, b])
    assert len(result) == 2


def test_deduplicate_keeps_different_postal_codes():
    """Properties in different postal codes are never merged."""
    a = _make_prop("iw-1", "immoweb", postal="9660")
    b = _make_prop("z-1", "zimmo", postal="9700")

    result = deduplicate_properties([a, b])
    assert len(result) == 2


def test_deduplicate_does_not_merge_same_source():
    """Two entries from the same source are never considered duplicates."""
    a = _make_prop("iw-1", "immoweb")
    b = _make_prop("iw-2", "immoweb")

    result = deduplicate_properties([a, b])
    assert len(result) == 2


def test_deduplicate_accepts_1pct_price_difference():
    """Prices within 1 % are treated as the same for cross-source matching."""
    a = _make_prop("iw-1", "immoweb", price=450_000)
    b = _make_prop("z-1", "zimmo", price=450_000 * 1.005)  # 0.5 % diff

    result = deduplicate_properties([a, b])
    assert len(result) == 1


def test_deduplicate_preserves_order():
    """The first occurrence is kept, not the last.

    All properties share the same postal code, price and address, but have
    different sources — only the first (p0/source0) should survive since each
    subsequent property is a cross-source duplicate of it.
    """
    props = [
        _make_prop(f"p{i}", f"source{i}") for i in range(5)
    ]
    # All have the same postal + price + address, different sources —
    # only the first should survive.
    result = deduplicate_properties(props)
    assert result[0].id == "p0"


def test_deduplicate_empty_list():
    assert deduplicate_properties([]) == []


def test_deduplicate_single_item():
    p = _make_prop("a", "immoweb")
    assert deduplicate_properties([p]) == [p]
