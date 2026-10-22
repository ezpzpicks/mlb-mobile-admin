"""Manual NBA research builder. No collection, scheduling or public pick writes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from builders.nba_example import example_package
from builders.nba_model import (
    InputError, MODEL_VERSION, STATUSES, evaluate_package, snapshot_json,
)

STATE_KEY = "_nba_research"
SNAPSHOT_TAB = "nba_research_snapshots"
SNAPSHOT_COLUMNS = ["Date", "Game ID", "Game", "Snapshot ID", "Model Version",
                    "As Of UTC", "Scenario Count", "Research Only", "Payload JSON"]
HISTORICAL_RATINGS = Path(__file__).resolve().parents[1] / "research/nba_results_2025_26/nba_ratings.json"


def _import_json(raw: bytes, state: dict):
    if len(raw) > 10_000_000:
        raise InputError("NBA JSON uploads are limited to 10 MB.")
    package = json.loads(raw)
    if isinstance(package, dict) and package.get("schema") == "nba-ratings-v1":
        from builders.nba_ratings import validate_bundle
        validate_bundle(package)
        state.update(ratings=package, package=None, result=None,
                     ratings_revision=state.get("ratings_revision", 0) + 1)
    else:
        from builders.nba_model import from_package
        from_package(package)
        state.update(package=package, result=None, revision=state.get("revision", 0) + 1)
        state.pop("ratings", None)


def _matchup_from_ratings(state: dict):
    bundle = state.get("ratings")
    if not bundle:
        return
    st.warning(f"Historical ratings through {bundle['data_through']}. Verify current rosters, injury reports, starting lineups and minutes before reviewing a matchup.")
    st.caption(f"{bundle['training_games']} team games · {bundle.get('player_training_games', bundle['training_games'])} checked player games · rating cutoff {bundle['as_of']}")
    evaluation = bundle.get("research_evaluation") or {}
    if evaluation.get("lineup_beats_team_margin_rmse") is False:
        st.warning(f"The lineup forecast did not beat the team-only control on {evaluation['holdout_games']} held-out games. These ratings are for research.")
    teams = {t["team_id"]: t["name"] for t in bundle["teams"]}
    options = sorted(teams, key=teams.get)
    revision = state.get("ratings_revision", 0)
    with st.form(f"nba_matchup_from_ratings_{revision}"):
        away = st.selectbox("Away team", options, format_func=teams.get, key=f"nba_ratings_away_{revision}")
        home = st.selectbox("Home team", options, index=1, format_func=teams.get, key=f"nba_ratings_home_{revision}")
        game = st.text_input("NBA game ID", value="", key=f"nba_ratings_game_{revision}")
        cutoff = st.text_input("Projection cutoff (ISO timestamp with timezone)",
            value=datetime.now(timezone.utc).isoformat(timespec="seconds"), key=f"nba_ratings_cutoff_{revision}")
        tipoff = st.text_input("Scheduled tipoff (ISO timestamp with timezone)", value="", key=f"nba_ratings_tipoff_{revision}")
        create = st.form_submit_button("Create matchup from ratings", use_container_width=True)
    if create:
        try:
            from builders.nba_ratings import package_from_bundle
            package = package_from_bundle(bundle, away, home, game_id=game, as_of=cutoff, tipoff=tipoff)
            from builders.nba_model import from_package
            from_package(package)
            state.update(package=package, result=None, revision=state.get("revision", 0) + 1)
        except (InputError, ValueError) as exc:
            st.error(str(exc))


def _roster_addition(state: dict):
    if not state.get("package"):
        return
    with st.expander("Add a player to the matchup"):
        st.caption("A traded player can reuse a dated rating. A newcomer starts with an explicit zero prior; assign his minutes in each scenario.")
        with st.form(f"nba_add_roster_player_{state.get('revision', 0)}"):
            side = st.selectbox("Add to roster", ["away", "home"],
                format_func=lambda s: state["package"][s]["name"])
            player_id = st.text_input("Player NBA ID", value="")
            name = st.text_input("Player name (required for a newcomer)", value="")
            add = st.form_submit_button("Add roster player")
        if add:
            try:
                from builders.nba_ratings import add_roster_player
                package = add_roster_player(state["package"], side, player_id, name, bundle=state.get("ratings"))
                state.update(package=package, result=None, revision=state.get("revision", 0) + 1)
                st.success("Player added with zero minutes. Assign his minutes and review each rotation.")
            except (InputError, ValueError) as exc:
                st.error(str(exc))


def _save_snapshot(package: dict, result: dict) -> bool:
    if package.get("is_demo"):
        return False
    # Storage imports and database I/O happen only on an explicit Save click.
    from shared.storage import append_row, sport_storage

    day = datetime.fromisoformat(result["tipoff"]).astimezone(ZoneInfo("America/New_York")).date()
    with sport_storage("NBA"):
        return append_row(SNAPSHOT_TAB, {
            "Date": str(day), "Game ID": result["game_id"],
            "Game": f"{result['away_team']} at {result['home_team']}",
            "Snapshot ID": result["snapshot_id"], "Model Version": MODEL_VERSION,
            "As Of UTC": result["as_of"], "Scenario Count": len(result["scenarios"]),
            "Research Only": "True", "Payload JSON": snapshot_json(package, result),
        }, SNAPSHOT_COLUMNS)


def _rotation_editor(plan: dict, profiles: list[dict], key: str) -> dict:
    names = {p["player_id"]: p["name"] for p in profiles}
    rows = [{"player_id": p["player_id"], "Player": names.get(p["player_id"], p["player_id"]),
             "minutes": p["minutes"], "status": p["status"],
             "starter": p.get("starter", False),
             "minutes_limit": p.get("minutes_limit", 48.0), "assumption": p.get("assumption", "")}
            for p in plan["players"]]
    edited = st.data_editor(pd.DataFrame(rows), key=key, hide_index=True,
        use_container_width=True, disabled=["player_id", "Player"],
        column_config={
            "minutes": st.column_config.NumberColumn("Projected minutes", min_value=0, max_value=48, step=1.0),
            "minutes_limit": st.column_config.NumberColumn("Minutes cap", min_value=0, max_value=48, step=1.0),
            "status": st.column_config.SelectboxColumn("Reported status", options=sorted(STATUSES), required=True),
            "assumption": st.column_config.SelectboxColumn("Scenario", options=["", "IN", "OUT"]),
            "starter": st.column_config.CheckboxColumn("Starting five"),
        })
    result = deepcopy(plan)
    result["players"] = [{k: row[k] for k in ("player_id", "minutes", "status", "minutes_limit", "assumption", "starter")}
                         for row in edited.to_dict("records")]
    st.caption(f"Projected team minutes: {pd.to_numeric(edited['minutes'], errors='coerce').sum():g} / 240")
    return result


def _show_projection(package: dict, result: dict):
    st.subheader("Rotation scenarios")
    st.caption(f"Projection cutoff: {result['as_of']} · {MODEL_VERSION}")
    if package.get("is_demo"):
        st.warning("Fictional example only. These are not real teams, players, ratings or betting picks.")
    st.dataframe(pd.DataFrame([{
        "Scenario": p["scenario"], "Away score": round(p["away_score"], 1),
        "Home score": round(p["home_score"], 1), "Fair home spread": round(p["fair_home_spread"], 1),
        "Total": round(p["total"], 1), "Spread edge (home)": None if p["home_spread_edge"] is None else round(p["home_spread_edge"], 1),
        "Total edge (over)": None if p["total_edge"] is None else round(p["total_edge"], 1),
        "Lineup margin change": round(p["lineup_margin_change"], 1),
        "Lineup status": "Ready for review" if p["lineup_ready"] else "Conditional",
    } for p in result["scenarios"]]), hide_index=True, use_container_width=True)
    st.write(f"Spread direction across the scenarios entered: **{result['spread_direction']}**")
    st.write(f"Total direction across the scenarios entered: **{result['total_direction']}**")
    if not result["scenario_coverage_complete"]:
        st.warning(f"Only {result['scenario_combinations_entered']} of {result['scenario_combinations_required']} unresolved participation combinations were entered. The displayed range does not cover every possible lineup.")
    st.caption("Positive spread edge favors home; negative favors away. Positive total edge favors over; negative favors under. Scenario ranges do not assign participation probabilities.")
    for projection in result["scenarios"]:
        with st.expander(f"{projection['scenario']} — lineup effects and outstanding news"):
            for flag in projection["flags"]:
                st.warning(flag)
            rows = []
            for side in ("away_rotation", "home_rotation"):
                for row in projection[side]["changes"]:
                    if row["minutes_change"]:
                        rows.append({"Team": projection[side]["team"], "Player": row["player"],
                                     "Baseline minutes": row["reference_minutes"], "Projected minutes": row["minutes"],
                                     "Offense change / 100": round(row["offense_change_per_100"], 2),
                                     "Defense improvement / 100": round(row["defense_improvement_per_100"], 2)})
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
            else:
                st.info("This rotation matches the baseline minutes.")
    encoded = snapshot_json(package, result)
    st.download_button("Download projection and input snapshot", encoded,
                       file_name=f"nba-{result['game_id']}-{result['snapshot_id'][:10]}.json", mime="application/json")
    state = st.session_state[STATE_KEY]
    saved = result["snapshot_id"] in state.get("saved_ids", [])
    if st.button("Save research snapshot", key="nba_save", disabled=bool(package.get("is_demo")) or saved):
        if _save_snapshot(package, result):
            state.setdefault("saved_ids", []).append(result["snapshot_id"])
            st.success("NBA research snapshot saved.")
    if saved:
        st.caption("This snapshot has already been saved in this session.")


def render():
    st.subheader("NBA lineup model")
    st.caption("Spreads and totals from team efficiency and the expected full-game rotation.")
    st.info("Research model: probabilities and A/B grades will follow historical calibration. Every projection is excluded from automatic posting.")
    with st.expander("Inputs and sources"):
        st.write("Load the checked historical ratings to choose two teams, or import a ratings bundle or complete matchup JSON. Historical rotation seeds require current roster and injury review. The fictional example download defines the matchup fields.")
        st.write("Player impacts are points per 100 possessions: positive offense creates points; positive defense prevents points. Baseline minutes must describe the same sample as the team ratings. Use regularized estimates with a documented source.")
        st.write("Copy current reported statuses from the NBA injury report and verify the starting lineup separately. For an unresolved player, keep the reported status and set the scenario to IN or OUT; assign all replacement minutes explicitly.")
        st.markdown("[NBA injury reports](https://official.nba.com/nba-injury-report-2026-27-season/) · [NBA projected daily lineups](https://www.nba.com/players/todays-lineups)")
        st.download_button("Download fictional input example", json.dumps(example_package(), indent=2),
                           file_name="nba-fictional-input-example.json", mime="application/json")
    uploaded = st.file_uploader("Import NBA ratings bundle or matchup JSON", type=["json"], key="nba_upload")
    state = st.session_state.setdefault(STATE_KEY, {"saved_ids": []})
    if uploaded is not None:
        raw = uploaded.getvalue()
        if raw != state.get("uploaded_bytes"):
            try:
                _import_json(raw, state)
                state["uploaded_bytes"] = raw
            except (InputError, ValueError) as exc:
                st.error(str(exc))
    if HISTORICAL_RATINGS.is_file() and st.button("Load checked 2025–26 ratings", key="nba_load_ratings"):
        try:
            _import_json(HISTORICAL_RATINGS.read_bytes(), state)
        except (InputError, ValueError) as exc:
            st.error(str(exc))
    _matchup_from_ratings(state)
    if st.button("Load fictional demo", key="nba_demo"):
        state.update(package=example_package(), result=None, revision=state.get("revision", 0) + 1)
        state.pop("ratings", None)
    if not state.get("package"):
        st.info("Import your data or load the fictional demo to compare a player's IN and OUT rotations.")
        return

    _roster_addition(state)

    package = deepcopy(state["package"])
    revision = state.get("revision", 0)
    with st.form(f"nba_projection_form_{revision}"):
        context = package["context"]
        st.markdown(f"**{package['away']['name']} at {package['home']['name']}**")
        provenance = package.get("rating_provenance")
        if isinstance(provenance, dict):
            st.caption(f"Historical rating sample through {provenance.get('data_through', 'an unspecified date')}; update the roster and rotation for this game.")
            evidence = provenance.get("research_evaluation") or {}
            if isinstance(evidence, dict) and evidence.get("lineup_beats_team_margin_rmse") is False and all(
                isinstance(evidence.get(k), (int, float)) for k in ("lineup_margin_rmse", "baseline_margin_rmse", "holdout_games")):
                st.warning(f"Historical check: lineup margin RMSE {evidence['lineup_margin_rmse']:.2f} vs team-only {evidence['baseline_margin_rmse']:.2f} on {evidence['holdout_games']} games. Lineup improvement has not been demonstrated.")
        context["as_of"] = st.text_input("Projection cutoff (ISO timestamp with timezone)", value=context["as_of"])
        context["tipoff"] = st.text_input("Tipoff (ISO timestamp with timezone)", value=context["tipoff"])
        for key, label in (("league_efficiency", "League points per 100 possessions"),
                           ("league_pace", "League possessions per 48 minutes"),
                           ("home_court_points", "Home-court margin adjustment"),
                           ("max_lineup_age_minutes", "Maximum injury/lineup report age in minutes")):
            context[key] = st.number_input(label, value=float(context[key]))
        with st.expander("Team ratings and reference rotations"):
            for side in ("away", "home"):
                team = package[side]
                st.markdown(f"**{team['name']}**")
                for key, label in (("offensive_rating", "Offensive rating"), ("defensive_rating", "Defensive rating"), ("pace", "Pace")):
                    team[key] = st.number_input(f"{team['name']} {label}", value=float(team[key]), key=f"nba_{revision}_{side}_{key}")
                for key, label in (("ratings_as_of", "Ratings cutoff"), ("reference_window", "Reference sample"),
                                   ("ratings_source", "Ratings source"), ("impact_source", "Player impact source"),
                                   ("impacts_as_of", "Player impact cutoff")):
                    team[key] = st.text_input(f"{team['name']} {label}", value=team[key], key=f"nba_{revision}_{side}_{key}")
                player_frame = st.data_editor(pd.DataFrame(team["players"]), hide_index=True, use_container_width=True,
                                             disabled=["player_id", "name"], key=f"nba_profiles_{revision}_{side}")
                team["players"] = player_frame.to_dict("records")
        market = package.get("market") or {"home_spread": None, "total": None, "as_of": context["as_of"], "source": "ScoresAndOdds (manual entry)"}
        home_line = st.text_input("Market home spread (blank if unavailable)", value="" if market["home_spread"] is None else str(market["home_spread"]))
        total_line = st.text_input("Market total (blank if unavailable)", value="" if market["total"] is None else str(market["total"]))
        market["as_of"] = st.text_input("Market line timestamp", value=market["as_of"])
        market["source"] = st.text_input("Market line source", value=market["source"])
        st.caption("Spreads and totals use default -110 pricing. No moneyline or player props in this version.")
        for index, scenario in enumerate(package["scenarios"]):
            st.markdown(f"**Scenario {index + 1}**")
            scenario["name"] = st.text_input("Scenario name", value=scenario["name"], key=f"nba_name_{revision}_{index}")
            for side in ("away", "home"):
                plan = scenario[side]
                team_name = package[side]["name"]
                st.markdown(f"**{team_name} rotation**")
                prefix = f"nba_plan_{revision}_{index}_{side}"
                for key, label in (("injury_as_of", "Injury report timestamp"), ("lineup_as_of", "Lineup timestamp"),
                                   ("injury_source", "Injury source"), ("lineup_source", "Lineup source")):
                    plan[key] = st.text_input(label, value=plan[key], key=f"{prefix}_{key}")
                plan["report_complete"] = st.checkbox("Team injury report complete", value=plan["report_complete"], key=f"{prefix}_complete")
                plan["starters_confirmed"] = st.checkbox("Starting five confirmed", value=plan["starters_confirmed"], key=f"{prefix}_confirmed")
                scenario[side] = _rotation_editor(plan, package[side]["players"], f"{prefix}_players")
        submitted = st.form_submit_button("Compare rotations", type="primary", use_container_width=True)
    if submitted:
        try:
            market["home_spread"] = None if not home_line.strip() else float(home_line)
            market["total"] = None if not total_line.strip() else float(total_line)
            package["market"] = market
            result = evaluate_package(package)
            state.update(package=package, result=result, result_package=deepcopy(package))
        except (InputError, ValueError) as exc:
            state["result"] = None
            st.error(str(exc))
    if state.get("result"):
        _show_projection(state["result_package"], state["result"])
