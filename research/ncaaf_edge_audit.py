"""Offline audit of frozen NCAAF forecasts; no network calls or production writes.

Run from this repository: python research/ncaaf_edge_audit.py --inputs DIR --output DIR
The component removals are additive sensitivity replays around saved simulation
means, not exact historical engine reruns. The unarchived simulation/floor effects
and spread regime correction remain in the reconstructed base.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unicodedata

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builders import cfb_schedule_adjustment as schedule_adjustment
from builders import cfb_game_regression as spread_regression
from builders import cfb_total_regression as total_regression

CURRENT = "cfb-v2.8.1-five-game-residual-totals-2026-10-07"
PROTOCOL = {
    "version": "ncaaf-saved-forecast-audit-2026-10-30-v1",
    "primary_metric": "one-unit-risk profit at the saved side-specific price",
    "general_windows": {"development": [1,2,3,4], "validation": [5,6], "later_check": [7,8,9]},
    "current_version_windows": {"selection": [6,7], "later_check": [8,9]},
    "current_component_selection": "among candidates with >=30 early plays and positive early units, maximize early units; otherwise no selection",
    "component_gate": "total absolute point edge >=3, unchanged across candidates",
    "candidate_weights": {"full": [1,1,1,1,1], "no_weather": [1,0,1,1,1],
        "no_personnel": [1,1,0,1,1], "no_continuity": [1,1,1,0,1],
        "no_schedule": [0,1,1,1,0], "half_schedule": [.5,1,1,1,.5],
        "no_offense_ramp": [1,1,1,1,0], "no_live_overlays": [1,0,0,0,1],
        "reconstructed_base": [0,0,0,0,0]},
    "weight_order": ["schedule", "weather", "personnel", "continuity", "offense_ramp"],
    "limits": ["Retrospective chronological checks, not a newly untouched experiment: broad outcomes were already reviewed.",
        "Nine related candidates; no best full-sample winner is promoted.",
        "One season and correlated games/weeks; normal independent-bet intervals overstate certainty.",
        "Saved weekly ratings can be refreshed after games; they are excluded as predictors.",
        "Component values are reconstructed from saved personnel and strictly prior-date scoring, not archived intermediate forecasts.",
        "The base retains unknown simulation/floor and spread-regime effects; detailed pace/efficiency and regime ablations require forward snapshots."]}

def number(value, default=float("nan")):
    try:
        n=float(value)
        return n if math.isfinite(n) else default
    except (TypeError,ValueError):
        return default

def truth(value):
    return str(value).lower().strip() in {"true","yes","1","y","completed","final"}

def canon(value):
    value=unicodedata.normalize("NFKD",str(value or ""))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).replace("&","and").split())

def load(path):
    return json.loads(path.read_text())

def result_value(actual, line, direction):
    delta=(actual-line)*direction
    return 1 if delta>1e-7 else -1 if delta < -1e-7 else 0

def profit(result, odds):
    return (100/abs(odds) if odds<0 else odds/100) if result>0 else -1.0 if result<0 else 0.0

def record(rows):
    if not rows:
        return {"n":0,"wins":0,"losses":0,"pushes":0,"units":0,"roi_pct":None,"win_pct":None,"wilson95_pct":None}
    w=sum(r["result"]==1 for r in rows);l=sum(r["result"]==-1 for r in rows);p=len(rows)-w-l
    units=sum(r["profit"] for r in rows);n=w+l;z=1.959963984540054
    phat=w/n if n else 0;den=1+z*z/n if n else 1
    center=(phat+z*z/(2*n))/den if n else 0
    width=z*math.sqrt(phat*(1-phat)/n+z*z/(4*n*n))/den if n else 0
    return {"n":len(rows),"wins":w,"losses":l,"pushes":p,"units":round(units,6),
        "roi_pct":round(100*units/len(rows),4),"win_pct":round(100*phat,4) if n else None,
        "wilson95_pct":[round(100*(center-width),2),round(100*(center+width),2)] if n else None}

def by(rows, key):
    groups=defaultdict(list)
    for r in rows:groups[str(key(r))].append(r)
    return {k:record(v) for k,v in sorted(groups.items())}

def personnel(row):
    names={"qb_continuity":"QB Continuity","qb_adjustment":"QB Adjustment","ol_adjustment":"OL Adjustment",
        "skill_adjustment":"Skill Adjustment","dl_adjustment":"Defensive Line Adjustment",
        "linebacker_adjustment":"Linebacker Adjustment","secondary_adjustment":"Secondary Adjustment",
        "kicker_adjustment":"Kicker Adjustment","special_teams_adjustment":"Special Teams Adjustment",
        "coaching_continuity":"Coaching Continuity","coordinator_continuity":"Coordinator Continuity"}
    p=SimpleNamespace(**{k:number(row.get(v), .5 if k=="qb_continuity" else .75 if k=="coaching_continuity" else .67 if k=="coordinator_continuity" else 0) for k,v in names.items()})
    p.offense_adjustment=p.qb_adjustment+p.ol_adjustment+p.skill_adjustment
    p.defense_adjustment=p.dl_adjustment+p.linebacker_adjustment+p.secondary_adjustment
    return p

class PriorSchedule:
    SCHEDULE_TAB="schedule"
    SCHEDULE_COLUMNS=[]
    _canonical_team_name=staticmethod(canon)
    def __init__(self, rows):self.rows=pd.DataFrame(rows)
    def read_sheet(self, *_):return self.rows.copy()

def make_games(inputs):
    slates=load(inputs/"daily_slate.json");schedules=load(inputs/"schedule.json")
    personnel_rows=load(inputs/"personnel_snapshots.json");tracker=load(inputs/"bet_tracker.json")
    schedule={str(x.get("Game ID")):x for x in schedules}
    people={(str(x.get("Game ID")),canon(x.get("Team"))):x for x in personnel_rows}
    prior=PriorSchedule(schedules);contexts={};games=[];missing=[]
    seen=set()
    for row in slates:
        game_id=str(row.get("Game ID",""));key=(row.get("Date"),game_id)
        if key in seen:raise ValueError(f"Duplicate saved forecast {key}")
        seen.add(key);s=schedule.get(game_id,{})
        if not truth(s.get("Completed")) or not math.isfinite(number(s.get("Away Score"))) or not math.isfinite(number(s.get("Home Score"))):continue
        version=row.get("Model Version","");week=int(number(row.get("Week"),0));date=str(row.get("Date"))[:10]
        if not version or week<1:continue
        away=canon(row.get("Away Team"));home=canon(row.get("Home Team"))
        p_a=people.get((game_id,away));p_h=people.get((game_id,home))
        g={"id":game_id,"date":date,"week":week,"game":row.get("Game"),"away":away,"home":home,
            "version":version,"current":version==CURRENT,"fbs":str(s.get("Away Classification","")).lower()=="fbs" and str(s.get("Home Classification","")).lower()=="fbs",
            "actual_total":number(s["Away Score"])+number(s["Home Score"]),
            "actual_margin":number(s["Home Score"])-number(s["Away Score"]),"slate":row}
        g.update({"saved_total":number(row.get("Projected Total")),"saved_margin":number(row.get("Projected Margin")),
            "market_total":number(row.get("Market Total")),"home_spread":number(row.get("Market Home Spread")),
            "weather":number(row.get("Weather Adjustment"),0),"venue":0 if truth(row.get("Neutral Site")) else number(row.get("Home Field Advantage"),0)-number(row.get("League HFA"),0)})
        g["component_eligible"]=bool(p_a and p_h)
        if g["component_eligible"]:
            a,h=personnel(p_a),personnel(p_h)
            weights={"Previous Season Weight":number(row.get("Previous Season Weight"),1)}
            projection={"away":weights,"home":weights}
            g.update({"total_personnel":total_regression._personnel_total_delta(a,h),
                "margin_personnel":spread_regression._personnel_margin(a,h),
                "total_continuity":total_regression._continuity_total_delta(projection,a,h),
                "margin_continuity":spread_regression._continuity_margin(projection,a,h)})
        else:
            missing.append({"game":g["game"],"id":game_id,"date":date})
            for k in ["total_personnel","margin_personnel","total_continuity","margin_continuity"]:g[k]=0
        g["schedule"]=0.0;g["offense_ramp"]=0.0;g["schedule_available"]=False
        if "v2.8" in version:
            if date not in contexts:
                before=schedule_adjustment._read_prior_games(prior,2026,week,date)
                contexts[date]=schedule_adjustment._fit_network(before)
                if not before.empty and not (pd.to_datetime(before["Game Date"])<pd.Timestamp(date)).all():raise AssertionError("Future score leaked into adjustment")
            profiles=contexts[date]["profiles"];a=profiles.get(away);h=profiles.get(home)
            if a and h:
                correction,ramp,_=schedule_adjustment._matchup_adjustments(a,h)
                g.update(schedule=correction,offense_ramp=ramp,schedule_available=True)
        g["base_total"]=g["saved_total"]-g["weather"]-g["total_personnel"]-g["total_continuity"]-g["schedule"]-g["offense_ramp"]
        g["base_margin"]=g["saved_margin"]-g["venue"]-g["margin_personnel"]-g["margin_continuity"]
        games.append(g)
    return sorted(games,key=lambda x:(x["week"],x["date"],x["id"])),tracker,missing

def play(g, market, prediction=None, enforce_eligibility=True):
    r=g["slate"]
    if market=="Total":
        line=g["market_total"]
        if not math.isfinite(line) or line<=0:return None
        pred=g["saved_total"] if prediction is None else prediction;edge=pred-line;side=1 if edge>=0 else -1
        odds=number(r.get("Total Over Odds" if side==1 else "Total Under Odds"),-110)
        outcome=result_value(g["actual_total"],line,side);direction="Over" if side==1 else "Under"
    else:
        if not math.isfinite(g["home_spread"]) or (enforce_eligibility and (not g["fbs"] or abs(g["home_spread"])>=20)):return None
        pred=g["saved_margin"] if prediction is None else prediction;edge=pred+g["home_spread"];side=1 if edge>=0 else -1
        odds=number(r.get("Home Spread Odds" if side==1 else "Away Spread Odds"),-110)
        outcome=result_value(g["actual_margin"],-g["home_spread"],side)
        direction="Favorite" if g["home_spread"]*side<0 else "Underdog" if g["home_spread"]*side>0 else "Pickem"
    return {"game_id":g["id"],"game":g["game"],"date":g["date"],"week":g["week"],"market":market,
        "version":g["version"],"current":g["current"],"edge_points":abs(edge),"signed_edge":edge,
        "side":side,"direction":direction,"result":outcome,"profit":profit(outcome,odds),"odds":odds,
        "grade":r.get(f"{market} Grade"),"probability":number(r.get(f"{market} Probability")),
        "reliability":number(r.get("Reliability")),"data_confidence":number(r.get("Data Confidence")),
        "personnel_confidence":number(r.get("Personnel Confidence")),"confluence":number(r.get(f"{market} Confluence")),
        "weather":g["weather"],"personnel_magnitude":abs(g["total_personnel"] if market=="Total" else g["margin_personnel"]),
        "pace":number(r.get("Expected Possessions")),"market_line":line if market=="Total" else g["home_spread"]}

def window_record(rows):
    return {"all":record(rows),"development_w1_4":record([r for r in rows if r["week"]<=4]),
        "validation_w5_6":record([r for r in rows if 5<=r["week"]<=6]),
        "later_w7_9":record([r for r in rows if r["week"]>=7])}

def audit(inputs, output):
    output.mkdir(parents=True,exist_ok=True);(output/"protocol.json").write_text(json.dumps(PROTOCOL,indent=2))
    games,tracker,missing=make_games(inputs)
    plays=[p for g in games for m in ["Spread","Total"] if (p:=play(g,m))]
    selected=[r for r in plays if r["grade"] in ["A Spread","B Spread","A Total","B Total"]]
    # Tracker grades are authoritative for the saved model record. Early spread
    # guardrails were applied retrospectively in the public reader; enforce 20+ exclusion.
    saved=[];checks=[];keyed={(g["date"],g["id"]):g for g in games}
    for r in tracker:
        if r.get("Grade") not in {"A Spread","B Spread","A Total","B Total"} or r.get("Result") not in {"Win","Loss","Push"}:continue
        tracker_line=re.search(r"([+-]?\d+(?:\.\d+)?)\s*$",r["Selection"])
        if r.get("Bet Type")=="Spread" and tracker_line and abs(float(tracker_line[1]))>=20:continue
        g=keyed.get((r.get("Date"),str(r.get("Game ID"))))
        if not g:continue
        p=play(g,r["Bet Type"],enforce_eligibility=False)
        if p is None:continue
        match=re.search(r"([+-]?\d+(?:\.\d+)?)\s*$",r["Selection"])
        n=float(match[1]);actual=g["actual_total"] if p["market"]=="Total" else g["actual_margin"]
        if p["market"]=="Total":side=1 if r["Selection"].startswith("Over") else -1;out=result_value(actual,n,side)
        else:
            team=canon(r["Selection"][:match.start()].strip());side=1 if team==g["home"] else -1
            if team not in {g["home"],g["away"]}:raise ValueError("Unknown tracker side")
            out=result_value(actual,-n if side==1 else n,side)
        if out!={"Win":1,"Loss":-1,"Push":0}[r["Result"]]:checks.append(r)
        p.update(result=out,profit=profit(out,number(r["Odds/Line"])),grade=r["Grade"],probability=number(r["Model Probability"]))
        saved.append(p)
    if checks:raise AssertionError(f"Tracker result mismatches: {checks}")
    if len({(r["date"],r["game_id"],r["market"]) for r in saved})!=len(saved):raise AssertionError("Duplicate tracker")
    summary={"protocol":PROTOCOL,"coverage":{"completed_forecasts":len(games),"eligible_market_forecasts":len(plays),
        "settled_tracked_plays":len(saved),"current_forecasts":sum(g["current"] for g in games),
        "missing_personnel":missing,"schedule_reconstruction_available_current":sum(g["current"] and g["schedule_available"] for g in games),
        "result_mismatches":len(checks)},"official":window_record(saved),"by_grade":by(saved,lambda r:r["grade"]),
        "by_version":by(saved,lambda r:r["version"]),"by_week":by(saved,lambda r:r["week"]),
        "current_by_grade":by([r for r in saved if r["current"]],lambda r:r["grade"])}
    buckets=[]
    for m,edges in [("Total",[0,3,6,10,float("inf")]),("Spread",[0,3,6,8,10,float("inf")])]:
        for current in [False,True]:
            rows=[r for r in plays if r["market"]==m and (r["current"] or not current)]
            for lo,hi in zip(edges,edges[1:]):
                rr=[r for r in rows if lo<=r["edge_points"]<hi]
                buckets.append({"market":m,"current_only":current,"low":lo,"high":hi if math.isfinite(hi) else None,**window_record(rr)})
    summary["fixed_edge_bands"]=buckets
    sensitivity={};current_games=[g for g in games if g["current"] and g["component_eligible"]]
    for name,weights in PROTOCOL["candidate_weights"].items():
        rows=[]
        for g in current_games:
            components=[g["schedule"],g["weather"],g["total_personnel"],g["total_continuity"],g["offense_ramp"]]
            pred=g["base_total"]+sum(a*b for a,b in zip(components,weights));p=play(g,"Total",pred)
            if p and p["edge_points"]>=3:rows.append(p)
        sensitivity[name]={"early_w6_7":record([r for r in rows if r["week"]<=7]),
            "later_w8_9":record([r for r in rows if r["week"]>=8]),"all":record(rows),"by_week":by(rows,lambda r:r["week"])}
    eligible=[(name,r["early_w6_7"]) for name,r in sensitivity.items() if r["early_w6_7"]["n"]>=30 and r["early_w6_7"]["units"]>0]
    selected_candidate=max(eligible,key=lambda v:v[1]["units"])[0] if eligible else None
    summary["current_total_additive_sensitivity"]={"candidates":sensitivity,"selected_on_early_weeks":selected_candidate,
        "later_result":sensitivity[selected_candidate]["later_w8_9"] if selected_candidate else None}
    # Add-one comparisons are descriptive, outside the nine-candidate selection.
    add_one={}
    for name,weight in [("base_only",[0,0,0,0,0]),("base_plus_schedule",[1,0,0,0,0]),
        ("base_plus_weather",[0,1,0,0,0]),("base_plus_personnel",[0,0,1,0,0]),
        ("base_plus_continuity",[0,0,0,1,0]),("base_plus_offense_ramp",[0,0,0,0,1])]:
        rows=[]
        for g in current_games:
            components=[g["schedule"],g["weather"],g["total_personnel"],g["total_continuity"],g["offense_ramp"]]
            p=play(g,"Total",g["base_total"]+sum(a*b for a,b in zip(components,weight)))
            if p and p["edge_points"]>=3-1e-7:rows.append(p)
        add_one[name]={"early_w6_7":record([r for r in rows if r["week"]<=7]),"later_w8_9":record([r for r in rows if r["week"]>=8]),"all":record(rows)}
    summary["descriptive_total_add_one_sensitivity"]=add_one
    margin_sensitivity={}
    for name in ["full","no_venue","no_personnel","no_continuity","reconstructed_base"]:
        rows=[]
        for g in current_games:
            pred=g["saved_margin"]
            if name in {"no_venue","reconstructed_base"}:pred-=g["venue"]
            if name in {"no_personnel","reconstructed_base"}:pred-=g["margin_personnel"]
            if name in {"no_continuity","reconstructed_base"}:pred-=g["margin_continuity"]
            p=play(g,"Spread",pred)
            if p and p["edge_points"]>=8-1e-7:rows.append(p)
        margin_sensitivity[name]={"early_w6_7":record([r for r in rows if r["week"]<=7]),"later_w8_9":record([r for r in rows if r["week"]>=8]),"all":record(rows)}
    summary["descriptive_spread_additive_sensitivity"]=margin_sensitivity
    summary["current_band_chronology"]=[]
    for lo,hi in [(0,3),(3,6),(6,float("inf"))]:
        rr=[r for r in plays if r["current"] and r["market"]=="Total" and lo<=r["edge_points"]<hi]
        summary["current_band_chronology"].append({"low":lo,"high":hi if math.isfinite(hi) else None,
            "early_w6_7":record([r for r in rr if r["week"]<=7]),"later_w8_9":record([r for r in rr if r["week"]>=8]),"all":record(rr)})
    # Individual saved contexts are fixed diagnostics, not a searched conjunction.
    context={}
    predicates={"confluence_0_2":lambda r:r["confluence"]<=2,"confluence_3_plus":lambda r:r["confluence"]>=3,
        "confluence_4_plus":lambda r:r["confluence"]>=4,"reliability_80_plus":lambda r:r["reliability"]>=80,
        "personnel_confidence_80_plus":lambda r:r["personnel_confidence"]>=80,"weather_adjustment_present":lambda r:abs(r["weather"])>0,
        "personnel_adjustment_1_plus":lambda r:r["personnel_magnitude"]>=1,"pace_12_3_plus":lambda r:r["pace"]>=12.3}
    for m in ["Spread","Total"]:
        rows=[r for r in saved if r["market"]==m]
        context[m]={name:window_record([r for r in rows if fn(r)]) for name,fn in predicates.items()}
        context[m]["direction"]=by(rows,lambda r:r["direction"])
    summary["context_diagnostics"]=context
    # Calibration uses only prior weeks and saved probabilities. No inversion or
    # calibration candidate is selected after seeing a test week's outcomes.
    calibration={}
    for market in ["Spread","Total"]:
        rows=[r for r in saved if r["market"]==market and r["result"]!=0 and math.isfinite(r["probability"])]
        bins=[]
        for lo,hi in [(.5,.6),(.6,.7),(.7,1.000001)]:
            rr=[r for r in rows if lo<=r["probability"]<hi];bins.append({"low":lo,"high":hi,**record(rr),"mean_model_probability":float(np.mean([r["probability"] for r in rr])) if rr else None})
        predictions=[]
        for week in range(5,10):
            train=[r for r in rows if r["week"]<week];test=[r for r in rows if r["week"]==week]
            if len(train)<30 or not test:continue
            x=lambda rr:np.array([[math.log(max(.001,min(.999,r["probability"]))/max(.001,1-min(.999,r["probability"])))] for r in rr])
            y=np.array([r["result"]==1 for r in train],dtype=int)
            model=LogisticRegression(C=1.0,solver="lbfgs",random_state=0).fit(x(train),y)
            for r,p in zip(test,model.predict_proba(x(test))[:,1]):predictions.append({**r,"calibrated_probability":float(p),"trained_through_week":week-1})
        raw=np.mean([(r["probability"]-(r["result"]==1))**2 for r in predictions]) if predictions else None
        adjusted=np.mean([(r["calibrated_probability"]-(r["result"]==1))**2 for r in predictions]) if predictions else None
        recommended=[r for r in predictions if r["calibrated_probability"]*(100/abs(r["odds"]) if r["odds"]<0 else r["odds"]/100)-(1-r["calibrated_probability"])>=.02]
        calibration[market]={"bins":bins,"rolling_raw_brier":raw,"rolling_calibrated_brier":adjusted,
            "rolling_positive_ev_record":window_record(recommended),"rolling_forecasts":len(predictions),
            "mean_raw_probability":float(np.mean([r["probability"] for r in predictions])) if predictions else None,
            "mean_calibrated_probability":float(np.mean([r["calibrated_probability"] for r in predictions])) if predictions else None}
    summary["probability_calibration"]=calibration
    history={str(r.get("Game ID")):r for r in load(inputs/"team_season_history.json")}
    clv=[]
    for r in saved:
        h=history.get(r["game_id"],{});g=keyed[(r["date"],r["game_id"])]
        close=number(h.get("Total" if r["market"]=="Total" else "Home Spread"))
        if not math.isfinite(close):continue
        value=(close-g["market_total"])*r["side"] if r["market"]=="Total" else (g["home_spread"]-close)*r["side"]
        clv.append({**r,"closing_reference":close,"clv":value,"reference_source":h.get("History Source")})
    summary["historical_closing_reference"]={"warning":"Existing season-history quote is a retrospective ESPN reference, not a timestamped archived closing capture; generic fallback may have been used.",
        "n":len(clv),"mean_points":float(np.mean([r["clv"] for r in clv])) if clv else None,
        "positive":record([r for r in clv if r["clv"]>0]),"flat":record([r for r in clv if r["clv"]==0]),"negative":record([r for r in clv if r["clv"]<0]),
        "by_market":{m:{"n":len(rr:=[r for r in clv if r["market"]==m]),"mean_points":float(np.mean([r["clv"] for r in rr])) if rr else None} for m in ["Spread","Total"]}}
    serial_games=[{k:v for k,v in g.items() if k!="slate"} for g in games]
    (output/"games_and_components.json").write_text(json.dumps(serial_games,indent=2,allow_nan=False))
    (output/"tracked_plays.json").write_text(json.dumps(saved,indent=2,allow_nan=False))
    (output/"closing_reference_plays.json").write_text(json.dumps(clv,indent=2,allow_nan=False))
    (output/"results.json").write_text(json.dumps(summary,indent=2,allow_nan=False))
    return summary

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--inputs",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    args=p.parse_args();r=audit(args.inputs,args.output)
    print(json.dumps({"coverage":r["coverage"],"official":r["official"],
        "candidates":{k:{w:v[w] for w in ["early_w6_7","later_w8_9","all"]} for k,v in r["current_total_additive_sensitivity"]["candidates"].items()},
        "selected":r["current_total_additive_sensitivity"]["selected_on_early_weeks"]},indent=2))
