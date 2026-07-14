from datetime import date

import pandas as pd
import streamlit as st

from shared.modeling import (
    american_implied_probability,
    clamp,
    expected_value_per_unit,
    market_grade,
    normal_cdf,
    probability_edge,
    reliability_score,
)
from shared.storage import append_row, read_sheet, sheets_ready, write_sheet

MODEL_VERSION = "cbb-v0.1-rotation-foundation-2026-07-13"
RATINGS_TAB = "cbb_team_ratings"
SLATE_TAB = "cbb_daily_slate"
TRACKER_TAB = "cbb_bet_tracker"

RATING_COLUMNS = [
    "Team", "AdjOE", "AdjDE", "Tempo", "eFG Off", "eFG Def", "TO Off", "TO Def",
    "ORB Rate", "FT Rate", "3PA Rate", "Rotation Adjustment", "Data Confidence", "Updated",
]
SLATE_COLUMNS = [
    "Date", "Game", "Away Team", "Home Team", "Projected Away", "Projected Home",
    "Projected Margin", "Projected Total", "Projected Possessions", "Market Home Spread", "Market Total",
    "Spread Pick", "Spread Probability", "Spread Edge", "Spread Grade",
    "Total Pick", "Total Probability", "Total Edge", "Total Grade",
    "ML Pick", "ML Probability", "ML Odds", "ML Edge", "ML Grade",
    "Reliability", "Rotation Confidence", "Model Version", "Notes",
]
TRACKER_COLUMNS = [
    "Date", "Game", "Bet Type", "Selection", "Odds/Line", "Model Probability",
    "Implied Probability", "Edge", "Expected Value", "Grade", "Result",
    "Reliability", "Model Version", "Notes",
]


def _num(value, default=0.0):
    try:
        if value in [None, ""] or pd.isna(value):
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _load_ratings():
    return read_sheet(RATINGS_TAB, RATING_COLUMNS)


def _defaults(ratings, team):
    if ratings is not None and not ratings.empty:
        match = ratings[ratings["Team"].astype(str).str.strip().str.lower() == str(team).strip().lower()]
        if not match.empty:
            return match.iloc[0].to_dict()
    return {
        "Team": team, "AdjOE": 105.0, "AdjDE": 105.0, "Tempo": 68.0,
        "eFG Off": 52.0, "eFG Def": 52.0, "TO Off": 18.0, "TO Def": 18.0,
        "ORB Rate": 30.0, "FT Rate": 30.0, "3PA Rate": 40.0,
        "Rotation Adjustment": 0.0, "Data Confidence": 55.0, "Updated": "",
    }


def _project(away, home, settings):
    league_efficiency = 105.0
    possessions = (
        (_num(away["Tempo"], 68) + _num(home["Tempo"], 68)) / 2.0
        + settings["pace_adjustment"]
    )
    possessions = clamp(possessions, 58, 82)

    away_efficiency = _num(away["AdjOE"], 105) + (_num(home["AdjDE"], 105) - league_efficiency)
    home_efficiency = _num(home["AdjOE"], 105) + (_num(away["AdjDE"], 105) - league_efficiency)

    away_shooting = 0.30 * ((_num(away["eFG Off"], 52) - 52) + (52 - _num(home["eFG Def"], 52)))
    home_shooting = 0.30 * ((_num(home["eFG Off"], 52) - 52) + (52 - _num(away["eFG Def"], 52)))
    away_turnovers = -0.22 * ((_num(away["TO Off"], 18) - 18) + (_num(home["TO Def"], 18) - 18))
    home_turnovers = -0.22 * ((_num(home["TO Off"], 18) - 18) + (_num(away["TO Def"], 18) - 18))
    away_second_chance = 0.11 * (_num(away["ORB Rate"], 30) - 30)
    home_second_chance = 0.11 * (_num(home["ORB Rate"], 30) - 30)
    away_free_throws = 0.07 * (_num(away["FT Rate"], 30) - 30)
    home_free_throws = 0.07 * (_num(home["FT Rate"], 30) - 30)

    away_efficiency += away_shooting + away_turnovers + away_second_chance + away_free_throws
    home_efficiency += home_shooting + home_turnovers + home_second_chance + home_free_throws
    away_efficiency += _num(away["Rotation Adjustment"], 0) * 100 / possessions
    home_efficiency += _num(home["Rotation Adjustment"], 0) * 100 / possessions

    away_score = away_efficiency * possessions / 100.0
    home_score = home_efficiency * possessions / 100.0
    home_score += settings["home_court"] / 2.0
    away_score -= settings["home_court"] / 2.0
    home_score += settings["matchup_margin_adjustment"] / 2.0
    away_score -= settings["matchup_margin_adjustment"] / 2.0
    home_score += settings["matchup_total_adjustment"] / 2.0
    away_score += settings["matchup_total_adjustment"] / 2.0

    away_score = clamp(away_score, 42, 110)
    home_score = clamp(home_score, 42, 110)
    return (
        round(away_score, 1), round(home_score, 1), round(home_score - away_score, 1),
        round(home_score + away_score, 1), round(possessions, 1),
    )


