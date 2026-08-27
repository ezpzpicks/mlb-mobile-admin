from pathlib import Path


PATH = Path("builders/nfl_builder.py")


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if old not in source:
        raise SystemExit(f"{label} not found")
    return source.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")

    # Make the player-prop projection call resilient to optional-argument changes.
    text = replace_once(
        text,
        '''                defense_profiles, rating, opponent_rating, projection, weather_adjustment, team_roles,\n                pregame_team_total=pregame_team_total,\n                pregame_team_total_source=pregame_team_total_source,\n''',
        '''                defense_profiles, rating, opponent_rating, projection, weather_adjustment,\n                role_context=team_roles,\n                pregame_team_total=pregame_team_total,\n                pregame_team_total_source=pregame_team_total_source,\n''',
        "player prop optional-argument call",
    )

    # Remove manual moneyline fields. The NFL builder only needs spread + total markets.
    text = replace_once(
        text,
        '''    away_ml_input = st.number_input(\n        f"{away_team} moneyline", value=None, step=5,\n        key=f"nfl_away_ml_{market_key}",\n    )\n\n''',
        "",
        "away moneyline input",
    )
    text = replace_once(
        text,
        '''    home_ml_input = st.number_input(\n        f"{home_team} moneyline", value=None, step=5,\n        key=f"nfl_home_ml_{market_key}",\n    )\n\n''',
        "",
        "home moneyline input",
    )

    text = replace_once(
        text,
        '''    st.caption("Manual entry only — no sportsbook/API lines or prices are loaded into the NFL builder.")\n''',
        '''    st.caption("Manual entry only — no sportsbook/API lines or prices are loaded into the NFL builder.")\n    st.caption("For spreads, each team is one side of the market, so each team has one spread price. Over/Under prices apply to the game total and player props.")\n''',
        "manual market caption",
    )

    text = replace_once(
        text,
        '''    total_market_ready = all(value is not None for value in [market_total_input, total_over_odds_input, total_under_odds_input])\n    ml_market_ready = all(value is not None for value in [away_ml_input, home_ml_input])\n    game_markets_ready = spread_market_ready and total_market_ready and ml_market_ready\n    if not game_markets_ready:\n        st.info("Enter both team spreads/prices, both moneylines, and the total with Over/Under prices before saving the game.")\n\n''',
        '''    total_market_ready = all(value is not None for value in [market_total_input, total_over_odds_input, total_under_odds_input])\n    game_markets_ready = spread_market_ready and total_market_ready\n    if not game_markets_ready:\n        st.info("Enter both team spreads/prices and the total with Over/Under prices before saving the game.")\n\n''',
        "game market readiness",
    )

    text = replace_once(
        text,
        '''    away_ml = int(away_ml_input) if away_ml_input is not None else -110\n    home_ml = int(home_ml_input) if home_ml_input is not None else -110\n''',
        '''    # Moneyline is intentionally disabled in the manual NFL builder.\n    away_ml = -110\n    home_ml = -110\n''',
        "moneyline values",
    )

    text = replace_once(
        text,
        '''    home_win = simulation["home_win"]\n    if home_win >= 0.5:\n        ml_pick, ml_probability, ml_odds, ml_pick_home = home_team, home_win, home_ml, True\n    else:\n        ml_pick, ml_probability, ml_odds, ml_pick_home = away_team, 1 - home_win, away_ml, False\n    ml_edge = probability_edge(ml_probability, ml_odds)\n    ml_confluence, ml_reasons = _moneyline_confluence(\n        ml_pick_home, ml_edge, away_rating, home_rating, away_lineup_summary, home_lineup_summary, reliability\n    )\n    ml_grade = _grade_moneyline(ml_probability, ml_edge, reliability, ml_confluence)\n    if not ml_market_ready:\n        ml_grade = "No market line"\n\n''',
        '''    # Keep contract-compatible placeholders, but do not grade or track moneyline.\n    ml_pick = ""\n    ml_probability = 0.0\n    ml_odds = -110\n    ml_edge = 0.0\n    ml_confluence = 0\n    ml_reasons: list[str] = []\n    ml_grade = "No market line"\n\n''',
        "moneyline grading block",
    )

    text = replace_once(
        text,
        '''    spread_edge_text = f"{spread_edge:+.1f} pts • {spread_price_edge:+.1%} price • {spread_pick_odds:+d}" if spread_market_ready else "Manual spread lines and prices required"\n    total_edge_text = f"{total_edge:+.1f} pts • {total_price_edge:+.1%} price • {total_pick_odds:+d}" if total_market_ready else "Manual total line and prices required"\n    ml_edge_text = f"{ml_edge:+.1%}" if ml_market_ready else "Manual moneyline prices required"\n    _market_card("Spread", margin_text, spread_pick, spread_probability, spread_edge_text, spread_grade, spread_confluence, spread_reasons)\n    _market_card("Total", f"{projection['total']:.1f} points", total_pick, total_probability, total_edge_text, total_grade, total_confluence, total_reasons)\n    _market_card("Moneyline", f"{ml_pick} {ml_probability:.1%} win probability", ml_pick, ml_probability, ml_edge_text, ml_grade, ml_confluence, ml_reasons)\n''',
        '''    spread_edge_text = f"{spread_edge:+.1f} pts • {spread_price_edge:+.1%} price • {spread_pick_odds:+d}" if spread_market_ready else "Manual spread lines and prices required"\n    total_edge_text = f"{total_edge:+.1f} pts • {total_price_edge:+.1%} price • {total_pick_odds:+d}" if total_market_ready else "Manual total line and prices required"\n    _market_card("Spread", margin_text, spread_pick, spread_probability, spread_edge_text, spread_grade, spread_confluence, spread_reasons)\n    _market_card("Total", f"{projection['total']:.1f} points", total_pick, total_probability, total_edge_text, total_grade, total_confluence, total_reasons)\n''',
        "moneyline market card",
    )

    text = replace_once(
        text,
        '''        "Total Over Odds": total_over_odds, "Total Under Odds": total_under_odds, "Away ML": away_ml, "Home ML": home_ml,\n''',
        '''        "Total Over Odds": total_over_odds, "Total Under Odds": total_under_odds, "Away ML": "", "Home ML": "",\n''',
        "slate moneyline prices",
    )
    text = replace_once(
        text,
        '''        "Total Confluence": total_confluence, "ML Pick": ml_pick, "ML Probability": round(ml_probability, 4),\n        "ML Odds": ml_odds, "ML Edge": round(ml_edge, 4), "ML Grade": ml_grade, "ML Confluence": ml_confluence,\n''',
        '''        "Total Confluence": total_confluence, "ML Pick": "", "ML Probability": "",\n        "ML Odds": "", "ML Edge": "", "ML Grade": "No market line", "ML Confluence": "",\n''',
        "slate moneyline results",
    )

    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
