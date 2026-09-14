from pathlib import Path

from shared.mlb_k_runtime_patch import run_mlb_builder_with_locked_k_regression


BUILDER = Path("builders/mlb_builder.py")
PUBLIC_APP = Path("public_app.py")
INIT = Path("shared/__init__.py")
REQUIREMENTS = Path("requirements.txt")


def finalize_mlb_builder():
    # Permanently materialize the currently validated V16.5 runtime logic into
    # the real builder source so there is no launch-time monkey patch anymore.
    source = run_mlb_builder_with_locked_k_regression(BUILDER, compile_only=True)

    strict_start = source.find("def _read_pitcher_recent_form_strict():")
    save_start = source.find("\ndef save_pitcher_recent_form", strict_start)
    if strict_start < 0 or save_start < 0:
        raise RuntimeError("Could not locate V16.5 pitcher-history storage block")
    next_def = source.find("\ndef ", save_start + 5)
    if next_def < 0:
        raise RuntimeError("Could not locate end of V16.5 pitcher-history storage block")

    turso_history = '''def _read_pitcher_recent_form_strict():
    _require_mlb_turso()
    existing = read_dataset("MLB", RECENT_FORM_TAB, RECENT_FORM_COLUMNS)
    if existing is None:
        return pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    out = existing.copy()
    for col in RECENT_FORM_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    return out[RECENT_FORM_COLUMNS].fillna("").astype(object)


def load_pitcher_recent_form():
    return _read_pitcher_recent_form_strict()


def save_pitcher_recent_form(df):
    incoming = df.copy() if df is not None else pd.DataFrame(columns=RECENT_FORM_COLUMNS)
    for col in RECENT_FORM_COLUMNS:
        if col not in incoming.columns:
            incoming[col] = ""
    incoming = incoming[RECENT_FORM_COLUMNS].fillna("").astype(object)

    existing = _read_pitcher_recent_form_strict()
    if not existing.empty and incoming.empty:
        raise RuntimeError("Refusing to replace non-empty pitcher history with an empty dataframe.")

    combined = pd.concat([existing, incoming], ignore_index=True)
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

    out = combined[RECENT_FORM_COLUMNS].fillna("").astype(str)
    replace_dataset("MLB", RECENT_FORM_TAB, out, RECENT_FORM_COLUMNS)
    return True

'''
    source = source[:strict_start] + turso_history + source[next_def + 1:]

    forbidden = [
        "gspread",
        "google.oauth2",
        "GOOGLE_CREDENTIALS",
        "get_or_create_worksheet(",
        "connect_to_sheets(",
        "_read_mlb_turso_fallback",
        "_mirror_mlb_turso",
    ]
    for token in forbidden:
        if token in source:
            raise RuntimeError(f"Legacy Google storage reference remains in permanent MLB builder: {token}")

    compile(source, str(BUILDER), "exec")
    BUILDER.write_text(source, encoding="utf-8")


def finalize_legacy_public_app():
    source = PUBLIC_APP.read_text(encoding="utf-8")
    source = source.replace("import gspread\n", "")
    source = source.replace("from google.oauth2.service_account import Credentials\n", "")
    anchor = "import streamlit as st\n"
    turso_import = "from shared.turso_storage import is_turso_ready, read_dataset\n"
    if turso_import not in source:
        source = source.replace(anchor, anchor + turso_import, 1)

    start = source.find("def get_google_credentials_json():")
    end = source.find("def load_tracker():", start)
    if start < 0 or end < 0:
        raise RuntimeError("Could not locate legacy public Google storage block")

    replacement = '''@st.cache_data(ttl=60)
def read_sheet(tab_name, columns):
    if not is_turso_ready():
        st.error("Turso is not configured or reachable.")
        return pd.DataFrame(columns=columns)
    try:
        df = read_dataset("MLB", str(tab_name), list(columns))
        if df is None or df.empty:
            return pd.DataFrame(columns=columns)
        out = df.copy()
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        return out[list(columns)].copy()
    except Exception as exc:
        st.error(f"Could not read Turso dataset 'MLB/{tab_name}': {exc}")
        return pd.DataFrame(columns=columns)


'''
    source = source[:start] + replacement + source[end:]
    source = source.replace("# Handles Google Sheets dates whether they come in as 2026-04-30,", "# Handles stored dates whether they come in as 2026-04-30,")

    for token in ("gspread", "google.oauth2", "GOOGLE_CREDENTIALS", "GOOGLE_SHEET_NAME", "connect_to_sheets("):
        if token in source:
            raise RuntimeError(f"Legacy Google storage reference remains in public_app.py: {token}")
    compile(source, str(PUBLIC_APP), "exec")
    PUBLIC_APP.write_text(source, encoding="utf-8")


def clean_runtime_dependencies_and_hooks():
    # Importing shared should now have no data-migration or source-rewriting side effects.
    INIT.write_text('''"""Shared EZPZ helpers. Production storage is Turso-only."""\n''', encoding="utf-8")

    requirements = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    requirements = [
        line for line in requirements
        if not line.strip().lower().startswith("gspread")
        and not line.strip().lower().startswith("google-auth")
    ]
    REQUIREMENTS.write_text("\n".join(requirements).rstrip() + "\n", encoding="utf-8")

    for obsolete in [
        "shared/mlb_k_runtime_patch.py",
        "shared/mlb_pitcher_history_recovery.py",
        "scripts/apply_mlb_v165_top2_whiff.py",
    ]:
        Path(obsolete).unlink(missing_ok=True)


def main():
    finalize_mlb_builder()
    finalize_legacy_public_app()
    clean_runtime_dependencies_and_hooks()

    # One-time migration machinery removes itself; only permanent source remains.
    Path("scripts/finalize_turso_cutover.py").unlink(missing_ok=True)
    Path(".github/workflows/finalize-turso-cutover.yml").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
