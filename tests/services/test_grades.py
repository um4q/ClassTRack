"""
Tests for app.services.grades - pure functions, no Qt, no store.

All fixtures here are plain dataclass instances built directly from
app.models; see the module docstring in app/services/grades.py (§6.1-6.4)
for the rules under test.
"""
from __future__ import annotations

from datetime import date

import pytest

from app import config
from app.models import Assessment, Course, GradeWeight, PreLab
from app.services.grades import (
    GradeResult,
    compute_grade,
    grade_trend,
    item_weight,
    labs_completed_counts,
    nait_pass_check,
    what_if,
    what_if_single_item,
)


# --------------------------------------------------------------------------
# item_weight
# --------------------------------------------------------------------------
def test_item_weight_override_wins_over_category_split():
    a = Assessment(weight_override=15.0)
    # category_weight_pct/survivors would be 40/5 = 8.0 if the override were
    # ignored - the override must win outright.
    assert item_weight(a, category_weight_pct=40.0, survivors=5) == 15.0


def test_item_weight_splits_evenly_across_survivors_when_no_override():
    a = Assessment(weight_override=None)
    assert item_weight(a, category_weight_pct=45.0, survivors=3) == pytest.approx(15.0)


def test_item_weight_zero_survivors_returns_zero_instead_of_dividing():
    a = Assessment(weight_override=None)
    assert item_weight(a, category_weight_pct=45.0, survivors=0) == 0.0


# --------------------------------------------------------------------------
# compute_grade
# --------------------------------------------------------------------------
def test_compute_grade_mix_of_graded_and_ungraded_items():
    """Ungraded items (score=None) and items missing max_score are excluded
    from the average but still count toward the category's item count (so
    per-item weight is split across ALL 4 quiz rows, not just the 2 graded
    ones). A second category with zero matching assessment rows still adds
    its weight_pct to total_weight."""
    weights = [
        GradeWeight(component="Theory", category="Quizzes", weight_pct=30, drop_lowest=0),
        GradeWeight(component="Theory", category="Participation", weight_pct=10, drop_lowest=0),
    ]
    assessments = [
        Assessment(assessment_id=1, category="Quizzes", score=8, max_score=10),
        Assessment(assessment_id=2, category="Quizzes", score=9, max_score=10),
        Assessment(assessment_id=3, category="Quizzes", score=None, max_score=10),  # ungraded
        Assessment(assessment_id=4, category="Quizzes", score=5, max_score=None),   # no max_score
    ]

    result = compute_grade(assessments, weights)

    assert result.total_weight == pytest.approx(40.0)  # 30 + 10, Participation has 0 rows
    assert result.graded_weight == pytest.approx(15.0)  # 2 graded items * (30/4) each
    assert result.earned_weight == pytest.approx(12.75)  # 7.5*0.8 + 7.5*0.9
    assert result.current == pytest.approx(85.0)
    assert result.projected == result.current


def test_compute_grade_component_filter_isolates_theory_or_lab():
    weights = [
        GradeWeight(component="Theory", category="Midterms", weight_pct=60),
        GradeWeight(component="Lab", category="LabReports", weight_pct=40),
    ]
    assessments = [
        Assessment(category="Midterms", score=70, max_score=100),
        Assessment(category="LabReports", score=90, max_score=100),
    ]

    theory_only = compute_grade(assessments, weights, component="Theory")
    lab_only = compute_grade(assessments, weights, component="Lab")
    combined = compute_grade(assessments, weights)

    assert theory_only.total_weight == pytest.approx(60.0)
    assert theory_only.current == pytest.approx(70.0)

    assert lab_only.total_weight == pytest.approx(40.0)
    assert lab_only.current == pytest.approx(90.0)

    assert combined.total_weight == pytest.approx(100.0)
    assert combined.current == pytest.approx(78.0)  # (60*0.7 + 40*0.9) / 100 * 100


def test_compute_grade_drop_lowest_breaks_a_tie_by_dropping_exactly_one():
    """Two items tie for lowest score ratio (both 50%); drop_lowest=1 must
    exclude exactly one of them, not both, and must not touch total_weight."""
    weights = [GradeWeight(component="Theory", category="Labs", weight_pct=30, drop_lowest=1)]
    tied_a = Assessment(assessment_id=10, category="Labs", score=5, max_score=10)
    tied_b = Assessment(assessment_id=11, category="Labs", score=5, max_score=10)
    high = Assessment(assessment_id=12, category="Labs", score=10, max_score=10)

    result = compute_grade([tied_a, tied_b, high], weights)

    # If both tied items were wrongly dropped, graded_weight would be 15.0
    # (only `high` left) and current would be 100.0. If drop_lowest were
    # ignored entirely, graded_weight would be 30.0 (3 kept) but current
    # would be ~66.67. Exactly-one-dropped gives these values instead:
    assert result.graded_weight == pytest.approx(30.0)  # 2 kept * 15.0 each
    assert result.current == pytest.approx(75.0)
    assert result.total_weight == pytest.approx(30.0)  # unaffected by drop_lowest


