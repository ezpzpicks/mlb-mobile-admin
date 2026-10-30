"""Frozen, prospective NFL experiments. Never changes official model grades."""
from __future__ import annotations

import hashlib
import json
import math

POLICY = "nfl-forward-2026-10-30-v1"
START_DATE = "2026-10-30"
PARAMETERS = {
    "start_date": START_DATE,
    "capture": "first successful pregame save; no early/late rewrite or backfill",
    "total_weights": {"scoring_delta": .5, "success_delta": -.5, "weather_delta": .5,
        "injury_delta": 1., "offset": 2., "minimum_gap": 1.75,
        "scoring_coefficient": .8206113159895836, "success_coefficient": 22.97227990680213,
        "weather_coefficient": 1.2968981090090854},
    "total_injury": {"minimum_gap": .5, "requires_directional_agreement": True},
    "rb_receiving": {"minimum_matchup_confidence": 80., "minimum_relative_gap": .1,
        "direction": "projection minus line", "requires_both_prices": True},
    "review": {"minimum_distinct_selected_games": 50, "minimum_weeks": 4,
        "automatic_promotion": False, "compare_same_game_benchmarks": True},
}
PARAMETER_JSON = json.dumps(PARAMETERS, sort_keys=True, separators=(",", ":"))
PARAMETER_HASH = hashlib.sha256(PARAMETER_JSON.encode()).hexdigest()
LABELS = {
    "totals_weights_v1": "Reweighted totals",
    "totals_injury_v1": "Injury-aligned totals",
    "rb_receiving_v1": "RB receiving candidate",
    "current_spread": "Current model spreads",
    "current_total": "Current model totals",
    "direction_spread": "All model spread directions",
    "direction_total": "All model total directions",
    "always_home": "Home benchmark",
    "always_away": "Away benchmark",
    "always_over": "Over benchmark",
    "always_under": "Under benchmark",
    "rb_current": "Current RB receiving grades",
    "rb_over": "RB receiving Over benchmark",
    "rb_under": "RB receiving Under benchmark",
}


def number(value):
    try:
        x = float(str(value).strip())
        return x if math.isfinite(x) else None
    except (ValueError, TypeError):
        return None


def price_profit(value):
    odds = number(value)
    if odds is None or abs(odds) < 100:
        return None
    return 100 / abs(odds) if odds < 0 else odds / 100


def league_center(ratings):
    """Use the same full league baseline as the frozen research formula."""
    if hasattr(ratings, "to_dict"):
        ratings = ratings.to_dict("records")
    unique = {str(r.get("Team", "")): r for r in ratings or [] if r.get("Team")}
    values = [(number(r.get("Points/Game")), number(r.get("Off Success Rate"))) for r in unique.values()]
    if len(values) != 32 or any(p is None or s is None for p, s in values):
        return None
    return {"points": sum(p for p, _ in values) / 32,
        "success": sum(s for _, s in values) / 32, "teams": 32}


def reweighted_total(slate, away, home, center):
    if not center:
        return None
    def n(row, field):
        return number(row.get(field))
    needed = [n(slate, "Projected Total"), n(slate, "Weather Adjustment"),
        *[n(slate, x) for x in ("Away Defensive Absence", "Home Defensive Absence", "Away Offensive Absence", "Home Offensive Absence")],
        *[n(r, x) for r in (away, home) for x in ("Points/Game", "Points Allowed/Game", "Off Success Rate", "Def Success Edge")]]
    if any(x is None for x in needed):
        return None
    total, weather, ad, hd, ao, ho, ap, aa, ast, ade, hp, ha, hst, hde = needed
    p = PARAMETERS["total_weights"]
    scoring = p["scoring_coefficient"] * (.5 * (ap + ha + hp + aa) - 2 * center["points"])
    success = p["success_coefficient"] * (ast - hde + hst - ade - 2 * center["success"])
    weather_component = 2 * p["weather_coefficient"] * weather
    return total + p["scoring_delta"] * scoring + p["success_delta"] * success + p["weather_delta"] * weather_component + p["injury_delta"] * (ad + hd - ao - ho) + p["offset"]


