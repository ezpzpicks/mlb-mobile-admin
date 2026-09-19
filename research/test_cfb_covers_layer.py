from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd

from builders import cfb_covers as covers


@dataclass
class FakePersonnel:
    expected_qb: str = "Unconfirmed"
    qb_confirmed: bool = False
    qb_continuity: float = 0.50
    qb_adjustment: float = 0.0
    ol_adjustment: float = 0.0
    skill_adjustment: float = 0.0
    dl_adjustment: float = 0.0
    linebacker_adjustment: float = 0.0
    secondary_adjustment: float = 0.0
    kicker_adjustment: float = 0.0
    special_teams_adjustment: float = 0.0
    coaching_continuity: float = 0.75
    coordinator_continuity: float = 0.67
    availability_confidence: float = 60.0
    source: str = "base"
    notes: str = ""


@dataclass
class FakeEnvironment:
    roof: str = "Outdoor"
    temperature: float = 70.0
    wind: float = 5.0
    precipitation_probability: float = 0.05
    weather_total_adjustment: float = 0.0
    weather_home_adjustment: float = 0.0
    home_field: float = 2.0
    weather_confidence: float = 50.0
    notes: str = "base weather"


class FakeBuilder:
    Personnel = FakePersonnel
    MODEL_VERSION = "cfb-test"

    def __init__(self):
        self.st = SimpleNamespace(session_state={})
        self.writes = []
        self.saved = []
        self._auto_save_selected_projection = lambda result: None
        self.auto_save_identity = self._auto_save_selected_projection
        self._clear_automatic_state = lambda: None
        self._ensure_automatic_day_slate_incremental = lambda *args, **kwargs: (1, 1)
        self.run_week = lambda *args, **kwargs: []
        self.default_personnel = lambda team, rating, season, week, game_id, live_candidate=True: FakePersonnel()
        self.build_environment = lambda game, season, manual_roof=None: FakeEnvironment()
        self.save_result = self._save_result

    def _save_result(self, result, include_no_plays=False):
        self.saved.append(result)
        return (1, 1)

    def _sheet(self, tab, columns):
        return pd.DataFrame(columns=columns)

    def write_sheet(self, tab, dataframe, columns):
        self.writes.append((tab, dataframe.copy()))
        return True

    @staticmethod
    def _weather_adjustment(temperature, wind, precipitation, roof):
        total = -1.0 if wind >= 15 else 0.0
        home = 0.25 if precipitation >= 0.50 else 0.0
        return total, home, "test weather"


class FakeIdentityBuilder:
    @staticmethod
    def _canonical_team_name(value):
        return str(value)

    @staticmethod
    def _espn_team_index():
        teams = [
            ("Georgia", "Georgia Bulldogs", "Bulldogs"),
            ("Georgia State", "Georgia State Panthers", "Panthers"),
            ("Arkansas", "Arkansas Razorbacks", "Razorbacks"),
            ("Arkansas State", "Arkansas State Red Wolves", "Red Wolves"),
            ("Oregon", "Oregon Ducks", "Ducks"),
            ("Oregon State", "Oregon State Beavers", "Beavers"),
            ("Utah", "Utah Utes", "Utes"),
            ("Utah State", "Utah State Aggies", "Aggies"),
        ]
        return {
            location: {
                "location": location,
                "displayName": display,
                "shortDisplayName": display,
                "name": mascot,
            }
            for location, display, mascot in teams
        }


class FakeAliasFallbackBuilder:
    _TEAM_NAME_ALIASES = {
        "Georgia": "Georgia",
        "Georgia Bulldogs": "Georgia",
        "Arkansas": "Arkansas",
        "Arkansas Razorbacks": "Arkansas",
    }

    @staticmethod
    def _canonical_team_name(value):
        return str(value)

    @staticmethod
    def _espn_team_index():
        return {}


class FakeGameFallbackBuilder:
    ESPN_SITE_BASE = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
    _TEAM_NAME_ALIASES = {}

    @staticmethod
    def _canonical_team_name(value):
        return str(value)

    @staticmethod
    def _espn_team_index():
        return {}

    @staticmethod
    def _public_json_get(url, params, optional=True, max_age=21600):
        assert str(params.get("event")) == "401000001"
        return {
            "header": {
                "competitions": [{
                    "competitors": [
                        {"team": {
                            "location": "Georgia",
                            "displayName": "Georgia Bulldogs",
                            "shortDisplayName": "Georgia",
                            "name": "Bulldogs",
                        }},
                        {"team": {
                            "location": "Arkansas",
                            "displayName": "Arkansas Razorbacks",
                            "shortDisplayName": "Arkansas",
                            "name": "Razorbacks",
                        }},
                    ]
                }]
            }
        }


