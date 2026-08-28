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


def _install_mlb_sheet_read_cache() -> None:
    """Reduce redundant MLB Google Sheet reads across Streamlit reruns.

    Streamlit reruns the MLB builder on nearly every widget interaction. The
    production builder also performs several tracking/history reads after a
    build, so repeated reruns can otherwise exceed Google's per-user Sheets read
    quota. Cache every MLB worksheet briefly, invalidate immediately after the
    builder writes that worksheet, pace uncached reads, and retry transient 429
    read-quota responses before surfacing an error.
    """
    try:
        import gspread
    except Exception:
        return

    worksheet_cls = gspread.Worksheet
    if getattr(worksheet_cls, "_ezpz_mlb_read_cache_installed", False):
        return

    cache_state_key = "_ezpz_mlb_sheet_read_cache_v2"
    ttl_seconds = 20.0

    # Google's default Sheets quota is 60 read requests/minute/user. Keep a
    # healthy cushion because the same service account can also be used by
    # another process. In normal use the cache means this limiter rarely waits.
    read_window_seconds = 60.0
    max_network_reads = 40
    read_timestamps = []
    rate_lock = threading.Lock()

    original_get_all_records = worksheet_cls.get_all_records
    original_get_all_values = worksheet_cls.get_all_values
    original_clear = worksheet_cls.clear
    original_update = worksheet_cls.update

    def _cache_enabled(worksheet) -> bool:
        return (
            str(st.session_state.get("selected_sport", "") or "").upper() == "MLB"
            and bool(str(getattr(worksheet, "title", "") or "").strip())
        )

    def _worksheet_identity(worksheet):
        spreadsheet_id = str(getattr(worksheet, "spreadsheet_id", "") or "")
        if not spreadsheet_id:
            spreadsheet = getattr(worksheet, "spreadsheet", None)
            spreadsheet_id = str(getattr(spreadsheet, "id", "") or "")
        return spreadsheet_id, str(getattr(worksheet, "title", "") or "")

    def _cache_key(worksheet, method_name, args, kwargs):
        spreadsheet_id, title = _worksheet_identity(worksheet)
        try:
            kwargs_key = repr(sorted(kwargs.items(), key=lambda item: item[0]))
        except Exception:
            kwargs_key = repr(kwargs)
        return spreadsheet_id, title, method_name, repr(args), kwargs_key

    def _cache_store():
        cache = st.session_state.get(cache_state_key)
        if not isinstance(cache, dict):
            cache = {}
            st.session_state[cache_state_key] = cache
        return cache

    def _is_read_quota_error(exc) -> bool:
        message = str(exc or "").lower()
        return (
            "429" in message
            and (
                "quota" in message
                or "read requests" in message
                or "rate limit" in message
            )
        )

    def _throttle_network_read() -> None:
        # Serialize only the small accounting section. If the session has
        # already generated a burst, wait until a slot leaves the rolling
        # 60-second window rather than sending another request that Google will
        # reject.
        with rate_lock:
            while True:
                now = time.monotonic()
                cutoff = now - read_window_seconds
                read_timestamps[:] = [stamp for stamp in read_timestamps if stamp > cutoff]
                if len(read_timestamps) < max_network_reads:
                    read_timestamps.append(now)
                    return
                wait_seconds = max(0.05, read_window_seconds - (now - read_timestamps[0]) + 0.05)
                time.sleep(wait_seconds)

    def _network_read_with_retry(worksheet, original_method, args, kwargs):
        # A quota burst may already have happened immediately before this patch
        # gets a chance to pace the next read. Brief retries make that transient
        # condition self-healing instead of showing the red APIError card.
        retry_delays = (0.0, 3.0, 8.0, 15.0)
        last_exc = None
        for attempt, delay in enumerate(retry_delays):
            if delay:
                time.sleep(delay)
            _throttle_network_read()
            try:
                return original_method(worksheet, *args, **kwargs)
            except Exception as exc:
                last_exc = exc
                if not _is_read_quota_error(exc) or attempt == len(retry_delays) - 1:
                    raise
        if last_exc is not None:
            raise last_exc

    def _cached_read(worksheet, method_name, original_method, args, kwargs):
        if not _cache_enabled(worksheet):
            return original_method(worksheet, *args, **kwargs)

        cache = _cache_store()
        key = _cache_key(worksheet, method_name, args, kwargs)
        now = time.monotonic()
        cached = cache.get(key)
        if cached is not None:
            cached_at, cached_value = cached
            if now - cached_at <= ttl_seconds:
                return copy.deepcopy(cached_value)
            cache.pop(key, None)

        value = _network_read_with_retry(worksheet, original_method, args, kwargs)
        cache[key] = (time.monotonic(), copy.deepcopy(value))
        return value

    def _invalidate_worksheet(worksheet) -> None:
        try:
            cache = st.session_state.get(cache_state_key, {})
            if not isinstance(cache, dict) or not cache:
                return
            spreadsheet_id, title = _worksheet_identity(worksheet)
            stale_keys = [
                key for key in list(cache)
                if len(key) >= 2 and key[0] == spreadsheet_id and key[1] == title
            ]
            for key in stale_keys:
                cache.pop(key, None)
        except Exception:
            pass

    def _get_all_records_cached(worksheet, *args, **kwargs):
        return _cached_read(
            worksheet,
            "get_all_records",
            original_get_all_records,
            args,
            kwargs,
        )

    def _get_all_values_cached(worksheet, *args, **kwargs):
        return _cached_read(
            worksheet,
            "get_all_values",
            original_get_all_values,
            args,
            kwargs,
        )

    def _clear_and_invalidate(worksheet, *args, **kwargs):
        result = original_clear(worksheet, *args, **kwargs)
        _invalidate_worksheet(worksheet)
        return result

    def _update_and_invalidate(worksheet, *args, **kwargs):
        result = original_update(worksheet, *args, **kwargs)
        _invalidate_worksheet(worksheet)
        return result

    worksheet_cls.get_all_records = _get_all_records_cached
    worksheet_cls.get_all_values = _get_all_values_cached
    worksheet_cls.clear = _clear_and_invalidate
    worksheet_cls.update = _update_and_invalidate
    worksheet_cls._ezpz_mlb_read_cache_installed = True


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

    st.caption("MLB remains the production engine. NFL includes regression game and QB/RB/WR yardage models. CFB now combines the validated spread-margin regression with an independent pace/efficiency totals regression, derives team scores algebraically, and retains live personnel/weather overlays plus calibrated market evaluation. CBB remains a foundation model for setup and shadow testing.")
    st.stop()

