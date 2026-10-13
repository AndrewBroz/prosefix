# prosefix

prosefix fixes spelling, style, typos and punctuation in Markdown files, in place.

```sh
prosefix draft.md             # fix one file in place
prosefix draft.md --diff      # show the changes without writing them
prosefix vault/ --check       # exit 1 if any file would change
```

prosefix runs two passes over each file with one model endpoint, one decision cache and one change list. The style pass (stylefix) settles one English variety and one style guide; the proof pass (proofix) fixes typos, punctuation, compounds and commas. Front matter, code, links, HTML and Obsidian syntax are never changed.

prosefix applies a fix only when it is always right; when judgement is needed it asks the model; anything uncertain is listed for you.

```text
Before:  We recieved the the new colour scheme -- the teams programme , which covers analysis,design and testing.
After:   We received the new colour scheme – the teams programme, which covers analysis, design and testing.
```

(A British draft, kept British, with the tools installed and no model. Words that depend on meaning, such as licence and license, are listed for review until a model decides them.)

## Install

```sh
brew install andrewbroz/tap/prosefix     # also installs Vale, LanguageTool and typos
uv tool install prosefix                 # or: pipx install prosefix
```

Without Homebrew, install the tools you want. prosefix runs every tier whose requirements are met:

| missing | skipped | still runs |
|---|---|---|
| [Vale](https://vale.sh) (`brew install vale`) | `variety`, `guide`, `ambiguous`, `numbers` | `serial-comma`, `abbreviations`, `punctuation`, and all of the proof pass |
| [LanguageTool](https://languagetool.org) (`brew install languagetool`) | `mechanical`, `adjudicated` | `hygiene`, `basics`, `compounds`, `commas` |
| [typos](https://github.com/crate-ci/typos) (`brew install typos-cli`) | `typos` | everything else |
| a model | decisions that need judgement, `commas` | rule-based tiers; undecided items are listed for review |

## First run

```sh
prosefix draft.md --diff   # see what would change
prosefix draft.md          # write it
```

The first in-place run prints `tip: prosefix --diff <path> shows changes without writing them`; it stops once prosefix has a cache directory.

**Without the tools.** A bare `uv tool install prosefix` still fixes the obvious mechanical faults with the `basics` tier, which needs nothing else: a doubled small word (`the the`; other repeats, such as `that that`, go to the model or to review), a space before punctuation (`programme ,`) and a missing space after a comma, semicolon or colon between two words (`analysis,design`). It never touches `1,000`, `10:30`, URLs or code. prosefix then says once, in one line, how to get the rest:

```text
prosefix: most fixes need Vale and LanguageTool — brew install andrewbroz/tap/prosefix (or: brew install vale languagetool typos-cli)
```

**Without a model**, one more line says `prosefix: model decisions and comma placement need a model — prosefix --setup`. Set one up, and check what this machine has:

```sh
prosefix --setup      # choose and test a model endpoint
prosefix --doctor     # tools found or missing, the endpoint, the settings file for this directory
```

The endpoint comes from flags, `PROSEFIX_LLM_*` / `ADJUDICATE_LLM_*` environment variables, `~/.config/prosefix/config.toml` and `~/.config/adjudicate/config.toml`, never from a project file. Without a model, `--llm none` (or no configured endpoint) still applies all rule-based tiers.

### Which English

When nothing sets the variety (no `--target`, no project `target`, no front matter `prosefix: {target: …}` or `lang:`), prosefix reads the note and keeps the variety it is already written in. It counts the distinct words that only one variety spells that way (`colour`, `programme`, `finalise` against `color`, `program`, `finalize`), outside code and quotations; names, words in your accept lists and words both varieties accept do not count. Two or more British words, and at least twice as many as American ones, mean UK; the same the other way means US. A note with words of both varieties but no clear majority keeps its spellings as written (no variety conversion; everything else follows the US default), and a note with no such words at all uses US. The per-file line says which:

```text
prosefix: draft.md: en-GB (detected) · 6 change(s), 4 for review
prosefix: draft.md: no clear variety (1 UK, 1 US words); variety spellings left alone — set --target or lang: · 1 change(s), 0 for review
prosefix: notes.md: no clear variety (0 UK, 0 US words); using en-US — set --target or lang: · 1 change(s), 0 for review
```

Each file is decided on its own, so a mixed vault keeps every note consistent. To choose instead, pass `--target uk` (or `us`), set `target` in the project file, or put `lang: en-GB` in a note's front matter.

### Review items

Anything prosefix will not apply on its own (no model, the model unsure, or no single right fix) is a review item, with its line and reason:

```text
draft.md:5: review — A comma may be missing after the conjunctive/linking adverb ‘However’. [proofix SENT_START_CONJUNCTIVE_LINKING_ADVERB_COMMA]
```

For one file (or stdin), review items go to stderr after the run. For several files, each file's line counts them and the total line adds `(--changes lists them)`; `--changes` prints every change, with a few words of context either side, and then the file's review items. `--report FILE` writes them all to a Markdown file. `-q` prints none of this.

## Use

```sh
prosefix note.md                          # rewrite one note in place
prosefix vault/ --diff                    # show what would change in a vault
prosefix vault/ --changes                 # rewrite, listing every change and review item
prosefix content/ --check --llm cached    # CI: fail if anything would change
prosefix - < in.md > out.md               # filter
prosefix note.md --target uk --skip commas
```

Directory walks take `*.md` and `*.markdown`, skipping `.git`, `.obsidian`, `node_modules`, other dot-directories and your `exclude` globs. Exit status: 0 success; 1 from `--check` (or `--strict`) when something would change; 2 for usage, configuration, input or write errors, a file that changed on disk during the run (it is left alone), or a failed `--require`. Any per-file error is reported on stderr and gives 2, but the other files are still processed.

Writes are atomic, keep the file's mode, and follow symlinks to their target; read-only files are reported and left alone.

With LanguageTool installed, prosefix starts one local LanguageTool server for the run (on 127.0.0.1 only), shares it between files and stops it when the run ends, including on errors and Ctrl-C. If it cannot start, or stops answering mid-run, prosefix says so in one line and uses the command line for the rest of the run. Ctrl-C stops it, saves the decisions already made, prints `prosefix: interrupted` and exits 130.

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

or opt out with `prosefix: off`. Only `target`, `style`, `only` and `skip` are allowed there. Flags beat front matter, which beats the project file; when none of them sets a target, the note's own spelling decides (see [Which English](#which-english)).

The tiers are `variety`, `guide`, `ambiguous`, `numbers`, `serial-comma`, `abbreviations`, `punctuation` (style) and `hygiene`, `mechanical`, `basics`, `typos`, `adjudicated`, `compounds`, `commas` (proof).

## JSON

`prosefix --json` prints one document, `"schema": 1`, for editors and plugins: the endpoint, the capabilities skipped, and for each file its status, settings (with `target_source`, one of `flag`, `front matter`, `lang`, `project`, `detected` or `default`, and `detected` word counts when detection ran, and `"variety_conversion": false` when no clear variety meant the variety spellings were left alone), a list of changes (`id`, `pass`, `check`, `start`, `end`, `line`, `before`, `after`, `reason`, `decided_by`, `confidence`, `depends_on`), review items and the full output. Offsets are UTF-16 code units into the whole original file; `line` is 1-based. A change that lists `depends_on` supersedes the changes it names. The details are in the man page (`man prosefix`, or `man/prosefix.1` in the source).

## License

MIT
