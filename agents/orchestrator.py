"""
Orchestrator — coordinates all agents in the Huizenjacht pipeline.

Pipeline steps
--------------
1. **Scrape** — run all scraper agents in parallel threads, collect Property objects
2. **Deduplicate** — filter out properties already sent to the user (disk cache)
3. **Enrich** — government data + AI analysis for each new property
4. **Score & filter** — keep only properties above a minimum AI score threshold
5. **Notify** — send the HTML email digest
6. **Persist** — mark sent properties as "seen" in the cache

The orchestrator is designed to be run once per day by the scheduler.
"""
from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Set

from agents.enrichment.ai_analyzer import AIAnalyzerAgent
from agents.enrichment.government import GovernmentEnrichmentAgent
from agents.notification.email_agent import EmailNotificationAgent
from agents.scrapers.immoweb import ImmowebScraper
from agents.scrapers.local_immo import LocalImmoScraper
from agents.scrapers.logic_immo import LogicImmoScraper
from agents.scrapers.nlp_normalizer import deduplicate_properties
from agents.scrapers.realo import RealoScraper
from agents.scrapers.social_media import SocialMediaScraper
from agents.scrapers.zimmo import ZimmoScraper
from config.settings import settings
from models.property import Property

logger = logging.getLogger(__name__)

# Minimum AI score to include a property in the digest (0–10)
_MIN_SCORE = 4.0


class Orchestrator:
    """
    Top-level coordinator for the Huizenjacht multi-agent pipeline.

    Usage::

        orchestrator = Orchestrator()
        orchestrator.run()
    """

    def __init__(self) -> None:
        self._scrapers = [
            ImmowebScraper(),
            ZimmoScraper(),
            RealoScraper(),
            LogicImmoScraper(),
            LocalImmoScraper(),
            SocialMediaScraper(),
        ]
        self._gov_agent = GovernmentEnrichmentAgent()
        self._ai_agent = AIAnalyzerAgent()
        self._email_agent = EmailNotificationAgent()
        self._seen_cache_path = settings.cache_path / "seen_properties.json"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> List[Property]:
        """Execute the full pipeline and return the list of properties notified."""
        start = datetime.now(timezone.utc)
        logger.info("=== Huizenjacht pipeline started at %s ===", start.isoformat())

        # 1. Scrape
        raw_properties = self._scrape_all()
        logger.info("Scrapers returned %d total listings", len(raw_properties))

        # 1a. Cross-source deduplication — remove the same property listed on
        #     multiple platforms (e.g. both Immoweb and Zimmo).
        raw_properties = deduplicate_properties(raw_properties)
        logger.info(
            "%d listings after cross-source deduplication", len(raw_properties)
        )

        # 2. Deduplicate
        new_properties = self._filter_new(raw_properties)
        logger.info("%d new listings after deduplication", len(new_properties))

        if not new_properties:
            logger.info("Nothing new today — no email sent")
            return []

        # 3. Enrich (gov + AI) — also parallel
        enriched = self._enrich_all(new_properties)

        # 4. Filter by score
        qualified = [p for p in enriched if (p.ai_analysis and p.ai_analysis.score >= _MIN_SCORE)]
        qualified.sort(key=lambda p: p.ai_analysis.score if p.ai_analysis else 0, reverse=True)
        logger.info(
            "%d properties qualify after score filter (min %.1f)", len(qualified), _MIN_SCORE
        )

        # 5. Notify
        if qualified:
            self._email_agent.send(qualified)

        # 6. Persist seen IDs (all new properties, regardless of score)
        self._mark_seen([p.id for p in enriched])

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("=== Pipeline finished in %.1f seconds ===", elapsed)
        return qualified

    # ------------------------------------------------------------------
    # Step 1: Scrape
    # ------------------------------------------------------------------

    def _scrape_all(self) -> List[Property]:
        """Run all scrapers concurrently and merge results."""
        results: List[Property] = []
        with ThreadPoolExecutor(max_workers=len(self._scrapers), thread_name_prefix="scraper") as ex:
            futures = {ex.submit(scraper.scrape): scraper.name for scraper in self._scrapers}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    listings = future.result()
                    logger.info("[%s] → %d listings", name, len(listings))
                    results.extend(listings)
                except Exception as exc:
                    logger.error("[%s] Scraper crashed: %s", name, exc)
        return results

    # ------------------------------------------------------------------
    # Step 2: Deduplicate
    # ------------------------------------------------------------------

    def _filter_new(self, properties: List[Property]) -> List[Property]:
        seen = self._load_seen()
        return [p for p in properties if p.id not in seen]

    def _load_seen(self) -> Set[str]:
        if self._seen_cache_path.exists():
            try:
                data = json.loads(self._seen_cache_path.read_text())
                return set(data)
            except Exception as exc:
                logger.warning("Could not load seen cache: %s", exc)
        return set()

    def _mark_seen(self, ids: List[str]) -> None:
        seen = self._load_seen()
        seen.update(ids)
        try:
            self._seen_cache_path.write_text(json.dumps(list(seen), indent=2))
        except Exception as exc:
            logger.warning("Could not write seen cache: %s", exc)

    # ------------------------------------------------------------------
    # Step 3: Enrich
    # ------------------------------------------------------------------

    def _enrich_all(self, properties: List[Property]) -> List[Property]:
        """Enrich each property with government data and AI analysis."""
        enriched: List[Property] = []
        # Use a modest thread pool — API rate limits apply
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="enricher") as ex:
            futures = {ex.submit(self._enrich_one, p): p.id for p in properties}
            for future in as_completed(futures):
                prop_id = futures[future]
                try:
                    enriched.append(future.result())
                except Exception as exc:
                    logger.error("Enrichment failed for %s: %s", prop_id, exc)
        return enriched

    def _enrich_one(self, prop: Property) -> Property:
        prop = self._gov_agent.enrich(prop)
        prop = self._ai_agent.analyze(prop)
        return prop
