from pathlib import Path

path = Path("builders/nfl_builder.py")
text = path.read_text(encoding="utf-8")

old_rb2 = '("RB2", ["RB", "HB", "FB"]),'
new_rb2 = '("RB2", ["RB", "HB"]),'
if text.count(old_rb2) != 1:
    raise SystemExit("RB2 slot definition not found exactly once")
text = text.replace(old_rb2, new_rb2, 1)

old_doc = '    """Daily current-team, depth-order and injury fallback from Sleeper.\n'
new_doc = '    """Daily current-team and injury/status fallback from Sleeper.\n'
if text.count(old_doc) != 1:
    raise SystemExit("Sleeper loader docstring anchor not found exactly once")
text = text.replace(old_doc, new_doc, 1)

for obsolete_line in [
    '                "depth_chart_order": _int(raw.get("depth_chart_order", 99), 99),\n',
    '                "depth_chart_position": _safe_text(raw.get("depth_chart_position", "")),\n',
]:
    if text.count(obsolete_line) != 1:
        raise SystemExit(f"obsolete Sleeper depth field not found exactly once: {obsolete_line.strip()}")
    text = text.replace(obsolete_line, "", 1)

insertion_anchor = "\n\n@st.cache_resource(ttl=21600, show_spinner=False)\ndef _load_snap_counts_season(season: int) -> pd.DataFrame:\n"
if text.count(insertion_anchor) != 1:
    raise SystemExit("snap-count loader insertion anchor not found exactly once")

espn_loader = '''

@st.cache_resource(ttl=3600, show_spinner=False)
def _load_espn_depth_charts() -> pd.DataFrame:
    """Load current NFL skill-position depth order from ESPN once per hour.

    ESPN is the live authority for QB/RB/WR/TE ordering. nflverse remains the
    detailed OL/defensive fallback, while Sleeper is reserved for injury/status
    metadata only.
    """
    team_url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=50"
    headers = {"User-Agent": "EZPZ-Picks/1.0"}
    try:
        response = requests.get(team_url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
        sports = payload.get("sports", []) if isinstance(payload, dict) else []
        leagues = sports[0].get("leagues", []) if sports else []
        entries = leagues[0].get("teams", []) if leagues else []

        team_ids: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            team_data = entry.get("team", entry)
            if not isinstance(team_data, dict):
                continue
            abbreviation = _normalize_team(team_data.get("abbreviation", ""))
            team_id = _safe_text(team_data.get("id", ""))
            if abbreviation in NFL_TEAMS and team_id:
                team_ids[abbreviation] = team_id

        if not team_ids:
            raise RuntimeError("ESPN team directory returned no NFL team IDs.")

        rows: list[dict[str, Any]] = []
        failed_teams: list[str] = []
        position_map = {
            "QB": "QB", "RB": "RB", "HB": "RB", "FB": "FB", "WR": "WR",
            "LWR": "WR", "RWR": "WR", "SWR": "WR", "TE": "TE",
        }

        with requests.Session() as session:
            session.headers.update(headers)
            for team in NFL_TEAMS:
                team_id = team_ids.get(team)
                if not team_id:
                    failed_teams.append(team)
                    continue
                url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/depthcharts"
                try:
                    team_response = session.get(url, timeout=15)
                    team_response.raise_for_status()
                    team_payload = team_response.json()
                    charts = team_payload.get("depthCharts", []) if isinstance(team_payload, dict) else []
                    before = len(rows)
                    for chart in charts:
                        if not isinstance(chart, dict):
                            continue
                        positions = chart.get("positions", {})
                        position_entries = list(positions.values()) if isinstance(positions, dict) else positions
                        if not isinstance(position_entries, list):
                            continue
                        for position_entry in position_entries:
                            if not isinstance(position_entry, dict):
                                continue
                            position_info = position_entry.get("position", {})
                            if not isinstance(position_info, dict):
                                position_info = {}
                            raw_position = _safe_text(
                                position_info.get("abbreviation", position_entry.get("abbreviation", ""))
                            ).upper()
                            position = position_map.get(raw_position, "")
                            if not position:
                                continue
                            athletes = position_entry.get("athletes", [])
                            if not isinstance(athletes, list):
                                continue
                            for order, athlete_entry in enumerate(athletes, start=1):
                                if not isinstance(athlete_entry, dict):
                                    continue
                                athlete = athlete_entry.get("athlete", athlete_entry)
                                if not isinstance(athlete, dict):
                                    continue
                                player_name = _safe_text(
                                    athlete.get("displayName", athlete.get("fullName", athlete.get("name", "")))
                                )
                                if not player_name:
                                    continue
                                rank = _int(athlete_entry.get("rank", order), order)
                                if rank <= 0:
                                    rank = order
                                rows.append({
                                    "team_norm": team,
                                    "player_name_display": player_name,
                                    "pos_norm": position,
                                    "pos_rank_num": rank,
                                    "pos_slot_num": order,
                                    "depth_source": "ESPN current depth chart",
                                })
                    if len(rows) == before:
                        failed_teams.append(team)
                except Exception:
                    failed_teams.append(team)

        output = pd.DataFrame(rows)
        if not output.empty:
            output["name_norm"] = output["player_name_display"].map(_normalize_name)
            output = output[
                output["team_norm"].isin(NFL_TEAMS)
                & output["name_norm"].astype(str).str.len().gt(0)
            ].copy()
            output = output.sort_values(
                ["team_norm", "pos_norm", "pos_rank_num", "pos_slot_num", "player_name_display"]
            )
            output = output.drop_duplicates(["team_norm", "pos_norm", "name_norm"], keep="first")

        loaded_teams = sorted(set(output.get("team_norm", pd.Series(dtype=str)).astype(str))) if not output.empty else []
        st.session_state["nfl_espn_depth_status"] = (
            f"ESPN depth charts loaded for {len(loaded_teams)}/32 teams."
            + (f" Fallback used for: {', '.join(sorted(set(failed_teams)))}." if failed_teams else "")
        )
        st.session_state.pop("nfl_espn_depth_error", None)
        return output.reset_index(drop=True) if not output.empty else pd.DataFrame()
    except Exception as exc:
        st.session_state["nfl_espn_depth_error"] = str(exc)
        st.session_state["nfl_espn_depth_status"] = "ESPN depth charts unavailable; nflverse fallback used."
        return pd.DataFrame()
'''
text = text.replace(insertion_anchor, espn_loader + insertion_anchor, 1)

