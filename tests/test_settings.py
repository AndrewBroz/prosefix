from pathlib import Path

import pytest

from prosefix.settings import SettingsError, Settings, find_project, front_matter, resolve


def proj(tmp_path, body, name="prosefix.toml"):
    (tmp_path / name).write_text(body)
    find_project.cache_clear()
    return find_project(tmp_path / "note.md")


def test_defaults_without_project(tmp_path):
    find_project.cache_clear()
    s = resolve(tmp_path / "n.md", "Text.\n", {}, find_project(tmp_path / "n.md"))
    assert (s.target, s.style, s.only, s.skip, s.off) == ("us", None, ("style", "proof"), frozenset(), False)


def test_project_file_is_found_upwards(tmp_path):
    (tmp_path / "prosefix.toml").write_text('target = "uk"\nskip = ["commas"]\n')
    (tmp_path / "a" / "b").mkdir(parents=True)
    find_project.cache_clear()
    p = find_project(tmp_path / "a" / "b" / "n.md")
    assert p.source == tmp_path / "prosefix.toml"
    s = resolve(tmp_path / "a" / "b" / "n.md", "x\n", {}, p)
    assert s.target == "uk" and s.skip == frozenset({"commas"})
    assert s.cache == tmp_path / ".prosefix" / "cache.json"


def test_pyproject_table(tmp_path):
    p = proj(tmp_path, '[tool.prosefix]\ntarget = "uk"\n', "pyproject.toml")
    assert p.values == {"target": "uk"}


@pytest.mark.parametrize("body, word", [('targit = "uk"\n', "targit"), ('target = "fr"\n', "target"),
                                         ('skip = ["nope"]\n', "nope"), ('only = "style"\n', "only")])
def test_bad_project_values(tmp_path, body, word):
    (tmp_path / "prosefix.toml").write_text(body)
    find_project.cache_clear()
    with pytest.raises(SettingsError, match=word) as e:
        find_project(tmp_path / "n.md")
    assert "prosefix.toml" in str(e.value)


def test_precedence_flags_front_matter_project(tmp_path):
    p = proj(tmp_path, 'target = "us"\nstyle = "chicago"\n')
    text = "---\nprosefix: {target: uk}\n---\nBody.\n"
    assert resolve(tmp_path / "n.md", text, {}, p).target == "uk"
    assert resolve(tmp_path / "n.md", text, {"target": "us"}, p).target == "us"
    assert resolve(tmp_path / "n.md", text, {}, p).style == "chicago"


def test_off_and_lang(tmp_path):
    p = proj(tmp_path, "")
    assert resolve(None, "---\nprosefix: off\n---\nx\n", {}, p).off
    assert resolve(None, "---\nlang: en-GB\n---\nx\n", {}, p).target == "uk"
    assert resolve(None, "---\nlang: en-GB\nprosefix: {target: us}\n---\nx\n", {}, p).target == "us"


def test_front_matter_keys_are_limited(tmp_path):
    with pytest.raises(SettingsError, match="exclude"):
        resolve(Path("n.md"), "---\nprosefix: {exclude: [a]}\n---\nx\n", {}, proj(tmp_path, ""))


def test_bad_front_matter_is_a_note_not_an_error(tmp_path):
    p = proj(tmp_path, 'target = "uk"\n')
    for text in ["---\nkey: [unclosed\n---\nBody.\n", "---\n- a list\n---\nBody.\n"]:
        s = resolve(tmp_path / "n.md", text, {}, p)
        assert s.target == "uk" and s.note


def test_accept_file_relative_to_project(tmp_path):
    (tmp_path / "terms.txt").write_text("# comment\nKubernetes\n\nPyPI\n")
    p = proj(tmp_path, 'accept = ["Obsidian"]\naccept_file = "terms.txt"\n')
    assert resolve(None, "x\n", {}, p).accept == ("Obsidian", "Kubernetes", "PyPI")


def test_front_matter_parsing():
    assert front_matter("No front matter\n") == (None, None)
    assert front_matter("---\na: 1\n---\nx") == ({"a": 1}, None)
    assert front_matter("---\r\na: 1\r\n---\r\nx")[0] == {"a": 1}
