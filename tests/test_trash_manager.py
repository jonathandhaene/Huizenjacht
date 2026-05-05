"""
Tests for scripts/trash_manager.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.trash_manager import TrashEntry, TrashManager, _RETENTION_DAYS


# ---------------------------------------------------------------------------
# TrashEntry
# ---------------------------------------------------------------------------

def test_trash_entry_defaults():
    e = TrashEntry("prop-1", ["data/images/prop-1/abc.jpg"])
    assert e.property_id == "prop-1"
    assert e.image_paths == ["data/images/prop-1/abc.jpg"]
    assert e.deleted_at
    assert e.purge_after
    # purge_after should be ~14 days after deleted_at
    deleted = datetime.fromisoformat(e.deleted_at)
    purge   = datetime.fromisoformat(e.purge_after)
    delta   = purge - deleted
    assert abs(delta.days - _RETENTION_DAYS) <= 1


def test_trash_entry_roundtrip():
    e = TrashEntry("p1", ["a.jpg", "b.png"])
    d = e.to_dict()
    e2 = TrashEntry.from_dict(d)
    assert e2.property_id == "p1"
    assert e2.image_paths == ["a.jpg", "b.png"]
    assert e2.deleted_at  == e.deleted_at
    assert e2.purge_after == e.purge_after


# ---------------------------------------------------------------------------
# TrashManager — basic load / save
# ---------------------------------------------------------------------------

def test_trash_manager_starts_empty(tmp_path):
    tm = TrashManager(tmp_path)
    assert tm.as_list() == []


def test_trash_manager_loads_existing(tmp_path):
    entry = {
        "property_id": "p-old",
        "image_paths": ["data/images/p-old/x.jpg"],
        "deleted_at":  "2026-01-01T00:00:00+00:00",
        "purge_after": "2026-01-15T00:00:00+00:00",
    }
    (tmp_path / "trash.json").write_text(json.dumps([entry]))

    tm = TrashManager(tmp_path)
    assert len(tm.as_list()) == 1
    assert tm.as_list()[0]["property_id"] == "p-old"


def test_trash_manager_tolerates_corrupt_file(tmp_path):
    (tmp_path / "trash.json").write_text("not valid json")
    tm = TrashManager(tmp_path)
    assert tm.as_list() == []


# ---------------------------------------------------------------------------
# TrashManager — trash_images
# ---------------------------------------------------------------------------

def test_trash_images_creates_entry(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["data/images/p1/a.jpg"])

    entries = tm.as_list()
    assert len(entries) == 1
    assert entries[0]["property_id"] == "p1"
    assert "data/images/p1/a.jpg" in entries[0]["image_paths"]


def test_trash_images_persists(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["data/images/p1/a.jpg"])

    tm2 = TrashManager(tmp_path)  # reload
    assert len(tm2.as_list()) == 1


def test_trash_images_no_duplicates(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["img.jpg"])
    tm.trash_images("p1", ["img.jpg"])  # same path again

    paths = tm.get_trashed_paths("p1")
    assert paths == {"img.jpg"}
    # Only one entry created (second call was a no-op)
    assert len(tm.as_list()) == 1


def test_trash_images_empty_list(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", [])
    assert tm.as_list() == []


# ---------------------------------------------------------------------------
# TrashManager — get_trashed_paths
# ---------------------------------------------------------------------------

def test_get_trashed_paths_single_entry(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg", "b.jpg"])
    assert tm.get_trashed_paths("p1") == {"a.jpg", "b.jpg"}


def test_get_trashed_paths_multiple_entries(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    # add a second batch after restoring would go away, but here we just add more
    entry = TrashEntry("p1", ["b.jpg"])
    tm._entries.append(entry)
    assert tm.get_trashed_paths("p1") == {"a.jpg", "b.jpg"}


def test_get_trashed_paths_no_entry(tmp_path):
    tm = TrashManager(tmp_path)
    assert tm.get_trashed_paths("unknown") == set()


def test_get_all_trashed_paths(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    tm.trash_images("p2", ["b.jpg"])
    assert tm.get_all_trashed_paths() == {"a.jpg", "b.jpg"}


# ---------------------------------------------------------------------------
# TrashManager — restore_images
# ---------------------------------------------------------------------------

def test_restore_images_removes_entries(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    restored = tm.restore_images("p1")

    assert restored == ["a.jpg"]
    assert tm.as_list() == []


def test_restore_images_only_affects_target(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    tm.trash_images("p2", ["b.jpg"])
    tm.restore_images("p1")

    remaining = tm.as_list()
    assert len(remaining) == 1
    assert remaining[0]["property_id"] == "p2"


def test_restore_images_idempotent(tmp_path):
    tm = TrashManager(tmp_path)
    restored1 = tm.restore_images("p1")  # nothing to restore
    assert restored1 == []


# ---------------------------------------------------------------------------
# TrashManager — empty_trash_for
# ---------------------------------------------------------------------------

def test_empty_trash_for_removes_entries(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    count = tm.empty_trash_for("p1")

    assert count == 1
    assert tm.as_list() == []


def test_empty_trash_for_deletes_files(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    img = docs_dir / "a.jpg"
    img.write_bytes(b"fake")

    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    tm.empty_trash_for("p1", docs_dir)

    assert not img.exists()


def test_empty_trash_for_missing_file_ok(tmp_path):
    """empty_trash_for should not raise even if the file is already gone."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()

    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["gone.jpg"])
    tm.empty_trash_for("p1", docs_dir)   # file doesn't exist — should not raise


