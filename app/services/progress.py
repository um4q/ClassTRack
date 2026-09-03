"""
Topic progress, focus ranking, spaced repetition (Leitner), streaks, and
goal-metric computation. See build spec §6.5, §6.6, §6.10, §6.11.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from app import config
from app.models import Assessment, Goal, PracticeQuestion, PreLab, StudyLogEntry, Topic


# --------------------------------------------------------------------------
# Topic progress (§6.5)
# --------------------------------------------------------------------------
def topic_progress_pct(topics: list[Topic]) -> float:
    """Mean of TOPIC_STATUS_VALUE across topics, as a 0-100 percentage."""
    if not topics:
        return 0.0
    total = sum(config.TOPIC_STATUS_VALUE.get(t.status, 0.0) for t in topics)
    return total / len(topics) * 100


def focus_score(topic: Topic, assessments: list[Assessment], today: date) -> float:
    """(6 - confidence) * exam_factor * staleness * priority_factor.
    Mastered AND reviewed within the last 7 days scores 0 (never surfaces)."""
    if topic.status == "Mastered" and topic.last_reviewed and \
            (today - topic.last_reviewed).days <= config.FOCUS_MASTERED_GRACE_DAYS:
        return 0.0

    exam_factor = config.FOCUS_EXAM_FACTOR_NONE
    for a in assessments:
        if a.due_date is None or topic.topic_id not in a.topic_ids:
            continue
        days = (a.due_date - today).days
        if days < 0:
            continue
        if days <= config.FOCUS_EXAM_WINDOW_HARD_DAYS:
            exam_factor = max(exam_factor, config.FOCUS_EXAM_FACTOR_HARD)
        elif days <= config.FOCUS_EXAM_WINDOW_SOFT_DAYS:
            exam_factor = max(exam_factor, config.FOCUS_EXAM_FACTOR_SOFT)

    if topic.last_reviewed is None:
        staleness = 2.0
    else:
        days_since = max(0, (today - topic.last_reviewed).days)
        staleness = 1 + min(days_since, config.FOCUS_STALENESS_CAP_DAYS) / config.FOCUS_STALENESS_CAP_DAYS

    priority_factor = 1 + topic.priority / 5
    return (6 - topic.confidence) * exam_factor * staleness * priority_factor


def rank_topics_by_focus(topics: list[Topic], assessments: list[Assessment], today: date, limit: Optional[int] = None) -> list[Topic]:
    ranked = sorted(topics, key=lambda t: focus_score(t, assessments, today), reverse=True)
    ranked = [t for t in ranked if focus_score(t, assessments, today) > 0]
    return ranked[:limit] if limit else ranked


# --------------------------------------------------------------------------
# Spaced repetition - Leitner (§6.6)
# --------------------------------------------------------------------------
def leitner_next_box(box: int, correct: bool) -> int:
    if correct:
        return min(box + 1, config.LEITNER_MAX_BOX)
    return config.LEITNER_MIN_BOX


def leitner_next_due(box: int, today: date) -> date:
    idx = max(0, min(box - 1, len(config.LEITNER_INTERVALS_DAYS) - 1))
    return today + timedelta(days=config.LEITNER_INTERVALS_DAYS[idx])


def practice_answer(question: PracticeQuestion, correct: bool, today: date) -> PracticeQuestion:
    """Update a PracticeQuestion in place after Got it / Missed it and
    return it (caller still needs to store.update_practice_question it)."""
    question.times_attempted += 1
    if correct:
        question.times_correct += 1
    question.box = leitner_next_box(question.box, correct)
    question.last_attempted = today
    question.next_due = leitner_next_due(question.box, today)
    return question


def _infer_topic_box(topic: Topic) -> int:
    """Topics don't persist an explicit Leitner box - infer the closest one
    from the last_reviewed/next_review gap, falling back to a status-based
    default (Mastered -> 3, Needs Focus -> 1, else 2)."""
    if topic.last_reviewed and topic.next_review:
        gap = (topic.next_review - topic.last_reviewed).days
        for i, days in enumerate(config.LEITNER_INTERVALS_DAYS):
            if gap <= days:
                return i + 1
    return {"Mastered": 3, "Needs Focus": 1}.get(topic.status, 2)


def topic_mark_reviewed(topic: Topic, today: date) -> Topic:
    """'Mark reviewed today': bump the topic's implied box by one."""
    box = leitner_next_box(_infer_topic_box(topic), correct=True)
    topic.last_reviewed = today
    topic.next_review = leitner_next_due(box, today)
    return topic


