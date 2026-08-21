"""Uniform public-database tab contract shared by non-MLB sport builders.

Each sport owns a separate Google Sheets workbook. Public-facing tab names and
trend/split schemas intentionally match MLB so ezpzpicks.com can read every
sport through the same database contract while keeping records isolated.
"""

PUBLIC_SLATE_TAB = "daily_slate"
PUBLIC_TRACKER_TAB = "bet_tracker"
ALL_GAME_TRENDS_TAB = "all_game_trends"
PUBLIC_SPLIT_TAB = "public_split_snapshots"
ODDS_SNAPSHOT_TAB = "odds_snapshot"
MODEL_CHANGE_LOG_TAB = "model_change_log"

PUBLIC_SPLIT_COLUMNS = [
    "Snapshot Time ET", "Opening Snapshot Time ET", "Date", "Game Time ET", "Game",
    "Away Team", "Home Team", "Data Type", "Market", "Selection", "Line", "Odds",
    "Opening Line", "Opening Odds", "Opening Implied %", "Current Implied %",
    "Opening Public %", "Current Public %", "Public Change %", "Opening Sharp %",
    "Current Sharp %", "Sharp Change %", "Public Bets %", "Public Money %",
    "Public Gap %", "Warning Key", "Warning", "Warning Tone", "Warning Negative",
    "Line Movement Signal", "Line Movement Tone", "Line Movement Basis",
    "Line Movement Value", "Popularity Rank", "Source", "Match Confidence", "Source URL",
]

ALL_GAME_TRENDS_COLUMNS = [
    "Date", "Game Key", "Game", "Game Time", "Away Team", "Home Team",
    "Market", "Selection", "Side", "Line", "Odds", "Odds/Line",
    "Model Grade", "Qualified", "Model %", "Implied %", "Edge %",
    "Model Version", "Correlation Block",
    "Result", "Actual Away Runs", "Actual Home Runs", "Actual Total", "Result Updated",
    "Public Bets %", "Public Money %", "Public Gap %", "Public Warning",
    "Public Warning Negative", "Public Split Source", "Public Split Market",
    "Public Split Selection", "Public Split Line", "Public Split Odds",
    "Public Split Match Confidence", "Public Split Snapshot Time",
    "Opening Public %", "Current Public %", "Public Change %",
    "Opening Sharp %", "Current Sharp %", "Sharp Change %",
    "Opening Public Split Line", "Opening Public Split Odds",
    "Opening Public Split Snapshot Time", "Opening Implied %", "Current Implied %",
    "Line Movement Signal", "Line Movement Tone", "Line Movement Basis",
    "Line Movement Value", "Trend Play", "Trend Score", "Trend Tier", "Trend Signals",
    "Trend All Time Record", "Trend Last 30 Record", "Trend Last 7 Record",
    "Trend Exact Sample", "Trend Score Details",
]

ODDS_SNAPSHOT_COLUMNS = [
    "Snapshot Time ET", "Date", "Game ID", "Game", "Away Team", "Home Team",
    "Market", "Selection", "Line", "Odds", "Source",
]
