"""Tests for app.services.scheduler - pure functions, no Qt, no store.

All ScheduleRow/Holiday/PreLab/Assessment/Topic instances below are
constructed directly from app.models dataclasses; nothing here touches a
workbook.
"""
from __future__ import annotations

import math
from datetime import date, time, timedelta

import pytest

from app.models import Assessment, Holiday, PreLab, ScheduleRow, Topic
from app.services import scheduler

# A real Monday, matching config.SETTINGS_DEFAULTS["week1_monday"] - used as
# the term's week-1 anchor by every test below that needs one.
WEEK1_MONDAY = date(2026, 8, 31)


# --------------------------------------------------------------------------
# term_week_number / week_start
# --------------------------------------------------------------------------
def test_term_week_number_on_week1_monday_itself_is_week_1():
    assert scheduler.term_week_number(WEEK1_MONDAY, WEEK1_MONDAY) == 1


def test_term_week_number_last_day_of_week1_is_still_week_1():
    assert scheduler.term_week_number(WEEK1_MONDAY + timedelta(days=6), WEEK1_MONDAY) == 1


def test_term_week_number_first_day_of_week2_rolls_over():
    assert scheduler.term_week_number(WEEK1_MONDAY + timedelta(days=7), WEEK1_MONDAY) == 2


def test_week_start_returns_the_monday_for_a_given_week_number():
    assert scheduler.week_start(1, WEEK1_MONDAY) == WEEK1_MONDAY
    assert scheduler.week_start(3, WEEK1_MONDAY) == WEEK1_MONDAY + timedelta(weeks=2)


@pytest.mark.parametrize(
    "offset_days",
    [0, 1, 3, 6, 7, 13, 14, 90, 365],
    ids=["mon-of-wk1", "tue-of-wk1", "thu-of-wk1", "sun-of-wk1", "mon-of-wk2",
         "sun-of-wk2", "mon-of-wk3", "3-months-out", "1-year-out"],
)
def test_term_week_number_and_week_start_round_trip(offset_days):
    """week_start(term_week_number(d, m), m)'s week must contain d - i.e. the
    resulting Monday is <= d and d falls within that 7-day block."""
    d = WEEK1_MONDAY + timedelta(days=offset_days)
    wk = scheduler.term_week_number(d, WEEK1_MONDAY)
    ws = scheduler.week_start(wk, WEEK1_MONDAY)
    assert ws <= d < ws + timedelta(days=7)
    assert ws.weekday() == 0  # a Monday


# --------------------------------------------------------------------------
# schedule_row_occurs_on
# --------------------------------------------------------------------------
def test_schedule_row_occurs_on_weekly_row_matches_its_weekday_every_week():
    row = ScheduleRow(day_of_week="Monday")
    a_monday = date(2026, 9, 7)
    another_monday = date(2026, 9, 14)
    a_tuesday = date(2026, 9, 8)
    assert scheduler.schedule_row_occurs_on(row, a_monday) is True
    assert scheduler.schedule_row_occurs_on(row, another_monday) is True
    assert scheduler.schedule_row_occurs_on(row, a_tuesday) is False


def test_schedule_row_occurs_on_explicit_dates_ignore_day_of_week_entirely():
    """A row with explicit dates occurs ONLY on those exact dates, even if
    day_of_week names a different (mismatched) weekday - and it does NOT
    occur on a date that matches day_of_week but isn't in the explicit
    list (e.g. the bi-weekly Friday-lab case)."""
    explicit_friday = date(2026, 9, 4)  # a real Friday
    other_friday = date(2026, 9, 11)    # matches day_of_week, not in dates
    row = ScheduleRow(day_of_week="Monday", dates=[explicit_friday])  # deliberately mismatched
    assert scheduler.schedule_row_occurs_on(row, explicit_friday) is True
    assert scheduler.schedule_row_occurs_on(row, other_friday) is False
    # Also does not fall back to matching an actual Monday, since dates is set.
    a_monday = date(2026, 9, 7)
    assert scheduler.schedule_row_occurs_on(row, a_monday) is False


