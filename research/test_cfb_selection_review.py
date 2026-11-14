from copy import deepcopy
import json
from pathlib import Path
import unittest

from builders.cfb_matchup_grades import grade_matchup, load_policy, apply_price_and_direction
from builders.cfb_selection_review import validate_review

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'research/fixtures/ncaaf_selection_preferences_2026_11_13.json').read_text())['cases']


def grade(name, *, line=None, total=None, policy=None):
    c = CASES[name]; values = deepcopy(c['inputs'])
    if line is not None: values['home_line'] = line
    if total is not None: values['total_line'] = total
    if policy is None:
        policy = load_policy()
        policy['selection_review']['enabled'] = True
    return grade_matchup(c['factors'], **values, policy=policy)


class SelectionReviewTests(unittest.TestCase):
    def test_live_policy_restores_candidates_for_manual_review(self):
        policy = load_policy()
        self.assertFalse(policy['selection_review']['enabled'])
        restored = grade('pitt', policy=policy)
        self.assertEqual(restored['spread']['grade'], 'B')
        self.assertEqual(restored['total']['grade'], 'B')
        self.assertNotIn('selection_review', restored['total'])
        self.assertEqual(grade('houston', line=12, policy=policy)['spread']['grade'], 'B')
        # The original market-size limit and projection direction still apply.
        self.assertEqual(grade('houston', line=20, policy=policy)['spread']['grade'], 'Research')
        self.assertNotEqual(grade('ucla', total=40, policy=policy)['total']['side'], 'Under')

    def test_pitt_favorite_and_over_are_excluded_without_flipping_the_selection(self):
        g = grade('pitt')
        self.assertEqual((g['spread']['grade'], g['spread']['side']), ('Research', 'home'))
        self.assertEqual((g['total']['grade'], g['total']['side']), ('Research', 'Over'))
        self.assertEqual(g['spread']['selection_review']['base_grade'], 'B')
        self.assertTrue(g['total']['selection_review']['blocked_reasons'])

    def test_illinois_ucla_under_retains_the_slow_attack_defense_path(self):
        g = grade('ucla')['total']
        self.assertEqual((g['grade'], g['side']), ('B', 'Under'))
        self.assertIn('slow-underdog-attack-versus-good-favorite-defense-with-moderate-favorite-tempo',
                      g['selection_review']['supporting_paths'])
        self.assertEqual(g['projected_total'], CASES['ucla']['inputs']['projected_total'])

    def test_houston_profile_has_an_inclusive_eleven_and_a_half_point_ceiling(self):
        for line, expected in [(8.5, 'B'), (11.5, 'B'), (11.5001, 'Research'), (12, 'Research')]:
            with self.subTest(line=line):
                g = grade('houston', line=line)['spread']
                self.assertEqual(g['grade'], expected)
                self.assertIn('strong-attack-versus-weak-attack-with-average-defenses', g['selection_review']['supporting_paths'])
        self.assertTrue(grade('houston')['spread']['selection_review']['notes'])

    def test_market_price_and_projection_requirements_remain_prerequisites(self):
        c = deepcopy(CASES['houston'])
        c['inputs']['projected_margin'] = -8
        g = grade_matchup(c['factors'], **c['inputs'])['spread']
        self.assertNotEqual(g['side'], 'away')
        g = grade('houston')['spread']
        self.assertEqual(apply_price_and_direction(g, {'pick':'Houston -8.5','price_edge':.01,'ev':0}, 'Houston -8.5', True)['grade'], 'Research')
        # A preferred Under pattern cannot reverse a projection above the total.
        self.assertNotEqual(grade('ucla', total=40)['total']['side'], 'Under')

    def test_legacy_rules_remain_replayable_and_outcomes_do_not_affect_new_rules(self):
        legacy = json.loads((ROOT / 'config/ncaaf_matchup_grades_v1.json').read_text())
        self.assertEqual(grade('pitt', policy=legacy)['spread']['grade'], 'B')
        self.assertEqual(grade('pitt', policy=legacy)['total']['grade'], 'B')
        for c in CASES.values():
            original = deepcopy(c['factors']); changed = deepcopy(original)
            changed.update(actual_home_score=0, actual_away_score=99, result='Win')
            self.assertEqual(grade_matchup(original, **c['inputs']), grade_matchup(changed, **c['inputs']))
            self.assertEqual(original, c['factors'])

    def test_invalid_preferences_are_rejected(self):
        for value in [None, True, float('nan'), 0, -1]:
            p = load_policy(); p['selection_review']['minimum_attacking_pair_z'] = value
            with self.assertRaises(ValueError): validate_review(p)


if __name__ == '__main__':
    unittest.main()
