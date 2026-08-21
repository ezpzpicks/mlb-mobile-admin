from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one old block, found {count}")
    return text.replace(old, new, 1)


# Repair the calibration module's markdown source if this is the first generation run.
module_path = Path("builders/cfb_market_calibration.py")
module = module_path.read_text()
bad_markdown = '''            st.markdown(
                f"**Calibrated margin residual SD:** {MARGIN_RESIDUAL_SD:.2f} points  "
                f"  
**Spread ATS calibration:** {'validated' if ATS_CALIBRATION_PROVEN else 'not historically validated; conservative grade gates retained'}"
            )'''
good_markdown = '''            st.markdown(
                f"**Calibrated margin residual SD:** {MARGIN_RESIDUAL_SD:.2f} points  \\n"
                f"**Spread ATS calibration:** {'validated' if ATS_CALIBRATION_PROVEN else 'not historically validated; conservative grade gates retained'}"
            )'''
if bad_markdown in module:
    module = module.replace(bad_markdown, good_markdown, 1)
module_path.write_text(module)


builder_path = Path("builders/cfb_builder.py")
builder = builder_path.read_text()
old_market_ui = '''    c1, c2, c3, c4 = st.columns(4)
    spread_default = _num(game.get("Home Spread"), 0.0)
    total_default = _num(game.get("Total"), 56.0)
    if not math.isfinite(spread_default): spread_default = 0.0
    if not math.isfinite(total_default): total_default = 56.0
    market_key = _text(game.get("Game ID"), f"{season}_{week}_{game['Away Team']}_{game['Home Team']}")
    with c1: market_spread = st.number_input("Home spread", -60.0, 60.0, float(spread_default), 0.5, key=f"spread_{market_key}")
    with c2: market_total = st.number_input("Total", 20.0, 120.0, float(total_default), 0.5, key=f"total_{market_key}")
    with c3: away_ml = st.number_input("Away moneyline", -5000.0, 5000.0, float(_num(game.get("Away ML"), 0.0)), 5.0, key=f"aml_{market_key}")
    with c4: home_ml = st.number_input("Home moneyline", -5000.0, 5000.0, float(_num(game.get("Home ML"), 0.0)), 5.0, key=f"hml_{market_key}")
'''
new_market_ui = '''    spread_default = _num(game.get("Home Spread"), 0.0)
    total_default = _num(game.get("Total"), 56.0)
    if not math.isfinite(spread_default): spread_default = 0.0
    if not math.isfinite(total_default): total_default = 56.0
    market_key = _text(game.get("Game ID"), f"{season}_{week}_{game['Away Team']}_{game['Home Team']}")

    st.markdown("### Sportsbook lines and prices")
    spread_col, total_col = st.columns(2)
    with spread_col:
        market_spread = st.number_input("Home spread line", -60.0, 60.0, float(spread_default), 0.5, key=f"spread_{market_key}")
        home_spread_odds = int(st.number_input(f"{game['Home Team']} spread odds", -5000, 5000, -110, 5, key=f"home_spread_odds_{market_key}"))
        away_spread_odds = int(st.number_input(f"{game['Away Team']} spread odds", -5000, 5000, -110, 5, key=f"away_spread_odds_{market_key}"))
    with total_col:
        market_total = st.number_input("Game total line", 20.0, 120.0, float(total_default), 0.5, key=f"total_{market_key}")
        total_over_odds = int(st.number_input("Over odds", -5000, 5000, -110, 5, key=f"total_over_odds_{market_key}"))
        total_under_odds = int(st.number_input("Under odds", -5000, 5000, -110, 5, key=f"total_under_odds_{market_key}"))
    st.caption("Spread and total grades use the entered side prices, no-vig implied probability, model probability, and EV. The sportsbook line itself is never a regression predictor.")

    with st.expander("Moneyline (optional / shadow testing)", expanded=False):
        c3, c4 = st.columns(2)
        with c3:
            away_ml = st.number_input("Away moneyline", -5000.0, 5000.0, float(_num(game.get("Away ML"), 0.0)), 5.0, key=f"aml_{market_key}")
        with c4:
            home_ml = st.number_input("Home moneyline", -5000.0, 5000.0, float(_num(game.get("Home ML"), 0.0)), 5.0, key=f"hml_{market_key}")
'''
builder = replace_once(builder, old_market_ui, new_market_ui, "sportsbook UI")

old_eval = '''        result = evaluate_game(
            game, ratings, away_personnel, home_personnel, environment,
            market_spread, market_total, away_ml, home_ml,
            {"spread": spread_available, "total": total_available, "moneyline": moneyline_available},
        )'''
new_eval = '''        result = evaluate_game(
            game, ratings, away_personnel, home_personnel, environment,
            market_spread, market_total, away_ml, home_ml,
            market_availability={"spread": spread_available, "total": total_available, "moneyline": moneyline_available},
            home_spread_odds=home_spread_odds,
            away_spread_odds=away_spread_odds,
            total_over_odds=total_over_odds,
            total_under_odds=total_under_odds,
        )'''
builder = replace_once(builder, old_eval, new_eval, "evaluate_game pricing args")

old_fingerprint = '''        fingerprint_fields = [
            result["game"].get("Game ID"), result["game"].get("Home Spread"),
            result["game"].get("Total"), result["game"].get("Away ML"),
            result["game"].get("Home ML"), result["reliability"], MODEL_VERSION,
        ]'''
new_fingerprint = '''        fingerprint_fields = [
            result["game"].get("Game ID"), result["game"].get("Home Spread"),
            result["game"].get("Total"), result["game"].get("Home Spread Odds"),
            result["game"].get("Away Spread Odds"), result["game"].get("Total Over Odds"),
            result["game"].get("Total Under Odds"), result["game"].get("Away ML"),
            result["game"].get("Home ML"), result["reliability"], MODEL_VERSION,
        ]'''
builder = replace_once(builder, old_fingerprint, new_fingerprint, "auto-save fingerprint")
builder_path.write_text(builder)


app_path = Path("app_mobile_admin.py")
app = app_path.read_text()
app = app.replace('"CFB": "cfb-v2.0-team-score-regression-2026-08-21",', '"CFB": "cfb-v2.1-calibrated-pricing-2026-08-21",')
old_cfb = '''elif selected_sport == "CFB":
    from builders import cfb_builder
    from builders.cfb_game_regression import install_regression_layer
    install_regression_layer(cfb_builder)
    cfb_builder.MODEL_VERSION = "cfb-v2.0-team-score-regression-2026-08-21"
    cfb_builder.render()'''
new_cfb = '''elif selected_sport == "CFB":
    from builders import cfb_builder
    from builders.cfb_game_regression import install_regression_layer
    from builders.cfb_market_calibration import install_market_calibration
    install_regression_layer(cfb_builder)
    install_market_calibration(cfb_builder)
    cfb_builder.MODEL_VERSION = "cfb-v2.1-calibrated-pricing-2026-08-21"
    cfb_builder.render()'''
app = replace_once(app, old_cfb, new_cfb, "CFB app install block")
app = app.replace(
    "CFB now uses the validated team-score regression for spread/margin while retaining the existing totals engine and live personnel/environment overlays.",
    "CFB now uses the validated team-score regression plus calibrated margin volatility and price-aware spread/total evaluation while retaining live personnel/environment overlays.",
)
app_path.write_text(app)

print("Applied CFB v2.1 calibrated pricing integration")
