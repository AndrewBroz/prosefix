import json
import threading
from pathlib import Path

from adjudicate import apply_changes
from prosefix import engine
from prosefix.settings import Settings

VAULT = Path(__file__).parent / "vault"
PROTECTED = ["[[Programme Notes|the programme note]]", "![[colour-wheel.png]]", "#policy/colour",
             "%%a private colour remark%%", "^colour-1", "> [!note]", "$c_{colour}$",
             "$$\ncolour = 1\n$$", 'color = "unchanged"', "title: Colour note"]


def run(text, **kw):
    s = Settings(**kw)
    return engine.process("t.md", text, s, None, engine.caches_for(s, "off"), replay=False)


def test_obsidian_constructs_survive_byte_for_byte():
    text = (VAULT / "obsidian.md").read_text()
    r = run(text, target="us")
    for piece in PROTECTED:
        assert piece in r.output, piece


def test_faithful_and_whole_file_offsets():
    text = (VAULT / "obsidian.md").read_text()
    r = run(text, target="us")
    assert apply_changes(text, r.changes) == r.output
    assert all(c.id and c.pass_ in ("stylefix", "proofix") for c in r.changes)


def test_idempotent():
    for f in VAULT.glob("*.md"):
        once = run(f.read_text(), target="uk").output
        assert run(once, target="uk").output == once, f.name


def test_off_is_skipped():
    r = run((VAULT / "off.md").read_text(), off=True)
    assert (r.status, r.reason, r.changes) == ("skipped", "prosefix: off", [])


def test_only_style():
    r = run('The color is "x" -- ok.\n', target="uk", only=("style",))
    assert {c.pass_ for c in r.changes} == {"stylefix"}


def test_shared_cache_written_once_and_valid(tmp_path):
    s = Settings(target="uk", cache=tmp_path / ".prosefix" / "cache.json")
    caches = engine.caches_for(s, "use")
    assert engine.caches_for(s, "use") is caches                 # one object per file
    threads = [threading.Thread(target=engine.process,
                                args=(f"n{i}.md", "The license was granted.\n", s, None, caches, False))
               for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not (tmp_path / ".prosefix" / "cache.json").exists()   # nothing written mid-run
    caches.flush()
    json.loads((tmp_path / ".prosefix" / "cache.json").read_text())


def test_dash_survives_next_to_protected_quotes():
    r = run('Read "#draft" and "$x$" -- now.\n', target="us")
    assert r.output == "Read “#draft” and “$x$”—now.\n"
