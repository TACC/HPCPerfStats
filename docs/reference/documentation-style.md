# Documentation style

This page sets where HPCPerfStats docs go and how they are written, formatted and linted.
It is for anyone who adds or edits Markdown in this repository, human or agent.

Each standard has an ID (P for prose, V for visual, S for positioning) and the source it rests on.
The lint hooks enforce most of them; the editorial pass covers the rest.

## Docs tree and placement

The tree follows [Diataxis]: each page belongs to one quadrant.

```text
README.md                    landing page (standard-readme order)
docs/
  index.md                   docs map: 4 quadrants + records
  tutorials/                 learn one reliable path
  how-to/
    operate/                 operator tasks on a running site
    develop/                 developer tasks in the checkout
  reference/                 describe code, config and contracts
  explanation/               discuss design, trade-offs, why
  plans/                     PLAN_TEMPLATE.md only
  history/                   records, not maintained
  measurements/              records: paper evidence
monitor/
  README.md                  landing page (shipped in EXTRA_DIST)
  docs/
    how-to/  explanation/  reference/
```

To place a new page:

1. Pick the quadrant from what the reader is doing.
   Learning by doing goes in `tutorials/`, solving one task in `how-to/`, looking up facts in `reference/`, understanding why in `explanation/`.
2. Put operator tasks in `how-to/operate/` and developer tasks in `how-to/develop/`.
3. Put monitor pages under `monitor/docs/` in the same quadrant directories.
4. Name the file in lowercase kebab-case.
   How-to names start with a verb (`upgrade-the-stack.md`).
   Explanation names read after "About" (`pipeline-concurrency.md`).

The docs map is `docs/index.md`, not `docs/README.md`.
A `README.md` under `docs/` matches the workspace-root README check in `hook_task_router.py` and pulls in the install rules; the layout test fails on one.

### Records and evergreen pages

Evergreen pages describe the code at HEAD and are maintained.
Every page in `tutorials/`, `how-to/`, `reference/`, `explanation/` and the READMEs is evergreen.

Records describe what was true at a date and are not maintained.
`docs/history/` and `docs/measurements/` hold records, as do the paper kits under `monitor/measurement/`, `tests/site_measurement/` and `protocols/`.

| Property                              | Evergreen                            | Record                                       |
| ------------------------------------- | ------------------------------------ | -------------------------------------------- |
| Current-state wording (P13)           | Required                             | Exempt                                       |
| Prose lint, naming and anatomy checks | Applied                              | Exempt                                       |
| First line after the H1               | Purpose + audience                   | `Historical record, <DATE>. Not maintained.` |
| Links                                 | Must resolve, including into records | Not checked                                  |

Evergreen pages link to records only as evidence.
`docs/reference/release-notes.md` is evergreen and append-only; it is the one page exempt from current-state wording.

## Prose standards

| ID  | Standard                                                                                                                                                                                                                                                           | Basis                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| P1  | 25 words or fewer per sentence (Vale warning, not error).                                                                                                                                                                                                          | [GOV.UK sentence length][govuk-25], [GOV.UK clear language][govuk-clear]           |
| P2  | Second person, active voice, present tense; "we" only in tutorials.                                                                                                                                                                                                | [Google tense][g-tense], [Microsoft voice][ms-voice], [Diataxis tutorials][dx-tut] |
| P3  | Key fact first in page, section and paragraph; the first two words carry the gist.                                                                                                                                                                                 | [NN/g inverted pyramid][nng-pyramid], [NN/g F-pattern][nng-f]                      |
| P4  | Paragraphs of 3-4 sentences at most (soft).                                                                                                                                                                                                                        | [Microsoft all abilities][ms-abilities], [digital.gov writing][dg-writing]         |
| P5  | Task headings start with an imperative verb; concept headings are noun phrases; sentence case; no `Overview`.                                                                                                                                                      | [Google headings][g-headings], [GitLab task topic][gl-task]                        |
| P6  | Link text names the destination; never `here` or `this doc`.                                                                                                                                                                                                       | [Google link text][g-link], [markdownlint MD059][md059]                            |
| P7  | Steps: lead-in "To X:", numbered list, one action per step, condition before action, result in the same step, `Optional:` prefix.                                                                                                                                  | [Google procedures][g-proc], [Microsoft steps][ms-steps]                           |
| P8  | No filler: `easy`, `simply`, `just`, `obviously`, `in order to`, `leverage`, `currently`, `e.g.`, `i.e.`, `etc.`, `there is`, `there are`.                                                                                                                         | [Google word list][g-words], [GitLab style guide][gl-style]                        |
| P9  | No AI-isms: `delve`, `tapestry`, `testament`, `pivotal`, `crucial`, `robust`, `seamless`, `comprehensive`, `showcase`, `foster`, `serves as`, `not only... but also`, `It is important to note`, `In this section, we will`, trailing "-ing" significance clauses. | [Wikipedia signs of AI writing][wp-ai], [Vale package explorer][vale-explorer]     |
| P10 | No hedging unless the uncertainty is real; then state the condition.                                                                                                                                                                                               | [Google tense][g-tense]                                                            |
| P11 | One term per concept: Valkey (never `Redis`), podman-compose, TimescaleDB, hypertable, `sync_timedb`.                                                                                                                                                              | [Google word list][g-words], [Write the Docs principles][wtd]                      |
| P12 | Numerals for quantities; space before units (`64 GiB`); GiB or GB as the tool reports it; ISO dates (`2026-11-27`).                                                                                                                                                | [Google numbers][g-numbers], [Google units][g-units]                               |
| P13 | Current state only: no `previously`, `no longer`, changelog tables or planning IDs (`D5`, `finding 18`).                                                                                                                                                           | `docs-current-state-only.mdc`                                                      |
| P14 | The purpose line states the audience.                                                                                                                                                                                                                              | [Diataxis], [GitLab topic types][gl-topics]                                        |

