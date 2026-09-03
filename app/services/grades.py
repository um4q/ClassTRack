"""
Weighted grades, component grades, the NAIT pass rule, what-if, and trend.

See build spec §6.1-6.4. Pure functions - callers fetch rows via ExcelStore
and pass them in; nothing here touches the workbook.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Optional

from app import config
from app.models import Assessment, Course, GradeWeight, PreLab


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------
@dataclass
class GradeResult:
    current: Optional[float]     # None if nothing graded yet, else 0-100
    earned_weight: float         # sum of w_i * score_i/max_i, in weight-points
    graded_weight: float         # sum of w_i over graded items, in weight-points
    total_weight: float          # sum of every category's weight_pct considered
    projected: Optional[float]   # current grade extrapolated over remaining weight


@dataclass
class NaitPassResult:
    theory: GradeResult
    lab: GradeResult
    theory_pass: bool
    lab_pass: bool
    labs_done: int
    labs_due: int                 # denominator to date
    lab_completion_pct: Optional[float]
    labs_total_term: int          # whole-term denominator
    lab_completion_pass: bool
    overall_pass: bool
    capped_grade: Optional[float]  # lowest component mark (lab capped if completion failed) if not overall_pass


@dataclass
class WhatIfResult:
    needed_avg: Optional[float]  # None if there is no remaining weight to earn
    achieved: bool               # target already met/guaranteed
    impossible: bool             # needed_avg > 100


# --------------------------------------------------------------------------
# Item / category weighting (§6.1)
# --------------------------------------------------------------------------
def item_weight(a: Assessment, category_weight_pct: float, survivors: int) -> float:
    """weight_override if set, else the category's weight_pct split evenly
    across ``survivors`` (assessment rows in that category, minus any
    dropped via drop_lowest)."""
    if a.weight_override is not None:
        return a.weight_override
    return category_weight_pct / survivors if survivors > 0 else 0.0


def compute_grade(
    assessments: list[Assessment],
    weights: list[GradeWeight],
    component: Optional[str] = None,
) -> GradeResult:
    """§6.2. ``assessments``/``weights`` should already be filtered to one
    course. Pass component="Theory"/"Lab" for a component-only grade."""
    ws = [w for w in weights if component is None or w.component == component]
    total_weight = sum(w.weight_pct for w in ws)
    earned = 0.0
    graded_weight = 0.0
    for w in ws:
        cat_items = [a for a in assessments if a.category == w.category]
        if not cat_items:
            continue
        graded_items = [a for a in cat_items if a.score is not None and a.max_score]
        drop_n = max(0, w.drop_lowest)
        kept = graded_items
        if drop_n and len(graded_items) > drop_n:
            ranked = sorted(graded_items, key=lambda a: a.score / a.max_score)
            dropped_ids = {a.assessment_id for a in ranked[:drop_n]}
            kept = [a for a in graded_items if a.assessment_id not in dropped_ids]
        survivors = max(1, len(cat_items) - drop_n) if drop_n else len(cat_items)
        for a in kept:
            iw = item_weight(a, w.weight_pct, survivors)
            pct = a.score / a.max_score
            earned += iw * pct
            graded_weight += iw
    current = (earned / graded_weight * 100) if graded_weight > 0 else None
    return GradeResult(current, earned, graded_weight, total_weight, current)


def labs_completed_counts(prelabs: list[PreLab], today: date) -> tuple[int, int, int]:
    """(done_to_date, due_to_date, whole_term_total). Excludes lab_type
    None/Practice per §6.2."""
    eligible = [p for p in prelabs if p.lab_type not in ("None", "Practice")]
    due = [p for p in eligible if p.lab_date and p.lab_date <= today]
    done = sum(1 for p in due if p.completed)
    return done, len(due), len(eligible)


def nait_pass_check(
    course: Course,
    assessments: list[Assessment],
    weights: list[GradeWeight],
    prelabs: list[PreLab],
    today: date,
) -> NaitPassResult:
    """Theory >= 50% AND Lab >= 50% AND lab completion% >= min_lab_completion_pct,
    else the course grade is capped at the lowest component mark (lab mark
    capped at 45% if completion failed). See §6.2."""
    theory = compute_grade(assessments, weights, component="Theory")
    lab = compute_grade(assessments, weights, component="Lab")
    theory_pass = theory.current is None or theory.current >= config.NAIT_PASS_THRESHOLD_PCT
    lab_pass = lab.current is None or lab.current >= config.NAIT_PASS_THRESHOLD_PCT

    done, due, total_term = labs_completed_counts(prelabs, today)
    lab_completion_pct = (done / due * 100) if due else None
    min_pct = course.min_lab_completion_pct if course.min_lab_completion_pct is not None else 0.0
    lab_completion_pass = lab_completion_pct is None or lab_completion_pct >= min_pct

    overall_pass = theory_pass and lab_pass and lab_completion_pass
    capped = None
    if not overall_pass:
        lab_mark = lab.current
        if not lab_completion_pass and lab_mark is not None:
            lab_mark = min(lab_mark, config.LAB_MARK_CAP_ON_FAIL_PCT)
        candidates = [c for c in (theory.current, lab_mark) if c is not None]
        capped = min(candidates) if candidates else None

    return NaitPassResult(
        theory, lab, theory_pass, lab_pass, done, due, lab_completion_pct,
        total_term, lab_completion_pass, overall_pass, capped,
    )


# --------------------------------------------------------------------------
# What-if (§6.3)
# --------------------------------------------------------------------------
def what_if(grade: GradeResult, target_pct: float) -> WhatIfResult:
    """Needed average % on every remaining (ungraded) weight-point to reach
    ``target_pct`` overall, given ``grade`` (see compute_grade)."""
    remaining_weight = grade.total_weight - grade.graded_weight
    if remaining_weight <= 0:
        achieved = grade.current is not None and grade.current >= target_pct
        return WhatIfResult(None, achieved, False)
    target_points = target_pct / 100 * grade.total_weight
    needed_avg = (target_points - grade.earned_weight) / remaining_weight * 100
    return WhatIfResult(needed_avg, needed_avg <= 0, needed_avg > 100)


def what_if_single_item(
    grade: GradeResult,
    target_pct: float,
    target_item_weight: float,
    assumed_avg_others_pct: float = 0.0,
) -> WhatIfResult:
    """Needed score % on ONE specific remaining item, assuming every other
    remaining item scores ``assumed_avg_others_pct``."""
    remaining_weight = grade.total_weight - grade.graded_weight
    other_remaining = remaining_weight - target_item_weight
    if target_item_weight <= 0:
        return WhatIfResult(None, False, False)
    target_points = target_pct / 100 * grade.total_weight
    contrib_others = other_remaining * assumed_avg_others_pct / 100
    needed_from_target = target_points - grade.earned_weight - contrib_others
    needed_avg = needed_from_target / target_item_weight * 100
    return WhatIfResult(needed_avg, needed_avg <= 0, needed_avg > 100)


# --------------------------------------------------------------------------
# Trend (§6.4)
# --------------------------------------------------------------------------
def grade_trend(assessments: list[Assessment], weights: list[GradeWeight]) -> list[tuple[date, float]]:
    """(due_date, cumulative current grade) after each graded item, in
    due_date order. Category item counts always use the FULL assessment
    list (so weight-per-item doesn't drift as the trend progresses) -
    only which items count as "graded" changes at each step."""
    graded = sorted(
        (a for a in assessments if a.score is not None and a.max_score and a.due_date),
        key=lambda a: a.due_date,
    )
    trend: list[tuple[date, float]] = []
    for cutoff in graded:
        snapshot = [
            a if (a.due_date and a.due_date <= cutoff.due_date and a.score is not None)
            else dataclasses.replace(a, score=None)
            for a in assessments
        ]
        gr = compute_grade(snapshot, weights)
        if gr.current is not None:
            trend.append((cutoff.due_date, gr.current))
    return trend
