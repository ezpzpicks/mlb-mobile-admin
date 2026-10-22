"""Pregame lock, result grading and append-only SQLite protocol tests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from builders.nba_live import digest
from builders.nba_model import InputError
from builders.nba_tracking import (LOCK_SCHEMA, _seal, export_ledger, grade_prediction,
    lock_prediction, result_observation, tracking_report, unique_predictions, verify_record)
from shared.nba_tracking_storage import load_ledger, save_observation, save_prediction
from test_nba_live import NOW, live_package


def locked(now=NOW):
    package, game = live_package(now)
    return lock_prediction(package, package['scenarios'][0]['name'], game, locked_at=now,
        roster_reviewed=True, minutes_reviewed=True, review_note="Synthetic test: unlisted players assumed IN; minute seed reviewed.")


def final_observation(record, *, home=115, away=110, observed_at=None):
    game = deepcopy(record['verification'])
    game.update(completed=True, status="STATUS_FINAL", state="post", period=4)
    game['home']['score'], game['away']['score'] = home, away
    game['source']['observed_at'] = observed_at or (datetime.fromisoformat(record['tipoff']) + timedelta(hours=3)).isoformat()
    return result_observation(game)


class PregameLockTests(unittest.TestCase):
    def test_explicit_forecast_and_all_inputs_are_archived_as_conditional(self):
        record = locked()
        verify_record(record, LOCK_SCHEMA)
        self.assertEqual(record['selected_scenario'], record['selected']['scenario'])
        self.assertEqual(record['selected']['snapshot_id'], record['projection']['scenarios'][0]['snapshot_id'])
        self.assertFalse(record['selected']['lineup_ready'])
        self.assertFalse(record['eligible_for_posting'])
        self.assertIn('live_provenance', record['inputs'])
        self.assertIn('injury_provenance', record['inputs'])
        self.assertTrue(json.loads(export_ledger([record], []))['predictions'])

    def test_server_time_blocks_after_tipoff_future_cutoff_and_stale_forecast(self):
        package, game = live_package()
        for time in (NOW - timedelta(seconds=1), NOW + timedelta(minutes=61), NOW + timedelta(hours=2)):
            with self.assertRaises(InputError):
                lock_prediction(package, package['scenarios'][0]['name'], game, locked_at=time,
                    roster_reviewed=True, minutes_reviewed=True, review_note="reviewed")

    def test_live_and_postponed_games_and_changed_tipoff_block_lock(self):
        package, game = live_package()
        for change in ('started', 'postponed', 'tipoff', 'team'):
            current = deepcopy(game)
            if change == 'started': current.update(status='STATUS_IN_PROGRESS', state='in', period=1)
            elif change == 'postponed': current['status'] = 'STATUS_POSTPONED'
            elif change == 'tipoff': current['tipoff'] = (NOW + timedelta(hours=3)).isoformat()
            else: current['home']['espn_team_id'] = '999'
            with self.assertRaises(InputError):
                lock_prediction(package, package['scenarios'][0]['name'], current, locked_at=NOW,
                    roster_reviewed=True, minutes_reviewed=True, review_note="reviewed")

    def test_fresh_status_and_review_acknowledgements_are_required(self):
        package, game = live_package()
        for overrides in ({'roster_reviewed': False}, {'minutes_reviewed': False}, {'review_note': ''}):
            args = dict(locked_at=NOW, roster_reviewed=True, minutes_reviewed=True, review_note='reviewed')
            args.update(overrides)
            with self.assertRaises(InputError): lock_prediction(package, package['scenarios'][0]['name'], game, **args)
        game['source']['observed_at'] = (NOW - timedelta(minutes=3)).isoformat()
        with self.assertRaisesRegex(InputError, 'Refresh game status'):
            lock_prediction(package, package['scenarios'][0]['name'], game, locked_at=NOW,
                roster_reviewed=True, minutes_reviewed=True, review_note='reviewed')

    def test_old_roster_stale_injury_future_minutes_or_unmatched_report_blocks(self):
        original, game = live_package()
        for change in ('roster', 'injury', 'unmatched', 'minutes', 'roster_conflict'):
            package = deepcopy(original)
            if change == 'roster': package['live_provenance']['rosters']['away']['source']['observed_at'] = (NOW - timedelta(days=2)).isoformat()
            elif change == 'injury': package['injury_provenance']['feed_as_of'] = (NOW - timedelta(hours=2)).isoformat()
            elif change == 'unmatched': package['injury_provenance']['audit']['unmatched'] = [{'player': 'Test unresolved'}]
            elif change == 'roster_conflict': package['live_provenance']['conflicts'] = ['Synthetic conflict']
            else: package['live_provenance']['recent']['away']['games'] = [{'game': {**game, 'tipoff': (NOW + timedelta(hours=1)).isoformat()}, 'players': []}]
            with self.assertRaises(InputError):
                lock_prediction(package, package['scenarios'][0]['name'], game, locked_at=NOW,
                    roster_reviewed=True, minutes_reviewed=True, review_note='reviewed')

    def test_demo_imports_departed_positive_minutes_and_unknown_scenario_block(self):
        package, game = live_package()
        for change in ('demo', 'import', 'departed', 'scenario'):
            p = deepcopy(package)
            name = p['scenarios'][0]['name']
            if change == 'demo': p['is_demo'] = True
            elif change == 'import': p.pop('live_provenance')
            elif change == 'departed': p['live_provenance']['identity']['away'].pop('1')
            else: name = 'not entered before tipoff'
            with self.assertRaises(InputError):
                lock_prediction(p, name, game, locked_at=NOW,
                    roster_reviewed=True, minutes_reviewed=True, review_note='reviewed')

    def test_editing_an_archived_record_breaks_its_fingerprint(self):
        record = locked()
        record['selected']['total'] += 10
        with self.assertRaisesRegex(InputError, 'fingerprint'): verify_record(record, LOCK_SCHEMA)


class ScoringTests(unittest.TestCase):
    def test_pending_and_final_use_same_frozen_forecast_and_team_control(self):
        record = locked()
        before = deepcopy(record)
        self.assertEqual(grade_prediction(record, None)['status'], 'PENDING')
        final = final_observation(record, home=115, away=110)
        graded = grade_prediction(record, final)
        self.assertEqual(graded['margin_error'], record['selected']['home_margin'] - 5)
        self.assertEqual(graded['baseline_total_error'], record['selected']['baseline_total'] - 225)
        self.assertEqual(graded['spread_result'], 'NO LINE')
        self.assertEqual(record, before)

    def test_market_signs_win_loss_push_pass_and_no_line(self):
        record = locked()
        for edge, cover, expected in ((2, 1, 'WIN'), (2, -1, 'LOSS'), (2, 0, 'PUSH'), (0, 1, 'PASS')):
            candidate = deepcopy(record)
            candidate['selected'].update(home_margin=4 + edge, total=224 + edge,
                                         market={'home_spread': -4, 'total': 224})
            candidate = _seal({k:v for k,v in candidate.items() if k != 'record_sha256'})
            graded = grade_prediction(candidate, final_observation(candidate, home=114+cover, away=110))
            self.assertEqual(graded['spread_result'], expected)
            self.assertEqual(graded['total_result'], expected)

    def test_later_status_observation_can_hold_settlement_without_rewriting_prediction(self):
        record = locked()
        final = final_observation(record)
        later_game = deepcopy(final['game'])
        later_game.update(completed=False, status='STATUS_POSTPONED', state='pre')
        later_game['source']['observed_at'] = (datetime.fromisoformat(final['observed_at']) + timedelta(minutes=5)).isoformat()
        report = tracking_report([record], [final, result_observation(later_game)])
        self.assertEqual(report['summary'][0]['graded_games'], 0)
        self.assertEqual(report['games'][0]['status'], 'STATUS_POSTPONED')

    def test_wrong_game_and_corrected_earlier_tipoff_are_not_silently_graded(self):
        record = locked()
        final = final_observation(record)
        other = deepcopy(final['game']); other['game_id'] = '999999'
        with self.assertRaises(InputError): grade_prediction(record, result_observation(other))
        earlier = deepcopy(final['game']); earlier['tipoff'] = (NOW - timedelta(minutes=1)).isoformat()
        self.assertEqual(grade_prediction(record, result_observation(earlier))['status'], 'EXCLUDED: start-time conflict')

    def test_duplicate_copies_count_once_and_conflicting_predictions_raise(self):
        record = locked()
        report = tracking_report([record, deepcopy(record)], [final_observation(record)])
        self.assertEqual(report['summary'][0]['locked_games'], 1)
        self.assertEqual(report['summary'][0]['graded_games'], 1)
        changed = deepcopy(record); changed['review_note'] += 'changed'
        changed = _seal({k:v for k,v in changed.items() if k != 'record_sha256'})
        with self.assertRaisesRegex(InputError, 'Conflicting'): unique_predictions([record, changed])

    def test_versions_are_reported_separately_and_mae_rmse_use_graded_games_only(self):
        record = locked()
        second = deepcopy(record); second['model_version'] = 'synthetic-second-version'; second['record_id'] = 'b'*64
        second = _seal({k:v for k,v in second.items() if k != 'record_sha256'})
        report = tracking_report([record, second], [final_observation(record)])
        self.assertEqual(len(report['summary']), 2)
        for group in report['summary']:
            self.assertEqual(group['graded_games'], 1)
            self.assertAlmostEqual(group['margin_mae'], abs(record['selected']['home_margin'] - 5))
            self.assertAlmostEqual(group['margin_rmse'], group['margin_mae'])
            self.assertNotIn('roi', group)

    def test_actual_minutes_can_be_compared_without_changing_the_forecast(self):
        record = locked()
        final = final_observation(record)
        boxes = {}
        for side in ('away','home'):
            projected = {p['player_id']:p['minutes'] for p in record['selected'][side+'_rotation']['changes']}
            boxes[record['verification'][side]['espn_team_id']] = [
                {'espn_athlete_id':eid, 'minutes':projected[pid]}
                for eid,pid in record['inputs']['live_provenance']['identity'][side].items()]
        obs = result_observation(final['game'], boxes)
        graded = grade_prediction(record,obs)
        self.assertEqual(graded['rotation_minutes_mae'],0)
        self.assertEqual(graded['rotation_players_compared'],18)


class AppendOnlyStorageTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.row_factory = sqlite3.Row
        self.sql = []
        def execute(sql):
            self.sql.append(sql)
            cursor = self.connection.execute(sql)
            return [dict(r) for r in cursor.fetchall()]
        self.db = SimpleNamespace(_execute=execute, _result_rows=lambda r:r,
            _sql_text=lambda v:"'" + str(v).replace("'", "''") + "'")
        self.backend = patch('shared.nba_tracking_storage._backend', return_value=self.db)
        self.backend.start()

    def tearDown(self):
        self.backend.stop()
        self.connection.close()

    def test_first_database_lock_survives_second_changed_save_and_reload(self):
        now = datetime.now(timezone.utc)
        record = locked(now)
        saved, inserted = save_prediction(record)
        self.assertTrue(inserted)
        changed = deepcopy(record); changed['review_note'] += ' later edited minutes'
        changed = _seal({k:v for k,v in changed.items() if k != 'record_sha256'})
        previous, inserted = save_prediction(changed)
        self.assertFalse(inserted)
        self.assertEqual(previous, saved)
        results = final_observation(record)
        self.assertTrue(save_observation(results))
        self.assertFalse(save_observation(results))
        predictions, observations = load_ledger(seed_records=[record])
        self.assertEqual(predictions, [saved])
        self.assertEqual(observations, [results])
        self.assertFalse(any('UPDATE ' in q or 'DELETE ' in q or 'dataset_rows' in q for q in self.sql))

    def test_database_clock_also_rejects_a_write_that_missed_tipoff(self):
        record = locked()
        record['tipoff'] = '2000-01-01T00:00:00+00:00'
        record = _seal({k:v for k,v in record.items() if k != 'record_sha256'})
        with self.assertRaisesRegex(InputError, 'tipoff passed'): save_prediction(record)
        self.assertEqual(load_ledger(), ([], []))

    def test_database_failure_is_not_reported_as_a_successful_save(self):
        record = locked()
        with patch('shared.nba_tracking_storage._backend', side_effect=InputError('offline')):
            with self.assertRaises(InputError): save_prediction(record)


if __name__ == '__main__':
    unittest.main()
