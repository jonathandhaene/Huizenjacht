"""
Tests for agents.scrapers.ai_extractor.

All tests mock the OpenAI client to avoid real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.scrapers.ai_extractor import (
    AIPropertyExtractor,
    _dict_to_property,
    _static_error_analysis,
    _strip_html_noise,
)
from models.property import PropertyType


# ── _strip_html_noise ─────────────────────────────────────────────────────────

def test_strip_html_noise_removes_script_blocks():
    html = '<html><script>var x = 1;</script><p>Hello</p></html>'
    result = _strip_html_noise(html)
    assert "var x" not in result
    assert "Hello" in result


def test_strip_html_noise_removes_style_blocks():
    html = '<html><style>.foo { color: red; }</style><p>World</p></html>'
    result = _strip_html_noise(html)
    assert "color: red" not in result
    assert "World" in result


def test_strip_html_noise_removes_tags():
    html = '<div class="card"><h2>Title</h2><p>Price: € 450.000</p></div>'
    result = _strip_html_noise(html)
    assert "<" not in result
    assert "Title" in result
    assert "450.000" in result


# ── _static_error_analysis ────────────────────────────────────────────────────

@pytest.mark.parametrize("status_code,expected_type", [
    (403, "bot_detection"),
    (400, "bad_request"),
    (429, "rate_limited"),
    (500, "unknown"),
    (404, "unknown"),
])
def test_static_error_analysis(status_code, expected_type):
    result = _static_error_analysis(status_code)
    assert result["error_type"] == expected_type
    assert "suggestions" in result
    assert isinstance(result["suggestions"], list)
    assert "retry_strategy" in result


def test_static_error_403_suggests_playwright():
    result = _static_error_analysis(403)
    assert result["retry_strategy"] == "playwright"


def test_static_error_429_suggests_wait_and_retry():
    result = _static_error_analysis(429)
    assert result["retry_strategy"] == "wait_and_retry"


# ── _dict_to_property ─────────────────────────────────────────────────────────

def test_dict_to_property_basic():
    item = {
        "title": "Hoeve met weiland",
        "price": 450_000,
        "postal_code": "9660",
        "municipality": "Brakel",
        "bedrooms": 4,
        "land_area": 12_000,
        "property_type": "farm",
    }
    prop = _dict_to_property(item, 0, "https://example.com", "realo")
    assert prop is not None
    assert prop.title == "Hoeve met weiland"
    assert prop.price == 450_000.0
    assert prop.postal_code == "9660"
    assert prop.municipality == "Brakel"
    assert prop.bedrooms == 4
    assert prop.land_area == 12_000.0
    assert prop.property_type == PropertyType.FARM
    assert prop.source == "realo"


def test_dict_to_property_defaults():
    """Missing fields default sensibly."""
    item = {"title": "Woning"}
    prop = _dict_to_property(item, 0, "https://example.com", "immoweb")
    assert prop is not None
    assert prop.price is None
    assert prop.bedrooms is None
    assert prop.property_type == PropertyType.HOUSE


def test_dict_to_property_unknown_type_defaults_to_house():
    item = {"title": "Eigendom", "property_type": "unknown_value"}
    prop = _dict_to_property(item, 0, "https://example.com", "test")
    assert prop.property_type == PropertyType.HOUSE


def test_dict_to_property_uses_source_url_fallback():
    item = {"title": "Woning"}
    prop = _dict_to_property(item, 0, "https://fallback.com/search", "test")
    assert prop.source_url == "https://fallback.com/search"


def test_dict_to_property_uses_item_source_url():
    item = {"title": "Woning", "source_url": "https://example.com/listing/42"}
    prop = _dict_to_property(item, 0, "https://fallback.com", "test")
    assert prop.source_url == "https://example.com/listing/42"


# ── AIPropertyExtractor — no API key ─────────────────────────────────────────

def test_extractor_not_available_without_api_key():
    with patch("config.settings.settings") as mock_settings:
        mock_settings.openai_api_key = ""
        extractor = AIPropertyExtractor()
    assert not extractor.available


def test_extractor_returns_empty_without_client():
    extractor = AIPropertyExtractor.__new__(AIPropertyExtractor)
    extractor._client = None
    extractor._model = "gpt-4o-mini"

    assert extractor.extract_from_html("<html></html>", "https://x.com", "test") == []
    assert extractor.extract_from_text("some text", "https://x.com", "test") == []


def test_extractor_analyze_error_returns_static_without_client():
    extractor = AIPropertyExtractor.__new__(AIPropertyExtractor)
    extractor._client = None
    extractor._model = "gpt-4o-mini"

    result = extractor.analyze_error("https://example.com", 403, "Forbidden")
    assert result["error_type"] == "bot_detection"


# ── AIPropertyExtractor — with mocked client ─────────────────────────────────

def _make_extractor_with_mock_client():
    """Return an AIPropertyExtractor with a mocked OpenAI client."""
    extractor = AIPropertyExtractor.__new__(AIPropertyExtractor)
    extractor._client = MagicMock()
    extractor._model = "gpt-4o-mini"
    return extractor


def _mock_openai_response(content: str):
    """Build a minimal mock of openai ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_extract_from_html_returns_properties():
    extractor = _make_extractor_with_mock_client()

    gpt_output = json.dumps([
        {
            "title": "Hoeve met schuur",
            "price": 480_000,
            "postal_code": "9660",
            "municipality": "Brakel",
            "bedrooms": 4,
            "land_area": 10_000,
            "property_type": "farm",
        }
    ])
    extractor._client.chat.completions.create.return_value = _mock_openai_response(gpt_output)

    props = extractor.extract_from_html("<html>...</html>", "https://realo.be/search", "realo")

    assert len(props) == 1
    assert props[0].title == "Hoeve met schuur"
    assert props[0].price == 480_000.0
    assert props[0].property_type == PropertyType.FARM


