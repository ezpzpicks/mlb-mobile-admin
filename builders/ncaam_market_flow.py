"""Research projections updated from prices and reported betting shares."""
from copy import deepcopy
from datetime import datetime, timedelta
import math

POLICY = "ncaam-directional-market-flow-2026-11-22-v2"
FEATURE_NAMES = ("line_movement", "money_movement", "money_ticket_divergence",
                 "ticket_only_movement", "same_window_confirmation", "lagged_confirmation")
HALF_LIFE_MINUTES = 120.0
CONFIRMATION_LOOKBACK_MINUTES = 60.0
PROFILES = {
    "balanced": {"weights": [1., 2., 1., .5, .5, .25], "spread_cap": 4., "total_cap": 6., "minimum_adjustment": 1.},
    "strong": {"weights": [2., 4., 1., 1., 1., .5], "spread_cap": 5., "total_cap": 8., "minimum_adjustment": 1.},
    "divergence": {"weights": [1., 2., 2., .5, 1., .5], "spread_cap": 4., "total_cap": 6., "minimum_adjustment": 1.},
}

def stamp(value):
    value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Timezone-aware timestamps are required.")
    return value

def number(value):
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        raise ValueError("A finite reported number is required.")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("A finite reported number is required.")
    return value

def share(value):
    if value is None:
        return None
    value = number(value)
    if not 0 <= value <= 100:
        raise ValueError("Shares must be between zero and 100.")
    return value

def sign(value):
    return 1 if value > 0 else -1 if value < 0 else 0

def flow_features(history, *, as_of, tipoff, market, half_life_minutes=HALF_LIFE_MINUTES):
    """Home-oriented spread quotes; Over-oriented total quotes.

    Line evidence is independent of money movement. Money/ticket channels use
    recent adjacent windows, without an opening-to-current direction clamp.
    Missing shares remain unknown. Earlier price is not attributed to later money.
    """
    clock, start = stamp(as_of), stamp(tipoff)
    if not clock < start:
        raise ValueError("Market updates must precede tipoff.")
    if market not in ("Spread", "Total") or number(half_life_minutes) <= 0:
        raise ValueError("Unsupported market or recency half-life.")
    points = {}
    for raw in history:
        t = stamp(raw["snapshotTime"])
        if t > clock or t >= start:
            continue
        point = {"line": number(raw["line"]), "betsPct": share(raw.get("betsPct")),
                 "moneyPct": share(raw.get("moneyPct"))}
        if t in points and points[t] != point:
            raise ValueError("Conflicting observations at the same timestamp.")
        points[t] = point
    timeline = sorted(points.items())
    out = {"policy": POLICY, "market": market, "as_of": clock.isoformat(),
           "feature_names": list(FEATURE_NAMES), "features": [0.] * 6,
           "observations": len(timeline), "intervals": []}
    if timeline:
        out.update(first_observed_at=timeline[0][0].isoformat(),
                   last_observed_at=timeline[-1][0].isoformat())
    if len(timeline) < 2:
        return {**out, "status": "insufficient_history"}
    # A price-only or unchanged refresh must not erase existing split evidence
    # through clock decay. Advance the split clock only on an observed share
    # change; the next genuine split update then reweights the prior intervals.
    split_updates = [t1 for (_, before), (t1, after) in zip(timeline, timeline[1:])
                     if any(before[k] is not None and after[k] is not None and before[k] != after[k]
                            for k in ("moneyPct", "betsPct"))]
    anchor = max(split_updates, default=timeline[0][0])
    features, prior_money, intervals = [0.] * 6, [], []
    for (t0, before), (t1, after) in zip(timeline, timeline[1:]):
        price = (after["line"] - before["line"]) * (-1 if market == "Spread" else 1)
        money_known = before["moneyPct"] is not None and after["moneyPct"] is not None
        bets_known = before["betsPct"] is not None and after["betsPct"] is not None
        money = (after["moneyPct"] - before["moneyPct"]) / 10 if money_known else None
        bets = (after["betsPct"] - before["betsPct"]) / 10 if bets_known else None
        recency = .5 ** (max(0., (anchor - t1).total_seconds() / 60) / half_life_minutes)
        vector, lagged = [price, 0., 0., 0., 0., 0.], 0.
        damping = 1 / (1 + .25 * abs(price)) if money is not None and money * price < 0 else 1.
        if money is not None:
            vector[1] = recency * money * damping
            if bets is not None and money * bets < 0:
                vector[2] = recency * sign(money) * min(abs(money), abs(bets)) * damping
            if money == 0 and bets is not None:
                # Declared prior: more tickets without more money share weakens
                # relative dollar backing for that side.
                vector[3] = -recency * bets
            if money * price > 0:
                vector[4] = recency * sign(money) * min(abs(money), abs(price))
        if money == 0 and price != 0:
            cutoff = t1 - timedelta(minutes=CONFIRMATION_LOOKBACK_MINUTES)
            lagged = sum(m * .5 ** ((t1 - t).total_seconds() / 60 / half_life_minutes)
                         for t, m in prior_money if cutoff <= t <= t0)
            if lagged * price > 0:
                vector[5] = recency * sign(price) * min(abs(lagged), abs(price))
        for i, value in enumerate(vector):
            features[i] += value
        if money is not None and money != 0:
            prior_money.append((t1, money))
        intervals.append({"start": t0.isoformat(), "end": t1.isoformat(),
                          "money_share_change": None if money is None else money * 10,
                          "ticket_share_change": None if bets is None else bets * 10,
                          "signed_market_move": price, "recency_weight": recency,
                          "price_opposition_damping": damping,
                          "prior_money_in_lag_window": lagged, "features": vector})
    first, last = timeline[0][1], timeline[-1][1]
    out.update(features=features, intervals=intervals,
               history_minutes=(timeline[-1][0] - timeline[0][0]).total_seconds() / 60,
               split_information_as_of=anchor.isoformat(),
               net_money_share_change=None if first["moneyPct"] is None or last["moneyPct"] is None else last["moneyPct"] - first["moneyPct"],
               recent_money_share_flow=features[1] * 10,
               status="ready" if any(abs(x) > 1e-12 for x in features) else "observed_unchanged")
    assert all(math.isfinite(x) for x in features)
    return out

