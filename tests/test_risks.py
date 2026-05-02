"""
Tests for the enhanced risk-assessment features:
  - New model fields (RiskLevel, RiskItem, extended GovernmentData)
  - GovernmentEnrichmentAgent._compile_risks()
  - AIAnalyzerAgent fallback scoring with new risk fields
"""
from __future__ import annotations

import pytest

from models.property import GovernmentData, Property, RiskItem, RiskLevel
from agents.enrichment.government import GovernmentEnrichmentAgent
from agents.enrichment.ai_analyzer import AIAnalyzerAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_property(**kwargs) -> Property:
    defaults = dict(
        id="test-risk-1",
        source="test",
        source_url="https://example.com/1",
        title="Testhoeve",
        municipality="Brakel",
        postal_code="9660",
        price=450_000,
        bedrooms=4,
        land_area=10_000,
    )
    defaults.update(kwargs)
    return Property(**defaults)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestRiskModel:
    def test_risk_level_values(self):
        assert RiskLevel.LOW == "low"
        assert RiskLevel.MEDIUM == "medium"
        assert RiskLevel.HIGH == "high"
        assert RiskLevel.UNKNOWN == "unknown"

    def test_risk_item_creation(self):
        item = RiskItem(
            name="Overstromingsrisico",
            level=RiskLevel.HIGH,
            detail="Hoog overstromingsrisico vastgesteld",
            source_url="https://www.waterinfo.be",
        )
        assert item.name == "Overstromingsrisico"
        assert item.level == RiskLevel.HIGH
        assert item.source_url == "https://www.waterinfo.be"

    def test_risk_item_no_source_url(self):
        """source_url is optional."""
        item = RiskItem(name="Test", level=RiskLevel.LOW, detail="OK")
        assert item.source_url is None

    def test_government_data_new_fields_default_none(self):
        gov = GovernmentData()
        assert gov.soil_contamination is None
        assert gov.erosion_risk is None
        assert gov.natura_2000 is None
        assert gov.ven_zone is None
        assert gov.signa_watersensitive is None
        assert gov.risks == []

    def test_government_data_risks_list(self):
        gov = GovernmentData(
            flood_risk="Hoog risico",
            risks=[
                RiskItem(name="Overstromingsrisico", level=RiskLevel.HIGH, detail="Hoog"),
            ],
        )
        assert len(gov.risks) == 1
        assert gov.risks[0].level == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# _compile_risks() tests
# ---------------------------------------------------------------------------

