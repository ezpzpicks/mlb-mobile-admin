"""Total consensus, separate score components and production isolation checks."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from builders import nfl_independent_spread as spread, nfl_independent_total as total
from builders.nfl_game_regression import project_matchup, simulate_game

EXAMPLES = json.loads((Path(__file__).parent / "fixtures/nfl_independent_total_examples.json").read_text())


def projection(example=None):
    e = example or EXAMPLES[0]
    base = project_matchup(e["away"], e["home"], {}, {}, e["settings"])
    first = spread.combine_projection(base, e["away"], e["home"], e["settings"])
    return total.combine_projection(first, e["away"], e["home"], e["settings"])


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda x:x["game_id"])
def test_frozen_total_models_and_agreement_match_research(example):
    features = total.feature_vector(example["away"], example["home"], example["settings"])
    for name,value in example["expected_features"].items():
        assert features[name] == pytest.approx(value,abs=1e-12)
    assert total.predict_model(total.PARAMETERS["tree"],features) == pytest.approx(example["expected_tree"],abs=1e-10)
    assert total.predict_model(total.PARAMETERS["regression"],features) == pytest.approx(example["expected_regression"],abs=1e-10)
    p=projection(example)
    assert total.grade_projection(p) == example["expected_grade"]
    assert p["total"] == pytest.approx(example["expected_tree"],abs=1e-10)
    assert p["home_score"]+p["away_score"] == pytest.approx(p["total"])
    assert p["home_score"]-p["away_score"] == pytest.approx(p["margin"])


@pytest.mark.parametrize("edge,confirmation,grade", [(3.999999,1.,"Non-Edge Total"),(4.,.000001,"Over"),
    (-3.999999,-1.,"Non-Edge Total"),(-4.,-.000001,"Under"),(12.,2.,"Over"),(-12.,-2.,"Under"),
    (5.,-1.,"Non-Edge Total"),(-5.,1.,"Non-Edge Total"),(5.,0.,"Non-Edge Total")])
def test_exact_gap_and_directional_agreement_have_no_strong_promotion(edge,confirmation,grade):
    p=projection();component=p["total_component"]
    market=component["features"]["market_total_centered"]+45
    component.update(tree_total=market+edge,regression_total=market+confirmation,edge=edge,
                     regression_edge=confirmation,agreement=edge*confirmation>0)
    p["total"]=market+edge
    assert total.grade_projection(p)==grade


@pytest.mark.parametrize("change", ["missing","wrong_hash","wrong_total","wrong_regression","wrong_agreement"])
def test_missing_or_spliced_total_proof_cannot_qualify(change):
    p=projection()
    if change=="missing":p.pop("total_component")
    if change=="wrong_hash":p["total_component"]["parameter_hash"]="other"
    if change=="wrong_total":p["total"]+=1
    if change=="wrong_regression":p["total_component"]["regression_total"]+=1
    if change=="wrong_agreement":p["total_component"]["agreement"]=not p["total_component"]["agreement"]
    assert total.grade_projection(p).startswith("No Play")


def test_confirmation_disagreement_keeps_point_forecast_without_a_total_bet():
    e=next(x for x in EXAMPLES if x["expected_grade"]=="Non-Edge Total")
    p=projection(e)
    assert abs(p["total_component"]["edge"])>=4
    assert p["total_component"]["agreement"] is False
    assert p["total"]==pytest.approx(e["expected_tree"])
    assert total.grade_projection(p)=="Non-Edge Total"


@pytest.mark.parametrize("change", ["missing_share","wrong_week","missing_quote","nonfinite"])
def test_bad_inputs_hold_totals_without_inventing_a_second_forecast(change):
    e=deepcopy(EXAMPLES[0])
    if change=="missing_share":e["home"].pop("Pass Play Share")
    if change=="wrong_week":e["settings"]["week"]+=1
    if change=="missing_quote":e["settings"]["spread_market_ready"]=False
    if change=="nonfinite":e["away"]["Rush EPA/Play"]=np.nan
    p=total.combine_projection({"total":45.,"margin":3.,"home_score":24.,"away_score":21.},e["away"],e["home"],e["settings"])
    assert p["total"]==45. and p["total_component"]["ready"] is False
    assert total.grade_projection(p).startswith("No Play")


def test_manual_total_override_is_honored_without_frozen_eligibility():
    e=deepcopy(EXAMPLES[0]);e["settings"]["manual_total_adjustment"]=2
    p=projection(e)
    assert p["total"]==pytest.approx(e["expected_tree"]+2)
    assert total.grade_projection(p).startswith("No Play")


def test_both_installers_keep_props_legacy_and_simulate_the_new_total():
    e=EXAMPLES[0]
    b=SimpleNamespace(_project_matchup=project_matchup,_simulate_game=simulate_game,
        _build_automated_ratings=lambda *_:pd.DataFrame(),_ensure_automated_ratings=lambda *_:pd.DataFrame(),
        _build_game_prop_rows=lambda *args,**kwargs:args[8],RATING_COLUMNS=[],SLATE_COLUMNS=[],TRACKER_COLUMNS=[],
        st=SimpleNamespace(session_state={}))
    spread.install_independent_spread_layer(b);total.install_independent_total_layer(b)
    p=b._project_matchup(e["away"],e["home"],{},{},e["settings"])
    old=project_matchup(e["away"],e["home"],{},{},e["settings"])
    simulation=b._simulate_game(p,e["settings"]["market_home_spread"],e["settings"]["market_total"],95.,321,simulations=2000)
    expected=simulate_game(p,e["settings"]["market_home_spread"],e["settings"]["market_total"],95.,321,simulations=2000)
    assert simulation["over"]==expected["over"]
    assert p["total"]==pytest.approx(e["expected_tree"])
    assert b._build_game_prop_rows(*([None]*8+[p]))==old
    original=b._project_matchup;total.install_independent_total_layer(b)
    assert b._project_matchup is original
    assert "Total Model JSON" in b.SLATE_COLUMNS and "Total Model JSON" in b.TRACKER_COLUMNS
    assert total.VERSION in total.storage_version(spread.storage_version("base",p),p)
