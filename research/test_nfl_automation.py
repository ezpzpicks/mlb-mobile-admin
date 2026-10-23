"""Source, scheduling and real SQLite transaction checks for NFL automation."""
from datetime import datetime, timedelta, timezone
import copy
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from builders.nfl_automation import checked_depth, fetch_depth, game_market, normalize_game, phase_for_game, withhold_unavailable_quotes
from builders.nfl_rotowire_props import SourceError, apply_quotes, parse_props_html
from shared import turso_storage as turso
from shared.nfl_auto_storage import NflDatabase, POLICY, save_run, upsert_results

NOW = datetime(2035, 10, 21, 12, 7, tzinfo=timezone.utc)


def game(kickoff=None):
    return normalize_game({"Season": 2035, "Week": 7, "Game ID": "2035_07_PIT_NO", "Away Team": "PIT", "Home Team": "NO",
        "Game Time": (kickoff or NOW + timedelta(hours=5)).isoformat(), "Away Score": "", "Home Score": ""})


def page(*rows, code="passyds", week=7, table_week=None):
    return f'<h1>Best NFL Player Props Week {week}: Compare Odds</h1><script>const dayNFL = "{table_week or week}"; const prop = "{code}"; const settings = {{container: propID, data: {json.dumps(list(rows))}}};</script>'


def source_row(**changes):
    return {"gameID": "1001", "playerID": "8439", "name": "Aaron Rodgers", "team": "PIT", "opp": "@NO",
        "draftkings_passyds": "245.5", "draftkings_passydsOver": "-115", "draftkings_passydsUnder": "-105", **changes}


def projection():
    return pd.DataFrame([{"Player": "Aaron Rodgers", "Team": "PIT", "Market": "Passing Yards", "Slot": "QB", "Projection": 260}])


def test_quotes_keep_real_same_book_line_and_both_prices():
    report = parse_props_html(page(source_row()), observed_at=NOW)
    out, matched = apply_quotes(projection(), game(), report, now=NOW)
    assert out.iloc[0]["Market Line"] == 245.5
    assert out.iloc[0]["Over Odds"] == -115
    assert out.iloc[0]["Under Odds"] == -105
    assert matched[0]["player_id"] == "rotowire:8439"
    assert "draftkings" in out.iloc[0]["Line Source"]


def test_missing_quote_does_not_manufacture_minus_110():
    row = source_row(draftkings_passydsUnder=None, fanduel_passyds="244.5", fanduel_passydsUnder="-120", fanduel_passydsOver="100")
    out, matched = apply_quotes(projection(), game(), parse_props_html(page(row), observed_at=NOW), now=NOW)
    assert matched[0]["book"] == "fanduel"
    assert out.iloc[0]["Under Odds"] == -120 and out.iloc[0]["Market Line"] == 244.5


def test_anytime_td_uses_one_price_not_a_yardage_line():
    report = parse_props_html(page(source_row(draftkings_anytd="250"), code="anytd"), observed_at=NOW)
    p = projection().assign(Market="Anytime TD")
    out, _ = apply_quotes(p, game(), report, now=NOW)
    assert out.iloc[0]["Market Line"] == 0.5 and out.iloc[0]["Over Odds"] == 250
    assert pd.isna(out.iloc[0]["Under Odds"])


@pytest.mark.parametrize("change", ["week", "team", "home_away", "name", "stale", "ambiguous"])
def test_wrong_identity_or_stale_quotes_are_blocked(change):
    row = source_row()
    if change == "team": row["team"] = "MIA"
    if change == "home_away": row["opp"] = "NO"
    if change == "name": row["name"] = "A. Rodgers"
    rows = [row, {**row, "playerID": "123"}] if change == "ambiguous" else [row]
    if change == "ambiguous":
        with pytest.raises(SourceError): parse_props_html(page(*rows), observed_at=NOW)
        return
    report = parse_props_html(page(*rows), observed_at=NOW)
    if change == "week": report["week"] = 6
    if change == "stale": report["observed_at"] = (NOW - timedelta(minutes=31)).isoformat()
    with pytest.raises(SourceError):
        apply_quotes(projection(), game(), report, now=NOW)


def test_changed_layout_or_week_is_not_an_empty_healthy_report():
    with pytest.raises(SourceError): parse_props_html("<h1>Subscribe now</h1>")
    with pytest.raises(SourceError): parse_props_html(page(source_row(), table_week=6))
    with pytest.raises(SourceError): parse_props_html(page(source_row()).replace('data: [', 'data: evil('))


