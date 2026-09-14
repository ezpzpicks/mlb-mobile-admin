"""NFL slot-specific defensive matchup learning.

This layer learns only from the current season and only from weeks completed
before the projection week. It uses the saved pregame lineup snapshots to map
actual weekly player production to the role that player occupied when the game
was projected (RB1, RB2, WR1, WR2, WR3, etc.).

The existing NFL model already adjusts for broad opponent position strength
(RB/WR/TE/QB). This layer intentionally applies only the *slot-specific residual*
after removing that broad positional tendency. That prevents double counting a
defense that is simply bad against every WR while still surfacing a real WR2 or
RB1 outlier.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

MODEL_VERSION = "nfl-v4.10-te1-prop-surface-2026-09-13"

TRACKED_SLOTS = {"QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1"}
SLOT_FAMILIES = {
    "QB": ("QB",),
    "RB1": ("RB1", "RB2"),
    "RB2": ("RB1", "RB2"),
    "WR1": ("WR1", "WR2", "WR3"),
    "WR2": ("WR1", "WR2", "WR3"),
    "WR3": ("WR1", "WR2", "WR3"),
    "TE1": ("TE1",),
}

MARKET_STATS = {
    "Passing Attempts": "attempts",
    "Passing Completions": "completions",
    "Passing Yards": "passing_yards",
    "Rushing Attempts": "carries",
    "Rushing Yards": "rushing_yards",
    "Targets": "targets",
    "Receptions": "receptions",
    "Receiving Yards": "receiving_yards",
    "Anytime TD": "anytime_tds",
}

MARKET_SLOTS = {
    "Passing Attempts": {"QB"},
    "Passing Completions": {"QB"},
    "Passing Yards": {"QB"},
    "Rushing Attempts": {"QB", "RB1", "RB2"},
    "Rushing Yards": {"QB", "RB1", "RB2"},
    "Targets": {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"},
    "Receptions": {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"},
    "Receiving Yards": {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"},
    "Anytime TD": {"QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1"},
}

# The broad position model is already doing most of the matchup work. These
# strengths govern only the incremental slot-specific outlier signal.
MARKET_STRENGTH = {
    "Passing Attempts": 0.18,
    "Passing Completions": 0.18,
    "Passing Yards": 0.24,
    "Rushing Attempts": 0.34,
    "Rushing Yards": 0.52,
    "Targets": 0.38,
    "Receptions": 0.38,
    "Receiving Yards": 0.52,
    "Anytime TD": 0.72,
}

MARKET_CAP = {
    "Passing Attempts": 0.04,
    "Passing Completions": 0.04,
    "Passing Yards": 0.05,
    "Rushing Attempts": 0.075,
    "Rushing Yards": 0.12,
    "Targets": 0.08,
    "Receptions": 0.08,
    "Receiving Yards": 0.12,
    "Anytime TD": 0.18,
}

_HISTORY_CACHE: dict[tuple[int, int], pd.DataFrame] = {}
_PROFILE_CACHE: dict[tuple[int, int, str, str, str], dict[str, float]] = {}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _slot(value: Any) -> str:
    raw = str(value or "").strip().upper()
    return "TE1" if raw == "TE" else raw


def _market_allowed_for_slot(market: str, slot: str) -> bool:
    return slot in MARKET_SLOTS.get(market, set())


def _slot_history(nfl_builder: Any, season: int, through_week: int) -> pd.DataFrame:
    """Return current-season actual production mapped to saved pregame slots."""
    season = int(season)
    through_week = int(through_week)
    key = (season, through_week)
    if key in _HISTORY_CACHE:
        return _HISTORY_CACHE[key].copy()
    if through_week < 1:
        empty = pd.DataFrame(columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
        _HISTORY_CACHE[key] = empty
        return empty.copy()

    try:
        lineups = nfl_builder.read_sheet(nfl_builder.LINEUP_TAB, nfl_builder.LINEUP_COLUMNS)
    except Exception:
        lineups = pd.DataFrame()
    if lineups is None or lineups.empty:
        empty = pd.DataFrame(columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
        _HISTORY_CACHE[key] = empty
        return empty.copy()

    lineup = lineups.copy()
    lineup["_season"] = pd.to_numeric(lineup.get("Season"), errors="coerce")
    lineup["_week"] = pd.to_numeric(lineup.get("Week"), errors="coerce")
    lineup["_slot"] = lineup.get("Slot", "").map(_slot)
    lineup["_unit"] = lineup.get("Unit", "").astype(str).str.lower().str.strip()
    lineup["_team"] = lineup.get("Team", "").map(nfl_builder._normalize_team)
    lineup["_player"] = lineup.get("Player", "").map(nfl_builder._normalize_name)
    lineup["_depth"] = pd.to_numeric(lineup.get("Depth Rank"), errors="coerce").fillna(99)
    lineup = lineup[
        (lineup["_season"] == season)
        & (lineup["_week"] >= 1)
        & (lineup["_week"] <= through_week)
        & (lineup["_unit"] == "offense")
        & lineup["_slot"].isin(TRACKED_SLOTS)
        & ~lineup["_player"].isin({"", "tbd", "unknown"})
    ].copy()
    if lineup.empty:
        empty = pd.DataFrame(columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
        _HISTORY_CACHE[key] = empty
        return empty.copy()

    # A game save replaces its prior snapshot, but keep this deterministic if a
    # workbook ever contains a duplicate row.
    lineup = lineup.sort_values(["_week", "_team", "_slot", "_depth"])
    lineup = lineup.drop_duplicates(["_week", "_team", "_slot"], keep="first")

    try:
        stats = nfl_builder._load_player_stats_season(season)
    except Exception:
        stats = pd.DataFrame()
    if stats is None or stats.empty:
        empty = pd.DataFrame(columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
        _HISTORY_CACHE[key] = empty
        return empty.copy()

    frame = stats.copy()
    if "season_type" in frame.columns:
        frame = frame[frame["season_type"].astype(str).str.upper() == "REG"].copy()
    frame["_week"] = pd.to_numeric(frame.get("week"), errors="coerce")
    frame = frame[(frame["_week"] >= 1) & (frame["_week"] <= through_week)].copy()
    if frame.empty:
        empty = pd.DataFrame(columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
        _HISTORY_CACHE[key] = empty
        return empty.copy()

    frame["_team"] = nfl_builder._player_team_column(frame).map(nfl_builder._normalize_team)
    frame["_player"] = nfl_builder._player_name_column(frame).map(nfl_builder._normalize_name)
    frame["_opponent"] = nfl_builder._column(frame, "opponent_team", default="").map(nfl_builder._normalize_team)
    frame["_position"] = nfl_builder._player_position_column(frame).map(nfl_builder._position_group)
    base_stats = set(MARKET_STATS.values()) - {"anytime_tds"}
    base_stats.update({"rushing_tds", "receiving_tds"})
    for stat in base_stats:
        frame[stat] = nfl_builder._numeric_frame_column(frame, stat)
    frame["anytime_tds"] = (
        pd.to_numeric(frame["rushing_tds"], errors="coerce").fillna(0.0)
        + pd.to_numeric(frame["receiving_tds"], errors="coerce").fillna(0.0)
    )

    aggregate = frame.groupby(["_week", "_team", "_opponent", "_player"], as_index=False).agg(
        **{stat: (stat, "sum") for stat in set(MARKET_STATS.values())}
    )
    merged = lineup.merge(aggregate, on=["_week", "_team", "_player"], how="inner")
    if merged.empty:
        empty = pd.DataFrame(columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
        _HISTORY_CACHE[key] = empty
        return empty.copy()

    rows: list[pd.DataFrame] = []
    for market, stat in MARKET_STATS.items():
        eligible = merged[merged["_slot"].isin(MARKET_SLOTS[market])].copy()
        if eligible.empty:
            continue
        part = pd.DataFrame({
            "season": season,
            "week": pd.to_numeric(eligible["_week"], errors="coerce"),
            "team": eligible["_team"].astype(str),
            "opponent": eligible["_opponent"].astype(str),
            "slot": eligible["_slot"].astype(str),
            "player": eligible["_player"].astype(str),
            "market": market,
            "actual": pd.to_numeric(eligible[stat], errors="coerce"),
        })
        rows.append(part)

    # TE1 is the only modeled tight-end depth slot, while the broad defensive
    # TE matchup already includes every tight end on the opponent. Preserve
    # that broad all-TE production as a pseudo-slot so TE1 can learn only the
    # residual primary-TE tendency instead of double counting general TE weakness.
    te_all = frame[frame["_position"].astype(str) == "TE"].copy()
    if not te_all.empty:
        te_all = te_all.groupby(["_week", "_team", "_opponent"], as_index=False).agg(
            targets=("targets", "sum"),
            receptions=("receptions", "sum"),
            receiving_yards=("receiving_yards", "sum"),
        )
        for market, stat in (("Targets", "targets"), ("Receptions", "receptions"), ("Receiving Yards", "receiving_yards")):
            rows.append(pd.DataFrame({
                "season": season,
                "week": pd.to_numeric(te_all["_week"], errors="coerce"),
                "team": te_all["_team"].astype(str),
                "opponent": te_all["_opponent"].astype(str),
                "slot": "TE_ALL",
                "player": "ALL TIGHT ENDS",
                "market": market,
                "actual": pd.to_numeric(te_all[stat], errors="coerce"),
            }))

    history = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"]
    )
    history = history[
        history["actual"].notna()
        & history["opponent"].astype(str).str.len().gt(0)
    ].copy()
    history = history.drop_duplicates(["season", "week", "team", "slot", "market"], keep="last")
    _HISTORY_CACHE[key] = history
    return history.copy()


def _touchdown_profile_from_history(history: pd.DataFrame, opponent: str, slot: str) -> dict[str, float]:
    """Learn where a defense funnels TDs after removing its general TD allowance."""
    slot = _slot(slot)
    opponent = str(opponent or "").strip().upper()
    if history is None or history.empty or slot not in TRACKED_SLOTS:
        return {"adjustment_pct": 0.0, "sample": 0.0}

    market_rows = history[history["market"].astype(str) == "Anytime TD"].copy()
    slot_rows = market_rows[market_rows["slot"].astype(str) == slot].copy()
    defense_slot = slot_rows[slot_rows["opponent"].astype(str) == opponent].copy()
    if market_rows.empty or slot_rows.empty or defense_slot.empty:
        return {"adjustment_pct": 0.0, "sample": 0.0}

    league_slot = float(pd.to_numeric(slot_rows["actual"], errors="coerce").mean())
    league_slot_n = int(pd.to_numeric(slot_rows["actual"], errors="coerce").notna().sum())
    defense_slot_values = pd.to_numeric(defense_slot["actual"], errors="coerce").dropna()
    defense_slot_avg = float(defense_slot_values.mean()) if not defense_slot_values.empty else 0.0
    sample = int(defense_slot[["week", "team"]].drop_duplicates().shape[0])
    if not (math.isfinite(league_slot) and league_slot > 0.04 and math.isfinite(defense_slot_avg)) or league_slot_n < 8:
        return {"adjustment_pct": 0.0, "sample": float(sample)}

    game_totals = market_rows.groupby(
        ["season", "week", "team", "opponent"], as_index=False
    )["actual"].sum()
    league_total = float(pd.to_numeric(game_totals["actual"], errors="coerce").mean()) if not game_totals.empty else 0.0
    defense_totals = game_totals[game_totals["opponent"].astype(str) == opponent].copy()
    defense_total_values = pd.to_numeric(defense_totals["actual"], errors="coerce").dropna()
    defense_total_avg = float(defense_total_values.mean()) if not defense_total_values.empty else league_total
    defense_games = int(defense_totals[["week", "team"]].drop_duplicates().shape[0]) if not defense_totals.empty else sample
    if not (math.isfinite(league_total) and league_total > 0.10 and math.isfinite(defense_total_avg)):
        return {"adjustment_pct": 0.0, "sample": float(sample)}

    # Touchdowns are sparse. Four league-average games act as a Bayesian prior
    # before the observed defense-specific rates can move the model materially.
    prior_games = 4.0
    shrunk_slot_rate = (
        float(defense_slot_values.sum()) + prior_games * league_slot
    ) / max(sample + prior_games, 1.0)
    shrunk_total_rate = (
        float(defense_total_values.sum()) + prior_games * league_total
    ) / max(defense_games + prior_games, 1.0)

    slot_index = shrunk_slot_rate / max(league_slot, 0.04)
    general_td_index = shrunk_total_rate / max(league_total, 0.10)
    outlier_index = slot_index / max(general_td_index, 0.35)
    raw_outlier = float(np.clip(outlier_index - 1.0, -1.0, 1.0))
    sample_weight = float(sample / (sample + 3.5)) if sample > 0 else 0.0
    league_coverage = float(np.clip(league_slot_n / 24.0, 0.40, 1.0))
    adjustment_pct = float(np.clip(
        raw_outlier * sample_weight * league_coverage * MARKET_STRENGTH["Anytime TD"],
        -MARKET_CAP["Anytime TD"], MARKET_CAP["Anytime TD"],
    ))

    absolute_index = defense_slot_avg / max(league_slot, 0.04)
    general_index_raw = defense_total_avg / max(league_total, 0.10)
    return {
        "adjustment_pct": adjustment_pct,
        "sample": float(sample),
        "league_sample": float(league_slot_n),
        "defense_slot_avg": defense_slot_avg,
        "league_slot_avg": league_slot,
        "defense_total_avg": defense_total_avg,
        "league_total_avg": league_total,
        "absolute_edge_pct": absolute_index - 1.0,
        "family_edge_pct": general_index_raw - 1.0,
        "slot_outlier_pct": outlier_index - 1.0,
        "sample_weight": sample_weight,
    }


def _profile_from_history(history: pd.DataFrame, opponent: str, slot: str, market: str) -> dict[str, float]:
    """Measure an exact-slot outlier after removing broad position weakness."""
    slot = _slot(slot)
    opponent = str(opponent or "").strip().upper()
    if market == "Anytime TD":
        return _touchdown_profile_from_history(history, opponent, slot)
    if history is None or history.empty or slot not in TRACKED_SLOTS or not _market_allowed_for_slot(market, slot):
        return {"adjustment_pct": 0.0, "sample": 0.0}

    market_rows = history[history["market"].astype(str) == str(market)].copy()
    slot_rows = market_rows[market_rows["slot"].astype(str) == slot].copy()
    defense_slot = slot_rows[slot_rows["opponent"].astype(str) == opponent].copy()
    if slot_rows.empty or defense_slot.empty:
        return {"adjustment_pct": 0.0, "sample": 0.0}

    league_slot = float(pd.to_numeric(slot_rows["actual"], errors="coerce").mean())
    defense_slot_avg = float(pd.to_numeric(defense_slot["actual"], errors="coerce").mean())
    league_slot_n = int(pd.to_numeric(slot_rows["actual"], errors="coerce").notna().sum())
    sample = int(defense_slot[["week", "team"]].drop_duplicates().shape[0])
    if not (math.isfinite(league_slot) and league_slot > 0.20 and math.isfinite(defense_slot_avg)) or league_slot_n < 8:
        return {"adjustment_pct": 0.0, "sample": float(sample)}

    absolute_index = defense_slot_avg / league_slot
    family_slots = SLOT_FAMILIES.get(slot, (slot,))
    family_rows = market_rows[market_rows["slot"].isin(family_slots)].copy()
    defense_family = family_rows[family_rows["opponent"].astype(str) == opponent].copy()
    league_family = float(pd.to_numeric(family_rows["actual"], errors="coerce").mean()) if not family_rows.empty else league_slot
    defense_family_avg = float(pd.to_numeric(defense_family["actual"], errors="coerce").mean()) if not defense_family.empty else defense_slot_avg
    family_index = defense_family_avg / league_family if math.isfinite(league_family) and league_family > 0.20 else 1.0

    if slot == "TE1":
        broad_rows = market_rows[market_rows["slot"].astype(str) == "TE_ALL"].copy()
        defense_broad = broad_rows[broad_rows["opponent"].astype(str) == opponent].copy()
        league_broad = float(pd.to_numeric(broad_rows["actual"], errors="coerce").mean()) if not broad_rows.empty else math.nan
        defense_broad_avg = float(pd.to_numeric(defense_broad["actual"], errors="coerce").mean()) if not defense_broad.empty else math.nan
        if math.isfinite(league_broad) and league_broad > 0.20 and math.isfinite(defense_broad_avg):
            family_index = defense_broad_avg / league_broad
            outlier_index = absolute_index / max(family_index, 0.25)
        else:
            # Without an all-TE baseline, broad TE defense already owns this signal.
            outlier_index = 1.0
    elif len(family_slots) == 1:
        # QB has no separate same-position depth slot to normalize against.
        outlier_index = 1.0
    else:
        outlier_index = absolute_index / max(family_index, 0.25)

    raw_outlier = float(np.clip(outlier_index - 1.0, -0.75, 0.75))
    sample_weight = float(sample / (sample + 3.0)) if sample > 0 else 0.0
    league_coverage = float(np.clip(league_slot_n / 20.0, 0.35, 1.0))
    strength = MARKET_STRENGTH.get(market, 0.30)
    cap = MARKET_CAP.get(market, 0.08)
    adjustment_pct = float(np.clip(raw_outlier * sample_weight * league_coverage * strength, -cap, cap))

    return {
        "adjustment_pct": adjustment_pct,
        "sample": float(sample),
        "league_sample": float(league_slot_n),
        "defense_slot_avg": defense_slot_avg,
        "league_slot_avg": league_slot,
        "absolute_edge_pct": absolute_index - 1.0,
        "family_edge_pct": family_index - 1.0,
        "slot_outlier_pct": outlier_index - 1.0,
        "sample_weight": sample_weight,
    }


def slot_matchup_profile(nfl_builder: Any, season: int, projection_week: int, opponent: str, slot: str, market: str) -> dict[str, float]:
    """Public helper used by diagnostics/tests and the projection wrapper."""
    season = int(season)
    projection_week = int(projection_week)
    normalized_opponent = nfl_builder._normalize_team(opponent)
    normalized_slot = _slot(slot)
    key = (season, projection_week, normalized_opponent, normalized_slot, str(market))
    if key in _PROFILE_CACHE:
        return dict(_PROFILE_CACHE[key])
    history = _slot_history(nfl_builder, season, max(0, projection_week - 1))
    profile = _profile_from_history(history, normalized_opponent, normalized_slot, str(market))
    _PROFILE_CACHE[key] = dict(profile)
    return profile


def _weighted_profile_share(profile: dict[str, Any], weights: tuple[tuple[str, float], ...]) -> float:
    values: list[tuple[float, float]] = []
    for key, weight in weights:
        value = _num(profile.get(key, np.nan), np.nan)
        if math.isfinite(value) and value >= 0:
            values.append((value, weight))
    if not values:
        return math.nan
    total_weight = sum(weight for _, weight in values)
    return sum(value * weight for value, weight in values) / max(total_weight, 1e-9)


def _touchdown_usage_multiplier(profile: dict[str, Any], slot: str) -> dict[str, float]:
    """Scale only the matchup edge by how concentrated this player's red-zone role is."""
    slot = _slot(slot)
    target_share = _num(profile.get("target_share", np.nan), np.nan)
    carry_share = _num(profile.get("carry_share", np.nan), np.nan)

    receiving_rz_share = _weighted_profile_share(profile, (
        ("redzone_target_share", 0.45),
        ("inside_10_target_share", 0.30),
        ("endzone_target_share", 0.25),
    ))
    rushing_rz_share = _weighted_profile_share(profile, (
        ("goal_line_carry_share", 0.55),
        ("inside_10_carry_share", 0.45),
    ))

    receiving_ratio = (
        receiving_rz_share / target_share
        if math.isfinite(receiving_rz_share) and math.isfinite(target_share) and target_share > 0.015
        else 1.0
    )
    rushing_ratio = (
        rushing_rz_share / carry_share
        if math.isfinite(rushing_rz_share) and math.isfinite(carry_share) and carry_share > 0.015
        else 1.0
    )

    if slot.startswith("RB"):
        usage_ratio = 0.60 * rushing_ratio + 0.40 * receiving_ratio
    elif slot == "QB":
        usage_ratio = rushing_ratio
    else:
        usage_ratio = receiving_ratio

    usage_ratio = float(np.clip(usage_ratio, 0.35, 2.50))
    multiplier = float(np.clip(1.0 + 0.35 * (usage_ratio - 1.0), 0.75, 1.35))
    return {
        "usage_ratio": usage_ratio,
        "multiplier": multiplier,
        "receiving_ratio": float(np.clip(receiving_ratio, 0.20, 3.0)),
        "rushing_ratio": float(np.clip(rushing_ratio, 0.20, 3.0)),
    }


