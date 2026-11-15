"""Retrospective new-formula replay using frozen factors, never final outcomes.

The replay is computed now. Its data were captured before kickoff; the new model
was not published then. It cannot enter the new live model's betting record.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path

from builders.cfb_market_relative_matchups import comparison,finite,from_saved_row
from builders.cfb_matchup_projection import MODEL_VERSION,PROJECTION_POLICY,project_from_factors


def replay(rows):
    games,errors=[],[]
    for row in rows:
        try:
            original=from_saved_row(row)
            factors=json.loads(row["Matchup Factors JSON"])
            capture=json.loads(row["Research Snapshot JSON"])
            if capture.get("captureStatus")!="PRE_GAME" or capture.get("gameId")!=str(row["Game ID"]):
                raise ValueError("Verified original model-overlay capture required")
            from builders.cfb_market_relative_matchups import instant
            if not instant(capture["capturedAt"]) < instant(original["kickoff"]):
                raise ValueError("Live overlays were not captured before kickoff")
            components=capture["components"]
            neutral=str(row.get("Neutral Site",False)).strip().lower() in ("true","yes","1")
            adj={"home_field":0. if neutral else finite(row["Home Field Advantage"]),
                "personnel_margin":finite(components["regression_personnel_delta"]),
                "personnel_total":finite(components["total_regression_personnel_delta"]),
                "continuity_margin":finite(components["regression_continuity_delta"]),
                "continuity_total":finite(components["total_regression_continuity_delta"]),
                "weather_total":finite(components["total_regression_weather_delta"])}
            # Preserve the input-decision clock, while separately identifying
            # this as a retrospective computation rather than a live decision.
            data_as_of=max(instant(original["as_of"]),instant(capture["capturedAt"])).isoformat()
            p=project_from_factors(factors,as_of=data_as_of,kickoff=original["kickoff"],adjustments=adj)
            odds={c["side"]:c["odds"] for c in original["alternatives"]}
            quote=comparison(factors,projected_margin=p["margin"],projected_total=p["total"],
                home_line=original["home_line"],total_line=original["total_line"],as_of=data_as_of,
                kickoff=original["kickoff"],odds=odds,model_version=MODEL_VERSION,policy_version=PROJECTION_POLICY)
            games.append({"game_id":original["game_id"],"date":original["date"],"game":original["game"],
                "teams":original["teams"],"kickoff":original["kickoff"],"original_forecast":{
                    "home_margin":original["projected_home_margin"],"total":original["projected_total"]},
                "projection":p,"comparison":quote})
        except (ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
            errors.append({"game_id":row.get("Game ID"),"reason":str(exc)})
    plays=[]
    for g in games:
        for c in g["comparison"]["alternatives"]:
            if c["edge_points"]>0:
                plays.append({"game_id":g["game_id"],"game":g["game"],"market":c["market"],
                    "role":c["role"],"side":c["side"],"team":c.get("team"),"line":c["line"],
                    "edge_points":c["edge_points"],"projected_home_margin":g["projection"]["margin"],
                    "projected_total":g["projection"]["total"]})
    plays.sort(key=lambda p:(-p["edge_points"],p["game"],p["side"]))
    return {"computed_at":datetime.now(timezone.utc).isoformat(),"model_version":MODEL_VERSION,
        "record_mode":"RETROSPECTIVE_FORMULA_REPLAY","uses_final_results":False,
        "games":games,"errors":errors,"ranked_point_gaps":plays,
        "positive_edge_counts":{role:sum(p["role"]==role for p in plays) for role in ("Favorite","Underdog","Over","Under")}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slate",required=True);parser.add_argument("--output",required=True)
    args=parser.parse_args();payload=json.loads(Path(args.slate).read_text())
    rows=payload if isinstance(payload,list) else payload["slateToday"]
    result=replay(rows);Path(args.output).write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"games":len(result["games"]),"errors":result["errors"],
        "positive_edge_counts":result["positive_edge_counts"],"top10":result["ranked_point_gaps"][:10]},indent=2))


if __name__=="__main__":main()
