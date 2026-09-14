import ast
import math
from pathlib import Path


def main() -> None:
    source = Path("builders/nfl_builder.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_current_season_role_weight"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = {
        "math": math,
        "_num": lambda value, default=0: float(value) if value is not None else float(default),
    }
    exec(compile(module, "<role-weight-test>", "exec"), scope)
    weight = scope["_current_season_role_weight"]
    expected = {0: 0.0, 1: 0.25, 2: 0.50, 3: 0.75, 4: 0.90, 8: 0.90, 17: 0.90}
    for games, target in expected.items():
        actual = weight(games)
        assert abs(actual - target) < 1e-12, (games, actual, target)
    assert "current_weight = _current_season_role_weight(current_games)" in source
    assert "Current-season usage {current_weight:.0%}" in source
    print("NFL progressive player-prop season weighting validation passed")


if __name__ == "__main__":
    main()
