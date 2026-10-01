"""Guarded v4.19 calibration for WR1/WR2 receiving yards only.

This patch leaves the fitted RB/WR regression, count props, WR3, RB, TE, rushing,
passing and TD paths unchanged.  It replaces only the final Receiving Yards row
for WR1/WR2 after the v4.18 slot layer has run.

Validated candidate reconstruction:
- start from the regression target baseline before the live target-role overlay
- keep the regression/live efficiency estimate, but shrink YPT 80% to 8.15
- discard the broad/variable-tier receiving-yard matchup multiplier
- apply only the exact-slot residual at 3x strength
"""
from __future__ import annotations

import math
from typing import Any

from builders import nfl_slot_matchups as slot_matchups

MODEL_VERSION = "nfl-v4.19-wr12-receiving-calibration-2026-10-01"
WR_SLOTS = {"WR1", "WR2"}
YPT_PRIOR = 8.15
EFFICIENCY_PRIOR_WEIGHT = 0.80
EXACT_SLOT_STRENGTH = 3.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "") or "") == market), None)


def _live_role_overlay(row: dict[str, Any] | None) -> float:
    """Read the regression target-role multiplier recorded in Confluence."""
    text = str((row or {}).get("Confluence", "") or "")
    lower = text.lower()
    marker = "live role overlay "
    start = lower.find(marker)
    if start < 0:
        return 1.0
    tail = text[start + len(marker):]
    x_pos = tail.lower().find("x")
    token = tail[:x_pos if x_pos >= 0 else None].strip()
    value = _num(token, 1.0)
    return value if value > 0.01 else 1.0


def _install() -> None:
    if getattr(slot_matchups, "_WR12_RECEIVING_V419_PATCHED", False):
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
        pre = _row(rows, "Receiving Yards")

        candidate: dict[str, float] | None = None
        pre_reason = ""
        if exact_slot in WR_SLOTS and pre is not None:
            pre_targets = max(0.0, _num(pre.get("Projected Targets"), 0.0))
            pre_receptions = max(0.0, _num(pre.get("Projected Receptions"), 0.0))
            pre_tprr = max(0.0, _num(pre.get("Targets Per Route"), 0.0))
            regression_eff = max(0.0, _num(pre.get("Efficiency"), YPT_PRIOR))
            role_overlay = _live_role_overlay(pre)
            regression_targets = pre_targets / role_overlay if role_overlay > 0 else pre_targets
            shrunk_eff = (
                (1.0 - EFFICIENCY_PRIOR_WEIGHT) * regression_eff
                + EFFICIENCY_PRIOR_WEIGHT * YPT_PRIOR
            )
            profile = slot_matchups.slot_matchup_profile(
                nfl_builder, int(season), int(projection_week), opponent,
                exact_slot, "Receiving Yards",
            ) if int(projection_week) > 1 else {}
            slot_adjustment = _num((profile or {}).get("adjustment_pct"), 0.0)
            slot_factor = 1.0 + EXACT_SLOT_STRENGTH * slot_adjustment
            final_eff = max(0.0, shrunk_eff * slot_factor)
            projection = max(0.0, regression_targets * final_eff)
            catch_rate = pre_receptions / pre_targets if pre_targets > 0 else 0.64
            target_scale = regression_targets / pre_targets if pre_targets > 0 else 1.0
            candidate = {
                "projection": projection,
                "targets": regression_targets,
                "receptions": max(0.0, regression_targets * catch_rate),
                "tprr": max(0.0, pre_tprr * target_scale),
                "regression_eff": regression_eff,
                "shrunk_eff": shrunk_eff,
                "final_eff": final_eff,
                "role_overlay": role_overlay,
                "slot_adjustment": slot_adjustment,
                "slot_factor": slot_factor,
            }
            pre_reason = str(pre.get("Confluence", "") or "").strip()

        result = original_apply(
            nfl_builder, rows, season, projection_week, opponent, slot,
            player_profile, profiles_frame,
        )

        if candidate is None:
            return result

        row = _row(result, "Receiving Yards")
        if row is None:
            return result

        projection = candidate["projection"]
        row["Projected Targets"] = round(candidate["targets"], 2)
        row["Projected Receptions"] = round(candidate["receptions"], 2)
        row["Targets Per Route"] = round(candidate["tprr"], 3)
        row["Efficiency"] = round(candidate["final_eff"], 3)
        row["Raw Projection"] = round(projection, 2)
        row["Calibration Adjustment"] = 0.0
        row["Projection"] = round(projection, 2)
        row["Fair Line"] = nfl_builder._fair_line(projection, "Receiving Yards")
        row["_sd"] = nfl_builder._prop_sd(
            "Receiving Yards", projection, _num(row.get("Reliability"), 70.0)
        )
        # Matchup Index remains a matchup-only field. The target-role removal and
        # YPT shrink are calibration steps, so only the exact-slot factor belongs here.
        row["Matchup Index"] = round(candidate["slot_factor"], 3)

        note = (
            f"v4.19 WR1/WR2 receiving calibration: removed live role overlay "
            f"{candidate['role_overlay']:.2f}x; YPT {candidate['regression_eff']:.2f} -> "
            f"{candidate['shrunk_eff']:.2f} (20% model / 80% {YPT_PRIOR:.2f} prior); "
            f"exact-slot residual {candidate['slot_adjustment']:+.1%} x{EXACT_SLOT_STRENGTH:.1f} "
            f"=> {candidate['slot_factor']:.3f}x"
        )
        row["Confluence"] = f"{pre_reason} • {note}".strip(" •")
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._WR12_RECEIVING_V419_PATCHED = True


_install()
