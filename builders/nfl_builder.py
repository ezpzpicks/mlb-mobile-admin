from datetime import date, datetime

import pandas as pd
import requests
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

MODEL_VERSION = "nfl-v0.1-foundation-2026-07-13"
RATINGS_TAB = "nfl_team_ratings"
SLATE_TAB = "nfl_daily_slate"
TRACKER_TAB = "nfl_bet_tracker"
SCHEDULE_TAB = "nfl_schedule"

RATING_COLUMNS = [
    "Team", "Power Rating", "Off EPA/Play", "Def EPA Edge", "Success Rate Edge",
    "Explosive Play Edge", "Pace", "QB Adjustment", "OL Adjustment", "Skill/Injury Adjustment",
    "Special Teams", "Data Confidence", "Updated",
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
SCHEDULE_COLUMNS = ["Season", "Week", "Game Date", "Away Team", "Home Team", "Game ID"]

NFL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
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
        match = ratings[ratings["Team"].astype(str).str.upper().str.strip() == str(team).upper().strip()]
        if not match.empty:
            return match.iloc[0].to_dict()
    return {
        "Team": team, "Power Rating": 0.0, "Off EPA/Play": 0.0, "Def EPA Edge": 0.0,
        "Success Rate Edge": 0.0, "Explosive Play Edge": 0.0, "Pace": 64.0,
        "QB Adjustment": 0.0, "OL Adjustment": 0.0, "Skill/Injury Adjustment": 0.0,
        "Special Teams": 0.0, "Data Confidence": 55.0, "Updated": "",
    }


def _seed_neutral_ratings():
    return pd.DataFrame([_defaults(pd.DataFrame(), team) for team in NFL_TEAMS], columns=RATING_COLUMNS)


def _sync_schedule(season):
    urls = [
        "https://github.com/nflverse/nfldata/raw/master/data/games.csv",
        "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv",
    ]
    last_error = None
    for url in urls:
        try:
            dataframe = pd.read_csv(url)
            if "season" not in dataframe.columns:
                continue
            dataframe = dataframe[pd.to_numeric(dataframe["season"], errors="coerce") == int(season)].copy()
            if dataframe.empty:
                continue
            output = pd.DataFrame({
                "Season": dataframe.get("season", ""),
                "Week": dataframe.get("week", ""),
                "Game Date": dataframe.get("gameday", dataframe.get("game_date", "")),
                "Away Team": dataframe.get("away_team", ""),
                "Home Team": dataframe.get("home_team", ""),
                "Game ID": dataframe.get("game_id", ""),
            })
            return output[SCHEDULE_COLUMNS]
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Could not load the nflverse schedule feed: {last_error}")


def _project(away, home, settings):
    away_power, home_power = _num(away["Power Rating"]), _num(home["Power Rating"])
    away_off, home_off = _num(away["Off EPA/Play"]), _num(home["Off EPA/Play"])
    away_def, home_def = _num(away["Def EPA Edge"]), _num(home["Def EPA Edge"])
    away_success, home_success = _num(away["Success Rate Edge"]), _num(home["Success Rate Edge"])
    away_explosive, home_explosive = _num(away["Explosive Play Edge"]), _num(home["Explosive Play Edge"])
    pace = (_num(away["Pace"], 64) + _num(home["Pace"], 64)) / 2

    projected_total = (
        44.5
        + 30.0 * (away_off + home_off)
        - 22.0 * (away_def + home_def)
        + 0.12 * (away_success + home_success)
        + 0.10 * (away_explosive + home_explosive)
        + (pace - 64.0) * 0.42
        + settings["weather_total_adjustment"]
        + settings["matchup_total_adjustment"]
    )
    projected_margin = (
        (home_power - away_power)
        + settings["home_field"]
        + 22.0 * ((home_off - away_off) + (home_def - away_def))
        + 0.08 * (home_success - away_success)
        + 0.08 * (home_explosive - away_explosive)
        + (_num(home["QB Adjustment"]) - _num(away["QB Adjustment"]))
        + (_num(home["OL Adjustment"]) - _num(away["OL Adjustment"]))
        + (_num(home["Skill/Injury Adjustment"]) - _num(away["Skill/Injury Adjustment"]))
        + 0.35 * (_num(home["Special Teams"]) - _num(away["Special Teams"]))
        + settings["matchup_margin_adjustment"]
    )
    projected_total = clamp(projected_total, 25, 72)
    projected_margin = clamp(projected_margin, -30, 30)
    home_score = clamp((projected_total + projected_margin) / 2, 6, 50)
    away_score = clamp(projected_total - home_score, 6, 50)
    return round(away_score, 1), round(home_score, 1), round(home_score - away_score, 1), round(home_score + away_score, 1)


def _render_ratings():
    st.subheader("NFL Team Ratings")
    st.caption("EPA fields are per-play values. Defensive EPA Edge is positive when the defense is better than average. Other adjustments are points.")
    ratings = _load_ratings()
    if ratings.empty:
        ratings = _seed_neutral_ratings()

    uploaded = st.file_uploader("Import team ratings CSV", type=["csv"], key="nfl_ratings_csv")
    if uploaded is not None:
        imported = pd.read_csv(uploaded)
        for column in RATING_COLUMNS:
            if column not in imported.columns:
                imported[column] = ""
        ratings = imported[RATING_COLUMNS]

    edited = st.data_editor(ratings, use_container_width=True, hide_index=True, num_rows="dynamic", key="nfl_ratings_editor")
    if st.button("Save NFL Ratings", type="primary", use_container_width=True):
        edited["Updated"] = str(date.today())
        if write_sheet(RATINGS_TAB, edited, RATING_COLUMNS):
            st.success("NFL ratings saved.")

    st.divider()
    season = st.number_input("nflverse schedule season", min_value=1999, max_value=2030, value=2026, step=1)
    if st.button("Sync NFL Schedule", use_container_width=True):
        try:
            schedule = _sync_schedule(season)
            if write_sheet(SCHEDULE_TAB, schedule, SCHEDULE_COLUMNS):
                st.success(f"Saved {len(schedule)} NFL games.")
        except Exception as exc:
            st.error(str(exc))


def _team_inputs(prefix, team, row):
    st.markdown(f"**{team}**")
    fields = [
        ("Power Rating", 0.1), ("Off EPA/Play", 0.01), ("Def EPA Edge", 0.01),
        ("Success Rate Edge", 0.5), ("Explosive Play Edge", 0.5), ("Pace", 0.5),
        ("QB Adjustment", 0.25), ("OL Adjustment", 0.25), ("Skill/Injury Adjustment", 0.25),
        ("Special Teams", 0.1), ("Data Confidence", 1.0),
    ]
    for field, step in fields:
        default = 64 if field == "Pace" else 55 if field == "Data Confidence" else 0
        row[field] = st.number_input(f"{prefix} {field}", value=_num(row.get(field), default), step=step, key=f"nfl_{prefix}_{field}")
    return row


def _render_build():
    ratings = _load_ratings()
    teams = sorted(set(NFL_TEAMS + [x for x in ratings.get("Team", pd.Series(dtype=str)).astype(str).tolist() if x.strip()]))
    st.subheader("NFL Matchup Builder")
    away_name = st.selectbox("Away team", teams, key="nfl_away")
    home_name = st.selectbox("Home team", [team for team in teams if team != away_name], key="nfl_home")
    away, home = _defaults(ratings, away_name), _defaults(ratings, home_name)

    with st.expander("Team, quarterback and injury inputs"):
        ca, ch = st.columns(2)
        with ca:
            away = _team_inputs("Away", away_name, away)
        with ch:
            home = _team_inputs("Home", home_name, home)

    c1, c2, c3 = st.columns(3)
    with c1:
        home_spread = st.number_input("Home spread", value=-2.5, step=0.5)
        home_ml = st.number_input("Home moneyline", value=-140, step=5)
    with c2:
        market_total = st.number_input("Game total", value=44.5, step=0.5)
        away_ml = st.number_input("Away moneyline", value=120, step=5)
    with c3:
        home_field = st.number_input("Home-field points", value=1.7, step=0.1)
        personnel_confidence = st.slider("Personnel confidence", 0, 100, 70, key="nfl_personnel_confidence")

    with st.expander("Advanced matchup adjustments"):
        matchup_margin_adjustment = st.number_input("Home matchup adjustment", value=0.0, step=0.25, key="nfl_matchup_margin")
        matchup_total_adjustment = st.number_input("Matchup total adjustment", value=0.0, step=0.25, key="nfl_matchup_total")
        weather_total_adjustment = st.number_input("Weather total adjustment", value=0.0, step=0.25, key="nfl_weather_total")
        notes = st.text_area("Notes", key="nfl_notes", placeholder="QB/practice status, offensive line changes, weather, rest, coverage matchup...")

    away_score, home_score, margin, projected_total = _project(away, home, {
        "home_field": home_field,
        "matchup_margin_adjustment": matchup_margin_adjustment,
        "matchup_total_adjustment": matchup_total_adjustment,
        "weather_total_adjustment": weather_total_adjustment,
    })

    spread_edge_home = margin + home_spread
    home_cover = normal_cdf(spread_edge_home / 13.6)
    if home_cover >= 0.5:
        spread_pick, spread_probability, spread_edge = f"{home_name} {home_spread:+.1f}", home_cover, spread_edge_home
    else:
        spread_pick, spread_probability, spread_edge = f"{away_name} {-home_spread:+.1f}", 1 - home_cover, -spread_edge_home
    spread_grade = market_grade(spread_edge, spread_probability)

    over_probability = normal_cdf((projected_total - market_total) / 11.8)
    if over_probability >= 0.5:
        total_pick, total_probability, total_edge = f"Over {market_total:.1f}", over_probability, projected_total - market_total
    else:
        total_pick, total_probability, total_edge = f"Under {market_total:.1f}", 1 - over_probability, market_total - projected_total
    total_grade = market_grade(total_edge, total_probability)

    home_win = normal_cdf(margin / 13.4)
    if home_win >= 0.5:
        ml_pick, ml_probability, ml_odds = home_name, home_win, home_ml
    else:
        ml_pick, ml_probability, ml_odds = away_name, 1 - home_win, away_ml
    ml_edge = probability_edge(ml_probability, ml_odds)
    ml_grade = market_grade(0, ml_probability, ml_edge)

    data_confidence = (_num(away["Data Confidence"], 55) + _num(home["Data Confidence"], 55)) / 2
    edge_strength = clamp(max(abs(spread_edge), abs(total_edge), abs(ml_edge) * 35) * 17, 0, 100)
    reliability = reliability_score(data_confidence, personnel_confidence, edge_strength, 8 if personnel_confidence < 50 else 0)

    a, b, c, d = st.columns(4)
    a.metric(away_name, f"{away_score:.1f}")
    b.metric(home_name, f"{home_score:.1f}")
    c.metric("Projected margin", f"{home_name} {margin:+.1f}")
    d.metric("Projected total", f"{projected_total:.1f}")

    st.dataframe(pd.DataFrame([
        {"Market": "Spread", "Selection": spread_pick, "Probability": f"{spread_probability:.1%}", "Edge": f"{spread_edge:+.1f} pts", "Grade": spread_grade},
        {"Market": "Total", "Selection": total_pick, "Probability": f"{total_probability:.1%}", "Edge": f"{total_edge:+.1f} pts", "Grade": total_grade},
        {"Market": "Moneyline", "Selection": ml_pick, "Probability": f"{ml_probability:.1%}", "Edge": f"{ml_edge:+.1%}", "Grade": ml_grade},
    ]), use_container_width=True, hide_index=True)
    st.metric("Reliability", f"{reliability:.0f}/100")

    game = f"{away_name} at {home_name}"
    slate_row = {
        "Date": str(date.today()), "Game": game, "Away Team": away_name, "Home Team": home_name,
        "Projected Away": away_score, "Projected Home": home_score, "Projected Margin": margin, "Projected Total": projected_total,
        "Market Home Spread": home_spread, "Market Total": market_total,
        "Spread Pick": spread_pick, "Spread Probability": round(spread_probability, 4), "Spread Edge": round(spread_edge, 2), "Spread Grade": spread_grade,
        "Total Pick": total_pick, "Total Probability": round(total_probability, 4), "Total Edge": round(total_edge, 2), "Total Grade": total_grade,
        "ML Pick": ml_pick, "ML Probability": round(ml_probability, 4), "ML Odds": ml_odds, "ML Edge": round(ml_edge, 4), "ML Grade": ml_grade,
        "Reliability": reliability, "Personnel Confidence": personnel_confidence, "Model Version": MODEL_VERSION, "Notes": notes,
    }
    if st.button("Save NFL Projection to Daily Slate", type="primary", use_container_width=True):
        if append_row(SLATE_TAB, slate_row, SLATE_COLUMNS):
            st.success("NFL projection saved.")

    spread_selected = st.checkbox(f"{spread_grade} Spread — {spread_pick}", value=spread_grade in ["A", "B"], key="nfl_spread_pick")
    total_selected = st.checkbox(f"{total_grade} Total — {total_pick}", value=total_grade in ["A", "B"], key="nfl_total_pick")
    ml_selected = st.checkbox(f"{ml_grade} Moneyline — {ml_pick}", value=ml_grade in ["A", "B"], key="nfl_ml_pick")
    if st.button("Send NFL Plays to Tracker", use_container_width=True):
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
        st.success(f"Sent {sent} NFL play(s) to the tracker.") if sent else st.info("No plays were selected.")


def _table(tab, columns, title):
    st.subheader(title)
    dataframe = read_sheet(tab, columns)
    st.dataframe(dataframe.iloc[::-1], use_container_width=True, hide_index=True) if not dataframe.empty else st.info("No rows yet.")


def render():
    st.caption("NFL foundation model • game markets first; props come after calibration")
    page = st.radio("NFL section", ["Build", "Team Ratings", "Slate", "Tracker", "Schedule", "Setup"], horizontal=True, key="nfl_nav")
    if page == "Build":
        _render_build()
    elif page == "Team Ratings":
        _render_ratings()
    elif page == "Slate":
        _table(SLATE_TAB, SLATE_COLUMNS, "NFL Daily Slate")
    elif page == "Tracker":
        _table(TRACKER_TAB, TRACKER_COLUMNS, "NFL Bet Tracker")
    elif page == "Schedule":
        _table(SCHEDULE_TAB, SCHEDULE_COLUMNS, "NFL Schedule")
    else:
        st.subheader("NFL Data Setup")
        st.write("The app creates neutral ratings for all 32 teams, accepts CSV imports, and can sync the nflverse schedule feed. Next development step is an automated weekly team-rating builder from nflverse play-by-play, player stats, snaps and injuries.")
        st.metric("Google Sheets", "Connected" if sheets_ready() else "Not configured")
