"""Regression tests for NFL anytime-touchdown engine safeguards."""
from __future__ import annotations

import pandas as pd

from builders import nfl_builder
import builders.nfl_slot_matchups as slot_model


def test_zero_td_rate_is_valid_evidence() -> None:
    prior = 0.078
    regressed = nfl_builder._regressed_rate(0.0, 60.0, prior, 90.0)
    expected = (0.0 * 60.0 + prior * 90.0) / 150.0
    assert abs(regressed - expected) < 1e-12
    assert regressed < prior


def test_team_td_normalization_only_scales_down() -> None:
    rows = [
        {"Market": "Anytime TD", "Projection": 0.80, "Raw Projection": 0.80, "Reliability": 80.0, "Confluence": ""},
        {"Market": "Anytime TD", "Projection": 0.70, "Raw Projection": 0.70, "Reliability": 80.0, "Confluence": ""},
        {"Market": "Anytime TD", "Projection": 0.60, "Raw Projection": 0.60, "Reliability": 80.0, "Confluence": ""},
        {"Market": "Receiving Yards", "Projection": 72.0, "Raw Projection": 72.0, "Reliability": 80.0, "Confluence": ""},
    ]
    out = nfl_builder._normalize_team_anytime_td_rows(rows, 1.50)
    td_total = sum(float(r["Projection"]) for r in out if r["Market"] == "Anytime TD")
    assert abs(td_total - 1.50) < 0.001
    assert out[-1]["Projection"] == 72.0
    assert all("team TD normalization" in r["Confluence"] for r in out[:3])

    under = [
        {"Market": "Anytime TD", "Projection": 0.30, "Raw Projection": 0.30, "Reliability": 80.0, "Confluence": ""},
        {"Market": "Anytime TD", "Projection": 0.40, "Raw Projection": 0.40, "Reliability": 80.0, "Confluence": ""},
    ]
    unchanged = nfl_builder._normalize_team_anytime_td_rows(under, 2.00)
    assert [r["Projection"] for r in unchanged] == [0.30, 0.40]


def test_one_game_td_matchup_signal_is_disabled() -> None:
    rows = []
    slots = ["QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1"]
    baselines = {"QB": 0.15, "RB1": 0.45, "RB2": 0.15, "WR1": 0.45, "WR2": 0.35, "WR3": 0.20, "TE1": 0.25}
    defenses = ["NYG", "DAL", "PHI", "WAS", "GB", "MIN", "DET", "CHI"]

    for idx, defense in enumerate(defenses):
        for slot in slots:
            actual = baselines[slot]
            if defense == "NYG" and slot == "TE1":
                actual = 1.0
            rows.append({
                "season": 2026, "week": 1, "team": f"T{idx}", "opponent": defense,
                "slot": slot, "player": f"{slot}{idx}", "market": "Anytime TD", "actual": actual,
            })

    history = pd.DataFrame(rows)
    profile = slot_model._profile_from_history(history, "NYG", "TE1", "Anytime TD")
    assert int(profile["sample"]) == 1
    assert profile["early_sample_maturity"] == 0.0
    assert profile["adjustment_pct"] == 0.0


if __name__ == "__main__":
    test_zero_td_rate_is_valid_evidence()
    test_team_td_normalization_only_scales_down()
    test_one_game_td_matchup_signal_is_disabled()
    print("NFL ATD engine tests passed")
