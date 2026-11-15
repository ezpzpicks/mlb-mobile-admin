"""Pregame archive acquisition for offline NCAAM matchup regression research.

All ratings are from the previous calendar date. Game-log adjusted efficiency
columns are deliberately excluded: those are retrospective outcome data.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import statistics
import time
from urllib.request import Request, urlopen

from builders.ncaam_model import team_key

UA = 'Mozilla/5.0 (compatible; EZPZPicks offline ratings research)'
ARCHIVE_URL = 'https://barttorvik.com/timemachine/team_results/{day}_team_results.json.gz'


def read_json(raw):
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return json.loads(raw.decode('utf-8-sig'))


def fetch(url, path):
    path = Path(path)
    if path.exists():
        raw = path.read_bytes()
    else:
        with urlopen(Request(url, headers={'User-Agent': UA}), timeout=35) as response:
            raw = response.read(8_000_001)
        if len(raw) > 8_000_000 or raw.lstrip().startswith((b'<', b'<!')):
            raise ValueError('Unexpected non-data or oversized provider response')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return raw


def parse_archive(raw, archive_day):
    parsed = read_json(raw)
    if not isinstance(parsed, list) or len(parsed) < 300:
        raise ValueError('Incomplete archived Division I population')
    teams = {}
    for row in parsed:
        if not isinstance(row, list) or len(row) != 45:
            raise ValueError('Unsupported dated ratings layout')
        key = team_key(row[1])
        values = [float(row[i]) for i in (4, 6, 44)]
        if not key or key in teams or not all(math.isfinite(x) for x in values):
            raise ValueError('Ambiguous or invalid archived team')
        if not (60 < values[0] < 150 and 60 < values[1] < 150 and 45 < values[2] < 95):
            raise ValueError('Archived rating outside supported units')
        teams[key] = dict(zip(('offense', 'defense', 'tempo'), values), team=str(row[1]))
    benchmark = {m: {'mean': statistics.mean(t[m] for t in teams.values()),
                     'sd': statistics.pstdev(t[m] for t in teams.values())}
                 for m in ('offense', 'defense', 'tempo')}
    if any(v['sd'] <= 0 for v in benchmark.values()):
        raise ValueError('Degenerate national benchmark')
    return {'date': archive_day, 'teams': teams, 'benchmark': benchmark}


def parse_game_logs(raw, season, cutoff='2026-11-15'):
    groups, rejected = defaultdict(list), Counter()
    for row in csv.reader(io.StringIO(raw.decode('utf-8-sig'))):
        if len(row) != 31 or row[22] != str(season):
            raise ValueError('Unsupported team-game log layout or season')
        day = datetime.strptime(row[0], '%m/%d/%y').date().isoformat()
        # Focus on the opening month, the intended November use case. No
        # current-day outcomes may enter the external current-season check.
        if day[5:7] != '11' or day >= cutoff:
            continue
        match = re.fullmatch(r'([WL]),\s*(\d+)\s*[-–]\s*(\d+)(?:\s.*)?', row[6])
        if not match or row[5] not in ('H', 'A', 'N'):
            rejected['invalid_result_or_venue_rows'] += 1
            continue
        won, high, low = match.groups()
        high, low = int(high), int(low)
        own, other = (high, low) if won == 'W' else (low, high)
        box = json.loads(row[29])
        # Torvik's raw possessions are the field after the second team score.
        # Do not use a score column as possessions, especially for overtime.
        if len(box) < 35:
            raise ValueError('Unsupported raw box-score layout')
        minutes, possessions = float(box[1]), float(box[34])
        published_pace = float(row[23])
        if (not 195 <= minutes <= 350 or not 35 < possessions < 150
            or not 40 < published_pace < 110 or high <= low or low <= 0
            or not math.isclose(published_pace, possessions * 200 / minutes, abs_tol=.25)):
            rejected['invalid_minutes_possessions_or_score_rows'] += 1
            continue
        if not math.isclose(own / possessions * 100, float(row[9]), abs_tol=.25):
            rejected['raw_efficiency_disagreement_rows'] += 1
            continue
        groups[row[24]].append({'season': season, 'date': day, 'team': team_key(row[2]),
            'opponent': team_key(row[4]), 'venue': row[5], 'points': own,
            'opponent_points': other, 'minutes': minutes, 'possessions': possessions,
            'pace': possessions * 200 / minutes, 'pp100': own / possessions * 100})
    games = []
    for key, rows in groups.items():
        if len(rows) != 2:
            rejected['unpaired_or_duplicate_games'] += 1
            continue
        a, b = rows
        if (a['team'] != b['opponent'] or a['opponent'] != b['team'] or a['date'] != b['date']
            or a['points'] != b['opponent_points'] or a['opponent_points'] != b['points']
            or abs(a['possessions'] - b['possessions']) > .1
            or abs(a['minutes'] - b['minutes']) > .1):
            rejected['conflicting_game_identity_or_result'] += 1
            continue
        neutral = a['venue'] == b['venue'] == 'N'
        if not neutral and {a['venue'], b['venue']} != {'H', 'A'}:
            rejected['conflicting_venue_games'] += 1
            continue
        home, away = sorted(rows, key=lambda r: r['team']) if neutral else ((a, b) if a['venue'] == 'H' else (b, a))
        games.append({'game_id': str(season) + '|' + key, 'season': season,
            'date': home['date'], 'home': home, 'away': away, 'neutral': neutral,
            'overtime': home['minutes'] >= 220})
    return sorted(games, key=lambda g: (g['date'], g['game_id'])), dict(rejected)


def collect(cache_dir, output, seasons=(2022, 2023, 2024, 2025, 2026, 2027)):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest, games, errors = [], [], []
    for season in seasons:
        url = f'https://barttorvik.com/getgamestats.php?year={season}&csv=1'
        path = cache_dir / f'games-{season}.csv'
        raw = fetch(url, path)
        parsed, audit = parse_game_logs(raw, season)
        games.extend(parsed)
        manifest.append({'kind': 'game_logs', 'season': season, 'url': url,
                         'sha256': hashlib.sha256(raw).hexdigest(), 'games': len(parsed), 'audit': audit})
        print(json.dumps({'event': 'game_logs', 'season': season, 'games': len(parsed), 'audit': audit}), flush=True)
    dates = sorted({(date.fromisoformat(g['date']) - timedelta(days=1)).isoformat() for g in games})
    archives = {}

    def download(day):
        time.sleep(.12)  # A small, bounded archive download; no HTML scraping.
        url = ARCHIVE_URL.format(day=day.replace('-', ''))
        path = cache_dir / 'archives' / f'{day}.json.gz'
        try:
            raw = fetch(url, path)
            archive = parse_archive(raw, day)
            return day, archive, {'kind': 'ratings', 'date': day, 'url': url,
                                 'sha256': hashlib.sha256(raw).hexdigest(), 'teams': len(archive['teams'])}, None
        except Exception as exc:
            return day, None, None, str(exc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, day) for day in dates]
        for i, future in enumerate(as_completed(futures), 1):
            day, archive, record, error = future.result()
            if error:
                errors.append({'date': day, 'reason': error})
            else:
                archives[day] = archive
                manifest.append(record)
            if i % 20 == 0 or i == len(futures):
                print(json.dumps({'event': 'archives', 'complete': i, 'total': len(futures), 'errors': len(errors)}), flush=True)
    rows, omitted = [], Counter()
    for game in games:
        archive_day = (date.fromisoformat(game['date']) - timedelta(days=1)).isoformat()
        archive = archives.get(archive_day)
        if not archive:
            omitted['missing_previous_day_archive'] += 1
            continue
        if any(game[side]['team'] not in archive['teams'] for side in ('away', 'home')):
            omitted['not_in_dated_division_one_population'] += 1
            continue
        b = archive['benchmark']
        row = {k: game[k] for k in ('game_id', 'season', 'date', 'neutral', 'overtime')}
        row['ratings_date'] = archive_day
        for metric in ('offense', 'defense', 'tempo'):
            row['mean_' + metric] = b[metric]['mean']
            row['sd_' + metric] = b[metric]['sd']
        for side in ('away', 'home'):
            g, rating = game[side], archive['teams'][game[side]['team']]
            row[side + '_team'] = g['team']
            for key in ('points', 'pp100', 'pace', 'possessions', 'minutes'):
                row[side + '_' + key] = g[key]
            for metric in ('offense', 'defense', 'tempo'):
                row[side + '_' + metric] = rating[metric]
                row[side + '_' + metric + '_z'] = (rating[metric] - b[metric]['mean']) / b[metric]['sd']
        rows.append(row)
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'cutoff_exclusive': '2026-11-15',
              'purpose': 'Opening-month pregame regression; no full-season or postgame rating features',
              'rating_archive_layout': {'team': 1, 'offense': 4, 'defense': 6, 'tempo': 44},
              'games': rows, 'exclusions': dict(omitted), 'download_errors': errors,
              'source_manifest': sorted(manifest, key=lambda r: (r['kind'], str(r.get('date', r.get('season')))))}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, separators=(',', ':'), allow_nan=False) + '\n')
    print(json.dumps({'event': 'complete', 'games': len(rows),
        'by_season': dict(Counter(r['season'] for r in rows)), 'exclusions': dict(omitted), 'errors': errors[:8]}), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    collect(args.cache, args.output)
