"""Exercise market validation and real SQLite transaction semantics without a network."""
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from shared import turso_storage as turso
from shared.ncaaf_auto_builder import (
    AUTO_TAB, Database, MarketGame, eligible_markets, match_schedule,
    protected_matchup, save_once, team_key, timestamp,
)

NOW = datetime(2026, 11, 22, 15, tzinfo=timezone.utc)


def market_rows():
    base = {"Date": "2026-11-22", "Away Team": "Away", "Home Team": "Home",
            "Source": "ScoresAndOdds", "Game Time": "2026-11-22T18:00:00Z",
            "Updated At": "11/22/2026, 9:45:00 AM EST", "Game Key": "source-game", "Snapshot Status": "LIVE"}
    return [{**base, "Market": market, "Selection": side, "Side": direction,
             "Line": str(line), "Odds": "-135", "Opening Line": str(line)}
            for market, side, direction, line in [
                ("Spread", "Away", "", 3.5), ("Spread", "Home", "", -3.5),
                ("Total", "Over", "Over", 48.5), ("Total", "Under", "Under", 48.5)]]


def test_lines_are_oriented_to_home_and_source_odds_are_ignored():
    markets, skipped = eligible_markets(market_rows(), NOW)
    assert not skipped
    assert markets[0].home_spread == -3.5
    assert markets[0].total == 48.5
    assert markets[0].summary()["assumed_odds"] == -110
    assert timestamp("11/22/2026, 9:45:00 AM EST").utcoffset() == timedelta(hours=-5)
    assert timestamp("10/22/2026, 9:45:00 AM EDT").utcoffset() == timedelta(hours=-4)


@pytest.mark.parametrize("change", ["missing_side", "bad_spread", "bad_total", "stale", "started", "different_kickoff", "wrong_date", "wrong_source"])
def test_incomplete_or_unverified_markets_do_not_build(change):
    rows = market_rows()
    if change == "missing_side": rows.pop()
    elif change == "bad_spread": rows[0]["Line"] = "4.5"
    elif change == "bad_total": rows[2]["Line"] = "49.5"
    elif change == "stale": rows[0]["Updated At"] = "11/21/2026, 9:45:00 AM EST"
    elif change == "different_kickoff": rows[0]["Game Time"] = "2026-11-22T19:00:00Z"
    elif change == "started":
        for row in rows: row["Game Time"] = NOW.isoformat()
    elif change == "wrong_date":
        for row in rows: row["Game Time"] = "2026-11-23T18:00:00Z"
    elif change == "wrong_source":
        for row in rows: row["Source"] = "Other feed"
    assert not eligible_markets(rows, NOW)[0]


def test_schedule_identity_aliases_and_protected_games():
    market = MarketGame("2026-11-22", "James Madison", "App State", NOW + timedelta(hours=3),
                        NOW, 11.5, 56.5, None, None, "sao-id")
    schedule = {"Season": "2026", "Week": "12", "Game ID": "123",
                "Away Team": "James Madison", "Home Team": "Appalachian State",
                "Game Time": (NOW + timedelta(hours=3)).isoformat(), "Completed": "False"}
    assert match_schedule(market, [schedule])["Game ID"] == "123"
    assert protected_matchup(market, schedule, [{"Game ID": "123"}])
    assert protected_matchup(market, schedule, [{"Date": market.date, "Away Team": market.away, "Home Team": market.home}])
    with pytest.raises(ValueError):
        match_schedule(market, [schedule, {**schedule, "Game ID": "456"}])


@pytest.mark.parametrize("market_name,schedule_name", [
    ("Pitt", "Pittsburgh"), ("Michigan St", "Michigan State"),
    ("Mississippi St", "Mississippi State"), ("Sacramento St", "Sacramento State"),
    ("Kansas St", "Kansas State"), ("Arizona St", "Arizona State"),
    ("UL Monroe", "Louisiana-Monroe"), ("Texas A&M", "Texas AandM"),
    ("Texas A & M", "Texas AandM"), ("San Jose St.", "San José State"),
])
def test_scoresandodds_schedule_aliases(market_name, schedule_name):
    assert team_key(market_name) == team_key(schedule_name)


