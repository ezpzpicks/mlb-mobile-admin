"""Admin view of strictly prospective NFL candidate and benchmark results."""
from __future__ import annotations

from collections import defaultdict
import json

from builders.nfl_research import LABELS, START_DATE, number

CANDIDATES = ("totals_weights_v1", "totals_injury_v1", "rb_receiving_v1")


def totals(rows):
    selected = [r for r in rows if r.get("selected")]
    settled = [r for r in selected if r.get("result") in {"W", "L", "P", "V"}]
    wins = sum(r["result"] == "W" for r in settled); losses = sum(r["result"] == "L" for r in settled)
    pushes = sum(r["result"] == "P" for r in settled); voids = sum(r["result"] == "V" for r in settled)
    risk = wins + losses + pushes
    units = sum(number(r.get("units")) or 0 for r in settled)
    forecasts = {r["game_id"] for r in selected}
    games = {r["game_id"] for r in settled if r["result"] != "V"}
    weeks = {(r.get("season"), r.get("week")) for r in settled if r["result"] != "V"}
    probabilities = [(number(r.get("probability")), r["result"] == "W") for r in settled if r["result"] in {"W", "L"}]
    probabilities = [(p, y) for p, y in probabilities if p is not None and 0 <= p <= 1]
    return {"Record": f"{wins}-{losses}-{pushes}", "Win %": round(100 * wins / (wins + losses), 1) if wins + losses else None,
        "Units": round(units, 2), "ROI %": round(100 * units / risk, 1) if risk else None,
        "Pending": len(selected) - len(settled), "Voids": voids, "Games graded": len(games), "Games captured": len(forecasts), "Weeks": len(weeks),
        "Predicted win %": round(100 * sum(p for p, _ in probabilities) / len(probabilities), 1) if probabilities else None,
        "Brier score": round(sum((p - y) ** 2 for p, y in probabilities) / len(probabilities), 4) if probabilities else None}


def matched_comparisons(rows):
    """Each benchmark is scored on precisely the same captured game/prop set."""
    def key(r):return r["game_id"], r["market"], r.get("team", ""), r.get("player", "")
    settled = [r for r in rows if r.get("selected") and r.get("result") in {"W", "L", "P"}]
    groups = defaultdict(list)
    for r in settled:groups[r["strategy"]].append(r)
    output = []
    for candidate in CANDIDATES:
        keys = {key(r) for r in groups[candidate]}
        benchmarks = ("rb_over", "rb_under") if candidate == "rb_receiving_v1" else ("always_over", "always_under", "direction_total")
        for benchmark in benchmarks:
            matched = [r for r in groups[benchmark] if key(r) in keys]
            matched_keys = {key(r) for r in matched}
            chosen = [r for r in groups[candidate] if key(r) in matched_keys]
            if not matched:
                continue
            a, b = totals(chosen), totals(matched)
            output.append({"Candidate": LABELS[candidate], "Benchmark": LABELS[benchmark], "Matched plays": len(matched),
                "Candidate record": a["Record"], "Benchmark record": b["Record"],
                "Candidate units": a["Units"], "Benchmark units": b["Units"], "Difference": round(a["Units"] - b["Units"], 2)})
    return output


def render_research():
    import pandas as pd
    import streamlit as st
    from shared import turso_storage as turso
    from shared.nfl_auto_storage import NflDatabase
    from shared.nfl_research_storage import read_rows

    st.subheader("NFL Forward Research")
    st.caption(f"New forecasts from {START_DATE}. Each first pregame save is frozen with its line, price and inputs. Historical backtests are excluded from these records.")
    st.info("Research candidates run alongside the official model. Review requires at least 50 distinct graded games and four NFL weeks, positive returns, and comparison with matched benchmarks. Promotion requires a separate decision.")

    @st.cache_data(ttl=60, show_spinner=False)
    def load_rows():
        db = NflDatabase(turso)
        try:return read_rows(db)
        finally:turso._pipeline = db.original_pipeline

    if st.button("Refresh research", key="nfl_research_refresh"):
        load_rows.clear()
    try:
        rows = load_rows()
    except Exception as exc:
        st.error(f"Research records are temporarily unavailable: {exc}")
        return
    if not rows:
        st.success("Tracking is ready. Records will begin with the next successful pregame builder save.")
    grouped = defaultdict(list)
    for row in rows:grouped[row["strategy"]].append(row)
    table = []
    for strategy in (*CANDIDATES, "current_total", "current_spread"):
        stats = totals(grouped[strategy]); stage = "Review due" if stats["Games graded"] >= 50 and stats["Weeks"] >= 4 else "Building sample"
        table.append({"Strategy": LABELS[strategy], **{k: stats[k] for k in ("Record", "Win %", "Units", "ROI %", "Pending", "Games graded", "Weeks")}, "Review": stage})
    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    st.caption("One unit risked per selected play. Pushes refund the stake; voids are excluded from ROI. Overlapping strategies are evaluated separately.")
    st.subheader("Matched benchmarks")
    comparison = matched_comparisons(rows)
    if comparison:st.dataframe(pd.DataFrame(comparison), hide_index=True, use_container_width=True)
    else:st.caption("Comparisons appear after candidate and benchmark forecasts have both settled.")
    with st.expander("Price quality and model confidence"):
        sources = sorted({r.get("price_source", "unknown") for r in rows})
        for source in sources:
            stats = totals([r for r in rows if r.get("price_source") == source and r["strategy"] in CANDIDATES])
            st.write({"Price source": source.replace("_", " "), **{k: stats[k] for k in ("Record", "Units", "Pending")}})
        st.caption("Game prices supplied by the automated builder are assumed −110. Recorded prop prices and manual entries are labeled separately. These results do not establish actual wager fills.")
        calibration = [{"Model": LABELS[s], **{k: totals(grouped[s])[k] for k in ("Record", "Predicted win %", "Win %", "Brier score")}} for s in ("direction_spread", "direction_total")]
        st.dataframe(pd.DataFrame(calibration), hide_index=True, use_container_width=True)
        st.caption("Research weight changes have no fabricated win probabilities. Model confidence is evaluated only where the original model supplied a probability.")
    with st.expander("Frozen rules and capture details"):
        st.write("Reweighted totals: scoring variation ×1.5, success-rate variation ×0.5, weather ×1.5, injury ×2, plus two points; minimum 1.75-point gap. League centering matches the research definition.")
        st.write("Injury-aligned totals: minimum half-point model gap and injury effect in the same direction. RB receiving: matchup confidence ≥80 and projection gap ≥10%, with both side prices present.")
        st.caption("Pregame forecasts cannot be replaced by later inputs. An unconfirmed zero-yard participation result remains pending for review.")
        if rows:
            strategy = st.selectbox("Strategy", list(LABELS), format_func=lambda x: LABELS[x], key="nfl_research_strategy")
            details = [{k: r.get(k) for k in ("date", "game", "player", "direction", "line", "odds", "projection", "selected", "reason", "result", "units", "captured_at", "price_source")} for r in grouped[strategy]]
            st.dataframe(pd.DataFrame(details), hide_index=True, use_container_width=True)
            st.download_button("Download frozen records", json.dumps(rows, indent=2), "nfl-forward-records.json", "application/json")
