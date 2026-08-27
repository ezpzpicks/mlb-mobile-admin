from pathlib import Path


PATH = Path("builders/nfl_builder.py")


def replace_between(source: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise SystemExit(f"start marker not found: {start_marker!r}")
    end = source.find(end_marker, start)
    if end < 0:
        raise SystemExit(f"end marker not found: {end_marker!r}")
    return source[:start] + replacement + source[end:]


def require_replace(source: str, old: str, new: str, label: str, count: int = 1) -> str:
    found = source.count(old)
    if found < count:
        raise SystemExit(f"{label} not found; expected at least {count}, found {found}")
    return source.replace(old, new, count)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    # Remove Odds API-only constants and lookup/request functions.
    text = replace_between(text, "NFL_TEAM_NAMES = {", "POSITION_EFFICIENCY_PRIORS = {", "")
    text = replace_between(text, "def _secret_value(", "def _grade_prop(", "")

    # Player projections should never arrive with a sportsbook market prefilled.
    text = require_replace(
        text,
        "        key = (_normalize_name(player), market)\n        market_data = market_lines.get(key, {})\n        rows.append({\n",
        "        rows.append({\n",
        "automatic player market seed",
    )
    text = require_replace(
        text,
        '            "Projection": round(projection, 2), "Fair Line": _fair_line(projection, market), "Market Line": market_data.get("line", np.nan),\n'
        '            "Over Odds": market_data.get("over_odds", -110), "Under Odds": market_data.get("under_odds", -110), "Line Source": market_data.get("source", ""),\n',
        '            "Projection": round(projection, 2), "Fair Line": _fair_line(projection, market), "Market Line": np.nan,\n'
        '            "Over Odds": np.nan, "Under Odds": np.nan, "Line Source": "",\n',
        "automatic prop market values",
    )

    # Remove the no-longer-used market_lines argument throughout the prop projection path.
    text = require_replace(
        text,
        "    weather_adjustment: float, market_lines: dict[tuple[str, str], dict[str, Any]],\n"
        "    role_context: dict[str, dict[str, Any]] | None = None,\n",
        "    weather_adjustment: float, role_context: dict[str, dict[str, Any]] | None = None,\n",
        "project_player_markets market_lines parameter",
    )
    text = require_replace(
        text,
        "    home_rating: dict[str, Any], projection: dict[str, float], weather_adjustment: float,\n"
        "    market_lines: dict[tuple[str, str], dict[str, Any]],\n"
        "    market_total: float | None = None, home_spread: float | None = None,\n",
        "    home_rating: dict[str, Any], projection: dict[str, float], weather_adjustment: float,\n"
        "    market_total: float | None = None, home_spread: float | None = None,\n",
        "build_game_prop_rows market_lines parameter",
    )
    text = require_replace(
        text,
        "                defense_profiles, rating, opponent_rating, projection, weather_adjustment, market_lines, team_roles,\n",
        "                defense_profiles, rating, opponent_rating, projection, weather_adjustment, team_roles,\n",
        "project player market_lines call",
    )

    # Require both manual prices before any player prop can be graded.
    new_evaluate = '''def _evaluate_prop_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if rows is None or rows.empty:
        return pd.DataFrame()
    output = []
    for _, row in rows.iterrows():
        item = row.to_dict()
        projection = _num(item.get("Projection", 0), 0)
        samples, distribution = _simulate_prop_distribution(item)
        item["Distribution"] = distribution
        item["Fair Line"] = _fair_line(float(np.median(samples)), _safe_text(item.get("Market", "")))
        item["_sd"] = float(np.std(samples, ddof=1))
        line = _num(item.get("Market Line", np.nan), np.nan)
        if not (math.isfinite(line) and line >= 0):
            item.update({"Pick": "Projection only", "Pick Odds": np.nan, "Model Probability": np.nan, "Push Probability": np.nan, "Implied Probability": np.nan, "Probability Edge": np.nan, "Projection Edge": np.nan, "Expected Value": np.nan, "Grade": "No market line", "Track": False})
            output.append(item)
            continue

        over_raw = _num(item.get("Over Odds", np.nan), np.nan)
        under_raw = _num(item.get("Under Odds", np.nan), np.nan)
        valid_prices = (
            math.isfinite(over_raw) and math.isfinite(under_raw)
            and abs(over_raw) >= 100 and abs(under_raw) >= 100
        )
        if not valid_prices:
            item.update({"Pick": "Enter both odds", "Pick Odds": np.nan, "Model Probability": np.nan, "Push Probability": np.nan, "Implied Probability": np.nan, "Probability Edge": np.nan, "Projection Edge": round(abs(projection - line), 2), "Expected Value": np.nan, "Grade": "Missing odds", "Track": False})
            output.append(item)
            continue

        over_odds, under_odds = int(round(over_raw)), int(round(under_raw))
        p_over = float(np.mean(samples > line))
        p_under = float(np.mean(samples < line))
        p_push = max(0.0, 1.0 - p_over - p_under)
        ev_over = _expected_value_with_push(p_over, p_under, over_odds)
        ev_under = _expected_value_with_push(p_under, p_over, under_odds)
        direction = "Over" if ev_over >= ev_under else "Under"
        probability = p_over if direction == "Over" else p_under
        lose_probability = p_under if direction == "Over" else p_over
        odds = over_odds if direction == "Over" else under_odds
        raw_over = american_implied_probability(over_odds)
        raw_under = american_implied_probability(under_odds)
        over_novig = raw_over / max(raw_over + raw_under, 1e-9)
        implied = over_novig if direction == "Over" else 1.0 - over_novig
        conditional_probability = probability / max(1.0 - p_push, 1e-9)
        probability_edge_value = conditional_probability - implied
        grade = _grade_prop(
            conditional_probability, probability_edge_value,
            _num(item.get("Reliability", 50), 50), direction,
            _num(item.get("Role Confidence", 50), 50),
            _safe_text(item.get("Market", "")),
        )
        item.update({
            "Line Source": "Manual market line", "Pick": f"{direction} {line:.1f}", "Pick Odds": odds,
            "Model Probability": round(conditional_probability, 4), "Push Probability": round(p_push, 4), "Implied Probability": round(implied, 4),
            "Probability Edge": round(probability_edge_value, 4), "Projection Edge": round(abs(projection - line), 2),
            "Expected Value": round(_expected_value_with_push(probability, lose_probability, odds), 4), "Grade": grade, "Track": grade in ["A Prop", "B Prop"],
        })
        output.append(item)
    return pd.DataFrame(output)


'''
    text = replace_between(text, "def _evaluate_prop_rows(", "def _build_game_prop_rows(", new_evaluate)

    # Manual game markets: vertical team-by-team flow, all fields blank until the user enters them.
    manual_game_inputs = '''    st.markdown("### Manual sportsbook lines and prices")
    st.caption("Manual entry only — no sportsbook/API lines or prices are loaded into the NFL builder.")

    st.markdown(f"**{away_team}**")
    away_spread_input = st.number_input(
        f"{away_team} spread line", value=None, step=0.5,
        key=f"nfl_away_spread_line_{market_key}",
    )
    away_spread_odds_input = st.number_input(
        f"{away_team} spread odds", value=None, step=5,
        key=f"nfl_away_spread_odds_{market_key}",
    )
    away_ml_input = st.number_input(
        f"{away_team} moneyline", value=None, step=5,
        key=f"nfl_away_ml_{market_key}",
    )

    st.markdown(f"**{home_team}**")
    home_spread_input = st.number_input(
        f"{home_team} spread line", value=None, step=0.5,
        key=f"nfl_home_spread_line_{market_key}",
    )
    home_spread_odds_input = st.number_input(
        f"{home_team} spread odds", value=None, step=5,
        key=f"nfl_home_spread_odds_{market_key}",
    )
    home_ml_input = st.number_input(
        f"{home_team} moneyline", value=None, step=5,
        key=f"nfl_home_ml_{market_key}",
    )

    st.markdown("**Game total**")
    market_total_input = st.number_input(
        "Point total line", value=None, step=0.5,
        key=f"nfl_total_{market_key}",
    )
    total_over_odds_input = st.number_input(
        "Over odds", value=None, step=5,
        key=f"nfl_total_over_odds_{market_key}",
    )
    total_under_odds_input = st.number_input(
        "Under odds", value=None, step=5,
        key=f"nfl_total_under_odds_{market_key}",
    )

    spread_market_ready = all(value is not None for value in [
        away_spread_input, away_spread_odds_input, home_spread_input, home_spread_odds_input,
    ])
    if spread_market_ready and abs(float(away_spread_input) + float(home_spread_input)) > 0.01:
        st.warning("The two spread lines should be opposites (for example +3.5 and -3.5). Check the manual entries.")
        spread_market_ready = False
    total_market_ready = all(value is not None for value in [market_total_input, total_over_odds_input, total_under_odds_input])
    ml_market_ready = all(value is not None for value in [away_ml_input, home_ml_input])
    game_markets_ready = spread_market_ready and total_market_ready and ml_market_ready
    if not game_markets_ready:
        st.info("Enter both team spreads/prices, both moneylines, and the total with Over/Under prices before saving the game.")

    away_spread_line = float(away_spread_input) if away_spread_input is not None else 0.0
    home_spread = float(home_spread_input) if home_spread_input is not None else 0.0
    away_spread_odds = int(away_spread_odds_input) if away_spread_odds_input is not None else -110
    home_spread_odds = int(home_spread_odds_input) if home_spread_odds_input is not None else -110
    market_total = float(market_total_input) if market_total_input is not None else 45.0
    total_over_odds = int(total_over_odds_input) if total_over_odds_input is not None else -110
    total_under_odds = int(total_under_odds_input) if total_under_odds_input is not None else -110
    away_ml = int(away_ml_input) if away_ml_input is not None else -110
    home_ml = int(home_ml_input) if home_ml_input is not None else -110
    st.caption(f"Automatic home field: {automatic_home_field:.1f} pts • {_safe_text(hfa_info.get('source', 'Rolling home-field model'))}")

'''
    text = replace_between(
        text,
        '    st.markdown("### Sportsbook lines and prices")\n',
        '    st.markdown("### Game environment")\n',
        manual_game_inputs,
    )

    text = require_replace(
        text,
        '        spread_pick = f"{away_team} {-home_spread:+.1f}"\n',
        '        spread_pick = f"{away_team} {away_spread_line:+.1f}"\n',
        "away spread selection label",
    )

    spread_gate = '    if spread_price_edge <= 0 or spread_ev <= 0:\n        spread_grade = "Non-Edge Spread"\n'
    text = require_replace(
        text,
        spread_gate,
        spread_gate + '    if not spread_market_ready:\n        spread_grade = "No market line"\n',
        "spread manual gate",
    )
    total_gate = '    if total_price_edge <= 0 or total_ev <= 0:\n        total_grade = "Non-Edge Total"\n'
    text = require_replace(
        text,
        total_gate,
        total_gate + '    if not total_market_ready:\n        total_grade = "No market line"\n',
        "total manual gate",
    )
    ml_gate = '    ml_grade = _grade_moneyline(ml_probability, ml_edge, reliability, ml_confluence)\n'
    text = require_replace(
        text,
        ml_gate,
        ml_gate + '    if not ml_market_ready:\n        ml_grade = "No market line"\n',
        "moneyline manual gate",
    )

    old_cards = (
        '    _market_card("Spread", margin_text, spread_pick, spread_probability, f"{spread_edge:+.1f} pts • {spread_price_edge:+.1%} price • {spread_pick_odds:+d}", spread_grade, spread_confluence, spread_reasons)\n'
        '    _market_card("Total", f"{projection[\'total\']:.1f} points", total_pick, total_probability, f"{total_edge:+.1f} pts • {total_price_edge:+.1%} price • {total_pick_odds:+d}", total_grade, total_confluence, total_reasons)\n'
        '    _market_card("Moneyline", f"{ml_pick} {ml_probability:.1%} win probability", ml_pick, ml_probability, f"{ml_edge:+.1%}", ml_grade, ml_confluence, ml_reasons)\n'
    )
    new_cards = '''    spread_edge_text = f"{spread_edge:+.1f} pts • {spread_price_edge:+.1%} price • {spread_pick_odds:+d}" if spread_market_ready else "Manual spread lines and prices required"
    total_edge_text = f"{total_edge:+.1f} pts • {total_price_edge:+.1%} price • {total_pick_odds:+d}" if total_market_ready else "Manual total line and prices required"
    ml_edge_text = f"{ml_edge:+.1%}" if ml_market_ready else "Manual moneyline prices required"
    _market_card("Spread", margin_text, spread_pick, spread_probability, spread_edge_text, spread_grade, spread_confluence, spread_reasons)
    _market_card("Total", f"{projection['total']:.1f} points", total_pick, total_probability, total_edge_text, total_grade, total_confluence, total_reasons)
    _market_card("Moneyline", f"{ml_pick} {ml_probability:.1%} win probability", ml_pick, ml_probability, ml_edge_text, ml_grade, ml_confluence, ml_reasons)
'''
    text = require_replace(text, old_cards, new_cards, "game market cards")

    # Continuous manual prop scroll in team/slot order. No data editor and no prop tile chart.
    manual_props = '''    st.markdown("### Manual player prop lines")
    st.caption("Manual entry only. The model projection is shown first, followed by the sportsbook line and both prices. No Odds API values are loaded.")

    prop_base = _build_game_prop_rows(
        away_team, home_team, away_lineup, home_lineup, profiles, defense_profiles,
        away_rating, home_rating, projection, weather_total_adjustment,
        market_total=market_total if total_market_ready else None,
        home_spread=home_spread if spread_market_ready else None,
    )
    evaluated_props = pd.DataFrame()
    if prop_base.empty:
        st.info("No skill-position players were resolved. Open Lineups, injuries and role overrides to correct the QB/RB/WR/TE card.")
    else:
        wager_mask = (
            ((prop_base["Position"].astype(str) == "QB") & (prop_base["Market"].astype(str) == "Passing Yards"))
            | ((prop_base["Position"].astype(str) == "RB") & (prop_base["Market"].astype(str).isin(["Rushing Yards", "Receiving Yards"])))
            | ((prop_base["Position"].astype(str) == "WR") & (prop_base["Market"].astype(str) == "Receiving Yards"))
        )
        prop_inputs = prop_base.copy()
        prop_inputs["Market Line"] = np.nan
        prop_inputs["Over Odds"] = np.nan
        prop_inputs["Under Odds"] = np.nan
        prop_inputs["Line Source"] = ""

        yard_inputs = prop_inputs.loc[wager_mask].copy()
        slot_order = {"QB": 0, "RB1": 1, "RB2": 2, "WR1": 3, "WR2": 4, "WR3": 5, "TE": 6}
        market_order = {"Passing Yards": 0, "Rushing Yards": 1, "Receiving Yards": 2}
        team_order = {away_team: 0, home_team: 1}
        yard_inputs["_team_order"] = yard_inputs["Team"].map(team_order).fillna(99)
        yard_inputs["_slot_order"] = yard_inputs["Slot"].map(slot_order).fillna(99)
        yard_inputs["_market_order"] = yard_inputs["Market"].map(market_order).fillna(99)
        yard_inputs = yard_inputs.sort_values(["_team_order", "_slot_order", "_market_order", "Player"])

        for team in [away_team, home_team]:
            team_rows = yard_inputs[yard_inputs["Team"].astype(str) == str(team)]
            if team_rows.empty:
                continue
            st.markdown(f"#### {team} player props")
            for idx, row in team_rows.iterrows():
                player = _safe_text(row.get("Player", ""))
                slot = _safe_text(row.get("Slot", ""))
                market = _safe_text(row.get("Market", ""))
                projection_value = _num(row.get("Projection", 0), 0)
                st.markdown(f"**{slot} — {player} · {market}**")
                st.caption(f"EZPZ projection: {projection_value:.1f}")
                line_col, over_col, under_col = st.columns(3)
                line_value = line_col.number_input(
                    "Line", value=None, step=0.5,
                    key=f"nfl_prop_line_{market_key}_{idx}",
                )
                over_value = over_col.number_input(
                    "Over odds", value=None, step=5,
                    key=f"nfl_prop_over_{market_key}_{idx}",
                )
                under_value = under_col.number_input(
                    "Under odds", value=None, step=5,
                    key=f"nfl_prop_under_{market_key}_{idx}",
                )
                if line_value is not None:
                    prop_inputs.at[idx, "Market Line"] = float(line_value)
                if over_value is not None:
                    prop_inputs.at[idx, "Over Odds"] = int(over_value)
                if under_value is not None:
                    prop_inputs.at[idx, "Under Odds"] = int(under_value)
                if line_value is not None and over_value is not None and under_value is not None:
                    prop_inputs.at[idx, "Line Source"] = "Manual market line"

        evaluated_props = _evaluate_prop_rows(prop_inputs)
        if not evaluated_props.empty:
            st.markdown("#### Prop grades")
            evaluated_wagers = evaluated_props.loc[wager_mask].copy()
            evaluated_wagers["_team_order"] = evaluated_wagers["Team"].map(team_order).fillna(99)
            evaluated_wagers["_slot_order"] = evaluated_wagers["Slot"].map(slot_order).fillna(99)
            evaluated_wagers["_market_order"] = evaluated_wagers["Market"].map(market_order).fillna(99)
            evaluated_wagers = evaluated_wagers.sort_values(["_team_order", "_slot_order", "_market_order", "Player"])
            for _, row in evaluated_wagers.iterrows():
                grade = _safe_text(row.get("Grade", ""))
                player = _safe_text(row.get("Player", ""))
                slot = _safe_text(row.get("Slot", ""))
                market = _safe_text(row.get("Market", ""))
                projection_value = _num(row.get("Projection", 0), 0)
                if grade in ["No market line", "Missing odds"]:
                    st.caption(f"{slot} — {player} · {market}: projection {projection_value:.1f} • {grade}")
                    continue
                pick = _safe_text(row.get("Pick", ""))
                pick_odds = _int(row.get("Pick Odds", 0), 0)
                probability = _num(row.get("Model Probability", 0), 0)
                edge = _num(row.get("Probability Edge", 0), 0)
                st.markdown(f"**{slot} — {player} · {market}: {pick} ({pick_odds:+d}) — {grade}**")
                st.caption(f"Projection {projection_value:.1f} • model {probability:.1%} • price edge {edge:+.1%}")

'''
    text = replace_between(
        text,
        '    st.markdown("### Automatic player props")\n',
        '    st.divider()\n    st.caption("This single action saves the game, lineup snapshot and every prop projection.',
        manual_props,
    )

    text = require_replace(
        text,
        '    if st.button("Save Game & Graded Plays", type="primary", use_container_width=True, key=f"nfl_save_everything_{market_key}"):\n',
        '    if st.button("Save Game & Graded Plays", type="primary", use_container_width=True, key=f"nfl_save_everything_{market_key}", disabled=not game_markets_ready):\n',
        "manual market save gate",
    )

    text = text.replace(
        'st.caption("NFL v4.2 regression slate • separate NFL database • price-aware spread/total markets • regression QB/RB/WR yard props")',
        'st.caption("NFL v4.2 regression slate • manual sportsbook entry • regression QB/RB/WR yard props")',
        1,
    )

    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