def identity_fallback_smoke() -> None:
    original_directory = covers._directory
    try:
        covers._directory = lambda _builder: []

        alias_builder = FakeAliasFallbackBuilder()
        georgia = covers._direct_team_urls(alias_builder, "Georgia")
        arkansas = covers._direct_team_urls(alias_builder, "Arkansas")
        assert georgia and georgia[0].endswith("/georgia-bulldogs/injuries"), georgia
        assert arkansas and arkansas[0].endswith("/arkansas-razorbacks/injuries"), arkansas

        game_builder = FakeGameFallbackBuilder()
        georgia = covers._direct_team_urls(game_builder, "Georgia", "401000001")
        arkansas = covers._direct_team_urls(game_builder, "Arkansas", "401000001")
        assert georgia and georgia[0].endswith("/georgia-bulldogs/injuries"), georgia
        assert arkansas and arkansas[0].endswith("/arkansas-razorbacks/injuries"), arkansas
    finally:
        covers._directory = original_directory


def identity_resolution_smoke() -> None:
    """State/base-name schools must resolve without the Covers directory."""
    builder = FakeIdentityBuilder()
    expected = {
        "Georgia": "georgia-bulldogs",
        "Georgia State": "georgia-state-panthers",
        "Arkansas": "arkansas-razorbacks",
        "Arkansas State": "arkansas-state-red-wolves",
        "Oregon": "oregon-ducks",
        "Oregon State": "oregon-state-beavers",
        "Utah": "utah-utes",
        "Utah State": "utah-state-aggies",
    }
    original_fetch = covers._fetch_html
    original_directory = covers._directory
    try:
        # Prove these identities do not depend on a healthy/parseable landing
        # directory. This is the failure mode that blocked the live builder.
        covers._directory = lambda _builder: (_ for _ in ()).throw(
            RuntimeError("directory intentionally unavailable")
        )

        def fake_fetch(_builder, url, ttl):
            return """
            <html><body><h1>Resolved Team</h1>
            <table><tr><th>POS</th><th>#</th><th>Player</th></tr>
              <tr><td>QB</td><td>1</td><td>Test Quarterback</td></tr>
            </table></body></html>
            """

        covers._fetch_html = fake_fetch
        with covers._LOCK:
            covers._TEAM_REPORTS.clear()

        for team, slug in expected.items():
            urls = covers._direct_team_urls(builder, team)
            assert urls and urls[0].endswith(f"/{slug}/injuries"), (team, urls)
            report = covers._team_report(builder, team)
            assert report["ok"], (team, report)
            assert report["url"].endswith(f"/{slug}/injuries"), (team, report["url"])
    finally:
        covers._fetch_html = original_fetch
        covers._directory = original_directory
        with covers._LOCK:
            covers._TEAM_REPORTS.clear()


def redirect_recovery_smoke() -> None:
    old = "https://www.covers.com/sport/football/ncaaf/teams/main/nc-state-wolfpack/injuries"
    canonical_overview = "https://www.covers.com/sport/football/ncaaf/teams/main/north-carolina-state-wolfpack"
    recovered = covers._redirected_injury_url(old, canonical_overview)
    assert recovered == (
        "https://www.covers.com/sport/football/ncaaf/teams/main/"
        "north-carolina-state-wolfpack/injuries"
    ), recovered

    unchanged = covers._redirected_injury_url(recovered, recovered)
    assert unchanged == "", unchanged

