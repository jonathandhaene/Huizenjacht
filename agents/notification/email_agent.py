"""
Email notification agent.

Sends a rich HTML digest email to the configured recipient when new
matching properties are found.  Uses Python's built-in `smtplib` so
no extra dependencies are needed beyond what's already in requirements.txt.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from config.settings import settings
from models.property import Property

logger = logging.getLogger(__name__)


class EmailNotificationAgent:
    """
    Sends an HTML email digest for a batch of new properties.

    Usage::

        agent = EmailNotificationAgent()
        agent.send(new_properties)
    """

    def send(self, properties: List[Property]) -> None:
        if not properties:
            logger.info("[email] No new properties — skipping notification")
            return

        if not settings.smtp_username:
            logger.warning(
                "[email] SMTP_USERNAME not configured — email not sent. "
                "Set SMTP_USERNAME and SMTP_PASSWORD in .env"
            )
            return

        subject = self._build_subject(properties)
        html_body = self._build_html(properties)
        self._send_email(subject, html_body)
        logger.info("[email] Digest sent to %s (%d properties)", settings.notification_email, len(properties))

    # ------------------------------------------------------------------

    def _build_subject(self, properties: List[Property]) -> str:
        date_str = datetime.now().strftime("%d/%m/%Y")
        count = len(properties)
        return (
            f"🏡 Huizenjacht — {count} nieuw{'e' if count != 1 else ''} "
            f"woning{'en' if count != 1 else ''} gevonden ({date_str})"
        )

    def _build_html(self, properties: List[Property]) -> str:
        cards = "\n".join(self._property_card(p) for p in properties)
        date_str = datetime.now().strftime("%A %d %B %Y om %H:%M")
        return f"""<!DOCTYPE html>
