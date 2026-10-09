"""Exercise skill-tier protection through the complete production WR wrapper chain."""
from __future__ import annotations

import copy
import math

import pandas as pd

from builders import nfl_builder as builder
from builders import nfl_slot_matchups as slots
import shared.auth  # noqa: F401; install the same v4.19-v4.27 chain as production


def peer_profiles(slot: str, relative: float) -> tuple[pd.DataFrame, dict]:
    rows = []
    for team_number in range(32):
        for rank, targets, share, routes in [(1, 10.0, 0.22, 0.72), (2, 6.0, 0.15, 0.65), (3, 2.0, 0.07, 0.54)]:
            rows.append({
                "player_name": f"Peer {team_number} WR{rank}",
                "team": f"T{team_number}", "position": "WR",
                "targets_pg": targets, "current_targets_pg": targets,
                "target_share": share, "route_participation": routes,
                "targets_per_route": 0.25, "yards_per_target": 8.0,
                "ngs_receiving_avg_intended_air_yards": 10.0,
                "ngs_receiving_avg_yac_above_expectation": 1.0,
                "man_ypt": 8.0, "zone_ypt": 8.0,
            })
    index = int(slot[-1]) - 1
    player = dict(rows[index])
    player["player_name"] = "Test Receiver"
    for key in (
        "targets_pg", "current_targets_pg", "target_share", "route_participation",
        "targets_per_route", "yards_per_target", "ngs_receiving_avg_intended_air_yards",
        "ngs_receiving_avg_yac_above_expectation", "man_ypt", "zone_ypt",
    ):
        player[key] *= relative
    rows[index] = player
    return pd.DataFrame(rows), player


def receiving_row() -> dict:
    return {
        "Player": "Test Receiver", "Position": "WR", "Market": "Receiving Yards",
        "Projection": 81.5, "Raw Projection": 81.5, "Calibration Adjustment": 0.0,
        "Projected Targets": 10.0, "Projected Receptions": 6.5,
        "Projected Routes": 30.0, "Route Participation": 0.85,
        "Targets Per Route": 1 / 3, "Efficiency": 8.15,
        "Matchup Index": 1.0, "Reliability": 80.0,
        "Confluence": "v4.4 regression targets × YPT • live role overlay 1.00x",
    }


def main() -> None:
    original_profile = slots.slot_matchup_profile
    try:
        for slot in ("WR1", "WR2"):
            for sign in (-1, 0, 1):
                def matchup_profile(*args, **kwargs):
                    market = args[-1]
                    adjustment = sign * (0.20 if market == "Targets" else 0.25 if market == "Receiving Yards" else 0.0)
                    return {"adjustment_pct": adjustment, "sample": 4.0}

                slots.slot_matchup_profile = matchup_profile
                outputs = []
                for relative in (1.30, 1.0, 0.50):
                    profiles, player = peer_profiles(slot, relative)
                    result = slots._apply_slot_overlay(
                        builder, [receiving_row()], 2026, 5, "TB", slot, player, profiles,
                    )[0]
                    outputs.append(result)
                    assert 0.62 - 1e-9 <= result["Matchup Index"] <= 1.38 + 1e-9
                    assert "variable tiers:" in result["Confluence"]
                    assert "x3.0" not in result["Confluence"]
                    assert abs(result["Projection"] - result["Projected Targets"] * result["Efficiency"]) < 0.06
                    assert abs(result["Projected Receptions"] / result["Projected Targets"] - 0.65) < 0.002
                    assert abs(result["Targets Per Route"] * result["Projected Routes"] - result["Projected Targets"]) < 0.03
                    assert result["Raw Projection"] == result["Projection"]
                    assert result["Calibration Adjustment"] == 0.0

                strong, ordinary, weak = outputs
                # An elite receiver keeps his skill baseline even against an extreme
                # defense. This catches the v4.19 post-tier overwrite directly.
                assert strong["Projection"] == 81.5, (slot, sign, strong)
                assert strong["Matchup Index"] == 1.0
                assert abs(strong["Projection"] - 81.5) <= abs(ordinary["Projection"] - 81.5)
                assert abs(ordinary["Projection"] - 81.5) <= abs(weak["Projection"] - 81.5)
                if sign:
                    assert math.isclose(ordinary["Projection"], 81.5 * (1 + 0.25 * sign), abs_tol=0.02)
                    assert weak["Matchup Index"] == (0.62 if sign < 0 else 1.38)
                else:
                    assert all(row["Projection"] == 81.5 for row in outputs)

        # Tier-1 opportunity remains protected when efficiency is ordinary, and
        # vice versa. Each side must use its own inputs rather than a player-wide tier.
        slots.slot_matchup_profile = lambda *args, **kwargs: {
            "adjustment_pct": -0.20 if args[-1] == "Targets" else -0.25, "sample": 4.0,
        }
        profiles, player = peer_profiles("WR1", 1.0)
        all_tiers = slots._yardage_variable_tiers(builder, profiles, player, "WR1", "Receiving Yards")
        original_tiers = slots._yardage_variable_tiers
        try:
            for protected_side in ("opportunity", "efficiency"):
                tiers = copy.deepcopy(all_tiers)
                for item in tiers[protected_side]:
                    item["tier"], item["weight"] = "Tier 1", 0.0
                slots._yardage_variable_tiers = lambda *args, **kwargs: tiers
                from builders.nfl_wr_receiving_v419 import _wr_matchup_factors
                factors = _wr_matchup_factors(builder, 2026, 5, "TB", "WR1", player, profiles)
                assert factors[f"{protected_side}_factor"] == 1.0
        finally:
            slots._yardage_variable_tiers = original_tiers

        slots.install_slot_matchup_layer(builder)
        assert builder.MODEL_VERSION == "nfl-v4.27-wr-receiving-tier-protection-2026-10-08"
        print("WR tier protection passed: production wrapper chain, both slots, elite/ordinary/weak inputs, independent sides, final caps and simulation consistency")
    finally:
        slots.slot_matchup_profile = original_profile


if __name__ == "__main__":
    main()
