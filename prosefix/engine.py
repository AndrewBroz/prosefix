"""One file through stylefix then proofix, with changes against the original."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

from adjudicate import (Cache, Change, ReviewItem, Skipped, apply_changes, compose,
                        protected_spans)
from proofix import api as proof
from proofix.commas import CommaCache
from stylefix import api as style
from stylefix.cli import BUILTIN_ACCEPT

from .settings import FRONT, PROOF_TIERS, STYLE_TIERS, Settings


class SharedCache(Cache):
    def save(self):            # adjudicate() calls this after every batch; we write once, at the end
        pass

    def flush(self):
        Cache.save(self)


class SharedCommaCache(CommaCache):
    def save(self):
        pass

    def flush(self):
        CommaCache.save(self)


@dataclass
class Caches:
    cache: Cache
    commas: CommaCache

    def flush(self):
        for c in (self.cache, self.commas):
            if hasattr(c, "flush"):
                c.flush()


_LOCK = threading.Lock()
_OPEN: dict[Path, Caches] = {}


def caches_for(settings: Settings, mode: str) -> Caches:
    """mode: use (read and write), replay (read; written only if new answers arrive), off (none)."""
    if mode == "off" or settings.cache is None:
        return Caches(Cache(None), CommaCache(None))
    with _LOCK:
        if settings.cache not in _OPEN:
            _OPEN[settings.cache] = Caches(SharedCache(settings.cache),
                                           SharedCommaCache(settings.cache.with_name("commas.json")))
        return _OPEN[settings.cache]


def flush_all() -> None:
    with _LOCK:
        for c in _OPEN.values():
            c.flush()
        _OPEN.clear()


def user_accept() -> tuple[list[str], set[str]]:
    words: list[str] = []
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for p in (BUILTIN_ACCEPT, cfg / "stylefix" / "accept.txt", cfg / "proofix" / "accept.txt"):
        if p.exists():
            words += [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
                      if l.strip() and not l.strip().startswith("#")]
    return words, {w.lower() for w in words}


@dataclass
class FileResult:
    path: str
    status: str                       # changed | unchanged | skipped | error
    reason: str | None
    settings: Settings
    original: str
    output: str
    changes: list[Change] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


def _touches(c: Change, spans: list[tuple[int, int]]) -> bool:
    return any(c.start < e and c.end > s or s < c.start < e for s, e in spans)


def process(label: str, text: str, settings: Settings, endpoint, caches: Caches,
            replay: bool, parallel: int = 4) -> FileResult:
    if settings.off:
        return FileResult(label, "skipped", "prosefix: off", settings, text, text)
    m = FRONT.match(text)
    head = m.group(0) if m else ""
    body = text[len(head):]
    style_accept, proof_accept = user_accept()
    style_accept += list(settings.accept)
    proof_accept |= {w.lower() for w in settings.accept}
    review: list[ReviewItem] = []
    skipped: list[Skipped] = []
    if settings.note:
        review.append(ReviewItem(1, "note", settings.note, "settings", "prosefix"))
    middle, first = body, []
    if "style" in settings.only:
        r = style.fix(body, style.Options(target=settings.target, style=settings.style,
                                          accept=style_accept, parallel=parallel,
                                          skip=settings.skip & frozenset(STYLE_TIERS),
                                          require_vale=False, replay_cache=replay),
                      endpoint, caches.cache)
        middle, first = r.text, r.changes
        review, skipped = review + r.review, skipped + r.skipped
        # Vale does not know Obsidian syntax: drop any change that touches a protected span.
        spans = protected_spans(body)
        kept = [c for c in first if not _touches(c, spans)]
        if len(kept) != len(first):
            first = kept
            middle = apply_changes(body, first)
    final, second = middle, []
    if "proof" in settings.only:
        r2 = proof.fix(middle, proof.Options(target=settings.target, accept=frozenset(proof_accept),
                                             style=settings.style or "none", parallel=parallel,
                                             skip=settings.skip & frozenset(PROOF_TIERS),
                                             replay_cache=replay),
                       endpoint, caches.cache, caches.commas)
        final, second = r2.text, r2.changes
        review += r2.review
        skipped += r2.skipped
    for n, c in enumerate(first, 1):
        c.id = f"s{n}"
    for n, c in enumerate(second, 1):
        c.id = f"p{n}"
    changes = compose(body, middle, first, second)
    shift = len(head)
    for c in changes:
        c.start += shift
        c.end += shift
    head_lines = head.count("\n")
    review = [ReviewItem(i.line + head_lines, i.kind, i.message, i.check, i.pass_) for i in review]
    output = head + final
    return FileResult(label, "changed" if output != text else "unchanged", None, settings,
                      text, output, changes, review, skipped)