def test_miami_and_texas_remain_distinct():
    assert team_key("Miami") != team_key("Miami (OH)")
    assert team_key("Texas") != team_key("Texas A&M")


@pytest.mark.parametrize("schedule_time", ["2026-11-22", "2026-11-22T00:00:00-05:00"])
def test_midnight_schedule_shell_uses_verified_market_kickoff(schedule_time):
    market = eligible_markets(market_rows(), NOW)[0][0]
    row = {"Season": "2026", "Week": "12", "Game ID": "123",
           "Away Team": "Away", "Home Team": "Home", "Game Time": schedule_time}
    result = match_schedule(market, [row])
    assert timestamp(result["Game Time"]) == market.kickoff
    assert row["Game Time"] == schedule_time
    with pytest.raises(ValueError, match="unique verified"):
        match_schedule(market, [row, {**row, "Game ID": "456"}])


@pytest.mark.parametrize("change", [
    {"Game Time": "2026-11-21T00:00:00-05:00"},
    {"Game Time": "2026-11-22T20:00:00Z"},
    {"Game Time": ""}, {"Completed": "True"}, {"Game ID": ""},
    {"Season": ""}, {"Week": ""}, {"Away Team": "Other"},
])
def test_placeholder_recovery_keeps_identity_and_real_time_gates(change):
    market = eligible_markets(market_rows(), NOW)[0][0]
    row = {"Season": "2026", "Week": "12", "Game ID": "123",
           "Away Team": "Away", "Home Team": "Home",
           "Game Time": "2026-11-22T00:00:00-05:00", **change}
    with pytest.raises(ValueError, match="unique verified"):
        match_schedule(market, [row])


def test_scheduled_ncaaf_processes_full_slate_without_changing_polling(monkeypatch):
    from scripts import run_scheduled_builders as runner
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] <= 900
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 0
    ncaaf = next(argv for argv in calls if argv[2].endswith("run_ncaaf_builder.py"))
    assert ncaaf[3:] == ["--commit", "--limit", "100"]


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.executescript('''
      CREATE TABLE dataset_rows (
        sport TEXT, dataset TEXT, row_index INTEGER, payload_json TEXT, source_hash TEXT,
        date_key TEXT, game_key TEXT, game TEXT, market TEXT, selection TEXT,
        result TEXT, snapshot_time TEXT, imported_at TEXT,
        PRIMARY KEY(sport,dataset,row_index));
      CREATE INDEX idx_date ON dataset_rows(sport,dataset,date_key);
      CREATE INDEX idx_game ON dataset_rows(sport,dataset,game_key);
      CREATE TABLE dataset_manifest (
        sport TEXT,dataset TEXT,source_workbook TEXT,source_worksheet TEXT,
        headers_json TEXT,row_count INTEGER,imported_at TEXT,source_kind TEXT,
        PRIMARY KEY(sport,dataset));
    ''')
    seen = []

    def pipeline(requests, **_kwargs):
        output = []
        try:
            for request in requests:
                sql = request["stmt"]["sql"]
                seen.append(sql)
                cursor = connection.execute(sql)
                rows = cursor.fetchall()
                output.append({"response": {"result": {
                    "cols": [{"name": column[0]} for column in cursor.description or []],
                    "rows": [[{"type": "null"} if value is None else {"type": "text", "value": str(value)} for value in row] for row in rows],
                    "rows_read": len(rows), "rows_written": max(0, cursor.rowcount),
                }}})
        except Exception:
            if connection.in_transaction: connection.rollback()
            raise
        # Match the real transport, which appends a close request/response.
        return [*output, {"response": {"type": "close"}}]

    fake = SimpleNamespace(_pipeline=pipeline, _result_rows=turso._result_rows, _sql_text=turso._sql_text)
    yield Database(fake), connection, seen
    connection.close()


