"""
Central configuration: paths, sheet schema, vocabularies, and colors.

Nothing in this module touches Qt or openpyxl directly - it is safe to import
from anywhere (services, ui, excel_store) without creating import cycles.
"""
from __future__ import annotations

import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
# BASE_DIR = the study_tracker/ project root (parent of the app/ package).
if getattr(sys, "frozen", False):  # pragma: no cover - not used in dev runs
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "data"
WORKBOOK_PATH = DATA_DIR / "study_tracker.xlsx"
BACKUPS_DIR = DATA_DIR / "backups"
COURSEPACKS_DIR = DATA_DIR / "coursepacks"
LOG_PATH = DATA_DIR / "app.log"
ASSETS_DIR = BASE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.png"
STYLES_QSS_PATH = Path(__file__).resolve().parent / "ui" / "styles.qss"

APP_NAME = "Study Tracker"
APP_ORG = "NAIT Instrumentation"

BACKUP_KEEP_COUNT = 20
AUTOSAVE_DEBOUNCE_MS = 2000
SAVE_LOCK_RETRY_MS = 10_000
TRAY_POLL_MS = 30 * 60 * 1000  # 30 minutes

# --------------------------------------------------------------------------
# Excel sheet schema - the canonical column order for every sheet.
# Used to build an empty workbook (with the right headers + dropdowns) when
# no workbook is found, and as the single source of truth for column order
# when writing rows. Keep in sync with models.py field order.
# --------------------------------------------------------------------------
SHEET_HEADERS: dict[str, list[str]] = {
    "Overview": [],  # formulas sheet; app never writes to it
    "README": [],  # free text sheet; app never writes to it
    "Courses": [
        "course_id", "code", "name", "term", "instructor", "email", "office",
        "lecture_section", "lab_section", "target_grade", "color_hex",
        "syllabus_path", "theory_coursepack", "lab_coursepack",
        "min_lab_completion_pct", "notes", "active",
    ],
    "Materials": [
        "material_id", "course_id", "label", "kind", "path", "sort_order",
    ],
    "GradeWeights": [
        "weight_id", "course_id", "component", "category", "weight_pct",
        "drop_lowest", "notes",
    ],
    "Topics": [
        "topic_id", "course_id", "unit", "section", "title", "status",
        "confidence", "priority", "link", "page", "estimated_hours",
        "last_reviewed", "next_review", "notes",
    ],
    "Assessments": [
        "assessment_id", "course_id", "type", "title", "category",
        "due_date", "due_time", "location", "status", "score", "max_score",
        "weight_override", "topic_ids", "estimated_hours", "notes",
    ],
    "PreLabs": [
        "prelab_id", "course_id", "lab_number", "title", "lab_type",
        "lab_date", "lab_time", "room", "prelab_due", "checklist", "link",
        "completed", "completed_on", "notes",
    ],
    "Schedule": [
        "schedule_id", "course_id", "kind", "section", "day_of_week",
        "start_time", "end_time", "room", "dates",
    ],
    "Holidays": ["date", "name", "classes_cancelled"],
    "SyllabusFlags": [
        "flag_id", "course_id", "item", "issue", "source", "resolved",
    ],
    "StudyLog": [
        "log_id", "course_id", "topic_id", "date", "start_time", "end_time",
        "minutes", "activity", "notes",
    ],
    "Attendance": ["attendance_id", "course_id", "date", "status"],
    "Goals": [
        "goal_id", "scope", "course_id", "description", "metric",
        "target_value", "current_value", "start_date", "end_date", "status",
    ],
    "PracticeQuestions": [
        "question_id", "course_id", "topic_id", "question_type",
        "question_text", "options", "answer_text", "explanation",
        "difficulty", "tags", "box", "times_attempted", "times_correct",
        "last_attempted", "next_due", "image_path",
    ],
    "MockExams": [
        "exam_id", "course_id", "name", "date_taken", "time_limit_min",
        "question_ids", "score", "max_score", "results_json", "duration_min",
    ],
    "Roadmap": [
        "roadmap_id", "course_id", "sort_order", "milestone", "start_date",
        "end_date", "topic_ids", "status", "auto_generated", "notes",
    ],
    "BrainDump": [
        "note_id", "date", "course_id", "title", "body_html", "tags",
        "created_at", "updated_at",
    ],
    "Settings": ["key", "value"],
}

