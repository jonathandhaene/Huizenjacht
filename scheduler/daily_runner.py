"""
Daily scheduler — runs the Huizenjacht pipeline once per day at the
configured time (DAILY_RUN_TIME in .env, default 07:00).

Can be run as:
    python -m scheduler.daily_runner

Or kept alive as a background process / systemd service / Docker container.
"""
from __future__ import annotations

import logging
import time

import schedule

from agents.orchestrator import Orchestrator
from config.settings import settings

logger = logging.getLogger(__name__)


def _run_pipeline() -> None:
    """Single pipeline execution — called by the scheduler."""
    try:
        orchestrator = Orchestrator()
        results = orchestrator.run()
        logger.info("Pipeline complete — %d properties notified", len(results))
    except Exception as exc:
        logger.exception("Pipeline failed with unhandled exception: %s", exc)


def start() -> None:
    """Schedule the pipeline and block forever."""
    run_time = settings.daily_run_time
    logger.info("Scheduling daily pipeline run at %s", run_time)
    schedule.every().day.at(run_time).do(_run_pipeline)

    logger.info("Scheduler started — waiting for next run at %s …", run_time)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    start()
