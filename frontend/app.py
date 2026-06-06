"""Flask dashboard for AI Recruiter Copilot."""

import os
import sys
import json
import subprocess
import threading
import queue
from datetime import datetime
from glob import glob

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from flask import Flask, render_template, jsonify, request, Response, stream_with_context
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

app = Flask(__name__)

PROJECT_ROOT   = os.path.join(os.path.dirname(__file__), "..")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
RUNS_LOG       = os.path.join(PROJECT_ROOT, "logs", "pipeline_runs.json")
JD_DIR         = os.path.join(PROJECT_ROOT, "configs", "job_descriptions")
PYTHON_BIN     = sys.executable  # works locally (.venv) and on server

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
TIER_ORDER = {"exceptional": 0, "strong": 1, "moderate": 2, "weak": 3, "": 4}

# Active pipeline subprocess queues  {run_id: Queue}
_pipeline_queues: dict[str, queue.Queue] = {}


# ── Google helpers ────────────────────────────────────────────
def get_sheets_service():
    try:
        creds = _load_google_creds()
        if not creds:
            return None
        return build("sheets", "v4", credentials=creds)
    except Exception:
        return None


def _load_google_creds():
    """Load Google credentials from token.json file OR GOOGLE_TOKEN env var (base64)."""
    import base64, json as _json
    from google.oauth2.credentials import Credentials as _Creds

    # 1. Try env var (Railway / production)
    token_b64 = os.getenv("GOOGLE_TOKEN_B64")
    if token_b64:
        try:
            token_data = base64.b64decode(token_b64).decode()
            return _Creds.from_authorized_user_info(_json.loads(token_data), SCOPES)
        except Exception as e:
            print(f"GOOGLE_TOKEN_B64 decode error: {e}")

    # 2. Fall back to local file (dev)
    token_path = os.path.join(PROJECT_ROOT, "token.json")
    if os.path.exists(token_path):
        try:
            return _Creds.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            print(f"token.json load error: {e}")

    return None


# ── Candidate helpers ─────────────────────────────────────────
COL = {
    "name": 0, "email": 1, "phone": 2,
    "score": 3, "tier": 4, "recommendation": 5,
    "technical_score": 6, "experience_score": 7,
    "education_score": 8, "communication_score": 9,
    "strengths": 10, "gaps": 11, "summary": 12,
    "status": 13, "interview_date": 14, "last_updated": 15,
}

def _cell(row, key, default=""):
    idx = COL[key]
    return row[idx] if len(row) > idx else default

def _int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return None

def _split(val):
    return [s.strip() for s in val.split(";") if s.strip()] if val else []


def fetch_candidates():
    service = get_sheets_service()
    if not service:
        print("WARNING: Could not connect to Google Sheets (no token or invalid credentials)")
        return []
    if not SPREADSHEET_ID:
        print("WARNING: SPREADSHEET_ID not set in .env")
        return []
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="Candidates!A:P",
        ).execute()
        rows = result.get("values", [])
        if not rows or len(rows) < 2:
            return []  # Sheet exists but no candidates yet

        candidates = []
        for row in rows[1:]:
            if not row or not row[0]:
                continue
            candidates.append({
                "name":                _cell(row, "name", "Unknown"),
                "email":               _cell(row, "email"),
                "phone":               _cell(row, "phone"),
                "score":               _int(_cell(row, "score")),
                "tier":                _cell(row, "tier"),
                "recommendation":      _cell(row, "recommendation"),
                "technical_score":     _int(_cell(row, "technical_score")),
                "experience_score":    _int(_cell(row, "experience_score")),
                "education_score":     _int(_cell(row, "education_score")),
                "communication_score": _int(_cell(row, "communication_score")),
                "strengths":           _split(_cell(row, "strengths")),
                "gaps":                _split(_cell(row, "gaps")),
                "summary":             _cell(row, "summary"),
                "status":              _cell(row, "status"),
                "interview_date":      _cell(row, "interview_date"),
            })

        candidates.sort(key=lambda c: (
            TIER_ORDER.get(c["tier"], 4),
            -(c["score"] or 0),
        ))
        return candidates

    except Exception as e:
        print(f"Sheets error: {e}")
        return []


def compute_stats(candidates):
    return {
        "total":       len(candidates),
        "passed":      sum(1 for c in candidates if (c["score"] or 0) >= 60),
        "rejected":    sum(1 for c in candidates if (c["score"] or 0) < 60),
        "scheduled":   sum(1 for c in candidates if c.get("interview_date")),
        "exceptional": sum(1 for c in candidates if c.get("tier") == "exceptional"),
    }