# --------------------------------------------------------------------------
# is_holiday / holiday_dates
# --------------------------------------------------------------------------
def test_is_holiday_true_only_for_a_cancelled_holiday_on_that_exact_date():
    d1, d2 = date(2026, 10, 12), date(2026, 10, 13)
    holidays = [Holiday(date=d1, name="Thanksgiving", classes_cancelled=True)]
    assert scheduler.is_holiday(d1, holidays) is True
    assert scheduler.is_holiday(d2, holidays) is False


def test_is_holiday_false_when_classes_are_not_actually_cancelled():
    d = date(2026, 10, 12)
    holidays = [Holiday(date=d, name="Observance", classes_cancelled=False)]
    assert scheduler.is_holiday(d, holidays) is False


def test_holiday_dates_excludes_uncancelled_and_dateless_rows():
    d1 = date(2026, 10, 12)
    d2 = date(2026, 11, 11)
    holidays = [
        Holiday(date=d1, name="Real holiday", classes_cancelled=True),
        Holiday(date=d2, name="Observance only", classes_cancelled=False),
        Holiday(date=None, name="TBD", classes_cancelled=True),
    ]
    assert scheduler.holiday_dates(holidays) == {d1}


# --------------------------------------------------------------------------
# agenda
# --------------------------------------------------------------------------
def test_agenda_on_holiday_excludes_schedule_but_keeps_prelabs_and_assessments():
    """Precise contract check (§6.8): a holiday day drops Schedule rows but
    NOT PreLabs or Assessments due that day."""
    d = date(2026, 9, 7)
    schedule = [ScheduleRow(schedule_id=1, course_id=1, kind="Lecture",
                             section="A1", day_of_week="Monday",
                             start_time=time(9, 0), end_time=time(10, 0))]
    holidays = [Holiday(date=d, name="Holiday", classes_cancelled=True)]
    prelabs = [
        PreLab(prelab_id=5, course_id=1, lab_number="1", title="Intro Lab",
               lab_type="Common", lab_date=d, lab_time=time(8, 0)),
        PreLab(prelab_id=6, course_id=1, lab_number="2", title="No-lab day",
               lab_type="None", lab_date=d, lab_time=time(7, 0)),
    ]
    assessments = [Assessment(assessment_id=9, course_id=1, type="Quiz",
                               title="Quiz 1", due_date=d)]

    items = scheduler.agenda(d, schedule, holidays, prelabs, assessments)

    kinds = [i.kind for i in items]
    ref_ids = [i.ref_id for i in items]
    assert "Lecture" not in kinds  # Schedule row dropped on a holiday
    assert "PreLab" in kinds  # PreLab NOT dropped on a holiday
    assert "Assessment" in kinds  # Assessment NOT dropped on a holiday
    assert 6 not in ref_ids  # lab_type == "None" is always excluded
    assert len(items) == 2


def test_agenda_on_non_holiday_includes_matching_schedule_rows():
    d = date(2026, 9, 7)
    schedule = [ScheduleRow(schedule_id=1, course_id=1, kind="Lecture",
                             section="A1", day_of_week="Monday",
                             start_time=time(9, 0), end_time=time(10, 0),
                             room="R100")]
    items = scheduler.agenda(d, schedule, [], [], [])
    assert len(items) == 1
    item = items[0]
    assert item.kind == "Lecture"
    assert item.title == "Lecture (A1)"
    assert item.ref_id == 1
    assert item.room == "R100"
    assert item.start_time == time(9, 0)


def test_agenda_excludes_prelab_with_lab_type_none_even_off_holiday():
    d = date(2026, 9, 7)
    prelabs = [PreLab(prelab_id=6, course_id=1, lab_number="2", title="Skip",
                       lab_type="None", lab_date=d)]
    assert scheduler.agenda(d, [], [], prelabs, []) == []