def _render_ratings():
    st.subheader("College Basketball Team Ratings")
    st.caption("AdjOE and AdjDE are points per 100 possessions; lower AdjDE is better. Percentage-style fields should be entered as numbers such as 52.4, not 0.524.")
    ratings = _load_ratings()
    uploaded = st.file_uploader("Import CBB ratings CSV", type=["csv"], key="cbb_ratings_csv")
    if uploaded is not None:
        imported = pd.read_csv(uploaded)
        for column in RATING_COLUMNS:
            if column not in imported.columns:
                imported[column] = ""
        ratings = imported[RATING_COLUMNS]
    if ratings.empty:
        ratings = pd.DataFrame([_defaults(ratings, "Example Team")], columns=RATING_COLUMNS)
    edited = st.data_editor(ratings, use_container_width=True, hide_index=True, num_rows="dynamic", key="cbb_ratings_editor")
    if st.button("Save CBB Ratings", type="primary", use_container_width=True):
        edited["Updated"] = str(date.today())
        if write_sheet(RATINGS_TAB, edited, RATING_COLUMNS):
            st.success("College basketball ratings saved.")


def _team_inputs(prefix, team, row):
    st.markdown(f"**{team}**")
    fields = [
        ("AdjOE", 0.1, 105), ("AdjDE", 0.1, 105), ("Tempo", 0.1, 68),
        ("eFG Off", 0.1, 52), ("eFG Def", 0.1, 52), ("TO Off", 0.1, 18),
        ("TO Def", 0.1, 18), ("ORB Rate", 0.1, 30), ("FT Rate", 0.1, 30),
        ("3PA Rate", 0.1, 40), ("Rotation Adjustment", 0.25, 0), ("Data Confidence", 1.0, 55),
    ]
    for field, step, fallback in fields:
        row[field] = st.number_input(f"{prefix} {field}", value=_num(row.get(field), fallback), step=step, key=f"cbb_{prefix}_{field}")
    return row


