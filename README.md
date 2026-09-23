# maze-book

A deterministic, data-driven generator for KDP-ready puzzle-book interiors.

The engine knows nothing about Halloween, about Jim, or about candy. A book is a
**data-only package**: a folder holding `input/`, and the build fills in
`output/` and `build/` beside it. Point the CLI at the folder and you get a
print-ready interior PDF, a cover, an answer key, and a machine-readable report
saying why it is safe to upload.

## Start here

Three commands, from a fresh clone to a finished book:

```bash
uv sync --extra dev                                        # 1. install everything
uv run pytest -q                                           # 2. check it works (740 tests)
uv run maze-book book build books/jims-halloween-maze-adventure   # 3. build the book
```

Step 1 is the only setup there is. `uv sync` reads `uv.lock` and creates `.venv`
with the exact pinned versions — you do not create a virtualenv, you do not
`pip install`, and you do not activate anything. Every command in this README
starts with `uv run`, which resolves that environment before running.

You also need **qpdf**, **poppler** (for `pdftotext` and `pdftoppm`) and a
working `fonts/` folder, all of which preflight checks for and names if missing:

```bash
brew install qpdf poppler        # macOS
```

Step 3 produces a 110-page 8.5 × 11 in interior — 50 mazes, 50 story pages, six
solution pages — in about twenty seconds, and exits non-zero if any one of
twenty preflight checks fails. The two files to upload land in
`books/jims-halloween-maze-adventure/output/`.

