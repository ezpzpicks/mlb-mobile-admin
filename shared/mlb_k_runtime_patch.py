from pathlib import Path

LOCKED_K_MODEL_VERSION = "v16.5-top2-whiff-refit-2026-09-02"
UNDER_TAIL_PROBABILITY_CALIBRATION = 0.05


def _replace_last(source: str, old: str, new: str, label: str) -> str:
    index = source.rfind(old)
    if index < 0:
        raise RuntimeError(f"MLB K runtime patch target not found: {label}")
    return source[:index] + new + source[index + len(old):]


def _replace_last_if_present(source: str, old: str, new: str) -> str:
    index = source.rfind(old)
    if index < 0:
        return source
    return source[:index] + new + source[index + len(old):]


def run_mlb_builder_with_locked_k_regression(builder_path, compile_only=False):
    """Run MLB with V16.4 protections plus the validated V16.5 Top-2 Whiff refit."""
    path = Path(builder_path)
    source = path.read_text(encoding="utf-8")

    old_version = 'K_MODEL_VERSION = "v16.3-mean-preserving-multi-k-tail-2026-08-09"'
    if old_version not in source:
        raise RuntimeError("Expected V16.3 K model version not found; refusing to patch an unknown builder.")
    source = source.replace(
        old_version,
        f'K_MODEL_VERSION = "{LOCKED_K_MODEL_VERSION}"',
        1,
    )

    source = _replace_last(
        source,
        '    global_projection = max(0.0, float(global_fit["intercept"]) + float(global_fit["slope"]) * raw)\n',
        '    # V16.4: replay testing showed the rolling mean calibration slightly worsened MAE/RMSE.\n'
        '    # Keep the locked regression as the production mean; the PMF remains mean-preserving.\n'
        '    global_projection = raw\n',
        "locked regression mean",
    )

    # The V16.3 Under tail was overconfident in completed-start replay: qualified
    # Unders averaged 66.4% model probability but hit 54.3%. A 5-point decision
    # calibration, followed by full side reselection, improved the replay to
    # 27-14 on Unders while preserving the 21-7 qualified Over record.
    source = _replace_last(
        source,
        '    under_probability = max(0.0, min(1.0, float(probabilities.get("under", 0.5) or 0.0)))\n'
        '    over_edge = over_probability - american_odds_to_implied_prob(parsed_over) * (1.0 - push)\n'
        '    under_edge = under_probability - american_odds_to_implied_prob(parsed_under) * (1.0 - push)\n',
        '    raw_under_probability = max(0.0, min(1.0, float(probabilities.get("under", 0.5) or 0.0)))\n'
        f'    under_probability = max(0.0, raw_under_probability - {UNDER_TAIL_PROBABILITY_CALIBRATION:.2f})\n'
        '    over_edge = over_probability - american_odds_to_implied_prob(parsed_over) * (1.0 - push)\n'
        '    under_edge = under_probability - american_odds_to_implied_prob(parsed_under) * (1.0 - push)\n',
        "Under tail decision-probability calibration",
    )

    old_publication_gate = '''    if published in ["LEAN OVER", "OVER", "STRONG OVER"]:
        if not partial_workload_support:
            published = "PASS"
            reasons.append(
                f"Starter workload lacks support: normal regime {normal_pitches:.1f} pitches / {normal_bf:.1f} BF"
            )
        elif not workload_supported and published in ["OVER", "STRONG OVER"]:
            published = "LEAN OVER"
            reasons.append(
                f"Partial workload support ({normal_pitches:.1f} pitches / {normal_bf:.1f} BF); capped at Lean Over"
            )
        if early_exit_risk == "High" and published in ["STRONG OVER", "OVER"]:
            published = "LEAN OVER"
            reasons.append("High modeled early-exit risk caps Over at Lean Over")
        elif published == "STRONG OVER" and early_exit_risk == "Medium":
            published = "OVER"
            reasons.append(f"{early_exit_risk} modeled early-exit risk prevents Strong Over")
'''
    new_publication_gate = '''    if published in ["LEAN OVER", "OVER", "STRONG OVER"]:
        # Workload and early-exit risk already enter the normal/early-exit BF
        # mixture that generates the PMF. A second Over-only veto double-counted
        # the same downside and suppressed 12-4 historical Over candidates.
        reasons.append(
            f"V16.4 symmetric publication: workload/early-exit risk already priced into the BF-mixture PMF "
            f"({normal_pitches:.1f} pitches / {normal_bf:.1f} BF; {early_exit_risk} early-exit risk); "
            "no second Over-only penalty"
        )
'''
    source = _replace_last(
        source,
        old_publication_gate,
        new_publication_gate,
        "Over-only workload publication gate",
    )

    # Historical role mix is useful inside the workload/projection model, but it
    # should not veto a pitcher who is currently occupying the starter slot and
    # has a fully supported starter workload. Openers/bulk arms remain blocked by
    # the explicit current-role gate, and partial/unsupported workloads stay
    # projection-only.
    old_historical_role_gate = '''    if published != "PASS" and hybrid_or_reliever:
        published = "PASS"
        reasons.append("V16 publishes established traditional starters only; hybrid/reliever workload is projection-only")
'''
    new_historical_role_gate = '''    if published != "PASS" and hybrid_or_reliever and not (role_upper == "STARTER" and workload_supported):
        published = "PASS"
        reasons.append("V16 requires a current starter role with full starter workload; hybrid/reliever workload without full support is projection-only")
'''
    source = _replace_last(
        source,
        old_historical_role_gate,
        new_historical_role_gate,
        "current full-workload starter publication eligibility",
    )

    source = _replace_last_if_present(
        source,
        '"""V16 live publication: traditional starters and 8 usable profiles only."""',
        '"""V16 live publication: current full-workload starters and 8 usable profiles."""',
    )

    source = _replace_last_if_present(
        source,
        '        "shadow_grade": published,\n',
        '        "shadow_grade": original,\n',
    )

    source = _replace_last_if_present(
        source,
        '            f"V16.3 true-mean regression projection {raw:.2f} → global calibration {global_projection:.2f}; "\n'
        '            "the multi-K layer reshapes the count PMF but preserves that mean. Pitcher, opponent, "\n',
        '            f"V16.4 locked regression projection {raw:.2f} is the production mean; "\n'
        '            "the multi-K layer reshapes the count PMF but preserves that mean. Pitcher, opponent, "\n',
    )

    source = source.replace(
        'K_MODEL_ARCHITECTURE = "v16.3_true_mean_x_mean_preserving_multi_k_tail_pmf"',
        'K_MODEL_ARCHITECTURE = "v16.4_locked_regression_mean_x_mean_preserving_multi_k_tail_pmf_x_under_tail_calibration"',
        1,
    )

    # pitcher_recent_form is a single, durable history table across model versions.
    # A transient Sheets read must never be converted to an empty dataframe and
    # followed by worksheet.clear(), because that can erase all prior starts.
    # Read it strictly, merge/upsert against the existing rows, and update in place
    # without clearing the worksheet. Model Version remains a per-row field.
    old_recent_form_io = '''def load_pitcher_recent_form():
    return read_sheet(RECENT_FORM_TAB, RECENT_FORM_COLUMNS)


def save_pitcher_recent_form(df):
    return write_sheet(RECENT_FORM_TAB, df, RECENT_FORM_COLUMNS)
'''
    new_recent_form_io = '''def _read_pitcher_recent_form_strict():
    worksheet = get_or_create_worksheet(RECENT_FORM_TAB, RECENT_FORM_COLUMNS)
    try:
        values = worksheet.get_all_values()
    except Exception as exc:
        raise RuntimeError(f"Could not safely read persistent pitcher history: {exc}") from exc

    if not values:
        return pd.DataFrame(columns=RECENT_FORM_COLUMNS)

    header = [str(value).strip() for value in values[0]]
    required = {"Date", "Pitcher"}
    if not required.issubset(set(header)):
        raise RuntimeError(
            "Persistent pitcher history has an invalid header; refusing a destructive rewrite."
        )

    positions = {
        col: header.index(col)
        for col in RECENT_FORM_COLUMNS
        if col in header
    }
    rows = []
    for values_row in values[1:]:
        row = {
            col: values_row[idx] if idx < len(values_row) else ""
            for col, idx in positions.items()
        }
        for col in RECENT_FORM_COLUMNS:
            if col not in row:
                row[col] = ""
        if any(str(row.get(col, "")).strip() for col in RECENT_FORM_COLUMNS):
            rows.append(row)

    if not rows:
        return pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    return pd.DataFrame(rows, columns=RECENT_FORM_COLUMNS).astype(object)


def load_pitcher_recent_form():
    return _read_pitcher_recent_form_strict()


def save_pitcher_recent_form(df):
    incoming = df.copy() if df is not None else pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    for col in RECENT_FORM_COLUMNS:
        if col not in incoming.columns:
            incoming[col] = ""
    incoming = incoming[RECENT_FORM_COLUMNS].astype(object)

    # Re-read immediately before every write. If that read fails, the exception
    # aborts the save rather than treating history as empty.
    existing = _read_pitcher_recent_form_strict()
    if existing is not None and not existing.empty and incoming.empty:
        raise RuntimeError(
            "Refusing to replace non-empty pitcher history with an empty dataframe."
        )

    combined = pd.concat([existing, incoming], ignore_index=True)
    for col in RECENT_FORM_COLUMNS:
        if col not in combined.columns:
            combined[col] = ""
    combined = combined[RECENT_FORM_COLUMNS].astype(object)

    if not combined.empty:
        def _history_key(row):
            game_key = str(row.get("Game Key", "") or "").strip()
            if not game_key:
                game_key = "|".join([
                    str(row.get("Team", "") or "").strip(),
                    str(row.get("Opponent", "") or "").strip(),
                ])
            return "|".join([
                str(row.get("Date", "") or "").strip(),
                game_key,
                normalize_name_for_match(row.get("Pitcher", "")),
                str(row.get("Role", "") or "").strip().upper(),
            ])

        combined["_history_key"] = combined.apply(_history_key, axis=1)
        combined = combined.drop_duplicates(subset=["_history_key"], keep="last")
        combined = combined.drop(columns=["_history_key"]).reset_index(drop=True)

    out = combined.fillna("").astype(str)
    values = [RECENT_FORM_COLUMNS] + out.values.tolist()
    worksheet = get_or_create_worksheet(RECENT_FORM_TAB, RECENT_FORM_COLUMNS)
    try:
        # Do not clear this history worksheet. The merged table is monotonic, so
        # an in-place update preserves prior rows even if a later write fails.
        worksheet.update(values)
        return True
    except Exception as exc:
        st.error(f"Could not safely update persistent pitcher history: {exc}")
        return False
'''
    source = _replace_last(
        source,
        old_recent_form_io,
        new_recent_form_io,
        "persistent pitcher recent-form storage",
    )

    old_change_log = '''                    "V16.3 pitcher strikeouts: V16.2 locked true-mean K-rate/BF regressions retained; "
                    "nine confirmed batter probabilities with uncapped repeated-PA expected counts; "
                    "replicated mean-preserving P(2+) calibration and convolved 0/1/2/3+ batter PMFs "
                    "over normal/early-exit BF; probability/price-edge side selection; true mean, mode, "
                    "median and both tail probabilities tracked; unreplicated archetype boosts excluded."
'''
    new_change_log = '''                    "V16.4 pitcher strikeouts: locked regression restored as the production mean after replay "
                    "showed rolling mean calibration slightly worsened MAE/RMSE; V16.3 mean-preserving multi-K "
                    "PMF retained; Under decision probability calibrated -5 points after 66.4% modeled / 54.3% "
                    "observed tail overconfidence; workload and early-exit remain inside the BF-mixture PMF, "
                    "with the second Over-only publication penalty removed; current full-workload starters can "
                    "publish even when older season role history includes relief usage; opener/bulk and lineup "
                    "eligibility protections remain active."
'''
    source = _replace_last_if_present(source, old_change_log, new_change_log)



    # V16.5 METADATA ALIGNMENT
    source = source.replace(
        'K_MODEL_OVERHAUL_DATE = "2026-08-09"',
        'K_MODEL_OVERHAUL_DATE = "2026-09-02"',
        1,
    )
    source = _replace_last_if_present(
        source,
        '            "V16.3 keeps the locked regression true mean, then applies the replicated "\n',
        '            "V16.5 refits the locked K-rate mean with usage-weighted Top-2 pitcher Whiff, then applies the replicated "\n',
    )
    source = _replace_last_if_present(
        source,
        '            "V16.3 starters only; confirmed order plus at least 8 usable hitter K profiles; "\n',
        '            "V16.5 current full-workload starters only; confirmed order plus at least 8 usable hitter K profiles; "\n',
    )

    old_v164_log = '''                    "V16.4 pitcher strikeouts: locked regression restored as the production mean after replay "
                    "showed rolling mean calibration slightly worsened MAE/RMSE; V16.3 mean-preserving multi-K "
                    "PMF retained; Under decision probability calibrated -5 points after 66.4% modeled / 54.3% "
                    "observed tail overconfidence; workload and early-exit remain inside the BF-mixture PMF, "
                    "with the second Over-only publication penalty removed; current full-workload starters can "
                    "publish even when older season role history includes relief usage; opener/bulk and lineup "
                    "eligibility protections remain active."
'''
    new_v165_log = '''                    "V16.5 pitcher strikeouts: the K-rate mean is jointly refit with usage-weighted season-to-date "
                    "Top-2 pitcher Whiff% using an 85-pitch minimum per primary pitch; insufficient Top-2 history "
                    "is neutral at the locked training mean. The feature replicated directionally in 2024-to-2025 "
                    "and improved the untouched 2026 holdout. V16.4 protections are retained: locked regression "
                    "mean, mean-preserving multi-K PMF, -5 point Under decision calibration, BF-mixture workload/" 
                    "early-exit handling, symmetric publication, and current full-workload starter eligibility."
'''
    source = _replace_last_if_present(source, old_v164_log, new_v165_log)

    # V16.5 TOP-2 WHIFF REFIT
    # The production-style ablation showed that Top-2 Whiff must be refit jointly
    # with the existing mean equation; bolting it onto the frozen V16.4 equation
    # worsened the 2026 holdout. These coefficients are the joint 2025 fit whose
    # direction replicated in 2024->2025 and improved the untouched 2026 holdout.
    v165_rate_model = '''_V162_RATE_MODEL = {
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
}'''
    rate_start = source.rfind('_V162_RATE_MODEL = {')
    rate_end = source.find('\n\n# BF volatility', rate_start)
    if rate_start < 0 or rate_end < 0:
        raise RuntimeError("V16.5 could not locate the final locked K-rate model block")
    source = source[:rate_start] + v165_rate_model + source[rate_end:]

    v165_helper = '''

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
'''
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

    namespace = {
        "__name__": "__main__",
        "__file__": str(path),
        "__package__": None,
    }
    exec(compile(source, str(path), "exec"), namespace, namespace)
    return namespace
