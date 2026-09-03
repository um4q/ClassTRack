"""
.ics (RFC5545) calendar export - assessments, pre-labs, and (optionally)
recurring/explicit-date class sessions from the weekly Schedule.

See build spec §5.11. Pure Python only - no Qt, no openpyxl, no external
`ics` library. Callers (the Settings page) fetch rows via ExcelStore and
pass them in; this module only builds the .ics text.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from app import config
from app.models import Assessment, Course, Holiday, PreLab, ScheduleRow

_CRLF = "\r\n"

# ScheduleRow.day_of_week -> RFC5545 2-letter BYDAY code.
_DAY_CODES = {
    "Monday": "MO", "Tuesday": "TU", "Wednesday": "WE", "Thursday": "TH",
    "Friday": "FR", "Saturday": "SA", "Sunday": "SU",
}


# --------------------------------------------------------------------------
# RFC5545 text helpers
# --------------------------------------------------------------------------
def _escape_text(s: Optional[str]) -> str:
    """Escape backslash, comma, semicolon and newlines per RFC5545 §3.3.11."""
    if not s:
        return ""
    s = s.replace("\\", "\\\\")
    s = s.replace(";", "\\;")
    s = s.replace(",", "\\,")
    s = s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return s


def _fold(line: str) -> str:
    """Fold a content line longer than 75 chars per RFC5545 §3.1 (each
    continuation line starts with a single space)."""
    if len(line) <= 75:
        return line
    parts = [line[:75]]
    rest = line[75:]
    while len(rest) > 74:
        parts.append(" " + rest[:74])
        rest = rest[74:]
    if rest:
        parts.append(" " + rest)
    return _CRLF.join(parts)


def _dtstamp_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _first_weekday_on_or_after(d: date, day_of_week: str) -> date:
    """The first date >= d that falls on the given weekday name."""
    try:
        target_idx = config.WEEKDAY_NAMES.index(day_of_week)
    except ValueError:
        return d
    delta = (target_idx - d.weekday()) % 7
    return d + timedelta(days=delta)


def _event_lines(
    uid: str,
    summary: str,
    start_date: date,
    start_time: Optional[time],
    end_time: Optional[time] = None,
    location: str = "",
    description: str = "",
    rrule: Optional[str] = None,
) -> list[str]:
    """Build the BEGIN:VEVENT..END:VEVENT lines for one event. A ``None``
    ``start_time`` produces an all-day (DATE) event; otherwise a timed
    event, defaulting to a 1-hour duration when no ``end_time`` is given."""
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{_dtstamp_now()}"]

    if start_time is None:
        lines.append(f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}")
        lines.append(f"DTEND;VALUE=DATE:{(start_date + timedelta(days=1)).strftime('%Y%m%d')}")
    else:
        lines.append(f"DTSTART:{start_date.strftime('%Y%m%d')}T{start_time.strftime('%H%M%S')}")
        if end_time is not None:
            end_dt = datetime.combine(start_date, end_time)
        else:
            end_dt = datetime.combine(start_date, start_time) + timedelta(hours=1)
        lines.append(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}")

    if rrule:
        lines.append(f"RRULE:{rrule}")

    lines.append(f"SUMMARY:{_escape_text(summary)}")
    if location:
        lines.append(f"LOCATION:{_escape_text(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape_text(description)}")

    lines.append("END:VEVENT")
    return lines


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def build_ics(
    courses: list[Course],
    assessments: list[Assessment],
    prelabs: list[PreLab],
    schedule: Optional[list[ScheduleRow]] = None,
    holidays: Optional[list[Holiday]] = None,
    include_classes: bool = False,
    semester_start: Optional[date] = None,
    semester_end: Optional[date] = None,
) -> str:
    """Return a complete RFC5545 .ics text (CRLF line endings) with:

    - one VEVENT per Assessment with a due_date (all-day if due_time is
      None), UID ``assessment-<id>@studytracker``;
    - one VEVENT per PreLab with ``lab_type != "None"`` and a lab_date,
      UID ``prelab-<id>@studytracker``;
    - if ``include_classes`` and ``schedule``/``semester_start``/
      ``semester_end`` are all given: for each ScheduleRow, either one
      VEVENT per explicit date (when ``row.dates`` is non-empty - e.g. the
      bi-weekly Friday labs) or a single weekly RRULE VEVENT bounded by
      ``semester_end`` (when ``row.dates`` is empty). Missing semester
      bounds simply omit class events - never an error.

    Text fields are escaped per RFC5545 §3.3.11. Pure Python, no Qt.
    """
    course_map = {c.course_id: c.code for c in courses}
    holidays = holidays or []
    holiday_dates = {h.date for h in holidays if h.date and h.classes_cancelled}

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Study Tracker//NAIT Instrumentation//EN",
        "CALSCALE:GREGORIAN",
    ]

    for a in assessments:
        if not a.due_date:
            continue
        code = course_map.get(a.course_id, "?")
        lines.extend(_event_lines(
            uid=f"assessment-{a.assessment_id}@studytracker",
            summary=f"{code}: {a.type} - {a.title}",
            start_date=a.due_date,
            start_time=a.due_time,
            location=a.location or "",
            description=a.notes or "",
        ))

    for p in prelabs:
        if p.lab_type == "None" or not p.lab_date:
            continue
        code = course_map.get(p.course_id, "?")
        lines.extend(_event_lines(
            uid=f"prelab-{p.prelab_id}@studytracker",
            summary=f"{code}: Lab {p.lab_number} - {p.title}",
            start_date=p.lab_date,
            start_time=p.lab_time,
            location=p.room or "",
            description=p.notes or "",
        ))

    if include_classes and schedule and semester_start and semester_end:
        for row in schedule:
            code = course_map.get(row.course_id, "?")
            summary = f"{code}: {row.kind} ({row.section})" if row.section else f"{code}: {row.kind}"
            if row.dates:
                # Explicit-date row (e.g. bi-weekly labs): one VEVENT per
                # date - simpler and equally correct as an RRULE+EXDATE.
                for d in row.dates:
                    if d < semester_start or d > semester_end or d in holiday_dates:
                        continue
                    lines.extend(_event_lines(
                        uid=f"schedule-{row.schedule_id}-{d.isoformat()}@studytracker",
                        summary=summary,
                        start_date=d,
                        start_time=row.start_time,
                        end_time=row.end_time,
                        location=row.room or "",
                    ))
            else:
                day_code = _DAY_CODES.get(row.day_of_week)
                if not day_code:
                    continue
                first = _first_weekday_on_or_after(semester_start, row.day_of_week)
                if first > semester_end:
                    continue
                until = f"{semester_end.strftime('%Y%m%d')}T235959Z"
                rrule = f"FREQ=WEEKLY;BYDAY={day_code};UNTIL={until}"
                lines.extend(_event_lines(
                    uid=f"schedule-{row.schedule_id}@studytracker",
                    summary=summary,
                    start_date=first,
                    start_time=row.start_time,
                    end_time=row.end_time,
                    location=row.room or "",
                    rrule=rrule,
                ))

    lines.append("END:VCALENDAR")
    return _CRLF.join(_fold(l) for l in lines) + _CRLF
