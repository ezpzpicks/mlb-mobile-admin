from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from builders import cfb_game_regression as spread_reg
from builders import cfb_total_regression as total_reg


class FakeBuilder:
    DEFAULT_SEASON = 2026
    MODEL_VERSION = "spread-installed"

    @staticmethod
    def _pbp_team_metrics(season, through_week):
        # Neutral advanced-data defaults are a valid research state (for example,
        # the first training season / early weeks). The test avoids network access.
        return pd.DataFrame()

    @staticmethod
    def project_matchup(game, ratings, away_personnel, home_personnel, environment):
        # Mimic the already-installed spread layer. Market spread is intentionally
        # ignored so the totals wrapper can prove it has no sportsbook predictor.
        margin = 14.0
        return {
            "away": {"Previous Season Weight": 1.0},
            "home": {"Previous Season Weight": 1.0},
            "away_points": 21.0,
            "home_points": 35.0,
            "margin": margin,
            "total": 56.0,
            "possessions": 12.0,
            "away_components": {},
            "home_components": {},
            "regression_base_margin": margin,
            "total_calibration": 1.5,
        }


def personnel(**overrides):
    values = {
        "offense_adjustment": 0.0,
        "defense_adjustment": 0.0,
        "kicker_adjustment": 0.0,
        "special_teams_adjustment": 0.0,
        "qb_continuity": 0.50,
        "coaching_continuity": 0.75,
        "coordinator_continuity": 0.67,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def environment(weather_total_adjustment=0.0):
    return SimpleNamespace(weather_total_adjustment=float(weather_total_adjustment))


def game(spread: float) -> pd.Series:
    return pd.Series({
        "Season": 2026,
        "Week": 1,
        "Away Team": "Weak",
        "Home Team": "Strong",
        "Neutral Site": False,
        "Home Spread": spread,
    })


def main() -> None:
    assert total_reg.MODEL_RESULT_PATH.exists(), total_reg.MODEL_RESULT_PATH
    model = total_reg._model()
    assert "spread_score_sum" not in model["features"], model["features"]

    spread_reg._CONTEXT_CACHE.clear()
    total_reg._METRIC_CONTEXT_CACHE.clear()
    spread_reg._CONTEXT_CACHE[(2026, 1)] = (
        {
            "Weak": spread_reg.TeamStats(power=-15.0, ppg=18.0, papg=34.0, games=12),
            "Strong": spread_reg.TeamStats(power=18.0, ppg=39.0, papg=18.0, games=12),
        },
        {},
    )

    builder = FakeBuilder()
    total_reg.install_total_regression(builder)
    neutral = personnel()

    p1 = builder.project_matchup(game(-3.0), pd.DataFrame(), neutral, neutral, environment())
    p2 = builder.project_matchup(game(-35.0), pd.DataFrame(), neutral, neutral, environment())

    # Sportsbook spread cannot alter the independent total or final spread margin.
    assert abs(p1["total"] - p2["total"]) < 1e-9, (p1["total"], p2["total"])
    assert abs(p1["margin"] - p2["margin"]) < 1e-9, (p1["margin"], p2["margin"])
    assert p1["total_regression_source"] == "independent_pace_efficiency_interactions"
    assert p1["legacy_total_calibration"] == 1.5
    assert p1["total_calibration"] == 0.0

    # The algebraic combination must preserve both model outputs exactly.
    assert abs((p1["home_points"] + p1["away_points"]) - p1["total"]) < 1e-9
    assert abs((p1["home_points"] - p1["away_points"]) - p1["margin"]) < 1e-9

    # Live information unavailable to the historical regression remains an overlay.
    home_offense_boost = personnel(offense_adjustment=2.0)
    p3 = builder.project_matchup(
        game(-3.0), pd.DataFrame(), neutral, home_offense_boost, environment()
    )
    assert abs((p3["total"] - p1["total"]) - 2.0) < 1e-9, (p1["total"], p3["total"])

    p4 = builder.project_matchup(
        game(-3.0), pd.DataFrame(), neutral, neutral, environment(-4.0)
    )
    assert abs((p4["total"] - p1["total"]) + 4.0) < 1e-9, (p1["total"], p4["total"])

    print("CFB independent totals regression smoke test passed")
    print(
        f"total={p1['total']:.3f}, margin={p1['margin']:.3f}, "
        f"home={p1['home_points']:.3f}, away={p1['away_points']:.3f}"
    )


if __name__ == "__main__":
    main()
