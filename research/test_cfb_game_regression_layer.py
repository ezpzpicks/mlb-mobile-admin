from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from builders import cfb_game_regression as reg


class FakeBuilder:
    DEFAULT_SEASON = 2026
    MODEL_VERSION = "legacy"

    @staticmethod
    def project_matchup(game, ratings, away_personnel, home_personnel, environment):
        # Fixed legacy total so the test can prove the regression layer replaces
        # margin without leaking the sportsbook spread into the mean projection.
        return {
            "away": {"Previous Season Weight": 1.0},
            "home": {"Previous Season Weight": 1.0},
            "away_points": 27.5,
            "home_points": 27.5,
            "margin": 0.0,
            "total": 55.0,
            "possessions": 12.0,
            "away_components": {},
            "home_components": {},
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


def environment():
    return SimpleNamespace(home_field=2.0, league_hfa=2.0)


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
    reg._CONTEXT_CACHE.clear()
    reg._CONTEXT_CACHE[(2026, 1)] = (
        {
            "Weak": reg.TeamStats(power=-15.0, ppg=18.0, papg=34.0, games=12),
            "Strong": reg.TeamStats(power=18.0, ppg=39.0, papg=18.0, games=12),
        },
        {},
    )

    builder = FakeBuilder()
    reg.install_regression_layer(builder)

    neutral_personnel = personnel()
    p1 = builder.project_matchup(game(-3.0), pd.DataFrame(), neutral_personnel, neutral_personnel, environment())
    p2 = builder.project_matchup(game(-35.0), pd.DataFrame(), neutral_personnel, neutral_personnel, environment())

    assert abs(p1["margin"] - p2["margin"]) < 1e-9, (p1["margin"], p2["margin"])
    assert abs(p1["total"] - 55.0) < 1e-9, p1["total"]
    assert p1["margin"] > 20.0, p1["margin"]
    assert p1["regression_research_version"] == reg.MODEL_RESEARCH_VERSION
    assert builder.MODEL_VERSION == reg.MODEL_VERSION

    home_boost = personnel(offense_adjustment=2.0)
    p3 = builder.project_matchup(game(-35.0), pd.DataFrame(), neutral_personnel, home_boost, environment())
    assert abs((p3["margin"] - p2["margin"]) - 2.0) < 1e-9, (p2["margin"], p3["margin"])
    assert abs(p3["total"] - 55.0) < 1e-9, p3["total"]

    print("CFB v2 regression layer smoke test passed")
    print(f"synthetic regression margin={p1['margin']:.3f}, total={p1['total']:.3f}")


if __name__ == "__main__":
    main()
