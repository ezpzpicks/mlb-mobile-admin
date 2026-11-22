"""Parity, qualification, isolation and lag checks for the NFL spread rollout."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from builders import nfl_independent_spread as model
from builders.nfl_game_regression import project_matchup

EXAMPLES = json.loads((Path(__file__).parent / "fixtures/nfl_independent_spread_examples.json").read_text())


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda x:x["game_id"])
def test_independent_component_matches_frozen_features_and_prediction(example):
    actual = model.feature_vector(example["away"], example["home"], example["settings"])
    for key, value in example["expected_features"].items():
        assert actual[key] == pytest.approx(value, abs=1e-12)
    assert model.forecast_margin(actual) == pytest.approx(example["expected_margin"], abs=1e-10)


def projection():
    e = EXAMPLES[0]
    base = project_matchup(e["away"], e["home"], {}, {}, e["settings"])
    return model.combine_projection(base, e["away"], e["home"], e["settings"])


@pytest.mark.parametrize("edge,grade", [(3.499999,"Non-Edge Spread"),(3.5,"B Spread"),(15,"B Spread"),
                                        (-3.499999,"Non-Edge Spread"),(-3.5,"B Spread"),(-15,"B Spread")])
def test_one_grade_uses_unrounded_gap_and_never_promotes_to_a(edge, grade):
    p = projection()
    p["spread_component"]["edge"] = edge
    p["spread_component"]["margin"] = p["spread_component"]["features"]["market_margin"] + edge
    p["margin"] = p["spread_component"]["margin"]
    assert model.grade_projection(p) == grade


def test_missing_or_spliced_component_cannot_receive_grade():
    p = projection()
    for change in [lambda x:x["spread_component"].update(ready=False),
                   lambda x:x["spread_component"].update(parameter_hash="wrong"),
                   lambda x:x.update(margin=x["margin"]+1),
                   lambda x:x["spread_component"].pop("edge")]:
        changed = deepcopy(p);change(changed)
        assert model.grade_projection(changed).startswith("No Play")
    assert model.grade_projection({}).startswith("No Play")


def test_combined_scores_keep_existing_total_even_with_personnel_and_weather():
    e = deepcopy(EXAMPLES[0])
    e["away"]["QB Adjustment"]=-4
    e["settings"]["weather_total_adjustment"]=-1.5
    base = project_matchup(e["away"],e["home"],{"offense_absence":2},{"defense_absence":1},e["settings"])
    p = model.combine_projection(base,e["away"],e["home"],e["settings"])
    assert p["total"] == base["total"]
    assert p["away_score"]+p["home_score"] == pytest.approx(base["total"])
    assert p["home_score"]-p["away_score"] == pytest.approx(e["expected_margin"])
    assert p["legacy_projection"] == base


@pytest.mark.parametrize("change", ["missing_share","wrong_week","wrong_cutoff","missing_market","nonfinite","wrong_game_week","future_cutoff"])
def test_incomplete_inputs_hold_spread_and_preserve_total(change):
    e=deepcopy(EXAMPLES[0])
    if change=="missing_share":e["home"].pop("Pass Play Share")
    if change=="wrong_week":e["home"]["Spread Input Week"]+=1
    if change=="wrong_cutoff":e["home"]["Spread Input Cutoff"]="2026-01-01"
    if change=="missing_market":e["settings"]["spread_market_ready"]=False
    if change=="nonfinite":e["home"]["Off Success Rate"]=np.nan
    if change=="wrong_game_week":e["settings"]["week"]+=1
    if change=="future_cutoff":
        e["home"]["Spread Input Cutoff"]=e["away"]["Spread Input Cutoff"]="2099-01-01"
    p=model.combine_projection({"total":45,"margin":2,"home_score":23.5,"away_score":21.5},e["away"],e["home"],e["settings"])
    assert p["total"]==45 and p["spread_component"]["ready"] is False
    assert model.grade_projection(p).startswith("No Play")


def fake_builder(pbp):
    schedule=pd.DataFrame([{"Game Type":"REG","Week":1,"Away Team":"KC","Home Team":"BUF","Away Score":21,"Home Score":24,"Game Date":"2026-09-13","Game ID":"123"},
                           {"Game Type":"REG","Week":2,"Away Team":"KC","Home Team":"BUF","Away Score":20,"Home Score":27,"Game Date":"2026-09-20","Game ID":"456"}])
    return SimpleNamespace(NFL_TEAMS=["KC","BUF"],_schedule_for_season=lambda _:schedule,
        _load_pbp_season=Mock(return_value=pbp),_download_to_disk=Mock(),_pbp_cache_path=lambda _:"unused-test-path")


def test_passing_counts_ignore_current_week_and_use_canonical_matchup_ids():
    pbp=pd.DataFrame({"season_type":["REG"]*4,"week":[1,1,2,2],"game_id":["2026_01_KC_BUF"]*2+["2026_02_KC_BUF"]*2,
        "posteam":["KC"]*4,"epa":[0.]*4,"qb_dropback":[1,0,1,1],"rush_attempt":[0,1,0,0]})
    b=fake_builder(pbp)
    counts,refreshed=model.passing_counts(b,2026,1)
    assert counts.loc["KC","dropbacks"]==1 and counts.loc["KC","valid"]==2 and not refreshed
    pbp.loc[pbp.week.eq(2),"qb_dropback"]=0
    counts2,_=model.passing_counts(b,2026,1)
    pd.testing.assert_frame_equal(counts,counts2)
    b._download_to_disk.assert_not_called()


def test_missing_previous_game_refreshes_once_and_then_holds_if_still_missing():
    b=fake_builder(pd.DataFrame())
    with pytest.raises(ValueError,match="earlier-week"):
        model.passing_counts(b,2026,1)
    assert b._download_to_disk.call_count==1 and b._load_pbp_season.call_count==2


def test_installation_preserves_totals_simulation_and_prop_projection_inputs():
    e=EXAMPLES[0]
    def sim(p,*_a,**_k):return {"home_cover":.5+p["margin"]/100,"over":.5+p["margin"]/200}
    base=project_matchup(e["away"],e["home"],{},{},e["settings"])
    b=SimpleNamespace(_project_matchup=project_matchup,_simulate_game=sim,_build_automated_ratings=lambda *_:pd.DataFrame(),
        _ensure_automated_ratings=lambda *_:pd.DataFrame(),_build_game_prop_rows=lambda *args,**kwargs:args[8],
        RATING_COLUMNS=[],SLATE_COLUMNS=[],TRACKER_COLUMNS=[],st=SimpleNamespace(session_state={}))
    model.install_independent_spread_layer(b)
    p=b._project_matchup(e["away"],e["home"],{},{},e["settings"])
    actual=b._simulate_game(p)
    assert actual["over"]==sim(base)["over"]
    assert actual["home_cover"]==sim(p)["home_cover"]
    assert b._build_game_prop_rows(*([None]*8+[p]))==base
    original=b._project_matchup;model.install_independent_spread_layer(b)
    assert b._project_matchup is original


def test_manual_margin_override_is_honored_without_borrowing_frozen_eligibility():
    e=deepcopy(EXAMPLES[0]);e["settings"]["manual_home_margin_adjustment"]=2
    p=model.combine_projection({"total":45,"margin":2,"home_score":23.5,"away_score":21.5},e["away"],e["home"],e["settings"])
    assert p["margin"]==pytest.approx(e["expected_margin"]+2)
    assert model.grade_projection(p).startswith("No Play")
