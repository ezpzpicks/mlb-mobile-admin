"""Preserve the evaluated model's inputs/components without changing predictions."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any

import numpy as np

SCHEMA_VERSION = "cfb-components-2026-10-30-v1"


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return str(value)


def research_snapshot(result: dict[str, Any], model_version: str,
                      now: datetime | None = None) -> str:
    """Unknown kickoff/after-start evaluations never become verified pregame data."""
    captured = now or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ValueError("Research capture clock must include a timezone")
    game = result.get("game", {})
    kickoff_text = str(game.get("Game Time", "") or "")
    try:
        kickoff = datetime.fromisoformat(kickoff_text.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            raise ValueError("Unknown kickoff timezone")
        status = "PRE_GAME" if captured < kickoff else "AFTER_START"
    except (ValueError, TypeError):
        status = "KICKOFF_UNKNOWN"
    projection = result.get("projection", {})
    prefixes = ("regression_", "total_regression_", "schedule_adjustment_", "regime_2026_")
    components = {k: v for k, v in projection.items() if k.startswith(prefixes)}
    sim = result.get("simulation", {})
    simulation = {k: sim[k] for k in ("away_mean", "home_mean", "away_p10", "away_p90",
        "home_p10", "home_p90", "margin_residual_sd") if k in sim}
    for key in ("totals", "margins"):
        if key in sim and len(sim[key]):
            simulation[f"{key}_sd"] = float(np.std(sim[key]))
    market_keys = ("pick", "odds", "probability", "push", "model_edge_points", "grade", "price_edge", "ev", "confluence")
    payload = {
        "schemaVersion": SCHEMA_VERSION, "modelVersion": model_version,
        "capturedAt": captured.astimezone(timezone.utc).isoformat(),
        "gameId": str(game.get("Game ID", "")), "gameKickoff": kickoff_text,
        "captureStatus": status,
        "deterministic": {k: projection.get(k) for k in ("away_points", "home_points", "margin", "total", "possessions")},
        "components": components, "simulation": simulation,
        "markets": {name: {k: result.get(source, {}).get(k) for k in market_keys}
            for name, source in (("spread", "spread"), ("total", "total_market"))},
    }
    return json.dumps(_safe(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
