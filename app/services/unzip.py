"""
Coursepack zip auto-extraction (build spec §1 "Coursepacks folder").

On every startup, for each *.zip in data/coursepacks/ with no matching
folder yet, extract it into data/coursepacks/<FOLDER>/, where FOLDER is the
first whitespace-delimited token of the zip's filename, with "_LAB"
appended when the filename ends in "LAB.zip". A Semester1*.zip bundle (course
zips + loose files) is extracted directly into data/coursepacks/ first, so
its contents land where the per-course rule above can then see them.
Nothing is ever deleted - already-extracted zips are simply skipped.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger("study_tracker")


def _folder_name_for_zip(zip_name: str) -> str:
    stem = Path(zip_name).stem
    tokens = stem.split()
    first_token = tokens[0] if tokens else stem
    if zip_name.lower().endswith("lab.zip"):
        return f"{first_token}_LAB"
    return first_token


def extract_coursepacks(coursepacks_dir: Path) -> list[str]:
    """Returns a human-readable log of what was extracted/skipped/failed."""
    coursepacks_dir = Path(coursepacks_dir)
    coursepacks_dir.mkdir(parents=True, exist_ok=True)
    log: list[str] = []

    # A top-level Semester*.zip bundle: extract straight into coursepacks_dir
    # so the course zips + loose files it contains land there.
    for z in sorted(coursepacks_dir.glob("*.zip")):
        if not z.stem.lower().startswith("semester"):
            continue
        try:
            with zipfile.ZipFile(z) as zf:
                zf.extractall(coursepacks_dir)
            log.append(f"Extracted bundle {z.name} into {coursepacks_dir.name}/")
        except (zipfile.BadZipFile, OSError) as e:
            logger.exception("Failed to extract bundle %s", z)
            log.append(f"FAILED to extract {z.name}: {e}")

    # Every remaining zip becomes its own course folder, unless that folder
    # already exists (already extracted on a previous run).
    for z in sorted(coursepacks_dir.glob("*.zip")):
        if z.stem.lower().startswith("semester"):
            continue
        folder = _folder_name_for_zip(z.name)
        dest = coursepacks_dir / folder
        if dest.exists():
            log.append(f"Skipped {z.name} (already extracted to {folder}/)")
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
            log.append(f"Extracted {z.name} -> {folder}/")
        except (zipfile.BadZipFile, OSError) as e:
            logger.exception("Failed to extract %s", z)
            log.append(f"FAILED to extract {z.name}: {e}")

    return log
