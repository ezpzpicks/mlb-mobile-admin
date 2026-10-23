"""One production NFL model loader for the admin and unattended builder."""
from __future__ import annotations


def load_production_builder():
    # This entry point installs the complete v4.19–v4.26 wrapper chain before
    # obtaining its patched slot installer, just as shared.auth does in the app.
    import builders.nfl_wr_countprops_v424  # noqa: F401
    from builders import nfl_builder
    from builders.nfl_covers import install_covers_weather
    from builders.nfl_game_regression import install_regression_layer
    from builders.nfl_skill_prop_regression import install_skill_prop_regression
    from builders.nfl_skill_prop_consistency import install_skill_prop_consistency
    from builders.nfl_slot_matchups import install_slot_matchup_layer
    install_regression_layer(nfl_builder)
    install_skill_prop_regression(nfl_builder)
    install_skill_prop_consistency(nfl_builder)
    install_slot_matchup_layer(nfl_builder)
    install_covers_weather(nfl_builder)
    return nfl_builder
