from copy import deepcopy
import json
from pathlib import Path
import unittest

from builders.ncaam_matchup_grades import apply_projection_agreement, grade_matchup, load_policy as load_total_policy, stamp
from builders.ncaam_selection_review import apply_total_review, validate_review
from builders.ncaam_spread_grades import grade_saved_spread, load_spread_policy

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'research/fixtures/ncaam_selection_preferences_2026_11_13.json').read_text())['cases']


def load_policy(path=None):
    """Keep the former preference cases explicitly enabled for historical replay."""
    p = load_total_policy() if path is None else load_total_policy(path)
    if path is None:
        p['selection_review']['enabled'] = True
    return p


def spread(forecast, policy=None):
    if policy is None:
        policy = load_spread_policy()
        policy['selection_review']['enabled'] = True
    return grade_saved_spread(forecast, [forecast['benchmark']], policy)


def total(forecast, policy=None):
    p = load_policy() if policy is None else policy
    g = grade_matchup(forecast['away_rating'], forecast['home_rating'], forecast['market']['total'],
        forecast['benchmark'], forecast['reference'], as_of=forecast['lockedAt'], tipoff=forecast['tipoff'],
        quote_observed_at=forecast['market']['as_of'], season=forecast['home_court']['season'], policy=p)
    return apply_total_review(apply_projection_agreement(g, forecast['total'], p), forecast, p)


class SelectionReviewTests(unittest.TestCase):
    def test_live_policies_leave_preference_decisions_to_manual_review(self):
        totals, spreads = load_total_policy(), load_spread_policy()
        self.assertFalse(totals['selection_review']['enabled'])
        self.assertFalse(spreads['selection_review']['enabled'])
        for name in ('rutgers', 'pitt', 'purdue', 'charlotte', 'marist', 'elon'):
            with self.subTest(spread=name):
                self.assertIn(spread(CASES[name], spreads)['grade'], ('A', 'B'))
        for name in ('tennessee', 'fordham', 'drake'):
            with self.subTest(total=name):
                self.assertIn(total(CASES[name], totals)['grade'], ('A', 'B'))
        # Disabling preferences does not bypass projection agreement.
        opposed = deepcopy(CASES['tennessee'])
        opposed['total'] = opposed['market']['total'] + 1
        self.assertEqual(total(opposed, totals)['grade'], 'Research')

    def test_reviewed_spread_exclusions_and_retained_candidates(self):
        expected = {'rutgers': 'Research', 'pitt': 'Research', 'purdue': 'Research', 'charlotte': 'Research',
                    'marist': 'Research', 'elon': 'Research', 'columbia': 'B', 'fiu': 'B'}
        for name, grade in expected.items():
            with self.subTest(name=name):
                g = spread(CASES[name])
                self.assertEqual(g['grade'], grade)
                self.assertEqual(bool(g['selection_review']['blocked_reasons']), grade == 'Research')

    def test_reviewed_total_exclusions_and_retained_candidates(self):
        for name, grade in [('miami', 'B'), ('fsu', 'A'), ('tennessee', 'Research'), ('fordham', 'Research'), ('drake', 'Research')]:
            with self.subTest(name=name):
                self.assertEqual(total(CASES[name])['grade'], grade)

    def test_large_favorite_can_qualify_with_stronger_defense(self):
        f = deepcopy(CASES['rutgers'])
        b = f['benchmark']['metrics']['defense']
        f['home_rating']['defense'] = b['mean'] - 1.2 * b['sd']
        self.assertEqual(spread(f)['grade'], 'A')

    def test_small_dog_tempo_fit_and_more_points_are_price_sensitive(self):
        f = deepcopy(CASES['pitt'])
        f['home_rating']['tempo'] = f['away_rating']['tempo']
        self.assertEqual(spread(f)['grade'], 'B')
        f = deepcopy(CASES['marist'])
        f['market']['home_spread'] = -5
        self.assertEqual(spread(f)['grade'], 'B')
        self.assertGreaterEqual(spread(f)['edge'], 1)
        # Neither the faster-underdog preference nor a better quote bypasses
        # the original projection edge window.
        f = deepcopy(CASES['fiu']); f['fair_home_spread'] = -2.5
        self.assertEqual(spread(f)['grade'], 'Research')

    def test_weak_absolute_favorite_has_an_inclusive_eight_point_cap(self):
        for points, expected in [(8, 'B'), (8.01, 'Research')]:
            f = deepcopy(CASES['columbia'])
            f['market']['home_spread'] = points
            f['fair_home_spread'] = points + 2
            self.assertEqual(spread(f)['grade'], expected)

    def test_high_total_execution_filter_is_price_sensitive(self):
        f = deepcopy(CASES['drake'])
        f['market']['total'] = 149.5
        self.assertIn(total(f)['grade'], ('A', 'B'))
        f['market']['total'] = 150
        self.assertEqual(total(f)['grade'], 'Research')

    def test_slow_favorite_exception_does_not_override_projection_agreement(self):
        f = CASES['purdue']; p = load_policy()
        assessment = apply_total_review({'side': 'Under', 'grade': 'B', 'line': f['market']['total'], 'z': f['z']}, f, p)
        self.assertIn('slow-strong-defense-heavy-favorite', assessment['selection_review']['supporting_paths'])
        reversed_favorite = deepcopy(f)
        reversed_favorite['market']['home_spread'] *= -1
        rejected = apply_total_review({'side': 'Under', 'grade': 'B', 'line': f['market']['total'], 'z': f['z']}, reversed_favorite, p)
        self.assertEqual(rejected['grade'], 'Research')
        gate = apply_projection_agreement({'side': 'Under', 'grade': 'B', 'line': f['market']['total']}, f['total'], p)
        self.assertEqual(gate['grade'], 'Research')
        self.assertGreater(f['total'], f['market']['total'])

    def test_outcomes_never_change_selection_or_exclusions(self):
        for name in CASES:
            f = deepcopy(CASES[name])
            f.update(totalResult='WIN', spreadResult='LOSS', actual_home_score=999, actual_away_score=0)
            with self.subTest(name=name):
                self.assertEqual(spread(f), spread(CASES[name]))
                self.assertEqual(total(f), total(CASES[name]))

    def test_original_inputs_are_not_rewritten_and_legacy_policy_is_replayable(self):
        before = deepcopy(CASES['rutgers'])
        spread(before); total(before)
        self.assertEqual(before, CASES['rutgers'])
        legacy = load_spread_policy(ROOT / 'config/ncaam_spread_grades_v1.json')
        self.assertEqual(spread(before, legacy)['grade'], 'B')
        legacy_total = load_policy(ROOT / 'config/ncaam_matchup_grades_v2.json')
        self.assertEqual(total(CASES['tennessee'], legacy_total)['grade'], 'B')

    def test_fixtures_use_only_available_pregame_inputs(self):
        for f in CASES.values():
            self.assertLess(stamp(f['lockedAt']), stamp(f['tipoff']))
            self.assertLessEqual(stamp(f['market']['as_of']), stamp(f['lockedAt']))
            self.assertLessEqual(stamp(f['benchmark']['observed_at']), stamp(f['lockedAt']))
            self.assertFalse(any('result' in k.lower() or 'score' in k.lower() for k in f))

    def test_invalid_review_thresholds_are_rejected(self):
        for value in [True, float('nan'), 0, -1]:
            p = load_policy(); p['selection_review']['large_favorite_points'] = value
            with self.assertRaises(ValueError): validate_review(p)


if __name__ == '__main__':
    unittest.main()
