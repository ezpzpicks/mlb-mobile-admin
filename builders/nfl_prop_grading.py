"""Unified NFL player-prop grading shared conceptually with the public site.

Yardage markets use the tested Strong / Regular / Lean system. Non-yardage
markets keep the builder's existing A Prop / B Prop / Lean rules.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

YARDAGE_PROP_MARKETS = {"Passing Yards", "Rushing Yards", "Receiving Yards"}
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

    # A meaningful projection-vs-line disagreement is required for every
    # official yardage tier. Reliability remains descriptive, not a grade gate.
    if gap_pct < MIN_PROJECTION_GAP_PCT or edge_pct < LEAN_EDGE_PCT:
        return "Non-Edge"
    if edge_pct >= STRONG_EDGE_PCT:
        return "Strong"
    if edge_pct >= REGULAR_EDGE_PCT:
        return "Regular"
    return "Lean"


def install_unified_prop_grading(nfl_builder: Any) -> None:
    """Make admin yardage grades match the public Strong/Regular/Lean system."""
    if getattr(nfl_builder, "_UNIFIED_PROP_GRADING_INSTALLED", False):
        return

    original = nfl_builder._evaluate_prop_rows

    def wrapped(rows: pd.DataFrame) -> pd.DataFrame:
        evaluated = original(rows)
        if evaluated is None or evaluated.empty:
            return evaluated

        output = evaluated.copy()
        for index, row in output.iterrows():
            market = str(row.get("Market", "") or "").strip()
            if market not in YARDAGE_PROP_MARKETS:
                continue
            output.at[index, "Grade"] = yardage_prop_grade(
                row.get("Projection"),
                row.get("Market Line"),
                row.get("Probability Edge"),
                row.get("Projection Edge"),
            )
        return output

    nfl_builder._evaluate_prop_rows = wrapped
    nfl_builder._UNIFIED_PROP_GRADING_INSTALLED = True
