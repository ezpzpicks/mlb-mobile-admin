from pathlib import Path

PATH = Path("shared/mlb_k_runtime_patch.py")
MARKER = "# V16.5 METADATA ALIGNMENT"
INSERT_BEFORE = "    # V16.5 TOP-2 WHIFF REFIT\n"


def main():
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("V16.5 metadata already aligned")
        return
    if INSERT_BEFORE not in text:
        raise RuntimeError("Could not find V16.5 runtime insertion point")

    block = r'''    # V16.5 METADATA ALIGNMENT
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

    old_v164_log = ''' + "'''" + r'''                    "V16.4 pitcher strikeouts: locked regression restored as the production mean after replay "
                    "showed rolling mean calibration slightly worsened MAE/RMSE; V16.3 mean-preserving multi-K "
                    "PMF retained; Under decision probability calibrated -5 points after 66.4% modeled / 54.3% "
                    "observed tail overconfidence; workload and early-exit remain inside the BF-mixture PMF, "
                    "with the second Over-only publication penalty removed; current full-workload starters can "
                    "publish even when older season role history includes relief usage; opener/bulk and lineup "
                    "eligibility protections remain active."
''' + "'''" + r'''
    new_v165_log = ''' + "'''" + r'''                    "V16.5 pitcher strikeouts: the K-rate mean is jointly refit with usage-weighted season-to-date "
                    "Top-2 pitcher Whiff% using an 85-pitch minimum per primary pitch; insufficient Top-2 history "
                    "is neutral at the locked training mean. The feature replicated directionally in 2024-to-2025 "
                    "and improved the untouched 2026 holdout. V16.4 protections are retained: locked regression "
                    "mean, mean-preserving multi-K PMF, -5 point Under decision calibration, BF-mixture workload/" 
                    "early-exit handling, symmetric publication, and current full-workload starter eligibility."
''' + "'''" + r'''
    source = _replace_last_if_present(source, old_v164_log, new_v165_log)

'''
    text = text.replace(INSERT_BEFORE, block + INSERT_BEFORE, 1)
    PATH.write_text(text, encoding="utf-8")
    print("Aligned V16.5 model metadata")


if __name__ == "__main__":
    main()
