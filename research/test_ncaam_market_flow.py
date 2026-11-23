import itertools
import json
from pathlib import Path
import unittest
from builders.ncaam_market_flow import (
    PROFILES, adjustment, flow_features, project_with_market_history, MarketProjectionState)
T0="2026-11-17T12:00:00Z";T1="2026-11-17T13:00:00Z";T2="2026-11-17T14:00:00Z";TIP="2026-11-17T15:00:00Z"

def history(bets,money,lines,times=None):
    times=times or [T0,T1]
    return [{"snapshotTime":t,"betsPct":b,"moneyPct":m,"line":l}
            for t,b,m,l in zip(times,bets,money,lines)]

def flow(h,market="Total",clock=T1):
    return flow_features(h,as_of=clock,tipoff=TIP,market=market)

def effect(h,market="Total",profile="balanced",clock=T1):
    return adjustment(150 if market=="Total" else 3,flow(h,market,clock),profile)["adjustment"]

def paired(t,line=150.,spread=-3.,money=50.,bets=50.):
    return {"snapshotTime":t,"home_spread":spread,"total":line,
            "home_bets_pct":bets,"home_money_pct":money,"over_bets_pct":bets,"over_money_pct":money}

class AdaptiveMarketFlow(unittest.TestCase):
    def test_declared_profiles_match_executed_parameters(self):
        spec=json.loads((Path(__file__).resolve().parents[1]/'config/ncaam_market_flow_research.json').read_text())
        for name,p in PROFILES.items():
            expected=spec['sizing']['profiles'][name]
            self.assertEqual(p['weights'],expected['weights'])
            self.assertEqual(p['spread_cap'],expected['spread_cap'])
            self.assertEqual(p['total_cap'],expected['total_cap'])
            self.assertEqual(p['minimum_adjustment'],spec['sizing']['minimum_nonzero_adjustment_points'])

    def test_eight_approved_examples_all_profiles(self):
        cases=[([50,26],[50,66],[151.5,149.5],"Total",1),
          ([12,26],[95,66],[151.5,149.5],"Total",-1),
          ([45,60],[50,75],[149.5,152.5],"Total",1),
          ([70,55],[80,60],[152.5,149.5],"Total",-1),
          ([40,60],[70,45],[150.5,150.5],"Total",-1),
          ([20,20],[80,80],[150.5,150.5],"Total",0),
          ([45,35],[50,75],[6.5,4.5],"Spread",1),
          ([20,40],[90,65],[-6.5,-4.5],"Spread",-1)]
        for p in PROFILES:
            for b,m,l,market,direction in cases:
                with self.subTest(profile=p,market=market,bets=b,money=m):
                    value=effect(history(b,m,l),market,p)
                    self.assertEqual(1 if value>0 else -1 if value<0 else 0,direction)

    def test_all_27_basic_movement_states_are_processed(self):
        for p in PROFILES:
            active=0
            for money,bets,line in itertools.product([-1,0,1],repeat=3):
                h=history([50,50+bets*10],[50,50+money*10],[150,150+line])
                f=flow(h);value=effect(h,profile=p)
                self.assertEqual(len(f["features"]),6)
                self.assertEqual(value==0,not any([money,bets,line]))
                active+=value!=0
            self.assertEqual(active,26)

    def test_line_only_total_rise(self):
        self.assertEqual(effect(history([50,50],[50,50],[150,152])),2)

    def test_line_only_total_fall(self):
        self.assertEqual(effect(history([50,50],[50,50],[150,148])),-2)

    def test_line_only_supports_underdog(self):
        self.assertGreater(effect(history([50,50],[50,50],[6.5,4.5]),"Spread"),0)

    def test_line_only_weakens_favorite(self):
        self.assertLess(effect(history([50,50],[50,50],[-6.5,-4.5]),"Spread"),0)

    def test_line_works_without_split_data(self):
        self.assertEqual(effect(history([None,None],[None,None],[150,152])),2)

    def test_missing_cash_is_not_fabricated_flat_cash(self):
        self.assertEqual(effect(history([50,70],[None,None],[150,150])),0)

    def test_observed_flat_cash_with_more_tickets(self):
        self.assertLess(effect(history([50,70],[50,50],[150,150])),0)

    def test_zero_percent_cash_is_valid_observed_data(self):
        self.assertLess(effect(history([10,20],[0,0],[150,150])),0)

    def test_degree_of_price_movement_is_progressive(self):
        values=[effect(history([50,50],[50,50],[150,150+x])) for x in [1,2,3]]
        self.assertEqual(values,[1,2,3])

    def test_degree_of_cash_movement_is_progressive(self):
        values=[effect(history([50,50],[50,50+x],[150,150])) for x in [5,10,20]]
        self.assertEqual(values,[1,2,4])

    def test_tiny_active_adjustments_have_requested_one_point_floor(self):
        self.assertEqual(effect(history([50,50],[50,51],[150,150])),1)

    def test_neutral_static_gap_remains_zero(self):
        self.assertEqual(effect(history([20,20],[80,80],[150,150])),0)

    def test_total_and_spread_caps_are_separate(self):
        h=history([50,50],[50,100],[150,170])
        self.assertEqual(abs(effect(h)),6)
        self.assertEqual(abs(effect(h,"Spread")),4)

    def test_recent_reversal_is_not_erased_by_equal_endpoints(self):
        h=history([50,60,40],[50,80,50],[150,153,150],[T0,T1,T2])
        self.assertLess(effect(h,clock=T2),0)
        self.assertEqual(flow(h,clock=T2)["net_money_share_change"],0)

    def test_late_cash_rebound_updates_projection_up(self):
        h=history([50,30,40],[50,20,70],[150,148,148],[T0,T1,T2])
        earlier=effect(h[:2])
        latest=effect(h,clock=T2)
        self.assertLess(earlier,0);self.assertGreater(latest,earlier);self.assertGreater(latest,0)

    def test_later_line_can_confirm_earlier_cash(self):
        h=history([50,50,50],[50,70,70],[150,150,152],[T0,T1,T2])
        f=flow(h,clock=T2)
        self.assertGreater(f["features"][5],0)
        self.assertEqual(f["features"][4],0)

    def test_earlier_line_is_independent_not_later_cash_confirmation(self):
        h=history([50,50,50],[50,50,70],[150,152,152],[T0,T1,T2])
        f=flow(h,clock=T2)
        self.assertEqual(f["features"][0],2)
        self.assertEqual(f["features"][4],0);self.assertEqual(f["features"][5],0)
        self.assertGreater(effect(h,clock=T2),0)

    def test_size_can_resolve_conflicting_evidence(self):
        self.assertLess(effect(history([50,51],[50,51],[150,146])),0)
        self.assertGreater(effect(history([50,26],[50,66],[151.5,149.5])),0)

    def test_different_paths_with_same_endpoints_can_differ(self):
        direct=history([50,40],[50,50],[150,150])
        reversal=history([50,60,40],[50,80,50],[150,153,150],[T0,T1,T2])
        self.assertGreater(effect(direct),0);self.assertLess(effect(reversal,clock=T2),0)

    def test_future_data_is_excluded(self):
        h=history([50,26],[50,66],[151.5,149.5])
        future={"snapshotTime":T2,"betsPct":99,"moneyPct":1,"line":100}
        self.assertEqual(flow(h)["features"],flow(h+[future])["features"])

    def test_duplicates_do_not_add_votes(self):
        h=history([50,26],[50,66],[151.5,149.5])
        self.assertEqual(flow(h)["features"],flow(h+[dict(h[-1])])["features"])

    def test_conflicting_duplicate_rejected(self):
        h=history([50,26],[50,66],[151.5,149.5])
        with self.assertRaises(ValueError):flow(h+[{**h[-1],"moneyPct":30}])

    def test_clock_alone_does_not_repeat_a_correction(self):
        h=history([50,26],[50,66],[151.5,149.5])
        self.assertEqual(flow(h)["features"],flow(h,clock=T2)["features"])

    def test_unchanged_share_refresh_does_not_erase_split_evidence(self):
        h=history([50,40,40],[80,50,50],[150,150,150],[T0,T1,T2])
        self.assertEqual(flow(h[:2])["features"],flow(h,clock=T2)["features"])

    def test_later_line_fall_with_flat_shares_cannot_raise_the_total(self):
        h=history([50,40,40],[80,50,50],[150,150,149],[T0,T1,T2])
        self.assertLessEqual(effect(h,clock=T2),effect(h[:2]))

    def test_updates_need_no_artificial_30_minute_delay(self):
        h=history([50,50],[50,50],[150,151],[T0,"2026-11-17T12:05:00Z"])
        f=flow(h,clock="2026-11-17T12:05:00Z")
        self.assertEqual(f["status"],"ready");self.assertEqual(adjustment(150,f)["projection"],151)

    def test_mirrored_spread_sides_remain_symmetric(self):
        h=history([45,35],[50,75],[6.5,4.5])
        opposite=[{**p,"line":-p["line"],"betsPct":100-p["betsPct"],"moneyPct":100-p["moneyPct"]} for p in h]
        self.assertAlmostEqual(effect(h,"Spread"),-effect(opposite,"Spread"))

    def test_invalid_values_rejected(self):
        h=history([50,26],[50,66],[151.5,149.5])
        for value in ["",True,float("nan"),101,-1]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):flow([{**h[0],"moneyPct":value},h[1]])

    def test_post_tipoff_rejected(self):
        with self.assertRaises(ValueError):
            flow_features(history([50,26],[50,66],[151.5,149.5]),as_of=TIP,tipoff=TIP,market="Total")

    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):adjustment(150,flow(history([50,50],[50,50],[150,151])),"unknown")

    def test_stream_updates_do_not_compound_previous_adjustments(self):
        s=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        s.update(paired(T0))
        a=s.update(paired(T1,line=151))
        b=s.update(paired(T2,line=152))
        self.assertEqual(a["total"],151);self.assertEqual(b["total"],152)

    def test_stream_matches_batch_recalculation(self):
        snapshots=[paired(T0),paired(T1,line=151,money=65,bets=40),paired(T2,line=152,money=60,bets=45)]
        s=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        for p in snapshots:result=s.update(p)
        total=history([50,40,45],[50,65,60],[150,151,152],[T0,T1,T2])
        self.assertAlmostEqual(result["total"],150+effect(total,clock=T2))
        self.assertAlmostEqual(result["home"]+result["away"],result["total"])
        self.assertAlmostEqual(result["home"]-result["away"],result["margin"])
        self.assertFalse(result["official_pick_eligible"])

    def test_repeated_stream_snapshot_is_idempotent(self):
        s=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        s.update(paired(T0));a=s.update(paired(T1,line=151,money=65))
        self.assertEqual(a,s.update(paired(T1,line=151,money=65)))

    def test_fresh_baseline_is_not_an_already_adjusted_score(self):
        s=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        s.update(paired(T0));a=s.update(paired(T1,line=151))
        b=s.update(paired(T1,line=151),baseline_total=155)
        self.assertEqual(b["total"]-a["total"],5)

    def test_out_of_order_stream_data_is_sorted(self):
        a=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        b=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        points=[paired(T0),paired(T1,line=151),paired(T2,line=152)]
        for p in points:x=a.update(p)
        for p in [points[0],points[2],points[1]]:y=b.update(p)
        self.assertEqual(x,y)

    def test_old_baseline_cannot_replace_fresh_baseline(self):
        s=MarketProjectionState(baseline_margin=3,baseline_total=150,tipoff=TIP)
        s.update(paired(T0))
        s.update(paired(T2,line=152),baseline_total=155)
        with self.assertRaises(ValueError):s.update(paired(T1,line=151),baseline_total=145)

    def test_mixed_market_timestamps_rejected(self):
        a=history([50,50],[50,50],[150,151]);b=history([50,50],[50,50],[-3,-4])
        a[-1]["snapshotTime"]="2026-11-17T12:55:00Z"
        with self.assertRaises(ValueError):
            project_with_market_history(baseline_margin=3,baseline_total=150,spread_history=b,
             total_history=a,profiles_by_market={"Spread":"balanced","Total":"balanced"},as_of=T1,tipoff=TIP)

if __name__=="__main__":unittest.main()
