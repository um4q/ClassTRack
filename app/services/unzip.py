"""
Coursepack zip auto-extraction (build spec §1 "Coursepacks folder").

On every startup, for each *.zip in data/coursepacks/ with no matching
folder yet, extract it into data/coursepacks/<FOLDER>/, where FOLDER is the
first whitespace-delimited token of the zip's filename, with "_LAB"
appended when the filename ends in "LAB.zip". A bundle zip (course zips +
loose files all wrapped in one export, e.g. "Semester1.zip") is detected by
its CONTENTS - any zip that itself contains a nested *.zip entry is
extracted directly into data/coursepacks/ first, so the per-course rule
above can then see what it contained; this isn't limited to a file literally
named "Semester*.zip", since different terms/exports name the bundle
differently. Nothing is ever deleted - already-extracted zips are tracked
per destination folder (a small ``.extracted_zips.txt`` marker) and
skipped, and if two differently-named zips both map to the same course
folder (e.g. a re-export with a slightly different filename), both get
extracted into that folder rather than only whichever sorts first.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

logger = logging.getLogger("study_tracker")

_MARKER_NAME = ".extracted_zips.txt"
_MAX_BUNDLE_PASSES = 3  # a bundle nested inside a bundle, at most this deep


def _folder_name_for_zip(zip_name: str) -> str:
    stem = Path(zip_name).stem
    tokens = stem.split()
    first_token = tokens[0] if tokens else stem
    if zip_name.lower().endswith("lab.zip"):
        return f"{first_token}_LAB"
    return first_token


def _is_bundle(z: Path) -> bool:
    """A bundle wraps other zips (course zips + loose files) rather than
    being one course's own material - detected by content, not by name,
    since exports from different terms/sources name the bundle differently."""
    try:
        with zipfile.ZipFile(z) as zf:
            return any(name.lower().endswith(".zip") for name in zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _read_marker(dest: Path) -> set[str]:
    marker = dest / _MARKER_NAME
    if not marker.exists():
        return set()
    try:
        return {line.strip() for line in marker.read_text(encoding="utf-8").splitlines() if line.strip()}
    except OSError:
        return set()


def _append_marker(dest: Path, entry_name: str) -> None:
    try:
        with (dest / _MARKER_NAME).open("a", encoding="utf-8") as f:
            f.write(entry_name + "\n")
    except OSError:
        logger.exception("Could not update extraction marker in %s", dest)


def extract_coursepacks(coursepacks_dir: Path) -> list[str]:
    """Returns a human-readable log of what was extracted/skipped/failed."""
    coursepacks_dir = Path(coursepacks_dir)
    coursepacks_dir.mkdir(parents=True, exist_ok=True)
    log: list[str] = []
    bundles_done = _read_marker(coursepacks_dir)

    # Flatten every bundle zip first - repeat a few passes since a bundle can
    # reveal a zip that is itself another bundle.
    for _pass in range(_MAX_BUNDLE_PASSES):
        found_new_bundle = False
        for z in sorted(coursepacks_dir.glob("*.zip")):
            if z.name in bundles_done or not _is_bundle(z):
                continue
            found_new_bundle = True
            try:
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(coursepacks_dir)
                log.append(f"Extracted bundle {z.name} into {coursepacks_dir.name}/")
                bundles_done.add(z.name)
                _append_marker(coursepacks_dir, z.name)
            except (zipfile.BadZipFile, OSError) as e:
                logger.exception("Failed to extract bundle %s", z)
                log.append(f"FAILED to extract {z.name}: {e}")
                bundles_done.add(z.name)  # don't retry a corrupt file forever
                _append_marker(coursepacks_dir, z.name)
        if not found_new_bundle:
            break

    # Every remaining (non-bundle) zip becomes/merges into its course folder.
    for z in sorted(coursepacks_dir.glob("*.zip")):
        if z.name in bundles_done:
            continue
        folder = _folder_name_for_zip(z.name)
        dest = coursepacks_dir / folder
        already = _read_marker(dest)
        if z.name in already:
            log.append(f"Skipped {z.name} (already extracted to {folder}/)")
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest)
            _append_marker(dest, z.name)
            verb = "Merged" if already else "Extracted"
            log.append(f"{verb} {z.name} -> {folder}/")
        except (zipfile.BadZipFile, OSError) as e:
            logger.exception("Failed to extract %s", z)
            log.append(f"FAILED to extract {z.name}: {e}")

    return log
