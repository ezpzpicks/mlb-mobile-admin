"""Frozen opening-season margin calibration; no market or result inputs.

The mean fit was selected in the November 2 chronological replay, before the
new-season evaluation. It improves point calibration, not validated betting ROI.
Historical training covered opening fortnights only, so use is explicitly dated.
"""
from __future__ import annotations

from datetime import date
import math

POLICY = "ncaam-opening-margin-2026-11-03-v1"
NEUTRAL_MARGIN_SCALE = 1.122098584690361
HOME_COURT_POINTS = 3.881992388010041
BASELINE_HOME_COURT = 3.2
TRAINING_SEASON = 2024
TRAINING_GAMES = 600
REPLAY_SHA256 = "d5eee52cd17f4de52fb2601a3478c9a6729781ef775b6c3c3893574478eb5524"
EVALUATED_WINDOWS = {2027: (date(2026, 11, 1), date(2026, 11, 14))}


def fitted_margin(neutral_margin, *, neutral_site):
    """Frozen historical fit, also usable by the offline chronological replay."""
    if not math.isfinite(float(neutral_margin)) or type(neutral_site) is not bool:
        raise ValueError("Margin calibration requires a finite margin and verified venue.")
    return NEUTRAL_MARGIN_SCALE * float(neutral_margin) + (0.0 if neutral_site else HOME_COURT_POINTS)


def calibrate_margin(neutral_margin, *, season, game_date, neutral_site):
    """Calibrate team strength symmetrically, then add verified home advantage.

    A stronger away team receives the same neutral-margin scaling as a stronger
    home team. Betting favorite, market line and availability changes are not
    predictors. Unstudied season phases retain the source baseline.
    """
    neutral_margin = float(neutral_margin)
    if not math.isfinite(neutral_margin) or type(neutral_site) is not bool:
        raise ValueError("Margin calibration requires a finite margin and verified venue.")
    day = date.fromisoformat(str(game_date))
    window = EVALUATED_WINDOWS.get(int(season))
    active = bool(window and window[0] <= day <= window[1])
    scale = NEUTRAL_MARGIN_SCALE if active else 1.0
    court = 0.0 if neutral_site else HOME_COURT_POINTS if active else BASELINE_HOME_COURT
    raw_margin = neutral_margin + (0.0 if neutral_site else BASELINE_HOME_COURT)
    margin = fitted_margin(neutral_margin, neutral_site=neutral_site) if active else raw_margin
    return {
        "policy": POLICY, "active": active,
        "reason": "Frozen opening-fortnight mean calibration" if active else "Outside the evaluated opening-fortnight window; source baseline retained",
        "game_date": day.isoformat(), "neutral_margin_scale": scale,
        "home_court_points": court, "source_home_margin": raw_margin,
        "calibrated_home_margin": margin, "margin_change": margin - raw_margin,
        "training_season": TRAINING_SEASON, "training_games": TRAINING_GAMES,
        "replay_sha256": REPLAY_SHA256,
        "betting_policy": "Research; betting advantage has not been established",
    }
