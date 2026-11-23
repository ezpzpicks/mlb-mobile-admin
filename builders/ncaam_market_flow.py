"""Directional NCAAM market corrections from synchronized pregame snapshots.

Shares describe the provider's reported sample, not absolute dollar inflows.
This module has no database, network, grading-policy or deployment side effects.
"""
from datetime import datetime
import math
import statistics

POLICY = "ncaam-directional-market-flow-2026-11-22-v1"
FEATURE_NAMES = ("money_only_flow", "money_and_tickets_aligned",
                 "money_against_tickets", "same_window_line_confirmation")
DEFAULT_HALF_LIFE_MINUTES = 180.0
MINIMUM_HISTORY_MINUTES = 30.0

def stamp(value):
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Timezone-aware timestamps are required.")
    return value

def number(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("A reported finite number is required.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("A reported finite number is required.")
    return value

def sign(value):
    return 1 if value > 0 else -1 if value < 0 else 0

def flow_features(history, *, as_of, tipoff, market, half_life_minutes=DEFAULT_HALF_LIFE_MINUTES):
    """Home-positive for spreads, Over-positive for totals.

    Each adjacent pair supplies money, tickets and price changes from the SAME
    interval. An earlier price move cannot confirm a later money-share change.
    Features are clipped to the sign of the first-to-current money-share change;
    counterflow can reduce/cancel a correction but cannot reverse that approved
    overall direction. A static ending money-ticket gap is never a feature.
    """
    clock, start = stamp(as_of), stamp(tipoff)
    if not clock < start:
        raise ValueError("A market correction must precede tipoff.")
    if market not in ("Spread", "Total"):
        raise ValueError("Choose Spread or Total.")
    if number(half_life_minutes) <= 0:
        raise ValueError("A positive recency half-life is required.")
    points = {}
    for point in history:
        t = stamp(point["snapshotTime"])
        if t > clock or t >= start:
            continue
        item = {k: number(point[k]) for k in ("line", "betsPct", "moneyPct")}
        if not all(0 <= item[k] <= 100 for k in ("betsPct", "moneyPct")):
            raise ValueError("Shares must lie between zero and 100.")
        if t in points and points[t] != item:
            raise ValueError("Conflicting observations at the same timestamp.")
        points[t] = item
    timeline = sorted(points.items())
    basic = {"policy": POLICY, "feature_names": list(FEATURE_NAMES),
             "as_of": clock.isoformat(), "market": market,
             "features": [0.0] * len(FEATURE_NAMES), "direction": 0,
             "observations": len(timeline), "intervals": []}
    if len(timeline) < 2:
        return {**basic, "status": "insufficient_history"}
    duration = (timeline[-1][0] - timeline[0][0]).total_seconds() / 60
    basic.update(history_minutes=duration, first_observed_at=timeline[0][0].isoformat(),
                 last_observed_at=timeline[-1][0].isoformat())
    if duration < MINIMUM_HISTORY_MINUTES:
        return {**basic, "status": "insufficient_history"}
    first, last = timeline[0][1], timeline[-1][1]
    money_change = last["moneyPct"] - first["moneyPct"]
    direction = sign(money_change)
    basic.update(money_share_change=money_change,
                 ticket_share_change=last["betsPct"] - first["betsPct"],
                 direction=direction)
    if direction == 0:
        return {**basic, "status": "no_net_money_share_change"}
    accumulated = [0.0] * len(FEATURE_NAMES)
    intervals = []
    for (t0, before), (t1, after) in zip(timeline, timeline[1:]):
        money = (after["moneyPct"] - before["moneyPct"]) / 10.0
        tickets = (after["betsPct"] - before["betsPct"]) / 10.0
        price = after["line"] - before["line"]
        if market == "Spread":
            price = -price
        side = sign(money)
        age = (clock - t1).total_seconds() / 60.0
        recency = 2.0 ** (-age / half_life_minutes)
        row = {"start": t0.isoformat(), "end": t1.isoformat(),
               "money_share_change": money * 10, "ticket_share_change": tickets * 10,
               "signed_market_move": price, "recency_weight": recency}
        if side == 0:
            row.update(features=[0.0] * len(FEATURE_NAMES), price_confirms_money=False)
            intervals.append(row)
            continue
        amplitude = abs(money)
        denominator = amplitude + abs(tickets)
        quiet = amplitude / denominator
        aligned = max(side * tickets, 0.0) / denominator
        divergent = max(-side * tickets, 0.0) / denominator
        price_amount = math.log1p(abs(price))
        damping = 1.0 / (1.0 + price_amount) if side * price < 0 else 1.0
        core = recency * side * amplitude * damping
        vector = [core * quiet, core * aligned, core * divergent,
                  recency * side * amplitude * price_amount if side * price > 0 else 0.0]
        for i, value in enumerate(vector):
            accumulated[i] += value
        row.update(features=vector, price_confirms_money=side * price > 0,
                   opposing_price_damping=damping)
        intervals.append(row)
    features = [direction * max(0.0, direction * value) for value in accumulated]
    assert all(direction * x >= 0 for x in features)
    return {**basic, "status": "ready", "features": features,
            "unclipped_interval_features": accumulated, "intervals": intervals}

def adjust_component(baseline, flow, coefficients):
    """Nonnegative magnitudes preserve the user-approved movement directions."""
    baseline = number(baseline)
    weights = [number(x) for x in coefficients]
    if len(weights) != len(FEATURE_NAMES) or any(x < 0 for x in weights):
        raise ValueError("Four nonnegative fitted coefficients are required.")
    if flow.get("policy") != POLICY:
        raise ValueError("Mismatched market-flow policy.")
    features = [number(x) for x in flow["features"]]
    direction = int(flow["direction"])
    if len(features) != len(weights) or direction not in (-1, 0, 1):
        raise ValueError("Invalid signed flow features.")
    if (direction == 0 and any(features)) or any(direction * x < -1e-12 for x in features):
        raise ValueError("Features reverse the approved money-share direction.")
    correction = sum(w * x for w, x in zip(weights, features))
    if not math.isfinite(correction):
        raise ValueError("Nonfinite market correction.")
    return {"policy": POLICY, "baseline": baseline, "adjustment": correction,
            "projection": baseline + correction, "direction": direction,
            "coefficients": weights, "features": features, "flow_status": flow["status"]}

def combine_components(margin, total):
    margin, total = number(margin), number(total)
    home, away = (total + margin) / 2.0, (total - margin) / 2.0
    if min(home, away) <= 0:
        raise ValueError("Combined team scores must be positive.")
    return {"home": home, "away": away, "margin": margin, "total": total}

def fit_nonnegative_ridge(rows, *, alpha=100.0):
    """Four-variable nonnegative ridge; training rows only, no intercept/search."""
    if not rows or number(alpha) <= 0:
        raise ValueError("Training rows and a positive fixed penalty are required.")
    size = len(FEATURE_NAMES)
    scales = [statistics.pstdev([number(r["features"][j]) for r in rows])
              for j in range(size)]
    scales = [x if x >= 1e-9 else 1.0 for x in scales]
    design = [[number(v) / s for v, s in zip(r["features"], scales)] for r in rows]
    target = [number(r["actual"]) - number(r["baseline"]) for r in rows]
    h = [[sum(x[i] * x[j] for x in design) + (alpha if i == j else 0.0)
          for j in range(size)] for i in range(size)]
    c = [sum(x[j] * y for x, y in zip(design, target)) for j in range(size)]
    weights = [0.0] * size
    for iteration in range(10000):
        change = 0.0
        for i in range(size):
            fresh = max(0.0, (c[i] - sum(h[i][j] * weights[j] for j in range(size) if j != i)) / h[i][i])
            change = max(change, abs(fresh - weights[i]))
            weights[i] = fresh
        if change < 1e-11:
            break
    else:
        raise ValueError("Constrained ridge did not converge.")
    gradient = [sum(h[i][j] * weights[j] for j in range(size)) - c[i] for i in range(size)]
    assert all(abs(g) < 1e-6 if w > 1e-8 else g >= -1e-6 for w, g in zip(weights, gradient))
    raw = [w / s for w, s in zip(weights, scales)]
    return {"policy": POLICY, "alpha": alpha, "coefficients": raw,
            "standardized_coefficients": weights, "feature_scales": scales,
            "training_games": len(rows), "iterations": iteration + 1,
            "feature_names": list(FEATURE_NAMES), "intercept": 0.0}


def project_with_market_history(*, baseline_margin, baseline_total, spread_history,
                                total_history, coefficients_by_market, as_of, tipoff):
    """Construct coherent research team scores from the independent components.

    The baseline total already includes its existing overtime allowance; this
    function adds the market correction once and does not recalculate overtime.
    Research output carries no live-grade or official-pick eligibility.
    """
    flows = {market: flow_features(history, as_of=as_of, tipoff=tipoff, market=market)
             for market, history in (("Spread", spread_history), ("Total", total_history))}
    latest = {f["last_observed_at"] for f in flows.values() if "last_observed_at" in f}
    if len(latest) > 1:
        raise ValueError("Spread and total need the same captured market timestamp.")
    spread = adjust_component(baseline_margin, flows["Spread"], coefficients_by_market["Spread"])
    total = adjust_component(baseline_total, flows["Total"], coefficients_by_market["Total"])
    scores = combine_components(spread["projection"], total["projection"])
    return {"policy": POLICY, "status": "research_only", "official_pick_eligible": False,
            "baseline_margin": number(baseline_margin), "baseline_total": number(baseline_total),
            "market_models": {"spread": spread, "total": total}, "flows": flows, **scores}
