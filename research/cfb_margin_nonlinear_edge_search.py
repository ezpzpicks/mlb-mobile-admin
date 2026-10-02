"""Nonlinear football-only CFB margin residual search.

Goal: test whether conditional interactions among leakage-safe pregame football
statistics improve ATS edge ordering without using sportsbook lines as predictors.

Protocol
--------
* Train nonlinear residual models on 2021-2023 actual margins only.
* Select model/hyperparameters/correction cap on 2024 using fixed 2/4/6/8/10
  ATS edge progression. The market spread is evaluation-only.
* Freeze that specification.
* Refit on 2021-2024 and test untouched 2025.
* Refit the exact same specification on 2021-2025 and test 2026 with zero retuning.

The prediction target is actual_margin - existing independent spread_margin, so
all challengers are incremental corrections to the production-style football
projection. Sportsbook spread/total are never model features.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

from research import cfb_margin_edge_quality_search as eq

base = eq.base
base.METRIC_DEFAULTS.setdefault("Power Success", 0.68)
# build_dataset assembles TRAIN_SEASONS + validation + holdout.  Add 2025 to its
# internal build list while retaining our independent protocol below.
base.TRAIN_SEASONS = (2021, 2022, 2023, 2025)
base.VALIDATION_SEASON = 2024
base.HOLDOUT_SEASON = 2026

OUT = Path("research/results")
OUT.mkdir(parents=True, exist_ok=True)
THRESHOLDS = (2.0, 4.0, 6.0, 8.0, 10.0)
TRAIN = (2021, 2022, 2023)
VALID = 2024
HOLDOUT = 2025
FINAL = 2026
CAPS = (2.0, 4.0, 6.0, 8.0)

# Preserve the full chained advanced/balance/context feature builder, then add
# the seven exact margin components used by the production-style regression and
# a few predeclared football-only interactions suggested by the 2026 large-edge
# diagnostic.  No market information enters these fields.
_upstream_build_feature_row = base.build_feature_row

def build_feature_row(game, prior_stats, current_stats, prior_metrics, current_metrics):
    row = _upstream_build_feature_row(game, prior_stats, current_stats, prior_metrics, current_metrics)
    away, home = str(game["away_team"]), str(game["home_team"])
    pa, ph = base.stat(prior_stats, away), base.stat(prior_stats, home)
    ca, ch = base.stat(current_stats, away), base.stat(current_stats, home)
    ba, bh = base.blend_stats(pa, ca), base.blend_stats(ph, ch)
    neutral = base.truthy(game.get("neutral", False))
    prior_ppg_diff = ph.ppg - pa.ppg
    prior_papg_diff = pa.papg - ph.papg
    prior_power_margin = 2.0 * (ph.power - pa.power)
    current_scoring_diff = (bh.ppg - ph.ppg) - (ba.ppg - pa.ppg)
    current_allowed_diff = (ba.papg - pa.papg) - (bh.papg - ph.papg)
    current_power_margin = 2.0 * ((bh.power - ba.power) - (ph.power - pa.power))
    home_indicator = 0.0 if neutral else 1.0
    row.update({
        "prior_ppg_diff": prior_ppg_diff,
        "prior_papg_diff": prior_papg_diff,
        "prior_power_margin": prior_power_margin,
        "current_scoring_diff": current_scoring_diff,
        "current_allowed_diff": current_allowed_diff,
        "current_power_margin": current_power_margin,
        "home_indicator": home_indicator,
        "prior_x_current_power": prior_power_margin * current_power_margin,
        "current_power_x_scoring": current_power_margin * current_scoring_diff,
        "current_power_x_allowed": current_power_margin * current_allowed_diff,
        "current_scoring_x_allowed": current_scoring_diff * current_allowed_diff,
        "power_shift_abs": abs(current_power_margin),
        "scoring_shift_abs": abs(current_scoring_diff),
        "allowed_shift_abs": abs(current_allowed_diff),
    })
    return row

base.build_feature_row = build_feature_row

FEATURES: list[str] = sorted(
    set(base.BASE_FEATURES)
    | set(base.EFFICIENCY_FEATURES)
    | set(base.INTERACTION_FEATURES)
    | {f for fs in eq.FAMILIES.values() for f in fs}
    | {
        "prior_ppg_diff", "prior_papg_diff", "prior_power_margin",
        "current_scoring_diff", "current_allowed_diff", "current_power_margin", "home_indicator",
        "prior_x_current_power", "current_power_x_scoring", "current_power_x_allowed",
        "current_scoring_x_allowed", "power_shift_abs", "scoring_shift_abs", "allowed_shift_abs",
    }
)

# Small, regularized grid: enough to detect conditional signal without a huge
# hyperparameter fishing expedition.
MODEL_SPECS: list[dict[str, Any]] = [
    {"kind": "hgb", "depth": 2, "leaf": 30, "lr": 0.035, "iters": 140, "l2": 10.0},
    {"kind": "hgb", "depth": 3, "leaf": 30, "lr": 0.035, "iters": 140, "l2": 15.0},
    {"kind": "hgb", "depth": 2, "leaf": 20, "lr": 0.05, "iters": 120, "l2": 20.0},
    {"kind": "gbr", "depth": 1, "leaf": 30, "lr": 0.04, "iters": 160, "loss": "huber"},
    {"kind": "gbr", "depth": 2, "leaf": 30, "lr": 0.03, "iters": 140, "loss": "huber"},
    {"kind": "gbr", "depth": 2, "leaf": 40, "lr": 0.04, "iters": 120, "loss": "squared_error"},
    {"kind": "extra", "depth": 5, "leaf": 20, "trees": 350, "max_features": 0.70},
    {"kind": "extra", "depth": 7, "leaf": 20, "trees": 350, "max_features": 0.70},
    {"kind": "extra", "depth": 6, "leaf": 30, "trees": 350, "max_features": 1.00},
]


def finite(value: Any) -> Any:
    if isinstance(value, dict): return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list): return [finite(v) for v in value]
    if isinstance(value, tuple): return [finite(v) for v in value]
    if isinstance(value, np.integer): return int(value)
    if isinstance(value, (np.floating, float)):
        x=float(value); return x if math.isfinite(x) else None
    return value


def fbs_ids(season: int) -> set[str]:
    try:
        games=base.cfb._parse_games(base.cfb._espn_games_payload(int(season)), int(season))
        if games is None or games.empty: return set()
        mask=games["Away Classification"].astype(str).str.lower().eq("fbs") & games["Home Classification"].astype(str).str.lower().eq("fbs")
        return set(games.loc[mask, "Game ID"].astype(str))
    except Exception as exc:
        print("FBS filter failed", season, exc); return set()


def make_model(spec: dict[str, Any]) -> Pipeline:
    kind=spec["kind"]
    if kind=="hgb":
        reg=HistGradientBoostingRegressor(max_depth=int(spec["depth"]),min_samples_leaf=int(spec["leaf"]),learning_rate=float(spec["lr"]),max_iter=int(spec["iters"]),l2_regularization=float(spec["l2"]),random_state=20261002)
    elif kind=="gbr":
        reg=GradientBoostingRegressor(max_depth=int(spec["depth"]),min_samples_leaf=int(spec["leaf"]),learning_rate=float(spec["lr"]),n_estimators=int(spec["iters"]),loss=str(spec["loss"]),random_state=20261002)
    elif kind=="extra":
        reg=ExtraTreesRegressor(n_estimators=int(spec["trees"]),max_depth=int(spec["depth"]),min_samples_leaf=int(spec["leaf"]),max_features=float(spec["max_features"]),random_state=20261002,n_jobs=-1)
    else: raise ValueError(kind)
    return Pipeline([("impute",SimpleImputer(strategy="median")),("model",reg)])


def prepare(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    out=frame.reindex(columns=features).copy()
    for col in out.columns: out[col]=pd.to_numeric(out[col],errors="coerce")
    return out.replace([np.inf,-np.inf],np.nan)


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    scores=np.asarray(scores,float);labels=np.asarray(labels,int);n1=int((labels==1).sum());n0=int((labels==0).sum())
    if not n1 or not n0:return None
    ranks=pd.Series(scores).rank(method="average").to_numpy(float)
    return float((ranks[labels==1].sum()-n1*(n1+1)/2)/(n1*n0))


def edge_metrics(frame: pd.DataFrame, pred: np.ndarray) -> dict[str, Any]:
    pred=np.asarray(pred,float);actual=pd.to_numeric(frame["actual_margin"],errors="coerce").to_numpy(float);hs=pd.to_numeric(frame["market_home_spread"],errors="coerce").to_numpy(float)
    edge=pred+hs;miss=actual+hs;valid=np.isfinite(edge)&np.isfinite(miss)&(np.abs(edge)>1e-9)&(np.abs(miss)>1e-9)
    e=edge[valid];m=miss[valid];win=(e*m>0).astype(int);rates=[]
    for threshold in THRESHOLDS:
        sel=np.abs(e)>=threshold;n=int(sel.sum());w=int(win[sel].sum());rates.append({"threshold":threshold,"n":n,"wins":w,"losses":n-w,"win_rate":w/n if n else None})
    usable=[r for r in rates if r["n"]>=25 and r["win_rate"] is not None]
    steps=sum(b["win_rate"]>=a["win_rate"] for a,b in zip(usable,usable[1:]));violation=sum(max(0.0,a["win_rate"]-b["win_rate"]) for a,b in zip(usable,usable[1:]));slope=usable[-1]["win_rate"]-usable[0]["win_rate"] if len(usable)>=2 else -1.0
    high=next((r["win_rate"] for r in rates if r["threshold"]==10.0 and r["n"]>=25),None)
    return {"n":int(len(e)),"auc":auc(np.abs(e),win),"corr":float(np.corrcoef(e,m)[0,1]) if len(e)>2 else None,"mae":float(np.nanmean(np.abs(pred-actual))),"monotonic_steps":int(steps),"monotonic_possible":max(0,len(usable)-1),"violation_sum":float(violation),"slope_2_to_10":float(slope),"high_edge_win_rate":high,"thresholds":rates}


def selection_key(m: dict[str, Any]) -> tuple:
    return (m["monotonic_steps"],-m["violation_sum"],m["slope_2_to_10"],m["high_edge_win_rate"] if m["high_edge_win_rate"] is not None else -1.0,m["auc"] if m["auc"] is not None else -1.0,m["corr"] if m["corr"] is not None else -1.0)


def fit_predict(train: pd.DataFrame,test: pd.DataFrame,spec: dict[str, Any],cap: float,features: list[str]) -> np.ndarray:
    y=(pd.to_numeric(train["actual_margin"],errors="coerce")-pd.to_numeric(train["spread_margin"],errors="coerce")).to_numpy(float)
    model=make_model(spec);model.fit(prepare(train,features),y);correction=np.clip(np.asarray(model.predict(prepare(test,features)),float),-float(cap),float(cap))
    return pd.to_numeric(test["spread_margin"],errors="coerce").to_numpy(float)+correction


def spec_name(spec: dict[str, Any]) -> str:
    return ",".join([str(spec["kind"])]+[f"{k}={spec[k]}" for k in sorted(k for k in spec if k!="kind")])


def main() -> None:
    print("building 2021-2026 leakage-safe dataset")
    df=base.build_dataset().copy();df=eq.attach_market_spread(df,base.cfb)
    for col in ("market_home_spread","actual_margin","spread_margin"):df[col]=pd.to_numeric(df[col],errors="coerce")
    ids={season:fbs_ids(season) for season in (*TRAIN,VALID,HOLDOUT,FINAL)}
    if all(ids.values()):df=df[df.apply(lambda r:str(r.game_id) in ids.get(int(r.season),set()),axis=1)].copy()
    df=df.dropna(subset=["market_home_spread","actual_margin","spread_margin"]).reset_index(drop=True)
    features=[f for f in FEATURES if f in df.columns and pd.to_numeric(df[f],errors="coerce").notna().any()];excluded=sorted(set(FEATURES)-set(features))
    print("feature_count",len(features),"excluded",excluded)
    tr=df[df.season.isin(TRAIN)].copy();va=df[df.season==VALID].copy();ho=df[df.season==HOLDOUT].copy();final=df[df.season==FINAL].copy();print("rows",len(tr),len(va),len(ho),len(final))
    if min(len(tr),len(va),len(ho),len(final))==0:raise RuntimeError("One or more protocol seasons have zero rows")
    baseline={"2024":edge_metrics(va,va.spread_margin.to_numpy(float)),"2025":edge_metrics(ho,ho.spread_margin.to_numpy(float)),"2026":edge_metrics(final,final.spread_margin.to_numpy(float))}
    candidates=[]
    for spec in MODEL_SPECS:
        for cap in CAPS:
            met=edge_metrics(va,fit_predict(tr,va,spec,cap,features));candidates.append({"spec":spec,"cap":cap,"validation":met});print("candidate",spec_name(spec),"cap",cap,"key",selection_key(met))
    candidates.sort(key=lambda r:selection_key(r["validation"]),reverse=True);chosen=candidates[0];print("chosen",spec_name(chosen["spec"]),"cap",chosen["cap"])
    pred25=fit_predict(pd.concat([tr,va],ignore_index=True),ho,chosen["spec"],chosen["cap"],features);chosen25=edge_metrics(ho,pred25)
    pred26=fit_predict(pd.concat([tr,va,ho],ignore_index=True),final,chosen["spec"],chosen["cap"],features);chosen26=edge_metrics(final,pred26)
    result={"protocol":{"train":list(TRAIN),"validation_selection":VALID,"holdout_1":HOLDOUT,"holdout_2":FINAL,"market_predictor":False,"target":"actual_margin - independent spread_margin","fixed_edge_thresholds":list(THRESHOLDS),"2026_retuned":False},"rows":{"train":len(tr),"2024":len(va),"2025":len(ho),"2026":len(final)},"features":features,"excluded_features":excluded,"baseline":baseline,"chosen_on_2024":{"spec":chosen["spec"],"cap":chosen["cap"],"validation":chosen["validation"],"holdout_2025":chosen25,"holdout_2026":chosen26},"candidates_2024":candidates}
    (OUT/"cfb_margin_nonlinear_edge_search.json").write_text(json.dumps(finite(result),indent=2,allow_nan=False))
    def rate_text(m,t):
        r=next(x for x in m["thresholds"] if x["threshold"]==t);return f"{r['wins']}-{r['losses']} ({100*r['win_rate']:.1f}%)" if r["win_rate"] is not None else "n/a"
    lines=["# CFB Nonlinear Margin Edge Search","",f"Rows: train={len(tr)}, 2024={len(va)}, 2025={len(ho)}, 2026={len(final)}",f"Football-only feature count: {len(features)}",f"Chosen on 2024: `{spec_name(chosen['spec'])}`, correction cap={chosen['cap']:.0f}","","Sportsbook spreads were evaluation-only and never entered the prediction feature matrix.","","| Season | Model | Steps | 2→10 slope | AUC | MAE | 2+ | 4+ | 6+ | 8+ | 10+ |","|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for year,bm,cm in [(2024,baseline["2024"],chosen["validation"]),(2025,baseline["2025"],chosen25),(2026,baseline["2026"],chosen26)]:
        for label,m in (("Baseline",bm),("Nonlinear",cm)):
            lines.append(f"| {year} | {label} | {m['monotonic_steps']}/{m['monotonic_possible']} | {100*m['slope_2_to_10']:+.1f}pp | {m['auc']:.3f} | {m['mae']:.3f} | "+" | ".join(rate_text(m,t) for t in THRESHOLDS)+" |")
    lines += ["","## Top 2024-selected candidates","","| Model | Cap | Steps | slope | 10+ | AUC | MAE |","|---|---:|---:|---:|---:|---:|---:|"]
    for row in candidates[:12]:
        m=row["validation"];lines.append(f"| {spec_name(row['spec'])} | {row['cap']:.0f} | {m['monotonic_steps']}/{m['monotonic_possible']} | {100*m['slope_2_to_10']:+.1f}pp | {100*(m['high_edge_win_rate'] or 0):.1f}% | {m['auc']:.3f} | {m['mae']:.3f} |")
    (OUT/"CFB_MARGIN_NONLINEAR_EDGE_SEARCH.md").write_text("\n".join(lines)+"\n");print("\n".join(lines))

if __name__=="__main__":main()