def _factor(profile: dict[str, float] | None) -> float:
    return 1.0 + _num((profile or {}).get("adjustment_pct"), 0.0)


def _append_reason(row: dict[str, Any], opponent: str, slot: str, market: str, profile: dict[str, float]) -> None:
    adjustment = _num(profile.get("adjustment_pct"), 0.0)
    if abs(adjustment) < 0.0005:
        return
    sample = int(_num(profile.get("sample"), 0.0))
    defense_avg = _num(profile.get("defense_slot_avg"), 0.0)
    league_avg = _num(profile.get("league_slot_avg"), 0.0)
    absolute = _num(profile.get("absolute_edge_pct"), 0.0)
    outlier = _num(profile.get("slot_outlier_pct"), 0.0)
    if market == "Anytime TD":
        team_edge = _num(profile.get("family_edge_pct"), 0.0)
        usage_ratio = _num(profile.get("usage_ratio"), 1.0)
        usage_multiplier = _num(profile.get("usage_multiplier"), 1.0)
        base_adjustment = _num(profile.get("base_adjustment_pct"), adjustment)
        note = (
            f"TD matchup {opponent} vs {slot}: {defense_avg:.2f} TD/g vs {league_avg:.2f} league "
            f"({absolute:+.0%}); general TD environment {team_edge:+.0%}; slot concentration {outlier:+.0%}; "
            f"{sample}g; red-zone role {usage_ratio:.2f}x (matchup amp {usage_multiplier:.2f}x); "
            f"base {base_adjustment:+.1%}, applied {adjustment:+.1%}"
        )
    else:
        note = (
            f"slot matchup {opponent} vs {slot} {market}: {defense_avg:.1f} vs {league_avg:.1f} league "
            f"({absolute:+.0%}); slot outlier {outlier:+.0%}; {sample}g; applied {adjustment:+.1%}"
        )
    current = str(row.get("Confluence", "") or "").strip()
    row["Confluence"] = f"{current} • {note}".strip(" •")


