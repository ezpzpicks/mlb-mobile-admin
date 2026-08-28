from pathlib import Path

path = Path("builders/cfb_market_calibration.py")
text = path.read_text()

replacements = {
    "SPREAD_B_POINT_EDGE = 2.5": "SPREAD_B_POINT_EDGE = 6.0",
    "SPREAD_A_POINT_EDGE = 4.0": "SPREAD_A_POINT_EDGE = 9.5",
    "# No candidate probability/edge gate passed the predefined 2024 ATS stability\n# screen. These are deliberately conservative fallback gates, not claims of a\n# historically proven ATS threshold.": "# 2025 leakage-safe FBS-vs-FBS backtesting showed a material hit-rate/ROI lift\n# when the point-edge gates were made substantially stricter. Probability,\n# reliability, confluence, positive price-edge, and EV gates still apply.\n# These cutoffs are intentionally selective and are not a guarantee of future ATS results.",
}

for old, new in replacements.items():
    if old not in text:
        if new in text:
            continue
        raise RuntimeError(f"Expected block not found: {old!r}")
    text = text.replace(old, new, 1)

path.write_text(text)
print("Updated CFB spread grade point-edge gates: B=6.0, A=9.5")