def test_phase_windows_cover_normal_international_and_dst_kickoffs():
    early = game(NOW + timedelta(minutes=83))
    assert phase_for_game(early, NOW, {}) == "early"
    assert phase_for_game(early, NOW + timedelta(minutes=30), {}) == "pregame"
    prior = {(early["Game ID"], "pregame"): {}}
    assert phase_for_game(early, NOW + timedelta(minutes=60), prior) == "late"
    assert phase_for_game(early, NOW + timedelta(minutes=80), prior) is None
    for offset in (4, 5):
        # The hour is Eastern, not a fixed UTC hour that drifts with DST.
        current = datetime(2035, 11 if offset == 5 else 10, 21, 8 + offset, 7, tzinfo=timezone.utc)
        g = game(current + timedelta(hours=5))
        assert phase_for_game(g, current, {}) == "early"
        assert phase_for_game(g, current - timedelta(hours=1), {}) is None


def market_rows(g=None):
    g = g or game()
    base = {"Date": g["Date"], "Game Time": g["Game Time"], "Away Team": "Pittsburgh Steelers", "Home Team": "New Orleans Saints",
        "Source": "ScoresAndOdds", "Snapshot Status": "LIVE", "Updated At": NOW.isoformat()}
    return [{**base, "Market": m, "Selection": s, "Side": s, "Line": v} for m, s, v in
        [("Spread", "Pittsburgh Steelers", 3.5), ("Spread", "New Orleans Saints", -3.5), ("Total", "Over", 45.5), ("Total", "Under", 45.5)]]


def test_game_market_normalizes_names_and_checks_all_four_sides():
    assert game_market(market_rows(), game(), NOW).home_spread == -3.5
    for change in ("missing", "stale", "kickoff", "spread", "source"):
        rows = market_rows()
        if change == "missing": rows.pop()
        if change == "stale": rows[0]["Updated At"] = (NOW - timedelta(hours=2)).isoformat()
        if change == "kickoff": rows[0]["Game Time"] = (NOW + timedelta(hours=6)).isoformat()
        if change == "spread": rows[0]["Line"] = 4.5
        if change == "source": rows[0]["Source"] = "other"
        with pytest.raises(SourceError): game_market(rows, game(), NOW)


def test_inactive_lists_are_required_and_only_confirm_game_eligibility():
    depth = pd.DataFrame([{"team_norm": t, "player_name_display": n, "espn_injury_status": s, "espn_auto_probability": p}
        for t, n, s, p in [("PIT", "Aaron Rodgers", "QUESTIONABLE", .65), ("NO", "Test Runner", "OUT", 0), ("PIT", "Test Receiver", "ACTIVE", 1)]])
    report = {"season": 2035, "observed_at": NOW.isoformat(), "teams": {"PIT": ["Test Receiver"], "NO": ["Bench Player"]}}
    result = checked_depth(depth, game(), report, NOW)
    assert list(result["espn_auto_probability"]) == [1, 0, 0]
    assert result.iloc[0]["espn_injury_status"] == "ACTIVE"
    del report["teams"]["NO"]
    with pytest.raises(SourceError): checked_depth(depth, game(), report, NOW)


@pytest.fixture
def database():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript('''CREATE TABLE dataset_rows (sport TEXT,dataset TEXT,row_index INTEGER,payload_json TEXT,source_hash TEXT,date_key TEXT,game_key TEXT,game TEXT,market TEXT,selection TEXT,result TEXT,snapshot_time TEXT,imported_at TEXT,PRIMARY KEY(sport,dataset,row_index));
    CREATE INDEX ix_game ON dataset_rows(sport,dataset,game_key); CREATE INDEX ix_date ON dataset_rows(sport,dataset,date_key);
    CREATE TABLE dataset_manifest(sport TEXT,dataset TEXT,source_workbook TEXT,source_worksheet TEXT,headers_json TEXT,row_count INTEGER,imported_at TEXT,source_kind TEXT,PRIMARY KEY(sport,dataset));''')
    def pipeline(requests, **kwargs):
        out = []
        try:
            for request in requests:
                cur = conn.execute(request["stmt"]["sql"])
                rows = cur.fetchall()
                out.append({"response": {"result": {"cols": [{"name": c[0]} for c in cur.description or []],
                    "rows": [[{"type": "null"} if v is None else {"type": "text", "value": str(v)} for v in r] for r in rows],
                    "rows_read": len(rows), "rows_written": max(0, cur.rowcount)}}})
            return [*out, {"response": {"type": "close"}}]
        except Exception:
            if conn.in_transaction: conn.rollback()
            raise
    fake = SimpleNamespace(_pipeline=pipeline, _sql_text=turso._sql_text, _result_rows=turso._result_rows, _metadata=turso._metadata)
    db = NflDatabase(fake)
    db.initialize()
    yield db, conn
    conn.close()


