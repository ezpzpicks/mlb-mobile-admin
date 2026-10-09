"""Guarded NFL receiving calibrations layered on the v4.18 slot model.

WR1/WR2 receiving yards (v4.19):
- start from the regression target baseline before the live target-role overlay
- shrink YPT 80% to the 8.15 WR prior
- discard the broad receiving-yard matchup multiplier
- apply exact-slot target and efficiency residuals through the existing per-input
  player tiers, with component and final matchup caps

RB receiving yards (v4.20):
- keep the existing RB receiving efficiency and matchup calculation unchanged
- reconcile final projected targets against the independent Routes x TPRR estimate
- if regression targets are above Routes x TPRR, pull them back by at most 1 target
- only restore targets upward for a high-TPRR receiving archetype with a suppressive
  live-role overlay, capped at 1.5 targets

All other markets and positions remain on their existing paths.
"""
from __future__ import annotations

import math
from typing import Any

from builders import nfl_slot_matchups as slot_matchups
from builders.nfl_prop_grading import install_unified_prop_grading

MODEL_VERSION = "nfl-v4.20-rb-receiving-target-hybrid-2026-10-01"
WR_SLOTS = {"WR1", "WR2"}
YPT_PRIOR = 8.15
EFFICIENCY_PRIOR_WEIGHT = 0.80

# Forward-validated RB receiving target reconciliation.  These values were fit on
# completed games before 2026-09-27 and then checked on the untouched 09-27 slate.
RB_TARGET_DOWN_CAP = 1.0
RB_TARGET_UP_CAP = 1.5
RB_ROUTE_TARGET_RATIO_MIN = 1.15
RB_ROUTE_PARTICIPATION_MIN = 0.45
RB_TPRR_MIN = 0.20
RB_ROLE_OVERLAY_MAX = 0.85


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


def _wr_matchup_factors(
    nfl_builder: Any,
    season: int,
    projection_week: int,
    opponent: str,
    exact_slot: str,
    player_profile: dict[str, Any] | None,
    profiles_frame: Any,
) -> dict[str, Any]:
    """Preserve skill-tier protection after the receiving baseline calibration.

    Target-volume evidence belongs to opportunity; the remaining yardage signal
    belongs to efficiency. Each input scales only its assigned matchup slice, as
    in v4.18. Do not amplify the slot residual again after its sample shrinkage,
    and enforce the final market cap after combining the protected components.
    """
    tiers = slot_matchups._yardage_variable_tiers(
        nfl_builder, profiles_frame, player_profile or {}, exact_slot, "Receiving Yards"
    )
    profiles = {
        market: slot_matchups.slot_matchup_profile(
            nfl_builder, int(season), int(projection_week), opponent, exact_slot, market
        ) if int(projection_week) > 1 else {}
        for market in ("Targets", "Receiving Yards")
    }
    target_adjustment = _num(profiles["Targets"].get("adjustment_pct"), 0.0)
    yardage_adjustment = _num(profiles["Receiving Yards"].get("adjustment_pct"), 0.0)
    target_factor = max(0.01, 1.0 + target_adjustment)
    yardage_factor = max(0.01, 1.0 + yardage_adjustment)
    opportunity_factor = slot_matchups._variable_scaled_matchup_factor(
        target_factor, tiers.get("opportunity")
    )
    efficiency_factor = slot_matchups._variable_scaled_matchup_factor(
        yardage_factor / target_factor, tiers.get("efficiency")
    )
    combined = opportunity_factor * efficiency_factor
    cap = slot_matchups.TOTAL_MATCHUP_CAP["Receiving Yards"]
    final_factor = min(1.0 + cap, max(1.0 - cap, combined))
    # Keep targets, YPT and the simulation's catch rate consistent with the cap.
    efficiency_factor *= final_factor / combined
    return {
        "opportunity_factor": opportunity_factor,
        "efficiency_factor": efficiency_factor,
        "final_factor": final_factor,
        "slot_adjustment": yardage_adjustment,
        "target_adjustment": target_adjustment,
        "tiers": tiers,
    }