# Sheets the app treats as row-record tables with an auto-incrementing first
# column (used by excel_store's generic CRUD engine + empty-template builder).
# Overview/README/Settings are excluded (formulas / free text / key-value).
CRUD_SHEETS = [s for s in SHEET_HEADERS if s not in ("Overview", "README", "Settings")]

# Excel data-validation dropdown lists, keyed by (sheet, column). Applied to a
# generous row range when building an empty workbook, and left untouched
# (never re-applied per-cell) when writing to the supplied workbook.
DATA_VALIDATIONS: dict[tuple[str, str], list[str]] = {
    ("Courses", "active"): ["TRUE", "FALSE"],
    ("Materials", "kind"): [
        "Coursepack", "Lab Coursepack", "Syllabus", "Course Outline",
        "Lecture Slides", "Lab Manual", "Lab Procedure", "Solutions",
        "Appendix", "Website", "Other",
    ],
    ("GradeWeights", "component"): ["Theory", "Lab"],
    ("Topics", "status"): ["Not Started", "Copied", "Learning", "Needs Focus", "Mastered"],
    ("Topics", "confidence"): ["1", "2", "3", "4", "5"],
    ("Topics", "priority"): ["1", "2", "3", "4", "5"],
    ("Assessments", "type"): [
        "Assignment", "Quiz", "Midterm", "Final", "Lab Report", "Project",
        "Presentation", "Practical Lab Assessment",
    ],
    ("Assessments", "status"): ["Not Started", "In Progress", "Submitted", "Graded"],
    ("PreLabs", "lab_type"): [
        "Common", "Rotational", "Weekly", "Assessment", "Practice",
        "Project", "None",
    ],
    ("PreLabs", "completed"): ["TRUE", "FALSE"],
    ("Schedule", "kind"): ["Lecture", "Lab", "Tutorial", "Seminar", "Study Block"],
    ("Schedule", "day_of_week"): [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    ],
    ("SyllabusFlags", "resolved"): ["TRUE", "FALSE"],
    ("Goals", "scope"): ["Semester", "Weekly", "Course"],
    ("Goals", "metric"): [
        "study_hours_per_week", "grade_at_least", "topics_mastered",
        "questions_per_week", "streak_days", "prelabs_on_time_pct",
    ],
    ("Goals", "status"): ["Active", "Achieved", "Missed"],
    ("PracticeQuestions", "question_type"): ["MCQ", "Short Answer", "Numeric", "True/False"],
    ("PracticeQuestions", "difficulty"): ["1", "2", "3", "4", "5"],
    ("PracticeQuestions", "box"): ["1", "2", "3", "4", "5"],
    ("StudyLog", "activity"): [
        "Reading", "Copying Notes", "Practice", "Review", "Lecture", "Lab", "Group Study",
    ],
    ("Attendance", "status"): ["Present", "Absent", "Late", "Excused"],
    ("Roadmap", "status"): ["Planned", "In Progress", "Done", "Slipped"],
    ("Roadmap", "auto_generated"): ["TRUE", "FALSE"],
}

# --------------------------------------------------------------------------
# Vocabularies (plain lists, mirrors DATA_VALIDATIONS values for UI combos)
# --------------------------------------------------------------------------
TOPIC_STATUSES = ["Not Started", "Copied", "Learning", "Needs Focus", "Mastered"]
MATERIAL_KINDS = DATA_VALIDATIONS[("Materials", "kind")]
GRADEWEIGHT_COMPONENTS = ["Theory", "Lab"]
ASSESSMENT_TYPES = DATA_VALIDATIONS[("Assessments", "type")]
ASSESSMENT_STATUSES = DATA_VALIDATIONS[("Assessments", "status")]
PRELAB_TYPES = DATA_VALIDATIONS[("PreLabs", "lab_type")]
SCHEDULE_KINDS = DATA_VALIDATIONS[("Schedule", "kind")]
DAYS_OF_WEEK = DATA_VALIDATIONS[("Schedule", "day_of_week")]
GOAL_SCOPES = DATA_VALIDATIONS[("Goals", "scope")]
GOAL_METRICS = DATA_VALIDATIONS[("Goals", "metric")]
GOAL_STATUSES = DATA_VALIDATIONS[("Goals", "status")]
PRACTICE_QUESTION_TYPES = DATA_VALIDATIONS[("PracticeQuestions", "question_type")]
STUDYLOG_ACTIVITIES = DATA_VALIDATIONS[("StudyLog", "activity")]
ATTENDANCE_STATUSES = DATA_VALIDATIONS[("Attendance", "status")]
ROADMAP_STATUSES = DATA_VALIDATIONS[("Roadmap", "status")]

