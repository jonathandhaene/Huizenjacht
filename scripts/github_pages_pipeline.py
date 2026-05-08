"""
GitHub Pages pipeline script.

Called by the GitHub Actions workflow (main.py --github-pages).

Steps:
1. Load existing properties from docs/data/properties.json
2. Run all scrapers to find new listings
3. Deduplicate against existing properties
4. Enrich new properties (government data + AI analysis)
5. Merge and save back to docs/data/properties.json  ← committed by Actions
6. Cache property images locally under docs/data/images/
7. Auto-purge trashed images older than 14 days (docs/data/trash.json)
8. Load docs/data/likes.json and detect new matches
9. Send match email for newly matched properties
10. Update docs/data/matches_notified.json
11. Send daily digest email for new properties
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

from agents.enrichment.ai_analyzer import AIAnalyzerAgent
from agents.enrichment.government import GovernmentEnrichmentAgent
from agents.notification.email_agent import EmailNotificationAgent
from agents.scrapers.immoweb import ImmowebScraper
from agents.scrapers.local_immo import LocalImmoScraper
from agents.scrapers.logic_immo import LogicImmoScraper
from agents.scrapers.realo import RealoScraper
from agents.scrapers.social_media import SocialMediaScraper
from agents.scrapers.zimmo import ZimmoScraper
from config.settings import settings
from models.property import Property

logger = logging.getLogger(__name__)

# Paths relative to repo root (where Actions runs)
_DATA_DIR = Path("docs/data")
_DOCS_DIR = _DATA_DIR.parent          # docs/
_PROPERTIES_FILE = _DATA_DIR / "properties.json"
_LIKES_FILE = _DATA_DIR / "likes.json"
_MATCHES_NOTIFIED_FILE = _DATA_DIR / "matches_notified.json"

# Minimum AI score to include in digest email
_MIN_SCORE = 4.0


def run() -> None:
    """Execute the full GitHub-Pages-optimised pipeline."""
    start = datetime.now(timezone.utc)
    logger.info("=== GitHub Pages pipeline started at %s ===", start.isoformat())

    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load existing properties (so we know what's already been found)
    existing = _load_properties()
    existing_ids: Set[str] = {p["id"] for p in existing}
    logger.info("Loaded %d existing properties", len(existing))

    # 2. Scrape
    raw_new = _scrape_new(existing_ids)
    logger.info("Found %d new listings after deduplication", len(raw_new))

    # 3. Enrich
    enriched_new: List[Property] = []
    if raw_new:
        enriched_new = _enrich(raw_new)

    # 4. Merge
    new_dicts = [_property_to_dict(p) for p in enriched_new]
    all_properties = new_dicts + existing   # newest first

    # 5. Cache images locally for any property that doesn't have local images yet
    _cache_images(all_properties)

    # 6. Save
    _save_properties(all_properties)
    logger.info("Saved %d total properties to %s", len(all_properties), _PROPERTIES_FILE)

    # 7. Auto-purge trashed images older than the retention window
    _auto_purge_trash()

    # 8. Check for new matches and notify
    _check_and_notify_matches(all_properties)

    # 9. Send digest email for new high-scoring properties
    qualified = [p for p in enriched_new if _score(p) >= _MIN_SCORE]
    qualified.sort(key=_score, reverse=True)
    if qualified:
        EmailNotificationAgent().send(qualified)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    logger.info("=== Pipeline finished in %.1f s ===", elapsed)


# ---------------------------------------------------------------------------
# Image caching
# ---------------------------------------------------------------------------

def _cache_images(properties: List[dict]) -> None:
    """Download and cache images locally for properties that don't have them yet.

    Operates in-place on the list of property dicts.  Failures per-property
    are logged but do not abort the pipeline.
    """
    from scripts.image_cache import process_properties_images

    try:
        process_properties_images(properties, _DOCS_DIR)
        cached = sum(1 for p in properties if p.get("images_local"))
        logger.info("Image cache: %d/%d properties have local images", cached, len(properties))
    except Exception as exc:
        logger.error("Image caching step failed: %s", exc)


# ---------------------------------------------------------------------------
# Trash auto-purge
# ---------------------------------------------------------------------------

def _auto_purge_trash() -> None:
    """Run the 14-day auto-purge pass against docs/data/trash.json."""
    from scripts.trash_manager import TrashManager

    try:
        tm = TrashManager(_DATA_DIR)
        purged = tm.auto_purge(_DOCS_DIR)
        if purged:
            logger.info("Trash auto-purge: removed %d expired entries", purged)
    except Exception as exc:
        logger.error("Trash auto-purge failed: %s", exc)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def _scrape_new(existing_ids: Set[str]) -> List[Property]:
    """Run all scrapers and return only properties not already known."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    scrapers = [
        ImmowebScraper(),
        LocalImmoScraper(),
        ZimmoScraper(),
        RealoScraper(),
        LogicImmoScraper(),
        SocialMediaScraper(),
    ]
    all_found: List[Property] = []
    with ThreadPoolExecutor(max_workers=len(scrapers), thread_name_prefix="scraper") as ex:
        futures = {ex.submit(s.scrape): s.name for s in scrapers}
        for future in as_completed(futures):
            name = futures[future]
            try:
                listings = future.result()
                logger.info("[%s] → %d listings", name, len(listings))
                all_found.extend(listings)
            except Exception as exc:
                logger.error("[%s] crashed: %s", name, exc)

    # Deduplicate against existing + deduplicate within this run
    seen: Set[str] = set(existing_ids)
    new: List[Property] = []
    for p in all_found:
        if p.id not in seen:
            seen.add(p.id)
            new.append(p)
    return new


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def _enrich(properties: List[Property]) -> List[Property]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    gov_agent = GovernmentEnrichmentAgent()
    ai_agent = AIAnalyzerAgent()
    enriched: List[Property] = []

    def _enrich_one(p: Property) -> Property:
        p = gov_agent.enrich(p)
        p = ai_agent.analyze(p)
        return p

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="enricher") as ex:
        futures = {ex.submit(_enrich_one, p): p.id for p in properties}
        for future in as_completed(futures):
            try:
                enriched.append(future.result())
            except Exception as exc:
                logger.error("Enrichment failed: %s", exc)

    return enriched


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------

