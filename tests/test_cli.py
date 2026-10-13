import io
import json
import os
from pathlib import Path

import pytest

from adjudicate import Change, apply_changes
from prosefix import jsonout
from prosefix.cli import main

DOC = 'The color of the program is "important" -- really.\n'


@pytest.fixture
def note(tmp_path):
    p = tmp_path / "n.md"
    p.write_text(DOC)
    return p


def test_in_place_by_default(note, capsys):
    assert main([str(note), "--llm", "none"]) == 0
    assert note.read_text() != DOC


def test_check_and_strict(note, capsys):
    assert main([str(note), "--check", "--llm", "none"]) == 1
    assert note.read_text() == DOC
    main([str(note), "--llm", "none"])
    assert main([str(note), "--check", "--llm", "none"]) == 0


def test_diff_writes_nothing(note, capsys):
    assert main([str(note), "--diff", "--llm", "none"]) == 0
    assert note.read_text() == DOC
    assert capsys.readouterr().out.startswith("---")


def test_stdin_filter(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    assert main(["-", "--llm", "none"]) == 0
    assert "—" in capsys.readouterr().out


def test_output_file(note, tmp_path):
    out = tmp_path / "o.md"
    assert main([str(note), "-o", str(out), "--llm", "none"]) == 0
    assert note.read_text() == DOC and out.exists()


def test_directory_walk_skips_hidden_and_excluded(tmp_path):
    (tmp_path / "prosefix.toml").write_text('exclude = ["drafts/**"]\n')
    for rel in ["a.md", "sub/b.markdown", ".obsidian/c.md", "drafts/d.md", "node_modules/e.md", "f.txt"]:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DOC)
    main([str(tmp_path), "--llm", "none", "-q"])
    changed = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")
                     if p.is_file() and p.suffix in (".md", ".markdown") and p.read_text() != DOC)
    assert changed == ["a.md", "sub/b.markdown"]


