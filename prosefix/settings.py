"""Settings for one file: built-in defaults < project file < front matter < flags."""

from __future__ import annotations

import functools
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

PASSES = ("style", "proof")
STYLE_TIERS = ("variety", "guide", "ambiguous", "numbers", "serial-comma", "abbreviations", "punctuation")
PROOF_TIERS = ("hygiene", "mechanical", "typos", "adjudicated", "compounds", "commas")
TIERS = STYLE_TIERS + PROOF_TIERS
SETTINGS_FILES = ("prosefix.toml", ".prosefix.toml")
FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)

_LISTS = {"only": PASSES, "skip": TIERS, "exclude": None, "accept": None}
_PROJECT_KEYS = ("target", "style", "only", "skip", "exclude", "accept", "accept_file", "cache")
_NOTE_KEYS = ("target", "style", "only", "skip")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    root: Path | None
    source: Path | None
    values: dict


@dataclass(frozen=True)
class Settings:
    target: str = "us"
    style: str | None = None
    only: tuple[str, ...] = PASSES
    skip: frozenset[str] = frozenset()
    exclude: tuple[str, ...] = ()
    accept: tuple[str, ...] = ()
    cache: Path | None = None
    project: Path | None = None
    off: bool = False
    note: str | None = None


def _validate(table, where: str, allowed: tuple[str, ...]) -> dict:
    if not isinstance(table, dict):
        raise SettingsError(f"{where}: settings must be a table")
    for k, v in table.items():
        if k not in allowed:
            raise SettingsError(f"{where}: unknown key '{k}' (known: {', '.join(allowed)})")
        if k == "target" and v not in ("us", "uk"):
            raise SettingsError(f"{where}: target must be \"us\" or \"uk\", not {v!r}")
        if k in ("style", "accept_file", "cache") and not isinstance(v, str):
            raise SettingsError(f"{where}: {k} must be a string, not {v!r}")
        if k in _LISTS:
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise SettingsError(f"{where}: {k} must be a list of strings, not {v!r}")
            choices = _LISTS[k]
            bad = [x for x in v if choices is not None and x not in choices]
            if bad:
                raise SettingsError(f"{where}: {k} has unknown value '{bad[0]}' "
                                    f"(known: {', '.join(choices)})")
    return dict(table)


def _load_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise SettingsError(f"{path}: cannot read settings: {e}") from e


@functools.lru_cache(maxsize=None)
def _project_for_dir(d: Path) -> Project:
    for p in [d, *d.parents]:
        for name in SETTINGS_FILES:
            f = p / name
            if f.is_file():
                return Project(p, f, _validate(_load_toml(f), str(f), _PROJECT_KEYS))
        py = p / "pyproject.toml"
        if py.is_file():
            table = _load_toml(py).get("tool", {}).get("prosefix")
            if table is not None:
                return Project(p, py, _validate(table, f"{py} [tool.prosefix]", _PROJECT_KEYS))
    return Project(None, None, {})


def find_project(path: Path) -> Project:
    d = path if path.is_dir() else path.parent
    return _project_for_dir(d.resolve())


find_project.cache_clear = _project_for_dir.cache_clear       # for tests


def front_matter(text: str) -> tuple[dict | None, str | None]:
    m = FRONT.match(text)
    if not m:
        return None, None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return None, f"front matter does not parse ({type(e).__name__}); project settings used"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "front matter is not a mapping; project settings used"
    return data, None


def _inside(project: Project, key: str, value: str) -> Path:
    """value resolved against the project root; it must stay inside it, symlinks included."""
    root = project.root
    bad = SettingsError(f"{project.source}: {key} must be a path inside {root}, not {value!r}")
    if Path(value).is_absolute():
        raise bad
    path = root / value
    if not path.resolve().is_relative_to(root.resolve()):
        raise bad
    return path


def _read_accept_file(project: Project, name: str) -> list[str]:
    path = _inside(project, "accept_file", name)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsError(f"{path}: cannot read accept_file: {e}") from e
    return [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]


def resolve(path: Path | None, text: str, flags: dict, project: Project) -> Settings:
    v: dict = {"target": "us"}
    v.update(project.values)
    fm, note = front_matter(text)
    off = False
    if fm is not None:
        p = fm.get("prosefix")
        where = f"{path or '-'}: front matter 'prosefix'"
        if p is False or p == "off":
            off = True
        elif isinstance(p, dict):
            v.update(_validate(p, where, _NOTE_KEYS))
        elif p is not None:
            raise SettingsError(f"{where} must be off or a mapping, not {p!r}")
        if not (isinstance(p, dict) and "target" in p):
            lang = str(fm.get("lang", "")).strip().lower()
            if lang in ("en-gb", "en-uk"):
                v["target"] = "uk"
            elif lang == "en-us":
                v["target"] = "us"
    v.update({k: val for k, val in flags.items() if val is not None})
    accept = list(v.get("accept", []))
    if project.root and "accept_file" in v:
        accept += _read_accept_file(project, v["accept_file"])
    if "cache" in v and project.root:
        cache = _inside(project, "cache", v["cache"])
    elif "cache" in v:                     # only from a flag or note: relative to the working directory
        cache = Path.cwd() / v["cache"]
    elif project.root:
        cache = project.root / ".prosefix" / "cache.json"
    else:
        cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "prosefix" / "cache.json"
    return Settings(target=v["target"], style=v.get("style"),
                    only=tuple(v.get("only", PASSES)), skip=frozenset(v.get("skip", ())),
                    exclude=tuple(v.get("exclude", ())), accept=tuple(accept),
                    cache=cache,
                    project=project.source, off=off, note=note)
