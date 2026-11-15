"""Point-edge tiers, symmetry, eligibility and historical-policy isolation."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from builders.cfb_matchup_grades import grade_matchup,load_policy,apply_price_and_direction
from research.test_cfb_matchup_grades import factors,AS_OF,KICKOFF


def grade(*,edge=6.,market="spread",under=False,f=None,**kwargs):
    values=dict(projected_margin=10.,projected_total=50.+edge*(-1 if under else 1),
        home_line=edge-10.,total_line=50.,as_of=AS_OF,kickoff=KICKOFF)
    values.update(kwargs)
    return grade_matchup(f or factors(),**values)[market]


class EdgeGradeTests(unittest.TestCase):
    def test_exact_boundaries_apply_to_both_markets_and_total_directions(self):
        for market in ("spread","total"):
            for edge,expected in ((0.,"Research"),(2.999,"Research"),(3.,"B"),(5.999,"B"),(6.,"A"),(12.,"A")):
                for under in (False,True):
                    with self.subTest(market=market,edge=edge,under=under):
                        g=grade(edge=edge,market=market,under=under)
                        self.assertEqual(g["grade"],expected)
                        self.assertEqual(g["edge_grade"],expected)

    def test_strong_profile_cannot_make_a_tiny_edge_an_a(self):
        self.assertEqual(grade(edge=.1)["grade"],"Research")
        self.assertEqual(grade(edge=.1,market="total")["grade"],"Research")

    def test_weaker_fast_underdog_can_have_an_a_at_a_good_number(self):
        f=factors();f["away"].update(offense=10.,defense=40.,tempo=15.)
        f["home"].update(offense=45.,defense=10.,tempo=15.)
        g=grade(f=f,projected_margin=1.,home_line=-8.)
        self.assertEqual((g["grade"],g["side"],g["role"],g["edge"]),("A","away","Underdog",7.))

    def test_factor_agreement_does_not_add_a_second_grade_bonus(self):
        f=factors();original=grade(edge=4.,f=f)
        f["away"].update(offense=45.,defense=10.,tempo=8.)
        f["home"].update(offense=10.,defense=45.,tempo=16.)
        changed=grade(edge=4.,f=f)
        self.assertEqual((changed["grade"],changed["edge"]),(original["grade"],original["edge"]))

    def test_favorite_dog_and_over_under_use_the_same_point_scale(self):
        fav=grade(projected_margin=10.,home_line=-4.)
        dog=grade(projected_margin=-2.,home_line=8.)
        self.assertEqual((fav["role"],dog["role"]),("Favorite","Underdog"))
        for g in (fav,dog,grade(market="total"),grade(market="total",under=True)):
            self.assertEqual((g["grade"],g["edge"]),("A",6.))

    def test_changed_line_promotes_downgrades_and_can_change_the_cover_side(self):
        self.assertEqual(grade(projected_margin=10.,home_line=-4.)["grade"],"A")
        self.assertEqual(grade(projected_margin=10.,home_line=-6.)["grade"],"B")
        self.assertEqual(grade(projected_margin=10.,home_line=-8.)["grade"],"Research")
        opposite=grade(projected_margin=10.,home_line=-16.)
        self.assertEqual((opposite["side"],opposite["role"],opposite["grade"]),("away","Underdog","A"))

    def test_large_spread_keeps_its_edge_grade_but_remains_projection_only(self):
        g=grade(projected_margin=30.,home_line=-21.)
        self.assertEqual((g["edge_grade"],g["grade"],g["eligible"]),("A","Research",False))
        self.assertTrue(any("20+" in x for x in g["confidence_flags"]))

    def test_price_and_market_checks_are_separate_from_the_point_edge_grade(self):
        g=grade();p={"pick":"Home -4","price_edge":.1,"ev":.1}
        self.assertTrue(apply_price_and_direction(g,p,"Home -4",True)["eligible"])
        for price in (dict(p,ev=0),dict(p,price_edge=0),dict(p,pick="Away +4")):
            result=apply_price_and_direction(g,price,"Home -4",True)
            self.assertEqual((result["edge_grade"],result["grade"],result["eligible"]),("A","Research",False))
        self.assertFalse(apply_price_and_direction(g,p,"Home -4",False)["eligible"])
        self.assertEqual(apply_price_and_direction(g,p,"Home -4",False)["edge_grade"],"Research")

    def test_outcomes_do_not_affect_a_grade_and_inputs_are_not_mutated(self):
        f=factors();original=deepcopy(f);before=grade(f=f)
        f.update(result="Win",actual_home=99,actual_away=0)
        self.assertEqual(grade(f=f),before)
        self.assertEqual(f["away"],original["away"])

    def test_invalid_or_reversed_cutoffs_fail_policy_loading(self):
        for a,b in ((3.,6.),(6.,0.),(True,3.),(float("nan"),3.)):
            with TemporaryDirectory() as directory:
                policy=load_policy();policy["thresholds"].update(spread_a_points=a,spread_b_points=b)
                path=Path(directory)/"policy.json";path.write_text(json.dumps(policy))
                with patch("builders.cfb_matchup_grades.POLICY_PATH",path),self.assertRaises(ValueError):load_policy()


if __name__=="__main__":unittest.main()
