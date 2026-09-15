# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A **generalized, data-driven generator for KDP-ready puzzle-book interiors**. The engine
(`src/maze_book/`) contains no book-specific knowledge. A book is a *data-only package*:

```
books/<book-id>/
  book.json          # the entire book contract (print, layout, generation, assets, content)
  assets/            # monochrome SVGs in fixed, case-sensitive folders
  front-matter.pdf   # optional; supplied pre-made
```

Adding a new book = new folder + assets + `book.json` (scene text lives inline in
`book.json` under `content.scenes[]`) → `book build` → a print-ready interior PDF.
**Never** add book-specific branching to `src/`; add a config knob or a profile instead.

`maze-book-repository-prd.md` is the authoritative spec. **§17 is the normative locked
contract** (repo layout, `book.json` shape, `MazeData`/`MazeAnalysis` schemas, pipeline,
CLI, output paths, exit codes) and overrides any looser wording earlier in the document.
`additional-context-old-file.md` is an earlier single-book PRD kept for provenance; the
parts worth keeping have already been merged into §17/§18. Do not treat it as normative.

## Environment: uv only

This project uses **uv exclusively** — never `pip`, `python -m venv`, or a bare `python`.

```bash
uv sync --extra dev            # create/refresh .venv from pyproject.toml + uv.lock
uv run maze-book --help        # run the CLI
uv run pytest -q               # full test suite
uv run pytest tests/test_candy_constraints.py -q          # one file
uv run pytest -k "determinism" -q                         # one pattern
uv run pytest tests/test_pipeline.py::test_end_to_end -q   # one test
uv add <pkg>                   # add a runtime dep (edits pyproject + lock)
uv add --dev <pkg>             # add a dev dep
```

Slow tests are marked `@pytest.mark.slow`; `uv run pytest -q -m "not slow"` for the fast
loop. External binary `qpdf` is required by preflight (`brew install qpdf`); its absence
must surface as a preflight failure, never a silent skip.

## Build commands

```bash
uv run maze-book book validate      books/jims-halloween-maze-adventure
uv run maze-book book generate-mazes books/jims-halloween-maze-adventure --only 1:5
uv run maze-book maze render        books/jims-halloween-maze-adventure --only 1 --format svg
uv run maze-book book assemble      books/jims-halloween-maze-adventure
uv run maze-book book preflight     books/jims-halloween-maze-adventure
uv run maze-book book build         books/jims-halloween-maze-adventure   # full pipeline
```

Everything lands under `output/<book-id>/` (see PRD §17.14). `--force` bypasses the
content-hash cache; `--only START:END` restricts to a 1-based maze range.

## Architecture: the load-bearing decisions

**1. One canonical maze representation.** `model/maze_data.py::MazeData` holds an
*open-edge* grid graph (`openEdges` = pairs of adjacent cells with no wall between them),
plus start/finish/assets. It is the single source of truth. Walls are *derived* from open
edges exactly once, in `rendering/geometry.py`. Renderers (SVG, PDF page, solution
thumbnail) consume that shared geometry — **no renderer may re-infer walls**, because two
inference sites is how SVG and PDF silently disagree.

**2. `mazelib` is quarantined behind one adapter.** Only
`generation/adapter_mazelib.py` may import `mazelib`. It implements the
`BaseMazeGenerator` Protocol from `generation/base.py` and returns a `MazeData`. Swapping
or removing the dependency must touch exactly one file.

**3. Loops are measured, never assumed.** The loop count is the cyclomatic number
`k = E − V + C` computed from the actual graph (`simulation/graph.py`). A profile asking
for `loops: 3..4` is checked against a measurement, not against how many edges we tried
to add.

**4. Braid-and-rebalance is why the profiles are satisfiable.** A perfect maze
(spanning tree) has far more dead ends than the profiles allow, and naively removing a
dead end adds a loop — which quickly blows the loop budget. So dead-end removal (add an
edge at a degree-1 cell) is paired with *rebalancing* (remove a cycle edge whose two
endpoints both have degree ≥3, which lowers `k` without creating a new dead end). See
`generation/braid.py`. Loops must also be *meaningful*: new edges are chosen between
cells that are far apart in the current graph (`minLoopLength`), never closing a 2×2
pocket.

**5. Exact route enumeration, with honest caps.** `simulation/routes.py` enumerates all
simple start→finish paths by DFS with a visited bitset and reachability pruning, bounded
by `maxRoutes` / `maxSearchNodes` / `timeBudgetSeconds`. If a bound trips, the analysis is
flagged `exact: false` and the attempt is rejected — a puzzle whose answer key is only
probably unique is not shippable. Scoring is a popcount over
`placement_mask & route_mask`.

**6. Candy placement is a guided constrained search, not sampling.** Random sampling
essentially never satisfies C1–C8 together. `simulation/candy.py` picks a target best
route, seeds it with cells exclusive to that route (to create the winning margin), then
adds shared cells to lift decoys to ≥60% of best. Cells are weighted by *route
frequency*, favouring the 30–70% band: cells on every route are free candy, cells on one
route are punishing. C1–C8 are defined once in `simulation/constraints.py` and are the
only place a maze is judged puzzle-valid.

