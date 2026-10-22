"""Append-only NBA research tables using the existing Turso connection.

No public pick/slate dataset is touched. Unique constraints make a second lock
for the same game/model version a no-op across sessions and concurrent clicks.
"""
from __future__ import annotations

import json

from builders.nba_model import InputError
from builders.nba_tracking import LOCK_SCHEMA, RESULT_SCHEMA, unique_predictions, verify_record


def _backend():
    from shared import turso_storage as db
    if not db.is_turso_ready():
        raise InputError("NBA tracking storage is unavailable. The prediction was not saved; download the snapshot for review.")
    return db


def _init(db):
    db._execute("CREATE TABLE IF NOT EXISTS nba_research_predictions (record_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, model_version TEXT NOT NULL, game_id TEXT NOT NULL, locked_at TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(experiment_id,model_version,game_id))")
    db._execute("CREATE TABLE IF NOT EXISTS nba_research_results (observation_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL)")


def _payload(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > 2_000_000:
        raise InputError("NBA tracking record exceeds the storage size limit.")
    return raw


def save_prediction(record: dict) -> tuple[dict, bool]:
    verify_record(record, LOCK_SCHEMA)
    db = _backend()
    _init(db)
    values = [record[k] for k in ("record_id", "experiment_id", "model_version", "game_id", "locked_at")]
    values.append(_payload(record))
    sql = "INSERT INTO nba_research_predictions (record_id,experiment_id,model_version,game_id,locked_at,payload_json) SELECT " + ",".join(db._sql_text(v) for v in values) + " WHERE julianday('now')<julianday(" + db._sql_text(record['tipoff']) + ") ON CONFLICT DO NOTHING RETURNING record_id"
    inserted = bool(db._result_rows(db._execute(sql)))
    rows = db._result_rows(db._execute("SELECT payload_json FROM nba_research_predictions WHERE record_id=" + db._sql_text(record["record_id"])))
    if len(rows) != 1:
        raise InputError("NBA tracking save could not be verified or tipoff passed before the database write. Refresh the tracker before retrying.")
    saved = json.loads(rows[0]["payload_json"])
    verify_record(saved, LOCK_SCHEMA)
    return saved, inserted


def save_observation(record: dict) -> bool:
    verify_record(record, RESULT_SCHEMA)
    db = _backend()
    _init(db)
    values = [record[k] for k in ("observation_id", "game_id", "observed_at")]
    values.append(_payload(record))
    sql = "INSERT INTO nba_research_results (observation_id,game_id,observed_at,payload_json) VALUES (" + ",".join(db._sql_text(v) for v in values) + ") ON CONFLICT DO NOTHING RETURNING observation_id"
    return bool(db._result_rows(db._execute(sql)))


def load_ledger(*, seed_records=()) -> tuple[list[dict], list[dict]]:
    db = _backend()
    _init(db)
    predictions = [json.loads(r["payload_json"]) for r in db._result_rows(db._execute("SELECT payload_json FROM nba_research_predictions ORDER BY locked_at,record_id"))]
    observations = [json.loads(r["payload_json"]) for r in db._result_rows(db._execute("SELECT payload_json FROM nba_research_results ORDER BY observed_at,observation_id"))]
    for o in observations:
        verify_record(o, RESULT_SCHEMA)
    return unique_predictions([*seed_records, *predictions]), observations
