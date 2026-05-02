#!/usr/bin/env python3
"""
Huizenjacht — entry point.

Usage:
    # Run the pipeline once right now
    python main.py --run-now

    # Start the daily scheduler (blocks)
    python main.py --schedule

    # Run the pipeline once and print results (no email)
    python main.py --run-now --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys

from config.settings import settings


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    _configure_logging()

    parser = argparse.ArgumentParser(
        description="Huizenjacht — multi-agent house-hunting pipeline"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-now",
        action="store_true",
        help="Execute the scraping + enrichment + notification pipeline immediately",
    )
    group.add_argument(
        "--schedule",
        action="store_true",
        help="Start the daily scheduler (blocks until killed)",
    )
    group.add_argument(
        "--github-pages",
        action="store_true",
        help=(
            "GitHub Pages / Actions mode: scrape → save to docs/data/properties.json "
            "→ check likes.json for matches → send notifications"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip sending the notification email (print results to stdout instead)",
    )
    args = parser.parse_args()

    if args.github_pages:
        import logging as _logging
        _logging.getLogger().setLevel(getattr(_logging, settings.log_level, _logging.INFO))
        from scripts.github_pages_pipeline import run as _gp_run
        _gp_run()
        sys.exit(0)
    elif args.schedule:
        from scheduler.daily_runner import start
        start()
    elif args.run_now:
        from agents.orchestrator import Orchestrator
        orchestrator = Orchestrator()

        if args.dry_run:
            # Monkey-patch the email agent to print instead of sending
            def _dry_send(properties):  # type: ignore[override]
                print(f"\n{'='*60}")
                print(f"DRY RUN — would have sent email with {len(properties)} properties:")
                print(f"{'='*60}")
                for p in properties:
                    score = p.ai_analysis.score if p.ai_analysis else "n/a"
                    print(f"  [{score}/10] {p.title} — {p.source_url}")
                    if p.ai_analysis:
                        print(f"         {p.ai_analysis.summary}")
                print()

            orchestrator._email_agent.send = _dry_send  # type: ignore[assignment]

        results = orchestrator.run()
        print(f"\nPipeline complete — {len(results)} properties {'would be ' if args.dry_run else ''}notified.")
        sys.exit(0)


if __name__ == "__main__":
    main()
