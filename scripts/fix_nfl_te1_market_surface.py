from pathlib import Path

OLD_VERSION = "nfl-v4.9-te1-yardage-matchups-2026-09-13"
NEW_VERSION = "nfl-v4.10-te1-prop-surface-2026-09-13"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    builder_path = Path("builders/nfl_builder.py")
    text = builder_path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        f'MODEL_VERSION = "{OLD_VERSION}"',
        f'MODEL_VERSION = "{NEW_VERSION}"',
        "builder version",
    )
    old_mask = '''        wager_mask = (
            ((prop_base["Position"].astype(str) == "QB") & (prop_base["Market"].astype(str) == "Passing Yards"))
            | ((prop_base["Position"].astype(str) == "RB") & (prop_base["Market"].astype(str).isin(["Rushing Yards", "Receiving Yards"])))
            | ((prop_base["Position"].astype(str) == "WR") & (prop_base["Market"].astype(str) == "Receiving Yards"))
        )
'''
    new_mask = '''        wager_mask = (
            ((prop_base["Position"].astype(str) == "QB") & (prop_base["Market"].astype(str) == "Passing Yards"))
            | ((prop_base["Position"].astype(str) == "RB") & (prop_base["Market"].astype(str).isin(["Rushing Yards", "Receiving Yards"])))
            | ((prop_base["Position"].astype(str) == "WR") & (prop_base["Market"].astype(str) == "Receiving Yards"))
            | ((prop_base["Position"].astype(str) == "TE") & (prop_base["Slot"].astype(str) == "TE1") & (prop_base["Market"].astype(str) == "Receiving Yards"))
        )
'''
    text = replace_once(text, old_mask, new_mask, "production wager mask")
    builder_path.write_text(text, encoding="utf-8")

    patch_path = Path("scripts/patch_nfl_manual_markets.py")
    patch_text = patch_path.read_text(encoding="utf-8")
    patch_text = replace_once(patch_text, old_mask, new_mask, "manual-market source template")
    patch_path.write_text(patch_text, encoding="utf-8")

    slot_path = Path("builders/nfl_slot_matchups.py")
    slot_text = slot_path.read_text(encoding="utf-8")
    slot_text = replace_once(
        slot_text,
        f'MODEL_VERSION = "{OLD_VERSION}"',
        f'MODEL_VERSION = "{NEW_VERSION}"',
        "slot layer version",
    )
    slot_path.write_text(slot_text, encoding="utf-8")

    app_path = Path("app_mobile_admin.py")
    app_text = app_path.read_text(encoding="utf-8")
    app_text = replace_once(
        app_text,
        f'"NFL": "{OLD_VERSION}"',
        f'"NFL": "{NEW_VERSION}"',
        "admin version",
    )
    app_path.write_text(app_text, encoding="utf-8")

    test_path = Path("research/test_nfl_te1_market_surface.py")
    test_path.write_text(
        '''from pathlib import Path\n\n\ndef main() -> None:\n    source = Path("builders/nfl_builder.py").read_text(encoding="utf-8")\n    assert '(prop_base["Position"].astype(str) == "TE")' in source\n    assert '(prop_base["Slot"].astype(str) == "TE1")' in source\n    assert 'yard_player_keys = {' in source\n    assert '(prop_inputs["Market"].astype(str) == "Anytime TD")' in source\n    assert 'in yard_player_keys' in source\n    print("NFL TE1 receiving-yard + Anytime TD surface validation passed")\n\n\nif __name__ == "__main__":\n    main()\n''',
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
