"""Validate public NBA possession/box-score history before fitting any model.

The input schema follows the NBA Stats exports published by SportsDataverse.
No network or database access happens in this module.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math

import numpy as np
import pandas as pd

from builders.nba_model import InputError, timestamp

OFF = [f"off_player_{i}" for i in range(1, 6)]
DEF = [f"def_player_{i}" for i in range(1, 6)]
EASTERN = ZoneInfo("America/New_York")


@dataclass
class History:
    observations: pd.DataFrame
    games: pd.DataFrame
    box: pd.DataFrame
    audit: dict
    source: str
    team_observations: pd.DataFrame | None = None
    team_games: pd.DataFrame | None = None
    team_box: pd.DataFrame | None = None


def _columns(frame, columns, label):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise InputError(f"{label} is missing columns: {', '.join(sorted(missing))}")


def _ids(values, label, *, game=False):
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any() or (~np.isfinite(numeric)).any() or (numeric <= 0).any() or (numeric % 1 != 0).any():
        raise InputError(f"{label} must contain positive integer IDs.")
    result = numeric.astype("int64").astype(str)
    if game:
        if result.str.len().gt(10).any():
            raise InputError("NBA game IDs cannot exceed ten digits.")
        result = result.str.zfill(10)
    return result


def _box_minutes(value):
    if value is None or pd.isna(value) or str(value).strip() == "":
        return 0.0  # Historical NBA box-score DNP rows carry blank minutes.
    text = str(value).strip()
    try:
        if ":" in text:
            minutes, seconds = text.split(":")
            # Some NBA exports round 30:59.9 to "30:60". Preserve the total
            # seconds, allowing exactly 60 rather than truncating that minute.
            if not 0 <= float(seconds) <= 60:
                raise ValueError
            result = float(minutes) + float(seconds) / 60
        else:
            result = float(text)
    except (ValueError, TypeError):
        raise InputError(f"Invalid historical minutes value: {text!r}") from None
    if not math.isfinite(result) or result < 0:
        raise InputError("Historical minutes must be finite and nonnegative.")
    return result


def _availability(day):
    # The archive has game dates, not historical publication times. Use a
    # conservative, explicit next-day-noon ET availability policy, not tipoff.
    return (datetime.fromisoformat(str(day)[:10]).replace(tzinfo=EASTERN) +
            timedelta(days=1, hours=12)).astimezone(ZoneInfo("UTC"))


def prepare_history(possessions, schedule, box_scores, *, source: str,
                    plus_minus_tolerance: float = 3.0,
                    weight_column: str | None = None) -> History:
    if not source.strip():
        raise InputError("Historical data source is required.")
    if not math.isfinite(plus_minus_tolerance) or plus_minus_tolerance < 0:
        raise InputError("Plus/minus tolerance must be nonnegative.")
    _columns(possessions, ["game_id", "period", "possession_number", "offense_team_id",
             "defense_team_id", "points", "count_as_possession", *OFF, *DEF], "Possessions")
    _columns(schedule, ["game_id", "game_date", "home_team_id", "home_team_name", "home_pts",
             "away_team_id", "away_team_name", "away_pts"], "Schedule")
    _columns(box_scores, ["game_id", "team_id", "person_id", "minutes", "plus_minus_points",
             "first_name", "family_name"], "Player box scores")
    p, g, b = possessions.copy(), schedule.copy(), box_scores.copy()
    for frame in (p, g, b):
        frame["game_id"] = _ids(frame.game_id, "Game IDs", game=True)
    p, g, b = [frame.loc[frame.game_id.str.startswith("002")].copy() for frame in (p, g, b)]
    if g.game_id.duplicated().any():
        raise InputError("Schedule has duplicate regular-season game IDs.")
    if p.duplicated(["game_id", "period", "possession_number"]).any():
        raise InputError("Possession keys are duplicated; do not count a source twice.")
    if b.duplicated(["game_id", "team_id", "person_id"]).any():
        raise InputError("Player box scores contain duplicate player/game/team rows.")
    for frame, columns in ((p, ["offense_team_id", "defense_team_id", *OFF, *DEF]),
                           (g, ["home_team_id", "away_team_id"]), (b, ["team_id", "person_id"])):
        for column in columns:
            frame[column] = _ids(frame[column], column)
    for frame, columns in ((p, ["points", "period"]), (g, ["home_pts", "away_pts"]),
                           (b, ["plus_minus_points"])):
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if weight_column is None:
        flags = p.count_as_possession.map(lambda v: str(v).strip().lower())
        if not flags.isin(["true", "false", "1", "0"]).all():
            raise InputError("Possession counting flags must be booleans.")
        p["weight"] = flags.isin(["true", "1"]).astype(float)
    else:
        _columns(p, [weight_column], "Weighted lineup samples")
        p["weight"] = pd.to_numeric(p[weight_column], errors="coerce")
        if not np.isfinite(p.weight).all() or (p.weight < 0).any():
            raise InputError("Lineup exposure weights must be finite and nonnegative.")
    b["minutes_value"] = b.minutes.map(_box_minutes)
    b["player_name"] = b.first_name.fillna("").astype(str).str.strip() + " " + b.family_name.fillna("").astype(str).str.strip()
    b["player_name"] = b.player_name.str.strip()
    g["game_date"] = pd.to_datetime(g.game_date, errors="coerce").dt.date
    if g.game_date.isna().any():
        raise InputError("Schedule contains invalid dates.")
    g["available_at"] = pd.to_datetime(g.game_date.map(_availability), utc=True)
    g = g.set_index("game_id", drop=False)
    present = set(p.game_id)
    missing_schedule = present - set(g.index)
    if missing_schedule:
        raise InputError("Possession games are missing from the schedule; dates cannot be inferred from IDs.")
    g = g.loc[g.index.isin(present)].copy()
    reasons: dict[str, set[str]] = {game: set() for game in g.index}

    def reject(game_ids, reason):
        for game in set(game_ids) & present:
            reasons[game].add(reason)

    invalid_points = (~np.isfinite(p.points)) | (p.points < 0) | (p.points % 1 != 0)
    reject(p.loc[invalid_points, "game_id"], "invalid_points")
    reject(p.loc[(p.period < 1) | (p.period % 1 != 0) | ~np.isfinite(p.period), "game_id"], "invalid_period")
    duplicate_lineup = p[OFF + DEF].nunique(axis=1) != 10
    reject(p.loc[duplicate_lineup, "game_id"], "invalid_ten_player_lineup")
    metadata = p[["game_id", "offense_team_id", "defense_team_id"]].join(
        g[["home_team_id", "away_team_id"]], on="game_id")
    correct_teams = ((metadata.offense_team_id == metadata.home_team_id) & (metadata.defense_team_id == metadata.away_team_id)) | (
        (metadata.offense_team_id == metadata.away_team_id) & (metadata.defense_team_id == metadata.home_team_id))
    reject(metadata.loc[~correct_teams, "game_id"], "team_id_mismatch")
    totals = p.groupby(["game_id", "offense_team_id"]).points.sum()
    exact_scores = set()
    for game, row in g.iterrows():
        if all(math.isfinite(row[f"{side}_pts"]) and row[f"{side}_pts"] > 0 and
               totals.get((game, row[f"{side}_team_id"]), np.nan) == row[f"{side}_pts"] for side in ("home", "away")):
            exact_scores.add(game)
        else:
            reasons[game].add("score_mismatch")

    # Cross-check credited on-floor points against independently stored player
    # box-score plus/minus; a final score alone cannot validate lineup identity.
    credited = None
    exposure = None
    for columns, team_column, sign in ((OFF, "offense_team_id", 1), (DEF, "defense_team_id", -1)):
        for column in columns:
            points = p.groupby(["game_id", team_column, column]).points.sum()
            points.index.names = ["game_id", "team_id", "person_id"]
            counts = p.groupby(["game_id", team_column, column]).size()
            counts.index.names = points.index.names
            credited = sign * points if credited is None else credited.add(sign * points, fill_value=0)
            exposure = counts if exposure is None else exposure.add(counts, fill_value=0)
    expected = b.set_index(["game_id", "team_id", "person_id"])
    active = expected.loc[expected.minutes_value > 0]
    compare = credited.to_frame("credited").join(expected[["plus_minus_points", "minutes_value"]])
    reject(compare.loc[compare.minutes_value.isna() | (compare.minutes_value <= 0)].index.get_level_values("game_id"), "lineup_player_not_active_in_box")
    discrepancy = (compare.credited - compare.plus_minus_points).abs()
    reject(compare.loc[~np.isfinite(compare.plus_minus_points) | (discrepancy > plus_minus_tolerance)].index.get_level_values("game_id"), "player_plus_minus_mismatch")
    reject(active.loc[~active.index.isin(credited.index)].index.get_level_values("game_id"), "active_player_missing_from_lineups")
    maximum_discrepancy = discrepancy.groupby(level="game_id").max()
    periods = p.groupby("game_id").period.max()
    for game, row in g.iterrows():
        expected_minutes = 48 + 5 * max(0, int(periods[game]) - 4)
        for side in ("home", "away"):
            team_box = b.loc[(b.game_id == game) & (b.team_id == row[f"{side}_team_id"])]
            if team_box.empty or not math.isclose(team_box.minutes_value.sum(), expected_minutes * 5, abs_tol=0.25):
                reasons[game].add("box_minutes_incomplete")
        g.loc[game, "game_minutes"] = expected_minutes

    # Combine identical ten-player samples within a game. Non-counting free
    # throws stay in the points numerator without becoming extra possessions.
    keys = ["game_id", "offense_team_id", "defense_team_id", *OFF, *DEF]
    grouped = p.groupby(keys, sort=False, as_index=False).agg(points=("points", "sum"), possessions=("weight", "sum"))
    reject(grouped.loc[(grouped.possessions == 0) & (grouped.points != 0), "game_id"], "points_without_counted_possession")
    good_games = {game for game, issues in reasons.items() if not issues}
    if not good_games:
        raise InputError("No games passed the score, lineup and box-minute checks.")
    observations = grouped.loc[grouped.game_id.isin(good_games) & (grouped.possessions > 0)].copy()
    observations["offense_players"] = list(observations[OFF].itertuples(index=False, name=None))
    observations["defense_players"] = list(observations[DEF].itertuples(index=False, name=None))
    observations = observations.drop(columns=OFF + DEF).join(g[["game_date", "available_at", "home_team_id"]], on="game_id")
    observations["home_offense"] = np.where(observations.offense_team_id == observations.home_team_id, 1.0, -1.0)
    games = g.loc[g.index.isin(good_games)].copy()
    game_possessions = observations.groupby(["game_id", "offense_team_id"]).possessions.sum()
    games["possessions"] = [sum(game_possessions.loc[game]) / 2.0 for game in games.index]
    boxes = b.loc[b.game_id.isin(good_games)].join(g[["game_date", "available_at"]], on="game_id")
    exclusions = Counter(issue for issues in reasons.values() for issue in issues)
    audit = {"source_games": len(g), "games_with_exact_scores": len(exact_scores),
             "accepted_games": len(good_games), "excluded_games": len(g) - len(good_games),
             "exclusion_reasons": dict(exclusions), "raw_regular_season_rows": len(p),
             "accepted_observation_groups": len(observations),
             "accepted_possessions": float(observations.possessions.sum()),
             "plus_minus_tolerance": plus_minus_tolerance,
             "median_game_max_player_plus_minus_error": float(maximum_discrepancy.median()),
             "lineup_sources": p.lineup_source.value_counts().to_dict() if "lineup_source" in p else {},
             "box_minute_rollovers": int(b.minutes.fillna("").astype(str).str.endswith(":60").sum()),
             "availability_policy": "Game date + 1 day, noon America/New_York; inferred, not archived publication time",
             "excluded_game_reasons": {game: sorted(issues) for game, issues in reasons.items() if issues}}
    return History(observations.reset_index(drop=True), games.reset_index(drop=True), boxes.reset_index(drop=True), audit, source)


def prepare_event_history(events, event_lineups, possessions, schedule, box_scores,
                          *, source: str, minute_tolerance_seconds: float = 2.0) -> History:
    """Use event-level lineups, independent score/minute checks, and foul-time FT credit.

    The possession export's opening lineup misses substitutions within a trip.
    Each counted possession is split equally over its distinct event lineups;
    half the total offensive/defensive exposure is used for BOTH sides. This
    is a documented exposure approximation, not a claim of exact stint counts.
    """
    if not math.isfinite(minute_tolerance_seconds) or minute_tolerance_seconds < 0:
        raise InputError("Minute tolerance must be finite and nonnegative.")
    keys = ["game_id", "period", "action_number"]
    units = [f"{side}_player_{i}" for side in ("home", "away") for i in range(1, 6)]
    _columns(events, [*keys, "order_index", "seconds_remaining", "score_home", "score_away",
                     "is_foul", "is_free_throw", "possession_number"], "Play by play")
    _columns(event_lineups, [*keys, *units], "Event lineups")
    _columns(possessions, ["game_id", "period", "possession_number", "count_as_possession"], "Possessions")
    _columns(schedule, ["game_id"], "Schedule")
    _columns(box_scores, ["game_id", "person_id", "minutes"], "Player box scores")
    e, lineups, p, games, boxes = [frame.copy() for frame in (
        events, event_lineups, possessions, schedule, box_scores)]
    for frame in (e, lineups, p, games, boxes):
        frame["game_id"] = _ids(frame.game_id, "Game IDs", game=True)
    e, lineups, p, games, boxes = [frame.loc[frame.game_id.str.startswith("002")].copy()
                                  for frame in (e, lineups, p, games, boxes)]
    if games.game_id.duplicated().any():
        raise InputError("Schedule has duplicate regular-season game IDs.")
    if not set(e.game_id).issubset(set(games.game_id)):
        raise InputError("Play-by-play games are missing from the schedule.")
    if e.duplicated(["game_id", "order_index"]).any():
        raise InputError("Play-by-play event ordering keys are duplicated.")
    pos_keys = ["game_id", "period", "possession_number"]
    if p.duplicated(pos_keys).any():
        raise InputError("Possession keys are duplicated.")
    for column in ("is_foul", "is_free_throw"):
        if not e[column].map(lambda v: isinstance(v, (bool, np.bool_))).all():
            raise InputError("Play-by-play event flags must be booleans.")
    flags = p.count_as_possession.astype(str).str.strip().str.lower()
    if not flags.isin(["true", "false", "1", "0"]).all():
        raise InputError("Possession counting flags must be booleans.")
    p["weight"] = flags.isin(["true", "1"]).astype(float)
    # NBA action numbers can repeat (e.g. a turnover and its steal). Match
    # occurrences, rejecting a key if differing lineups make it ambiguous.
    ambiguous = lineups.groupby(keys)[units].nunique().max(axis=1) > 1
    e["_occurrence"] = e.groupby(keys, sort=False).cumcount()
    lineups["_occurrence"] = lineups.groupby(keys, sort=False).cumcount()
    e = e.merge(lineups[[*keys, "_occurrence", *units]], on=[*keys, "_occurrence"],
                how="left", validate="one_to_one", indicator=True)
    e = e.sort_values(["game_id", "order_index"], kind="stable").reset_index(drop=True)
    present = set(e.game_id)
    issues = {game: set() for game in present}

    def reject(game_ids, reason):
        for game in set(game_ids) & present:
            issues[game].add(reason)

    reject(ambiguous.loc[ambiguous].index.get_level_values("game_id"), "ambiguous_event_lineup")
    reject(e.loc[e._merge != "both", "game_id"], "missing_event_lineup")
    counts_e, counts_l = e.groupby(keys).size(), lineups.groupby(keys).size()
    counts_e, counts_l = counts_e.align(counts_l, join="outer", fill_value=0)
    unequal = counts_e.ne(counts_l)
    reject(unequal.loc[unequal].index.get_level_values("game_id"), "event_lineup_count_mismatch")
    raw_units = e[units].apply(pd.to_numeric, errors="coerce")
    invalid = (~np.isfinite(raw_units)).any(axis=1) | (raw_units <= 0).any(axis=1) | (raw_units % 1 != 0).any(axis=1)
    reject(e.loc[invalid | (raw_units.nunique(axis=1) != 10), "game_id"], "invalid_ten_player_lineup")
    e[units] = raw_units.fillna(0).astype("int64").astype(str)
    for column in ("period", "order_index", "seconds_remaining"):
        e[column] = pd.to_numeric(e[column], errors="coerce")
        reject(e.loc[~np.isfinite(e[column]), "game_id"], "invalid_event_clock")
    reject(e.loc[(e.period < 1) | (e.period % 1 != 0) | (e.order_index < 0) |
                 (e.order_index % 1 != 0), "game_id"], "invalid_event_order")
    limit = pd.Series(np.where(e.period <= 4, 720.0, 300.0), index=e.index)
    reject(e.loc[(e.seconds_remaining < 0) | (e.seconds_remaining > limit), "game_id"], "invalid_event_clock")
    previous_clock = e.groupby(["game_id", "period"]).seconds_remaining.shift(1).fillna(limit)
    e["elapsed"] = previous_clock - e.seconds_remaining
    reject(e.loc[e.elapsed < -0.001, "game_id"], "clock_backwards")
    endings = e.groupby(["game_id", "period"]).seconds_remaining.last()
    reject(endings.loc[endings != 0].index.get_level_values("game_id"), "period_incomplete")
    for side in ("home", "away"):
        raw = pd.to_numeric(e[f"score_{side}"].replace(r"^\s*$", np.nan, regex=True), errors="coerce")
        score = raw.groupby(e.game_id).ffill().fillna(0)
        e[f"{side}_points"] = score - score.groupby(e.game_id).shift(1).fillna(0)
    reject(e.loc[(e.home_points < 0) | (e.away_points < 0), "game_id"], "negative_score_change")
    reject(e.loc[(e.home_points > 3) | (e.away_points > 3) |
                 ((e.home_points > 0) & (e.away_points > 0)), "game_id"], "invalid_score_change")

    # The box plus/minus convention assigns an FT to the lineup at the foul,
    # even when someone substitutes between the foul and the individual FT.
    last_foul = pd.Series(e.index, index=e.index).where(e.is_foul).groupby([e.game_id, e.period]).ffill()
    owner = pd.Series(e.index, index=e.index)
    ft = e.index[e.is_free_throw & last_foul.notna()]
    foul = last_foul.loc[ft].astype(int)
    same_clock = np.isclose(e.loc[ft, "seconds_remaining"].to_numpy(),
                            e.loc[foul, "seconds_remaining"].to_numpy(), atol=0.001, rtol=0)
    owner.loc[ft[same_clock]] = foul.to_numpy()[same_clock]
    point_rows = e[["game_id", "period", "home_points", "away_points", *units]].copy()
    point_rows[units] = e.loc[owner.astype(int), units].to_numpy()
    boxes["person_id"] = _ids(boxes.person_id, "Player IDs")
    if boxes.duplicated(["game_id", "person_id"]).any():
        raise InputError("Player box scores contain duplicate player/game rows.")
    boxes["minutes_value"] = boxes.minutes.map(_box_minutes)
    clock_minutes = None
    for column in units:
        value = e.groupby(["game_id", column]).elapsed.sum() / 60.0
        value.index.names = ["game_id", "person_id"]
        clock_minutes = value if clock_minutes is None else clock_minutes.add(value, fill_value=0)
    expected = boxes.set_index(["game_id", "person_id"]).minutes_value
    minute_comparison = pd.concat([clock_minutes.rename("clock"), expected.rename("box")], axis=1).fillna(0)
    difference = (minute_comparison.clock - minute_comparison.box).abs()
    reject(difference.loc[difference > minute_tolerance_seconds / 60 + 0.0001].index.get_level_values("game_id"),
           "event_player_minutes_mismatch")

    # Equal sharing avoids assigning every point in a split FT trip to the
    # possession's opening five. Preserve the original whole-game count.
    unique_units = e.loc[e.possession_number.notna(), [*pos_keys, *units]].drop_duplicates()
    weights = unique_units.merge(p[[*pos_keys, "weight"]], on=pos_keys, how="left", validate="many_to_one")
    reject(weights.loc[weights.weight.isna(), "game_id"], "event_possession_missing")
    represented = unique_units[pos_keys].drop_duplicates().assign(represented=True)
    missing = p.merge(represented, on=pos_keys, how="left", validate="one_to_one")
    reject(missing.loc[(missing.weight > 0) & missing.represented.isna(), "game_id"], "counted_possession_missing_events")
    weights["exposure"] = weights.weight / weights.groupby(pos_keys).weight.transform("size") / 2.0
    grouping = ["game_id", "period", *units]
    exposure = weights.groupby(grouping, as_index=False).exposure.sum()
    points = point_rows.groupby(grouping, as_index=False)[["home_points", "away_points"]].sum()
    samples = points.merge(exposure, on=grouping, how="outer", validate="one_to_one").fillna(
        {"home_points": 0.0, "away_points": 0.0, "exposure": 0.0})
    samples = samples.merge(games[["game_id", "home_team_id", "away_team_id"]], on="game_id", validate="many_to_one")
    keep = {game for game, reasons in issues.items() if not reasons}
    samples = samples.loc[samples.game_id.isin(keep)].reset_index(drop=True)
    if samples.empty:
        raise InputError("No games passed event-lineup and clock checks.")
    rows = []
    for side, other, number in (("home", "away", 0), ("away", "home", 1)):
        frame = samples[["game_id", "period"]].copy()
        frame["possession_number"] = 2 * samples.index + number
        frame["offense_team_id"], frame["defense_team_id"] = samples[f"{side}_team_id"], samples[f"{other}_team_id"]
        frame["points"], frame["exposure_possessions"] = samples[f"{side}_points"], samples.exposure
        frame["count_as_possession"] = True
        frame["lineup_source"] = "Event lineups; foul-time FT credit; shared possession exposure"
        frame[OFF] = samples[[f"{side}_player_{i}" for i in range(1, 6)]].to_numpy()
        frame[DEF] = samples[[f"{other}_player_{i}" for i in range(1, 6)]].to_numpy()
        rows.append(frame)
    history = prepare_history(pd.concat(rows, ignore_index=True), games, boxes,
        source=source, plus_minus_tolerance=0.0, weight_column="exposure_possessions")
    for game, reasons in history.audit["excluded_game_reasons"].items():
        issues[game].update(reasons)
    excluded = {game: sorted(reasons) for game, reasons in issues.items() if reasons}
    history.audit.update({"source_games": len(present), "accepted_games": len(history.games),
        "excluded_games": len(excluded), "excluded_game_reasons": excluded,
        "exclusion_reasons": dict(Counter(reason for reasons in excluded.values() for reason in reasons)),
        "raw_regular_season_events": len(e), "raw_regular_season_possessions": len(p),
        "minute_tolerance_seconds": minute_tolerance_seconds,
        "maximum_accepted_player_minute_error_seconds": float(difference.loc[
            difference.index.get_level_values("game_id").isin(history.games.game_id)].max() * 60),
        "free_throw_events_using_foul_lineup": int((owner != e.index).sum()),
        "free_throw_events_with_changed_point_lineup": int((point_rows[units] != e[units]).any(axis=1).sum()),
        "possession_exposure_policy": "Each counted possession shared equally across distinct event lineups; average both sides for common exposure",
        "point_credit_policy": "FT credited to most recent same-clock foul lineup; all player box plus/minus must match exactly"})
    final_scores = e.groupby("game_id")[["home_points", "away_points"]].sum().join(
        games.set_index("game_id")[["home_pts", "away_pts"]])
    history.audit["games_with_exact_scores"] = int(((final_scores.home_points == final_scores.home_pts) &
        (final_scores.away_points == final_scores.away_pts)).sum())
    return history


def attach_box_history(history: History, schedule, box_scores) -> History:
    """Broader team sample independent of event-lineup reconstruction quality.

    Both team baselines and player exposures use the same box estimate of
    possessions: mean(FGA + 0.44*FTA - player OREB + TOV) across the two teams.
    Player observations are rescaled, never given invented point/lineup data.
    """
    stats = ["field_goals_attempted", "free_throws_attempted", "rebounds_offensive", "turnovers", "points"]
    _columns(schedule, ["game_id", "game_date", "home_team_id", "away_team_id", "home_team_name",
                        "away_team_name", "home_pts", "away_pts"], "Schedule")
    _columns(box_scores, ["game_id", "team_id", "person_id", "minutes", "first_name", "family_name", *stats], "Player box scores")
    g, b = schedule.copy(), box_scores.copy()
    for frame in (g, b):
        frame["game_id"] = _ids(frame.game_id, "Game IDs", game=True)
    g, b = [frame.loc[frame.game_id.str.startswith("002")].copy() for frame in (g, b)]
    if g.game_id.duplicated().any() or b.duplicated(["game_id", "team_id", "person_id"]).any():
        raise InputError("Team history contains duplicate games or player box scores.")
    for frame, columns in ((g, ["home_team_id", "away_team_id"]), (b, ["team_id", "person_id"])):
        for column in columns:
            frame[column] = _ids(frame[column], column)
    g["game_date"] = pd.to_datetime(g.game_date, errors="coerce").dt.date
    if g.game_date.isna().any():
        raise InputError("Team history has invalid game dates.")
    g["available_at"] = pd.to_datetime(g.game_date.map(_availability), utc=True)
    b["minutes_value"] = b.minutes.map(_box_minutes)
    b["player_name"] = (b.first_name.fillna("").astype(str).str.strip() + " " +
                        b.family_name.fillna("").astype(str).str.strip()).str.strip()
    for column in stats:
        b[column] = pd.to_numeric(b[column], errors="coerce")
    for column in ("home_pts", "away_pts"):
        g[column] = pd.to_numeric(g[column], errors="coerce")
    invalid = (~np.isfinite(b[stats])).any(axis=1) | (b[stats] < 0).any(axis=1) | (b[stats] % 1 != 0).any(axis=1)
    invalid_games = set(b.loc[invalid, "game_id"])
    totals = b.groupby(["game_id", "team_id"])[[*stats, "minutes_value"]].sum()
    records, exclusions = [], {}
    for row in g.to_dict("records"):
        game, issues, sides = row["game_id"], set(), []
        if game in invalid_games:
            issues.add("invalid_box_stats")
        if row["home_team_id"] == row["away_team_id"]:
            issues.add("team_id_mismatch")
        for side in ("home", "away"):
            key = (game, row[f"{side}_team_id"])
            if key not in totals.index:
                issues.add("missing_box_team")
                continue
            team = totals.loc[key]
            if not math.isfinite(row[f"{side}_pts"]) or row[f"{side}_pts"] <= 0 or team.points != row[f"{side}_pts"]:
                issues.add("box_score_mismatch")
            duration = 48 + 5 * round((team.minutes_value / 5 - 48) / 5)
            if duration < 48 or not math.isclose(team.minutes_value, duration * 5, abs_tol=0.25):
                issues.add("box_minutes_incomplete")
            estimate = team.field_goals_attempted + 0.44 * team.free_throws_attempted - team.rebounds_offensive + team.turnovers
            if not math.isfinite(estimate) or estimate <= 0:
                issues.add("invalid_box_possession_estimate")
            sides.append((duration, estimate))
        if len(sides) == 2 and sides[0][0] != sides[1][0]:
            issues.add("box_minutes_incomplete")
        if issues:
            exclusions[game] = sorted(issues)
        else:
            row.update(game_minutes=sides[0][0], possessions=sum(x[1] for x in sides) / 2)
            records.append(row)
    if not records:
        raise InputError("No games passed team box-score checks.")
    team_games = pd.DataFrame(records)
    team_box = b.loc[b.game_id.isin(team_games.game_id)].merge(
        team_games[["game_id", "game_date", "available_at"]], on="game_id", validate="many_to_one")
    team_rows = []
    for side, other, venue in (("home", "away", 1.0), ("away", "home", -1.0)):
        frame = team_games[["game_id", "game_date", "available_at", "possessions"]].copy()
        frame["offense_team_id"], frame["defense_team_id"] = team_games[f"{side}_team_id"], team_games[f"{other}_team_id"]
        frame["points"], frame["home_offense"] = team_games[f"{side}_pts"], venue
        team_rows.append(frame)
    accepted = set(team_games.game_id)
    player_games = history.games.loc[history.games.game_id.isin(accepted)].copy()
    if player_games.empty:
        raise InputError("No event-verified games have a valid matching team box score.")
    estimate = team_games.set_index("game_id").possessions
    original = history.games.set_index("game_id").possessions
    observations = history.observations.loc[history.observations.game_id.isin(accepted)].copy()
    observations["possessions"] *= observations.game_id.map(estimate / original)
    player_games["possessions"] = player_games.game_id.map(estimate)
    audit = dict(history.audit)
    player_exclusions = {game: list(reasons) for game, reasons in audit["excluded_game_reasons"].items()}
    for game in set(history.games.game_id) - accepted:
        player_exclusions[game] = sorted(set(player_exclusions.get(game, [])) | {"team_box_checks_failed"})
    audit.update({"team_source_games": len(g), "team_accepted_games": len(team_games),
        "team_excluded_games": len(exclusions), "team_excluded_game_reasons": exclusions,
        "possession_units": "Box estimate: mean of both teams' FGA + 0.44*FTA - player OREB + TOV; event exposures rescaled per game",
        "player_games_removed_by_box_checks": len(history.games) - len(player_games),
        "accepted_games": len(player_games), "accepted_observation_groups": len(observations),
        "accepted_possessions": float(observations.possessions.sum()),
        "excluded_games": len(player_exclusions), "excluded_game_reasons": player_exclusions,
        "exclusion_reasons": dict(Counter(r for reasons in player_exclusions.values() for r in reasons))})
    return History(observations, player_games,
        history.box.loc[history.box.game_id.isin(accepted)].copy(), audit, history.source,
        pd.concat(team_rows, ignore_index=True), team_games, team_box)


def prior_history(history: History, as_of) -> History:
    cutoff = timestamp(as_of, "training cutoff")
    obs = history.observations.loc[history.observations.available_at <= cutoff].copy()
    games = history.games.loc[history.games.available_at <= cutoff].copy()
    box = history.box.loc[history.box.available_at <= cutoff].copy()
    if obs.empty or games.empty:
        raise InputError("No verified completed games were available at the training cutoff.")
    optional = {key: None if getattr(history, key) is None else getattr(history, key).loc[
        getattr(history, key).available_at <= cutoff].copy()
        for key in ("team_observations", "team_games", "team_box")}
    return History(obs, games, box, history.audit, history.source, **optional)


def forecast_minutes(history: History, team_id: str, as_of, window: int = 5) -> dict[str, float]:
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        raise InputError("Minutes forecast window must be a positive integer.")
    cutoff = timestamp(as_of, "minutes forecast cutoff")
    game_history = history.team_games if history.team_games is not None else history.games
    box_history = history.team_box if history.team_box is not None else history.box
    games = game_history.loc[(game_history.available_at <= cutoff) & (
        (game_history.home_team_id == str(team_id)) | (game_history.away_team_id == str(team_id)))]
    games = games.sort_values(["game_date", "game_id"]).tail(window)
    if games.empty:
        raise InputError(f"No prior verified minutes for team {team_id}.")
    boxes = box_history.loc[box_history.game_id.isin(games.game_id) & (box_history.team_id == str(team_id))].copy()
    totals = boxes.groupby("game_id").minutes_value.sum()
    boxes["regulation_minutes"] = boxes.minutes_value * 240 / boxes.game_id.map(totals)
    estimate = boxes.groupby("person_id").regulation_minutes.sum() / len(games)
    # A traded player's old appearances must not seed both teams. Ownership
    # comes only from the latest VERIFIED box roster available at the cutoff.
    known = box_history.loc[box_history.available_at <= cutoff].copy()
    known = known.sort_values(["available_at", "minutes_value", "game_id"], kind="stable")
    ownership = known.drop_duplicates("person_id", keep="last").set_index("person_id").team_id
    estimate = estimate.loc[estimate.index.map(ownership).astype(str) == str(team_id)]
    if estimate.sum() <= 0:
        raise InputError("No prior rotation minutes remain on the latest known roster.")
    estimate = estimate * 240 / estimate.sum()
    for _ in range(len(estimate) + 1):
        excess = float((estimate - 48).clip(lower=0).sum())
        estimate = estimate.clip(upper=48)
        if excess < 1e-8:
            break
        eligible = (estimate > 0) & (estimate < 48)
        if not eligible.any():
            raise InputError("The prior roster cannot supply 240 regulation minutes.")
        estimate.loc[eligible] += excess * estimate.loc[eligible] / estimate.loc[eligible].sum()
    result = {str(player): float(minutes) for player, minutes in estimate.items() if minutes > 0}
    if not math.isclose(sum(result.values()), 240.0, abs_tol=0.01):
        raise InputError("Historical minutes forecast does not total 240.")
    return result
