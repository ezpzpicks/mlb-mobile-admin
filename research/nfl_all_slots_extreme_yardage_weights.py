"""All-slot extreme yardage-tier matchup-weight backtest.

Grading variable:
    player's pregame trailing-8 average yardage
    / league average pregame trailing-8 yardage for the EXACT slot that week.

Exact slots:
    QB1, RB1, RB2, WR1, WR2, WR3, TE1

Extreme matchup weights by yardage tier:
    Tier 1 >=125% of exact-slot average:   0%
    Tier 2 110-125%:                     50%
    Tier 3 90-110%:                     100%
    Tier 4 75-90%:                      150%
    Tier 5 <75%:                        200%

All features are pregame/lagged. Discovery is 2021-23, 2024 confirms model
structure, 2021-24 is refit, and 2025 remains untouched holdout.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from builders import nfl_builder as nflb
from research import nfl_rb_wr_prop_regression as base

DISCOVERY = {2021, 2022, 2023}
CONFIRMATION = {2024}
TRAIN = DISCOVERY | CONFIRMATION
HOLDOUT = {2025}
LOAD_SEASONS = [2020, 2021, 2022, 2023, 2024, 2025]

TIER_WEIGHTS = {
    "Tier 1": 0.00,
    "Tier 2": 0.50,
    "Tier 3": 1.00,
    "Tier 4": 1.50,
    "Tier 5": 2.00,
}


def _num(value: Any, default: float = np.nan) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _first_column(df: pd.DataFrame, names: list[str], default: str = "") -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series(default, index=df.index)


def _normalize_position(value: Any) -> str:
    text = str(value or "").upper().strip()
    if text in {"RB", "HB", "FB"}:
        return "RB"
    if text in {"WR", "TE", "QB"}:
        return text
    return text


def _normalize_player_stats(season: int) -> pd.DataFrame:
    raw = nflb._load_player_stats_season(int(season))
    if raw is None or raw.empty:
        raise RuntimeError(f"No nflverse player stats for {season}")
    df = raw.copy()
    if "season_type" in df.columns:
        df = df[df["season_type"].astype(str).str.upper() == "REG"].copy()
    df["season"] = int(season)
    df["week"] = pd.to_numeric(df.get("week"), errors="coerce")
    df = df[df["week"].notna()].copy()
    df["week"] = df["week"].astype(int)
    df["player"] = _first_column(df, ["player_display_name", "player_name", "full_name"]).astype(str).str.strip()
    df["player_key"] = df["player"].map(nflb._normalize_name)
    df["team"] = _first_column(df, ["recent_team", "team"]).map(nflb._normalize_team)
    df["opponent"] = _first_column(df, ["opponent_team"]).map(nflb._normalize_team)
    df["position"] = _first_column(df, ["position", "position_group"]).map(_normalize_position)

    numeric = [
        "attempts", "completions", "passing_yards", "passing_epa",
        "carries", "rushing_yards", "rushing_epa",
        "targets", "receptions", "receiving_yards", "receiving_epa",
        "receiving_air_yards", "receiving_yards_after_catch",
    ]
    for col in numeric:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df[df["player_key"].str.len() > 0].copy()


def _rolling_mean(frame: pd.DataFrame, group: list[str], value: str, window: int) -> pd.Series:
    return frame.groupby(group, sort=False)[value].transform(
        lambda s: pd.to_numeric(s, errors="coerce").shift(1).rolling(window, min_periods=1).mean()
    )


def _rolling_ratio(frame: pd.DataFrame, group: list[str], numerator: str, denominator: str, window: int) -> pd.Series:
    n = frame.groupby(group, sort=False)[numerator].transform(
        lambda s: pd.to_numeric(s, errors="coerce").shift(1).rolling(window, min_periods=1).sum()
    )
    d = frame.groupby(group, sort=False)[denominator].transform(
        lambda s: pd.to_numeric(s, errors="coerce").shift(1).rolling(window, min_periods=1).sum()
    )
    return n / d.replace(0, np.nan)


def _add_game_context(players: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for season in sorted(players["season"].unique()):
        schedule = nflb._schedule_for_season(int(season), refresh=True)
        if schedule is None or schedule.empty:
            continue
        schedule = schedule.copy()
        if "Game Type" in schedule.columns:
            schedule = schedule[schedule["Game Type"].astype(str).str.upper() == "REG"].copy()
        for _, game in schedule.iterrows():
            week = int(_num(game.get("Week"), 0))
            if week <= 0:
                continue
            home = nflb._normalize_team(game.get("Home Team", ""))
            away = nflb._normalize_team(game.get("Away Team", ""))
            spread = _num(game.get("Spread Line"), 0.0)
            total = _num(game.get("Total Line"), 45.0)
            if not math.isfinite(total):
                total = 45.0
            if not math.isfinite(spread):
                spread = 0.0
            rows += [
                {
                    "season": int(season), "week": week, "team": home, "home": 1.0,
                    "team_spread": spread, "game_total": total,
                    "team_total": float(np.clip((total - spread) / 2.0, 6.0, 48.0)),
                },
                {
                    "season": int(season), "week": week, "team": away, "home": 0.0,
                    "team_spread": -spread, "game_total": total,
                    "team_total": float(np.clip((total + spread) / 2.0, 6.0, 48.0)),
                },
            ]
    ctx = pd.DataFrame(rows).drop_duplicates(["season", "week", "team"], keep="last")
    return players.merge(ctx, on=["season", "week", "team"], how="left")


def build_dataset() -> pd.DataFrame:
    df = pd.concat([_normalize_player_stats(s) for s in LOAD_SEASONS], ignore_index=True)
    df = df[df["position"].isin(["QB", "RB", "WR", "TE"])].copy()
    df = df.sort_values(["player_key", "season", "week"]).reset_index(drop=True)

    team_week = df.groupby(["season", "week", "team"], as_index=False).agg(
        team_pass_attempts=("attempts", "sum"),
        team_rush_attempts=("carries", "sum"),
        team_targets=("targets", "sum"),
    )
    df = df.merge(team_week, on=["season", "week", "team"], how="left")
    df["attempt_share"] = df["attempts"] / df["team_pass_attempts"].replace(0, np.nan)
    df["carry_share"] = df["carries"] / df["team_rush_attempts"].replace(0, np.nan)
    df["target_share_calc"] = df["targets"] / df["team_targets"].replace(0, np.nan)

    df = df.sort_values(["player_key", "season", "week"]).reset_index(drop=True)
    for w in [3, 8]:
        df[f"attempts_avg{w}"] = _rolling_mean(df, ["player_key"], "attempts", w)
        df[f"carries_avg{w}"] = _rolling_mean(df, ["player_key"], "carries", w)
        df[f"targets_avg{w}"] = _rolling_mean(df, ["player_key"], "targets", w)
        df[f"attempt_share_avg{w}"] = _rolling_mean(df, ["player_key"], "attempt_share", w)
        df[f"carry_share_avg{w}"] = _rolling_mean(df, ["player_key"], "carry_share", w)
        df[f"target_share_avg{w}"] = _rolling_mean(df, ["player_key"], "target_share_calc", w)

        df[f"ypa{w}"] = _rolling_ratio(df, ["player_key"], "passing_yards", "attempts", w)
        df[f"pass_epa_per_attempt{w}"] = _rolling_ratio(df, ["player_key"], "passing_epa", "attempts", w)
        df[f"ypc{w}"] = _rolling_ratio(df, ["player_key"], "rushing_yards", "carries", w)
        df[f"rush_epa_per_carry{w}"] = _rolling_ratio(df, ["player_key"], "rushing_epa", "carries", w)
        df[f"ypt{w}"] = _rolling_ratio(df, ["player_key"], "receiving_yards", "targets", w)
        df[f"rec_epa_per_target{w}"] = _rolling_ratio(df, ["player_key"], "receiving_epa", "targets", w)
        df[f"air_yards_per_target{w}"] = _rolling_ratio(df, ["player_key"], "receiving_air_yards", "targets", w)
        df[f"yac_per_reception{w}"] = _rolling_ratio(df, ["player_key"], "receiving_yards_after_catch", "receptions", w)

    # Exact grading inputs: trailing average yards before the current game.
    df["passing_yards_avg8_pregame"] = _rolling_mean(df, ["player_key"], "passing_yards", 8)
    df["rushing_yards_avg8_pregame"] = _rolling_mean(df, ["player_key"], "rushing_yards", 8)
    df["receiving_yards_avg8_pregame"] = _rolling_mean(df, ["player_key"], "receiving_yards", 8)

    team_week = team_week.sort_values(["team", "season", "week"]).reset_index(drop=True)
    for w in [3, 8]:
        for col in ["team_pass_attempts", "team_rush_attempts", "team_targets"]:
            team_week[f"{col}_avg{w}"] = _rolling_mean(team_week, ["team"], col, w)
    df = df.merge(
        team_week[
            [
                "season", "week", "team",
                "team_pass_attempts_avg3", "team_pass_attempts_avg8",
                "team_rush_attempts_avg3", "team_rush_attempts_avg8",
                "team_targets_avg3", "team_targets_avg8",
            ]
        ],
        on=["season", "week", "team"], how="left",
    )

    # Broad position opponent allowance, lagged.
    defense = df.groupby(["season", "week", "opponent", "position"], as_index=False).agg(
        allowed_attempts=("attempts", "sum"),
        allowed_pass_yards=("passing_yards", "sum"),
        allowed_carries=("carries", "sum"),
        allowed_rush_yards=("rushing_yards", "sum"),
        allowed_targets=("targets", "sum"),
        allowed_rec_yards=("receiving_yards", "sum"),
    ).rename(columns={"opponent": "defense"})
    defense = defense.sort_values(["defense", "position", "season", "week"]).reset_index(drop=True)
    defense["opp_attempts_avg8"] = _rolling_mean(defense, ["defense", "position"], "allowed_attempts", 8)
    defense["opp_ypa8"] = _rolling_ratio(defense, ["defense", "position"], "allowed_pass_yards", "allowed_attempts", 8)
    defense["opp_carries_avg8"] = _rolling_mean(defense, ["defense", "position"], "allowed_carries", 8)
    defense["opp_ypc8"] = _rolling_ratio(defense, ["defense", "position"], "allowed_rush_yards", "allowed_carries", 8)
    defense["opp_targets_avg8"] = _rolling_mean(defense, ["defense", "position"], "allowed_targets", 8)
    defense["opp_ypt8"] = _rolling_ratio(defense, ["defense", "position"], "allowed_rec_yards", "allowed_targets", 8)
    df = df.merge(
        defense[
            [
                "season", "week", "defense", "position",
                "opp_attempts_avg8", "opp_ypa8",
                "opp_carries_avg8", "opp_ypc8",
                "opp_targets_avg8", "opp_ypt8",
            ]
        ],
        left_on=["season", "week", "opponent", "position"],
        right_on=["season", "week", "defense", "position"],
        how="left",
    )
    df = _add_game_context(df)

    # Historical position priors only fill missing lagged efficiency in small samples.
    priors: dict[str, float] = {}
    qb = df["position"].eq("QB") & df["attempts"].ge(10)
    priors["QB_ypa"] = float(df.loc[qb, "passing_yards"].sum() / max(df.loc[qb, "attempts"].sum(), 1))
    rb = df["position"].eq("RB") & df["carries"].ge(3)
    priors["RB_ypc"] = float(df.loc[rb, "rushing_yards"].sum() / max(df.loc[rb, "carries"].sum(), 1))
    for pos in ["RB", "WR", "TE"]:
        m = df["position"].eq(pos) & df["targets"].ge(1)
        priors[f"{pos}_ypt"] = float(df.loc[m, "receiving_yards"].sum() / max(df.loc[m, "targets"].sum(), 1))

    qbm = df["position"].eq("QB")
    df.loc[qbm, "ypa8"] = df.loc[qbm, "ypa8"].fillna(priors["QB_ypa"])
    df.loc[qbm, "opp_ypa8"] = df.loc[qbm, "opp_ypa8"].fillna(priors["QB_ypa"])
    rbm = df["position"].eq("RB")
    df.loc[rbm, "ypc8"] = df.loc[rbm, "ypc8"].fillna(priors["RB_ypc"])
    df.loc[rbm, "opp_ypc8"] = df.loc[rbm, "opp_ypc8"].fillna(priors["RB_ypc"])
    for pos in ["RB", "WR", "TE"]:
        m = df["position"].eq(pos)
        df.loc[m, "ypt8"] = df.loc[m, "ypt8"].fillna(priors[f"{pos}_ypt"])
        df.loc[m, "opp_ypt8"] = df.loc[m, "opp_ypt8"].fillna(priors[f"{pos}_ypt"])

    for long_col, short_col in [
        ("attempts_avg8", "attempts_avg3"),
        ("carries_avg8", "carries_avg3"),
        ("targets_avg8", "targets_avg3"),
        ("attempt_share_avg8", "attempt_share_avg3"),
        ("carry_share_avg8", "carry_share_avg3"),
        ("target_share_avg8", "target_share_avg3"),
        ("team_pass_attempts_avg8", "team_pass_attempts_avg3"),
        ("team_rush_attempts_avg8", "team_rush_attempts_avg3"),
        ("team_targets_avg8", "team_targets_avg3"),
    ]:
        df[long_col] = pd.to_numeric(df[long_col], errors="coerce").fillna(pd.to_numeric(df[short_col], errors="coerce"))

    df["raw_qb_attempts"] = df["team_pass_attempts_avg8"] * df["attempt_share_avg8"]
    df["raw_qb_ypa"] = df["ypa8"] * np.clip(df["opp_ypa8"] / priors["QB_ypa"], 0.82, 1.18)
    df["raw_rb_carries"] = df["team_rush_attempts_avg8"] * df["carry_share_avg8"]
    df["raw_rb_ypc"] = df["ypc8"] * np.clip(df["opp_ypc8"] / priors["RB_ypc"], 0.82, 1.18)
    df["raw_targets"] = df["team_targets_avg8"] * df["target_share_avg8"]
    df["raw_ypt"] = np.nan
    for pos in ["RB", "WR", "TE"]:
        m = df["position"].eq(pos)
        df.loc[m, "raw_ypt"] = df.loc[m, "ypt8"] * np.clip(
            df.loc[m, "opp_ypt8"] / priors[f"{pos}_ypt"], 0.82, 1.18
        )

    df["actual_ypa"] = df["passing_yards"] / df["attempts"].replace(0, np.nan)
    df["actual_ypc"] = df["rushing_yards"] / df["carries"].replace(0, np.nan)
    df["actual_ypt"] = df["receiving_yards"] / df["targets"].replace(0, np.nan)
    return df.replace([np.inf, -np.inf], np.nan)


def assign_slots(data: pd.DataFrame) -> pd.DataFrame:
    df = data.copy()
    df["slot"] = ""

    qb = df["position"].eq("QB")
    qrank = df.loc[qb].groupby(["season", "week", "team"])["raw_qb_attempts"].rank(method="first", ascending=False)
    df.loc[qb, "_rank"] = qrank
    df.loc[qb & df["_rank"].eq(1), "slot"] = "QB1"

    rb = df["position"].eq("RB")
    rrank = df.loc[rb].groupby(["season", "week", "team"])["raw_rb_carries"].rank(method="first", ascending=False)
    df.loc[rb, "_rank"] = rrank
    df.loc[rb & df["_rank"].eq(1), "slot"] = "RB1"
    df.loc[rb & df["_rank"].eq(2), "slot"] = "RB2"

    wr = df["position"].eq("WR")
    wrank = df.loc[wr].groupby(["season", "week", "team"])["raw_targets"].rank(method="first", ascending=False)
    df.loc[wr, "_rank"] = wrank
    df.loc[wr & df["_rank"].eq(1), "slot"] = "WR1"
    df.loc[wr & df["_rank"].eq(2), "slot"] = "WR2"
    df.loc[wr & df["_rank"].eq(3), "slot"] = "WR3"

    te = df["position"].eq("TE")
    trank = df.loc[te].groupby(["season", "week", "team"])["raw_targets"].rank(method="first", ascending=False)
    df.loc[te, "_rank"] = trank
    df.loc[te & df["_rank"].eq(1), "slot"] = "TE1"
    return df.drop(columns=["_rank"], errors="ignore")


def fit_base_models(
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
    role_min: float,
    min_efficiency_opportunity: float,
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    subset = data[data["position"].eq(position)].copy()
    subset = subset[pd.to_numeric(subset[role_filter], errors="coerce").ge(role_min)].copy()
    d = subset[subset["season"].isin(DISCOVERY)].copy()
    c = subset[subset["season"].isin(CONFIRMATION)].copy()

    od = d.dropna(subset=[opportunity_target] + opportunity_candidates)
    oc = c.dropna(subset=[opportunity_target] + opportunity_candidates)
    ed = d[d[opportunity_target].ge(min_efficiency_opportunity)].dropna(
        subset=[efficiency_target] + efficiency_candidates
    )
    ec = c[c[opportunity_target].ge(min_efficiency_opportunity)].dropna(
        subset=[efficiency_target] + efficiency_candidates
    )
    opp = base.select_model(od, oc, opportunity_target, opportunity_candidates, {raw_opportunity})
    eff = base.select_model(ed, ec, efficiency_target, efficiency_candidates, {raw_efficiency})
    return opp, eff, subset


def predict_base(
    opp: dict[str, Any],
    eff: dict[str, Any],
    frame: pd.DataFrame,
    opportunity_clip: tuple[float, float],
    efficiency_clip: tuple[float, float],
) -> np.ndarray:
    po = np.clip(base.predict(opp, frame), *opportunity_clip)
    pe = np.clip(base.predict(eff, frame), *efficiency_clip)
    return po * pe


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


def add_strength_and_matchup(
    frame: pd.DataFrame,
    *,
    actual_col: str,
    player_avg_col: str,
    player_baseline_col: str,
) -> pd.DataFrame:
    df = frame.copy()
    df = df[df["slot"].astype(str).str.len().gt(0)].copy()
    df[player_avg_col] = pd.to_numeric(df[player_avg_col], errors="coerce")
    df[player_baseline_col] = pd.to_numeric(df[player_baseline_col], errors="coerce")
    df[actual_col] = pd.to_numeric(df[actual_col], errors="coerce")
    df = df[
        df[player_avg_col].gt(0)
        & df[player_baseline_col].gt(0.5)
        & df[actual_col].notna()
    ].copy()

    # Exact-slot league average of each player's own lagged pregame yardage average.
    df["slot_league_avg_yards"] = df.groupby(["season", "week", "slot"])[player_avg_col].transform("mean")
    df["strength_ratio"] = df[player_avg_col] / df["slot_league_avg_yards"].replace(0, np.nan)
    df["tier"] = pd.cut(
        df["strength_ratio"],
        bins=[-np.inf, 0.75, 0.90, 1.10, 1.25, np.inf],
        labels=["Tier 5", "Tier 4", "Tier 3", "Tier 2", "Tier 1"],
        include_lowest=True,
    ).astype(str)

    df["actual_vs_player_baseline"] = (
        df[actual_col] / df[player_baseline_col].clip(lower=1.0)
    ).clip(0.0, 4.0)
    df = df.sort_values(["opponent", "slot", "season", "week", "team", "player_key"]).copy()

    def history(vals: pd.Series) -> pd.Series:
        x = pd.to_numeric(vals, errors="coerce").shift(1)
        s = x.rolling(8, min_periods=1).sum()
        n = x.rolling(8, min_periods=1).count()
        return ((s + 4.0) / (n + 4.0) - 1.0).clip(-0.45, 0.45)

    df["slot_matchup_edge"] = (
        df.groupby(["opponent", "slot"])["actual_vs_player_baseline"].transform(history)
    ).fillna(0.0)
    return df.sort_values(["season", "week", "team", "slot", "player_key"]).copy()


def fit_fixed_beta(frame: pd.DataFrame) -> tuple[float, float]:
    train = frame[frame["season"].isin(TRAIN)].copy()
    y = (train["actual"] - train["base_pred"]).to_numpy(float)
    x = train[["fixed_term"]].to_numpy(float)
    model = sm.OLS(y, x).fit(cov_type="HC3")
    return float(model.params[0]), float(model.pvalues[0])


def run_market(
    data: pd.DataFrame,
    *,
    market: str,
    position: str,
    actual_col: str,
    player_avg_col: str,
    player_baseline: pd.Series,
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
    valid_slots: list[str],
) -> dict[str, Any]:
    working = data.copy()
    working["_player_only_baseline"] = player_baseline
    enriched = add_strength_and_matchup(
        working[working["position"].eq(position)].copy(),
        actual_col=actual_col,
        player_avg_col=player_avg_col,
        player_baseline_col="_player_only_baseline",
    )

    opp, eff, subset = fit_base_models(
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
        opp["features"] + eff["features"] +
        [actual_col, "_player_only_baseline", "slot", "tier", "slot_matchup_edge"]
    ))
    usable = subset.dropna(subset=required).copy()
    usable = usable[usable["slot"].isin(valid_slots)].copy()
    usable["base_pred"] = predict_base(opp, eff, usable, opportunity_clip, efficiency_clip)
    usable["actual"] = pd.to_numeric(usable[actual_col], errors="coerce")
    usable = usable[usable["actual"].notna() & usable["base_pred"].gt(0.5)].copy()
    usable["fixed_term"] = usable["base_pred"] * usable["slot_matchup_edge"]

    beta, beta_p = fit_fixed_beta(usable)
    hold = usable[usable["season"].isin(HOLDOUT)].copy()
    actual = hold["actual"].to_numpy(float)
    base_pred = hold["base_pred"].to_numpy(float)
    fixed_pred = base_pred + hold["fixed_term"].to_numpy(float) * beta
    weights = hold["tier"].map(TIER_WEIGHTS).astype(float).to_numpy()
    extreme_pred = base_pred + hold["fixed_term"].to_numpy(float) * beta * weights

    fixed_pred = np.clip(fixed_pred, base_pred * 0.65, base_pred * 1.35)
    extreme_pred = np.clip(extreme_pred, base_pred * 0.65, base_pred * 1.35)

    base_m = metrics(actual, base_pred)
    fixed_m = metrics(actual, fixed_pred)
    extreme_m = metrics(actual, extreme_pred)

    slots: dict[str, Any] = {}
    for slot in valid_slots:
        smask = hold["slot"].eq(slot).to_numpy()
        if not smask.any():
            continue
        sb = metrics(actual[smask], base_pred[smask])
        sf = metrics(actual[smask], fixed_pred[smask])
        se = metrics(actual[smask], extreme_pred[smask])
        tier_rows: dict[str, Any] = {}
        for tier in ["Tier 1", "Tier 2", "Tier 3", "Tier 4", "Tier 5"]:
            mask = smask & hold["tier"].eq(tier).to_numpy()
            if not mask.any():
                continue
            mb = metrics(actual[mask], base_pred[mask])
            mf = metrics(actual[mask], fixed_pred[mask])
            me = metrics(actual[mask], extreme_pred[mask])
            tier_rows[tier] = {
                "n": int(mask.sum()),
                "weight": TIER_WEIGHTS[tier],
                "base_mae": mb["mae"],
                "fixed_100pct_mae": mf["mae"],
                "extreme_mae": me["mae"],
                "extreme_vs_base_improvement_pct": improvement(mb["mae"], me["mae"]),
                "extreme_vs_fixed_improvement_pct": improvement(mf["mae"], me["mae"]),
            }
        slots[slot] = {
            "n": int(smask.sum()),
            "base_metrics": sb,
            "fixed_100pct_metrics": sf,
            "extreme_metrics": se,
            "extreme_vs_base_mae_improvement_pct": improvement(sb["mae"], se["mae"]),
            "extreme_vs_fixed_mae_improvement_pct": improvement(sf["mae"], se["mae"]),
            "tiers": tier_rows,
        }

    return {
        "market": market,
        "position": position,
        "holdout_rows": int(len(hold)),
        "fixed_beta_train_2021_24": beta,
        "fixed_beta_pvalue_train_2021_24": beta_p,
        "base_metrics": base_m,
        "fixed_100pct_metrics": fixed_m,
        "extreme_metrics": extreme_m,
        "extreme_vs_base_mae_improvement_pct": improvement(base_m["mae"], extreme_m["mae"]),
        "extreme_vs_fixed_mae_improvement_pct": improvement(fixed_m["mae"], extreme_m["mae"]),
        "slots": slots,
    }


def main() -> None:
    out = Path("artifacts/nfl_all_slots_extreme_yardage_weights")
    out.mkdir(parents=True, exist_ok=True)
    data = assign_slots(build_dataset())

    common = ["team_total", "team_spread", "home"]

    qb_player_baseline = pd.to_numeric(data["raw_qb_attempts"], errors="coerce") * pd.to_numeric(data["ypa8"], errors="coerce")
    rb_rush_player_baseline = pd.to_numeric(data["raw_rb_carries"], errors="coerce") * pd.to_numeric(data["ypc8"], errors="coerce")
    rec_player_baseline = pd.to_numeric(data["raw_targets"], errors="coerce") * pd.to_numeric(data["ypt8"], errors="coerce")

    results: dict[str, Any] = {
        "research_version": "nfl-all-slots-extreme-yardage-tier-weights-2026-09-24",
        "tier_definition": {
            "Tier 1": ">=125% of exact-slot league-average pregame yardage",
            "Tier 2": "110%-125%",
            "Tier 3": "90%-110%",
            "Tier 4": "75%-90%",
            "Tier 5": "<75%",
        },
        "tier_matchup_weights": TIER_WEIGHTS,
        "method": (
            "Player grade is trailing-8 pregame average yardage divided by the same week's league average "
            "of that lagged yardage for the exact slot. Slot matchup histories use only prior games. "
            "2021-23 discovery, 2024 confirmation/refit, untouched 2025 holdout."
        ),
    }

    results["qb_passing_yards"] = run_market(
        data,
        market="QB passing yards",
        position="QB",
        actual_col="passing_yards",
        player_avg_col="passing_yards_avg8_pregame",
        player_baseline=qb_player_baseline,
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
        valid_slots=["QB1"],
    )

    results["rb_rushing_yards"] = run_market(
        data,
        market="RB rushing yards",
        position="RB",
        actual_col="rushing_yards",
        player_avg_col="rushing_yards_avg8_pregame",
        player_baseline=rb_rush_player_baseline,
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
        valid_slots=["RB1", "RB2"],
    )

    results["rb_receiving_yards"] = run_market(
        data,
        market="RB receiving yards",
        position="RB",
        actual_col="receiving_yards",
        player_avg_col="receiving_yards_avg8_pregame",
        player_baseline=rec_player_baseline,
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
        valid_slots=["RB1", "RB2"],
    )

    results["wr_receiving_yards"] = run_market(
        data,
        market="WR receiving yards",
        position="WR",
        actual_col="receiving_yards",
        player_avg_col="receiving_yards_avg8_pregame",
        player_baseline=rec_player_baseline,
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
        valid_slots=["WR1", "WR2", "WR3"],
    )

    results["te_receiving_yards"] = run_market(
        data,
        market="TE receiving yards",
        position="TE",
        actual_col="receiving_yards",
        player_avg_col="receiving_yards_avg8_pregame",
        player_baseline=rec_player_baseline,
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
        valid_slots=["TE1"],
    )

    path = out / "nfl_all_slots_extreme_yardage_weights_results.json"
    path.write_text(json.dumps(results, indent=2, sort_keys=True))
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
