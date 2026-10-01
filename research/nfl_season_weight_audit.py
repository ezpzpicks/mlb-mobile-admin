"""Counterfactual audit of NFL current-season team-rating weights.

Rebuilds the production team-score regression from public nflverse inputs while
changing only the prior/current-season blend. No production files are modified.

Primary decision question: should the progressive 2026 season-weight curve be
accelerated for Week 4 and beyond?
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from builders import nfl_builder as nflb
from builders import nfl_game_regression as game_regression

AUDIT_SEASONS = {2025: [2, 3, 4], 2026: [2, 3]}
FIXED_WEIGHTS = [round(x, 2) for x in np.arange(0.0, 0.65, 0.05)]
MULTIPLIERS = [0.50, 0.75, 1.00, 1.10, 1.25, 1.40, 1.50, 1.75, 2.00]


def num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def schedule_for(season: int) -> pd.DataFrame:
    schedule = nflb._schedule_for_season(int(season), refresh=True)
    if schedule is None or schedule.empty:
        raise RuntimeError(f"No schedule for {season}")
    out = schedule.copy()
    if "Game Type" in out.columns:
        out = out[out["Game Type"].astype(str).str.upper() == "REG"].copy()
    out["Week"] = pd.to_numeric(out["Week"], errors="coerce")
    out["Away Score"] = pd.to_numeric(out["Away Score"], errors="coerce")
    out["Home Score"] = pd.to_numeric(out["Home Score"], errors="coerce")
    return out


def weather_adjustment(row: pd.Series) -> float:
    return float(
        nflb._weather_adjustment(
            str(row.get("Roof", "") or ""),
            num(row.get("Temperature"), 70.0),
            num(row.get("Wind"), 6.0),
            "",
        )
    )


def build_ratings(
    prior: pd.DataFrame,
    current: pd.DataFrame,
    season: int,
    week: int,
    weight_fn: Callable[[int, float | None], float],
) -> pd.DataFrame:
    original = nflb._season_weight
    nflb._season_weight = weight_fn
    try:
        return nflb._blend_team_metrics(prior, current, int(season), int(week))
    finally:
        nflb._season_weight = original


def project_week(
    schedule: pd.DataFrame,
    ratings: pd.DataFrame,
    season: int,
    week: int,
) -> pd.DataFrame:
    games = schedule[
        (pd.to_numeric(schedule["Week"], errors="coerce") == int(week))
        & pd.to_numeric(schedule["Away Score"], errors="coerce").notna()
        & pd.to_numeric(schedule["Home Score"], errors="coerce").notna()
    ].copy()
    rows: list[dict[str, Any]] = []
    for _, game in games.iterrows():
        away_team = nflb._normalize_team(game.get("Away Team", ""))
        home_team = nflb._normalize_team(game.get("Home Team", ""))
        away = nflb._team_row(ratings, away_team, season, week)
        home = nflb._team_row(ratings, home_team, season, week)
        home_field = num(
            nflb._automatic_home_field_advantage(season, week, home_team, game).get("value"),
            0.0,
        )
        projection = game_regression.project_matchup(
            away,
            home,
            {"offense_absence": 0.0, "defense_absence": 0.0},
            {"offense_absence": 0.0, "defense_absence": 0.0},
            {
                "home_field": home_field,
                "home_rest_edge": 0.0,
                "weather_total_adjustment": weather_adjustment(game),
                "manual_total_adjustment": 0.0,
                "manual_home_margin_adjustment": 0.0,
            },
        )
        actual_margin = num(game.get("Home Score")) - num(game.get("Away Score"))
        rows.append(
            {
                "season": int(season),
                "week": int(week),
                "game_id": str(game.get("Game ID", "")),
                "away": away_team,
                "home": home_team,
                "actual_margin": actual_margin,
                "projected_margin": num(projection.get("margin")),
                "abs_error": abs(num(projection.get("margin")) - actual_margin),
            }
        )
    return pd.DataFrame(rows)


def summarize(frame: pd.DataFrame) -> dict[str, float | int | None]:
    if frame is None or frame.empty:
        return {"n": 0, "mae": None, "bias": None}
    err = frame["projected_margin"].astype(float) - frame["actual_margin"].astype(float)
    return {
        "n": int(len(frame)),
        "mae": round(float(np.mean(np.abs(err))), 4),
        "bias": round(float(np.mean(err)), 4),
    }


def main() -> None:
    output_dir = Path("artifacts/nfl_season_weight_audit")
    output_dir.mkdir(parents=True, exist_ok=True)

    original_weight_fn = nflb._season_weight
    schedules: dict[int, pd.DataFrame] = {}
    full_metrics: dict[int, pd.DataFrame] = {}
    pbp: dict[int, pd.DataFrame] = {}

    for season in [2024, 2025, 2026]:
        print(f"Loading {season} schedule/PBP...")
        schedules[season] = schedule_for(season)
        pbp[season] = nflb._load_pbp_season(season)
        if pbp[season] is None or pbp[season].empty:
            raise RuntimeError(f"No PBP for {season}")
        full_metrics[season] = nflb._season_team_metrics(
            pbp[season], schedules[season], through_week=None
        )

    detailed_rows: list[dict[str, Any]] = []
    production_frames: dict[tuple[int, int], pd.DataFrame] = {}
    fixed_frames: dict[tuple[int, int, float], pd.DataFrame] = {}
    multiplier_frames: dict[tuple[int, int, float], pd.DataFrame] = {}

    for season, weeks in AUDIT_SEASONS.items():
        prior = full_metrics[season - 1]
        for week in weeks:
            current = nflb._season_team_metrics(
                pbp[season], schedules[season], through_week=week - 1
            )

            prod_ratings = build_ratings(
                prior, current, season, week, original_weight_fn
            )
            prod_frame = project_week(schedules[season], prod_ratings, season, week)
            production_frames[(season, week)] = prod_frame
            avg_prod_weight = float(
                pd.to_numeric(prod_ratings["Current Season Weight"], errors="coerce").mean()
            )
            prod_summary = summarize(prod_frame)
            detailed_rows.append(
                {
                    "season": season,
                    "week": week,
                    "mode": "production",
                    "parameter": 1.0,
                    "avg_current_weight": round(avg_prod_weight, 4),
                    **prod_summary,
                }
            )

            for fixed_weight in FIXED_WEIGHTS:
                fn = lambda projection_week, current_games=None, w=fixed_weight: float(w)
                ratings = build_ratings(prior, current, season, week, fn)
                frame = project_week(schedules[season], ratings, season, week)
                fixed_frames[(season, week, fixed_weight)] = frame
                summary = summarize(frame)
                avg_weight = float(
                    pd.to_numeric(ratings["Current Season Weight"], errors="coerce").mean()
                )
                detailed_rows.append(
                    {
                        "season": season,
                        "week": week,
                        "mode": "fixed_weight",
                        "parameter": fixed_weight,
                        "avg_current_weight": round(avg_weight, 4),
                        **summary,
                    }
                )

            for multiplier in MULTIPLIERS:
                def scaled_weight(
                    projection_week: int,
                    current_games: float | None = None,
                    m: float = multiplier,
                ) -> float:
                    return float(np.clip(original_weight_fn(projection_week, current_games) * m, 0.0, 0.93))

                ratings = build_ratings(prior, current, season, week, scaled_weight)
                frame = project_week(schedules[season], ratings, season, week)
                multiplier_frames[(season, week, multiplier)] = frame
                summary = summarize(frame)
                avg_weight = float(
                    pd.to_numeric(ratings["Current Season Weight"], errors="coerce").mean()
                )
                detailed_rows.append(
                    {
                        "season": season,
                        "week": week,
                        "mode": "multiplier",
                        "parameter": multiplier,
                        "avg_current_weight": round(avg_weight, 4),
                        **summary,
                    }
                )

    detail = pd.DataFrame(detailed_rows)
    detail.to_csv(output_dir / "weight_grid_by_week.csv", index=False)

    fixed_best: dict[str, Any] = {}
    for season, weeks in AUDIT_SEASONS.items():
        for week in weeks:
            subset = detail[
                (detail["season"] == season)
                & (detail["week"] == week)
                & (detail["mode"] == "fixed_weight")
            ].sort_values(["mae", "parameter"])
            fixed_best[f"{season}_W{week}"] = subset.head(5).to_dict(orient="records")

    multiplier_summary: list[dict[str, Any]] = []
    scopes = {
        "2025_W2_W4": [(2025, 2), (2025, 3), (2025, 4)],
        "2026_W2": [(2026, 2)],
        "2026_W3": [(2026, 3)],
        "2026_W2_W3": [(2026, 2), (2026, 3)],
        "all_audit_weeks": [(2025, 2), (2025, 3), (2025, 4), (2026, 2), (2026, 3)],
    }
    for scope_name, pairs in scopes.items():
        for multiplier in MULTIPLIERS:
            frames = [multiplier_frames[(s, w, multiplier)] for s, w in pairs]
            combined = pd.concat(frames, ignore_index=True)
            row = {
                "scope": scope_name,
                "multiplier": multiplier,
                **summarize(combined),
            }
            multiplier_summary.append(row)
    multiplier_df = pd.DataFrame(multiplier_summary)
    multiplier_df.to_csv(output_dir / "multiplier_summary.csv", index=False)

    production_summary: list[dict[str, Any]] = []
    for scope_name, pairs in scopes.items():
        combined = pd.concat([production_frames[p] for p in pairs], ignore_index=True)
        production_summary.append({"scope": scope_name, **summarize(combined)})

    hist = multiplier_df[multiplier_df["scope"] == "2025_W2_W4"].sort_values(
        ["mae", "multiplier"]
    )
    best_hist_multiplier = float(hist.iloc[0]["multiplier"])
    validation_2026 = multiplier_df[
        (multiplier_df["scope"] == "2026_W2_W3")
        & (multiplier_df["multiplier"] == best_hist_multiplier)
    ].iloc[0].to_dict()

    # Translate multiplier candidates into a Week 4 effective weight for a typical
    # team with three completed games, using the current production function.
    week4_base = float(original_weight_fn(4, 3.0))
    week4_candidates = {
        str(m): round(float(np.clip(week4_base * m, 0.0, 0.93)), 4)
        for m in MULTIPLIERS
    }

    result = {
        "audit_version": "nfl-season-weight-audit-2026-10-01",
        "method": (
            "Production team-score regression and public nflverse inputs; only the "
            "prior/current team-metric blend changes. Current-personnel lineup "
            "absence overlays are held neutral so comparisons isolate season weight."
        ),
        "fixed_weight_grid": FIXED_WEIGHTS,
        "multipliers": MULTIPLIERS,
        "production_week4_effective_weight_three_games": round(week4_base, 4),
        "week4_effective_weight_by_multiplier": week4_candidates,
        "production_summary": production_summary,
        "best_fixed_weights_by_week": fixed_best,
        "multiplier_summary": multiplier_df.to_dict(orient="records"),
        "historical_selection": {
            "best_multiplier_on_2025_W2_W4": best_hist_multiplier,
            "2025_metrics": hist.iloc[0].to_dict(),
            "untouched_2026_W2_W3_validation": validation_2026,
        },
    }
    (output_dir / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
