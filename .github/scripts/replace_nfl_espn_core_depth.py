from pathlib import Path

path = Path("builders/nfl_builder.py")
text = path.read_text(encoding="utf-8")

old_version = 'MODEL_VERSION = "nfl-v3.3-qb-passing-yards-regression-2026-08-13"'
new_version = 'MODEL_VERSION = "nfl-v3.4-espn-core-depth-2026-09-13"'
if old_version in text:
    text = text.replace(old_version, new_version, 1)
elif new_version not in text:
    raise SystemExit("NFL MODEL_VERSION anchor not found")

team_aliases = '''TEAM_ALIASES = {
    "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "OAK": "LV",
    "SD": "LAC", "STL": "LAR", "WSH": "WAS",
}
'''
espn_team_ids = '''TEAM_ALIASES = {
    "ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "OAK": "LV",
    "SD": "LAC", "STL": "LAR", "WSH": "WAS",
}

# Stable ESPN Core team IDs. Using these directly avoids the site.api team
# directory, which is blocked from server environments and caused stale
# nflverse ordering to be used silently.
ESPN_TEAM_IDS = {
    "ARI": "22", "ATL": "1", "BAL": "33", "BUF": "2", "CAR": "29", "CHI": "3",
    "CIN": "4", "CLE": "5", "DAL": "6", "DEN": "7", "DET": "8", "GB": "9",
    "HOU": "34", "IND": "11", "JAX": "30", "KC": "12", "LAC": "24", "LAR": "14",
    "LV": "13", "MIA": "15", "MIN": "16", "NE": "17", "NO": "18", "NYG": "19",
    "NYJ": "20", "PHI": "21", "PIT": "23", "SEA": "26", "SF": "25", "TB": "27",
    "TEN": "10", "WAS": "28",
}
'''
if "ESPN_TEAM_IDS = {" not in text:
    if text.count(team_aliases) != 1:
        raise SystemExit("TEAM_ALIASES block not found exactly once")
    text = text.replace(team_aliases, espn_team_ids, 1)

start = text.find("@st.cache_resource(ttl=3600, show_spinner=False)\ndef _load_espn_depth_charts() -> pd.DataFrame:")
end = text.find("\n\n@st.cache_resource(ttl=900, show_spinner=False)\ndef _load_espn_injuries() -> pd.DataFrame:", start)
if start < 0 or end < 0:
    raise SystemExit("ESPN depth loader boundaries not found")

