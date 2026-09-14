from pathlib import Path


def main() -> None:
    source = Path("builders/nfl_builder.py").read_text(encoding="utf-8")
    assert '(prop_base["Position"].astype(str) == "TE")' in source
    assert '(prop_base["Slot"].astype(str) == "TE1")' in source
    assert 'yard_player_keys = {' in source
    assert '(prop_inputs["Market"].astype(str) == "Anytime TD")' in source
    assert 'in yard_player_keys' in source
    print("NFL TE1 receiving-yard + Anytime TD surface validation passed")


if __name__ == "__main__":
    main()