def test_extract_from_text_returns_properties():
    extractor = _make_extractor_with_mock_client()

    gpt_output = json.dumps([
        {"title": "Villa te koop", "price": 550_000, "property_type": "villa"}
    ])
    extractor._client.chat.completions.create.return_value = _mock_openai_response(gpt_output)

    props = extractor.extract_from_text("Villa te koop voor € 550.000", "https://fb.com/post", "social_media")

    assert len(props) == 1
    assert props[0].property_type == PropertyType.VILLA


def test_extract_returns_empty_on_gpt_api_failure():
    extractor = _make_extractor_with_mock_client()
    extractor._client.chat.completions.create.side_effect = Exception("API error")

    props = extractor.extract_from_html("<html></html>", "https://x.com", "test")
    assert props == []


def test_extract_handles_markdown_json_fences():
    """GPT sometimes wraps JSON in ```json ... ``` — we should handle that."""
    extractor = _make_extractor_with_mock_client()

    gpt_output = "```json\n[{\"title\": \"Woning\", \"price\": 300000}]\n```"
    extractor._client.chat.completions.create.return_value = _mock_openai_response(gpt_output)

    props = extractor.extract_from_html("<html></html>", "https://x.com", "test")
    assert len(props) == 1
    assert props[0].price == 300_000.0


def test_extract_handles_empty_gpt_array():
    extractor = _make_extractor_with_mock_client()
    extractor._client.chat.completions.create.return_value = _mock_openai_response("[]")

    props = extractor.extract_from_html("<html></html>", "https://x.com", "test")
    assert props == []


def test_analyze_error_returns_ai_result():
    extractor = _make_extractor_with_mock_client()
    ai_result = {
        "error_type": "bot_detection",
        "likely_cause": "Cloudflare WAF",
        "suggestions": ["Use Playwright"],
        "retry_strategy": "playwright",
    }
    extractor._client.chat.completions.create.return_value = _mock_openai_response(
        json.dumps(ai_result)
    )

    result = extractor.analyze_error("https://example.com", 403, "<html>Forbidden</html>")
    assert result["error_type"] == "bot_detection"
    assert result["retry_strategy"] == "playwright"


def test_analyze_error_falls_back_on_api_failure():
    extractor = _make_extractor_with_mock_client()
    extractor._client.chat.completions.create.side_effect = Exception("timeout")

    result = extractor.analyze_error("https://example.com", 403, "")
    # Falls back to static analysis
    assert result["error_type"] == "bot_detection"
