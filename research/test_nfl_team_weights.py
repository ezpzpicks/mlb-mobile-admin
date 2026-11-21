"""Integrity checks for the research-only rolling team regression."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research import nfl_team_weights as study


def fixture() -> pd.DataFrame:
    rng = np.random.default_rng(20261121)
    rows = []
    for season in [2022, 2023, 2024, 2025]:
        for week in range(1, 5):
            for game in range(2):
                away = study.TEAMS[game * 2]; home = study.TEAMS[game * 2 + 1]
                for side, team, opponent, sign in [("away", away, home, -1), ("home", home, away, 1)]:
                    values = dict(zip(study.FEATURES, rng.normal(size=len(study.FEATURES))))
                    values["off_scoring"] += 22.5
                    values["def_scoring_allowed"] += 22.5
                    target = 22.0 + values["off_pass_epa"] * 2 + rng.normal()
                    rows.append({"season": season, "week": week, "game_id": f"{season}_{week}_{game}",
                                 "gameday": f"{season}-10-{week * 7:02d}",
                                 "input_cutoff": f"{season}-10-{week * 7:02d}",
                                 "max_current_input_date": f"{season}-09-30" if week == 1 else f"{season}-10-{(week - 1) * 7:02d}",
                                 "team": team, "opponent": opponent, "side": side,
                                 "target": target, "points": target + sign * 0.75,
                                 "hfa_offset": sign * 0.75, "production_core": 22.0,
                                 "current_games": week - 1, "opponent_current_games": week - 1,
                                 "rating_current_weight": study.nflb._season_weight(week, week - 1),
                                 "market_margin": 1.5, "market_total": 44.5, **values})
    return pd.DataFrame(rows)


class RollingTeamWeightsTests(unittest.TestCase):
    def test_actual_sample_cap_and_bye_week(self):
        self.assertEqual(study.nflb._season_weight(1, 0), 0)
        self.assertEqual(study.nflb._season_weight(2, 1), 0.15)
        self.assertEqual(study.nflb._season_weight(4, 3), 0.4096)
        self.assertEqual(study.nflb._season_weight(11, 10), 0.7003)
        self.assertLess(study.nflb._season_weight(11, 9), study.nflb._season_weight(11, 10))

    def test_each_score_enters_one_offense_and_one_defense_block(self):
        frame = fixture().iloc[:2]
        basis = np.array([[1.0, 2.0], [1.0, 3.0]])
        design = study.joint_design(frame, basis).toarray()
        self.assertEqual(design.shape, (2, 128))
        for i, row in enumerate(frame.itertuples()):
            off = study.TEAM_INDEX[row.team] * 2
            defense = (32 + study.TEAM_INDEX[row.opponent]) * 2
            np.testing.assert_array_equal(design[i, off:off + 2], basis[i])
            np.testing.assert_array_equal(design[i, defense:defense + 2], basis[i])
            self.assertEqual(np.count_nonzero(design[i]), 4)

    def test_offense_and_defense_keep_different_unit_variables(self):
        data = fixture(); league = study.LeagueFit.fit(data)
        frame = data.iloc[:2]
        offense = study.profile_basis(frame, league, "full", 0)
        defense = study.profile_basis(frame, league, "full", 1)
        self.assertEqual(offense.shape, (2, 10))
        self.assertFalse(np.allclose(offense[:, 1:], defense[:, 1:]))
        design = study.joint_design(frame, (offense, defense)).toarray()
        off_idx = study.TEAM_INDEX[frame.iloc[0].team] * 10
        def_idx = (32 + study.TEAM_INDEX[frame.iloc[0].opponent]) * 10
        np.testing.assert_array_equal(design[0, off_idx:off_idx + 10], offense[0])
        np.testing.assert_array_equal(design[0, def_idx:def_idx + 10], defense[0])

    def test_week_one_uses_prior_profiles_exactly(self):
        data = fixture(); league = study.LeagueFit.fit(data[data.season < 2025])
        frame = data[(data.season == 2025) & (data.week == 1)]
        prior = np.ones((2, 32, 1)) * 2
        current = np.ones((2, 32, 1)) * 100
        result = study.rolling_profile_prediction(frame, league, "intercept", prior, current)
        np.testing.assert_allclose(result, league.predict(frame) + 4)

    def test_offense_and_opponent_use_their_own_sample_sizes(self):
        data = fixture(); league = study.LeagueFit.fit(data[data.season < 2025])
        frame = data.iloc[:1].copy()
        frame["week"] = 11; frame["current_games"] = 10; frame["opponent_current_games"] = 9
        prior = np.zeros((2, 32, 1)); current = np.ones((2, 32, 1))
        current[1] = 2.0
        result = study.rolling_profile_prediction(frame, league, "intercept", prior, current)
        expected = league.predict(frame) + 0.7003 + 2 * study.nflb._season_weight(11, 9)
        np.testing.assert_allclose(result, expected)

    def test_future_week_outcomes_cannot_change_earlier_forecasts(self):
        data = fixture()
        before, _ = study.replay(data, 2025, [("core", 32.0)])
        changed = data.copy()
        changed.loc[(changed.season == 2025) & (changed.week >= 3), "target"] += 10000
        changed.loc[(changed.season == 2025) & (changed.week >= 3), "points"] += 10000
        after, _ = study.replay(changed, 2025, [("core", 32.0)])
        for column in ["league", "core_32", "independent"]:
            np.testing.assert_allclose(before.loc[before.week <= 3, column], after.loc[after.week <= 3, column])
        self.assertFalse(np.allclose(before.loc[before.week == 4, "core_32"], after.loc[after.week == 4, "core_32"]))

    def test_missing_profiles_fall_back_to_league(self):
        data = fixture(); league = study.LeagueFit.fit(data)
        profiles = study.fit_profiles(data.iloc[:0], league, "full", 32.0)
        self.assertEqual(profiles.shape, (2, 32, 10))
        np.testing.assert_array_equal(profiles, np.zeros_like(profiles))

    def test_regularization_reduces_profile_magnitude(self):
        data = fixture(); league = study.LeagueFit.fit(data[data.season < 2024])
        frame = data[data.season == 2024]
        weak = study.fit_profiles(frame, league, "full", 8)
        strong = study.fit_profiles(frame, league, "full", 128)
        self.assertLess(np.linalg.norm(strong), np.linalg.norm(weak))

    def test_dataset_rejects_future_input_and_missing_side(self):
        data = fixture(); study.validate_dataset(data)
        leaking = data.copy(); leaking.loc[0, "max_current_input_date"] = leaking.loc[0, "input_cutoff"]
        with self.assertRaisesRegex(ValueError, "leakage"):
            study.validate_dataset(leaking)
        with self.assertRaisesRegex(ValueError, "one home and one away"):
            study.validate_dataset(data.iloc[1:])

    def test_pushes_are_neither_wins_nor_losses(self):
        games = pd.DataFrame({"predicted_margin": [7, -7, 7], "market_margin": [3, -3, 3], "actual_margin": [3, -4, 2]})
        record = study.betting_record(games, "margin", 1.5)
        self.assertEqual((record["wins"], record["losses"], record["pushes"]), (1, 1, 1))
        self.assertEqual(record["win_rate_excluding_pushes"], 0.5)
        self.assertAlmostEqual(record["profit_units"], 100 / 110 - 1)

    def test_exports_all_64_profiles_without_requiring_a_game_this_week(self):
        data = fixture()
        _, latest = study.replay(data, 2025, [("full", 32)])
        coefficients, summary = study.export_profiles(data, latest, "full_32", 2025)
        self.assertEqual(len(summary), 64)
        self.assertEqual(len(coefficients), 640)
        self.assertEqual(summary.groupby("team").unit.nunique().min(), 2)
        self.assertFalse(coefficients.duplicated(["team", "unit", "feature"]).any())


if __name__ == "__main__":
    unittest.main()
