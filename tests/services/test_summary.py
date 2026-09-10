"""
Tests for app.services.summary - pure functions, no Qt, no store.

All fixtures here are plain dataclass instances built directly from
app.models; see the module docstring in app/services/summary.py (build
spec §6.9) for the rules under test.
"""
from __future__ import annotations

from datetime import date

import pytest

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
from app.services.summary import (
    WeeklySummaryContext,
    compute_weekly_summary,
    iso_week_str,
)

# Shared week window used across compute_weekly_summary tests: Mon Sep 7
# through Sun Sep 13, 2026, with "today" mid-week on Sep 10.
WEEK_START = date(2026, 9, 7)
WEEK_END = date(2026, 9, 13)
TODAY = date(2026, 9, 10)


def _ctx(**overrides) -> WeeklySummaryContext:
    defaults = dict(
        today=TODAY,
        week_start=WEEK_START,
        week_end=WEEK_END,
        courses=[],
        assessments=[],
        topics=[],
        prelabs=[],
        study_log=[],
        weights=[],
        mock_exams=[],
        roadmap_items=[],
        goals=[],
    )
    defaults.update(overrides)
    return WeeklySummaryContext(**defaults)


# --------------------------------------------------------------------------
# iso_week_str
# --------------------------------------------------------------------------
def test_iso_week_str_known_date_formats_as_iso_year_and_week():
    assert iso_week_str(date(2026, 9, 10)) == "2026-W37"


def test_iso_week_str_january_first_can_fall_in_prior_iso_year():
    """Jan 1 2023 is a Sunday - ISO calendar assigns it to week 52 of the
    PRIOR ISO year (2022), not week 1 of 2023. Verified against Python's
    own date.isocalendar(): (2022, 52, 7)."""
    d = date(2023, 1, 1)
    assert d.isocalendar()[:2] == (2022, 52)
    assert iso_week_str(d) == "2022-W52"


def test_iso_week_str_late_december_can_fall_in_next_iso_year():
    """Dec 29 2025 is a Monday that starts the ISO year 2026's first week,
    even though the calendar year is still 2025. Verified against Python's
    own date.isocalendar(): (2026, 1, 1)."""
    d = date(2025, 12, 29)
    assert d.isocalendar()[:2] == (2026, 1)
    assert iso_week_str(d) == "2026-W01"


# --------------------------------------------------------------------------
# compute_weekly_summary - hours
# --------------------------------------------------------------------------
def test_hours_total_and_by_course_sum_within_week_and_exclude_outside_entries():
    study_log = [
        StudyLogEntry(course_id=1, date=date(2026, 9, 7), minutes=60),   # in week
        StudyLogEntry(course_id=1, date=date(2026, 9, 13), minutes=90),  # in week
        StudyLogEntry(course_id=2, date=date(2026, 9, 9), minutes=30),   # in week
        StudyLogEntry(course_id=1, date=date(2026, 9, 6), minutes=999),  # before week
        StudyLogEntry(course_id=1, date=date(2026, 9, 14), minutes=999),  # after week
        StudyLogEntry(course_id=3, date=None, minutes=999),              # no date at all
    ]
    ctx = _ctx(study_log=study_log)

    result = compute_weekly_summary(ctx)

    assert result.hours_by_course == {1: 2.5, 2: 0.5}
    assert result.hours_total == pytest.approx(3.0)


def test_hours_goal_none_when_no_matching_weekly_study_hours_goal():
    # Wrong metric on an otherwise-matching Weekly goal, and a matching
    # metric on the wrong scope - neither should satisfy the lookup.
    goals = [
        Goal(scope="Weekly", metric="topics_reviewed", target_value=5.0),
        Goal(scope="Course", metric="study_hours_per_week", target_value=8.0),
    ]

    assert compute_weekly_summary(_ctx(goals=[])).hours_goal is None
    assert compute_weekly_summary(_ctx(goals=goals)).hours_goal is None


def test_hours_goal_set_when_matching_weekly_study_hours_goal_exists():
    goals = [Goal(scope="Weekly", metric="study_hours_per_week", target_value=12.0)]

    result = compute_weekly_summary(_ctx(goals=goals))

    assert result.hours_goal == pytest.approx(12.0)


