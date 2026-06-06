#!/usr/bin/env python3
"""
AI Recruiter Copilot — main pipeline.

Emits structured NODE:<name>:<status>:<count> lines so the dashboard
pipeline visualizer can track progress in real time.
"""

import os
import sys
import json
import logging
import traceback
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

os.makedirs("logs", exist_ok=True)
os.makedirs("resumes", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("logs/recruiter.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("recruiter")

SPREADSHEET_ID      = os.getenv("SPREADSHEET_ID")
SCREENING_THRESHOLD = 60
DAYS_BACK           = 7
RUNS_LOG            = "logs/pipeline_runs.json"


# ── Pipeline run tracker ──────────────────────────────────────
class PipelineRun:
    """Records per-node timing and counts; persists to RUNS_LOG."""

    NODES = ["gmail", "pdf_parser", "screener", "calendar", "sheets"]

    def __init__(self):
        self.run_id     = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.started_at = datetime.now().isoformat()
        self.finished_at = None
        self.status     = "running"
        self.nodes      = {}

    def _emit(self, name, status, count=0, message=""):
        """Print structured line parsed by the SSE log streamer."""
        tag = f"NODE:{name}:{status}:{count}"
        print(tag, flush=True)
        log.info(tag + (f"  {message}" if message else ""))

    def start(self, name):
        self.nodes[name] = {"status": "running", "started_at": datetime.now().isoformat(), "count": 0}
        self._emit(name, "running")

    def done(self, name, count=0, message=""):
        n = self.nodes.setdefault(name, {})
        n.update({"status": "done", "count": count,
                  "finished_at": datetime.now().isoformat(), "message": message})
        self._emit(name, "done", count, message)

    def error(self, name, err=""):
        n = self.nodes.setdefault(name, {})
        n.update({"status": "error", "error": str(err),
                  "finished_at": datetime.now().isoformat()})
        self._emit(name, "error", 0, str(err))

    def save(self, status="done"):
        self.finished_at = datetime.now().isoformat()
        self.status      = status
        record = {
            "run_id":      self.run_id,
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
            "status":      self.status,
            "nodes":       self.nodes,
        }
        runs = []
        if os.path.exists(RUNS_LOG):
            try:
                with open(RUNS_LOG, encoding="utf-8") as f:
                    runs = json.load(f)
            except Exception:
                runs = []
        runs.insert(0, record)
        runs = runs[:30]
        with open(RUNS_LOG, "w", encoding="utf-8") as f:
            json.dump(runs, f, indent=2)
        log.info("Run %s saved to %s", self.run_id, RUNS_LOG)


# ── Startup validation ────────────────────────────────────────
def validate_env():
    missing = []
    if not os.getenv("GROQ_API_KEY") or os.getenv("GROQ_API_KEY", "").startswith("your_"):
        missing.append("GROQ_API_KEY")
    if not os.getenv("SPREADSHEET_ID"):
        missing.append("SPREADSHEET_ID")
    if not os.path.exists("token.json"):
        missing.append("token.json (run: python setup_auth.py)")
    if missing:
        log.error("Missing: %s", ", ".join(missing))
        sys.exit(1)


def banner(text, char="=", width=64):
    log.info(char * width)
    log.info("  %s", text)
    log.info(char * width)


# ── Main pipeline ─────────────────────────────────────────────
def run_pipeline():
    from agents.automation_agent import AutomationAgent
    from agents.screening_agent import ScreeningAgent

    banner("AI Recruiter Copilot")
    validate_env()

    run = PipelineRun()
    counts = dict(total=0, passed=0, rejected=0, scheduled=0, failed=0)

    try:
        # ── Init ──────────────────────────────────────────────
        log.info("--- Initializing agents ---")
        automation_agent = AutomationAgent()
        screening_agent  = ScreeningAgent()
        log.info("[OK] AutomationAgent + ScreeningAgent ready")

        # ── Gmail ─────────────────────────────────────────────
        log.info("--- Scanning Gmail (last %d days) ---", DAYS_BACK)
        run.start("gmail")
        candidates = automation_agent.parse_gmail_resumes(days_back=DAYS_BACK)
        run.done("gmail", len(candidates))

        if not candidates:
            log.info("No resume emails found.")
            run.save()
            banner("Done — 0 candidates", char="-")
            return

        counts["total"] = len(candidates)
        log.info("Found %d candidate(s)", counts["total"])

        # ── PDF parser ────────────────────────────────────────
        run.start("pdf_parser")
        parsed_count = sum(1 for c in candidates if c.resume_text)
        run.done("pdf_parser", parsed_count)

        # Save PDFs to resumes/
        _save_resumes(candidates)

        # ── Screener ──────────────────────────────────────────
        log.info("--- Screening candidates ---")
        run.start("screener")
        screening_results = {}
        for candidate in candidates:
            try:
                result = screening_agent.screen(candidate)
                screening_results[candidate.email] = result
                sym = {"exceptional": "***", "strong": "** ", "moderate": "*  "}.get(result.tier, "   ")
                log.info("  %s %-20s  %3d/100  %-11s  %s",
                         sym, candidate.name, result.overall_score,
                         result.tier.upper(), result.recommendation)
            except Exception as e:
                log.error("Screening error for %s: %s", candidate.name, e)
        run.done("screener", len(screening_results))

        # ── Calendar ──────────────────────────────────────────
        log.info("--- Scheduling interviews ---")
        run.start("calendar")
        scheduled = 0

        for i, candidate in enumerate(candidates, 1):
            name  = candidate.name  or f"Candidate {i}"
            email = candidate.email or ""
            result = screening_results.get(email)

            if result and result.overall_score < SCREENING_THRESHOLD:
                counts["rejected"] += 1
                continue

            counts["passed"] += 1
            try:
                interview_dt = (
                    datetime.now() + timedelta(days=7)
                ).replace(hour=10, minute=0, second=0, microsecond=0)
                cal = automation_agent.schedule_interview_in_calendar(
                    candidate_name=name,
                    candidate_email=email,
                    interview_date=interview_dt.isoformat(),
                )
                if cal.get("success"):
                    scheduled += 1
                    counts["scheduled"] += 1
                    log.info("  SCHED  %s -> %s", name, interview_dt.strftime("%Y-%m-%d %H:%M"))
                else:
                    counts["failed"] += 1
                    log.warning("  SCHED FAIL  %s: %s", name, cal.get("error"))
            except Exception as e:
                counts["failed"] += 1
                log.error("  Calendar error %s: %s", name, e)

        run.done("calendar", scheduled)

        # ── Sheets ────────────────────────────────────────────
        log.info("--- Updating Google Sheets ---")
        run.start("sheets")
        sheet_count = 0

        for i, candidate in enumerate(candidates, 1):
            name   = candidate.name  or f"Candidate {i}"
            email  = candidate.email or ""
            result = screening_results.get(email)

            is_rejected  = result and result.overall_score < SCREENING_THRESHOLD
            status       = (f"Rejected (score: {result.overall_score})"
                            if is_rejected else
                            f"Scheduled (score: {result.overall_score}, {result.tier})"
                            if result else "Pending")
            interview_dt_str = ""
            if not is_rejected and result:
                interview_dt_str = (
                    datetime.now() + timedelta(days=7)
                ).replace(hour=10, minute=0, second=0, microsecond=0).isoformat()

            try:
                automation_agent.update_candidate_in_sheet(
                    candidate_name=name, candidate_email=email,
                    status=status, interview_date=interview_dt_str,
                    spreadsheet_id=SPREADSHEET_ID, tab_name="Candidates",
                    phone=candidate.phone or "",
                    overall_score=result.overall_score if result else 0,
                    tier=result.tier if result else "",
                    recommendation=result.recommendation if result else "",
                    technical_score=result.technical_score if result else 0,
                    experience_score=result.experience_score if result else 0,
                    education_score=result.education_score if result else 0,
                    communication_score=result.communication_score if result else 0,
                    strengths=result.strengths if result else [],
                    gaps=result.gaps if result else [],
                    summary=result.summary if result else "",
                )
                sheet_count += 1
            except Exception as e:
                log.error("  Sheet write failed for %s: %s", name, e)

        run.done("sheets", sheet_count)
        run.save("done")

        banner(
            f"Done  |  {counts['total']} scanned  "
            f"|  {counts['passed']} passed  "
            f"|  {counts['rejected']} rejected  "
            f"|  {counts['scheduled']} scheduled",
            char="-",
        )

    except Exception as e:
        log.error("Fatal: %s", e)
        traceback.print_exc()
        run.save("error")
        sys.exit(1)


def _save_resumes(candidates):
    """Save resume text to resumes/<name>.txt for reference."""
    for c in candidates:
        if c.resume_text and c.name:
            safe_name = "".join(ch for ch in c.name if ch.isalnum() or ch in " _-").strip()
            path = os.path.join("resumes", f"{safe_name}.txt")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(c.resume_text)
            except Exception:
                pass


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as e:
        log.error("Fatal: %s", e)
        traceback.print_exc()
        sys.exit(1)
