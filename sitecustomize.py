"""Render startup hooks for the EZPZ admin.

Production data persistence is Turso-only. This module intentionally contains
no Google Sheets bootstrap, quota handling, or shared-workbook fallback logic.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse


def _is_streamlit_runtime() -> bool:
    executable = os.path.basename(str(sys.argv[0] or "")).lower()
    return executable == "streamlit" or executable.startswith("streamlit-")


def _log_turso_env_visibility() -> None:
    """Log only Turso env key presence/length and safe URL hostname, never secret values."""
    names = (
        "TURSO_DATABASE_URL",
        "TURSO_URL",
        "turso_TURSO_DATABASE_URL",
        "DATABASE_URL",
        "TURSO_AUTH_TOKEN",
        "TURSO_DATABASE_AUTH_TOKEN",
        "turso_TURSO_AUTH_TOKEN",
        "TURSO_TOKEN",
        "DATABASE_AUTH_TOKEN",
    )
    details = []
    for name in names:
        present = name in os.environ
        value = str(os.environ.get(name, "") or "")
        details.append(f"{name}=present:{present},len:{len(value)}")
    print("Turso env visibility: " + " | ".join(details))

    for name in ("TURSO_DATABASE_URL", "TURSO_URL", "turso_TURSO_DATABASE_URL", "DATABASE_URL"):
        raw = str(os.environ.get(name, "") or "").strip()
        if not raw:
            continue
        normalized = "https://" + raw[len("libsql://") :] if raw.startswith("libsql://") else raw
        try:
            host = urlparse(normalized).netloc or "invalid-url"
        except Exception:
            host = "invalid-url"
        print(f"Turso URL target: key={name}, host={host}")
        break




def _run_temporary_mlb_k_gate_audit() -> None:
    """One-time read-only audit of the current MLB K structural publication gates."""
    try:
        import json
        import math
        import pandas as pd
        from shared.turso_storage import read_dataset

        cols = [
            "Date", "Game Key", "Pitcher", "Team", "Opponent", "Role", "Model Version",
            "Projection", "True Projection", "Line", "Grade", "Published Grade", "Shadow Grade",
            "Tail Selected Side", "Tail Probability Edge", "Price Edge", "Actual Ks",
            "Projected Pitches", "Projected Batters Faced",
            "Normal Workload Pitches", "Normal Workload BF",
            "Lineup Confirmed", "Lineup Hitters Found", "Nine-Hitter Requirement Passed",
            "Workload Support", "Grade Restriction Reason", "Opener", "Bulk Pitcher",
            "Data Health Notes", "Calibration Notes",
        ]
        df = read_dataset("MLB", "pitcher_recent_form", cols)
        if df is None or df.empty:
            print("[mlb-k-gate-audit] no pitcher_recent_form rows", flush=True)
            return

        def num(v):
            try:
                s = str(v or "").replace("%", "").replace("−", "-").strip()
                if not s or s.lower() in {"nan", "none", "<na>"}:
                    return math.nan
                value = float(s)
                # Persisted probability/edge fields are normally decimals. Protect
                # against an older row that may have saved a percent representation.
                return value / 100.0 if abs(value) > 1.0 and "Edge" in "" else value
            except Exception:
                return math.nan

        def edge_num(v):
            value = num(v)
            if pd.isna(value):
                return value
            return value / 100.0 if abs(value) > 1.0 else value

        def truth(v):
            return str(v or "").strip().upper() in {"TRUE", "YES", "1", "Y"}

        out = df.copy()
        out["_date"] = pd.to_datetime(out["Date"], errors="coerce")
        # Same comparable V16.3-V16.5 window used by the threshold analysis.
        out = out[(out["_date"] >= pd.Timestamp("2026-08-10")) & (out["_date"] <= pd.Timestamp("2026-09-11"))].copy()

        for c in [
            "Projection", "True Projection", "Line", "Actual Ks", "Projected Pitches",
            "Projected Batters Faced", "Normal Workload Pitches", "Normal Workload BF",
            "Lineup Hitters Found",
        ]:
            out["_" + c] = out[c].map(num)
        out["_Tail Probability Edge"] = out["Tail Probability Edge"].map(edge_num)
        out["_Price Edge"] = out["Price Edge"].map(edge_num)

        def edge_value(row):
            value = row["_Tail Probability Edge"]
            return value if pd.notna(value) else row["_Price Edge"]

        def projection_value(row):
            value = row["_True Projection"]
            return value if pd.notna(value) else row["_Projection"]

        def side_value(row):
            side = str(row.get("Tail Selected Side", "") or "").strip().upper()
            if side in {"OVER", "UNDER"}:
                return side
            grade_text = " ".join([
                str(row.get("Grade", "") or ""),
                str(row.get("Published Grade", "") or ""),
                str(row.get("Shadow Grade", "") or ""),
            ]).upper()
            if "UNDER" in grade_text:
                return "UNDER"
            if "OVER" in grade_text:
                return "OVER"
            projection = projection_value(row)
            line = row["_Line"]
            if pd.notna(projection) and pd.notna(line):
                return "OVER" if projection > line else "UNDER" if projection < line else ""
            return ""

        out["_edge"] = out.apply(edge_value, axis=1)
        out["_proj"] = out.apply(projection_value, axis=1)
        out["_side"] = out.apply(side_value, axis=1)

        def gap_value(row):
            projection, line, side = row["_proj"], row["_Line"], row["_side"]
            if pd.isna(projection) or pd.isna(line) or line <= 0 or side not in {"OVER", "UNDER"}:
                return math.nan
            return (projection - line) / line if side == "OVER" else (line - projection) / line

        def result_value(row):
            actual, line, side = row["_Actual Ks"], row["_Line"], row["_side"]
            if pd.isna(actual) or pd.isna(line) or side not in {"OVER", "UNDER"}:
                return ""
            if abs(actual - line) < 1e-9:
                return "P"
            won = actual > line if side == "OVER" else actual < line
            return "W" if won else "L"

        out["_gap"] = out.apply(gap_value, axis=1)
        out["_result"] = out.apply(result_value, axis=1)
        usable = out[
            out["_result"].isin(["W", "L"])
            & out["_edge"].notna()
            & out["_gap"].notna()
        ].copy()

        def wl(frame):
            return {
                "n": int(len(frame)),
                "w": int((frame["_result"] == "W").sum()),
                "l": int((frame["_result"] == "L").sum()),
            }

        baseline = usable[(usable["_edge"] >= 0.15) & (usable["_gap"] >= 0.10)].copy()
        strong = baseline[baseline["_gap"] >= 0.225].copy()
        regular = baseline[(baseline["_gap"] >= 0.10) & (baseline["_gap"] < 0.225)].copy()
        lean = usable[
            (usable["_edge"] >= 0.10) & (usable["_edge"] < 0.15)
            & (usable["_gap"] >= 0.15) & (usable["_gap"] < 0.25)
        ].copy()

        def gate_status(row):
            role_ok = str(row.get("Role", "") or "").strip().upper() == "STARTER"

            # The legacy-named field stores the V16 compound lineup gate. If absent,
            # reconstruct its observable pieces from confirmed source + profile count.
            gate_text = str(row.get("Nine-Hitter Requirement Passed", "") or "").strip()
            lineup_ok = truth(gate_text) if gate_text else (
                truth(row.get("Lineup Confirmed", ""))
                and float(row.get("_Lineup Hitters Found", math.nan)) >= 8
            )

            support = str(row.get("Workload Support", "") or "").strip().upper()
            restriction = str(row.get("Grade Restriction Reason", "") or "").strip().lower()
            published = str(row.get("Published Grade", "") or "").strip().upper()
            shadow = str(row.get("Shadow Grade", "") or "").strip().upper()
            notes = " ".join([
                str(row.get("Data Health Notes", "") or ""),
                str(row.get("Calibration Notes", "") or ""),
            ]).lower()

            # Exact hybrid boolean was not persisted in the historical schema.
            # FULL support always satisfies the current hybrid exception. An explicit
            # historical restriction proves failure. A non-PASS published grade with
            # a non-PASS shadow proves the gate passed at build time.
            if support == "FULL":
                hybrid_ok, hybrid_status = True, "FULL_SUPPORT"
            elif "hybrid/reliever workload without full support" in restriction:
                hybrid_ok, hybrid_status = False, "EXPLICIT_FAIL"
            elif shadow not in {"", "PASS"} and published not in {"", "PASS"}:
                hybrid_ok, hybrid_status = True, "PASSED_AT_BUILD"
            elif "hybrid" in notes or "reliever" in notes:
                hybrid_ok, hybrid_status = False, "HYBRID_WITHOUT_FULL_SUPPORT"
            else:
                hybrid_ok, hybrid_status = None, "UNKNOWN_IF_HYBRID"

            definite_pass = role_ok and lineup_ok and hybrid_ok is True
            definite_fail = (not role_ok) or (not lineup_ok) or hybrid_ok is False
            return pd.Series({
                "_role_ok": role_ok,
                "_lineup_ok": lineup_ok,
                "_hybrid_ok": hybrid_ok,
                "_hybrid_status": hybrid_status,
                "_definite_pass": definite_pass,
                "_definite_fail": definite_fail,
            })

        if not baseline.empty:
            baseline = pd.concat([baseline, baseline.apply(gate_status, axis=1)], axis=1)
        else:
            for c in ["_role_ok", "_lineup_ok", "_hybrid_ok", "_hybrid_status", "_definite_pass", "_definite_fail"]:
                baseline[c] = []

        passed = baseline[baseline["_definite_pass"] == True].copy()
        failed = baseline[baseline["_definite_fail"] == True].copy()
        unknown = baseline[
            (baseline["_definite_pass"] != True) & (baseline["_definite_fail"] != True)
        ].copy()

        # Also test each gate independently to measure whether it actually removes
        # winners/losses from the edge>=15% + gap>=10% group.
        role_only = baseline[baseline["_role_ok"] == True].copy()
        lineup_only = baseline[baseline["_lineup_ok"] == True].copy()
        hybrid_known_pass = baseline[baseline["_hybrid_ok"] == True].copy()

        summary = {
            "window_rows": int(len(out)),
            "usable_completed": wl(usable),
            "baseline_edge15_gap10": wl(baseline),
            "strong_edge15_gap22_5": wl(strong),
            "regular_edge15_gap10_to22_5": wl(regular),
            "lean_edge10_to15_gap15_to25": wl(lean),
            "after_role_gate": wl(role_only),
            "after_lineup_gate": wl(lineup_only),
            "after_known_hybrid_gate_pass": wl(hybrid_known_pass),
            "after_all_current_gates_definite": wl(passed),
            "definite_gate_failures": wl(failed),
            "hybrid_gate_unknown_only": wl(unknown),
            "role_fail_count": int((baseline["_role_ok"] == False).sum()) if not baseline.empty else 0,
            "lineup_fail_count": int((baseline["_lineup_ok"] == False).sum()) if not baseline.empty else 0,
            "workload_support_counts": baseline["Workload Support"].astype(str).value_counts(dropna=False).to_dict() if not baseline.empty else {},
            "lineup_gate_counts": baseline["Nine-Hitter Requirement Passed"].astype(str).value_counts(dropna=False).to_dict() if not baseline.empty else {},
        }
        print("[mlb-k-gate-audit] summary=" + json.dumps(summary, sort_keys=True), flush=True)

        for _, row in baseline.sort_values(["_date", "Pitcher"]).iterrows():
            candidate = {
                "date": str(row.get("Date", "")),
                "pitcher": str(row.get("Pitcher", "")),
                "side": str(row.get("_side", "")),
                "line": None if pd.isna(row.get("_Line")) else float(row.get("_Line")),
                "projection": None if pd.isna(row.get("_proj")) else float(row.get("_proj")),
                "edge_pct": round(float(row.get("_edge", 0.0)) * 100.0, 1),
                "gap_pct": round(float(row.get("_gap", 0.0)) * 100.0, 1),
                "result": str(row.get("_result", "")),
                "role": str(row.get("Role", "")),
                "workload_support": str(row.get("Workload Support", "")),
                "normal_pitches": None if pd.isna(row.get("_Normal Workload Pitches")) else float(row.get("_Normal Workload Pitches")),
                "normal_bf": None if pd.isna(row.get("_Normal Workload BF")) else float(row.get("_Normal Workload BF")),
                "lineup_confirmed": str(row.get("Lineup Confirmed", "")),
                "hitters": None if pd.isna(row.get("_Lineup Hitters Found")) else float(row.get("_Lineup Hitters Found")),
                "lineup_gate": str(row.get("Nine-Hitter Requirement Passed", "")),
                "published_grade": str(row.get("Published Grade", "")),
                "shadow_grade": str(row.get("Shadow Grade", "")),
                "hybrid_gate_status": str(row.get("_hybrid_status", "")),
                "definite_pass": bool(row.get("_definite_pass", False)),
                "definite_fail": bool(row.get("_definite_fail", False)),
                "restriction": str(row.get("Grade Restriction Reason", "")),
            }
            print("[mlb-k-gate-audit] candidate=" + json.dumps(candidate, sort_keys=True), flush=True)
    except Exception as exc:
        print(f"[mlb-k-gate-audit] ERROR: {type(exc).__name__}: {exc}", flush=True)

if _is_streamlit_runtime():
    _log_turso_env_visibility()
    _run_temporary_mlb_k_gate_audit()

    try:
        from shared import storage

        initialized = storage.initialize_sport_workbooks(("NFL", "CFB", "CBB"))
        print(
            "Turso sport storage startup ready: "
            + ", ".join(f"{sport}={value}" for sport, value in sorted(initialized.items()))
        )
    except Exception as exc:
        # Keep the UI bootable so the storage error is visible in the app/logs.
        # There is deliberately no Google Sheets fallback.
        print(f"Turso sport storage startup failed: {exc}")

    try:
        from shared.mlb_builder_resume import install_mlb_builder_resume

        install_mlb_builder_resume()
        print("MLB builder recovery checkpointing ready")
    except Exception as exc:
        print(f"MLB builder recovery checkpointing failed: {exc}")