start = text.find("def _latest_depth_chart(season: int) -> pd.DataFrame:")
end = text.find("\ndef _injury_lookup(season: int, week: int)", start)
if start < 0 or end < 0:
    raise SystemExit("_latest_depth_chart function boundaries not found")

new_latest = '''def _latest_depth_chart(season: int) -> pd.DataFrame:
    """Return the current depth chart with ESPN authoritative for skill roles.

    Current-season QB/RB/WR/TE ordering comes from ESPN's live depth-chart API.
    nflverse remains the detailed OL/defensive source and the fallback whenever
    ESPN is unavailable for a specific team or position. Sleeper remains an
    injury/status source only because its role-order metadata can be stale.
    """
    depth = _load_depth_charts_season(season)
    if depth.empty and season > 2001:
        depth = _load_depth_charts_season(season - 1)

    if depth.empty:
        out = pd.DataFrame(columns=[
            "team_norm", "player_name_display", "pos_norm", "pos_rank_num",
            "pos_slot_num", "depth_source",
        ])
    else:
        out = depth.copy()
        out["team_norm"] = _column(out, "team", default="").map(_normalize_team)
        out["player_name_display"] = _column(out, "player_name", "full_name", default="").astype(str)
        out["pos_norm"] = _column(out, "pos_abb", "position", default="").astype(str).str.upper()
        out["pos_rank_num"] = pd.to_numeric(
            _column(out, "pos_rank", "depth_team", default=99), errors="coerce"
        ).fillna(99)
        out["pos_slot_num"] = pd.to_numeric(
            _column(out, "pos_slot", default=99), errors="coerce"
        ).fillna(99)
        out["depth_source"] = "nflverse depth chart"
        if "dt" in out.columns:
            out["dt_parsed"] = pd.to_datetime(out["dt"], errors="coerce", utc=True)
            latest = out.groupby("team_norm")["dt_parsed"].transform("max")
            current = out[(out["dt_parsed"] == latest) | out["dt_parsed"].isna()].copy()
            if not current.empty:
                out = current

    espn = _load_espn_depth_charts() if int(season) == int(DEFAULT_SEASON) else pd.DataFrame()
    if not espn.empty:
        espn_skill = espn[
            espn["pos_norm"].astype(str).str.upper().isin(["QB", "RB", "WR", "TE"])
        ].copy()
        if not espn_skill.empty:
            for (team, position), _ in espn_skill.groupby(["team_norm", "pos_norm"], dropna=False):
                if out.empty:
                    break
                mask = (
                    out["team_norm"].astype(str).eq(str(team))
                    & out["pos_norm"].astype(str).str.upper().eq(str(position).upper())
                )
                out = out.loc[~mask].copy()
            out = pd.concat([out, espn_skill], ignore_index=True, sort=False)

    if out.empty:
        return out
    out["player_name_display"] = out["player_name_display"].astype(str)
    out["name_norm"] = out["player_name_display"].map(_normalize_name)
    out = out[
        out["team_norm"].astype(str).str.len().gt(0)
        & out["name_norm"].astype(str).str.len().gt(0)
    ].copy()
    out = out.sort_values(
        ["team_norm", "pos_norm", "pos_rank_num", "pos_slot_num", "player_name_display"]
    )
    return out.drop_duplicates(["team_norm", "name_norm"], keep="first").reset_index(drop=True)

'''
text = text[:start] + new_latest + text[end:]

legacy_patterns = [
    '"depth_chart_order": _int(raw.get("depth_chart_order"',
    '"depth_chart_position": _safe_text(raw.get("depth_chart_position"',
    'pd.to_numeric(skill["depth_chart_order"]',
]
if any(pattern in text for pattern in legacy_patterns):
    raise SystemExit("legacy Sleeper depth-order code still remains")

path.write_text(text, encoding="utf-8")