class Builder:
    MODEL_VERSION = "production-test"
    SLATE_TAB, TRACKER_TAB, PERSONNEL_TAB = "daily_slate", "bet_tracker", "personnel_snapshots"
    SLATE_COLUMNS = ["Date", "Game ID", "Game", "Away Team", "Home Team", "Notes", "Spread Odds", "Total Odds"]
    TRACKER_COLUMNS = ["Date", "Game ID", "Game", "Bet Type", "Selection", "Result", "Units", "Odds"]
    PERSONNEL_COLUMNS = ["Season", "Week", "Game ID", "Team"]

    def slate_row(self, result):
        game = result["game"]
        return pd.DataFrame([{**game, "Date": "2026-11-22", "Game": "Away @ Home", "Notes": "Model notes", "Spread Odds": -110, "Total Odds": -110,
            "Spread Pick": "Home -3.5", "Total Pick": "Over 48.5", "Spread Grade": "A",
            "Total Grade": "B", "Projected Away": 20, "Projected Home": 30}])

    def tracker_rows(self, result, include_no_plays=False):
        return pd.DataFrame([{"Date": "2026-11-22", "Game ID": "123", "Game": "Away @ Home", "Bet Type": market,
                              "Selection": market, "Result": "Pending", "Units": 0, "Odds": -110}
                             for market in ("Spread", "Total", "Moneyline")])

    def personnel_row(self, personnel, team, season, week, game_id):
        return pd.DataFrame([{"Season": season, "Week": week, "Game ID": game_id, "Team": team}])


def save_fixture():
    market = eligible_markets(market_rows(), NOW)[0][0]
    result = {"game": {"Game ID": "123", "Season": "2026", "Week": "12", "Away Team": "Away", "Home Team": "Home"},
              "away_personnel": None, "home_personnel": None}
    return Builder(), result, market


def test_market_tracking_keeps_forecasts_and_personnel_without_new_game_grades(database):
    from shared.market_projection_grading import POLICY as tracking_policy
    db, connection, seen = database
    activation = {"policy": tracking_policy, "modelVersion": "ncaaf-market-tracking-2026-11-23-v1", "gradingStatus": "ungraded"}
    connection.execute("INSERT INTO dataset_rows(sport,dataset,row_index,date_key,payload_json) VALUES('NCAAF','market_projection_activation',1,'policy',?)",
                       (json.dumps({"Details JSON": json.dumps(activation)}),))
    builder, result, market = save_fixture()
    assert save_once(db, builder, result, market, now=NOW)
    assert connection.execute("SELECT COUNT(*) FROM dataset_rows WHERE dataset='daily_slate'").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM dataset_rows WHERE dataset='personnel_snapshots'").fetchone()[0] == 2
    assert connection.execute("SELECT COUNT(*) FROM dataset_rows WHERE dataset='bet_tracker'").fetchone()[0] == 0


def insert_existing(connection, dataset, payload, game_key="123"):
    connection.execute("INSERT INTO dataset_rows(sport,dataset,row_index,payload_json,date_key,game_key) VALUES('NCAAF',?,1,?,'2026-11-22',?)",
                       (dataset, json.dumps(payload), game_key))


def test_save_is_atomic_and_repeat_runs_write_nothing(database):
    db, connection, seen = database
    builder, result, market = save_fixture()
    assert save_once(db, builder, result, market, now=NOW)
    first = connection.execute("SELECT dataset,payload_json FROM dataset_rows ORDER BY dataset,row_index").fetchall()
    assert len(first) == 7  # Slate + two tracker + two personnel + completion + audit.
    tracker = [json.loads(payload) for dataset, payload in first if dataset == "bet_tracker"]
    assert {row["Bet Type"] for row in tracker} == {"Spread", "Total"}
    assert all(row["Odds Assumption"] == "-110" for row in tracker)
    written = db.rows_written
    assert not save_once(db, builder, result, market, now=NOW)
    assert db.rows_written == written
    assert first == connection.execute("SELECT dataset,payload_json FROM dataset_rows ORDER BY dataset,row_index").fetchall()
    assert not any("odds_snapshot" in sql for sql in seen)


