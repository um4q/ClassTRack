# Study Tracker

A desktop study/grade/schedule tracker for one NAIT Instrumentation Engineering
Technology student, Fall 2026 term (Sep 1 - Dec 16, 2026), lecture section A01
and lab section X02, across all six courses (CMTC2341, CNTR2371, INST2310,
INST2340, INST2361, INST2380).

Everything lives in **one Excel workbook**, `data/study_tracker.xlsx` - it is
the source of truth for courses, grade weights, syllabus topics, exam dates,
X02 lab sessions and their pre-lab checklists, the weekly timetable, holidays,
and known syllabus conflicts. The app reads and writes that workbook directly
(via openpyxl), so you can also bulk-edit it in Excel at any time and either
relaunch or press `Ctrl+R` to pick up your changes.

## Running it

1. Make sure the supplied `data/study_tracker.xlsx` is in place (it already is
   if you cloned/unzipped this project as-is). **Never delete it** unless you
   want a blank workbook - see "Empty workbook" below.
2. Double-click **`start.bat`**. On first run it creates a `.venv` virtual
   environment and installs `requirements.txt`; after that it starts in a few
   seconds. Requires Python 3.11+ on PATH (get it from
   https://www.python.org/downloads/, tick "Add python.exe to PATH").
3. Drop your `Semester1.zip` export (or the individual course zips) into
   `data/coursepacks/`. Every launch, Study Tracker extracts any zip that
   doesn't have a matching folder yet - nothing is ever deleted, so it's safe
   to leave the zips there. You can also trigger this manually from
   **Settings > Extract coursepack zips now**.

## Editing in Excel

Close Study Tracker (or just don't have it running) before editing the
workbook directly in Excel, to avoid the two of you fighting over the file.
While Study Tracker is running and you edit+save the workbook in Excel, press
`Ctrl+R` (or Settings > Reload from Excel) to pick up the change. Conversely,
if you have the workbook open in Excel while Study Tracker tries to autosave,
you'll see a yellow "Workbook is open in Excel" banner - close it in Excel and
Study Tracker will save automatically within ~10 seconds; no edits are lost.

Every sheet's dropdown columns (status, type, day-of-week, ...) already have
Excel data-validation lists - keep using them if you edit rows by hand.

## What the ⚠ badges mean

- **On a Grade Weights row** (Course Detail > Grades): this category's weight
  is a **placeholder**, not a real number from a syllabus - INST2361 (no
  syllabus was in the export) and INST2380's theory weight (no theory
  syllabus was in the export) are examples. Replace it once you know the real
  number.
- **On an Assessment** (exam/midterm/final/practical/project): its `notes`
  contain `CONFLICT`, `INFERRED`, or `TBC` - two source documents disagreed on
  the date, the date/lab order was inferred rather than stated outright, or
  the exact day is still To Be Confirmed. Hover the badge to read the note,
  and confirm the real date on Brightspace when you can.
- **On the dashboard**: a running count of unresolved rows in the
  `SyllabusFlags` sheet (syllabus vs. weekly-schedule vs. portal
  disagreements found while building this workbook). Tick a flag's
  "Resolved" checkbox once you've confirmed the real answer.

## Backups

Before every save, the previous workbook is copied to
`data/backups/study_tracker_YYYYMMDD_HHMMSS.xlsx` (the newest 20 are kept).
If something ever goes wrong, your last 20 saves are sitting right there.

## Empty workbook

If `data/study_tracker.xlsx` is missing when Study Tracker starts, it creates
an empty workbook with the same sheets/headers (and a README sheet) so the
app still runs - but with no semester data. There is no "sample data" to
explore; restore the supplied workbook for your real Fall 2026 data.

## Project layout

```
study_tracker/
├─ start.bat, requirements.txt, main.py
├─ app/
│  ├─ config.py, models.py, excel_store.py
│  ├─ services/        # pure calculation logic (grades, progress, scheduler, ...)
│  └─ ui/               # PySide6 pages/widgets
├─ data/
│  ├─ study_tracker.xlsx   # the workbook (supplied)
│  ├─ app.log, backups/
│  └─ coursepacks/         # drop Semester1.zip / course zips here
└─ assets/icon.png
```

## Shortcuts

`Ctrl+S` save now · `Ctrl+N` new note · `Ctrl+1`..`Ctrl+9` jump to sidebar page
· `Ctrl+R` reload from Excel · `Ctrl+T` start/stop the study timer.
