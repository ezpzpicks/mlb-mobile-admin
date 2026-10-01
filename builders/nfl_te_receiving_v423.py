"""Guarded TE1 Receiving Yards matchup neutralization.

v4.23 changes only TE1 Receiving Yards:
- keep the existing target/opportunity projection;
- keep the existing player efficiency projection;
- remove the broad position matchup adjustment from the final yardage projection;
- remove the exact-slot TE1 matchup adjustment from the final yardage projection.

The historical audit showed the current v4.18 matchup layer materially worsened
recent TE1 Receiving Yards accuracy. All v4.19-v4.22 behavior remains installed
underneath this wrapper and all other markets/slots are untouched.
"""
from __future__ import annotations

import math
import re
from typing import Any

from builders import nfl_slot_matchups as slot_matchups
from builders import nfl_rb_countprops_v422 as v422  # installs v4.19-v4.22 first

MODEL_VERSION = "nfl-v4.23-te1-receiving-matchup-neutral-2026-10-01"

_TOTAL_MATCHUP_RE = re.compile(r"total matchup\s+([+-]?\d+(?:\.\d+)?)%", re.IGNORECASE)
_SLOT_APPLIED_RE = re.compile(
    r"slot matchup .*? applied\s+([+-]?\d+(?:\.\d+)?)%",
    re.IGNORECASE,
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _row(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
    return next((row for row in rows if str(row.get("Market", "") or "") == market), None)


def _matchup_pct(text: str) -> tuple[float | None, str]:
    """Return the matchup percent already applied to the final projection.

    Prefer the recorded total matchup because it includes both broad and exact-slot
    effects. If a row has only an exact-slot note, fall back to that applied percent.
    """
    total_matches = list(_TOTAL_MATCHUP_RE.finditer(text))
    if total_matches:
        return _num(total_matches[-1].group(1), math.nan), "total"

    slot_matches = list(_SLOT_APPLIED_RE.finditer(text))
    if slot_matches:
        return _num(slot_matches[-1].group(1), math.nan), "slot-only"
    return None, "none"


def _neutralize_te1_receiving_matchup(
    nfl_builder: Any,
    rows: list[dict[str, Any]],
    exact_slot: str,
) -> None:
    if exact_slot != "TE1":
        return

    row = _row(rows, "Receiving Yards")
    if row is None:
        return

    text = str(row.get("Confluence", "") or "")
    matchup_pct, source = _matchup_pct(text)
    if matchup_pct is None or not math.isfinite(matchup_pct):
        return

    factor = 1.0 + matchup_pct / 100.0
    if factor <= 0.05 or abs(factor - 1.0) < 1e-9:
        return

    old_projection = max(0.0, _num(row.get("Projection"), 0.0))
    if old_projection <= 0.0:
        return

    # This exactly mirrors the validated audit counterfactual: reverse only the
    # final matchup multiplier. Projected Targets and Efficiency remain untouched,
    # preserving the underlying TE opportunity/efficiency model.
    new_projection = max(0.0, old_projection / factor)
    row["Raw Projection"] = round(new_projection, 2)
    row["Calibration Adjustment"] = 0.0
    row["Projection"] = round(new_projection, 2)
    row["Fair Line"] = nfl_builder._fair_line(new_projection, "Receiving Yards")
    row["_sd"] = nfl_builder._prop_sd(
        "Receiving Yards", new_projection, _num(row.get("Reliability"), 70.0)
    )
    row["Matchup Index"] = 1.0

    note = (
        f"v4.23 TE1 receiving matchup neutralization: reversed {source} matchup "
        f"{matchup_pct:+.1f}% ({old_projection:.2f} -> {new_projection:.2f}); "
        "target opportunity and player efficiency unchanged"
    )
    row["Confluence"] = f"{text.strip()} • {note}".strip(" •")


def _install() -> None:
    if getattr(slot_matchups, "_TE_RECEIVING_V423_PATCHED", False):
        return

    original_apply = slot_matchups._apply_slot_overlay
    original_install = slot_matchups.install_slot_matchup_layer

    def patched_apply(
        nfl_builder: Any,
        rows: list[dict[str, Any]],
        season: int,
        projection_week: int,
        opponent: str,
        slot: str,
        player_profile: dict[str, Any] | None = None,
        profiles_frame: Any = None,
    ) -> list[dict[str, Any]]:
        exact_slot = slot_matchups._slot(slot)
        result = original_apply(
            nfl_builder,
            rows,
            season,
            projection_week,
            opponent,
            slot,
            player_profile,
            profiles_frame,
        )
        _neutralize_te1_receiving_matchup(nfl_builder, result, exact_slot)
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._TE_RECEIVING_V423_PATCHED = True


_install()
