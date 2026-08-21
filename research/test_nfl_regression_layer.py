"""Small production smoke test for the NFL v4 game regression layer."""
from __future__ import annotations

import math

from builders import nfl_builder
from builders.nfl_game_regression import MODEL_VERSION, install_regression_layer


def main() -> None:
    install_regression_layer(nfl_builder)
    assert nfl_builder.MODEL_VERSION == MODEL_VERSION

    away = {
        "Points/Game": 23.5, "Points Allowed/Game": 21.8,
        "Off Success Rate": 0.445, "Def Success Edge": 0.015,
        "Pass EPA/DB": 0.12, "Pass Def EPA Edge": 0.03,
        "Rush EPA/Play": 0.02, "Rush Def EPA Edge": 0.01,
        "Pace": 64.5,
        "QB Adjustment": 0.0, "OL Adjustment": 0.0, "Skill/Injury Adjustment": 0.0,
        "Front Seven Adjustment": 0.0, "Secondary Adjustment": 0.0, "Special Teams": 0.0,
    }
    home = {
        "Points/Game": 25.0, "Points Allowed/Game": 20.5,
        "Off Success Rate": 0.458, "Def Success Edge": 0.022,
        "Pass EPA/DB": 0.15, "Pass Def EPA Edge": 0.04,
        "Rush EPA/Play": 0.03, "Rush Def EPA Edge": 0.015,
        "Pace": 65.0,
        "QB Adjustment": 0.0, "OL Adjustment": 0.0, "Skill/Injury Adjustment": 0.0,
        "Front Seven Adjustment": 0.0, "Secondary Adjustment": 0.0, "Special Teams": 0.0,
    }
    lineup = {"offense_absence": 0.0, "defense_absence": 0.0}
    settings = {
        "home_field": 1.8,
        "home_rest_edge": 0.8,
        "weather_total_adjustment": -0.5,
        "manual_total_adjustment": 0.0,
        "manual_home_margin_adjustment": 0.0,
    }

    projection = nfl_builder._project_matchup(away, home, lineup, lineup, settings)
    assert 25.0 <= projection["total"] <= 75.0
    assert -31.0 <= projection["margin"] <= 31.0
    assert math.isfinite(projection["away_score"]) and math.isfinite(projection["home_score"])
    assert projection["research_version"] == "nfl-team-score-regression-2026-08-20"

    simulation = nfl_builder._simulate_game(projection, -2.5, 46.5, 75.0, 20260820, simulations=2500)
    for key in ["home_win", "home_cover", "over"]:
        assert 0.0 <= simulation[key] <= 1.0
    assert simulation["margin_sd"] > 0 and simulation["total_sd"] > 0

    spread = nfl_builder._spread_confluence(True, 2.0, away, home, lineup, lineup, 75.0)
    total = nfl_builder._total_confluence(False, 2.0, projection, away, home, -1.0, 75.0)
    moneyline = nfl_builder._moneyline_confluence(True, 0.06, away, home, lineup, lineup, 75.0)
    assert isinstance(spread[0], int) and isinstance(total[0], int) and isinstance(moneyline[0], int)
    print("NFL v4 regression smoke test passed")


if __name__ == "__main__":
    main()
