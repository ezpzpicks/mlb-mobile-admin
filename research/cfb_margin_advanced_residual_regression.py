"""Advanced CFB margin residual research.

Tests whether advanced football matchup variables improve the validated independent
CFB spread/margin regression. Sportsbook lines are never predictors.

Protocol: train 2021-23, select family/ridge/cap on 2024, confirm on 2025.
Candidate families were specified from the separate 2026 investigation before
opening their 2025 performance.
"""
from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd
from research import cfb_totals_efficiency_regression as base

RESULTS_DIR=Path("research/results"); RESULTS_DIR.mkdir(parents=True,exist_ok=True)
RESULT_JSON=RESULTS_DIR/"cfb_margin_advanced_residual_results.json"
RESULT_MD=RESULTS_DIR/"CFB_MARGIN_ADVANCED_RESIDUAL_RESULTS.md"
TRAIN=(2021,2022,2023); VALID=2024; HOLDOUT=2025
ALPHAS=(1.0,4.0,16.0,64.0,256.0); CAPS=(2.0,3.0,4.0,5.0,6.0,8.0)
_original=base.build_feature_row

def bm(prior,current,name): return float(base.blend_metric(prior,current,name))
def pair_adv(h_off,a_def,a_off,h_def): return .5*(h_off+a_def)-.5*(a_off+h_def)

def build_row(game,prior_stats,current_stats,prior_metrics,current_metrics):
    row=_original(game,prior_stats,current_stats,prior_metrics,current_metrics)
    away,home=str(game["away_team"]),str(game["home_team"]); neutral=base.truthy(game.get("neutral",False))
    ap,hp=prior_metrics.get(away),prior_metrics.get(home); ac,hc=current_metrics.get(away),current_metrics.get(home)
    am=lambda n: bm(ap,ac,n); hm=lambda n: bm(hp,hc,n)
    a_score,h_score=base.spread_scores(away,home,neutral,prior_stats,current_stats)
    row["spread_margin"]=float(h_score-a_score); row["actual_margin"]=float(base.num(game.get("actual_margin"),0.0))
    direct={
      "finish_def_diff":"Finishing Drives Defense Raw","finish_off_diff":"Finishing Drives Offense",
      "ypp_diff":"Yards Per Play","ypp_allowed_diff":"Yards Per Play Allowed",
      "third_off_diff":"Third Down Rate","third_def_diff":"Third Down Defense Raw",
      "rush_epa_diff":"Rush EPA/PPA","rush_def_diff":"Rush Defense Raw",
      "line_off_diff":"Line Yards Offense","line_def_diff":"Line Yards Defense Raw","power_success_diff":"Power Success",
      "havoc_allowed_diff":"Havoc Allowed","havoc_created_diff":"Havoc Created",
      "sack_allowed_diff":"Sack Rate Allowed","sack_created_diff":"Sack Rate Created",
      "pass_epa_diff":"Pass EPA/PPA","pass_def_diff":"Pass Defense Raw",
      "success_off_diff":"Success Rate Offense","success_def_diff":"Success Rate Defense Raw",
      "explosive_off_diff":"Explosiveness Offense","explosive_def_diff":"Explosiveness Defense Raw",
      "turnover_diff":"Turnover Rate","takeaway_diff":"Takeaway Rate"}
    for out,src in direct.items(): row[out]=hm(src)-am(src)
    pairs={
      "finish_matchup_adv":("Finishing Drives Offense","Finishing Drives Defense Raw"),
      "ypp_matchup_adv":("Yards Per Play","Yards Per Play Allowed"),
      "third_matchup_adv":("Third Down Rate","Third Down Defense Raw"),
      "rush_matchup_adv":("Rush EPA/PPA","Rush Defense Raw"),
      "line_matchup_adv":("Line Yards Offense","Line Yards Defense Raw"),
      "pass_matchup_adv":("Pass EPA/PPA","Pass Defense Raw"),
      "success_matchup_adv":("Success Rate Offense","Success Rate Defense Raw"),
      "explosive_matchup_adv":("Explosiveness Offense","Explosiveness Defense Raw")}
    for out,(off,deff) in pairs.items(): row[out]=pair_adv(hm(off),am(deff),am(off),hm(deff))
    row["sack_matchup_adv"]=pair_adv(hm("Sack Rate Created"),am("Sack Rate Allowed"),am("Sack Rate Created"),hm("Sack Rate Allowed"))
    row["havoc_balance_diff"]=(hm("Havoc Created")-hm("Havoc Allowed"))-(am("Havoc Created")-am("Havoc Allowed"))
    row["turnover_pressure_adv"]=(hm("Takeaway Rate")-hm("Turnover Rate"))-(am("Takeaway Rate")-am("Turnover Rate"))
    return row
