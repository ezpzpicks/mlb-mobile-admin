import os
from datetime import date, datetime

import pandas as pd
import requests
import streamlit as st

from shared.modeling import (
    american_implied_probability,
    clamp,
    expected_value_per_unit,
    fair_american_odds,
    market_grade,
    normal_cdf,
    probability_edge,
    reliability_score,
)
from shared.storage import append_row, read_sheet, sheets_ready, write_sheet

MODEL_VERSION = "cfb-v0.1-foundation-2026-07-13"
RATINGS_TAB = "cfb_team_ratings"
SLATE_TAB = "cfb_daily_slate"
TRACKER_TAB = "cfb_bet_tracker"

RATING_COLUMNS = [
    "Team", "Power Rating", "Offensive Rating", "Defensive Rating", "Pace",
    "QB Adjustment", "Personnel Adjustment", "Special Teams", "Data Confidence", "Updated",
]
SLATE_COLUMNS = [
    "Date", "Game", "Away Team", "Home Team", "Projected Away", "Projected Home",
    "Projected Margin", "Projected Total", "Market Home Spread", "Market Total",
    "Spread Pick", "Spread Probability", "Spread Edge", "Spread Grade",
    "Total Pick", "Total Probability", "Total Edge", "Total Grade",
    "ML Pick", "ML Probability", "ML Odds", "ML Edge", "ML Grade",
    "Reliability", "Personnel Confidence", "Model Version", "Notes",
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


def _team_defaults(ratings, team):
    if ratings is not None and not ratings.empty:
        match = ratings[ratings["Team"].astype(str).str.strip().str.lower() == str(team).strip().lower()]
        if not match.empty:
            row = match.iloc[0]
            return {column: row.get(column, "") for column in RATING_COLUMNS}
    return {
        "Team": team, "Power Rating": 0.0, "Offensive Rating": 0.0,
        "Defensive Rating": 0.0, "Pace": 70.0, "QB Adjustment": 0.0,
        "Personnel Adjustment": 0.0, "Special Teams": 0.0,
        "Data Confidence": 55.0, "Updated": "",
    }


def _cfbd_key():
    key = os.environ.get("CFBD_API_KEY", "")
    if key:
        return key
    try:
        return str(st.secrets.get("CFBD_API_KEY", "") or "")
    except Exception:
        return ""


def _cfbd_get(path, params):
    key = _cfbd_key()
    if not key:
        raise RuntimeError("Add CFBD_API_KEY to Render or Streamlit secrets first.")
    response = requests.get(
        f"https://api.collegefootballdata.com{path}",
        params=params,
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _sync_sp_ratings(year):
    payload = _cfbd_get("/ratings/sp", {"year": int(year)})
    rows = []
    for item in payload:
        offense = item.get("offense", {}) or {}
        defense = item.get("defense", {}) or {}
        special = item.get("specialTeams", {}) or {}
        overall = _num(item.get("rating"), 0)
        offense_rating = _num(offense.get("rating"), overall)
        defense_raw = _num(defense.get("rating"), overall)
        # Convert to a positive-is-good defensive scale relative to the list average later.
        rows.append({
            "Team": item.get("team", ""),
            "Power Rating": overall,
            "Offensive Rating": offense_rating,
            "Defensive Rating": defense_raw,
            "Pace": 70.0,
            "QB Adjustment": 0.0,
            "Personnel Adjustment": 0.0,
            "Special Teams": _num(special.get("rating"), 0),
            "Data Confidence": 82.0,
            "Updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
    dataframe = pd.DataFrame(rows, columns=RATING_COLUMNS)
    if not dataframe.empty:
        for column in ["Power Rating", "Offensive Rating", "Defensive Rating", "Special Teams"]:
            series = pd.to_numeric(dataframe[column], errors="coerce")
            mean = series.mean()
            if column == "Defensive Rating":
                dataframe[column] = (mean - series).round(2)
            else:
                dataframe[column] = (series - mean).round(2)
    return dataframe


def _project(away, home, settings):
    away_power = _num(away["Power Rating"])
    home_power = _num(home["Power Rating"])
    away_off = _num(away["Offensive Rating"])
    home_off = _num(home["Offensive Rating"])
    away_def = _num(away["Defensive Rating"])
    home_def = _num(home["Defensive Rating"])
    away_pace = _num(away["Pace"], 70)
    home_pace = _num(home["Pace"], 70)

    pace_adjustment = (((away_pace + home_pace) / 2.0) - 70.0) * 0.48
    projected_total = (
        55.0
        + 0.42 * (away_off + home_off)
        - 0.30 * (away_def + home_def)
        + pace_adjustment
        + settings["weather_total_adjustment"]
        + settings["matchup_total_adjustment"]
    )

    projected_margin = (
        (home_power - away_power)
        + settings["home_field"]
        + 0.18 * ((home_off - away_off) + (home_def - away_def))
        + (_num(home["QB Adjustment"]) - _num(away["QB Adjustment"]))
        + (_num(home["Personnel Adjustment"]) - _num(away["Personnel Adjustment"]))
        + 0.20 * (_num(home["Special Teams"]) - _num(away["Special Teams"]))
        + settings["matchup_margin_adjustment"]
    )

    projected_total = clamp(projected_total, 28, 92)
    projected_margin = clamp(projected_margin, -45, 45)
    home_score = clamp((projected_total + projected_margin) / 2.0, 3, 70)
    away_score = clamp(projected_total - home_score, 3, 70)
    projected_total = home_score + away_score
    projected_margin = home_score - away_score
    return round(away_score, 1), round(home_score, 1), round(projected_margin, 1), round(projected_total, 1)


def _render_ratings():
    st.subheader("CFB Team Ratings")
    st.caption("Positive Offensive, Defensive and Power ratings are better than average. QB and personnel adjustments are measured in points.")
    ratings = _load_ratings()

    c1, c2 = st.columns(2)
    with c1:
        sync_year = st.number_input("CFBD ratings season", min_value=2014, max_value=2030, value=2025, step=1)
    with c2:
        if st.button("Sync CFBD SP+ Ratings", use_container_width=True):
            try:
                synced = _sync_sp_ratings(sync_year)
                if synced.empty:
                    st.warning("CFBD returned no SP+ ratings for that season.")
                elif write_sheet(RATINGS_TAB, synced, RATING_COLUMNS):
                    st.success(f"Saved {len(synced)} CFB team ratings.")
                    st.rerun()
            except Exception as exc:
                st.error(str(exc))

    uploaded = st.file_uploader("Import or replace ratings from CSV", type=["csv"], key="cfb_ratings_csv")
    if uploaded is not None:
        imported = pd.read_csv(uploaded)
        for column in RATING_COLUMNS:
            if column not in imported.columns:
                imported[column] = ""
        ratings = imported[RATING_COLUMNS]

    if ratings.empty:
        ratings = pd.DataFrame([_team_defaults(ratings, "Example Team")], columns=RATING_COLUMNS)

    edited = st.data_editor(ratings, use_container_width=True, hide_index=True, num_rows="dynamic", key="cfb_ratings_editor")
    if st.button("Save CFB Ratings", type="primary", use_container_width=True):
        edited["Updated"] = edited["Updated"].where(edited["Updated"].astype(str).str.strip() != "", str(date.today()))
        if write_sheet(RATINGS_TAB, edited, RATING_COLUMNS):
            st.success("CFB ratings saved.")


def _render_build():
    ratings = _load_ratings()
    teams = sorted([x for x in ratings.get("Team", pd.Series(dtype=str)).astype(str).tolist() if x.strip()])
    st.subheader("CFB Matchup Builder")
    st.caption("Research beta. The model combines power ratings, offense/defense, pace, quarterback value, personnel and market prices.")

    use_saved = bool(teams)
    if use_saved:
        away_name = st.selectbox("Away team", teams, key="cfb_away_team")
        home_options = [team for team in teams if team != away_name] or teams
        home_name = st.selectbox("Home team", home_options, key="cfb_home_team")
    else:
        st.info("No CFB ratings are saved yet. Enter teams manually or use Team Ratings to sync/import data.")
        away_name = st.text_input("Away team", value="Away Team", key="cfb_away_manual")
        home_name = st.text_input("Home team", value="Home Team", key="cfb_home_manual")

    away = _team_defaults(ratings, away_name)
    home = _team_defaults(ratings, home_name)

    with st.expander("Team and personnel adjustments", expanded=not use_saved):
        col_a, col_h = st.columns(2)
        with col_a:
            st.markdown(f"**{away_name}**")
            for label, default in [
                ("Power Rating", away["Power Rating"]), ("Offensive Rating", away["Offensive Rating"]),
                ("Defensive Rating", away["Defensive Rating"]), ("Pace", away["Pace"]),
                ("QB Adjustment", away["QB Adjustment"]), ("Personnel Adjustment", away["Personnel Adjustment"]),
                ("Special Teams", away["Special Teams"]), ("Data Confidence", away["Data Confidence"]),
            ]:
                away[label] = st.number_input(f"Away {label}", value=_num(default, 70 if label == "Pace" else 55 if label == "Data Confidence" else 0), step=0.1, key=f"cfb_away_{label}")
        with col_h:
            st.markdown(f"**{home_name}**")
            for label, default in [
                ("Power Rating", home["Power Rating"]), ("Offensive Rating", home["Offensive Rating"]),
                ("Defensive Rating", home["Defensive Rating"]), ("Pace", home["Pace"]),
                ("QB Adjustment", home["QB Adjustment"]), ("Personnel Adjustment", home["Personnel Adjustment"]),
                ("Special Teams", home["Special Teams"]), ("Data Confidence", home["Data Confidence"]),
            ]:
                home[label] = st.number_input(f"Home {label}", value=_num(default, 70 if label == "Pace" else 55 if label == "Data Confidence" else 0), step=0.1, key=f"cfb_home_{label}")

    st.markdown("#### Market and environment")
    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread", value=-3.0, step=0.5, help="Use -3 when the home team is favored by 3.")
        home_ml = st.number_input("Home moneyline", value=-150, step=5)
    with c2:
        market_total = st.number_input("Game total", value=55.0, step=0.5)
        away_ml = st.number_input("Away moneyline", value=130, step=5)
    with c3:
        home_field = st.number_input("Home-field points", value=2.5, step=0.25)
        personnel_confidence = st.slider("Personnel confidence", 0, 100, 65)

    with st.expander("Advanced matchup adjustments"):
        matchup_margin_adjustment = st.number_input("Home matchup adjustment", value=0.0, step=0.25)
        matchup_total_adjustment = st.number_input("Matchup total adjustment", value=0.0, step=0.25)
        weather_total_adjustment = st.number_input("Weather total adjustment", value=0.0, step=0.25)
        notes = st.text_area("Notes", placeholder="QB status, offensive line injuries, travel, weather, scheme matchup...")

    away_score, home_score, margin, projected_total = _project(away, home, {
        "home_field": home_field,
        "matchup_margin_adjustment": matchup_margin_adjustment,
        "matchup_total_adjustment": matchup_total_adjustment,
        "weather_total_adjustment": weather_total_adjustment,
    })

    spread_edge_home = margin + home_spread
    home_cover = normal_cdf(spread_edge_home / 16.5)
    if home_cover >= 0.5:
        spread_pick, spread_probability, spread_edge = f"{home_name} {home_spread:+.1f}", home_cover, spread_edge_home
    else:
        away_line = -home_spread
        spread_pick, spread_probability, spread_edge = f"{away_name} {away_line:+.1f}", 1 - home_cover, -spread_edge_home
    spread_grade = market_grade(spread_edge, spread_probability)

    over_probability = normal_cdf((projected_total - market_total) / 14.5)
    if over_probability >= 0.5:
        total_pick, total_probability, total_edge = f"Over {market_total:.1f}", over_probability, projected_total - market_total
    else:
        total_pick, total_probability, total_edge = f"Under {market_total:.1f}", 1 - over_probability, market_total - projected_total
    total_grade = market_grade(total_edge, total_probability)

    home_win = normal_cdf(margin / 16.0)
    if home_win >= 0.5:
        ml_pick, ml_probability, ml_odds = home_name, home_win, home_ml
    else:
        ml_pick, ml_probability, ml_odds = away_name, 1 - home_win, away_ml
    ml_prob_edge = probability_edge(ml_probability, ml_odds)
    ml_grade = market_grade(0, ml_probability, ml_prob_edge)

    data_confidence = (_num(away["Data Confidence"], 55) + _num(home["Data Confidence"], 55)) / 2
    edge_strength = clamp(max(abs(spread_edge), abs(total_edge), abs(ml_prob_edge) * 35) * 16, 0, 100)
    reliability = reliability_score(data_confidence, personnel_confidence, edge_strength, 6 if personnel_confidence < 50 else 0)

    st.markdown("#### Projection")
    a, b, c, d = st.columns(4)
    a.metric(away_name, f"{away_score:.1f}")
    b.metric(home_name, f"{home_score:.1f}")
    c.metric("Projected margin", f"{home_name} {margin:+.1f}")
    d.metric("Projected total", f"{projected_total:.1f}")

    st.markdown("#### Model plays")
    rows = pd.DataFrame([
        {"Market": "Spread", "Selection": spread_pick, "Probability": f"{spread_probability:.1%}", "Edge": f"{spread_edge:+.1f} pts", "Grade": spread_grade},
        {"Market": "Total", "Selection": total_pick, "Probability": f"{total_probability:.1%}", "Edge": f"{total_edge:+.1f} pts", "Grade": total_grade},
        {"Market": "Moneyline", "Selection": ml_pick, "Probability": f"{ml_probability:.1%}", "Edge": f"{ml_prob_edge:+.1%}", "Grade": ml_grade},
    ])
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.metric("Reliability", f"{reliability:.0f}/100")

    game = f"{away_name} at {home_name}"
    slate_row = {
        "Date": str(date.today()), "Game": game, "Away Team": away_name, "Home Team": home_name,
        "Projected Away": away_score, "Projected Home": home_score, "Projected Margin": margin,
        "Projected Total": projected_total, "Market Home Spread": home_spread, "Market Total": market_total,
        "Spread Pick": spread_pick, "Spread Probability": round(spread_probability, 4), "Spread Edge": round(spread_edge, 2), "Spread Grade": spread_grade,
        "Total Pick": total_pick, "Total Probability": round(total_probability, 4), "Total Edge": round(total_edge, 2), "Total Grade": total_grade,
        "ML Pick": ml_pick, "ML Probability": round(ml_probability, 4), "ML Odds": ml_odds, "ML Edge": round(ml_prob_edge, 4), "ML Grade": ml_grade,
        "Reliability": reliability, "Personnel Confidence": personnel_confidence, "Model Version": MODEL_VERSION, "Notes": notes,
    }

    if st.button("Save CFB Projection to Daily Slate", type="primary", use_container_width=True):
        if append_row(SLATE_TAB, slate_row, SLATE_COLUMNS):
            st.success("CFB projection saved.")

    st.markdown("#### Send selected plays to tracker")
    spread_selected = st.checkbox(f"{spread_grade} Spread — {spread_pick}", value=spread_grade in ["A", "B"])
    total_selected = st.checkbox(f"{total_grade} Total — {total_pick}", value=total_grade in ["A", "B"])
    ml_selected = st.checkbox(f"{ml_grade} Moneyline — {ml_pick}", value=ml_grade in ["A", "B"])
    if st.button("Send CFB Plays to Tracker", use_container_width=True):
        sent = 0
        if spread_selected:
            odds = -110
            sent += int(append_row(TRACKER_TAB, {
                "Date": str(date.today()), "Game": game, "Bet Type": "Spread", "Selection": spread_pick,
                "Odds/Line": odds, "Model Probability": round(spread_probability, 4), "Implied Probability": round(american_implied_probability(odds), 4),
                "Edge": round(spread_probability - american_implied_probability(odds), 4), "Expected Value": round(expected_value_per_unit(spread_probability, odds), 4),
                "Grade": spread_grade, "Result": "Pending", "Reliability": reliability, "Model Version": MODEL_VERSION, "Notes": notes,
            }, TRACKER_COLUMNS))
        if total_selected:
            odds = -110
            sent += int(append_row(TRACKER_TAB, {
                "Date": str(date.today()), "Game": game, "Bet Type": "Total", "Selection": total_pick,
                "Odds/Line": odds, "Model Probability": round(total_probability, 4), "Implied Probability": round(american_implied_probability(odds), 4),
                "Edge": round(total_probability - american_implied_probability(odds), 4), "Expected Value": round(expected_value_per_unit(total_probability, odds), 4),
                "Grade": total_grade, "Result": "Pending", "Reliability": reliability, "Model Version": MODEL_VERSION, "Notes": notes,
            }, TRACKER_COLUMNS))
        if ml_selected:
            sent += int(append_row(TRACKER_TAB, {
                "Date": str(date.today()), "Game": game, "Bet Type": "Moneyline", "Selection": ml_pick,
                "Odds/Line": ml_odds, "Model Probability": round(ml_probability, 4), "Implied Probability": round(american_implied_probability(ml_odds), 4),
                "Edge": round(ml_prob_edge, 4), "Expected Value": round(expected_value_per_unit(ml_probability, ml_odds), 4),
                "Grade": ml_grade, "Result": "Pending", "Reliability": reliability, "Model Version": MODEL_VERSION, "Notes": notes,
            }, TRACKER_COLUMNS))
        st.success(f"Sent {sent} CFB play(s) to the tracker.") if sent else st.info("No plays were selected.")


def _render_table(tab, columns, title):
    st.subheader(title)
    dataframe = read_sheet(tab, columns)
    if dataframe.empty:
        st.info("No rows yet.")
    else:
        st.dataframe(dataframe.iloc[::-1], use_container_width=True, hide_index=True)


def render():
    st.caption("CFB foundation model • intended for preseason setup and shadow testing")
    page = st.radio("CFB section", ["Build", "Team Ratings", "Slate", "Tracker", "Setup"], horizontal=True, key="cfb_nav")
    if page == "Build":
        _render_build()
    elif page == "Team Ratings":
        _render_ratings()
    elif page == "Slate":
        _render_table(SLATE_TAB, SLATE_COLUMNS, "CFB Daily Slate")
    elif page == "Tracker":
        _render_table(TRACKER_TAB, TRACKER_COLUMNS, "CFB Bet Tracker")
    else:
        st.subheader("CFB Data Setup")
        st.write("Add `CFBD_API_KEY` to Render or `.streamlit/secrets.toml` to sync SP+ ratings. The same Google Sheets credentials already used by MLB create the CFB tabs automatically.")
        st.write("The model is operational with imported/manual ratings, but should remain in shadow testing until its probability and edge thresholds are calibrated on historical games.")
        st.metric("Google Sheets", "Connected" if sheets_ready() else "Not configured")
        st.metric("CFBD API", "Configured" if _cfbd_key() else "Key needed")
