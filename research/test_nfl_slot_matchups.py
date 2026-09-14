from __future__ import annotations

import pandas as pd

from builders import nfl_slot_matchups as slot_model


def _row(week: int, defense: str, slot: str, market: str, actual: float, team: str) -> dict:
    return {
        "season": 2026,
        "week": week,
        "team": team,
        "opponent": defense,
        "slot": slot,
        "player": f"{team}-{slot}",
        "market": market,
        "actual": actual,
    }


def build_history() -> pd.DataFrame:
    rows = []
    defenses = ["NYG", "DAL", "PHI", "WAS", "GB", "MIN", "DET", "CHI"]
    for week in [1, 2, 3]:
        for index, defense in enumerate(defenses):
            team = f"T{week}{index}"
            # League WR baselines sit near 60/45/30. NYG is ordinary against
            # WR1/WR3 but materially soft against WR2.
            wr1 = 61.0 if defense == "NYG" else 60.0
            wr2 = 82.0 if defense == "NYG" else 45.0
            wr3 = 31.0 if defense == "NYG" else 30.0
            rows.extend([
                _row(week, defense, "WR1", "Receiving Yards", wr1, team),
                _row(week, defense, "WR2", "Receiving Yards", wr2, team),
                _row(week, defense, "WR3", "Receiving Yards", wr3, team),
                _row(week, defense, "WR1", "Targets", 8.0, team),
                _row(week, defense, "WR2", "Targets", 10.0 if defense == "NYG" else 6.0, team),
                _row(week, defense, "WR3", "Targets", 4.0, team),
            ])
    return pd.DataFrame(rows)


def main() -> None:
    history = build_history()

    wr2 = slot_model._profile_from_history(history, "NYG", "WR2", "Receiving Yards")
    assert int(wr2["sample"]) == 3
    assert wr2["defense_slot_avg"] > wr2["league_slot_avg"]
    assert wr2["slot_outlier_pct"] > 0.20
    assert 0.03 < wr2["adjustment_pct"] <= slot_model.MARKET_CAP["Receiving Yards"]

    wr1 = slot_model._profile_from_history(history, "NYG", "WR1", "Receiving Yards")
    assert abs(wr1["adjustment_pct"]) < abs(wr2["adjustment_pct"])

    targets = slot_model._profile_from_history(history, "NYG", "WR2", "Targets")
    assert targets["adjustment_pct"] > 0
    assert targets["adjustment_pct"] <= slot_model.MARKET_CAP["Targets"]

    # If a defense is equally weak against every WR slot, the broad WR matchup
    # already captures it and this layer should not double count it.
    broad = history.copy()
    broad.loc[(broad["opponent"] == "NYG") & (broad["market"] == "Receiving Yards") & (broad["slot"] == "WR1"), "actual"] = 84.0
    broad.loc[(broad["opponent"] == "NYG") & (broad["market"] == "Receiving Yards") & (broad["slot"] == "WR2"), "actual"] = 63.0
    broad.loc[(broad["opponent"] == "NYG") & (broad["market"] == "Receiving Yards") & (broad["slot"] == "WR3"), "actual"] = 42.0
    broad_wr2 = slot_model._profile_from_history(broad, "NYG", "WR2", "Receiving Yards")
    assert abs(broad_wr2["adjustment_pct"]) < 0.02

    # Single-slot positions are already handled by the broad position layer.
    te_rows = pd.DataFrame([
        _row(1, "NYG", "TE", "Receiving Yards", 90.0, "A"),
        _row(1, "DAL", "TE", "Receiving Yards", 45.0, "B"),
        _row(1, "PHI", "TE", "Receiving Yards", 45.0, "C"),
        _row(1, "WAS", "TE", "Receiving Yards", 45.0, "D"),
        _row(1, "GB", "TE", "Receiving Yards", 45.0, "E"),
        _row(1, "MIN", "TE", "Receiving Yards", 45.0, "F"),
        _row(1, "DET", "TE", "Receiving Yards", 45.0, "G"),
        _row(1, "CHI", "TE", "Receiving Yards", 45.0, "H"),
    ])
    te = slot_model._profile_from_history(te_rows, "NYG", "TE", "Receiving Yards")
    assert te["adjustment_pct"] == 0.0

    print("NFL slot matchup tests passed")


if __name__ == "__main__":
    main()
