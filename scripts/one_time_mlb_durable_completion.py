from pathlib import Path
import re


path = Path("builders/mlb_builder.py")
text = path.read_text(encoding="utf-8")

if "MLB_BUILDER_COMPLETION_TAB" in text:
    raise SystemExit("Durable MLB completion flow is already installed")

start = text.index("def get_saved_game_filters_for_date(selected_date):")
end = text.index("\ndef _legacy_render_auto_matchup_builder_v1", start)

replacement = '''MLB_BUILDER_COMPLETION_TAB = "builder_completed"
MLB_BUILDER_COMPLETION_COLUMNS = [
    "Date", "Game Key", "Away Team", "Home Team", "Game Label", "Model Version", "Saved At",
]


def load_mlb_builder_completions():
    """Read the durable manual MLB builder-completion ledger from Turso."""
    return read_sheet(MLB_BUILDER_COMPLETION_TAB, MLB_BUILDER_COMPLETION_COLUMNS)


def save_mlb_builder_completions(df):
    return write_sheet(MLB_BUILDER_COMPLETION_TAB, df, MLB_BUILDER_COMPLETION_COLUMNS)


def mark_mlb_builder_completed(slate_date, game_key, away_team, home_team, game_label):
    """Persist an explicit successful MLB Build save independent of daily_slate.

    daily_slate is intentionally mutable because DraftKings/public-data refreshes
    update it before first pitch. Completion state must therefore live separately.
    """
    df = load_mlb_builder_completions()
    if df is None or df.empty:
        df = pd.DataFrame(columns=MLB_BUILDER_COMPLETION_COLUMNS)
    else:
        df = df.copy()
    for col in MLB_BUILDER_COMPLETION_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[MLB_BUILDER_COMPLETION_COLUMNS].astype(object)

    date_text = str(slate_date or "").strip()
    game_key_text = str(game_key or "").strip()
    away_text = str(away_team or "").strip()
    home_text = str(home_team or "").strip()
    label_text = str(game_label or f"{away_text} at {home_text}").strip()

    same_date = df["Date"].astype(str).str.strip().eq(date_text)
    if game_key_text:
        same_game = df["Game Key"].astype(str).str.strip().eq(game_key_text)
    else:
        same_game = (
            df["Away Team"].astype(str).map(_norm_game_text).eq(_norm_game_text(away_text))
            & df["Home Team"].astype(str).map(_norm_game_text).eq(_norm_game_text(home_text))
        )
    df = df.loc[~(same_date & same_game)].copy()

    row = {
        "Date": date_text,
        "Game Key": game_key_text,
        "Away Team": away_text,
        "Home Team": home_text,
        "Game Label": label_text,
        "Model Version": MODEL_VERSION,
        "Saved At": eastern_now().isoformat(),
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    return save_mlb_builder_completions(df)


def _collect_completed_game_filters(frame, slate_date, saved_ids, saved_labels, saved_matchups, require_model_build=False):
    if frame is None or frame.empty or "Date" not in frame.columns:
        return
    view = frame[frame["Date"].astype(str).str.strip().eq(str(slate_date))].copy()
    if view.empty:
        return
    if require_model_build:
        view = view.loc[view.apply(_slate_row_has_model_build, axis=1)].copy()
        if view.empty:
            return

    if "Game Key" in view.columns:
        for value in view["Game Key"].astype(str).tolist():
            value = str(value).strip()
            if value and value.lower() not in {"nan", "none"}:
                saved_ids.add(value)

    label_column = "Game Label" if "Game Label" in view.columns else "Game" if "Game" in view.columns else ""
    if label_column:
        for value in view[label_column].astype(str).tolist():
            label = _norm_game_text(value)
            if label:
                saved_labels.add(label)

    if "Away Team" in view.columns and "Home Team" in view.columns:
        for _, row in view.iterrows():
            away = _norm_game_text(row.get("Away Team", ""))
            home = _norm_game_text(row.get("Home Team", ""))
            if away and home:
                saved_matchups.add(f"{away} at {home}")


def get_saved_game_filters_for_date(selected_date):
    """Return every MLB matchup that has actually completed the manual builder.

    Completion is sourced first from a dedicated immutable builder ledger. Existing
    games are automatically recovered from game_projection_history, which is saved
    on every successful build. daily_slate remains only a backward-compatible
    fallback because DraftKings/public-data refreshes are allowed to mutate it.
    """
    slate_date = selected_date.strftime("%Y-%m-%d") if hasattr(selected_date, "strftime") else str(selected_date)
    saved_ids = set()
    saved_labels = set()
    saved_matchups = set()

    # Permanent source of truth for all new saves.
    completion_df = load_mlb_builder_completions()
    _collect_completed_game_filters(
        completion_df, slate_date, saved_ids, saved_labels, saved_matchups,
        require_model_build=False,
    )

    # Durable recovery/backfill for matchups built before the completion ledger
    # existed, including any game whose mutable daily_slate row was refreshed.
    history_df = read_sheet(GAME_PROJECTION_HISTORY_TAB, GAME_PROJECTION_HISTORY_COLUMNS)
    _collect_completed_game_filters(
        history_df, slate_date, saved_ids, saved_labels, saved_matchups,
        require_model_build=False,
    )

    # Backward-compatible fallback for older built daily_slate rows. Shell rows are
    # explicitly excluded so pregame DraftKings tracking cannot hide an unbuilt game.
    slate_df = load_slate()
    _collect_completed_game_filters(
        slate_df, slate_date, saved_ids, saved_labels, saved_matchups,
        require_model_build=True,
    )

    return saved_ids, saved_labels, saved_matchups
'''

text = text[:start] + replacement + text[end:]

# Persist the completion marker inside the same atomic Turso batch as the matchup.
needle = "            add_bets_batch(tracker_bet_batch)\n"
if text.count(needle) != 1:
    raise SystemExit(f"Expected one batched tracker-save line, found {text.count(needle)}")
text = text.replace(
    needle,
    "            mark_mlb_builder_completed(slate_date, game_key, away_team, home_team, game_label)\n" + needle,
    1,
)

path.write_text(text, encoding="utf-8")
