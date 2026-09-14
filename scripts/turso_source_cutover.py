from pathlib import Path

path = Path("builders/mlb_builder.py")
text = path.read_text(encoding="utf-8")

# Remove the Google client imports from the permanent builder source.
text = text.replace("import gspread\n", "")
text = text.replace("from google.oauth2.service_account import Credentials\n", "")

storage_title = "# GOOGLE SHEETS STORAGE"
title_at = text.find(storage_title)
if title_at < 0:
    raise SystemExit("Google storage section marker was not found")
section_start = text.rfind("# -----------------------", 0, title_at)
if section_start < 0:
    raise SystemExit("Google storage section start was not found")
write_at = text.find("def write_sheet(", title_at)
if write_at < 0:
    raise SystemExit("write_sheet was not found in storage section")
section_end = text.find("# -----------------------", write_at)
if section_end < 0:
    raise SystemExit("storage section end marker was not found")

replacement = '''# -----------------------
# TURSO STORAGE
# -----------------------

from shared.turso_storage import is_turso_ready, read_dataset, replace_dataset


def _require_mlb_turso():
    if not is_turso_ready():
        raise RuntimeError(
            "Turso is not configured or reachable. Google Sheets is no longer a production fallback."
        )


def read_sheet(tab_name, columns):
    """Read the MLB dataset from Turso using the builder's established table contract."""
    columns = list(columns or [])
    try:
        _require_mlb_turso()
        df = read_dataset("MLB", str(tab_name), columns)
        if df is None:
            empty = pd.DataFrame(columns=columns)
            replace_dataset("MLB", str(tab_name), empty, columns)
            return empty
        out = df.copy()
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        return out[columns].fillna("").astype(object)
    except Exception as exc:
        st.error(f"Could not read Turso dataset 'MLB/{tab_name}': {exc}")
        return pd.DataFrame(columns=columns)


def write_sheet(tab_name, df, columns):
    """Replace the MLB dataset in Turso. No Google write or fallback is attempted."""
    columns = list(columns or [])
    try:
        _require_mlb_turso()
        out = df.copy() if df is not None else pd.DataFrame(columns=columns)
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        out = out[columns].fillna("").astype(str)
        replace_dataset("MLB", str(tab_name), out, columns)
        return True
    except Exception as exc:
        st.error(f"Could not write Turso dataset 'MLB/{tab_name}': {exc}")
        return False


'''
text = text[:section_start] + replacement + text[section_end:]

# The old handpick optimization directly edited a worksheet row. Turso has no
# Sheets quota problem, so persist the canonical tracker table through write_sheet.
handpick_start = text.find("def save_handpick_tracker_row(")
if handpick_start < 0:
    raise SystemExit("save_handpick_tracker_row was not found")
next_def = text.find("\ndef ", handpick_start + 4)
if next_def < 0:
    raise SystemExit("could not locate function following save_handpick_tracker_row")
handpick_replacement = '''def save_handpick_tracker_row(tracker_df, row_idx, append_new=False):
    """Persist the canonical bet tracker after a handpick edit through Turso."""
    if row_idx not in tracker_df.index:
        return False
    return write_sheet(TRACKER_TAB, tracker_df, TRACKER_COLUMNS)
'''
text = text[:handpick_start] + handpick_replacement + text[next_def + 1:]

for forbidden in (
    "import gspread",
    "google.oauth2.service_account",
    "GOOGLE_CREDENTIALS",
    "get_or_create_worksheet(",
    "connect_to_sheets(",
    "_read_mlb_turso_fallback",
    "_mirror_mlb_turso",
):
    if forbidden in text:
        raise SystemExit(f"legacy Google storage reference remains in MLB builder: {forbidden}")

path.write_text(text, encoding="utf-8")

# This is a one-time source migration, not a runtime patch. Remove the migration
# machinery in the same commit that contains the permanent builder change.
Path("scripts/turso_source_cutover.py").unlink(missing_ok=True)
Path(".github/workflows/turso-source-cutover.yml").unlink(missing_ok=True)
