from pathlib import Path

path = Path("builders/mlb_builder.py")
text = path.read_text(encoding="utf-8")

marker = "\ndef read_sheet(tab_name, columns):\n"
helpers = '''\ndef _read_mlb_turso_fallback(tab_name, columns, read_error=None):
    """Use the last mirrored MLB dataset only when the authoritative Sheet read fails."""
    try:
        from shared.turso_storage import is_turso_ready, read_dataset

        if not is_turso_ready():
            return None
        fallback = read_dataset("MLB", str(tab_name), list(columns))
        if fallback is None:
            return None
        for col in columns:
            if col not in fallback.columns:
                fallback[col] = ""
        print(
            f"[turso-read-fallback] MLB/{tab_name}: {len(fallback)} rows"
            + (f" after Sheets error: {read_error}" if read_error else "")
        )
        return fallback[list(columns)].astype(object)
    except Exception as turso_error:
        print(f"[turso-read-fallback] MLB/{tab_name} failed: {turso_error}")
        return None


def _mirror_mlb_turso(tab_name, dataframe, columns):
    """Mirror a successful MLB Sheet write while Sheets remains authoritative."""
    try:
        from shared.turso_storage import is_turso_ready, replace_dataset

        if not is_turso_ready():
            return
        saved = replace_dataset("MLB", str(tab_name), dataframe, list(columns))
        print(f"[turso-dual-write] MLB/{tab_name}: mirrored {saved} rows")
    except Exception as turso_error:
        # Migration safety rule: Turso must never make a successful Sheets save fail.
        print(f"[turso-dual-write] MLB/{tab_name} mirror failed; Sheets remains authoritative: {turso_error}")


def read_sheet(tab_name, columns):
'''
if text.count(marker) != 1:
    raise SystemExit(f"Expected exactly one MLB read_sheet marker, found {text.count(marker)}")
text = text.replace(marker, helpers, 1)

old_read_failure = '''    except Exception as e:
        st.error(f"Could not read Google Sheet tab '{tab_name}': {e}")
        return pd.DataFrame(columns=columns)


def write_sheet(tab_name, df, columns):
'''
new_read_failure = '''    except Exception as e:
        fallback = _read_mlb_turso_fallback(tab_name, columns, e)
        if fallback is not None:
            return fallback
        st.error(f"Could not read Google Sheet tab '{tab_name}': {e}")
        return pd.DataFrame(columns=columns)


def write_sheet(tab_name, df, columns):
'''
if text.count(old_read_failure) != 1:
    raise SystemExit(f"Expected exactly one MLB read failure block, found {text.count(old_read_failure)}")
text = text.replace(old_read_failure, new_read_failure, 1)

old_write = '''def write_sheet(tab_name, df, columns):
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)

        out = df.copy() if df is not None else pd.DataFrame(columns=columns)
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        out = out[columns]
        out = out.fillna("").astype(str)

        worksheet.clear()
        values = [columns] + out.values.tolist()
        worksheet.update(values)
        return True
    except Exception as e:
        st.error(f"Could not write Google Sheet tab '{tab_name}': {e}")
        return False
'''
new_write = '''def write_sheet(tab_name, df, columns):
    try:
        worksheet = get_or_create_worksheet(tab_name, columns)

        out = df.copy() if df is not None else pd.DataFrame(columns=columns)
        for col in columns:
            if col not in out.columns:
                out[col] = ""
        out = out[columns]
        out = out.fillna("").astype(str)

        worksheet.clear()
        values = [columns] + out.values.tolist()
        worksheet.update(values)
        _mirror_mlb_turso(tab_name, out, columns)
        return True
    except Exception as e:
        st.error(f"Could not write Google Sheet tab '{tab_name}': {e}")
        return False
'''
if text.count(old_write) != 1:
    raise SystemExit(f"Expected exactly one MLB write_sheet block, found {text.count(old_write)}")
text = text.replace(old_write, new_write, 1)

path.write_text(text, encoding="utf-8")
print("Applied permanent MLB Sheets-to-Turso dual-write/fallback storage changes.")
