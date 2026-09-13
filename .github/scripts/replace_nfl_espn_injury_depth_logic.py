from pathlib import Path

path = Path("builders/nfl_builder.py")
text = path.read_text(encoding="utf-8")

# Persist the automatic replacement-level deduction with lineup snapshots.
old_columns = '''LINEUP_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Team", "Unit", "Slot", "Player", "Position",
    "Depth Rank", "Injury Status", "Auto Play Probability", "Manual Play Probability",
    "Manual Role Share", "Base Impact", "Manual Impact", "Effective Play Probability", "Absence Cost",
    "Model Version",
]'''
new_columns = '''LINEUP_COLUMNS = [
    "Date", "Season", "Week", "Game ID", "Team", "Unit", "Slot", "Player", "Position",
    "Depth Rank", "Depth Downgrade", "Injury Status", "Auto Play Probability", "Manual Play Probability",
    "Manual Role Share", "Base Impact", "Manual Impact", "Effective Play Probability", "Absence Cost",
    "Model Version",
]'''
if text.count(old_columns) != 1:
    raise SystemExit("LINEUP_COLUMNS block not found exactly once")
text = text.replace(old_columns, new_columns, 1)

# Add ESPN's league-wide current injury report as the live injury authority.
anchor = '''\n\n@st.cache_resource(ttl=21600, show_spinner=False)\ndef _load_snap_counts_season(season: int) -> pd.DataFrame:\n'''
if text.count(anchor) != 1:
    raise SystemExit("snap-count insertion anchor not found exactly once")

espn_injury_loader = r'''

@st.cache_resource(ttl=900, show_spinner=False)
def _load_espn_injuries() -> pd.DataFrame:
    """Load ESPN's current NFL injury report every 15 minutes.

    This is the same current-status family that powers ESPN's team injury/depth
    pages. Depth order stays in the ESPN depth-chart loader; this function owns
    live availability labels such as O, Q, D, IR and suspended.
    """
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
    headers = {"User-Agent": "EZPZ-Picks/1.0"}

    def status_text(value: Any) -> str:
        if isinstance(value, dict):
            for key in ["abbreviation", "shortName", "name", "description", "displayName"]:
                candidate = _safe_text(value.get(key, ""))
                if candidate:
                    return candidate
            return ""
        return _safe_text(value)

    def normalize_status(value: Any) -> str:
        raw = status_text(value).strip()
        upper = raw.upper()
        aliases = {
            "O": "Out", "OUT": "Out",
            "Q": "Questionable", "QUESTIONABLE": "Questionable",
            "D": "Doubtful", "DOUBTFUL": "Doubtful",
            "IR": "IR", "INJURED RESERVE": "IR",
            "PUP": "PUP", "PHYSICALLY UNABLE TO PERFORM": "PUP",
            "SSPD": "Suspended", "SUSP": "Suspended", "SUSPENDED": "Suspended",
            "ACTIVE": "Active", "HEALTHY": "Healthy",
        }
        if upper in aliases:
            return aliases[upper]
        for token, normalized in [
            ("INJURED RESERVE", "IR"), ("QUESTIONABLE", "Questionable"),
            ("DOUBTFUL", "Doubtful"), ("SUSPEND", "Suspended"),
            ("OUT", "Out"), ("PUP", "PUP"),
        ]:
            if token in upper:
                return normalized
        return raw.title() if raw else ""

    try:
        response = requests.get(url, headers=headers, timeout=25)
        response.raise_for_status()
        payload = response.json()
        groups = payload.get("injuries", []) if isinstance(payload, dict) else []
        rows: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            team_data = group.get("team", {})
            if not isinstance(team_data, dict):
                team_data = {}
            team = _normalize_team(team_data.get("abbreviation", ""))
            if not team or team not in NFL_TEAMS:
                continue
            injuries = group.get("injuries", [])
            if not isinstance(injuries, list):
                continue
            for item in injuries:
                if not isinstance(item, dict):
                    continue
                athlete = item.get("athlete", {})
                if not isinstance(athlete, dict):
                    athlete = {}
                player_name = _safe_text(
                    athlete.get("displayName", athlete.get("fullName", athlete.get("name", "")))
                )
                if not player_name:
                    continue

                status = normalize_status(item.get("status", ""))
                if not status:
                    candidate = normalize_status(item.get("type", ""))
                    if candidate.upper() in {
                        "OUT", "QUESTIONABLE", "DOUBTFUL", "IR", "PUP", "SUSPENDED"
                    }:
                        status = candidate
                if not status:
                    status = "Questionable"

                details = item.get("details", {})
                if not isinstance(details, dict):
                    details = {}
                injury = (
                    _safe_text(details.get("type", ""))
                    or _safe_text(details.get("detail", ""))
                    or _safe_text(item.get("shortComment", ""))
                    or _safe_text(item.get("longComment", ""))
                )
                rows.append({
                    "team_norm": team,
                    "player_name_display": player_name,
                    "name_norm": _normalize_name(player_name),
                    "status": status,
                    "injury": injury,
                    "source": "ESPN current injury report",
                })

        output = pd.DataFrame(rows)
        if output.empty:
            st.session_state["nfl_espn_injury_status"] = "ESPN returned no current NFL injury rows."
            return output
        output = output[
            output["team_norm"].isin(NFL_TEAMS)
            & output["name_norm"].astype(str).str.len().gt(0)
        ].drop_duplicates(["team_norm", "name_norm"], keep="last")
        st.session_state["nfl_espn_injury_status"] = f"ESPN current injuries loaded for {len(output):,} players."
        st.session_state.pop("nfl_espn_injury_error", None)
        return output.reset_index(drop=True)
    except Exception as exc:
        st.session_state["nfl_espn_injury_error"] = str(exc)
        st.session_state["nfl_espn_injury_status"] = "ESPN injuries unavailable; nflverse/Sleeper fallback used."
        return pd.DataFrame()
'''
text = text.replace(anchor, espn_injury_loader + anchor, 1)

