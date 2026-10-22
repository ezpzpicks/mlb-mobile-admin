"""A later outcome cannot choose hyperparameters or change the frozen fit."""
from contextlib import redirect_stdout
from io import StringIO
import unittest

from builders.nba_history import prepare_history
from research.nba_fit import research_run
from test_nba_ratings import source_fixture


class ChronologicalEvaluationTests(unittest.TestCase):
    def test_changed_holdout_outcomes_cannot_change_selection_or_pre_holdout_ratings(self):
        p, g, b = source_fixture()
        before = prepare_history(p, g, b, source="Synthetic chronological check")
        future_games = set(g.loc[g.game_date >= "2025-12-01", "game_id"])
        p.loc[p.game_id.isin(future_games), "points"] += 1
        # Each test player receives 50 offensive and 50 defensive possessions;
        # adding a point to both sides leaves their independent PM unchanged.
        g.loc[g.game_id.isin(future_games), ["home_pts", "away_pts"]] += 60
        after = prepare_history(p, g, b, source="Synthetic chronological check")
        options = dict(validation_start="2025-11-20", holdout_start="2025-12-01",
            holdout_end="2025-12-07", ratings_cutoff="2025-12-10T00:00:00Z",
            player_penalties=(100.0, 1000.0), minute_windows=(2,))
        with redirect_stdout(StringIO()):
            v1, t1, frozen1, final1 = research_run(before, **options)
            v2, t2, frozen2, final2 = research_run(after, **options)
        self.assertEqual(v1, v2)
        self.assertEqual(frozen1, frozen2)
        self.assertEqual(t1["games"], 3)
        self.assertEqual(t1["skipped"], [])
        self.assertNotEqual(t1["metrics"], t2["metrics"])
        self.assertNotEqual(final1["league_efficiency"], final2["league_efficiency"])
        self.assertFalse(final1["research_evaluation"]["eligible_for_posting"])


if __name__ == "__main__":
    unittest.main()
