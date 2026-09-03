"""
Week numbers, agendas, the Mon-Fri week view, and roadmap generation.

See build spec §3 (term week number), §6.7 (roadmap generation) and §6.8
(agenda). Pure functions only - callers pass in already-loaded rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Optional

from app import config
from app.models import Assessment, Holiday, PreLab, RoadmapItem, ScheduleRow, Topic


# --------------------------------------------------------------------------
# Week numbers
# --------------------------------------------------------------------------
def term_week_number(d: date, week1_monday: date) -> int:
    """Week 1 = the 7-day block starting week1_monday (e.g. Sep 1-4 2026)."""
    return ((d - week1_monday).days // 7) + 1


def week_start(week_number: int, week1_monday: date) -> date:
    """The Monday of the given 1-based term week."""
    return week1_monday + timedelta(weeks=week_number - 1)


def week_monday_containing(d: date, week1_monday: date) -> date:
    return week_start(term_week_number(d, week1_monday), week1_monday)


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------
def is_holiday(d: date, holidays: list[Holiday]) -> bool:
    return any(h.date == d and h.classes_cancelled for h in holidays)


def holiday_dates(holidays: list[Holiday]) -> set[date]:
    return {h.date for h in holidays if h.date and h.classes_cancelled}


# --------------------------------------------------------------------------
# Agenda / week view
# --------------------------------------------------------------------------
@dataclass
class AgendaItem:
    kind: str  # "Lecture" | "Lab" | "Tutorial" | "Seminar" | "Study Block" | "PreLab" | "Assessment"
    course_id: int
    title: str
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    room: str = ""
    ref_id: int = 0
    flagged: bool = False


def schedule_row_occurs_on(row: ScheduleRow, d: date) -> bool:
    """A weekly row (no explicit dates) occurs on its day_of_week every
    week; a row with explicit dates occurs ONLY on those dates (used for
    the bi-weekly Friday labs)."""
    if row.dates:
        return d in row.dates
    return config.WEEKDAY_NAMES[d.weekday()] == row.day_of_week


def agenda(
    d: date,
    schedule: list[ScheduleRow],
    holidays: list[Holiday],
    prelabs: list[PreLab],
    assessments: list[Assessment],
) -> list[AgendaItem]:
    """Schedule rows on this date (minus holidays) + PreLabs (lab_type !=
    'None') + Assessments due this date, sorted by time. See spec §6.8."""
    items: list[AgendaItem] = []
    if not is_holiday(d, holidays):
        for r in schedule:
            if schedule_row_occurs_on(r, d):
                items.append(AgendaItem(
                    kind=r.kind, course_id=r.course_id,
                    title=f"{r.kind} ({r.section})", start_time=r.start_time,
                    end_time=r.end_time, room=r.room, ref_id=r.schedule_id,
                ))
    for p in prelabs:
        if p.lab_date == d and p.lab_type != "None":
            items.append(AgendaItem(
                kind="PreLab", course_id=p.course_id,
                title=f"Lab {p.lab_number}: {p.title}", start_time=p.lab_time,
                room=p.room, ref_id=p.prelab_id,
            ))
    for a in assessments:
        if a.due_date == d:
            items.append(AgendaItem(
                kind="Assessment", course_id=a.course_id,
                title=f"{a.type}: {a.title}", start_time=a.due_time,
                room=a.location, ref_id=a.assessment_id, flagged=a.is_flagged,
            ))
    items.sort(key=lambda i: i.start_time or time(23, 59))
    return items


def week_view(
    monday: date,
    schedule: list[ScheduleRow],
    holidays: list[Holiday],
    prelabs: list[PreLab],
    assessments: list[Assessment],
) -> dict[date, list[AgendaItem]]:
    """Monday-Friday agenda for the term week starting on ``monday``."""
    return {
        monday + timedelta(days=i): agenda(monday + timedelta(days=i), schedule, holidays, prelabs, assessments)
        for i in range(5)
    }


# --------------------------------------------------------------------------
# Roadmap generation (§6.7)
# --------------------------------------------------------------------------
def _section_range_label(topics: list[Topic]) -> str:
    secs = [t.section for t in topics if t.section]
    if not secs:
        return "; ".join(t.title[:40] for t in topics[:2]) or "Review"
    if len(secs) == 1:
        return secs[0]
    return f"{secs[0]}–{secs[-1]}"


def generate_roadmap(
    course_id: int,
    topics: list[Topic],
    week1_monday: date,
    start_from: date,
    horizon_end: date,
    target_topic_ids: Optional[set[int]] = None,
) -> list[RoadmapItem]:
    """Weekly milestones covering non-Mastered topics (optionally restricted
    to ``target_topic_ids``, e.g. an exam's coverage) from ``start_from``
    up to 3 days before ``horizon_end`` (an exam date or semester_end),
    distributed by estimated_hours, plus a final 3-day "Review + mock exam"
    milestone. Every returned row has auto_generated=True; the caller is
    responsible for deleting old auto rows first (see
    ExcelStore.delete_auto_roadmap) so a re-run replaces only those."""
    pool = [
        t for t in topics
        if t.status != "Mastered" and (target_topic_ids is None or t.topic_id in target_topic_ids)
    ]
    pool.sort(key=lambda t: (t.unit, t.section))
    review_start = horizon_end - timedelta(days=3)
    if not pool or review_start <= start_from:
        return [RoadmapItem(
            course_id=course_id, sort_order=1,
            milestone=f"Review + mock exam ({review_start.strftime('%b %d')}-{horizon_end.strftime('%b %d')})",
            start_date=max(start_from, review_start), end_date=horizon_end,
            topic_ids=[t.topic_id for t in pool], status="Planned",
            auto_generated=True, notes="",
        )]

    weeks: list[tuple[date, date]] = []
    cursor = week_monday_containing(start_from, week1_monday)
    while cursor <= review_start - timedelta(days=1):
        w_end = min(cursor + timedelta(days=4), review_start - timedelta(days=1))
        weeks.append((max(cursor, start_from), w_end))
        cursor += timedelta(weeks=1)
    if not weeks:
        weeks = [(start_from, review_start - timedelta(days=1))]

    total_hours = sum(t.estimated_hours or config.DEFAULT_ESTIMATED_HOURS for t in pool)
    hours_per_week = total_hours / len(weeks)

    items: list[RoadmapItem] = []
    idx = 0
    for w_start, w_end in weeks:
        bucket: list[Topic] = []
        bucket_hours = 0.0
        while idx < len(pool) and (bucket_hours < hours_per_week or not bucket):
            t = pool[idx]
            bucket.append(t)
            bucket_hours += t.estimated_hours or config.DEFAULT_ESTIMATED_HOURS
            idx += 1
        if not bucket:
            continue
        wk_num = term_week_number(w_start, week1_monday)
        items.append(RoadmapItem(
            course_id=course_id, sort_order=len(items) + 1,
            milestone=f"Week {wk_num} ({w_start.strftime('%b %d')}): {_section_range_label(bucket)}",
            start_date=w_start, end_date=w_end,
            topic_ids=[t.topic_id for t in bucket], status="Planned",
            auto_generated=True, notes="",
        ))
    if idx < len(pool) and items:
        items[-1].topic_ids.extend(t.topic_id for t in pool[idx:])
    elif idx < len(pool):
        items.append(RoadmapItem(
            course_id=course_id, sort_order=1,
            milestone=_section_range_label(pool[idx:]),
            start_date=start_from, end_date=review_start - timedelta(days=1),
            topic_ids=[t.topic_id for t in pool[idx:]], status="Planned",
            auto_generated=True, notes="",
        ))

    review_wk = term_week_number(review_start, week1_monday)
    items.append(RoadmapItem(
        course_id=course_id, sort_order=len(items) + 1,
        milestone=f"Week {review_wk}: Review + mock exam",
        start_date=review_start, end_date=horizon_end,
        topic_ids=[], status="Planned", auto_generated=True, notes="",
    ))
    return items


def roadmap_pace(topics: list[Topic], today: date, weeks_remaining: float) -> tuple[float, float]:
    """(current_pace, needed_pace) in topics-mastered-per-week."""
    mastered_recent = sum(
        1 for t in topics
        if t.status == "Mastered" and t.last_reviewed and (today - t.last_reviewed).days <= 14
    )
    current_pace = mastered_recent / 2.0
    remaining = sum(1 for t in topics if t.status != "Mastered")
    needed_pace = (remaining / weeks_remaining) if weeks_remaining > 0 else float("inf")
    return current_pace, needed_pace