versions = {
    "MLB": "v15.2-public-betting-splits-2026-07-27",
    "CFB": "cfb-v2.3-independent-total-2026-08-28",
    "NFL": "nfl-v4.2-price-aware-odds-2026-08-21",
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
    # Keep the production builder itself unchanged; only avoid redundant Sheet
    # downloads while Streamlit reruns the same controls and tracking helpers.
    _install_mlb_sheet_read_cache()
    runpy.run_path(str(ROOT / "builders" / "mlb_builder.py"), run_name="__main__")
elif selected_sport == "CFB":
    set_storage_sport("CFB")
    from builders import cfb_builder
    from builders.cfb_game_regression import install_regression_layer
    from builders.cfb_total_regression import install_total_regression
    from builders.cfb_market_calibration import install_market_calibration
    install_regression_layer(cfb_builder)
    install_total_regression(cfb_builder)
    install_market_calibration(cfb_builder)
    cfb_builder.MODEL_VERSION = "cfb-v2.3-independent-total-2026-08-28"
    cfb_builder.render()
elif selected_sport == "NFL":
    set_storage_sport("NFL")
    from builders import nfl_builder
    from builders.nfl_game_regression import install_regression_layer
    from builders.nfl_skill_prop_regression import install_skill_prop_regression
    from builders.nfl_skill_prop_consistency import install_skill_prop_consistency
    install_regression_layer(nfl_builder)
    install_skill_prop_regression(nfl_builder)
    install_skill_prop_consistency(nfl_builder)
    nfl_builder.MODEL_VERSION = "nfl-v4.2-price-aware-odds-2026-08-21"
    nfl_builder.render()
elif selected_sport == "CBB":
    set_storage_sport("CBB")
    from builders.cbb_builder import render
    render()