def sample_result():
    from builders import nfl_builder as b
    g = game()
    common = {"Date": g["Date"], "Game ID": g["Game ID"], "Game": "PIT at NO", "Away Team": "PIT", "Home Team": "NO", "Model Version": "test"}
    return b, {"input_hash": "abc", "provenance": {"props": {"observed_at": NOW.isoformat()}, "depth_observed_at": NOW.isoformat(), "game_market_observed_at": NOW.isoformat(), "inactives": {"observed_at": NOW.isoformat()}}, "slate": {**common, "Spread Grade": "A Spread", "Total Grade": "B Total"},
        "props": [{**common, "Player": "Test", "Market": "Passing Yards", "Grade": "Regular", "Market Line": 200}],
        "prop_tracker": [{**common, "Player": "Test", "Market": "Passing Yards", "Market Line": 200, "Pick": "Over 200", "Result": "Pending"}],
        "game_tracker": [{**common, "Bet Type": "Total", "Selection": "Over 45", "Result": "Pending"}],
        "lineups": [{**common, "Team": "PIT", "Unit": "Offense", "Slot": "QB", "Player": "Test"}]}


def test_early_preview_then_final_save_are_atomic_and_idempotent(database):
    db, conn = database
    b, result = sample_result()
    assert save_run(db, b, copy.deepcopy(result), game(), "early", NOW)
    assert not db.rows("prop_tracker") and not db.rows("prop_projections")
    assert db.rows("daily_slate")[0]["Spread Grade"] == "Pending pregame"
    assert not save_run(db, b, copy.deepcopy(result), game(), "early", NOW)
    assert save_run(db, b, copy.deepcopy(result), game(), "pregame", NOW)
    assert len(db.rows("daily_slate")) == 1 and db.rows("daily_slate")[0]["Spread Grade"] == "A Spread"
    assert len(db.rows("prop_tracker")) == 1 and len(db.runs(game()["Date"])) == 2
    before = conn.total_changes
    assert not save_run(db, b, copy.deepcopy(result), game(), "pregame", NOW)
    assert conn.total_changes == before


def test_manual_games_and_elapsed_kickoffs_are_protected(database):
    db, _ = database
    b, result = sample_result()
    with db.writes(): upsert_results(db, "daily_slate", [{**result["slate"], "Notes": "Manual"}], b.SLATE_COLUMNS, NOW)
    assert not save_run(db, b, result, game(), "pregame", NOW)
    assert not db.runs(game()["Date"])
    assert db.rows("daily_slate")[0]["Notes"] == "Manual"
    assert not save_run(db, b, result, game(NOW - timedelta(minutes=1)), "early", NOW)


def test_result_updates_preserve_other_games_and_existing_audit_columns(database):
    db, conn = database
    b, result = sample_result()
    rows = [*result["prop_tracker"], {**result["prop_tracker"][0], "Game ID": "other", "Notes": "Preserve"}]
    upsert_results(db, "prop_tracker", rows, b.PROP_TRACKER_COLUMNS, NOW)
    row = db.rows("prop_tracker")[0]
    row["Result"] = "Win"
    upsert_results(db, "prop_tracker", [row], b.PROP_TRACKER_COLUMNS, NOW)
    assert len(db.rows("prop_tracker")) == 2
    assert db.rows("prop_tracker")[1]["Notes"] == "Preserve"
    before = conn.total_changes
    upsert_results(db, "prop_tracker", [row], b.PROP_TRACKER_COLUMNS, NOW)
    # The data rows are unchanged; bookkeeping touches only two bounded rows.
    assert conn.total_changes - before <= 2


def test_weekly_cache_roundtrip_and_incidental_write_guard(database):
    db, _ = database
    db.cache("week7", {"rows": [{"a": 1, "b": None}], "version": "test"}, NOW)
    assert db.cached("week7")[1]["rows"] == [{"a": 1, "b": None}]
    with pytest.raises(RuntimeError): db.query("DELETE FROM dataset_rows")


def test_stale_inputs_cannot_be_saved_after_a_cold_model_build(database):
    db, _ = database
    b, result = sample_result()
    result["provenance"]["depth_observed_at"] = (NOW - timedelta(minutes=16)).isoformat()
    with pytest.raises(SourceError): save_run(db, b, result, game(), "pregame", NOW)
    assert not db.rows("daily_slate") and not db.runs(game()["Date"])


def test_late_review_never_rewrites_the_locked_play(database):
    db, _ = database
    b, result = sample_result()
    assert not save_run(db, b, result, game(), "late", NOW)
    assert save_run(db, b, copy.deepcopy(result), game(), "pregame", NOW)
    locked = db.rows("prop_projections")
    result["props"][0]["Market Line"] = 999
    result["input_hash"] = "changed"
    assert save_run(db, b, result, game(), "late", NOW)
    assert db.rows("prop_projections") == locked
    assert db.runs(game()["Date"])[(game()["Game ID"], "late")]["input_hash"] == "changed"


