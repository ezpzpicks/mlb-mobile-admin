"""Check that week finals repair saved scores without changing pregame markets."""
from __future__ import annotations

from datetime import date

import pandas as pd

from builders.cfb_result_backfill import missing_final_updates, preserve_saved_finals


def game(game_id: str, day: str, week: int, *, completed=False, away="", home="") -> dict:
    return {
        "Season": "2026", "Season Type": "regular", "Week": str(week),
        "Game Date": day, "Game ID": game_id, "Completed": completed,
        "Away Score": away, "Home Score": home, "Market Total": "55.5",
    }


def event(game_id: str, away: str, home: str, *, completed=True) -> dict:
    return {
        "id": game_id,
        "competitions": [{
            "status": {"type": {"completed": completed}},
            "competitors": [
                {"homeAway": "home", "score": home},
                {"homeAway": "away", "score": away},
            ],
        }],
    }


def main() -> None:
    saved = pd.DataFrame([
        game("final", "2026-09-12", 2),
        game("canceled", "2026-09-12", 2),
        game("already-saved", "2026-09-12", 2, completed=True, away="21", home="17"),
        game("today", "2026-10-08", 6),
        game("future", "2026-10-10", 6),
    ])
    requests = []

    def fetch(season: int, season_type: int, week: int) -> list[dict]:
        requests.append((season, season_type, week))
        assert week == 2
        return [
            event("final", "10", "17"),
            event("canceled", "", "", completed=False),
            event("already-saved", "99", "99"),
            event("today", "30", "40"),
        ]

    changes, stats = missing_final_updates(saved, {2026, 2025}, date(2026, 10, 8), fetch)
    assert stats == {"candidates": 2, "updated": 1, "weeks": 1}, stats
    assert requests == [(2026, 2, 2)], requests
    assert list(changes["Game ID"]) == ["final"]
    assert changes.iloc[0]["Away Score"] == 10.0
    assert changes.iloc[0]["Home Score"] == 17.0
    assert changes.iloc[0]["Market Total"] == "55.5"

    stale_feed = pd.DataFrame([game("final", "2026-09-12", 2)])
    current = pd.concat([saved, changes], ignore_index=True).drop_duplicates(
        ["Season", "Game ID"], keep="last"
    )
    preserved = preserve_saved_finals(current, stale_feed)
    assert bool(preserved.iloc[0]["Completed"])
    assert preserved.iloc[0]["Away Score"] == 10.0
    assert preserved.iloc[0]["Home Score"] == 17.0
    assert preserved.iloc[0]["Market Total"] == "55.5"

    # A genuinely newer final from the feed remains authoritative.
    corrected = pd.DataFrame([game("final", "2026-09-12", 2, completed=True, away=13, home=17)])
    assert preserve_saved_finals(current, corrected).iloc[0]["Away Score"] == 13
    print("CFB final-score backfill and sticky settlement passed")


if __name__ == "__main__":
    main()