<html lang="nl">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Huizenjacht Digest</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #f5f5f5; color: #333; margin: 0; padding: 0; }}
    .wrapper {{ max-width: 700px; margin: 0 auto; background: #fff; padding: 24px; }}
    h1 {{ color: #2c7a2c; border-bottom: 2px solid #2c7a2c; padding-bottom: 8px; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; margin: 16px 0; padding: 16px; background: #fafafa; }}
    .card h2 {{ margin: 0 0 4px; font-size: 1.1em; }}
    .card h2 a {{ color: #1a5276; text-decoration: none; }}
    .card h2 a:hover {{ text-decoration: underline; }}
    .meta {{ font-size: 0.85em; color: #666; margin-bottom: 8px; }}
    .score-badge {{ display: inline-block; background: #2c7a2c; color: #fff; border-radius: 12px; padding: 2px 10px; font-size: 0.85em; font-weight: bold; }}
    .score-medium {{ background: #d4ac0d; }}
    .score-low {{ background: #c0392b; }}
    .section-title {{ font-weight: bold; margin: 10px 0 4px; font-size: 0.9em; color: #555; }}
    ul.compact {{ margin: 0; padding-left: 18px; font-size: 0.9em; }}
    ul.compact li {{ margin-bottom: 2px; }}
    .gov-box {{ background: #eaf4ea; border-left: 4px solid #2c7a2c; padding: 8px 12px; margin-top: 10px; font-size: 0.88em; }}
    .footer {{ font-size: 0.8em; color: #999; margin-top: 24px; border-top: 1px solid #eee; padding-top: 12px; }}
    img.thumb {{ max-width: 200px; max-height: 140px; border-radius: 4px; margin-top: 8px; float: right; margin-left: 12px; }}
  </style>
</head>
<body>
  <div class="wrapper">
    <h1>🏡 Huizenjacht Dagelijkse Digest</h1>
    <p>Goedemorgen! Hier zijn de nieuwste woningaanbiedingen die passen bij jouw zoekcriteria
       in de <strong>Vlaamse Ardennen</strong> (budget: max. € {settings.max_price:,}).<br>
       Rapport gegenereerd op {date_str}.</p>
    {cards}
    <div class="footer">
      Automatisch gegenereerd door de Huizenjacht multi-agent pipeline.<br>
      Zoekregio: {settings.search_region} | Postcode(s): {settings.search_postal_codes}
    </div>
  </div>
</body>
</html>"""

    def _property_card(self, prop: Property) -> str:
        price_str = f"€ {prop.price:,.0f}" if prop.price else "Prijs op aanvraag"
        location = ", ".join(filter(None, [prop.address, prop.postal_code, prop.municipality]))

        thumb_html = ""
        if prop.images:
            thumb_html = f'<img class="thumb" src="{prop.images[0]}" alt="Foto">'

        # AI analysis section
        ai_html = ""
        analysis = prop.ai_analysis
        if analysis:
            badge_class = (
                "score-badge"
                if analysis.score >= 7
                else ("score-medium" if analysis.score >= 4 else "score-low")
            )
            pros_html = "".join(f"<li>✅ {p}</li>" for p in analysis.pros[:5])
            cons_html = "".join(f"<li>⚠️ {c}</li>" for c in analysis.cons[:5])
            rec_html = "".join(f"<li>💡 {r}</li>" for r in analysis.recommendations[:4])
            ai_html = f"""
      <div class="section-title">AI Analyse</div>
      <span class="{badge_class}">Score: {analysis.score:.1f}/10</span>
      <p style="margin:6px 0;font-size:0.9em">{analysis.summary}</p>
      {"<div class='section-title'>Voordelen</div><ul class='compact'>" + pros_html + "</ul>" if pros_html else ""}
      {"<div class='section-title'>Aandachtspunten</div><ul class='compact'>" + cons_html + "</ul>" if cons_html else ""}
      {"<div class='section-title'>Aanbevelingen</div><ul class='compact'>" + rec_html + "</ul>" if rec_html else ""}
"""

        # Government data section
        gov_html = ""
        gov = prop.government_data
        if gov and any([gov.zoning, gov.flood_risk, gov.animal_keeping_allowed is not None]):
            rows = []
            if gov.zoning:
                rows.append(f"<strong>Zone:</strong> {gov.zoning}")
            if gov.agricultural_zone is not None:
                rows.append(f"<strong>Agrarisch:</strong> {'✅ ja' if gov.agricultural_zone else '❌ nee'}")
            if gov.animal_keeping_allowed is not None:
                rows.append(
                    f"<strong>Dieren houden:</strong> {'✅ toegelaten' if gov.animal_keeping_allowed else '❌ niet toegelaten'}"
                )
            if gov.bnb_possible is not None:
                rows.append(f"<strong>B&B:</strong> {'✅ waarschijnlijk mogelijk' if gov.bnb_possible else '⚠️ onduidelijk'}")
            if gov.flood_risk:
                rows.append(f"<strong>Overstromingsrisico:</strong> ⚠️ {gov.flood_risk}")
            if gov.heritage_protected:
                rows.append("<strong>Erfgoedbescherming:</strong> ⚠️ ja")
            if gov.source_url:
                rows.append(f'<a href="{gov.source_url}">📍 Bekijk op Geopunt</a>')
            gov_html = f'<div class="gov-box">{"<br>".join(rows)}</div>'

        source_badge = f'<span style="font-size:0.8em;background:#eee;border-radius:8px;padding:2px 8px;margin-left:6px">{prop.source}</span>'

        return f"""
    <div class="card">
      {thumb_html}
      <h2><a href="{prop.source_url}">{prop.title}</a>{source_badge}</h2>
      <div class="meta">
        💶 {price_str} &nbsp;|&nbsp;
        📍 {location or 'Onbekende locatie'} &nbsp;|&nbsp;
        🛏 {prop.bedrooms or '?'} slpk &nbsp;|&nbsp;
        🌿 {f"{prop.land_area:,.0f} m²" if prop.land_area else '? m²'} perceel
      </div>
      {ai_html}
      {gov_html}
    </div>"""

    # ------------------------------------------------------------------

    def _send_email(self, subject: str, html_body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = settings.notification_email
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(settings.smtp_username, settings.smtp_password)
                server.sendmail(
                    settings.smtp_from,
                    settings.notification_email,
                    msg.as_string(),
                )
            logger.info("[email] Digest sent to %s", settings.notification_email)
        except Exception as exc:
            logger.error("[email] Failed to send email: %s", exc)
            raise
