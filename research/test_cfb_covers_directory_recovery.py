from __future__ import annotations

from builders import cfb_covers as covers


LANDING_HTML = """
<html><body>
  <a href="/sport/football/ncaaf/teams/main/syracuse-orange">SyracuseOrange</a>
  <a href="/sport/football/ncaaf/teams/main/pittsburgh-panthers">PittsburghPanthers</a>
  <a href="/sport/football/ncaaf/teams/main/miami-hurricanes/injuries">MiamiHurricanes</a>
</body></html>
"""


def main() -> None:
    original_fetch = covers._fetch_html
    original_cache = covers._DIRECTORY

    try:
        covers._fetch_html = lambda builder, url, ttl: LANDING_HTML
        covers._DIRECTORY = None

        # Team-directory recovery is now native to cfb_covers.py. There is no
        # runtime monkey-patch layer left to install.
        directory = covers._directory(object())
        assert len(directory) == 3, directory
        native_slugs = {row["slug"] for row in directory}
        assert native_slugs == {
            "syracuse-orange", "pittsburgh-panthers", "miami-hurricanes"
        }, native_slugs
        assert all(row["url"].endswith("/injuries") for row in directory)

        syracuse = covers._team_url(object(), "Syracuse")
        assert syracuse == (
            "https://www.covers.com/sport/football/ncaaf/teams/main/"
            "syracuse-orange/injuries"
        ), syracuse

        pitt = covers._team_url(object(), "Pittsburgh")
        assert pitt == (
            "https://www.covers.com/sport/football/ncaaf/teams/main/"
            "pittsburgh-panthers/injuries"
        ), pitt

        miami = covers._team_url(object(), "Miami FL")
        assert miami.endswith("/miami-hurricanes/injuries"), miami

        print("CFB Covers overview-link directory recovery test passed")
    finally:
        covers._fetch_html = original_fetch
        covers._DIRECTORY = original_cache


if __name__ == "__main__":
    main()
