"""
Dataclasses for every workbook row.

These are pure data holders - no Qt, no openpyxl. Every field already holds a
native Python type (``int``, ``float``, ``bool``, ``date``, ``time``, a list,
or ``str``); ``excel_store.py`` is solely responsible for converting to/from
the raw cell values described in the build spec (dates ``YYYY-MM-DD``, times
``HH:MM``, booleans ``TRUE``/``FALSE``, lists comma-separated).

Field-encoding contract used by ``excel_store``'s generic (de)serializer:
    - Plain ``int`` / ``float`` / ``str`` / ``bool`` / ``date`` / ``time``
      fields are converted directly (bool accepts real bool cells as well as
      "TRUE"/"FALSE"/"1"/"0"/"yes"/"no" text; dates/times accept both native
      Excel date/time cells and ISO strings, since a student may hand-edit in
      Excel and have it auto-format the cell).
    - ``Optional[X]`` fields convert an empty cell to ``None``.
    - ``list[str]`` / ``list[int]`` / ``list[date]`` fields are split on the
      field's delimiter (default ``,``; ``metadata={"delim": "|"}`` for
      pipe-separated fields such as ``options``) and each element converted
      to the list's inner type.
    - ``list[ChecklistItem]`` fields use the special ``[ ] item|[x] item``
      encoding (see ``ChecklistItem`` below); always pipe-delimited.
    - Every "id" field (``course_id``, ``topic_id``, ...) is a plain ``int``;
      ``0`` on a not-yet-saved instance means "assign a new id on add_row".

Sheets are listed in the same order as ``config.SHEET_HEADERS``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import List, Optional


def _list_field(delim: str = ",") -> "dataclasses.Field":
    return field(default_factory=list, metadata={"delim": delim})


# --------------------------------------------------------------------------
# Small value objects
# --------------------------------------------------------------------------
@dataclass
class ChecklistItem:
    """One line of a PreLabs.checklist, e.g. '[x] Bring memory stick'."""
    text: str = ""
    checked: bool = False


# --------------------------------------------------------------------------
# Courses
# --------------------------------------------------------------------------
@dataclass
class Course:
    course_id: int = 0
    code: str = ""
    name: str = ""
    term: str = ""
    instructor: str = ""
    email: str = ""
    office: str = ""
    lecture_section: str = ""
    lab_section: str = ""
    target_grade: Optional[float] = None
    color_hex: str = "#3b82f6"
    syllabus_path: str = ""
    theory_coursepack: str = ""
    lab_coursepack: str = ""
    min_lab_completion_pct: Optional[float] = None
    notes: str = ""
    active: bool = True


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------
@dataclass
class Material:
    material_id: int = 0
    course_id: int = 0
    label: str = ""
    kind: str = "Other"
    path: str = ""
    sort_order: int = 0


# --------------------------------------------------------------------------
# GradeWeights
# --------------------------------------------------------------------------
@dataclass
class GradeWeight:
    weight_id: int = 0
    course_id: int = 0
    component: str = "Theory"          # "Theory" | "Lab"
    category: str = ""
    weight_pct: float = 0.0
    drop_lowest: int = 0
    notes: str = ""

    @property
    def is_placeholder(self) -> bool:
        return "PLACEHOLDER" in (self.category or "").upper()


# --------------------------------------------------------------------------
# Topics
# --------------------------------------------------------------------------
@dataclass
class Topic:
    topic_id: int = 0
    course_id: int = 0
    unit: str = ""
    section: str = ""
    title: str = ""
    status: str = "Not Started"
    confidence: int = 3
    priority: int = 3
    link: str = ""
    page: Optional[int] = None
    estimated_hours: float = 1.5
    last_reviewed: Optional[date] = None
    next_review: Optional[date] = None
    notes: str = ""


# --------------------------------------------------------------------------
# Assessments
# --------------------------------------------------------------------------
@dataclass
class Assessment:
    assessment_id: int = 0
    course_id: int = 0
    type: str = "Assignment"
    title: str = ""
    category: str = ""
    due_date: Optional[date] = None
    due_time: Optional[time] = None
    location: str = ""
    status: str = "Not Started"
    score: Optional[float] = None
    max_score: Optional[float] = 100.0
    weight_override: Optional[float] = None
    topic_ids: List[int] = _list_field()
    estimated_hours: float = 0.0
    notes: str = ""

    @property
    def is_flagged(self) -> bool:
        n = (self.notes or "").upper()
        return "CONFLICT" in n or "INFERRED" in n or "TBC" in n


# --------------------------------------------------------------------------
# PreLabs
# --------------------------------------------------------------------------
@dataclass
class PreLab:
    prelab_id: int = 0
    course_id: int = 0
    lab_number: str = ""
    title: str = ""
    lab_type: str = "Common"  # Common|Rotational|Weekly|Assessment|Practice|Project|None
    lab_date: Optional[date] = None
    lab_time: Optional[time] = None
    room: str = ""
    prelab_due: Optional[date] = None
    checklist: List[ChecklistItem] = field(default_factory=list, metadata={"delim": "|"})
    link: str = ""
    completed: bool = False
    completed_on: Optional[date] = None
    notes: str = ""

    @property
    def checklist_progress(self) -> tuple[int, int]:
        done = sum(1 for i in self.checklist if i.checked)
        return done, len(self.checklist)


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
@dataclass
class ScheduleRow:
    schedule_id: int = 0
    course_id: int = 0
    kind: str = "Lecture"  # Lecture|Lab|Tutorial|Seminar|Study Block
    section: str = ""
    day_of_week: str = "Monday"
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    room: str = ""
    dates: List[date] = _list_field()  # non-empty => occurs ONLY on these dates


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------
@dataclass
class Holiday:
    date: Optional[date] = None
    name: str = ""
    classes_cancelled: bool = True


# --------------------------------------------------------------------------
# SyllabusFlags
# --------------------------------------------------------------------------
@dataclass
class SyllabusFlag:
    flag_id: int = 0
    course_id: int = 0
    item: str = ""
    issue: str = ""
    source: str = ""
    resolved: bool = False


# --------------------------------------------------------------------------
# StudyLog
# --------------------------------------------------------------------------
@dataclass
class StudyLogEntry:
    log_id: int = 0
    course_id: int = 0
    topic_id: Optional[int] = None
    date: Optional[date] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    minutes: int = 0
    activity: str = "Reading"
    notes: str = ""


# --------------------------------------------------------------------------
# Attendance
# --------------------------------------------------------------------------
@dataclass
class AttendanceEntry:
    attendance_id: int = 0
    course_id: int = 0
    date: Optional[date] = None
    status: str = "Present"


# --------------------------------------------------------------------------
# Goals
# --------------------------------------------------------------------------
@dataclass
class Goal:
    goal_id: int = 0
    scope: str = "Weekly"  # Semester|Weekly|Course
    course_id: Optional[int] = None
    description: str = ""
    metric: str = "study_hours_per_week"
    target_value: float = 0.0
    current_value: Optional[float] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    status: str = "Active"


# --------------------------------------------------------------------------
# PracticeQuestions
# --------------------------------------------------------------------------
@dataclass
class PracticeQuestion:
    question_id: int = 0
    course_id: int = 0
    topic_id: Optional[int] = None
    question_type: str = "Short Answer"  # MCQ|Short Answer|Numeric|True/False
    question_text: str = ""
    options: List[str] = field(default_factory=list, metadata={"delim": "|"})
    answer_text: str = ""
    explanation: str = ""
    difficulty: int = 1
    tags: List[str] = _list_field()
    box: int = 1
    times_attempted: int = 0
    times_correct: int = 0
    last_attempted: Optional[date] = None
    next_due: Optional[date] = None
    image_path: str = ""


# --------------------------------------------------------------------------
# MockExams
# --------------------------------------------------------------------------
@dataclass
class MockExam:
    exam_id: int = 0
    course_id: int = 0
    name: str = ""
    date_taken: Optional[date] = None
    time_limit_min: int = 30
    question_ids: List[int] = _list_field()
    score: Optional[float] = None
    max_score: Optional[float] = None
    results_json: str = ""
    duration_min: Optional[float] = None


# --------------------------------------------------------------------------
# Roadmap
# --------------------------------------------------------------------------
@dataclass
class RoadmapItem:
    roadmap_id: int = 0
    course_id: int = 0
    sort_order: int = 0
    milestone: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    topic_ids: List[int] = _list_field()
    status: str = "Planned"  # Planned|In Progress|Done|Slipped
    auto_generated: bool = False
    notes: str = ""


# --------------------------------------------------------------------------
# BrainDump
# --------------------------------------------------------------------------
@dataclass
class BrainDumpNote:
    note_id: int = 0
    date: Optional[date] = None
    course_id: Optional[int] = None
    title: str = ""
    body_html: str = ""
    tags: List[str] = _list_field()
    created_at: str = ""
    updated_at: str = ""


# --------------------------------------------------------------------------
# Settings (key/value sheet - not a CRUD list; see ExcelStore.get_setting)
# --------------------------------------------------------------------------
@dataclass
class SettingRow:
    key: str = ""
    value: str = ""


# Sheet name -> dataclass, used by excel_store's generic engine and by
# services/ui for type hints. Keep in sync with config.CRUD_SHEETS.
SHEET_MODELS = {
    "Courses": Course,
    "Materials": Material,
    "GradeWeights": GradeWeight,
    "Topics": Topic,
    "Assessments": Assessment,
    "PreLabs": PreLab,
    "Schedule": ScheduleRow,
    "Holidays": Holiday,
    "SyllabusFlags": SyllabusFlag,
    "StudyLog": StudyLogEntry,
    "Attendance": AttendanceEntry,
    "Goals": Goal,
    "PracticeQuestions": PracticeQuestion,
    "MockExams": MockExam,
    "Roadmap": RoadmapItem,
    "BrainDump": BrainDumpNote,
}

# Primary-key field name per sheet (first column, int, 0 = unsaved).
SHEET_ID_FIELD = {
    "Courses": "course_id",
    "Materials": "material_id",
    "GradeWeights": "weight_id",
    "Topics": "topic_id",
    "Assessments": "assessment_id",
    "PreLabs": "prelab_id",
    "Schedule": "schedule_id",
    "Holidays": None,          # no synthetic id; keyed by date+name
    "SyllabusFlags": "flag_id",
    "StudyLog": "log_id",
    "Attendance": "attendance_id",
    "Goals": "goal_id",
    "PracticeQuestions": "question_id",
    "MockExams": "exam_id",
    "Roadmap": "roadmap_id",
    "BrainDump": "note_id",
}
