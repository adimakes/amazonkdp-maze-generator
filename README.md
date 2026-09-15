# maze-book

A deterministic, data-driven generator for KDP-ready puzzle-book interiors.

The engine knows nothing about Halloween, about Jim, or about candy. A book is a
**data-only package**: a folder, some SVGs, and one `book.json`. Point the CLI at
it and you get a print-ready interior PDF, an answer key, and a machine-readable
report saying why it is safe to upload.

```bash
uv sync --extra dev
uv run maze-book book build books/jims-halloween-maze-adventure
```

That produces a 112-page 8.5 × 11 in interior — 50 mazes, 50 story pages, six
solution pages — in about twenty seconds, and exits non-zero if any one of
nineteen preflight checks fails.

---

## Environment: uv, and only uv

This project uses **uv exclusively**. Never `pip`, never `python -m venv`, never a
bare `python`. Every command below starts with `uv run`, which resolves the
environment from `uv.lock` before running anything — so the interpreter, the
dependency set and the lockfile can never drift apart.

```bash
uv sync --extra dev        # create or refresh .venv from pyproject.toml + uv.lock
uv run maze-book --help    # run the CLI
uv run pytest -q           # the full test suite
uv add <pkg>               # add a runtime dependency (edits pyproject + lock)
uv add --dev <pkg>         # add a dev dependency
```

If you have never used uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
You do not activate the virtualenv and you do not install this package — `uv run`
handles both.

### External tools

`qpdf`, `pdftotext` and `pdftoppm` are required by preflight:

```bash
brew install qpdf poppler          # macOS
apt-get install qpdf poppler-utils # Debian/Ubuntu
```

Their absence is a **preflight failure**, never a silent skip — a skipped check
that reads as a pass is how an unchecked PDF gets announced as print-ready. For
local work `--lenient` reports the skip explicitly instead of failing.

---

## Starting a new book

Copy a book package, edit two things, build:

```bash
cp -r books/jims-halloween-maze-adventure books/my-new-book
# edit books/my-new-book/book.json  -> id, title, seed, mazeCount, profileId, scenes
# replace books/my-new-book/assets/**/*.svg with your artwork
uv run maze-book book build books/my-new-book
```

**No source change is ever required** to change theme, story, asset names,
collectible variants, maze count, trim size or difficulty. If you find yourself
wanting to edit `src/`, the answer is a config knob or a new profile.

A book package looks like this. The folder names are part of the contract and are
case-sensitive:

```text
books/<book-id>/
├── book.json                       # the entire book: print, layout, generation, assets, content
├── assets/
│   ├── beginning-vectors/          # the start marker
│   ├── ending-vectors/             # the finish marker
│   ├── maze-vectors/
│   │   ├── dead-end/               # markers for wrong turns
│   │   └── collectibles/           # the things to collect
│   └── page-vectors/               # optional story-page decoration
└── front-matter.pdf                # optional, single pages, odd page count
```

Story text lives inline in `book.json` under `content.scenes[]`, one
title/text scene per maze. There is no second content file.

### The two rules a new book must obey

**Front matter must have an odd page count.** Front matter is read from the PDF
itself — you never retype the count into `book.json` — and an odd count is what
puts scene 1's story page on an even (left) page. Add or remove front matter
**two pages at a time**; a one-page change flips every spread in the book.

