from __future__ import annotations

from builders import cfb_covers as covers


def main() -> None:
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

    print("CFB Covers parser/matching smoke test passed")


if __name__ == "__main__":
    main()