def _apply_rb_receiving_target_hybrid(nfl_builder: Any, rows: list[dict[str, Any]], exact_slot: str) -> None:
    """Reconcile RB receiving-yard targets with the independent route/TPRR estimate.

    This intentionally leaves YPT/efficiency untouched.  It reproduces the tested
    target-only hybrid using the fully adjusted v4.18 receiving-yard row.
    """
    if exact_slot not in {"RB1", "RB2", "RB3", "RB4"}:
        return

    row = _row(rows, "Receiving Yards")
    if row is None:
        return

    targets = max(0.0, _num(row.get("Projected Targets"), 0.0))
    routes = max(0.0, _num(row.get("Projected Routes"), 0.0))
    tprr = max(0.0, _num(row.get("Targets Per Route"), 0.0))
    route_participation = max(0.0, _num(row.get("Route Participation"), 0.0))
    if targets <= 0.0 or routes <= 0.0 or tprr <= 0.0:
        return

    route_targets = routes * tprr
    role_overlay = _live_role_overlay(row)
    new_targets = targets
    mode = ""

    if route_targets < targets:
        # Regression target volume can run materially above the route-based target
        # estimate.  Pull it back, but never by more than one target.
        new_targets = max(route_targets, targets - RB_TARGET_DOWN_CAP)
        mode = "down"
    else:
        target_ratio = route_targets / targets if targets > 0 else 1.0
        is_receiving_archetype = (
            target_ratio >= RB_ROUTE_TARGET_RATIO_MIN
            and route_participation >= RB_ROUTE_PARTICIPATION_MIN
            and tprr >= RB_TPRR_MIN
            and role_overlay < RB_ROLE_OVERLAY_MAX
        )
        if is_receiving_archetype:
            new_targets = min(route_targets, targets + RB_TARGET_UP_CAP)
            mode = "up-archetype"

    if not mode or abs(new_targets - targets) < 1e-9:
        return

    scale = new_targets / targets
    old_projection = max(0.0, _num(row.get("Projection"), 0.0))
    old_receptions = max(0.0, _num(row.get("Projected Receptions"), 0.0))
    new_projection = max(0.0, old_projection * scale)

    row["Projected Targets"] = round(new_targets, 2)
    row["Projected Receptions"] = round(old_receptions * scale, 2)
    # Keep Efficiency, Routes, Route Participation and TPRR unchanged.  TPRR is
    # the independent role estimate used to cross-check the regression targets.
    row["Raw Projection"] = round(new_projection, 2)
    row["Calibration Adjustment"] = 0.0
    row["Projection"] = round(new_projection, 2)
    row["Fair Line"] = nfl_builder._fair_line(new_projection, "Receiving Yards")
    row["_sd"] = nfl_builder._prop_sd(
        "Receiving Yards", new_projection, _num(row.get("Reliability"), 70.0)
    )

    note = (
        f"v4.20 RB receiving target hybrid ({mode}): regression targets "
        f"{targets:.2f} -> {new_targets:.2f}; Routes x TPRR {route_targets:.2f} "
        f"({routes:.2f} x {tprr:.3f}); route participation {route_participation:.1%}; "
        f"live role overlay {role_overlay:.2f}x; YPT unchanged"
    )
    previous = str(row.get("Confluence", "") or "").strip()
    row["Confluence"] = f"{previous} • {note}".strip(" •")


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

        candidate: dict[str, Any] | None = None
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
            matchup = _wr_matchup_factors(
                nfl_builder, season, projection_week, opponent, exact_slot,
                player_profile, profiles_frame,
            )
            final_targets = regression_targets * matchup["opportunity_factor"]
            final_eff = max(0.0, shrunk_eff * matchup["efficiency_factor"])
            projection = max(0.0, final_targets * final_eff)
            catch_rate = pre_receptions / pre_targets if pre_targets > 0 else 0.64
            target_scale = regression_targets / pre_targets if pre_targets > 0 else 1.0
            candidate = {
                "projection": projection,
                "targets": final_targets,
                "receptions": max(0.0, final_targets * catch_rate),
                "tprr": max(0.0, pre_tprr * target_scale * matchup["opportunity_factor"]),
                "regression_eff": regression_eff,
                "shrunk_eff": shrunk_eff,
                "final_eff": final_eff,
                "role_overlay": role_overlay,
                **matchup,
            }
            pre_reason = str(pre.get("Confluence", "") or "").strip()

        result = original_apply(
            nfl_builder, rows, season, projection_week, opponent, slot,
            player_profile, profiles_frame,
        )

        # RB target reconciliation is intentionally applied to the fully adjusted
        # v4.18 row so it matches the historical audit construction exactly.
        _apply_rb_receiving_target_hybrid(nfl_builder, result, exact_slot)

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
        # Record the actual protected matchup factor, separate from calibration.
        row["Matchup Index"] = round(candidate["final_factor"], 3)

        note = (
            f"v4.19 WR1/WR2 receiving calibration: removed live role overlay "
            f"{candidate['role_overlay']:.2f}x; YPT {candidate['regression_eff']:.2f} -> "
            f"{candidate['shrunk_eff']:.2f} (20% model / 80% {YPT_PRIOR:.2f} prior); "
            f"exact-slot residual {candidate['slot_adjustment']:+.1%}; "
            f"tier-protected targets {candidate['opportunity_factor']:.3f}x / "
            f"YPT {candidate['efficiency_factor']:.3f}x => {candidate['final_factor']:.3f}x"
        )
        tier_parts = [
            f"{side}: " + ", ".join(
                f"{item['label']} {item['tier']}@{item['weight']:.0%}"
                for item in candidate["tiers"].get(side, [])
            )
            for side in ("opportunity", "efficiency")
        ]
        row["Confluence"] = (
            f"{pre_reason} • {note} • variable tiers: {' | '.join(tier_parts)}"
        ).strip(" •")
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        install_unified_prop_grading(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._WR12_RECEIVING_V419_PATCHED = True


_install()
