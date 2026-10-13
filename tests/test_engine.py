import json
import shutil
import threading
from pathlib import Path

import pytest
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


_BOM_HEAD = "\ufeff---\ntitle: Color program -- draft\nprosefix: {target: uk}\n---\n"


def _bom_note(tmp_path):
    from prosefix.settings import find_project, resolve
    text = _BOM_HEAD + "\nThe color program -- ok.\n"
    s = resolve(tmp_path / "n.md", text, {}, find_project(tmp_path / "n.md"))
    return text, s, engine.process("n.md", text, s, None, engine.caches_for(s, "off"), replay=False)


def test_byte_order_mark_before_front_matter_keeps_the_head_without_tools(tmp_path):
    # Needs no external tool: the dash is stylefix's own punctuation pass, so this
    # runs (and must pass) on the CI jobs without vale, languagetool or typos.
    text, s, r = _bom_note(tmp_path)
    assert s.target == "uk"                        # the front matter is read
    assert r.output.startswith(_BOM_HEAD)          # BOM and front matter byte for byte
    body = r.output[len(_BOM_HEAD):]               # the body is still fixed (colour only with vale)
    assert body in ("\nThe color program \u2013 ok.\n", "\nThe colour program \u2013 ok.\n")
    assert all(c.start >= len(_BOM_HEAD) for c in r.changes)
    assert apply_changes(text, r.changes) == r.output


@pytest.mark.skipif(not shutil.which("vale"), reason="variety spelling needs vale")
def test_byte_order_mark_before_front_matter_keeps_the_head(tmp_path):
    text, s, r = _bom_note(tmp_path)
    assert s.target == "uk"
    assert r.output.startswith(_BOM_HEAD) and "The colour program" in r.output
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


# One LanguageTool server per run --------------------------------------------------------

import os
import signal
import time

import proofix.lt
from prosefix import cli
from prosefix.cli import main
from prosefix.settings import PROOF_TIERS

needs_server = pytest.mark.skipif(not (shutil.which("languagetool-server") and shutil.which("languagetool")),
                                  reason="no languagetool-server")


def _alive(pid: int) -> bool:
    """The server's process group (the launcher and its JVM) still has a member."""
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _record_servers(monkeypatch) -> list[int]:
    pids: list[int] = []
    real = proofix.lt.LanguageToolServer.__enter__

    def enter(self):
        out = real(self)
        pids.append(self.process.pid)
        return out
    monkeypatch.setattr(proofix.lt.LanguageToolServer, "__enter__", enter)
    return pids


def _raise_on_call(monkeypatch, owner, name: str, n: int, exc: BaseException):
    """owner.name raises exc on its n-th call; the calls before it are real."""
    real, calls, lock = getattr(owner, name), [], threading.Lock()

    def wrapper(*a, **kw):
        with lock:
            calls.append(1)
            k = len(calls)
        if k == n:
            raise exc
        return real(*a, **kw)
    monkeypatch.setattr(owner, name, wrapper)


@needs_server
def test_one_server_for_many_files(tmp_path, monkeypatch):
    pids = _record_servers(monkeypatch)
    for i in range(3):
        (tmp_path / f"{i}.md").write_text("We recieved the the scheme.\n")
    assert main([str(tmp_path), "--llm", "none", "--target", "us"]) == 0
    assert len(pids) == 1
    assert not _alive(pids[0])
    assert (tmp_path / "0.md").read_text() == "We received the scheme.\n"


