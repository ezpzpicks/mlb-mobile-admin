"""Scheduled NFL projections using the unchanged production model and grades."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from builders.nfl_rotowire_props import SourceError, apply_quotes, name_key, team_key
from shared.ncaaf_auto_builder import MarketGame, number, timestamp
from shared.nfl_auto_storage import POLICY, clean, records

ET = ZoneInfo("America/New_York")


def normalize_game(row):
    game = dict(row)
    kickoff = timestamp(row.get("Game Time"))
    if kickoff is None:
        raw = str(row.get("Game Date", row.get("Date", "")))[:10] + " " + str(row.get("Game Time", ""))
        try:
            kickoff = datetime.strptime(raw, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
        except ValueError:
            raise SourceError("NFL schedule kickoff is missing or invalid.") from None
    game.update({"Date": str(kickoff.astimezone(ET).date()), "Game Date": str(kickoff.astimezone(ET).date()),
        "Game Time": kickoff.isoformat(), "Away Team": team_key(row["Away Team"]), "Home Team": team_key(row["Home Team"]),
        "Season": int(float(row["Season"])), "Week": int(float(row["Week"])), "Game ID": str(row["Game ID"])})
    if game["Away Team"] == game["Home Team"] or not game["Game ID"]:
        raise SourceError("Invalid NFL schedule identity.")
    return game


def phase_for_game(game, now, previous):
    kickoff = timestamp(game["Game Time"])
    minutes = (kickoff - now).total_seconds() / 60
    local = now.astimezone(ET)
    if game["Date"] != str(local.date()) or local.hour < 8 or minutes < 10:
        return None
    if str(game.get("Completed", "")).lower() in {"true", "1", "yes"}:
        return None
    if all(str(game.get(k, "")).strip() not in {"", "None", "nan"} for k in ("Away Score", "Home Score")):
        return None
    gid = game["Game ID"]
    if minutes <= 75 and (gid, "pregame") not in previous:
        return "pregame"
    if 15 <= minutes <= 45 and (gid, "pregame") in previous and (gid, "late") not in previous:
        return "late"
    if minutes > 75 and (gid, "early") not in previous and (gid, "pregame") not in previous:
        return "early"
    return None


def game_market(rows, game, now):
    normalized = []
    for row in rows:
        try:
            value = dict(row)
            for col in ("Away Team", "Home Team"):
                value[col] = team_key(value[col])
            if value.get("Market") == "Spread":
                value["Selection"] = team_key(value.get("Selection"))
            normalized.append(value)
        except (SourceError, KeyError):
            continue
    selected = {}
    for row in normalized:
        if row.get("Date") != game["Date"] or row.get("Away Team") != game["Away Team"] or row.get("Home Team") != game["Home Team"]:
            continue
        if str(row.get("Source", "")).lower() != "scoresandodds" or row.get("Snapshot Status") not in {"LIVE", "FINAL_PREGAME"}:
            continue
        key = row.get("Market"), row.get("Selection") if row.get("Market") == "Spread" else str(row.get("Side") or row.get("Selection", "")).lower()
        captured = timestamp(row.get("Updated At"))
        if captured and (key not in selected or captured > timestamp(selected[key]["Updated At"])):
            selected[key] = row
    sides = [selected.get(k) for k in (("Spread", game["Away Team"]), ("Spread", game["Home Team"]), ("Total", "over"), ("Total", "under"))]
    if not all(sides):
        raise SourceError("Waiting for fresh ScoresAndOdds spread/total sides matching the NFL kickoff.")
    times = [timestamp(r.get("Updated At")) for r in sides]
    kickoffs = [timestamp(r.get("Game Time")) for r in sides]
    lines = [number(r.get("Line")) for r in sides]
    if any(x is None for x in [*times, *kickoffs, *lines]):
        raise SourceError("Incomplete NFL market timestamps or lines.")
    if any(not -timedelta(minutes=5) <= now - t <= timedelta(minutes=90) for t in times) or max(times) - min(times) > timedelta(minutes=5):
        raise SourceError("NFL spread/total snapshots are stale or inconsistent.")
    kickoff = timestamp(game["Game Time"])
    if kickoff <= now or any(abs((t - kickoff).total_seconds()) > 60 for t in kickoffs):
        raise SourceError("NFL market kickoff does not match the live schedule.")
    if abs(lines[0] + lines[1]) > .001 or abs(lines[2] - lines[3]) > .001 or lines[2] <= 0:
        raise SourceError("NFL spread/total sides are inconsistent.")
    return MarketGame(game["Date"], game["Away Team"], game["Home Team"], kickoff, min(times), lines[1], lines[2], None, None, str(sides[1].get("Game Key", "")))


def fetch_depth(builder, teams, season, *, on_event=None):
    """Resolve only scheduled teams directly from ESPN Core, with bounded I/O.

    The manual loader's 21-page all-time athlete directory is unnecessary here.
    Skill positions retain the full chart so an injured WR3 can be replaced by
    the actual next receiver. Other positions retain rank-three depth.
    """
    started = datetime.now(timezone.utc)
    def get(url):
        response = requests.get(url, headers={"User-Agent": "EZPZ-NFL-Automation/1.0"}, timeout=15)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise SourceError("ESPN Core returned an invalid response.")
        return payload
    def chart(team):
        url = f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/{season}/teams/{builder.ESPN_TEAM_IDS[team]}/depthcharts"
        data = get(url)
        charts = [x for x in data.get("items", []) if isinstance(x, dict)]
        selected = []
        for markers in ({"QB", "RB", "WR", "TE", "LT", "LG", "C", "RG", "RT"},
                        {"DE", "LDE", "RDE", "LE", "RE", "EDGE", "DT", "NT", "MLB", "LB", "ILB", "LILB", "RILB", "OLB", "LCB", "RCB", "CB", "NB", "S", "FS", "SS", "LOLB", "ROLB", "WLB", "SLB"}):
            def score(c):
                return len({str(e.get("position", {}).get("abbreviation", k)).upper() for k, e in c.get("positions", {}).items()} & markers)
            best = max(charts, key=score, default=None)
            if best is None or score(best) < 4:
                raise SourceError(f"ESPN Core has no complete depth chart for {team}.")
            if best not in selected:
                selected.append(best)
        entries = []
        for c in selected:
            for key, entry in c.get("positions", {}).items():
                raw_pos = str(entry.get("position", {}).get("abbreviation", key)).upper()
                pos = builder._normalize_espn_depth_position(raw_pos)
                if not pos:
                    continue
                for order, athlete in enumerate(entry.get("athletes", []), 1):
                    rank = builder._int(athlete.get("rank", order), order)
                    if rank > 3 and pos not in {"QB", "RB", "WR", "TE"}:
                        continue
                    ref = str(athlete.get("athlete", {}).get("$ref", "")).replace("http://", "https://")
                    if not re.fullmatch(r"https://sports\.core\.api\.espn\.com/v2/sports/football/leagues/nfl/(?:seasons/\d{4}/)?athletes/\d+(?:\?[^\s]*)?", ref):
                        raise SourceError(f"Invalid ESPN Core player identity for {team}.")
                    entries.append((team, pos, raw_pos, max(1, rank), builder._int(athlete.get("slot", order), order), ref))
        return entries
    failures = {}
    def safe_chart(team):
        try:
            return chart(team)
        except Exception as exc:
            failures[team] = str(exc)
            return []
    def safe_get(url):
        try:
            return get(url)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = [r for group in pool.map(safe_chart, sorted(set(teams))) for r in group]
        urls = list(dict.fromkeys(row[-1] for row in entries))
        athletes = dict(zip(urls, pool.map(safe_get, urls)))
    rows = []
    for team, pos, raw_pos, rank, slot, ref in entries:
        athlete = athletes[ref]
        if not athlete:
            failures[team] = "A current ESPN Core player could not be resolved."
            continue
        name = str(athlete.get("displayName", athlete.get("fullName", "")))
        if not name or not athlete.get("status"):
            failures[team] = "Incomplete current player status."
            continue
        status, injury, probability = builder._espn_athlete_availability(athlete)
        rows.append({"team_norm": team, "player_name_display": name, "name_norm": builder._normalize_name(name),
            "pos_norm": pos, "raw_pos_norm": raw_pos, "pos_rank_num": rank, "pos_slot_num": slot,
            "depth_source": "ESPN Core current depth chart", "espn_id": ref.split("/athletes/")[1].split("?")[0],
            "espn_injury_status": status, "espn_injury": injury, "espn_auto_probability": probability})
    if on_event:
        on_event("espn_core_depth_read", teams=sorted(set(teams)), players=len(athletes), observed_at=started.isoformat(), rejected_teams=failures)
    frame = pd.DataFrame([r for r in rows if r["team_norm"] not in failures])
    if not frame.empty:
        frame = frame.sort_values(["team_norm", "pos_norm", "pos_rank_num", "pos_slot_num", "player_name_display"]).drop_duplicates(["team_norm", "pos_norm", "name_norm"])
    return frame, started.isoformat()


def checked_depth(depth, game, inactives, now):
    if int(inactives["season"]) != game["Season"]:
        raise SourceError("RotoWire inactive list belongs to a different season.")
    age = now - datetime.fromisoformat(inactives["observed_at"])
    if not timedelta(0) <= age <= timedelta(minutes=15):
        raise SourceError("Refresh game-day inactives before the final NFL build.")
    output = depth.copy()
    for team in (game["Away Team"], game["Home Team"]):
        if team not in inactives["teams"]:
            raise SourceError(f"Waiting for {team}'s published game-day inactive list.")
        inactive = {name_key(p) for p in inactives["teams"][team]}
        for index, row in output[output["team_norm"] == team].iterrows():
            status = str(row["espn_injury_status"]).upper()
            if name_key(row["player_name_display"]) in inactive:
                output.at[index, "espn_injury_status"] = "INACTIVE"
                output.at[index, "espn_auto_probability"] = 0.0
            elif status not in {"OUT", "IR", "INJURED RESERVE", "SUSPENDED", "PUP", "NFI"}:
                # This confirms eligibility, not health or a full workload.
                output.at[index, "espn_injury_status"] = "ACTIVE"
                output.at[index, "espn_auto_probability"] = 1.0
    return output


def weekly_inputs(builder, db, season, week, *, now, commit, on_event):
    from builders import nfl_slot_matchups as slots
    key = f"weekly:{builder.MODEL_VERSION}:{season}:{week}"
    cached = db.cached(key)
    if cached and timedelta(0) <= now - datetime.fromisoformat(cached[0]) <= timedelta(hours=36):
        data = cached[1]
        input_observed_at = cached[0]
        on_event("weekly_inputs_cache_hit", season=season, week=week)
    else:
        input_observed_at = now.isoformat()
        on_event("weekly_inputs_build", season=season, week=week)
        ratings = builder._build_automated_ratings(season, week)
        if not builder._ratings_quality(ratings)["valid"]:
            raise SourceError("Complete production NFL ratings are not ready.")
        stats = {str(year): records(builder._load_player_stats_season(year)) for year in (season - 1, season)}
        if not stats[str(season)] or max(int(r.get("week", 0) or 0) for r in stats[str(season)]) < max(1, week - 1):
            raise SourceError("Current NFL player statistics are not updated through the previous week.")
        data = {"ratings": records(ratings), "stats": stats,
            "profiles": records(builder._blended_player_profiles(season, week)),
            "defense_profiles": records(builder._defense_position_profiles(season, week)),
            "player_values": builder._blend_player_values(season, week),
            "slot_history": records(slots._slot_history(builder, season, max(0, week - 1))),
            "schedules": {str(y): records(builder._schedule_for_season(y)) for y in range(season - builder.HOME_FIELD_LOOKBACK_SEASONS, season + 1)}}
        if not data["profiles"] or not data["defense_profiles"]:
            raise SourceError("NFL player or opponent profiles are unavailable.")
        if commit:
            db.cache(key, data, now)
    stats_loader, schedule_loader = builder._load_player_stats_season, builder._schedule_for_season
    builder._load_player_stats_season = lambda year: pd.DataFrame(data["stats"][str(year)]) if str(year) in data["stats"] else stats_loader(year)
    builder._schedule_for_season = lambda year, refresh=False: pd.DataFrame(data["schedules"][str(year)]) if str(year) in data["schedules"] else schedule_loader(year, refresh=refresh)
    slots._HISTORY_CACHE[(season, max(0, week - 1))] = pd.DataFrame(data["slot_history"], columns=["season", "week", "team", "opponent", "slot", "player", "market", "actual"])
    return {**data, "input_cache_key": key, "input_observed_at": input_observed_at, "ratings": pd.DataFrame(data["ratings"]), "profiles": pd.DataFrame(data["profiles"]), "defense_profiles": pd.DataFrame(data["defense_profiles"])}


def environment(builder, game, *, defaults=None):
    from builders import nfl_covers
    if defaults is None:
        defaults = builder._schedule_defaults(pd.Series(game))
    roof = str(defaults.get("roof", "outdoors")).lower()
    if roof in {"dome", "closed"}:
        return {"roof": roof, "temperature": 70.0, "wind": 0.0, "precipitation": "None", "source": "Checked schedule: enclosed stadium"}
    # The admin permits manual weather overrides. An unattended run must have a
    # current Covers card rather than silently using the UI's 70 F / 6 mph defaults.
    if defaults.get("weather_source") != "Covers.com":
        raise SourceError("Current Covers weather is unavailable for this outdoor NFL game.")
    cache = nfl_covers.CACHE_FILE
    if not cache.is_file() or datetime.now().timestamp() - cache.stat().st_mtime > 1800:
        raise SourceError("Covers weather cache is stale; no automatic outdoor forecast saved.")
    return {**{k: defaults[k] for k in ("roof", "temperature", "wind", "precipitation")}, "source": "Covers.com"}


def projection_settings(defaults, home_field, weather_adjustment):
    # Match the scheduled admin's automatic rest/travel adjustment, including
    # its existing cap. Manual overrides remain zero in unattended builds.
    rest = max(-1.5, min(1.5, (defaults["home_rest"] - defaults["away_rest"]) * .18))
    return {"home_field": home_field, "weather_total_adjustment": weather_adjustment,
            "home_rest_edge": rest, "manual_home_margin_adjustment": 0.0, "manual_total_adjustment": 0.0}


def input_identity(game, market, quotes, depth, weather, inactives):
    # Observation timestamps and HTML hashes change even if the model inputs do
    # not. Exclude those from the change fingerprint; keep them in provenance.
    value = {"game": {k: game[k] for k in ("Game ID", "Game Time", "Away Team", "Home Team", "Season", "Week")},
        "schedule_context": {k: game.get(k, "") for k in ("Away Rest", "Home Rest", "Location")},
        "market": [market.home_spread, market.total], "quotes": sorted(quotes, key=lambda q: (q["team"], q["player"], q["market"], q["book"])),
        "depth": sorted(records(depth), key=lambda r: (r["team_norm"], r["pos_norm"], r["pos_rank_num"], r["name_norm"])),
        "weather": weather, "inactives": {t: inactives["teams"].get(t, []) for t in (game["Away Team"], game["Home Team"])} if inactives else {}}
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def withhold_unavailable_quotes(evaluated, threshold):
    """Keep the public site's independent yardage grader from reviving a hold."""
    output = evaluated.copy()
    held = pd.to_numeric(output.get("_play_probability", pd.Series(0.0, index=output.index)), errors="coerce").fillna(0.0) < threshold
    output.loc[held, "Grade"] = "Availability hold"
    output.loc[held, "Track"] = False
    output.loc[held, "Pick"] = "Availability hold"
    for column in ("Market Line", "Over Odds", "Under Odds", "Pick Odds", "Implied Probability", "Probability Edge", "Projection Edge", "Expected Value"):
        output.loc[held, column] = float("nan")
    output.loc[held, "Line Source"] = "Quote withheld: availability below 90%; original quote retained in build provenance"
    return output


