"""
Image caching utilities for the Huizenjacht pipeline.

Downloads property images from remote URLs and stores them locally under
docs/data/images/<property_id>/<url_hash>.<ext>.  Files that already exist
are not re-downloaded (deterministic path based on URL hash).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 10          # seconds per request
_MAX_IMAGE_BYTES  = 10 * 1024 * 1024  # 10 MB hard cap
_MAX_IMAGES_PER_PROPERTY = 5   # don't hoard too many images per listing

_ALLOWED_MIME = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "image/gif",
}

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/webp": ".webp",
    "image/gif":  ".gif",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cache_property_images(
    prop_id: str,
    image_urls: list[str],
    docs_dir: Path,
) -> list[str]:
    """Download and cache images for one property.

    Returns a list of relative paths (relative to *docs_dir*) for each
    successfully cached image, suitable for storage in ``images_local``.
    Files that are already cached are reused without a network request.
    Failures are logged but do not raise.
    """
    if not image_urls:
        return []

    prop_dir = docs_dir / "data" / "images" / _safe_id(prop_id)
    prop_dir.mkdir(parents=True, exist_ok=True)

    local_paths: list[str] = []
    for url in image_urls[:_MAX_IMAGES_PER_PROPERTY]:
        url_hash = _url_hash(url)
        existing = list(prop_dir.glob(f"{url_hash}*"))
        if existing:
            rel = _rel(existing[0], docs_dir)
            local_paths.append(rel)
            logger.debug("Image already cached: %s", rel)
            continue

        result = _download(url)
        if result is None:
            continue

        data, ext = result
        dest = prop_dir / f"{url_hash}{ext}"
        try:
            dest.write_bytes(data)
        except OSError as exc:
            logger.warning("Could not write cached image %s: %s", dest, exc)
            continue

        rel = _rel(dest, docs_dir)
        local_paths.append(rel)
        logger.debug("Cached image %s → %s", url, rel)

    return local_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _download(url: str) -> Optional[tuple[bytes, str]]:
    """Download one image URL.  Returns (bytes, ext) or None on any failure."""
    try:
        resp = requests.get(
            url,
            timeout=_DOWNLOAD_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
            stream=True,
        )
        resp.raise_for_status()

        mime = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime not in _ALLOWED_MIME:
            logger.debug("Skipping %s: unexpected content-type %s", url, mime)
            return None

        data = resp.raw.read(_MAX_IMAGE_BYTES + 1, decode_content=True)
        if len(data) > _MAX_IMAGE_BYTES:
            logger.debug("Skipping %s: image too large (%d bytes)", url, len(data))
            return None

        ext = _MIME_TO_EXT.get(mime) or _ext_from_url(url)
        return data, ext

    except Exception as exc:
        logger.debug("Could not download image %s: %s", url, exc)
        return None


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _safe_id(prop_id: str) -> str:
    """Return a filesystem-safe version of a property ID."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in prop_id)


def _ext_from_url(url: str) -> str:
    """Guess extension from URL path, defaulting to .jpg."""
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def _rel(path: Path, base: Path) -> str:
    """Return *path* relative to *base*, always using forward slashes."""
    return str(path.relative_to(base)).replace("\\", "/")


# ---------------------------------------------------------------------------
# Pipeline helper
# ---------------------------------------------------------------------------

def process_properties_images(
    properties: list[dict],
    docs_dir: Path,
) -> list[dict]:
    """Cache images for all properties that don't yet have local images.

    Mutates each dict in-place (adds ``images_local``, ``images_remote``,
    ``images_cached_at``) and returns the same list.
    """
    for prop in properties:
        if prop.get("images_local"):
            continue  # already cached — skip

        urls = prop.get("images") or []
        if not urls:
            continue

        prop_id = prop.get("id", "unknown")
        try:
            local = cache_property_images(prop_id, urls, docs_dir)
        except Exception as exc:
            logger.warning("Image caching failed for %s: %s", prop_id, exc)
            local = []

        if local:
            prop["images_remote"] = urls
            prop["images_local"]  = local
            prop["images_cached_at"] = datetime.now(timezone.utc).isoformat()

    return properties
