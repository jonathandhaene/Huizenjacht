"""
Integration tests for the GitHub Pages pipeline script.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from models.property import AIAnalysis, Property, PropertyType


def _mock_prop(prop_id: str = "test-1", score: float = 7.0) -> Property:
    p = Property(
        id=prop_id,
        source="test",
        source_url=f"https://example.com/{prop_id}",
        title=f"Test woning {prop_id}",
        property_type=PropertyType.HOUSE,
        price=500_000,
        municipality="Brakel",
        bedrooms=4,
        land_area=8_000,
    )
    p.ai_analysis = AIAnalysis(
        score=score,
        summary="Test",
        pros=["groot perceel"],
        cons=[],
        recommendations=[],
    )
    return p


# ---------------------------------------------------------------------------
# _load_properties / _save_properties
# ---------------------------------------------------------------------------

def test_load_properties_missing_file(tmp_path, monkeypatch):
    import scripts.github_pages_pipeline as pipeline

    monkeypatch.setattr(pipeline, "_PROPERTIES_FILE", tmp_path / "props.json")
    result = pipeline._load_properties()
    assert result == []


def test_save_and_load_properties(tmp_path, monkeypatch):
    import scripts.github_pages_pipeline as pipeline

    monkeypatch.setattr(pipeline, "_PROPERTIES_FILE", tmp_path / "props.json")
    data = [{"id": "x", "title": "Test", "price": 500000}]
    pipeline._save_properties(data)
    loaded = pipeline._load_properties()
    assert loaded == data


def test_property_to_dict():
    import scripts.github_pages_pipeline as pipeline

    prop = _mock_prop()
    d = pipeline._property_to_dict(prop)
    assert d["id"] == "test-1"
    assert d["price"] == 500_000
    assert isinstance(d["first_seen"], str)  # serialised to string


# ---------------------------------------------------------------------------
# Match detection
# ---------------------------------------------------------------------------

def test_no_match_with_single_like(tmp_path, monkeypatch):
    import scripts.github_pages_pipeline as pipeline

    monkeypatch.setattr(pipeline, "_LIKES_FILE", tmp_path / "likes.json")
    monkeypatch.setattr(pipeline, "_MATCHES_NOTIFIED_FILE", tmp_path / "notified.json")
    monkeypatch.setattr(pipeline, "_DATA_DIR", tmp_path)

    likes = {"test-1": {"Jonathan": "2024-05-01T09:00:00Z"}}
    (tmp_path / "likes.json").write_text(json.dumps(likes))

    sent_emails = []
    monkeypatch.setattr(pipeline, "_send_match_email", lambda *a: sent_emails.append(a))

    pipeline._check_and_notify_matches([{"id": "test-1", "title": "Test", "source_url": "http://x.com"}])
    assert sent_emails == []  # No match — only 1 user liked


def test_match_with_two_likes_sends_email(tmp_path, monkeypatch):
    import scripts.github_pages_pipeline as pipeline

    monkeypatch.setattr(pipeline, "_LIKES_FILE", tmp_path / "likes.json")
    monkeypatch.setattr(pipeline, "_MATCHES_NOTIFIED_FILE", tmp_path / "notified.json")
    monkeypatch.setattr(pipeline, "_DATA_DIR", tmp_path)

    likes = {
        "test-1": {
            "Jonathan": "2024-05-01T09:00:00Z",
            "Sarah":    "2024-05-01T10:00:00Z",
        }
    }
    (tmp_path / "likes.json").write_text(json.dumps(likes))

    sent_emails = []
    monkeypatch.setattr(pipeline, "_send_match_email", lambda props, lks: sent_emails.append(props))

    pipeline._check_and_notify_matches([{"id": "test-1", "title": "Hoeve", "source_url": "http://x.com"}])
    assert len(sent_emails) == 1
    assert sent_emails[0][0]["id"] == "test-1"


def test_match_not_sent_twice(tmp_path, monkeypatch):
    import scripts.github_pages_pipeline as pipeline

    monkeypatch.setattr(pipeline, "_LIKES_FILE", tmp_path / "likes.json")
    notified_path = tmp_path / "notified.json"
    notified_path.write_text(json.dumps(["test-1"]))  # already notified
    monkeypatch.setattr(pipeline, "_MATCHES_NOTIFIED_FILE", notified_path)

    likes = {"test-1": {"Jonathan": "2024-05-01T09:00:00Z", "Sarah": "2024-05-01T10:00:00Z"}}
    (tmp_path / "likes.json").write_text(json.dumps(likes))

    sent_emails = []
    monkeypatch.setattr(pipeline, "_send_match_email", lambda *a: sent_emails.append(a))

    pipeline._check_and_notify_matches([{"id": "test-1", "title": "Hoeve", "source_url": "http://x.com"}])
    assert sent_emails == []  # Already notified — should not send again


def test_notified_list_updated_after_match(tmp_path, monkeypatch):
    import scripts.github_pages_pipeline as pipeline

    monkeypatch.setattr(pipeline, "_LIKES_FILE", tmp_path / "likes.json")
    notified_path = tmp_path / "notified.json"
    monkeypatch.setattr(pipeline, "_MATCHES_NOTIFIED_FILE", notified_path)

    likes = {"test-2": {"Jonathan": "2024-05-01T09:00:00Z", "Sarah": "2024-05-01T10:00:00Z"}}
    (tmp_path / "likes.json").write_text(json.dumps(likes))

    monkeypatch.setattr(pipeline, "_send_match_email", lambda *a: None)

    pipeline._check_and_notify_matches([{"id": "test-2", "title": "Hoeve", "source_url": "http://x.com"}])

    notified = json.loads(notified_path.read_text())
    assert "test-2" in notified
