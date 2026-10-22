"""Manual live research workflow. Every network/database action is explicit."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from builders.nba_model import InputError, timestamp

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "research/nba_live_bootstrap.json"
ET = ZoneInfo("America/New_York")


def bootstrap_records():
    if not BOOTSTRAP.is_file():
        return []
    from builders.nba_tracking import unique_predictions
    return unique_predictions(json.loads(BOOTSTRAP.read_text())["predictions"])


def live_inputs(state, *, ui):
    st = ui
    from builders.nba_live import fetch_matchup_inputs, fetch_slate, prepare_live_package
    with st.expander("Live NBA games and current rotations"):
        st.caption("Load a scheduled game, current ESPN rosters, and up to 10 completed regular-season games per team. Suggested minutes need review before the first forecast is locked.")
        day = st.text_input("Slate date (ET, YYYY-MM-DD)", value=str(datetime.now(ET).date()), key="nba_live_slate_day")
        if st.button("Fetch NBA slate", key="nba_live_fetch_slate"):
            try:
                state["live_slate"] = fetch_slate(day)
                state["result"] = None
                state.pop("live_slate_error", None)
            except (InputError, ValueError) as exc:
                state["result"] = None
                state["live_slate_error"] = str(exc)
        if state.get("live_slate_error"):
            st.error(state["live_slate_error"])
        slate = state.get("live_slate")
        if slate:
            st.caption(f"Slate for {slate['date']} observed {slate['source']['observed_at']}")
            for excluded in slate["excluded"]:
                st.warning(f"Game {excluded['game_id']} excluded: {excluded['reason']}")
            choices = {g["game_id"]: g for g in slate["games"] if g["status"] == "STATUS_SCHEDULED"}
            if choices:
                gid = st.selectbox("Scheduled NBA game", list(choices),
                    format_func=lambda k: f"{choices[k]['away']['name']} at {choices[k]['home']['name']} · {timestamp(choices[k]['tipoff'], 'tipoff').astimezone(ET):%I:%M %p ET}")
                if st.button("Prepare live matchup with ESPN inputs", key="nba_prepare_live"):
                    try:
                        from builders.nba_injuries import fetch_espn_injuries
                        from builders.nba_ratings import validate_bundle
                        bundle = state.get("ratings") or json.loads((ROOT / "research/nba_results_2025_26/nba_ratings.json").read_text())
                        validate_bundle(bundle)
                        inputs = fetch_matchup_inputs(choices[gid])
                        injury = fetch_espn_injuries()
                        package = prepare_live_package(bundle, inputs, injury, as_of=datetime.now(timezone.utc))
                        state.update(package=package, result=None, ratings=bundle, espn_report=injury,
                            revision=state.get("revision", 0) + 1)
                        st.success("Current roster and minute suggestion loaded. Review both rotations, injury assumptions and any missing minute evidence, then compare.")
                    except (InputError, ValueError, KeyError, TypeError, OSError, IndexError) as exc:
                        state["result"] = None
                        st.error(f"Live matchup was not applied: {exc}")
            else:
                st.info("No scheduled regular-season games in this source response.")
        provenance = (state.get("package") or {}).get("live_provenance")
        if provenance:
            for conflict in provenance.get("conflicts", []):
                st.warning(conflict + ". Live locking is blocked until resolved.")
            st.caption("Recent playing time is a starting estimate. Prior-game starters are not confirmed for this game. Missing players and trade changes retain the dated reference rotation behind the team ratings.")
            for side in ("away", "home"):
                roster = provenance["rosters"][side]
                recent = provenance["recent"][side]
                st.markdown(f"**{roster['name']}** · {len(recent['games'])} checked recent games · roster observed {roster['source']['observed_at']}")
                st.dataframe(pd.DataFrame([{"Player": p["name"], "Minute weight": round(p["weight"], 1),
                    "Minute evidence": p["basis"], "Identity": p["identity_basis"]}
                    for p in provenance["minute_audit"][side]]), hide_index=True, use_container_width=True)
                for rejection in recent["rejected"]:
                    st.warning(f"Recent game {rejection['game_id']} not used: {rejection['reason']}")


def lock_controls(state, package, result, *, ui):
    st = ui
    if not package.get("live_provenance") or package.get("is_demo"):
        return
    from builders.nba_tracking import lock_prediction
    with st.expander("Lock first forecast for live tracking"):
        st.caption("Locks the last compared forecast and one chosen scenario. The first lock for each game and model version is permanent; later research snapshots do not replace it. Conditional forecasts retain all unresolved lineup flags.")
        with st.form("nba_live_lock_" + result["snapshot_id"]):
            selected = st.selectbox("Scenario to track", [p["scenario"] for p in result["scenarios"]])
            rosters = st.checkbox("I reviewed both current rosters", value=False)
            minutes = st.checkbox("I reviewed projected minutes and injury assumptions", value=False)
            note = st.text_input("Tracking review note and outstanding assumptions", value="")
            clicked = st.form_submit_button("Lock pregame research forecast")
        if clicked:
            try:
                from builders.nba_live import fetch_game
                from shared.nba_tracking_storage import save_prediction
                game, _ = fetch_game(result["game_id"])
                record = lock_prediction(package, selected, game, locked_at=datetime.now(timezone.utc),
                    roster_reviewed=rosters, minutes_reviewed=minutes, review_note=note)
                seeds = {r["record_id"]: r for r in bootstrap_records()}
                if record["record_id"] in seeds:
                    saved, inserted = seeds[record["record_id"]], False
                else:
                    saved, inserted = save_prediction(record)
                state["last_locked"] = saved
                state.pop("tracking_ledger", None)
                if inserted:
                    st.success(f"First forecast locked at {saved['locked_at']}. Load the live tracker to review it.")
                else:
                    st.info(f"This game/version already has a first forecast from {saved['locked_at']}. That forecast remains the tracked prediction.")
            except (InputError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                st.error(f"No new forecast locked: {exc}")


def tracker(state, *, ui):
    st = ui
    from builders.nba_tracking import export_ledger, result_observation, tracking_report
    with st.expander("NBA live model tracker"):
        st.caption("One first pregame prediction per game and model version. Compare lineup and team-only errors on the same games. Market records require a line captured before tipoff; no prices or profit are inferred.")
        if st.button("Load / refresh NBA tracker", key="nba_load_tracker"):
            try:
                from shared.nba_tracking_storage import load_ledger
                state["tracking_ledger"] = load_ledger(seed_records=bootstrap_records())
                state.pop("tracking_error", None)
            except Exception as exc:
                state.pop("tracking_ledger", None)
                state["tracking_error"] = str(exc)
        if state.get("tracking_error"):
            st.error(f"Tracker could not be loaded: {state['tracking_error']}")
        if "tracking_ledger" not in state:
            seeds = bootstrap_records()
            if seeds:
                st.info(f"{len(seeds)} opening research forecasts are included. Load the tracker to combine them with saved forecasts and results.")
            return
        records, observations = state["tracking_ledger"]
        results_day = st.text_input("Result date (ET, YYYY-MM-DD)", value=str(datetime.now(ET).date()), key="nba_results_day")
        if st.button("Fetch and save NBA results for date", key="nba_fetch_tracking_results"):
            try:
                from datetime import date
                from builders.nba_live import checked_box_minutes, fetch_game
                from shared.nba_tracking_storage import load_ledger, save_observation
                target = date.fromisoformat(results_day)
                game_ids = sorted({r["game_id"] for r in records if timestamp(r["tipoff"], "tipoff").astimezone(ET).date() == target})
                if not game_ids:
                    st.info("No locked games for this date.")
                for gid in game_ids:
                    try:
                        game, raw = fetch_game(gid)
                        boxes, box_error = None, ""
                        if game["completed"]:
                            try:
                                boxes = checked_box_minutes(raw, game)
                            except (InputError, KeyError, TypeError, ValueError, IndexError) as exc:
                                box_error = str(exc)
                                st.warning(f"Game {gid}: final score retained; actual minute check unavailable ({box_error}).")
                        save_observation(result_observation(game, boxes, box_error=box_error))
                    except Exception as exc:
                        st.error(f"Game {gid}: result was not saved ({exc}).")
                state["tracking_ledger"] = load_ledger(seed_records=bootstrap_records())
                records, observations = state["tracking_ledger"]
            except Exception as exc:
                st.error(f"Result refresh did not complete: {exc}")
        try:
            report = tracking_report(records, observations)
            if report["summary"]:
                st.dataframe(pd.DataFrame(report["summary"]), hide_index=True, use_container_width=True)
                st.dataframe(pd.DataFrame(report["games"]), hide_index=True, use_container_width=True)
            else:
                st.info("No pregame forecasts locked yet.")
            st.caption(report["policy"])
            st.download_button("Download NBA tracking ledger", export_ledger(records, observations),
                file_name="nba-prospective-tracking.json", mime="application/json")
        except (InputError, ValueError, KeyError, TypeError) as exc:
            st.error(f"Tracking report could not be calculated: {exc}")
