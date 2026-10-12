"""External programs prosefix can use, and how to get them."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

HINTS = {"vale": "brew install vale", "languagetool": "brew install languagetool",
         "typos": "brew install typos-cli", "model": "prosefix --setup"}


@dataclass(frozen=True)
class Tools:
    vale: str | None
    languagetool: str | None
    typos: str | None


def detect() -> Tools:
    return Tools(shutil.which("vale"),
                 shutil.which("languagetool") or shutil.which("languagetool-commandline"),
                 shutil.which("typos"))


def install_hint(name: str) -> str:
    return HINTS[name]
