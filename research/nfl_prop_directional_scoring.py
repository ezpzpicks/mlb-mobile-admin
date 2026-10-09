"""Score projection direction against saved lines without tuning thresholds."""
from __future__ import annotations

import math


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def usable_line(row: dict) -> bool:
    line = finite_number(row.get("market_line"))
    return line is not None and line > 0


def direction(row: dict, policy: str) -> str | None:
    if not usable_line(row):
        return None
    projection = finite_number(row.get(policy))
    if projection is None or projection == float(row["market_line"]):
        return None
    return "Over" if projection > float(row["market_line"]) else "Under"


def outcome(row: dict, policy: str) -> str:
    side = direction(row, policy)
    actual = finite_number(row.get("actual"))
    if side is None or actual is None:
        return "No pick"
    line = float(row["market_line"])
    if actual == line:
        return "Push"
    return "Win" if (actual > line) == (side == "Over") else "Loss"


def record(rows: list[dict], policy: str) -> dict:
    counts = {label: 0 for label in ("Win", "Loss", "Push", "No pick")}
    units, priced = 0.0, 0
    for row in rows:
        result = outcome(row, policy)
        counts[result] += 1
        if result == "No pick":
            continue
        side = direction(row, policy)
        odds = finite_number(row.get("over_odds" if side == "Over" else "under_odds"))
        if odds is None or abs(odds) < 100:
            continue
        priced += 1
        if result == "Win":
            units += 100.0 / abs(odds) if odds < 0 else odds / 100.0
        elif result == "Loss":
            units -= 1.0
    settled = counts["Win"] + counts["Loss"]
    return {
        "wins": counts["Win"], "losses": counts["Loss"],
        "pushes": counts["Push"], "no_picks": counts["No pick"],
        "hit_rate_pct": round(100.0 * counts["Win"] / settled, 2) if settled else None,
        "priced_picks": priced, "net_units_at_saved_odds": round(units, 3),
    }


def comparison(rows: list[dict]) -> dict:
    both_called = [row for row in rows if all(direction(row, p) for p in ("current", "candidate"))]
    output = {"usable_lines": len(rows), "both_called_lines": len(both_called)}
    for policy in ("current", "candidate"):
        selected = [row for row in rows if row.get(f"{policy}_grade") in ("Strong", "Regular")]
        output[policy] = {
            "all_calls": record(rows, policy),
            "same_sample_calls": record(both_called, policy),
            "over": record([row for row in rows if direction(row, policy) == "Over"], policy),
            "under": record([row for row in rows if direction(row, policy) == "Under"], policy),
            "strong": record([row for row in rows if row.get(f"{policy}_grade") == "Strong"], policy),
            "regular": record([row for row in rows if row.get(f"{policy}_grade") == "Regular"], policy),
            "strong_or_regular": record(selected, policy),
        }
    return output


def directional_report(rows: list[dict]) -> dict:
    usable, excluded = [], []
    for row in rows:
        if usable_line(row) and finite_number(row.get("actual")) is not None:
            usable.append(row)
        else:
            excluded.append({
                **{key: row.get(key) for key in ("Week", "Game ID", "Player", "Team", "Slot")},
                "reason": "Missing or invalid saved market line or actual result",
            })
    groups = {"all": usable}
    for slot in sorted({row["Slot"] for row in usable}):
        groups[slot] = [row for row in usable if row["Slot"] == slot]
    for week in sorted({int(row["Week"]) for row in usable}):
        groups[f"week_{week}"] = [row for row in usable if int(row["Week"]) == week]
    groups["positive_yardage_residual"] = [row for row in usable if row["yardage_adjustment"] > 0]
    groups["negative_yardage_residual"] = [row for row in usable if row["yardage_adjustment"] < 0]
    changed = [row for row in usable if direction(row, "current") != direction(row, "candidate")]
    grades = {"Strong", "Regular"}
    added = [row for row in usable if row.get("candidate_grade") in grades and row.get("current_grade") not in grades]
    removed = [row for row in usable if row.get("current_grade") in grades and row.get("candidate_grade") not in grades]
    retained = [row for row in usable if all(row.get(f"{p}_grade") in grades for p in ("current", "candidate"))]
    return {
        "summary": {name: comparison(group) for name, group in groups.items()},
        "line_exclusions": excluded,
        "changed_calls": {
            "n": len(changed), "current": record(changed, "current"),
            "candidate": record(changed, "candidate"),
            "fixed_losses": sum(outcome(row, "current") == "Loss" and outcome(row, "candidate") == "Win" for row in changed),
            "lost_wins": sum(outcome(row, "current") == "Win" and outcome(row, "candidate") == "Loss" for row in changed),
            "rows": [{
                **row, "current_direction": direction(row, "current"),
                "candidate_direction": direction(row, "candidate"),
                "current_outcome": outcome(row, "current"),
                "candidate_outcome": outcome(row, "candidate"),
            } for row in changed],
        },
        "publishable_changes": {
            "added": {"n": len(added), "candidate": record(added, "candidate")},
            "removed": {"n": len(removed), "current": record(removed, "current")},
            "retained": {"n": len(retained), "current": record(retained, "current"), "candidate": record(retained, "candidate")},
        },
    }