# --------------------------------------------------------------------------
# labs_completed_counts
# --------------------------------------------------------------------------
def test_labs_completed_counts_excludes_none_and_practice_and_splits_due_vs_term():
    today = date(2026, 3, 1)
    prelabs = [
        PreLab(lab_type="Common", lab_date=date(2026, 1, 10), completed=True),      # due, done
        PreLab(lab_type="Weekly", lab_date=date(2026, 9, 20), completed=False),     # eligible, not yet due
        PreLab(lab_type="Practice", lab_date=date(2026, 1, 5), completed=True),     # excluded (Practice)
        PreLab(lab_type="None", lab_date=date(2026, 1, 5), completed=True),         # excluded (None)
        PreLab(lab_type="Rotational", lab_date=None, completed=False),             # eligible, no date -> not due
        PreLab(lab_type="Common", lab_date=date(2026, 2, 1), completed=False),      # due, not done
    ]

    done, due, whole_term = labs_completed_counts(prelabs, today)

    assert (done, due, whole_term) == (1, 2, 4)


# --------------------------------------------------------------------------
# nait_pass_check
# --------------------------------------------------------------------------
def _weights_theory_lab():
    return [
        GradeWeight(component="Theory", category="Exams", weight_pct=100, drop_lowest=0),
        GradeWeight(component="Lab", category="LabWork", weight_pct=100, drop_lowest=0),
    ]


def _due_prelabs(n_due: int, n_completed: int):
    return [
        PreLab(lab_type="Common", lab_date=date(2026, 1, 1 + i), completed=(i < n_completed))
        for i in range(n_due)
    ]


def test_nait_pass_check_all_components_pass():
    course = Course(min_lab_completion_pct=80.0)
    assessments = [
        Assessment(category="Exams", score=80, max_score=100),
        Assessment(category="LabWork", score=80, max_score=100),
    ]
    prelabs = _due_prelabs(5, 4)  # 80% completion

    result = nait_pass_check(course, assessments, _weights_theory_lab(), prelabs, date(2026, 3, 1))

    assert result.theory_pass is True
    assert result.lab_pass is True
    assert result.lab_completion_pass is True
    assert result.overall_pass is True
    assert result.capped_grade is None


def test_nait_pass_check_theory_fails_caps_at_theory_mark():
    course = Course(min_lab_completion_pct=80.0)
    assessments = [
        Assessment(category="Exams", score=40, max_score=100),   # below 50
        Assessment(category="LabWork", score=80, max_score=100),
    ]
    prelabs = _due_prelabs(5, 4)

    result = nait_pass_check(course, assessments, _weights_theory_lab(), prelabs, date(2026, 3, 1))

    assert result.theory_pass is False
    assert result.lab_pass is True
    assert result.lab_completion_pass is True
    assert result.overall_pass is False
    assert result.capped_grade == pytest.approx(40.0)  # min(40, 80)


def test_nait_pass_check_lab_fails_caps_at_lab_mark():
    course = Course(min_lab_completion_pct=80.0)
    assessments = [
        Assessment(category="Exams", score=80, max_score=100),
        Assessment(category="LabWork", score=30, max_score=100),  # below 50
    ]
    prelabs = _due_prelabs(5, 4)

    result = nait_pass_check(course, assessments, _weights_theory_lab(), prelabs, date(2026, 3, 1))

    assert result.theory_pass is True
    assert result.lab_pass is False
    assert result.lab_completion_pass is True
    assert result.overall_pass is False
    assert result.capped_grade == pytest.approx(30.0)  # min(80, 30)