new_loader = r'''@st.cache_resource(ttl=86400, show_spinner=False)
def _load_espn_athlete_name_index() -> dict[str, str]:
    """Load ESPN's enriched athlete ID-to-name index once per day.

    ESPN Core depth charts store athlete references rather than embedded names.
    The v3 athlete index returns the names in bulk, avoiding hundreds of
    individual athlete requests every time live depth charts refresh.
    """
    headers = {"User-Agent": "EZPZ-Picks/1.0"}
    names: dict[str, str] = {}
    try:
        with requests.Session() as session:
            session.headers.update(headers)
            first_url = "https://sports.core.api.espn.com/v3/sports/football/nfl/athletes?limit=1000&page=1"
            first_response = session.get(first_url, timeout=45)
            first_response.raise_for_status()
            first_payload = first_response.json()
            if not isinstance(first_payload, dict):
                return names

            page_count = max(1, _int(first_payload.get("pageCount", 1), 1))
            payloads = [first_payload]
            for page in range(2, page_count + 1):
                response = session.get(
                    f"https://sports.core.api.espn.com/v3/sports/football/nfl/athletes?limit=1000&page={page}",
                    timeout=45,
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    payloads.append(payload)

            for payload in payloads:
                for athlete in payload.get("items", []):
                    if not isinstance(athlete, dict):
                        continue
                    athlete_id = _safe_text(athlete.get("id", ""))
                    player_name = _safe_text(
                        athlete.get("displayName", athlete.get("fullName", athlete.get("name", "")))
                    )
                    if athlete_id and player_name:
                        names[athlete_id] = player_name

        st.session_state["nfl_espn_athlete_index_status"] = (
            f"ESPN athlete index loaded {len(names):,} names."
        )
        st.session_state.pop("nfl_espn_athlete_index_error", None)
        return names
    except Exception as exc:
        st.session_state["nfl_espn_athlete_index_error"] = str(exc)
        return names


@st.cache_resource(ttl=900, show_spinner=False)
def _load_espn_depth_charts() -> pd.DataFrame:
    """Load current NFL skill-position depth order from ESPN Core.

    ESPN Core is the live authority for QB/RB/WR/TE ordering. The older
    site.api endpoint is intentionally not used because server-side access is
    denied and previously forced the builder onto stale nflverse ordering.
    nflverse remains the fallback only when ESPN Core is unavailable for a
    specific team or position.
    """
    headers = {"User-Agent": "EZPZ-Picks/1.0"}
    athlete_names = _load_espn_athlete_name_index()
    rows: list[dict[str, Any]] = []
    failed_teams: list[str] = []
    position_map = {"QB": "QB", "RB": "RB", "HB": "RB", "WR": "WR", "TE": "TE"}

    def athlete_id_from_ref(ref: str) -> str:
        ref = _safe_text(ref)
        marker = "/athletes/"
        if marker not in ref:
            return ""
        return ref.split(marker, 1)[1].split("?", 1)[0].strip("/")

    with requests.Session() as session:
        session.headers.update(headers)
        for team in NFL_TEAMS:
            team_id = ESPN_TEAM_IDS.get(team, "")
            if not team_id:
                failed_teams.append(team)
                continue
            url = (
                "https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/"
                f"seasons/{int(DEFAULT_SEASON)}/teams/{team_id}/depthcharts"
            )
            try:
                response = session.get(url, timeout=20)
                response.raise_for_status()
                payload = response.json()
                charts = payload.get("items", []) if isinstance(payload, dict) else []

                # Select the offensive chart by its skill-position coverage, not
                # by a hard-coded formation label such as "3WR 1TE".
                best_positions: dict[str, Any] = {}
                best_score = -1
                for chart in charts:
                    if not isinstance(chart, dict):
                        continue
                    positions = chart.get("positions", {})
                    if not isinstance(positions, dict):
                        continue
                    normalized_keys = {
                        _safe_text(
                            (entry.get("position", {}) or {}).get("abbreviation", key)
                            if isinstance(entry, dict) else key
                        ).upper()
                        for key, entry in positions.items()
                    }
                    score = sum(1 for pos in ["QB", "RB", "WR", "TE"] if pos in normalized_keys)
                    if score > best_score:
                        best_score = score
                        best_positions = positions

                if best_score < 3 or not best_positions:
                    failed_teams.append(team)
                    continue

                before = len(rows)
                for position_key, position_entry in best_positions.items():
                    if not isinstance(position_entry, dict):
                        continue
                    position_info = position_entry.get("position", {})
                    if not isinstance(position_info, dict):
                        position_info = {}
                    raw_position = _safe_text(position_info.get("abbreviation", position_key)).upper()
                    position = position_map.get(raw_position, "")
                    if not position:
                        continue
                    athletes = position_entry.get("athletes", [])
                    if not isinstance(athletes, list):
                        continue

                    for order, athlete_entry in enumerate(athletes, start=1):
                        if not isinstance(athlete_entry, dict):
                            continue
                        athlete_ref = athlete_entry.get("athlete", {})
                        if not isinstance(athlete_ref, dict):
                            athlete_ref = {}
                        ref = _safe_text(athlete_ref.get("$ref", "")).replace("http://", "https://")
                        espn_id = athlete_id_from_ref(ref)
                        player_name = athlete_names.get(espn_id, "")

                        # If a brand-new player has not reached the daily athlete
                        # index yet, resolve only that missing player directly.
                        if not player_name and ref:
                            try:
                                athlete_response = session.get(ref, timeout=10)
                                athlete_response.raise_for_status()
                                athlete = athlete_response.json()
                                if isinstance(athlete, dict):
                                    player_name = _safe_text(
                                        athlete.get("displayName", athlete.get("fullName", athlete.get("name", "")))
                                    )
                            except Exception:
                                player_name = ""
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
                            "depth_source": "ESPN Core current depth chart",
                            "espn_id": espn_id,
                        })

                if len(rows) == before:
                    failed_teams.append(team)
            except Exception as exc:
                failed_teams.append(team)
                st.session_state[f"nfl_espn_depth_error_{team}"] = str(exc)

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
        f"ESPN Core depth charts loaded for {len(loaded_teams)}/32 teams."
        + (f" nflverse fallback used for: {', '.join(sorted(set(failed_teams)))}." if failed_teams else "")
    )
    if loaded_teams:
        st.session_state.pop("nfl_espn_depth_error", None)
    else:
        st.session_state["nfl_espn_depth_error"] = "ESPN Core returned no usable skill depth rows."
    return output.reset_index(drop=True) if not output.empty else pd.DataFrame()
'''

text = text[:start] + new_loader + text[end:]

text = text.replace(
    "Current-season QB/RB/WR/TE ordering comes from ESPN's live depth-chart API.",
    "Current-season QB/RB/WR/TE ordering comes from ESPN Core's live depth chart.",
)

cache_anchor = '''                _load_depth_charts_season, _load_injuries_season, _load_sleeper_players,
                _load_espn_depth_charts, _load_espn_injuries, _load_snap_counts_season,
'''
cache_replacement = '''                _load_depth_charts_season, _load_injuries_season, _load_sleeper_players,
                _load_espn_athlete_name_index, _load_espn_depth_charts, _load_espn_injuries, _load_snap_counts_season,
'''
if cache_anchor in text:
    text = text.replace(cache_anchor, cache_replacement, 1)
elif "_load_espn_athlete_name_index, _load_espn_depth_charts" not in text:
    raise SystemExit("ESPN cache-clear anchor not found")

path.write_text(text, encoding="utf-8")
print("Replaced NFL skill depth authority with ESPN Core and ESPN athlete-name index.")
