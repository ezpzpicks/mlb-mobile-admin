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
    db._execute("CREATE INDEX IF NOT EXISTS nba_research_predictions_by_time ON nba_research_predictions(locked_at,game_id)")
    db._execute("CREATE INDEX IF NOT EXISTS nba_research_results_by_game ON nba_research_results(game_id,observed_at DESC)")


def initialize_tracking():
    _init(_backend())


def _read_if_initialized(db, sql):
    try:
        return db._result_rows(db._execute(sql))
    except Exception as exc:
        if "no such table" in str(exc).lower():
            return []
        raise


def prediction_keys(game_ids, model_versions):
    """Bounded indexed key read; do not load season-sized forecast archives."""
    if not game_ids or not model_versions:
        return set()
    db = _backend()
    q = db._sql_text
    rows = _read_if_initialized(db, "SELECT game_id,model_version FROM nba_research_predictions "
        "WHERE experiment_id=" + q("nba-2026-27-prospective-v1") + " AND model_version IN (" +
        ",".join(q(v) for v in model_versions) + ") AND game_id IN (" + ",".join(q(v) for v in game_ids) + ")")
    return {(r["game_id"], r["model_version"]) for r in rows}


def latest_observation(game_id):
    db = _backend()
    rows = _read_if_initialized(db, "SELECT payload_json FROM nba_research_results WHERE game_id=" +
        db._sql_text(game_id) + " ORDER BY observed_at DESC,observation_id DESC LIMIT 1")
    if not rows:
        return None
    observation = json.loads(rows[0]["payload_json"])
    verify_record(observation, RESULT_SCHEMA)
    return observation


def pending_game_ids(since, *, seed_records=()):
    """Only recent, unsettled IDs; finished games do not get polled forever."""
    db = _backend()
    rows = _read_if_initialized(db, "SELECT DISTINCT p.game_id FROM nba_research_predictions p "
        "WHERE p.locked_at>=" + db._sql_text(since) + " AND COALESCE((SELECT "
        "json_extract(r.payload_json,'$.game.status') FROM nba_research_results r "
        "WHERE r.game_id=p.game_id ORDER BY r.observed_at DESC,r.observation_id DESC LIMIT 1),'')!='STATUS_FINAL'")
    pending = {r["game_id"] for r in rows}
    for record in seed_records:
        verify_record(record, LOCK_SCHEMA)
        if record["locked_at"] >= since:
            last = latest_observation(record["game_id"])
            if last is None or last["game"]["status"] != "STATUS_FINAL":
                pending.add(record["game_id"])
    return sorted(pending)


def save_observation_if_changed(record):
    """Do not rewrite identical scores/boxes just for a new retrieval time."""
    verify_record(record, RESULT_SCHEMA)
    previous = latest_observation(record["game_id"])
    def facts(value):
        return {"game": {k: v for k, v in value["game"].items() if k != "source"},
                "box_minutes": value.get("box_minutes"), "box_error": value.get("box_error", "")}
    if previous is not None and facts(previous) == facts(record):
        return False
    return save_observation(record)


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