def topic_status_changed(topic: Topic, new_status: str, today: date) -> Topic:
    """A status change updates last_reviewed/next_review using the Leitner
    box implied by the new status (Mastered -> box 3, Needs Focus -> box 1);
    other statuses leave the review schedule untouched."""
    topic.status = new_status
    box = {"Mastered": 3, "Needs Focus": 1}.get(new_status)
    if box is not None:
        topic.last_reviewed = today
        topic.next_review = leitner_next_due(box, today)
    return topic


# --------------------------------------------------------------------------
# Streaks (§6.11)
# --------------------------------------------------------------------------
def study_streak(study_log: list[StudyLogEntry], today: date) -> tuple[int, int]:
    """(current_streak, best_streak) in consecutive days with >= 15 study
    minutes; current streak must end today or yesterday."""
    minutes_by_day: dict[date, int] = {}
    for e in study_log:
        if e.date:
            minutes_by_day[e.date] = minutes_by_day.get(e.date, 0) + (e.minutes or 0)
    active_days = {d for d, m in minutes_by_day.items() if m >= config.STREAK_MIN_MINUTES}
    if not active_days:
        return 0, 0

    current = 0
    cursor = today if today in active_days else today - timedelta(days=1)
    while cursor in active_days:
        current += 1
        cursor -= timedelta(days=1)

    ordered = sorted(active_days)
    best = run = 1
    for i in range(1, len(ordered)):
        run = run + 1 if (ordered[i] - ordered[i - 1]).days == 1 else 1
        best = max(best, run)
    return current, best


# --------------------------------------------------------------------------
# Goal metrics (§6.10)
# --------------------------------------------------------------------------
def _week_bounds(today: date, week_start_day: str = "Monday") -> tuple[date, date]:
    offset = config.WEEKDAY_NAMES.index(week_start_day) if week_start_day in config.WEEKDAY_NAMES else 0
    start = today - timedelta(days=(today.weekday() - offset) % 7)
    return start, start + timedelta(days=6)


def compute_goal_progress(
    goal: Goal,
    today: date,
    *,
    topics: Optional[list[Topic]] = None,
    study_log: Optional[list[StudyLogEntry]] = None,
    prelabs: Optional[list[PreLab]] = None,
    questions: Optional[list[PracticeQuestion]] = None,
    current_grade: Optional[float] = None,
) -> Optional[float]:
    """Recompute a Goal's current_value from its metric. Callers pass in
    only the collections relevant to that goal's scope (course-filtered for
    a Course-scoped goal, full lists for Semester/Weekly)."""
    m = goal.metric
    if m == "study_hours_per_week":
        if study_log is None:
            return None
        wk_start, wk_end = _week_bounds(today)
        minutes = sum(e.minutes or 0 for e in study_log if e.date and wk_start <= e.date <= wk_end)
        return round(minutes / 60, 2)
    if m == "grade_at_least":
        return current_grade
    if m == "topics_mastered":
        return float(sum(1 for t in (topics or []) if t.status == "Mastered"))
    if m == "questions_per_week":
        if questions is None:
            return None
        wk_start, wk_end = _week_bounds(today)
        return float(sum(
            1 for q in questions
            if q.last_attempted and wk_start <= q.last_attempted <= wk_end
        ))
    if m == "streak_days":
        current, _best = study_streak(study_log or [], today)
        return float(current)
    if m == "prelabs_on_time_pct":
        if prelabs is None:
            return None
        eligible = [p for p in prelabs if p.lab_type not in ("None", "Practice") and p.lab_date and p.lab_date < today]
        if not eligible:
            return None
        on_time = sum(1 for p in eligible if p.completed_on and p.completed_on <= p.prelab_due)
        return on_time / len(eligible) * 100
    return None


def goal_status(goal: Goal) -> str:
    """Active/Achieved/Missed from current_value vs target_value and dates."""
    if goal.current_value is not None and goal.current_value >= goal.target_value:
        return "Achieved"
    return "Active"
