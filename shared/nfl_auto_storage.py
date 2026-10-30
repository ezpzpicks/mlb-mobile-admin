"""Bounded NFL automation reads, reusable model inputs and atomic game saves."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
import zlib

from shared.ncaaf_auto_builder import Database

POLICY = "nfl-auto-rotowire-v1"


def clean(value):
    import pandas as pd
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if hasattr(value, "item"):
        value = value.item()
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return value if isinstance(value, (str, int, float, bool)) else str(value)


class NflDatabase(Database):
    def exists(self, table):
        return bool(self.query(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name={self.turso._sql_text(table)}"))

    def initialize(self):
        with self.writes():
            self.query("CREATE TABLE IF NOT EXISTS nfl_auto_cache (cache_key TEXT PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL)")
            self.query("CREATE TABLE IF NOT EXISTS nfl_auto_runs (game_id TEXT NOT NULL, phase TEXT NOT NULL, policy TEXT NOT NULL, date_key TEXT NOT NULL, observed_at TEXT NOT NULL, input_hash TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(game_id,phase,policy))")
            self.query("CREATE INDEX IF NOT EXISTS nfl_auto_runs_date ON nfl_auto_runs(date_key,policy)")
        from shared.nfl_research_storage import initialize
        initialize(self)

    def rows(self, dataset, day=None, *, where="", columns=None):
        q = self.turso._sql_text
        sql = f"SELECT payload_json FROM dataset_rows WHERE sport='NFL' AND dataset={q(dataset)}"
        if day:
            sql += f" AND date_key={q(day)}"
        if where:
            sql += f" AND ({where})"
        rows = [json.loads(row["payload_json"]) for row in self.query(sql)]
        return [{c: row.get(c, "") for c in columns} for row in rows] if columns else rows

    def schedule(self, day):
        q = self.turso._sql_text
        return self.rows("schedule", where=f"date_key={q(day)} OR json_extract(payload_json,'$.\"Game Date\"')={q(day)} OR substr(json_extract(payload_json,'$.\"Game Time\"'),1,10)={q(day)}")

    def runs(self, day):
        if not self.exists("nfl_auto_runs"):
            return {}
        q = self.turso._sql_text
        return {(r["game_id"], r["phase"]): r for r in self.query(f"SELECT game_id,phase,policy,date_key,observed_at,input_hash FROM nfl_auto_runs WHERE date_key={q(day)} AND policy={q(POLICY)}")}

    def run_payload(self, game_id, phase):
        q = self.turso._sql_text
        rows = self.query(f"SELECT payload_json FROM nfl_auto_runs WHERE game_id={q(game_id)} AND phase={q(phase)} AND policy={q(POLICY)}")
        if not rows:
            return None
        payload = json.loads(rows[0]["payload_json"])
        if payload.get("codec") == "zlib-base64":
            return json.loads(zlib.decompress(base64.b64decode(payload["data"])))
        return payload

    def cached(self, key):
        if not self.exists("nfl_auto_cache"):
            return None
        rows = self.query(f"SELECT observed_at,payload FROM nfl_auto_cache WHERE cache_key={self.turso._sql_text(key)}")
        if not rows:
            return None
        data = json.loads(zlib.decompress(base64.b64decode(rows[0]["payload"])))
        return rows[0]["observed_at"], data

    def cache(self, key, value, now):
        payload = base64.b64encode(zlib.compress(json.dumps(clean(value), separators=(",", ":"), allow_nan=False).encode(), 6)).decode()
        q = self.turso._sql_text
        with self.writes():
            self.query(f"INSERT INTO nfl_auto_cache VALUES ({q(key)},{q(now.isoformat())},{q(payload)}) ON CONFLICT(cache_key) DO UPDATE SET observed_at=excluded.observed_at,payload=excluded.payload")


def records(frame):
    return clean(frame.to_dict("records"))


def _dataset_requests(db, dataset, rows, columns, now, *, gate="1", update=False):
    """Update exact existing identities; never replace or scan a whole dataset."""
    q = db.turso._sql_text
    output = []
    keys = {"daily_slate": ["Date", "Away Team", "Home Team"], "prop_projections": ["Game ID", "Player", "Market"],
        "prop_tracker": ["Game ID", "Player", "Market", "Pick", "Market Line"],
        "bet_tracker": ["Game ID", "Bet Type", "Selection"],
        "lineup_snapshots": ["Game ID", "Team", "Unit", "Slot"],
        "builder_completed": ["Date", "Away Team", "Home Team"],
        "prop_calibration": ["Game ID", "Player", "Market", "Model Version"]}[dataset]
    def req(sql):
        return {"type": "execute", "stmt": {"sql": sql, "args": []}}
    scoped_ids = sorted({str(r.get("Game ID", "")) for r in rows})
    scope = f"sport='NFL' AND dataset={q(dataset)} AND game_key IN ({','.join(q(x) for x in scoped_ids)})"
    if dataset == "daily_slate":
        # Public schedule shells use ESPN event IDs; the model uses nflverse IDs.
        # Count and match this small date partition so a shell is upgraded in
        # place instead of appearing as a second game on Full Slate.
        scope = f"sport='NFL' AND dataset='daily_slate' AND date_key IN ({','.join(q(str(r.get('Date',''))) for r in rows)})"
    counter = "nfl_count_" + dataset
    if rows:
        output.append(req(f"CREATE TEMP TABLE {counter} AS SELECT (SELECT COUNT(*) FROM dataset_rows WHERE {scope}) AS before_count WHERE ({gate})"))
    for raw in rows:
        row = {str(k): "" if v is None else str(v) for k, v in clean(raw).items()}
        payload = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        meta = db.turso._metadata(row)
        where = f"sport='NFL' AND dataset={q(dataset)} AND date_key={q(meta['date_key'])}"
        if row.get("Game ID") and dataset != "daily_slate":
            where += f" AND game_key={q(row['Game ID'])}"
        for key in keys:
            path = '$."' + key + '"'
            where += f" AND COALESCE(json_extract(payload_json,{q(path)}),'')={q(row.get(key, ''))}"
        if update:
            assignments = [f"payload_json=json_patch(payload_json,{q(payload)})", "source_hash=''", f"imported_at={q(now.isoformat())}"]
            assignments += [f"{k}={q(v)}" for k, v in meta.items()]
            # Compare values rather than the digest: another writer may have
            # appended result/audit columns that are absent from this update.
            changed = " OR ".join(f"COALESCE(CAST(json_extract(payload_json,{q('$."'+k+'"')}) AS TEXT),'')<>{q(v)}" for k, v in row.items())
            output.append(req(f"UPDATE dataset_rows SET {','.join(assignments)} WHERE {where} AND ({changed}) AND ({gate})"))
        output.append(req("INSERT INTO dataset_rows (sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) "
            f"SELECT 'NFL',{q(dataset)},COALESCE((SELECT MAX(row_index)+1 FROM dataset_rows WHERE sport='NFL' AND dataset={q(dataset)}),1),{q(payload)},{q(digest)},"
            + ",".join(q(meta[k]) for k in ("date_key", "game_key", "game", "market", "selection", "result", "snapshot_time"))
            + f",{q(now.isoformat())} WHERE ({gate}) AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE {where})"))
    if rows:
        # Adjust the existing manifest by this game's row-count delta. Both
        # counts use the game index; season-long history is never counted.
        output.append(req("INSERT INTO dataset_manifest (sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) "
            f"SELECT 'NFL',{q(dataset)},'admin-turso-native',{q(dataset)},{q(json.dumps(list(dict.fromkeys(columns))))},"
            f"COALESCE((SELECT row_count FROM dataset_manifest WHERE sport='NFL' AND dataset={q(dataset)}),0)"
            f"+(SELECT COUNT(*) FROM dataset_rows WHERE {scope})-COALESCE((SELECT before_count FROM {counter}),0),{q(now.isoformat())},'turso' WHERE ({gate}) "
            "ON CONFLICT(sport,dataset) DO UPDATE SET headers_json=excluded.headers_json,row_count=excluded.row_count,imported_at=excluded.imported_at"))
        output.append(req(f"DROP TABLE {counter}"))
    return output


def save_run(db, builder, result, game, phase, now):
    q = db.turso._sql_text
    gid, day = str(game["Game ID"]), str(game["Date"])
    # Use the real database clock in production as well as the evaluated clock.
    # A cold build crossing kickoff cannot save a purported pregame prediction.
    guard = f"datetime('now')<datetime({q(game['Game Time'])}) AND datetime({q(now.isoformat())})<datetime({q(game['Game Time'])})"
    provenance = result.get("provenance", {})
    if phase != "late":
        from builders.nfl_rotowire_props import SourceError
        from datetime import timedelta
        observations = [(provenance.get("props", {}).get("observed_at"), 30),
            (provenance.get("depth_observed_at"), 15), (provenance.get("game_market_observed_at"), 90)]
        if phase == "pregame":
            observations.append(((provenance.get("inactives") or {}).get("observed_at"), 15))
        for stamp, minutes in observations:
            observed = datetime.fromisoformat(stamp) if stamp else None
            if observed is None or not -timedelta(minutes=1) <= now - observed <= timedelta(minutes=minutes):
                raise SourceError("NFL inputs became stale before the atomic save.")
            guard += f" AND datetime('now')<=datetime({q(stamp)},'+{minutes} minutes')"
    guard += f" AND NOT EXISTS(SELECT 1 FROM nfl_auto_runs WHERE game_id={q(gid)} AND phase={q(phase)} AND policy={q(POLICY)})"
    if phase == "late":
        guard += f" AND EXISTS(SELECT 1 FROM nfl_auto_runs WHERE game_id={q(gid)} AND phase='pregame' AND policy={q(POLICY)})"
    if phase in {"early", "pregame"}:
        guard += f" AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE sport='NFL' AND game_key={q(gid)} AND dataset IN ('bet_tracker','prop_tracker','lineup_snapshots'))"
        pair = f"date_key={q(day)} AND lower(json_extract(payload_json,'$.\"Away Team\"'))=lower({q(game['Away Team'])}) AND lower(json_extract(payload_json,'$.\"Home Team\"'))=lower({q(game['Home Team'])})"
        model_present = "(COALESCE(json_extract(payload_json,'$.\"Model Version\"'),'')<>'' OR COALESCE(json_extract(payload_json,'$.\"Projected Away\"'),'')<>'' OR COALESCE(json_extract(payload_json,'$.\"Projected Home\"'),'')<>'')"
        owned = f"(COALESCE(json_extract(payload_json,'$.\"Build Policy\"'),'')={q(POLICY)} OR instr(COALESCE(json_extract(payload_json,'$.\"Model Version\"'),''),{q('|' + POLICY)})>0)"
        guard += f" AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE sport='NFL' AND dataset='daily_slate' AND (game_key={q(gid)} OR ({pair})) AND {model_present} AND NOT {owned})"
        guard += f" AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE sport='NFL' AND dataset='builder_completed' AND date_key={q(day)} AND lower(json_extract(payload_json,'$.\"Away Team\"'))=lower({q(game['Away Team'])}) AND lower(json_extract(payload_json,'$.\"Home Team\"'))=lower({q(game['Home Team'])}))"
    raw_payload = json.dumps(clean(result), separators=(",", ":"), allow_nan=False)
    payload = (json.dumps({"codec": "zlib-base64", "data": base64.b64encode(zlib.compress(raw_payload.encode(), 6)).decode()}, separators=(",", ":"))
               if len(raw_payload) > 2048 else raw_payload)
    def req(sql): return {"type": "execute", "stmt": {"sql": sql, "args": []}}
    requests = [req("BEGIN IMMEDIATE"), req(f"CREATE TEMP TABLE nfl_save_gate AS SELECT 1 AS allowed WHERE {guard}")]
    gate = "EXISTS(SELECT 1 FROM nfl_save_gate)"
    if phase != "late":
        slate = dict(result["slate"])
        if phase == "early":
            # Public football selectors explicitly exclude "No Play". An
            # arbitrary nonempty status such as "Pending pregame" qualifies.
            slate.update({"Spread Grade": "No Play (pending pregame)", "Total Grade": "No Play (pending pregame)"})
        specs = [(builder.SLATE_TAB, [slate], builder.SLATE_COLUMNS)]
        if phase == "pregame":
            specs += [(builder.PROP_SLATE_TAB, result["props"], builder.PROP_PROJECTION_COLUMNS),
                      (builder.TRACKER_TAB, result["game_tracker"], builder.TRACKER_COLUMNS),
                      (builder.PROP_TRACKER_TAB, result["prop_tracker"], builder.PROP_TRACKER_COLUMNS),
                      (builder.LINEUP_TAB, result["lineups"], builder.LINEUP_COLUMNS),
                      (builder.BUILDER_COMPLETION_TAB, [{"Sport": "NFL", "Date": day, "Game ID": gid,
                        "Away Team": game["Away Team"], "Home Team": game["Home Team"], "Game Label": result["slate"]["Game"], "Saved At": now.isoformat()}], builder.BUILDER_COMPLETION_COLUMNS)]
        for dataset, rows, columns in specs:
            for row in rows:
                row.update({"Build Policy": POLICY, "Build Phase": phase, "Build At": now.isoformat()})
            extra = ["Build Policy", "Build Phase", "Build At", "Game Time"]
            requests += _dataset_requests(db, dataset, rows, [*columns, *extra], now, gate=gate, update=dataset == "daily_slate")
    if phase == "pregame":
        from shared.nfl_research_storage import capture_requests
        requests += capture_requests(db, result, game, now, gate=gate)
    requests += [req(f"INSERT INTO nfl_auto_runs SELECT {q(gid)},{q(phase)},{q(POLICY)},{q(day)},{q(now.isoformat())},{q(result['input_hash'])},{q(payload)} WHERE {gate}"),
        req("SELECT COUNT(*) AS saved FROM nfl_save_gate"), req("DROP TABLE nfl_save_gate"), req("COMMIT")]
    with db.writes():
        responses = db.pipeline(requests, max_attempts=1)
    return bool(int(db.turso._result_rows(responses[len(requests) - 3])[0]["saved"]))


def upsert_results(db, dataset, rows, columns, now):
    if not rows:
        return True
    request = lambda sql: {"type": "execute", "stmt": {"sql": sql, "args": []}}
    with db.writes():
        db.pipeline([request("BEGIN IMMEDIATE"), *_dataset_requests(db, dataset, rows, columns, now, update=True), request("COMMIT")], max_attempts=1)
    return True
