"""LLM-powered candidate screening agent using Groq."""

import os
import json
import logging
from dataclasses import dataclass
from typing import Optional
from groq import Groq

logger = logging.getLogger(__name__)


@dataclass
class ScreeningResult:
    overall_score: int          # 0-100
    technical_score: int        # 0-100
    experience_score: int       # 0-100
    education_score: int        # 0-100
    communication_score: int    # 0-100
    tier: str                   # exceptional / strong / moderate / weak
    recommendation: str         # fast-track / interview / conditional / reject
    strengths: list[str]
    gaps: list[str]
    summary: str
    raw_response: str


SYSTEM_PROMPT = """You are an expert technical recruiter. Given a job description and a candidate's resume text, evaluate the candidate and return a JSON object with this exact structure:

{
  "overall_score": <integer 0-100>,
  "technical_score": <integer 0-100>,
  "experience_score": <integer 0-100>,
  "education_score": <integer 0-100>,
  "communication_score": <integer 0-100>,
  "tier": "<exceptional|strong|moderate|weak>",
  "recommendation": "<fast-track|interview|conditional|reject>",
  "strengths": ["<strength 1>", "<strength 2>", ...],
  "gaps": ["<gap 1>", "<gap 2>", ...],
  "summary": "<2-3 sentence plain-English summary>"
}

Scoring tiers:
- exceptional: 85-100 → fast-track to final round
- strong: 70-84 → proceed to technical interview
- moderate: 60-69 → conditional interview
- weak: 0-59 → reject or talent pool

Return ONLY valid JSON — no markdown, no explanation outside the JSON."""


class ScreeningAgent:
    """Screens candidates against a job description using Groq."""

    # Best Groq model for structured JSON output
    MODEL = "llama-3.3-70b-versatile"

    def __init__(self, job_description: Optional[str] = None, job_file: Optional[str] = None):
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "your_groq_api_key_here":
            raise ValueError("GROQ_API_KEY is not set in your .env file")

        self.client = Groq(api_key=api_key)

        if job_description:
            self.job_description = job_description
        elif job_file:
            with open(job_file) as f:
                self.job_description = f.read()
        else:
            default_jd = "configs/job_descriptions/software_engineer.txt"
            if os.path.exists(default_jd):
                with open(default_jd) as f:
                    self.job_description = f.read()
            else:
                self.job_description = "Software Engineer — Python, SQL, REST APIs, 2+ years experience"

    def screen(self, candidate) -> ScreeningResult:
        """Score a CandidateProfile against the job description.

        Args:
            candidate: CandidateProfile dataclass instance

        Returns:
            ScreeningResult with scores, tier, and reasoning
        """
        resume_text = candidate.resume_text or ""
        skills_hint = ", ".join(candidate.skills) if candidate.skills else "unknown"

        user_message = f"""JOB DESCRIPTION:
{self.job_description}

CANDIDATE: {candidate.name}
EXTRACTED SKILLS: {skills_hint}
EDUCATION: {candidate.education or 'not specified'}
EXPERIENCE: {candidate.experience or 'not specified'}

RESUME TEXT:
{resume_text[:4000]}"""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                max_tokens=1024,
                response_format={"type": "json_object"},  # forces valid JSON output
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_message},
                ],
            )

            raw = response.choices[0].message.content.strip()
            data = json.loads(raw)

            return ScreeningResult(
                overall_score=data.get("overall_score", 0),
                technical_score=data.get("technical_score", 0),
                experience_score=data.get("experience_score", 0),
                education_score=data.get("education_score", 0),
                communication_score=data.get("communication_score", 0),
                tier=data.get("tier", "weak"),
                recommendation=data.get("recommendation", "reject"),
                strengths=data.get("strengths", []),
                gaps=data.get("gaps", []),
                summary=data.get("summary", ""),
                raw_response=raw,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Groq returned invalid JSON for {candidate.name}: {e}")
            return self._fallback_result(candidate.name, str(e))
        except Exception as e:
            logger.error(f"Screening failed for {candidate.name}: {e}")
            return self._fallback_result(candidate.name, str(e))

    def _fallback_result(self, name: str, error: str) -> ScreeningResult:
        return ScreeningResult(
            overall_score=0,
            technical_score=0,
            experience_score=0,
            education_score=0,
            communication_score=0,
            tier="weak",
            recommendation="reject",
            strengths=[],
            gaps=[f"Screening error: {error}"],
            summary=f"Could not screen {name} due to an error.",
            raw_response="",
        )
