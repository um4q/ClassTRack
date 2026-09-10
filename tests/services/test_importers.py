"""
Tests for app.services.importers - pure functions, no Qt, no store.

Covers CSV import/export round-tripping, pasted Q:/A:/E: block parsing, and
the best-effort syllabus PDF date scan (§5.6, §5.11).
"""
from __future__ import annotations

import pytest

from app.models import PracticeQuestion
from app.services.importers import (
    export_practice_csv,
    parse_practice_csv,
    parse_practice_pasted,
    scan_syllabus_pdf_dates,
)


# --------------------------------------------------------------------------
# parse_practice_csv
# --------------------------------------------------------------------------
def test_parse_practice_csv_row_with_options_becomes_mcq():
    csv_text = (
        "question,options,answer,explanation,topic,difficulty,tags\n"
        "What is 2+2?,2|3|4|5,4,Basic arithmetic,3,2,\"math,easy\"\n"
    )
    questions = parse_practice_csv(csv_text, course_id=7)
    assert len(questions) == 1
    q = questions[0]
    assert q.question_type == "MCQ"
    assert q.question_text == "What is 2+2?"
    assert q.options == ["2", "3", "4", "5"]
    assert q.answer_text == "4"
    assert q.explanation == "Basic arithmetic"
    assert q.topic_id == 3
    assert q.difficulty == 2
    assert q.tags == ["math", "easy"]
    assert q.course_id == 7
    # New-question defaults per module contract.
    assert q.question_id == 0
    assert q.box == 1
    assert q.times_attempted == 0
    assert q.times_correct == 0


def test_parse_practice_csv_row_without_options_becomes_short_answer():
    csv_text = (
        "question,options,answer,explanation,topic,difficulty,tags\n"
        "Define entropy.,,A measure of disorder,,,,\n"
    )
    questions = parse_practice_csv(csv_text, course_id=1)
    assert len(questions) == 1
    q = questions[0]
    assert q.question_type == "Short Answer"
    assert q.options == []
    assert q.answer_text == "A measure of disorder"
    assert q.topic_id is None


def test_parse_practice_csv_skips_row_missing_question_text():
    csv_text = (
        "question,options,answer,explanation,topic,difficulty,tags\n"
        ",,some answer,,,,\n"
        "Real question,,Real answer,,,,\n"
    )
    questions = parse_practice_csv(csv_text, course_id=1)
    # Malformed row (blank question) is skipped, not raised; the valid row
    # after it still comes through.
    assert len(questions) == 1
    assert questions[0].question_text == "Real question"


def test_parse_practice_csv_difficulty_out_of_range_falls_back_to_default():
    csv_text = (
        "question,options,answer,explanation,topic,difficulty,tags\n"
        "Too high,,ans,,,9,\n"
        "Too low,,ans,,,0,\n"
        "Not a number,,ans,,,banana,\n"
    )
    questions = parse_practice_csv(csv_text, course_id=1)
    assert len(questions) == 3
    assert [q.difficulty for q in questions] == [1, 1, 1]


def test_parse_practice_csv_empty_text_returns_empty_list():
    assert parse_practice_csv("", course_id=1) == []
    assert parse_practice_csv("   \n  ", course_id=1) == []


# --------------------------------------------------------------------------
# parse_practice_pasted
# --------------------------------------------------------------------------
def test_parse_practice_pasted_multiple_blocks_separated_by_blank_lines():
    text = (
        "Q: What is the capital of France?\n"
        "A: Paris\n"
        "E: It's on the Seine.\n"
        "\n"
        "Q: What is 7*6?\n"
        "A: 42\n"
    )
    questions = parse_practice_pasted(text, course_id=4)
    assert len(questions) == 2

    q1, q2 = questions
    assert q1.question_text == "What is the capital of France?"
    assert q1.answer_text == "Paris"
    assert q1.explanation == "It's on the Seine."
    assert q1.course_id == 4
    assert q1.question_type == "Short Answer"

    assert q2.question_text == "What is 7*6?"
    assert q2.answer_text == "42"
    assert q2.explanation == ""


def test_parse_practice_pasted_block_missing_answer_is_skipped():
    text = (
        "Q: This block has no answer.\n"
        "\n"
        "Q: This one does.\n"
        "A: Yes it does.\n"
    )
    questions = parse_practice_pasted(text, course_id=1)
    assert len(questions) == 1
    assert questions[0].question_text == "This one does."
    assert questions[0].answer_text == "Yes it does."


def test_parse_practice_pasted_multiline_question_body_captured_fully():
    text = (
        "Q: This is a long question that\n"
        "wraps across several lines\n"
        "before the answer begins.\n"
        "A: The final answer.\n"
    )
    questions = parse_practice_pasted(text, course_id=1)
    assert len(questions) == 1
    assert questions[0].question_text == (
        "This is a long question that wraps across several lines "
        "before the answer begins."
    )
    assert questions[0].answer_text == "The final answer."


def test_parse_practice_pasted_empty_text_returns_empty_list():
    assert parse_practice_pasted("", course_id=1) == []
    assert parse_practice_pasted("   \n\n  ", course_id=1) == []