@needs_server
def test_server_stopped_after_exception(tmp_path, monkeypatch):
    pids = _record_servers(monkeypatch)
    for i in range(2):
        (tmp_path / f"{i}.md").write_text("We recieved the scheme.\n")
    # the second file's settings blow up after the first file has started the server
    _raise_on_call(monkeypatch, cli, "resolve", 2, RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        main([str(tmp_path), "--llm", "none", "--target", "us", "-j", "1"])
    assert pids and not _alive(pids[0])


@needs_server
def test_server_stopped_on_ctrl_c(tmp_path, monkeypatch, capsys):
    pids = _record_servers(monkeypatch)
    for i in range(6):
        (tmp_path / f"{i}.md").write_text("We recieved the scheme.\n")
    _raise_on_call(monkeypatch, engine, "process", 2, KeyboardInterrupt())
    assert main([str(tmp_path), "--llm", "none", "--target", "us", "-j", "2"]) == 130
    assert pids and not any(_alive(p) for p in pids)
    err = capsys.readouterr().err
    assert err.endswith("prosefix: interrupted\n") and "Traceback" not in err


@needs_server
def test_real_sigint_exits_130_without_traceback(tmp_path):
    import signal
    import subprocess
    import sys
    for i in range(12):
        (tmp_path / f"{i}.md").write_text(DRAFT_FOR_SIGINT)
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "c"), XDG_CACHE_HOME=str(tmp_path / "k"))
    proc = subprocess.Popen([sys.executable, "-c", "import sys; from prosefix.cli import main; sys.exit(main())",
                             str(tmp_path), "--llm", "none", "--check", "-j", "1"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    child = None
    for _ in range(600):                     # until prosefix has started its server
        found = subprocess.run(["pgrep", "-P", str(proc.pid)], capture_output=True, text=True).stdout.split()
        if found:
            child = int(found[0])
            break
        time.sleep(0.05)
    assert child, "no server started"
    time.sleep(1)
    proc.send_signal(signal.SIGINT)
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, err
    assert "Traceback" not in err and err.endswith("prosefix: interrupted\n")
    assert not _alive(child)


DRAFT_FOR_SIGINT = "We recieved the the new colour scheme -- it looks great.\n" * 20


@needs_server
def test_server_that_cannot_be_executed_falls_back(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bin"
    bad.mkdir()
    exe = bad / "languagetool-server"
    exe.write_text("#!/nonexistent/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bad}{os.pathsep}{os.environ['PATH']}")
    notes = tmp_path / "notes"
    notes.mkdir()
    for i in range(3):
        (notes / f"{i}.md").write_text("We recieved the the scheme.\n")
    assert main([str(notes), "--llm", "none", "--target", "us"]) == 0
    err = capsys.readouterr().err
    assert err.count("prosefix: LanguageTool server did not start (") == 1
    assert "; using the command line" in err
    assert all((notes / f"{i}.md").read_text() == "We received the scheme.\n" for i in range(3))


@needs_server
def test_server_dying_mid_run_falls_back(tmp_path, monkeypatch, capsys):
    servers = []
    real = proofix.lt.LanguageToolServer.__enter__

    def enter(self):
        out = real(self)
        servers.append(self)
        return out
    monkeypatch.setattr(proofix.lt.LanguageToolServer, "__enter__", enter)
    real_resolve, calls = cli.resolve, []

    def resolve(*a, **kw):                   # before the second file: the server dies
        calls.append(1)
        if len(calls) == 2:
            os.killpg(servers[0].process.pid, signal.SIGKILL)
            servers[0].process.wait()
        return real_resolve(*a, **kw)
    monkeypatch.setattr(cli, "resolve", resolve)
    for i in range(3):
        (tmp_path / f"{i}.md").write_text("We recieved the the scheme.\n")
    assert main([str(tmp_path), "--llm", "none", "--target", "us", "-j", "1"]) == 0
    err = capsys.readouterr().err
    assert err.count("prosefix: LanguageTool server stopped (") == 1, err
    assert "; using the command line" in err and "LTError" not in err
    assert len(servers) == 1
    assert all((tmp_path / f"{i}.md").read_text() == "We received the scheme.\n" for i in range(3))


def test_server_fallback_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(proofix.lt, "SERVER_START_TIMEOUT", 0.01)
    for i in range(3):
        (tmp_path / f"{i}.md").write_text("We recieved the scheme.\n")
    main([str(tmp_path), "--llm", "none", "--target", "us"])
    err = capsys.readouterr().err
    have = bool(shutil.which("languagetool-server") and shutil.which("languagetool"))
    assert err.count("prosefix: LanguageTool server did not start (") == (1 if have else 0)
    if have:
        assert "; using the command line" in err
        assert (tmp_path / "0.md").read_text() == "We received the scheme.\n"    # the command line did it


def test_no_server_when_switched_off_or_not_needed(tmp_path, monkeypatch):
    starts = []
    monkeypatch.setattr(proofix.lt.LanguageToolServer, "__enter__",
                        lambda self: starts.append(1) or (_ for _ in ()).throw(AssertionError("started")))
    (tmp_path / "n.md").write_text("We recieved the scheme.\n")
    monkeypatch.setenv("PROSEFIX_NO_LT_SERVER", "1")
    main([str(tmp_path / "n.md"), "--llm", "none", "--target", "us", "--diff"])
    monkeypatch.delenv("PROSEFIX_NO_LT_SERVER")
    main([str(tmp_path / "n.md"), "--llm", "none", "--only", "style", "--diff"])
    main([str(tmp_path / "n.md"), "--llm", "none", "--skip", "mechanical,adjudicated", "--diff"])
    assert starts == []


def test_proof_options_and_server_passed(monkeypatch):
    seen = {}
    real = engine.proof.fix

    def fix(text, options, endpoint, cache, commas, lt_server=None):
        seen.update(options=options, lt_server=lt_server)
        return real(text, options, endpoint, cache, commas)
    monkeypatch.setattr(engine.proof, "fix", fix)

    class Runner:
        def server(self, settings):
            return "SERVER"
    s = Settings(target="uk")
    engine.process("t.md", "Text.\n", s, None, engine.caches_for(s, "off"), replay=False, lt=Runner())
    assert seen["options"].oxford_ize is False and seen["lt_server"] == "SERVER"


def test_basics_is_a_proof_tier(tmp_path, capsys):
    assert "basics" in PROOF_TIERS
    p = tmp_path / "n.md"
    p.write_text("We need analysis,design and testing.\n")
    assert main([str(p), "--llm", "none", "--only", "proof", "--skip",
                 "basics,mechanical,adjudicated,typos", "--diff"]) == 0
    assert capsys.readouterr().out == ""


@needs_server
def test_server_killed_when_interrupted_while_stopping(tmp_path, monkeypatch):
    pids = _record_servers(monkeypatch)

    def interrupted(self, *exc):          # Ctrl-C arrives while the server is being stopped
        raise KeyboardInterrupt
    monkeypatch.setattr(proofix.lt.LanguageToolServer, "__exit__", interrupted)
    (tmp_path / "n.md").write_text("We recieved the scheme.\n")
    assert main([str(tmp_path / "n.md"), "--llm", "none", "--target", "us"]) == 130
    assert pids
    for _ in range(50):
        if not _alive(pids[0]):
            break
        time.sleep(0.1)
    assert not _alive(pids[0])


def test_undecided_variety_skips_stylefix_variety_tiers(monkeypatch):
    seen = []
    real = engine.style.fix

    def spy(text, opts, ep, cache):
        seen.append(opts.skip)
        return real(text, opts, ep, cache)
    monkeypatch.setattr(engine.style, "fix", spy)
    run("The colour and the flavor.\n", target="us", variety_conversion=False)
    assert seen and all({"variety", "ambiguous"} <= s for s in seen)
    seen.clear()
    run("The colour and the flavor.\n", target="us")
    assert seen and not any({"variety", "ambiguous"} & s for s in seen)


@pytest.mark.skipif(not shutil.which("vale"), reason="variety spelling needs vale")
def test_undecided_variety_leaves_both_spellings():
    text = "The colour and the flavor. The theatre is open.\n"
    r = run(text, target="us", variety_conversion=False)
    assert "colour" in r.output and "theatre" in r.output
    assert not [i for i in r.review if "Variant" in i.check]
    assert "color" in run(text, target="us").output


def test_model_removed_double_is_labelled_by_the_model(monkeypatch):
    from adjudicate.llm import Endpoint

    class First(Endpoint):
        def __init__(self):
            super().__init__(url="http://x/v1", model="m")

        def chat(self, system, user, timeout=None, json_mode=True):
            if "comma placement" in system:
                return json.dumps({"changed": [], "notes": []})
            ids = [int(l.split()[1]) for l in user.splitlines() if l.startswith("Item ")]
            return json.dumps({"decisions": [{"id": i, "choice": 1, "confidence": 0.95,
                                              "reason": "one copy"} for i in ids]})
    monkeypatch.setenv("PATH", "/nonexistent")
    s = Settings(target="us")
    r = engine.process("t.md", "How do you know that that is wrong?\n", s, First(),
                       engine.caches_for(s, "off"), replay=False)
    assert r.output == "How do you know that is wrong?\n"
    [c] = r.changes
    assert (c.pass_, c.check, c.decided_by, c.reason) == ("proofix", "adjudicated", "model", "one copy")
