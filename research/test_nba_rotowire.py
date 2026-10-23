"""Public HTML source contracts, identity matching, locked rotations and runner I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html import escape
from types import SimpleNamespace
import os
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from builders.nba_automation import ROTOWIRE_POLICY, ROTOWIRE_VERSIONS, automated_package
from builders.nba_model import InputError, evaluate_package
from builders.nba_rotowire import URL, fetch_rotowire_lineups, parse_rotowire_html, prepare_rotowire_package
from builders.nba_tracking import lock_prediction
from builders.nba_market import apply_market
from nba_market_fixtures import market_rows
from test_nba_live import NOW, fixture
from test_nba_builder import FakeStreamlit, load_builder
import test_nba_automation as automatic_tests
from scripts import run_nba_builder as runner


def inputs_fixture(now=NOW):
    bundle, inputs, old = fixture(now)
    for side, team, name in zip(("away", "home"), bundle["teams"], ("Denver Nuggets", "Oklahoma City Thunder")):
        team["name"] = inputs["game"][side]["name"] = inputs["rosters"][side]["name"] = name
    return bundle, inputs, old


def html_fixture(inputs, *, confirmed=False, out=True, questionable=True):
    tipoff = datetime.fromisoformat(inputs["game"]["tipoff"]).astimezone(ZoneInfo("America/New_York"))
    lists, teams = [], []
    for side, css, abbr in (("away", "is-visit", "DEN"), ("home", "is-home", "OKC")):
        teams.append(f'<a class="lineup__team {css}"><div class="lineup__abbr">{abbr}</div></a>')
        label = "Confirmed" if confirmed else "Expected"
        rows = [f'<li class="lineup__status is-{label.lower()}">{label} Lineup</li>']
        def player(index, injury=""):
            name = inputs["rosters"][side]["players"][index]["name"]
            pid = index + 1 + (100 if side == "home" else 0)
            return f'<li class="lineup__player"><a title="{escape(name)}" href="/basketball/player/test-person-{pid}">Abbreviated</a>' + (f'<span class="lineup__inj">{injury}</span>' if injury else '') + '</li>'
        rows += [player(i, "Ques" if questionable and side == "away" and i == 0 else "") for i in range(5)]
        rows.append('<li class="lineup__title is-middle">MAY NOT PLAY</li>')
        if questionable and side == "away": rows.append(player(0, "Ques"))
        if out: rows.append(player(8, "Out"))
        lists.append(f'<ul class="lineup__list {css}">' + ''.join(rows) + '</ul>')
    return f'<div class="page-title__secondary">Starting lineups for {tipoff:%B} {tipoff.day}, {tipoff.year}</div><div class="lineups"><div class="lineup is-nba" data-lnum="1"><div class="lineup__time">{tipoff:%I:%M %p} ET</div>' + ''.join(teams + lists) + '</div></div>'


def report_fixture(inputs, now=NOW, **kw):
    return parse_rotowire_html(html_fixture(inputs, **kw), fetched_at=now)


def prepared(*, confirmed=False):
    bundle, inputs, _ = inputs_fixture()
    report = report_fixture(inputs, confirmed=confirmed)
    package = prepare_rotowire_package(bundle, inputs, report, as_of=NOW)
    return apply_market(package, market_rows(inputs["game"], NOW), inputs["game"], NOW), inputs["game"]


class RotowireHtmlTests(unittest.TestCase):
    def test_expected_confirmed_full_names_own_ids_and_questionable_duplicates(self):
        _, inputs, _ = inputs_fixture()
        for confirmed in (False, True):
            report = report_fixture(inputs, confirmed=confirmed)
            self.assertEqual(len(report["games"]), 1)
            away = report["games"][0]["away"]
            self.assertEqual(away["confirmed"], confirmed)
            self.assertEqual(len(away["starters"]), 5)
            self.assertEqual(len(away["players"]), 6)
            first = next(p for p in away["players"] if p["player_id"] == "rotowire:1")
            self.assertEqual(first["name"], inputs["rosters"]["away"]["players"][0]["name"])
            self.assertEqual(first["status"], "QUESTIONABLE")
            self.assertEqual(away["players"][-1]["status"], "OUT")

    def test_login_walls_missing_dates_and_incomplete_markups_are_not_healthy_empty_reports(self):
        _, inputs, _ = inputs_fixture()
        html = html_fixture(inputs)
        for text in ('<html>Sign in</html>', html.replace('page-title__secondary', 'changed'), html.replace('data-lnum="1"', 'data-other="1"')):
            with self.subTest(text=text[:40]), self.assertRaises(InputError): parse_rotowire_html(text, fetched_at=NOW)

    def test_malformed_players_unknown_statuses_and_conflicting_starters_reject_the_matchup(self):
        _, inputs, _ = inputs_fixture()
        original = html_fixture(inputs)
        for html in (original.replace('>Ques<', '>Out<'), original.replace('>Ques<', '>NewStatus<'),
                     original.replace('title="Fictional A Player 1"', 'title="F. Player"'),
                     original.replace('is-expected', 'is-unknown'), original.replace('test-person-1"', 'test-person-2"')):
            self.assertEqual(parse_rotowire_html(html, fetched_at=NOW)["games"], [])

    def test_out_for_season_and_doubtful_remain_distinct_reported_designations(self):
        _, inputs, _ = inputs_fixture()
        for label, status in (("OFS", "OUT"), ("Doubt", "DOUBTFUL"), ("Prob", "PROBABLE")):
            r = parse_rotowire_html(html_fixture(inputs).replace('>Out<', f'>{label}<'), fetched_at=NOW)
            self.assertEqual(r["games"][0]["home"]["players"][-1]["status"], status)

    def test_fetch_is_bounded_single_public_html_request_and_failures_are_not_retried(self):
        _, inputs, _ = inputs_fixture()
        response = MagicMock(); response.__enter__.return_value = response
        response.read.return_value = html_fixture(inputs).encode(); response.status = 200; response.geturl.return_value = URL
        with patch('builders.nba_rotowire.urlopen', return_value=response) as request:
            self.assertEqual(len(fetch_rotowire_lineups()["games"]), 1)
            request.assert_called_once()
            self.assertEqual(request.call_args.args[0].full_url, URL)
        for failure in (OSError('offline'), PermissionError('HTTP 403')):
            with patch('builders.nba_rotowire.urlopen', side_effect=failure) as request:
                with self.assertRaises(InputError): fetch_rotowire_lineups()
                request.assert_called_once()
        response.read.return_value = b'x' * 2_000_001
        with patch('builders.nba_rotowire.urlopen', return_value=response):
            with self.assertRaisesRegex(InputError, 'size limit'): fetch_rotowire_lineups()


class RotowireRotationTests(unittest.TestCase):
    def test_out_minutes_redistribute_to_240_bench_players_remain_unknown_and_healthy_is_never_inferred(self):
        package, _ = prepared()
        for side in ("away", "home"):
            plan = package["scenarios"][0][side]
            self.assertEqual(plan["players"][8]["status"], "OUT")
            self.assertEqual(plan["players"][8]["minutes"], 0)
            self.assertEqual(plan["players"][6]["status"], "UNKNOWN")
            self.assertGreater(plan["players"][6]["minutes"], 0)
            self.assertEqual(sum(p["starter"] for p in plan["players"]), 5)
            self.assertAlmostEqual(sum(p["minutes"] for p in plan["players"]), 240)
            self.assertFalse(plan["report_complete"])
        self.assertNotIn('injury_provenance', package)

    def test_confirmed_questionable_starter_has_in_and_out_scenarios_without_false_replacement_confirmation(self):
        package, _ = prepared(confirmed=True)
        automatic = automated_package(package, 'early')
        self.assertTrue(automatic["scenarios"][0]["away"]["starters_confirmed"])
        alternative = automatic["scenarios"][1]["away"]
        self.assertEqual(alternative["players"][0]["minutes"], 0)
        self.assertFalse(alternative["starters_confirmed"])
        self.assertFalse(any(p["starter"] for p in alternative["players"]))
        projections = evaluate_package(automatic)["scenarios"]
        self.assertTrue(projections[0]["starters_confirmed"])
        self.assertFalse(projections[1]["starters_confirmed"])

    def test_matching_rejects_other_dates_tipoffs_reversed_teams_stale_reports_and_unmatched_roster_names(self):
        bundle, inputs, _ = inputs_fixture()
        original = report_fixture(inputs)
        for change in ('date', 'time', 'teams', 'stale', 'future', 'name', 'duplicate'):
            report = deepcopy(original)
            if change == 'date': report['date'] = '2026-10-21'
            if change == 'time': report['games'][0]['tipoff'] = (NOW + timedelta(hours=3)).isoformat()
            if change == 'teams': report['games'][0]['away'], report['games'][0]['home'] = report['games'][0]['home'], report['games'][0]['away']
            if change == 'stale': report['fetched_at'] = (NOW-timedelta(minutes=61)).isoformat()
            if change == 'future': report['fetched_at'] = (NOW+timedelta(seconds=1)).isoformat()
            if change == 'name': report['games'][0]['away']['players'][0]['name'] = 'Not On This Roster'
            if change == 'duplicate': report['games'].append(deepcopy(report['games'][0]))
            with self.subTest(change=change), self.assertRaises(InputError): prepare_rotowire_package(bundle, inputs, report, as_of=NOW)

    def test_locks_are_separate_source_versions_and_keep_exact_html_source_provenance(self):
        package, game = prepared(confirmed=True)
        package = automated_package(package, 'early')
        record = lock_prediction(package, package['automation']['primary_scenario'], game, locked_at=NOW,
            roster_reviewed=False, minutes_reviewed=False, review_note=package['automation']['assumptions'], automation_policy=ROTOWIRE_POLICY)
        self.assertEqual(record['model_version'], ROTOWIRE_VERSIONS['early'])
        self.assertEqual(record['selected']['availability_mode'], 'rotowire_html')
        self.assertTrue(record['selected']['injury_verified'])
        self.assertEqual(record['selected']['availability_source'], 'RotoWire')
        self.assertEqual(record['inputs']['availability_provenance']['source_url'], URL)
        self.assertFalse(record['eligible_for_posting'])

    def test_tampered_status_confirmation_and_policy_cannot_be_locked(self):
        original, game = prepared()
        for change in ('status', 'confirmed', 'source', 'policy'):
            p = automated_package(original, 'early')
            if change == 'status': p['scenarios'][0]['away']['players'][8]['status'] = 'AVAILABLE'
            if change == 'confirmed': p['scenarios'][0]['away']['starters_confirmed'] = True
            if change == 'source': p['availability_provenance']['source_sha256'] = '0'*64
            with self.subTest(change=change), self.assertRaises(InputError):
                lock_prediction(p, p['automation']['primary_scenario'], game, locked_at=NOW, roster_reviewed=False, minutes_reviewed=False,
                    review_note='Source contract test', automation_policy='nba-auto-market-v2' if change == 'policy' else ROTOWIRE_POLICY)

    def test_off_roster_out_entries_are_audited_without_inventing_or_removing_a_current_player(self):
        bundle, inputs, _ = inputs_fixture()
        report = report_fixture(inputs)
        report['games'][0]['away']['players'].append({'player_id':'rotowire:99999', 'name':'Departed Player',
            'status':'OUT', 'source_status':'Out', 'source_url':URL})
        package = prepare_rotowire_package(bundle, inputs, report, as_of=NOW)
        ignored = package['availability_provenance']['audit']['away']['ignored_off_roster_out']
        self.assertEqual(ignored[0]['name'], 'Departed Player')
        self.assertAlmostEqual(sum(p['minutes'] for p in package['scenarios'][0]['away']['players']), 240)


class RotowireRunnerTests(unittest.TestCase):
    def test_read_only_diagnostic_needs_no_database_and_calls_no_espn_injury_feed(self):
        _, inputs, _ = inputs_fixture()
        with patch.object(runner, 'fetch_rotowire_lineups', return_value=report_fixture(inputs)), patch.object(runner, 'emit'), \
             patch('shared.turso_storage.is_turso_ready', side_effect=AssertionError('No database in diagnostic')), \
             patch.object(runner, 'fetch_espn_injuries', side_effect=AssertionError('No ESPN injury request')):
            self.assertEqual(runner.run(SimpleNamespace(check_rotowire=True)), 0)

    def test_actual_sqlite_run_saves_roto_once_per_phase_and_repeats_fetch_no_source(self):
        harness = automatic_tests.ScheduledPersistenceTests(); harness.setUp()
        harness.tipoff = harness.tipoff.replace(second=0, microsecond=0)
        try:
            def source(**kwargs):
                now = runner.clock(); _, inputs, _ = inputs_fixture(now)
                inputs['game']['tipoff'] = harness.tipoff.isoformat()
                return report_fixture(inputs, now)
            with patch.dict(os.environ, {'NBA_AVAILABILITY_MODE':'rotowire_html'}), \
                 patch('test_nba_automation.fixture', side_effect=inputs_fixture), \
                 patch.object(runner, 'fetch_rotowire_lineups', side_effect=source) as read:
                self.assertEqual(harness.run_at(harness.now)[:3], (0, 1, 0), harness.events)
                self.assertEqual(harness.run_at(harness.now + timedelta(minutes=30))[:3], (0, 0, 0))
                self.assertEqual(harness.run_at(harness.now + timedelta(minutes=90))[:3], (0, 1, 0))
                self.assertEqual(read.call_count, 2)
                from shared.nba_tracking_storage import load_ledger
                records = load_ledger()[0]
                self.assertEqual({r['model_version'] for r in records}, set(ROTOWIRE_VERSIONS.values()))
                with patch.object(runner, 'fetch_rotowire_lineups', side_effect=InputError('HTML unavailable')):
                    self.assertEqual(harness.run_at(harness.now + timedelta(hours=5), final=True)[:3], (0, 0, 0))
                self.assertEqual(len(load_ledger()[1]), 1)
        finally: harness.tearDown()

    def test_ui_fetch_uses_roto_and_preserves_original_observation_on_failure(self):
        _, inputs, _ = inputs_fixture()
        st = FakeStreamlit(); st.button = lambda label, **kw: label == 'Fetch latest RotoWire lineups'
        builder = load_builder(st)
        with patch('builders.nba_rotowire.fetch_rotowire_lineups', return_value=report_fixture(inputs)), \
             patch('builders.nba_injuries.fetch_espn_injuries', side_effect=AssertionError('No ESPN injuries')):
            builder.render()
        state = st.session_state[builder.STATE_KEY]; old = state['rotowire_report']
        state['result'] = {'old':'forecast'}
        with patch('builders.nba_rotowire.fetch_rotowire_lineups', side_effect=InputError('HTTP 403')): builder.render()
        self.assertIs(state['rotowire_report'], old)
        self.assertIsNone(state['result'])


if __name__ == '__main__': unittest.main()