## Visual standards

| ID  | Standard                                                                                                                                                                 | Basis                                                                             |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| V1  | One H1 on line 1; no skipped levels; H2 and H3, H4 only in long reference pages.                                                                                         | [Google headings][g-headings], [markdownlint rules][mdlint] (MD001, MD025, MD041) |
| V2  | No bold as a heading; bold only for UI labels and term-list terms; no bold or italics for emphasis.                                                                      | [GitLab text and formatting][gl-text], [Google highlights][g-highlights]          |
| V3  | Numbered list for a sequence, bullets for a set, term list for definitions; 2-7 parallel items.                                                                          | [Microsoft lists][ms-lists], [GitLab Markdown][gl-md]                             |
| V4  | Tables only for items with 2 or more attributes; 4 columns or fewer; one line per cell; an intro sentence; `None`, never blank.                                          | [Microsoft tables][ms-tables]                                                     |
| V5  | Every fence has a language tag: `bash`, `console`, `python`, `sql`, `ini`, `yaml`, `text`.                                                                               | [markdownlint rules][mdlint] (MD040)                                              |
| V6  | No `$` prompts in copyable `bash` blocks; `console` only for transcripts; input and output in separate blocks.                                                           | [Google code samples][g-samples], [markdownlint rules][mdlint] (MD014)            |
| V7  | `bash` and `sh` lines 68 characters or fewer, no heredocs; other code 120 characters.                                                                                    | Repo paste-safety hook; PyMarkdown MD013 code setting                             |
| V8  | GitHub alerts at top level only, at most 2 per page, never back-to-back; inside a step use plain `Warning:` text.                                                        | [GitHub alerts][gh-alerts]                                                        |
| V9  | Alert severity: `WARNING` for data loss, outage or irreversible steps; `CAUTION` for risky but recoverable; `IMPORTANT` for what success needs; `NOTE` and `TIP` rarely. | [GitHub alerts][gh-alerts], [Google notices][g-notices]                           |
| V10 | Mermaid for state machines and data flow, with `accTitle` and `accDescr`, and a takeaway sentence after.                                                                 | [GitHub diagrams][gh-diagrams], [Mermaid accessibility][mermaid-a11y]             |
| V11 | `<details>` only for long output, config dumps and logs; never steps or warnings; blank line after `</summary>`.                                                         | [GitHub collapsed sections][gh-details], [NN/g layer-cake][nng-layer]             |
| V12 | Images need alt text that carries the information; never screenshot terminal text.                                                                                       | [Google code syntax][g-syntax], [markdownlint rules][mdlint] (MD045)              |
| V13 | No emoji or `:shortcodes:`; status cells say Yes, No or Partial; badges only in the root README, at most 4 live signals.                                                 | [GitHub accessibility tips][gh-a11y], [AbilityNet emoji][abilitynet]              |
| V14 | One sentence per source line; split a line over about 120 characters at a clause.                                                                                        | [Semantic line breaks][sembr], [GitLab Markdown][gl-md]                           |
| V15 | `-` bullets; consecutive numbering (mdformat writes `01.` for lists of 10 or more, accepted); one blank line between blocks.                                             | [markdownlint rules][mdlint] (MD004, MD029, MD012)                                |
| V16 | Characters: see the character allowlist below.                                                                                                                           | [Microsoft all abilities][ms-abilities]; repo drift guard                         |
| V17 | Identifiers, paths, flags and INI keys in backticks; INI keys as `` `[SECTION] key` ``.                                                                                  | [Google code syntax][g-syntax]; drift guard INI check                             |
| V18 | Placeholders are `<UPPER_SNAKE>`, always inside a code span or fence, with a one-line legend under the block.                                                            | [GFM spec][gfm] (bare `<name>` parses as HTML)                                    |

