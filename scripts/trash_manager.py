"""
Trash management for locally cached property images.

Provides soft-delete (trash), restore, empty, and auto-purge operations
against a ``docs/data/trash.json`` file.  Actual file deletion only happens
on an explicit "empty" call or when the pipeline runs auto-purge after the
14-day retention window.

JSON format of trash.json::

    [
      {
        "property_id": "immoweb-123",
        "image_paths": ["data/images/immoweb-123/abc.jpg"],
        "deleted_at":  "2026-05-05T18:00:00+00:00",
        "purge_after": "2026-05-19T18:00:00+00:00"
      },
      ...
    ]
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_TRASH_FILENAME = "trash.json"
_RETENTION_DAYS = 14


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

class TrashEntry:
    """One trash record (one batch of images trashed together)."""

    def __init__(
        self,
        property_id: str,
        image_paths: list[str],
        deleted_at: Optional[str] = None,
        purge_after: Optional[str] = None,
    ) -> None:
        self.property_id = property_id
        self.image_paths = list(image_paths)
        now = datetime.now(timezone.utc)
        self.deleted_at  = deleted_at  or now.isoformat()
        self.purge_after = purge_after or (now + timedelta(days=_RETENTION_DAYS)).isoformat()

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "property_id": self.property_id,
            "image_paths": self.image_paths,
            "deleted_at":  self.deleted_at,
            "purge_after": self.purge_after,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrashEntry":
        return cls(
            property_id = d["property_id"],
            image_paths = d.get("image_paths") or [],
            deleted_at  = d.get("deleted_at"),
            purge_after = d.get("purge_after"),
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class TrashManager:
    """Load / save / manipulate trash.json entries.

    Operations are **idempotent**: trashing already-trashed paths, restoring
    non-trashed paths, deleting missing files — all succeed silently.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir   = data_dir
        self.trash_file = data_dir / _TRASH_FILENAME
        self._entries: list[TrashEntry] = []
        self._load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.trash_file.exists():
            return
        try:
            raw = json.loads(self.trash_file.read_text(encoding="utf-8"))
            self._entries = [TrashEntry.from_dict(d) for d in raw]
        except Exception as exc:
            logger.warning("Could not load %s: %s", self.trash_file, exc)

    def _save(self) -> None:
        self.trash_file.write_text(
            json.dumps(
                [e.to_dict() for e in self._entries],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # ── Read helpers ──────────────────────────────────────────────────────────

    def get_trashed_paths(self, property_id: str) -> set[str]:
        """Return the set of image paths currently in trash for *property_id*."""
        paths: set[str] = set()
        for e in self._entries:
            if e.property_id == property_id:
                paths.update(e.image_paths)
        return paths

    def get_all_trashed_paths(self) -> set[str]:
        """Return every image path currently held in trash."""
        paths: set[str] = set()
        for e in self._entries:
            paths.update(e.image_paths)
        return paths

    def as_list(self) -> list[dict]:
        """Return a JSON-serialisable snapshot of all trash entries."""
        return [e.to_dict() for e in self._entries]

    # ── Mutation ──────────────────────────────────────────────────────────────

    def trash_images(self, property_id: str, image_paths: list[str]) -> None:
        """Soft-delete images: add them to trash (skip already-trashed paths)."""
        if not image_paths:
            return
        already = self.get_trashed_paths(property_id)
        new = [p for p in image_paths if p not in already]
        if not new:
            return
        self._entries.append(TrashEntry(property_id, new))
        self._save()
        logger.info("Trashed %d image(s) for %s", len(new), property_id)

    def restore_images(self, property_id: str) -> list[str]:
        """Remove all trash entries for *property_id*. Returns restored paths."""
        restored: list[str] = []
        keep: list[TrashEntry] = []
        for e in self._entries:
            if e.property_id == property_id:
                restored.extend(e.image_paths)
            else:
                keep.append(e)
        self._entries = keep
        if restored:
            self._save()
            logger.info("Restored %d image(s) for %s", len(restored), property_id)
        return restored

    def empty_trash_for(
        self, property_id: str, docs_dir: Optional[Path] = None
    ) -> int:
        """Permanently purge trashed images for one property. Returns count."""
        to_purge: list[TrashEntry] = []
        keep: list[TrashEntry] = []
        for e in self._entries:
            (to_purge if e.property_id == property_id else keep).append(e)
        self._entries = keep
        if to_purge:
            if docs_dir:
                for e in to_purge:
                    _delete_files(e.image_paths, docs_dir)
            self._save()
            logger.info("Emptied trash for %s (%d entries)", property_id, len(to_purge))
        return len(to_purge)

    def empty_trash_for_multiple(
        self,
        property_ids: list[str],
        docs_dir: Optional[Path] = None,
    ) -> int:
        """Permanently purge trashed images for several properties."""
        total = 0
        for pid in property_ids:
            total += self.empty_trash_for(pid, docs_dir)
        return total

    def empty_all_trash(self, docs_dir: Optional[Path] = None) -> int:
        """Permanently purge every trashed image. Returns count of entries."""
        if docs_dir:
            for e in self._entries:
                _delete_files(e.image_paths, docs_dir)
        count = len(self._entries)
        self._entries = []
        if count:
            self._save()
            logger.info("Emptied all trash (%d entries)", count)
        return count

    def auto_purge(self, docs_dir: Optional[Path] = None) -> int:
        """Delete entries whose ``purge_after`` timestamp has passed.

        Called automatically at the end of each pipeline run.
        Returns the number of entries purged.
        """
        now = datetime.now(timezone.utc)
        keep: list[TrashEntry] = []
        purge: list[TrashEntry] = []
        for e in self._entries:
            try:
                purge_after = datetime.fromisoformat(e.purge_after)
                # Make timezone-aware if naive
                if purge_after.tzinfo is None:
                    purge_after = purge_after.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                keep.append(e)
                continue
            (purge if purge_after <= now else keep).append(e)

        self._entries = keep
        if purge:
            if docs_dir:
                for e in purge:
                    _delete_files(e.image_paths, docs_dir)
            self._save()
            logger.info("Auto-purged %d trash entries", len(purge))
        return len(purge)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _delete_files(paths: list[str], docs_dir: Path) -> None:
    """Delete files relative to *docs_dir*. Idempotent — missing files are OK."""
    for rel in paths:
        full = docs_dir / rel
        if full.exists():
            try:
                full.unlink()
                logger.debug("Deleted cached image: %s", full)
            except OSError as exc:
                logger.warning("Could not delete %s: %s", full, exc)
