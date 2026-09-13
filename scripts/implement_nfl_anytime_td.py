from __future__ import annotations

import ast
from pathlib import Path


PATH = Path("builders/nfl_builder.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def insert_before(text: str, marker: str, addition: str, label: str) -> str:
    if marker not in text:
        raise SystemExit(f"{label}: marker not found")
    return text.replace(marker, addition + marker, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"{label}: start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"{label}: end marker not found")
    return text[:start_at] + replacement + text[end_at:]


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'Version 4.4 makes sportsbook player-prop projections conditional on the player\nbeing active, fixes the receiving-yard compound distribution, and keeps generic\ndepth-chart priors from compressing established WR1/WR3 roles toward the middle.\n',
        'Version 4.5 adds a first-class Anytime TD market built from projected scoring\nenvironment, active-game workload, touchdown efficiency, and individual red-zone\nusage while preserving the active-player and receiving-role calibration fixes.\n',
        "module version description",
    )
    text = replace_once(
        text,
        'MODEL_VERSION = "nfl-v4.4-active-prop-role-calibration-2026-09-13"',
        'MODEL_VERSION = "nfl-v4.5-anytime-td-2026-09-13"',
        "model version",
    )

    text = insert_before(
        text,
        'def _column(df: pd.DataFrame, *names: str, default: Any = 0) -> pd.Series:\n',
        '''def _player_usage_key(player_id: Any, player_name: Any) -> str:\n    player_id_text = _safe_text(player_id)\n    if player_id_text and player_id_text.lower() not in ["nan", "none"]:\n        return player_id_text\n    return _normalize_name(player_name)\n\n\n''',
        "player usage key helper",
    )

    td_usage_helper = '''@st.cache_resource(ttl=21600, show_spinner=False)\ndef _season_touchdown_usage(season: int, through_week: int | None = None) -> pd.DataFrame:\n    """Aggregate player goal-line/red-zone scoring usage from compact nflverse PBP."""\n    columns = [\n        "player_usage_key", "team", "goal_line_carries", "inside_10_carries",\n        "redzone_targets", "inside_10_targets", "endzone_targets",\n        "goal_line_carry_share", "inside_10_carry_share", "redzone_target_share",\n        "inside_10_target_share", "endzone_target_share",\n    ]\n    pbp = _load_pbp_season(int(season))\n    if pbp is None or pbp.empty:\n        return pd.DataFrame(columns=columns)\n    frame = pbp.copy()\n    del pbp\n    if "season_type" in frame.columns:\n        frame = frame[frame["season_type"].astype(str).str.upper() == "REG"].copy()\n    if through_week is not None and "week" in frame.columns:\n        frame = frame[pd.to_numeric(frame["week"], errors="coerce") <= int(through_week)].copy()\n    if frame.empty:\n        return pd.DataFrame(columns=columns)\n\n    frame["_yard"] = pd.to_numeric(_column(frame, "yardline_100", default=np.nan), errors="coerce")\n    frame["_td"] = pd.to_numeric(_column(frame, "touchdown", default=0), errors="coerce").fillna(0).gt(0.5)\n    frame["_rush"] = pd.to_numeric(_column(frame, "rush_attempt", default=0), errors="coerce").fillna(0).gt(0.5)\n    frame["_pass"] = pd.to_numeric(_column(frame, "pass_attempt", default=0), errors="coerce").fillna(0).gt(0.5)\n    frame["_air"] = pd.to_numeric(_column(frame, "air_yards", default=np.nan), errors="coerce")\n    frame = frame[(frame["_yard"] <= 20) | frame["_td"]].copy()\n    if frame.empty:\n        return pd.DataFrame(columns=columns)\n\n    rush = frame[frame["_rush"]].copy()\n    rush["_name"] = _column(rush, "rusher_player_name", default="").astype(str)\n    rush["_id"] = _column(rush, "rusher_player_id", default="").astype(str)\n    rush["player_usage_key"] = [\n        _player_usage_key(player_id, player_name) for player_id, player_name in zip(rush["_id"], rush["_name"])\n    ]\n    rush["team"] = _column(rush, "posteam", default="").map(_normalize_team)\n    rush = rush[rush["player_usage_key"].astype(str).str.len().gt(0)].copy()\n    if rush.empty:\n        rush_summary = pd.DataFrame(columns=["player_usage_key", "team", "goal_line_carries", "inside_10_carries"])\n    else:\n        rush["goal_line_carry"] = (rush["_yard"] <= 5).astype(float)\n        rush["inside_10_carry"] = (rush["_yard"] <= 10).astype(float)\n        rush_summary = rush.groupby(["player_usage_key", "team"], as_index=False).agg(\n            goal_line_carries=("goal_line_carry", "sum"),\n            inside_10_carries=("inside_10_carry", "sum"),\n        )\n\n    receiving = frame[frame["_pass"]].copy()\n    receiving["_name"] = _column(receiving, "receiver_player_name", default="").astype(str)\n    receiving["_id"] = _column(receiving, "receiver_player_id", default="").astype(str)\n    receiving["player_usage_key"] = [\n        _player_usage_key(player_id, player_name) for player_id, player_name in zip(receiving["_id"], receiving["_name"])\n    ]\n    receiving["team"] = _column(receiving, "posteam", default="").map(_normalize_team)\n    receiving = receiving[receiving["player_usage_key"].astype(str).str.len().gt(0)].copy()\n    if receiving.empty:\n        receiving_summary = pd.DataFrame(columns=[\n            "player_usage_key", "team", "redzone_targets", "inside_10_targets", "endzone_targets",\n        ])\n    else:\n        receiving["redzone_target"] = (receiving["_yard"] <= 20).astype(float)\n        receiving["inside_10_target"] = (receiving["_yard"] <= 10).astype(float)\n        receiving["endzone_target"] = (\n            receiving["_air"].notna() & receiving["_yard"].notna() & (receiving["_air"] >= receiving["_yard"] - 0.5)\n        ).astype(float)\n        receiving_summary = receiving.groupby(["player_usage_key", "team"], as_index=False).agg(\n            redzone_targets=("redzone_target", "sum"),\n            inside_10_targets=("inside_10_target", "sum"),\n            endzone_targets=("endzone_target", "sum"),\n        )\n\n    usage = rush_summary.merge(receiving_summary, on=["player_usage_key", "team"], how="outer")\n    count_columns = ["goal_line_carries", "inside_10_carries", "redzone_targets", "inside_10_targets", "endzone_targets"]\n    for column in count_columns:\n        usage[column] = pd.to_numeric(usage.get(column, 0), errors="coerce").fillna(0.0)\n    share_map = {\n        "goal_line_carries": "goal_line_carry_share",\n        "inside_10_carries": "inside_10_carry_share",\n        "redzone_targets": "redzone_target_share",\n        "inside_10_targets": "inside_10_target_share",\n        "endzone_targets": "endzone_target_share",\n    }\n    for source, destination in share_map.items():\n        team_total = usage.groupby("team")[source].transform("sum")\n        usage[destination] = usage[source] / team_total.replace(0, np.nan)\n    return usage[columns].replace([np.inf, -np.inf], np.nan)\n\n\n'''
    text = insert_before(
        text,
        '@st.cache_resource(ttl=21600, show_spinner=False)\ndef _load_player_stats_season(season: int) -> pd.DataFrame:\n',
        td_usage_helper,
        "touchdown usage loader",
    )

    text = replace_once(
        text,
        '    df["player_name"] = _player_name_column(df).astype(str)\n    df["player_name_norm"] = df["player_name"].map(_normalize_name)\n    df["team"] = _player_team_column(df)\n',
        '    df["player_name"] = _player_name_column(df).astype(str)\n    df["player_name_norm"] = df["player_name"].map(_normalize_name)\n    df["player_usage_key"] = [_player_usage_key(player_id, player_name) for player_id, player_name in zip(_player_id_column(df), df["player_name"])]\n    df["team"] = _player_team_column(df)\n',
        "season profile player usage key",
    )
    text = replace_once(
        text,
        '        player_name=("player_name", "last"), team=("team", "last"), position=("position", "last"),\n',
        '        player_name=("player_name", "last"), player_usage_key=("player_usage_key", "last"), team=("team", "last"), position=("position", "last"),\n',
        "profile group usage key",
    )
    text = replace_once(
        text,
        '        ("rushing_yards", "rushing_yards_pg"), ("targets", "targets_pg"), ("receptions", "receptions_pg"),\n        ("receiving_yards", "receiving_yards_pg"), ("estimated_routes", "estimated_routes_pg"),\n',
        '        ("rushing_yards", "rushing_yards_pg"), ("rushing_tds", "rushing_tds_pg"),\n        ("targets", "targets_pg"), ("receptions", "receptions_pg"),\n        ("receiving_yards", "receiving_yards_pg"), ("receiving_tds", "receiving_tds_pg"),\n        ("estimated_routes", "estimated_routes_pg"),\n',
        "TD per-game profile metrics",
    )
    text = replace_once(
        text,
        '    grouped["rush_ypc"] = grouped["rushing_yards"] / grouped["carries"].replace(0, np.nan)\n    grouped["catch_rate"] = grouped["receptions"] / grouped["targets"].replace(0, np.nan)\n',
        '    grouped["rush_ypc"] = grouped["rushing_yards"] / grouped["carries"].replace(0, np.nan)\n    grouped["rush_td_rate"] = grouped["rushing_tds"] / grouped["carries"].replace(0, np.nan)\n    grouped["catch_rate"] = grouped["receptions"] / grouped["targets"].replace(0, np.nan)\n    grouped["receiving_td_rate"] = grouped["receiving_tds"] / grouped["targets"].replace(0, np.nan)\n',
        "TD efficiency profile metrics",
    )
    text = replace_once(
        text,
        '    grouped["target_share"] = grouped["targets_pg"] / grouped["team_targets"].replace(0, np.nan)\n\n    for stat_type, columns in [\n',
        '''    grouped["target_share"] = grouped["targets_pg"] / grouped["team_targets"].replace(0, np.nan)\n\n    touchdown_usage = _season_touchdown_usage(int(season), through_week)\n    td_count_columns = ["goal_line_carries", "inside_10_carries", "redzone_targets", "inside_10_targets", "endzone_targets"]\n    td_share_columns = ["goal_line_carry_share", "inside_10_carry_share", "redzone_target_share", "inside_10_target_share", "endzone_target_share"]\n    if touchdown_usage is not None and not touchdown_usage.empty:\n        grouped = grouped.merge(touchdown_usage, on=["player_usage_key", "team"], how="left")\n    for column in td_count_columns:\n        if column not in grouped.columns:\n            grouped[column] = 0.0\n        grouped[column] = pd.to_numeric(grouped[column], errors="coerce").fillna(0.0)\n    for column in td_share_columns:\n        if column not in grouped.columns:\n            grouped[column] = np.nan\n        grouped[column] = pd.to_numeric(grouped[column], errors="coerce")\n\n    for stat_type, columns in [\n''',
        "merge touchdown usage profiles",
    )

    text = replace_once(
        text,
        '        "carries_pg", "rushing_yards_pg", "targets_pg", "receptions_pg", "receiving_yards_pg",\n        "attempt_share", "carry_share", "target_share", "snap_share", "estimated_routes_pg",\n        "route_participation", "targets_per_route",\n',
        '        "carries_pg", "rushing_yards_pg", "rushing_tds_pg", "targets_pg", "receptions_pg",\n        "receiving_yards_pg", "receiving_tds_pg", "attempt_share", "carry_share", "target_share",\n        "snap_share", "estimated_routes_pg", "route_participation", "targets_per_route",\n        "goal_line_carry_share", "inside_10_carry_share", "redzone_target_share",\n        "inside_10_target_share", "endzone_target_share",\n',
        "blended TD role metrics",
    )
    text = replace_once(
        text,
        '        "completion_rate", "pass_ypa", "pass_td_rate", "interception_rate", "rush_ypc", "catch_rate",\n',
        '        "completion_rate", "pass_ypa", "pass_td_rate", "interception_rate", "rush_ypc", "rush_td_rate",\n        "catch_rate", "receiving_td_rate",\n',
        "blended TD efficiency metrics",
    )
    text = replace_once(
        text,
        '    total_metrics = ["attempts", "completions", "passing_yards", "passing_tds", "interceptions", "carries", "rushing_yards", "targets", "receptions", "receiving_yards", "chart_targets", "man_targets", "zone_targets"]\n',
        '    total_metrics = ["attempts", "completions", "passing_yards", "passing_tds", "interceptions", "carries", "rushing_yards", "rushing_tds", "targets", "receptions", "receiving_yards", "receiving_tds", "goal_line_carries", "inside_10_carries", "redzone_targets", "inside_10_targets", "endzone_targets", "chart_targets", "man_targets", "zone_targets"]\n',
        "blended TD total metrics",
    )

    td_projection_helpers = '''TD_SLOT_LAMBDA_PRIORS = {\n    "QB": 0.14, "RB1": 0.50, "RB2": 0.21, "WR1": 0.40,\n    "WR2": 0.29, "WR3": 0.18, "TE": 0.30,\n}\nTD_RUSH_RATE_PRIORS = {"QB": 0.038, "RB": 0.036, "WR": 0.012, "TE": 0.006}\nTD_RECEIVING_RATE_PRIORS = {"QB": 0.005, "RB": 0.048, "WR": 0.066, "TE": 0.078}\n\n\ndef _team_touchdown_context(\n    profiles: pd.DataFrame, team: str, team_rating: dict[str, Any],\n    opponent_rating: dict[str, Any], pregame_team_total: float,\n) -> dict[str, float]:\n    implied_points = clamp(_num(pregame_team_total, 22.5), 6.0, 48.0)\n    offensive_rz = _num(team_rating.get("Red Zone TD Rate", 0.56), 0.56)\n    defensive_edge = _num(opponent_rating.get("Red Zone Def Edge", 0.0), 0.0)\n    rz_factor = clamp(1.0 + 0.70 * (offensive_rz - 0.56) - 0.55 * defensive_edge, 0.86, 1.14)\n    expected_team_tds = clamp((implied_points / 8.75) * rz_factor, 0.55, 5.0)\n\n    historical_team_tds = np.nan\n    if profiles is not None and not profiles.empty:\n        subset = profiles[profiles["team"].astype(str).map(_normalize_team) == _normalize_team(team)].copy()\n        if not subset.empty:\n            rushing = pd.to_numeric(subset.get("rushing_tds_pg", 0), errors="coerce").fillna(0.0)\n            receiving = pd.to_numeric(subset.get("receiving_tds_pg", 0), errors="coerce").fillna(0.0)\n            historical_team_tds = float((rushing + receiving).sum())\n    if not math.isfinite(_num(historical_team_tds, np.nan)) or historical_team_tds < 0.80:\n        historical_team_tds = 2.55\n    scoring_scale = clamp(expected_team_tds / max(historical_team_tds, 0.80), 0.72, 1.35)\n    return {\n        "expected_team_tds": float(expected_team_tds),\n        "historical_team_tds": float(historical_team_tds),\n        "scoring_scale": float(scoring_scale),\n    }\n\n\ndef _anytime_touchdown_lambda(\n    profile: dict[str, Any], profiles: pd.DataFrame, team: str, position: str, slot: str,\n    projected_carries: float, projected_targets: float, role: dict[str, Any],\n    team_rating: dict[str, Any], opponent_rating: dict[str, Any], pregame_team_total: float,\n) -> tuple[float, str, float]:\n    pos = _position_group(position)\n    carries = max(0.0, _num(projected_carries, 0.0))\n    targets = max(0.0, _num(projected_targets, 0.0))\n    games = max(0.0, _num(profile.get("games", 0.0), 0.0))\n\n    rush_prior = TD_RUSH_RATE_PRIORS.get(pos, 0.012)\n    receiving_prior = TD_RECEIVING_RATE_PRIORS.get(pos, 0.050)\n    rush_rate = _regressed_rate(\n        _num(profile.get("rush_td_rate", rush_prior), rush_prior),\n        _num(profile.get("carries", 0.0), 0.0), rush_prior, 85.0,\n    )\n    receiving_rate = _regressed_rate(\n        _num(profile.get("receiving_td_rate", receiving_prior), receiving_prior),\n        _num(profile.get("targets", 0.0), 0.0), receiving_prior, 90.0,\n    )\n\n    carry_share = clamp(_num(role.get("carry_share", profile.get("carry_share", 0.0)), 0.0), 0.0, 0.95)\n    target_share = clamp(_num(role.get("target_share", profile.get("target_share", 0.0)), 0.0), 0.0, 0.45)\n    goal_line_share = _num(profile.get("goal_line_carry_share", np.nan), np.nan)\n    inside_10_carry_share = _num(profile.get("inside_10_carry_share", np.nan), np.nan)\n    redzone_target_share = _num(profile.get("redzone_target_share", np.nan), np.nan)\n    inside_10_target_share = _num(profile.get("inside_10_target_share", np.nan), np.nan)\n    endzone_target_share = _num(profile.get("endzone_target_share", np.nan), np.nan)\n\n    rushing_priority = np.nan\n    if math.isfinite(goal_line_share) or math.isfinite(inside_10_carry_share):\n        rushing_priority = (\n            0.68 * (goal_line_share if math.isfinite(goal_line_share) else inside_10_carry_share)\n            + 0.32 * (inside_10_carry_share if math.isfinite(inside_10_carry_share) else goal_line_share)\n        )\n    receiving_priority = np.nan\n    if math.isfinite(endzone_target_share) or math.isfinite(inside_10_target_share) or math.isfinite(redzone_target_share):\n        endzone_value = endzone_target_share if math.isfinite(endzone_target_share) else (inside_10_target_share if math.isfinite(inside_10_target_share) else redzone_target_share)\n        inside_value = inside_10_target_share if math.isfinite(inside_10_target_share) else (redzone_target_share if math.isfinite(redzone_target_share) else endzone_value)\n        receiving_priority = 0.62 * endzone_value + 0.38 * inside_value\n\n    rush_slot_factor = {"QB": 1.08, "RB1": 1.16, "RB2": 0.88}.get(slot, 0.88 if pos not in ["RB", "QB"] else 1.0)\n    receiving_slot_factor = {"RB1": 1.00, "RB2": 0.88, "WR1": 1.10, "WR2": 1.00, "WR3": 0.90, "TE": 1.08}.get(slot, 1.0)\n    if math.isfinite(rushing_priority) and carry_share > 0.01:\n        rush_role_factor = clamp(1.0 + 0.30 * (rushing_priority - carry_share) / max(carry_share, 0.10), 0.74, 1.36)\n    else:\n        rush_role_factor = rush_slot_factor\n    if math.isfinite(receiving_priority) and target_share > 0.01:\n        receiving_role_factor = clamp(1.0 + 0.28 * (receiving_priority - target_share) / max(target_share, 0.08), 0.76, 1.34)\n    else:\n        receiving_role_factor = receiving_slot_factor\n\n    workload_lambda = carries * rush_rate * rush_role_factor + targets * receiving_rate * receiving_role_factor\n    historical_td_pg = max(0.0, _num(profile.get("rushing_tds_pg", 0.0), 0.0) + _num(profile.get("receiving_tds_pg", 0.0), 0.0))\n    slot_prior = TD_SLOT_LAMBDA_PRIORS.get(slot, 0.20 if pos in ["WR", "TE"] else 0.12)\n    history_weight = clamp(games / (games + 8.0), 0.0, 0.78)\n    history_anchor = history_weight * historical_td_pg + (1.0 - history_weight) * slot_prior\n    base_lambda = 0.76 * workload_lambda + 0.24 * history_anchor\n\n    team_context = _team_touchdown_context(profiles, team, team_rating, opponent_rating, pregame_team_total)\n    td_lambda = clamp(base_lambda * team_context["scoring_scale"], 0.01, 1.45)\n    reason = (\n        f"Projected {team_context['expected_team_tds']:.2f} team TDs • active carry/target workload • "\n        "goal-line/end-zone role • regressed rushing/receiving TD efficiency"\n    )\n    return float(td_lambda), reason, float(team_context["expected_team_tds"])\n\n\n'''
    text = insert_before(
        text,
        'def _pregame_implied_team_total(\n',
        td_projection_helpers,
        "TD projection helpers",
    )

    text = replace_once(
        text,
        '    base = {"Passing Attempts": 5.2, "Passing Completions": 4.2, "Passing Yards": 55.0, "Passing TDs": 1.0, "Interceptions": 0.68, "Rushing Attempts": 4.0, "Rushing Yards": 23.0, "Targets": 2.5, "Receptions": 1.9, "Receiving Yards": 25.0}.get(market, max(1.0, projection * 0.32))\n',
        '    base = {"Passing Attempts": 5.2, "Passing Completions": 4.2, "Passing Yards": 55.0, "Passing TDs": 1.0, "Interceptions": 0.68, "Rushing Attempts": 4.0, "Rushing Yards": 23.0, "Targets": 2.5, "Receptions": 1.9, "Receiving Yards": 25.0, "Anytime TD": 0.65}.get(market, max(1.0, projection * 0.32))\n',
        "Anytime TD prop standard deviation",
    )
    text = replace_once(
        text,
        'def _fair_line(projection: float, market: str) -> float:\n    return round(math.floor(projection) + 0.5, 1) if market in ["Passing TDs", "Interceptions", "Receptions", "Targets", "Passing Attempts", "Passing Completions", "Rushing Attempts"] else round(round(projection * 2) / 2, 1)\n',
        'def _fair_line(projection: float, market: str) -> float:\n    if market == "Anytime TD":\n        return 0.5\n    return round(math.floor(projection) + 0.5, 1) if market in ["Passing TDs", "Interceptions", "Receptions", "Targets", "Passing Attempts", "Passing Completions", "Rushing Attempts"] else round(round(projection * 2) / 2, 1)\n',
        "Anytime TD fair line",
    )
    text = replace_once(
        text,
        '    caps = {"Passing Attempts": 2.2, "Passing Completions": 1.8, "Passing Yards": 18.0, "Passing TDs": 0.25, "Interceptions": 0.20, "Rushing Attempts": 2.0, "Rushing Yards": 10.0, "Targets": 1.3, "Receptions": 0.85, "Receiving Yards": 10.0}\n',
        '    caps = {"Passing Attempts": 2.2, "Passing Completions": 1.8, "Passing Yards": 18.0, "Passing TDs": 0.25, "Interceptions": 0.20, "Rushing Attempts": 2.0, "Rushing Yards": 10.0, "Targets": 1.3, "Receptions": 0.85, "Receiving Yards": 10.0, "Anytime TD": 0.15}\n',
        "Anytime TD calibration cap",
    )
    text = replace_once(
        text,
        '    elif market in ["Passing TDs", "Interceptions"]:\n        dispersion = 4.2 if market == "Passing TDs" else 2.8\n',
        '    elif market == "Anytime TD":\n        samples = rng.poisson(projection, size=draws).astype(float)\n        distribution = "Poisson anytime-touchdown count"\n    elif market in ["Passing TDs", "Interceptions"]:\n        dispersion = 4.2 if market == "Passing TDs" else 2.8\n',
        "Anytime TD distribution",
    )

    qb_rush_line = '        add_market("Rushing Yards", rush_attempts * qb_rush_ypc, rush_index, attempts=rush_attempts, efficiency=qb_rush_ypc, reason="Rush attempts × adjusted YPC • pressure • run defense")\n'
    text = replace_once(
        text,
        qb_rush_line,
        qb_rush_line + '''        td_lambda, td_reason, expected_team_tds = _anytime_touchdown_lambda(\n            profile, profiles, team, pos, slot, rush_attempts, 0.0, role,\n            team_rating, opponent_rating, pregame_team_total,\n        )\n        add_market(\n            "Anytime TD", td_lambda, expected_team_tds / 2.55, attempts=rush_attempts,\n            efficiency=td_lambda / max(rush_attempts, 0.25), reason=td_reason,\n        )\n''',
        "QB Anytime TD projection",
    )
    rb_receiving_line = '        add_market("Receiving Yards", receiving_yards, rec_index, targets=targets, receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr, efficiency=adjusted_ypt, coverage_matchup=short_index, reason="Targets × adjusted YPT • checkdown/short coverage matchup")\n'
    text = replace_once(
        text,
        rb_receiving_line,
        rb_receiving_line + '''        td_lambda, td_reason, expected_team_tds = _anytime_touchdown_lambda(\n            profile, profiles, team, pos, slot, carries, targets, role,\n            team_rating, opponent_rating, pregame_team_total,\n        )\n        add_market(\n            "Anytime TD", td_lambda, expected_team_tds / 2.55, attempts=carries, targets=targets,\n            receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr,\n            efficiency=td_lambda / max(carries + targets, 0.25), reason=td_reason,\n        )\n''',
        "RB Anytime TD projection",
    )
    receiver_line = '        add_market("Receiving Yards", receiving_yards, rec_index, targets=targets, receptions=receptions, routes=routes, route_participation=route_part, tprr=tprr, efficiency=adjusted_ypt, coverage_matchup=coverage_factor, reason="Targets × adjusted YPT • depth/YAC • coverage shell • pressure/weather")\n'
    text = replace_once(
        text,
        receiver_line,
        receiver_line + '''        td_lambda, td_reason, expected_team_tds = _anytime_touchdown_lambda(\n            profile, profiles, team, pos, slot, 0.0, targets, role,\n            team_rating, opponent_rating, pregame_team_total,\n        )\n        add_market(\n            "Anytime TD", td_lambda, expected_team_tds / 2.55, targets=targets, receptions=receptions,\n            routes=routes, route_participation=route_part, tprr=tprr,\n            efficiency=td_lambda / max(targets, 0.25), reason=td_reason,\n        )\n''',
        "WR/TE Anytime TD projection",
    )

    td_grade_helper = '''def _grade_anytime_td(\n    probability: float, probability_edge_value: float, expected_value: float,\n    reliability: float, role_confidence: float,\n) -> str:\n    # TD props are high variance, so initial A/B gates are deliberately stricter than yardage props.\n    if probability >= 0.25 and probability_edge_value >= 0.075 and expected_value >= 0.12 and reliability >= 78 and role_confidence >= 76:\n        return "A Prop"\n    if probability >= 0.20 and probability_edge_value >= 0.050 and expected_value >= 0.08 and reliability >= 70 and role_confidence >= 68:\n        return "B Prop"\n    if probability_edge_value >= 0.030 and expected_value > 0 and reliability >= 62:\n        return "Lean"\n    return "Non-Edge Prop"\n\n\n'''
    text = insert_before(
        text,
        'def _evaluate_prop_rows(rows: pd.DataFrame) -> pd.DataFrame:\n',
        td_grade_helper,
        "Anytime TD grade helper",
    )

    evaluator_marker = '        item["_sd"] = float(np.std(samples, ddof=1))\n        line = _num(item.get("Market Line", np.nan), np.nan)\n'
    evaluator_replacement = '''        item["_sd"] = float(np.std(samples, ddof=1))\n        market = _safe_text(item.get("Market", ""))\n        if market == "Anytime TD":\n            td_probability = float(1.0 - math.exp(-projection))\n            odds_raw = _num(item.get("Over Odds", np.nan), np.nan)\n            item["Market Line"] = 0.5\n            item["Fair Line"] = 0.5\n            if not (math.isfinite(odds_raw) and abs(odds_raw) >= 100):\n                item.update({\n                    "Pick": "Enter odds", "Pick Odds": np.nan, "Model Probability": round(td_probability, 4),\n                    "Push Probability": 0.0, "Implied Probability": np.nan, "Probability Edge": np.nan,\n                    "Projection Edge": np.nan, "Expected Value": np.nan, "Grade": "Missing odds", "Track": False,\n                })\n                output.append(item)\n                continue\n            odds = int(round(odds_raw))\n            implied = american_implied_probability(odds)\n            probability_edge_value = td_probability - implied\n            settled_ev = expected_value_per_unit(td_probability, odds)\n            grade = _grade_anytime_td(\n                td_probability, probability_edge_value, settled_ev,\n                _num(item.get("Reliability", 50), 50), _num(item.get("Role Confidence", 50), 50),\n            )\n            if probability_edge_value <= 0 or settled_ev <= 0:\n                grade = "Non-Edge Prop"\n            if _num(item.get("_play_probability", 1.0), 1.0) < MIN_GRADED_PROP_PLAY_PROBABILITY:\n                grade = "Injury hold"\n            item.update({\n                "Line Source": "Manual anytime TD price", "Pick": "Anytime TD", "Pick Odds": odds,\n                "Model Probability": round(td_probability, 4), "Push Probability": 0.0,\n                "Implied Probability": round(implied, 4), "Probability Edge": round(probability_edge_value, 4),\n                "Projection Edge": round(probability_edge_value, 4), "Expected Value": round(settled_ev, 4),\n                "Grade": grade, "Track": grade in ["A Prop", "B Prop"],\n            })\n            output.append(item)\n            continue\n\n        line = _num(item.get("Market Line", np.nan), np.nan)\n'''
    text = replace_once(text, evaluator_marker, evaluator_replacement, "Anytime TD evaluator")

    actual_old = '''    values = {\n        "Passing Attempts": attempts,\n        "Passing Completions": _num(stat_row.get("completions", 0), 0),\n        "Passing Yards": _num(stat_row.get("passing_yards", 0), 0),\n        "Passing TDs": _num(stat_row.get("passing_tds", 0), 0),\n        "Interceptions": _num(stat_row.get("interceptions", 0), 0),\n        "Rushing Attempts": carries,\n        "Rushing Yards": _num(stat_row.get("rushing_yards", 0), 0),\n        "Targets": targets,\n        "Receptions": receptions,\n        "Receiving Yards": _num(stat_row.get("receiving_yards", 0), 0),\n    }\n'''
    actual_new = '''    values = {\n        "Passing Attempts": attempts,\n        "Passing Completions": _num(stat_row.get("completions", 0), 0),\n        "Passing Yards": _num(stat_row.get("passing_yards", 0), 0),\n        "Passing TDs": _num(stat_row.get("passing_tds", 0), 0),\n        "Interceptions": _num(stat_row.get("interceptions", 0), 0),\n        "Rushing Attempts": carries,\n        "Rushing Yards": _num(stat_row.get("rushing_yards", 0), 0),\n        "Targets": targets,\n        "Receptions": receptions,\n        "Receiving Yards": _num(stat_row.get("receiving_yards", 0), 0),\n        "Anytime TD": _num(stat_row.get("rushing_tds", 0), 0) + _num(stat_row.get("receiving_tds", 0), 0),\n    }\n'''
    text = replace_once(text, actual_old, actual_new, "Anytime TD actual value")
    text = replace_once(
        text,
        '    if market in ["Passing Attempts", "Passing Completions", "Passing Yards", "Passing TDs", "Interceptions"]:\n',
        '    if market == "Anytime TD":\n        opportunity = carries + targets\n        efficiency = actual / opportunity if opportunity > 0 else 0.0\n    elif market in ["Passing Attempts", "Passing Completions", "Passing Yards", "Passing TDs", "Interceptions"]:\n',
        "Anytime TD actual opportunity",
    )
    text = replace_once(
        text,
        'def _projected_opportunity_for_market(row: dict[str, Any], market: str) -> float:\n    if market in ["Passing Attempts", "Passing Completions", "Passing Yards", "Passing TDs", "Interceptions", "Rushing Attempts", "Rushing Yards"]:\n',
        'def _projected_opportunity_for_market(row: dict[str, Any], market: str) -> float:\n    if market == "Anytime TD":\n        return _num(row.get("Projected Player Attempts", 0), 0) + _num(row.get("Projected Targets", 0), 0)\n    if market in ["Passing Attempts", "Passing Completions", "Passing Yards", "Passing TDs", "Interceptions", "Rushing Attempts", "Rushing Yards"]:\n',
        "Anytime TD projected opportunity",
    )
    text = replace_once(
        text,
        'def _bet_result_from_actual(pick: str, line: float, actual: float) -> str:\n    if abs(actual - line) < 1e-9:\n',
        'def _bet_result_from_actual(pick: str, line: float, actual: float) -> str:\n    if _safe_text(pick).lower().startswith("anytime td"):\n        return "Win" if actual >= 1.0 else "Loss"\n    if abs(actual - line) < 1e-9:\n',
        "Anytime TD result grading",
    )
    text = replace_once(
        text,
        '            elif market.startswith("Rushing"):\n                tracker.at[index, "Actual Attempts"] = _num(stat_row.get("carries", 0), 0)\n',
        '            elif market.startswith("Rushing") or market == "Anytime TD":\n                tracker.at[index, "Actual Attempts"] = _num(stat_row.get("carries", 0), 0)\n',
        "Anytime TD tracker carries",
    )

    manual_td_ui = '''    st.markdown("### Manual player prop lines")\n    st.caption("Manual entry only. Player projections are conditional on being active; availability below 90% is held out of A/B grades. No Odds API values are loaded.")\n\n    prop_base = _build_game_prop_rows(\n        away_team, home_team, away_lineup, home_lineup, profiles, defense_profiles,\n        away_rating, home_rating, projection, weather_total_adjustment,\n        market_total=market_total if total_market_ready else None,\n        home_spread=home_spread if spread_market_ready else None,\n    )\n    evaluated_props = pd.DataFrame()\n    if prop_base.empty:\n        st.info("No skill-position players were resolved. Open Lineups, injuries and role overrides to correct the QB/RB/WR/TE card.")\n    else:\n        wager_mask = (\n            ((prop_base["Position"].astype(str) == "QB") & (prop_base["Market"].astype(str) == "Passing Yards"))\n            | ((prop_base["Position"].astype(str) == "RB") & (prop_base["Market"].astype(str).isin(["Rushing Yards", "Receiving Yards"])))\n            | ((prop_base["Position"].astype(str) == "WR") & (prop_base["Market"].astype(str) == "Receiving Yards"))\n        )\n        prop_inputs = prop_base.copy()\n        prop_inputs["Market Line"] = np.nan\n        prop_inputs["Over Odds"] = np.nan\n        prop_inputs["Under Odds"] = np.nan\n        prop_inputs["Line Source"] = ""\n\n        yard_inputs = prop_inputs.loc[wager_mask].copy()\n        slot_order = {"QB": 0, "RB1": 1, "RB2": 2, "WR1": 3, "WR2": 4, "WR3": 5, "TE": 6}\n        market_order = {"Passing Yards": 0, "Rushing Yards": 1, "Receiving Yards": 2, "Anytime TD": 3}\n        team_order = {away_team: 0, home_team: 1}\n        yard_inputs["_team_order"] = yard_inputs["Team"].map(team_order).fillna(99)\n        yard_inputs["_slot_order"] = yard_inputs["Slot"].map(slot_order).fillna(99)\n        yard_inputs["_market_order"] = yard_inputs["Market"].map(market_order).fillna(99)\n        yard_inputs = yard_inputs.sort_values(["_team_order", "_slot_order", "_market_order", "Player"])\n\n        yard_player_keys = {\n            (_normalize_team(row.get("Team", "")), _normalize_name(row.get("Player", "")))\n            for _, row in yard_inputs.iterrows()\n        }\n\n        for team in [away_team, home_team]:\n            team_rows = yard_inputs[yard_inputs["Team"].astype(str) == str(team)]\n            if team_rows.empty:\n                continue\n            st.markdown(f"#### {team} player props")\n            for idx, row in team_rows.iterrows():\n                player = _safe_text(row.get("Player", ""))\n                slot = _safe_text(row.get("Slot", ""))\n                market = _safe_text(row.get("Market", ""))\n                projection_value = _num(row.get("Projection", 0), 0)\n                st.markdown(f"**{slot} — {player} · {market}**")\n                st.caption(f"EZPZ projection: {projection_value:.1f}")\n                line_col, over_col, under_col = st.columns(3)\n                line_value = line_col.number_input(\n                    "Line", value=None, step=0.5,\n                    key=f"nfl_prop_line_{market_key}_{idx}",\n                )\n                over_value = over_col.number_input(\n                    "Over odds", value=None, step=5,\n                    key=f"nfl_prop_over_{market_key}_{idx}",\n                )\n                under_value = under_col.number_input(\n                    "Under odds", value=None, step=5,\n                    key=f"nfl_prop_under_{market_key}_{idx}",\n                )\n                if line_value is not None:\n                    prop_inputs.at[idx, "Market Line"] = float(line_value)\n                if over_value is not None:\n                    prop_inputs.at[idx, "Over Odds"] = int(over_value)\n                if under_value is not None:\n                    prop_inputs.at[idx, "Under Odds"] = int(under_value)\n                if line_value is not None and over_value is not None and under_value is not None:\n                    prop_inputs.at[idx, "Line Source"] = "Manual market line"\n\n        td_inputs = prop_inputs[\n            (prop_inputs["Market"].astype(str) == "Anytime TD")\n            & prop_inputs.apply(\n                lambda row: (_normalize_team(row.get("Team", "")), _normalize_name(row.get("Player", ""))) in yard_player_keys,\n                axis=1,\n            )\n        ].copy()\n        td_inputs["_team_order"] = td_inputs["Team"].map(team_order).fillna(99)\n        td_inputs["_slot_order"] = td_inputs["Slot"].map(slot_order).fillna(99)\n        td_inputs = td_inputs.sort_values(["_team_order", "_slot_order", "Player"])\n\n        if not td_inputs.empty:\n            st.markdown("#### Anytime TD odds")\n            st.caption("Same players as the yardage section above. Enter one American-odds price per player; no second price is required.")\n            for team in [away_team, home_team]:\n                team_td_rows = td_inputs[td_inputs["Team"].astype(str) == str(team)]\n                if team_td_rows.empty:\n                    continue\n                st.markdown(f"**{team}**")\n                for idx, row in team_td_rows.iterrows():\n                    player = _safe_text(row.get("Player", ""))\n                    slot = _safe_text(row.get("Slot", ""))\n                    td_lambda = max(0.0, _num(row.get("Projection", 0), 0))\n                    td_probability = 1.0 - math.exp(-td_lambda)\n                    st.markdown(f"**{slot} — {player}**")\n                    st.caption(f"EZPZ anytime TD probability: {td_probability:.1%} • expected TDs λ={td_lambda:.2f}")\n                    odds_value = st.number_input(\n                        "Anytime TD odds", value=None, step=5,\n                        key=f"nfl_anytime_td_odds_{market_key}_{idx}",\n                    )\n                    if odds_value is not None:\n                        prop_inputs.at[idx, "Market Line"] = 0.5\n                        prop_inputs.at[idx, "Over Odds"] = int(odds_value)\n                        prop_inputs.at[idx, "Line Source"] = "Manual anytime TD price"\n\n        evaluated_props = _evaluate_prop_rows(prop_inputs)\n        if not evaluated_props.empty:\n            st.markdown("#### Prop grades")\n            evaluated_yard_mask = (\n                ((evaluated_props["Position"].astype(str) == "QB") & (evaluated_props["Market"].astype(str) == "Passing Yards"))\n                | ((evaluated_props["Position"].astype(str) == "RB") & (evaluated_props["Market"].astype(str).isin(["Rushing Yards", "Receiving Yards"])))\n                | ((evaluated_props["Position"].astype(str) == "WR") & (evaluated_props["Market"].astype(str) == "Receiving Yards"))\n            )\n            evaluated_td_mask = (\n                (evaluated_props["Market"].astype(str) == "Anytime TD")\n                & evaluated_props.apply(\n                    lambda row: (_normalize_team(row.get("Team", "")), _normalize_name(row.get("Player", ""))) in yard_player_keys,\n                    axis=1,\n                )\n            )\n            evaluated_wagers = evaluated_props.loc[evaluated_yard_mask | evaluated_td_mask].copy()\n            evaluated_wagers["_team_order"] = evaluated_wagers["Team"].map(team_order).fillna(99)\n            evaluated_wagers["_slot_order"] = evaluated_wagers["Slot"].map(slot_order).fillna(99)\n            evaluated_wagers["_market_order"] = evaluated_wagers["Market"].map(market_order).fillna(99)\n            evaluated_wagers = evaluated_wagers.sort_values(["_team_order", "_slot_order", "_market_order", "Player"])\n            for _, row in evaluated_wagers.iterrows():\n                grade = _safe_text(row.get("Grade", ""))\n                player = _safe_text(row.get("Player", ""))\n                slot = _safe_text(row.get("Slot", ""))\n                market = _safe_text(row.get("Market", ""))\n                projection_value = _num(row.get("Projection", 0), 0)\n                probability = _num(row.get("Model Probability", np.nan), np.nan)\n                if grade in ["No market line", "Missing odds"]:\n                    if market == "Anytime TD" and math.isfinite(probability):\n                        st.caption(f"{slot} — {player} · Anytime TD: model {probability:.1%} • {grade}")\n                    else:\n                        st.caption(f"{slot} — {player} · {market}: projection {projection_value:.1f} • {grade}")\n                    continue\n                pick = _safe_text(row.get("Pick", ""))\n                pick_odds = _int(row.get("Pick Odds", 0), 0)\n                edge = _num(row.get("Probability Edge", 0), 0)\n                st.markdown(f"**{slot} — {player} · {market}: {pick} ({pick_odds:+d}) — {grade}**")\n                if market == "Anytime TD":\n                    st.caption(f"Expected TDs λ={projection_value:.2f} • model {probability:.1%} • price edge {edge:+.1%}")\n                else:\n                    st.caption(f"Projection {projection_value:.1f} • model {probability:.1%} • price edge {edge:+.1%}")\n\n'''
    text = replace_between(
        text,
        '    st.markdown("### Manual player prop lines")\n',
        '    st.divider()\n    st.caption("This single action saves the game, lineup snapshot and every prop projection.',
        manual_td_ui,
        "manual player props and Anytime TD UI",
    )

    text = text.replace(
        'st.caption("NFL v4.2 regression slate • manual sportsbook entry • regression QB/RB/WR yard props")',
        'st.caption("NFL v4.5 regression slate • manual sportsbook entry • yardage + Anytime TD props")',
        1,
    )

    required_markers = [
        'MODEL_VERSION = "nfl-v4.5-anytime-td-2026-09-13"',
        'def _season_touchdown_usage(',
        'def _anytime_touchdown_lambda(',
        '"Anytime TD", td_lambda',
        'def _grade_anytime_td(',
        'st.markdown("#### Anytime TD odds")',
        'key=f"nfl_anytime_td_odds_{market_key}_{idx}"',
        '"Anytime TD": _num(stat_row.get("rushing_tds", 0), 0) + _num(stat_row.get("receiving_tds", 0), 0)',
    ]
    missing = [marker for marker in required_markers if marker not in text]
    if missing:
        raise SystemExit(f"validation markers missing: {missing}")
    ast.parse(text)
    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
