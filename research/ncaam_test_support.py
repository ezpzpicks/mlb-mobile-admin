"""Complete synthetic rating populations and atomic builder storage fixtures."""
from copy import deepcopy


def complete_population(teams):
    teams = deepcopy(teams)
    for i in range(365 - len(teams)):
        teams[f'synthetic-school-{i}'] = {'team': f'Synthetic school {i}',
            'offense': 105 + (i % 13 - 6), 'defense': 105 + (i % 11 - 5),
            'tempo': 71 + (i % 7 - 3)}
    return teams


def atomic_grade_storage(monkeypatch, runner, saved, clock):
    import builders.ncaam_matchup_live as totals
    import builders.ncaam_spread_grades as spreads
    monkeypatch.setattr(totals, 'datetime', clock)
    monkeypatch.setattr(spreads, 'datetime', clock)
    # These tests exercise quote-source priority/capacity, not a live database.
    monkeypatch.setattr(runner, 'load_reference_quotes', lambda *_a: [])
    annotations = []
    def save(_db, entries):
        assert [dataset for dataset, _, _ in entries] == [runner.store.FORECASTS, runner.store.MATCHUP_GRADES, runner.store.SPREAD_GRADES]
        assert all(immutable is True for _, _, immutable in entries)
        saved.append(entries[0][1])
        annotations.extend(entries[1:])
    monkeypatch.setattr(runner.store, 'upsert_many', save)
    return annotations
