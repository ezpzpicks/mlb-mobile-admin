from pathlib import Path

LOCKED_K_MODEL_VERSION = "v16.4-locked-mean-tail-calibrated-2026-08-24"
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


def run_mlb_builder_with_locked_k_regression(builder_path):
    """Run MLB with the replay-validated V16.4 strikeout fixes."""
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

    namespace = {
        "__name__": "__main__",
        "__file__": str(path),
        "__package__": None,
    }
    exec(compile(source, str(path), "exec"), namespace, namespace)
    return namespace