### Character allowlist

Docs use ASCII plus three exceptions (this page itself is pure ASCII):

- The degree sign (U+00B0), for temperatures and angles.
- The micro sign (U+00B5), for units such as microseconds.
- Accented letters in proper names, spelled the way the name's owner spells them.

Everything else is out, including the section sign U+00A7 (write "section 4.5"), em and en dashes, arrows, ellipsis, smart quotes, check marks, emoji and non-breaking spaces.
Use `-`, `->`, `...`, `x` and `>=` instead.
Visual interest comes from alerts, tables, Mermaid and `<details>`, which are all ASCII.

## Positioning standards

| ID  | Standard                                                                                                                                                                                                     | Basis                                                                                       |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| S1  | Page anatomy: H1, 1-2 sentence purpose + audience, page-wide hazard alert if any, Prerequisites, quick answer in the first screen, body, Verify, Troubleshooting (symptom first), Related (5 links at most). | [NN/g scrolling][nng-scroll], [GitLab task topic][gl-task], [Good Docs templates][gooddocs] |
| S2  | Warnings go before the step they guard.                                                                                                                                                                      | [Google highlights][g-highlights]                                                           |
| S3  | No table of contents in pages (GitHub builds an outline); the root README keeps a short hand-written docs map.                                                                                               | [GitHub basic formatting][gh-basic]                                                         |
| S4  | Split a page that mixes quadrants, covers 2 or more independent tasks, or passes about 10 steps; merge topics of 1-2 sentences.                                                                              | [Diataxis], [Microsoft steps][ms-steps], [GitLab topic types][gl-topics]                    |
| S5  | Each fact lives on one page; other pages link to its heading anchor.                                                                                                                                         | [Write the Docs principles][wtd]                                                            |
| S6  | Headings are stable anchors; renaming one means fixing inbound `#fragment` links (the layout test checks them).                                                                                              | [GitHub basic formatting][gh-basic], [markdownlint rules][mdlint] (MD051)                   |
| S7  | No YAML front matter; the directory encodes the quadrant.                                                                                                                                                    | [GitHub YAML metadata][gh-yaml], [GitLab front matter MR][gl-frontmatter]                   |
| S8  | Root README order: title, one-line description, short background, install link, quick start, docs map, contributing, license.                                                                                | [standard-readme spec][std-readme], [Make a README][makeareadme]                            |
| S9  | Package READMEs say what the directory is, how to run its one command, and link into `docs/`.                                                                                                                | Repo decision                                                                               |
| S10 | Generated docs carry `` Generated by `<SCRIPT>`; do not edit `` as the first line after the H1; edit the generator instead.                                                                                  | Repo generators                                                                             |

## Page templates

Start every new page from its quadrant template.
Headings in angle brackets are filled in; the rest are literal.

```text
Tutorial
  # <OUTCOME>
  purpose + audience
  ## Before you begin
  ## Step N: <ACTION>        (each step ends with expected output)
  ## What you built
  ## Related

How-to
  # <VERB> <OBJECT>
  purpose + audience + "Use it when ..."
  [page-wide hazard alert]
  ## Prerequisites
  ## <TASK>                  ("To X:" + numbered steps)
  ## Verify the result
  ## Troubleshooting         (symptom first)
  ## Related

Reference
  # <THING>
  one sentence: what it describes and which component
  ## <SECTION>               (mirrors code or config; table or
                              term list, same pattern for every
                              entry, short example)

Explanation
  # <NOUN PHRASE READ AFTER "ABOUT">
  summary paragraph
  ## <ASPECT>
  ## Alternatives and trade-offs
  ## Related
```

Records start with the H1 and `Historical record, <DATE>. Not maintained.` on the next line.

## Editorial pass

Run this checklist on every new or rewritten evergreen page before you commit:

