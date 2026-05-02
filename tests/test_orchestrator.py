"""
Tests for the Orchestrator — using mocked scrapers and agents.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.property import AIAnalysis, Property, PropertyType


def _mock_property(prop_id: str = "test-1", score: float = 7.0) -> Property:
    p = Property(
        id=prop_id,
        source="test",
        source_url=f"https://example.com/{prop_id}",
        title=f"Test woning {prop_id}",
        property_type=PropertyType.HOUSE,
        price=500_000,
        bedrooms=4,
        land_area=8_000,
    )
    p.ai_analysis = AIAnalysis(
        score=score,
        summary="Test summary",
        pros=["groot perceel"],
        cons=[],
        recommendations=[],
    )
    return p


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_filter_new_empty_cache(tmp_path):
    """All properties are 'new' when there's no cache."""
    from agents.orchestrator import Orchestrator

    orch = Orchestrator()
    orch._seen_cache_path = tmp_path / "seen.json"

    props = [_mock_property("a"), _mock_property("b")]
    new = orch._filter_new(props)
    assert len(new) == 2


def test_filter_new_with_existing_cache(tmp_path):
    """Already-seen properties are filtered out."""
    from agents.orchestrator import Orchestrator

    orch = Orchestrator()
    cache_path = tmp_path / "seen.json"
    cache_path.write_text(json.dumps(["test-1"]))
    orch._seen_cache_path = cache_path

    props = [_mock_property("test-1"), _mock_property("test-2")]
    new = orch._filter_new(props)
    assert len(new) == 1
    assert new[0].id == "test-2"


def test_mark_seen_persists(tmp_path):
    """mark_seen writes IDs to disk and they're picked up next time."""
    from agents.orchestrator import Orchestrator

    orch = Orchestrator()
    orch._seen_cache_path = tmp_path / "seen.json"

    orch._mark_seen(["a", "b"])
    seen = orch._load_seen()
    assert "a" in seen
    assert "b" in seen


def test_mark_seen_is_additive(tmp_path):
    """mark_seen appends to existing cache without losing entries."""
    from agents.orchestrator import Orchestrator

    orch = Orchestrator()
    cache_path = tmp_path / "seen.json"
    cache_path.write_text(json.dumps(["a"]))
    orch._seen_cache_path = cache_path

    orch._mark_seen(["b"])
    seen = orch._load_seen()
    assert "a" in seen
    assert "b" in seen


# ---------------------------------------------------------------------------
# Score filtering
# ---------------------------------------------------------------------------

def test_run_filters_by_score(tmp_path):
    """Only properties with score >= _MIN_SCORE are notified."""
    from agents.orchestrator import Orchestrator, _MIN_SCORE

    orch = Orchestrator()
    orch._seen_cache_path = tmp_path / "seen.json"

    low_score_prop = _mock_property("low", score=_MIN_SCORE - 1)
    high_score_prop = _mock_property("high", score=_MIN_SCORE + 1)

    # Mock all sub-components
    orch._scrapers[0].scrape = MagicMock(return_value=[low_score_prop, high_score_prop])
    for s in orch._scrapers[1:]:
        s.scrape = MagicMock(return_value=[])
    orch._gov_agent.enrich = MagicMock(side_effect=lambda p: p)
    orch._ai_agent.analyze = MagicMock(side_effect=lambda p: p)  # scores already set
    orch._email_agent.send = MagicMock()

    notified = orch.run()

    assert len(notified) == 1
    assert notified[0].id == "high"
    orch._email_agent.send.assert_called_once()


def test_run_no_email_when_nothing_new(tmp_path):
    """Email agent is not called when all properties have been seen."""
    from agents.orchestrator import Orchestrator

    orch = Orchestrator()
    cache_path = tmp_path / "seen.json"
    cache_path.write_text(json.dumps(["test-1"]))
    orch._seen_cache_path = cache_path

    prop = _mock_property("test-1")
    for s in orch._scrapers:
        s.scrape = MagicMock(return_value=[prop])
    orch._email_agent.send = MagicMock()

    result = orch.run()
    assert result == []
    orch._email_agent.send.assert_not_called()
