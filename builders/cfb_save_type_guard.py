"""Type-safety guard for CFB persistence rows.

Saved rating snapshots are string-backed in Turso. The live CFB projection can
therefore carry season weights such as ``"0.75"`` even though the model treats
them as numbers. The legacy ``slate_row`` averages those weights only when Save
is pressed, which raises ``TypeError: unsupported operand type(s) for /: 'str'
and 'int'``. Keep the live result untouched and coerce only the persistence copy.
"""
from __future__ import annotations

import copy
from typing import Any


def install_save_type_guard(builder: Any) -> None:
    """Make ``builder.slate_row`` safe for string-backed numeric snapshots."""
    if getattr(builder, "_EZPZ_CFB_SAVE_TYPE_GUARD", False):
        return

    original_slate_row = builder.slate_row

    def safe_slate_row(result: dict[str, Any]):
        safe_result = copy.copy(result)
        projection = copy.copy(result.get("projection", {}))

        for side in ("away", "home"):
            rating = projection.get(side)
            if not isinstance(rating, dict):
                continue
            safe_rating = copy.copy(rating)
            safe_rating["Previous Season Weight"] = builder._num(
                safe_rating.get("Previous Season Weight"), 1.0
            )
            safe_rating["Current Season Weight"] = builder._num(
                safe_rating.get("Current Season Weight"), 0.0
            )
            projection[side] = safe_rating

        safe_result["projection"] = projection
        frame = original_slate_row(safe_result)

        # Market calibration appends mixed bool/int/float/text fields after this
        # wrapper is installed. Object dtype prevents pandas from rejecting those
        # assignments on newer versions while storage still stringifies on write.
        try:
            return frame.astype(object)
        except Exception:
            return frame

    builder.slate_row = safe_slate_row
    builder._EZPZ_CFB_SAVE_TYPE_GUARD = True


__all__ = ["install_save_type_guard"]
