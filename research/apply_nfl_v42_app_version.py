"""Update the NFL admin version after the v4.2 odds-input layer is applied."""
from pathlib import Path

PATH = Path("app_mobile_admin.py")
VERSION = "nfl-v4.2-price-aware-odds-2026-08-21"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one source match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    "NFL": "nfl-v4.1-rb-wr-regression-2026-08-20",\n',
        f'    "NFL": "{VERSION}",\n',
        "NFL header version",
    )
    text = replace_once(
        text,
        '    install_skill_prop_consistency(nfl_builder)\n    nfl_builder.render()\n',
        f'    install_skill_prop_consistency(nfl_builder)\n    nfl_builder.MODEL_VERSION = "{VERSION}"\n    nfl_builder.render()\n',
        "NFL runtime version",
    )
    PATH.write_text(text, encoding="utf-8")
    final = PATH.read_text(encoding="utf-8")
    if final.count(VERSION) < 2:
        raise RuntimeError("NFL v4.2 version was not installed in both header and runtime")
    print("NFL v4.2 admin version applied")


if __name__ == "__main__":
    main()
