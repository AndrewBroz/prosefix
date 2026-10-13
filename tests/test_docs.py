"""The man page and --help must keep up with the parser."""

import re
from pathlib import Path

from prosefix import __version__
from prosefix.cli import build_parser
from prosefix.settings import TIERS

ROOT = Path(__file__).parent.parent
MAN = (ROOT / "man" / "prosefix.1").read_text(encoding="utf-8")
PLAIN = MAN.replace("\\-", "-")          # roff writes --llm as \-\-llm


def test_every_long_option_is_in_the_man_page():
    missing = [o for a in build_parser()._actions for o in a.option_strings
               if o.startswith("--") and o not in PLAIN]
    assert missing == []


def test_llm_choices_match():
    choices = next(a.choices for a in build_parser()._actions if "--llm" in a.option_strings)
    assert "{" + "|".join(choices) + "}" in PLAIN


def test_man_version_matches():
    m = re.search(r'^\.TH \S+ 1 "[^"]*" "prosefix ([^"]+)"', MAN, re.M)
    assert m and m.group(1) == __version__


def test_every_tier_is_in_the_man_page():
    assert [t for t in TIERS if t not in PLAIN] == []


def test_help_points_at_documentation_that_ships():
    text = build_parser().format_help()
    assert "https://github.com/AndrewBroz/prosefix" in text
    assert "man/prosefix.1" in text


def test_nothing_personal_ships():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (readme, MAN, build_parser().format_help()):
        assert "~/Code" not in text and "dgx" not in text.lower()


def test_man_page_asks_for_tbl():
    assert MAN.startswith("'\\\" t\n")          # the man page has tables


def _help(option):
    return next(a.help for a in build_parser()._actions if option in a.option_strings)


def test_help_for_target_and_changes_says_what_they_do():
    assert "detect" in _help("--target")
    assert "review" in _help("--changes")


def test_docs_match_the_basics_tier_and_the_tip():
    flat = " ".join(PLAIN.split())
    assert "Nothing is written to remember it" not in flat
    assert "closed list" in flat and "a, an, the, of, to, at, for, with, from, into, onto" in flat
    assert "listed for review" in flat.split("Needs nothing.", 1)[1][:900]
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "a, an, the, of, to, at, for, with, from, into, onto" in readme
