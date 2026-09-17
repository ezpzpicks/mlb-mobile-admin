from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from builders.cfb_save_type_guard import install_save_type_guard


class FakeBuilder:
    def __init__(self) -> None:
        self._EZPZ_CFB_SAVE_TYPE_GUARD = False

    @staticmethod
    def _num(value, default=0.0):
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def slate_row(result):
        projection = result["projection"]
        prior = round(
            (
                projection["away"]["Previous Season Weight"]
                + projection["home"]["Previous Season Weight"]
            )
            / 2,
            3,
        )
        current = round(
            (
                projection["away"]["Current Season Weight"]
                + projection["home"]["Current Season Weight"]
            )
            / 2,
            3,
        )
        return pd.DataFrame(
            [{"Previous Season Weight": prior, "Current Season Weight": current}]
        )


def main() -> None:
    builder = FakeBuilder()
    result = {
        "projection": {
            "away": {
                "Previous Season Weight": "0.75",
                "Current Season Weight": "0.25",
            },
            "home": {
                "Previous Season Weight": "0.55",
                "Current Season Weight": "0.45",
            },
        }
    }

    install_save_type_guard(builder)
    frame = builder.slate_row(result)

    assert float(frame.iloc[0]["Previous Season Weight"]) == 0.65
    assert float(frame.iloc[0]["Current Season Weight"]) == 0.35
    assert result["projection"]["away"]["Previous Season Weight"] == "0.75"
    assert result["projection"]["home"]["Current Season Weight"] == "0.45"

    # Installation is idempotent across Streamlit reruns.
    guarded = builder.slate_row
    install_save_type_guard(builder)
    assert builder.slate_row is guarded

    print("CFB save type guard smoke test passed")


if __name__ == "__main__":
    main()
