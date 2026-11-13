"""Source identity, frozen decisions, pricing and numeric-model preservation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from builders.cfb_matchup_grades import grade_matchup, install_matchup_grading, load_policy, apply_price_and_direction
from builders.cfb_sp_ratings import parse_population, team_key, usable

NOW = datetime.now(timezone.utc)
AS_OF = NOW.isoformat()
KICKOFF = (NOW + timedelta(hours=4)).isoformat()


def factors():
    return {"away": {"team": "Away", "offense": 38., "defense": 35., "tempo": 13.5},
        "home": {"team": "Home", "offense": 32., "defense": 31., "tempo": 13.},
        "observed_at": (NOW - timedelta(minutes=5)).isoformat(), "season": 2026,
        "population_sha256": "a" * 64,
        "national": {"offense": {"mean": 27., "sd": 7., "teams": 138},
            "defense": {"mean": 27., "sd": 7., "teams": 138},
            "tempo": {"mean": 12., "sd": 1., "teams": 138}}}


def grade(f=None, **kwargs):
    values = dict(projected_margin=3., projected_total=58., home_line=-5.5,
                  total_line=54., as_of=AS_OF, kickoff=KICKOFF)
    values.update(kwargs)
    return grade_matchup(f or factors(), **values)


def html(count=138, year=2026):
    rows = ''.join(f'<tr><td>{i+1}. Team {i} (4-2)</td><td>3.2</td><td>{25+i/50:.2f} (1)</td><td>{28-i/100:.2f} (2)</td><td>0.1 (3)</td></tr>' for i in range(count))
    return f'<script type="application/ld+json">{{"temporalCoverage":"{year}"}}</script><table><tr><th>Team</th><th>SP+ Rating</th><th>Offense</th><th>Defense</th><th>Special Teams</th></tr>{rows}</table>'


class MatchupTests(unittest.TestCase):
    def test_published_population_rejects_partial_wrong_year_duplicate_or_malformed_data(self):
        self.assertEqual(len(parse_population(html(), 2026, AS_OF)['teams']), 138)
        for source in [html(10), html(year=2025), html().replace('Team 1 (', 'Team 0 ('), html().replace('25.00 (1)', 'N/A')]:
            with self.assertRaises(ValueError):
                parse_population(source, 2026, AS_OF)
        self.assertNotEqual(team_key('Miami'), team_key('Miami (OH)'))
        self.assertEqual(team_key('Texas AandM'), team_key('Texas A&M'))

    def test_source_cannot_be_backdated_or_reused_for_historical_decisions(self):
        p = parse_population(html(), 2026, AS_OF)
        self.assertFalse(usable(p, 2026, (NOW-timedelta(seconds=1)).isoformat()))
        self.assertFalse(usable(p, 2025, AS_OF))
        self.assertFalse(usable(p, 2026, (NOW+timedelta(days=9)).isoformat()))
        for field, value in [('observed_at', KICKOFF), ('observed_at', (NOW-timedelta(days=9)).isoformat())]:
            f=factors();f[field]=value
            with self.assertRaises(ValueError): grade(f)
        with self.assertRaises(ValueError): grade(as_of=KICKOFF)
        with self.assertRaises(ValueError): grade(as_of='2026-11-13T12:00:00')

    def test_projection_direction_and_three_factors_control_totals_not_gap_size(self):
        self.assertEqual(grade()['total']['grade'], 'A')
        self.assertEqual(grade(projected_total=54.01)['total']['grade'], 'A')
        self.assertEqual(grade(projected_total=52)['total']['grade'], 'Research')
        self.assertEqual(grade(projected_total=54)['total']['grade'], 'Research')
        f=factors();f['away']['tempo']=11;f['home']['tempo']=11
        self.assertEqual(grade(f, projected_total=100)['total']['grade'], 'Research')
        for side in ('away','home'):
            f[side].update(offense=20, defense=20)
        g=grade(f, projected_total=50)['total']
        self.assertEqual((g['grade'],g['side']), ('A','Under'))

    def test_losing_underdog_can_qualify_for_a_cover_and_large_spreads_stay_excluded(self):
        f=factors();f['away'].update(offense=37,defense=17,tempo=11.2);f['home'].update(offense=28,defense=27,tempo=11.5)
        g=grade(f)['spread']
        self.assertEqual((g['grade'],g['side'],g['role'],g['line']), ('A','away','Underdog',5.5))
        self.assertEqual(grade(f,home_line=-20)['spread']['grade'],'Research')
        self.assertEqual(grade(f,home_line=-3)['spread']['grade'],'Research')
        f['away']['offense']=28
        self.assertEqual(grade(f)['spread']['grade'],'B')
        f['away']['tempo']=14;f['home']['tempo']=14
        self.assertEqual(grade(f)['spread']['grade'],'Research')

    def test_results_are_not_inputs_and_price_selected_opposition_cannot_get_a_grade(self):
        f=factors();before=deepcopy(f);g=grade(f)['total']
        f.update(result='Win',actualTotal=99)
        self.assertEqual(grade(f)['total'],g)
        self.assertEqual(before['away'],f['away'])
        market={'pick':'Over 54','price_edge':.1,'ev':.1}
        self.assertEqual(apply_price_and_direction(g,market,'Over 54',True)['grade'],'A')
        for p in [dict(market,pick='Under 54'),dict(market,ev=0),dict(market,price_edge=0)]:
            self.assertEqual(apply_price_and_direction(g,p,'Over 54',True)['grade'],'Research')
        self.assertEqual(apply_price_and_direction(g,market,'Over 54',False)['grade'],'Research')

    def test_builder_capture_changes_grades_without_changing_projection_or_simulation(self):
        result={'game':{'Season':2026,'Week':12,'Game Time':KICKOFF,'Home Spread':-5.5,'Total':54},
            'projection':{'total':58.,'margin':3.},'simulation':{'away_mean':27.5,'home_mean':30.5},
            'spread':{'pick':'Away +5.5','price_edge':.1,'ev':.1,'grade':'A Spread'},
            'total_market':{'pick':'Over 54','price_edge':.1,'ev':.1,'grade':'A Total'},
            'market_availability':{'spread':True,'total':True}}
        original=deepcopy(result)
        builder=SimpleNamespace(SLATE_COLUMNS=['Spread Grade','Total Grade'],TRACKER_COLUMNS=['Grade'],
            evaluate_game=lambda *a,**kw:deepcopy(result),
            slate_row=lambda r:pd.DataFrame([{'Spread Grade':r['spread']['grade'],'Total Grade':r['total_market']['grade']}]),
            tracker_rows=lambda r,*a,**kw:pd.DataFrame([{'Grade':r['total_market']['grade']}]))
        install_matchup_grading(builder);install_matchup_grading(builder)
        with patch('builders.cfb_matchup_grades.load_population',return_value={}), patch('builders.cfb_matchup_grades.collect_factors',return_value=factors()):
            captured=builder.evaluate_game()
        self.assertEqual(captured['simulation'],original['simulation'])
        self.assertEqual(captured['projection']['total'],original['projection']['total'])
        row=builder.slate_row(captured).iloc[0]
        self.assertEqual(json.loads(row['Matchup Total JSON'])['grade'],'A')
        self.assertEqual(json.loads(row['Matchup Factors JSON'])['population_sha256'],'a'*64)
        self.assertEqual(builder.tracker_rows(captured).iloc[0]['Grading Policy Version'],load_policy()['version'])
        with patch('builders.cfb_matchup_grades.load_population',side_effect=ValueError('no published source')):
            unavailable=builder.evaluate_game()
        self.assertEqual(unavailable['spread']['grade'],'No Play')
        self.assertEqual(unavailable['total_market']['grade'],'No Play')
        self.assertEqual(unavailable['simulation'],original['simulation'])


if __name__ == '__main__':
    unittest.main()
