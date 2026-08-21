from types import SimpleNamespace
from pathlib import Path

import numpy as np

from builders import cfb_market_calibration as calibration
from shared.modeling import american_implied_probability, expected_value_per_unit


def prob_with_push(values, threshold, over=True):
    values = np.asarray(values, dtype=float)
    diff = values + threshold if over else threshold - values
    win = float(np.mean(diff > 1e-9))
    push = float(np.mean(np.abs(diff) <= 1e-9))
    return win / max(1e-9, 1.0 - push), push


pricing_builder = SimpleNamespace(
    american_implied_probability=american_implied_probability,
    expected_value_per_unit=expected_value_per_unit,
    _prob_with_push=prob_with_push,
)

# Side-specific prices must matter to selection and EV.
sim = {"margins": np.array([7.0] * 56 + [-7.0] * 44, dtype=float)}
home_value = calibration._priced_spread_market(pricing_builder, sim, 0.0, "Home", "Away", 100, -120)
assert home_value["team"] == "Home"
assert home_value["odds"] == 100
assert home_value["ev"] > 0

away_value = calibration._priced_spread_market(pricing_builder, sim, 0.0, "Home", "Away", -300, 180)
assert away_value["team"] == "Away"
assert away_value["odds"] == 180

# The selected conservative spread gates are exactly the research fallbacks.
assert calibration._grade_spread(0.58, 4.0, 72, 4) == "A Spread"
assert calibration._grade_spread(0.55, 2.5, 62, 3) == "B Spread"
assert calibration._grade_spread(0.549, 10.0, 90, 6) == "No Play"
assert calibration.ATS_CALIBRATION_PROVEN is False
assert abs(calibration.MARGIN_RESIDUAL_SD - 17.75939215594032) < 1e-9
assert calibration.TEAM_RESIDUAL_FEATURES == ()

# Install the simulation wrapper against a small fake builder. The legacy total
# distribution must remain byte-for-byte/numerically unchanged while margins use
# the calibrated residual width.
def legacy_sim(projection, seed, simulations=50000):
    totals = np.full(int(simulations), 100.0)
    return {
        "totals": totals,
        "margins": np.zeros(int(simulations)),
        "away_scores": np.full(int(simulations), 50.0),
        "home_scores": np.full(int(simulations), 50.0),
        "away_mean": 50.0,
        "home_mean": 50.0,
        "away_p10": 50.0,
        "away_p90": 50.0,
        "home_p10": 50.0,
        "home_p90": 50.0,
        "home_win": 0.5,
        "away_win": 0.5,
    }


fake = SimpleNamespace(
    simulate_game=legacy_sim,
    _grade_total=lambda *args: "No Play",
    _display_result=lambda result: None,
    slate_row=lambda result: __import__("pandas").DataFrame([{}]),
    tracker_rows=lambda result, include_no_plays=False: __import__("pandas").DataFrame(),
    SLATE_COLUMNS=[],
    TRACKER_COLUMNS=[],
    SIMULATIONS=50000,
)
calibration.install_market_calibration(fake)
projection = {"margin": 0.0}
out = fake.simulate_game(projection, "smoke", simulations=50000)
assert np.array_equal(out["totals"], np.full(50000, 100.0))
assert abs(float(np.std(out["margins"])) - calibration.MARGIN_RESIDUAL_SD) < 0.35
assert out["margin_residual_sd"] == calibration.MARGIN_RESIDUAL_SD

# Generated UI/app wiring assertions.
builder_source = Path("builders/cfb_builder.py").read_text()
app_source = Path("app_mobile_admin.py").read_text()
module_source = Path("builders/cfb_market_calibration.py").read_text()
assert "Sportsbook lines and prices" in builder_source
assert "Home spread odds" in builder_source or "spread odds" in builder_source
assert "total_over_odds=total_over_odds" in builder_source
assert "total_under_odds=total_under_odds" in builder_source
assert "install_market_calibration(cfb_builder)" in app_source
assert "cfb-v2.1-calibrated-pricing-2026-08-21" in app_source
assert "Price-aware veto" in module_source
assert "TEAM_RESIDUAL_FEATURES: tuple[str, ...] = ()" in module_source

print("CFB v2.1 calibrated-pricing smoke tests passed")
