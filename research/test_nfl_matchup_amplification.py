import ast
from pathlib import Path


def _literal_assignment(tree: ast.Module, name: str):
    node = next(
        n for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
    )
    return ast.literal_eval(node.value)


def main() -> None:
    builder_source = Path("builders/nfl_builder.py").read_text(encoding="utf-8")
    slot_source = Path("builders/nfl_slot_matchups.py").read_text(encoding="utf-8")

    # Syntax validation without importing Streamlit/data dependencies.
    builder_tree = ast.parse(builder_source)
    slot_tree = ast.parse(slot_source)

    slots = _literal_assignment(slot_tree, "TRACKED_SLOTS")
    expected_slots = {"QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE1"}
    assert slots == expected_slots, (slots, expected_slots)

    market_slots = _literal_assignment(slot_tree, "MARKET_SLOTS")
    assert market_slots["Passing Yards"] == {"QB"}
    assert market_slots["Rushing Yards"] == {"QB", "RB1", "RB2"}
    assert market_slots["Receiving Yards"] == {"RB1", "RB2", "WR1", "WR2", "WR3", "TE1"}

    broad = _literal_assignment(slot_tree, "BROAD_MATCHUP_STRENGTH")
    total_caps = _literal_assignment(slot_tree, "TOTAL_MATCHUP_CAP")
    for market in ("Passing Yards", "Rushing Yards", "Receiving Yards"):
        assert broad[market] >= 0.75
        assert total_caps[market] >= 0.35

    assert "outlier_index = absolute_index" in slot_source
    assert "current_weight = _matchup_season_weight" in builder_source
    assert "if games == 2:\n        return 0.60" in builder_source
    assert "if games == 3:\n        return 0.75" in builder_source

    # Ensure both edited files compile.
    compile(builder_source, "builders/nfl_builder.py", "exec")
    compile(slot_source, "builders/nfl_slot_matchups.py", "exec")
    print("NFL matchup amplification validation passed")


if __name__ == "__main__":
    main()