# --------------------------------------------------------------------------
# export_practice_csv (round-trip with parse_practice_csv)
# --------------------------------------------------------------------------
def test_export_practice_csv_round_trips_through_parse_practice_csv():
    questions_in = [
        PracticeQuestion(
            question_id=5,
            course_id=2,
            topic_id=9,
            question_type="MCQ",
            question_text="Pick a prime.",
            options=["2", "4", "6"],
            answer_text="2",
            explanation="2 is the only even prime.",
            difficulty=3,
            tags=["math", "primes"],
            box=2,
            times_attempted=4,
            times_correct=3,
        ),
        PracticeQuestion(
            question_id=6,
            course_id=2,
            topic_id=None,
            question_type="Short Answer",
            question_text="Name a noble gas.",
            options=[],
            answer_text="Argon",
            explanation="",
            difficulty=1,
            tags=[],
        ),
    ]

    csv_text = export_practice_csv(questions_in)
    reparsed = parse_practice_csv(csv_text, course_id=2)

    assert len(reparsed) == 2

    r0, r1 = reparsed
    assert r0.question_text == "Pick a prime."
    assert r0.question_type == "MCQ"
    assert r0.options == ["2", "4", "6"]
    assert r0.answer_text == "2"
    assert r0.explanation == "2 is the only even prime."
    assert r0.topic_id == 9
    assert r0.difficulty == 3
    assert r0.tags == ["math", "primes"]

    assert r1.question_text == "Name a noble gas."
    assert r1.question_type == "Short Answer"
    assert r1.options == []
    assert r1.answer_text == "Argon"
    assert r1.topic_id is None
    assert r1.difficulty == 1
    assert r1.tags == []


def test_export_practice_csv_empty_list_produces_header_only():
    csv_text = export_practice_csv([])
    lines = csv_text.strip("\n").splitlines()
    assert lines == ["question,options,answer,explanation,topic,difficulty,tags"]
    assert parse_practice_csv(csv_text, course_id=1) == []


# --------------------------------------------------------------------------
# scan_syllabus_pdf_dates
# --------------------------------------------------------------------------
def _pypdf_available() -> bool:
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


def test_scan_syllabus_pdf_dates_missing_file_returns_empty_list(tmp_path):
    # Applies regardless of whether pypdf is installed - a nonexistent path
    # must degrade gracefully either way.
    missing = tmp_path / "does_not_exist.pdf"
    assert scan_syllabus_pdf_dates(missing) == []


def test_scan_syllabus_pdf_dates_without_pypdf_returns_empty_list(tmp_path):
    if _pypdf_available():
        pytest.skip("pypdf is installed; graceful-degradation path not exercised here")
    # Even a path that doesn't exist would return [], so create a
    # placeholder file to prove it's the missing-dependency path (not the
    # missing-file path) that's responsible when pypdf is absent.
    fake_pdf = tmp_path / "syllabus.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    assert scan_syllabus_pdf_dates(fake_pdf) == []


def test_scan_syllabus_pdf_dates_finds_dated_assessment_line(tmp_path):
    if not _pypdf_available():
        pytest.skip("pypdf not installed")

    import pypdf

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    # pypdf's PageObject doesn't offer a text-drawing helper, so build a
    # minimal content stream by hand that draws one line of text using a
    # standard font, and merge it onto the blank page.
    content = (
        b"BT /F1 12 Tf 72 700 Td "
        b"(Midterm - October 6, 2026) Tj "
        b"ET"
    )
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        StreamObject,
    )

    content_obj = StreamObject()
    content_obj.set_data(content)
    content_ref = writer._add_object(content_obj)

    font_obj = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_obj)

    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })

    page[NameObject("/Contents")] = content_ref
    page[NameObject("/Resources")] = resources

    pdf_path = tmp_path / "syllabus.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)

    results = scan_syllabus_pdf_dates(pdf_path)

    assert len(results) == 1
    entry = results[0]
    assert entry["date"] == "2026-10-06"
    assert entry["page"] == 1
    assert "Midterm" in entry["title"]
    assert "October 6, 2026" in entry["raw_line"]


def test_scan_syllabus_pdf_dates_line_without_keyword_is_ignored(tmp_path):
    if not _pypdf_available():
        pytest.skip("pypdf not installed")

    import pypdf
    from pypdf.generic import (
        DictionaryObject,
        NameObject,
        StreamObject,
    )

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    content = (
        b"BT /F1 12 Tf 72 700 Td "
        b"(Office hours - October 6, 2026) Tj "
        b"ET"
    )
    content_obj = StreamObject()
    content_obj.set_data(content)
    content_ref = writer._add_object(content_obj)

    font_obj = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = writer._add_object(font_obj)

    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
    })

    page[NameObject("/Contents")] = content_ref
    page[NameObject("/Resources")] = resources

    pdf_path = tmp_path / "syllabus_no_keyword.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)

    # "Office hours" contains none of the exam/assessment keywords, so the
    # line should not be picked up even though it has a valid-looking date.
    assert scan_syllabus_pdf_dates(pdf_path) == []
