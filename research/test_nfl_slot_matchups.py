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
                _row(week, defense, "TE1", "Receiving Yards", 92.0 if defense == "NYG" else 45.0, team),
                _row(week, defense, "TE_ALL", "Receiving Yards", 72.0 if defense == "NYG" else 55.0, team),
            ])
    return pd.DataFrame(rows)



def build_td_history() -> pd.DataFrame:
    rows = []
    defenses = ["NYG", "DAL", "PHI", "WAS", "GB", "MIN", "DET", "CHI"]
    for week in [1, 2, 3, 4]:
        for index, defense in enumerate(defenses):
            team = f"TD{week}{index}"
            # Keep total tracked anytime TDs near 2.0/game for every defense,
            # but make NYG distribute a much larger share of those TDs to TE.
            te_td = 1.00 if defense == "NYG" else 0.25
            wr1_td = 0.20 if defense == "NYG" else 0.45
            wr2_td = 0.20 if defense == "NYG" else 0.35
            wr3_td = 0.10 if defense == "NYG" else 0.20
            rb1_td = 0.30 if defense == "NYG" else 0.45
            rb2_td = 0.10 if defense == "NYG" else 0.15
            qb_td = 0.10 if defense == "NYG" else 0.15
            rows.extend([
                _row(week, defense, "TE1", "Anytime TD", te_td, team),
                _row(week, defense, "WR1", "Anytime TD", wr1_td, team),
                _row(week, defense, "WR2", "Anytime TD", wr2_td, team),
                _row(week, defense, "WR3", "Anytime TD", wr3_td, team),
                _row(week, defense, "RB1", "Anytime TD", rb1_td, team),
                _row(week, defense, "RB2", "Anytime TD", rb2_td, team),
                _row(week, defense, "QB", "Anytime TD", qb_td, team),
            ])
    return pd.DataFrame(rows)


def main() -> None:
    assert slot_model._slot("TE") == "TE1"
    assert slot_model._slot("TE1") == "TE1"
    assert "TE1" in slot_model.TRACKED_SLOTS and "TE" not in slot_model.TRACKED_SLOTS
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

    # TE1 now compares its exact production against the defense's all-TE
    # allowance, so a primary-TE funnel can add a residual without double counting.
    te = slot_model._profile_from_history(history, "NYG", "TE1", "Receiving Yards")
    assert int(te["sample"]) == 3
    assert te["defense_slot_avg"] > te["league_slot_avg"]
    assert te["family_edge_pct"] > 0
    assert te["slot_outlier_pct"] > 0
    assert te["adjustment_pct"] > 0


    td_history = build_td_history()
    te_td = slot_model._profile_from_history(td_history, "NYG", "TE1", "Anytime TD")
    assert int(te_td["sample"]) == 4
    assert te_td["defense_slot_avg"] > te_td["league_slot_avg"]
    assert te_td["slot_outlier_pct"] > 0.20
    assert 0.02 < te_td["adjustment_pct"] <= slot_model.MARKET_CAP["Anytime TD"]

    # A generally high-TD defense should not create a false slot signal if its
    # TD distribution by slot remains proportional to league expectations.
    proportional = td_history.copy()
    nyg = proportional["opponent"] == "NYG"
    baseline = {"TE1": 0.25, "WR1": 0.45, "WR2": 0.35, "WR3": 0.20, "RB1": 0.45, "RB2": 0.15, "QB": 0.15}
    for slot, value in baseline.items():
        mask = nyg & (proportional["slot"] == slot)
        proportional.loc[mask, "actual"] = value * 1.8
    proportional_te = slot_model._profile_from_history(proportional, "NYG", "TE1", "Anytime TD")
    assert abs(proportional_te["adjustment_pct"]) < 0.01

    high_rz_te = {
        "target_share": 0.16,
        "redzone_target_share": 0.30,
        "inside_10_target_share": 0.34,
        "endzone_target_share": 0.38,
    }
    low_rz_te = {
        "target_share": 0.16,
        "redzone_target_share": 0.08,
        "inside_10_target_share": 0.07,
        "endzone_target_share": 0.06,
    }
    high_usage = slot_model._touchdown_usage_multiplier(high_rz_te, "TE1")
    low_usage = slot_model._touchdown_usage_multiplier(low_rz_te, "TE1")
    assert high_usage["usage_ratio"] > 1.5
    assert high_usage["multiplier"] > 1.0
    assert low_usage["usage_ratio"] < 0.7
    assert low_usage["multiplier"] < 1.0

    print("NFL slot matchup tests passed")


if __name__ == "__main__":
    main()
