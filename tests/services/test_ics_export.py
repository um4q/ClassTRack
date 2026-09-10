"""
Tests for app.services.ics_export - pure functions, no Qt, no store.

All fixtures here are plain dataclass instances built directly from
app.models; see the module docstring in app/services/ics_export.py (§5.11)
for the rules under test.
"""
from __future__ import annotations

from datetime import date, time

import pytest

from app.models import Assessment, Course, Holiday, PreLab, ScheduleRow
from app.services.ics_export import build_ics


def _course(course_id=1, code="ELEX2500"):
    return Course(course_id=course_id, code=code, name="Digital Systems", term="Fall 2026")


# --------------------------------------------------------------------------
# Overall envelope
# --------------------------------------------------------------------------
def test_build_ics_empty_input_still_wraps_valid_vcalendar_with_crlf():
    ics = build_ics(courses=[], assessments=[], prelabs=[])
    assert ics.startswith("BEGIN:VCALENDAR" + "\r\n")
    assert ics.endswith("END:VCALENDAR\r\n")
    assert "\r\n" in ics
    # No bare LF anywhere - every line ending must be a full CRLF pair.
    assert "\r\n".join(ics.split("\r\n")) == ics
    lines = ics.split("\r\n")
    assert lines[0] == "BEGIN:VCALENDAR"
    assert "VERSION:2.0" in lines
    assert lines[-2] == "END:VCALENDAR"
    assert lines[-1] == ""  # trailing CRLF produces one empty split segment


# --------------------------------------------------------------------------
# Assessments: timed vs all-day
# --------------------------------------------------------------------------
def test_build_ics_assessment_with_due_time_produces_timed_dtstart():
    course = _course()
    a = Assessment(
        assessment_id=7,
        course_id=1,
        type="Quiz",
        title="Chapter 3",
        due_date=date(2026, 10, 5),
        due_time=time(14, 30),
    )
    ics = build_ics(courses=[course], assessments=[a], prelabs=[])
    assert "DTSTART:20261005T143000" in ics
    assert "DTEND:20261005T153000" in ics  # defaults to 1-hour duration
    assert "UID:assessment-7@studytracker" in ics
    assert "SUMMARY:ELEX2500: Quiz - Chapter 3" in ics
    # Must NOT also emit an all-day DATE-valued DTSTART for this event.
    assert "DTSTART;VALUE=DATE" not in ics


def test_build_ics_assessment_without_due_time_produces_all_day_date_value():
    course = _course()
    a = Assessment(
        assessment_id=8,
        course_id=1,
        type="Assignment",
        title="Lab Report",
        due_date=date(2026, 11, 1),
        due_time=None,
    )
    ics = build_ics(courses=[course], assessments=[a], prelabs=[])
    assert "DTSTART;VALUE=DATE:20261101" in ics
    assert "DTEND;VALUE=DATE:20261102" in ics
    assert "DTSTART:2026" not in ics  # no timed-form DTSTART present


def test_build_ics_assessment_without_due_date_is_skipped_entirely():
    course = _course()
    a = Assessment(assessment_id=9, course_id=1, title="No date yet", due_date=None)
    ics = build_ics(courses=[course], assessments=[a], prelabs=[])
    assert "assessment-9@studytracker" not in ics
    assert "BEGIN:VEVENT" not in ics


# --------------------------------------------------------------------------
# PreLabs: lab_type "None" excluded, others included
# --------------------------------------------------------------------------
def test_build_ics_prelab_with_lab_type_none_is_excluded():
    course = _course()
    p = PreLab(
        prelab_id=3,
        course_id=1,
        lab_number="4",
        title="Skipped Lab",
        lab_type="None",
        lab_date=date(2026, 10, 10),
    )
    ics = build_ics(courses=[course], assessments=[], prelabs=[p])
    assert "prelab-3@studytracker" not in ics
    assert "BEGIN:VEVENT" not in ics


