"""The forward deployment uses the approved balanced scorer and has no grades."""
import json
from pathlib import Path
import unittest
from builders.ncaam_market_flow import POLICY, PROFILES, flow_features, adjustment

ROOT=Path(__file__).resolve().parents[1]

class LiveMarketTrackingPolicy(unittest.TestCase):
    def test_live_configuration_freezes_balanced_weights_and_defers_grades(self):
        p=json.loads((ROOT/'config/ncaam_market_tracking_live.json').read_text())
        self.assertEqual(p['flow_policy'],POLICY)
        self.assertEqual(p['profile'],'balanced')
        self.assertEqual(p['weights'],PROFILES['balanced']['weights'])
        self.assertEqual(p['spread_cap'],4)
        self.assertEqual(p['total_cap'],6)
        self.assertEqual(p['grade'],'Ungraded')
        self.assertFalse(p['official_pick_eligible'])
        self.assertFalse(p['regrade_history'])

    def test_frozen_cross_language_examples_match_the_approved_python_scorer(self):
        fixture=json.loads((ROOT/'research/fixtures/ncaam_market_tracking_flow.json').read_text())
        self.assertEqual(len(fixture['cases']),90)
        self.assertEqual(fixture['parameters'],json.loads((ROOT/'config/ncaam_market_tracking_live.json').read_text()))
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                f=flow_features(case['history'],as_of=case['asOf'],tipoff=case['tipoff'],market=case['market'])
                actual=adjustment(case['baseline'],f,'balanced')
                self.assertEqual(f['features'],case['expected']['features'])
                self.assertAlmostEqual(actual['projection'],case['expected']['projection'])
                self.assertAlmostEqual(actual['adjustment'],case['expected']['adjustment'])

if __name__=='__main__':unittest.main()
