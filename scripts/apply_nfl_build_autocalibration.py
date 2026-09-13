from pathlib import Path


path = Path("builders/nfl_builder.py")
text = path.read_text()
old = '''def _render_build() -> None:\n    _inject_clean_builder_styles()\n    st.subheader("NFL Automated Slate + Prop Builder")\n    st.caption("Opening this page automatically resolves the slate, ratings, current roles, estimated routes, charting/coverage matchups and calibrated player projections.")\n\n    auto_season = _current_nfl_season()\n'''
new = '''def _render_build() -> None:\n    _inject_clean_builder_styles()\n    st.subheader("NFL Automated Slate + Prop Builder")\n    st.caption("Opening this page automatically resolves the slate, ratings, current roles, estimated routes, charting/coverage matchups and calibrated player projections.")\n\n    try:\n        updated, message = _auto_update_prop_tracker()\n        if updated:\n            st.success(message)\n    except Exception as exc:\n        st.warning(f"Automatic completed-projection calibration could not finish: {exc}")\n\n    auto_season = _current_nfl_season()\n'''
if old not in text:
    raise SystemExit("NFL build insertion anchor not found")
path.write_text(text.replace(old, new, 1))