def test_build_ics_prelab_with_real_lab_type_included_with_summary_and_location():
    course = _course()
    p = PreLab(
        prelab_id=4,
        course_id=1,
        lab_number="4",
        title="Op-Amp Circuits",
        lab_type="Common",
        lab_date=date(2026, 10, 12),
        lab_time=time(9, 0),
        room="B203",
    )
    ics = build_ics(courses=[course], assessments=[], prelabs=[p])
    assert "UID:prelab-4@studytracker" in ics
    assert "SUMMARY:ELEX2500: Lab 4 - Op-Amp Circuits" in ics
    assert "LOCATION:B203" in ics
    assert "DTSTART:20261012T090000" in ics


def test_build_ics_prelab_without_lab_date_is_skipped_even_if_lab_type_valid():
    course = _course()
    p = PreLab(prelab_id=5, course_id=1, lab_type="Common", lab_date=None)
    ics = build_ics(courses=[course], assessments=[], prelabs=[p])
    assert "prelab-5@studytracker" not in ics


# --------------------------------------------------------------------------
# RFC5545 text escaping
# --------------------------------------------------------------------------
def test_build_ics_escapes_comma_semicolon_and_embedded_newline_in_notes_and_title():
    course = _course()
    a = Assessment(
        assessment_id=10,
        course_id=1,
        type="Exam",
        title="Midterm; Units 1,2",
        due_date=date(2026, 10, 20),
        due_time=time(10, 0),
        notes="Bring calculator,\ncrib sheet; no phones",
    )
    ics = build_ics(courses=[course], assessments=[a], prelabs=[])
    # SUMMARY: the semicolon and comma in the title must be backslash-escaped.
    assert "SUMMARY:ELEX2500: Exam - Midterm\\; Units 1\\,2" in ics
    # DESCRIPTION: comma, semicolon, and the embedded newline (as \n) all escaped.
    assert "DESCRIPTION:Bring calculator\\,\\ncrib sheet\\; no phones" in ics
    # Raw (unescaped) forms must not appear on their own.
    assert "Midterm; Units" not in ics
    assert "Units 1,2" not in ics


# --------------------------------------------------------------------------
# include_classes=False: never emit class VEVENTs
# --------------------------------------------------------------------------
def test_build_ics_include_classes_false_omits_schedule_even_when_passed():
    course = _course()
    schedule = [
        ScheduleRow(
            schedule_id=1,
            course_id=1,
            kind="Lecture",
            day_of_week="Monday",
            start_time=time(9, 0),
            end_time=time(10, 0),
        )
    ]
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=schedule,
        include_classes=False,
        semester_start=date(2026, 9, 1),
        semester_end=date(2026, 12, 15),
    )
    assert "schedule-1@studytracker" not in ics
    assert "BEGIN:VEVENT" not in ics


# --------------------------------------------------------------------------
# include_classes=True, weekly recurring row (no explicit dates) -> RRULE
# --------------------------------------------------------------------------
def test_build_ics_weekly_schedule_row_without_dates_produces_single_rrule_vevent():
    course = _course()
    row = ScheduleRow(
        schedule_id=2,
        course_id=1,
        kind="Lecture",
        section="A1",
        day_of_week="Wednesday",
        start_time=time(9, 0),
        end_time=time(10, 30),
        room="B101",
    )
    semester_start = date(2026, 9, 8)  # a Tuesday
    semester_end = date(2026, 12, 11)
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=[row],
        include_classes=True,
        semester_start=semester_start,
        semester_end=semester_end,
    )
    # Exactly one VEVENT for this schedule row (the recurring one), not one
    # per week.
    assert ics.count("UID:schedule-2@studytracker") == 1
    assert "RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20261211T235959Z" in ics
    # First occurrence on/after semester_start that falls on Wednesday.
    assert "DTSTART:20260909T090000" in ics
    assert "DTEND:20260909T103000" in ics
    assert "SUMMARY:ELEX2500: Lecture (A1)" in ics
    assert "LOCATION:B101" in ics