def compute(builder, game, market, weekly, depth, props, *, now, phase, depth_at, inactives=None):
    if not timedelta(0) <= now - datetime.fromisoformat(depth_at) <= timedelta(minutes=15):
        raise SourceError("Current depth/availability observation is stale.")
    if depth.empty or not {game["Away Team"], game["Home Team"]}.issubset(set(depth["team_norm"])):
        raise SourceError("A complete current depth chart is unavailable for this NFL matchup.")
    if phase != "early":
        depth = checked_depth(depth, game, inactives, now)
    depth = depth[depth["team_norm"].isin([game["Away Team"], game["Home Team"]])].copy()
    injuries = {(r["team_norm"], r["name_norm"]): {"status": r["espn_injury_status"], "auto_probability": r["espn_auto_probability"], "injury": r["espn_injury"]} for r in records(depth)}
    lineups, summaries, ratings = {}, {}, {}
    for side in ("Away", "Home"):
        team = game[f"{side} Team"]
        ratings[side] = builder._team_row(weekly["ratings"], team, game["Season"], game["Week"])
        lineup = builder._auto_lineup(team, game["Season"], game["Week"], depth, injuries, weekly["player_values"])
        lineups[side], summaries[side] = builder._finalize_lineup(lineup)
        skill = lineups[side][lineups[side]["Slot"].isin(["QB", "RB1", "WR1", "WR2", "WR3", "TE1"])]
        if skill["Player"].astype(str).str.upper().isin(["", "TBD", "UNKNOWN"]).any():
            raise SourceError(f"The current {team} skill-position depth is incomplete.")
    defaults = builder._schedule_defaults(pd.Series(game))
    weather = environment(builder, game, defaults=defaults)
    weather_adjustment = builder._weather_adjustment(weather["roof"], weather["temperature"], weather["wind"], weather["precipitation"])
    reliability, data_confidence, personnel_confidence = builder._reliability(ratings["Away"], ratings["Home"], summaries["Away"], summaries["Home"], game["Week"], False)
    hfa = builder._automatic_home_field_advantage(game["Season"], game["Week"], game["Home Team"], pd.Series(game))
    settings = projection_settings(defaults, hfa["value"], weather_adjustment)
    projection = builder._project_matchup(ratings["Away"], ratings["Home"], summaries["Away"], summaries["Home"], settings)
    seed = builder._simulation_seed(game["Game ID"], game["Away Team"], game["Home Team"], game["Season"], game["Week"])
    simulation = builder._simulate_game(projection, market.home_spread, market.total, reliability, seed)
    selections = {}
    for kind in ("Spread", "Total"):
        home_prob = simulation["home_cover"] if kind == "Spread" else simulation["over"]
        positive = home_prob >= 0.5
        probability = home_prob if positive else 1 - home_prob
        if kind == "Spread":
            pick = f"{game['Home Team']} {market.home_spread:+.1f}" if positive else f"{game['Away Team']} {-market.home_spread:+.1f}"
            edge = (projection["margin"] + market.home_spread) * (1 if positive else -1)
            confluence, _ = builder._spread_confluence(positive, edge, ratings["Away"], ratings["Home"], summaries["Away"], summaries["Home"], reliability)
            grade = builder._grade_spread(probability, edge, reliability, confluence)
        else:
            pick = f"{'Over' if positive else 'Under'} {market.total:.1f}"
            edge = (projection["total"] - market.total) * (1 if positive else -1)
            confluence, _ = builder._total_confluence(positive, edge, projection, ratings["Away"], ratings["Home"], weather_adjustment, reliability)
            grade = builder._grade_total_direction(pick, probability, edge, reliability, confluence)
        from shared.modeling import expected_value_per_unit
        if probability <= 0.5 or expected_value_per_unit(probability, -110) <= 0:
            grade = f"Non-Edge {kind}"
        selections[kind] = {"Pick": pick, "Probability": probability, "Edge": round(edge, 2), "Grade": grade, "Confluence": confluence}
    label = f"{game['Away Team']} at {game['Home Team']}"
    note = f"{POLICY}; {phase}; ScoresAndOdds game lines, assumed -110 game prices; RotoWire actual prop prices; {weather['source']}; availability observed, workload conditional."
    version = f"{builder.MODEL_VERSION}|{POLICY}"
    slate = {"Date": game["Date"], "Season": game["Season"], "Week": game["Week"], "Game ID": game["Game ID"], "Game": label,
        "Away Team": game["Away Team"], "Home Team": game["Home Team"], "Game Time": game["Game Time"],
        "Projected Away": round(projection["away_score"], 1), "Projected Home": round(projection["home_score"], 1),
        "Projected Margin": round(projection["margin"], 1), "Projected Total": round(projection["total"], 1),
        "Market Home Spread": market.home_spread, "Market Total": market.total,
        "Home Spread Odds": -110, "Away Spread Odds": -110, "Total Over Odds": -110, "Total Under Odds": -110,
        "Reliability": reliability, "Data Confidence": data_confidence, "Personnel Confidence": personnel_confidence,
        "Previous Season Weight": ratings["Away"].get("Previous Season Weight", ""), "Current Season Weight": ratings["Away"].get("Current Season Weight", ""),
        "Weather Adjustment": weather_adjustment, "Roof": weather["roof"], "Temperature": weather["temperature"], "Wind": weather["wind"],
        "ML Grade": "No market line", "Model Version": version, "Notes": note}
    for side in ("Away", "Home"):
        for label_part, field in (("Low", "low"), ("High", "high")):
            slate[f"{side} Score {label_part}"] = round(simulation[f"{side.lower()}_{field}"], 1)
        slate[f"{side} Offensive Absence"] = summaries[side]["offense_absence"]
        slate[f"{side} Defensive Absence"] = summaries[side]["defense_absence"]
    for kind, values in selections.items():
        slate.update({f"{kind} {k}": round(v, 4) if k == "Probability" else v for k, v in values.items()})
    base = builder._build_game_prop_rows(game["Away Team"], game["Home Team"], lineups["Away"], lineups["Home"], weekly["profiles"], weekly["defense_profiles"], ratings["Away"], ratings["Home"], projection, weather_adjustment, market_total=market.total, home_spread=market.home_spread)
    quoted, matched = apply_quotes(base, game, props, now=now)
    evaluated = builder._evaluate_prop_rows(quoted)
    # Keep availability outside the market-grade formula; an unconfirmed player
    # cannot be published merely because a mathematical yardage edge is large.
    evaluated = withhold_unavailable_quotes(evaluated, builder.MIN_GRADED_PROP_PLAY_PROBABILITY)
    p_rows = builder._prop_rows_for_storage(evaluated, game["Date"], game["Season"], game["Week"], game["Game ID"], label, note)
    p_tracker = builder._graded_prop_tracker_rows(evaluated, game["Date"], game["Season"], game["Week"], game["Game ID"], label, note)
    s, t = selections["Spread"], selections["Total"]
    g_tracker = builder._game_tracker_rows(game["Date"], game["Season"], game["Week"], game["Game ID"], label,
        s["Pick"], s["Probability"], -110, 0.5, s["Grade"], s["Confluence"],
        t["Pick"], t["Probability"], -110, 0.5, t["Grade"], t["Confluence"], "", 0.0, -110, "No market line", 0,
        reliability, data_confidence, personnel_confidence, projection["away_score"], projection["home_score"], note)
    l_rows = []
    for side, lineup in lineups.items():
        for row in records(lineup):
            row.update({"Date": game["Date"], "Season": game["Season"], "Week": game["Week"], "Game ID": game["Game ID"], "Team": game[f"{side} Team"]})
            l_rows.append(row)
    for row in [*p_rows, *p_tracker, *g_tracker, *l_rows]:
        row.update({"Model Version": version, "Game Time": game["Game Time"]})
    fingerprint = input_identity(game, market, matched, depth, weather, inactives)
    return clean({"slate": slate, "props": p_rows, "prop_tracker": p_tracker, "game_tracker": g_tracker, "lineups": l_rows,
        "input_hash": fingerprint, "provenance": {"props": {k: props[k] for k in ("source", "source_sha256", "observed_at", "week")},
            "quotes": matched, "depth_observed_at": depth_at, "game_market_observed_at": market.captured.isoformat(),
            "inactives": inactives, "weather": weather, "model_version": builder.MODEL_VERSION,
            "weekly_input_key": weekly.get("input_cache_key"), "weekly_input_observed_at": weekly.get("input_observed_at"),
            "ratings": ratings, "home_field": hfa, "projection_settings": settings,
            "lineup_summaries": summaries, "game_projection": projection}})
