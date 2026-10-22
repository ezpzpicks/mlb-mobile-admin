from __future__ import annotations

import os
from unittest.mock import patch

import pandas as pd

from shared import storage
from shared.public_contract import ALL_GAME_TRENDS_TAB, PUBLIC_SPLIT_TAB


def check_storage_contract() -> None:
    # Production is Turso-native. Legacy Sheets settings must not redirect any
    # sport into another namespace or bring back the removed Sheets fallback.
    previous = storage.get_storage_sport()
    expected = {"NFL":"NFL", "CFB":"NCAAF", "NCAAF":"NCAAF", "CBB":"NCAAM",
                "NCAAM":"NCAAM", "NBA":"NBA", "MLB":"MLB", "":""}
    try:
        with patch.dict(os.environ, {"GOOGLE_SHEET_NAME":"Legacy MLB DB",
                                   "NFL_GOOGLE_SHEET_NAME":"Legacy custom NFL DB"}):
            for sport, namespace in expected.items():
                config = storage.storage_database_config(sport)
                assert config["dataset_sport"] == namespace
                assert config["sheet_name"] == (f"Turso {namespace}" if namespace else "Turso")
                assert config["sheet_id"] == (f"turso:{namespace}" if namespace else "")
                assert config["namespace"] == ""

        storage.set_storage_sport("NCAAF")
        assert storage.get_storage_sport() == "CFB"
        assert storage.storage_database_name() == "Turso NCAAF"
        columns = ["Game ID", "Snapshot ID"]
        payload = {"Game ID":"synthetic-game", "Snapshot ID":"synthetic-snapshot"}
        with patch.object(storage, "is_turso_ready", return_value=True), \
             patch.object(storage, "append_dataset_rows", return_value=1) as append, \
             patch.object(storage, "read_dataset", return_value=pd.DataFrame([payload])) as read:
            with storage.sport_storage("NBA"):
                assert storage.storage_database_identity() == "turso:NBA"
                assert storage.append_row("nba_research_snapshots", payload, columns)
                assert storage.read_sheet("nba_research_snapshots", columns).iloc[0]["Game ID"] == "synthetic-game"
                with storage.sport_storage("NCAAM"):
                    assert storage.storage_database_identity() == "turso:NCAAM"
                assert storage.get_storage_sport() == "NBA"
            assert storage.get_storage_sport() == "CFB"
            append.assert_called_once_with("NBA", "nba_research_snapshots", [payload], columns)
            read.assert_called_once_with("NBA", "nba_research_snapshots", columns)
    finally:
        storage.set_storage_sport(previous)


def main() -> None:
    from builders import cfb_builder, nfl_builder
    check_storage_contract()

    for builder in [nfl_builder, cfb_builder]:
        assert builder.SLATE_TAB == "daily_slate"
        assert builder.TRACKER_TAB == "bet_tracker"
        assert builder.ALL_GAME_TRENDS_TAB == ALL_GAME_TRENDS_TAB
        assert builder.PUBLIC_SPLIT_TAB == PUBLIC_SPLIT_TAB
        assert builder.ODDS_SNAPSHOT_TAB == "odds_snapshot"

    print("Uniform sport database smoke test passed")
    print("NFL database=", storage.storage_database_config("NFL")["sheet_name"])
    print("CFB database=", storage.storage_database_config("CFB")["sheet_name"])


if __name__ == "__main__":
    main()

