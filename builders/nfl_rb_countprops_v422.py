"""Propagate the validated RB receiving target reconciliation into count props.

v4.22 changes only RB Targets and Receptions:
- Targets: apply the same opportunity factor that v4.20 already applied to the
  player's Receiving Yards target estimate.
- Receptions: apply that same opportunity factor to projected targets and
  receptions, preserving the existing catch-rate estimate.
- Do not add catch-rate shrinkage.

All v4.19-v4.21 behavior remains installed underneath this wrapper.
"""
from __future__ import annotations

import math
import re
from typing import Any

from builders import nfl_slot_matchups as slot_matchups
from builders import nfl_rb_rushing_v421 as v421  # installs v4.19-v4.21 first

MODEL_VERSION = "nfl-v4.22-rb-targets-receptions-reconciliation-2026-10-01"
RB_SLOTS = {"RB1", "RB2", "RB3", "RB4"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "") or "") == market), None)


def _receiving_target_factor(rows: list[dict[str, Any]]) -> tuple[float, float, float, str]:
    """Read the target change already applied by the v4.20 Receiving Yards hybrid.

    v4.20 records its pre/post target counts in Confluence after operating on the
    fully matchup-adjusted receiving row. Reusing that exact factor keeps the count
    props aligned with the validated receiving target correction without recreating
    the rule on a different pre-matchup target baseline.
    """
    row = _row(rows, "Receiving Yards")
    if row is None:
        return 1.0, 0.0, 0.0, ""

    text = str(row.get("Confluence", "") or "")
    pattern = re.compile(
        r"v4\.20 RB receiving target hybrid \(([^)]+)\): regression targets\s+"
        r"([0-9.]+)\s*->\s*([0-9.]+)",
        re.IGNORECASE,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return 1.0, 0.0, 0.0, ""

    match = matches[-1]
    before = _num(match.group(2), 0.0)
    after = _num(match.group(3), 0.0)
    if before <= 0.0 or after <= 0.0:
        return 1.0, before, after, str(match.group(1) or "")
    return after / before, before, after, str(match.group(1) or "")


def _scale_field(row: dict[str, Any], key: str, factor: float) -> None:
    value = _num(row.get(key), math.nan)
    if math.isfinite(value) and value >= 0.0:
        row[key] = round(value * factor, 3 if key == "Targets Per Route" else 2)


def _apply_count_prop_factor(nfl_builder: Any, rows: list[dict[str, Any]], exact_slot: str) -> None:
    if exact_slot not in RB_SLOTS:
        return

    factor, recv_before, recv_after, mode = _receiving_target_factor(rows)
    if abs(factor - 1.0) < 1e-9:
        return

    for market in ("Targets", "Receptions"):
        row = _row(rows, market)
        if row is None:
            continue

        old_projection = max(0.0, _num(row.get("Projection"), 0.0))
        if old_projection <= 0.0:
            continue

        # Opportunity changes only. Scaling targets and receptions together keeps
        # the existing reception rate intact; Efficiency and Matchup Index remain
        # untouched.
        _scale_field(row, "Projected Targets", factor)
        _scale_field(row, "Targets Per Route", factor)
        if market == "Receptions":
            _scale_field(row, "Projected Receptions", factor)

        new_projection = max(0.0, old_projection * factor)
        row["Raw Projection"] = round(new_projection, 2)
        row["Calibration Adjustment"] = 0.0
        row["Projection"] = round(new_projection, 2)
        row["Fair Line"] = nfl_builder._fair_line(new_projection, market)
        row["_sd"] = nfl_builder._prop_sd(
            market, new_projection, _num(row.get("Reliability"), 70.0)
        )

        note = (
            f"v4.22 RB count-prop target reconciliation: reused v4.20 Receiving Yards "
            f"target factor {factor:.3f}x ({recv_before:.2f} -> {recv_after:.2f}, {mode}); "
            f"{market} {old_projection:.2f} -> {new_projection:.2f}; "
            f"catch rate and matchup unchanged"
        )
        previous = str(row.get("Confluence", "") or "").strip()
        row["Confluence"] = f"{previous} • {note}".strip(" •")


def _install() -> None:
    if getattr(slot_matchups, "_RB_COUNTPROPS_V422_PATCHED", False):
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
            nfl_builder, rows, season, projection_week, opponent, slot,
            player_profile, profiles_frame,
        )
        _apply_count_prop_factor(nfl_builder, result, exact_slot)
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._RB_COUNTPROPS_V422_PATCHED = True


_install()
