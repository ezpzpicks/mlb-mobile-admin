from pathlib import Path

path = Path("builders/cfb_market_calibration.py")
text = path.read_text()

text = text.replace(
    'MODEL_VERSION = "cfb-v2.1-calibrated-pricing-2026-08-21"',
    'MODEL_VERSION = "cfb-v2.2-edge-only-grading-2026-08-28"',
    1,
)

old_comment = """# 2025 leakage-safe FBS-vs-FBS backtesting showed a material hit-rate/ROI lift
# when the point-edge gates were made substantially stricter. Probability,
# reliability, confluence, positive price-edge, and EV gates still apply.
# These cutoffs are intentionally selective and are not a guarantee of future ATS results."""
new_comment = """# 2025 leakage-safe FBS-vs-FBS backtesting showed a material hit-rate/ROI lift
# when the point-edge gates were made substantially stricter. Spread A/B grading
# is intentionally simple: point edge determines the grade, while probability,
# reliability, and confluence are tracked as diagnostics. Actual price must still
# have positive no-vig edge and positive EV or the play is vetoed."""
if old_comment in text:
    text = text.replace(old_comment, new_comment, 1)

old_grade = """def _grade_spread(probability: float, point_edge: float, reliability: float, confluence: int) -> str:
    if probability >= SPREAD_A_PROBABILITY and point_edge >= SPREAD_A_POINT_EDGE and reliability >= 72 and confluence >= 4:
        return \"A Spread\"
    if probability >= SPREAD_B_PROBABILITY and point_edge >= SPREAD_B_POINT_EDGE and reliability >= 62 and confluence >= 3:
        return \"B Spread\"
    return \"No Play\""" 
new_grade = """def _grade_spread(probability: float, point_edge: float, reliability: float, confluence: int) -> str:
    # Probability, Reliability, and Confluence remain recorded for diagnostics,
    # but they do not veto a spread grade. The 2025 holdout-supported point edge
    # is the grading signal; actual price/EV is enforced immediately afterward.
    if point_edge >= SPREAD_A_POINT_EDGE:
        return \"A Spread\"
    if point_edge >= SPREAD_B_POINT_EDGE:
        return \"B Spread\"
    return \"No Play\"""
if old_grade not in text:
    if new_grade not in text:
        raise RuntimeError("Expected _grade_spread block not found")
else:
    text = text.replace(old_grade, new_grade, 1)

path.write_text(text)
print("CFB spread grades simplified: B>=6.0, A>=9.5; probability/reliability/confluence diagnostics only")