def test_agenda_sorts_by_time_ascending_with_none_time_items_last():
    d = date(2026, 9, 7)
    schedule = [
        ScheduleRow(schedule_id=1, course_id=1, kind="Lecture", section="A1",
                    day_of_week="Monday", start_time=time(13, 0)),
        ScheduleRow(schedule_id=2, course_id=1, kind="Tutorial", section="B1",
                    day_of_week="Monday", start_time=time(9, 0)),
    ]
    prelabs = [PreLab(prelab_id=5, course_id=1, lab_number="1", title="Lab",
                       lab_type="Common", lab_date=d, lab_time=None)]
    assessments = [Assessment(assessment_id=9, course_id=1, type="Quiz",
                               title="Quiz", due_date=d, due_time=time(11, 0))]

    items = scheduler.agenda(d, schedule, [], prelabs, assessments)

    assert [i.ref_id for i in items] == [2, 9, 1, 5]
    assert [i.start_time for i in items] == [time(9, 0), time(11, 0), time(13, 0), None]


def test_agenda_flags_assessment_whose_notes_signal_a_conflict():
    d = date(2026, 9, 7)
    assessments = [Assessment(assessment_id=1, course_id=1, type="Midterm",
                               title="Midterm 1", due_date=d, notes="CONFLICT with lab")]
    items = scheduler.agenda(d, [], [], [], assessments)
    assert items[0].flagged is True


# --------------------------------------------------------------------------
# week_view
# --------------------------------------------------------------------------
def test_week_view_returns_exactly_five_weekdays_monday_through_friday():
    monday = date(2026, 9, 7)
    week = scheduler.week_view(monday, [], [], [], [])
    assert len(week) == 5
    dates = list(week.keys())
    assert dates == [monday + timedelta(days=i) for i in range(5)]
    assert [d.strftime("%A") for d in dates] == [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    ]


def test_week_view_places_each_days_agenda_under_its_own_date():
    monday = date(2026, 9, 7)
    wednesday = monday + timedelta(days=2)
    schedule = [ScheduleRow(schedule_id=1, course_id=1, kind="Lab",
                             section="X02", day_of_week="Wednesday",
                             start_time=time(14, 0))]
    week = scheduler.week_view(monday, schedule, [], [], [])
    assert week[wednesday][0].kind == "Lab"
    assert week[monday] == []


# --------------------------------------------------------------------------
# generate_roadmap
# --------------------------------------------------------------------------
def _topic(topic_id, status="Learning", hours=2.0, unit="Unit 1", section="1.1"):
    return Topic(topic_id=topic_id, course_id=1, unit=unit, section=section,
                 title=f"Topic {topic_id}", status=status, estimated_hours=hours)


def test_generate_roadmap_normal_case_distributes_topics_across_weeks_with_final_review():
    """8 non-mastered topics at 2h each over a 4-week span (16h / 4 weeks =
    4h/week => exactly 2 topics per weekly bucket), plus a trailing
    3-day review milestone."""
    week1_monday = WEEK1_MONDAY
    start_from = date(2026, 9, 7)     # a Monday
    horizon_end = date(2026, 10, 5)   # a Monday, 4 weeks later
    topics = [_topic(i) for i in range(1, 9)]

    items = scheduler.generate_roadmap(course_id=1, topics=topics,
                                        week1_monday=week1_monday,
                                        start_from=start_from,
                                        horizon_end=horizon_end)

    assert len(items) == 5  # 4 weekly milestones + 1 final review
    assert all(it.auto_generated is True for it in items)
    assert all(it.course_id == 1 for it in items)
    assert all(it.status == "Planned" for it in items)

    weekly_items, review_item = items[:-1], items[-1]
    assert all("Week " in it.milestone for it in weekly_items)
    assert "Review + mock exam" in review_item.milestone
    assert review_item.end_date == horizon_end
    assert review_item.topic_ids == []  # review row carries no topics of its own

    # Every non-mastered topic is scheduled into exactly one weekly bucket.
    all_scheduled = [tid for it in weekly_items for tid in it.topic_ids]
    assert sorted(all_scheduled) == list(range(1, 9))
    assert len(all_scheduled) == len(set(all_scheduled))  # no duplicates
    assert [len(it.topic_ids) for it in weekly_items] == [2, 2, 2, 2]

    # sort_order is a contiguous 1-based sequence matching list order.
    assert [it.sort_order for it in items] == list(range(1, len(items) + 1))