# Replace injury merge wholesale. ESPN current injuries override the older
# sources only for the live season; historical/backtest seasons remain untouched.
start = text.find("def _injury_lookup(season: int, week: int) -> dict[tuple[str, str], dict[str, Any]]:")
end = text.find("\ndef _status_probability(status: Any) -> float:", start)
if start < 0 or end < 0:
    raise SystemExit("_injury_lookup function boundaries not found")
new_injury_lookup = r'''def _injury_lookup(season: int, week: int) -> dict[tuple[str, str], dict[str, Any]]:
    """Merge nflverse/Sleeper fallbacks with ESPN as live-season authority."""
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    injuries = _load_injuries_season(season)
    if not injuries.empty:
        out = injuries.copy()
        out["team_norm"] = _column(out, "team", default="").map(_normalize_team)
        out["name_norm"] = _column(out, "full_name", "player_name", default="").map(_normalize_name)
        if "week" in out.columns:
            week_values = pd.to_numeric(out["week"], errors="coerce")
            eligible = out[week_values <= int(week)].copy()
            if not eligible.empty:
                out = eligible
                latest_week = out.groupby(["team_norm", "name_norm"])["week"].transform("max")
                out = out[pd.to_numeric(out["week"], errors="coerce") == pd.to_numeric(latest_week, errors="coerce")]
        for _, row in out.iterrows():
            key = (_normalize_team(row.get("team_norm", "")), _normalize_name(row.get("name_norm", "")))
            status = _safe_text(_first_existing(row, "report_status", "practice_status", default="")).upper()
            lookup[key] = {
                "status": status.title() if status else "Healthy",
                "injury": _safe_text(_first_existing(row, "report_primary_injury", "practice_primary_injury", default="")),
                "auto_probability": _status_probability(status),
                "source": "nflverse injury report",
            }

    sleeper = _load_sleeper_players()
    if not sleeper.empty:
        for _, row in sleeper.iterrows():
            team = _normalize_team(row.get("team", ""))
            name = _normalize_name(row.get("full_name", ""))
            if not team or not name:
                continue
            injury_status = _safe_text(row.get("injury_status", ""))
            practice = _safe_text(row.get("practice_participation", ""))
            roster_status = _safe_text(row.get("status", ""))
            status = injury_status or practice or roster_status or "Healthy"
            if roster_status.upper() == "ACTIVE" and not injury_status and not practice:
                status = "Healthy"
            injury = _safe_text(row.get("injury_body_part", "")) or _safe_text(row.get("injury_notes", ""))
            lookup[(team, name)] = {
                "status": status.title(),
                "injury": injury,
                "auto_probability": _status_probability(status),
                "source": "Sleeper daily players",
            }

    # ESPN's current injury page is the final authority for the current season.
    if int(season) == int(DEFAULT_SEASON):
        espn = _load_espn_injuries()
        if espn is not None and not espn.empty:
            for _, row in espn.iterrows():
                team = _normalize_team(row.get("team_norm", ""))
                name = _normalize_name(row.get("name_norm", row.get("player_name_display", "")))
                status = _safe_text(row.get("status", "")) or "Questionable"
                if not team or not name:
                    continue
                lookup[(team, name)] = {
                    "status": status,
                    "injury": _safe_text(row.get("injury", "")),
                    "auto_probability": _status_probability(status),
                    "source": "ESPN current injury report",
                }
    return lookup

'''
text = text[:start] + new_injury_lookup + text[end:]