MATERIAL_KIND_GROUPS = {
    "Coursepack": ["Coursepack", "Lab Coursepack"],
    "Syllabus": ["Syllabus", "Course Outline", "Lecture Slides", "Lab Manual"],
    "Lab procedures": ["Lab Procedure", "Solutions"],
    "Appendices": ["Appendix", "Website", "Other"],
}

# --------------------------------------------------------------------------
# Colors
# --------------------------------------------------------------------------
TOPIC_STATUS_COLORS = {
    "Not Started": "#6b7280",
    "Copied": "#3b82f6",
    "Learning": "#f59e0b",
    "Needs Focus": "#ef4444",
    "Mastered": "#22c55e",
}
# Topic-progress weight per status (see services/progress.py topic_progress()).
TOPIC_STATUS_VALUE = {
    "Not Started": 0.0,
    "Copied": 0.25,
    "Learning": 0.5,
    "Needs Focus": 0.5,
    "Mastered": 1.0,
}

URGENCY_OVERDUE = "#ef4444"
URGENCY_SOON = "#f59e0b"      # < 48h
URGENCY_UPCOMING = "#eab308"  # < 7 days
URGENCY_NORMAL = "#6b7280"

STATUS_BADGE_COLORS = {
    "Not Started": "#6b7280", "In Progress": "#3b82f6", "Submitted": "#f59e0b",
    "Graded": "#22c55e", "Planned": "#6b7280", "Done": "#22c55e",
    "Slipped": "#ef4444", "Active": "#3b82f6", "Achieved": "#22c55e",
    "Missed": "#ef4444",
}

FLAG_BADGE_COLOR = "#f59e0b"
DEFAULT_COURSE_COLOR = "#3b82f6"

# Default palette handed out to new courses (cycled by index).
COURSE_COLOR_PALETTE = [
    "#2E86DE", "#E17055", "#00B894", "#6C5CE7", "#FDCB6E", "#D63031",
    "#0984E3", "#00CEC9",
]

# --------------------------------------------------------------------------
# Spaced repetition (Leitner)
# --------------------------------------------------------------------------
LEITNER_INTERVALS_DAYS = [1, 2, 4, 7, 15]  # index 0 unused; box 1..5 -> index
LEITNER_MIN_BOX = 1
LEITNER_MAX_BOX = 5

# --------------------------------------------------------------------------
# Focus score tuning (services/progress.py)
# --------------------------------------------------------------------------
FOCUS_EXAM_WINDOW_HARD_DAYS = 21
FOCUS_EXAM_WINDOW_SOFT_DAYS = 35
FOCUS_EXAM_FACTOR_HARD = 2.0
FOCUS_EXAM_FACTOR_SOFT = 1.5
FOCUS_EXAM_FACTOR_NONE = 1.0
FOCUS_STALENESS_CAP_DAYS = 30
FOCUS_MASTERED_GRACE_DAYS = 7

# --------------------------------------------------------------------------
# Settings sheet keys + fallback defaults (used when the key is missing).
# --------------------------------------------------------------------------
SETTINGS_DEFAULTS = {
    "semester_start": "2026-09-01",
    "semester_end": "2026-12-16",
    "week1_monday": "2026-08-31",
    "exam_week_start": "2026-12-14",
    "week_start_day": "Monday",
    "theme": "dark",
    "pdf_opener_cmd": "",
    "notifications": "TRUE",
    "default_study_block_min": "50",
    "last_summary_week": "",
    "workbook_version": "1",
    "coursepack_root": "coursepacks",
    "student_lab_section": "X02",
    "institution": "NAIT - Instrumentation Engineering Technology",
}

# --------------------------------------------------------------------------
# Misc thresholds
# --------------------------------------------------------------------------
NAIT_PASS_THRESHOLD_PCT = 50.0
LAB_MARK_CAP_ON_FAIL_PCT = 45.0
UPCOMING_EXAM_WINDOW_DAYS = 30
DEADLINE_WINDOW_DAYS = 7
STREAK_MIN_MINUTES = 15
DEFAULT_ESTIMATED_HOURS = 1.5
DEFAULT_POMODORO_STUDY_MIN = 25
DEFAULT_POMODORO_BREAK_MIN = 5

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