@pytest.mark.parametrize("dataset,payload,key", [
    ("bet_tracker", {"Game ID": "123", "Result": "W", "Units": "4"}, "123"),
    ("personnel_snapshots", {"Game ID": "123", "Team": "Away"}, "123"),
    ("builder_completed", {"Date": "2026-11-22", "Away Team": "Away", "Home Team": "Home"}, ""),
    ("daily_slate", {"Game ID": "123", "Actual Away": "0"}, "123"),
])
def test_existing_manual_or_settled_records_are_protected_inside_transaction(database, dataset, payload, key):
    db, connection, _ = database
    insert_existing(connection, dataset, payload, key)
    before = connection.execute("SELECT * FROM dataset_rows").fetchall()
    assert not save_once(db, *save_fixture(), now=NOW)
    assert before == connection.execute("SELECT * FROM dataset_rows").fetchall()
    assert db.rows_written == 0


def test_unsaved_background_slate_can_be_completed_without_losing_extra_fields(database):
    db, connection, _ = database
    insert_existing(connection, "daily_slate", {"Date": "2026-11-22", "Game ID": "123", "Existing Extra": "keep"})
    assert save_once(db, *save_fixture(), now=NOW)
    row = json.loads(connection.execute("SELECT payload_json FROM dataset_rows WHERE dataset='daily_slate'").fetchone()[0])
    assert row["Existing Extra"] == "keep"


def test_compute_phase_cannot_write_even_if_a_model_hook_tries(database):
    db, connection, _ = database
    with pytest.raises(RuntimeError, match="read-only"):
        db.pipeline([{"type": "execute", "stmt": {"sql": "DELETE FROM dataset_rows"}}])
    assert db.blocked_writes == 1
    assert connection.execute("SELECT COUNT(*) FROM dataset_rows").fetchone()[0] == 0


def test_started_or_stale_game_cannot_save_after_slow_calculation(database):
    db, connection, _ = database
    assert not save_once(db, *save_fixture(), now=NOW + timedelta(hours=4))
    assert not save_once(db, *save_fixture(), now=NOW + timedelta(hours=2))
    assert connection.execute("SELECT COUNT(*) FROM dataset_rows").fetchone()[0] == 0


def test_preview_does_not_lock_or_grade_and_normal_save_still_works(database):
    db, connection, _ = database
    builder, result, market = save_fixture()
    assert save_once(db, builder, result, market, now=NOW, preview=True)
    rows = connection.execute('SELECT dataset,payload_json FROM dataset_rows').fetchall()
    assert len(rows) == 1 and rows[0][0] == 'game_previews'
    preview = json.loads(rows[0][1])
    assert preview['Build Phase'] == 'preview'
    assert preview['Spread Grade'] == preview['Total Grade'] == 'Ungraded preview'
    assert not save_once(db, builder, result, market, now=NOW, preview=True)
    assert save_once(db, builder, result, market, now=NOW)
    assert not save_once(db, builder, result, market, now=NOW, preview=True)
    assert not save_once(db, builder, result, market, now=NOW)
    assert connection.execute("SELECT COUNT(*) FROM dataset_rows WHERE dataset='builder_completed'").fetchone()[0] == 1


def test_preview_rejects_started_stale_and_manual_games(database):
    db, connection, _ = database
    builder, result, market = save_fixture()
    assert not save_once(db, builder, result, market, now=market.kickoff, preview=True)
    assert not save_once(db, builder, result, market, now=NOW + timedelta(minutes=91), preview=True)
    insert_existing(connection, 'bet_tracker', {'Game ID': '123'})
    assert not save_once(db, builder, result, market, now=NOW, preview=True)


