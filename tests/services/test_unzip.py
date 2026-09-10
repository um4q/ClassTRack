"""
Tests for app.services.unzip - pure Python zip-extraction logic (no Qt, no
store; see app/services/unzip.py's module docstring for the full spec).

All zip fixtures here are real zip files built with Python's zipfile module
under pytest's own tmp_path - nothing touches data/study_tracker.xlsx.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from app.services.unzip import extract_coursepacks


def _make_zip(path: Path, files: dict[str, bytes]) -> None:
    """Write a real zip at `path` containing `files` (arcname -> content)."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _make_bundle_zip(path: Path, nested_zips: list[Path], loose_files: dict[str, bytes]) -> None:
    """Write a zip whose entries include other already-built zip files plus
    loose files - simulating a whole-semester export bundle."""
    with zipfile.ZipFile(path, "w") as zf:
        for nested in nested_zips:
            zf.write(nested, arcname=nested.name)
        for name, content in loose_files.items():
            zf.writestr(name, content)


def test_extract_coursepacks_bundle_detected_by_content_is_flattened_then_courses_extracted(tmp_path):
    """'Class Resources.zip' doesn't start with 'semester' - it must still be
    recognized as a bundle by its CONTENTS (it contains nested *.zip
    entries), get flattened directly into coursepacks_dir, and then have
    its nested course zips extracted into per-course folders named by the
    first whitespace token of the zip's stem, with _LAB appended when the
    filename ends in LAB.zip."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    coursepacks_dir = tmp_path / "coursepacks"
    coursepacks_dir.mkdir()

    math_zip = build_dir / "Math 101.zip"
    _make_zip(math_zip, {"notes.txt": b"math notes"})

    physics_lab_zip = build_dir / "Physics 201 LAB.zip"
    _make_zip(physics_lab_zip, {"manual.txt": b"physics lab manual"})

    bundle_zip = coursepacks_dir / "Class Resources.zip"
    _make_bundle_zip(
        bundle_zip,
        nested_zips=[math_zip, physics_lab_zip],
        loose_files={"readme.txt": b"welcome to the semester"},
    )

    log = extract_coursepacks(coursepacks_dir)

    # The bundle itself was flattened: its contents now sit directly in
    # coursepacks_dir (the loose file stays put; the nested zips are then
    # picked up as ordinary course zips).
    assert (coursepacks_dir / "Math 101.zip").exists()
    assert (coursepacks_dir / "Physics 201 LAB.zip").exists()
    assert (coursepacks_dir / "readme.txt").read_bytes() == b"welcome to the semester"
    assert "Extracted bundle Class Resources.zip into coursepacks/" in log

    # Nested zips were then extracted into their own course folders using
    # the first-whitespace-token rule ("Math 101" -> "Math"), with _LAB
    # appended for the one whose filename ends in LAB.zip.
    assert (coursepacks_dir / "Math" / "notes.txt").read_bytes() == b"math notes"
    assert (coursepacks_dir / "Physics_LAB" / "manual.txt").read_bytes() == b"physics lab manual"
    assert "Extracted Math 101.zip -> Math/" in log
    assert "Extracted Physics 201 LAB.zip -> Physics_LAB/" in log


def test_extract_coursepacks_second_run_is_idempotent_and_only_skips(tmp_path):
    """Running extraction twice on the same directory must not re-extract
    or blow up - every log line from the second pass should report a
    Skipped course zip (the bundle itself produces no second-pass entry at
    all, since it's tracked as already-handled and silently ignored on
    later passes)."""
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    coursepacks_dir = tmp_path / "coursepacks"
    coursepacks_dir.mkdir()

    math_zip = build_dir / "Math 101.zip"
    _make_zip(math_zip, {"notes.txt": b"math notes"})
    physics_lab_zip = build_dir / "Physics 201 LAB.zip"
    _make_zip(physics_lab_zip, {"manual.txt": b"physics lab manual"})

    bundle_zip = coursepacks_dir / "Class Resources.zip"
    _make_bundle_zip(bundle_zip, [math_zip, physics_lab_zip], {"readme.txt": b"hi"})

    first_log = extract_coursepacks(coursepacks_dir)
    assert first_log, "sanity check: the first run should actually do work"

    second_log = extract_coursepacks(coursepacks_dir)

    assert second_log, "second run should still report the skipped course zips"
    assert all("Skipped" in line for line in second_log)
    assert any("Skipped Math 101.zip (already extracted to Math/)" == line for line in second_log)
    assert any(
        "Skipped Physics 201 LAB.zip (already extracted to Physics_LAB/)" == line
        for line in second_log
    )


def test_extract_coursepacks_two_differently_named_zips_merge_into_same_course_folder(tmp_path):
    """A re-export with a slightly different filename that maps to the same
    course folder must be MERGED alongside the original, not skip/overwrite
    it - both files should end up present in the one destination folder."""
    coursepacks_dir = tmp_path / "coursepacks"
    coursepacks_dir.mkdir()

    first_zip = coursepacks_dir / "Chem 101.zip"
    _make_zip(first_zip, {"syllabus.txt": b"chem syllabus"})

    second_zip = coursepacks_dir / "Chem 102b Reexport.zip"
    _make_zip(second_zip, {"lab_manual.txt": b"chem lab manual"})

    log = extract_coursepacks(coursepacks_dir)

    dest = coursepacks_dir / "Chem"
    assert (dest / "syllabus.txt").read_bytes() == b"chem syllabus"
    assert (dest / "lab_manual.txt").read_bytes() == b"chem lab manual"

    assert "Extracted Chem 101.zip -> Chem/" in log
    assert "Merged Chem 102b Reexport.zip -> Chem/" in log


def test_extract_coursepacks_corrupt_zip_logs_failure_without_raising(tmp_path):
    """An invalid zip file must produce a FAILED log entry for that file
    alone, not raise and abort the whole extraction run."""
    coursepacks_dir = tmp_path / "coursepacks"
    coursepacks_dir.mkdir()

    corrupt_zip = coursepacks_dir / "Broken.zip"
    corrupt_zip.write_bytes(os.urandom(256))  # garbage bytes, not a real zip

    log = extract_coursepacks(coursepacks_dir)  # must return normally, not raise

    assert any(line.startswith("FAILED to extract Broken.zip") for line in log)
