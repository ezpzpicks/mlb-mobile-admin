from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one old block, found {count}")
    return text.replace(old, new, 1)


def patch_nfl() -> None:
    path = Path("builders/nfl_builder.py")
    text = path.read_text()
    text = replace_once(
        text,
        "from shared.storage import read_sheet, sheets_ready, write_sheet\n",
        "from shared.storage import get_or_create_worksheet, read_sheet, sheets_ready, write_sheet\n"
        "from shared.public_contract import (\n"
        "    ALL_GAME_TRENDS_COLUMNS, ALL_GAME_TRENDS_TAB, ODDS_SNAPSHOT_COLUMNS,\n"
        "    ODDS_SNAPSHOT_TAB, PUBLIC_SPLIT_COLUMNS, PUBLIC_SPLIT_TAB,\n"
        ")\n",
        "NFL storage/public-contract imports",
    )
    old = '''RATINGS_TAB = "nfl_team_ratings"
SLATE_TAB = "nfl_daily_slate"
TRACKER_TAB = "nfl_bet_tracker"
SCHEDULE_TAB = "nfl_schedule"
LINEUP_TAB = "nfl_lineup_snapshots"
MODEL_LOG_TAB = "nfl_model_change_log"
PROP_SLATE_TAB = "nfl_prop_projections"
PROP_TRACKER_TAB = "nfl_prop_tracker"
PROP_CALIBRATION_TAB = "nfl_prop_calibration"
'''
    new = '''# This model now owns a dedicated NFL workbook. Within that database, public
# tab names mirror MLB so the public site can use one sport-agnostic contract.
RATINGS_TAB = "team_ratings"
SLATE_TAB = "daily_slate"
TRACKER_TAB = "bet_tracker"
SCHEDULE_TAB = "schedule"
LINEUP_TAB = "lineup_snapshots"
MODEL_LOG_TAB = "model_change_log"
PROP_SLATE_TAB = "prop_projections"
PROP_TRACKER_TAB = "prop_tracker"
PROP_CALIBRATION_TAB = "prop_calibration"
'''
    text = replace_once(text, old, new, "NFL tab contract")
    marker = '''def render() -> None:
    st.caption("NFL v4.2 regression slate • price-aware spread/total markets • regression QB/RB/WR yard props")
'''
    replacement = '''def _ensure_public_database_contract() -> None:
    """Create the uniform public tabs in the NFL database on first use."""
    if not sheets_ready():
        return
    for tab, columns in [
        (SLATE_TAB, SLATE_COLUMNS),
        (TRACKER_TAB, TRACKER_COLUMNS),
        (ALL_GAME_TRENDS_TAB, ALL_GAME_TRENDS_COLUMNS),
        (PUBLIC_SPLIT_TAB, PUBLIC_SPLIT_COLUMNS),
        (ODDS_SNAPSHOT_TAB, ODDS_SNAPSHOT_COLUMNS),
    ]:
        get_or_create_worksheet(tab, columns)


def render() -> None:
    _ensure_public_database_contract()
    st.caption("NFL v4.2 regression slate • separate NFL database • price-aware spread/total markets • regression QB/RB/WR yard props")
'''
    text = replace_once(text, marker, replacement, "NFL public database initialization")
    path.write_text(text)


def patch_cfb() -> None:
    path = Path("builders/cfb_builder.py")
    text = path.read_text()
    text = replace_once(
        text,
        "from shared.storage import read_sheet, sheets_ready, write_sheet\n",
        "from shared.storage import get_or_create_worksheet, read_sheet, sheets_ready, write_sheet\n"
        "from shared.public_contract import (\n"
        "    ALL_GAME_TRENDS_COLUMNS, ALL_GAME_TRENDS_TAB, ODDS_SNAPSHOT_COLUMNS,\n"
        "    ODDS_SNAPSHOT_TAB, PUBLIC_SPLIT_COLUMNS, PUBLIC_SPLIT_TAB,\n"
        ")\n",
        "CFB storage/public-contract imports",
    )
    old = '''RATINGS_TAB = "cfb_team_ratings"
SLATE_TAB = "cfb_daily_slate"
TRACKER_TAB = "cfb_bet_tracker"
SCHEDULE_TAB = "cfb_schedule"
PERSONNEL_TAB = "cfb_personnel_snapshots"
CALIBRATION_TAB = "cfb_calibration"
MODEL_LOG_TAB = "cfb_model_change_log"
'''
    new = '''# This model now owns a dedicated CFB workbook. Within that database, public
# tab names mirror MLB so the public site can use one sport-agnostic contract.
RATINGS_TAB = "team_ratings"
SLATE_TAB = "daily_slate"
TRACKER_TAB = "bet_tracker"
SCHEDULE_TAB = "schedule"
PERSONNEL_TAB = "personnel_snapshots"
CALIBRATION_TAB = "calibration"
MODEL_LOG_TAB = "model_change_log"
'''
    text = replace_once(text, old, new, "CFB tab contract")
    marker = '''def render() -> None:
'''
    replacement = '''def _ensure_public_database_contract() -> None:
    """Create the uniform public tabs in the CFB database on first use."""
    if not sheets_ready():
        return
    for tab, columns in [
        (SLATE_TAB, SLATE_COLUMNS),
        (TRACKER_TAB, TRACKER_COLUMNS),
        (ALL_GAME_TRENDS_TAB, ALL_GAME_TRENDS_COLUMNS),
        (PUBLIC_SPLIT_TAB, PUBLIC_SPLIT_COLUMNS),
        (ODDS_SNAPSHOT_TAB, ODDS_SNAPSHOT_COLUMNS),
    ]:
        get_or_create_worksheet(tab, columns)


def render() -> None:
    _ensure_public_database_contract()
'''
    text = replace_once(text, marker, replacement, "CFB public database initialization")
    path.write_text(text)


def patch_admin() -> None:
    path = Path("app_mobile_admin.py")
    text = path.read_text()
    text = replace_once(
        text,
        "from shared.auth import require_admin_password\nfrom shared.ui import SPORT_META, apply_global_styles, render_brand_header, render_sport_header\n",
        "from shared.auth import require_admin_password\n"
        "from shared.storage import set_storage_sport, storage_database_name\n"
        "from shared.ui import SPORT_META, apply_global_styles, render_brand_header, render_sport_header\n",
        "admin storage import",
    )
    text = replace_once(
        text,
        '''elif selected_sport == "CFB":
    from builders import cfb_builder
''',
        '''elif selected_sport == "CFB":
    set_storage_sport("CFB")
    from builders import cfb_builder
''',
        "admin CFB database selection",
    )
    text = replace_once(
        text,
        '''elif selected_sport == "NFL":
    from builders import nfl_builder
''',
        '''elif selected_sport == "NFL":
    set_storage_sport("NFL")
    from builders import nfl_builder
''',
        "admin NFL database selection",
    )
    text = replace_once(
        text,
        '''elif selected_sport == "CBB":
    from builders.cbb_builder import render
''',
        '''elif selected_sport == "CBB":
    set_storage_sport("CBB")
    from builders.cbb_builder import render
''',
        "admin CBB database selection",
    )
    text = replace_once(
        text,
        '''if selected_sport == "MLB":
    # Keep the production builder itself unchanged; only avoid redundant Sheet
''',
        '''if selected_sport != "MLB":
    st.caption(f"Database: {storage_database_name(selected_sport)}")

if selected_sport == "MLB":
    # Keep the production builder itself unchanged; only avoid redundant Sheet
''',
        "admin database label",
    )
    path.write_text(text)


if __name__ == "__main__":
    patch_nfl()
    patch_cfb()
    patch_admin()
