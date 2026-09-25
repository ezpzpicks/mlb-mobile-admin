"""Selective exact-slot component tier backtest.

Only activates tiered matchup weighting where BOTH conditions are met:
1) component matchup coefficient is statistically meaningful in 2021-24 (p < .05)
2) the exact-slot tiered overlay improved 2025 holdout MAE in the prior decomposition test

Selected rules:
- RB1 rushing: opportunity tier only
- RB1 receiving: opportunity tier only
- WR1 receiving: efficiency tier only
All other slots/components remain baseline.

Tier weights: T1=0%, T2=50%, T3=100%, T4=150%, T5=200%.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from research import nfl_all_slots_extreme_yardage_weights as prior
from research import nfl_component_tier_matchup_weights as comp
from research import nfl_rb_wr_prop_regression as base

HOLDOUT={2025}
TIER_WEIGHTS=comp.TIER_WEIGHTS


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str,float]:
    actual=np.asarray(actual,float); pred=np.asarray(pred,float)
    err=pred-actual
    return {
        "n":int(len(actual)),
        "mae":float(np.mean(np.abs(err))),
        "rmse":float(np.sqrt(np.mean(err**2))),
        "bias":float(np.mean(err)),
    }


def improvement(old: float,new: float)->float:
    return float(100.0*(old-new)/max(old,1e-9))


def run_market(
    data: pd.DataFrame,
    *,
    market: str,
    position: str,
    valid_slots: list[str],
    actual_yards_col: str,
    actual_opportunity_col: str,
    actual_efficiency_col: str,
    opportunity_avg_col: str,
    efficiency_avg_col: str,
    opportunity_target: str,
    efficiency_target: str,
    raw_opportunity: str,
    raw_efficiency: str,
    opportunity_candidates: list[str],
    efficiency_candidates: list[str],
    role_filter: str,
    role_min: float,
    min_efficiency_opportunity: float,
    opportunity_clip: tuple[float,float],
    efficiency_clip: tuple[float,float],
    selected_opportunity_slots: set[str],
    selected_efficiency_slots: set[str],
) -> dict[str,Any]:
    rows=data[data["position"].eq(position)].copy()
    enriched=comp.add_component_grades_and_matchups(
        rows,
        actual_opportunity_col=actual_opportunity_col,
        actual_efficiency_col=actual_efficiency_col,
        opportunity_avg_col=opportunity_avg_col,
        efficiency_avg_col=efficiency_avg_col,
    )

    opp_model,eff_model,subset=prior.fit_base_models(
        enriched,
        position=position,
        kind=market,
        opportunity_target=opportunity_target,
        efficiency_target=efficiency_target,
        raw_opportunity=raw_opportunity,
        raw_efficiency=raw_efficiency,
        opportunity_candidates=opportunity_candidates,
        efficiency_candidates=efficiency_candidates,
        role_filter=role_filter,
        role_min=role_min,
        min_efficiency_opportunity=min_efficiency_opportunity,
    )

    required=list(set(
        opp_model["features"]+eff_model["features"]+[
            actual_yards_col,actual_opportunity_col,actual_efficiency_col,
            "slot","opportunity_tier","efficiency_tier",
            "opportunity_matchup_edge","efficiency_matchup_edge",
        ]
    ))
    usable=subset.dropna(subset=required).copy()
    usable=usable[usable["slot"].isin(valid_slots)].copy()

    bo,be=comp._component_preds(
        opp_model,eff_model,usable,opportunity_clip,efficiency_clip
    )
    usable["base_opp"]=bo
    usable["base_eff"]=be
    usable["actual_yards"]=pd.to_numeric(usable[actual_yards_col],errors="coerce")
    usable["actual_opp"]=pd.to_numeric(usable[actual_opportunity_col],errors="coerce")
    usable["actual_eff"]=pd.to_numeric(usable[actual_efficiency_col],errors="coerce")
    usable=usable[
        usable["actual_yards"].notna()
        & usable["actual_opp"].notna()
        & usable["base_opp"].gt(0)
        & usable["base_eff"].gt(0)
    ].copy()

    usable["opp_term"]=usable["base_opp"]*usable["opportunity_matchup_edge"]
    usable["eff_term"]=usable["base_eff"]*usable["efficiency_matchup_edge"]
    beta_opp,p_opp=comp._fit_beta(usable,"actual_opp","base_opp","opp_term")
    beta_eff,p_eff=comp._fit_beta(
        usable,"actual_eff","base_eff","eff_term",
        min_volume_col=actual_opportunity_col,
        min_volume=min_efficiency_opportunity,
    )

    hold=usable[usable["season"].isin(HOLDOUT)].copy()
    bo=hold["base_opp"].to_numpy(float)
    be=hold["base_eff"].to_numpy(float)
    pred_base=bo*be

    opp_adj=bo.copy()
    eff_adj=be.copy()

    opp_weights=hold["opportunity_tier"].map(TIER_WEIGHTS).astype(float).to_numpy()
    eff_weights=hold["efficiency_tier"].map(TIER_WEIGHTS).astype(float).to_numpy()
    opp_slots=hold["slot"].isin(selected_opportunity_slots).to_numpy()
    eff_slots=hold["slot"].isin(selected_efficiency_slots).to_numpy()

    if opp_slots.any():
        raw=bo+beta_opp*hold["opp_term"].to_numpy(float)*opp_weights
        raw=np.clip(raw,bo*0.65,bo*1.35)
        raw=np.clip(raw,*opportunity_clip)
        opp_adj[opp_slots]=raw[opp_slots]

    if eff_slots.any():
        raw=be+beta_eff*hold["eff_term"].to_numpy(float)*eff_weights
        raw=np.clip(raw,be*0.65,be*1.35)
        raw=np.clip(raw,*efficiency_clip)
        eff_adj[eff_slots]=raw[eff_slots]

    pred_selective=opp_adj*eff_adj
    actual=hold["actual_yards"].to_numpy(float)
    base_m=metrics(actual,pred_base)
    sel_m=metrics(actual,pred_selective)

    slots={}
    for slot in valid_slots:
        mask=hold["slot"].eq(slot).to_numpy()
        if not mask.any():
            continue
        bm=metrics(actual[mask],pred_base[mask])
        sm=metrics(actual[mask],pred_selective[mask])
        slots[slot]={
            "n":int(mask.sum()),
            "opportunity_tier_active":bool(slot in selected_opportunity_slots),
            "efficiency_tier_active":bool(slot in selected_efficiency_slots),
            "base_metrics":bm,
            "selective_metrics":sm,
            "mae_improvement_pct":improvement(bm["mae"],sm["mae"]),
        }

    return {
        "market":market,
        "holdout_rows":int(len(hold)),
        "opportunity_beta":beta_opp,
        "opportunity_pvalue":p_opp,
        "efficiency_beta":beta_eff,
        "efficiency_pvalue":p_eff,
        "base_metrics":base_m,
        "selective_metrics":sel_m,
        "mae_improvement_pct":improvement(base_m["mae"],sel_m["mae"]),
        "slots":slots,
    }


def main()->None:
    out=Path("artifacts/nfl_selective_component_tiers")
    out.mkdir(parents=True,exist_ok=True)
    data=prior.assign_slots(prior.build_dataset())
    common=["team_total","team_spread","home"]

    results={
        "research_version":"nfl-selective-proven-component-tiers-2026-09-24",
        "selection_rule":"activate tiering only when training component p<.05 and exact-slot 2025 tiered holdout MAE improved in prior decomposition",
        "tier_weights":TIER_WEIGHTS,
        "active_rules":{
            "RB1 rushing":"opportunity tier only",
            "RB1 receiving":"opportunity tier only",
            "WR1 receiving":"efficiency tier only",
        },
        "inactive_by_design":[
            "QB1 passing tiers","RB2 rushing tiers","RB2 receiving tiers",
            "WR2 receiving tiers","WR3 receiving tiers","TE1 receiving tiers",
            "all non-selected efficiency/opportunity overlays",
        ],
    }

    results["qb_passing_yards"]=run_market(
        data,market="QB passing yards",position="QB",valid_slots=["QB1"],
        actual_yards_col="passing_yards",actual_opportunity_col="attempts",
        actual_efficiency_col="actual_ypa",opportunity_avg_col="attempts_avg8",
        efficiency_avg_col="ypa8",opportunity_target="attempts",efficiency_target="actual_ypa",
        raw_opportunity="raw_qb_attempts",raw_efficiency="raw_qb_ypa",
        opportunity_candidates=[
            "raw_qb_attempts","attempts_avg3","attempts_avg8","attempt_share_avg3",
            "attempt_share_avg8","team_pass_attempts_avg3","team_pass_attempts_avg8","opp_attempts_avg8"
        ]+common,
        efficiency_candidates=["raw_qb_ypa","ypa8","pass_epa_per_attempt8","opp_ypa8"]+common,
        role_filter="raw_qb_attempts",role_min=10.0,min_efficiency_opportunity=10.0,
        opportunity_clip=(10.0,55.0),efficiency_clip=(4.0,10.0),
        selected_opportunity_slots=set(),selected_efficiency_slots=set(),
    )

    results["rb_rushing_yards"]=run_market(
        data,market="RB rushing yards",position="RB",valid_slots=["RB1","RB2"],
        actual_yards_col="rushing_yards",actual_opportunity_col="carries",
        actual_efficiency_col="actual_ypc",opportunity_avg_col="carries_avg8",
        efficiency_avg_col="ypc8",opportunity_target="carries",efficiency_target="actual_ypc",
        raw_opportunity="raw_rb_carries",raw_efficiency="raw_rb_ypc",
        opportunity_candidates=[
            "raw_rb_carries","carries_avg3","carries_avg8","carry_share_avg3","carry_share_avg8",
            "team_rush_attempts_avg3","team_rush_attempts_avg8","opp_carries_avg8"
        ]+common,
        efficiency_candidates=["raw_rb_ypc","ypc8","rush_epa_per_carry8","opp_ypc8"]+common,
        role_filter="raw_rb_carries",role_min=3.0,min_efficiency_opportunity=3.0,
        opportunity_clip=(0.0,35.0),efficiency_clip=(2.0,7.0),
        selected_opportunity_slots={"RB1"},selected_efficiency_slots=set(),
    )

    results["rb_receiving_yards"]=run_market(
        data,market="RB receiving yards",position="RB",valid_slots=["RB1","RB2"],
        actual_yards_col="receiving_yards",actual_opportunity_col="targets",
        actual_efficiency_col="actual_ypt",opportunity_avg_col="targets_avg8",
        efficiency_avg_col="ypt8",opportunity_target="targets",efficiency_target="actual_ypt",
        raw_opportunity="raw_targets",raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets","targets_avg3","targets_avg8","target_share_avg3","target_share_avg8",
            "team_targets_avg3","team_targets_avg8","opp_targets_avg8"
        ]+common,
        efficiency_candidates=[
            "raw_ypt","ypt8","rec_epa_per_target8","air_yards_per_target8",
            "yac_per_reception8","opp_ypt8"
        ]+common,
        role_filter="raw_targets",role_min=1.5,min_efficiency_opportunity=1.0,
        opportunity_clip=(0.0,15.0),efficiency_clip=(2.5,12.0),
        selected_opportunity_slots={"RB1"},selected_efficiency_slots=set(),
    )

    results["wr_receiving_yards"]=run_market(
        data,market="WR receiving yards",position="WR",valid_slots=["WR1","WR2","WR3"],
        actual_yards_col="receiving_yards",actual_opportunity_col="targets",
        actual_efficiency_col="actual_ypt",opportunity_avg_col="targets_avg8",
        efficiency_avg_col="ypt8",opportunity_target="targets",efficiency_target="actual_ypt",
        raw_opportunity="raw_targets",raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets","targets_avg3","targets_avg8","target_share_avg3","target_share_avg8",
            "team_targets_avg3","team_targets_avg8","opp_targets_avg8"
        ]+common,
        efficiency_candidates=[
            "raw_ypt","ypt8","rec_epa_per_target8","air_yards_per_target8",
            "yac_per_reception8","opp_ypt8"
        ]+common,
        role_filter="raw_targets",role_min=2.5,min_efficiency_opportunity=2.0,
        opportunity_clip=(0.0,20.0),efficiency_clip=(3.0,16.0),
        selected_opportunity_slots=set(),selected_efficiency_slots={"WR1"},
    )

    results["te_receiving_yards"]=run_market(
        data,market="TE receiving yards",position="TE",valid_slots=["TE1"],
        actual_yards_col="receiving_yards",actual_opportunity_col="targets",
        actual_efficiency_col="actual_ypt",opportunity_avg_col="targets_avg8",
        efficiency_avg_col="ypt8",opportunity_target="targets",efficiency_target="actual_ypt",
        raw_opportunity="raw_targets",raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets","targets_avg3","targets_avg8","target_share_avg3","target_share_avg8",
            "team_targets_avg3","team_targets_avg8","opp_targets_avg8"
        ]+common,
        efficiency_candidates=[
            "raw_ypt","ypt8","rec_epa_per_target8","air_yards_per_target8",
            "yac_per_reception8","opp_ypt8"
        ]+common,
        role_filter="raw_targets",role_min=1.5,min_efficiency_opportunity=1.0,
        opportunity_clip=(0.0,15.0),efficiency_clip=(2.5,14.0),
        selected_opportunity_slots=set(),selected_efficiency_slots=set(),
    )

    keys=["qb_passing_yards","rb_rushing_yards","rb_receiving_yards","wr_receiving_yards","te_receiving_yards"]
    n=sum(results[k]["holdout_rows"] for k in keys)
    base_weighted=sum(results[k]["base_metrics"]["mae"]*results[k]["holdout_rows"] for k in keys)/n
    sel_weighted=sum(results[k]["selective_metrics"]["mae"]*results[k]["holdout_rows"] for k in keys)/n
    results["combined"]={
        "n":n,
        "base_weighted_mae":base_weighted,
        "selective_weighted_mae":sel_weighted,
        "mae_improvement_pct":improvement(base_weighted,sel_weighted),
    }

    path=out/"nfl_selective_component_tiers_results.json"
    path.write_text(json.dumps(results,indent=2,sort_keys=True))
    print(json.dumps(results,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
