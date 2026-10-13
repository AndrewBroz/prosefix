"""One file through stylefix then proofix, with changes against the original."""

from __future__ import annotations

import contextlib
import copy
import os
import shutil
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path

from adjudicate import Annotation, Cache, Change, ReviewItem, Skipped, compose, derive_changes
from proofix import api as proof
from proofix import lt as proof_lt
from proofix.commas import CommaCache
from stylefix import api as style
from stylefix.cli import BUILTIN_ACCEPT

from .settings import FRONT, PROOF_TIERS, STYLE_TIERS, Settings


class _Flushable:
    """Written once, at the end of the run, and only if a decision was added:
    preview modes and read-only projects never write an unchanged cache."""

    def _mark_clean(self):
        self._saved = copy.deepcopy(self.data)

    @property
    def dirty(self) -> bool:
        return self.data != self._saved

    def save(self):            # the libraries call this after every batch; we write once, at the end
        pass

    def flush(self):
        if self.dirty:
            self._base.save(self)
            self._mark_clean()


class SharedCache(_Flushable, Cache):
    _base = Cache

    def __init__(self, path):
        Cache.__init__(self, path)
        self._mark_clean()


class SharedCommaCache(_Flushable, CommaCache):
    _base = CommaCache

    def __init__(self, path):
        CommaCache.__init__(self, path)
        self._mark_clean()


@dataclass
class Caches:
    cache: Cache
    commas: CommaCache

    def flush(self) -> list[str]:
        """Errors, one per file that could not be saved; saving never stops the run."""
        errors = []
        for c in (self.cache, self.commas):
            if hasattr(c, "flush"):
                try:
                    c.flush()
                except OSError as e:
                    errors.append(f"cannot save decisions to {c.path}: {e.strerror or e}")
        return errors


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


def flush_all() -> list[str]:
    errors: list[str] = []
    with _LOCK:
        for c in _OPEN.values():
            errors += c.flush()
        _OPEN.clear()
    return errors


class LanguageToolRunner:
    """One languagetool-server for the whole run, shared by every worker.

    Started on the first file that will ask LanguageTool, at most once (under a lock).
    If it cannot start, or stops answering later, `warn` gets one line and every file
    from then on uses the command line. close() stops it; the CLI calls it however the
    run ends."""

    def __init__(self, warn):
        self._warn = warn
        self._lock = threading.Lock()
        self._stack = contextlib.ExitStack()
        self._server = None
        self._given_up = os.environ.get("PROSEFIX_NO_LT_SERVER", "") not in ("", "0")
        self._closed = False

    @staticmethod
    def wanted(settings: Settings) -> bool:
        """This file's proof pass will run LanguageTool."""
        return ("proof" in settings.only and not settings.off
                and not {"mechanical", "adjudicated"} <= settings.skip
                and proof_lt.find_languagetool() is not None)

    def server(self, settings: Settings):
        """The running server for this file, or None: use the command line."""
        if self._given_up or not self.wanted(settings):
            return None
        with self._lock:
            if self._server is None and not (self._given_up or self._closed):
                if not shutil.which("languagetool-server"):
                    self._given_up = True
                    return None
                try:
                    self._server = self._stack.enter_context(proof_lt.LanguageToolServer())
                except (proof_lt.LTError, OSError) as e:      # OSError: it cannot even be executed
                    self._given_up = True
                    self._warn(f"LanguageTool server did not start ({e}); using the command line")
            return self._server

    def lost(self, server, error: Exception) -> None:
        """`server` failed a check after it had started: stop it, say so once, and use
        the command line from now on. Workers still using it fail and retry too."""
        with self._lock:
            if self._server is not server:              # another worker already reported it
                return
            self._server, self._given_up = None, True
            self._warn(f"LanguageTool server stopped ({error}); using the command line")
            self._stack.close()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            server, self._server = self._server, None
            try:
                self._stack.close()
            except BaseException:          # a second Ctrl-C while it stops: kill it outright
                if server is not None and server.process.poll() is None:
                    server._signal(signal.SIGKILL)
                    server.process.wait(timeout=5)
                raise


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


def process(label: str, text: str, settings: Settings, endpoint, caches: Caches,
            replay: bool, parallel: int = 4, lt: LanguageToolRunner | None = None) -> FileResult:
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
    style_opts = style.Options(target=settings.target, style=settings.style,
                               accept=style_accept, parallel=parallel,
                               skip=settings.skip & frozenset(STYLE_TIERS),
                               require_vale=False, replay_cache=replay)
    middle, first = body, []
    if "style" in settings.only:
        r = style.fix(body, style_opts, endpoint, caches.cache)
        middle, first = r.text, r.changes
        review, skipped = review + r.review, skipped + r.skipped
    final, second = middle, []
    if "proof" in settings.only:
        proof_opts = proof.Options(target=settings.target, accept=frozenset(proof_accept),
                                   style=settings.style or "none", parallel=parallel,
                                   skip=settings.skip & frozenset(PROOF_TIERS),
                                   replay_cache=replay,
                                   oxford_ize=False)      # uk means -ise in every guide
        server = lt.server(settings) if lt is not None else None
        try:
            r2 = proof.fix(middle, proof_opts, endpoint, caches.cache, caches.commas, lt_server=server)
        except proof_lt.LTError as e:
            if server is None:
                raise
            lt.lost(server, e)                 # the server died mid-run: this file again, by command line
            r2 = proof.fix(middle, proof_opts, endpoint, caches.cache, caches.commas)
        final, second = r2.text, r2.changes
        review += r2.review
        skipped += r2.skipped
        if final != middle and "style" in settings.only:
            # proofix's output is styled again (hygiene can decode "&mdash;" into a dash
            # stylefix has not seen), so a second run changes nothing. "proofix + restyle"
            # is the second pass, labelled from both passes' changes.
            r3 = style.fix(final, style_opts, endpoint, caches.cache)
            if r3.text != final:
                seen = {(i.kind, i.message, i.check) for i in review}
                review += [i for i in r3.review if (i.kind, i.message, i.check) not in seen]
                skipped += [k for k in r3.skipped if k not in skipped]
                second = _second_pass(middle, r3.text, [("proofix", c) for c in r2.changes]
                                      + [("stylefix", c) for c in r3.changes])
                final = r3.text
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
    if settings.note:                  # prosefix's own note is about the front matter: line 1
        review.insert(0, ReviewItem(1, "note", settings.note, "settings", "prosefix"))
    output = head + final
    return FileResult(label, "changed" if output != text else "unchanged", None, settings,
                      text, output, changes, review, skipped)


_TAG = "\x00"


def _second_pass(middle: str, final: str, tagged) -> list[Change]:
    """Changes middle -> final, each labelled from the proofix or restyle change
    it matches and given that pass; a hunk nothing matches is proofix's."""
    ann = [Annotation(c.before, c.after, f"{origin}{_TAG}{c.check}", c.reason, c.decided_by,
                      c.confidence) for origin, c in tagged]
    out = derive_changes(middle, final, ann, f"proofix{_TAG}proofix")
    for c in out:
        c.pass_, _, c.check = c.check.partition(_TAG) if _TAG in c.check else ("proofix", "", c.check)
    return out