# --------------------------------------------------------------------------
# compute_weekly_summary - assessments submitted/graded
# --------------------------------------------------------------------------
def test_assessments_submitted_and_graded_counted_only_within_week():
    assessments = [
        Assessment(due_date=date(2026, 9, 8), status="Submitted"),   # in week
        Assessment(due_date=date(2026, 9, 12), status="Graded"),     # in week
        Assessment(due_date=date(2026, 9, 9), status="Not Started"), # in week, neither status
        Assessment(due_date=date(2026, 9, 1), status="Submitted"),   # before week
        Assessment(due_date=date(2026, 9, 20), status="Graded"),     # after week
        Assessment(due_date=None, status="Submitted"),               # no due date
    ]

    result = compute_weekly_summary(_ctx(assessments=assessments))

    assert result.assessments_submitted == 1
    assert result.assessments_graded == 1


# --------------------------------------------------------------------------
# compute_weekly_summary - topics mastered this week
# --------------------------------------------------------------------------
def test_topics_mastered_this_week_requires_mastered_status_and_in_window_review():
    topics = [
        Topic(status="Mastered", last_reviewed=date(2026, 9, 10)),      # counts
        Topic(status="Mastered", last_reviewed=date(2026, 9, 1)),       # wrong week
        Topic(status="In Progress", last_reviewed=date(2026, 9, 10)),   # wrong status
        Topic(status="Mastered", last_reviewed=None),                   # no review date
    ]

    result = compute_weekly_summary(_ctx(topics=topics))

    assert result.topics_mastered_this_week == 1


# --------------------------------------------------------------------------
# compute_weekly_summary - pre-lab punctuality
# --------------------------------------------------------------------------
def test_prelabs_on_time_vs_late_split_by_completed_on_vs_prelab_due():
    prelabs = [
        # completed on/before its due date -> on time
        PreLab(lab_date=date(2026, 9, 8), completed=True,
               completed_on=date(2026, 9, 6), prelab_due=date(2026, 9, 7)),
        # completed after its due date -> late
        PreLab(lab_date=date(2026, 9, 9), completed=True,
               completed_on=date(2026, 9, 9), prelab_due=date(2026, 9, 7)),
        # completed but no prelab_due recorded -> can't confirm on time -> late
        PreLab(lab_date=date(2026, 9, 10), completed=True,
               completed_on=date(2026, 9, 9), prelab_due=None),
        # not completed at all -> excluded from both counts
        PreLab(lab_date=date(2026, 9, 11), completed=False,
               completed_on=None, prelab_due=date(2026, 9, 10)),
        # completed on time but lab held outside the week -> excluded
        PreLab(lab_date=date(2026, 9, 1), completed=True,
               completed_on=date(2026, 8, 30), prelab_due=date(2026, 8, 31)),
    ]

    result = compute_weekly_summary(_ctx(prelabs=prelabs))

    assert result.prelabs_on_time == 1
    assert result.prelabs_late == 2


# --------------------------------------------------------------------------
# compute_weekly_summary - overdue items
# --------------------------------------------------------------------------
def test_overdue_items_includes_overdue_assessment_and_overdue_incomplete_prelab():
    courses = [Course(course_id=1, code="MATH101")]
    assessments = [
        # overdue, not submitted/graded -> included
        Assessment(course_id=1, title="Essay 1", due_date=date(2026, 9, 5), status="Not Started"),
        # overdue but already submitted -> excluded
        Assessment(course_id=1, title="Essay 2", due_date=date(2026, 9, 5), status="Submitted"),
        # overdue but already graded -> excluded
        Assessment(course_id=1, title="Essay 3", due_date=date(2026, 9, 5), status="Graded"),
        # not yet due -> excluded
        Assessment(course_id=1, title="Essay 4", due_date=date(2026, 9, 20), status="Not Started"),
    ]
    prelabs = [
        # overdue, incomplete, real lab type, has a lab number -> included w/ "Lab N: title"
        PreLab(course_id=1, lab_number="3", title="Intro Lab", lab_date=date(2026, 9, 3),
               completed=False, lab_type="Common"),
        # overdue, incomplete, no lab number -> included using bare title
        PreLab(course_id=1, lab_number="", title="Weekly Check-in", lab_date=date(2026, 9, 4),
               completed=False, lab_type="Weekly"),
        # overdue but already completed -> excluded
        PreLab(course_id=1, lab_number="5", title="Done Lab", lab_date=date(2026, 9, 3),
               completed=True, lab_type="Common"),
        # overdue, incomplete, but lab_type Practice -> excluded per spec
        PreLab(course_id=1, lab_number="6", title="Practice Lab", lab_date=date(2026, 9, 3),
               completed=False, lab_type="Practice"),
        # overdue, incomplete, but lab_type None -> excluded per spec
        PreLab(course_id=1, lab_number="7", title="Placeholder Lab", lab_date=date(2026, 9, 3),
               completed=False, lab_type="None"),
    ]

    result = compute_weekly_summary(_ctx(courses=courses, assessments=assessments, prelabs=prelabs))

    assert result.overdue_items == [
        "MATH101: Essay 1 (was due Sep 05)",
        "MATH101: Lab 3: Intro Lab (was due Sep 03)",
        "MATH101: Weekly Check-in (was due Sep 04)",
    ]


