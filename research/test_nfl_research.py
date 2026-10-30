import copy
import json
import sqlite3
from datetime import timedelta

import pytest

from builders.nfl_research import PARAMETER_HASH, POLICY, attach, league_center, predictions, reweighted_total, settle
from builders.nfl_research_dashboard import matched_comparisons, totals
from shared.nfl_research_storage import capture_manual, capture_requests, initialize, pending, read_rows, request, settle_rows
from shared.nfl_auto_storage import save_run
from research.test_nfl_automation import NOW, database, game, sample_result


def slate():
    return {"Game": "PIT at NO", "Away Team": "PIT", "Home Team": "NO", "Model Version": "test", "Projected Total": 46., "Projected Margin": 4.,
        "Market Total": 44., "Market Home Spread": -3., "Spread Pick": "NO -3.0", "Total Pick": "Over 44.0", "Spread Probability": .6, "Total Probability": .6,
        "Spread Grade": "A Spread", "Total Grade": "Over", "Home Spread Odds": -110, "Away Spread Odds": -105,
        "Total Over Odds": -110, "Total Under Odds": -120, "Weather Adjustment": -1.,
        "Away Offensive Absence": .2, "Home Offensive Absence": .2, "Away Defensive Absence": .5, "Home Defensive Absence": .5}


def context():
    return {"away_rating": {"Points/Game": 24., "Points Allowed/Game": 20., "Off Success Rate": .44, "Def Success Edge": .01},
        "home_rating": {"Points/Game": 28., "Points Allowed/Game": 22., "Off Success Rate": .45, "Def Success Edge": .02},
        "league_center": {"points": 22.5, "success": .43, "teams": 32}}


def prop():
    return {"Team": "PIT", "Player": "Test Runner", "Position": "RB", "Market": "Receiving Yards", "Projection": 30., "Market Line": 20.,
        "Over Odds": -115, "Under Odds": -105, "Pick": "Under 20.0", "Model Probability": .6, "Matchup Confidence": 85., "Grade": "Regular"}


def entry(rows, strategy):
    return next(r for r in rows if r["strategy"] == strategy)


def populated_result():
    builder, result = sample_result()
    result["slate"].update(slate()); result["props"] = [prop()]
    c = context(); result["provenance"]["ratings"] = {"Away": c["away_rating"], "Home": c["home_rating"]}
    league = [{"Team": str(i), "Points/Game": 22.5, "Off Success Rate": .43} for i in range(32)]
    attach(result, league)
    return builder, result


def test_frozen_totals_formula_matches_centered_research_definition():
    c = context()
    expected = 46 + .5 * .8206113159895836 * 2 - .5 * 22.97227990680213 * 0 + .5 * (-2 * 1.2968981090090854) + .6 + 2
    assert reweighted_total(slate(), c["away_rating"], c["home_rating"], c["league_center"]) == pytest.approx(expected)


def test_missing_inputs_skip_candidate_instead_of_substituting_neutral_values():
    s = slate(); s["Weather Adjustment"] = ""
    rows = predictions(s, [], context())
    assert not entry(rows, "totals_weights_v1")["selected"]
    assert entry(rows, "totals_weights_v1")["projection"] is None
    assert entry(rows, "always_over")["selected"]
    assert league_center([{"Team": str(i), "Points/Game": 22, "Off Success Rate": .4} for i in range(31)]) is None


def test_injury_filter_follows_projection_gap_and_has_no_new_probability():
    s = slate(); s["Total Pick"] = "Under 44"
    r = entry(predictions(s, [], context()), "totals_injury_v1")
    assert r["selected"] and r["direction"] == "Over" and r["probability"] is None
    s["Home Offensive Absence"] = 10
    assert not entry(predictions(s, [], context()), "totals_injury_v1")["selected"]


