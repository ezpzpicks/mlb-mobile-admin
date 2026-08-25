from pathlib import Path

LOCKED_K_MODEL_VERSION = "v16.4-locked-mean-symmetric-publication-2026-08-24"


def _replace_last(source: str, old: str, new: str, label: str) -> str:
    index = source.rfind(old)
    if index < 0:
        raise RuntimeError(f"MLB K runtime patch target not found: {label}")
    return source[:index] + new + source[index + len(old):]


def run_mlb_builder_with_locked_k_regression(builder_path):
    """Run MLB with the validated V16.4 strikeout fixes.

    The locked regression coefficients and V16.3 mean-preserving PMF stay intact.
    This only removes the rolling mean calibration and the second Over-only
    workload/early-exit publication penalty that replay testing showed was
    suppressing winning Over candidates.
    """
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
        # V16.4: workload and early-exit risk already enter the normal/early-exit
        # BF-mixture PMF that produces the Over/Under probabilities. Penalizing
        # only Overs here counted the same downside risk twice and created a
        # directional publication bias.
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

    source = _replace_last(
        source,
        '        "shadow_grade": published,\n',
        '        "shadow_grade": original,\n',
        "shadow grade tracking",
    )

    source = _replace_last(
        source,
        '            f"V16.3 true-mean regression projection {raw:.2f} → global calibration {global_projection:.2f}; "\n'
        '            "the multi-K layer reshapes the count PMF but preserves that mean. Pitcher, opponent, "\n',
        '            f"V16.4 locked regression projection {raw:.2f} is the production mean; "\n'
        '            "the multi-K layer reshapes the count PMF but preserves that mean. Pitcher, opponent, "\n',
        "calibration status text",
    )

    source = source.replace(
        'K_MODEL_ARCHITECTURE = "v16.3_true_mean_x_mean_preserving_multi_k_tail_pmf"',
        'K_MODEL_ARCHITECTURE = "v16.4_locked_regression_mean_x_mean_preserving_multi_k_tail_pmf"',
        1,
    )

    namespace = {
        "__name__": "__main__",
        "__file__": str(path),
        "__package__": None,
    }
    exec(compile(source, str(path), "exec"), namespace, namespace)
    return namespace
