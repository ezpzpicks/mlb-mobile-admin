"""Lazy builder package hooks.

The CFB hook keeps the college-football builder usable during transient ESPN
outages without ever crossing into another sport's workbook. MLB, NFL, NCAAM,
and CFB continue to use their own storage contracts.
"""
from __future__ import annotations

import importlib
import math
from datetime import date, timedelta
from typing import Any


def _patch_cfb_builder(module: Any) -> Any:
    if getattr(module, "_ezpz_cfb_persistence_patch", False):
        return module

    try:
        from shared.storage import get_storage_sport
    except Exception:
        get_storage_sport = lambda: ""  # type: ignore[assignment]

    original_ensure_schedule = module._ensure_automatic_schedule
    original_get_cached_ratings = module._get_cached_ratings
    original_ratings_are_fresh = module._ratings_are_fresh

    def _cfb_storage_active() -> bool:
        try:
            return str(get_storage_sport() or "").upper() == "CFB"
        except Exception:
            return False

    def _set_schedule_warning(message: str) -> None:
        try:
            module.st.session_state["cfb_auto_schedule_warning"] = str(message)
        except Exception:
            pass

    def _clear_schedule_warning() -> None:
        try:
            module.st.session_state.pop("cfb_auto_schedule_warning", None)
        except Exception:
            pass

    def _set_ratings_warning(message: str) -> None:
        try:
            module.st.session_state["cfb_auto_ratings_warning"] = str(message)
        except Exception:
            pass

    def _public_json_get(
        url: str,
        params: dict[str, Any] | None = None,
        *,
        optional: bool = False,
        max_age: int | None = None,
    ) -> Any:
        """Keep ESPN failures short and visible while preserving stale-cache fallback."""
        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        if max_age is None:
            max_age = int(getattr(module, "CACHE_SECONDS", 21600))
        path = module._cache_path(url, params)
        if path.exists() and (module.time.time() - path.stat().st_mtime) <= max_age:
            try:
                return module.json.loads(path.read_text())
            except Exception:
                pass

        is_espn = "espn.com" in str(url).lower()
        timeout = (3, 8) if is_espn else 60
        try:
            response = module.requests.get(
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "EZPZ-Picks-NCAAF/1.5 (public-data model; contact admin@ezpzpicks.com)",
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            path.write_text(module.json.dumps(payload))
            return payload
        except Exception as exc:
            if path.exists():
                try:
                    return module.json.loads(path.read_text())
                except Exception:
                    pass
            if is_espn and "/scoreboard" in str(url):
                _set_schedule_warning(f"ESPN schedule request failed: {exc}")
            if optional:
                return {} if str(url).endswith(".json") else []
            raise

    module._public_json_get = _public_json_get

    # ESPN's season-wide scoreboard request is not schema/coverage-stable across
    # historical seasons. A zero-row response therefore must NOT be treated as a
    # confirmed empty season. Always fall back to bounded week requests before
    # declaring the source unavailable.
    def _espn_events_uncached(season: int) -> list[dict[str, Any]]:
        events: dict[str, dict[str, Any]] = {}

        def fetch(params: dict[str, Any]) -> list[dict[str, Any]]:
            payload = module._public_json_get(
                f"{module.ESPN_SITE_BASE}/scoreboard",
                params,
                optional=True,
                max_age=21600 if season >= date.today().year else 86400 * 30,
            )
            return payload.get("events", []) if isinstance(payload, dict) else []

        for season_type in (2, 3):
            for event in fetch({"dates": int(season), "limit": 1000, "groups": 80, "seasontype": season_type}):
                event_id = module._text(event.get("id"))
                if event_id:
                    events[event_id] = event

        # Run the bounded week fallback whenever the season-level payload is
        # incomplete, including the important zero-event historical case.
        if len(events) < 100:
            requests_to_make = [
                {"dates": int(season), "limit": 500, "groups": 80, "seasontype": season_type, "week": week}
                for season_type, max_week in ((2, 18), (3, 8))
                for week in range(0, max_week + 1)
            ]
            with module.ThreadPoolExecutor(max_workers=8, thread_name_prefix="ezpz-cfb-espn") as executor:
                futures = [executor.submit(fetch, params) for params in requests_to_make]
                for future in module.as_completed(futures):
                    try:
                        for event in future.result():
                            event_id = module._text(event.get("id"))
                            if event_id:
                                events[event_id] = event
                    except Exception:
                        continue

        if not events:
            _set_schedule_warning(
                "ESPN returned no usable season or week schedule. The builder used the CFB database fallback and did not create neutral team ratings."
            )
        return list(events.values())

    try:
        module._espn_events = module.st.cache_data(ttl=21600, show_spinner=False)(_espn_events_uncached)
    except Exception:
        module._espn_events = _espn_events_uncached

    def _week_from_date(game_date: date) -> int:
        # Week 0 is the final Saturday of August; the following Saturday is Week 1.
        august_last = date(game_date.year, 8, 31)
        week_zero = august_last - timedelta(days=(august_last.weekday() - 5) % 7)
        return max(0, int((game_date - week_zero).days // 7))

    def _trend_schedule_fallback(season: int) -> Any:
        """Build a minimal schedule only from this CFB database's trend table."""
        if not _cfb_storage_active():
            return module.pd.DataFrame(columns=module.SCHEDULE_COLUMNS)
        try:
            trends = module._sheet(module.ALL_GAME_TRENDS_TAB, module.ALL_GAME_TRENDS_COLUMNS)
        except Exception:
            return module.pd.DataFrame(columns=module.SCHEDULE_COLUMNS)
        if trends is None or trends.empty:
            return module.pd.DataFrame(columns=module.SCHEDULE_COLUMNS)

        frame = trends.copy()
        parsed_dates = module.pd.to_datetime(frame.get("Date"), errors="coerce")
        frame = frame[parsed_dates.dt.year == int(season)].copy()
        if frame.empty:
            return module.pd.DataFrame(columns=module.SCHEDULE_COLUMNS)

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for _, row in frame.iterrows():
            game_id = module._text(row.get("Game Key"))
            away = module._text(row.get("Away Team"))
            home = module._text(row.get("Home Team"))
            date_text = module._text(row.get("Date"))
            if not game_id or not away or not home or not date_text or game_id in seen:
                continue
            try:
                game_day = module.pd.to_datetime(date_text, errors="raise").date()
            except Exception:
                continue
            seen.add(game_id)
            item = {column: "" for column in module.SCHEDULE_COLUMNS}
            item.update(
                {
                    "Season": int(season),
                    "Week": _week_from_date(game_day),
                    "Season Type": 2,
                    "Game Date": game_day.isoformat(),
                    "Game Time": module._text(row.get("Game Time")),
                    "Away Team": away,
                    "Home Team": home,
                    "Completed": False,
                    "Neutral Site": False,
                    "Conference Game": False,
                    "Line Provider": "CFB all_game_trends fallback",
                    "Game ID": game_id,
                }
            )
            rows.append(item)

        if not rows:
            return module.pd.DataFrame(columns=module.SCHEDULE_COLUMNS)
        return module.pd.DataFrame(rows, columns=module.SCHEDULE_COLUMNS).sort_values(["Week", "Game Date", "Game Time"])

    def _ensure_automatic_schedule(season: int, provider: str = "", force: bool = False) -> Any:
        """Use only the CFB workbook for durable/fallback schedule state."""
        schedule = original_ensure_schedule(season, provider, force)
        if schedule is not None and not schedule.empty:
            _clear_schedule_warning()
            return schedule

        fallback = _trend_schedule_fallback(season)
        if fallback is not None and not fallback.empty:
            provider_key = module.hashlib.sha1(provider.strip().lower().encode()).hexdigest()[:8]
            session_key = f"cfb_auto_schedule_{season}_{provider_key}"
            try:
                module.st.session_state[session_key] = fallback.copy()
            except Exception:
                pass
            _set_schedule_warning(
                "ESPN schedule was unavailable, so the builder loaded the current slate from this CFB database's all_game_trends table. Force Refresh will retry the full ESPN schedule."
            )
            return fallback
        return schedule

    module._ensure_automatic_schedule = _ensure_automatic_schedule

    def _clean_team_name(value: Any) -> str:
        text = module._normalize_team(value)
        return "" if text.lower() in {"", "nan", "none", "null"} else text

    def _valid_team_frame(frame: Any, *, minimum: int = 20) -> bool:
        if frame is None or getattr(frame, "empty", True) or "Team" not in frame.columns:
            return False
        names = frame["Team"].map(_clean_team_name)
        return int(names.ne("").sum()) >= int(minimum)

    def _ratings_look_real(frame: Any) -> bool:
        if not _valid_team_frame(frame):
            return False
        view = frame[frame["Team"].map(_clean_team_name).ne("")].copy()
        for column in ("Power Rating", "Preseason Rating", "Points Per Game"):
            if column not in view.columns:
                continue
            values = module.pd.to_numeric(view[column], errors="coerce").dropna()
            if len(values) >= 20 and float(values.std(ddof=0)) > 0.05:
                return True
        return False

    def _get_cached_ratings(season: int, week: int) -> Any:
        frame = original_get_cached_ratings(season, week)
        if frame is None or frame.empty or "Team" not in frame.columns:
            return frame
        names = frame["Team"].map(_clean_team_name)
        return frame[names.ne("")].copy()

    def _ratings_are_fresh(frame: Any, max_age_seconds: int | None = None) -> bool:
        # Never allow the old one-row neutral fallback to become a six-hour
        # "fresh" cache entry. A bad data pull now fails closed and retries.
        if not _ratings_look_real(frame):
            return False
        if max_age_seconds is None:
            return bool(original_ratings_are_fresh(frame))
        return bool(original_ratings_are_fresh(frame, max_age_seconds))

    module._get_cached_ratings = _get_cached_ratings
    module._ratings_are_fresh = _ratings_are_fresh

    edge_sources = {
        "EPA/PPA Defense Edge": "EPA/PPA Defense Raw",
        "Success Rate Defense Edge": "Success Rate Defense Raw",
        "Pass Defense Edge": "Pass Defense Raw",
        "Rush Defense Edge": "Rush Defense Raw",
        "Explosiveness Defense Edge": "Explosiveness Defense Raw",
        "Finishing Drives Defense Edge": "Finishing Drives Defense Raw",
        "Field Position Defense Edge": "Field Position Defense Raw",
        "Line Yards Defense Edge": "Line Yards Defense Raw",
        "Standard Downs Defense Edge": "Standard Downs Defense Raw",
        "Passing Downs Defense Edge": "Passing Downs Defense Raw",
        "Stuff Rate Edge": "Stuff Rate Defense Raw",
        "Third Down Defense Edge": "Third Down Defense Raw",
        "Red Zone Defense Edge": "Red Zone Defense Raw",
    }

    performance_columns = [
        "Offense Rating", "Defense Rating", "Special Teams Rating",
        "EPA/PPA Offense", "EPA/PPA Defense Edge",
        "Success Rate Offense", "Success Rate Defense Edge",
        "Pass EPA/PPA", "Pass Defense Edge",
        "Rush EPA/PPA", "Rush Defense Edge",
        "Explosiveness Offense", "Explosiveness Defense Edge",
        "Havoc Allowed", "Havoc Created",
        "Finishing Drives Offense", "Finishing Drives Defense Edge",
        "Field Position Offense", "Field Position Defense Edge",
        "Line Yards Offense", "Line Yards Defense Edge",
        "Power Success", "Stuff Rate Edge",
        "Standard Downs Offense", "Standard Downs Defense Edge",
        "Passing Downs Offense", "Passing Downs Defense Edge",
        "Points Per Drive", "Points Allowed Per Drive",
        "Points Per Game", "Points Allowed Per Game",
        "Yards Per Play", "Yards Per Play Allowed",
        "Third Down Rate", "Third Down Defense Edge",
        "Red Zone TD Rate", "Red Zone Defense Edge",
        "Turnover Rate", "Takeaway Rate",
        "Sack Rate Allowed", "Sack Rate Created",
        "Pace Seconds Per Play", "Plays Per Game", "Possessions Per Game",
        "SP+ Rating", "FPI Rating", "Elo Rating", "SRS Rating",
    ]

    roster_columns = [
        "Returning Production", "Returning Passing", "Returning Receiving",
        "Returning Rushing", "Talent Rating", "Recruiting Rating", "Portal Rating",
        "QB Continuity", "Coaching Continuity", "Coordinator Continuity",
    ]

    def _finite(value: Any) -> bool:
        try:
            return math.isfinite(float(value))
        except Exception:
            return False

    def _row_for(frame: Any, team: str) -> dict[str, Any]:
        if frame is None or frame.empty or "Team" not in frame.columns:
            return {}
        subset = frame[frame["Team"].map(_clean_team_name) == team]
        return subset.iloc[-1].to_dict() if not subset.empty else {}

    def _current_metric_available(
        column: str,
        raw_current: dict[str, Any],
        current_avail: dict[str, bool],
        fbs_games: float,
    ) -> bool:
        if fbs_games <= 0:
            return False
        if column in {"Offense Rating", "Defense Rating"}:
            return True
        if column == "Special Teams Rating":
            return bool(current_avail.get("advanced"))
        if column == "Pace Seconds Per Play" and not current_avail.get("advanced"):
            return False
        source = edge_sources.get(column, column)
        return _finite(raw_current.get(source))

    def _build_team_ratings(season: int, week: int) -> Any:
        """Build ratings without letting missing current data erase the prior season."""
        prior_frame, prior_avail = module._season_features(season - 1, None)
        current_frame, current_avail = module._season_features(season, week)

        if prior_frame is None:
            prior_frame = module.pd.DataFrame()
        if current_frame is None:
            current_frame = module.pd.DataFrame()

        if "Team" in prior_frame.columns:
            prior_frame = prior_frame[prior_frame["Team"].map(_clean_team_name).ne("")].copy()
        if "Team" in current_frame.columns:
            current_frame = current_frame[current_frame["Team"].map(_clean_team_name).ne("")].copy()

        # The preseason regression depends on a real prior-season sample. Do not
        # write average-team placeholders if the historical source is unavailable.
        if not _valid_team_frame(prior_frame):
            message = (
                f"CFB {season - 1} prior-season team data did not load. "
                "Ratings were not saved because neutral defaults would flatten every matchup."
            )
            _set_ratings_warning(message)
            raise RuntimeError(message)

        prior_input = prior_frame.copy()
        current_lookup = current_frame.set_index("Team") if not current_frame.empty else module.pd.DataFrame()
        if not current_lookup.empty:
            for column in roster_columns:
                if column in current_lookup.columns:
                    mapping = current_lookup[column].to_dict()
                    fallback = prior_input.get(column, module.pd.Series(index=prior_input.index, dtype=float))
                    prior_input[column] = prior_input["Team"].map(mapping).combine_first(fallback)

        preseason_frame = module._prior_components(prior_input)
        prior_model = module._current_components(prior_frame)
        current_model = module._current_components(current_frame)
        prior_weight, current_weight = module._season_weights(week)

        teams = sorted(
            {
                _clean_team_name(value)
                for frame in (preseason_frame, prior_model, current_model)
                if frame is not None and not frame.empty and "Team" in frame.columns
                for value in frame["Team"].tolist()
                if _clean_team_name(value)
            }
        )
        if len(teams) < 20:
            message = f"Only {len(teams)} usable CFB teams were available for {season} Week {week}; ratings were not saved."
            _set_ratings_warning(message)
            raise RuntimeError(message)

        rows: list[dict[str, Any]] = []
        for team in teams:
            preseason_row = _row_for(preseason_frame, team)
            prior_row = _row_for(prior_model, team)
            current_row = _row_for(current_model, team)
            raw_current = _row_for(current_frame, team)

            preseason = module._num(preseason_row.get("Preseason Rating"), 0.0)
            games = module._num(current_row.get("Games"), 0.0)
            fbs_games = module._num(current_row.get("FBS Games"), 0.0)
            sample_factor = module.clamp(fbs_games / 6.0, 0.0, 1.0)
            effective_current = current_weight * sample_factor
            effective_prior = 1.0 - effective_current
            current_power = module._num(current_row.get("Current Power"), preseason)

            conference = (
                module._text(raw_current.get("Conference"))
                or module._text(preseason_row.get("Conference"))
                or module._text(prior_row.get("Conference"))
            )
            classification = (
                module._text(raw_current.get("Classification"))
                or module._text(preseason_row.get("Classification"))
                or module._text(prior_row.get("Classification"))
                or "fbs"
            )

            data_conf = (
                34.0
                + 28.0 * sample_factor
                + (10.0 if prior_avail.get("advanced") else 0.0)
                + (6.0 if prior_avail.get("roster") else 0.0)
                + (10.0 if current_avail.get("advanced") else 0.0)
                + (8.0 if current_avail.get("roster") else 0.0)
                + min(10.0, fbs_games * 1.5)
            )

            row: dict[str, Any] = {
                "Team": team,
                "Conference": conference,
                "Classification": classification,
                "Season": season,
                "Projection Week": week,
                "Previous Season Weight": round(effective_prior, 3),
                "Current Season Weight": round(effective_current, 3),
                "Preseason Rating": round(preseason, 3),
                "Power Rating": round(effective_prior * preseason + effective_current * current_power, 3),
                "Games": int(games),
                "FBS Games": int(fbs_games),
                "Data Confidence": round(module.clamp(data_conf, 20.0, 98.0), 1),
                "Advanced Data Available": bool(prior_avail.get("advanced") or current_avail.get("advanced")),
                "Roster Data Available": bool(prior_avail.get("roster") or current_avail.get("roster")),
                "Source": "ESPN prior-season performance + progressive current-season blend; SportsDataverse advanced upgrade",
                "Updated": module._now(),
            }

            # At Week 0/1 this remains 100% prior-season performance. As actual
            # FBS games accumulate, only metrics that truly exist in the current
            # raw feed are blended in. Missing current advanced fields no longer
            # overwrite real prior data with neutral constants.
            for column in performance_columns:
                prior_value = prior_row.get(column, preseason_row.get(column))
                current_value = current_row.get(column)
                use_current = (
                    effective_current > 0
                    and _current_metric_available(column, raw_current, current_avail, fbs_games)
                    and _finite(current_value)
                )
                if _finite(prior_value) and use_current:
                    value = effective_prior * float(prior_value) + effective_current * float(current_value)
                elif _finite(prior_value):
                    value = float(prior_value)
                elif use_current:
                    value = float(current_value)
                else:
                    value = module.NEUTRAL.get(column, 0.0)
                row[column] = round(value, 6) if isinstance(value, (int, float)) else value

            # Returning production/roster/continuity are forward-looking inputs,
            # so prefer the upcoming/current-season row even when games=0.
            for column in roster_columns:
                current_value = raw_current.get(column)
                prior_value = preseason_row.get(column, prior_row.get(column))
                if _finite(current_value):
                    row[column] = float(current_value)
                elif _finite(prior_value):
                    row[column] = float(prior_value)
                else:
                    row[column] = module.NEUTRAL.get(column, 0.0)

            for column in module.RATING_COLUMNS:
                if column not in row:
                    candidate = preseason_row.get(column, prior_row.get(column, current_row.get(column, "")))
                    row[column] = candidate
            rows.append(row)

        output = module.pd.DataFrame(rows)
        for column in module.RATING_COLUMNS:
            if column not in output:
                output[column] = ""
        output = output[module.RATING_COLUMNS].sort_values("Power Rating", ascending=False)

        if not _ratings_look_real(output):
            message = (
                f"CFB {season} Week {week} ratings failed validation. "
                "The model refused to save a flat neutral rating table."
            )
            _set_ratings_warning(message)
            raise RuntimeError(message)

        # Replace this season/week atomically so any old blank neutral row is
        # removed instead of surviving as a separate Team=\"\" key.
        existing = module._sheet(module.RATINGS_TAB, module.RATING_COLUMNS)
        if existing is None or existing.empty:
            combined = output
        else:
            keep = existing[
                ~(
                    (existing["Season"].astype(str) == str(season))
                    & (existing["Projection Week"].astype(str) == str(week))
                )
            ].copy()
            combined = module.pd.concat([keep, output], ignore_index=True)
        module._write(module.RATINGS_TAB, combined, module.RATING_COLUMNS)
        try:
            module.st.session_state.pop("cfb_auto_ratings_warning", None)
        except Exception:
            pass
        return output

    module.build_team_ratings = _build_team_ratings
    module._ezpz_cfb_persistence_patch = True
    return module


def __getattr__(name: str) -> Any:
    if name == "cfb_builder":
        module = importlib.import_module(f"{__name__}.cfb_builder")
        module = _patch_cfb_builder(module)
        globals()[name] = module
        return module
    raise AttributeError(name)