# --------------------------------------------------------------------------
# include_classes=True, explicit dates row -> one VEVENT per date
# --------------------------------------------------------------------------
def test_build_ics_schedule_row_with_explicit_dates_produces_one_vevent_per_date():
    course = _course()
    row = ScheduleRow(
        schedule_id=3,
        course_id=1,
        kind="Lab",
        day_of_week="Friday",
        start_time=time(13, 0),
        end_time=time(16, 0),
        room="C210",
        dates=[date(2026, 9, 18), date(2026, 10, 2)],
    )
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=[row],
        include_classes=True,
        semester_start=date(2026, 9, 1),
        semester_end=date(2026, 12, 15),
    )
    # No RRULE at all for an explicit-dates row.
    assert "RRULE" not in ics
    assert "UID:schedule-3-2026-09-18@studytracker" in ics
    assert "UID:schedule-3-2026-10-02@studytracker" in ics
    assert ics.count("SUMMARY:ELEX2500: Lab") == 2
    assert "DTSTART:20260918T130000" in ics
    assert "DTSTART:20261002T130000" in ics


def test_build_ics_explicit_date_outside_semester_bounds_is_excluded():
    course = _course()
    row = ScheduleRow(
        schedule_id=4,
        course_id=1,
        kind="Lab",
        day_of_week="Friday",
        start_time=time(13, 0),
        end_time=time(16, 0),
        dates=[date(2026, 8, 20), date(2026, 10, 2), date(2027, 1, 5)],
    )
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=[row],
        include_classes=True,
        semester_start=date(2026, 9, 1),
        semester_end=date(2026, 12, 15),
    )
    # Only the in-range date survives; the ones before/after the semester
    # are silently dropped.
    assert ics.count("BEGIN:VEVENT") == 1
    assert "schedule-4-2026-10-02@studytracker" in ics
    assert "schedule-4-2026-08-20@studytracker" not in ics
    assert "schedule-4-2027-01-05@studytracker" not in ics


def test_build_ics_explicit_date_on_holiday_is_excluded():
    course = _course()
    row = ScheduleRow(
        schedule_id=5,
        course_id=1,
        kind="Lab",
        day_of_week="Friday",
        dates=[date(2026, 10, 9), date(2026, 10, 16)],
    )
    holidays = [Holiday(date=date(2026, 10, 9), name="Thanksgiving", classes_cancelled=True)]
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=[row],
        holidays=holidays,
        include_classes=True,
        semester_start=date(2026, 9, 1),
        semester_end=date(2026, 12, 15),
    )
    assert ics.count("BEGIN:VEVENT") == 1
    assert "schedule-5-2026-10-16@studytracker" in ics
    assert "schedule-5-2026-10-09@studytracker" not in ics


# --------------------------------------------------------------------------
# include_classes=True but semester bounds missing -> degrade, never raise
# --------------------------------------------------------------------------
def test_build_ics_include_classes_true_without_semester_bounds_omits_class_events():
    course = _course()
    row = ScheduleRow(
        schedule_id=6,
        course_id=1,
        kind="Lecture",
        day_of_week="Monday",
        start_time=time(9, 0),
        end_time=time(10, 0),
    )
    # No semester_start/semester_end supplied at all.
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=[row],
        include_classes=True,
    )
    assert "schedule-6@studytracker" not in ics
    assert "BEGIN:VEVENT" not in ics
    assert ics.startswith("BEGIN:VCALENDAR")
    assert ics.endswith("END:VCALENDAR\r\n")


def test_build_ics_include_classes_true_with_only_semester_start_omits_class_events():
    course = _course()
    row = ScheduleRow(schedule_id=7, course_id=1, kind="Lecture", day_of_week="Monday")
    ics = build_ics(
        courses=[course],
        assessments=[],
        prelabs=[],
        schedule=[row],
        include_classes=True,
        semester_start=date(2026, 9, 1),
        semester_end=None,
    )
    assert "schedule-7@studytracker" not in ics
    assert "BEGIN:VEVENT" not in ics
