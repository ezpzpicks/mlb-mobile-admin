from __future__ import annotations
from types import SimpleNamespace
import pandas as pd

from builders import cfb_2026_regime as regime


def main() -> None:
    # Frozen model sanity: at the training means, only the intercept remains.
    at_mean = dict(regime.FEATURE_MEANS)
    correction, contributions = regime._predict_correction(at_mean)
    assert abs(correction - regime.MODEL_INTERCEPT) < 1e-12
    assert all(abs(v) < 1e-12 for v in contributions.values())

    class FakeBuilder:
        DEFAULT_SEASON = 2026

        @staticmethod
        def project_matchup(game, ratings, away_personnel, home_personnel, environment):
            return {
                "away_points": 24.0,
                "home_points": 34.0,
                "margin": 10.0,
                "total": 58.0,
                "regression_away_features": {"current_power_delta": -2.0},
                "regression_home_features": {"current_power_delta": 2.0},
            }

    builder = FakeBuilder()
    original_feature_row = regime._feature_row
    try:
        regime._feature_row = lambda *args, **kwargs: dict(regime.FEATURE_MEANS)
        regime.install_regime_adjustment(builder)
        personnel = SimpleNamespace()
        env = SimpleNamespace()
        out = builder.project_matchup(
            pd.Series({"Season": 2026, "Week": 5, "Away Team": "Away", "Home Team": "Home"}),
            pd.DataFrame(), personnel, personnel, env,
        )
        assert out["regime_2026_applied"] is True
        assert abs(out["total"] - 58.0) < 1e-12
        assert abs((out["home_points"] + out["away_points"]) - 58.0) < 1e-12
        assert abs(out["margin"] - (10.0 + regime.MODEL_INTERCEPT)) < 1e-12

        pre = builder.project_matchup(
            pd.Series({"Season": 2026, "Week": 4, "Away Team": "Away", "Home Team": "Home"}),
            pd.DataFrame(), personnel, personnel, env,
        )
        assert pre["regime_2026_applied"] is False
        assert abs(pre["margin"] - 10.0) < 1e-12
    finally:
        regime._feature_row = original_feature_row

    print("CFB 2026 regime layer tests passed")


if __name__ == "__main__":
    main()