def _apply_slot_overlay(
    nfl_builder: Any,
    rows: list[dict[str, Any]],
    season: int,
    projection_week: int,
    opponent: str,
    slot: str,
    player_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    slot = _slot(slot)
    if slot not in TRACKED_SLOTS or not rows or projection_week <= 1:
        return rows

    profiles = {
        market: slot_matchup_profile(nfl_builder, season, projection_week, opponent, slot, market)
        for market in MARKET_STATS
        if _market_allowed_for_slot(market, slot)
    }
    td_profile = profiles.get("Anytime TD")
    if td_profile is not None:
        usage = _touchdown_usage_multiplier(player_profile or {}, slot)
        base_adjustment = _num(td_profile.get("adjustment_pct"), 0.0)
        final_adjustment = float(np.clip(
            base_adjustment * usage["multiplier"],
            -MARKET_CAP["Anytime TD"], MARKET_CAP["Anytime TD"],
        ))
        td_profile = dict(td_profile)
        td_profile["base_adjustment_pct"] = base_adjustment
        td_profile["adjustment_pct"] = final_adjustment
        td_profile["usage_ratio"] = usage["usage_ratio"]
        td_profile["usage_multiplier"] = usage["multiplier"]
        profiles["Anytime TD"] = td_profile

    factors = {market: _factor(profile) for market, profile in profiles.items()}

    pass_attempt_factor = factors.get("Passing Attempts", 1.0)
    rush_attempt_factor = factors.get("Rushing Attempts", 1.0)
    target_factor = factors.get("Targets", 1.0)
    reception_factor = factors.get("Receptions", target_factor)

    for row in rows:
        market = str(row.get("Market", "") or "")
        profile = profiles.get(market)
        market_factor = factors.get(market, 1.0)

        # Keep compound simulations internally consistent with the slot signal.
        if market in {"Passing Completions", "Passing Yards"}:
            row["Projected Player Attempts"] = round(max(0.0, _num(row.get("Projected Player Attempts")) * pass_attempt_factor), 2)
        if market == "Passing Completions":
            row["Projected Completions"] = round(max(0.0, _num(row.get("Projected Completions")) * market_factor), 2)
            if pass_attempt_factor > 0:
                row["Efficiency"] = round(max(0.0, _num(row.get("Efficiency")) * market_factor / pass_attempt_factor), 3)
        elif market == "Passing Yards" and pass_attempt_factor > 0:
            row["Efficiency"] = round(max(0.0, _num(row.get("Efficiency")) * market_factor / pass_attempt_factor), 3)

        if market == "Rushing Attempts":
            row["Projected Player Attempts"] = round(max(0.0, _num(row.get("Projected Player Attempts")) * market_factor), 2)
        elif market == "Rushing Yards":
            row["Projected Player Attempts"] = round(max(0.0, _num(row.get("Projected Player Attempts")) * rush_attempt_factor), 2)
            if rush_attempt_factor > 0:
                row["Efficiency"] = round(max(0.0, _num(row.get("Efficiency")) * market_factor / rush_attempt_factor), 3)

        if market == "Targets":
            row["Projected Targets"] = round(max(0.0, _num(row.get("Projected Targets")) * market_factor), 2)
            row["Targets Per Route"] = round(max(0.0, _num(row.get("Targets Per Route")) * market_factor), 3)
        elif market == "Receptions":
            row["Projected Targets"] = round(max(0.0, _num(row.get("Projected Targets")) * target_factor), 2)
            row["Projected Receptions"] = round(max(0.0, _num(row.get("Projected Receptions")) * market_factor), 2)
            if target_factor > 0:
                row["Efficiency"] = round(max(0.0, _num(row.get("Efficiency")) * market_factor / target_factor), 3)
        elif market == "Receiving Yards":
            row["Projected Targets"] = round(max(0.0, _num(row.get("Projected Targets")) * target_factor), 2)
            row["Projected Receptions"] = round(max(0.0, _num(row.get("Projected Receptions")) * reception_factor), 2)
            row["Targets Per Route"] = round(max(0.0, _num(row.get("Targets Per Route")) * target_factor), 3)
            if target_factor > 0:
                row["Efficiency"] = round(max(0.0, _num(row.get("Efficiency")) * market_factor / target_factor), 3)

        if profile is None or abs(market_factor - 1.0) < 0.0005:
            continue

        old_projection = max(0.0, _num(row.get("Projection"), 0.0))
        projection = max(0.0, old_projection * market_factor)
        # This is a first-class model matchup input, not a residual calibration.
        row["Raw Projection"] = round(projection, 2)
        row["Calibration Adjustment"] = 0.0
        row["Projection"] = round(projection, 2)
        row["Fair Line"] = nfl_builder._fair_line(projection, market)
        row["_sd"] = nfl_builder._prop_sd(market, projection, _num(row.get("Reliability"), 70.0))
        row["Matchup Index"] = round(max(0.01, _num(row.get("Matchup Index"), 1.0) * market_factor), 3)
        _append_reason(row, nfl_builder._normalize_team(opponent), slot, market, profile)

    return rows


def clear_slot_matchup_cache() -> None:
    _HISTORY_CACHE.clear()
    _PROFILE_CACHE.clear()


def install_slot_matchup_layer(nfl_builder: Any) -> None:
    """Install current-season slot-vs-defense learning, including TD distribution outliers."""
    if getattr(nfl_builder, "_SLOT_MATCHUP_LAYER_INSTALLED", False):
        nfl_builder.MODEL_VERSION = MODEL_VERSION
        return

    original = nfl_builder._project_player_markets

    def wrapped(*args, **kwargs):
        rows = original(*args, **kwargs)
        player = str(args[0] if len(args) > 0 else kwargs.get("player", "") or "")
        slot = str(args[2] if len(args) > 2 else kwargs.get("slot", "") or "")
        opponent = str(args[4] if len(args) > 4 else kwargs.get("opponent", "") or "")
        profiles_frame = args[7] if len(args) > 7 else kwargs.get("profiles", pd.DataFrame())
        team_rating = args[9] if len(args) > 9 else kwargs.get("team_rating", {})
        if not isinstance(team_rating, dict):
            team_rating = {}
        season = int(_num(team_rating.get("Season"), 0.0))
        projection_week = int(_num(team_rating.get("Projection Week"), 0.0))
        if season <= 0 or projection_week <= 0:
            return rows
        try:
            player_profile = nfl_builder._profile_lookup(profiles_frame, player)
        except Exception:
            player_profile = {}
        return _apply_slot_overlay(
            nfl_builder, rows, season, projection_week, opponent, slot, player_profile
        )

    nfl_builder._project_player_markets = wrapped
    nfl_builder.MODEL_VERSION = MODEL_VERSION
    nfl_builder._SLOT_MATCHUP_LAYER_INSTALLED = True
