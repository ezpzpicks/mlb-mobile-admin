"""Smoke tests for the NFL v4.1 RB/WR regression prop layer."""
from __future__ import annotations

import math

from builders import nfl_builder
import builders.nfl_skill_prop_regression as skill


def _context(position: str) -> dict[str, float]:
    base = {
        "available": 1.0,
        "player_games": 8.0,
        "carries_avg3": 15.0,
        "targets_avg3": 5.0,
        "targets_avg8": 6.4,
        "carry_share_avg3": 0.55,
        "carry_share_avg8": 0.52,
        "target_share_avg3": 0.20,
        "target_share_avg8": 0.19,
        "team_rush_avg8": 27.0,
        "team_targets_avg8": 34.0,
        "opp_carries_avg8": 25.0,
        "opp_targets_avg8": 18.0 if position == "WR" else 7.0,
        "ypc8": 4.35,
        "ypt8": 8.2 if position == "WR" else 6.2,
        "air_yards_per_target8": 10.5 if position == "WR" else 2.0,
        "opp_ypc8": 4.20,
        "opp_ypt8": 8.0 if position == "WR" else 6.0,
        "team_spread": -2.5,
        "home": 1.0,
        "team_total": 24.0,
    }
    base["raw_rb_carries"] = base["team_rush_avg8"] * base["carry_share_avg8"]
    base["raw_rb_ypc"] = base["ypc8"] * (base["opp_ypc8"] / 4.25)
    base["raw_targets"] = base["team_targets_avg8"] * base["target_share_avg8"]
    prior = 8.15 if position == "WR" else 6.15
    base["raw_ypt"] = base["ypt8"] * (base["opp_ypt8"] / prior)
    return base


def _fake_original(player, position, slot, *args, **kwargs):
    pos = str(position).upper()
    if pos == "RB":
        return [
            {"Market": "Rushing Attempts", "Raw Projection": 15.0, "Calibration Adjustment": 0.0, "Projection": 15.0, "Projected Player Attempts": 15.0, "Reliability": 76.0},
            {"Market": "Rushing Yards", "Raw Projection": 64.5, "Calibration Adjustment": 0.0, "Projection": 64.5, "Projected Player Attempts": 15.0, "Efficiency": 4.3, "Reliability": 76.0, "Confluence": "legacy"},
            {"Market": "Targets", "Raw Projection": 5.0, "Calibration Adjustment": 0.0, "Projection": 5.0, "Projected Targets": 5.0, "Reliability": 74.0},
            {"Market": "Receiving Yards", "Raw Projection": 31.0, "Calibration Adjustment": 0.0, "Projection": 31.0, "Projected Targets": 5.0, "Efficiency": 6.2, "Reliability": 74.0, "Confluence": "legacy"},
        ]
    return [
        {"Market": "Targets", "Raw Projection": 7.5, "Calibration Adjustment": 0.0, "Projection": 7.5, "Projected Targets": 7.5, "Reliability": 77.0},
        {"Market": "Receiving Yards", "Raw Projection": 65.0, "Calibration Adjustment": 0.0, "Projection": 65.0, "Projected Targets": 7.5, "Projected Receptions": 5.0, "Efficiency": 8.67, "Reliability": 77.0, "Confluence": "legacy"},
    ]


def main() -> None:
    rb_carries, rb_ypc = skill.project_rb_rushing(_context("RB"))
    rb_targets, rb_ypt = skill.project_rb_receiving(_context("RB"))
    wr_targets, wr_ypt = skill.project_wr_receiving(_context("WR"))
    assert 0 < rb_carries < 35 and 2 <= rb_ypc <= 7
    assert 0 < rb_targets < 15 and 2.5 <= rb_ypt <= 12
    assert 0 < wr_targets < 20 and 3 <= wr_ypt <= 16

    nfl_builder._project_player_markets = _fake_original
    skill._CONTEXT_CACHE.clear()
    skill._live_history_context = lambda nflb, season, projection_week, player, team, opponent, position, team_total, home_away: _context(str(position).upper())
    if hasattr(nfl_builder, "_SKILL_PROP_REGRESSION_INSTALLED"):
        delattr(nfl_builder, "_SKILL_PROP_REGRESSION_INSTALLED")
    skill.install_skill_prop_regression(nfl_builder)
    assert nfl_builder.MODEL_VERSION == skill.MODEL_VERSION

    rating = {"Season": 2026, "Projection Week": 1}
    rb_rows = nfl_builder._project_player_markets(
        "Test RB", "RB", "RB1", "BUF", "MIA", "Home", None, None, None,
        rating, {}, {"home_score": 24.0, "away_score": 21.0}, 0.0, {}, {}, 24.0, "test"
    )
    wr_rows = nfl_builder._project_player_markets(
        "Test WR", "WR", "WR1", "BUF", "MIA", "Home", None, None, None,
        rating, {}, {"home_score": 24.0, "away_score": 21.0}, 0.0, {}, {}, 24.0, "test"
    )
    rb_rush = next(row for row in rb_rows if row["Market"] == "Rushing Yards")
    rb_rec = next(row for row in rb_rows if row["Market"] == "Receiving Yards")
    wr_rec = next(row for row in wr_rows if row["Market"] == "Receiving Yards")
    for row in [rb_rush, rb_rec, wr_rec]:
        assert math.isfinite(float(row["Projection"])) and float(row["Projection"]) >= 0
        assert "v4.1 regression" in str(row["Confluence"])
    print("NFL v4.1 RB/WR regression smoke test passed")


if __name__ == "__main__":
    main()
