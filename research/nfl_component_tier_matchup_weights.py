"""Independent opportunity-tier and efficiency-tier NFL matchup backtest.

Each player receives TWO independent exact-slot grades:
  opportunity: lagged attempts/carries/targets vs exact-slot league average
  efficiency:  lagged YPA/YPC/YPT vs exact-slot league average

Tier weights remain the deliberately extreme test:
  T1=0%, T2=50%, T3=100%, T4=150%, T5=200%.

We test:
  1) opportunity-side matchup only, tiered by opportunity
  2) efficiency-side matchup only, tiered by efficiency
  3) both component matchups at 100%
  4) both component matchups independently tiered

2021-23 discovery / 2024 confirmation-refit / untouched 2025 holdout.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from research import nfl_all_slots_extreme_yardage_weights as prior
from research import nfl_rb_wr_prop_regression as base

DISCOVERY = {2021, 2022, 2023}
CONFIRMATION = {2024}
TRAIN = DISCOVERY | CONFIRMATION
HOLDOUT = {2025}
TIER_WEIGHTS = {
    "Tier 1": 0.00,
    "Tier 2": 0.50,
    "Tier 3": 1.00,
    "Tier 4": 1.50,
    "Tier 5": 2.00,
}
TIER_ORDER = ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5"]


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    actual = np.asarray(actual, float)
    pred = np.asarray(pred, float)
    err = pred - actual
    return {
        "n": int(len(actual)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(err)),
    }


def improvement(old: float, new: float) -> float:
    return float(100.0 * (old - new) / max(old, 1e-9))


def _tier_from_ratio(ratio: pd.Series) -> pd.Series:
    return pd.cut(
        ratio,
        bins=[-np.inf, 0.75, 0.90, 1.10, 1.25, np.inf],
        labels=["Tier 5", "Tier 4", "Tier 3", "Tier 2", "Tier 1"],
        include_lowest=True,
    ).astype(str)


def _history_ratio_edge(values: pd.Series) -> pd.Series:
    x = pd.to_numeric(values, errors="coerce").shift(1)
    total = x.rolling(8, min_periods=1).sum()
    n = x.rolling(8, min_periods=1).count()
    return ((total + 4.0) / (n + 4.0) - 1.0).clip(-0.45, 0.45)


def add_component_grades_and_matchups(
    frame: pd.DataFrame,
    *,
    actual_opportunity_col: str,
    actual_efficiency_col: str,
    opportunity_avg_col: str,
    efficiency_avg_col: str,
) -> pd.DataFrame:
    df = frame.copy()
    df = df[df["slot"].astype(str).str.len().gt(0)].copy()

    for col in [
        actual_opportunity_col, actual_efficiency_col,
        opportunity_avg_col, efficiency_avg_col,
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Independent exact-slot grades. A player may occupy different tiers for
    # opportunity and efficiency in the same game.
    df["slot_avg_opportunity"] = df.groupby(
        ["season", "week", "slot"]
    )[opportunity_avg_col].transform("mean")
    df["slot_avg_efficiency"] = df.groupby(
        ["season", "week", "slot"]
    )[efficiency_avg_col].transform("mean")

    df["opportunity_ratio"] = (
        df[opportunity_avg_col] / df["slot_avg_opportunity"].replace(0, np.nan)
    )
    df["efficiency_ratio"] = (
        df[efficiency_avg_col] / df["slot_avg_efficiency"].replace(0, np.nan)
    )
    df["opportunity_tier"] = _tier_from_ratio(df["opportunity_ratio"])
    df["efficiency_tier"] = _tier_from_ratio(df["efficiency_ratio"])

    # Exact-slot matchup histories are also decomposed by the same two projection
    # components, using only prior games against that opponent/slot.
    df["actual_vs_own_opportunity"] = (
        df[actual_opportunity_col] / df[opportunity_avg_col].replace(0, np.nan)
    ).clip(0.0, 4.0)
    df["actual_vs_own_efficiency"] = (
        df[actual_efficiency_col] / df[efficiency_avg_col].replace(0, np.nan)
    ).clip(0.0, 4.0)

    df = df.sort_values(
        ["opponent", "slot", "season", "week", "team", "player_key"]
    ).copy()
    df["opportunity_matchup_edge"] = (
        df.groupby(["opponent", "slot"])["actual_vs_own_opportunity"]
        .transform(_history_ratio_edge)
        .fillna(0.0)
    )
    df["efficiency_matchup_edge"] = (
        df.groupby(["opponent", "slot"])["actual_vs_own_efficiency"]
        .transform(_history_ratio_edge)
        .fillna(0.0)
    )
    return df.sort_values(
        ["season", "week", "team", "slot", "player_key"]
    ).copy()


def _fit_beta(
    frame: pd.DataFrame,
    actual_col: str,
    base_col: str,
    term_col: str,
    min_volume_col: str | None = None,
    min_volume: float = 0.0,
) -> tuple[float, float]:
    train = frame[frame["season"].isin(TRAIN)].copy()
    if min_volume_col:
        train = train[pd.to_numeric(train[min_volume_col], errors="coerce").ge(min_volume)]
    train = train.dropna(subset=[actual_col, base_col, term_col])
    y = (train[actual_col] - train[base_col]).to_numpy(float)
    x = train[[term_col]].to_numpy(float)
    model = sm.OLS(y, x).fit(cov_type="HC3")
    return float(model.params[0]), float(model.pvalues[0])


def _component_preds(
    opp_model: dict[str, Any],
    eff_model: dict[str, Any],
    frame: pd.DataFrame,
    opp_clip: tuple[float, float],
    eff_clip: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray]:
    opp = np.clip(base.predict(opp_model, frame), *opp_clip)
    eff = np.clip(base.predict(eff_model, frame), *eff_clip)
    return opp, eff


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
    opportunity_clip: tuple[float, float],
    efficiency_clip: tuple[float, float],
) -> dict[str, Any]:
    position_rows = data[data["position"].eq(position)].copy()
    enriched = add_component_grades_and_matchups(
        position_rows,
        actual_opportunity_col=actual_opportunity_col,
        actual_efficiency_col=actual_efficiency_col,
        opportunity_avg_col=opportunity_avg_col,
        efficiency_avg_col=efficiency_avg_col,
    )

    opp_model, eff_model, subset = prior.fit_base_models(
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

    required = list(set(
        opp_model["features"] + eff_model["features"] + [
            actual_yards_col, actual_opportunity_col, actual_efficiency_col,
            "slot", "opportunity_tier", "efficiency_tier",
            "opportunity_matchup_edge", "efficiency_matchup_edge",
        ]
    ))
    usable = subset.dropna(subset=required).copy()
    usable = usable[usable["slot"].isin(valid_slots)].copy()

    base_opp, base_eff = _component_preds(
        opp_model, eff_model, usable, opportunity_clip, efficiency_clip
    )
    usable["base_opp"] = base_opp
    usable["base_eff"] = base_eff
    usable["actual_yards"] = pd.to_numeric(usable[actual_yards_col], errors="coerce")
    usable["actual_opp"] = pd.to_numeric(usable[actual_opportunity_col], errors="coerce")
    usable["actual_eff"] = pd.to_numeric(usable[actual_efficiency_col], errors="coerce")
    usable = usable[
        usable["actual_yards"].notna()
        & usable["actual_opp"].notna()
        & usable["base_opp"].gt(0)
        & usable["base_eff"].gt(0)
    ].copy()

    # Recompute aligned base arrays after filtering.
    base_opp = usable["base_opp"].to_numpy(float)
    base_eff = usable["base_eff"].to_numpy(float)
    usable["opp_term"] = usable["base_opp"] * usable["opportunity_matchup_edge"]
    usable["eff_term"] = usable["base_eff"] * usable["efficiency_matchup_edge"]

    beta_opp, p_opp = _fit_beta(
        usable, "actual_opp", "base_opp", "opp_term"
    )
    beta_eff, p_eff = _fit_beta(
        usable, "actual_eff", "base_eff", "eff_term",
        min_volume_col=actual_opportunity_col,
        min_volume=min_efficiency_opportunity,
    )

    hold = usable[usable["season"].isin(HOLDOUT)].copy()
    actual_yards = hold["actual_yards"].to_numpy(float)
    bo = hold["base_opp"].to_numpy(float)
    be = hold["base_eff"].to_numpy(float)
    ot = hold["opp_term"].to_numpy(float)
    et = hold["eff_term"].to_numpy(float)

    opp_100 = bo + beta_opp * ot
    eff_100 = be + beta_eff * et
    opp_w = hold["opportunity_tier"].map(TIER_WEIGHTS).astype(float).to_numpy()
    eff_w = hold["efficiency_tier"].map(TIER_WEIGHTS).astype(float).to_numpy()
    opp_ext = bo + beta_opp * ot * opp_w
    eff_ext = be + beta_eff * et * eff_w

    # Keep exact-slot overlays bounded relative to the component projection, then
    # respect the component's original model clip.
    opp_100 = np.clip(opp_100, bo * 0.65, bo * 1.35)
    eff_100 = np.clip(eff_100, be * 0.65, be * 1.35)
    opp_ext = np.clip(opp_ext, bo * 0.65, bo * 1.35)
    eff_ext = np.clip(eff_ext, be * 0.65, be * 1.35)
    opp_100 = np.clip(opp_100, *opportunity_clip)
    opp_ext = np.clip(opp_ext, *opportunity_clip)
    eff_100 = np.clip(eff_100, *efficiency_clip)
    eff_ext = np.clip(eff_ext, *efficiency_clip)

    pred_base = bo * be
    pred_opp_only = opp_ext * be
    pred_eff_only = bo * eff_ext
    pred_both_100 = opp_100 * eff_100
    pred_both_ext = opp_ext * eff_ext

    variants = {
        "base": pred_base,
        "opportunity_tier_only": pred_opp_only,
        "efficiency_tier_only": pred_eff_only,
        "both_components_100pct": pred_both_100,
        "both_components_independent_tiers": pred_both_ext,
    }
    variant_metrics = {name: metrics(actual_yards, pred) for name, pred in variants.items()}
    base_mae = variant_metrics["base"]["mae"]

    slot_results: dict[str, Any] = {}
    for slot in valid_slots:
        smask = hold["slot"].eq(slot).to_numpy()
        if not smask.any():
            continue
        slot_entry: dict[str, Any] = {
            "n": int(smask.sum()),
            "variants": {},
            "opportunity_tiers": {},
            "efficiency_tiers": {},
            "tier_cross": {},
        }
        for name, pred in variants.items():
            m = metrics(actual_yards[smask], pred[smask])
            slot_entry["variants"][name] = m

        for tier in TIER_ORDER:
            omask = smask & hold["opportunity_tier"].eq(tier).to_numpy()
            if omask.any():
                mb = metrics(actual_yards[omask], pred_base[omask])
                mx = metrics(actual_yards[omask], pred_opp_only[omask])
                slot_entry["opportunity_tiers"][tier] = {
                    "n": int(omask.sum()),
                    "weight": TIER_WEIGHTS[tier],
                    "base_mae": mb["mae"],
                    "opportunity_tier_mae": mx["mae"],
                    "improvement_pct": improvement(mb["mae"], mx["mae"]),
                }

            emask = smask & hold["efficiency_tier"].eq(tier).to_numpy()
            if emask.any():
                mb = metrics(actual_yards[emask], pred_base[emask])
                mx = metrics(actual_yards[emask], pred_eff_only[emask])
                slot_entry["efficiency_tiers"][tier] = {
                    "n": int(emask.sum()),
                    "weight": TIER_WEIGHTS[tier],
                    "base_mae": mb["mae"],
                    "efficiency_tier_mae": mx["mae"],
                    "improvement_pct": improvement(mb["mae"], mx["mae"]),
                }

        # Cross-tier cells reveal whether e.g. T1 opportunity / T3 efficiency
        # behaves differently from a single blended player grade.
        for otier in TIER_ORDER:
            for etier in TIER_ORDER:
                cmask = (
                    smask
                    & hold["opportunity_tier"].eq(otier).to_numpy()
                    & hold["efficiency_tier"].eq(etier).to_numpy()
                )
                if cmask.sum() < 8:
                    continue
                mb = metrics(actual_yards[cmask], pred_base[cmask])
                mx = metrics(actual_yards[cmask], pred_both_ext[cmask])
                slot_entry["tier_cross"][f"{otier} opportunity / {etier} efficiency"] = {
                    "n": int(cmask.sum()),
                    "base_mae": mb["mae"],
                    "independent_tier_mae": mx["mae"],
                    "improvement_pct": improvement(mb["mae"], mx["mae"]),
                }

        slot_results[slot] = slot_entry

    return {
        "market": market,
        "position": position,
        "holdout_rows": int(len(hold)),
        "opportunity_matchup_beta_train_2021_24": beta_opp,
        "opportunity_matchup_pvalue_train_2021_24": p_opp,
        "efficiency_matchup_beta_train_2021_24": beta_eff,
        "efficiency_matchup_pvalue_train_2021_24": p_eff,
        "variant_metrics": variant_metrics,
        "improvement_vs_base_pct": {
            name: improvement(base_mae, m["mae"])
            for name, m in variant_metrics.items()
            if name != "base"
        },
        "slots": slot_results,
    }


def main() -> None:
    out = Path("artifacts/nfl_component_tier_matchup_weights")
    out.mkdir(parents=True, exist_ok=True)

    data = prior.assign_slots(prior.build_dataset())
    common = ["team_total", "team_spread", "home"]

    results: dict[str, Any] = {
        "research_version": "nfl-independent-opportunity-efficiency-tiers-2026-09-24",
        "tier_definition": {
            "Tier 1": ">=125% of exact-slot component average",
            "Tier 2": "110%-125%",
            "Tier 3": "90%-110%",
            "Tier 4": "75%-90%",
            "Tier 5": "<75%",
        },
        "tier_matchup_weights": TIER_WEIGHTS,
        "method": (
            "Opportunity and efficiency are graded independently against the exact-slot "
            "league average using lagged pregame inputs. Exact-slot opponent effects are "
            "also decomposed into opportunity and efficiency histories. 2021-24 learns "
            "component matchup coefficients; 2025 is untouched holdout."
        ),
    }

    results["qb_passing_yards"] = run_market(
        data,
        market="QB passing yards",
        position="QB",
        valid_slots=["QB1"],
        actual_yards_col="passing_yards",
        actual_opportunity_col="attempts",
        actual_efficiency_col="actual_ypa",
        opportunity_avg_col="attempts_avg8",
        efficiency_avg_col="ypa8",
        opportunity_target="attempts",
        efficiency_target="actual_ypa",
        raw_opportunity="raw_qb_attempts",
        raw_efficiency="raw_qb_ypa",
        opportunity_candidates=[
            "raw_qb_attempts", "attempts_avg3", "attempts_avg8",
            "attempt_share_avg3", "attempt_share_avg8",
            "team_pass_attempts_avg3", "team_pass_attempts_avg8",
            "opp_attempts_avg8",
        ] + common,
        efficiency_candidates=[
            "raw_qb_ypa", "ypa8", "pass_epa_per_attempt8", "opp_ypa8",
        ] + common,
        role_filter="raw_qb_attempts",
        role_min=10.0,
        min_efficiency_opportunity=10.0,
        opportunity_clip=(10.0, 55.0),
        efficiency_clip=(4.0, 10.0),
    )

    results["rb_rushing_yards"] = run_market(
        data,
        market="RB rushing yards",
        position="RB",
        valid_slots=["RB1", "RB2"],
        actual_yards_col="rushing_yards",
        actual_opportunity_col="carries",
        actual_efficiency_col="actual_ypc",
        opportunity_avg_col="carries_avg8",
        efficiency_avg_col="ypc8",
        opportunity_target="carries",
        efficiency_target="actual_ypc",
        raw_opportunity="raw_rb_carries",
        raw_efficiency="raw_rb_ypc",
        opportunity_candidates=[
            "raw_rb_carries", "carries_avg3", "carries_avg8",
            "carry_share_avg3", "carry_share_avg8",
            "team_rush_attempts_avg3", "team_rush_attempts_avg8",
            "opp_carries_avg8",
        ] + common,
        efficiency_candidates=[
            "raw_rb_ypc", "ypc8", "rush_epa_per_carry8", "opp_ypc8",
        ] + common,
        role_filter="raw_rb_carries",
        role_min=3.0,
        min_efficiency_opportunity=3.0,
        opportunity_clip=(0.0, 35.0),
        efficiency_clip=(2.0, 7.0),
    )

    results["rb_receiving_yards"] = run_market(
        data,
        market="RB receiving yards",
        position="RB",
        valid_slots=["RB1", "RB2"],
        actual_yards_col="receiving_yards",
        actual_opportunity_col="targets",
        actual_efficiency_col="actual_ypt",
        opportunity_avg_col="targets_avg8",
        efficiency_avg_col="ypt8",
        opportunity_target="targets",
        efficiency_target="actual_ypt",
        raw_opportunity="raw_targets",
        raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets", "targets_avg3", "targets_avg8",
            "target_share_avg3", "target_share_avg8",
            "team_targets_avg3", "team_targets_avg8",
            "opp_targets_avg8",
        ] + common,
        efficiency_candidates=[
            "raw_ypt", "ypt8", "rec_epa_per_target8",
            "air_yards_per_target8", "yac_per_reception8", "opp_ypt8",
        ] + common,
        role_filter="raw_targets",
        role_min=1.5,
        min_efficiency_opportunity=1.0,
        opportunity_clip=(0.0, 15.0),
        efficiency_clip=(2.5, 12.0),
    )

    results["wr_receiving_yards"] = run_market(
        data,
        market="WR receiving yards",
        position="WR",
        valid_slots=["WR1", "WR2", "WR3"],
        actual_yards_col="receiving_yards",
        actual_opportunity_col="targets",
        actual_efficiency_col="actual_ypt",
        opportunity_avg_col="targets_avg8",
        efficiency_avg_col="ypt8",
        opportunity_target="targets",
        efficiency_target="actual_ypt",
        raw_opportunity="raw_targets",
        raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets", "targets_avg3", "targets_avg8",
            "target_share_avg3", "target_share_avg8",
            "team_targets_avg3", "team_targets_avg8",
            "opp_targets_avg8",
        ] + common,
        efficiency_candidates=[
            "raw_ypt", "ypt8", "rec_epa_per_target8",
            "air_yards_per_target8", "yac_per_reception8", "opp_ypt8",
        ] + common,
        role_filter="raw_targets",
        role_min=2.5,
        min_efficiency_opportunity=2.0,
        opportunity_clip=(0.0, 20.0),
        efficiency_clip=(3.0, 16.0),
    )

    results["te_receiving_yards"] = run_market(
        data,
        market="TE receiving yards",
        position="TE",
        valid_slots=["TE1"],
        actual_yards_col="receiving_yards",
        actual_opportunity_col="targets",
        actual_efficiency_col="actual_ypt",
        opportunity_avg_col="targets_avg8",
        efficiency_avg_col="ypt8",
        opportunity_target="targets",
        efficiency_target="actual_ypt",
        raw_opportunity="raw_targets",
        raw_efficiency="raw_ypt",
        opportunity_candidates=[
            "raw_targets", "targets_avg3", "targets_avg8",
            "target_share_avg3", "target_share_avg8",
            "team_targets_avg3", "team_targets_avg8",
            "opp_targets_avg8",
        ] + common,
        efficiency_candidates=[
            "raw_ypt", "ypt8", "rec_epa_per_target8",
            "air_yards_per_target8", "yac_per_reception8", "opp_ypt8",
        ] + common,
        role_filter="raw_targets",
        role_min=1.5,
        min_efficiency_opportunity=1.0,
        opportunity_clip=(0.0, 15.0),
        efficiency_clip=(2.5, 14.0),
    )

    path = out / "nfl_component_tier_matchup_weights_results.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True))
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