def test_nait_pass_check_completion_fails_caps_lab_mark_at_config_ceiling():
    """Theory and lab marks both individually pass (>=50), but completion
    is below min_lab_completion_pct - the lab mark used for the cap must be
    clamped to config.LAB_MARK_CAP_ON_FAIL_PCT, not the raw (high) lab
    score."""
    course = Course(min_lab_completion_pct=80.0)
    assessments = [
        Assessment(category="Exams", score=80, max_score=100),
        Assessment(category="LabWork", score=90, max_score=100),  # passes on its own
    ]
    prelabs = _due_prelabs(5, 1)  # 20% completion, well below 80%

    result = nait_pass_check(course, assessments, _weights_theory_lab(), prelabs, date(2026, 3, 1))

    assert result.theory_pass is True
    assert result.lab_pass is True
    assert result.lab_completion_pass is False
    assert result.overall_pass is False
    assert result.lab.current == pytest.approx(90.0)  # raw lab mark stays 90...
    # ...but capped_grade uses lab capped at LAB_MARK_CAP_ON_FAIL_PCT (45), not 90
    assert result.capped_grade == pytest.approx(config.LAB_MARK_CAP_ON_FAIL_PCT)
    assert result.capped_grade == pytest.approx(min(80.0, config.LAB_MARK_CAP_ON_FAIL_PCT))


def test_nait_pass_check_missing_min_completion_defaults_to_zero_threshold():
    """course.min_lab_completion_pct=None means ANY completion percentage
    (including 0%) passes the completion gate."""
    course = Course(min_lab_completion_pct=None)
    assessments = [
        Assessment(category="Exams", score=80, max_score=100),
        Assessment(category="LabWork", score=80, max_score=100),
    ]
    prelabs = _due_prelabs(5, 0)  # 0% completion

    result = nait_pass_check(course, assessments, _weights_theory_lab(), prelabs, date(2026, 3, 1))

    assert result.lab_completion_pct == pytest.approx(0.0)
    assert result.lab_completion_pass is True
    assert result.overall_pass is True
    assert result.capped_grade is None


# --------------------------------------------------------------------------
# what_if
# --------------------------------------------------------------------------
def test_what_if_already_achieved_even_with_remaining_weight():
    grade = GradeResult(current=90.0, earned_weight=45.0, graded_weight=50.0, total_weight=100.0, projected=90.0)

    result = what_if(grade, target_pct=40)

    assert result.needed_avg == pytest.approx(-10.0)
    assert result.achieved is True
    assert result.impossible is False


def test_what_if_impossible_when_needed_average_exceeds_100():
    grade = GradeResult(current=50.0, earned_weight=45.0, graded_weight=90.0, total_weight=100.0, projected=50.0)

    result = what_if(grade, target_pct=95)

    assert result.needed_avg == pytest.approx(500.0)
    assert result.achieved is False
    assert result.impossible is True


def test_what_if_normal_case_returns_achievable_needed_average():
    grade = GradeResult(current=80.0, earned_weight=40.0, graded_weight=50.0, total_weight=100.0, projected=80.0)

    result = what_if(grade, target_pct=85)

    assert result.needed_avg == pytest.approx(90.0)
    assert result.achieved is False
    assert result.impossible is False


def test_what_if_remaining_weight_zero_reports_achieved_from_final_grade():
    fully_graded_met = GradeResult(current=85.0, earned_weight=100.0, graded_weight=100.0, total_weight=100.0, projected=85.0)
    fully_graded_missed = GradeResult(current=70.0, earned_weight=70.0, graded_weight=100.0, total_weight=100.0, projected=70.0)

    met = what_if(fully_graded_met, target_pct=80)
    missed = what_if(fully_graded_missed, target_pct=80)

    assert met.needed_avg is None
    assert met.achieved is True
    assert met.impossible is False

    assert missed.needed_avg is None
    assert missed.achieved is False
    assert missed.impossible is False


# --------------------------------------------------------------------------
# what_if_single_item
# --------------------------------------------------------------------------
def test_what_if_single_item_normal_case():
    grade = GradeResult(current=90.0, earned_weight=54.0, graded_weight=60.0, total_weight=100.0, projected=90.0)

    result = what_if_single_item(grade, target_pct=92, target_item_weight=20, assumed_avg_others_pct=95)

    assert result.needed_avg == pytest.approx(95.0)
    assert result.achieved is False
    assert result.impossible is False


def test_what_if_single_item_impossible_when_needed_average_exceeds_100():
    grade = GradeResult(current=93.75, earned_weight=75.0, graded_weight=80.0, total_weight=100.0, projected=93.75)

    result = what_if_single_item(grade, target_pct=95, target_item_weight=5, assumed_avg_others_pct=0)

    assert result.needed_avg == pytest.approx(400.0)
    assert result.impossible is True
    assert result.achieved is False


