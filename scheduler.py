#!/usr/bin/env python3
"""
AI Recruiter Copilot — continuous scheduler.

Runs the pipeline on a fixed interval so new resume emails
are processed automatically without manual intervention.

Usage:
    python scheduler.py              # runs every 6 hours (default)
    python scheduler.py --hours 2   # runs every 2 hours
    python scheduler.py --minutes 30 # runs every 30 minutes (testing)
"""

import argparse
import logging
import sys
import time
from datetime import datetime

import os
import schedule

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("logs/scheduler.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("scheduler")


def job():
    log.info("=" * 56)
    log.info("  Pipeline triggered at %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 56)
    try:
        from main import run_pipeline
        run_pipeline()
    except Exception as e:
        log.error("Pipeline run failed: %s", e)


def main():
    parser = argparse.ArgumentParser(description="AI Recruiter Copilot scheduler")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--hours",   type=float, default=6,  help="Interval in hours (default: 6)")
    group.add_argument("--minutes", type=float,             help="Interval in minutes (for testing)")
    args = parser.parse_args()

    if args.minutes:
        interval_desc = f"every {args.minutes} minute(s)"
        schedule.every(args.minutes).minutes.do(job)
    else:
        interval_desc = f"every {args.hours} hour(s)"
        schedule.every(args.hours).hours.do(job)

    log.info("Scheduler started — running pipeline %s", interval_desc)
    log.info("Press Ctrl+C to stop.")
    log.info("")

    # Run once immediately on startup
    job()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Scheduler stopped.")
        sys.exit(0)
