from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def load_patch_module():
    path = Path("shared/mlb_k_runtime_patch.py")
    spec = spec_from_file_location("mlb_k_runtime_patch_v165_test", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load_patch_module()
    source = module.run_mlb_builder_with_locked_k_regression(
        Path("builders/mlb_builder.py"), compile_only=True
    )

    assert module.LOCKED_K_MODEL_VERSION == "v16.5-top2-whiff-refit-2026-09-02"
    assert "v16.5_top2_whiff_refit_x_mean_preserving_multi_k_tail_pmf_x_under_tail_calibration" in source
    assert '"pitcher_top2_whiff": 0.05514715229839276' in source
    assert '"pitcher_top2_whiff": 0.21297380470250624' in source
    assert '"pitcher_top2_whiff": 0.04939686561234128' in source
    assert "_V165_TOP2_MIN_PITCHES = 85.0" in source
    assert "def _v165_top2_pitcher_whiff" in source
    assert 'recent_pitch["pitcher_top2_whiff"] = top2_whiff_details["value"]' in source
    assert '"pitcher_top2_whiff": _safe_float_or_none((recent_pitch or {}).get("pitcher_top2_whiff"))' in source
    assert 'under_probability = max(0.0, raw_under_probability - 0.05)' in source
    assert "global_projection = raw" in source
    assert "the multi-K layer reshapes the count PMF but preserves that mean" in source
    assert "Could not safely read persistent pitcher history" in source

    # The old frozen six-variable rate equation must not remain active after patching.
    assert '"pit_k_rate_l8": 0.14996542114827904' not in source

    print("V16.5 Top-2 Whiff runtime patch compiles and preserves V16.4 protections")


if __name__ == "__main__":
    main()