@pytest.mark.parametrize('hour,commit,classification,fallback', [
    (7, True, 'fbs', False), (8, True, 'fbs', False), (9, True, 'fbs', False),
    (8, False, 'fbs', False), (8, True, 'fcs', False),
    (8, True, 'unknown', False), (8, True, 'fbs', True),
    (8, True, 'fbs', 'unavailable'),
])
def test_real_entry_point_morning_and_normal_lifecycle(database, monkeypatch, hour, commit, classification, fallback):
    from scripts import run_ncaaf_builder as runner
    db, connection, _ = database
    now = NOW.replace(hour=hour + 5)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(runner, 'datetime', Clock)
    monkeypatch.setattr(runner, 'Database', lambda _: db)
    monkeypatch.setattr(turso, 'is_turso_ready', lambda: True)
    monkeypatch.setenv('NCAAF_AUTO_BUILDER_ENABLED', '1')
    builder, result, _ = save_fixture()
    game = {**result['game'], 'Game Time': '2026-11-22T18:00:00Z',
            'Away Classification': classification, 'Home Classification': 'fbs'}
    insert_existing(connection, 'schedule', game)
    rows = market_rows()
    for row in rows:
        row['Updated At'] = now.isoformat()
    market = eligible_markets(rows, now)[0][0]
    if not fallback:
        for i,row in enumerate(rows):
            connection.execute("INSERT INTO dataset_rows(sport,dataset,row_index,date_key,payload_json) VALUES('NCAAF','weekly_market_trends',?,'2026-11-22',?)", (i+1,json.dumps(row)))
    monkeypatch.setattr(runner, 'missing_schedule_games', lambda *a: [game] if fallback else [])
    def fetch_fallback(*args):
        if fallback == 'unavailable':
            raise RuntimeError('provider unavailable')
        return [market], []
    monkeypatch.setattr(runner, 'fetch_covers_markets', fetch_fallback)
    monkeypatch.setattr(runner, 'prepare_builder', lambda db: builder)
    monkeypatch.setattr(runner, 'ratings_for_week', lambda *a: object())
    monkeypatch.setattr(runner, 'compute', lambda *a: result)
    # Exercise the real transaction with the same simulated clock as run().
    monkeypatch.setattr(runner, 'save_once', lambda *a, **kw: save_once(*a, now=now, **kw))
    args = SimpleNamespace(ignore_window=False, commit=commit, evaluate=not commit, limit=8)
    assert runner.run(args) == 0
    expected = commit and classification == 'fbs' and fallback != 'unavailable'
    dataset = 'game_previews' if hour < 9 else 'daily_slate'
    assert len(db.rows(dataset)) == int(expected)
    if hour < 9:
        assert not db.rows('bet_tracker') and not db.rows('builder_completed')
    written = db.rows_written
    assert runner.run(args) == 0
    assert db.rows_written == written


@pytest.mark.parametrize('hour', [0, 3, 6])
def test_no_overnight_database_or_model_work(monkeypatch, capsys, hour):
    from scripts import run_ncaaf_builder as runner
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.replace(hour=hour + 5)
    monkeypatch.setattr(runner, 'datetime', Clock)
    monkeypatch.setattr(turso, 'is_turso_ready', lambda: pytest.fail('overnight database work'))
    assert runner.run(SimpleNamespace(ignore_window=True, commit=True, evaluate=False, limit=8)) == 0
    assert json.loads(capsys.readouterr().out)['event'] == 'outside_build_window'


def test_production_slate_schema_publishes_kickoff_and_indexed_identity(database):
    import ast
    from pathlib import Path
    # Use the actual production schema, which deliberately has Date but no Game Time.
    tree = ast.parse(Path('builders/cfb_builder.py').read_text())
    columns = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'SLATE_COLUMNS' for t in node.targets))
    assert 'Date' in columns and 'Game Time' not in columns
    class ProductionShape(Builder):
        SLATE_COLUMNS = columns
        def slate_row(self, result):
            row = super().slate_row(result)
            row['Date'] = result['game']['Game Date']
            row['Model Version'] = self.MODEL_VERSION
            row['Projected Margin'], row['Projected Total'] = 10, 50
            return row.reindex(columns=self.SLATE_COLUMNS).astype(object)
    db, connection, _ = database
    _, result, market = save_fixture()
    result['game'].update({'Game Date': market.date, 'Game Time': market.kickoff.isoformat(),
                          'Away Classification': 'fbs', 'Home Classification': 'fbs'})
    for preview in [True, False]:
        assert save_once(db, ProductionShape(), result, market, now=NOW, preview=preview)
        dataset = 'game_previews' if preview else 'daily_slate'
        raw, date_key, game_key = connection.execute('SELECT payload_json,date_key,game_key FROM dataset_rows WHERE dataset=?', (dataset,)).fetchone()
        row = json.loads(raw)
        assert date_key == row['Date'] == market.date
        assert game_key == row['Game ID'] == '123'
        assert timestamp(row['Game Time']) == market.kickoff
        assert timestamp(row['Build At']) == NOW
        assert row['Projected Home'] == '30' and row['Projected Away'] == '20'