def _load_properties() -> List[dict]:
    if not _PROPERTIES_FILE.exists():
        return []
    try:
        return json.loads(_PROPERTIES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load %s: %s", _PROPERTIES_FILE, exc)
        return []


def _save_properties(properties: List[dict]) -> None:
    _PROPERTIES_FILE.write_text(
        json.dumps(properties, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _property_to_dict(prop: Property) -> dict:
    data = prop.model_dump()
    # Convert datetime objects to ISO strings
    for key in ("first_seen", "last_seen"):
        if isinstance(data.get(key), datetime):
            data[key] = data[key].isoformat()
    return data


# ---------------------------------------------------------------------------
# Match detection
# ---------------------------------------------------------------------------

def _check_and_notify_matches(all_properties: List[dict]) -> None:
    """Detect new mutual likes and send match email."""
    if not _LIKES_FILE.exists():
        return

    try:
        likes: Dict[str, Dict[str, str]] = json.loads(_LIKES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not load likes: %s", exc)
        return

    notified: List[str] = []
    if _MATCHES_NOTIFIED_FILE.exists():
        try:
            notified = json.loads(_MATCHES_NOTIFIED_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # A match = property has 2 or more unique user likes
    new_matches: List[dict] = []
    for prop_id, user_likes in likes.items():
        if len(user_likes) >= 2 and prop_id not in notified:
            # Find the property in our list
            prop_data = next((p for p in all_properties if p["id"] == prop_id), None)
            if prop_data:
                new_matches.append(prop_data)

    if new_matches:
        logger.info("🎉 %d new match(es) — sending match email", len(new_matches))
        _send_match_email(new_matches, likes)
        notified.extend(p["id"] for p in new_matches)
        _MATCHES_NOTIFIED_FILE.write_text(
            json.dumps(notified, indent=2), encoding="utf-8"
        )


def _send_match_email(matched_props: List[dict], likes: Dict[str, Dict[str, str]]) -> None:
    """Send a 'Match!' notification email."""
    if not settings.smtp_username:
        logger.warning("SMTP not configured — match email not sent")
        return

    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    names = list({name for user_likes in likes.values() for name in user_likes.keys()})
    partners = " & ".join(sorted(names)) or "jullie beiden"

    cards_html = ""
    for prop in matched_props:
        user_likes = likes.get(prop["id"], {})
        liked_by = " en ".join(
            f"<strong>{name}</strong> (❤️ {ts[:10]})"
            for name, ts in sorted(user_likes.items())
        )
        price_str = f"€ {prop['price']:,.0f}" if prop.get("price") else "Prijs op aanvraag"
        score = (prop.get("ai_analysis") or {}).get("score", "")
        score_html = f'<span style="background:#2d6a2d;color:#fff;border-radius:10px;padding:2px 8px;font-size:.85em">⭐ {score}/10</span>' if score else ""
        img_html = f'<img src="{prop["images"][0]}" style="max-width:200px;border-radius:6px;float:right;margin-left:12px" alt="">' if prop.get("images") else ""
        cards_html += f"""
<div style="border:2px solid #e74c3c;border-radius:10px;padding:16px;margin:12px 0;background:#fff8f8">
  {img_html}
  <h3 style="margin:0 0 6px"><a href="{prop['source_url']}" style="color:#c0392b">{prop['title']}</a></h3>
  <div style="color:#666;font-size:.9em">📍 {prop.get('municipality','')}&nbsp; 💶 {price_str}&nbsp; 🛏 {prop.get('bedrooms','?')}&nbsp; 🌿 {f"{prop['land_area']:,.0f} m²" if prop.get('land_area') else '?'} {score_html}</div>
  <div style="margin-top:8px">❤️ Geliked door: {liked_by}</div>
</div>"""

    html = f"""<!DOCTYPE html><html lang="nl"><head><meta charset="UTF-8"></head><body
  style="font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:0">
<div style="max-width:620px;margin:0 auto;background:#fff;padding:24px;border-radius:8px">
  <h1 style="color:#c0392b;border-bottom:2px solid #e74c3c;padding-bottom:8px">
    🎉 Het is een Match! — Huizenjacht
  </h1>
  <p><strong>{partners}</strong> hebben allebei ❤️ geliked op dezelfde woning(en)!</p>
  {cards_html}
  <p style="color:#999;font-size:.8em;margin-top:24px;border-top:1px solid #eee;padding-top:12px">
    Bekijk alle panden op je smartphone via de Huizenjacht web app.
  </p>
</div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎉 Match gevonden! — Huizenjacht ({len(matched_props)} pand{'en' if len(matched_props)!=1 else ''})"
    msg["From"] = settings.smtp_from
    msg["To"] = settings.notification_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from, settings.notification_email, msg.as_string())
        logger.info("Match email sent to %s", settings.notification_email)
    except Exception as exc:
        logger.error("Failed to send match email: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score(prop: Property) -> float:
    if prop.ai_analysis:
        return prop.ai_analysis.score
    return 0.0
