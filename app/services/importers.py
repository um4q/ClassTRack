"""
Practice-question import/export and optional syllabus PDF date scanning.
See build spec §5.6 ("Manage: ... import CSV ... and pasted Q:/A:/E: blocks,
export CSV") and §5.11 ("Import syllabus PDF (regex date scan -> preview ->
add assessments)").

Pure Python only - no Qt. All new PracticeQuestion objects are handed back
with question_id=0, box=1, times_attempted=0, times_correct=0;
ExcelStore.add_practice_question assigns the real id.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Optional

from app.models import PracticeQuestion

logger = logging.getLogger("study_tracker")

# Header row shared by parse_practice_csv / export_practice_csv, so export
# round-trips straight back through import.
CSV_HEADER = ["question", "options", "answer", "explanation", "topic", "difficulty", "tags"]


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------
def parse_practice_csv(csv_text: str, course_id: int) -> list[PracticeQuestion]:
    """Parse a CSV string (header: question,options,answer,explanation,topic,
    difficulty,tags) into new PracticeQuestion objects for course_id.

    options is "|"-joined for MCQ, blank otherwise. topic is a topic_id int
    or blank. difficulty is 1-5, default 1 on anything unparsable. tags is
    comma-separated. Malformed rows are skipped (logged), never raised.
    """
    questions: list[PracticeQuestion] = []
    if not csv_text or not csv_text.strip():
        return questions

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except Exception:
        logger.exception("parse_practice_csv: could not open CSV text as a DictReader")
        return questions

    for i, row in enumerate(reader):
        try:
            q = _row_to_question(row, course_id)
            if q is not None:
                questions.append(q)
        except Exception:
            logger.warning("parse_practice_csv: skipping malformed row %d: %r", i, row, exc_info=True)
    return questions


def _row_to_question(row: dict, course_id: int) -> Optional[PracticeQuestion]:
    question_text = (row.get("question") or "").strip()
    if not question_text:
        return None

    options_raw = (row.get("options") or "").strip()
    options = [o.strip() for o in options_raw.split("|") if o.strip()] if options_raw else []
    question_type = "MCQ" if options else "Short Answer"

    topic_raw = (row.get("topic") or "").strip()
    topic_id: Optional[int] = None
    if topic_raw:
        try:
            topic_id = int(float(topic_raw))
        except (TypeError, ValueError):
            topic_id = None

    difficulty = _parse_difficulty(row.get("difficulty"))

    tags_raw = (row.get("tags") or "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    return PracticeQuestion(
        question_id=0,
        course_id=course_id,
        topic_id=topic_id,
        question_type=question_type,
        question_text=question_text,
        options=options,
        answer_text=(row.get("answer") or "").strip(),
        explanation=(row.get("explanation") or "").strip(),
        difficulty=difficulty,
        tags=tags,
        box=1,
        times_attempted=0,
        times_correct=0,
        last_attempted=None,
        next_due=None,
        image_path="",
    )


def _parse_difficulty(raw) -> int:
    try:
        d = int(float(str(raw).strip()))
    except (TypeError, ValueError, AttributeError):
        return 1
    if d < 1 or d > 5:
        return 1
    return d


# --------------------------------------------------------------------------
# Pasted-text (Q:/A:/E:) import
# --------------------------------------------------------------------------
def parse_practice_pasted(text: str, course_id: int) -> list[PracticeQuestion]:
    """Parse pasted text made of blank-line-separated blocks, each block
    holding lines starting "Q:", "A:", and optionally "E:" (explanation).
    Everything from "Q:" up to the next "A:" line (inclusive of wrapped
    lines) is the question text. Malformed blocks (no Q: or no A:) are
    skipped rather than raised.
    """
    questions: list[PracticeQuestion] = []
    if not text or not text.strip():
        return questions

    # Split into blocks on one-or-more blank lines.
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        try:
            q = _parse_one_block(block, course_id)
            if q is not None:
                questions.append(q)
        except Exception:
            logger.warning("parse_practice_pasted: skipping malformed block: %r", block, exc_info=True)
    return questions


def _parse_one_block(block: str, course_id: int) -> Optional[PracticeQuestion]:
    lines = block.splitlines()

    question_lines: list[str] = []
    answer_lines: list[str] = []
    explanation_lines: list[str] = []
    section = None  # None | "Q" | "A" | "E"

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Q:"):
            section = "Q"
            question_lines.append(stripped[2:].strip())
            continue
        if stripped.startswith("A:"):
            section = "A"
            answer_lines.append(stripped[2:].strip())
            continue
        if stripped.startswith("E:"):
            section = "E"
            explanation_lines.append(stripped[2:].strip())
            continue
        # Continuation line (wrapped text) - appends to whichever section is
        # currently open.
        if not stripped:
            continue
        if section == "Q":
            question_lines.append(stripped)
        elif section == "A":
            answer_lines.append(stripped)
        elif section == "E":
            explanation_lines.append(stripped)
        # else: stray text before any "Q:" - ignore.

    question_text = " ".join(l for l in question_lines if l).strip()
    answer_text = " ".join(l for l in answer_lines if l).strip()
    explanation = " ".join(l for l in explanation_lines if l).strip()

    if not question_text or not answer_text:
        return None

    return PracticeQuestion(
        question_id=0,
        course_id=course_id,
        topic_id=None,
        question_type="Short Answer",
        question_text=question_text,
        options=[],
        answer_text=answer_text,
        explanation=explanation,
        difficulty=1,
        tags=[],
        box=1,
        times_attempted=0,
        times_correct=0,
        last_attempted=None,
        next_due=None,
        image_path="",
    )


# --------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------
def export_practice_csv(questions: list[PracticeQuestion]) -> str:
    """Serialize questions to CSV text with the same header used by
    parse_practice_csv, so export round-trips back through import."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_HEADER, lineterminator="\n")
    writer.writeheader()
    for q in questions:
        try:
            writer.writerow({
                "question": q.question_text or "",
                "options": "|".join(q.options or []),
                "answer": q.answer_text or "",
                "explanation": q.explanation or "",
                "topic": "" if q.topic_id is None else str(q.topic_id),
                "difficulty": str(q.difficulty if q.difficulty else 1),
                "tags": ",".join(q.tags or []),
            })
        except Exception:
            logger.warning("export_practice_csv: skipping unwritable question %r", q, exc_info=True)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Syllabus PDF date scan (best effort, optional pypdf dependency; §5.11)
