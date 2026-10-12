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
    caches.cache.data["k"] = {"word": "license", "choice": "licence"}  # a decision arrived
    assert caches.flush() == []
    json.loads((tmp_path / ".prosefix" / "cache.json").read_text())
    assert not (tmp_path / ".prosefix" / "commas.json").exists()      # no comma decision: not written


def test_dash_survives_next_to_protected_quotes():
    r = run('Read "#draft" and "$x$" -- now.\n', target="us")
    assert r.output == "Read “#draft” and “$x$”—now.\n"


def test_entity_dash_is_restyled_in_the_same_run():
    text = "a &mdash; b\n"
    r = run(text, target="us")
    assert r.output == "a—b\n"
    assert apply_changes(text, r.changes) == r.output
    assert run(r.output, target="us").output == r.output
    assert {c.pass_ for c in r.changes} <= {"stylefix", "proofix"}


def test_second_pass_labels_restyle_and_proofix_changes():
    from adjudicate import Change
    middle = "a &mdash; b and teh c\n"
    final = "a—b and the c\n"
    tagged = [("proofix", Change(2, 9, "&mdash;", "—", "hygiene")),
              ("proofix", Change(18, 21, "teh", "the", "typos", decided_by="rule")),
              ("stylefix", Change(1, 4, " — ", "—", "punctuation"))]
    out = engine._second_pass(middle, final, tagged)
    assert apply_changes(middle, out) == final
    labels = {(c.before, c.after): (c.pass_, c.check) for c in out}
    assert labels[("teh", "the")] == ("proofix", "typos")
    assert all(c.pass_ in ("stylefix", "proofix") and "\x00" not in c.check for c in out)


def test_byte_order_mark_before_front_matter_keeps_the_head(tmp_path):
    from prosefix.settings import find_project, resolve
    head = "\ufeff---\ntitle: Color program -- draft\nprosefix: {target: uk}\n---\n"
    text = head + "\nThe color program -- ok.\n"
    s = resolve(tmp_path / "n.md", text, {}, find_project(tmp_path / "n.md"))
    assert s.target == "uk"                        # the front matter is read
    r = engine.process("n.md", text, s, None, engine.caches_for(s, "off"), replay=False)
    assert r.output.startswith(head) and "The colour program" in r.output
    assert apply_changes(text, r.changes) == r.output


def test_bad_front_matter_note_stays_on_line_1(tmp_path):
    text = "---\nkey: [unclosed\nmore: x\n---\nBody colour.\n"
    s = Settings(target="us", note="front matter does not parse")
    r = engine.process("n.md", text, s, None, engine.caches_for(s, "off"), replay=False)
    [note] = [i for i in r.review if i.pass_ == "prosefix"]
    assert note.line == 1


def test_unchanged_caches_are_not_written_and_save_errors_are_reported(tmp_path):
    s = Settings(target="uk", cache=tmp_path / "ro" / ".prosefix" / "cache.json")
    (tmp_path / "ro").mkdir()
    caches = engine.caches_for(s, "use")
    engine.process("n.md", "The licence was granted.\n", s, None, caches, False)
    assert engine.flush_all() == [] and not (tmp_path / "ro" / ".prosefix").exists()
    caches = engine.caches_for(s, "use")
    caches.cache.data["k"] = {"word": "x"}
    (tmp_path / "ro").chmod(0o555)
    try:
        errors = engine.flush_all()
    finally:
        (tmp_path / "ro").chmod(0o755)
    assert len(errors) == 1 and errors[0].startswith("cannot save decisions to ")
    assert "cache.json: Permission denied" in errors[0]


def _have_tools():
    import shutil
    return shutil.which("vale") and (shutil.which("languagetool") or shutil.which("languagetool-commandline"))


import pytest  # noqa: E402

SAMPLES = [Path(__file__).parent.parent.parent / "stylefix" / "examples" / "sample.md",
           Path(__file__).parent.parent.parent / "proofix" / "examples" / "typos.md"]


@pytest.mark.skipif(not _have_tools(), reason="needs vale and languagetool")
@pytest.mark.skipif(not all(p.exists() for p in SAMPLES), reason="needs the stylefix and proofix examples")
@pytest.mark.parametrize("target", ["us", "uk"])
def test_second_run_over_the_example_vault_changes_nothing(tmp_path, target):
    s = Settings(target=target)
    for p in SAMPLES:
        once = engine.process(p.name, p.read_text(), s, None, engine.caches_for(s, "off"), replay=False)
        assert once.status == "changed"
        twice = engine.process(p.name, once.output, s, None, engine.caches_for(s, "off"), replay=False)
        assert twice.output == once.output, (p.name, [(c.before, c.after, c.pass_, c.check) for c in twice.changes])
