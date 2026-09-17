from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from builders import cfb_interactive_recovery as recovery
from shared import turso_storage as turso


class FakeBuilder:
    SLATE_TAB = "daily_slate"
    TRACKER_TAB = "bet_tracker"
    PERSONNEL_TAB = "personnel_snapshots"

    SLATE_COLUMNS = ["Date", "Season", "Week", "Game ID", "Game", "Spread Grade"]
    TRACKER_COLUMNS = ["Date", "Season", "Week", "Game ID", "Game", "Bet Type", "Selection", "Grade"]
    PERSONNEL_COLUMNS = ["Date", "Season", "Week", "Game ID", "Team", "Expected QB"]

    def __init__(self) -> None:
        self.st = SimpleNamespace(session_state={})

    def slate_row(self, result):
        game = result["game"]
        return pd.DataFrame(
            [
                {
                    "Date": "2026-09-17",
                    "Season": game["Season"],
                    "Week": game["Week"],
                    "Game ID": game["Game ID"],
                    "Game": f"{game['Away Team']} @ {game['Home Team']}",
                    "Spread Grade": "A Spread",
                }
            ]
        )

    def tracker_rows(self, result, include_no_plays=False):
        game = result["game"]
        return pd.DataFrame(
            [
                {
                    "Date": "2026-09-17",
                    "Season": game["Season"],
                    "Week": game["Week"],
                    "Game ID": game["Game ID"],
                    "Game": f"{game['Away Team']} @ {game['Home Team']}",
                    "Bet Type": "Spread",
                    "Selection": f"{game['Home Team']} -3",
                    "Grade": "A Spread",
                }
            ]
        )

    def personnel_row(self, personnel, team, season, week, game_id):
        return pd.DataFrame(
            [
                {
                    "Date": "2026-09-17",
                    "Season": season,
                    "Week": week,
                    "Game ID": game_id,
                    "Team": team,
                    "Expected QB": getattr(personnel, "expected_qb", "QB"),
                }
            ]
        )


def main() -> None:
    builder = FakeBuilder()
    result = {
        "game": {
            "Season": 2026,
            "Week": 4,
            "Game ID": "401000001",
            "Away Team": "Away State",
            "Home Team": "Home State",
        },
        "away_personnel": SimpleNamespace(expected_qb="Away QB"),
        "home_personnel": SimpleNamespace(expected_qb="Home QB"),
    }

    calls: list[dict] = []
    original_pipeline = turso._pipeline
    original_invalidate = turso._invalidate_dataset_cache
    original_mark = turso._mark_dataset_known
    try:
        def fake_pipeline(requests, timeout=30.0, max_attempts=4):
            calls.append({"requests": requests, "timeout": timeout, "max_attempts": max_attempts})
            return [{} for _ in requests]

        turso._pipeline = fake_pipeline
        turso._invalidate_dataset_cache = lambda *args, **kwargs: None
        turso._mark_dataset_known = lambda *args, **kwargs: None

        saved, plays = recovery._save_result_direct(builder, result, False)
    finally:
        turso._pipeline = original_pipeline
        turso._invalidate_dataset_cache = original_invalidate
        turso._mark_dataset_known = original_mark

    assert saved == 1
    assert plays == 1
    assert len(calls) == 1, "interactive save must use exactly one Turso round trip"
    call = calls[0]
    assert call["timeout"] == 12.0
    assert call["max_attempts"] == 2

    sql = "\n".join(
        request.get("stmt", {}).get("sql", "")
        for request in call["requests"]
        if request.get("type") == "execute"
    )
    assert "BEGIN IMMEDIATE" in sql
    assert "COMMIT" in sql
    assert "daily_slate" in sql
    assert "bet_tracker" in sql
    assert "personnel_snapshots" in sql
    assert "UPDATE dataset_rows" in sql
    assert "WHERE changes()=0" in sql
    assert "SELECT row_index,payload_json FROM dataset_rows" not in sql
    assert "ORDER BY row_index" not in sql

    print("CFB direct save smoke test passed")


if __name__ == "__main__":
    main()