# --------------------------------------------------------------------------
_DATE_KEYWORDS = ("midterm", "exam", "final", "assessment", "quiz", "due")

_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

# "Week of Month Day" / "Month Day, Year" / "Month Day" (year optional)
_MONTH_DAY_RE = re.compile(
    rf"\b(?:Week of\s+)?(?P<month>{_MONTH_NAMES})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s*(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)
# ISO "YYYY-MM-DD"
_ISO_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")

_MONTH_LOOKUP = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_DEFAULT_YEAR = 2026  # Fall 2026 term; used when a Month/Day date has no year.


def _resolve_month_day(month_str: str, day_str: str, year_str: Optional[str]) -> Optional[str]:
    month = _MONTH_LOOKUP.get(month_str.strip(".").lower())
    if month is None:
        return None
    try:
        day = int(day_str)
    except ValueError:
        return None
    year = int(year_str) if year_str else _DEFAULT_YEAR
    if not (1 <= day <= 31):
        return None
    try:
        from datetime import date as _date
        _date(year, month, day)  # validates day-in-month
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def _resolve_iso(year_str: str, month_str: str, day_str: str) -> Optional[str]:
    try:
        year, month, day = int(year_str), int(month_str), int(day_str)
        from datetime import date as _date
        _date(year, month, day)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def scan_syllabus_pdf_dates(pdf_path) -> list[dict]:
    """Best-effort regex scan of a syllabus PDF for lines that look like a
    date near an exam/assessment keyword. Returns a list of
    {"title": str, "date": "YYYY-MM-DD"|None, "page": int, "raw_line": str}
    dicts, one per matching line. Never raises - any failure (missing
    pypdf, corrupt/unreadable PDF, ...) is logged and an empty list is
    returned.
    """
    try:
        try:
            import pypdf  # optional dependency
        except ImportError:
            logger.warning("scan_syllabus_pdf_dates: pypdf is not installed; skipping PDF date scan")
            return []

        path = Path(pdf_path)
        if not path.exists():
            logger.warning("scan_syllabus_pdf_dates: file not found: %s", path)
            return []

        results: list[dict] = []
        reader = pypdf.PdfReader(str(path))
        for page_index, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                logger.warning("scan_syllabus_pdf_dates: could not extract text from page %d of %s",
                                page_index + 1, path, exc_info=True)
                continue

            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                lowered = line.lower()
                if not any(kw in lowered for kw in _DATE_KEYWORDS):
                    continue

                date_str = None
                m = _ISO_RE.search(line)
                if m:
                    date_str = _resolve_iso(m.group("year"), m.group("month"), m.group("day"))
                if date_str is None:
                    m = _MONTH_DAY_RE.search(line)
                    if m:
                        date_str = _resolve_month_day(m.group("month"), m.group("day"), m.group("year"))

                results.append({
                    "title": line,
                    "date": date_str,
                    "page": page_index + 1,
                    "raw_line": raw_line,
                })
        return results
    except Exception:
        logger.warning("scan_syllabus_pdf_dates: failed to scan %s", pdf_path, exc_info=True)
        return []
