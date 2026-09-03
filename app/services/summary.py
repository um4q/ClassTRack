"""
Weekly summary: hours vs goal, submissions/grading, mastery, pre-lab
punctuality, overdue items, at-risk courses, mock results, slipped
milestones. See build spec §6.9 and §5.11 "Show weekly summary now".

Pure functions/dataclasses - callers (ExcelStore-backed UI) fetch every
sheet's full (un-filtered) rows and pass them in via WeeklySummaryContext;
this module does the per-course filtering itself. No Qt imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from app.models import (
    Assessment,
    Course,
    Goal,
    GradeWeight,
    MockExam,
    PreLab,
    RoadmapItem,
    StudyLogEntry,
    Topic,
)
from app.services import grades


def iso_week_str(d: date) -> str:
    """e.g. date(2026, 9, 10) -> '2026-W37' (ISO year/week)."""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


# --------------------------------------------------------------------------
# Context / result
# --------------------------------------------------------------------------
@dataclass
class WeeklySummaryContext:
    today: date
    week_start: date
    week_end: date
    courses: list[Course]
    assessments: list[Assessment]
    topics: list[Topic]
    prelabs: list[PreLab]
    study_log: list[StudyLogEntry]
    weights: list[GradeWeight]
    mock_exams: list[MockExam]
    roadmap_items: list[RoadmapItem]
    goals: list[Goal]


@dataclass
class WeeklySummary:
    hours_total: float
    hours_by_course: dict[int, float]
    hours_goal: Optional[float]
    assessments_submitted: int
    assessments_graded: int
    topics_mastered_this_week: int
    prelabs_on_time: int
    prelabs_late: int
    overdue_items: list[str]
    courses_below_target: list[str]
    courses_failing_nait: list[str]
    mock_results: list[str]
    slipped_milestones: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _fmt_date(d: date) -> str:
    return d.strftime("%b %d")


def _fmt_num(n: Optional[float]) -> str:
    if n is None:
        return "?"
    try:
        return str(int(n)) if float(n).is_integer() else f"{n:g}"
    except (TypeError, ValueError):
        return "?"


# --------------------------------------------------------------------------
# Weekly summary (§6.9)
# --------------------------------------------------------------------------
def compute_weekly_summary(ctx: WeeklySummaryContext) -> WeeklySummary:
    course_map = {c.course_id: c.code for c in ctx.courses}

    # Hours (total + per course) from study_log entries within the week.
    hours_by_course: dict[int, float] = {}
    for e in ctx.study_log:
        if e.date and ctx.week_start <= e.date <= ctx.week_end:
            hours_by_course[e.course_id] = hours_by_course.get(e.course_id, 0.0) + (e.minutes or 0) / 60.0
    hours_by_course = {cid: round(h, 2) for cid, h in hours_by_course.items()}
    hours_total = round(sum(hours_by_course.values()), 2)

    hours_goal: Optional[float] = None
    for g in ctx.goals:
        if g.scope == "Weekly" and g.metric == "study_hours_per_week":
            hours_goal = g.target_value
            break

    # Assessments submitted/graded, due within the week.
    assessments_submitted = 0
    assessments_graded = 0
    for a in ctx.assessments:
        if a.due_date and ctx.week_start <= a.due_date <= ctx.week_end:
            if a.status == "Submitted":
                assessments_submitted += 1
            elif a.status == "Graded":
                assessments_graded += 1

    # Topics newly Mastered this week (by last_reviewed falling in the week).
    topics_mastered_this_week = sum(
        1 for t in ctx.topics
        if t.status == "Mastered" and t.last_reviewed and ctx.week_start <= t.last_reviewed <= ctx.week_end
    )

    # Pre-lab punctuality for labs held this week.
    prelabs_on_time = 0
    prelabs_late = 0
    for p in ctx.prelabs:
        if p.lab_date and ctx.week_start <= p.lab_date <= ctx.week_end and p.completed:
            if p.completed_on and p.prelab_due and p.completed_on <= p.prelab_due:
                prelabs_on_time += 1
            else:
                prelabs_late += 1

    # Overdue: assessments past due and not Submitted/Graded, plus
    # incomplete real (non None/Practice) labs whose lab_date has passed.
    overdue_items: list[str] = []
    for a in ctx.assessments:
        if a.due_date and a.due_date < ctx.today and a.status not in ("Submitted", "Graded"):
            code = course_map.get(a.course_id, "?")
            overdue_items.append(f"{code}: {a.title} (was due {_fmt_date(a.due_date)})")
    for p in ctx.prelabs:
        if (p.lab_date and p.lab_date < ctx.today and not p.completed
                and p.lab_type not in ("None", "Practice")):
            code = course_map.get(p.course_id, "?")
            label = f"Lab {p.lab_number}: {p.title}" if p.lab_number else (p.title or "Pre-lab")
            overdue_items.append(f"{code}: {label} (was due {_fmt_date(p.lab_date)})")

    # Per-course risk: below target grade, or failing the NAIT pass rule.
    courses_below_target: list[str] = []
    courses_failing_nait: list[str] = []
    for c in ctx.courses:
        c_assessments = [a for a in ctx.assessments if a.course_id == c.course_id]
        c_weights = [w for w in ctx.weights if w.course_id == c.course_id]
        c_prelabs = [p for p in ctx.prelabs if p.course_id == c.course_id]

        gr = grades.compute_grade(c_assessments, c_weights)
        if gr.current is not None and c.target_grade is not None and gr.current < c.target_grade:
            courses_below_target.append(c.code)

        npc = grades.nait_pass_check(c, c_assessments, c_weights, c_prelabs, ctx.today)
        if npc.overall_pass is False:
            courses_failing_nait.append(c.code)

    # Mock exams sat this week.
    mock_results: list[str] = []
    for m in ctx.mock_exams:
        if m.date_taken and ctx.week_start <= m.date_taken <= ctx.week_end:
            code = course_map.get(m.course_id, "?")
            mock_results.append(f"{code}: {m.name} - {_fmt_num(m.score)}/{_fmt_num(m.max_score)}")

    # Slipped roadmap milestones (any course, current state - not week-scoped).
    slipped_milestones: list[str] = []
    for r in ctx.roadmap_items:
        if r.status == "Slipped":
            code = course_map.get(r.course_id, "?")
            slipped_milestones.append(f"{code}: {r.milestone}")

    return WeeklySummary(
        hours_total=hours_total,
        hours_by_course=hours_by_course,
        hours_goal=hours_goal,
        assessments_submitted=assessments_submitted,
        assessments_graded=assessments_graded,
        topics_mastered_this_week=topics_mastered_this_week,
        prelabs_on_time=prelabs_on_time,
        prelabs_late=prelabs_late,
        overdue_items=overdue_items,
        courses_below_target=courses_below_target,
        courses_failing_nait=courses_failing_nait,
        mock_results=mock_results,
        slipped_milestones=slipped_milestones,
    )