# Make depth selection availability-aware. Definite outs are skipped and the
# next healthy player is promoted while preserving the original depth rank.
start = text.find("def _slot_player(\n")
end = text.find("\ndef _auto_lineup(\n", start)
if start < 0 or end < 0:
    raise SystemExit("_slot_player function boundaries not found")
new_slot_player = r'''def _slot_player(
    depth: pd.DataFrame,
    team: str,
    position_options: list[str],
    occurrence: int,
    injury_lookup: dict[tuple[str, str], dict[str, Any]],
    used_names: set[str],
) -> tuple[str, str, int, list[str]]:
    if depth is None or depth.empty:
        return "", position_options[0], occurrence + 1, []
    team_norm = _normalize_team(team)
    team_rows = depth[depth["team_norm"] == team_norm].copy()
    if team_rows.empty:
        return "", position_options[0], occurrence + 1, []
    rows = team_rows[team_rows["pos_norm"].isin([p.upper() for p in position_options])].copy()
    if rows.empty:
        return "", position_options[0], occurrence + 1, []
    rows = rows.sort_values(["pos_rank_num", "pos_slot_num", "player_name_display"])
    available = rows[~rows["player_name_display"].map(_normalize_name).isin(used_names)]
    if available.empty:
        available = rows

    skipped_unavailable: list[str] = []
    for _, row in available.iterrows():
        player = _safe_text(row.get("player_name_display", ""))
        name = _normalize_name(player)
        if not name:
            continue
        injury = injury_lookup.get((team_norm, name), {})
        play_probability = _num(injury.get("auto_probability", 1.0), 1.0)
        if play_probability <= 0.05:
            # Mark the inactive player used so a later RB/WR slot does not try
            # to recycle the same unavailable player.
            used_names.add(name)
            skipped_unavailable.append(name)
            continue
        return (
            player,
            _safe_text(row.get("pos_norm", position_options[0])),
            _int(row.get("pos_rank_num", occurrence + 1), occurrence + 1),
            skipped_unavailable,
        )

    # If every listed player is unavailable, retain the top listed player so the
    # normal zero play-probability absence logic still flags the position.
    fallback = available.iloc[0]
    player = _safe_text(fallback.get("player_name_display", ""))
    return (
        player,
        _safe_text(fallback.get("pos_norm", position_options[0])),
        _int(fallback.get("pos_rank_num", occurrence + 1), occurrence + 1),
        [],
    )

'''
text = text[:start] + new_slot_player + text[end:]

start = text.find("def _auto_lineup(\n")
end = text.find("\ndef _finalize_lineup(\n", start)
if start < 0 or end < 0:
    raise SystemExit("_auto_lineup function boundaries not found")
