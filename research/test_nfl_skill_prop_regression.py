"""Smoke tests for the NFL v4.4 player-prop and RB/WR regression layers."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from builders import nfl_builder
import builders.nfl_skill_prop_regression as skill
import builders.nfl_skill_prop_consistency as consistency


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
            {"Market": "Receptions", "Raw Projection": 3.8, "Calibration Adjustment": 0.0, "Projection": 3.8, "Projected Targets": 5.0, "Projected Receptions": 3.8, "Reliability": 74.0},
            {"Market": "Receiving Yards", "Raw Projection": 31.0, "Calibration Adjustment": 0.0, "Projection": 31.0, "Projected Targets": 5.0, "Projected Receptions": 3.8, "Efficiency": 6.2, "Reliability": 74.0, "Confluence": "legacy"},
        ]
    return [
        {"Market": "Targets", "Raw Projection": 7.5, "Calibration Adjustment": 0.0, "Projection": 7.5, "Projected Targets": 7.5, "Reliability": 77.0},
        {"Market": "Receptions", "Raw Projection": 5.0, "Calibration Adjustment": 0.0, "Projection": 5.0, "Projected Targets": 7.5, "Projected Receptions": 5.0, "Reliability": 77.0},
        {"Market": "Receiving Yards", "Raw Projection": 65.0, "Calibration Adjustment": 0.0, "Projection": 65.0, "Projected Targets": 7.5, "Projected Receptions": 5.0, "Efficiency": 8.67, "Reliability": 77.0, "Confluence": "legacy"},
    ]


def _receiving_row(play_probability: float, line: float = 39.5) -> dict[str, float | str]:
    return {
        "Team": "CHI",
        "Player": "Simulation Test WR",
        "Position": "WR",
        "Slot": "WR1",
        "Market": "Receiving Yards",
        "Projection": 45.13,
        "Projected Targets": 5.6,
        "Projected Receptions": 3.7,
        "Efficiency": 8.06,
        "Reliability": 86.0,
        "Role Confidence": 86.0,
        "Market Line": line,
        "Over Odds": -110,
        "Under Odds": -110,
        "_play_probability": play_probability,
    }


def main() -> None:
    # A slate reset removes only the selected date from generated tables. It
    # must preserve prior dates and must not touch ratings/calibration tables.
    original_sheets_ready = nfl_builder.sheets_ready
    original_read_sheet = nfl_builder.read_sheet
    original_write_sheet = nfl_builder.write_sheet
    generated_tabs = [
        nfl_builder.SLATE_TAB,
        nfl_builder.PROP_SLATE_TAB,
        nfl_builder.TRACKER_TAB,
        nfl_builder.PROP_TRACKER_TAB,
        nfl_builder.LINEUP_TAB,
    ]
    stored = {
        tab: pd.DataFrame({"Date": ["2026-09-12", "2026-09-13"], "Marker": ["keep", "reset"]})
        for tab in generated_tabs
    }
    reset_writes = {}
    try:
        nfl_builder.sheets_ready = lambda: True
        nfl_builder.read_sheet = lambda tab, columns: stored[tab].copy()

        def _capture_reset(tab, dataframe, columns):
            reset_writes[tab] = dataframe.copy()
            return True

        nfl_builder.write_sheet = _capture_reset
        reset_ok, reset_counts, _ = nfl_builder._reset_slate_date("2026-09-13")
    finally:
        nfl_builder.sheets_ready = original_sheets_ready
        nfl_builder.read_sheet = original_read_sheet
        nfl_builder.write_sheet = original_write_sheet
    assert reset_ok
    assert reset_counts == {tab: 1 for tab in generated_tabs}
    assert set(reset_writes) == set(generated_tabs)
    for dataframe in reset_writes.values():
        assert dataframe["Date"].tolist() == ["2026-09-12"]

    rb_carries, rb_ypc = skill.project_rb_rushing(_context("RB"))
    rb_targets, rb_ypt = skill.project_rb_receiving(_context("RB"))
    wr_targets, wr_ypt = skill.project_wr_receiving(_context("WR"))
    assert 0 < rb_carries < 35 and 2 <= rb_ypc <= 7
    assert 0 < rb_targets < 15 and 2.5 <= rb_ypt <= 12
    assert 0 < wr_targets < 20 and 3 <= wr_ypt <= 16

    # Established high- and low-volume receivers should retain more separation
    # than the generic depth-chart middle. WR3's no-history fallback is also
    # intentionally lower than the old 14% / 68% / 18.5% role assumption.
    wr1_default = nfl_builder._role_defaults("WR", "WR1")
    wr3_default = nfl_builder._role_defaults("WR", "WR3")
    assert wr1_default["target_share"] / wr3_default["target_share"] > 2.0
    assert wr3_default["route_participation"] == 0.64
    returning_wr1, _, _ = nfl_builder._expected_role_metric(
        {"current_games": 0, "prior_target_share": 0.31, "team": "BUF"},
        "BUF", "WR", "WR1", "target_share", wr1_default["target_share"],
    )
    returning_wr3, _, _ = nfl_builder._expected_role_metric(
        {"current_games": 0, "prior_target_share": 0.07, "team": "BUF"},
        "BUF", "WR", "WR3", "target_share", wr3_default["target_share"],
    )
    assert returning_wr1 > 0.285
    assert returning_wr3 < 0.09

    # The projection itself must be an active-game estimate. Availability can
    # lower confidence, but it cannot multiply projected routes/targets/yards.
    original_calibration = nfl_builder._prop_calibration_adjustment
    nfl_builder._prop_calibration_adjustment = lambda *args: {"adjustment": 0.0, "sample": 0, "residual_sd": np.nan}
    active_rows = {}
    try:
        for availability in (1.0, 0.65):
            role = {
                "test wr": {
                    "play_probability": availability,
                    "target_share": 0.25,
                    "snap_share": 0.92,
                    "route_participation": 0.92,
                    "targets_per_route": 0.245,
                    "role_note": "test role",
                }
            }
            rows = nfl_builder._project_player_markets(
                "Test WR", "WR", "WR1", "BUF", "MIA", "Home",
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                {"Pace": 64.0, "Data Confidence": 80.0}, {},
                {"home_score": 24.0, "away_score": 21.0, "total": 45.0}, 0.0,
                role_context=role, pregame_team_total=24.0,
            )
            active_rows[availability] = next(row for row in rows if row["Market"] == "Receiving Yards")
    finally:
        nfl_builder._prop_calibration_adjustment = original_calibration
    assert active_rows[1.0]["Projection"] == active_rows[0.65]["Projection"]
    assert active_rows[1.0]["Projected Targets"] == active_rows[0.65]["Projected Targets"]
    assert active_rows[0.65]["Reliability"] < active_rows[1.0]["Reliability"]

    # Player-prop settlement is conditional on the player being active. A 65%
    # availability tag must not inject 35% zero-yard Under outcomes or change YPR.
    healthy_samples, _ = nfl_builder._simulate_prop_distribution(_receiving_row(1.0))
    questionable_samples, _ = nfl_builder._simulate_prop_distribution(_receiving_row(0.65))
    assert np.array_equal(healthy_samples, questionable_samples)
    assert math.isclose(float(np.mean(healthy_samples)), 45.13, rel_tol=0.06)

    questionable = nfl_builder._evaluate_prop_rows(pd.DataFrame([_receiving_row(0.65)])).iloc[0]
    assert str(questionable["Pick"]).startswith("Over 39.5")
    assert questionable["Grade"] == "Injury hold"
    assert not bool(questionable["Track"])
    assert float(questionable["Fair Line"]) > 20.0

    # A skewed distribution may lower the median, but it may never flip the bet
    # direction against the active-game projection shown to the user.
    projection_over = nfl_builder._evaluate_prop_rows(pd.DataFrame([_receiving_row(1.0, 40.0)])).iloc[0]
    projection_under = nfl_builder._evaluate_prop_rows(pd.DataFrame([_receiving_row(1.0, 54.5)])).iloc[0]
    assert str(projection_over["Pick"]).startswith("Over 40.0")
    assert str(projection_under["Pick"]).startswith("Under 54.5")

    nfl_builder._project_player_markets = _fake_original
    skill._CONTEXT_CACHE.clear()
    consistency._STATS_CACHE.clear()
    skill._live_history_context = lambda nflb, season, projection_week, player, team, opponent, position, team_total, home_away: _context(str(position).upper())
    for flag in ["_SKILL_PROP_REGRESSION_INSTALLED", "_SKILL_PROP_CONSISTENCY_INSTALLED"]:
        if hasattr(nfl_builder, flag):
            delattr(nfl_builder, flag)
    if hasattr(skill, "_NORMALIZED_STATS_CACHE_INSTALLED"):
        delattr(skill, "_NORMALIZED_STATS_CACHE_INSTALLED")
    skill.install_skill_prop_regression(nfl_builder)
    consistency.install_skill_prop_consistency(nfl_builder)
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
        assert "v4.4 regression" in str(row["Confluence"])

    # Receiving-yard simulations need a catch count that matches the newly
    # regressed target count. Preserve the live model's catch probability.
    assert math.isclose(float(rb_rec["Projected Receptions"]) / float(rb_rec["Projected Targets"]), 3.8 / 5.0, rel_tol=0.02)
    assert math.isclose(float(wr_rec["Projected Receptions"]) / float(wr_rec["Projected Targets"]), 5.0 / 7.5, rel_tol=0.02)
    assert "live catch-rate" in str(rb_rec["Confluence"])
    assert "live catch-rate" in str(wr_rec["Confluence"])
    print("NFL v4.4 active-game prop and RB/WR regression smoke test passed")


if __name__ == "__main__":
    main()
