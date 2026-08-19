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


def _run_mlb_builder_fast(builder_path: Path) -> None:
    """Run MLB with small performance-only patches applied in memory.

    The production MLB builder is intentionally kept intact. These patches avoid
    repeated Google Sheet downloads on every Streamlit widget rerun and stop the
    Handpick Any candidate matcher from rescanning historical tracker rows.
    """
    source = builder_path.read_text(encoding="utf-8")

    # Streamlit reruns the whole Slate page whenever game/market selectors change.
    # Cache Sheet reads briefly so those UI-only reruns do not redownload the same
    # tabs. Any successful write clears the cache immediately below.
    read_marker = "\ndef read_sheet(tab_name, columns):\n"
    cached_read = "\n@st.cache_data(ttl=20, show_spinner=False)\ndef read_sheet(tab_name, columns):\n"
    if read_marker in source and "@st.cache_data(ttl=20, show_spinner=False)\ndef read_sheet" not in source:
        source = source.replace(read_marker, cached_read, 1)

    write_marker = "        worksheet.update(values)\n        return True\n"
    write_with_invalidation = (
        "        worksheet.update(values)\n"
        "        try:\n"
        "            read_sheet.clear()\n"
        "        except Exception:\n"
        "            pass\n"
        "        return True\n"
    )
    if write_marker in source and "read_sheet.clear()" not in source:
        source = source.replace(write_marker, write_with_invalidation, 1)

    # Candidate construction is pure for a given saved slate/research table, so
    # market/game selector reruns can reuse it instead of reparsing every play.
    build_marker = "\ndef build_handpickable_plays(today_slate, all_game_trends=None):\n"
    cached_build = (
        "\n@st.cache_data(ttl=20, show_spinner=False)\n"
        "def build_handpickable_plays(today_slate, all_game_trends=None):\n"
    )
    if build_marker in source and "@st.cache_data(ttl=20, show_spinner=False)\ndef build_handpickable_plays" not in source:
        source = source.replace(build_marker, cached_build, 1)

    # Keep completed-bet filtering correct, but only scan today's tracker rows.
    # Previously every candidate scanned the entire historical tracker twice.
    tracker_marker = (
        "            date_mask = tracker_df[\"Date\"].astype(str).str.strip() == today\n"
        "            result_mask = tracker_df[\"Result\"].astype(str).str.strip().str.upper().isin([\"\", \"PENDING\"])\n"
        "            today_tracker = tracker_df[date_mask & result_mask].copy()\n"
    )
    tracker_fast = (
        "            date_mask = tracker_df[\"Date\"].astype(str).str.strip() == today\n"
        "            today_all_tracker = tracker_df[date_mask].copy()\n"
        "            result_mask = tracker_df[\"Result\"].astype(str).str.strip().str.upper().isin([\"\", \"PENDING\"])\n"
        "            today_tracker = tracker_df[date_mask & result_mask].copy()\n"
    )
    if tracker_marker in source:
        source = source.replace(tracker_marker, tracker_fast, 1)

    empty_tracker_marker = "            today_tracker = pd.DataFrame(columns=TRACKER_COLUMNS)\n\n        if \"Favorite Pick\" not in today_tracker.columns:\n"
    empty_tracker_fast = (
        "            today_tracker = pd.DataFrame(columns=TRACKER_COLUMNS)\n"
        "            today_all_tracker = today_tracker.copy()\n\n"
        "        if \"Favorite Pick\" not in today_tracker.columns:\n"
    )
    if empty_tracker_marker in source:
        source = source.replace(empty_tracker_marker, empty_tracker_fast, 1)

    candidate_marker = (
        "                    best_play_is_open_for_handpick(play, tracker_df)\n"
        "                    and not handpick_play_is_already_selected(play, tracker_df)\n"
    )
    candidate_fast = (
        "                    best_play_is_open_for_handpick(play, today_all_tracker)\n"
        "                    and not handpick_play_is_already_selected(play, today_all_tracker)\n"
    )
    if candidate_marker in source:
        source = source.replace(candidate_marker, candidate_fast, 1)

    namespace = {
        "__name__": "__main__",
        "__file__": str(builder_path),
        "__package__": None,
    }
    exec(compile(source, str(builder_path), "exec"), namespace)


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

    st.caption("MLB remains the production engine. NFL now includes the automated slate, lineup-aware game engine, and in-depth calibrated QB/RB/WR/TE prop model. CFB now automatically loads the slate, free public data, available markets, environment, and spread/moneyline/totals projections with no setup sequence or sports-data API key. CBB remains a foundation model for setup and shadow testing.")
    st.stop()

versions = {
    "MLB": "v15.2-public-betting-splits-2026-07-27",
    "CFB": "cfb-v1.2-free-no-key-score-distribution-2026-07-18",
    "NFL": "nfl-v3.3-qb-passing-yards-regression-2026-08-13",
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
    # Execute MLB only after selection, with performance-only runtime patches.
    _run_mlb_builder_fast(ROOT / "builders" / "mlb_builder.py")
elif selected_sport == "CFB":
    from builders.cfb_builder import render
    render()
elif selected_sport == "NFL":
    from builders.nfl_builder import render
    render()
elif selected_sport == "CBB":
    from builders.cbb_builder import render
    render()