def test_empty_trash_for_multiple(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    tm.trash_images("p2", ["b.jpg"])
    tm.trash_images("p3", ["c.jpg"])

    count = tm.empty_trash_for_multiple(["p1", "p2"])
    assert count == 2

    remaining = tm.as_list()
    assert len(remaining) == 1
    assert remaining[0]["property_id"] == "p3"


# ---------------------------------------------------------------------------
# TrashManager — empty_all_trash
# ---------------------------------------------------------------------------

def test_empty_all_trash(tmp_path):
    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["a.jpg"])
    tm.trash_images("p2", ["b.jpg"])
    count = tm.empty_all_trash()

    assert count == 2
    assert tm.as_list() == []


def test_empty_all_trash_deletes_files(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "x.jpg").write_bytes(b"img")

    tm = TrashManager(tmp_path)
    tm.trash_images("p1", ["x.jpg"])
    tm.empty_all_trash(docs_dir)

    assert not (docs_dir / "x.jpg").exists()


# ---------------------------------------------------------------------------
# TrashManager — auto_purge
# ---------------------------------------------------------------------------

def test_auto_purge_removes_expired(tmp_path):
    past = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    entry = {
        "property_id": "old-prop",
        "image_paths": ["data/images/old-prop/x.jpg"],
        "deleted_at":  past,
        "purge_after": past,
    }
    (tmp_path / "trash.json").write_text(json.dumps([entry]))

    tm = TrashManager(tmp_path)
    count = tm.auto_purge()

    assert count == 1
    assert tm.as_list() == []


def test_auto_purge_keeps_fresh(tmp_path):
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    entry = {
        "property_id": "new-prop",
        "image_paths": ["data/images/new-prop/x.jpg"],
        "deleted_at":  datetime.now(timezone.utc).isoformat(),
        "purge_after": future,
    }
    (tmp_path / "trash.json").write_text(json.dumps([entry]))

    tm = TrashManager(tmp_path)
    count = tm.auto_purge()

    assert count == 0
    assert len(tm.as_list()) == 1


def test_auto_purge_deletes_files(tmp_path):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    img = docs_dir / "data" / "images" / "p1" / "old.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"fake")

    past = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    entry = {
        "property_id": "p1",
        "image_paths": ["data/images/p1/old.jpg"],
        "deleted_at":  past,
        "purge_after": past,
    }
    (tmp_path / "trash.json").write_text(json.dumps([entry]))

    tm = TrashManager(tmp_path)
    tm.auto_purge(docs_dir)

    assert not img.exists()


def test_auto_purge_mixed_entries(tmp_path):
    past   = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    entries = [
        {"property_id": "old", "image_paths": ["old.jpg"], "deleted_at": past,   "purge_after": past},
        {"property_id": "new", "image_paths": ["new.jpg"], "deleted_at": past,   "purge_after": future},
    ]
    (tmp_path / "trash.json").write_text(json.dumps(entries))

    tm = TrashManager(tmp_path)
    count = tm.auto_purge()

    assert count == 1
    remaining = tm.as_list()
    assert len(remaining) == 1
    assert remaining[0]["property_id"] == "new"
