from __future__ import annotations

from builders import cfb_covers as covers
from builders import cfb_interactive_recovery as recovery


LANDING_HTML = """
<html><body>
  <a href="/sport/football/ncaaf/teams/main/syracuse-orange">SyracuseOrange</a>
  <a href="/sport/football/ncaaf/teams/main/pittsburgh-panthers">PittsburghPanthers</a>
  <a href="/sport/football/ncaaf/teams/main/miami-hurricanes/injuries">MiamiHurricanes</a>
</body></html>
"""


def main() -> None:
    original_fetch = covers._fetch_html
    original_directory = covers._directory
    original_cache = covers._DIRECTORY
    original_flag = getattr(covers, "_EZPZ_CFB_TEAM_DIRECTORY_RECOVERY", False)

    try:
        covers._fetch_html = lambda builder, url, ttl: LANDING_HTML
        covers._DIRECTORY = None
        if hasattr(covers, "_EZPZ_CFB_TEAM_DIRECTORY_RECOVERY"):
            delattr(covers, "_EZPZ_CFB_TEAM_DIRECTORY_RECOVERY")

        # Core resolver must now handle Covers overview links by itself. The
        # recovery wrapper remains compatible, but correctness no longer depends
        # on installation order.
        native_directory = covers._directory(object())
        assert len(native_directory) == 3, native_directory
        native_slugs = {row["slug"] for row in native_directory}
        assert native_slugs == {
            "syracuse-orange", "pittsburgh-panthers", "miami-hurricanes"
        }, native_slugs
        assert all(row["url"].endswith("/injuries") for row in native_directory)

        covers._DIRECTORY = None
        recovery._install_covers_team_directory_fix(covers)

        directory = covers._directory(object())
        assert len(directory) == 3, directory

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
        covers._directory = original_directory
        covers._DIRECTORY = original_cache
        if original_flag:
            covers._EZPZ_CFB_TEAM_DIRECTORY_RECOVERY = True
        elif hasattr(covers, "_EZPZ_CFB_TEAM_DIRECTORY_RECOVERY"):
            delattr(covers, "_EZPZ_CFB_TEAM_DIRECTORY_RECOVERY")


if __name__ == "__main__":
    main()