**7. Determinism is derived, not global.** Never call `random` module-level functions.
Every stochastic step draws from a stream derived from
`(book.seed, book.id, mazeIndex, attempt, purpose)` via `model/seeds.py`, so regenerating
maze 7 cannot reshuffle maze 8, and topology/endpoints/candy/asset-variant/page-decoration
choices are independent. `MazeData` JSON uses stable key order + sorted `openEdges` + UTF-8
so identical inputs produce **byte-identical** files.

**8. Profiles carry difficulty; `book.json` references one.** `profiles/*.json` map maze
index ranges → grid size, candy/loop/dead-end ranges, margin, wall stroke width. The
Halloween act table is normative (PRD §17.4). Changing difficulty means editing data.

**9. Physical page parity is a correctness property.** Even pages are left (verso), odd
pages are right (recto). Story-left/maze-right facing pairs only work if the front matter
has an **odd** page count, so front-matter pages are counted from the actual PDF and blank
pages are inserted to preserve parity. `book/page_plan.py` emits an auditable
`page-plan.json`; parity is asserted in preflight, not hoped for.

**10. Two PDFs, on purpose.** `book-interior-editable.pdf` keeps live text (proofreading,
spellcheck, and the `pdftotext` markers preflight looks for); `book-interior.pdf` is the
production file. For v1 **both embed their fonts rather than outlining text** — §17.11
defers outlining because it makes proofreading and text preflight harder, and §18.8
restates the check accordingly as "every font embedded", not "no fonts present". The two
files are therefore identical today; keeping them separate is what lets a future
production profile add outlining to one without touching the other's contract. Both are
byte-deterministic for a fixed input.

## Preflight is the gate

`preflight/` reads the answer back out of the finished PDF rather than trusting
what the renderer was handed — comparing inputs would only prove the renderer
agrees with itself. The mandatory cross-check (§18.8) extracts every printed
"Best possible" with `pdftotext` and compares it to the analysis JSON. Safe area
is measured by rasterizing and looking, because the layout arithmetic is exactly
what would be wrong if the check were needed.

A missing external tool is a **failure** in production mode, never a skip; a skip
that reads as a pass is how an unchecked PDF gets announced as print-ready.
`--lenient` is the local diagnostic mode and reports skips explicitly.

ReportLab writes `initialFontName` into every page preamble whether or not
anything draws with it, so canvases must be created with a bundled face — the
Helvetica default puts an unembeddable base-14 font in a file that never drew a
character, and fails the font check.

## SVG asset contract

Assets are validated (`assets/validate.py`) against a hard subset, and the *same* subset
definition drives the minimal internal SVG→ReportLab converter
(`rendering/svg_to_pdf.py`). One subset, two consumers — never two dialects.
Requirements: SVG 1.1 plain, `viewBox="0 0 100 100"`, artwork centred with ≥6 units of
padding, fill `#000000` only, **no `stroke` attributes** (strokes don't scale with the
icon — 5.5 mm finale icons become blobs), no feature/gap thinner than 7 units, ≤12
subpaths, no text, no background rect, no raster, no gradients, no opacity.

Public folder names are part of the contract and case-sensitive:
`beginning-vectors/`, `ending-vectors/`, `maze-vectors/dead-end/`,
`maze-vectors/collectibles/`, `page-vectors/` (optional).

## Profile traps

A profile is a set of promises the generator has to be able to keep, and three
numbers will quietly make one unsatisfiable while every schema and unit test
still passes. Three shipped profiles were broken this way. `tests/test_profile.py`
guards all three; know them before writing a profile:

- **`deadEndDepth.min` must be 1.** Braiding creates a dead end by opening an edge
  at a degree-1 cell, which leaves a depth-1 stub. The rebalancer has no move that
  deepens one on request, so a floor above 1 rejects shapes for a property that
  cannot be asked for. It dominated rejections at 43 of 50 attempts.
- **`temptingFraction x candies` must leave room under the best score.** C5 wants a
  decoy scoring `>= fraction x best`; C1 requires the best to be unique. So a
  tempting route needs a score in `[ceil(fraction x best), best)`, and at 0.9 with
  five candies that interval is empty — arithmetically unsatisfiable, not tight.
- **Every `*Scale` must be `<= 1 - 2 x cellClearanceFraction`.** Assets are centred
  in their cell and must stay inside the safe inset; a larger scale fails at
  placement time on the first page drawn, far from the number that caused it.

Settle profile questions by measuring, not by arguing: generate against a patched
copy and count acceptances. That is how the PRD §17.4 drift and all three broken
profiles were resolved.

## Failure behaviour

Exit codes are a contract (`errors.py`): `0` success, `2` invalid input/config, `3` maze
generation or semantic failure, `4` rendering/assembly failure, `5` PDF/preflight failure.
Generation failure means *all* `maxAttemptsPerMaze` attempts were rejected — report the
per-constraint rejection histogram, never emit a maze that failed validation. Book paths
are POSIX-relative to `book.json`; absolute paths, `..` traversal and symlinks escaping
the package are rejected.
