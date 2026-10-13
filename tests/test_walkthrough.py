"""The spec's walkthrough, end to end: a short British draft, a newcomer's first runs."""

import shutil
import time
from pathlib import Path

import pytest

from prosefix.cli import main

DRAFT = (Path(__file__).parent / "data" / "walkthrough.md").read_text()

needs_server = pytest.mark.skipif(not (shutil.which("languagetool-server") and shutil.which("languagetool")),
                                  reason="no languagetool-server")


def test_without_tools(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    p = tmp_path / "draft.md"
    p.write_text(DRAFT)
    main([str(p), "--llm", "none"])
    out, err = p.read_text(), capsys.readouterr().err
    assert "the the" not in out and "programme ," not in out and "analysis, design" in out
    assert err.count("most fixes need Vale and LanguageTool") == 1


@pytest.mark.skipif(not (shutil.which("vale") and shutil.which("languagetool")), reason="needs tools")
def test_with_tools_keeps_british(tmp_path, capsys):
    p = tmp_path / "draft.md"
    p.write_text(DRAFT)
    main([str(p), "--llm", "none"])
    text, (stdout, err) = p.read_text(), capsys.readouterr()
    assert "colour" in text and "programme" in text and "received" in text and "the the" not in text
    assert "en-GB (detected)" in stdout                 # the report line: stdout for files on disk
    assert "3 September" in text and "spells out" not in err


@pytest.mark.skipif(not shutil.which("vale"), reason="needs vale")
def test_explicit_us_converts(tmp_path):
    p = tmp_path / "draft.md"
    p.write_text(DRAFT)
    main([str(p), "--llm", "none", "--target", "us"])
    assert "color" in p.read_text()


@needs_server
def test_server_faster_than_command_line(tmp_path, monkeypatch):
    for i in range(10):
        (tmp_path / f"{i}.md").write_text(DRAFT)
    monkeypatch.delenv("PROSEFIX_NO_LT_SERVER", raising=False)
    t0 = time.monotonic()
    main([str(tmp_path), "--llm", "none", "--check"])
    with_server = time.monotonic() - t0
    monkeypatch.setenv("PROSEFIX_NO_LT_SERVER", "1")
    t0 = time.monotonic()
    main([str(tmp_path), "--llm", "none", "--check"])
    without = time.monotonic() - t0
    assert with_server * 1.5 < without          # measured: about 8 s against 21 s