class TestCompileRisks:
    """Test GovernmentEnrichmentAgent._compile_risks() in isolation."""

    def _compile(self, **gov_kwargs) -> list[RiskItem]:
        gov = GovernmentData(**gov_kwargs)
        return GovernmentEnrichmentAgent._compile_risks(gov)

    def test_no_risks_returns_flood_low(self):
        """Without any risk data, flood risk is LOW."""
        risks = self._compile()
        flood = next((r for r in risks if "Overstromings" in r.name), None)
        assert flood is not None
        assert flood.level == RiskLevel.LOW

    def test_flood_risk_high(self):
        risks = self._compile(flood_risk="Hoog overstromingsrisico")
        flood = next(r for r in risks if "Overstromings" in r.name)
        assert flood.level == RiskLevel.HIGH

    def test_flood_risk_medium(self):
        risks = self._compile(flood_risk="Mogelijk overstromingsgebied")
        flood = next(r for r in risks if "Overstromings" in r.name)
        assert flood.level == RiskLevel.MEDIUM

    def test_soil_contamination_high_for_sanering(self):
        risks = self._compile(soil_contamination="saneringsgebied")
        soil = next(r for r in risks if "Bodem" in r.name)
        assert soil.level == RiskLevel.HIGH

    def test_soil_contamination_medium_for_investigatie(self):
        risks = self._compile(soil_contamination="investigatiegebied")
        soil = next(r for r in risks if "Bodem" in r.name)
        assert soil.level == RiskLevel.MEDIUM

    def test_no_soil_contamination_returns_low(self):
        risks = self._compile()
        soil = next((r for r in risks if "Bodem" in r.name), None)
        assert soil is not None
        assert soil.level == RiskLevel.LOW

    def test_ven_zone_is_high(self):
        risks = self._compile(ven_zone=True)
        ven = next(r for r in risks if "VEN" in r.name)
        assert ven.level == RiskLevel.HIGH

    def test_natura_2000_is_medium(self):
        risks = self._compile(natura_2000=True)
        sbz = next(r for r in risks if "Natura" in r.name)
        assert sbz.level == RiskLevel.MEDIUM

    def test_heritage_protection_is_medium(self):
        risks = self._compile(heritage_protected=True)
        heritage = next(r for r in risks if "erfgoed" in r.name.lower())
        assert heritage.level == RiskLevel.MEDIUM

    def test_watersensitive_is_medium(self):
        risks = self._compile(signa_watersensitive=True)
        water = next(r for r in risks if "watergevoelig" in r.name.lower())
        assert water.level == RiskLevel.MEDIUM

    def test_erosion_very_high_is_high(self):
        risks = self._compile(erosion_risk="Zeer hoog")
        erosion = next(r for r in risks if "Erosie" in r.name)
        assert erosion.level == RiskLevel.HIGH

    def test_erosion_matig_is_medium(self):
        risks = self._compile(erosion_risk="Matig")
        erosion = next(r for r in risks if "Erosie" in r.name)
        assert erosion.level == RiskLevel.MEDIUM

    def test_nature_zone_is_medium(self):
        risks = self._compile(nature_zone=True)
        nature = next((r for r in risks if "Natuur" in r.name), None)
        assert nature is not None
        assert nature.level == RiskLevel.MEDIUM

    def test_risks_sorted_high_first(self):
        """High risks must come before medium and low."""
        risks = self._compile(
            flood_risk="Hoog",
            soil_contamination="investigatiegebied",
            ven_zone=True,
        )
        levels = [r.level for r in risks]
        high_idx = [i for i, lv in enumerate(levels) if lv == RiskLevel.HIGH]
        medium_idx = [i for i, lv in enumerate(levels) if lv == RiskLevel.MEDIUM]
        low_idx = [i for i, lv in enumerate(levels) if lv == RiskLevel.LOW]
        if high_idx and medium_idx:
            assert max(high_idx) < min(medium_idx)
        if medium_idx and low_idx:
            assert max(medium_idx) < min(low_idx)

    def test_all_risks_have_source_url(self):
        """Every compiled risk item should have a source URL for transparency."""
        risks = self._compile(
            flood_risk="Mogelijk",
            soil_contamination="investigatiegebied",
            ven_zone=True,
            natura_2000=True,
            heritage_protected=True,
            signa_watersensitive=True,
            erosion_risk="Hoog",
        )
        for r in risks:
            assert r.source_url, f"Risk '{r.name}' has no source_url"


# ---------------------------------------------------------------------------
# AI analyzer fallback scoring with new risk fields
# ---------------------------------------------------------------------------

class TestAIFallbackRisks:
    def test_soil_contamination_penalises_score(self):
        agent = AIAnalyzerAgent()
        prop_clean = _make_property()
        prop_clean.government_data = GovernmentData()
        prop_dirty = _make_property()
        prop_dirty.government_data = GovernmentData(soil_contamination="saneringsgebied")

        score_clean = agent._fallback_analyze(prop_clean).score
        score_dirty = agent._fallback_analyze(prop_dirty).score
        assert score_dirty < score_clean

    def test_ven_zone_penalises_score(self):
        agent = AIAnalyzerAgent()
        prop_no_ven = _make_property()
        prop_no_ven.government_data = GovernmentData()
        prop_ven = _make_property()
        prop_ven.government_data = GovernmentData(ven_zone=True)

        assert agent._fallback_analyze(prop_ven).score < agent._fallback_analyze(prop_no_ven).score

    def test_natura_2000_penalises_score(self):
        agent = AIAnalyzerAgent()
        prop = _make_property()
        prop.government_data = GovernmentData(natura_2000=True)
        analysis = agent._fallback_analyze(prop)
        assert any("natura" in c.lower() or "sbz" in c.lower() or "passende" in c.lower()
                   for c in analysis.cons)

    def test_soil_contamination_adds_recommendation(self):
        agent = AIAnalyzerAgent()
        prop = _make_property()
        prop.government_data = GovernmentData(soil_contamination="investigatiegebied")
        analysis = agent._fallback_analyze(prop)
        assert any("bodemattest" in r.lower() or "ovam" in r.lower()
                   for r in analysis.recommendations)

    def test_score_still_clamped_with_many_penalties(self):
        agent = AIAnalyzerAgent()
        prop = _make_property()
        prop.government_data = GovernmentData(
            flood_risk="Hoog",
            soil_contamination="saneringsgebied",
            ven_zone=True,
            natura_2000=True,
            signa_watersensitive=True,
            heritage_protected=True,
        )
        analysis = agent._fallback_analyze(prop)
        assert analysis.score >= 0.0
        assert analysis.score <= 10.0
