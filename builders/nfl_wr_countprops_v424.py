"""Guarded WR2 Targets/Receptions role calibration layered on v4.23.

v4.24 changes only WR2 Targets and Receptions:
- partially neutralize the live target-role overlay using role^-0.5;
- scale Receptions by the identical target factor so the existing catch rate is preserved;
- do not blend Routes x TPRR and do not add catch-rate shrinkage.

WR1/WR3 and all v4.19-v4.23 behavior remain unchanged underneath this wrapper.
"""
from __future__ import annotations

import math
import re
from typing import Any

from builders import nfl_slot_matchups as slot_matchups
from builders import nfl_te_receiving_v423 as v423  # installs v4.19-v4.23 first

MODEL_VERSION = "nfl-v4.24-wr2-targets-receptions-role-calibration-2026-10-01"
WR2_ROLE_NEUTRALIZATION = 0.50

_ROLE_RE = re.compile(
    r"(?:removed\s+)?live role(?:/injury)? overlay\s+([0-9.]+)x",
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


def _wr_role_overlay(rows: list[dict[str, Any]]) -> float:
    """Read the live-role multiplier recorded on the calibrated Receiving Yards row."""
    receiving = _row(rows, "Receiving Yards")
    if receiving is None:
        return 1.0
    text = str(receiving.get("Confluence", "") or "")
    matches = list(_ROLE_RE.finditer(text))
    if not matches:
        return 1.0
    role = _num(matches[-1].group(1), 1.0)
    return role if role > 0.05 else 1.0


def _scale_field(row: dict[str, Any], key: str, factor: float) -> None:
    value = _num(row.get(key), math.nan)
    if math.isfinite(value) and value >= 0.0:
        decimals = 3 if key == "Targets Per Route" else 2
        row[key] = round(value * factor, decimals)


def _apply_wr2_count_calibration(
    nfl_builder: Any,
    rows: list[dict[str, Any]],
    exact_slot: str,
) -> None:
    if exact_slot != "WR2":
        return

    role_overlay = _wr_role_overlay(rows)
    if role_overlay <= 0.05:
        return

    factor = role_overlay ** (-WR2_ROLE_NEUTRALIZATION)
    if not math.isfinite(factor) or factor <= 0.0 or abs(factor - 1.0) < 1e-9:
        return

    for market in ("Targets", "Receptions"):
        row = _row(rows, market)
        if row is None:
            continue

        old_projection = max(0.0, _num(row.get("Projection"), 0.0))
        if old_projection <= 0.0:
            continue

        _scale_field(row, "Projected Targets", factor)
        _scale_field(row, "Targets Per Route", factor)
        if market == "Receptions":
            _scale_field(row, "Projected Receptions", factor)

        new_projection = max(0.0, old_projection * factor)
        row["Raw Projection"] = round(new_projection, 2)
        row["Calibration Adjustment"] = 0.0
        row["Projection"] = round(new_projection, 2)
        row["Fair Line"] = nfl_builder._fair_line(new_projection, market)
        row["_sd"] = nfl_builder._prop_sd(
            market, new_projection, _num(row.get("Reliability"), 70.0)
        )

        previous = str(row.get("Confluence", "") or "").strip()
        note = (
            f"v4.24 WR2 count-prop role calibration: live role overlay "
            f"{role_overlay:.2f}x half-neutralized with role^-0.5 => {factor:.3f}x; "
            f"{market} {old_projection:.2f} -> {new_projection:.2f}; "
            "Routes x TPRR blend disabled; catch rate and matchup unchanged"
        )
        row["Confluence"] = f"{previous} • {note}".strip(" •")


def _install() -> None:
    if getattr(slot_matchups, "_WR2_COUNTPROPS_V424_PATCHED", False):
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
        _apply_wr2_count_calibration(nfl_builder, result, exact_slot)
        return result

    def patched_install(nfl_builder: Any) -> None:
        original_install(nfl_builder)
        nfl_builder.MODEL_VERSION = MODEL_VERSION

    slot_matchups._apply_slot_overlay = patched_apply
    slot_matchups.install_slot_matchup_layer = patched_install
    slot_matchups._WR2_COUNTPROPS_V424_PATCHED = True


_install()

# Keep v4.24 as the existing loader entry point used by shared/auth.py, then
# layer v4.25 on top without modifying authentication behavior.
import builders.nfl_qb_passing_v425  # noqa: E402,F401
