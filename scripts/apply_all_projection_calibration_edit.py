from pathlib import Path


PATH = Path("builders/nfl_builder.py")
START_MARKER = "def _auto_update_prop_tracker() -> tuple[int, str]:"
END_MARKER = "\ndef _game_teams_from_label"

REPLACEMENT = r'''def _auto_update_prop_tracker() -> tuple[int, str]:
    if not sheets_ready():
        return 0, "Google Sheets is not configured."

    projections = read_sheet(PROP_SLATE_TAB, PROP_PROJECTION_COLUMNS)
    tracker = read_sheet(PROP_TRACKER_TAB, PROP_TRACKER_COLUMNS)

    if (projections is None or projections.empty) and (tracker is None or tracker.empty):
        return 0, "No saved prop projections yet."

    if tracker is None:
        tracker = pd.DataFrame(columns=PROP_TRACKER_COLUMNS)
    for column in [
        "Result", "Actual Attempts", "Actual Completions", "Actual Targets", "Actual Receptions", "Actual Result",
        "Opportunity Error", "Efficiency Error", "Projection Residual",
    ]:
        if column in tracker.columns:
            tracker[column] = tracker[column].astype(object)

    existing_calibration = read_sheet(PROP_CALIBRATION_TAB, PROP_CALIBRATION_COLUMNS)
    if existing_calibration is None:
        existing_calibration = pd.DataFrame(columns=PROP_CALIBRATION_COLUMNS)

    calibration_key_columns = ["Season", "Week", "Game ID", "Player", "Market", "Model Version"]

    def calibration_key(row: pd.Series | dict[str, Any]) -> tuple[Any, ...]:
        return (
            _int(row.get("Season", 0), 0),
            _int(row.get("Week", 0), 0),
            _safe_text(row.get("Game ID", "")),
            _normalize_name(row.get("Player", "")),
            _safe_text(row.get("Market", "")),
            _safe_text(row.get("Model Version", MODEL_VERSION)) or MODEL_VERSION,
        )

    calibrated_keys: set[tuple[Any, ...]] = set()
    if not existing_calibration.empty:
        calibrated_keys = {
            calibration_key(calibration_row)
            for _, calibration_row in existing_calibration.iterrows()
        }

    season_cache: dict[int, pd.DataFrame] = {}

    def completed_values(row: pd.Series | dict[str, Any]) -> dict[str, Any] | None:
        market = _safe_text(row.get("Market", ""))
        season = _int(row.get("Season", 0), 0)
        week = _int(row.get("Week", 0), 0)
        if season <= 0 or week <= 0 or not market:
            return None

        if season not in season_cache:
            stats = _load_player_stats_season(season)
            if stats is None or stats.empty:
                season_cache[season] = pd.DataFrame()
            else:
                stats = stats.copy()
                stats["player_name_norm"] = _player_name_column(stats).map(_normalize_name)
                stats["team_norm"] = _player_team_column(stats)
                season_cache[season] = stats

        stats = season_cache[season]
        if stats.empty:
            return None

        player_name = _normalize_name(row.get("Player", ""))
        team = _normalize_team(row.get("Team", ""))
        matches = stats[
            (stats["player_name_norm"] == player_name)
            & (pd.to_numeric(stats.get("week"), errors="coerce") == week)
        ].copy()
        if team:
            team_matches = matches[matches["team_norm"] == team]
            if not team_matches.empty:
                matches = team_matches
        if matches.empty:
            return None

        stat_row = matches.iloc[-1].to_dict()
        actual, actual_opportunity, actual_efficiency = _actual_market_values(stat_row, market)
        if actual is None:
            return None

        projection = _num(row.get("Projection", 0), 0)
        market_line = _num(row.get("Market Line", np.nan), np.nan)
        projected_opportunity = _projected_opportunity_for_market(dict(row), market)
        projected_efficiency = _num(row.get("Efficiency", 0), 0)
        return {
            "market": market,
            "season": season,
            "week": week,
            "stat_row": stat_row,
            "actual": float(actual),
            "actual_opportunity": float(actual_opportunity),
            "actual_efficiency": float(actual_efficiency),
            "projection": float(projection),
            "market_line": float(market_line),
            "projected_opportunity": float(projected_opportunity),
            "projected_efficiency": float(projected_efficiency),
        }

    calibration_rows: list[dict[str, Any]] = []
    calibrated_projection_count = 0

    if projections is not None and not projections.empty:
        for _, row in projections.iterrows():
            key = calibration_key(row)
            if key in calibrated_keys:
                continue

            values = completed_values(row)
            if values is None:
                continue

            calibration_rows.append({
                "Date": _safe_text(row.get("Date", "")) or str(date.today()),
                "Season": values["season"],
                "Week": values["week"],
                "Game ID": row.get("Game ID", ""),
                "Player": row.get("Player", ""),
                "Position": row.get("Position", ""),
                "Market": values["market"],
                "Projection": values["projection"],
                "Market Line": values["market_line"],
                "Actual Result": values["actual"],
                "Projected Opportunity": round(values["projected_opportunity"], 3),
                "Actual Opportunity": round(values["actual_opportunity"], 3),
                "Projected Efficiency": round(values["projected_efficiency"], 4),
                "Actual Efficiency": round(values["actual_efficiency"], 4),
                "Opportunity Error": round(values["actual_opportunity"] - values["projected_opportunity"], 3),
                "Efficiency Error": round(values["actual_efficiency"] - values["projected_efficiency"], 4),
                "Projection Residual": round(values["actual"] - values["projection"], 3),
                "Opponent": row.get("Opponent", ""),
                "Role Confidence": row.get("Role Confidence", ""),
                "Reliability": row.get("Reliability", ""),
                "Model Version": row.get("Model Version", MODEL_VERSION),
            })
            calibrated_keys.add(key)
            calibrated_projection_count += 1

    graded_tracker_count = 0
    if tracker is not None and not tracker.empty:
        for index, row in tracker.iterrows():
            if _safe_text(row.get("Actual Result", "")):
                continue

            values = completed_values(row)
            if values is None:
                continue

            stat_row = values["stat_row"]
            market = values["market"]
            if market.startswith("Passing") or market == "Interceptions":
                tracker.at[index, "Actual Attempts"] = _num(stat_row.get("attempts", 0), 0)
            elif market.startswith("Rushing"):
                tracker.at[index, "Actual Attempts"] = _num(stat_row.get("carries", 0), 0)
            else:
                tracker.at[index, "Actual Attempts"] = ""

            tracker.at[index, "Actual Completions"] = _num(stat_row.get("completions", 0), 0)
            tracker.at[index, "Actual Targets"] = _num(stat_row.get("targets", 0), 0)
            tracker.at[index, "Actual Receptions"] = _num(stat_row.get("receptions", 0), 0)
            tracker.at[index, "Actual Result"] = round(values["actual"], 3)
            tracker.at[index, "Opportunity Error"] = round(
                values["actual_opportunity"] - values["projected_opportunity"], 3
            )
            tracker.at[index, "Efficiency Error"] = round(
                values["actual_efficiency"] - values["projected_efficiency"], 4
            )
            tracker.at[index, "Projection Residual"] = round(
                values["actual"] - values["projection"], 3
            )
            if math.isfinite(values["market_line"]):
                tracker.at[index, "Result"] = _bet_result_from_actual(
                    _safe_text(row.get("Pick", "")), values["market_line"], values["actual"]
                )
            graded_tracker_count += 1

    if graded_tracker_count:
        write_sheet(PROP_TRACKER_TAB, tracker, PROP_TRACKER_COLUMNS)

    if calibration_rows:
        new_calibration = pd.DataFrame(calibration_rows, columns=PROP_CALIBRATION_COLUMNS)
        if not existing_calibration.empty:
            combined = pd.concat([existing_calibration, new_calibration], ignore_index=True)
            combined = combined.drop_duplicates(subset=calibration_key_columns, keep="last")
        else:
            combined = new_calibration
        write_sheet(PROP_CALIBRATION_TAB, combined, PROP_CALIBRATION_COLUMNS)
        _prop_calibration_data.clear()

    total_updates = calibrated_projection_count + graded_tracker_count
    return total_updates, (
        f"Calibrated {calibrated_projection_count} completed projection(s); "
        f"updated {graded_tracker_count} graded tracker result(s)."
    )


'''


def main() -> None:
    text = PATH.read_text()
    start = text.index(START_MARKER)
    end = text.index(END_MARKER, start)
    updated = text[:start] + REPLACEMENT + text[end:]
    PATH.write_text(updated)


if __name__ == "__main__":
    main()
