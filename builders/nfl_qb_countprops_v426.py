"""QB Passing Attempts/Completions opportunity calibration layered on v4.25.

v4.26 changes only QB count props:
- Passing Attempts: apply the already-validated Passing Yards attempt calibration
  to the final standalone attempts projection;
- Passing Completions: apply the same calibration to projected QB attempts and
  scale completions by that opportunity factor, preserving completion rate;
- no additional spread/total game-script adjustment and no completion-rate shrinkage.

All v4.19-v4.25 behavior remains unchanged underneath this wrapper.
"""
from __future__ import annotations

import math
from typing import Any

from builders import nfl_slot_matchups as slot_matchups

MODEL_VERSION = "nfl-v4.27-wr-receiving-tier-protection-2026-10-08"
ATTEMPT_CALIBRATION_INTERCEPT = 18.848437
ATTEMPT_CALIBRATION_SLOPE = 0.405922


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "") or "") == market), None)


def _calibrated_attempts(value: float) -> float:
    value = max(0.0, _num(value, 0.0))
    return max(0.0, ATTEMPT_CALIBRATION_INTERCEPT + ATTEMPT_CALIBRATION_SLOPE * value)


def _refresh_distribution(nfl_builder: Any, row: dict[str, Any], market: str, projection: float) -> None:
    row["Raw Projection"] = round(projection, 2)
    row["Calibration Adjustment"] = 0.0
    row["Projection"] = round(projection, 2)
    row["Fair Line"] = nfl_builder._fair_line(projection, market)
    row["_sd"] = nfl_builder._prop_sd(
        market, projection, _num(row.get("Reliability"), 70.0)
    )


def _apply_qb_count_calibration(
    nfl_builder: Any,
    rows: list[dict[str, Any]],
    exact_slot: str,
) -> None:
    if exact_slot != "QB":
        return

    attempts_row = _row(rows, "Passing Attempts")
    if attempts_row is not None:
        old_projection = max(0.0, _num(attempts_row.get("Projection"), 0.0))
        old_opportunity = max(0.0, _num(attempts_row.get("Projected Player Attempts"), old_projection))
        if old_projection > 0.0:
            new_projection = _calibrated_attempts(old_projection)
            new_opportunity = _calibrated_attempts(old_opportunity)
            attempts_row["Projected Player Attempts"] = round(new_opportunity, 2)
            _refresh_distribution(nfl_builder, attempts_row, "Passing Attempts", new_projection)

            previous = str(attempts_row.get("Confluence", "") or "").strip()
            note = (
                "v4.26 QB count opportunity calibration: existing Passing Yards attempt "
                f"formula {ATTEMPT_CALIBRATION_INTERCEPT:.6f} + "
                f"{ATTEMPT_CALIBRATION_SLOPE:.6f}x applied to standalone Passing Attempts; "
                f"Projection {old_projection:.2f} -> {new_projection:.2f}; "
                "existing matchup/game environment preserved; no new game-script adjustment"
            )
            attempts_row["Confluence"] = f"{previous} • {note}".strip(" •")

    completions_row = _row(rows, "Passing Completions")
    if completions_row is None:
        return

    old_projection = max(0.0, _num(completions_row.get("Projection"), 0.0))
    old_opportunity = max(0.0, _num(completions_row.get("Projected Player Attempts"), 0.0))
    if old_projection <= 0.0 or old_opportunity <= 0.0:
        return

    new_opportunity = _calibrated_attempts(old_opportunity)
    factor = new_opportunity / old_opportunity
    if not math.isfinite(factor) or factor <= 0.0:
        return

    old_completions = max(0.0, _num(completions_row.get("Projected Completions"), old_projection))
    old_efficiency = max(0.0, _num(completions_row.get("Efficiency"), 0.0))
    new_projection = max(0.0, old_projection * factor)

    completions_row["Projected Player Attempts"] = round(new_opportunity, 2)
    completions_row["Projected Completions"] = round(old_completions * factor, 2)
    # Efficiency is deliberately untouched: the validated gain came from
    # opportunity volume, while completion probability was already calibrated.
    _refresh_distribution(nfl_builder, completions_row, "Passing Completions", new_projection)

    previous = str(completions_row.get("Confluence", "") or "").strip()
    note = (
        "v4.26 QB count opportunity calibration: projected attempts "
        f"{old_opportunity:.2f} -> {new_opportunity:.2f} ({factor:.3f}x); "
        f"Passing Completions {old_projection:.2f} -> {new_projection:.2f}; "
        f"completion efficiency preserved at {old_efficiency:.3f}; no rate shrinkage"
    )
    completions_row["Confluence"] = f"{previous} • {note}".strip(" •")


def _install() -> None:
    if getattr(slot_matchups, "_QB_COUNTPROPS_V426_PATCHED", False):
        return

    original_apply = slot_matchups._apply_slot_overlay
    original_install = slot_matchups.install_slot_matchup_layer

    def patched_apply(
        nfl_builder: Any,
        rows: list[dict[str, Any]],
        season: int,
        projection_week: int,
        opponent: str,
        slot: str,
        player_profile: dict[str, Any] | None = None,
        profiles_frame: Any = None,
    ) -> list[dict[str, Any]]:
        exact_slot = slot_matchups._slot(slot)
        result = original_apply(
            nfl_builder,
            rows,
            season,
            projection_week,
            opponent,
            slot,
            player_profile,
            profiles_frame,
        )
        _apply_qb_count_calibration(nfl_builder, result, exact_slot)
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._QB_COUNTPROPS_V426_PATCHED = True


_install()
