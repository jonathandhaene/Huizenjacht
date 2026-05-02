"""
Tests for the AI analyzer enrichment agent (rule-based fallback path).
"""
from __future__ import annotations

import pytest

from models.property import GovernmentData, Property, PropertyType


def _make_property(**kwargs) -> Property:
    defaults = dict(
        id="test-1",
        source="test",
        source_url="https://example.com/1",
        title="Hoeve met weiland",
        property_type=PropertyType.FARM,
        price=490_000,
        municipality="Brakel",
        bedrooms=4,
        land_area=10_000,
        living_area=250,
        description="Prachtige hoeve met stal en schuur, B&B mogelijkheden",
    )
    defaults.update(kwargs)
    return Property(**defaults)


def test_fallback_scores_good_property():
    """A property matching all criteria gets a high score."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop = _make_property()
    analysis = agent._fallback_analyze(prop)
    assert analysis.score >= 6.0
    assert len(analysis.pros) > 0


def test_fallback_penalises_over_budget():
    """A property over budget gets a lower score."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop_over = _make_property(price=800_000)
    prop_ok = _make_property(price=400_000)

    analysis_over = agent._fallback_analyze(prop_over)
    analysis_ok = agent._fallback_analyze(prop_ok)

    assert analysis_over.score < analysis_ok.score


def test_fallback_penalises_too_few_bedrooms():
    """A property with fewer bedrooms than required scores lower."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop_few = _make_property(bedrooms=1)
    prop_enough = _make_property(bedrooms=4)

    assert agent._fallback_analyze(prop_few).score < agent._fallback_analyze(prop_enough).score


def test_fallback_agricultural_zone_bonus():
    """Agricultural zoning adds a bonus to the score."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop = _make_property()
    prop.government_data = GovernmentData(agricultural_zone=True)
    with_agri = agent._fallback_analyze(prop)

    prop2 = _make_property()
    prop2.government_data = None
    without_agri = agent._fallback_analyze(prop2)

    assert with_agri.score > without_agri.score


def test_fallback_flood_risk_penalty():
    """Flood risk adds a negative note to the analysis."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop = _make_property()
    prop.government_data = GovernmentData(flood_risk="Mogelijk overstromingsgebied")
    analysis = agent._fallback_analyze(prop)

    assert any("overstromings" in c.lower() for c in analysis.cons)


def test_analyze_attaches_to_property():
    """analyze() method attaches AIAnalysis to the property."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop = _make_property()
    result = agent.analyze(prop)
    assert result.ai_analysis is not None
    assert 0 <= result.ai_analysis.score <= 10


def test_score_is_clamped():
    """Score is always between 0 and 10."""
    from agents.enrichment.ai_analyzer import AIAnalyzerAgent

    agent = AIAnalyzerAgent()
    prop = _make_property(price=10, bedrooms=20, land_area=100_000)
    analysis = agent._fallback_analyze(prop)
    assert 0 <= analysis.score <= 10