def _entry(slate, strategy, market, direction, line, odds, *, selected=True, projection=None,
           player="", team="", probability=None, reason="", price_source="assumed_-110"):
    valid = line is not None and direction in {"Home", "Away", "Over", "Under"} and price_profit(odds) is not None
    return {"strategy": strategy, "label": LABELS[strategy], "market": market,
        "player": player, "team": team, "direction": direction, "line": line, "odds": number(odds),
        "selected": bool(selected and valid), "projection": projection, "probability": probability,
        "reason": reason or ("" if valid else "Missing line, direction or price"), "price_source": price_source,
        "model_version": str(slate.get("Model Version", ""))}


def predictions(slate, props, context, *, price_source="assumed_-110"):
    """Returns selected and skipped decisions, never a fabricated probability."""
    rows = []
    total = number(slate.get("Projected Total")); margin = number(slate.get("Projected Margin"))
    total_line = number(slate.get("Market Total")); spread_line = number(slate.get("Market Home Spread"))
    for market in ("Spread", "Total"):
        if market == "Spread":
            positive = str(slate.get("Spread Pick", "")).split(" ")[0] == str(slate.get("Home Team", ""))
            direction = "Home" if positive else "Away"
            line = spread_line if positive or spread_line is None else -spread_line
            odds = number(slate.get("Home Spread Odds" if positive else "Away Spread Odds"))
            grade_ok = slate.get("Spread Grade") in {"A Spread", "B Spread"}; projection = margin
        else:
            positive = str(slate.get("Total Pick", "")).lower().startswith("over")
            direction = "Over" if positive else "Under"; line = total_line
            odds = number(slate.get("Total Over Odds" if positive else "Total Under Odds"))
            grade_ok = slate.get("Total Grade") in {"Over", "Under", "Strong Over", "Strong Under"}; projection = total
        kwargs = dict(projection=projection, probability=number(slate.get(market + " Probability")), price_source=price_source)
        rows.append(_entry(slate, "current_" + market.lower(), market, direction, line, odds, selected=grade_ok,
            reason="" if grade_ok else "No qualifying official grade", **kwargs))
        rows.append(_entry(slate, "direction_" + market.lower(), market, direction, line, odds, selected=projection is not None, **kwargs))
    for strategy, market, direction, line, price_key in [
        ("always_home", "Spread", "Home", spread_line, "Home Spread Odds"),
        ("always_away", "Spread", "Away", -spread_line if spread_line is not None else None, "Away Spread Odds"),
        ("always_over", "Total", "Over", total_line, "Total Over Odds"),
        ("always_under", "Total", "Under", total_line, "Total Under Odds"),
    ]:
        rows.append(_entry(slate, strategy, market, direction, line, number(slate.get(price_key)), price_source=price_source))
    adjusted = reweighted_total(slate, context.get("away_rating", {}), context.get("home_rating", {}), context.get("league_center"))
    gap = adjusted - total_line if adjusted is not None and total_line is not None else None
    over = gap is not None and gap > 0
    rows.append(_entry(slate, "totals_weights_v1", "Total", "Over" if over else "Under", total_line,
        slate.get("Total Over Odds" if over else "Total Under Odds"), selected=gap is not None and abs(gap) >= PARAMETERS["total_weights"]["minimum_gap"],
        projection=adjusted, reason="Missing frozen rating inputs" if adjusted is None else "" if gap is not None and abs(gap) >= 1.75 else "Below 1.75 point gap", price_source=price_source))
    injury = [number(slate.get(k)) for k in ("Away Defensive Absence", "Home Defensive Absence", "Away Offensive Absence", "Home Offensive Absence")]
    effect = injury[0] + injury[1] - injury[2] - injury[3] if all(x is not None for x in injury) else None
    gap = total - total_line if total is not None and total_line is not None else None
    agrees = gap is not None and effect is not None and abs(gap) >= .5 and gap * effect > 0
    over = gap is not None and gap > 0
    rows.append(_entry(slate, "totals_injury_v1", "Total", "Over" if over else "Under", total_line,
        slate.get("Total Over Odds" if over else "Total Under Odds"), selected=agrees, projection=total,
        reason="" if agrees else "Injury direction or minimum gap not met", price_source=price_source))
    for prop in props or []:
        if prop.get("Position") != "RB" or prop.get("Market") != "Receiving Yards":
            continue
        line = number(prop.get("Market Line")); proj = number(prop.get("Projection")); confidence = number(prop.get("Matchup Confidence"))
        priced = line is not None and line > 0 and line <= 200 and proj is not None
        model_over = str(prop.get("Pick", "")).lower().startswith("over")
        over = priced and proj > line
        gap = abs(proj - line) / line if priced else None
        both_prices = all(price_profit(prop.get(k)) is not None for k in ("Over Odds", "Under Odds"))
        eligible = priced and both_prices and confidence is not None and confidence >= 80 and gap >= .1
        held = "hold" in str(prop.get("Grade", "")).lower() or "hold" in str(prop.get("Pick", "")).lower()
        kwargs = dict(player=str(prop.get("Player", "")), team=str(prop.get("Team", "")), projection=proj,
            price_source="recorded_prop_price")
        tracked = context.get("tracked_prop_keys")
        original_tracked = ([str(prop.get(k, "")) for k in ("Team", "Player", "Market", "Pick")] in tracked) if tracked is not None else prop.get("Grade") in {"Strong", "Regular", "Lean", "A Prop", "B Prop"}
        for strategy, positive, selected in [("rb_receiving_v1", over, eligible and not held),
                ("rb_over", True, priced and not held), ("rb_under", False, priced and not held),
                ("rb_current", model_over, priced and not held and original_tracked)]:
            rows.append(_entry(slate, strategy, "Receiving Yards", "Over" if positive else "Under", line,
                prop.get("Over Odds" if positive else "Under Odds"), selected=selected,
                reason="" if selected else "Availability, quote, confidence or gap requirement not met", **kwargs))
    unique = {}
    for row in rows:
        key = row["strategy"], row["market"], row.get("team", ""), row.get("player", "")
        if key in unique and unique[key] != row:
            unique[key].update(selected=False, reason="Conflicting duplicate forecasts")
        else:
            unique.setdefault(key, row)
    return list(unique.values())