**Then read [Make a second book](#make-a-second-book).** That is the whole
workflow: copy a folder, drop in pictures, edit one JSON, run three commands.

> **If you are an AI agent onboarding to this repository:** read `CLAUDE.md`
> next for the architecture and the decisions that are load-bearing, then
> `.claude/skills/new-book/SKILL.md` before writing or changing any book. The
> README tells you how to run it; those two tell you why it is built this way
> and what the engine cannot decide for you.

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

## Make a second book

A book is a folder. Copy it, change what's inside, build.

```bash
cp -r books/jims-halloween-maze-adventure books/my-new-book
```

Then four edits, in this order:

**1. Put your pictures in `input/artwork/`.** Anything PIL can open, or an SVG
(rendered with `rsvg-convert`, so strokes are fine). Black line art on white is
what works; the importer decides which pixels are ink and stops.

**2. Tell it which picture is which**, in `input/artwork/artwork.json`:

```json
{ "assets": [
  { "target": "collectibles/lollipop.svg", "source": "my-lollipop.jpeg" },
  { "target": "dead-ends/ghost.svg",       "source": "my-ghost.jpeg" }
] }
```

The `target` folder decides the job: `start`, `finish`, `collectibles`,
`dead-ends`, `decorations`.

```bash
uv run python tools/import_rasters.py books/my-new-book
```

That writes print-ready PNGs into `input/assets/`, each sized for how big it
actually prints, and tells you per file whether it passed.

**3. Edit `input/book.json`.** The whole book is in there. The fields you will
always change:

```json
{ "book": { "id": "my-new-book", "title": "...", "seed": 12345, "mazeCount": 50 },
  "content": { "scenes": [ { "number": 1, "title": "...", "text": "..." } ] } }
```

`book.id` must match the folder name. `seed` is what makes your mazes yours —
change it and you get a different fifty.

Each scene can name its own finish marker with `finishVector`, a file in
`input/assets/finish/`: the thing the story sends the character to, so the
maze ends at the costume box in the attic scene and at the gate in the garden
scene. A scene without one finishes at `assets.finishAsset`.

**4. Build.**

```bash
uv run python tools/make_front_matter.py --book books/my-new-book
uv run maze-book book build books/my-new-book
uv run python tools/make_cover.py --book books/my-new-book
```

Upload the two files in `books/my-new-book/output/`. That folder has nothing
else in it.

### A translation, or another edition

Copy the folder again and change only the words:

```bash
cp -r books/my-new-book books/my-new-book_spanish
# edit books/my-new-book_spanish/input/book.json: id, title, subtitle, locale,
# the scenes, the front matter, and content.labels
uv run python tools/make_front_matter.py --book books/my-new-book_spanish
uv run maze-book book build books/my-new-book_spanish
```

Set `book.seedId` to the original's id in every edition. Mazes are seeded from
it rather than from `book.id`, so every edition prints the same fifty mazes and
the same answer key.

`content.labels` translates the words the engine prints on every page: START
and FINISH, the tally prompt, "Total", the "Best possible" line (preflight reads
the score back with the same template, so keep words before `{n}`), the "Maze
{n}" folio, the band names, and the front matter's headings and copyright line.
Any key left out stays in English. The Halloween book has two editions,
`books/jims-halloween-maze-adventure_spanish` and `_german`.

**Editions must stay in step.** Everything except the words has to be
identical: print, layout, generation, assets, outputs, the seed, and each
scene's `pageVector` and `finishVector`. `tests/test_editions.py` fails when
they drift. Change one edition's config or artwork and you change all of them.

The cover is the one thing a copy cannot bring along: a cover whose artwork has
the title painted in needs new artwork per language, and KDP requires the cover
title to match the book's metadata. The editions have no `cover` block until
that artwork exists.

### What lives where

```text
books/<book-id>/
├── input/                  ← everything you supply. Edit freely.
│   ├── book.json           the whole book: print, layout, generation, assets, content
│   ├── front-matter.pdf    optional; built by tools/make_front_matter.py
│   ├── artwork/            your pictures + artwork.json saying which is which
│   └── assets/             print-ready, generated from artwork/
│       ├── start/          the start marker
│       ├── finish/         the finish marker
│       ├── collectibles/   the things to collect
│       ├── dead-ends/      markers for wrong turns
│       └── decorations/    optional page decoration
├── output/                 ← the two files you upload to KDP. Nothing else.
│   ├── book-interior.pdf
│   └── book-cover.pdf
└── build/                  ← working files. Regenerable; delete any time.
    ├── contact-sheet.png   all 50 mazes and answers on one image — look here first
    ├── page-plan.json      every page's number, side, kind and facing pair
    ├── preflight.json      machine-readable verdict
    ├── book-interior-editable.pdf   live text, for proofreading
    ├── mazes/              per-maze JSON, analysis and inspection SVG
    └── pages/              one PDF per physical page
```

The split is by who the file is for. If something in `output/` is not going to
KDP, it is in the wrong folder.

**No source change is ever required** to change theme, story, asset names,
collectible variants, maze count, trim size or difficulty. If you find yourself
wanting to edit `src/`, the answer is a config knob or a new profile.

> **Writing the book, rather than building it:** `.claude/skills/new-book/SKILL.md`
> carries the judgement this README does not — which supplied drawing survives at
> 5 mm, how to keep fifty scenes from sounding like one scene written fifty
> times, what a profile may promise, and what two rounds of review against real
> printed pages turned up. Claude Code loads it with `/new-book`; read it
> yourself before starting a package.

### The two rules a new book must obey

**Front matter must have an odd page count.** It is read from the PDF itself —
you never retype the count into `book.json` — and an odd count is what puts
scene 1's story page on an even (left) page. Add or remove front matter **two
pages at a time**; a one-page change flips every spread in the book.

**Assets must pass validation.** Run `uv run maze-book book validate <book>` and
it tells you, per file, which rule failed and what it measured. See
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
books/<book-id>/
├── output/                     # what you upload, and only that
│   ├── book-interior.pdf
│   └── book-cover.pdf
└── build/                      # everything the build needed on the way
    ├── normalized-book.json    # exactly what this build was made from, with hashes
    ├── mazes/001.json          # canonical MazeData
    ├── mazes/001.analysis.json # routes, scores, constraint verdicts
    ├── mazes/001.svg           # inspection vector
    ├── pages/page-001.pdf      # one file per physical page
    ├── page-plan.json          # every page's number, side, kind and facing pair
    ├── contact-sheet.png       # all mazes and their answers on one image
    ├── preflight.json          # machine-readable verdict
    └── book-interior-editable.pdf   # live text, for proofreading
```

The cover is built separately, because its spine width depends on the finished
interior's page count:

```bash
uv run python tools/make_cover.py --book books/my-new-book
```

It reads that count out of the built PDF rather than out of `book.json`. A spine
measured against a stale number is a cover that arrives folded in the wrong
place, and no amount of care in the file fixes it afterwards.

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

**Vector assets** are validated against a hard subset, and the *same* subset
definition drives the internal SVG→PDF converter:

| Rule | Requirement |
|---|---|
| Format | SVG 1.1, `viewBox="0 0 100 100"`, no `width`/`height` |
| Elements | `svg`, `g`, `path` only (plus ignored `title`/`desc`/`metadata`) |
| Paint | `fill="#000000"` only |
| **Strokes** | **none, anywhere** — convert strokes to filled paths |
| Composition | centred within 12 units, ≥ 6 units of padding, ≤ 85% coverage |
| Detail | no feature or gap thinner than the printed-size rule below, and a subpath budget that scales with it |
| Forbidden | text, raster, gradients, opacity, filters, external references |

**Raster assets** are judged on the two questions that decide whether a picture
prints:

| Rule | Requirement |
|---|---|
| Format | PNG, square, pure black and white — no level between 0 and 255 |
| Composition | ≥ 4% padding, ink centred within 12%, ≤ 85% coverage |
| Resolution | ≥ 300 dpi at the size it is *drawn*, which preflight reads out of the finished PDF |

Grey is the one thing a monochrome press cannot take: it halftones. JPEG is
rejected for the same reason — its artefacts around a hard black edge are grey.

**No `<circle>`, `<rect>`, `<ellipse>` or `<line>`** — every shape is a `<path>`.

The stroke rule is the one that surprises people. Stroke width does not scale
with the icon: a 6-unit line that reads as confident on a 0.6 in start marker
becomes a black blob at the 5.5 mm of an 18×18 finale collectible.

**The minimum feature size is not a constant.** 0.39 mm is about the thinnest
mark a print-on-demand press holds on cream stock, and that is the rule. What it
comes to in viewBox units depends on how big the artwork prints: 7 units at the
5.5 mm of a finale collectible, but only 2.6 units on a 0.6 in page decoration,
which has six times the room. `AssetProfile.for_print_size()` states the
millimetre once and lets the units follow, and `profiles_for_roles()` reads every
size off the book's own profile and layout — the smallest cell in the last band,
the marker fraction, the story page. So each file is judged by the rule its
printed size earns, and `book validate` prints which rule it used:

```text
assets      25 SVG(s) pass the 18.5 subset
```

It is a *width*, not a radius: every limb must be at least that thick and every
gap that wide, which is why icon art has to be chunky.

### Turning supplied artwork into assets

If the artwork arrives as a picture, it ships as a picture:

```bash
uv run python tools/import_rasters.py books/my-new-book
```

That thresholds each source with Otsu, crops to the ink, pads to a centred
square, and writes a bitonal PNG sized at 600 dpi for the printed size that
asset's role actually reaches.

**Tracing artwork into the vector subset loses the drawing.** Thresholding,
morphology and curve fitting each throw something away, and what comes out the
far end validates cleanly while looking nothing like the source — a ghost with
no eyes, a pumpkin bucket with no face. The subset is the right contract for
artwork you are *drawing* as flat shapes. It is the wrong one for artwork that
arrives as a picture.

A book may ship either, or both. The contract is the folder and the composition,
not the file format.

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
