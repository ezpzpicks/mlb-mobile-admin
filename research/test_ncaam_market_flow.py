import unittest
from builders.ncaam_market_flow import flow_features, adjust_component, fit_nonnegative_ridge, combine_components, project_with_market_history
T0="2026-11-17T12:00:00+00:00";T1="2026-11-17T13:00:00+00:00";TIP="2026-11-17T14:00:00+00:00"
def hist(bets,money,line):
 return [{"snapshotTime":t,"betsPct":b,"moneyPct":m,"line":l} for t,b,m,l in zip([T0,T1],bets,money,line)]
def flow(h,market="Total"):
 return flow_features(h,as_of=T1,tipoff=TIP,market=market)
class DirectionalFlow(unittest.TestCase):
 def test_all_eight_approved_patterns(self):
  cases=[("Over divergence",[50,26],[50,66],[151.5,149.5],"Total",1),
   ("Fading Over money",[12,26],[95,66],[151.5,149.5],"Total",-1),
   ("All Over",[45,60],[50,75],[149.5,152.5],"Total",1),
   ("All Under",[70,55],[80,60],[152.5,149.5],"Total",-1),
   ("Money Under line flat",[40,60],[70,45],[150.5,150.5],"Total",-1),
   ("Static gap",[20,20],[80,80],[150.5,150.5],"Total",0),
   ("Underdog support",[45,35],[50,75],[6.5,4.5],"Spread",1),
   ("Favorite weakening",[20,40],[90,65],[-6.5,-4.5],"Spread",-1)]
  for name,b,m,l,market,direction in cases:
   with self.subTest(name=name):
    f=flow(hist(b,m,l),market);a=adjust_component(150 if market=="Total" else 3,f,[1,1,1,1])
    self.assertEqual(f["direction"],direction)
    self.assertEqual(1 if a["adjustment"]>0 else -1 if a["adjustment"]<0 else 0,direction)
 def test_uconn_arizona_cannot_receive_over_boost(self):
  f=flow(hist([82,26],[85,66],[148.5,146.5]))
  for weights in [[1,1,1,1],[0,0,1,0],[2,3,4,5],[0,0,0,0]]:
   with self.subTest(weights=weights):self.assertLessEqual(adjust_component(149.05137374727326,f,weights)["projection"],149.05137374727326)
 def test_endpoint_levels_do_not_override_direction(self):
  a=flow(hist([50,26],[50,66],[151.5,149.5]));b=flow(hist([12,26],[95,66],[151.5,149.5]))
  self.assertEqual(a["direction"],1);self.assertEqual(b["direction"],-1)
 def test_earlier_price_change_does_not_confirm_later_money(self):
  early=[{"snapshotTime":T0,"betsPct":50,"moneyPct":50,"line":150},
   {"snapshotTime":"2026-11-17T12:30:00+00:00","betsPct":50,"moneyPct":50,"line":152},
   {"snapshotTime":T1,"betsPct":50,"moneyPct":70,"line":152}]
  same=hist([50,50],[50,70],[150,152])
  self.assertEqual(flow(early)["features"][3],0);self.assertGreater(flow(same)["features"][3],0)
 def test_confirmation_increases_support(self):
  plain=adjust_component(150,flow(hist([50,30],[50,70],[150,150])),[1,1,1,1])["adjustment"]
  confirmed=adjust_component(150,flow(hist([50,30],[50,70],[150,152])),[1,1,1,1])["adjustment"]
  opposed=adjust_component(150,flow(hist([50,30],[50,70],[150,148])),[1,1,1,1])["adjustment"]
  self.assertGreater(confirmed,plain);self.assertGreater(plain,opposed);self.assertGreater(opposed,0)
 def test_future_observations_are_excluded(self):
  h=hist([50,26],[50,66],[151.5,149.5])
  future={"snapshotTime":"2026-11-17T13:30:00+00:00","betsPct":99,"moneyPct":1,"line":120}
  self.assertEqual(flow(h)["features"],flow(h+[future])["features"])
 def test_duplicate_observation_is_not_an_extra_vote(self):
  h=hist([50,26],[50,66],[151.5,149.5])
  self.assertEqual(flow(h)["features"],flow(h+[dict(h[-1])])["features"])
 def test_conflicting_duplicate_is_rejected(self):
  h=hist([50,26],[50,66],[151.5,149.5]);bad={**h[-1],"moneyPct":30}
  with self.assertRaises(ValueError):flow(h+[bad])
 def test_spread_team_orientation_symmetry(self):
  h=hist([45,35],[50,75],[6.5,4.5]);other=[{**p,"betsPct":100-p["betsPct"],"moneyPct":100-p["moneyPct"],"line":-p["line"]} for p in h]
  a=flow(h,"Spread")["features"];b=flow(other,"Spread")["features"]
  for x,y in zip(a,b):self.assertAlmostEqual(x,-y)
 def test_total_over_under_orientation_symmetry(self):
  h=hist([45,60],[50,75],[149.5,152.5]);other=[{**p,"betsPct":100-p["betsPct"],"moneyPct":100-p["moneyPct"],"line":-p["line"]} for p in h]
  for x,y in zip(flow(h)["features"],flow(other)["features"]):self.assertAlmostEqual(x,-y)
 def test_history_and_missing_data_fail_closed(self):
  h=hist([50,26],[50,66],[151.5,149.5])
  self.assertEqual(flow(h[:1])["status"],"insufficient_history")
  for value in [None,"",float("nan"),True,101,-1]:
   with self.subTest(value=value):
    with self.assertRaises(ValueError):flow([{**h[0],"moneyPct":value},h[1]])
 def test_post_tipoff_rejected(self):
  with self.assertRaises(ValueError):flow_features(hist([50,26],[50,66],[151.5,149.5]),as_of=TIP,tipoff=TIP,market="Total")
 def test_negative_weight_is_rejected(self):
  with self.assertRaises(ValueError):adjust_component(150,flow(hist([50,26],[50,66],[151.5,149.5])),[1,-1,1,1])
 def test_fitted_weights_cannot_reverse_approved_direction(self):
  rows=[{"features":[-1,-2,-3,-4],"actual":170,"baseline":150} for _ in range(20)]
  fit=fit_nonnegative_ridge(rows)
  self.assertEqual(fit["coefficients"],[0,0,0,0])
 def test_positive_evidence_can_fit_positive_weight(self):
  rows=[{"features":[1,0,0,0],"actual":155,"baseline":150} for _ in range(100)]
  fit=fit_nonnegative_ridge(rows);self.assertGreater(fit["coefficients"][0],0)
 def test_joint_scores_preserve_components(self):
  a=combine_components(7.2,153.8)
  self.assertAlmostEqual(a["home"]-a["away"],7.2);self.assertAlmostEqual(a["home"]+a["away"],153.8)
 def test_candidate_combines_adjustments_once(self):
  out=project_with_market_history(baseline_margin=3,baseline_total=150,
   spread_history=hist([45,35],[50,75],[6.5,4.5]),total_history=hist([50,26],[50,66],[151.5,149.5]),
   coefficients_by_market={"Spread":[1,1,1,1],"Total":[1,1,1,1]},as_of=T1,tipoff=TIP)
  self.assertAlmostEqual(out["home"]-out["away"],out["market_models"]["spread"]["projection"])
  self.assertAlmostEqual(out["home"]+out["away"],out["market_models"]["total"]["projection"])
  self.assertAlmostEqual(out["total"],150+out["market_models"]["total"]["adjustment"])
  self.assertFalse(out["official_pick_eligible"])
 def test_candidate_preserves_baseline_without_new_flow(self):
  out=project_with_market_history(baseline_margin=3,baseline_total=150,
   spread_history=hist([20,20],[80,80],[6.5,6.5]),total_history=hist([20,20],[80,80],[151.5,151.5]),
   coefficients_by_market={"Spread":[1,1,1,1],"Total":[1,1,1,1]},as_of=T1,tipoff=TIP)
  self.assertEqual(out["margin"],3);self.assertEqual(out["total"],150)
 def test_candidate_rejects_mixed_market_timestamps(self):
  total=hist([50,26],[50,66],[151.5,149.5]);total[-1]["snapshotTime"]="2026-11-17T12:55:00+00:00"
  with self.assertRaises(ValueError):
   project_with_market_history(baseline_margin=3,baseline_total=150,
    spread_history=hist([45,35],[50,75],[6.5,4.5]),total_history=total,
    coefficients_by_market={"Spread":[1,1,1,1],"Total":[1,1,1,1]},as_of=T1,tipoff=TIP)

if __name__=="__main__":unittest.main()