base.build_feature_row=build_row

FAMILIES={
 "finishing_defense":["finish_def_diff"],
 "finishing_matchup":["finish_matchup_adv"],
 "finishing_plus_ypp":["finish_def_diff","ypp_matchup_adv"],
 "finishing_plus_third":["finish_def_diff","third_matchup_adv"],
 "finishing_plus_disruption":["finish_def_diff","sack_matchup_adv","havoc_balance_diff"],
 "finishing_plus_rush":["finish_def_diff","rush_matchup_adv"],
 "finishing_plus_trench":["finish_def_diff","rush_matchup_adv","line_matchup_adv","power_success_diff"],
 "finishing_plus_pass":["finish_def_diff","pass_matchup_adv"],
 "finishing_plus_explosive":["finish_def_diff","explosive_matchup_adv"],
 "finishing_plus_turnover":["finish_def_diff","turnover_pressure_adv"],
 "scoring_defense_core":["finish_def_diff","ypp_allowed_diff","third_def_diff","rush_def_diff","line_def_diff","pass_def_diff","success_def_diff","explosive_def_diff"],
 "finish_trench_disruption":["finish_def_diff","rush_matchup_adv","line_matchup_adv","sack_matchup_adv","havoc_balance_diff"],
 "finish_ypp_third":["finish_def_diff","ypp_matchup_adv","third_matchup_adv"]}

def fit(frame,features,alpha):
    x=frame[features].astype(float).replace([np.inf,-np.inf],np.nan); means=x.mean(); x=x.fillna(means); stds=x.std(ddof=0).replace(0.0,1.0)
    z=((x-means)/stds).to_numpy(float); y=(frame["actual_margin"]-frame["spread_margin"]).to_numpy(float)
    design=np.column_stack([np.ones(len(z)),z]); penalty=np.eye(design.shape[1])*alpha; penalty[0,0]=0
    beta=np.linalg.solve(design.T@design+penalty,design.T@y)
    return {"features":features,"alpha":alpha,"intercept":float(beta[0]),"coef":{f:float(v) for f,v in zip(features,beta[1:])},"means":{f:float(means[f]) for f in features},"stds":{f:float(stds[f]) for f in features}}
def correction(frame,model,cap):
    cols=[]
    for f in model["features"]:
        v=pd.to_numeric(frame[f],errors="coerce").to_numpy(float); v=np.where(np.isfinite(v),v,model["means"][f]); cols.append((v-model["means"][f])/(model["stds"][f] or 1.0))
    z=np.column_stack(cols); b=np.array([model["coef"][f] for f in model["features"]]); return np.clip(model["intercept"]+z@b,-cap,cap)
def metrics(frame,pred):
    actual=frame["actual_margin"].to_numpy(float); err=pred-actual
    return {"n":int(len(frame)),"mae":float(np.mean(np.abs(err))),"rmse":float(np.sqrt(np.mean(err**2))),"bias":float(np.mean(err)),"corr":float(np.corrcoef(actual,pred)[0,1]) if len(frame)>2 else 0.0}