def _render_build():
    ratings = _load_ratings()
    teams = sorted([x for x in ratings.get("Team", pd.Series(dtype=str)).astype(str).tolist() if x.strip()])
    st.subheader("College Basketball Matchup Builder")
    st.caption("Rotation-aware foundation model using adjusted efficiency, tempo, four-factor matchup inputs and player availability adjustments.")

    if teams:
        away_name = st.selectbox("Away team", teams, key="cbb_away")
        home_name = st.selectbox("Home team", [x for x in teams if x != away_name] or teams, key="cbb_home")
    else:
        st.info("No CBB ratings are saved yet. Enter teams manually or import a ratings CSV.")
        away_name = st.text_input("Away team", value="Away Team", key="cbb_away_manual")
        home_name = st.text_input("Home team", value="Home Team", key="cbb_home_manual")

    away, home = _defaults(ratings, away_name), _defaults(ratings, home_name)
    with st.expander("Team ratings and rotation inputs", expanded=not bool(teams)):
        ca, ch = st.columns(2)
        with ca:
            away = _team_inputs("Away", away_name, away)
        with ch:
            home = _team_inputs("Home", home_name, home)

    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread", value=-3.0, step=0.5, key="cbb_home_spread")
        home_ml = st.number_input("Home moneyline", value=-150, step=5, key="cbb_home_ml")
    with c2:
        market_total = st.number_input("Game total", value=142.0, step=0.5, key="cbb_total")
        away_ml = st.number_input("Away moneyline", value=130, step=5, key="cbb_away_ml")
    with c3:
        home_court = st.number_input("Home-court points", value=3.2, step=0.1, key="cbb_hca")
        rotation_confidence = st.slider("Rotation confidence", 0, 100, 65, key="cbb_rotation_confidence")

    with st.expander("Advanced matchup adjustments"):
        pace_adjustment = st.number_input("Possession adjustment", value=0.0, step=0.25, key="cbb_pace_adjust")
        matchup_margin_adjustment = st.number_input("Home matchup adjustment", value=0.0, step=0.25, key="cbb_margin_adjust")
        matchup_total_adjustment = st.number_input("Matchup total adjustment", value=0.0, step=0.25, key="cbb_total_adjust")
        notes = st.text_area("Notes", key="cbb_notes", placeholder="Projected minutes, questionable players, travel, rest, shooting matchup...")

    away_score, home_score, margin, projected_total, possessions = _project(away, home, {
        "home_court": home_court,
        "pace_adjustment": pace_adjustment,
        "matchup_margin_adjustment": matchup_margin_adjustment,
        "matchup_total_adjustment": matchup_total_adjustment,
    })

    spread_edge_home = margin + home_spread
    home_cover = normal_cdf(spread_edge_home / 10.8)
    if home_cover >= 0.5:
        spread_pick, spread_probability, spread_edge = f"{home_name} {home_spread:+.1f}", home_cover, spread_edge_home
    else:
        spread_pick, spread_probability, spread_edge = f"{away_name} {-home_spread:+.1f}", 1 - home_cover, -spread_edge_home
    spread_grade = market_grade(spread_edge, spread_probability)

    over_probability = normal_cdf((projected_total - market_total) / 12.5)
    if over_probability >= 0.5:
        total_pick, total_probability, total_edge = f"Over {market_total:.1f}", over_probability, projected_total - market_total
    else:
        total_pick, total_probability, total_edge = f"Under {market_total:.1f}", 1 - over_probability, market_total - projected_total
    total_grade = market_grade(total_edge, total_probability)

    home_win = normal_cdf(margin / 10.5)
    if home_win >= 0.5:
        ml_pick, ml_probability, ml_odds = home_name, home_win, home_ml
    else:
        ml_pick, ml_probability, ml_odds = away_name, 1 - home_win, away_ml
    ml_edge = probability_edge(ml_probability, ml_odds)
    ml_grade = market_grade(0, ml_probability, ml_edge)

    data_confidence = (_num(away["Data Confidence"], 55) + _num(home["Data Confidence"], 55)) / 2
    edge_strength = clamp(max(abs(spread_edge), abs(total_edge) / 1.7, abs(ml_edge) * 30) * 18, 0, 100)
    volatility_penalty = 10 if rotation_confidence < 45 else 4 if rotation_confidence < 65 else 0
    reliability = reliability_score(data_confidence, rotation_confidence, edge_strength, volatility_penalty)

    a, b, c, d = st.columns(4)
    a.metric(away_name, f"{away_score:.1f}")
    b.metric(home_name, f"{home_score:.1f}")
    c.metric("Projected margin", f"{home_name} {margin:+.1f}")
    d.metric("Possessions / total", f"{possessions:.1f} / {projected_total:.1f}")

    st.dataframe(pd.DataFrame([
        {"Market": "Spread", "Selection": spread_pick, "Probability": f"{spread_probability:.1%}", "Edge": f"{spread_edge:+.1f} pts", "Grade": spread_grade},
        {"Market": "Total", "Selection": total_pick, "Probability": f"{total_probability:.1%}", "Edge": f"{total_edge:+.1f} pts", "Grade": total_grade},
        {"Market": "Moneyline", "Selection": ml_pick, "Probability": f"{ml_probability:.1%}", "Edge": f"{ml_edge:+.1%}", "Grade": ml_grade},
    ]), use_container_width=True, hide_index=True)
    st.metric("Reliability", f"{reliability:.0f}/100")

    game = f"{away_name} at {home_name}"
    slate_row = {
        "Date": str(date.today()), "Game": game, "Away Team": away_name, "Home Team": home_name,
        "Projected Away": away_score, "Projected Home": home_score, "Projected Margin": margin,
        "Projected Total": projected_total, "Projected Possessions": possessions,
        "Market Home Spread": home_spread, "Market Total": market_total,
        "Spread Pick": spread_pick, "Spread Probability": round(spread_probability, 4), "Spread Edge": round(spread_edge, 2), "Spread Grade": spread_grade,
        "Total Pick": total_pick, "Total Probability": round(total_probability, 4), "Total Edge": round(total_edge, 2), "Total Grade": total_grade,
        "ML Pick": ml_pick, "ML Probability": round(ml_probability, 4), "ML Odds": ml_odds, "ML Edge": round(ml_edge, 4), "ML Grade": ml_grade,
        "Reliability": reliability, "Rotation Confidence": rotation_confidence, "Model Version": MODEL_VERSION, "Notes": notes,
    }
    if st.button("Save CBB Projection to Daily Slate", type="primary", use_container_width=True):
        if append_row(SLATE_TAB, slate_row, SLATE_COLUMNS):
            st.success("College basketball projection saved.")

    spread_selected = st.checkbox(f"{spread_grade} Spread — {spread_pick}", value=spread_grade in ["A", "B"], key="cbb_spread_pick")
    total_selected = st.checkbox(f"{total_grade} Total — {total_pick}", value=total_grade in ["A", "B"], key="cbb_total_pick")
    ml_selected = st.checkbox(f"{ml_grade} Moneyline — {ml_pick}", value=ml_grade in ["A", "B"], key="cbb_ml_pick")
    if st.button("Send CBB Plays to Tracker", use_container_width=True):
        plays = []
        if spread_selected:
            plays.append(("Spread", spread_pick, -110, spread_probability, spread_grade))
        if total_selected:
            plays.append(("Total", total_pick, -110, total_probability, total_grade))
        if ml_selected:
            plays.append(("Moneyline", ml_pick, ml_odds, ml_probability, ml_grade))
        sent = 0
        for bet_type, selection, odds, probability, grade in plays:
            implied = american_implied_probability(odds)
            sent += int(append_row(TRACKER_TAB, {
                "Date": str(date.today()), "Game": game, "Bet Type": bet_type, "Selection": selection,
                "Odds/Line": odds, "Model Probability": round(probability, 4), "Implied Probability": round(implied, 4),
                "Edge": round(probability - implied, 4), "Expected Value": round(expected_value_per_unit(probability, odds), 4),
                "Grade": grade, "Result": "Pending", "Reliability": reliability, "Model Version": MODEL_VERSION, "Notes": notes,
            }, TRACKER_COLUMNS))
        st.success(f"Sent {sent} CBB play(s) to the tracker.") if sent else st.info("No plays were selected.")


