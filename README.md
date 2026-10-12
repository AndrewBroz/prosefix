# prosefix

Clean, consistent prose in Markdown: stylefix's spelling and style guide plus proofix's typos, punctuation and commas, in one command.

prosefix runs two passes over each file with one model endpoint, one decision cache and one change list. The style pass (stylefix) settles one English variety and one style guide; the proof pass (proofix) fixes typos, punctuation, compounds and commas. Front matter, code, links, HTML and Obsidian syntax are never changed. Files are rewritten in place.

```text
Before:  The colour of the "new" programme was fine,  and we honour it -- really.
After:   The color of the “new” program was fine, and we honor it—really.
```

(US target, rules only. With a model, words that depend on meaning, such as licence and license, are decided too.)

## Install

```sh
brew install andrewbroz/tap/prosefix     # also installs Vale, LanguageTool and typos
uv tool install prosefix                 # or: pipx install prosefix
```

Without Homebrew, install the tools you want. prosefix runs every tier whose requirements are met and says once, on stderr, what it skipped:

| missing | skipped | still runs |
|---|---|---|
| [Vale](https://vale.sh) (`brew install vale`) | `variety`, `guide`, `ambiguous`, `numbers` | `serial-comma`, `abbreviations`, `punctuation`, and all of the proof pass |
| [LanguageTool](https://languagetool.org) (`brew install languagetool`) | `mechanical`, `adjudicated` | `hygiene`, `compounds`, `commas` |
| [typos](https://github.com/crate-ci/typos) (`brew install typos-cli`) | `typos` | everything else |
| a model | decisions that need judgement, `commas` | rule-based tiers; undecided items are reported |

## First run

```sh
prosefix --setup      # choose and test a model endpoint
prosefix --doctor     # tools found or missing, the endpoint, the settings file for this directory
```

The endpoint comes from flags, `PROSEFIX_LLM_*` / `ADJUDICATE_LLM_*` environment variables, `~/.config/prosefix/config.toml` and `~/.config/adjudicate/config.toml`, never from a project file. Without a model, `--llm none` (or no configured endpoint) still applies all rule-based tiers.

## Use

```sh
prosefix note.md                          # rewrite one note in place
prosefix vault/ --diff                    # show what would change in a vault
prosefix content/ --check --llm cached    # CI: fail if anything would change
prosefix - < in.md > out.md               # filter
prosefix note.md --target uk --skip commas
```

Directory walks take `*.md` and `*.markdown`, skipping `.git`, `.obsidian`, `node_modules`, other dot-directories and your `exclude` globs. Exit status: 0 success; 1 from `--check` (or `--strict`) when something would change; 2 for usage, configuration, input or write errors, a file that changed on disk during the run (it is left alone), or a failed `--require`. Any per-file error is reported on stderr and gives 2, but the other files are still processed.

Writes are atomic, keep the file's mode, and follow symlinks to their target; read-only files are reported and left alone.

## Obsidian

Run prosefix in place on a vault. Wikilinks and their aliases, embeds, `#tags`, `%%comments%%`, `^block-ids`, the `> [!note]` marker of a callout, maths, and Markdown code, links and HTML are never edited; quotes beside them are curled sensibly. Prose that only looks like syntax ("issue #42", "C#", "costs $5 and $10") is edited as prose.

Until the prosefix plugin exists, use the Shell Commands community plugin with:

```text
prosefix {{file_path:absolute}}
```

## Publishing

```yaml
- run: pipx install prosefix
- run: prosefix content/ --check --llm cached
```

Commit `.prosefix/` so CI replays your decisions: `--llm cached` applies decisions already in the cache and never contacts a model. With no settings file, decisions are cached under `$XDG_CACHE_HOME/prosefix/` (default `~/.cache/prosefix/`) instead.

## Settings

prosefix looks upward from each file for `prosefix.toml`, `.prosefix.toml`, or `[tool.prosefix]` in `pyproject.toml`:

```toml
target = "uk"                    # "us" or "uk"
style = "oxford"                 # style guide
only = ["style", "proof"]        # passes to run
skip = ["commas"]                # tiers to skip
exclude = ["drafts/**", "templates/**"]
accept = ["Obsidian", "Zettelkasten"]
accept_file = ".prosefix/accept.txt"
cache = ".prosefix/cache.json"   # comma decisions go to commas.json beside it
```

`exclude` globs are relative to the settings file; `**` crosses directories, `*` and `?` do not, and a bare directory name does not exclude its contents (write `templates/**`). `cache` and `accept_file` must stay inside the project directory.

A note can override the project in its front matter:

```yaml
---
lang: en-GB                      # sets the target when prosefix.target is absent
prosefix:
  target: uk
  skip: [commas]
---
```

or opt out with `prosefix: off`. Only `target`, `style`, `only` and `skip` are allowed there. Flags beat front matter, which beats the project file.

The tiers are `variety`, `guide`, `ambiguous`, `numbers`, `serial-comma`, `abbreviations`, `punctuation` (style) and `hygiene`, `mechanical`, `typos`, `adjudicated`, `compounds`, `commas` (proof).

## JSON

`prosefix --json` prints one document, `"schema": 1`, for editors and plugins: the endpoint, the capabilities skipped, and for each file its status, settings, a list of changes (`id`, `pass`, `check`, `start`, `end`, `line`, `before`, `after`, `reason`, `decided_by`, `confidence`, `depends_on`), review items and the full output. Offsets are UTF-16 code units into the whole original file; `line` is 1-based. A change that lists `depends_on` supersedes the changes it names. The details are in the man page (`man prosefix`, or `man/prosefix.1` in the source).

## License

MIT
