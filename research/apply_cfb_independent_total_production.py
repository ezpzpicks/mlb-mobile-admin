"""Apply the validated independent CFB totals layer to the production admin path.

This script is intentionally small and idempotent. It changes only the CFB install
order/version labels and the CFB diagnostics copy; the spread regression itself is
left untouched.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app_mobile_admin.py"
MARKET = ROOT / "builders" / "cfb_market_calibration.py"

NEW_VERSION = "cfb-v2.3-independent-total-2026-08-28"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one {label} target, found {count}")
    return text.replace(old, new, 1)


def patch_app() -> None:
    text = APP.read_text()
    text = text.replace(
        '"CFB": "cfb-v2.1-calibrated-pricing-2026-08-21",',
        f'"CFB": "{NEW_VERSION}",',
    )
    text = text.replace(
        'CFB now uses the validated team-score regression plus calibrated margin volatility and price-aware spread/total evaluation while retaining live personnel/environment overlays.',
        'CFB now combines the validated spread-margin regression with an independent pace/efficiency totals regression, derives team scores algebraically, and retains live personnel/weather overlays plus calibrated market evaluation.',
    )
    old_block = '''elif selected_sport == "CFB":\n    set_storage_sport("CFB")\n    from builders import cfb_builder\n    from builders.cfb_game_regression import install_regression_layer\n    from builders.cfb_market_calibration import install_market_calibration\n    install_regression_layer(cfb_builder)\n    install_market_calibration(cfb_builder)\n    cfb_builder.MODEL_VERSION = "cfb-v2.1-calibrated-pricing-2026-08-21"\n    cfb_builder.render()'''
    new_block = f'''elif selected_sport == "CFB":\n    set_storage_sport("CFB")\n    from builders import cfb_builder\n    from builders.cfb_game_regression import install_regression_layer\n    from builders.cfb_total_regression import install_total_regression\n    from builders.cfb_market_calibration import install_market_calibration\n    install_regression_layer(cfb_builder)\n    install_total_regression(cfb_builder)\n    install_market_calibration(cfb_builder)\n    cfb_builder.MODEL_VERSION = "{NEW_VERSION}"\n    cfb_builder.render()'''
    text = replace_once(text, old_block, new_block, "CFB install block")
    APP.write_text(text)


def patch_market_calibration() -> None:
    text = MARKET.read_text()
    text = text.replace(
        'MODEL_VERSION = "cfb-v2.2-edge-only-grading-2026-08-28"',
        f'MODEL_VERSION = "{NEW_VERSION}"',
    )
    text = text.replace(
        '- preserves the existing totals distribution exactly;',
        '- preserves the existing totals simulation mechanics while centering them on the independent totals regression;',
    )
    text = text.replace(
        '# Preserve the existing CFB totals engine exactly. Only the margin draw is\n        # replaced by the observed out-of-sample CFB v2 residual distribution.',
        '# Preserve the existing CFB totals simulation mechanics. The deterministic\n        # total mean now comes from the independent totals-regression layer; only\n        # the margin draw is replaced by the observed out-of-sample residual width.',
    )

    old_breakdown = '''        breakdown = pd.DataFrame([\n            {"Component": "Regression base margin", "Home-margin points": _num(projection.get("regression_base_margin"))},\n            {"Component": "Team-specific residual model", "Home-margin points": _num(projection.get("team_specific_residual_delta"))},\n            {"Component": "Live personnel / injuries", "Home-margin points": _num(projection.get("regression_personnel_delta"))},\n            {"Component": "QB / coaching continuity", "Home-margin points": _num(projection.get("regression_continuity_delta"))},\n            {"Component": "Venue / travel / rest deviation", "Home-margin points": _num(projection.get("regression_venue_delta"))},\n            {"Component": "Final deterministic margin", "Home-margin points": _num(projection.get("margin"))},\n        ])\n        with st.expander("CFB v2.1 regression + market calibration breakdown", expanded=False):\n            st.dataframe(breakdown, hide_index=True, use_container_width=True)'''
    new_breakdown = '''        breakdown = pd.DataFrame([\n            {"Component": "Regression base margin", "Home-margin points": _num(projection.get("regression_base_margin"))},\n            {"Component": "Team-specific residual model", "Home-margin points": _num(projection.get("team_specific_residual_delta"))},\n            {"Component": "Live personnel / injuries", "Home-margin points": _num(projection.get("regression_personnel_delta"))},\n            {"Component": "QB / coaching continuity", "Home-margin points": _num(projection.get("regression_continuity_delta"))},\n            {"Component": "Venue / travel / rest deviation", "Home-margin points": _num(projection.get("regression_venue_delta"))},\n            {"Component": "Final deterministic margin", "Home-margin points": _num(projection.get("margin"))},\n        ])\n        total_breakdown = pd.DataFrame([\n            {"Component": "Independent regression base total", "Total points": _num(projection.get("total_regression_base"), _num(projection.get("total")))},\n            {"Component": "Live personnel / injuries", "Total points": _num(projection.get("total_regression_personnel_delta"))},\n            {"Component": "QB / coaching continuity", "Total points": _num(projection.get("total_regression_continuity_delta"))},\n            {"Component": "Weather", "Total points": _num(projection.get("total_regression_weather_delta"))},\n            {"Component": "Score-consistency floor", "Total points": _num(projection.get("total_regression_consistency_adjustment"))},\n            {"Component": "Final deterministic total", "Total points": _num(projection.get("total"))},\n        ])\n        with st.expander("CFB v2.3 spread + independent total breakdown", expanded=False):\n            st.markdown("**Spread / margin model**")\n            st.dataframe(breakdown, hide_index=True, use_container_width=True)\n            st.markdown("**Independent totals model**")\n            st.dataframe(total_breakdown, hide_index=True, use_container_width=True)'''
    text = replace_once(text, old_breakdown, new_breakdown, "CFB diagnostics breakdown")
    MARKET.write_text(text)


def main() -> None:
    patch_app()
    patch_market_calibration()
    print(f"Applied {NEW_VERSION} production wiring")


if __name__ == "__main__":
    main()
