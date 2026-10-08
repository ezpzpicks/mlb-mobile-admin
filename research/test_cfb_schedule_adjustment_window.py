"""Check the v2.8.1 rolling FBS history at and across the season boundary."""
from __future__ import annotations

import pandas as pd

from builders import cfb_schedule_adjustment as adjustment


def fixture_game(season, week, game_date, away, home, away_score, home_score,
                 *, away_class="FBS", home_class="FBS", completed=True):
    return {
        "Season": season, "Week": week, "Game Date": game_date,
        "Away Team": away, "Home Team": home,
        "Away Score": away_score, "Home Score": home_score,
        "Away Classification": away_class, "Home Classification": home_class,
        "Completed": completed,
    }


class FakeBuilder:
    SCHEDULE_TAB = "schedule"
    SCHEDULE_COLUMNS = []
    DEFAULT_SEASON = 2026

    def __init__(self, games):
        self.games = pd.DataFrame(games)

    def read_sheet(self, tab, columns):
        assert tab == self.SCHEDULE_TAB
        return self.games.copy()

    @staticmethod
    def _canonical_team_name(team):
        return team

    @staticmethod
    def project_matchup(game, ratings, away_personnel, home_personnel, environment):
        return {"away_points": 20.0, "home_points": 30.0, "margin": 10.0, "total": 50.0}


def main() -> None:
    games = [fixture_game(2026, 1, "2026-08-29", "A", "B", 99, 7)]
    games += [
        fixture_game(2026, week, f"2026-09-{week + 1:02d}", "A", f"C{week}",
                     20 + week, 14)
        for week in range(2, 7)
    ]
    games += [
        fixture_game(2026, 7, "2026-10-31", "A", "FCS", 77, 3, home_class="FCS"),
        fixture_game(2026, 8, "2026-11-01", "A", "D", 88, 3, completed=False),
        fixture_game(2027, 1, "2027-08-28", "A", "B", 60, 10),
        fixture_game(2027, 1, "2027-09-01", "A", "E", 27, 17),
        fixture_game(2028, 1, "2028-08-28", "A", "B", 70, 10),
    ]
    builder = FakeBuilder(games)

    week_one = adjustment._read_prior_games(builder, 2027, 1, "2027-08-28")
    assert set(week_one["Season"]) == {2026}
    assert set(week_one["Home Canon"]).isdisjoint({"FCS", "D"})
    assert int(week_one["Away History"].sum()) == 5
    old = week_one.loc[week_one["Home Canon"].eq("B")].iloc[0]
    assert not bool(old["Away History"]) and bool(old["Home History"])

    profiles = adjustment._fit_network(week_one)["profiles"]
    assert all(profile.games <= 5 for profile in profiles.values())
    assert profiles["A"].games == 5
    assert profiles["B"].games == 1
    assert 22 <= profiles["A"].raw_ppg <= 26  # The 99-point sixth game is excluded for A.
    assert abs(profiles["A"].raw_papg - 14.0) < 1e-9

    # The week fallback also carries the last season forward when the date is absent.
    no_date = adjustment._read_prior_games(builder, 2027, 1)
    assert set(no_date["Season"]) == {2026}
    assert adjustment._fit_network(no_date)["profiles"]["A"].games == 5

    week_two = adjustment._read_prior_games(builder, 2027, 2, "2027-09-15")
    a_games = week_two.loc[week_two["Away History"] & week_two["Away Canon"].eq("A")]
    assert len(a_games) == 5
    assert set(a_games["Game Date"]) == {
        "2026-09-05", "2026-09-06", "2026-09-07", "2027-08-28", "2027-09-01"
    }

    adjustment._CONTEXT_CACHE.clear()
    adjustment.install_schedule_adjustment(builder)
    out = builder.project_matchup(
        pd.Series({"Season": 2027, "Week": 1, "Game Date": "2027-08-28",
                   "Away Team": "A", "Home Team": "B"}),
        pd.DataFrame(), None, None, None,
    )
    assert out["schedule_adjustment_applied"] is True
    assert out["schedule_adjustment_away_profile"]["games"] == 5
    assert out["schedule_adjustment_home_profile"]["games"] == 1
    assert out["margin"] == 10.0
    assert abs(out["home_points"] + out["away_points"] - out["total"]) < 1e-9
    adjustment._CONTEXT_CACHE.clear()
    print("CFB five-game cross-season schedule adjustment passed")


if __name__ == "__main__":
    main()
