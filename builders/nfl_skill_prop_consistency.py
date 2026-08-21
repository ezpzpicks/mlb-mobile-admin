"""Production guardrails for the NFL v4.1 RB/WR regression layer.

Keeps receiving-yard simulation inputs internally consistent after target-volume
regression and caches normalized historical stat frames so the same player data
is not rebuilt for every RB/WR on a slate.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from builders import nfl_skill_prop_regression as skill

_STATS_CACHE: dict[tuple[int, int | None], Any] = {}


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "")) == market), None)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if np.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _install_history_cache() -> None:
    if getattr(skill, "_NORMALIZED_STATS_CACHE_INSTALLED", False):
        return
    original = skill._normalized_stats

    def cached(nfl_builder: Any, season: int, through_week: int | None):
        key = (int(season), int(through_week) if through_week is not None else None)
        if key not in _STATS_CACHE:
            _STATS_CACHE[key] = original(nfl_builder, int(season), through_week)
        return _STATS_CACHE[key]

    skill._normalized_stats = cached
    skill._NORMALIZED_STATS_CACHE_INSTALLED = True


def install_skill_prop_consistency(nfl_builder: Any) -> None:
    """Install post-regression consistency fixes without changing fitted coefficients."""
    _install_history_cache()
    if getattr(nfl_builder, "_SKILL_PROP_CONSISTENCY_INSTALLED", False):
        return

    original = nfl_builder._project_player_markets

    def wrapped(*args, **kwargs):
        rows = original(*args, **kwargs)
        position = ""
        if len(args) >= 2:
            position = str(args[1] or "")
        elif "position" in kwargs:
            position = str(kwargs.get("position") or "")
        pos = nfl_builder._position_group(position)
        if pos not in {"RB", "WR"}:
            return rows

        receiving_yards = _row(rows, "Receiving Yards")
        receptions_market = _row(rows, "Receptions")
        if receiving_yards is None:
            return rows

        new_targets = max(0.0, _num(receiving_yards.get("Projected Targets"), 0.0))
        legacy_targets = _num(receptions_market.get("Projected Targets"), 0.0) if receptions_market else 0.0
        legacy_receptions = _num(receptions_market.get("Projected Receptions"), 0.0) if receptions_market else 0.0

        if legacy_targets > 0:
            catch_rate = float(np.clip(legacy_receptions / legacy_targets, 0.25, 0.94))
        else:
            profile_receptions = _num(receiving_yards.get("Projected Receptions"), 0.0)
            prior_targets = max(new_targets, 0.25)
            catch_rate = float(np.clip(profile_receptions / prior_targets, 0.25, 0.94)) if profile_receptions > 0 else (0.76 if pos == "RB" else 0.64)

        receiving_yards["Projected Receptions"] = round(new_targets * catch_rate, 2)
        note = str(receiving_yards.get("Confluence", "") or "")
        if "live catch-rate" not in note:
            receiving_yards["Confluence"] = f"{note} • live catch-rate {catch_rate:.1%}".strip(" •")
        return rows

    nfl_builder._project_player_markets = wrapped
    nfl_builder._SKILL_PROP_CONSISTENCY_INSTALLED = True