def test_generate_roadmap_mastered_topics_are_excluded_from_buckets():
    week1_monday = WEEK1_MONDAY
    start_from = date(2026, 9, 7)
    horizon_end = date(2026, 10, 5)
    topics = [_topic(1, status="Mastered"), _topic(2), _topic(3)]

    items = scheduler.generate_roadmap(1, topics, week1_monday, start_from, horizon_end)

    all_scheduled = [tid for it in items for tid in it.topic_ids]
    assert 1 not in all_scheduled
    assert set(all_scheduled) == {2, 3}


def test_generate_roadmap_not_enough_time_still_returns_a_sensible_single_item():
    """review_start (horizon_end - 3 days) <= start_from: no room for weekly
    buckets, but the function must still return something usable rather than
    crashing or returning an empty list."""
    week1_monday = WEEK1_MONDAY
    start_from = date(2026, 12, 1)
    horizon_end = date(2026, 12, 3)  # review_start = Nov 30 <= start_from
    topics = [_topic(1), _topic(2), _topic(3)]

    items = scheduler.generate_roadmap(1, topics, week1_monday, start_from, horizon_end)

    assert len(items) == 1
    item = items[0]
    assert "Review + mock exam" in item.milestone
    assert item.auto_generated is True
    assert item.start_date == max(start_from, horizon_end - timedelta(days=3))
    assert item.end_date == horizon_end
    assert sorted(item.topic_ids) == [1, 2, 3]  # all pending topics folded in


def test_generate_roadmap_all_topics_already_mastered_returns_empty_pool_review_item():
    """An empty pool (every topic Mastered) hits the same short-circuit as
    the not-enough-time case, but for a different reason - confirm it still
    returns one review item with no topics, not an empty list."""
    week1_monday = WEEK1_MONDAY
    start_from = date(2026, 9, 7)
    horizon_end = date(2026, 10, 5)  # plenty of time - not the time-based branch
    topics = [_topic(i, status="Mastered") for i in range(1, 9)]

    items = scheduler.generate_roadmap(1, topics, week1_monday, start_from, horizon_end)

    assert len(items) == 1
    assert "Review + mock exam" in items[0].milestone
    assert items[0].topic_ids == []
    assert items[0].auto_generated is True


def test_generate_roadmap_target_topic_ids_restricts_the_pool():
    week1_monday = WEEK1_MONDAY
    start_from = date(2026, 9, 7)
    horizon_end = date(2026, 10, 5)
    topics = [_topic(1), _topic(2), _topic(3)]

    items = scheduler.generate_roadmap(1, topics, week1_monday, start_from,
                                        horizon_end, target_topic_ids={2})

    all_scheduled = [tid for it in items for tid in it.topic_ids]
    assert set(all_scheduled) == {2}


# --------------------------------------------------------------------------
# roadmap_pace
# --------------------------------------------------------------------------
def test_roadmap_pace_needed_pace_is_infinite_when_zero_weeks_remaining():
    """weeks_remaining == 0 must not raise ZeroDivisionError; the function
    signals 'impossible pace' via +inf instead."""
    today = date(2026, 9, 10)
    topics = [Topic(topic_id=1, status="Learning"), Topic(topic_id=2, status="Not Started")]
    current_pace, needed_pace = scheduler.roadmap_pace(topics, today, 0)
    assert needed_pace == math.inf
    assert current_pace == 0.0  # no topics mastered in the last 14 days


def test_roadmap_pace_negative_weeks_remaining_is_also_infinite_not_negative():
    today = date(2026, 9, 10)
    topics = [Topic(topic_id=1, status="Learning")]
    _, needed_pace = scheduler.roadmap_pace(topics, today, -1.0)
    assert needed_pace == math.inf


def test_roadmap_pace_normal_case_computes_both_paces():
    today = date(2026, 9, 10)
    topics = [
        Topic(topic_id=1, status="Mastered", last_reviewed=today - timedelta(days=5)),
        Topic(topic_id=2, status="Mastered", last_reviewed=today - timedelta(days=20)),  # outside 14d window
        Topic(topic_id=3, status="Learning"),
        Topic(topic_id=4, status="Not Started"),
    ]
    current_pace, needed_pace = scheduler.roadmap_pace(topics, today, 2.0)
    assert current_pace == pytest.approx(0.5)   # 1 mastered-in-14-days / 2.0
    assert needed_pace == pytest.approx(1.0)     # 2 remaining / 2.0 weeks
