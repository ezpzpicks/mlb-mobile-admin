"""Date-indexed NCAAM reads and atomic, idempotent forecast saves."""
from __future__ import annotations

import json

SPORT = "NCAAM"
FORECASTS = "ncaam_model_forecasts"
RATINGS = "ncaam_rating_inputs"
MATCHUP_GRADES = "ncaam_matchup_total_grades"


def rows(db, dataset, day):
    q = db._sql_text
    data = db._result_rows(db._execute(
        f"SELECT payload_json FROM dataset_rows WHERE sport='NCAAM' AND dataset={q(dataset)} AND date_key={q(day)}"))
    return [json.loads(row["payload_json"]) for row in data]


def rows_for_keys(db, dataset, keys):
    """One bounded indexed read for requested team rosters."""
    if not keys: return []
    if len(set(keys)) > 400: raise ValueError('Too many NCAAM input keys.')
    q = db._sql_text
    data = db._result_rows(db._execute(
        f"SELECT payload_json FROM dataset_rows WHERE sport='NCAAM' AND dataset={q(dataset)} AND date_key IN ({','.join(q(key) for key in sorted(set(keys)))})"))
    return [json.loads(row['payload_json']) for row in data]


def upsert(db, dataset, row, *, immutable=False):
    """The transaction protects stable positions and the first saved forecast."""
    upsert_many(db, [(dataset, row, immutable)])


def upsert_many(db, entries):
    """Save a forecast and its grade together; retain immutable capture identities."""
    if not entries:
        return
    q = db._sql_text
    statements = []
    manifests = {}
    for dataset, row, immutable in entries:
        identity = (f"sport='NCAAM' AND dataset={q(dataset)} AND date_key={q(row['Date'])} "
                    f"AND game_key={q(row['Game Key'])} AND market={q(row.get('Market', ''))} AND selection={q(row.get('Selection', ''))}")
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
        values = [q(SPORT), q(dataset), f"(SELECT COALESCE(MAX(row_index),0)+1 FROM dataset_rows WHERE sport='NCAAM' AND dataset={q(dataset)})",
                  q(payload), "''", q(row["Date"]), q(row["Game Key"]), q(row.get("Game", "")), q(row.get("Market", "")),
                  q(row.get("Selection", "")), q(row.get("Result", "")), q(row.get("Snapshot Time ET", "")), q(row.get("Updated At", ""))]
        statements.append(f"INSERT INTO dataset_rows (sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) SELECT {','.join(values)} WHERE NOT EXISTS (SELECT 1 FROM dataset_rows WHERE {identity})")
        if not immutable:
            statements.append(f"UPDATE dataset_rows SET payload_json={q(payload)}, imported_at={q(row.get('Updated At', ''))} WHERE {identity} AND payload_json!={q(payload)}")
        manifests[dataset] = row
    for dataset, row in manifests.items():
        statements.append(f"INSERT INTO dataset_manifest (sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) VALUES ('NCAAM',{q(dataset)},'turso-native',{q(dataset)},{q(json.dumps(list(row)))},(SELECT COALESCE(MAX(row_index),0) FROM dataset_rows WHERE sport='NCAAM' AND dataset={q(dataset)}),{q(row.get('Updated At',''))},'turso') ON CONFLICT(sport,dataset) DO UPDATE SET row_count=excluded.row_count,headers_json=excluded.headers_json,imported_at=excluded.imported_at")
    # Hrana sequences stop on error. Closing an uncommitted stream rolls back.
    db._pipeline([{"type": "sequence", "sql": ";\n".join(["BEGIN IMMEDIATE", *statements, "COMMIT"])}])