def attach(result, league_ratings, *, price_source="assumed_-110"):
    provenance = result.get("provenance", {}); ratings = provenance.get("ratings", {})
    context = {"away_rating": ratings.get("Away", {}), "home_rating": ratings.get("Home", {}),
        "league_center": league_center(league_ratings), "price_source": price_source,
        "model_inputs_observed_at": provenance.get("weekly_input_observed_at"),
        "market_observed_at": provenance.get("game_market_observed_at")}
    if "prop_tracker" in result:
        context["tracked_prop_keys"] = [[str(p.get(k, "")) for k in ("Team", "Player", "Market", "Pick")] for p in result["prop_tracker"]]
    result["research"] = {"policy": POLICY, "parameter_hash": PARAMETER_HASH, "context": context,
        "predictions": predictions(result["slate"], result.get("props", []), context, price_source=price_source)}
    return result


def settle(entry, *, away=None, home=None, actual=None):
    line = number(entry.get("line")); odds = number(entry.get("odds")); payoff = price_profit(odds)
    if not entry.get("selected") or line is None or payoff is None:
        return None
    if entry["market"] == "Spread":
        if away is None or home is None:
            return None
        value = (home - away if entry["direction"] == "Home" else away - home) + line
    else:
        value = actual if entry["market"] == "Receiving Yards" else None if away is None or home is None else away + home
        if value is None:
            return None
        value = (value - line) * (1 if entry["direction"] == "Over" else -1)
    result = "P" if abs(value) < 1e-9 else "W" if value > 0 else "L"
    return {"result": result, "units": round(payoff if result == "W" else -1. if result == "L" else 0., 6)}