def parser_smoke() -> None:
    team_html = """
    <html><body><h1>Baylor Bears</h1>
    <table><tr><th>Player</th><th>POS</th><th>Status</th></tr>
      <tr><td>D. Lagway</td><td>QB</td><td>Questionable - Ankle</td></tr>
      <tr><td>J. Porter</td><td>WR</td><td>Questionable - Undisclosed</td></tr>
      <tr><td>C. Redding</td><td>S</td><td>Out - Knee</td></tr>
    </table>
    <h2>Starters - Last Game</h2><h3>Offense</h3>
    <table><tr><th>POS</th><th>#</th><th>Player</th></tr>
      <tr><td>QB</td><td>2</td><td>DJ Lagway</td></tr>
      <tr><td>RB</td><td>35</td><td>Dawson Pendergrass</td></tr>
      <tr><td>WR</td><td>3</td><td>Jadon Porter</td></tr>
    </table>
    <h3>Defense</h3>
    <table><tr><th>POS</th><th>#</th><th>Player</th></tr>
      <tr><td>LB</td><td>5</td><td>Travion Barnes</td></tr>
      <tr><td>S</td><td>11</td><td>Devin Turner</td></tr>
    </table></body></html>
    """
    report = covers._parse_team_report(team_html, "https://example.test/baylor/injuries")
    assert report["ok"]
    assert len(report["injuries"]) == 3, report
    assert len(report["starters"]) == 5, report
    assert report["starters"][0] == {"unit": "Offense", "position": "QB", "player": "DJ Lagway"}
    assert covers._player_matches("D. Lagway", "DJ Lagway")
    assert covers._player_matches("J. Porter", "Jadon Porter")
    assert not covers._player_matches("D. Lagway", "Jadon Porter")
    assert covers._severity("Out - Knee") == 1.0
    assert 0.35 <= covers._severity("Questionable - Ankle") <= 0.45
    assert covers._candidate_score("Baylor", "Baylor Bears") > 0.87
    assert covers._candidate_score("Florida State", "Florida State Seminoles") > 0.94

    weather_html = """
    <html><body>
      <section><h3>Prairie View A&amp;M @ Baylor</h3>
        <div>McLane Stadium · 81.2 °F</div><div>8.1 Mph Wind</div>
        <div>65.73% Humidity</div><div>1.00% P.O.P</div>
      </section>
      <section><h3>Oklahoma @ Michigan</h3>
        <div>Michigan Stadium · 71.0 °F</div><div>12.5 Mph Wind</div>
        <div>30.00% Humidity</div><div>18.00% P.O.P</div>
      </section>
    </body></html>
    """
    weather = covers._parse_weather_html(weather_html)
    assert len(weather) == 2, weather
    baylor = weather[0]
    assert baylor["away"] == "Prairie View A&M"
    assert baylor["home"] == "Baylor"
    assert abs(baylor["temperature"] - 81.2) < 1e-9
    assert abs(baylor["wind"] - 8.1) < 1e-9
    assert abs(baylor["precipitation"] - 0.01) < 1e-9


def overlay_smoke() -> None:
    builder = FakeBuilder()
    original_report = covers._team_report
    original_weather_match = covers._match_weather
    try:
        covers._team_report = lambda _builder, team: {
            "team": team,
            "url": "https://example.test/baylor/injuries",
            "ok": True,
            "starters": [
                {"unit": "Offense", "position": "QB", "player": "DJ Lagway"},
                {"unit": "Offense", "position": "RB", "player": "Dawson Pendergrass"},
                {"unit": "Offense", "position": "WR", "player": "Jadon Porter"},
            ],
            "injuries": [
                {"player": "D. Lagway", "position": "QB", "status": "Questionable - Ankle", "detail": ""},
                {"player": "J. Porter", "position": "WR", "status": "Questionable - Undisclosed", "detail": ""},
            ],
        }
        covers._match_weather = lambda _builder, away, home: {
            "away": away, "home": home, "temperature": 78.0, "wind": 18.0, "precipitation": 0.60,
        }

        covers.install_covers_layer(builder)

        # Runtime guard's automatic saver must remain untouched; history writes
        # happen on explicit Save or at the end of an automatic slate.
        assert builder._auto_save_selected_projection is builder.auto_save_identity

        personnel = builder.default_personnel("Baylor", {}, 2026, 2, "game-1")
        assert personnel.expected_qb == "DJ Lagway"
        assert personnel.qb_confirmed is False
        assert personnel.qb_adjustment < 0.0
        assert personnel.skill_adjustment < 0.0
        assert "Covers.com" in personnel.source
        assert builder.writes == [], "interactive projection must not write Sheets"

        env = builder.build_environment(pd.Series({"Away Team": "Prairie View A&M", "Home Team": "Baylor"}), 2026)
        assert env.temperature == 78.0
        assert env.wind == 18.0
        assert env.precipitation_probability == 0.60
        assert env.weather_total_adjustment == -1.0
        assert env.weather_home_adjustment == 0.25
        assert env.home_field == 2.25
        assert env.weather_confidence >= 92.0

        builder.save_result({"game": "test"})
        tabs = [tab for tab, _ in builder.writes]
        assert covers.STARTER_HISTORY_TAB in tabs
        assert covers.INJURY_HISTORY_TAB in tabs

        builder._clear_automatic_state()
        assert builder.st.session_state.get("cfb_covers_pending_starters") == []
        assert builder.st.session_state.get("cfb_covers_pending_injuries") == []
    finally:
        covers._team_report = original_report
        covers._match_weather = original_weather_match


def main() -> None:
    redirect_recovery_smoke()
    identity_fallback_smoke()
    identity_resolution_smoke()
    parser_smoke()
    overlay_smoke()
    print("CFB Covers parser/matching + overlay smoke test passed")


if __name__ == "__main__":
    main()
