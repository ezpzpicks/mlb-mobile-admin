"""Outcome checks for the retrospective over/under comparison."""
from research.nfl_prop_directional_scoring import directional_report, outcome, record


def row(**updates):
    values = {
        "Week": 4, "Game ID": "example", "Player": "Receiver", "Team": "DAL", "Slot": "WR2",
        "market_line": 50.5, "current": 40.0, "candidate": 60.0, "actual": 70.0,
        "over_odds": -110, "under_odds": -110, "yardage_adjustment": 0.1,
        "current_grade": "Strong", "candidate_grade": "Regular",
    }
    return {**values, **updates}


def main():
    # The smaller yardage error can still lose against the line: under 50.5
    # correctly settles at 49, while a closer 51-yard projection loses.
    case = row(current=20, candidate=51, actual=49)
    assert outcome(case, "current") == "Win"
    assert outcome(case, "candidate") == "Loss"
    assert outcome(row(), "current") == "Loss"
    assert outcome(row(), "candidate") == "Win"
    assert outcome(row(actual=50.5), "candidate") == "Push"
    assert outcome(row(candidate=50.5), "candidate") == "No pick"
    cases = [row(), row(actual=20), row(actual=50.5), row(candidate=50.5)]
    scored = record(cases, "candidate")
    assert (scored["wins"], scored["losses"], scored["pushes"], scored["no_picks"]) == (1, 1, 1, 1)
    assert scored["hit_rate_pct"] == 50.0
    assert scored["priced_picks"] == 3
    assert scored["net_units_at_saved_odds"] == -0.091
    compared = directional_report([
        row(), row(actual=20), row(candidate=50.5), row(market_line=None),
        row(current_grade="Non-Edge"), row(candidate_grade="Non-Edge"),
    ])
    assert compared["summary"]["all"]["usable_lines"] == 5
    assert compared["summary"]["all"]["both_called_lines"] == 4
    assert len(compared["line_exclusions"]) == 1
    assert compared["changed_calls"]["fixed_losses"] == 3
    assert compared["changed_calls"]["lost_wins"] == 1
    assert compared["publishable_changes"]["added"]["n"] == 1
    assert compared["publishable_changes"]["removed"]["n"] == 1
    print("NFL directional settlement checks passed")


if __name__ == "__main__":
    main()
