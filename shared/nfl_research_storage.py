"""Append-once NFL forecasts with indexed, result-only settlement updates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os

from builders.nfl_research import POLICY, PARAMETER_HASH, PARAMETER_JSON, START_DATE, number, settle
from shared.ncaaf_auto_builder import timestamp


def request(sql):
    return {"type": "execute", "stmt": {"sql": sql, "args": []}}


def initialize(db):
    q = db.turso._sql_text
    sql = [
        "CREATE TABLE IF NOT EXISTS nfl_research_policies (policy TEXT PRIMARY KEY, parameter_hash TEXT NOT NULL, parameters_json TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS nfl_research_games (game_id TEXT NOT NULL, policy TEXT NOT NULL, date_key TEXT NOT NULL, kickoff TEXT NOT NULL, captured_at TEXT NOT NULL, input_hash TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(game_id,policy))",
        "CREATE TABLE IF NOT EXISTS nfl_research_predictions (prediction_key TEXT PRIMARY KEY, game_id TEXT NOT NULL, policy TEXT NOT NULL, date_key TEXT NOT NULL, kickoff TEXT NOT NULL, strategy TEXT NOT NULL, market TEXT NOT NULL, player TEXT NOT NULL, selected INTEGER NOT NULL, result TEXT NOT NULL DEFAULT '', units REAL, settled_at TEXT, payload_json TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS nfl_research_pending ON nfl_research_predictions(policy,selected,result,kickoff)",
        "CREATE INDEX IF NOT EXISTS nfl_research_dates ON nfl_research_predictions(policy,date_key)",
        f"INSERT OR IGNORE INTO nfl_research_policies VALUES ({q(POLICY)},{q(PARAMETER_HASH)},{q(PARAMETER_JSON)})",
    ]
    with db.writes():
        db.pipeline([request(s) for s in sql])
    rows = db.query(f"SELECT parameter_hash FROM nfl_research_policies WHERE policy={q(POLICY)}")
    if not rows or rows[0]["parameter_hash"] != PARAMETER_HASH:
        raise RuntimeError("NFL research parameters changed without a new policy version.")


def capture_requests(db, result, game, now, *, gate="1"):
    """Included in the official save transaction; same SQL kickoff guard."""
    research = result.get("research")
    if not research:
        return []
    if research.get("policy") != POLICY or research.get("parameter_hash") != PARAMETER_HASH:
        raise ValueError("Unexpected NFL research policy.")
    kickoff = timestamp(game.get("Game Time"))
    if kickoff is None or now >= kickoff or str(game.get("Date", "")) < START_DATE or str(game.get("Game ID", "")).startswith("TEST"):
        return []
    kickoff = kickoff.astimezone(timezone.utc)
    q = db.turso._sql_text; gid = str(game["Game ID"]); day = str(game["Date"])
    valid = f"({gate}) AND datetime('now')<datetime({q(kickoff.isoformat())}) AND datetime({q(now.isoformat())})<datetime({q(kickoff.isoformat())})"
    unique = f"game_id={q(gid)} AND policy={q(POLICY)}"
    # The temp gate is evaluated BEFORE inserting the game. Duplicate captures
    # cannot fill previously skipped/missing predictions or move a saved line.
    sql = [f"CREATE TEMP TABLE nfl_research_capture_gate AS SELECT 1 WHERE {valid} AND NOT EXISTS(SELECT 1 FROM nfl_research_games WHERE {unique})"]
    allowed = "EXISTS(SELECT 1 FROM nfl_research_capture_gate)"
    context = {"slate": result.get("slate", {}), "context": research.get("context", {}),
        "input_hash": result.get("input_hash", ""), "captured_at": now.isoformat(),
        "source_commit": os.getenv("RENDER_GIT_COMMIT", ""), "policy": POLICY, "parameter_hash": PARAMETER_HASH}
    payload = json.dumps(context, separators=(",", ":"), allow_nan=False)
    sql.append(f"INSERT INTO nfl_research_games SELECT {q(gid)},{q(POLICY)},{q(day)},{q(kickoff.isoformat())},{q(now.isoformat())},{q(result.get('input_hash',''))},{q(payload)} WHERE {allowed}")
    for entry in research.get("predictions", []):
        identity = "|".join([POLICY, gid, entry["strategy"], entry.get("team", ""), entry.get("player", ""), entry["market"]])
        key = hashlib.sha256(identity.encode()).hexdigest()
        payload = {**entry, "policy": POLICY, "game_id": gid, "date": day,
            "season": int(game["Season"]), "week": int(game["Week"]), "game": result["slate"].get("Game", gid),
            "kickoff": kickoff.isoformat(), "captured_at": now.isoformat(), "input_hash": result.get("input_hash", ""),
            "parameter_hash": PARAMETER_HASH}
        data = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        sql.append("INSERT INTO nfl_research_predictions (prediction_key,game_id,policy,date_key,kickoff,strategy,market,player,selected,payload_json) "
            f"SELECT {q(key)},{q(gid)},{q(POLICY)},{q(day)},{q(kickoff.isoformat())},{q(entry['strategy'])},{q(entry['market'])},{q(entry.get('player',''))},{int(entry['selected'])},{q(data)} WHERE {allowed}")
    sql += ["SELECT COUNT(*) AS captured FROM nfl_research_capture_gate", "DROP TABLE nfl_research_capture_gate"]
    return [request(s) for s in sql]


def capture_manual(slate, props, league_ratings, away, home, schedule_row, tracked_props=None):
    """Capture a successful scheduled manual save, never a test/backdated game."""
    if schedule_row is None:
        return False
    from builders.nfl_research import attach
    from shared.nfl_auto_storage import NflDatabase
    from shared import turso_storage as turso
    now = datetime.now(timezone.utc)
    value = dict(slate)
    kickoff = timestamp(value.get("Game Time")) or timestamp(schedule_row.get("Game Time")) or timestamp(str(value["Date"]) + "T" + str(schedule_row.get("Game Time", "")))
    if kickoff is None or now >= kickoff or str(value["Date"]) < START_DATE or str(value.get("Game ID", "")).startswith("TEST"):
        return False
    value["Game Time"] = kickoff.isoformat()
    game = {k: value[k] for k in ("Date", "Season", "Week", "Game ID", "Game Time")}
    result = {"slate": value, "props": props, "input_hash": hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest(),
        "provenance": {"ratings": {"Away": away, "Home": home}, "game_market_observed_at": now.isoformat()}}
    if tracked_props is not None:
        result["prop_tracker"] = tracked_props
    from shared.nfl_auto_storage import clean
    result = clean(attach(result, league_ratings, price_source="manual_input"))
    db = NflDatabase(turso)
    try:
        initialize(db)
        requests = [request("BEGIN IMMEDIATE"), *capture_requests(db, result, game, now), request("COMMIT")]
        with db.writes():
            responses = db.pipeline(requests, max_attempts=1)
        return bool(int(db.turso._result_rows(responses[len(requests) - 3])[0]["captured"]))
    finally:
        turso._pipeline = db.original_pipeline


def pending(db, now):
    if not db.exists("nfl_research_predictions"):
        return []
    q = db.turso._sql_text
    # Index bounds prevent a full archive read on each existing cron cycle.
    lower = (now - timedelta(days=21)).isoformat(); upper = (now - timedelta(hours=4)).isoformat()
    return db.query(f"SELECT prediction_key,game_id,payload_json FROM nfl_research_predictions WHERE policy={q(POLICY)} AND selected=1 AND result='' AND kickoff>={q(lower)} AND kickoff<={q(upper)} LIMIT 1000")


def settle_rows(db, rows, finals, actuals, now):
    """Only the result columns are mutable. No forecast payload is updated."""
    q = db.turso._sql_text; commands = []
    for row in rows:
        entry = json.loads(row["payload_json"]); scores = finals.get(row["game_id"])
        if not scores:
            continue
        actual = None
        if entry["market"] == "Receiving Yards":
            key = (entry["game_id"], entry.get("team", ""), entry.get("player", ""))
            observed = actuals.get(key)
            # A zero with no confirmed participation is unresolved, not a free
            # Under win or an assumed void. Book-specific voids need review.
            if observed is None or not observed.get("participated"):
                continue
            actual = observed["yards"]
        graded = settle(entry, away=scores[0], home=scores[1], actual=actual)
        if graded is None:
            continue
        commands.append(request(f"UPDATE nfl_research_predictions SET result={q(graded['result'])},units={graded['units']},settled_at={q(now.isoformat())} WHERE prediction_key={q(row['prediction_key'])} AND policy={q(POLICY)} AND result=''"))
    if commands:
        with db.writes():
            db.pipeline([request("BEGIN IMMEDIATE"), *commands, request("COMMIT")], max_attempts=1)
    return len(commands)


def settle_recent(db, builder, now):
    rows = pending(db, now)
    if not rows:
        return 0
    from shared.nfl_auto_storage import records
    import pandas as pd
    wanted = {r["game_id"] for r in rows}; seasons = {json.loads(r["payload_json"])["season"] for r in rows}
    finals, stats_cache = {}, {}
    for season in seasons:
        for r in records(builder._schedule_for_season(season, refresh=True)):
            away = number(r.get("Away Score")); home = number(r.get("Home Score"))
            status = str(r.get("Status", "")).lower()
            if str(r.get("Completed", "")).lower() in {"false", "0", "no"} or status in {"live", "in_progress", "in progress", "scheduled"}:
                continue
            if str(r.get("Game ID")) in wanted and away is not None and home is not None and away >= 0 and home >= 0 and away + home > 0:
                finals[str(r["Game ID"])] = (away, home)
    if not finals:
        return 0
    props = [json.loads(r["payload_json"]) for r in rows if json.loads(r["payload_json"])["market"] == "Receiving Yards" and r["game_id"] in finals]
    actuals = {}
    for entry in props:
        season = entry["season"]
        if season not in stats_cache:
            stats = builder._load_player_stats_season(season).copy()
            if not stats.empty:
                stats["_name"] = builder._player_name_column(stats).map(builder._normalize_name)
                stats["_team"] = builder._player_team_column(stats).map(builder._normalize_team)
            stats_cache[season] = stats
        stats = stats_cache[season]
        if stats.empty:
            continue
        same = stats[(stats["_name"] == builder._normalize_name(entry["player"])) &
            (stats["_team"] == builder._normalize_team(entry["team"])) &
            (pd.to_numeric(stats["week"], errors="coerce") == int(entry["week"]))]
        if len(same) != 1:
            continue
        stat = same.iloc[0]; yards = number(stat.get("receiving_yards"))
        participated = any((number(stat.get(k)) or 0) > 0 for k in ("carries", "targets", "receptions", "offense_snaps")) or (yards is not None and yards != 0)
        if yards is not None:
            actuals[(entry["game_id"], entry["team"], entry["player"])] = {"yards": yards, "participated": participated}
    return settle_rows(db, rows, finals, actuals, now)


def read_rows(db, limit=5000):
    if not db.exists("nfl_research_predictions"):
        return []
    q = db.turso._sql_text
    rows = db.query(f"SELECT payload_json,result,units,settled_at FROM nfl_research_predictions WHERE policy={q(POLICY)} ORDER BY date_key DESC LIMIT {min(5000, max(1, int(limit)))}")
    return [{**json.loads(r["payload_json"]), "result": r["result"], "units": number(r["units"]), "settled_at": r["settled_at"]} for r in rows]