def base_metrics(frame): return metrics(frame,frame["spread_margin"].to_numpy(float))
def score(train,valid,features,alpha,cap):
    model=fit(train,features,alpha); pred=valid["spread_margin"].to_numpy(float)+correction(valid,model,cap); return metrics(valid,pred),model
def finite(x):
    if isinstance(x,dict): return {k:finite(v) for k,v in x.items()}
    if isinstance(x,list): return [finite(v) for v in x]
    if isinstance(x,float) and not math.isfinite(x): return None
    return x

def main():
    data=base.build_dataset().copy(); needed=["spread_margin","actual_margin"]+sorted({f for fs in FAMILIES.values() for f in fs})
    for c in needed: data[c]=pd.to_numeric(data[c],errors="coerce")
    data=data.dropna(subset=["spread_margin","actual_margin"]).reset_index(drop=True)
    train=data[data.season.isin(TRAIN)].copy(); valid=data[data.season==VALID].copy(); hold=data[data.season==HOLDOUT].copy()
    bv,bh=base_metrics(valid),base_metrics(hold); bests=[]
    for name,features in FAMILIES.items():
        best=None
        for alpha in ALPHAS:
            for cap in CAPS:
                m,_=score(train,valid,features,alpha,cap); row={"family":name,"features":features,"alpha":alpha,"cap":cap,**m}
                if best is None or (row["mae"],row["rmse"])<(best["mae"],best["rmse"]): best=row
        bests.append(best)
    bests.sort(key=lambda x:(x["mae"],x["rmse"])); chosen=bests[0]; tv=pd.concat([train,valid],ignore_index=True); family=[]
    for row in bests:
        model=fit(tv,row["features"],row["alpha"]); pred=hold["spread_margin"].to_numpy(float)+correction(hold,model,row["cap"]); hm=metrics(hold,pred)
        family.append({**row,"holdout":hm,"valid_mae_improvement":bv["mae"]-row["mae"],"holdout_mae_improvement":bh["mae"]-hm["mae"]})
    picked=next(x for x in family if x["family"]==chosen["family"])
    results={"protocol":{"train":list(TRAIN),"validation":VALID,"holdout":HOLDOUT,"sportsbook_predictors_used":False,"target":"actual_margin - fixed independent spread_margin","selection":"2026-derived families; family/alpha/cap selected on 2024 only"},"dataset":{"rows":len(data),"train":len(train),"validation":len(valid),"holdout":len(hold)},"baseline":{"validation":bv,"holdout":bh},"chosen_on_2024":chosen,"chosen_2025":picked["holdout"],"chosen_holdout_mae_improvement":picked["holdout_mae_improvement"],"family_results":family}
    RESULT_JSON.write_text(json.dumps(finite(results),indent=2,allow_nan=False))
    lines=["# CFB Advanced Margin Residual Research","","Sportsbook lines are evaluation-only and never predictors.",f"- Train: {TRAIN}",f"- Validation: {VALID}",f"- Holdout: {HOLDOUT}",f"- Baseline validation MAE: {bv['mae']:.3f}",f"- Baseline holdout MAE: {bh['mae']:.3f}","","| Family | 2024 MAE | 2024 Δ | 2025 MAE | 2025 Δ | Alpha | Cap |","|---|---:|---:|---:|---:|---:|---:|"]
    for r in family: lines.append(f"| {r['family']} | {r['mae']:.3f} | {r['valid_mae_improvement']:+.3f} | {r['holdout']['mae']:.3f} | {r['holdout_mae_improvement']:+.3f} | {r['alpha']:.0f} | {r['cap']:.0f} |")
    lines += ["","## Chosen on 2024","",f"- Family: {chosen['family']}",f"- 2025 MAE: {picked['holdout']['mae']:.3f}",f"- 2025 improvement: {picked['holdout_mae_improvement']:+.3f}"]
    RESULT_MD.write_text("\n".join(lines)+"\n"); print("\n".join(lines))
if __name__=="__main__": main()