def test_receiving_rule_uses_projection_gap_not_skewed_distribution_pick():
    rows = predictions(slate(), [prop()], context())
    assert entry(rows, "rb_receiving_v1")["direction"] == "Over"
    assert entry(rows, "rb_receiving_v1")["odds"] == -115
    assert entry(rows, "rb_current")["direction"] == "Under"
    p = prop(); p["Under Odds"] = ""
    assert not entry(predictions(slate(), [p], context()), "rb_receiving_v1")["selected"]
    p = prop(); p["Grade"] = "Availability hold"
    assert not entry(predictions(slate(), [p], context()), "rb_receiving_v1")["selected"]


def test_official_prop_baseline_requires_actual_original_tracker_membership():
    c = {**context(), "tracked_prop_keys": []}
    rows = predictions(slate(), [prop()], c)
    assert not entry(rows, "rb_current")["selected"]
    assert entry(rows, "rb_receiving_v1")["selected"]


def test_conflicting_duplicate_quotes_are_withheld_without_breaking_official_save():
    changed = prop(); changed["Market Line"] = 25
    rows = predictions(slate(), [prop(), changed], context())
    candidate = [r for r in rows if r["strategy"] == "rb_receiving_v1"]
    assert len(candidate) == 1 and not candidate[0]["selected"]
    assert candidate[0]["reason"] == "Conflicting duplicate forecasts"


def test_capture_is_atomic_append_once_and_never_backfills_an_early_preview(database):
    db, conn = database; b, result = populated_result()
    assert save_run(db, b, copy.deepcopy(result), game(), "early", NOW)
    assert not read_rows(db)
    assert save_run(db, b, result, game(), "pregame", NOW)
    captured = read_rows(db)
    assert captured and all(r["captured_at"] == NOW.isoformat() for r in captured)
    assert entry(captured, "totals_weights_v1")["probability"] is None
    original_payloads = conn.execute("SELECT prediction_key,payload_json FROM nfl_research_predictions ORDER BY prediction_key").fetchall()
    result["research"]["predictions"][0]["line"] = 999
    with db.writes():db.pipeline([request("BEGIN IMMEDIATE"), *capture_requests(db, result, game(), NOW), request("COMMIT")])
    assert conn.execute("SELECT prediction_key,payload_json FROM nfl_research_predictions ORDER BY prediction_key").fetchall() == original_payloads
    assert save_run(db, b, result, game(), "late", NOW)
    assert conn.execute("SELECT prediction_key,payload_json FROM nfl_research_predictions ORDER BY prediction_key").fetchall() == original_payloads


def test_capture_rejects_elapsed_games_and_incorrect_policy(database):
    db, _ = database; _, r = populated_result()
    assert not capture_requests(db, r, game(NOW - timedelta(minutes=1)), NOW)
    old = game(); old["Date"] = "2026-10-01"
    assert not capture_requests(db, r, old, NOW)
    r["research"]["parameter_hash"] = "changed"
    with pytest.raises(ValueError):capture_requests(db, r, game(), NOW)


def test_manual_capture_reports_success_once_and_restores_database_connection(database, monkeypatch):
    from shared import turso_storage as turso
    db, _ = database
    monkeypatch.setattr(turso, "_pipeline", db.original_pipeline)
    value = {**slate(), **game()}
    c = context()
    league = [{"Team": str(i), "Points/Game": 22.5, "Off Success Rate": .43} for i in range(32)]
    args = (value, [prop()], league, c["away_rating"], c["home_rating"], game(), [prop()])
    assert capture_manual(*args)
    assert turso._pipeline is db.original_pipeline
    rows = read_rows(db)
    assert rows and entry(rows, "rb_current")["selected"]
    assert entry(rows, "totals_weights_v1")["price_source"] == "manual_input"
    assert not capture_manual(*args)
    assert turso._pipeline is db.original_pipeline
    assert len(read_rows(db)) == len(rows)


