from pathlib import Path


PATH = Path("builders/mlb_builder.py")
text = PATH.read_text(encoding="utf-8")

function_marker = "\ndef render_auto_matchup_builder("
function_start = text.index(function_marker)

market_marker = '    st.divider()\n    st.subheader("Market Inputs")\n'
market_start = text.index(market_marker, function_start)
market_end_marker = "    # Compute the shared six-inning pace before opener controls"
market_end = text.index(market_end_marker, market_start)

later_start_marker = "    total_input_col1, total_input_col2, total_input_col3 = st.columns(3)\n"
later_start = text.index(later_start_marker, market_end)
later_end_marker = "    home_k_6ip = home_k_6ip_precal\n"
later_end = text.index(later_end_marker, later_start)

new_market = '''    st.divider()
    st.subheader("Market Inputs")
    st.caption(
        "Enter all standard lines and prices below, then tap Apply Lines & Odds once. "
        "The builder will not rerun the full matchup model while you edit each field."
    )

    with st.form(key=f"mlb_market_inputs_{game.get('game_pk')}", clear_on_submit=False):
        input_col1, input_col2 = st.columns(2)
        with input_col1:
            home_k_line = st.number_input(f"{home_pitcher} K Line", value=float(home_k_defaults["line"]), step=0.5, key=f"home_k_{game.get('game_pk')}")
            home_k_over_odds = st.number_input(f"{home_pitcher} Over Odds", value=int(home_k_defaults["odds"]), step=5, key=f"home_k_over_odds_{game.get('game_pk')}")
            home_k_under_odds = st.number_input(f"{home_pitcher} Under Odds", value=int(home_k_defaults["odds"]), step=5, key=f"home_k_under_odds_{game.get('game_pk')}")
            home_ml_odds = st.number_input(f"{home_team} Moneyline Odds", value=int(home_ml_default), step=5, key=f"home_ml_{game.get('game_pk')}")
        with input_col2:
            away_k_line = st.number_input(f"{away_pitcher} K Line", value=float(away_k_defaults["line"]), step=0.5, key=f"away_k_{game.get('game_pk')}")
            away_k_over_odds = st.number_input(f"{away_pitcher} Over Odds", value=int(away_k_defaults["odds"]), step=5, key=f"away_k_over_odds_{game.get('game_pk')}")
            away_k_under_odds = st.number_input(f"{away_pitcher} Under Odds", value=int(away_k_defaults["odds"]), step=5, key=f"away_k_under_odds_{game.get('game_pk')}")
            away_ml_odds = st.number_input(f"{away_team} Moneyline Odds", value=int(away_ml_default), step=5, key=f"away_ml_{game.get('game_pk')}")

        st.markdown("**Game Total / First Inning**")
        total_input_col1, total_input_col2, total_input_col3 = st.columns(3)
        with total_input_col1:
            total_runs_line = st.number_input("Game Total Line", value=8.5, step=0.5, key=f"total_runs_line_{game.get('game_pk')}")
        with total_input_col2:
            total_over_odds = st.number_input("Game Total Over Odds", value=-110, step=5, key=f"total_over_odds_{game.get('game_pk')}")
        with total_input_col3:
            total_under_odds = st.number_input("Game Total Under Odds", value=-110, step=5, key=f"total_under_odds_{game.get('game_pk')}")
        nrfi_price_col, yrfi_price_col = st.columns(2)
        with nrfi_price_col:
            nrfi_odds = st.number_input("NRFI Odds", value=-110, step=5, key=f"nrfi_odds_{game.get('game_pk')}")
        with yrfi_price_col:
            yrfi_odds = st.number_input("YRFI Odds", value=-110, step=5, key=f"yrfi_odds_{game.get('game_pk')}")

        market_inputs_submitted = st.form_submit_button(
            "Apply Lines & Odds",
            type="primary",
            use_container_width=True,
        )

    st.caption("Moneyline edges are graded against no-vig fair prices. Total Over/Under and NRFI/YRFI grades use their own entered prices and require positive EV.")
    if market_inputs_submitted:
        st.success("Lines and odds applied. The market grades below are updated.")

'''

# Remove the later total/NRFI/YRFI widget block first so offsets stay simple.
text = text[:later_start] + text[later_end:]
# Replace only the active builder's standard market block.
text = text[:market_start] + new_market + text[market_end:]

PATH.write_text(text, encoding="utf-8")
