"""
Tests for the email notification agent.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from models.property import AIAnalysis, GovernmentData, Property, PropertyType


def _make_prop(prop_id: str = "p1") -> Property:
    p = Property(
        id=prop_id,
        source="immoweb",
        source_url=f"https://www.immoweb.be/nl/{prop_id}",
        title="Landelijke hoeve",
        property_type=PropertyType.FARM,
        price=490_000,
        municipality="Brakel",
        postal_code="9660",
        address="Dorpsstraat 1",
        bedrooms=4,
        land_area=12_000,
        images=["https://img.immoweb.be/1.jpg"],
    )
    p.government_data = GovernmentData(
        zoning="Agrarisch gebied",
        agricultural_zone=True,
        animal_keeping_allowed=True,
        bnb_possible=True,
    )
    p.ai_analysis = AIAnalysis(
        score=8.5,
        summary="Uitstekende match",
        pros=["groot perceel", "agrarische zone"],
        cons=[],
        recommendations=["Vraag RUP-attest op"],
    )
    return p


# ---------------------------------------------------------------------------

def test_no_email_when_no_properties():
    """send() does nothing when the list is empty."""
    from agents.notification.email_agent import EmailNotificationAgent

    agent = EmailNotificationAgent()
    with patch("smtplib.SMTP") as mock_smtp:
        agent.send([])
        mock_smtp.assert_not_called()


def test_no_email_when_smtp_not_configured():
    """send() logs a warning and returns when SMTP_USERNAME is not set."""
    from agents.notification.email_agent import EmailNotificationAgent
    from config.settings import settings

    agent = EmailNotificationAgent()
    original = settings.smtp_username
    try:
        settings.__dict__["smtp_username"] = ""
        with patch("smtplib.SMTP") as mock_smtp:
            agent.send([_make_prop()])
            mock_smtp.assert_not_called()
    finally:
        settings.__dict__["smtp_username"] = original


def test_build_html_contains_key_info():
    """The generated HTML contains price, title and municipality."""
    from agents.notification.email_agent import EmailNotificationAgent

    agent = EmailNotificationAgent()
    prop = _make_prop()
    html = agent._build_html([prop])

    assert "Landelijke hoeve" in html
    assert "490" in html  # price
    assert "Brakel" in html
    assert "8.5" in html  # AI score
    assert "Agrarisch gebied" in html


def test_build_subject_singular_plural():
    from agents.notification.email_agent import EmailNotificationAgent

    agent = EmailNotificationAgent()
    subject_one = agent._build_subject([_make_prop()])
    subject_multi = agent._build_subject([_make_prop("a"), _make_prop("b")])

    assert "1 nieuwe" not in subject_one or "1 nieuw" in subject_one
    assert "2 nieuwe" in subject_multi


def test_email_sent_via_smtp(monkeypatch):
    """send() calls smtplib.SMTP when credentials are configured."""
    from agents.notification.email_agent import EmailNotificationAgent
    from config.settings import settings

    # Temporarily provide credentials
    monkeypatch.setattr(settings, "smtp_username", "sender@example.com")
    monkeypatch.setattr(settings, "smtp_password", "secret")

    mock_server = MagicMock()
    mock_smtp_cls = MagicMock(return_value=mock_server)
    mock_server.__enter__ = MagicMock(return_value=mock_server)
    mock_server.__exit__ = MagicMock(return_value=False)

    agent = EmailNotificationAgent()
    with patch("smtplib.SMTP", mock_smtp_cls):
        agent.send([_make_prop()])

    mock_smtp_cls.assert_called_once()
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("sender@example.com", "secret")
    mock_server.sendmail.assert_called_once()
