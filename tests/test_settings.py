"""
Tests for the configuration / settings module.
"""
import os

import pytest


def test_settings_defaults():
    """Default settings are populated correctly."""
    from config.settings import Settings

    s = Settings()
    assert s.max_price == 600_000
    assert s.min_bedrooms == 3
    assert s.min_land_area == 5_000
    assert s.notification_email == "jonathan.dhaene@gmail.com"
    assert s.daily_run_time == "07:00"


def test_postal_code_list():
    from config.settings import Settings

    s = Settings(search_postal_codes="9600,9620,9630")
    codes = s.postal_code_list
    assert codes == ["9600", "9620", "9630"]


def test_keyword_list():
    from config.settings import Settings

    s = Settings(keywords="hoeve,boerderij,weiland")
    kws = s.keyword_list
    assert "hoeve" in kws
    assert "weiland" in kws


def test_log_level_uppercase():
    from config.settings import Settings

    s = Settings(log_level="debug")
    assert s.log_level == "DEBUG"


def test_cache_path_created(tmp_path, monkeypatch):
    """cache_path property creates the directory if it doesn't exist."""
    from config.settings import Settings

    cache_dir = str(tmp_path / "test_cache")
    s = Settings(cache_dir=cache_dir)
    path = s.cache_path
    assert path.exists()
    assert path.is_dir()