def test_research_failure_rolls_back_the_official_save_too(database):
    db, conn = database; b, result = populated_result()
    conn.execute("CREATE TRIGGER fail_research BEFORE INSERT ON nfl_research_predictions BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):save_run(db, b, result, game(), "pregame", NOW)
    assert not db.rows("daily_slate") and not read_rows(db) and not db.runs(game()["Date"])


def test_policy_changes_cannot_reuse_an_existing_cohort_version(database):
    db, conn = database
    conn.execute("UPDATE nfl_research_policies SET parameter_hash='old'")
    with pytest.raises(RuntimeError):initialize(db)


def test_settlement_preserves_forecast_payloads_and_does_not_award_unconfirmed_zero_yards(database):
    db, conn = database; b, result = populated_result()
    assert save_run(db, b, result, game(), "pregame", NOW)
    before = conn.execute("SELECT prediction_key,payload_json FROM nfl_research_predictions ORDER BY prediction_key").fetchall()
    now = NOW + timedelta(hours=10); rows = pending(db, now); finals = {game()["Game ID"]: (20, 24)}
    key = (game()["Game ID"], "PIT", "Test Runner")
    settle_rows(db, rows, finals, {key: {"yards": 0, "participated": False}}, now)
    rb = entry(read_rows(db), "rb_receiving_v1")
    assert rb["result"] == ""
    unresolved = pending(db, now)
    settle_rows(db, unresolved, finals, {key: {"yards": 20, "participated": True}}, now)
    rb = entry(read_rows(db), "rb_receiving_v1")
    assert rb["result"] == "P" and rb["units"] == 0
    assert conn.execute("SELECT prediction_key,payload_json FROM nfl_research_predictions ORDER BY prediction_key").fetchall() == before
    changes = conn.total_changes
    assert settle_rows(db, pending(db, now), finals, {}, now) == 0
    assert conn.total_changes == changes


def test_prices_spread_signs_and_pushes_are_accounted_for():
    base = {"selected": True, "market": "Spread", "direction": "Away", "line": 3, "odds": -110}
    assert settle(base, away=20, home=23) == {"result": "P", "units": 0}
    assert settle({**base, "line": 3.5}, away=20, home=23) == {"result": "W", "units": pytest.approx(100/110)}
    assert settle({**base, "line": 2.5}, away=20, home=23)["result"] == "L"
    assert settle({**base, "market": "Total", "direction": "Under", "line": 44}, away=20, home=24)["result"] == "P"


def test_matched_benchmarks_exclude_other_games_and_pending_results():
    rows = [dict(strategy=s, game_id=g, market="Total", player="", team="", selected=True, result=r, units=u, season=2026, week=9)
        for s, g, r, u in [("totals_weights_v1", "a", "W", .91), ("always_over", "a", "L", -1), ("always_over", "b", "W", .91), ("totals_weights_v1", "c", "", None)]]
    matched = matched_comparisons(rows)
    assert len(matched) == 1 and matched[0]["Matched plays"] == 1 and matched[0]["Difference"] == 1.91
    assert totals(rows[:1])["Games graded"] == 1 and totals(rows[-1:])["Pending"] == 1


def test_dashboard_renders_empty_forward_cohort_without_a_database_call(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import shared.nfl_research_storage as storage
    monkeypatch.setattr(storage, "read_rows", lambda db: [])
    app = AppTest.from_string("from builders.nfl_research_dashboard import render_research\nrender_research()")
    app.run(timeout=10)
    assert not app.exception
    assert any("next successful pregame" in r.value for r in app.success)


def test_kickoffs_are_normalized_to_utc_for_indexed_pending_ranges(database):
    from zoneinfo import ZoneInfo
    db, conn = database; b, result = populated_result()
    g = game(); g["Game Time"] = (NOW + timedelta(hours=2)).astimezone(ZoneInfo("America/New_York")).isoformat()
    assert save_run(db, b, result, g, "pregame", NOW)
    kickoff = conn.execute("SELECT kickoff FROM nfl_research_games").fetchone()[0]
    assert kickoff.endswith('+00:00')
    assert not pending(db, NOW + timedelta(hours=5))
    assert pending(db, NOW + timedelta(hours=7))