# ── Job descriptions ──────────────────────────────────────────
def get_job_roles():
    roles = []
    for path in sorted(glob(os.path.join(JD_DIR, "*.txt"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        label = slug.replace("_", " ").title()
        roles.append({"slug": slug, "label": label, "path": path})
    return roles


# ── Routes: Dashboard ─────────────────────────────────────────
@app.route("/")
def dashboard():
    candidates = fetch_candidates()
    stats      = compute_stats(candidates)
    roles      = get_job_roles()
    return render_template("dashboard.html",
        candidates=candidates, stats=stats,
        active_filter="all",
        roles=roles,
        now=datetime.now().strftime("%d %b %Y, %H:%M"))

@app.route("/api/candidates")
def api_candidates():
    return jsonify(fetch_candidates())


# ── Routes: Pipeline visualizer ───────────────────────────────
@app.route("/pipeline")
def pipeline_view():
    runs  = _load_runs()
    roles = get_job_roles()
    return render_template("pipeline.html",
        runs=runs, roles=roles,
        now=datetime.now().strftime("%d %b %Y, %H:%M"))

@app.route("/api/pipeline/runs")
def api_runs():
    return jsonify(_load_runs())

@app.route("/api/pipeline/run", methods=["POST"])
def api_run_pipeline():
    """Spawn main.py in background; return run_id for SSE stream."""
    data    = request.get_json(silent=True) or {}
    role    = data.get("role", "software_engineer")
    run_id  = datetime.now().strftime("%Y%m%d_%H%M%S")
    q: queue.Queue = queue.Queue()
    _pipeline_queues[run_id] = q

    env = os.environ.copy()
    env["PIPELINE_ROLE"] = role

    def _run():
        try:
            proc = subprocess.Popen(
                [PYTHON_BIN, os.path.join(PROJECT_ROOT, "main.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                cwd=PROJECT_ROOT, env=env,
            )
            for line in proc.stdout:
                q.put(line.rstrip())
            proc.wait()
        except Exception as e:
            q.put(f"ERROR: {e}")
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"run_id": run_id})

@app.route("/api/pipeline/stream/<run_id>")
def api_pipeline_stream(run_id):
    """SSE: stream live output of a running pipeline."""
    q = _pipeline_queues.get(run_id)
    if not q:
        return Response("data: {\"error\": \"run not found\"}\n\n",
                        content_type="text/event-stream")

    def generate():
        while True:
            try:
                line = q.get(timeout=30)
            except queue.Empty:
                yield "event: heartbeat\ndata: {}\n\n"
                continue
            if line is None:
                yield "event: done\ndata: {}\n\n"
                _pipeline_queues.pop(run_id, None)
                break
            yield f"data: {json.dumps({'line': line})}\n\n"

    return Response(stream_with_context(generate()),
                    content_type="text/event-stream",
                    headers={"X-Accel-Buffering": "no",
                             "Cache-Control": "no-cache"})


# ── Routes: Candidate reply ────────────────────────────────────
@app.route("/api/reply", methods=["POST"])
def api_reply():
    """Generate a reply email draft for a candidate using Groq."""
    data = request.get_json(silent=True) or {}
    candidate = data.get("candidate", {})
    reply_type = data.get("type", "shortlist")  # shortlist | rejection

    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))

        if reply_type == "shortlist":
            instruction = (
                f"Write a warm, professional shortlist email to {candidate.get('name')} "
                f"for a software engineering role. They scored {candidate.get('score')}/100 "
                f"and were rated {candidate.get('tier')}. "
                f"Mention 1-2 of their strengths: {', '.join(candidate.get('strengths', [])[:2])}. "
                f"Invite them to an interview. Keep it under 120 words. "
                f"Return JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
            )
        else:
            instruction = (
                f"Write a respectful, encouraging rejection email to {candidate.get('name')} "
                f"for a software engineering role. They scored {candidate.get('score')}/100. "
                f"Be kind, keep it brief, leave the door open for future roles. "
                f"Under 100 words. "
                f"Return JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
            )

        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": instruction}],
        )
        draft = json.loads(resp.choices[0].message.content)
        return jsonify({"success": True, "subject": draft.get("subject", ""), "body": draft.get("body", "")})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Routes: Job descriptions ──────────────────────────────────
@app.route("/api/roles")
def api_roles():
    return jsonify(get_job_roles())


# ── Helpers ───────────────────────────────────────────────────
def _load_runs():
    if not os.path.exists(RUNS_LOG):
        return []
    try:
        with open(RUNS_LOG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


if __name__ == "__main__":
    app.run(debug=True, port=5050)