def test_json_document(note, capsys):
    assert main([str(note), "--json", "--llm", "none"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == 1 and doc["endpoint"] is None
    [f] = doc["files"]
    assert f["status"] == "changed" and note.read_text() == DOC
    rebuilt = DOC
    for c in sorted(f["changes"], key=lambda c: (c["start"], c["end"]), reverse=True):
        rebuilt = rebuilt[:c["start"]] + c["after"] + rebuilt[c["end"]:]   # ASCII: utf16 == code points
    assert rebuilt == f["output"]


def test_utf16_offsets():
    text = "a\U0001F600b"
    m = jsonout.utf16_offsets(text)
    assert m[1] == 1 and m[2] == 3 and m[3] == 4


def test_json_partial_selection(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text('The colour is "x".\n')
    main([str(p), "--json", "--llm", "none", "--target", "us"])
    f = json.loads(capsys.readouterr().out)["files"][0]
    changes = [Change(c["start"], c["end"], c["before"], c["after"], id=c["id"],
                      depends_on=tuple(c["depends_on"])) for c in f["changes"]]
    s_ids = [c.id for c in changes if c.id.startswith("s")]
    if s_ids:
        drop = s_ids[0]
        keep = [c for c in changes if c.id != drop and drop not in c.depends_on]
        apply_changes(p.read_text(), keep)      # must not raise


def test_idempotent_on_crlf(tmp_path):
    p = tmp_path / "n.md"
    p.write_bytes(b"The color is fine.\r\nSecond line.\r\n")
    main([str(p), "--llm", "none"])
    assert main([str(p), "--check", "--llm", "none"]) == 0


def test_bad_settings_exit_2(tmp_path, note, capsys):
    (note.parent / "prosefix.toml").write_text("targit = 1\n")
    assert main([str(note), "--llm", "none"]) == 2
    assert "targit" in capsys.readouterr().err


def test_missing_tools_degrade_and_require(note, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert main([str(note), "--llm", "none"]) == 0
    err = capsys.readouterr().err
    assert "most fixes need Vale and LanguageTool" in err and "vale" in err
    assert main([str(note), "--llm", "none", "--require", "vale"]) == 2


def test_no_args_is_usage_error(capsys):
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2


def test_doctor_runs_without_input(capsys):
    assert main(["--doctor"]) == 0
    assert "vale" in capsys.readouterr().out.lower()


def test_exclude_globs(tmp_path):
    from prosefix.cli import excluded
    assert excluded("drafts/a/b.md", ("drafts/**",))
    assert excluded("a.md", ("*.md",))
    assert excluded("sub/x.md", ("sub/*.md",))
    assert not excluded("other/x.md", ("sub/*.md",))
    assert not excluded("a.md", ("drafts/**",))


def test_missing_path_exit_2(note, tmp_path, capsys):
    assert main([str(note), str(tmp_path / "nope.md"), "--llm", "none"]) == 2
    assert "no such file" in capsys.readouterr().err
    assert note.read_text() != DOC


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unwritable_file_does_not_stop_the_run(tmp_path, capsys):
    ro = tmp_path / "a_ro.md"
    ok = tmp_path / "b.md"
    ro.write_text(DOC)
    ok.write_text(DOC)
    ro.chmod(0o444)
    assert main([str(tmp_path), "--llm", "none"]) == 2
    err = capsys.readouterr().err
    assert "cannot write" in err and "a_ro.md" in err
    assert ro.read_text() == DOC and ok.read_text() != DOC


def test_write_keeps_mode(note):
    note.chmod(0o640)
    main([str(note), "--llm", "none"])
    assert note.stat().st_mode & 0o777 == 0o640 and note.read_text() != DOC


@pytest.mark.parametrize("flags", [[], ["--check"]])
def test_non_utf8_file_is_reported(tmp_path, capsys, flags):
    p = tmp_path / "bad.md"
    p.write_bytes("caf\xe9 au lait\n".encode("latin-1"))
    assert main([str(p), "--llm", "none", *flags]) == 2
    assert "bad.md" in capsys.readouterr().err


def test_stdin_error_still_emits_input(tmp_path, monkeypatch, capsys):
    (tmp_path / "prosefix.toml").write_text("targit = 1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    assert main(["-", "--llm", "none"]) == 2
    cap = capsys.readouterr()
    assert cap.out == DOC and "targit" in cap.err


def test_strict_check_on_clean_note_passes(note):
    main([str(note), "--llm", "none"])
    assert main([str(note), "--check", "--strict", "--llm", "none"]) == 0


def _excluding_project(tmp_path):
    (tmp_path / "prosefix.toml").write_text('exclude = ["drafts/**"]\n')
    (tmp_path / "drafts").mkdir()


def test_stdin_filename_matching_exclude_is_left_alone(tmp_path, monkeypatch, capsys):
    _excluding_project(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    assert main(["-", "--llm", "none", "--stdin-filename", str(tmp_path / "drafts" / "a.md")]) == 0
    assert capsys.readouterr().out == DOC
    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    assert main(["-", "--llm", "none", "--json", "--stdin-filename", str(tmp_path / "drafts" / "a.md")]) == 0
    entry = json.loads(capsys.readouterr().out)["files"][0]
    assert entry["status"] == "skipped" and "excluded by" in entry["reason"]


def test_stdin_filename_not_excluded_is_still_fixed(tmp_path, monkeypatch, capsys):
    _excluding_project(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    assert main(["-", "--llm", "none", "--stdin-filename", str(tmp_path / "notes.md")]) == 0
    assert capsys.readouterr().out != DOC


def test_diff_marks_a_missing_final_newline_and_patch_applies_it(tmp_path, capsys):
    import shutil
    import subprocess
    p = tmp_path / "n.md"
    p.write_text(DOC.rstrip("\n"))
    assert main([str(p), "--diff", "--llm", "none"]) == 0
    diff = capsys.readouterr().out
    assert "\n\\ No newline at end of file\n" in diff
    if shutil.which("patch"):
        copy = tmp_path / "copy.md"
        copy.write_text(DOC.rstrip("\n"))
        subprocess.run(["patch", "-s", str(copy)], input=diff, text=True, check=True)
        main([str(p), "--llm", "none"])
        assert copy.read_text() == p.read_text()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_check_with_cached_decisions_in_a_read_only_project(tmp_path, capsys):
    (tmp_path / "prosefix.toml").write_text("")
    p = tmp_path / "n.md"
    p.write_text(DOC)
    tmp_path.chmod(0o555)
    try:
        assert main([str(p), "--check", "--llm", "cached"]) == 1
    finally:
        tmp_path.chmod(0o755)
    assert "Traceback" not in capsys.readouterr().err
    assert not (tmp_path / ".prosefix").exists()


@pytest.mark.parametrize("flags", [["--check"], ["--diff"], ["--json"], []])
def test_preview_modes_write_no_empty_cache(tmp_path, flags):
    (tmp_path / "prosefix.toml").write_text("")
    p = tmp_path / "n.md"
    p.write_text(DOC)
    main([str(p), "--llm", "cached", *flags])
    assert not (tmp_path / ".prosefix").exists()


def test_cache_save_failure_is_reported_not_fatal(note, monkeypatch, capsys):
    from prosefix import engine
    monkeypatch.setattr(engine, "flush_all", lambda: ["cannot save decisions to /x/cache.json: Permission denied"])
    assert main([str(note), "--llm", "cached"]) == 0
    assert "prosefix: cannot save decisions to /x/cache.json: Permission denied" in capsys.readouterr().err


@pytest.mark.parametrize("umask", [0o022, 0o002, 0o077])
def test_new_output_file_follows_the_umask(note, tmp_path, umask):
    out = tmp_path / "o.md"
    old = os.umask(umask)
    try:
        assert main([str(note), "-o", str(out), "--llm", "none"]) == 0
    finally:
        os.umask(old)
    assert out.stat().st_mode & 0o777 == 0o666 & ~umask


def test_file_edited_during_the_run_is_left_alone(note, monkeypatch, capsys):
    from prosefix import engine
    real = engine.process

    def process(*a, **kw):
        r = real(*a, **kw)
        note.write_text(DOC + "Typed while prosefix ran.\n")     # Obsidian autosaves
        return r
    monkeypatch.setattr(engine, "process", process)
    assert main([str(note), "--llm", "none"]) == 2
    assert note.read_text() == DOC + "Typed while prosefix ran.\n"
    assert f"prosefix: {note}: changed during the run; left alone" in capsys.readouterr().err


def test_unwritable_report_is_an_error_not_a_traceback(note, tmp_path, capsys):
    assert main([str(note), "--llm", "none", "--report", str(tmp_path / "no" / "r.md")]) == 2
    assert "cannot write report" in capsys.readouterr().err


def test_unknown_style_is_a_usage_error(note, capsys):
    with pytest.raises(SystemExit) as e:
        main([str(note), "--style", "nope", "--llm", "none"])
    assert e.value.code == 2 and "chicago" in capsys.readouterr().err


def test_output_with_stdin_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(DOC))
    with pytest.raises(SystemExit) as e:
        main(["-", "-o", str(tmp_path / "out.md"), "--llm", "none"])
    assert e.value.code == 2 and "-o cannot be used with -" in capsys.readouterr().err
    assert not (tmp_path / "out.md").exists()


def test_decisions_are_saved_when_the_run_is_interrupted(tmp_path):
    from prosefix import engine
    (tmp_path / "prosefix.toml").write_text("")
    p = tmp_path / "n.md"
    p.write_text(DOC)

    def process(label, text, settings, endpoint, caches, replay, parallel=4, lt=None):
        caches.cache.data["k"] = {"word": "color", "choice": "colour"}     # a paid decision
        raise KeyboardInterrupt
    import unittest.mock
    with unittest.mock.patch.object(engine, "process", process), pytest.raises(KeyboardInterrupt):
        main([str(p), "--llm", "cached"])
    assert json.loads((tmp_path / ".prosefix" / "cache.json").read_text()) == {
        "k": {"word": "color", "choice": "colour"}}


def test_detected_variety_is_reported_and_kept(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("The colour scheme and the programme -- finalise the licence.\n")
    assert main([str(p), "--llm", "none"]) == 0
    assert "colour" in p.read_text() and "programme" in p.read_text()
    assert "en-GB (detected)" in capsys.readouterr().out


def test_detected_variety_label_is_on_stderr_for_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("The colour scheme and the programme -- finalise the licence.\n"))
    assert main(["-", "--llm", "none"]) == 0
    cap = capsys.readouterr()
    assert "en-GB (detected)" in cap.err and "detected" not in cap.out


def test_json_target_source(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("The colour scheme and the programme.\n")
    main([str(p), "--json", "--llm", "none"])
    s = json.loads(capsys.readouterr().out)["files"][0]["settings"]
    assert s["target_source"] == "detected" and s["detected"]["uk"] >= 2


def test_single_file_prints_review_items(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("I had had enough of it.\n")
    main([str(p), "--llm", "none", "--target", "us"])
    cap = capsys.readouterr()
    assert "review —" in cap.err and "[proofix" in cap.err and f"{p}:1:" in cap.err


def test_many_files_hint_instead_of_items(tmp_path, capsys):
    for n in "ab":
        (tmp_path / f"{n}.md").write_text("I had had enough of it.\n")
    main([str(tmp_path), "--llm", "none", "--target", "us"])
    cap = capsys.readouterr()
    assert "review —" not in cap.err + cap.out and "(--changes lists them)" in cap.out


def test_changes_lists_review_after_changes(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("The \"color\" is here. I had had enough of it.\n")     # quotes curl without tools
    main([str(p), "--llm", "none", "--target", "uk", "--changes"])
    err = capsys.readouterr().err
    assert err.index("->") < err.index("review —")


def test_change_line_has_context_window():
    from types import SimpleNamespace as NS
    from prosefix.cli import _change_line
    text = "Everything about the programme , which covers analysis and design and testing."
    start = text.index(" ,")
    c = NS(start=start, end=start + 2, before=" ,", after=",", pass_="proofix", check="C",
           decided_by="rule", reason="")
    line = _change_line("f.md", 1, c, text)
    assert "'programme , which' -> 'programme, which'" in line
    nl = "end of a sentence.\ncolour here and more words"
    s = nl.index("colour")
    c = NS(start=s, end=s + 6, before="colour", after="color", pass_="stylefix", check="C",
           decided_by="rule", reason="")
    assert "sentence.\u23cecolour here" in _change_line("f.md", 2, c, nl)


def test_one_tools_line_and_one_model_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    p = tmp_path / "n.md"
    p.write_text("The the end.\n")
    main([str(p), "--llm", "auto", "--target", "us"])
    err = capsys.readouterr().err
    assert err.count("most fixes need Vale and LanguageTool") == 1
    assert "brew install vale languagetool typos-cli" in err
    assert err.count("need a model — prosefix --setup") == 1
    assert "skipped" not in err


def test_llm_none_prints_no_model_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    p = tmp_path / "n.md"
    p.write_text("The the end.\n")
    main([str(p), "--llm", "none", "--target", "us"])
    err = capsys.readouterr().err
    assert "need a model" not in err and "most fixes need" in err


def test_quiet_prints_no_tools_model_or_tip(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    p = tmp_path / "n.md"
    p.write_text("The the end.\n")
    main([str(p), "-q", "--target", "us"])
    assert capsys.readouterr().err == ""


def test_first_run_tip_once(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("The color -- here.\n")
    main([str(p), "--llm", "none"])
    assert "tip: prosefix --diff" in capsys.readouterr().err
    main([str(p), "--llm", "none", "-q"])
    assert "tip:" not in capsys.readouterr().err


def test_no_tip_in_check_diff_json_stdin_output(tmp_path, capsys, monkeypatch):
    p = tmp_path / "n.md"
    p.write_text("The color -- here.\n")
    for extra in (["--check"], ["--diff"], ["--json"], ["-o", str(tmp_path / "o.md")]):
        main([str(p), "--llm", "none", *extra])
        assert "tip:" not in capsys.readouterr().err, extra
    monkeypatch.setattr("sys.stdin", io.StringIO("The color -- here.\n"))
    main(["-", "--llm", "none"])
    assert "tip:" not in capsys.readouterr().err


def test_help_opens_plainly(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    head = capsys.readouterr().out.split("\n\n")[1]
    assert head.startswith("prosefix fixes spelling, style, typos and punctuation in Markdown files, in place.")


def test_tip_once_with_llm_none_and_fresh_cache_home(tmp_path, capsys, monkeypatch):
    home = tmp_path / "cachehome"
    monkeypatch.setenv("XDG_CACHE_HOME", str(home))
    p = tmp_path / "n.md"
    p.write_text("The color -- here.\n")
    main([str(p), "--llm", "none"])
    assert "tip: prosefix --diff" in capsys.readouterr().err
    assert (home / "prosefix").is_dir() and not list((home / "prosefix").iterdir())
    p.write_text("The color -- here.\n")
    main([str(p), "--llm", "none"])
    assert "tip:" not in capsys.readouterr().err


def test_single_file_diff_prints_review_lines_to_stderr(tmp_path, capsys):
    p = tmp_path / "n.md"
    p.write_text("I had had enough of it.\n")
    main([str(p), "--llm", "none", "--target", "us", "--diff"])
    cap = capsys.readouterr()
    assert "review \u2014" in cap.err and "review" not in cap.out
    main([str(p), "--llm", "none", "--target", "us", "--diff", "-q"])
    assert "review \u2014" not in capsys.readouterr().err