new_auto_lineup = r'''def _auto_lineup(
    team: str,
    season: int,
    week: int,
    depth: pd.DataFrame,
    injury_lookup: dict[tuple[str, str], dict[str, Any]],
    player_values: dict[str, float],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    position_seen: dict[str, int] = {}
    for unit, slot_specs in [("Offense", OFFENSE_SLOTS), ("Defense", DEFENSE_SLOTS)]:
        for slot, positions in slot_specs:
            family = positions[0]
            occurrence = position_seen.get(family, 0)
            player, position, depth_rank, skipped_unavailable = _slot_player(
                depth, team, positions, occurrence, injury_lookup, used
            )
            position_seen[family] = occurrence + 1
            if player:
                used.add(_normalize_name(player))
            injury = injury_lookup.get((_normalize_team(team), _normalize_name(player)), {})
            status = injury.get("status", "Healthy") if player else "Unknown"
            auto_probability = injury.get("auto_probability", 1.0 if player else 0.75)
            base = POSITION_BASE_IMPACT.get(slot, 0.35)
            player_value = player_values.get(_normalize_name(player), 0.0)
            if slot == "QB" and player_value > 0:
                base = player_value
            elif slot.startswith("RB") and player_value > 0:
                base = max(base, player_value * (1.0 if slot == "RB1" else 0.45))
            elif slot.startswith("WR") and player_value > 0:
                multiplier = {"WR1": 1.0, "WR2": 0.75, "WR3": 0.50}.get(slot, 1.0)
                base = max(base, player_value * multiplier)
            elif slot == "TE" and player_value > 0:
                base = max(base, player_value)

            # A healthy backup replacing a definite out should still cost the
            # team points. Use the existing position impact as the cap, with the
            # skipped depth count as a fallback and historical player-value gap
            # as a refinement when both players have meaningful data.
            position_cap = POSITION_BASE_IMPACT.get(slot, 0.35)
            fallback_penalty = position_cap * min(0.85, 0.30 * len(skipped_unavailable))
            known_skipped_values = [
                _num(player_values.get(name, 0.0), 0.0)
                for name in skipped_unavailable
                if _num(player_values.get(name, 0.0), 0.0) > 0
            ]
            value_gap = 0.0
            if player_value > 0 and known_skipped_values:
                value_gap = max(0.0, max(known_skipped_values) - player_value) * 0.75
            depth_downgrade = clamp(max(fallback_penalty, value_gap), 0.0, position_cap)

            if depth_rank > 1:
                base *= max(0.45, 1.0 - 0.18 * (depth_rank - 1))
            rows.append({
                "Unit": unit,
                "Slot": slot,
                "Player": player or "TBD",
                "Position": position,
                "Depth Rank": depth_rank,
                "Depth Downgrade": round(depth_downgrade, 3),
                "Injury Status": status,
                "Auto Play Probability": round(auto_probability, 2),
                "Manual Play Probability": np.nan,
                "Manual Role Share": np.nan,
                "Base Impact": round(base, 2),
                "Manual Impact": 0.0,
            })
    return pd.DataFrame(rows)

'''
text = text[:start] + new_auto_lineup + text[end:]

# Count the promoted-backup penalty in the same team-level absence adjustment
# already consumed by game projections and reliability.
old_absence = '''    out["Effective Play Probability"] = effective.round(3)
    out["Absence Cost"] = ((1.0 - effective) * impact).round(3)
'''
new_absence = '''    out["Effective Play Probability"] = effective.round(3)
    depth_downgrade = pd.to_numeric(out.get("Depth Downgrade", 0.0), errors="coerce").fillna(0.0).clip(lower=0.0)
    out["Depth Downgrade"] = depth_downgrade.round(3)
    out["Absence Cost"] = (((1.0 - effective) * impact) + depth_downgrade).round(3)
'''
if text.count(old_absence) != 1:
    raise SystemExit("finalize-lineup absence block not found exactly once")
text = text.replace(old_absence, new_absence, 1)

# Make the automatic depth downgrade visible but read-only in the lineup editor.
old_visible = '''    visible_columns = [
        "Unit", "Slot", "Player", "Position", "Injury Status",
        "Manual Play Probability", "Manual Role Share", "Manual Impact",
    ]'''
new_visible = '''    visible_columns = [
        "Unit", "Slot", "Player", "Position", "Depth Rank", "Depth Downgrade", "Injury Status",
        "Manual Play Probability", "Manual Role Share", "Manual Impact",
    ]'''
