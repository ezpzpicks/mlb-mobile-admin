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


# TEMP 2026-09-22 MLB pitcher-K eligibility audit.
# Reads production history once per process, prints only non-secret aggregate/candidate data,
# and does not write or alter any dataset.
@st.cache_resource(show_spinner=False)
def _temporary_mlb_k_eligibility_audit_20260922():
    import json
    import math
    import pandas as pd
    from shared.turso_storage import read_dataset

    cols = [
        "Date", "Game Key", "Pitcher", "Team", "Opponent", "Role", "Model Version",
        "Projection", "True Projection", "Line", "Grade", "Published Grade", "Shadow Grade",
        "Tail Selected Side", "Tail Probability Edge", "Price Edge", "Actual Ks",
        "Projected Pitches", "Projected Batters Faced", "Normal Workload Pitches", "Normal Workload BF",
        "Lineup Confirmed", "Lineup Hitters Found", "Nine-Hitter Requirement Passed",
        "Workload Support", "Grade Restriction Reason", "Opener", "Bulk Pitcher",
    ]
    df = read_dataset("MLB", "pitcher_recent_form", cols)
    if df is None or df.empty:
        print("[mlb-k-gate-audit] no pitcher_recent_form rows")
        return True

    def num(v):
        try:
            s = str(v or "").replace("%","").replace("−","-").strip()
            if not s or s.lower() in {"nan","none","<na>"}:
                return math.nan
            return float(s)
        except Exception:
            return math.nan

    def truth(v):
        return str(v or "").strip().upper() in {"TRUE","YES","1","Y"}

    out = df.copy()
    out["_date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out[(out["_date"] >= pd.Timestamp("2026-08-10")) & (out["_date"] <= pd.Timestamp("2026-09-11"))].copy()

    for c in ["Projection","True Projection","Line","Tail Probability Edge","Price Edge","Actual Ks",
              "Projected Pitches","Projected Batters Faced","Normal Workload Pitches","Normal Workload BF",
              "Lineup Hitters Found"]:
        out["_"+c] = out[c].map(num)

    def edge_value(row):
        v = row["_Tail Probability Edge"]
        return v if pd.notna(v) else row["_Price Edge"]

    def projection_value(row):
        v = row["_True Projection"]
        return v if pd.notna(v) else row["_Projection"]

    def side_value(row):
        s = str(row.get("Tail Selected Side","") or "").strip().upper()
        if s in {"OVER","UNDER"}:
            return s
        g = " ".join([
            str(row.get("Grade","") or ""),
            str(row.get("Published Grade","") or ""),
            str(row.get("Shadow Grade","") or ""),
        ]).upper()
        if "UNDER" in g:
            return "UNDER"
        if "OVER" in g:
            return "OVER"
        p = projection_value(row)
        line = row["_Line"]
        if pd.notna(p) and pd.notna(line):
            return "OVER" if p > line else "UNDER" if p < line else ""
        return ""

    out["_edge"] = out.apply(edge_value, axis=1)
    out["_proj"] = out.apply(projection_value, axis=1)
    out["_side"] = out.apply(side_value, axis=1)

    def gap_value(row):
        p, line, side = row["_proj"], row["_Line"], row["_side"]
        if pd.isna(p) or pd.isna(line) or line <= 0 or side not in {"OVER","UNDER"}:
            return math.nan
        return ((p-line)/line) if side == "OVER" else ((line-p)/line)

    def result_value(row):
        actual, line, side = row["_Actual Ks"], row["_Line"], row["_side"]
        if pd.isna(actual) or pd.isna(line) or side not in {"OVER","UNDER"}:
            return ""
        if abs(actual-line) < 1e-9:
            return "P"
        win = actual > line if side == "OVER" else actual < line
        return "W" if win else "L"

    out["_gap"] = out.apply(gap_value, axis=1)
    out["_result"] = out.apply(result_value, axis=1)
    usable = out[out["_result"].isin(["W","L"]) & out["_edge"].notna() & out["_gap"].notna()].copy()

    def wl(frame):
        w = int((frame["_result"]=="W").sum())
        l = int((frame["_result"]=="L").sum())
        return {"n": int(len(frame)), "w": w, "l": l}

    baseline = usable[(usable["_edge"] >= 0.15) & (usable["_gap"] >= 0.10)].copy()
    strong = baseline[baseline["_gap"] >= 0.225].copy()
    regular = baseline[(baseline["_gap"] >= 0.10) & (baseline["_gap"] < 0.225)].copy()
    lean = usable[(usable["_edge"] >= 0.10) & (usable["_edge"] < 0.15) & (usable["_gap"] >= 0.15) & (usable["_gap"] < 0.25)].copy()

    def gate_status(row):
        role_ok = str(row.get("Role","") or "").strip().upper() == "STARTER"
        lineup_ok = truth(row.get("Nine-Hitter Requirement Passed",""))
        if not lineup_ok:
            lineup_ok = truth(row.get("Lineup Confirmed","")) and (row["_Lineup Hitters Found"] >= 8)

        support = str(row.get("Workload Support","") or "").strip().upper()
        restriction = str(row.get("Grade Restriction Reason","") or "").strip().lower()
        published = str(row.get("Published Grade","") or "").strip().upper()
        shadow = str(row.get("Shadow Grade","") or "").strip().upper()

        # Exact current hybrid gate is automatically satisfied when full starter workload exists.
        # If support is not FULL, a recorded structural pass/fail resolves most historical rows.
        if support == "FULL":
            hybrid_ok = True
            hybrid_status = "FULL"
        elif "hybrid/reliever workload without full support" in restriction:
            hybrid_ok = False
            hybrid_status = "FAIL_RECORDED"
        elif shadow not in {"","PASS"} and published not in {"","PASS"}:
            hybrid_ok = True
            hybrid_status = "PASSED_AT_BUILD"
        else:
            hybrid_ok = None
            hybrid_status = "UNKNOWN_IF_HYBRID"

        definite_pass = role_ok and lineup_ok and (hybrid_ok is True)
        definite_fail = (not role_ok) or (not lineup_ok) or (hybrid_ok is False)
        return pd.Series({
            "_role_ok": role_ok, "_lineup_ok": lineup_ok, "_hybrid_ok": hybrid_ok,
            "_hybrid_status": hybrid_status, "_definite_pass": definite_pass,
            "_definite_fail": definite_fail,
        })

    if not baseline.empty:
        baseline = pd.concat([baseline, baseline.apply(gate_status, axis=1)], axis=1)
    else:
        for c in ["_role_ok","_lineup_ok","_hybrid_ok","_hybrid_status","_definite_pass","_definite_fail"]:
            baseline[c] = []

    passed = baseline[baseline["_definite_pass"] == True].copy()
    failed = baseline[baseline["_definite_fail"] == True].copy()
    unknown = baseline[(baseline["_definite_pass"] != True) & (baseline["_definite_fail"] != True)].copy()

    summary = {
        "window_rows": int(len(out)),
        "usable_completed": wl(usable),
        "baseline_edge15_gap10": wl(baseline),
        "strong_edge15_gap22_5": wl(strong),
        "regular_edge15_gap10_to22_5": wl(regular),
        "lean_edge10_to15_gap15_to25": wl(lean),
        "gate_definite_pass": wl(passed),
        "gate_definite_fail": wl(failed),
        "gate_unknown_hybrid_only": wl(unknown),
        "role_fail_count": int((baseline["_role_ok"] == False).sum()) if not baseline.empty else 0,
        "lineup_fail_count": int((baseline["_lineup_ok"] == False).sum()) if not baseline.empty else 0,
        "workload_support_counts": baseline["Workload Support"].astype(str).value_counts(dropna=False).to_dict() if not baseline.empty else {},
        "nine_hitter_counts": baseline["Nine-Hitter Requirement Passed"].astype(str).value_counts(dropna=False).to_dict() if not baseline.empty else {},
    }
    print("[mlb-k-gate-audit] summary=" + json.dumps(summary, sort_keys=True))

    for _, r in baseline.sort_values(["_date","Pitcher"]).iterrows():
        row = {
            "date": str(r.get("Date","")), "pitcher": str(r.get("Pitcher","")),
            "side": str(r.get("_side","")), "line": r.get("_Line"), "projection": r.get("_proj"),
            "edge_pct": round(float(r.get("_edge",0))*100,1), "gap_pct": round(float(r.get("_gap",0))*100,1),
            "result": str(r.get("_result","")), "role": str(r.get("Role","")),
            "workload_support": str(r.get("Workload Support","")),
            "normal_pitches": None if pd.isna(r.get("_Normal Workload Pitches")) else float(r.get("_Normal Workload Pitches")),
            "normal_bf": None if pd.isna(r.get("_Normal Workload BF")) else float(r.get("_Normal Workload BF")),
            "lineup_confirmed": str(r.get("Lineup Confirmed","")),
            "hitters": None if pd.isna(r.get("_Lineup Hitters Found")) else float(r.get("_Lineup Hitters Found")),
            "lineup_gate": str(r.get("Nine-Hitter Requirement Passed","")),
            "published_grade": str(r.get("Published Grade","")),
            "shadow_grade": str(r.get("Shadow Grade","")),
            "hybrid_gate_status": str(r.get("_hybrid_status","")),
            "definite_pass": bool(r.get("_definite_pass",False)),
            "definite_fail": bool(r.get("_definite_fail",False)),
            "restriction": str(r.get("Grade Restriction Reason","")),
        }
        print("[mlb-k-gate-audit] candidate=" + json.dumps(row, sort_keys=True))
    return True

try:
    _temporary_mlb_k_eligibility_audit_20260922()
except Exception as exc:
    print(f"[mlb-k-gate-audit] ERROR: {type(exc).__name__}: {exc}")


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


def _install_cfb_evaluation_cache(builder) -> None:
    """Reuse the already-rendered CFB result on unchanged Streamlit reruns.

    A Streamlit button click reruns the script from the top. The CFB save buttons
    sit below ``evaluate_game()``, so pressing Save used to run the full 30,000-
    simulation projection a second time before the button branch could persist the
    result. On the small Render admin instance that can saturate the CPU for roughly
    a minute even though the actual Turso save is only one bounded round trip.

    Cache only the most recent interactive evaluation and include every user/model
    input that can change the displayed grade. This makes a Save rerun reuse exactly
    what is already on screen while still invalidating immediately when a line,
    price, personnel input, environment input, or selected-team rating changes.
    """
    if getattr(builder, "_EZPZ_CFB_EVALUATION_CACHE", False):
        return

    original_evaluate_game = builder.evaluate_game

    def object_signature(value):
        try:
            return tuple(sorted((str(key), repr(item)) for key, item in vars(value).items()))
        except Exception:
            return repr(value)

    def selected_rating_signature(game, ratings):
        output = []
        try:
            teams = [str(game.get("Away Team", "")), str(game.get("Home Team", ""))]
            team_values = ratings["Team"].astype(str)
            for team in teams:
                matched = ratings.loc[team_values == team]
                if matched.empty:
                    output.append((team, "missing"))
                    continue
                row = matched.iloc[0]
                output.append(
                    (
                        team,
                        repr(row.get("Power Rating", "")),
                        repr(row.get("Offense Rating", "")),
                        repr(row.get("Defense Rating", "")),
                        repr(row.get("Data Confidence", "")),
                        repr(row.get("Updated", "")),
                    )
                )
        except Exception:
            return ()
        return tuple(output)

    def cached_evaluate_game(*args, **kwargs):
        # evaluate_game(game, ratings, away_personnel, home_personnel,
        # environment, market_spread, market_total, away_ml, home_ml, ...)
        if len(args) < 5:
            return original_evaluate_game(*args, **kwargs)

        game = args[0]
        ratings = args[1]
        game_id = str(game.get("Game ID", ""))
        signature = repr(
            (
                str(getattr(builder, "MODEL_VERSION", "")),
                game_id,
                str(game.get("Season", "")),
                str(game.get("Week", "")),
                selected_rating_signature(game, ratings),
                object_signature(args[2]),
                object_signature(args[3]),
                object_signature(args[4]),
                tuple(repr(value) for value in args[5:]),
                tuple(sorted((str(key), repr(value)) for key, value in kwargs.items())),
            )
        )

        cache_key = "_cfb_interactive_evaluation_key"
        result_key = "_cfb_interactive_evaluation_result"
        if st.session_state.get(cache_key) == signature:
            cached = st.session_state.get(result_key)
            if isinstance(cached, dict):
                print(f"[cfb-eval-cache] hit game={game_id}")
                return cached

        started = time.perf_counter()
        result = original_evaluate_game(*args, **kwargs)
        st.session_state[cache_key] = signature
        st.session_state[result_key] = result
        print(f"[cfb-eval-cache] miss game={game_id} computed_in={time.perf_counter() - started:.3f}s")
        return result

    builder.evaluate_game = cached_evaluate_game
    builder._EZPZ_CFB_EVALUATION_CACHE = True


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
    "NFL": "nfl-v4.14-atd-engine-calibration-2026-09-20",
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
    from builders.cfb_save_type_guard import install_save_type_guard
    from builders.cfb_game_regression import install_regression_layer
    from builders.cfb_total_regression import install_total_regression
    from builders.cfb_market_calibration import install_market_calibration
    from builders.cfb_covers import install_covers_layer
    from builders.cfb_interactive_recovery import install_interactive_recovery
    from builders.cfb_runtime_guard import install_runtime_guard
    install_runtime_guard(cfb_builder)
    install_save_type_guard(cfb_builder)
    install_regression_layer(cfb_builder)
    install_total_regression(cfb_builder)
    install_market_calibration(cfb_builder)
    install_covers_layer(cfb_builder)
    install_interactive_recovery(cfb_builder)
    _install_cfb_evaluation_cache(cfb_builder)
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