- Remove AI-isms, filler and hedges (P8-P10).
- Rewrite passive sentences that hide the actor.
- Split sentences over 25 words (P1).
- Put a language tag on every fence (V5) and remove `$` prompts (V6).
- Keep shell lines at 68 characters or fewer, with no heredocs (V7).
- Write placeholders as `<UPPER_SNAKE>` in a code span or fence, with a one-line legend under the block (V18).
- Write compose commands as `podman-compose -p hpcperfstats`, with no `-f` and no host `cd`.
- Standardize terms (Valkey, podman-compose, GiB) and the `` `[SECTION] key` `` INI form (P11, V17).
- Remove bold except UI labels and term-list terms (V2).
- Turn walls of prose into steps, tables or term lists where the content has that shape (V3, V4).
- Add one Mermaid diagram where prose describes a state machine or flow (V10).

## Consolidation method

Merging or moving existing pages keeps every unique technical detail and drops only facts proven stale.

1. Build a fact ledger with `scripts/doc_fact_coverage.py extract` and a hand list of prose caveats, saved under `test_runs/docs-consolidation/`.
2. Verify each fact against code and mark it `keep`, `fix` (with the pin) or `drop` (with evidence it is gone).
   Ask the owner about any fact code cannot settle.
3. Give each `keep` or `fix` fact one home page and heading; other copies become links (S5).
4. Draft fresh prose from the quadrant template; do not paste old paragraphs.
5. Run the editorial pass.
6. Reconcile with `scripts/doc_fact_coverage.py check <LEDGER> <DESTINATIONS>`, which fails if a `keep` or `fix` token is missing.
7. Move every pin in the same commit: tests, router patterns, rule globs, `EXTRA_DIST`, allowlists, generator paths, `.dockerignore` and inbound links.
   Delete the old file; leave no redirect stub.
8. Run the drift guard, the layout test, the linters and the pinned tests; add `make check` for monitor pages.

`<LEDGER>` is the ledger file; `<DESTINATIONS>` is the list of new pages.
The ledger format (columns, kinds and the keep, fix, drop and ask statuses) is in the docstring of `scripts/doc_fact_coverage.py`.

## Lint toolchain

All tools are pip packages in the host `.venv`, run as `repo: local` pre-commit hooks.

| Tool (version)                                                                                    | Stage                 | Role                                                                                                          |
| ------------------------------------------------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------- |
| [mdformat] 0.7.22 with `mdformat-gfm` 1.0.0, `mdformat-gfm-alerts` 2.1.0, `mdformat-tables` 1.0.0 | commit                | Formatter, `--wrap keep --number` from `.mdformat.toml`; keeps one-sentence lines, tables, alerts and fences. |
| [PyMarkdown] (`pymarkdownlnt` 0.9.42)                                                             | commit, changed files | Structure lint with `.pymarkdown.json`; MD013 on for code only; MD051 and MD059 on.                           |
| [Vale] 3.28.1 (pip wrapper) with the local `HPC` style                                            | commit                | Prose lint with `.vale.ini`; skips code spans and fences.                                                     |
| codespell 2.4.3                                                                                   | commit                | Spelling with `.codespellrc`; ignore words `cna` and `unparseable`.                                           |
| `hpcperfstats/tests/test_docs_layout.py`                                                          | pre-push              | Offline link, anchor and reference-link check, plus naming and page anatomy.                                  |

The Vale rules split into two levels:

- Error: AI-isms, filler, hedges, and banned terms such as `Redis` (use Valkey).
- Warning: sentence length and passive voice.

### Vale first run

The Vale pip wrapper installs a placeholder, not the binary.
On its first run it downloads the Vale binary from GitHub once ("vale not found. Downloading it...") into the wrapper's `site-packages` directory.
After that it works fully offline.
A fresh `.venv` with no network access fails that first run with a clear message; run the hook once while online.

Vale reads styles from the local `.vale/styles/` directory and never runs `vale sync`, so hooks need no network.

### Vendored style versions

This table lists each vendored Vale style and its version, so an upgrade is a reviewed diff.

| Style | Source                                 | Version              |
| ----- | -------------------------------------- | -------------------- |
| `HPC` | `.vale/styles/HPC/` in this repository | Local v1, 2026-11-27 |

Add a row when you vendor a third-party package such as Google or ai-tells from the [Vale package explorer][vale-explorer].

### Scope of the hooks