def adjustment(baseline, flow, profile="balanced"):
    if flow.get("policy") != POLICY or profile not in PROFILES:
        raise ValueError("Mismatched flow policy or sizing profile.")
    parameters = PROFILES[profile]
    features = [number(x) for x in flow["features"]]
    if len(features) != 6:
        raise ValueError("Incomplete movement features.")
    contributions = [v * w for v, w in zip(features, parameters["weights"])]
    raw = sum(contributions)
    cap = parameters["spread_cap"] if flow["market"] == "Spread" else parameters["total_cap"]
    minimum = parameters["minimum_adjustment"]
    # Requested nonzero minimum is a design prior, not a proven true edge.
    value = 0. if abs(raw) < 1e-9 else sign(raw) * min(cap, max(minimum, abs(raw)))
    baseline = number(baseline)
    return {"policy": POLICY, "profile": profile, "baseline": baseline,
            "raw_adjustment": raw, "adjustment": value, "projection": baseline + value,
            "contributions": dict(zip(FEATURE_NAMES, contributions)),
            "parameters": deepcopy(parameters), "flow_status": flow["status"]}

def combine_components(margin, total):
    margin, total = number(margin), number(total)
    home, away = (total + margin) / 2, (total - margin) / 2
    if min(home, away) <= 0:
        raise ValueError("Combined team scores must be positive.")
    return {"home": home, "away": away, "margin": margin, "total": total}

def project_with_market_history(*, baseline_margin, baseline_total, spread_history,
                                total_history, profiles_by_market, as_of, tipoff):
    flows = {m: flow_features(h, as_of=as_of, tipoff=tipoff, market=m)
             for m, h in (("Spread", spread_history), ("Total", total_history))}
    latest = {f["last_observed_at"] for f in flows.values() if "last_observed_at" in f}
    if len(latest) > 1:
        raise ValueError("Both markets need the same captured timestamp.")
    spread = adjustment(baseline_margin, flows["Spread"], profiles_by_market["Spread"])
    total = adjustment(baseline_total, flows["Total"], profiles_by_market["Total"])
    scores = combine_components(spread["projection"], total["projection"])
    return {"policy": POLICY, "status": "research_only", "official_pick_eligible": False,
            "as_of": stamp(as_of).isoformat(), "market_models": {"spread": spread, "total": total},
            "flows": flows, **scores}

class MarketProjectionState:
    """Recompute from the unadjusted baseline on every paired market snapshot."""
    def __init__(self, *, baseline_margin, baseline_total, tipoff, profiles_by_market=None):
        self.baseline_margin = number(baseline_margin)
        self.baseline_total = number(baseline_total)
        self.tipoff = tipoff
        self.profiles = dict(profiles_by_market or {"Spread": "balanced", "Total": "balanced"})
        self._points = {}
        self._baseline_at = None

    def update(self, snapshot, *, baseline_margin=None, baseline_total=None):
        t = stamp(snapshot["snapshotTime"])
        if t >= stamp(self.tipoff):
            raise ValueError("Market updates must precede tipoff.")
        point = {"snapshotTime": t.isoformat(), "home_spread": number(snapshot["home_spread"]),
                 "total": number(snapshot["total"]),
                 "home_bets_pct": share(snapshot.get("home_bets_pct")),
                 "home_money_pct": share(snapshot.get("home_money_pct")),
                 "over_bets_pct": share(snapshot.get("over_bets_pct")),
                 "over_money_pct": share(snapshot.get("over_money_pct"))}
        if t in self._points and self._points[t] != point:
            raise ValueError("Conflicting observations at the same timestamp.")
        baseline_update = baseline_margin is not None or baseline_total is not None
        if baseline_update and self._baseline_at is not None and t < self._baseline_at:
            raise ValueError("Baseline updates cannot move backward in time.")
        fresh_margin = self.baseline_margin if baseline_margin is None else number(baseline_margin)
        fresh_total = self.baseline_total if baseline_total is None else number(baseline_total)
        combine_components(fresh_margin, fresh_total)
        self._points[t] = point
        self.baseline_margin, self.baseline_total = fresh_margin, fresh_total
        if baseline_update:
            self._baseline_at = t
        points = [p for _, p in sorted(self._points.items())]
        histories = {m: [{"snapshotTime": p["snapshotTime"], "line": p[line],
                          "betsPct": p[bets], "moneyPct": p[money]} for p in points]
                     for m, line, bets, money in (
                         ("Spread", "home_spread", "home_bets_pct", "home_money_pct"),
                         ("Total", "total", "over_bets_pct", "over_money_pct"))}
        return project_with_market_history(baseline_margin=self.baseline_margin,
            baseline_total=self.baseline_total, spread_history=histories["Spread"],
            total_history=histories["Total"], profiles_by_market=self.profiles,
            as_of=points[-1]["snapshotTime"], tipoff=self.tipoff)
