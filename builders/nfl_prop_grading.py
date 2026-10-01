"""Unified NFL player-prop grading shared with the public-site yardage rules.

Yardage markets use the tested Strong / Regular / Lean system. Non-yardage
markets keep the builder's existing A Prop / B Prop / Lean rules.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

YARDAGE_PROP_MARKETS = {"Passing Yards", "Rushing Yards", "Receiving Yards"}
TRACKED_YARDAGE_GRADES = {"Strong", "Regular", "Lean"}
MIN_PROJECTION_GAP_PCT = 30.0
STRONG_EDGE_PCT = 30.0
REGULAR_EDGE_PCT = 16.0
LEAN_EDGE_PCT = 12.0


def _num(value: Any, default: float = math.nan) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _probability_edge_pct(value: Any) -> float:
    edge = _num(value, math.nan)
    if not math.isfinite(edge):
        return math.nan
    return edge * 100.0 if abs(edge) <= 1.0 else edge


def yardage_prop_grade(
    projection: Any,
    market_line: Any,
    probability_edge: Any,
    projection_edge: Any = math.nan,
) -> str:
    """Return the production yardage tier used on both admin and public pages."""
    line = _num(market_line, math.nan)
    if not math.isfinite(line) or line <= 0:
        return "Non-Edge"

    stored_gap = _num(projection_edge, math.nan)
    projected = _num(projection, math.nan)
    gap = abs(stored_gap) if math.isfinite(stored_gap) else (
        abs(projected - line) if math.isfinite(projected) else math.nan
    )
    if not math.isfinite(gap):
        return "Non-Edge"

    gap_pct = gap / abs(line) * 100.0
    edge_pct = _probability_edge_pct(probability_edge)
    if not math.isfinite(edge_pct):
        return "Non-Edge"

    # The 30% projection-gap gate applies to every publishable yardage tier.
    # Reliability stays descriptive and does not rescue or suppress a grade.
    if gap_pct < MIN_PROJECTION_GAP_PCT or edge_pct < LEAN_EDGE_PCT:
        return "Non-Edge"
    if edge_pct >= STRONG_EDGE_PCT:
        return "Strong"
    if edge_pct >= REGULAR_EDGE_PCT:
        return "Regular"
    return "Lean"


def _tracker_key(row: Any) -> tuple[str, str, str, str]:
    getter = row.get if hasattr(row, "get") else lambda _key, default="": default
    return (
        str(getter("Player", "") or "").strip(),
        str(getter("Market", "") or "").strip(),
        str(getter("Market Line", "") or "").strip(),
        str(getter("Pick", "") or "").strip(),
    )


def install_unified_prop_grading(nfl_builder: Any) -> None:
    """Make admin grading, highlighting and tracking match public yardage tiers."""
    if getattr(nfl_builder, "_UNIFIED_PROP_GRADING_INSTALLED", False):
        return

    original_evaluate = nfl_builder._evaluate_prop_rows
    original_is_graded = nfl_builder._is_graded_prop
    original_tracker_rows = nfl_builder._graded_prop_tracker_rows

    def evaluate(rows: pd.DataFrame) -> pd.DataFrame:
        evaluated = original_evaluate(rows)
        if evaluated is None or evaluated.empty:
            return evaluated

        output = evaluated.copy()
        for index, row in output.iterrows():
            market = str(row.get("Market", "") or "").strip()
            if market not in YARDAGE_PROP_MARKETS:
                continue
            grade = yardage_prop_grade(
                row.get("Projection"),
                row.get("Market Line"),
                row.get("Probability Edge"),
                row.get("Projection Edge"),
            )
            output.at[index, "Grade"] = grade
            if "Track" in output.columns:
                output.at[index, "Track"] = grade in TRACKED_YARDAGE_GRADES
        return output

    def is_graded_prop(grade: str) -> bool:
        text = str(grade or "").strip()
        return text in TRACKED_YARDAGE_GRADES or bool(original_is_graded(text))

    def graded_prop_tracker_rows(
        evaluated: pd.DataFrame,
        slate_date: str,
        season: int,
        week: int,
        game_id: str,
        game: str,
        notes: str,
    ) -> list[dict[str, Any]]:
        if evaluated is None or evaluated.empty:
            return []

        # The legacy tracker helper only admits A/B. Feed it a temporary copy so
        # its storage contract remains untouched, then restore the unified tier.
        temp = evaluated.copy()
        unified: dict[tuple[str, str, str, str], str] = {}
        for index, row in temp.iterrows():
            market = str(row.get("Market", "") or "").strip()
            grade = str(row.get("Grade", "") or "").strip()
            if market not in YARDAGE_PROP_MARKETS or grade not in TRACKED_YARDAGE_GRADES:
                continue
            unified[_tracker_key(row)] = grade
            temp.at[index, "Grade"] = "A Prop" if grade == "Strong" else "B Prop"

        stored = original_tracker_rows(
            temp, slate_date, season, week, game_id, game, notes
        )
        for row in stored:
            grade = unified.get(_tracker_key(row))
            if grade:
                row["Grade"] = grade
        return stored

    nfl_builder._evaluate_prop_rows = evaluate
    nfl_builder._is_graded_prop = is_graded_prop
    nfl_builder._graded_prop_tracker_rows = graded_prop_tracker_rows
    nfl_builder._UNIFIED_PROP_GRADING_INSTALLED = True