def _table(tab, columns, title):
    st.subheader(title)
    dataframe = read_sheet(tab, columns)
    st.dataframe(dataframe.iloc[::-1], use_container_width=True, hide_index=True) if not dataframe.empty else st.info("No rows yet.")


def render():
    st.caption("College basketball foundation model • use projected rotation adjustments before trusting a matchup")
    page = st.radio("CBB section", ["Build", "Team Ratings", "Slate", "Tracker", "Setup"], horizontal=True, key="cbb_nav")
    if page == "Build":
        _render_build()
    elif page == "Team Ratings":
        _render_ratings()
    elif page == "Slate":
        _table(SLATE_TAB, SLATE_COLUMNS, "CBB Daily Slate")
    elif page == "Tracker":
        _table(TRACKER_TAB, TRACKER_COLUMNS, "CBB Bet Tracker")
    else:
        st.subheader("College Basketball Data Setup")
        st.write("Import adjusted-efficiency and four-factor ratings into the Team Ratings section. The model stores separate CBB slate and tracker tabs in the same Google Sheets database used by MLB.")
        st.write("The next data phase is CollegeBasketballData API schedule/team-metric synchronization plus an eight-player projected-minutes and availability engine.")
        st.metric("Google Sheets", "Connected" if sheets_ready() else "Not configured")