def test_partial_final_save_rolls_back_all_datasets(database):
    db, conn = database
    b, result = sample_result()
    conn.execute("CREATE TRIGGER fail_props BEFORE INSERT ON dataset_rows WHEN NEW.dataset='prop_tracker' BEGIN SELECT RAISE(ABORT,'simulated failure'); END")
    with pytest.raises(sqlite3.IntegrityError): save_run(db, b, result, game(), "pregame", NOW)
    assert not db.rows("daily_slate") and not db.rows("prop_projections") and not db.runs(game()["Date"])


def test_schedule_shell_is_upgraded_without_duplicate_full_slate_game(database):
    db, conn = database
    b, result = sample_result()
    shell = {"Date": game()["Date"], "Game ID": "espn-event-123", "Away Team": "PIT", "Home Team": "NO", "Game": "PIT @ NO", "Model Version": "", "Notes": "Schedule-only shell"}
    upsert_results(db, "daily_slate", [shell], b.SLATE_COLUMNS, NOW)
    assert save_run(db, b, result, game(), "pregame", NOW)
    rows = db.rows("daily_slate")
    assert len(rows) == 1 and rows[0]["Game ID"] == game()["Game ID"]
    assert conn.execute("SELECT row_count FROM dataset_manifest WHERE dataset='daily_slate'").fetchone()[0] == 1


def test_public_schedule_sync_cannot_turn_our_preview_into_a_manual_lock(database):
    db, conn = database
    b, result = sample_result()
    result["slate"]["Model Version"] = "model|" + POLICY
    assert save_run(db, b, copy.deepcopy(result), game(), "early", NOW)
    conn.execute("UPDATE dataset_rows SET payload_json=json_remove(payload_json,'$.\"Build Policy\"','$.\"Build Phase\"','$.\"Build At\"') WHERE dataset='daily_slate'")
    assert save_run(db, b, result, game(), "pregame", NOW)


def test_targeted_core_loader_includes_replacements_beyond_wr3(monkeypatch):
    from builders import nfl_builder as b
    def entries(pos, count=1):
        return {"position": {"abbreviation": pos}, "athletes": [{"rank": i, "slot": i,
            "athlete": {"$ref": f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2035/athletes/{100*len(pos)+i}"}} for i in range(1, count+1)]}
    charts = [{"positions": {p: entries(p, 4 if p == "WR" else 1) for p in ("QB", "RB", "WR", "TE", "LT")}},
              {"positions": {p: entries(p) for p in ("DE", "DT", "CB", "FS")}}]
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        payload = {"items": charts} if url.endswith("depthcharts") else {"displayName": "Test Player " + url.split("/")[-1], "status": {"name": "Active"}, "injuries": []}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)
    monkeypatch.setattr("builders.nfl_automation.requests.get", get)
    depth, _ = fetch_depth(b, {"PIT"}, 2035)
    assert len(depth[depth["pos_norm"] == "WR"]) == 4
    assert not any("v3" in c for c in calls)


def test_nfl_failure_does_not_skip_other_scheduled_sports(monkeypatch):
    from scripts import run_scheduled_builders as runner
    monkeypatch.setenv("NFL_AUTO_BUILDER_ENABLED", "1")
    calls = []
    def run(argv, **kwargs):
        calls.append(argv[2])
        return SimpleNamespace(returncode=1 if argv[2].endswith("run_nfl_builder.py") else 0)
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 1
    assert [p.split("/")[-1] for p in calls] == ["run_nba_builder.py", "run_nfl_builder.py", "run_ncaaf_builder.py"]


def test_public_yardage_regrading_cannot_revive_an_availability_hold():
    rows = pd.DataFrame([{"Player": name, "_play_probability": p, "Market Line": 100, "Projection": 200,
        "Probability Edge": .5, "Projection Edge": 100, "Over Odds": -110, "Under Odds": -110,
        "Grade": "Strong", "Track": True, "Pick": "Over 100", "Line Source": "RotoWire / draftkings"}
        for name, p in (("Unresolved", .65), ("Eligible", .9))])
    out = withhold_unavailable_quotes(rows, .9)
    assert out.iloc[0]["Grade"] == "Availability hold" and not out.iloc[0]["Track"]
    assert pd.isna(out.iloc[0]["Market Line"]) and pd.isna(out.iloc[0]["Probability Edge"])
    assert out.iloc[1]["Grade"] == "Strong" and out.iloc[1]["Market Line"] == 100