**Assets must pass the SVG subset.** Run `uv run maze-book book validate <book>`
and it will tell you, per file, which rule failed and what it measured. See
[SVG assets](#svg-assets) below.

---

## Commands

```bash
uv run maze-book book validate       books/<book-id>
uv run maze-book book generate-mazes books/<book-id> [--only 1:5] [--force]
uv run maze-book book assemble       books/<book-id> [--no-editable]
uv run maze-book book preflight      books/<book-id> [--lenient]
uv run maze-book book build          books/<book-id>   # all of the above

uv run maze-book maze generate books/<book-id> --index 7
uv run maze-book maze validate books/<book-id> --index 7
uv run maze-book maze render   books/<book-id> --index 7 --format svg,pdf
```

`python cli.py …` works identically from a fresh checkout.

| Flag | Meaning |
|---|---|
| `--seed N` | override `book.seed` for this run |
| `--profile ID` | override `book.profileId` |
| `--only START:END` | restrict to a 1-based maze range (`3`, `:10`, `40:` all work) |
| `--force` | bypass the artifact cache and regenerate |
| `--output DIR` | output root (default `output/`) |
| `--format svg,pdf,json` | what `maze render` should write |
| `--editable` / `--no-editable` | write the live-text proof PDF (default on) |

The commands are deliberately **not** interchangeable. `maze render` and `book
assemble` read cached artifacts and refuse to generate; only `book
generate-mazes` and `book build` create mazes. "Render what I already checked"
and "make me something new" are different intentions, and silently doing the
second means the page you proofed and the page you print came from different
mazes.

### Exit codes

These are a public contract; CI can depend on them.

| Code | Meaning |
|---|---|
| `0` | success |
| `2` | invalid input or configuration |
| `3` | maze generation or semantic-validation failure |
| `4` | rendering or assembly failure |
| `5` | PDF or preflight failure |

---

## Output

```text
output/<book-id>/
├── normalized-book.json        # exactly what this build was made from, with hashes
├── mazes/001.json              # canonical MazeData
├── mazes/001.analysis.json     # routes, scores, constraint verdicts
├── mazes/001.svg               # inspection vector
├── pages/page-001.pdf          # one file per physical page
├── page-plan.json              # every page's number, side, kind and facing pair
├── contact-sheet.png           # all mazes and their answers on one image
├── preflight.json              # machine-readable verdict
├── book-interior-editable.pdf  # live text, for proofreading
└── book-interior.pdf           # the file you upload
```

The **contact sheet** is the one to look at first. Constraints catch per-maze
defects; they cannot see that five mazes in a row look alike, that a solution
hugs one edge, or that the finale reads easier than the act before it. Those are
judgements about the book, and the contact sheet is how you make them in a few
seconds.

---

## How it works

The pipeline, and the decisions that shape it:

**One canonical maze representation.** `MazeData` stores an *open-edge* graph —
pairs of adjacent cells with no wall between them. Walls are derived from it
exactly once, in `rendering/geometry.py`. No renderer re-infers a wall, because
two inference sites is how the SVG and the PDF come to disagree about a single
wall, and the disagreement is invisible until a child hits a dead end the answer
key calls a corridor.

**`mazelib` is quarantined.** Only `generation/adapter_mazelib.py` imports it.
Replacing or removing the dependency touches exactly one file.

**Loops are measured, never assumed.** The loop count is the cyclomatic number
`k = E − V + C`, computed from the graph that actually exists. A profile asking
for `loops: 3..4` is checked against a measurement, not against how many edges we
tried to add.

**Braid-and-rebalance.** A perfect maze has far more dead ends than any profile
allows, and removing a dead end adds a loop — which blows the loop budget almost
immediately. So dead-end removal is paired with *rebalancing*: removing a cycle
edge whose endpoints both have degree ≥ 3, which lowers `k` without creating a
new dead end. See `generation/braid.py`.

**Exact route enumeration, with honest caps.** All simple start→finish paths are
enumerated by DFS with a visited bitset, bounded by `maxRoutes`,
`maxSearchNodes` and `timeBudgetSeconds`. If a bound trips, the analysis is
flagged `exact: false` and the attempt is **rejected** — a puzzle whose answer key
is only probably unique is not shippable.

**Candy placement is a guided search.** Random sampling essentially never
satisfies C1–C8 together. The placer picks a target best route, seeds it with
cells exclusive to that route, then lifts decoys to a configurable fraction of
the best. Cells are weighted by route frequency, favouring the 30–70% band: a
cell on every route is free candy, a cell on one route is punishing.

**Determinism is derived, not global.** Nothing calls module-level `random`.
Every stochastic step draws from a stream derived from `(book.seed, book.id,
mazeIndex, attempt, purpose)` via blake2b, so regenerating maze 7 cannot reshuffle
maze 8. `MazeData` JSON uses stable key order and sorted edges, so identical
inputs produce byte-identical files.

**Physical page parity is a correctness property.** Even pages are left (verso),
odd are right (recto). `book/page_plan.py` is a pure planning step that emits an
auditable `page-plan.json`; parity is asserted in preflight, not hoped for.

**Two PDFs, on purpose.** `book-interior-editable.pdf` keeps live text for
proofreading and text extraction; `book-interior.pdf` is the production file. For
v1 both embed their fonts rather than outlining text — outlining makes
proofreading and text preflight harder, so the check is "every font embedded",
not "no fonts present".

---

## Difficulty profiles

`profiles/*.json` map maze-index ranges to grid size, loop and dead-end budgets,
candy counts, margins and stroke weights. Changing difficulty means editing data.

| Profile | Book | Grids |
|---|---|---|
| `child_6_7` | 30 mazes, ages 6–7 | 6×6 → 10×10 |
| `halloween_6_10` | 50 mazes, ages 6–10 | 8×8 → 18×18 |
| `older_child` | 48 mazes, ages 8–11 | 10×10 → 16×16 |
| `adult` | 30 mazes | 16×16 → 18×18 |

A profile is a set of promises the generator has to be able to keep, and three
numbers in particular will quietly make a profile unsatisfiable. The test suite
checks all three, but they are worth knowing before you write one:

- **`deadEndDepth.min` must be 1.** A dead end is created by opening an edge at a
  degree-1 cell, which leaves a depth-1 stub. The rebalancer has no move that
  deepens one on request, so a floor above 1 rejects shapes for a property that
  cannot be asked for.
- **`temptingFraction × candies` must leave room under the best score.** A decoy
  counts as tempting at `fraction × best`, and the best must be unique — so a
  tempting route needs a score in `[ceil(fraction × best), best)`. At 0.9 with
  five candies that interval is empty.
- **Every `*Scale` must be ≤ `1 − 2 × cellClearanceFraction`.** Assets are centred
  in their cell and must stay inside the safe inset.

---

## SVG assets

Assets are validated against a hard subset, and the *same* subset definition
drives the internal SVG→PDF converter. One subset, two consumers — never two
dialects, because a second SVG code path is how the on-screen proof and the
printed page diverge.

| Rule | Requirement |
|---|---|
| Format | SVG 1.1, `viewBox="0 0 100 100"`, no `width`/`height` |
| Elements | `svg`, `g`, `path` only (plus ignored `title`/`desc`/`metadata`) |
| Paint | `fill="#000000"` only |
| **Strokes** | **none, anywhere** — convert strokes to filled paths |
| Composition | centred within 12 units, ≥ 6 units of padding, ≤ 85% coverage |
| Detail | no feature or gap thinner than 7 units, ≤ 12 subpaths |
| Forbidden | text, raster, gradients, opacity, filters, external references |

**No `<circle>`, `<rect>`, `<ellipse>` or `<line>`** — every shape is a `<path>`.

The stroke rule is the one that surprises people. Stroke width does not scale
with the icon: a 6-unit line that reads as confident on a 0.6 in start marker
becomes a black blob at the 5.5 mm of an 18×18 finale collectible. And 7 units is
a *width*, not a radius — every limb must be at least 7 units thick and every gap
at least 7 units wide, which is why the placeholder art is so chunky. At finale
size, 7 units is 0.39 mm, about the thinnest mark a print-on-demand press holds
on cream stock.

`tools/make_placeholder_assets.py` generates the shipped placeholder set from a
cubic-only primitive library and can check its own output:

```bash
uv run python tools/make_placeholder_assets.py books/my-new-book --check
```

---

## Development

```bash
uv run pytest -q                                  # everything (~50 s)
uv run pytest -q -m "not slow"                    # fast loop (~24 s)
uv run pytest tests/test_braid.py -q               # one file
uv run pytest -k "parity" -q                       # one pattern
uv run pytest tests/test_page_plan.py::test_the_halloween_target_is_112_pages_exactly -q
```

Tests marked `slow` build whole books. They are the ones that catch the failures
that matter: three shipped profiles once passed every unit test and could not
generate a single maze between them, because nothing asked them to.

`maze-book-repository-prd.md` is the authoritative spec. **§17 is the normative
locked contract** and overrides looser wording earlier in the document;
`additional-context-old-file.md` is an earlier single-book PRD kept only for
provenance.
