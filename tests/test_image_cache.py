"""
Tests for scripts/image_cache.py
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(content: bytes, content_type: str, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        resp.raise_for_status.side_effect = HTTPError("boom")
    resp.headers = {"Content-Type": content_type}
    raw = MagicMock()
    raw.read.return_value = content
    resp.raw = raw
    return resp


# ---------------------------------------------------------------------------
# _url_hash
# ---------------------------------------------------------------------------

def test_url_hash_deterministic():
    from scripts.image_cache import _url_hash
    h1 = _url_hash("https://example.com/image.jpg")
    h2 = _url_hash("https://example.com/image.jpg")
    assert h1 == h2


def test_url_hash_different_urls():
    from scripts.image_cache import _url_hash
    h1 = _url_hash("https://a.com/1.jpg")
    h2 = _url_hash("https://a.com/2.jpg")
    assert h1 != h2


def test_url_hash_length():
    from scripts.image_cache import _url_hash
    assert len(_url_hash("https://example.com/img.jpg")) == 16


# ---------------------------------------------------------------------------
# _ext_from_url
# ---------------------------------------------------------------------------

def test_ext_from_url_jpg():
    from scripts.image_cache import _ext_from_url
    assert _ext_from_url("https://cdn.example.com/photo.jpg") == ".jpg"


def test_ext_from_url_jpeg_normalised():
    from scripts.image_cache import _ext_from_url
    assert _ext_from_url("https://cdn.example.com/photo.jpeg") == ".jpg"


def test_ext_from_url_png():
    from scripts.image_cache import _ext_from_url
    assert _ext_from_url("https://cdn.example.com/photo.png") == ".png"


def test_ext_from_url_unknown_defaults_to_jpg():
    from scripts.image_cache import _ext_from_url
    assert _ext_from_url("https://cdn.example.com/photo") == ".jpg"


# ---------------------------------------------------------------------------
# _download
# ---------------------------------------------------------------------------

def test_download_success(tmp_path):
    from scripts.image_cache import _download

    fake_bytes = b"\xff\xd8\xff" + b"\x00" * 100
    resp = _make_response(fake_bytes, "image/jpeg")

    with patch("scripts.image_cache.requests.get", return_value=resp):
        result = _download("https://example.com/photo.jpg")

    assert result is not None
    data, ext = result
    assert data == fake_bytes
    assert ext == ".jpg"


def test_download_bad_content_type():
    from scripts.image_cache import _download

    resp = _make_response(b"<html>not an image</html>", "text/html")

    with patch("scripts.image_cache.requests.get", return_value=resp):
        result = _download("https://example.com/page.html")

    assert result is None


def test_download_network_error():
    from scripts.image_cache import _download

    with patch("scripts.image_cache.requests.get", side_effect=ConnectionError("timeout")):
        result = _download("https://example.com/photo.jpg")

    assert result is None


def test_download_http_error():
    from scripts.image_cache import _download

    resp = _make_response(b"", "image/jpeg", status_code=403)

    with patch("scripts.image_cache.requests.get", return_value=resp):
        result = _download("https://example.com/photo.jpg")

    assert result is None


# ---------------------------------------------------------------------------
# cache_property_images
# ---------------------------------------------------------------------------

def test_cache_property_images_creates_file(tmp_path):
    from scripts.image_cache import cache_property_images

    fake_bytes = b"\x89PNG" + b"\x00" * 50
    resp = _make_response(fake_bytes, "image/png")

    with patch("scripts.image_cache.requests.get", return_value=resp):
        local = cache_property_images(
            "test-prop-1",
            ["https://example.com/img.png"],
            tmp_path,
        )

    assert len(local) == 1
    dest = tmp_path / local[0]
    assert dest.exists()
    assert dest.read_bytes() == fake_bytes


def test_cache_property_images_no_redownload(tmp_path):
    from scripts.image_cache import cache_property_images

    fake_bytes = b"\xff\xd8\xff" + b"\x00" * 50
    resp = _make_response(fake_bytes, "image/jpeg")

    url = "https://example.com/photo.jpg"

    with patch("scripts.image_cache.requests.get", return_value=resp) as mock_get:
        cache_property_images("prop-a", [url], tmp_path)
        # Second call — file exists already
        cache_property_images("prop-a", [url], tmp_path)

    # requests.get should only have been called once
    assert mock_get.call_count == 1


def test_cache_property_images_failed_download_skipped(tmp_path):
    from scripts.image_cache import cache_property_images

    with patch("scripts.image_cache.requests.get", side_effect=ConnectionError("fail")):
        local = cache_property_images(
            "prop-b",
            ["https://example.com/broken.jpg"],
            tmp_path,
        )

    assert local == []


def test_cache_property_images_relative_path(tmp_path):
    from scripts.image_cache import cache_property_images

    fake_bytes = b"\xff\xd8\xff" + b"\x00" * 10
    resp = _make_response(fake_bytes, "image/jpeg")

    with patch("scripts.image_cache.requests.get", return_value=resp):
        local = cache_property_images("my-prop", ["https://x.com/img.jpg"], tmp_path)

    assert len(local) == 1
    assert local[0].startswith("data/images/my-prop/")
    assert "/" in local[0]  # forward slashes


def test_cache_property_images_empty_urls(tmp_path):
    from scripts.image_cache import cache_property_images

    local = cache_property_images("x", [], tmp_path)
    assert local == []


# ---------------------------------------------------------------------------
# process_properties_images
# ---------------------------------------------------------------------------

def test_process_properties_images_sets_fields(tmp_path):
    from scripts.image_cache import process_properties_images

    props = [
        {"id": "p1", "images": ["https://example.com/a.jpg"]},
    ]

    fake_bytes = b"\xff\xd8\xff" + b"\x00" * 20
    resp = _make_response(fake_bytes, "image/jpeg")

    with patch("scripts.image_cache.requests.get", return_value=resp):
        result = process_properties_images(props, tmp_path)

    p = result[0]
    assert p.get("images_local")
    assert p["images_remote"] == ["https://example.com/a.jpg"]
    assert p.get("images_cached_at")


def test_process_properties_images_skips_already_cached(tmp_path):
    from scripts.image_cache import process_properties_images

    props = [
        {
            "id": "p2",
            "images": ["https://example.com/b.jpg"],
            "images_local": ["data/images/p2/existing.jpg"],
        }
    ]

    with patch("scripts.image_cache.requests.get") as mock_get:
        process_properties_images(props, tmp_path)

    mock_get.assert_not_called()
