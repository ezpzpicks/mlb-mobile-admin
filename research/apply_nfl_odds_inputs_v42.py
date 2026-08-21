"""Apply the NFL v4.2 sportsbook line/price input changes.

This is intentionally an exact-source patch so the large NFL builder can be
modified reproducibly in CI without replacing unrelated model code.
"""
from __future__ import annotations

from pathlib import Path

PATH = Path("builders/nfl_builder.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '    "Market Home Spread", "Market Total", "Away ML", "Home ML",\n',
        '    "Market Home Spread", "Market Total", "Home Spread Odds", "Away Spread Odds",\n'
        '    "Total Over Odds", "Total Under Odds", "Away ML", "Home ML",\n',
        "slate odds columns",
    )

    text = replace_once(
        text,
        '            "away_ml": 110, "home_ml": -130, "home_spread": -2.5, "total": 44.5,\n',
        '            "away_ml": 110, "home_ml": -130, "home_spread": -2.5, "total": 44.5,\n'
        '            "home_spread_odds": -110, "away_spread_odds": -110,\n'
        '            "total_over_odds": -110, "total_under_odds": -110,\n',
        "test-market defaults",
    )

    text = replace_once(
        text,
        '        "total": _num(row.get("Total Line", 44.5), 44.5),\n',
        '        "total": _num(row.get("Total Line", 44.5), 44.5),\n'
        '        "home_spread_odds": -110,\n'
        '        "away_spread_odds": -110,\n'
        '        "total_over_odds": -110,\n'
        '        "total_under_odds": -110,\n',
        "scheduled-market defaults",
    )

    old_market_ui = '''    st.markdown("### Market and game environment")
    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread", value=float(defaults["home_spread"]), step=0.5, key=f"nfl_home_spread_{market_key}")
        home_ml = st.number_input("Home moneyline", value=int(defaults["home_ml"]), step=5, key=f"nfl_home_ml_{market_key}")
    with c2:
        market_total = st.number_input("Game total", value=float(defaults["total"]), step=0.5, key=f"nfl_total_{market_key}")
        away_ml = st.number_input("Away moneyline", value=int(defaults["away_ml"]), step=5, key=f"nfl_away_ml_{market_key}")
    with c3:
        st.metric("Automatic home field", f"{automatic_home_field:.1f} pts")
        precipitation = st.selectbox("Precipitation", ["None", "Rain", "Heavy Rain", "Snow", "Heavy Snow"], key=f"nfl_precip_{market_key}")
    st.caption(_safe_text(hfa_info.get("source", "Rolling home-field model")))

    roof_options = ["outdoors", "dome", "closed", "open"]
    roof_default = defaults["roof"].lower() if defaults["roof"].lower() in roof_options else "outdoors"
    c4, c5, c6 = st.columns(3)
    with c4:
        roof = st.selectbox("Roof", roof_options, index=roof_options.index(roof_default), key=f"nfl_roof_{market_key}")
    with c5:
        temperature = st.number_input("Temperature °F", value=float(defaults["temperature"]), step=1.0, key=f"nfl_temp_{market_key}")
    with c6:
        wind = st.number_input("Wind mph", value=float(defaults["wind"]), min_value=0.0, step=1.0, key=f"nfl_wind_{market_key}")
'''
    new_market_ui = '''    st.markdown("### Sportsbook lines and prices")
    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread line", value=float(defaults["home_spread"]), step=0.5, key=f"nfl_home_spread_{market_key}")
        home_spread_odds = int(st.number_input(f"{home_team} spread odds", value=int(defaults.get("home_spread_odds", -110)), step=5, key=f"nfl_home_spread_odds_{market_key}"))
        away_spread_odds = int(st.number_input(f"{away_team} spread odds", value=int(defaults.get("away_spread_odds", -110)), step=5, key=f"nfl_away_spread_odds_{market_key}"))
    with c2:
        market_total = st.number_input("Game total line", value=float(defaults["total"]), step=0.5, key=f"nfl_total_{market_key}")
        total_over_odds = int(st.number_input("Over odds", value=int(defaults.get("total_over_odds", -110)), step=5, key=f"nfl_total_over_odds_{market_key}"))
        total_under_odds = int(st.number_input("Under odds", value=int(defaults.get("total_under_odds", -110)), step=5, key=f"nfl_total_under_odds_{market_key}"))
    with c3:
        home_ml = st.number_input("Home moneyline", value=int(defaults["home_ml"]), step=5, key=f"nfl_home_ml_{market_key}")
        away_ml = st.number_input("Away moneyline", value=int(defaults["away_ml"]), step=5, key=f"nfl_away_ml_{market_key}")
        st.metric("Automatic home field", f"{automatic_home_field:.1f} pts")
    st.caption("Use the exact sportsbook line and both side prices. Spread/total EV and price edge use these odds instead of assuming -110.")
    st.caption(_safe_text(hfa_info.get("source", "Rolling home-field model")))

    st.markdown("### Game environment")
    roof_options = ["outdoors", "dome", "closed", "open"]
    roof_default = defaults["roof"].lower() if defaults["roof"].lower() in roof_options else "outdoors"
    c4, c5, c6, c7 = st.columns(4)
    with c4:
        precipitation = st.selectbox("Precipitation", ["None", "Rain", "Heavy Rain", "Snow", "Heavy Snow"], key=f"nfl_precip_{market_key}")
    with c5:
        roof = st.selectbox("Roof", roof_options, index=roof_options.index(roof_default), key=f"nfl_roof_{market_key}")
    with c6:
        temperature = st.number_input("Temperature °F", value=float(defaults["temperature"]), step=1.0, key=f"nfl_temp_{market_key}")
    with c7:
        wind = st.number_input("Wind mph", value=float(defaults["wind"]), min_value=0.0, step=1.0, key=f"nfl_wind_{market_key}")
'''
    text = replace_once(text, old_market_ui, new_market_ui, "game market UI")

    old_game_selection = '''    home_cover = simulation["home_cover"]
    spread_edge_home = projection["margin"] + home_spread
    if home_cover >= 0.5:
        spread_pick, spread_probability, spread_edge, spread_pick_home = f"{home_team} {home_spread:+.1f}", home_cover, spread_edge_home, True
    else:
        spread_pick, spread_probability, spread_edge, spread_pick_home = f"{away_team} {-home_spread:+.1f}", 1 - home_cover, -spread_edge_home, False
    spread_confluence, spread_reasons = _spread_confluence(
        spread_pick_home, spread_edge, away_rating, home_rating, away_lineup_summary, home_lineup_summary, reliability
    )
    spread_grade = _grade_spread(spread_probability, spread_edge, reliability, spread_confluence)

    over_probability = simulation["over"]
    if over_probability >= 0.5:
        total_pick, total_probability, total_edge, over_pick = f"Over {market_total:.1f}", over_probability, projection["total"] - market_total, True
    else:
        total_pick, total_probability, total_edge, over_pick = f"Under {market_total:.1f}", 1 - over_probability, market_total - projection["total"], False
    total_confluence, total_reasons = _total_confluence(
        over_pick, total_edge, projection, away_rating, home_rating, weather_total_adjustment, reliability
    )
    total_grade = _grade_total_direction(total_pick, total_probability, total_edge, reliability, total_confluence)
'''
    new_game_selection = '''    home_cover = simulation["home_cover"]
    away_cover = 1.0 - home_cover
    spread_edge_home = projection["margin"] + home_spread
    raw_home_spread_implied = american_implied_probability(home_spread_odds)
    raw_away_spread_implied = american_implied_probability(away_spread_odds)
    spread_market_sum = max(raw_home_spread_implied + raw_away_spread_implied, 1e-9)
    home_spread_novig = raw_home_spread_implied / spread_market_sum
    away_spread_novig = raw_away_spread_implied / spread_market_sum
    home_spread_ev = expected_value_per_unit(home_cover, home_spread_odds)
    away_spread_ev = expected_value_per_unit(away_cover, away_spread_odds)
    if home_spread_ev >= away_spread_ev:
        spread_pick = f"{home_team} {home_spread:+.1f}"
        spread_probability, spread_edge, spread_pick_home = home_cover, spread_edge_home, True
        spread_pick_odds, spread_implied = home_spread_odds, home_spread_novig
        spread_ev = home_spread_ev
    else:
        spread_pick = f"{away_team} {-home_spread:+.1f}"
        spread_probability, spread_edge, spread_pick_home = away_cover, -spread_edge_home, False
        spread_pick_odds, spread_implied = away_spread_odds, away_spread_novig
        spread_ev = away_spread_ev
    spread_price_edge = spread_probability - spread_implied
    spread_confluence, spread_reasons = _spread_confluence(
        spread_pick_home, spread_edge, away_rating, home_rating, away_lineup_summary, home_lineup_summary, reliability
    )
    spread_grade = _grade_spread(spread_probability, spread_edge, reliability, spread_confluence)
    if spread_price_edge <= 0 or spread_ev <= 0:
        spread_grade = "Non-Edge Spread"

    over_probability = simulation["over"]
    under_probability = 1.0 - over_probability
    raw_over_implied = american_implied_probability(total_over_odds)
    raw_under_implied = american_implied_probability(total_under_odds)
    total_market_sum = max(raw_over_implied + raw_under_implied, 1e-9)
    over_novig = raw_over_implied / total_market_sum
    under_novig = raw_under_implied / total_market_sum
    over_ev = expected_value_per_unit(over_probability, total_over_odds)
    under_ev = expected_value_per_unit(under_probability, total_under_odds)
    if over_ev >= under_ev:
        total_pick = f"Over {market_total:.1f}"
        total_probability, total_edge, over_pick = over_probability, projection["total"] - market_total, True
        total_pick_odds, total_implied, total_ev = total_over_odds, over_novig, over_ev
    else:
        total_pick = f"Under {market_total:.1f}"
        total_probability, total_edge, over_pick = under_probability, market_total - projection["total"], False
        total_pick_odds, total_implied, total_ev = total_under_odds, under_novig, under_ev
    total_price_edge = total_probability - total_implied
    total_confluence, total_reasons = _total_confluence(
        over_pick, total_edge, projection, away_rating, home_rating, weather_total_adjustment, reliability
    )
    total_grade = _grade_total_direction(total_pick, total_probability, total_edge, reliability, total_confluence)
    if total_price_edge <= 0 or total_ev <= 0:
        total_grade = "Non-Edge Total"
'''
    text = replace_once(text, old_game_selection, new_game_selection, "price-aware spread/total selection")

    text = replace_once(
        text,
        '    _market_card("Spread", margin_text, spread_pick, spread_probability, f"{spread_edge:+.1f} pts", spread_grade, spread_confluence, spread_reasons)\n'
        '    _market_card("Total", f"{projection[\'total\']:.1f} points", total_pick, total_probability, f"{total_edge:+.1f} pts", total_grade, total_confluence, total_reasons)\n',
        '    _market_card("Spread", margin_text, spread_pick, spread_probability, f"{spread_edge:+.1f} pts • {spread_price_edge:+.1%} price • {spread_pick_odds:+d}", spread_grade, spread_confluence, spread_reasons)\n'
        '    _market_card("Total", f"{projection[\'total\']:.1f} points", total_pick, total_probability, f"{total_edge:+.1f} pts • {total_price_edge:+.1%} price • {total_pick_odds:+d}", total_grade, total_confluence, total_reasons)\n',
        "market-card price display",
    )

    text = replace_once(
        text,
        '        "Home Score High": round(simulation["home_high"], 1), "Market Home Spread": home_spread,\n'
        '        "Market Total": market_total, "Away ML": away_ml, "Home ML": home_ml,\n',
        '        "Home Score High": round(simulation["home_high"], 1), "Market Home Spread": home_spread,\n'
        '        "Market Total": market_total, "Home Spread Odds": home_spread_odds, "Away Spread Odds": away_spread_odds,\n'
        '        "Total Over Odds": total_over_odds, "Total Under Odds": total_under_odds, "Away ML": away_ml, "Home ML": home_ml,\n',
        "slate-row market prices",
    )

    old_prop_editor = '''        with st.expander("Sportsbook prop lines and prices", expanded=False):
            st.caption("Only use this section when automatic player lines are unavailable or need an override.")
            input_columns = [
                "Team", "Player", "Market", "Projection", "Market Line", "Over Odds", "Under Odds", "Line Source",
            ]
            prop_inputs = st.data_editor(
                prop_base,
                use_container_width=True,
                hide_index=True,
                key=f"nfl_prop_inputs_{market_key}",
                column_order=input_columns,
                disabled=[column for column in input_columns if column not in ["Market Line", "Over Odds", "Under Odds"]],
                column_config={
                    "Projection": st.column_config.NumberColumn(format="%.1f"),
                    "Market Line": st.column_config.NumberColumn("Sportsbook Line", min_value=0.0, step=0.5, format="%.1f"),
                    "Over Odds": st.column_config.NumberColumn(step=5),
                    "Under Odds": st.column_config.NumberColumn(step=5),
                },
            )
        evaluated_props = _evaluate_prop_rows(prop_inputs)
'''
    new_prop_editor = '''        with st.expander("QB / RB / WR yard lines and prices", expanded=True):
            st.caption("Enter the sportsbook line, Over odds and Under odds. Only QB passing yards, RB rushing/receiving yards and WR receiving yards are wager-input markets here.")
            wager_mask = (
                ((prop_base["Position"].astype(str) == "QB") & (prop_base["Market"].astype(str) == "Passing Yards"))
                | ((prop_base["Position"].astype(str) == "RB") & (prop_base["Market"].astype(str).isin(["Rushing Yards", "Receiving Yards"])))
                | ((prop_base["Position"].astype(str) == "WR") & (prop_base["Market"].astype(str) == "Receiving Yards"))
            )
            yard_inputs = prop_base.loc[wager_mask].copy()
            input_columns = [
                "Team", "Player", "Market", "Projection", "Market Line", "Over Odds", "Under Odds", "Line Source",
            ]
            edited_yards = st.data_editor(
                yard_inputs,
                use_container_width=True,
                hide_index=True,
                key=f"nfl_yard_inputs_{market_key}",
                column_order=input_columns,
                disabled=[column for column in input_columns if column not in ["Market Line", "Over Odds", "Under Odds"]],
                column_config={
                    "Projection": st.column_config.NumberColumn(format="%.1f"),
                    "Market Line": st.column_config.NumberColumn("Sportsbook Line", min_value=0.0, step=0.5, format="%.1f"),
                    "Over Odds": st.column_config.NumberColumn("Over Odds", step=5),
                    "Under Odds": st.column_config.NumberColumn("Under Odds", step=5),
                },
            )
            prop_inputs = prop_base.copy()
            prop_inputs.loc[~wager_mask, "Market Line"] = np.nan
            prop_inputs.loc[~wager_mask, "Line Source"] = ""
            if not edited_yards.empty:
                for column in ["Market Line", "Over Odds", "Under Odds", "Line Source"]:
                    if column in edited_yards.columns:
                        prop_inputs.loc[edited_yards.index, column] = edited_yards[column]
        evaluated_props = _evaluate_prop_rows(prop_inputs)
'''
    text = replace_once(text, old_prop_editor, new_prop_editor, "yard-only price editor")

    old_tracker_signature = '''def _game_tracker_rows(
    slate_date: str, season: int, week: int, game_id: str, game: str,
    spread_pick: str, spread_probability: float, spread_grade: str, spread_confluence: int,
    total_pick: str, total_probability: float, total_grade: str, total_confluence: int,
    ml_pick: str, ml_probability: float, ml_odds: int, ml_grade: str, ml_confluence: int,
    reliability: float, data_confidence: float, personnel_confidence: float,
    projected_away: float, projected_home: float, notes: str,
) -> list[dict[str, Any]]:
    candidates = [
        ("Spread", spread_pick, -110, spread_probability, spread_grade, spread_confluence),
        ("Total", total_pick, -110, total_probability, total_grade, total_confluence),
        ("Moneyline", ml_pick, ml_odds, ml_probability, ml_grade, ml_confluence),
    ]
    rows = []
    for bet_type, selection, odds, probability, grade, confluence in candidates:
        if not _is_graded_game_play(grade):
            continue
        implied = american_implied_probability(odds)
'''
    new_tracker_signature = '''def _game_tracker_rows(
    slate_date: str, season: int, week: int, game_id: str, game: str,
    spread_pick: str, spread_probability: float, spread_odds: int, spread_implied: float,
    spread_grade: str, spread_confluence: int,
    total_pick: str, total_probability: float, total_odds: int, total_implied: float,
    total_grade: str, total_confluence: int,
    ml_pick: str, ml_probability: float, ml_odds: int, ml_grade: str, ml_confluence: int,
    reliability: float, data_confidence: float, personnel_confidence: float,
    projected_away: float, projected_home: float, notes: str,
) -> list[dict[str, Any]]:
    candidates = [
        ("Spread", spread_pick, spread_odds, spread_implied, spread_probability, spread_grade, spread_confluence),
        ("Total", total_pick, total_odds, total_implied, total_probability, total_grade, total_confluence),
        ("Moneyline", ml_pick, ml_odds, None, ml_probability, ml_grade, ml_confluence),
    ]
    rows = []
    for bet_type, selection, odds, implied_override, probability, grade, confluence in candidates:
        if not _is_graded_game_play(grade):
            continue
        implied = float(implied_override) if implied_override is not None else american_implied_probability(odds)
'''
    text = replace_once(text, old_tracker_signature, new_tracker_signature, "game tracker prices")

    text = replace_once(
        text,
        '            spread_pick, spread_probability, spread_grade, spread_confluence,\n'
        '            total_pick, total_probability, total_grade, total_confluence,\n',
        '            spread_pick, spread_probability, spread_pick_odds, spread_implied, spread_grade, spread_confluence,\n'
        '            total_pick, total_probability, total_pick_odds, total_implied, total_grade, total_confluence,\n',
        "tracker call prices",
    )

    text = replace_once(
        text,
        '    st.caption("NFL v3.3 automated slate • regression-calibrated QB passing yards • in-depth QB/RB/WR/TE props")\n',
        '    st.caption("NFL v4.2 regression slate • price-aware spread/total markets • regression QB/RB/WR yard props")\n',
        "NFL caption",
    )

    PATH.write_text(text, encoding="utf-8")

    required = [
        "Home Spread Odds", "Away Spread Odds", "Total Over Odds", "Total Under Odds",
        "spread_price_edge", "total_price_edge", "QB / RB / WR yard lines and prices",
        "spread_pick_odds, spread_implied", "total_pick_odds, total_implied",
    ]
    final = PATH.read_text(encoding="utf-8")
    missing = [item for item in required if item not in final]
    if missing:
        raise RuntimeError(f"Patch verification missing: {missing}")
    print("NFL v4.2 sportsbook odds-input patch applied")


if __name__ == "__main__":
    main()
