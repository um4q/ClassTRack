"""Tests for app.services.progress - pure functions, no Qt, no store.

All Topic/Assessment/Goal/etc. instances below are constructed directly from
app.models dataclasses; nothing here touches a workbook.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import config
from app.models import (
    Assessment,
    Goal,
    PracticeQuestion,
    PreLab,
    StudyLogEntry,
    Topic,
)
from app.services import progress

# A fixed reference date used by every test that takes an explicit `today`
# param (i.e. everything except goal_status, which reads date.today()
# itself). Chosen as a Wednesday so week-bounds math below is easy to verify
# by hand: Monday 2024-01-08 .. Sunday 2024-01-14.
TODAY = date(2024, 1, 10)


# --------------------------------------------------------------------------
# topic_progress_pct
# --------------------------------------------------------------------------
def test_topic_progress_pct_empty_list_returns_zero():
    assert progress.topic_progress_pct([]) == 0.0


def test_topic_progress_pct_mix_of_every_status_averages_config_values():
    topics = [Topic(status=s) for s in config.TOPIC_STATUS_VALUE]
    # (0 + 0.25 + 0.5 + 0.5 + 1.0) / 5 * 100 = 45.0
    expected = sum(config.TOPIC_STATUS_VALUE.values()) / len(topics) * 100
    assert progress.topic_progress_pct(topics) == pytest.approx(expected)
    assert progress.topic_progress_pct(topics) == pytest.approx(45.0)


# --------------------------------------------------------------------------
# focus_score
# --------------------------------------------------------------------------
def test_focus_score_mastered_reviewed_within_grace_is_zero():
    topic = Topic(status="Mastered", last_reviewed=TODAY - timedelta(days=3))
    assert progress.focus_score(topic, [], TODAY) == 0.0


def test_focus_score_mastered_reviewed_exactly_at_grace_boundary_is_zero():
    topic = Topic(
        status="Mastered",
        last_reviewed=TODAY - timedelta(days=config.FOCUS_MASTERED_GRACE_DAYS),
    )
    assert progress.focus_score(topic, [], TODAY) == 0.0


def test_focus_score_mastered_reviewed_just_past_grace_uses_normal_formula():
    topic = Topic(
        status="Mastered",
        confidence=3,
        priority=0,
        last_reviewed=TODAY - timedelta(days=config.FOCUS_MASTERED_GRACE_DAYS + 1),
    )
    days_since = config.FOCUS_MASTERED_GRACE_DAYS + 1
    expected_staleness = 1 + min(days_since, config.FOCUS_STALENESS_CAP_DAYS) / config.FOCUS_STALENESS_CAP_DAYS
    assert progress.focus_score(topic, [], TODAY) == pytest.approx((6 - 3) * 1.0 * expected_staleness * 1.0)


@pytest.mark.parametrize(
    "days_out,expected_factor",
    [
        (0, config.FOCUS_EXAM_FACTOR_HARD),
        (config.FOCUS_EXAM_WINDOW_HARD_DAYS, config.FOCUS_EXAM_FACTOR_HARD),
        (config.FOCUS_EXAM_WINDOW_HARD_DAYS + 1, config.FOCUS_EXAM_FACTOR_SOFT),
        (config.FOCUS_EXAM_WINDOW_SOFT_DAYS, config.FOCUS_EXAM_FACTOR_SOFT),
        (config.FOCUS_EXAM_WINDOW_SOFT_DAYS + 1, config.FOCUS_EXAM_FACTOR_NONE),
    ],
    ids=["hard-today", "hard-boundary", "soft-just-past-hard", "soft-boundary", "just-past-soft"],
)
def test_focus_score_exam_factor_tiers(days_out, expected_factor):
    topic = Topic(topic_id=1, confidence=3, priority=0, last_reviewed=TODAY)
    assessment = Assessment(due_date=TODAY + timedelta(days=days_out), topic_ids=[1])
    # staleness=1 (reviewed today), priority_factor=1 -> score isolates exam_factor
    expected = (6 - 3) * expected_factor * 1.0 * 1.0
    assert progress.focus_score(topic, [assessment], TODAY) == pytest.approx(expected)


def test_focus_score_past_due_assessment_is_ignored():
    topic = Topic(topic_id=1, confidence=3, priority=0, last_reviewed=TODAY)
    assessment = Assessment(due_date=TODAY - timedelta(days=5), topic_ids=[1])
    expected = (6 - 3) * config.FOCUS_EXAM_FACTOR_NONE * 1.0 * 1.0
    assert progress.focus_score(topic, [assessment], TODAY) == pytest.approx(expected)


def test_focus_score_assessment_not_referencing_topic_is_ignored():
    topic = Topic(topic_id=1, confidence=3, priority=0, last_reviewed=TODAY)
    assessment = Assessment(due_date=TODAY + timedelta(days=5), topic_ids=[999])
    expected = (6 - 3) * config.FOCUS_EXAM_FACTOR_NONE * 1.0 * 1.0
    assert progress.focus_score(topic, [assessment], TODAY) == pytest.approx(expected)


def test_focus_score_multiple_assessments_uses_the_max_exam_factor():
    topic = Topic(topic_id=1, confidence=3, priority=0, last_reviewed=TODAY)
    soft = Assessment(due_date=TODAY + timedelta(days=config.FOCUS_EXAM_WINDOW_SOFT_DAYS), topic_ids=[1])
    hard = Assessment(due_date=TODAY + timedelta(days=10), topic_ids=[1])
    expected = (6 - 3) * config.FOCUS_EXAM_FACTOR_HARD * 1.0 * 1.0
    assert progress.focus_score(topic, [soft, hard], TODAY) == pytest.approx(expected)


def test_focus_score_never_reviewed_topic_has_staleness_two():
    topic = Topic(confidence=3, priority=0, last_reviewed=None)
    expected = (6 - 3) * config.FOCUS_EXAM_FACTOR_NONE * 2.0 * 1.0
    assert progress.focus_score(topic, [], TODAY) == pytest.approx(expected)


def test_focus_score_staleness_is_capped_beyond_cap_days():
    capped = Topic(confidence=3, priority=0, last_reviewed=TODAY - timedelta(days=config.FOCUS_STALENESS_CAP_DAYS))
    way_over = Topic(confidence=3, priority=0, last_reviewed=TODAY - timedelta(days=config.FOCUS_STALENESS_CAP_DAYS * 5))
    score_at_cap = progress.focus_score(capped, [], TODAY)
    score_over_cap = progress.focus_score(way_over, [], TODAY)
    # both clamp to the same max staleness (2.0) so their scores are equal
    assert score_at_cap == pytest.approx(score_over_cap)
    assert score_at_cap == pytest.approx((6 - 3) * 1.0 * 2.0 * 1.0)


@pytest.mark.parametrize("priority,expected_factor", [(0, 1.0), (5, 2.0), (10, 3.0)])
def test_focus_score_priority_scales_linearly(priority, expected_factor):
    topic = Topic(confidence=3, priority=priority, last_reviewed=TODAY)
    expected = (6 - 3) * config.FOCUS_EXAM_FACTOR_NONE * 1.0 * expected_factor
    assert progress.focus_score(topic, [], TODAY) == pytest.approx(expected)


# --------------------------------------------------------------------------
# rank_topics_by_focus
# --------------------------------------------------------------------------
def test_rank_topics_by_focus_excludes_zero_score_topics():
    zero = Topic(topic_id=1, status="Mastered", last_reviewed=TODAY)
    nonzero = Topic(topic_id=2, status="Learning", confidence=1, priority=5, last_reviewed=None)
    ranked = progress.rank_topics_by_focus([zero, nonzero], [], TODAY)
    assert nonzero in ranked
    assert zero not in ranked
    assert len(ranked) == 1


def test_rank_topics_by_focus_orders_highest_score_first():
    low = Topic(topic_id=1, confidence=5, priority=1, last_reviewed=TODAY)
    mid = Topic(topic_id=2, confidence=3, priority=1, last_reviewed=TODAY)
    high = Topic(topic_id=3, confidence=1, priority=5, last_reviewed=None)
    ranked = progress.rank_topics_by_focus([low, mid, high], [], TODAY)
    assert [t.topic_id for t in ranked] == [3, 2, 1]
    scores = [progress.focus_score(t, [], TODAY) for t in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rank_topics_by_focus_limit_truncates_to_top_n():
    topics = [Topic(topic_id=i, confidence=i % 5, priority=i % 5, last_reviewed=TODAY) for i in range(1, 6)]
    full = progress.rank_topics_by_focus(topics, [], TODAY)
    limited = progress.rank_topics_by_focus(topics, [], TODAY, limit=2)
    assert len(limited) == 2
    assert limited == full[:2]


# --------------------------------------------------------------------------
# leitner_next_box / leitner_next_due
# --------------------------------------------------------------------------
@pytest.mark.parametrize("box", [1, 2, 3, 4])
def test_leitner_next_box_correct_increments(box):
    assert progress.leitner_next_box(box, correct=True) == box + 1


def test_leitner_next_box_correct_caps_at_max_box():
    assert progress.leitner_next_box(config.LEITNER_MAX_BOX, correct=True) == config.LEITNER_MAX_BOX


@pytest.mark.parametrize("box", [1, 3, config.LEITNER_MAX_BOX])
def test_leitner_next_box_incorrect_resets_to_min_box(box):
    assert progress.leitner_next_box(box, correct=False) == config.LEITNER_MIN_BOX


def test_leitner_next_due_box_one_uses_first_interval():
    due = progress.leitner_next_due(1, TODAY)
    assert due == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[0])


def test_leitner_next_due_box_five_uses_last_interval():
    due = progress.leitner_next_due(config.LEITNER_MAX_BOX, TODAY)
    assert due == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[-1])


def test_leitner_next_due_out_of_range_box_clamps_to_table_bounds():
    below = progress.leitner_next_due(0, TODAY)
    above = progress.leitner_next_due(99, TODAY)
    assert below == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[0])
    assert above == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[-1])


# --------------------------------------------------------------------------
# practice_answer
# --------------------------------------------------------------------------
def test_practice_answer_correct_updates_counts_box_and_next_due():
    q = PracticeQuestion(box=2, times_attempted=2, times_correct=1)
    result = progress.practice_answer(q, correct=True, today=TODAY)
    assert result is q
    assert q.times_attempted == 3
    assert q.times_correct == 2
    assert q.box == 3
    assert q.last_attempted == TODAY
    assert q.next_due == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[2])


def test_practice_answer_incorrect_resets_box_but_still_counts_attempt():
    q = PracticeQuestion(box=4, times_attempted=5, times_correct=3)
    result = progress.practice_answer(q, correct=False, today=TODAY)
    assert result is q
    assert q.times_attempted == 6
    assert q.times_correct == 3  # unchanged - it was a miss
    assert q.box == config.LEITNER_MIN_BOX
    assert q.last_attempted == TODAY
    assert q.next_due == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[config.LEITNER_MIN_BOX - 1])


# --------------------------------------------------------------------------
# topic_mark_reviewed / topic_status_changed
# --------------------------------------------------------------------------
def test_topic_mark_reviewed_uses_mastered_default_box_when_no_schedule_yet():
    topic = Topic(status="Mastered", last_reviewed=None, next_review=None)
    result = progress.topic_mark_reviewed(topic, TODAY)
    assert result is topic
    assert topic.last_reviewed == TODAY
    # inferred box (Mastered -> 3) bumped by one correct answer -> box 4
    assert topic.next_review == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[3])


def test_topic_mark_reviewed_uses_needs_focus_default_box_when_no_schedule_yet():
    topic = Topic(status="Needs Focus", last_reviewed=None, next_review=None)
    progress.topic_mark_reviewed(topic, TODAY)
    # inferred box (Needs Focus -> 1) bumped by one -> box 2
    assert topic.next_review == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[1])


def test_topic_mark_reviewed_infers_box_from_existing_review_gap_over_status_default():
    # gap of 10 days between last_reviewed and next_review falls into the
    # box-5 bucket (LEITNER_INTERVALS_DAYS[4] == 15 >= 10), even though the
    # topic's status ("Learning") would otherwise default to box 2.
    topic = Topic(
        status="Learning",
        last_reviewed=TODAY - timedelta(days=20),
        next_review=TODAY - timedelta(days=10),
    )
    progress.topic_mark_reviewed(topic, TODAY)
    assert topic.last_reviewed == TODAY
    # inferred box 5, bumped by one correct answer, capped at max box 5
    assert topic.next_review == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[-1])


def test_topic_status_changed_to_mastered_sets_box_three_schedule():
    topic = Topic(status="Not Started", last_reviewed=None, next_review=None)
    result = progress.topic_status_changed(topic, "Mastered", TODAY)
    assert result is topic
    assert topic.status == "Mastered"
    assert topic.last_reviewed == TODAY
    assert topic.next_review == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[2])


def test_topic_status_changed_to_needs_focus_sets_box_one_schedule():
    topic = Topic(status="Learning", last_reviewed=None, next_review=None)
    progress.topic_status_changed(topic, "Needs Focus", TODAY)
    assert topic.status == "Needs Focus"
    assert topic.last_reviewed == TODAY
    assert topic.next_review == TODAY + timedelta(days=config.LEITNER_INTERVALS_DAYS[0])


def test_topic_status_changed_to_unrelated_status_leaves_review_schedule_untouched():
    old_reviewed = TODAY - timedelta(days=30)
    old_next = TODAY - timedelta(days=5)
    topic = Topic(status="Not Started", last_reviewed=old_reviewed, next_review=old_next)
    progress.topic_status_changed(topic, "Learning", TODAY)
    assert topic.status == "Learning"
    assert topic.last_reviewed == old_reviewed
    assert topic.next_review == old_next


# --------------------------------------------------------------------------
# study_streak
# --------------------------------------------------------------------------
def test_study_streak_empty_log_returns_zero_zero():
    assert progress.study_streak([], TODAY) == (0, 0)


def test_study_streak_gap_before_today_limits_current_to_the_recent_run():
    # an older 3-day run, a gap, then a 2-day run ending today
    log = [
        StudyLogEntry(date=TODAY - timedelta(days=10), minutes=20),
        StudyLogEntry(date=TODAY - timedelta(days=9), minutes=20),
        StudyLogEntry(date=TODAY - timedelta(days=8), minutes=20),
        StudyLogEntry(date=TODAY - timedelta(days=1), minutes=20),
        StudyLogEntry(date=TODAY, minutes=20),
    ]
    current, best = progress.study_streak(log, TODAY)
    assert current == 2
    assert best == 3  # best streak is the older run, not the current one


def test_study_streak_ending_yesterday_still_counts_as_current():
    # nothing logged today, but yesterday continues an active streak - and
    # two sub-threshold entries on the same day sum to the minimum minutes.
    log = [
        StudyLogEntry(date=TODAY - timedelta(days=2), minutes=20),
        StudyLogEntry(date=TODAY - timedelta(days=1), minutes=7),
        StudyLogEntry(date=TODAY - timedelta(days=1), minutes=8),  # 7+8 == STREAK_MIN_MINUTES
    ]
    current, best = progress.study_streak(log, TODAY)
    assert current == 2
    assert best == 2


def test_study_streak_day_below_minimum_minutes_does_not_count():
    log = [StudyLogEntry(date=TODAY, minutes=config.STREAK_MIN_MINUTES - 1)]
    assert progress.study_streak(log, TODAY) == (0, 0)


# --------------------------------------------------------------------------
# compute_goal_progress
# --------------------------------------------------------------------------
def _goal(metric: str, **kw) -> Goal:
    return Goal(metric=metric, target_value=1.0, **kw)


def test_compute_goal_progress_study_hours_per_week_sums_only_the_current_week():
    log = [
        StudyLogEntry(date=date(2024, 1, 8), minutes=60),   # Monday, in week
        StudyLogEntry(date=date(2024, 1, 14), minutes=30),  # Sunday, in week
        StudyLogEntry(date=date(2024, 1, 7), minutes=1000),  # prior week, excluded
        StudyLogEntry(date=date(2024, 1, 15), minutes=1000),  # next week, excluded
    ]
    result = progress.compute_goal_progress(_goal("study_hours_per_week"), TODAY, study_log=log)
    assert result == pytest.approx(1.5)  # 90 minutes / 60


def test_compute_goal_progress_study_hours_per_week_none_when_study_log_missing():
    result = progress.compute_goal_progress(_goal("study_hours_per_week"), TODAY)
    assert result is None


def test_compute_goal_progress_grade_at_least_passes_through_current_grade():
    result = progress.compute_goal_progress(_goal("grade_at_least"), TODAY, current_grade=87.5)
    assert result == 87.5


def test_compute_goal_progress_topics_mastered_counts_mastered_topics():
    topics = [
        Topic(status="Mastered"),
        Topic(status="Mastered"),
        Topic(status="Learning"),
        Topic(status="Not Started"),
    ]
    result = progress.compute_goal_progress(_goal("topics_mastered"), TODAY, topics=topics)
    assert result == 2.0


def test_compute_goal_progress_topics_mastered_defaults_to_zero_not_none_when_missing():
    # unlike study_log/questions/prelabs, topics has no explicit "missing"
    # branch - a missing collection is treated as empty, not unknown.
    result = progress.compute_goal_progress(_goal("topics_mastered"), TODAY)
    assert result == 0.0


def test_compute_goal_progress_questions_per_week_counts_attempts_in_week():
    questions = [
        PracticeQuestion(last_attempted=date(2024, 1, 8)),   # in week
        PracticeQuestion(last_attempted=date(2024, 1, 20)),  # out of week
        PracticeQuestion(last_attempted=None),               # never attempted
    ]
    result = progress.compute_goal_progress(_goal("questions_per_week"), TODAY, questions=questions)
    assert result == 1.0


def test_compute_goal_progress_questions_per_week_none_when_questions_missing():
    result = progress.compute_goal_progress(_goal("questions_per_week"), TODAY)
    assert result is None


def test_compute_goal_progress_streak_days_delegates_to_study_streak():
    log = [
        StudyLogEntry(date=TODAY - timedelta(days=1), minutes=20),
        StudyLogEntry(date=TODAY, minutes=20),
    ]
    result = progress.compute_goal_progress(_goal("streak_days"), TODAY, study_log=log)
    assert result == 2.0


def test_compute_goal_progress_streak_days_defaults_to_zero_when_log_missing():
    result = progress.compute_goal_progress(_goal("streak_days"), TODAY)
    assert result == 0.0


def test_compute_goal_progress_prelabs_on_time_pct_computes_percentage():
    prelabs = [
        PreLab(lab_type="Common", lab_date=TODAY - timedelta(days=5),
               prelab_due=TODAY - timedelta(days=6), completed_on=TODAY - timedelta(days=7)),  # on time
        PreLab(lab_type="Common", lab_date=TODAY - timedelta(days=3),
               prelab_due=TODAY - timedelta(days=4), completed_on=None),  # never completed -> late
        PreLab(lab_type="Practice", lab_date=TODAY - timedelta(days=2),
               prelab_due=TODAY - timedelta(days=3), completed_on=TODAY - timedelta(days=3)),  # excluded type
        PreLab(lab_type="Common", lab_date=TODAY + timedelta(days=5),
               prelab_due=TODAY + timedelta(days=4), completed_on=None),  # not yet due, excluded
    ]
    result = progress.compute_goal_progress(_goal("prelabs_on_time_pct"), TODAY, prelabs=prelabs)
    assert result == pytest.approx(50.0)  # 1 of 2 eligible was on time


def test_compute_goal_progress_prelabs_on_time_pct_none_when_prelabs_missing():
    result = progress.compute_goal_progress(_goal("prelabs_on_time_pct"), TODAY)
    assert result is None


def test_compute_goal_progress_prelabs_on_time_pct_none_when_no_eligible_prelabs():
    prelabs = [PreLab(lab_type="Practice", lab_date=TODAY - timedelta(days=1), prelab_due=TODAY - timedelta(days=2))]
    result = progress.compute_goal_progress(_goal("prelabs_on_time_pct"), TODAY, prelabs=prelabs)
    assert result is None


def test_compute_goal_progress_unknown_metric_returns_none():
    result = progress.compute_goal_progress(_goal("not_a_real_metric"), TODAY)
    assert result is None


# --------------------------------------------------------------------------
# goal_status
# --------------------------------------------------------------------------
def test_goal_status_achieved_when_current_value_meets_target():
    goal = Goal(current_value=90.0, target_value=80.0, end_date=date.today() - timedelta(days=1))
    # end_date is already in the past, but hitting the target takes priority
    assert progress.goal_status(goal) == "Achieved"


def test_goal_status_missed_when_past_end_date_without_hitting_target():
    # Regression test: goal_status() previously never returned "Missed" even
    # though the model/config/docstring all expect it - a goal past its
    # end_date without reaching target_value stayed "Active" forever.
    goal = Goal(current_value=50.0, target_value=80.0, end_date=date.today() - timedelta(days=1))
    assert progress.goal_status(goal) == "Missed"


def test_goal_status_missed_when_current_value_is_none_and_past_end_date():
    goal = Goal(current_value=None, target_value=80.0, end_date=date.today() - timedelta(days=1))
    assert progress.goal_status(goal) == "Missed"


def test_goal_status_active_when_end_date_in_future_and_target_not_met():
    goal = Goal(current_value=10.0, target_value=80.0, end_date=date.today() + timedelta(days=10))
    assert progress.goal_status(goal) == "Active"


def test_goal_status_active_when_end_date_is_exactly_today():
    goal = Goal(current_value=10.0, target_value=80.0, end_date=date.today())
    assert progress.goal_status(goal) == "Active"


def test_goal_status_active_when_no_end_date_and_no_current_value():
    goal = Goal(current_value=None, target_value=80.0, end_date=None)
    assert progress.goal_status(goal) == "Active"
