"""Leakage-safe average-yardage x slot-matchup interaction test.

Player tier strength is defined exactly as requested: the player's pregame
average yardage divided by the league average pregame yardage for the exact
depth-chart slot (RB1, RB2, WR1, WR2, WR3), separately by prop market.
All averages use only games completed before the projected game.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from research import nfl_rb_wr_prop_regression as base


DISCOVERY = {2021, 2022, 2023}
CONFIRMATION = {2024}
HOLDOUT = {2025}


def _metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, float)
    pred = np.asarray(pred, float)
    err = pred - actual
    return {
        "n": int(len(actual)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(err)),
    }


def _improvement(old_mae: float, new_mae: float) -> float:
    return float(100.0 * (old_mae - new_mae) / max(old_mae, 1e-9))


def _assign_slots(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    df["slot"] = ""

    rb = df["position"].eq("RB")
    rb_rank = (
        df.loc[rb]
        .groupby(["season", "week", "team"])["raw_rb_carries"]
        .rank(method="first", ascending=False)
    )
    df.loc[rb, "_slot_rank"] = rb_rank
    df.loc[rb & df["_slot_rank"].eq(1), "slot"] = "RB1"
    df.loc[rb & df["_slot_rank"].eq(2), "slot"] = "RB2"

    wr = df["position"].eq("WR")
    wr_rank = (
        df.loc[wr]
        .groupby(["season", "week", "team"])["raw_targets"]
        .rank(method="first", ascending=False)
    )
    df.loc[wr, "_slot_rank"] = wr_rank
    df.loc[wr & df["_slot_rank"].eq(1), "slot"] = "WR1"
    df.loc[wr & df["_slot_rank"].eq(2), "slot"] = "WR2"
    df.loc[wr & df["_slot_rank"].eq(3), "slot"] = "WR3"
    return df.drop(columns=["_slot_rank"], errors="ignore")


def _add_market_strength_and_slot_matchup(
    frame: pd.DataFrame,
    *,
    actual_col: str,
    player_only_baseline_col: str,
    player_avg_col: str,
) -> pd.DataFrame:
    df = frame.copy()
    df = df[df["slot"].astype(str).str.len().gt(0)].copy()
    df[player_only_baseline_col] = pd.to_numeric(df[player_only_baseline_col], errors="coerce")
    df[player_avg_col] = pd.to_numeric(df[player_avg_col], errors="coerce")
    df[actual_col] = pd.to_numeric(df[actual_col], errors="coerce")
    df = df[
        df[player_only_baseline_col].gt(0.5)
        & df[player_avg_col].gt(0.0)
        & df[actual_col].notna()
    ].copy()

    # Requested grading variable:
    # player's PRIOR average yards / league PRIOR average yards for exact slot.
    # The league comparator is contemporaneous (same season/week) but every
    # player's input is itself lagged, so the target game never leaks in.
    df["slot_league_avg_yards"] = (
        df.groupby(["season", "week", "slot"])[player_avg_col]
        .transform("mean")
    )
    df["strength_ratio"] = (
        df[player_avg_col] / df["slot_league_avg_yards"].replace(0, np.nan)
    ).clip(0.40, 1.80)
    df["strength_centered"] = df["strength_ratio"] - 1.0

    # Tiers are absolute performance bands relative to exact-slot league average,
    # not percentile buckets.
    df["tier"] = pd.cut(
        df["strength_ratio"],
        bins=[-np.inf, 0.75, 0.90, 1.10, 1.25, np.inf],
        labels=["Tier 5", "Tier 4", "Tier 3", "Tier 2", "Tier 1"],
        include_lowest=True,
    ).astype(str)

    # Keep the matchup construction identical to the prior test so only the
    # player grading variable changes.
    df["actual_vs_player_baseline"] = (
        df[actual_col] / df[player_only_baseline_col].clip(lower=1.0)
    ).clip(0.0, 4.0)

    df = df.sort_values(["opponent", "slot", "season", "week", "team", "player_key"]).copy()

    # Defense-slot residual from PRIOR games only. Four neutral prior games shrink
    # early samples toward 1.0, matching the model's general early-season philosophy.
    def history_signal(vals: pd.Series) -> pd.Series:
        vals = pd.to_numeric(vals, errors="coerce")
        shifted = vals.shift(1)
        roll_sum = shifted.rolling(8, min_periods=1).sum()
        roll_n = shifted.rolling(8, min_periods=1).count()
        shrunk_ratio = (roll_sum + 4.0) / (roll_n + 4.0)
        return (shrunk_ratio - 1.0).clip(-0.45, 0.45)

    df["slot_matchup_edge"] = (
        df.groupby(["opponent", "slot"])["actual_vs_player_baseline"]
        .transform(history_signal)
    )
    df["slot_matchup_edge"] = pd.to_numeric(df["slot_matchup_edge"], errors="coerce").fillna(0.0)

    return df.sort_values(["season", "week", "team", "slot", "player_key"]).copy()


def _fit_base_models(
    data: pd.DataFrame,
    *,
    position: str,
    kind: str,
    opportunity_target: str,
    efficiency_target: str,
    raw_opportunity: str,
    raw_efficiency: str,
    opportunity_candidates: list[str],
    efficiency_candidates: list[str],
    role_filter: str,
    min_efficiency_opportunity: float,
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    subset = data[data["position"].eq(position)].copy()
    subset = subset[pd.to_numeric(subset[role_filter], errors="coerce").notna()].copy()
    if kind == "rush":
        subset = subset[subset[role_filter] >= 3.0]
    else:
        subset = subset[subset[role_filter] >= (1.5 if position == "RB" else 2.5)]

    discovery = subset[subset["season"].isin(DISCOVERY)].copy()
    confirmation = subset[subset["season"].isin(CONFIRMATION)].copy()

    opp_d = discovery.dropna(subset=[opportunity_target] + opportunity_candidates)
    opp_c = confirmation.dropna(subset=[opportunity_target] + opportunity_candidates)
    eff_d = discovery[discovery[opportunity_target] >= min_efficiency_opportunity].dropna(
        subset=[efficiency_target] + efficiency_candidates
    )
    eff_c = confirmation[confirmation[opportunity_target] >= min_efficiency_opportunity].dropna(
        subset=[efficiency_target] + efficiency_candidates
    )

    opp = base.select_model(opp_d, opp_c, opportunity_target, opportunity_candidates, {raw_opportunity})
    eff = base.select_model(eff_d, eff_c, efficiency_target, efficiency_candidates, {raw_efficiency})
    return opp, eff, subset


def _predict_base(
    opp: dict[str, Any],
    eff: dict[str, Any],
    frame: pd.DataFrame,
    *,
    opportunity_clip: tuple[float, float],
    efficiency_clip: tuple[float, float],
) -> np.ndarray:
    pred_opp = np.clip(base.predict(opp, frame), *opportunity_clip)
    pred_eff = np.clip(base.predict(eff, frame), *efficiency_clip)
    return pred_opp * pred_eff


def _fit_no_intercept(y: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    model = sm.OLS(np.asarray(y, float), np.asarray(x, float)).fit(cov_type="HC3")
    return np.asarray(model.params, float), np.asarray(model.pvalues, float)


def _run_market(
    data: pd.DataFrame,
    *,
    name: str,
    position: str,
    kind: str,
    opportunity_target: str,
    efficiency_target: str,
    raw_opportunity: str,
    raw_efficiency: str,
    opportunity_candidates: list[str],
    efficiency_candidates: list[str],
    role_filter: str,
    min_efficiency_opportunity: float,
    opportunity_clip: tuple[float, float],
    efficiency_clip: tuple[float, float],
    actual_col: str,
    player_only_baseline: pd.Series,
) -> dict[str, Any]:
    working = data.copy()
    working["_player_only_baseline"] = player_only_baseline

    opp, eff, subset = _fit_base_models(
        working,
        position=position,
        kind=kind,
        opportunity_target=opportunity_target,
        efficiency_target=efficiency_target,
        raw_opportunity=raw_opportunity,
        raw_efficiency=raw_efficiency,
        opportunity_candidates=opportunity_candidates,
        efficiency_candidates=efficiency_candidates,
        role_filter=role_filter,
        min_efficiency_opportunity=min_efficiency_opportunity,
    )

    required = list(set(
        opp["features"] + eff["features"]
        + [actual_col, "_player_only_baseline", "slot", "strength_centered", "slot_matchup_edge"]
    ))
    usable = subset.dropna(subset=required).copy()
    usable = usable[usable["slot"].astype(str).str.len().gt(0)].copy()
    usable["base_pred"] = _predict_base(
        opp, eff, usable,
        opportunity_clip=opportunity_clip,
        efficiency_clip=efficiency_clip,
    )
    usable["actual"] = pd.to_numeric(usable[actual_col], errors="coerce")
    usable = usable[usable["actual"].notna() & usable["base_pred"].gt(0.5)].copy()

    # Current same-weight matchup test: every player gets the same beta.
    usable["fixed_term"] = usable["base_pred"] * usable["slot_matchup_edge"]

    # Proposed model: matchup sensitivity changes continuously with player strength.
    usable["interaction_term"] = (
        usable["fixed_term"] * usable["strength_centered"]
    )

    disc = usable[usable["season"].isin(DISCOVERY)].copy()
    conf = usable[usable["season"].isin(CONFIRMATION)].copy()
    train = usable[usable["season"].isin(DISCOVERY | CONFIRMATION)].copy()
    hold = usable[usable["season"].isin(HOLDOUT)].copy()

    def residual(frame: pd.DataFrame) -> np.ndarray:
        return (frame["actual"] - frame["base_pred"]).to_numpy(float)

    fixed_d_beta, fixed_d_p = _fit_no_intercept(
        residual(disc), disc[["fixed_term"]].to_numpy(float)
    )
    fixed_c_beta, fixed_c_p = _fit_no_intercept(
        residual(conf), conf[["fixed_term"]].to_numpy(float)
    )
    aware_d_beta, aware_d_p = _fit_no_intercept(
        residual(disc), disc[["fixed_term", "interaction_term"]].to_numpy(float)
    )
    aware_c_beta, aware_c_p = _fit_no_intercept(
        residual(conf), conf[["fixed_term", "interaction_term"]].to_numpy(float)
    )

    fixed_beta, fixed_p = _fit_no_intercept(
        residual(train), train[["fixed_term"]].to_numpy(float)
    )
    aware_beta, aware_p = _fit_no_intercept(
        residual(train), train[["fixed_term", "interaction_term"]].to_numpy(float)
    )

    base_pred = hold["base_pred"].to_numpy(float)
    fixed_pred = base_pred + hold[["fixed_term"]].to_numpy(float) @ fixed_beta
    aware_pred = base_pred + hold[["fixed_term", "interaction_term"]].to_numpy(float) @ aware_beta

    # Keep overlays within a broad sanity range; this prevents a small number of
    # noisy slot histories from dominating the comparison.
    fixed_pred = np.clip(fixed_pred, base_pred * 0.65, base_pred * 1.35)
    aware_pred = np.clip(aware_pred, base_pred * 0.65, base_pred * 1.35)

    actual = hold["actual"].to_numpy(float)
    m_base = _metrics(actual, base_pred)
    m_fixed = _metrics(actual, fixed_pred)
    m_aware = _metrics(actual, aware_pred)

    tier_metrics: dict[str, Any] = {}
    for tier in ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5"]:
        t = hold[hold["tier"].eq(tier)].copy()
        if t.empty:
            continue
        tb = t["base_pred"].to_numpy(float)
        tf = tb + t[["fixed_term"]].to_numpy(float) @ fixed_beta
        ta = tb + t[["fixed_term", "interaction_term"]].to_numpy(float) @ aware_beta
        tf = np.clip(tf, tb * 0.65, tb * 1.35)
        ta = np.clip(ta, tb * 0.65, tb * 1.35)
        truth = t["actual"].to_numpy(float)
        mb = _metrics(truth, tb)
        mf = _metrics(truth, tf)
        ma = _metrics(truth, ta)
        tier_metrics[tier] = {
            "n": int(len(t)),
            "base_mae": mb["mae"],
            "fixed_matchup_mae": mf["mae"],
            "strength_aware_mae": ma["mae"],
            "strength_aware_vs_fixed_improvement_pct": _improvement(mf["mae"], ma["mae"]),
            "strength_aware_vs_base_improvement_pct": _improvement(mb["mae"], ma["mae"]),
        }

    return {
        "market": name,
        "holdout_rows": int(len(hold)),
        "base_metrics": m_base,
        "fixed_same_weight_matchup_metrics": m_fixed,
        "strength_aware_matchup_metrics": m_aware,
        "fixed_vs_base_mae_improvement_pct": _improvement(m_base["mae"], m_fixed["mae"]),
        "strength_aware_vs_base_mae_improvement_pct": _improvement(m_base["mae"], m_aware["mae"]),
        "strength_aware_vs_fixed_mae_improvement_pct": _improvement(m_fixed["mae"], m_aware["mae"]),
        "fixed_beta_train_2021_24": float(fixed_beta[0]),
        "fixed_beta_pvalue_train_2021_24": float(fixed_p[0]),
        "aware_beta_train_2021_24": {
            "same_weight_matchup": float(aware_beta[0]),
            "strength_x_matchup": float(aware_beta[1]),
        },
        "aware_pvalue_train_2021_24": {
            "same_weight_matchup": float(aware_p[0]),
            "strength_x_matchup": float(aware_p[1]),
        },
        "interaction_sign_check": {
            "discovery_2021_23_beta": float(aware_d_beta[1]),
            "discovery_2021_23_pvalue": float(aware_d_p[1]),
            "confirmation_2024_beta": float(aware_c_beta[1]),
            "confirmation_2024_pvalue": float(aware_c_p[1]),
            "same_sign": bool(np.sign(aware_d_beta[1]) == np.sign(aware_c_beta[1])),
            "supports_elite_less_matchup_sensitive": bool(
                aware_d_beta[1] < 0 and aware_c_beta[1] < 0
            ),
        },
        "fixed_sign_check": {
            "discovery_beta": float(fixed_d_beta[0]),
            "discovery_pvalue": float(fixed_d_p[0]),
            "confirmation_beta": float(fixed_c_beta[0]),
            "confirmation_pvalue": float(fixed_c_p[0]),
        },
        "tiers": tier_metrics,
    }


def main() -> None:
    out = Path("artifacts/nfl_player_strength_yardage_ratio")
    out.mkdir(parents=True, exist_ok=True)

    data = _assign_slots(base.build_dataset())

    # Pregame yardage averages: trailing 8 player games, shifted one game so the
    # current result is never included. Rolling history naturally crosses season
    # boundaries, giving Week 1 a prior-season anchor when the player has history.
    data = data.sort_values(["player_key", "season", "week"]).copy()
    data["rushing_yards_avg8_pregame"] = (
        data.groupby("player_key")["rushing_yards"]
        .transform(lambda s: pd.to_numeric(s, errors="coerce").shift(1).rolling(8, min_periods=2).mean())
    )
    data["receiving_yards_avg8_pregame"] = (
        data.groupby("player_key")["receiving_yards"]
        .transform(lambda s: pd.to_numeric(s, errors="coerce").shift(1).rolling(8, min_periods=2).mean())
    )

    # Build market-specific player-only baselines, then learn defense-slot history
    # from prior games only.
    rush_baseline = (
        pd.to_numeric(data["raw_rb_carries"], errors="coerce")
        * pd.to_numeric(data["ypc8"], errors="coerce")
    )
    rec_baseline = (
        pd.to_numeric(data["raw_targets"], errors="coerce")
        * pd.to_numeric(data["ypt8"], errors="coerce")
    )

    # Add the baseline columns before enrichment so strength and opponent history
    # are market-specific.
    rb_rush = data[data["position"].eq("RB")].copy()
    rb_rush["_player_only_baseline"] = rush_baseline.loc[rb_rush.index]
    rb_rush = _add_market_strength_and_slot_matchup(
        rb_rush,
        actual_col="rushing_yards",
        player_only_baseline_col="_player_only_baseline",
        player_avg_col="rushing_yards_avg8_pregame",
    )

    rb_rec = data[data["position"].eq("RB")].copy()
    rb_rec["_player_only_baseline"] = rec_baseline.loc[rb_rec.index]
    rb_rec = _add_market_strength_and_slot_matchup(
        rb_rec,
        actual_col="receiving_yards",
        player_only_baseline_col="_player_only_baseline",
        player_avg_col="receiving_yards_avg8_pregame",
    )

    wr_rec = data[data["position"].eq("WR")].copy()
    wr_rec["_player_only_baseline"] = rec_baseline.loc[wr_rec.index]
    wr_rec = _add_market_strength_and_slot_matchup(
        wr_rec,
        actual_col="receiving_yards",
        player_only_baseline_col="_player_only_baseline",
        player_avg_col="receiving_yards_avg8_pregame",
    )

    common = ["team_total", "team_spread", "home"]
    results: dict[str, Any] = {
        "research_version": "nfl-player-strength-yardage-ratio-2026-09-24",
        "method": (
            "Slots reconstructed from lagged pregame workload; strength is player trailing-8 average yards "
            "divided by the contemporaneous league average of lagged average yards for that exact slot; "
            "tiers use absolute ratio bands (<75%, 75-90%, 90-110%, 110-125%, >=125%); defense-slot matchup learned from "
            "prior normalized performances only with four-game neutral shrinkage; current regression "
            "is baseline; same-weight slot matchup compared with strength-aware interaction; 2021-23 "
            "discovery, 2024 sign confirmation, 2021-24 refit, untouched 2025 holdout."
        ),
    }

    results["rb_rushing_yards"] = _run_market(
        rb_rush,
        name="RB rushing yards",
        position="RB", kind="rush",
        opportunity_target="carries", efficiency_target="actual_ypc",
        raw_opportunity="raw_rb_carries", raw_efficiency="raw_rb_ypc",
        opportunity_candidates=[
            "raw_rb_carries", "carries_avg3", "carries_avg8", "carry_share_avg3",
            "carry_share_avg8", "team_rush_avg3", "team_rush_avg8", "opp_carries_avg8"
        ] + common,
        efficiency_candidates=[
            "raw_rb_ypc", "ypc8", "rush_epa_per_carry8", "opp_ypc8"
        ] + common,
        role_filter="raw_rb_carries", min_efficiency_opportunity=3.0,
        opportunity_clip=(0.0, 35.0), efficiency_clip=(2.0, 7.0),
        actual_col="rushing_yards",
        player_only_baseline=rb_rush["_player_only_baseline"],
    )

    results["rb_receiving_yards"] = _run_market(
        rb_rec,
        name="RB receiving yards",
        position="RB", kind="receive",
        opportunity_target="targets", efficiency_target="actual_ypt",
        raw_opportunity="raw_targets", raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets", "targets_avg3", "targets_avg8", "target_share_avg3",
            "target_share_avg8", "team_targets_avg3", "team_targets_avg8", "opp_targets_avg8"
        ] + common,
        efficiency_candidates=[
            "raw_ypt", "ypt8", "rec_epa_per_target8", "air_yards_per_target8",
            "yac_per_reception8", "opp_ypt8"
        ] + common,
        role_filter="raw_targets", min_efficiency_opportunity=1.0,
        opportunity_clip=(0.0, 15.0), efficiency_clip=(2.5, 12.0),
        actual_col="receiving_yards",
        player_only_baseline=rb_rec["_player_only_baseline"],
    )

    results["wr_receiving_yards"] = _run_market(
        wr_rec,
        name="WR receiving yards",
        position="WR", kind="receive",
        opportunity_target="targets", efficiency_target="actual_ypt",
        raw_opportunity="raw_targets", raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets", "targets_avg3", "targets_avg8", "target_share_avg3",
            "target_share_avg8", "team_targets_avg3", "team_targets_avg8", "opp_targets_avg8"
        ] + common,
        efficiency_candidates=[
            "raw_ypt", "ypt8", "rec_epa_per_target8", "air_yards_per_target8",
            "yac_per_reception8", "opp_ypt8"
        ] + common,
        role_filter="raw_targets", min_efficiency_opportunity=2.0,
        opportunity_clip=(0.0, 20.0), efficiency_clip=(3.0, 16.0),
        actual_col="receiving_yards",
        player_only_baseline=wr_rec["_player_only_baseline"],
    )

    path = out / "nfl_player_strength_yardage_ratio_results.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True))
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