Each hook's `files:` pattern covers the evergreen pages that follow this guide and widens as pages migrate.
The four hooks share one pattern, the `md_lint_files` YAML anchor in `.pre-commit-config.yaml`, and one exclude, `md_lint_exclude`.
The hooks exclude `docs/history/`, `docs/measurements/`, generated docs (lint the generator templates instead), `node_modules` and `test_runs`.
After mdformat touches a doc that a test pins, run that test, because mdformat changes code-span escapes.

## Related

- [Diataxis]
- [Google developer documentation style guide][g-highlights]
- [GitLab documentation style guide][gl-style]
- [Microsoft writing style guide][ms-voice]
- [Write the Docs principles][wtd]

[abilitynet]: https://abilitynet.org.uk/news-blogs/four-ways-make-emojis-accessible
[dg-writing]: https://digital.gov/guides/plain-language/writing
[diataxis]: https://diataxis.fr/
[dx-tut]: https://diataxis.fr/tutorials/
[g-headings]: https://developers.google.com/style/headings
[g-highlights]: https://developers.google.com/style/highlights
[g-link]: https://developers.google.com/style/link-text
[g-notices]: https://developers.google.com/style/notices
[g-numbers]: https://developers.google.com/style/numbers
[g-proc]: https://developers.google.com/style/procedures
[g-samples]: https://developers.google.com/style/code-samples
[g-syntax]: https://developers.google.com/style/code-syntax
[g-tense]: https://developers.google.com/style/tense
[g-units]: https://developers.google.com/style/units-of-measure
[g-words]: https://developers.google.com/style/word-list
[gfm]: https://github.github.com/gfm/
[gh-a11y]: https://github.blog/developer-skills/github/5-tips-for-making-your-github-profile-page-accessible/
[gh-alerts]: https://github.blog/changelog/2023-12-14-new-markdown-extension-alerts-provide-distinctive-styling-for-significant-content/
[gh-basic]: https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax
[gh-details]: https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/organizing-information-with-collapsed-sections
[gh-diagrams]: https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams
[gh-yaml]: https://github.blog/news-insights/product-news/viewing-yaml-metadata-in-your-documents/
[gl-frontmatter]: https://gitlab.com/gitlab-org/gitlab-foss/-/merge_requests/3072
[gl-md]: https://docs.gitlab.com/development/documentation/styleguide/markdown/
[gl-style]: https://docs.gitlab.com/development/documentation/styleguide/
[gl-task]: https://docs.gitlab.com/development/documentation/topic_types/task/
[gl-text]: https://docs.gitlab.com/development/documentation/styleguide/text_and_formatting/
[gl-topics]: https://docs.gitlab.com/development/documentation/topic_types/
[gooddocs]: https://www.thegooddocsproject.dev/template
[govuk-25]: https://insidegovuk.blog.gov.uk/2014/08/04/sentence-length-why-25-words-is-our-limit/
[govuk-clear]: https://guidance.publishing.service.gov.uk/writing-to-gov-uk-standards/writing-guidelines/clear-language/
[makeareadme]: https://www.makeareadme.com/
[md059]: https://github.com/DavidAnson/markdownlint/blob/main/doc/md059.md
[mdformat]: https://github.com/hukkin/mdformat
[mdlint]: https://github.com/DavidAnson/markdownlint/blob/main/README.md
[mermaid-a11y]: https://mermaid.js.org/config/accessibility.html
[ms-abilities]: https://learn.microsoft.com/en-us/style-guide/accessibility/writing-all-abilities
[ms-lists]: https://learn.microsoft.com/en-us/style-guide/scannable-content/lists
[ms-steps]: https://learn.microsoft.com/en-us/style-guide/procedures-instructions/writing-step-by-step-instructions
[ms-tables]: https://learn.microsoft.com/en-us/style-guide/scannable-content/tables
[ms-voice]: https://learn.microsoft.com/en-us/style-guide/top-10-tips-style-voice
[nng-f]: https://www.nngroup.com/articles/f-shaped-pattern-reading-web-content/
[nng-layer]: https://www.nngroup.com/articles/layer-cake-pattern-scanning/
[nng-pyramid]: https://www.nngroup.com/articles/inverted-pyramid/
[nng-scroll]: https://www.nngroup.com/articles/scrolling-and-attention/
[pymarkdown]: https://pypi.org/project/pymarkdownlnt/
[sembr]: https://sembr.org/
[std-readme]: https://github.com/RichardLitt/standard-readme
[vale]: https://docs.vale.sh/
[vale-explorer]: https://vale.sh/explorer
[wp-ai]: https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
[wtd]: https://www.writethedocs.org/guide/writing/docs-principles/