def test_overdue_items_unknown_course_id_falls_back_to_question_mark_code():
    assessments = [Assessment(course_id=999, title="Mystery", due_date=date(2026, 9, 1), status="Not Started")]

    result = compute_weekly_summary(_ctx(courses=[], assessments=assessments))

    assert result.overdue_items == ["?: Mystery (was due Sep 01)"]


# --------------------------------------------------------------------------
# compute_weekly_summary - courses_below_target / courses_failing_nait
# --------------------------------------------------------------------------
def test_courses_below_target_and_courses_failing_nait_populate_independently():
    """Three courses, each built to exercise a different branch:
      - PASS101: comfortably meets its target and NAIT thresholds -> neither list.
      - LOW201: current grade (70) is below its target_grade (90) but both
        components individually clear the NAIT 50% pass bar -> below_target only.
      - FAIL301: has no target_grade set (so it can never appear in
        below_target) but its Lab component (30%) fails the NAIT 50%
        threshold -> failing_nait only.
    """
    courses = [
        Course(course_id=1, code="PASS101", target_grade=50.0),
        Course(course_id=2, code="LOW201", target_grade=90.0),
        Course(course_id=3, code="FAIL301", target_grade=None),
    ]
    weights = [
        GradeWeight(course_id=1, component="Theory", category="Exams", weight_pct=100),
        GradeWeight(course_id=1, component="Lab", category="LabWork", weight_pct=100),
        GradeWeight(course_id=2, component="Theory", category="Exams", weight_pct=100),
        GradeWeight(course_id=2, component="Lab", category="LabWork", weight_pct=100),
        GradeWeight(course_id=3, component="Theory", category="Exams", weight_pct=100),
        GradeWeight(course_id=3, component="Lab", category="LabWork", weight_pct=100),
    ]
    assessments = [
        Assessment(course_id=1, category="Exams", score=90, max_score=100),
        Assessment(course_id=1, category="LabWork", score=90, max_score=100),
        Assessment(course_id=2, category="Exams", score=70, max_score=100),
        # LOW201 has no LabWork item at all: lab.current stays None -> lab_pass
        # defaults to True, so this course fails ONLY the target check.
        Assessment(course_id=3, category="Exams", score=80, max_score=100),
        Assessment(course_id=3, category="LabWork", score=30, max_score=100),  # below 50
    ]

    result = compute_weekly_summary(_ctx(courses=courses, assessments=assessments, weights=weights))

    assert result.courses_below_target == ["LOW201"]
    assert result.courses_failing_nait == ["FAIL301"]


# --------------------------------------------------------------------------
# compute_weekly_summary - mock exam results
# --------------------------------------------------------------------------
def test_mock_results_within_week_formatted_with_score_over_max_and_unknown_score():
    courses = [Course(course_id=1, code="CHEM101")]
    mock_exams = [
        MockExam(course_id=1, name="Practice Midterm", date_taken=date(2026, 9, 9), score=85.0, max_score=100.0),
        MockExam(course_id=1, name="Ungraded Attempt", date_taken=date(2026, 9, 10), score=None, max_score=50.0),
        MockExam(course_id=1, name="Old Exam", date_taken=date(2026, 8, 1), score=70.0, max_score=100.0),  # outside week
    ]

    result = compute_weekly_summary(_ctx(courses=courses, mock_exams=mock_exams))

    assert result.mock_results == [
        "CHEM101: Practice Midterm - 85/100",
        "CHEM101: Ungraded Attempt - ?/50",
    ]


# --------------------------------------------------------------------------
# compute_weekly_summary - slipped roadmap milestones
# --------------------------------------------------------------------------
def test_slipped_milestones_lists_slipped_status_regardless_of_dates():
    courses = [Course(course_id=1, code="BIO201")]
    roadmap_items = [
        RoadmapItem(course_id=1, milestone="Unit 2 review", status="Slipped",
                    start_date=date(2026, 1, 1), end_date=date(2026, 2, 1)),  # well outside the week
        RoadmapItem(course_id=1, milestone="Unit 3 review", status="In Progress"),
        RoadmapItem(course_id=1, milestone="Unit 4 review", status="Done"),
    ]

    result = compute_weekly_summary(_ctx(courses=courses, roadmap_items=roadmap_items))

    assert result.slipped_milestones == ["BIO201: Unit 2 review"]
