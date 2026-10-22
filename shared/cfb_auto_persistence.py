"""Atomic, keyed persistence for the unattended NCAAF builder."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from shared.ncaaf_auto_builder import ASSUMED_ODDS, AUTO_TAB, PRICE_NOTE


def save_once_transaction(db, builder, result, market, now=None):
    from shared import turso_storage as turso
    from builders.cfb_interactive_recovery import _normalized_records, _storage_metadata

    q = turso._sql_text
    game = result["game"]
    game_id, day = str(game["Game ID"]), market.date
    stamp = datetime.now(timezone.utc).isoformat()
    clock = f"datetime({q(now.isoformat())})" if now else "datetime('now')"
    game_where = f"sport='NCAAF' AND game_key={q(game_id)}"
    pair_where = (
        f"lower(json_extract(payload_json,'$.\"Away Team\"'))=lower({q(game['Away Team'])}) AND "
        f"lower(json_extract(payload_json,'$.\"Home Team\"'))=lower({q(game['Home Team'])})"
    )
    guard = (
        f"{clock}<datetime({q(market.kickoff.isoformat())}) "
        f"AND {clock}<=datetime({q(market.captured.isoformat())},'+90 minutes') "
        f"AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE {game_where} "
        f"AND dataset IN ({q(AUTO_TAB)},'bet_tracker','personnel_snapshots')) "
        f"AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE sport='NCAAF' AND dataset='builder_completed' "
        f"AND date_key={q(day)} AND {pair_where}) "
        f"AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE {game_where} AND dataset='daily_slate' "
        "AND (COALESCE(json_extract(payload_json,'$.\"Actual Away\"'),'')<>'' "
        "OR COALESCE(json_extract(payload_json,'$.\"Actual Home\"'),'')<>''))"
    )
    slate = builder.slate_row(result).copy()
    tracker = builder.tracker_rows(result, False).copy()
    # The unattended trial owns spreads/totals only, even if a future model
    # accidentally returns a graded moneyline when its market is unavailable.
    if not tracker.empty:
        tracker = tracker[tracker["Bet Type"].isin(["Spread", "Total"])].copy()
    personnel = [
        builder.personnel_row(result[f"{side}_personnel"], game[f"{side.title()} Team"],
                              int(float(game["Season"])), int(float(game["Week"])), game_id)
        for side in ("away", "home")
    ]
    extension = ["Build Source", "Build Captured At", "Odds Assumption"]
    specs = []
    for dataset, frame, columns, keys in [
        (builder.SLATE_TAB, slate, list(builder.SLATE_COLUMNS), ["Date", "Game ID"]),
        (builder.TRACKER_TAB, tracker, list(builder.TRACKER_COLUMNS), ["Date", "Game ID", "Bet Type"]),
        *[(builder.PERSONNEL_TAB, frame, list(builder.PERSONNEL_COLUMNS), ["Season", "Game ID", "Team"]) for frame in personnel],
    ]:
        if frame.empty:
            continue
        frame["Build Source"] = "Scheduled ScoresAndOdds builder"
        frame["Build Captured At"] = market.captured.isoformat()
        frame["Odds Assumption"] = str(ASSUMED_ODDS)
        if "Notes" in frame.columns:
            frame["Notes"] = frame["Notes"].fillna("").astype(str) + "; " + PRICE_NOTE
        columns = list(dict.fromkeys([*columns, *extension]))
        specs.append((dataset, _normalized_records(frame, columns), columns, keys))

    # Keep the same personnel history as an explicit admin save, but flush only
    # after this game's transaction has passed its manual-save protection gate.
    from builders import cfb_covers
    for kind, dataset, columns, keys in [
        ("starters", cfb_covers.STARTER_HISTORY_TAB, cfb_covers.STARTER_HISTORY_COLUMNS,
         ["Season", "Week", "Team", "Unit", "Position", "Player"]),
        ("injuries", cfb_covers.INJURY_HISTORY_TAB, cfb_covers.INJURY_HISTORY_COLUMNS,
         ["Observed Date", "Team", "Player", "Status"]),
    ]:
        pending = cfb_covers._pending(builder, kind)
        if pending:
            import pandas as pd
            specs.append((dataset, _normalized_records(pd.DataFrame(pending), columns), list(columns), keys))

    completion = {"Sport": "CFB", "Date": day, "Game ID": game_id,
                  "Away Team": str(game["Away Team"]), "Home Team": str(game["Home Team"]),
                  "Game Label": str(slate.iloc[0]["Game"]), "Saved At": stamp,
                  "Build Source": "Scheduled ScoresAndOdds builder"}
    audit = {**completion, "Model Version": str(builder.MODEL_VERSION),
             "Market Home Spread": str(market.home_spread), "Market Total": str(market.total),
             "Odds Assumption": str(ASSUMED_ODDS), "Market Captured At": market.captured.isoformat(),
             "Market Game Key": market.source_key}
    specs.extend([
        ("builder_completed", [completion], list(completion), ["Date", "Game ID"]),
        (AUTO_TAB, [audit], list(audit), ["Date", "Game ID"]),
    ])

    def request(sql):
        return {"type": "execute", "stmt": {"sql": sql, "args": []}}

    requests = [request("BEGIN IMMEDIATE"),
                request(f"CREATE TEMP TABLE ncaaf_builder_gate AS SELECT 1 AS allowed WHERE {guard}")]
    manifests = {}
    for dataset, records, columns, keys in specs:
        manifests[dataset] = columns
        for row in records:
            metadata = _storage_metadata(row)
            # Personnel snapshots do not have a Date column in the legacy schema.
            # Set indexed metadata explicitly so subsequent checks remain bounded.
            metadata["date_key"] = day
            metadata["game_key"] = game_id
            payload = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            where = f"sport='NCAAF' AND dataset={q(dataset)} AND game_key={q(game_id)}"
            for key in keys:
                path = '$."' + key.replace('"', '\\"') + '"'
                where += f" AND json_extract(payload_json,{q(path)})={q(row.get(key, ''))}"
            assignments = [f"payload_json=json_patch(payload_json,{q(payload)})", "source_hash=''",
                           f"imported_at={q(stamp)}"]
            assignments += [f"{name}={q(value)}" for name, value in metadata.items()]
            gate = "EXISTS(SELECT 1 FROM ncaaf_builder_gate)"
            requests.append(request(f"UPDATE dataset_rows SET {','.join(assignments)} WHERE {where} AND {gate}"))
            requests.append(request(
                "INSERT INTO dataset_rows "
                "(sport,dataset,row_index,payload_json,source_hash,date_key,game_key,game,market,selection,result,snapshot_time,imported_at) "
                f"SELECT 'NCAAF',{q(dataset)},COALESCE((SELECT MAX(row_index)+1 FROM dataset_rows "
                f"WHERE sport='NCAAF' AND dataset={q(dataset)}),1),{q(payload)},{q(digest)},"
                + ",".join(q(metadata[name]) for name in ("date_key", "game_key", "game", "market", "selection", "result", "snapshot_time"))
                + f",{q(stamp)} WHERE {gate} AND NOT EXISTS(SELECT 1 FROM dataset_rows WHERE {where})"
            ))
    for dataset, columns in manifests.items():
        headers = q(json.dumps(columns, separators=(",", ":")))
        requests.append(request(
            "INSERT INTO dataset_manifest "
            "(sport,dataset,source_workbook,source_worksheet,headers_json,row_count,imported_at,source_kind) "
            f"SELECT 'NCAAF',{q(dataset)},'admin-turso-native',{q(dataset)},{headers},"
            f"(SELECT COUNT(*) FROM dataset_rows WHERE sport='NCAAF' AND dataset={q(dataset)}),"
            f"{q(stamp)},'turso' WHERE EXISTS(SELECT 1 FROM ncaaf_builder_gate) "
            "ON CONFLICT(sport,dataset) DO UPDATE SET headers_json=excluded.headers_json,"
            "row_count=excluded.row_count,imported_at=excluded.imported_at"
        ))
    requests.extend([request("SELECT COUNT(*) AS saved FROM ncaaf_builder_gate"),
                     request("DROP TABLE ncaaf_builder_gate"), request("COMMIT")])
    with db.writes():
        results = db.pipeline(requests, timeout=30.0, max_attempts=2)
    # The HTTP pipeline appends a close response after our SQL responses.
    # Address this SELECT by its request index, not relative to the response tail.
    rows = turso._result_rows(results[len(requests) - 3])
    return bool(rows and int(rows[0]["saved"]))