def test_what_if_single_item_achieved_when_needed_average_non_positive():
    grade = GradeResult(current=96.0, earned_weight=48.0, graded_weight=50.0, total_weight=100.0, projected=96.0)

    result = what_if_single_item(grade, target_pct=10, target_item_weight=10, assumed_avg_others_pct=0)

    assert result.needed_avg == pytest.approx(-380.0)
    assert result.achieved is True
    assert result.impossible is False


def test_what_if_single_item_zero_target_weight_returns_none_without_dividing():
    grade = GradeResult(current=50.0, earned_weight=10.0, graded_weight=20.0, total_weight=100.0, projected=50.0)

    result = what_if_single_item(grade, target_pct=80, target_item_weight=0)

    assert result == what_if_single_item(grade, target_pct=80, target_item_weight=0)
    assert result.needed_avg is None
    assert result.achieved is False
    assert result.impossible is False


# --------------------------------------------------------------------------
# grade_trend
# --------------------------------------------------------------------------
def test_grade_trend_empty_when_nothing_graded_or_no_due_dates():
    assert grade_trend([], []) == []

    weights = [GradeWeight(component="Theory", category="X", weight_pct=100)]
    ungraded = [Assessment(category="X", score=None, max_score=10, due_date=date(2026, 1, 1))]
    no_due_date = [Assessment(category="X", score=8, max_score=10, due_date=None)]

    assert grade_trend(ungraded, weights) == []
    assert grade_trend(no_due_date, weights) == []


def test_grade_trend_cumulative_values_increase_with_increasing_scores():
    weights = [GradeWeight(component="Theory", category="Quizzes", weight_pct=100, drop_lowest=0)]
    assessments = [
        Assessment(assessment_id=1, category="Quizzes", due_date=date(2026, 1, 1), score=5, max_score=10),
        Assessment(assessment_id=2, category="Quizzes", due_date=date(2026, 1, 10), score=6, max_score=10),
        Assessment(assessment_id=3, category="Quizzes", due_date=date(2026, 1, 20), score=7, max_score=10),
        Assessment(assessment_id=4, category="Quizzes", due_date=date(2026, 2, 1), score=8, max_score=10),
    ]

    trend = grade_trend(assessments, weights)

    assert [d for d, _ in trend] == [
        date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 20), date(2026, 2, 1),
    ]
    values = [v for _, v in trend]
    assert values == pytest.approx([50.0, 55.0, 60.0, 65.0])
    # every subsequent score beat the running average, so it must climb
    assert all(values[i] <= values[i + 1] for i in range(len(values) - 1))


def test_grade_trend_category_item_count_uses_full_assessment_list_not_just_graded_so_far():
    """The Quizzes category has 4 rows total, only 2 of which are ever
    graded within this trend. Per-item weight for Quizzes must be based on
    all 4 rows (20/4=5 each) at EVERY step, not on however many rows happen
    to be graded at that point in time - otherwise the weight-per-item
    would inflate as items get graded, distorting earlier cumulative
    values relative to later ones."""
    weights = [
        GradeWeight(component="Theory", category="Quizzes", weight_pct=20, drop_lowest=0),
        GradeWeight(component="Theory", category="Exam", weight_pct=80, drop_lowest=0),
    ]
    assessments = [
        Assessment(assessment_id=1, category="Exam", due_date=date(2026, 1, 1), score=8, max_score=10),
        Assessment(assessment_id=2, category="Quizzes", due_date=date(2026, 1, 10), score=10, max_score=10),
        Assessment(assessment_id=3, category="Quizzes", due_date=date(2026, 1, 20), score=4, max_score=10),
        Assessment(assessment_id=4, category="Quizzes", due_date=date(2026, 2, 1), score=None, max_score=10),
        Assessment(assessment_id=5, category="Quizzes", due_date=date(2026, 2, 15), score=None, max_score=10),
    ]

    trend = grade_trend(assessments, weights)

    assert [d for d, _ in trend] == [date(2026, 1, 1), date(2026, 1, 10), date(2026, 1, 20)]
    # Step 1: only the Exam is graded -> 80*0.8 / 80 * 100
    assert trend[0][1] == pytest.approx(80.0)
    # Step 2: Exam (80/80) + one quiz weighted at 20/4=5 (not 20/1=20, which
    # a "survivors = graded-so-far" bug would produce and which would give
    # 84.0 instead) -> (64 + 5) / (80 + 5) * 100
    assert trend[1][1] == pytest.approx(69 / 85 * 100)
    assert trend[1][1] != pytest.approx(84.0)
    # Step 3: Exam + both quizzes, each still weighted at 20/4=5 -> (64+7)/(80+10)*100
    assert trend[2][1] == pytest.approx(71 / 90 * 100)
