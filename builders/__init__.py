"""Lazy builder package hooks.

The CFB hook keeps the college-football builder usable during transient ESPN
outages without ever crossing into another sport's workbook. MLB, NFL, NCAAM,
and CFB continue to use their own storage contracts.
"""
from __future__ import annotations

import importlib
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
                    "User-Agent": "EZPZ-Picks-NCAAF/1.4 (public-data model; contact admin@ezpzpicks.com)",
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

    # Rebuild the ESPN season loader with an outage circuit-breaker. The old
    # loader launched roughly 50 week requests even when both season-level
    # requests returned nothing, turning one outage into several minutes of waiting.
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
                events[module._text(event.get("id"))] = event

        # If ESPN is completely unavailable, do not fan out dozens of requests.
        # The schedule wrapper below will immediately fall back to the CFB DB.
        if not events:
            _set_schedule_warning(
                "ESPN returned no season schedule; skipped week-by-week retry storm and used the CFB database fallback."
            )
            return []

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
                            events[module._text(event.get("id"))] = event
                    except Exception:
                        continue
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
    module._ezpz_cfb_persistence_patch = True
    return module


def __getattr__(name: str) -> Any:
    if name == "cfb_builder":
        module = importlib.import_module(f"{__name__}.cfb_builder")
        module = _patch_cfb_builder(module)
        globals()[name] = module
        return module
    raise AttributeError(name)
