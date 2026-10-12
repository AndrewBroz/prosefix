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
    assert err.count("skipped") >= 1 and "brew install vale" in err
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
