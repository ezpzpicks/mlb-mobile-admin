"""CFB v2.1 market-calibration and price-aware betting layer.

The validated CFB v2 regression remains the mean-margin engine. This layer:
- replaces the old spread simulation width with the 2024 out-of-sample residual SD;
- preserves the existing totals distribution exactly;
- prices both spread sides and total sides from entered American odds with no-vig probabilities;
- applies conservative A/B spread gates plus positive price-edge and EV vetoes;
- records actual selected prices in the slate/tracker;
- exposes the regression/overlay/market breakdown in the admin UI.

Historical ATS calibration did not establish a profitable/stable probability bucket,
so the spread probabilities are distribution probabilities, not claims of historical
cover-frequency calibration. No new roster/talent/portal residual coefficient is
applied because none passed 2024 confirmation.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st

MODEL_VERSION = "cfb-v2.1-calibrated-pricing-2026-08-21"
CALIBRATION_RESEARCH_VERSION = "cfb-v2-calibration-team-residual-2026-08-21"

# 2024 out-of-sample residual distribution from the 2021-23-trained CFB v2 model.
MARGIN_RESIDUAL_SD = 17.75939215594032
MARGIN_ROBUST_SIGMA = 16.871932143212987

# 2025 leakage-safe FBS-vs-FBS backtesting showed a material hit-rate/ROI lift
# when the point-edge gates were made substantially stricter. Probability,
# reliability, confluence, positive price-edge, and EV gates still apply.
# These cutoffs are intentionally selective and are not a guarantee of future ATS results.
SPREAD_B_PROBABILITY = 0.55
SPREAD_B_POINT_EDGE = 6.0
SPREAD_A_PROBABILITY = 0.58
SPREAD_A_POINT_EDGE = 9.5
ATS_CALIBRATION_PROVEN = False

# Historical roster/returning/portal candidates did not have usable 2024
# confirmation coverage; no new residual coefficients are promoted.
TEAM_RESIDUAL_FEATURES: tuple[str, ...] = ()
TEAM_RESIDUAL_STATUS = "No historical team-specific residual factor passed 2024 confirmation"

SLATE_EXTRA_COLUMNS = [
    "Home Spread Odds", "Away Spread Odds", "Total Over Odds", "Total Under Odds",
    "Spread Odds", "Spread Implied Probability", "Spread Price Edge", "Spread Expected Value",
    "Total Odds", "Total Implied Probability", "Total Price Edge", "Total Expected Value",
    "Margin Residual SD", "ATS Calibration Proven",
]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
        return value if math.isfinite(value) else float(default)
    except Exception:
        return float(default)


def _american_odds(value: Any, default: int = -110) -> int:
    odds = int(round(_num(value, default)))
    if odds == 0:
        return int(default)
    return odds


def _no_vig_pair(cfb_builder: Any, first_odds: int, second_odds: int) -> tuple[float, float]:
    first = float(cfb_builder.american_implied_probability(first_odds))
    second = float(cfb_builder.american_implied_probability(second_odds))
    total = max(first + second, 1e-9)
    return first / total, second / total


def _priced_spread_market(
    cfb_builder: Any,
    sim: dict[str, Any],
    home_spread: float,
    home: str,
    away: str,
    home_odds: int,
    away_odds: int,
) -> dict[str, Any]:
    home_prob, push = cfb_builder._prob_with_push(sim["margins"], home_spread, True)
    away_prob = max(0.0, min(1.0, 1.0 - home_prob))
    home_novig, away_novig = _no_vig_pair(cfb_builder, home_odds, away_odds)
    home_point_edge = float(np.mean(sim["margins"])) + float(home_spread)

    options = [
        {
            "pick": f"{home} {float(home_spread):+g}", "team": home,
            "probability": float(home_prob), "push": float(push),
            "model_edge_points": float(home_point_edge), "odds": int(home_odds),
            "implied": float(home_novig), "price_edge": float(home_prob - home_novig),
            "ev": float(cfb_builder.expected_value_per_unit(home_prob, home_odds)),
        },
        {
            "pick": f"{away} {-float(home_spread):+g}", "team": away,
            "probability": float(away_prob), "push": float(push),
            "model_edge_points": float(-home_point_edge), "odds": int(away_odds),
            "implied": float(away_novig), "price_edge": float(away_prob - away_novig),
            "ev": float(cfb_builder.expected_value_per_unit(away_prob, away_odds)),
        },
    ]
    # Price selects the side; strength gates below prevent a plus-money price from
    # manufacturing a graded play when the underlying model probability is weak.
    return max(options, key=lambda option: (option["ev"], option["price_edge"], option["probability"]))


def _priced_total_market(
    cfb_builder: Any,
    sim: dict[str, Any],
    total_line: float,
    over_odds: int,
    under_odds: int,
) -> dict[str, Any]:
    over_prob, push = cfb_builder._prob_with_push(sim["totals"], -float(total_line), True)
    under_prob = max(0.0, min(1.0, 1.0 - over_prob))
    over_novig, under_novig = _no_vig_pair(cfb_builder, over_odds, under_odds)
    model_total = float(np.mean(sim["totals"]))
    over_edge = model_total - float(total_line)

    options = [
        {
            "pick": f"Over {float(total_line):g}", "probability": float(over_prob),
            "push": float(push), "model_edge_points": float(over_edge), "odds": int(over_odds),
            "implied": float(over_novig), "price_edge": float(over_prob - over_novig),
            "ev": float(cfb_builder.expected_value_per_unit(over_prob, over_odds)),
        },
        {
            "pick": f"Under {float(total_line):g}", "probability": float(under_prob),
            "push": float(push), "model_edge_points": float(-over_edge), "odds": int(under_odds),
            "implied": float(under_novig), "price_edge": float(under_prob - under_novig),
            "ev": float(cfb_builder.expected_value_per_unit(under_prob, under_odds)),
        },
    ]
    return max(options, key=lambda option: (option["ev"], option["price_edge"], option["probability"]))


def _grade_spread(probability: float, point_edge: float, reliability: float, confluence: int) -> str:
    if probability >= SPREAD_A_PROBABILITY and point_edge >= SPREAD_A_POINT_EDGE and reliability >= 72 and confluence >= 4:
        return "A Spread"
    if probability >= SPREAD_B_PROBABILITY and point_edge >= SPREAD_B_POINT_EDGE and reliability >= 62 and confluence >= 3:
        return "B Spread"
    return "No Play"


def install_market_calibration(cfb_builder: Any) -> None:
    if getattr(cfb_builder, "_MARKET_CALIBRATION_LAYER_INSTALLED", False):
        return

    # The CFB v2 mean-margin layer must be installed first by app_mobile_admin.py.
    original_simulate = cfb_builder.simulate_game
    original_grade_total = cfb_builder._grade_total
    original_display_result = cfb_builder._display_result
    original_slate_row = cfb_builder.slate_row
    original_tracker_rows = cfb_builder.tracker_rows

    for column in SLATE_EXTRA_COLUMNS:
        if column not in cfb_builder.SLATE_COLUMNS:
            cfb_builder.SLATE_COLUMNS.append(column)

    def simulate_game(projection: dict[str, Any], seed: str, simulations: int = None) -> dict[str, Any]:
        simulations = int(simulations or cfb_builder.SIMULATIONS)
        # Preserve the existing CFB totals engine exactly. Only the margin draw is
        # replaced by the observed out-of-sample CFB v2 residual distribution.
        legacy = original_simulate(projection, seed, simulations=simulations)
        totals = np.asarray(legacy["totals"], dtype=float).copy()
        digest = hashlib.sha256(f"{seed}-{MODEL_VERSION}-margin".encode()).hexdigest()
        rng = np.random.default_rng(int(digest[:16], 16))
        margins = float(projection["margin"]) + MARGIN_RESIDUAL_SD * rng.standard_normal(len(totals))
        # A margin cannot exceed the total score in absolute value if both scores
        # are nonnegative. This only trims impossible far-tail combinations.
        margins = np.clip(margins, -totals, totals)
        home_scores = (totals + margins) / 2.0
        away_scores = (totals - margins) / 2.0
        legacy.update({
            "away_scores": away_scores,
            "home_scores": home_scores,
            "margins": margins,
            "totals": totals,
            "away_mean": float(np.mean(away_scores)),
            "home_mean": float(np.mean(home_scores)),
            "away_p10": float(np.percentile(away_scores, 10)),
            "away_p90": float(np.percentile(away_scores, 90)),
            "home_p10": float(np.percentile(home_scores, 10)),
            "home_p90": float(np.percentile(home_scores, 90)),
            "home_win": float(np.mean(margins > 0)),
            "away_win": float(np.mean(margins < 0)),
            "margin_residual_sd": float(MARGIN_RESIDUAL_SD),
            "margin_robust_sigma": float(MARGIN_ROBUST_SIGMA),
            "ats_calibration_proven": bool(ATS_CALIBRATION_PROVEN),
        })
        return legacy

    def evaluate_game(
        game: pd.Series,
        ratings: pd.DataFrame,
        away_personnel: Any,
        home_personnel: Any,
        environment: Any,
        market_home_spread: float,
        market_total: float,
        away_ml: float,
        home_ml: float,
        market_availability: dict[str, bool] | None = None,
        home_spread_odds: int = -110,
        away_spread_odds: int = -110,
        total_over_odds: int = -110,
        total_under_odds: int = -110,
        simulations: int | None = None,
    ) -> dict[str, Any]:
        market_availability = market_availability or {"spread": True, "total": True, "moneyline": True}
        game_values = game.to_dict() if isinstance(game, pd.Series) else dict(game)
        market_home_spread = float(cfb_builder._num(market_home_spread, 0.0))
        market_total = float(cfb_builder._num(market_total, 56.0))
        away_ml = float(cfb_builder._num(away_ml, 0.0))
        home_ml = float(cfb_builder._num(home_ml, 0.0))
        home_spread_odds = _american_odds(home_spread_odds)
        away_spread_odds = _american_odds(away_spread_odds)
        total_over_odds = _american_odds(total_over_odds)
        total_under_odds = _american_odds(total_under_odds)
        game_values.update({
            "Home Spread": market_home_spread,
            "Total": market_total,
            "Away ML": away_ml,
            "Home ML": home_ml,
            "Home Spread Odds": home_spread_odds,
            "Away Spread Odds": away_spread_odds,
            "Total Over Odds": total_over_odds,
            "Total Under Odds": total_under_odds,
        })
        game_obj = pd.Series(game_values, dtype=object)

        projection = cfb_builder.project_matchup(game_obj, ratings, away_personnel, home_personnel, environment)
        projection["team_specific_residual_delta"] = 0.0
        projection["team_specific_residual_status"] = TEAM_RESIDUAL_STATUS
        projection["market_calibration_research_version"] = CALIBRATION_RESEARCH_VERSION

        simulation = cfb_builder.simulate_game(
            projection,
            cfb_builder._text(game_obj.get("Game ID"), f"{game_obj['Away Team']}-{game_obj['Home Team']}"),
            simulations=int(simulations or cfb_builder.SIMULATIONS),
        )
        spread = _priced_spread_market(
            cfb_builder, simulation, market_home_spread,
            str(game_obj["Home Team"]), str(game_obj["Away Team"]),
            home_spread_odds, away_spread_odds,
        )
        total = _priced_total_market(cfb_builder, simulation, market_total, total_over_odds, total_under_odds)
        moneyline = cfb_builder._moneyline_market(
            simulation, game_obj["Home Team"], game_obj["Away Team"], home_ml, away_ml
        )
        reliability, reliability_parts, reliability_reasons = cfb_builder.reliability_score(
            game_obj, projection, simulation, away_personnel, home_personnel, environment
        )
        spread_conf, spread_support = cfb_builder._confluence(
            projection, spread["team"], game_obj["Home Team"], "spread"
        )
        total_direction = "over" if total["pick"].startswith("Over") else "under"
        total_conf, total_support = cfb_builder._confluence(
            projection, game_obj["Home Team"], game_obj["Home Team"], "total", total_direction
        )
        ml_conf, ml_support = cfb_builder._confluence(
            projection, moneyline["pick"], game_obj["Home Team"], "moneyline"
        )

        spread["grade"] = _grade_spread(
            spread["probability"], spread["model_edge_points"], reliability, spread_conf
        )
        total["grade"] = original_grade_total(
            total["probability"], total["model_edge_points"], reliability, total_conf
        )
        moneyline["grade"] = cfb_builder._grade_ml(
            moneyline["edge"], moneyline["ev"], reliability, ml_conf
        )

        # Price-aware veto: model strength cannot rescue a side whose actual price
        # has non-positive no-vig edge or non-positive expected value.
        if spread["price_edge"] <= 0 or spread["ev"] <= 0:
            spread["grade"] = "No Play"
        if total["price_edge"] <= 0 or total["ev"] <= 0:
            total["grade"] = "No Play"
        if not market_availability.get("spread", False):
            spread["grade"] = "No Play"
        if not market_availability.get("total", False):
            total["grade"] = "No Play"
        if not market_availability.get("moneyline", False):
            moneyline["grade"] = "No Play"

        if not ATS_CALIBRATION_PROVEN:
            spread_support = list(spread_support) + ["ATS thresholds are conservative fallbacks; historical calibration unproven"]
        spread.update({"confluence": spread_conf, "support": spread_support})
        total.update({"confluence": total_conf, "support": total_support})
        moneyline.update({"confluence": ml_conf, "support": ml_support})
        return {
            "projection": projection,
            "simulation": simulation,
            "spread": spread,
            "total_market": total,
            "moneyline": moneyline,
            "reliability": reliability,
            "reliability_parts": reliability_parts,
            "reliability_reasons": reliability_reasons,
            "environment": environment,
            "away_personnel": away_personnel,
            "home_personnel": home_personnel,
            "game": game_obj,
            "market_availability": market_availability,
        }

    def slate_row(result: dict[str, Any]) -> pd.DataFrame:
        frame = original_slate_row(result)
        if frame.empty:
            return frame.reindex(columns=cfb_builder.SLATE_COLUMNS)
        idx = frame.index[0]
        game = result["game"]
        spread = result["spread"]
        total = result["total_market"]
        values = {
            "Home Spread Odds": _american_odds(game.get("Home Spread Odds", -110)),
            "Away Spread Odds": _american_odds(game.get("Away Spread Odds", -110)),
            "Total Over Odds": _american_odds(game.get("Total Over Odds", -110)),
            "Total Under Odds": _american_odds(game.get("Total Under Odds", -110)),
            "Spread Odds": int(spread.get("odds", -110)),
            "Spread Implied Probability": round(_num(spread.get("implied"), 0.5), 4),
            "Spread Price Edge": round(_num(spread.get("price_edge")), 4),
            "Spread Expected Value": round(_num(spread.get("ev")), 4),
            "Total Odds": int(total.get("odds", -110)),
            "Total Implied Probability": round(_num(total.get("implied"), 0.5), 4),
            "Total Price Edge": round(_num(total.get("price_edge")), 4),
            "Total Expected Value": round(_num(total.get("ev")), 4),
            "Margin Residual SD": round(MARGIN_RESIDUAL_SD, 4),
            "ATS Calibration Proven": bool(ATS_CALIBRATION_PROVEN),
        }
        for key, value in values.items():
            frame.at[idx, key] = value
        return frame.reindex(columns=cfb_builder.SLATE_COLUMNS)

    def tracker_rows(result: dict[str, Any], include_no_plays: bool = False) -> pd.DataFrame:
        frame = original_tracker_rows(result, include_no_plays)
        if frame.empty:
            return frame
        for idx, row in frame.iterrows():
            bet_type = str(row.get("Bet Type", ""))
            market = result["spread"] if bet_type == "Spread" else result["total_market"] if bet_type == "Total" else None
            if market is None:
                continue
            frame.at[idx, "Odds/Line"] = int(market.get("odds", -110))
            frame.at[idx, "Implied Probability"] = round(_num(market.get("implied"), 0.5), 4)
            frame.at[idx, "Edge"] = round(_num(market.get("price_edge")), 4)
            frame.at[idx, "Expected Value"] = round(_num(market.get("ev")), 4)
        return frame.reindex(columns=cfb_builder.TRACKER_COLUMNS)

    def market_card(title: str, market: dict[str, Any], edge_label: str) -> None:
        grade = market["grade"]
        support = ", ".join(market.get("support", [])) or "No strong multi-factor agreement"
        pricing = ""
        if "odds" in market:
            pricing = (
                f"<div class='metric'>Price: {int(market['odds']):+d}</div>"
                f"<div class='metric'>No-vig implied: {100*_num(market.get('implied'), 0.5):.1f}%</div>"
                f"<div class='metric'>Price edge: {100*_num(market.get('price_edge', market.get('edge', 0.0))):+.1f}%</div>"
            )
        ev = f"<div class='metric'>EV: {_num(market.get('ev'))*100:+.1f}%</div>" if "ev" in market else ""
        st.markdown(
            f"<div class='market-card'><h4>{title}</h4><div class='pick'>{market['pick']}</div>"
            f"<div class='{cfb_builder._grade_class(grade)}'><b>{grade}</b></div>"
            f"<div class='metric'>Probability: {cfb_builder._percent(market['probability'])}</div>"
            f"<div class='metric'>{edge_label}</div>{pricing}{ev}"
            f"<div class='metric'>Confluence: {market['confluence']}/6</div>"
            f"<div class='why'>{support}</div></div>",
            unsafe_allow_html=True,
        )

    def display_result(result: dict[str, Any]) -> None:
        original_display_result(result)
        projection = result["projection"]
        sim = result["simulation"]
        spread = result["spread"]
        total = result["total_market"]
        breakdown = pd.DataFrame([
            {"Component": "Regression base margin", "Home-margin points": _num(projection.get("regression_base_margin"))},
            {"Component": "Team-specific residual model", "Home-margin points": _num(projection.get("team_specific_residual_delta"))},
            {"Component": "Live personnel / injuries", "Home-margin points": _num(projection.get("regression_personnel_delta"))},
            {"Component": "QB / coaching continuity", "Home-margin points": _num(projection.get("regression_continuity_delta"))},
            {"Component": "Venue / travel / rest deviation", "Home-margin points": _num(projection.get("regression_venue_delta"))},
            {"Component": "Final deterministic margin", "Home-margin points": _num(projection.get("margin"))},
        ])
        with st.expander("CFB v2.1 regression + market calibration breakdown", expanded=False):
            st.dataframe(breakdown, hide_index=True, use_container_width=True)
            st.markdown(
                f"**Calibrated margin residual SD:** {MARGIN_RESIDUAL_SD:.2f} points  \n"
                f"**Spread ATS calibration:** {'validated' if ATS_CALIBRATION_PROVEN else 'not historically validated; conservative grade gates retained'}"
            )
            st.caption(TEAM_RESIDUAL_STATUS)
            pricing = pd.DataFrame([
                {"Market": "Spread", "Selection": spread.get("pick"), "Odds": spread.get("odds"), "No-vig implied": spread.get("implied"), "Model probability": spread.get("probability"), "Price edge": spread.get("price_edge"), "EV": spread.get("ev")},
                {"Market": "Total", "Selection": total.get("pick"), "Odds": total.get("odds"), "No-vig implied": total.get("implied"), "Model probability": total.get("probability"), "Price edge": total.get("price_edge"), "EV": total.get("ev")},
            ])
            st.dataframe(pricing, hide_index=True, use_container_width=True)
            if "margin_residual_sd" in sim:
                st.caption(f"Simulation margin SD: {sim['margin_residual_sd']:.2f} • totals distribution retained from the existing CFB totals engine")

    cfb_builder.simulate_game = simulate_game
    cfb_builder.evaluate_game = evaluate_game
    cfb_builder.slate_row = slate_row
    cfb_builder.tracker_rows = tracker_rows
    cfb_builder._market_card = market_card
    cfb_builder._display_result = display_result
    cfb_builder.MODEL_VERSION = MODEL_VERSION
    cfb_builder._MARKET_CALIBRATION_LAYER_INSTALLED = True
