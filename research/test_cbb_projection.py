"""Check basketball projection behavior without initializing Streamlit or storage.

Compile the production math functions directly from their source so these tests
exercise the builder's formulas without importing its interactive UI or secrets.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def load_projection():
    namespace = {"pd": pd}
    for relative, names in [
        ("shared/modeling.py", {"clamp"}),
        ("builders/cbb_builder.py", {"_num", "_project"}),
    ]:
        path = ROOT / relative
        tree = ast.parse(path.read_text())
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        if {node.name for node in functions} != names:
            raise AssertionError(f"Projection functions missing from {relative}")
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["_project"]


class ProjectionBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.project = load_projection()
        self.team = {
            "AdjOE": 105.0, "AdjDE": 105.0, "Tempo": 68.0,
            "eFG Off": 52.0, "eFG Def": 52.0, "TO Off": 18.0,
            "TO Def": 18.0, "ORB Rate": 30.0, "FT Rate": 30.0,
            "Rotation Adjustment": 0.0,
        }
        self.settings = {
            "home_court": 0.0, "pace_adjustment": 0.0,
            "matchup_margin_adjustment": 0.0, "matchup_total_adjustment": 0.0,
        }

    def test_better_home_shooting_defense_reduces_away_score(self):
        better = self.project(self.team, {**self.team, "eFG Def": 48.0}, self.settings)
        baseline = self.project(self.team, self.team, self.settings)
        worse = self.project(self.team, {**self.team, "eFG Def": 56.0}, self.settings)
        self.assertLess(better[0], baseline[0])
        self.assertLess(baseline[0], worse[0])
        self.assertEqual(better[1], baseline[1])

    def test_better_away_shooting_defense_reduces_home_score(self):
        better = self.project({**self.team, "eFG Def": 48.0}, self.team, self.settings)
        baseline = self.project(self.team, self.team, self.settings)
        worse = self.project({**self.team, "eFG Def": 56.0}, self.team, self.settings)
        self.assertLess(better[1], baseline[1])
        self.assertLess(baseline[1], worse[1])
        self.assertEqual(better[0], baseline[0])

    def test_better_opponent_adjusted_defense_reduces_scoring(self):
        baseline = self.project(self.team, self.team, self.settings)
        better_home = self.project(self.team, {**self.team, "AdjDE": 100.0}, self.settings)
        better_away = self.project({**self.team, "AdjDE": 100.0}, self.team, self.settings)
        self.assertLess(better_home[0], baseline[0])
        self.assertLess(better_away[1], baseline[1])

    def test_neutral_court_swapping_teams_swaps_scores(self):
        other = {**self.team, "AdjOE": 112.0, "AdjDE": 98.0, "Tempo": 73.0,
                 "eFG Off": 55.0, "eFG Def": 49.0, "TO Off": 16.0}
        first = self.project(self.team, other, self.settings)
        swapped = self.project(other, self.team, self.settings)
        self.assertEqual(first[0], swapped[1])
        self.assertEqual(first[1], swapped[0])
        self.assertEqual(first[2], -swapped[2])
        self.assertEqual(first[3:], swapped[3:])

    def test_home_court_changes_margin_without_changing_total(self):
        neutral = self.project(self.team, self.team, self.settings)
        home = self.project(self.team, self.team, {**self.settings, "home_court": 3.2})
        self.assertAlmostEqual(home[2] - neutral[2], 3.2)
        self.assertEqual(home[3], neutral[3])
        self.assertEqual(home[4], neutral[4])

    def test_rotation_loss_reduces_only_affected_teams_score(self):
        baseline = self.project(self.team, self.team, self.settings)
        injured = self.project({**self.team, "Rotation Adjustment": -4.0}, self.team, self.settings)
        self.assertAlmostEqual(baseline[0] - injured[0], 4.0)
        self.assertEqual(baseline[1], injured[1])

    def test_faster_teams_increase_possessions_and_total(self):
        slow = self.project({**self.team, "Tempo": 64.0}, {**self.team, "Tempo": 64.0}, self.settings)
        fast = self.project({**self.team, "Tempo": 72.0}, {**self.team, "Tempo": 72.0}, self.settings)
        self.assertGreater(fast[3], slow[3])
        self.assertGreater(fast[4], slow[4])

    def test_opponent_forcing_more_turnovers_reduces_scoring(self):
        baseline = self.project(self.team, self.team, self.settings)
        turnovers = self.project(self.team, {**self.team, "TO Def": 23.0}, self.settings)
        self.assertLess(turnovers[0], baseline[0])
        self.assertEqual(turnovers[1], baseline[1])

    def test_better_offensive_shooting_increases_own_score(self):
        baseline = self.project(self.team, self.team, self.settings)
        shooting = self.project({**self.team, "eFG Off": 56.0}, self.team, self.settings)
        self.assertGreater(shooting[0], baseline[0])
        self.assertEqual(shooting[1], baseline[1])

    def test_projection_does_not_mutate_inputs(self):
        team_before, settings_before = deepcopy(self.team), deepcopy(self.settings)
        self.project(self.team, self.team, self.settings)
        self.assertEqual(self.team, team_before)
        self.assertEqual(self.settings, settings_before)


if __name__ == "__main__":
    unittest.main()
