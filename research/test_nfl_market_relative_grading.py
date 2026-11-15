"""Behavioral checks for quote-aware NFL matchup support."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from builders import nfl_game_regression as model

MATCHUP_LABELS = {"Scoring matchup", "Success-rate matchup"}
LINEUP = {"offense_absence": 0.0, "defense_absence": 0.0}


def rating(**changes):
    return {"Points/Game": 22.5, "Points Allowed/Game": 22.5,
        "Off Success Rate": .43, "Def Success Edge": 0.0, **changes}


def forecast(away=None, home=None, **settings):
    return model.project_matchup(away or rating(), home or rating(), LINEUP, LINEUP, settings)


def spread_support(away, home, projection, line, pick_home):
    edge = (projection["margin"] + line) * (1 if pick_home else -1)
    return set(model.spread_confluence(pick_home, edge, away, home, LINEUP, LINEUP, 80,
        home_spread=line, projection=projection)[1]) & MATCHUP_LABELS


def total_support(away, home, projection, line, over):
    edge = (projection["total"] - line) * (1 if over else -1)
    return set(model.total_confluence(over, edge, projection, away, home, 0.0, 80,
        market_total=line)[1]) & MATCHUP_LABELS


def test_better_team_does_not_automatically_support_laying_seven():
    away, home = rating(**{"Points/Game": 21}), rating(**{"Points/Game": 25})
    projection = forecast(away, home, home_field=1.36)
    assert projection["margin"] == 3.0
    assert spread_support(away, home, projection, -7.0, False) == MATCHUP_LABELS
    assert not spread_support(away, home, projection, -7.0, True)
    # Same teams and forecast, a different price changes the supporting side.
    assert spread_support(away, home, projection, -1.0, True) == MATCHUP_LABELS


@pytest.mark.parametrize("over,line", [(True, 39.0), (False, 47.0)])
def test_below_average_scoring_can_support_over_a_low_total(over, line):
    away = home = rating(**{"Points/Game": 21, "Points Allowed/Game": 21, "Off Success Rate": .42})
    projection = forecast(away, home)
    assert 41 < projection["total"] < 43
    assert total_support(away, home, projection, line, over) == MATCHUP_LABELS
    assert not total_support(away, home, projection, line, not over)


def test_above_average_scoring_can_support_under_an_expensive_total():
    away = home = rating(**{"Points/Game": 26, "Points Allowed/Game": 26, "Off Success Rate": .46})
    projection = forecast(away, home)
    assert projection["total"] > 45
    assert total_support(away, home, projection, 55.0, False) == MATCHUP_LABELS
    assert not total_support(away, home, projection, 55.0, True)


def test_scoring_and_success_checks_can_disagree_at_the_same_price():
    away = home = rating(**{"Points/Game": 29, "Points Allowed/Game": 29, "Off Success Rate": .35})
    projection = forecast(away, home)
    assert total_support(away, home, projection, 45.0, True) == {"Scoring matchup"}
    assert total_support(away, home, projection, 45.0, False) == {"Success-rate matchup"}


def test_pushes_do_not_award_directional_matchup_support():
    team = rating()
    projection = forecast(team, team)
    assert not spread_support(team, team, projection, 0.0, True)
    assert not spread_support(team, team, projection, 0.0, False)
    assert not total_support(team, team, projection, projection["total"], True)
    assert not total_support(team, team, projection, projection["total"], False)


@pytest.mark.parametrize("quote", [None, float("nan"), float("inf"), -float("inf")])
def test_missing_or_nonfinite_market_does_not_manufacture_matchup_support(quote):
    team = rating()
    projection = forecast(team, team)
    spread = model.spread_confluence(True, 4, team, team, LINEUP, LINEUP, 80,
        home_spread=quote, projection=projection)
    total = model.total_confluence(True, 4, projection, team, team, 0, 80, market_total=quote)
    assert not (set(spread[1]) & MATCHUP_LABELS)
    assert not (set(total[1]) & MATCHUP_LABELS)


@pytest.mark.parametrize("projection", [None, {}, {"away_score": 20, "home_score": float("nan")}])
def test_missing_forecast_does_not_create_point_estimates(projection):
    assert model._matchup_point_estimates(rating(), rating(), projection) == {}


def test_team_swap_preserves_matchup_support_with_opposite_spread_notation():
    away, home = rating(**{"Points/Game": 19}), rating(**{"Points/Game": 27})
    projection = forecast(away, home)
    swapped = {**projection, "away_score": projection["home_score"],
        "home_score": projection["away_score"], "margin": -projection["margin"]}
    assert spread_support(away, home, projection, -7, False) == spread_support(home, away, swapped, 7, True)
    assert total_support(away, home, projection, 47, False) == total_support(home, away, swapped, 47, False)


def test_venue_personnel_weather_and_manual_offsets_are_carried_once():
    away = rating(**{"Points/Game": 20, "Off Success Rate": .40})
    home = rating(**{"Points/Game": 27, "Off Success Rate": .46})
    baseline = forecast(away, home)
    changed = forecast(away, home, home_field=2.0, weather_total_adjustment=-1.0,
        manual_home_margin_adjustment=1.0, manual_total_adjustment=-2.0)
    first = model._matchup_point_estimates(away, home, baseline)
    second = model._matchup_point_estimates(away, home, changed)
    for label in MATCHUP_LABELS:
        assert second[label]["margin"] - first[label]["margin"] == pytest.approx(changed["margin"] - baseline["margin"])
        assert second[label]["total"] - first[label]["total"] == pytest.approx(changed["total"] - baseline["total"])
    injured = {**baseline, "home_score": baseline["home_score"] - 2,
        "margin": baseline["margin"] - 2, "total": baseline["total"] - 2}
    injury_estimates = model._matchup_point_estimates(away, home, injured)
    for label in MATCHUP_LABELS:
        assert injury_estimates[label]["total"] == pytest.approx(first[label]["total"] - 2)


def test_grade_checks_do_not_mutate_inputs_or_change_seeded_forecasts():
    team = rating()
    projection = forecast(team, team)
    inputs = deepcopy((team, projection))
    simulation = model.simulate_game(projection, -3.5, 44.5, 80, 123, 2000)
    for line in (-7, 0, 7):
        spread_support(team, team, projection, line, True)
    for line in (39, 45, 55):
        total_support(team, team, projection, line, True)
    assert (team, projection) == inputs
    assert forecast(team, team) == projection
    assert model.simulate_game(projection, -3.5, 44.5, 80, 123, 2000) == simulation


def test_automatic_builder_passes_real_quotes_and_versions_only_game_grades(monkeypatch):
    from builders import nfl_automation as automation, nfl_builder as builder
    now = datetime(2035, 10, 21, 12, 7, tzinfo=timezone.utc)
    game = automation.normalize_game({"Season": 2035, "Week": 7, "Game ID": "2035_07_PIT_NO",
        "Away Team": "PIT", "Home Team": "NO", "Game Time": (now + timedelta(hours=5)).isoformat()})
    depth = pd.DataFrame([{"team_norm": team, "name_norm": team, "pos_norm": "QB", "pos_rank_num": 1,
        "espn_injury_status": "Active", "espn_auto_probability": 1.0, "espn_injury": ""} for team in ("PIT", "NO")])
    calls = {}
    def spread(*args, **kwargs):
        calls["spread"] = kwargs
        return model.spread_confluence(*args, **kwargs)
    def total(*args, **kwargs):
        calls["total"] = kwargs
        return model.total_confluence(*args, **kwargs)
    fake = SimpleNamespace(MODEL_VERSION="unchanged-forecast", GAME_GRADING_VERSION=model.GRADING_VERSION,
        MIN_GRADED_PROP_PLAY_PROBABILITY=.9, _team_row=lambda *args: rating(),
        _auto_lineup=lambda *args: pd.DataFrame([{"Slot": "QB", "Player": "Starter"}]),
        _finalize_lineup=lambda rows: (rows, LINEUP), _weather_adjustment=lambda *args: 0.0,
        _reliability=lambda *args: (80, 80, 80), _automatic_home_field_advantage=lambda *args: {"value": 0.0},
        _project_matchup=model.project_matchup, _simulation_seed=lambda *args: 123,
        _simulate_game=model.simulate_game, _spread_confluence=spread, _total_confluence=total,
        _grade_spread=builder._grade_spread, _grade_total_direction=builder._grade_total_direction,
        _build_game_prop_rows=lambda *args, **kwargs: pd.DataFrame(), _evaluate_prop_rows=lambda rows: rows,
        _prop_rows_for_storage=lambda *args: [{"Model Version": "prop"}],
        _graded_prop_tracker_rows=lambda *args: [], _game_tracker_rows=builder._game_tracker_rows)
    monkeypatch.setattr(automation, "environment", lambda *args: {"roof": "dome", "temperature": 70,
        "wind": 0, "precipitation": "None", "source": "fixture"})
    monkeypatch.setattr(automation, "apply_quotes", lambda rows, *args, **kwargs: (rows, []))
    monkeypatch.setattr(automation, "withhold_unavailable_quotes", lambda rows, *args: rows)
    market = SimpleNamespace(home_spread=-7.0, total=39.0, captured=now)
    weekly = {"ratings": [], "player_values": {}, "profiles": [], "defense_profiles": []}
    props = {"source": "fixture", "source_sha256": "fixture", "observed_at": now.isoformat(), "week": 7}
    result = automation.compute(fake, game, market, weekly, depth, props, now=now, phase="early", depth_at=now.isoformat())
    assert calls["spread"]["home_spread"] == -7
    assert calls["spread"]["projection"] == forecast()
    assert calls["total"] == {"market_total": 39.0}
    assert result["slate"]["Spread Pick"] == "PIT +7.0"
    assert result["slate"]["Total Pick"] == "Over 39.0"
    assert result["slate"]["Spread Grade"] == "A Spread"
    assert result["slate"]["Total Grade"] == "Strong Over"
    assert model.GRADING_VERSION in result["slate"]["Model Version"]
    assert result["game_tracker"] and all(model.GRADING_VERSION in row["Model Version"] for row in result["game_tracker"])
    assert model.GRADING_VERSION not in result["props"][0]["Model Version"]
    assert result["provenance"]["game_grading_version"] == model.GRADING_VERSION
    assert json.loads(result["slate"]["Spread Matchup Edges JSON"]) == pytest.approx([7, 7])
    assert json.loads(result["slate"]["Total Matchup Edges JSON"]) == pytest.approx([5.92, 5.92])
