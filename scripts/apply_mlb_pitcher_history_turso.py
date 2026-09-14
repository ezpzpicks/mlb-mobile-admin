from pathlib import Path

path = Path("shared/mlb_k_runtime_patch.py")
text = path.read_text(encoding="utf-8")

old_read = '''    try:
        values = worksheet.get_all_values()
    except Exception as exc:
        raise RuntimeError(f"Could not safely read persistent pitcher history: {exc}") from exc
'''
new_read = '''    try:
        values = worksheet.get_all_values()
    except Exception as exc:
        fallback = _read_mlb_turso_fallback(RECENT_FORM_TAB, RECENT_FORM_COLUMNS, exc)
        if fallback is not None:
            return fallback
        raise RuntimeError(f"Could not safely read persistent pitcher history: {exc}") from exc
'''
if text.count(old_read) != 1:
    raise SystemExit(f"Expected one strict pitcher-history read block, found {text.count(old_read)}")
text = text.replace(old_read, new_read, 1)

old_write = '''        worksheet.update(values)
        return True
    except Exception as exc:
        st.error(f"Could not safely update persistent pitcher history: {exc}")
'''
new_write = '''        worksheet.update(values)
        _mirror_mlb_turso(RECENT_FORM_TAB, out, RECENT_FORM_COLUMNS)
        return True
    except Exception as exc:
        st.error(f"Could not safely update persistent pitcher history: {exc}")
'''
if text.count(old_write) != 1:
    raise SystemExit(f"Expected one pitcher-history write block, found {text.count(old_write)}")
text = text.replace(old_write, new_write, 1)

path.write_text(text, encoding="utf-8")
print("Applied Turso fallback/mirror to persistent MLB pitcher history.")
