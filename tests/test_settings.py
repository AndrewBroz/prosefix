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


def test_cache_without_project_is_in_the_user_cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xc"))
    find_project.cache_clear()
    s = resolve(tmp_path / "n.md", "Text.\n", {}, find_project(tmp_path / "n.md"))
    assert s.cache == tmp_path / "xc" / "prosefix" / "cache.json"
    monkeypatch.delenv("XDG_CACHE_HOME")
    s = resolve(tmp_path / "n.md", "Text.\n", {}, find_project(tmp_path / "n.md"))
    assert s.cache == Path.home() / ".cache" / "prosefix" / "cache.json"


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


def _resolve_in(tmp_path, body):
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "prosefix.toml").write_text(body)
    find_project.cache_clear()
    return resolve(root / "n.md", "x\n", {}, find_project(root / "n.md"))


@pytest.mark.parametrize("key, value", [("cache", "../outside.json"), ("cache", "/tmp/x.json"),
                                         ("accept_file", "../../etc/passwd"),
                                         ("accept_file", "/etc/passwd"),
                                         ("cache", "link/c.json"), ("accept_file", "link/words.txt")])
def test_cache_and_accept_file_stay_inside_the_project(tmp_path, key, value):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "words.txt").write_text("a\n")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "link").symlink_to(outside)
    with pytest.raises(SettingsError, match=f"{key} must be a path inside"):
        _resolve_in(tmp_path, f'{key} = "{value}"\n')


def test_relative_cache_inside_the_project_works(tmp_path):
    s = _resolve_in(tmp_path, 'cache = "sub/c.json"\n')
    assert s.cache == tmp_path / "proj" / "sub" / "c.json"


def test_default_cache_dir_symlinked_outside_is_refused(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "proj" / ".prosefix").symlink_to(tmp_path / "out")
    with pytest.raises(SettingsError, match="must be a path inside"):
        _resolve_in(tmp_path, 'target = "us"\n')


def test_commas_file_symlinked_outside_is_refused(tmp_path):
    (tmp_path / "proj" / "c").mkdir(parents=True)
    (tmp_path / "out.json").write_text("{}")
    (tmp_path / "proj" / "c" / "commas.json").symlink_to(tmp_path / "out.json")
    with pytest.raises(SettingsError, match="must be a path inside"):
        _resolve_in(tmp_path, 'cache = "c/cache.json"\n')


def test_front_matter_after_a_byte_order_mark():
    assert front_matter("\ufeff---\nprosefix: {target: uk}\n---\nx\n") == ({"prosefix": {"target": "uk"}}, None)


@pytest.mark.parametrize("where", ["project", "note"])
def test_unknown_style_names_the_key_and_the_known_guides(tmp_path, where):
    with pytest.raises(SettingsError, match=r"style has unknown value 'nope' \(known: chicago, .*oxford"):
        if where == "project":
            resolve(tmp_path / "n.md", "x\n", {}, proj(tmp_path, 'style = "nope"\n'))
        else:
            resolve(tmp_path / "n.md", "---\nprosefix: {style: nope}\n---\nx\n", {}, proj(tmp_path, ""))


UK = "The colour scheme and the programme. Finalise the licence.\n"
US = "The color scheme and the program. Finalize the license.\n"


def test_detected_when_nothing_sets_a_target(tmp_path):
    p = proj(tmp_path, "")
    s = resolve(tmp_path / "n.md", UK, {}, p)
    assert (s.target, s.target_source) == ("uk", "detected") and s.detected[0] >= 2


def test_default_when_unclear(tmp_path):
    s = resolve(tmp_path / "n.md", "Plain words only.\n", {}, proj(tmp_path, ""))
    assert (s.target, s.target_source, s.detected) == ("us", "default", (0, 0))


@pytest.mark.parametrize("body, flags, front, want", [
    ("", {"target": "us"}, "", ("us", "flag")),
    ('target = "us"\n', {}, "", ("us", "project")),
    ("", {}, "---\nprosefix: {target: us}\n---\n", ("us", "front matter")),
    ("", {}, "---\nlang: en-US\n---\n", ("us", "lang")),
])
def test_explicit_targets_beat_detection(tmp_path, body, flags, front, want):
    s = resolve(tmp_path / "n.md", front + UK, flags, proj(tmp_path, body))
    assert (s.target, s.target_source) == want and s.detected is None


def test_project_target_beats_detection(tmp_path):
    s = resolve(tmp_path / "n.md", UK, {}, proj(tmp_path, 'target = "us"\n'))
    assert (s.target, s.target_source) == ("us", "project")


def test_other_lang_does_not_set_target_so_detection_runs(tmp_path):
    s = resolve(tmp_path / "n.md", "---\nlang: en-AU\n---\n" + UK, {}, proj(tmp_path, ""))
    assert (s.target, s.target_source) == ("uk", "detected")
