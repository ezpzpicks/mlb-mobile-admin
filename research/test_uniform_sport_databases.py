from __future__ import annotations

import os

from shared import storage
from builders import cfb_builder, nfl_builder
from shared.public_contract import ALL_GAME_TRENDS_TAB, PUBLIC_SPLIT_TAB


def main() -> None:
    # Football must never fall back into MLB's generic database merely because
    # GOOGLE_SHEET_NAME is configured for baseball.
    os.environ["GOOGLE_SHEET_NAME"] = "MLB Model Database - Existing"
    for key in [
        "NFL_GOOGLE_SHEET_NAME", "NFL_GOOGLE_SHEET_ID",
        "CFB_GOOGLE_SHEET_NAME", "CFB_GOOGLE_SHEET_ID",
        "NCAAF_GOOGLE_SHEET_NAME", "NCAAF_GOOGLE_SHEET_ID",
    ]:
        os.environ.pop(key, None)

    assert storage.storage_database_config("NFL")["sheet_name"] == "NFL Model Database"
    assert storage.storage_database_config("CFB")["sheet_name"] == "CFB Model Database"
    assert storage.storage_database_config("")["sheet_name"] == "MLB Model Database - Existing"

    os.environ["NFL_GOOGLE_SHEET_NAME"] = "Custom NFL DB"
    assert storage.storage_database_config("NFL")["sheet_name"] == "Custom NFL DB"
    os.environ.pop("NFL_GOOGLE_SHEET_NAME", None)

    storage.set_storage_sport("NCAAF")
    assert storage.get_storage_sport() == "CFB"
    assert storage.storage_database_name() == "CFB Model Database"

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
