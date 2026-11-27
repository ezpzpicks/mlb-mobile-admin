from datetime import datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest

from builders import nfl_automation as auto, nfl_covers as covers
from builders.nfl_builder import _weather_adjustment
from scripts import run_nfl_builder as runner

GAME = {"Away Team": "KC", "Home Team": "BUF", "Season": 2026, "Week": 12}


def card(**changes):
    return {"fetched_at": datetime.now(timezone.utc).timestamp(), "temperature": 30,
            "wind": 20, "precipitation": "Rain", "condition_supported": True, **changes}


@pytest.mark.parametrize("observation,status", [
    ({}, "unavailable"), (card(wind="broken"), "unavailable"),
    (card(fetched_at=1), "unavailable"), (card(condition_supported=False), "unavailable"),
    (card(), "available"),
])
def test_outdoor(monkeypatch, observation, status):
    monkeypatch.setattr(covers, "_match_weather", lambda a, h: observation)
    result = runner.environment(None, GAME)
    assert result["status"] == status and result["roof"] is None
    if status == "available":
        assert _weather_adjustment(None, result["temperature"], result["wind"], result["precipitation"]) == -3.7
    else:
        assert result["reason"]
    if not observation or observation.get("fetched_at") == 1:
        assert all(result[key] is None for key in ("temperature", "wind", "precipitation"))


def test_enclosed(monkeypatch):
    monkeypatch.setattr(covers, "_match_weather", Mock(side_effect=AssertionError))
    result = runner.environment(None, {**GAME, "Roof": "dome"})
    assert result["status"] == "enclosed"
    assert all(result[key] is None for key in ("temperature", "wind", "precipitation"))


def test_pregame_calls_projection_despite_provider_outage(monkeypatch):
    monkeypatch.setattr(covers, "_match_weather", Mock(side_effect=OSError))
    builder = Mock()
    builder._finalize_lineup.return_value = (pd.DataFrame([{"Slot": "QB", "Player": "Starter"}]), {})
    builder._reliability.return_value = (80, 80, 80)
    builder._automatic_home_field_advantage.return_value = {"value": 2}
    builder._project_matchup.side_effect = RuntimeError("projection reached")
    now = datetime.now(timezone.utc)
    depth = pd.DataFrame([dict(team_norm=t, name_norm="Starter", espn_injury_status="Active",
        espn_auto_probability=1, espn_injury="") for t in ("KC", "BUF")])
    monkeypatch.setattr(auto, "checked_depth", lambda *args: depth)
    args = (builder, {**GAME, "Date": "2026-11-26"}, Mock(), {"ratings": {}, "player_values": {}}, depth, {})
    with pytest.raises(RuntimeError, match="projection reached"):
        runner.compute(*args, now=now, phase="pregame", depth_at=now.isoformat())
    assert builder._project_matchup.call_args.args[-1]["weather_total_adjustment"] == 0
    builder._weather_adjustment.assert_not_called()
    with pytest.raises(auto.SourceError, match="stale"):
        runner.compute(*args, now=now, phase="pregame", depth_at="2000-01-01T00:00:00+00:00")
