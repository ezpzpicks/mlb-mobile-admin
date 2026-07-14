import os

import streamlit as st

SPORT_META = {
    "MLB": ("⚾", "MLB", "Live v15 model"),
    "CFB": ("🏈", "College Football", "Spread, moneyline and total beta"),
    "NFL": ("🏈", "NFL", "Spread, moneyline and total beta"),
    "CBB": ("🏀", "College Basketball", "Rotation-aware game model beta"),
}


def apply_global_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 0.8rem; padding-left: 0.75rem; padding-right: 0.75rem; max-width: 1120px;}
        .sport-card {background:linear-gradient(145deg,#0b1220,#111c31);border:1px solid #263a59;border-radius:1.05rem;padding:1rem;margin:.25rem 0;min-height:128px;box-shadow:0 8px 24px rgba(0,0,0,.22)}
        .sport-card-title {font-size:1.12rem;font-weight:900;color:#f8fafc;margin-bottom:.25rem}
        .sport-card-sub {font-size:.80rem;color:#a9b8ce;line-height:1.25}
        .model-card {background:linear-gradient(145deg,#0f172a,#111827);border:1px solid #26364d;border-radius:1rem;padding:.85rem;margin:.45rem 0;box-shadow:0 7px 20px rgba(0,0,0,.18)}
        .model-card h4 {color:#f8fafc;margin:0 0 .4rem 0}
        .muted {color:#94a3b8;font-size:.78rem}
        .stButton > button {width:100%;border-radius:.75rem;font-weight:800;min-height:2.7rem}
        div[data-testid="stMetric"] {background:#111827;border:1px solid #2c3c52;padding:.65rem;border-radius:.85rem}
        @media (max-width:768px){h1{font-size:1.5rem!important}.block-container{padding-left:.45rem;padding-right:.45rem}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_brand_header(title: str = "EZPZ Model Builder", subtitle: str = "Unified multi-sport admin") -> None:
    if os.path.exists("ezpz_logo.png"):
        st.image("ezpz_logo.png", width=155)
    st.title(title)
    st.caption(subtitle)


def render_sport_header(sport: str, version: str) -> None:
    icon, label, _ = SPORT_META.get(sport, ("", sport, ""))
    left, right = st.columns([1, 3])
    with left:
        if st.button("← Sports", key=f"back_to_sports_{sport}", use_container_width=True):
            st.session_state["selected_sport"] = ""
            st.rerun()
    with right:
        st.markdown(f"### {icon} {label}")
        st.caption(f"Model: {version}")
