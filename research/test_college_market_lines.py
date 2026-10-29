"""Exercise real Covers markup, college-only routing and save provenance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from shared.college_market_lines import (
    fetch_covers_markets, find_college_market, missing_schedule_games,
    parse_covers_markets,
)
from shared.ncaaf_auto_builder import eligible_markets, match_schedule, save_once
from research.test_ncaaf_auto_builder import database, market_rows, save_fixture, NOW as SAO_NOW

NOW = datetime(2026, 10, 27, 20, tzinfo=timezone.utc)
HTML = (Path(__file__).parent / "fixtures/covers_college_2026_10_27.html").read_text()


def test_tonights_two_games_from_real_markup_and_no_split_fields():
    markets, rejected = parse_covers_markets(HTML, "NCAAF", NOW)
    assert not rejected
    assert [(m.away, m.home, m.kickoff.hour, m.home_spread, m.total) for m in markets] == [
        ("Delaware", "Western Kentucky", 19, -2.5, 55.5),
        ("Sam Houston", "Missouri State", 20, -5.5, 51.5),
    ]
    assert all(m.source == "Covers" and m.opening_home_spread is None and m.opening_total is None for m in markets)
    assert all("bets" not in key.lower() and "money" not in key.lower() for m in markets for key in m.summary())


@pytest.mark.parametrize("sport", ["NCAAB", "NCAAM", "CBB"])
def test_college_basketball_uses_same_verified_card_format(sport):
    markets, rejected = parse_covers_markets(HTML.replace("ncaaf-", "ncaab-"), sport, NOW)
    assert len(markets) == 2 and not rejected
    assert markets[0].source_key == "covers:ncaab:379247"
    assert "/basketball/ncaab/" in markets[0].source_url


def test_covers_display_abbreviation_uses_linked_full_team_identity():
    # Real Covers cards label Jacksonville State as JSU in the headline; JSU
    # must not be guessed as Jackson State or left unmatched to the schedule.
    html = HTML.replace("Delaware @ Western Kentucky", "JSU @ New Mexico State")
    html = html.replace('alt="Delaware"', 'alt="Jacksonville State"')
    html = html.replace('alt="Western Kentucky"', 'alt="New Mexico State"')
    markets, rejected = parse_covers_markets(html, "NCAAF", NOW)
    assert not rejected
    market = next(m for m in markets if m.home == "New Mexico State")
    assert market.away == "Jacksonville State"
    row = {"Game ID": "401871057", "Season": "2026", "Week": "9",
           "Away Team": "Jacksonville State", "Home Team": "New Mexico State",
           "Game Time": market.kickoff.isoformat()}
    assert match_schedule(market, [row])["Game ID"] == "401871057"


@pytest.mark.parametrize("sport", ["NBA", "NFL", "MLB", "NHL", "WNBA", ""])
def test_professional_sports_are_rejected_before_network(sport, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: pytest.fail("Professional sport made a network request"))
    with pytest.raises(ValueError, match="restricted"):
        fetch_covers_markets(sport, NOW)


@pytest.mark.parametrize("before,after", [
    ("o/u 55.5", "o/u --"), ("+2.5", "+3.5"),
    ("7:00 PM ET", "TBA"), ("7:00 PM ET", "7:00 PM"),
])
def test_partial_or_inconsistent_card_does_not_block_other_game(before, after):
    markets, rejected = parse_covers_markets(HTML.replace(before, after), "NCAAF", NOW)
    assert [m.home for m in markets] == ["Missouri State"]
    assert len(rejected) == 1


def test_stale_page_and_started_games_are_excluded():
    with pytest.raises(ValueError, match="stale"):
        parse_covers_markets(HTML, "NCAAF", NOW, captured_at=NOW - timedelta(minutes=91))
    assert not parse_covers_markets(HTML, "NCAAF", NOW + timedelta(hours=4))[0]
    assert not parse_covers_markets(HTML, "NCAAF", NOW + timedelta(days=1))[0]


def test_duplicate_matchup_fails_closed():
    markets, rejected = parse_covers_markets(HTML + HTML, "NCAAF", NOW)
    assert not markets and len(rejected) == 2


def test_pick_em_zero_spread_is_valid():
    markets, _ = parse_covers_markets(HTML.replace("+2.5", "PK").replace("-2.5", "PK"), "NCAAF", NOW)
    assert markets[0].home_spread == 0


def schedule(markets):
    return [{"Game ID": str(i + 1), "Season": "2026", "Week": "9", "Away Team": m.away,
             "Home Team": m.home, "Game Time": m.kickoff.isoformat()} for i, m in enumerate(markets)]


def test_fallback_only_targets_missing_unsaved_schedule_games():
    markets, _ = parse_covers_markets(HTML, "NCAAF", NOW)
    rows = schedule(markets)
    assert len(missing_schedule_games([], rows, [], NOW)) == 2
    assert missing_schedule_games(markets, rows, [], NOW) == []
    assert missing_schedule_games([], rows, [{"Game ID": "1"}, {"Game ID": "2"}], NOW) == []
    assert missing_schedule_games([], [{**row, "Completed": "True"} for row in rows], [], NOW) == []


def test_covers_resolves_midnight_schedule_and_retains_kickoff_gate():
    markets, _ = parse_covers_markets(HTML, "NCAAF", NOW)
    rows = [{**row, "Game Time": "2026-10-27"} for row in schedule(markets)]
    assert len(missing_schedule_games([], rows, [], NOW)) == 2
    for market in markets:
        assert match_schedule(market, rows)["Game Time"] == market.kickoff.isoformat()
    with pytest.raises(ValueError, match="unique"):
        match_schedule(markets[0], [{**rows[0], "Game Time": "2026-10-27T15:00:00-04:00"}])


def test_retained_scoresandodds_has_priority():
    market = find_college_market("NCAAB", market_rows(), "Away", "Home", SAO_NOW,
                                 covers_loader=lambda *_a: pytest.fail("Fresh saved lines should avoid Covers"))
    assert market.source == "ScoresAndOdds" and market.home_spread == -3.5


def test_manual_ncaab_load_matches_only_requested_pair():
    markets, _ = parse_covers_markets(HTML.replace("ncaaf-", "ncaab-"), "NCAAB", NOW)
    market = find_college_market("NCAAB", [], "Sam Houston State", "Missouri State", NOW,
                                 covers_loader=lambda *_a: (markets, []))
    assert market.home_spread == -5.5
    with pytest.raises(ValueError, match="No complete"):
        find_college_market("NCAAB", [], "Other", "Missouri State", NOW,
                            covers_loader=lambda *_a: (markets, []))


def test_cached_http_page_cannot_be_relabelled_as_fresh(monkeypatch):
    import requests
    from types import SimpleNamespace
    response = SimpleNamespace(content=HTML.encode(), text=HTML,
                               headers={"Age": "7200"}, raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: response)
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="stale"):
        fetch_covers_markets("NCAAF", now)


def test_covers_saves_source_and_price_assumption_without_split_history(database):
    db, connection, seen = database
    builder, result, market = save_fixture()
    market = replace(market, source="Covers", source_url="https://www.covers.com/sports/ncaaf/matchups")
    assert save_once(db, builder, result, market, now=SAO_NOW)
    saved = connection.execute("SELECT dataset,payload_json FROM dataset_rows").fetchall()
    for dataset, payload in saved:
        row = json.loads(payload)
        assert row["Build Source"] == "Scheduled Covers builder"
        assert row["Market Source URL"] == market.source_url
        if "Notes" in row:
            assert "Covers lines" in row["Notes"] and "ScoresAndOdds" not in row["Notes"]
    assert not any(dataset in {"weekly_market_trends", "odds_snapshot"} for dataset, _ in saved)
    written = db.rows_written
    assert not save_once(db, builder, result, market, now=SAO_NOW)
    assert db.rows_written == written


@pytest.mark.parametrize("saved,network_fails", [(False, False), (True, False), (False, True)])
def test_scheduled_plan_requests_one_slate_only_for_unbuilt_games(monkeypatch, capsys, saved, network_fails):
    from types import SimpleNamespace
    from scripts import run_ncaaf_builder as runner
    from shared import turso_storage as turso

    markets, _ = parse_covers_markets(HTML, "NCAAF", NOW)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    class FakeDB:
        def rows(self, dataset, *args, **kwargs):
            if dataset == "schedule": return schedule(markets)
            if dataset == "builder_completed" and saved: return [{"Game ID": "1"}, {"Game ID": "2"}]
            return []
        def query(self, sql): return []
        def usage(self): return {"rows_written": 0}
    calls = []
    def load(sport, now):
        calls.append(sport)
        if network_fails: raise TimeoutError("test timeout")
        return markets, []
    monkeypatch.setattr(runner, "datetime", Clock)
    monkeypatch.setattr(runner, "Database", lambda *_a: FakeDB())
    monkeypatch.setattr(runner, "fetch_covers_markets", load)
    monkeypatch.setattr(turso, "is_turso_ready", lambda: True)
    assert runner.run(SimpleNamespace(ignore_window=False, commit=False, evaluate=False, limit=100)) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert calls == ([] if saved else ["NCAAF"])
    if not saved and not network_fails:
        plan = next(event for event in events if event["event"] == "plan")
        assert plan["pending"] == 2
        assert {game["source"] for game in plan["games"]} == {"Covers"}
    if network_fails:
        assert any(event["event"] == "covers_fallback_unavailable" for event in events)