if text.count(old_visible) != 1:
    raise SystemExit("lineup editor visible columns block not found exactly once")
text = text.replace(old_visible, new_visible, 1)

old_disabled = '        disabled=["Unit", "Slot"],\n'
new_disabled = '        disabled=["Unit", "Slot", "Depth Rank", "Depth Downgrade"],\n'
if text.count(old_disabled) != 1:
    raise SystemExit("lineup editor disabled list not found exactly once")
text = text.replace(old_disabled, new_disabled, 1)

old_unit_config = '''            "Slot": st.column_config.TextColumn("Slot", disabled=True, width="small"),
            "Player": st.column_config.TextColumn("Player", width="medium"),'''
new_unit_config = '''            "Slot": st.column_config.TextColumn("Slot", disabled=True, width="small"),
            "Depth Rank": st.column_config.NumberColumn("Depth", disabled=True, width="small", format="%d"),
            "Depth Downgrade": st.column_config.NumberColumn(
                "Depth pts", disabled=True, width="small", format="%.2f",
                help="Automatic points deducted when a deeper player is promoted because higher depth-chart players are unavailable.",
            ),
            "Player": st.column_config.TextColumn("Player", width="medium"),'''
if text.count(old_unit_config) != 1:
    raise SystemExit("lineup editor column-config anchor not found exactly once")
text = text.replace(old_unit_config, new_unit_config, 1)

# ESPN injury normalization can return Suspended; keep it selectable for manual overrides.
old_options = '                options=["Healthy", "Active", "Full", "Limited", "Questionable", "Doubtful", "Out", "IR", "PUP", "Unknown"],\n'
new_options = '                options=["Healthy", "Active", "Full", "Limited", "Questionable", "Doubtful", "Out", "IR", "PUP", "Suspended", "Unknown"],\n'
if text.count(old_options) != 1:
    raise SystemExit("injury status options not found exactly once")
text = text.replace(old_options, new_options, 1)

# Refresh controls must clear the new live ESPN caches too.
old_refresh = '''                _load_schedule_live, _load_schedule_csv_fallback, _load_player_stats_season,
                _load_depth_charts_season, _load_injuries_season, _load_sleeper_players, _load_snap_counts_season,
                _load_nextgen_season, _load_ftn_charting_season, _load_participation_season,'''
new_refresh = '''                _load_schedule_live, _load_schedule_csv_fallback, _load_player_stats_season,
                _load_depth_charts_season, _load_injuries_season, _load_sleeper_players,
                _load_espn_depth_charts, _load_espn_injuries, _load_snap_counts_season,
                _load_nextgen_season, _load_ftn_charting_season, _load_participation_season,'''
if text.count(old_refresh) != 1:
    raise SystemExit("force-refresh loader block not found exactly once")
text = text.replace(old_refresh, new_refresh, 1)

old_caption = '''        st.caption("Current QB/RB/WR/TE teams and depth order use the daily Sleeper player feed, with nflverse depth charts retained for detailed line positions. Injury status is merged automatically; manual play probability remains the final override.")
        if st.session_state.get("nfl_sleeper_error"):
            st.warning("The daily Sleeper roster/injury fallback is unavailable on this run. Verify current teams, starters and play probabilities manually before saving.")'''
new_caption = '''        st.caption("Current QB/RB/WR/TE depth order comes from ESPN. ESPN current injury status is applied to that depth chart automatically, so definite outs are skipped and the next available player is promoted with a depth-based point deduction. nflverse/Sleeper remain fallbacks; manual play probability remains the final override.")
        if st.session_state.get("nfl_espn_depth_error"):
            st.warning("ESPN depth charts are unavailable on this run; nflverse depth fallback is active.")
        if st.session_state.get("nfl_espn_injury_error"):
            st.warning("ESPN current injuries are unavailable on this run; nflverse/Sleeper injury fallback is active.")'''
if text.count(old_caption) != 1:
    raise SystemExit("lineup source caption block not found exactly once")
text = text.replace(old_caption, new_caption, 1)

path.write_text(text, encoding="utf-8")
