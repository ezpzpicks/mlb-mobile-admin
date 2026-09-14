import copy
import runpy
import threading
import time
from pathlib import Path

import streamlit as st

from shared.auth import require_admin_password
from shared.storage import (
    initialize_sport_workbooks,
    set_storage_sport,
    storage_database_name,
)
from shared.ui import SPORT_META, apply_global_styles, render_brand_header, render_sport_header

ROOT = Path(__file__).resolve().parent
LOGO_FILE = str(ROOT / "ezpz_logo.png")
PAGE_ICON = LOGO_FILE if Path(LOGO_FILE).exists() else None

st.set_page_config(
    page_title="EZPZ Multi-Sport Admin",
    layout="centered",
    page_icon=PAGE_ICON,
    initial_sidebar_state="collapsed",
)
apply_global_styles()

# Bootstrap the dedicated football workbooks before the password gate. This is
# cached by shared.storage, so a normal Render wake-up is enough to create the
# databases once without requiring a manual NFL/CFB navigation step.
try:
    initialize_sport_workbooks(("NFL", "CFB"))
except Exception as exc:
    print(f"Sport workbook bootstrap failed: {exc}")

require_admin_password(LOGO_FILE)


def _query_sport() -> str:
    try:
        return str(st.query_params.get("sport", "") or "").upper()
    except Exception:
        return ""


def _set_sport(sport: str) -> None:
    st.session_state["selected_sport"] = sport
    try:
        if sport:
            st.query_params["sport"] = sport.lower()
        elif "sport" in st.query_params:
            del st.query_params["sport"]
    except Exception:
        pass


valid_sports = set(SPORT_META)
selected_sport = str(st.session_state.get("selected_sport", "") or "").upper()
query_sport = _query_sport()
if not selected_sport and query_sport in valid_sports:
    selected_sport = query_sport
    st.session_state["selected_sport"] = selected_sport

if selected_sport not in valid_sports:
    _set_sport("")
    render_brand_header("EZPZ Model Builder", "One private admin app for every sport")
    st.markdown(
        """
        <div class="model-card">
          <h4>Choose a sport</h4>
          <div class="muted">Only the selected engine loads, so MLB stays isolated and the app does not run every sport on each interaction.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    rows = [("MLB", "NFL"), ("CFB", "CBB")]
    for left_sport, right_sport in rows:
        left, right = st.columns(2)
        for column, sport in [(left, left_sport), (right, right_sport)]:
            icon, label, subtitle = SPORT_META[sport]
            with column:
                st.markdown(
                    f'<div class="sport-card"><div class="sport-card-title">{icon} {label}</div><div class="sport-card-sub">{subtitle}</div></div>',
                    unsafe_allow_html=True,
                )
                if st.button(f"Open {label}", key=f"open_{sport}", use_container_width=True):
                    _set_sport(sport)
                    st.rerun()

    st.caption("MLB remains the production engine. NFL includes regression game and QB/RB/WR yardage models plus TE1 receiving-yard projections. CFB now combines the validated spread-margin regression with an independent pace/efficiency totals regression, derives team scores algebraically, and retains live personnel/weather overlays plus calibrated market evaluation. CBB remains a foundation model for setup and shadow testing.")
    st.stop()

versions = {
    "MLB": "v15.2-public-betting-splits-2026-07-27",
    "CFB": "cfb-v2.4-covers-personnel-weather-2026-09-11",
    "NFL": "nfl-v4.11-progressive-prop-season-weight-2026-09-14",
    "CBB": "cbb-v0.1-rotation-foundation-2026-07-13",
}
if selected_sport == "NFL":
    # The shared sport header includes a next-sport shortcut (shown as
    # "Open College Basketball" on the NFL page). Use a focused NFL header instead.
    header_left, header_right = st.columns([3, 1])
    icon, label, subtitle = SPORT_META["NFL"]
    with header_left:
        st.markdown(f"## {icon} {label} Model Builder")
        st.caption(f"{subtitle} • {versions['NFL']}")
    with header_right:
        if st.button("← All Sports", key="nfl_back_to_sports", use_container_width=True):
            _set_sport("")
            st.rerun()
else:
    render_sport_header(selected_sport, versions[selected_sport])

if selected_sport != "MLB":
    st.caption(f"Database: {storage_database_name(selected_sport)}")

if selected_sport == "MLB":
    runpy.run_path(str(ROOT / "builders" / "mlb_builder.py"), run_name="__main__")
elif selected_sport == "CFB":
    set_storage_sport("CFB")
    from builders import cfb_builder
    from builders.cfb_game_regression import install_regression_layer
    from builders.cfb_total_regression import install_total_regression
    from builders.cfb_market_calibration import install_market_calibration
    from builders.cfb_covers import install_covers_layer
    install_regression_layer(cfb_builder)
    install_total_regression(cfb_builder)
    install_market_calibration(cfb_builder)
    install_covers_layer(cfb_builder)
    cfb_builder.MODEL_VERSION = "cfb-v2.4-covers-personnel-weather-2026-09-11"
    cfb_builder.render()
elif selected_sport == "NFL":
    set_storage_sport("NFL")
    from builders import nfl_builder
    from builders.nfl_covers import install_covers_weather
    from builders.nfl_game_regression import install_regression_layer
    from builders.nfl_skill_prop_regression import install_skill_prop_regression
    from builders.nfl_skill_prop_consistency import install_skill_prop_consistency
    from builders.nfl_slot_matchups import install_slot_matchup_layer
    install_regression_layer(nfl_builder)
    install_skill_prop_regression(nfl_builder)
    install_skill_prop_consistency(nfl_builder)
    install_slot_matchup_layer(nfl_builder)
    install_covers_weather(nfl_builder)
    nfl_builder.render()
elif selected_sport == "CBB":
    set_storage_sport("CBB")
    from builders.cbb_builder import render
    render()
