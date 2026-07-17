import runpy
from pathlib import Path

import streamlit as st

from shared.auth import require_admin_password
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

    st.caption("MLB remains the production engine. NFL now includes the automated slate, lineup-aware game engine, and in-depth calibrated QB/RB/WR/TE prop model. CFB and CBB remain foundation models for setup and shadow testing.")
    st.stop()

versions = {
    "MLB": "v15.0-k-overhaul-2026-07-13",
    "CFB": "cfb-v0.1-foundation-2026-07-13",
    "NFL": "nfl-v3.0-in-depth-props-2026-07-17",
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

if selected_sport == "MLB":
    # Execute the preserved MLB production builder only after MLB is selected.
    runpy.run_path(str(ROOT / "builders" / "mlb_builder.py"), run_name="__main__")
elif selected_sport == "CFB":
    from builders.cfb_builder import render
    render()
elif selected_sport == "NFL":
    from builders.nfl_builder import render
    render()
elif selected_sport == "CBB":
    from builders.cbb_builder import render
    render()
