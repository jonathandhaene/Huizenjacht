"""
Tests for the Property data model.
"""
from datetime import datetime

import pytest

from models.property import (
    AIAnalysis,
    GovernmentData,
    Property,
    PropertyType,
)


def test_property_minimal():
    """A Property can be created with only required fields."""
    p = Property(
        id="test-1",
        source="test",
        source_url="https://example.com/1",
        title="Test woning",
    )
    assert p.id == "test-1"
    assert p.source == "test"
    assert p.property_type == PropertyType.HOUSE
    assert isinstance(p.first_seen, datetime)
    assert p.images == []
    assert p.features == []


def test_property_full():
    """A fully populated Property round-trips correctly."""
    gov = GovernmentData(
        zoning="Agrarisch gebied",
        agricultural_zone=True,
        animal_keeping_allowed=True,
        bnb_possible=True,
        flood_risk=None,
        heritage_protected=False,
    )
    analysis = AIAnalysis(
        score=8.5,
        summary="Uitstekend perceel",
        pros=["groot perceel"],
        cons=[],
        recommendations=["Vraag RUP-attest op"],
    )
    p = Property(
        id="immoweb-123",
        source="immoweb",
        source_url="https://www.immoweb.be/nl/zoekertje/123",
        title="Hoeve met weiland",
        property_type=PropertyType.FARM,
        price=480_000,
        address="Dorpsstraat 1",
        postal_code="9660",
        municipality="Brakel",
        land_area=12_000,
        living_area=250,
        bedrooms=4,
        bathrooms=2,
        images=["https://example.com/img1.jpg"],
        features=["stal", "schuur"],
        government_data=gov,
        ai_analysis=analysis,
    )
    assert p.price == 480_000
    assert p.government_data.agricultural_zone is True
    assert p.ai_analysis.score == 8.5


def test_property_type_enum():
    """PropertyType values are accessible as strings (use_enum_values=True)."""
    p = Property(
        id="x",
        source="test",
        source_url="https://example.com",
        title="Test",
        property_type=PropertyType.FARM,
    )
    assert p.property_type == "farm"


def test_ai_analysis_score_bounds():
    """AIAnalysis score must be between 0 and 10."""
    with pytest.raises(Exception):
        AIAnalysis(score=11, summary="too high", pros=[], cons=[], recommendations=[])
    with pytest.raises(Exception):
        AIAnalysis(score=-1, summary="too low", pros=[], cons=[], recommendations=[])


def test_government_data_defaults():
    gov = GovernmentData()
    assert gov.zoning is None
    assert gov.agricultural_zone is None
    assert gov.flood_risk is None
