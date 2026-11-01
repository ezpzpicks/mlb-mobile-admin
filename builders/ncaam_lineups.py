"""Public college depth-chart lineups. RotoWire does not confirm CBB fives."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from builders.ncaam_model import team_key, timestamp
from shared.espn_ncaam_lines import response_observed_at

URL = 'https://www.rotowire.com/daily/ncaab/lineups.php'
ET = ZoneInfo('America/New_York')
STATUSES = {'': 'UNKNOWN', 'GTD': 'QUESTIONABLE', 'QUES': 'QUESTIONABLE', 'QUESTIONABLE': 'QUESTIONABLE',
            'OUT': 'OUT', 'OFS': 'OUT', 'DOUBTFUL': 'DOUBTFUL', 'DOUBT': 'DOUBTFUL', 'PROB': 'PROBABLE', 'PROBABLE': 'PROBABLE'}


def valid_source(url):
    parsed = urlparse(url)
    return parsed.scheme == 'https' and parsed.netloc == 'www.rotowire.com' and parsed.path == '/daily/ncaab/lineups.php' and (not parsed.query or re.fullmatch(r'slateID=[1-9]\d*', parsed.query))


def fingerprint(lineup):
    content = {side: {'starters': [(p['id'], p['status'], p['position']) for p in lineup[side]['starters']],
                      'injuries': [(p['id'], p['status']) for p in lineup[side]['injuries']]} for side in ('away', 'home')}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:16]


def parse_lineups(html, games, observed_at, source_url=URL):
    if not valid_source(source_url) or len(html.encode()) > 2_000_000:
        raise ValueError('Invalid public RotoWire college lineup source.')
    observed = timestamp(observed_at)
    soup = BeautifulSoup(html, 'html.parser')
    if not soup.select('.lineup__box'):
        raise ValueError('RotoWire college lineup cards are unavailable.')
    lineups, rejected, seen, ambiguous = [], [], set(), set()
    for box in soup.select('.lineup__box'):
        try:
            names = [box.select_one(f'.lineup__mteam.{css}') for css in ('is-visit', 'is-home')]
            if any(n is None for n in names):
                continue
            names = [n.get_text(' ', strip=True) for n in names]
            matches = [g for g in games if all(team_key(names[i]) in {team_key(a) for a in g[side+'_aliases']}
                                                   for i, side in enumerate(('away', 'home')))]
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError('Ambiguous college team identity.')
            game = matches[0]
            clock = box.parent.select_one('.lineup__time')
            if clock is None:
                raise ValueError('College lineup tipoff is missing.')
            raw = clock.get_text(' ', strip=True)
            local = timestamp(game['tipoff']).astimezone(ET)
            match = re.fullmatch(r'([A-Z]{3})\s+(\d{1,2})/(\d{1,2})\s+(\d{1,2}:\d{2}\s+[AP]M)\s+ET', raw, re.I)
            if not match or (int(match[2]), int(match[3])) != (local.month, local.day):
                raise ValueError('College lineup date does not match the official game.')
            tipoff = datetime.strptime(f'{local.year}-{match[2]}-{match[3]} {match[4]}', '%Y-%m-%d %I:%M %p').replace(tzinfo=ET)
            if tipoff != local or match[1].upper() != local.strftime('%a').upper():
                raise ValueError('College lineup tipoff does not match the official game.')
            if game['game_id'] in seen:
                ambiguous.add(game['game_id'])
                raise ValueError('Duplicate college lineup matchup.')
            lineup = {'sport': 'NCAAM', 'gameId': game['game_id'], 'date': game['date'], 'tipoff': game['tipoff'],
                      'observedAt': observed.isoformat(), 'source': 'RotoWire', 'sourceUrl': source_url, 'basis': 'Depth chart projections'}
            used = set()
            for side, css in (('away', 'is-visit'), ('home', 'is-home')):
                listing = box.select_one(f'.lineup__list.{css}')
                if listing is None:
                    raise ValueError('College lineup team section is missing.')
                players, starters, injuries, in_injuries = {}, [], [], False
                for row in listing.find_all('li', recursive=False):
                    if 'lineup__title' in row.get('class', []):
                        if row.get_text(' ', strip=True).lower() != 'injuries':
                            raise ValueError('Unrecognized college availability section.')
                        in_injuries = True
                    if 'lineup__player' not in row.get('class', []):
                        continue
                    link = row.select_one('a[href]')
                    identity = re.fullmatch(r'/cbasketball/player/[^/?#]+-([1-9]\d*)', link.get('href', '')) if link else None
                    name = link.get('title', '').strip() if link else ''
                    labels = [span.get_text(' ', strip=True).upper() for span in row.find_all('span', recursive=False)]
                    labels = [label for label in labels if label]
                    label = labels[0] if len(labels) == 1 else ''
                    if not identity or not name or len(labels) > 1 or label not in STATUSES:
                        raise ValueError('Unverified college player identity or status.')
                    pid = 'rotowire:' + identity[1]
                    position = row.select_one('.lineup__pos')
                    position = position.get_text(strip=True) if position else ''
                    player = {'id': pid, 'name': name, 'position': position, 'status': STATUSES[label]}
                    if pid in players:
                        if players[pid]['name'] != name or players[pid]['status'] != player['status']:
                            raise ValueError('Conflicting college player availability.')
                        if not in_injuries and position not in players[pid]['position'].split('/'):
                            players[pid]['position'] += '/' + position
                    else:
                        players[pid] = player
                    if in_injuries:
                        if pid not in starters and pid not in injuries:
                            injuries.append(pid)
                    elif pid not in starters:
                        starters.append(pid)
                if len(starters) > 5 or used.intersection(players):
                    raise ValueError('Ambiguous college starting lineup.')
                if any(players[pid]['status'] in {'OUT', 'DOUBTFUL'} for pid in starters):
                    raise ValueError('College starter conflicts with an OUT/DOUBTFUL status.')
                used.update(players)
                lineup[side] = {'team': game[side], 'confirmed': False, 'complete': len(starters) == 5,
                                'starters': [players[pid] for pid in starters], 'injuries': [players[pid] for pid in injuries]}
            lineup['fingerprint'] = fingerprint(lineup)
            lineups.append(lineup); seen.add(game['game_id'])
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            rejected.append(str(exc))
    # Only advertised slates for the requested dates are eligible for another read.
    days = {datetime.fromisoformat(g['date']).strftime('%b %-d') for g in games}
    links = []
    for link in soup.select('a[href]'):
        href = link['href']
        url = 'https://www.rotowire.com' + href if href.startswith('/') else href
        if valid_source(url) and '?slateID=' in url and any(re.search(r'\b'+re.escape(day)+r'(?!\d)', link.get_text(' ', strip=True)) for day in days):
            if url not in links: links.append(url)
    return [lineup for lineup in lineups if lineup['gameId'] not in ambiguous], links, rejected


def fetch_lineups(games, now=None):
    now = now or datetime.now(timezone.utc)
    pages, output, rejected = [URL], {}, []
    for index in range(3):
        if index >= len(pages): break
        url = pages[index]
        request = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'text/html'})
        with urlopen(request, timeout=15) as response:
            raw = response.read(2_000_001)
            observed = response_observed_at(response.headers, datetime.now(timezone.utc))
            if not valid_source(response.geturl()): raise ValueError('RotoWire redirected outside its public college lineup page.')
        if len(raw) > 2_000_000: raise ValueError('RotoWire college lineup response is too large.')
        found, links, errors = parse_lineups(raw.decode('utf-8'), games, observed.isoformat(), url)
        for lineup in found: output[lineup['gameId']] = lineup
        rejected.extend(errors)
        if any(g['game_id'] not in output for g in games):
            pages.extend(link for link in links if link not in pages)
        else: break
    return list(output.values()), rejected
