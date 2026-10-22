"""One production model configuration for the admin and scheduled builder."""
from __future__ import annotations

MODEL_VERSION = "cfb-v2.8.1-five-game-residual-totals-2026-10-07"


def load_production_builder():
    from builders import cfb_builder
    from builders.cfb_runtime_guard import install_runtime_guard
    from builders.cfb_save_type_guard import install_save_type_guard
    from builders.cfb_game_regression import install_regression_layer
    from builders.cfb_total_regression import install_total_regression
    from builders.cfb_schedule_adjustment import install_schedule_adjustment
    from builders.cfb_2026_regime import install_regime_adjustment
    from builders.cfb_market_calibration import install_market_calibration
    from builders.cfb_covers import install_covers_layer
    from builders.cfb_interactive_recovery import install_interactive_recovery

    # Order matches the interactive admin; every installer is idempotent.
    install_runtime_guard(cfb_builder)
    install_save_type_guard(cfb_builder)
    install_regression_layer(cfb_builder)
    install_total_regression(cfb_builder)
    install_schedule_adjustment(cfb_builder)
    install_regime_adjustment(cfb_builder)
    install_market_calibration(cfb_builder)
    install_covers_layer(cfb_builder)
    install_interactive_recovery(cfb_builder)
    cfb_builder.MODEL_VERSION = MODEL_VERSION
    return cfb_builder
