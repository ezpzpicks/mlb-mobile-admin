from pathlib import Path

PATCH_PATH = Path("shared/mlb_k_runtime_patch.py")
INIT_PATH = Path("shared/__init__.py")

V165_MARKER = "# V16.5 TOP-2 WHIFF REFIT"


def replace_once(text, old, new, label):
    if old not in text:
        raise RuntimeError(f"Could not find {label}")
    return text.replace(old, new, 1)


def main():
    patch = PATCH_PATH.read_text(encoding="utf-8")
    if V165_MARKER in patch:
        print("V16.5 patch already installed")
        return

    patch = replace_once(
        patch,
        'LOCKED_K_MODEL_VERSION = "v16.4-locked-mean-tail-calibrated-2026-08-24"',
        'LOCKED_K_MODEL_VERSION = "v16.5-top2-whiff-refit-2026-09-02"',
        "runtime K model version",
    )
    patch = replace_once(
        patch,
        'def run_mlb_builder_with_locked_k_regression(builder_path):\n    """Run MLB with the replay-validated V16.4 strikeout fixes."""',
        'def run_mlb_builder_with_locked_k_regression(builder_path, compile_only=False):\n    """Run MLB with V16.4 protections plus the validated V16.5 Top-2 Whiff refit."""',
        "runtime patch function signature",
    )

    insertion = r'''

    # V16.5 TOP-2 WHIFF REFIT
    # The production-style ablation showed that Top-2 Whiff must be refit jointly
    # with the existing mean equation; bolting it onto the frozen V16.4 equation
    # worsened the 2026 holdout. These coefficients are the joint 2025 fit whose
    # direction replicated in 2024->2025 and improved the untouched 2026 holdout.
    v165_rate_model = ''' + "'''" + r'''_V162_RATE_MODEL = {
    "intercept": -1.2949280364809683,
    "coefficients": {
        "lineup_k_rate": 0.11789729882678486,
        "pit_k_rate_l8": 0.12578267383476258,
        "pit_fastball_velo_l8": 0.06549049674134155,
        "home_pitcher": 0.04461784343986594,
        "pit_release_extension_l8": 0.030721052008882185,
        "pitcher_left": 0.033177181185853936,
        "pitcher_top2_whiff": 0.05514715229839276,
    },
    "means": {
        "lineup_k_rate": 0.2227793688749265,
        "pit_k_rate_l8": 0.2191662521641847,
        "pit_fastball_velo_l8": 93.77818826211985,
        "home_pitcher": 0.49879306561334213,
        "pit_release_extension_l8": 6.477459770881901,
        "pitcher_left": 0.2589422865920562,
        "pitcher_top2_whiff": 0.21297380470250624,
    },
    "stds": {
        "lineup_k_rate": 0.022183060774291,
        "pit_k_rate_l8": 0.0546488826310782,
        "pit_fastball_velo_l8": 2.1419588597787538,
        "home_pitcher": 0.49999854330726434,
        "pit_release_extension_l8": 0.38363394019113894,
        "pitcher_left": 0.4380538537743204,
        "pitcher_top2_whiff": 0.04939686561234128,
    },
}''' + "'''" + r'''
    rate_start = source.rfind('_V162_RATE_MODEL = {')
    rate_end = source.find('\n\n# BF volatility', rate_start)
    if rate_start < 0 or rate_end < 0:
        raise RuntimeError("V16.5 could not locate the final locked K-rate model block")
    source = source[:rate_start] + v165_rate_model + source[rate_end:]

    v165_helper = ''' + "'''" + r'''

_V165_TOP2_WHIFF_MEAN = 0.21297380470250624
_V165_TOP2_WHIFF_STD = 0.04939686561234128
_V165_TOP2_MIN_PITCHES = 85.0


def _v165_top2_pitcher_whiff(pitcher, pitcher_arsenal_df):
    """Season-to-date usage-weighted Whiff% across the pitcher's two primary pitches.

    Live Savant arsenal rows expose pitch count but not swing count, so the 85-pitch
    minimum is the validated live-data proxy for the historical 40-swing requirement.
    Missing/insufficient history returns the 2025 training mean, making this term z=0.
    """
    neutral = {
        "value": _V165_TOP2_WHIFF_MEAN,
        "available": False,
        "pitch_types": [],
        "pitches": [],
        "usage": [],
        "whiff": [],
        "status": "Top-2 Whiff neutral fallback: insufficient live arsenal history",
    }
    try:
        rows = _pitcher_arsenal_rows(pitcher, pitcher_arsenal_df)
        if rows is None or rows.empty:
            return neutral
        top = rows.head(2).copy()
        if len(top) < 2:
            return neutral
        for col in ["Usage", "Whiff", "Pitches"]:
            if col not in top.columns:
                return neutral
            top[col] = pd.to_numeric(top[col], errors="coerce")
        if top[["Usage", "Whiff", "Pitches"]].isna().any().any():
            return neutral
        if (top["Pitches"] < _V165_TOP2_MIN_PITCHES).any():
            neutral["pitch_types"] = top["Pitch Type"].astype(str).tolist()
            neutral["pitches"] = top["Pitches"].astype(float).round(0).tolist()
            neutral["status"] = "Top-2 Whiff neutral fallback: one or both primary pitches below 85 pitches"
            return neutral
        if (top["Usage"] <= 0).any() or (top["Whiff"] <= 0).any():
            return neutral
        usage_total = float(top["Usage"].sum())
        if usage_total <= 0:
            return neutral
        value = float((top["Usage"] * top["Whiff"]).sum() / usage_total)
        # Keep live extrapolation inside the support of the training distribution.
        lower = _V165_TOP2_WHIFF_MEAN - (3.0 * _V165_TOP2_WHIFF_STD)
        upper = _V165_TOP2_WHIFF_MEAN + (3.0 * _V165_TOP2_WHIFF_STD)
        value = max(lower, min(upper, value))
        return {
            "value": value,
            "available": True,
            "pitch_types": top["Pitch Type"].astype(str).tolist(),
            "pitches": top["Pitches"].astype(float).round(0).tolist(),
            "usage": top["Usage"].astype(float).round(4).tolist(),
            "whiff": top["Whiff"].astype(float).round(4).tolist(),
            "status": "V16.5 usage-weighted season Top-2 Whiff active",
        }
    except Exception as exc:
        neutral["status"] = f"Top-2 Whiff neutral fallback: {exc}"
        return neutral
''' + "'''" + r'''
    target_def = 'def _v162_target_k_rate(lineup_k_rate, pitcher_hand, pitcher_is_home, season_profile, workload, recent_pitch):\n'
    target_index = source.rfind(target_def)
    if target_index < 0:
        raise RuntimeError("V16.5 could not locate the final target K-rate function")
    source = source[:target_index] + v165_helper + '\n' + source[target_index:]

    source = _replace_last(
        source,
        '        "pit_release_extension_l8": components["release_extension"],\n'
        '        "pitcher_left": 1.0 if str(pitcher_hand or "R").upper().startswith("L") else 0.0,\n',
        '        "pit_release_extension_l8": components["release_extension"],\n'
        '        "pitcher_left": 1.0 if str(pitcher_hand or "R").upper().startswith("L") else 0.0,\n'
        '        "pitcher_top2_whiff": _safe_float_or_none((recent_pitch or {}).get("pitcher_top2_whiff")),\n',
        "V16.5 Top-2 Whiff K-rate input",
    )

    source = _replace_last(
        source,
        '    recent_pitch["diagnostic_rate_multiplier"] = recent_pitch.get("rate_multiplier", 1.0)\n'
        '    recent_pitch["rate_multiplier"] = 1.0\n'
        '    arsenal = dict(pitch_type_arsenal_adjustment(\n',
        '    recent_pitch["diagnostic_rate_multiplier"] = recent_pitch.get("rate_multiplier", 1.0)\n'
        '    recent_pitch["rate_multiplier"] = 1.0\n'
        '    top2_whiff_details = _v165_top2_pitcher_whiff(pitcher, pitcher_arsenal_df)\n'
        '    recent_pitch["pitcher_top2_whiff"] = top2_whiff_details["value"]\n'
        '    recent_pitch["pitcher_top2_whiff_available"] = bool(top2_whiff_details.get("available"))\n'
        '    recent_pitch["pitcher_top2_whiff_details"] = top2_whiff_details\n'
        '    arsenal = dict(pitch_type_arsenal_adjustment(\n',
        "V16.5 live Top-2 Whiff feature build",
    )

    source = _replace_last(
        source,
        '        "fastball_velocity_input": regression_rate.get("fastball_velocity_l8", ""),\n'
        '        "release_extension_input": regression_rate.get("release_extension_l8", ""),\n'
        '        "structural_std": round(structural_std, 3),\n',
        '        "fastball_velocity_input": regression_rate.get("fastball_velocity_l8", ""),\n'
        '        "release_extension_input": regression_rate.get("release_extension_l8", ""),\n'
        '        "pitcher_top2_whiff_input": round(float(top2_whiff_details.get("value", _V165_TOP2_WHIFF_MEAN)), 5),\n'
        '        "pitcher_top2_whiff_available": bool(top2_whiff_details.get("available")),\n'
        '        "pitcher_top2_whiff_status": top2_whiff_details.get("status", ""),\n'
        '        "pitcher_top2_pitch_types": list(top2_whiff_details.get("pitch_types", []) or []),\n'
        '        "structural_std": round(structural_std, 3),\n',
        "V16.5 Top-2 Whiff diagnostics",
    )

    source = source.replace(
        'K_MODEL_ARCHITECTURE = "v16.4_locked_regression_mean_x_mean_preserving_multi_k_tail_pmf_x_under_tail_calibration"',
        'K_MODEL_ARCHITECTURE = "v16.5_top2_whiff_refit_x_mean_preserving_multi_k_tail_pmf_x_under_tail_calibration"',
        1,
    )

    source = _replace_last_if_present(
        source,
        '            f"V16.4 locked regression projection {raw:.2f} is the production mean; "\\n',
        '            f"V16.5 Top-2 Whiff refit projection {raw:.2f} is the production mean; "\\n',
    )

    if compile_only:
        compile(source, str(path), "exec")
        return source
'''

    patch = replace_once(
        patch,
        '    namespace = {\n',
        insertion + '\n    namespace = {\n',
        "runtime execution namespace",
    )

    PATCH_PATH.write_text(patch, encoding="utf-8")

    init = INIT_PATH.read_text(encoding="utf-8")
    init = init.replace("_ezpz_v164_k_patch_installed", "_ezpz_v165_k_patch_installed")
    init = init.replace("V16.4 MLB runtime patch", "V16.5 MLB runtime patch")
    INIT_PATH.write_text(init, encoding="utf-8")
    print("Installed V16.5 Top-2 Whiff runtime patch")


if __name__ == "__main__":
    main()
