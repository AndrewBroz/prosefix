"""prosefix command line."""

from __future__ import annotations

import argparse
import difflib
import errno
import functools
import json
import os
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from adjudicate import ConfigError, Skipped, check_endpoint, resolve_endpoint, run_setup
from stylefix import styleguides

from . import __version__, engine, jsonout, tools
from .settings import PASSES, TIERS, Settings, SettingsError, find_project, resolve

SKIP_DIRS = {".git", ".obsidian", "node_modules"}
EXTS = {".md", ".markdown"}

DESCRIPTION = """\
prosefix fixes spelling, style, typos and punctuation in Markdown files, in place.

  prosefix draft.md
  prosefix draft.md --diff
  prosefix vault/ --check"""

EPILOG = """\
prosefix runs stylefix's spelling and style guide, then proofix's typos,
punctuation and commas, in one pass. - reads stdin and writes stdout. Front
matter, code, links, HTML and Obsidian syntax are never changed.

more examples:
  prosefix --setup                      choose and test a model endpoint
  prosefix vault/ --diff                show what would change in a vault
  prosefix content/ --check --llm cached   CI: fail if anything would change
  prosefix - < in.md > out.md           filter

settings: prosefix.toml, .prosefix.toml or [tool.prosefix] in pyproject.toml,
found upwards from each file; front matter `prosefix: off` or
`prosefix: {target: uk}`, or `lang: en-GB`. Flags beat front matter, which
beats the project file.

Full documentation: https://github.com/AndrewBroz/prosefix (man page:
man/prosefix.1 in the source, installed by the Homebrew formula)."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="prosefix", description=DESCRIPTION, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("paths", nargs="*", metavar="PATH", help="files, directories, or - for stdin")
    p.add_argument("-o", "--output", type=Path, metavar="FILE", help="write one file's result here")
    p.add_argument("--stdin-filename", type=Path, metavar="PATH",
                   help="with -, the path used for settings lookup, exclude and --json")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--diff", action="store_true", help="print a unified diff; write nothing")
    m.add_argument("--check", action="store_true",
                   help="write nothing; exit 1 if any file would change")
    m.add_argument("--json", action="store_true", help="print proposals as JSON (schema 1); write nothing")
    p.add_argument("--strict", action="store_true", help="with --check, review items also fail")
    p.add_argument("--target", choices=["us", "uk"], help="English variety")
    p.add_argument("--style", metavar="GUIDE", choices=styleguides.choices(),
                   help="style guide, as stylefix --style: " + ", ".join(styleguides.choices()))
    p.add_argument("--only", choices=PASSES, help="run one pass only")
    p.add_argument("--skip", metavar="TIER,...", help="tiers to skip: " + ", ".join(TIERS))
    p.add_argument("--llm", choices=["auto", "ollama", "none", "cached"], default="auto",
                   help="auto: configured endpoint or local Ollama; cached: decisions in the "
                        "cache only, never a model; none: rules only (default: %(default)s)")
    p.add_argument("--url", metavar="URL", help="OpenAI-compatible base URL; overrides every configured url")
    p.add_argument("--model", metavar="NAME", help="model name at the endpoint")
    p.add_argument("--timeout", type=float, metavar="SEC", help="seconds per model request")
    p.add_argument("--no-cache", action="store_true", help="neither read nor write the decision cache")
    p.add_argument("--require", metavar="TOOL,...",
                   help="fail (exit 2) if any of vale, languagetool, typos, model is missing")
    p.add_argument("--changes", action="store_true", help="print every change to stderr")
    p.add_argument("--report", type=Path, metavar="FILE", help="write a Markdown report of all files")
    p.add_argument("-q", "--quiet", action="store_true", help="only errors on stderr")
    p.add_argument("-j", "--jobs", type=int, default=4, metavar="N", help="files in parallel (default: %(default)s)")
    c = p.add_argument_group("configuration", "set up or check the model endpoint, then exit")
    c.add_argument("--setup", action="store_true", help="choose, test and save a model endpoint")
    c.add_argument("--check-endpoint", action="store_true",
                   help="show the endpoint prosefix would use and test it")
    c.add_argument("--doctor", action="store_true",
                   help="show which tools, endpoint and settings this machine and directory have")
    p.add_argument("--version", action="version", version=f"prosefix {__version__}")
    return p


def excluded(rel: str, patterns) -> bool:
    """rel: posix path relative to the project root. `**` crosses directories; `*` and `?` do not."""
    for pat in patterns:
        rx = _glob_regex(pat)
        if rx.match(rel):
            return True
    return False


@functools.lru_cache(maxsize=None)
def _glob_regex(pat: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile(out + r"\Z")


@dataclass
class Job:
    label: str
    path: Path | None            # None: stdin
    lookup: Path
    text: str | None = None
    error: str | None = None


def _walk(directory: Path):
    for p in sorted(directory.rglob("*")):
        rel = p.relative_to(directory)
        if not p.is_file() or p.suffix not in EXTS:
            continue
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        yield p


def _is_excluded(p: Path) -> bool:
    try:
        project = find_project(p)
    except SettingsError:
        return False                       # the job reports it
    if project.root is None:
        return False
    try:
        settings_exclude = project.values.get("exclude", ())
        rel = p.resolve().relative_to(project.root.resolve()).as_posix()
    except ValueError:
        return False
    return excluded(rel, tuple(settings_exclude))


class ChangedDuringRun(Exception):
    """The file on disk is no longer the text that was processed."""


def _read_umask() -> int:
    old = os.umask(0)
    os.umask(old)
    return old


_UMASK = 0o022                 # read once, at startup, in main()


def _write_atomic(path: Path, text: str, expect: str | None = None) -> None:
    """Write via a temporary file beside the target, keeping its mode (a new file gets
    0666 less the umask); a crash cannot truncate it. With `expect`, the target is
    re-read just before the rename and left alone if it no longer holds that text."""
    if path.is_symlink():
        path = path.resolve()
    if path.exists() and not os.access(path, os.W_OK):      # a rename would bypass a read-only file
        raise PermissionError(errno.EACCES, "Permission denied", str(path))
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o666 & ~_UMASK
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        if expect is not None:
            try:
                with open(path, encoding="utf-8", newline="") as fh:
                    now = fh.read()
            except (OSError, UnicodeDecodeError):
                now = None
            if now != expect:
                raise ChangedDuringRun(str(path))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _err(msg: str) -> None:
    print(f"prosefix: {msg}", file=sys.stderr)


def _doctor(args) -> int:
    t = tools.detect()
    for name, found in (("vale", t.vale), ("languagetool", t.languagetool), ("typos", t.typos)):
        print(f"{name:<13}" + (f"found    {found}" if found else f"missing  {tools.install_hint(name)}"))
    preset = "none" if args.llm in ("none", "cached") else args.llm
    check_endpoint("prosefix", out=sys.stdout, preset=preset, url=args.url, model=args.model,
                   timeout=args.timeout)
    try:
        src = find_project(Path.cwd()).source
    except SettingsError as e:
        print(f"settings: {e}")
    else:
        print(f"settings: {src or 'none (defaults)'}")
    return 0


_CONTEXT = 12


def _window(text: str, start: int, end: int) -> tuple[str, str]:
    """Up to _CONTEXT characters either side of text[start:end], cut back to word boundaries."""
    lo, hi = max(0, start - _CONTEXT), min(len(text), end + _CONTEXT)
    left, right = text[lo:start], text[end:hi]
    if lo > 0 and not text[lo - 1].isspace() and left and not left[0].isspace():
        left = re.sub(r"^\S+", "", left).lstrip()
    if hi < len(text) and not text[hi].isspace() and right and not right[-1].isspace():
        right = re.sub(r"\S+$", "", right).rstrip()
    return left, right


def _change_line(label: str, line: int, c, text: str) -> str:
    extra = f", {c.reason}" if c.reason else ""
    left, right = _window(text, c.start, c.end)

    def show(mid: str) -> str:
        return (left + mid + right).replace("\r\n", "\u23ce").replace("\n", "\u23ce")
    return (f"{label}:{line}: {show(c.before)!r} -> {show(c.after)!r}  "
            f"[{c.pass_} {c.check}, {c.decided_by}{extra}]")


def _review_line(label: str, item) -> str:
    return f"{label}:{item.line}: review \u2014 {item.message} [{item.pass_} {item.check}]"


def _lines(r):
    for c in r.changes:
        yield _change_line(r.path, r.original.count("\n", 0, c.start) + 1, c, r.original)


def _unified_diff(a: str, b: str, label: str):
    """difflib's unified diff, with the marker patch(1) needs after a last line
    that has no newline."""
    for line in difflib.unified_diff(a.splitlines(True), b.splitlines(True), label, label):
        if line.endswith("\n"):
            yield line
        else:
            yield line + "\n\\ No newline at end of file\n"


def _shown_review(r):
    return [] if r.status == "error" else r.review


def _tip(path: str) -> None:
    try:
        _user_cache_dir().mkdir(parents=True, exist_ok=True)      # empty: this is what remembers the tip
    except OSError:
        pass
    print(f"tip: prosefix --diff {path} shows changes without writing them", file=sys.stderr)


def _variety_note(s) -> str:
    """Says which variety was assumed when the user did not set one."""
    if s.target_source == "detected":
        return f"{'en-GB' if s.target == 'uk' else 'en-US'} (detected) \u00b7 "
    if s.target_source == "default" and s.detected is not None:
        if not s.variety_conversion:
            return (f"no clear variety ({s.detected[0]} UK, {s.detected[1]} US words); variety "
                    "spellings left alone \u2014 set --target or lang: \u00b7 ")
        return (f"no clear variety ({s.detected[0]} UK, {s.detected[1]} US words); using en-US "
                "\u2014 set --target or lang: \u00b7 ")
    return ""


def _user_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "prosefix"


def main(argv=None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:          # the server is stopped and the caches saved by now
        print("prosefix: interrupted", file=sys.stderr)
        return 130


def _main(argv=None) -> int:
    global _UMASK
    _UMASK = _read_umask()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.setup:
        return run_setup("prosefix")
    if args.check_endpoint:
        preset = "none" if args.llm in ("none", "cached") else args.llm
        return check_endpoint("prosefix", preset=preset, url=args.url, model=args.model,
                              timeout=args.timeout)
    if args.doctor:
        return _doctor(args)
    if not args.paths:
        parser.error("give files, directories, or - for stdin")
    if args.output and "-" in args.paths:
        parser.error("-o cannot be used with -")
    if args.output and (len(args.paths) > 1 or Path(args.paths[0]).is_dir()):
        parser.error("-o takes exactly one input file")
    if args.strict and not args.check:
        parser.error("--strict needs --check")
    skip_list = [s for s in (args.skip or "").split(",") if s]
    unknown = [s for s in skip_list if s not in TIERS]
    if unknown:
        parser.error(f"unknown tier(s) in --skip: {', '.join(unknown)} (known: {', '.join(TIERS)})")
    flags = {"target": args.target, "style": args.style,
             "only": (args.only,) if args.only else None, "skip": skip_list or None}

    failed = False
    if args.llm in ("none", "cached"):
        ep = None
    else:
        try:
            ep = resolve_endpoint("prosefix", args.llm, url=args.url, model=args.model,
                                  timeout=args.timeout)
        except ConfigError as e:
            _err(str(e))
            return 2
    mode = "off" if (args.no_cache or args.llm == "none") else "replay" if args.llm == "cached" else "use"
    replay = args.llm == "cached"

    if args.require:
        t = tools.detect()
        have = {"vale": t.vale, "languagetool": t.languagetool, "typos": t.typos,
                "model": ep}
        for name in [n.strip() for n in args.require.split(",") if n.strip()]:
            if name not in have:
                parser.error(f"--require: unknown tool {name!r} (known: {', '.join(have)})")
            if not have[name]:
                _err(f"required {name} is missing ({tools.install_hint(name)})")
                failed = True
        if failed:
            return 2

    jobs: list[Job] = []
    for raw in args.paths:
        if raw == "-":
            label = str(args.stdin_filename) if args.stdin_filename else "-"
            jobs.append(Job(label, None, args.stdin_filename or Path.cwd() / "stdin.md",
                            text=sys.stdin.read()))
            continue
        p = Path(raw)
        if p.is_dir():
            for f in _walk(p):
                if not _is_excluded(f):
                    jobs.append(Job(str(f), f, f))
        elif p.is_file():
            jobs.append(Job(raw, p, p))
        else:
            _err(f"no such file: {raw}")
            failed = True

    def run(job: Job) -> engine.FileResult:
        text, settings = job.text, Settings()
        if job.path is None and args.stdin_filename and _is_excluded(job.lookup):
            src = find_project(job.lookup).source
            return engine.FileResult(job.label, "skipped", f"excluded by {src}", settings, text, text)
        try:
            if text is None:
                try:
                    with open(job.path, encoding="utf-8", newline="") as fh:
                        text = fh.read()
                except UnicodeDecodeError as e:
                    return engine.FileResult(job.label, "error", f"not UTF-8 text: {e.reason}",
                                             settings, "", "")
                except OSError as e:
                    return engine.FileResult(job.label, "error", f"cannot read: {e.strerror}",
                                             settings, "", "")
            project = find_project(job.lookup)
            settings = resolve(job.lookup, text, flags, project)
        except SettingsError as e:
            return engine.FileResult(job.label, "error", str(e), settings, text or "", text or "")
        try:
            return engine.process(job.label, text, settings, ep, engine.caches_for(settings, mode), replay,
                                  lt=runner)
        except Exception as e:                               # one bad file must not sink the run
            msg = f"{type(e).__name__}: {e}"
            return engine.FileResult(job.label, "error", msg, settings, text, text)

    first_run = False
    if not (args.quiet or args.json or args.diff or args.check or args.output) and jobs \
            and all(j.path is not None for j in jobs):
        first_run = not _user_cache_dir().exists()

    runner = engine.LanguageToolRunner(_err)
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            try:
                results = list(pool.map(run, jobs))
            except BaseException:     # Ctrl-C or a crash: files not yet started are not started
                pool.shutdown(wait=False, cancel_futures=True)
                raise
    finally:                          # the LanguageTool server never outlives the run, and
        try:                          # decisions already paid for survive Ctrl-C or a crash
            runner.close()
        finally:
            if mode != "off":
                for msg in engine.flush_all():
                    _err(msg)
    for r in results:
        if r.status == "error":
            failed = True
            if not args.json:
                _err(f"{r.path}: {r.reason}")

    skipped: dict[str, Skipped] = {}
    for r in results:
        for s in r.skipped:
            skipped.setdefault(s.what, s)
    model_line = None
    if ep is None and args.llm not in ("none", "cached"):
        model_line = "model decisions and comma placement need a model \u2014 prosefix --setup"
    elif args.llm == "cached" and ep is None:
        model_line = ("skipped model decisions and comma placement "
                      "(only cached decisions are used: run without --llm cached)")
    skipped_list = list(skipped.values())
    if model_line:
        why, fix = (("only cached decisions are used", "run without --llm cached")
                    if args.llm == "cached" else ("no model endpoint", "prosefix --setup"))
        skipped_list.append(Skipped("model decisions and comma placement", why, fix))
    missing = [s.fix.split()[-1] for s in skipped.values() if s.fix.startswith("brew install ")]
    missing.sort(key=lambda n: ("vale", "languagetool", "typos-cli").index(n)
                 if n in ("vale", "languagetool", "typos-cli") else 9)
    if not (args.quiet or args.json):
        if missing:
            _err("most fixes need Vale and LanguageTool \u2014 brew install andrewbroz/tap/prosefix "
                 f"(or: brew install {' '.join(missing)})")
        for s in skipped.values():
            if not s.fix.startswith("brew install "):
                _err(f"skipped {s.what} ({s.why}: {s.fix})")
        if model_line:
            _err(model_line)

    changed = [r for r in results if r.status == "changed"]
    reviewed = [r for r in results if r.review]
    if args.changes:
        for r in results:
            for line in _lines(r):
                print(line, file=sys.stderr)
            for i in _shown_review(r):
                print(_review_line(r.path, i), file=sys.stderr)
    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as fh:
                for r in results:
                    fh.write(f"## {r.path}\n\n")
                    if r.status in ("error", "skipped"):
                        fh.write(f"{r.status}: {r.reason}\n\n")
                    for line in _lines(r):
                        fh.write(f"- {line}\n")
                    for i in r.review:
                        fh.write(f"- review, line {i.line}: [{i.pass_} {i.check}] {i.message}\n")
                    fh.write("\n")
        except OSError as e:
            _err(f"{args.report}: cannot write report: {e.strerror or e}")
            failed = True

    n_changes = sum(len(r.changes) for r in changed)
    if args.json:
        json.dump(jsonout.document(results, ep, skipped_list), sys.stdout, ensure_ascii=False, indent=1)
        sys.stdout.write("\n")
    elif args.diff:
        for r in changed:
            sys.stdout.writelines(_unified_diff(r.original, r.output, r.path))
        if len(jobs) == 1 and not (args.quiet or args.changes):
            for i in _shown_review(results[0]):
                print(_review_line(results[0].path, i), file=sys.stderr)
    elif args.check:
        flagged = [r for r in results if r.status == "changed" or (args.strict and r.review)]
        for r in flagged:
            why = "would change" if r.status == "changed" else "has review items:"
            print(f"{why} {r.path}")
        if not args.quiet:
            print(f"would change {len(flagged)} file(s)" if flagged else "nothing to change")
        if flagged:
            return 2 if failed else 1
    else:
        for job, r in zip(jobs, results):
            if job.path is None:                       # stdin filter: always emit text, even on error
                sys.stdout.write(r.output if r.status != "error" else job.text)
                continue
            if r.status == "error" or not (args.output or r.status == "changed"):
                continue
            target = Path(args.output) if args.output else job.path
            try:
                _write_atomic(target, r.output, expect=None if args.output else r.original)
            except ChangedDuringRun:
                _err(f"{target}: changed during the run; left alone")
                failed = True
            except OSError as e:
                _err(f"{target}: cannot write: {e.strerror or e}")
                failed = True
        if not args.quiet:
            out = sys.stderr if any(j.path is None for j in jobs) else sys.stdout
            for r in results:
                if r.status in ("changed",) or (r.review and r.status != "error"):
                    print(f"prosefix: {r.path}: {_variety_note(r.settings)}{len(r.changes)} change(s), "
                          f"{len(r.review)} for review", file=out)
            n_review = sum(len(r.review) for r in results)
            single = len(jobs) == 1
            hint = " (--changes lists them)" if n_review and not single and not args.changes else ""
            print(f"prosefix: {len(changed)} file(s) changed, {n_changes} change(s), "
                  f"{n_review} for review{hint}", file=out)
            if single and not args.changes:
                for i in _shown_review(results[0]):
                    print(_review_line(results[0].path, i), file=sys.stderr)
            if first_run:
                _tip(jobs[0].label)
    return 2 if failed else 0
