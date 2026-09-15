# Universal Maze Book Repository PRD

## Repository scope

**Status:** Proposed final repository PRD
**Version:** 1.0
**Purpose:** Define the reusable repository that generates themed maze books from a self-contained book folder.
**First book:** Jim's Halloween Maze Adventure
**Primary output:** Black-and-white, no-bleed, single-page KDP paperback interior PDF
**Primary implementation:** Python 3.11+

This PRD is intentionally narrower than the full Halloween publishing PRD. It specifies the reusable generator repository, the book-package contract, maze generation and validation, asset loading, story loading, SVG/PDF rendering, and book assembly. Pricing, Amazon listing metadata, tax, legal publication obligations, advertising, and seasonal launch planning are outside this repository scope.

## 1. Product decision

The repository shall contain one shared maze-generation and book-assembly codebase plus one self-contained folder per book.

A new book is created by copying an existing book folder, changing its JSON configuration/story, and replacing or reusing its SVG assets. The generator code is not copied or modified for each theme.

```text
common generator code + book folder = generated book
```

The Halloween PRD remains the source for the first book's concrete content and quality targets. This repository PRD generalizes those requirements so the same code can generate Halloween, Christmas, dinosaurs, space, or other square-grid maze books.

## 2. Main differences from the Halloween PRD

### 2.1 Repository structure

The Halloween PRD uses a flat, book-specific structure:

```text
config/*.yaml
assets/icons/*.svg
src/jimmaze/
```

The universal repository uses self-contained book packages:

```text
books/<book-id>/book.json
books/<book-id>/assets/**/*.svg
```

This is the most important structural change.

### 2.2 Configuration and story format

The Halloween PRD separates configuration and story into multiple YAML files. The universal repository uses one required `book.json` per book containing:

- Book identity and locale.
- Seed and maze count.
- Difficulty profile or act profiles.
- KDP dimensions and page layout.
- Asset references.
- Story scenes.
- Output settings.

This gives the user the requested copy-folder workflow and makes each book package portable.

### 2.3 Asset folders

The Halloween PRD uses one flat `assets/icons/` directory. The universal repository uses role-based vector folders:

```text
assets/
├── beginning-vectors/
├── ending-vectors/
└── maze-vectors/
    ├── dead-end/
    └── collectibles/
```

The generator resolves assets by role, not by hardcoded Halloween filenames.

A small optional `page-vectors/` folder is also supported for story-page decorations such as bats, ghosts, webs, moons, and trees. This is necessary because the first Halloween PRD includes story-page decorations that do not belong in a maze start, finish, dead-end, or collectible folder. It may be omitted when a book has no page-only artwork.

### 2.4 Maze representation

The Halloween PRD describes the maze as a wall-based `mazelib` grid. The universal repository uses a canonical grid graph with normalized open edges.

`mazelib` remains useful as a generation adapter: it supplies a two-dimensional maze grid and supports imperfect mazes with loops. The adapter converts that grid into the repository's canonical graph immediately. The rest of the system does not depend on `mazelib`'s internal grid format. The Python project is `john-science/mazelib`; it documents a NumPy maze grid, generation algorithms, transmuters, and support for imperfect mazes. [mazelib on GitHub](https://github.com/john-science/mazelib)

### 2.5 Quality rules

The Halloween PRD has stronger concrete rules than the original architecture, including:

- Unique highest-candy route.
- Minimum score margin.
- Shortest route must not be the best route.
- Best route length limit.
- Tempting alternative routes.
- No candy in dead ends.
- Candy spacing rules.

These rules will be implemented as configurable profile constraints. They are the default for the Halloween profile, not universal hardcoded constants.

### 2.6 Publishing and KDP material

The Halloween PRD contains publishing, pricing, legal, AI disclosure, cover, and launch details. Those remain useful operational notes but are not core repository requirements. The repository shall support relevant machine-readable fields—such as print dimensions, color mode, bleed, and content-origin metadata—but shall not encode legal or marketplace assumptions into maze generation.

## 3. User workflow

### 3.1 Generate one maze

```bash
python cli.py maze generate \
  --book books/jims-halloween-maze-adventure \
  --index 1
```

Expected outputs:

```text
output/jims-halloween-maze-adventure/mazes/001.json
output/jims-halloween-maze-adventure/mazes/001.analysis.json
output/jims-halloween-maze-adventure/mazes/001.svg
```

### 3.2 Validate one maze

```bash
python cli.py maze validate \
  --book books/jims-halloween-maze-adventure \
  --index 1
```

### 3.3 Build all mazes and the book

```bash
python cli.py book build \
  --book books/jims-halloween-maze-adventure
```

### 3.4 Build another book

```bash
cp -R books/jims-halloween-maze-adventure books/jims-christmas-present-hunt
# Edit the copied book.json and replace/reuse the SVG assets.
python cli.py book build \
  --book books/jims-christmas-present-hunt
```

The CLI may accept `--theme` as a backwards-compatible alias for `--book`, but the internal concept should be a **book package**, because one folder represents one complete generated book rather than a reusable theme fragment.

## 4. Repository structure

```text
maze-book-generator/
├── README.md
├── pyproject.toml
├── cli.py
├── schemas/
│   ├── book.schema.json
│   ├── maze-data.schema.json
│   ├── maze-analysis.schema.json
│   └── difficulty-profile.schema.json
├── src/maze_book/
│   ├── cli.py
│   ├── model/
│   │   ├── maze.py
│   │   ├── analysis.py
│   │   └── pages.py
│   ├── generation/
│   │   ├── adapter_mazelib.py
│   │   ├── generator.py
│   │   ├── difficulty.py
│   │   └── seeds.py
│   ├── simulation/
│   │   ├── graph.py
│   │   ├── reachability.py
│   │   ├── paths.py
│   │   ├── scoring.py
│   │   └── validation.py
│   ├── assets/
│   │   ├── loader.py
│   │   ├── svg_validator.py
│   │   └── placement.py
│   ├── content/
│   │   └── loader.py
│   ├── rendering/
│   │   ├── geometry.py
│   │   ├── svg.py
│   │   ├── pdf.py
│   │   └── text.py
│   ├── book/
│   │   ├── planner.py
│   │   ├── assembler.py
│   │   └── solutions.py
│   └── preflight/
│       ├── maze.py
│       ├── book.py
│       └── pdf.py
├── profiles/
│   ├── child_6_7.json
│   ├── halloween_6_10.json
│   ├── older_child.json
│   └── adult.json
├── books/
│   └── jims-halloween-maze-adventure/
│       ├── book.json
│       ├── assets/
│       │   ├── beginning-vectors/
│       │   │   └── jim_start.svg
│       │   ├── ending-vectors/
│       │   │   └── candy_bucket.svg
│       │   ├── maze-vectors/
│       │   │   ├── dead-end/
│       │   │   │   ├── pumpkin_a.svg
│       │   │   │   └── pumpkin_b.svg
│       │   │   └── collectibles/
│       │   │       ├── candy_wrapped.svg
│       │   │       ├── candy_lolly.svg
│       │   │       ├── candy_corn.svg
│       │   │       ├── candy_bar.svg
│       │   │       └── candy_worm.svg
│       │   └── page-vectors/
│       │       ├── jim_costume.svg
│       │       ├── jim_head.svg
│       │       ├── deco_bat.svg
│       │       ├── deco_ghost.svg
│       │       ├── deco_spider.svg
│       │       ├── deco_web_corner.svg
│       │       ├── deco_moon.svg
│       │       ├── deco_gravestone.svg
│       │       └── deco_tree.svg
│       └── front-matter.pdf
├── tests/
└── output/
```

`front-matter.pdf` is optional. If the title page and other supplied pages are not generated by this repository, the book package may include them as an external PDF referenced by `book.json`.

## 5. Book-package contract

Every book package must contain exactly one required JSON configuration file:

```text
books/<book-id>/book.json
```

It may contain SVG assets and an optional supplied front-matter PDF. The generator must not require code changes when a new book package is added.

### 5.1 Example `book.json`

```json
{
  "schemaVersion": "1.0",
  "book": {
    "id": "jims-halloween-maze-adventure",
    "title": "Jim's Halloween Maze Adventure",
    "subtitle": "50 Candy Collecting Mazes for Kids Ages 6 to 10",
    "locale": "en-US",
    "seed": 42001,
    "mazeCount": 50,
    "difficultyProfile": "halloween_6_10",
    "contentOrigin": {
      "text": "human",
      "images": "ai-generated-and-vector-cleaned",
      "translations": "not-applicable"
    }
  },
  "print": {
    "trimWidthIn": 8.5,
    "trimHeightIn": 11.0,
    "interiorBleed": false,
    "colorMode": "black-and-white",
    "paper": "white",
    "safeMarginsIn": {
      "inside": 0.625,
      "outside": 0.5,
      "top": 0.5,
      "bottom": 0.5
    },
    "gutterIn": 0.625
  },
  "layout": {
    "frontMatterPdf": "front-matter.pdf",
    "storyPageSide": "left",
    "mazePageSide": "right",
    "firstSceneStoryPage": 6,
    "mazeSquareIn": 6.75,
    "tallyPosition": "below",
    "showMazeNumber": true,
    "showBestPossibleScore": true,
    "solutionsPerPage": 9,
    "solutionPagesStartAfterContent": true
  },
  "assets": {
    "beginningVectors": "assets/beginning-vectors",
    "endingVectors": "assets/ending-vectors",
    "mazeVectors": "assets/maze-vectors",
    "deadEndVectors": "assets/maze-vectors/dead-end",
    "collectibleVectors": "assets/maze-vectors/collectibles",
    "pageVectors": "assets/page-vectors",
    "startAsset": "jim_start.svg",
    "endAsset": "candy_bucket.svg",
    "deadEndAssetPolicy": "random-from-folder",
    "collectibleAssetPolicy": "random-from-folder"
  },
  "outputs": {
    "writeMazeJson": true,
    "writeAnalysisJson": true,
    "writeMazeSvg": true,
    "writeStandaloneMazePdf": true,
    "writeBookPdf": true,
    "writeContactSheet": true,
    "writePreflightReport": true
  },
  "scenes": [
    {
      "number": 1,
      "title": "The Attic Hunt",
      "text": "Jim's costume box is somewhere up in the attic. Find a way through before the sun goes down.",
      "pageVector": "jim_costume.svg"
    },
    {
      "number": 2,
      "title": "The Stuck Zipper",
      "text": "The ghost suit is inside out and the zipper will not move. Get across the bedroom to the sewing box.",
      "pageVector": "jim_costume.svg"
    }
  ]
}
```

The Halloween package will contain all 50 scene objects. The `scenes` array is the requested one-line story format: one title and one short text value per maze. The schema shall enforce that scene numbers are unique, consecutive, and equal to `mazeCount`.

### 5.2 Asset resolution rules

The manifest must use role-based paths, while individual assets are referenced by filename or asset ID.

Required v1 roles:

- Beginning vector: start marker.
- Ending vector: finish marker.
- Maze collectible vector: contributes to route score.
- Maze dead-end vector: marks an unusable dead end.

Optional role:

- Page vector: story-page or page-furniture artwork.

The generator must fail with a clear error if:

- A required folder is missing.
- A configured asset file is missing.
- An asset is not SVG.
- An SVG fails the print-profile validation.
- A maze references an asset role that has no available file.

## 6. Canonical maze model

The canonical model is theme-independent and renderer-independent.

```json
{
  "schemaVersion": "1.0",
  "mazeId": "jims-halloween-maze-adventure-001",
  "seed": 42001001,
  "grid": {
    "rows": 8,
    "cols": 8,
    "coordinateSystem": "row-major-top-left"
  },
  "start": { "row": 1, "col": 1 },
  "finish": { "row": 7, "col": 7 },
  "openEdges": [
    [[1, 1], [1, 2]],
    [[1, 2], [2, 2]]
  ],
  "blockedCells": [],
  "assets": [
    {
      "assetId": "jim_start.svg",
      "role": "start",
      "cell": { "row": 1, "col": 1 },
      "collectibleValue": 0,
      "scale": 0.60
    },
    {
      "assetId": "candy_bucket.svg",
      "role": "finish",
      "cell": { "row": 7, "col": 7 },
      "collectibleValue": 0,
      "scale": 0.70
    },
    {
      "assetId": "candy_lolly.svg",
      "role": "collectible",
      "cell": { "row": 3, "col": 4 },
      "collectibleValue": 1,
      "scale": 0.60
    }
  ],
  "metadata": {
    "bookId": "jims-halloween-maze-adventure",
    "mazeIndex": 1,
    "difficultyProfile": "halloween_6_10"
  }
}
```

### 6.1 Graph rules

- Every open edge joins two orthogonally adjacent cells.
- Each edge is stored once in canonical order.
- Missing in-bounds edges render as walls.
- Outer boundaries render as walls except where the renderer intentionally creates a start/finish opening.
- The start and finish are distinct and unblocked.
- Assets do not define traversability. The graph defines traversability.
- A visual obstacle must be represented as a blocked cell or graph rule; it must never merely cover an open path.

## 7. Generation and simulation requirements

### 7.1 Generation adapter

The first implementation shall support `john-science/mazelib` through `generation/adapter_mazelib.py`:

1. Generate a base maze using a seeded algorithm.
2. Convert its 2D grid to the canonical open-edge graph.
3. Add controlled openings or use a controlled transmutation step to create loops.
4. Select start and finish in configured regions.
5. Run the repository's own route simulator and acceptance rules.

The adapter is replaceable. A future native graph generator may be added without changing the canonical model, scoring system, renderer, or book assembler.

### 7.2 Deterministic seeds

A maze must be reproducible from:

```text
book.seed + book.id + maze index + generation attempt
```

Use independent derived random streams for:

- Topology.
- Start/finish selection.
- Collectible placement.
- Asset-variant selection.
- Page decoration.

Regenerating maze 17 must not change mazes 1–16 or 18–50.

### 7.3 Difficulty profiles

Difficulty is a named profile, not a hardcoded age check. A profile may define:

- Grid-size range.
- Cycle or loop budget.
- Valid route-count range.
- Dead-end count and depth.
- Start-to-finish distance.
- Candy-count range.
- Candy score margin.
- Maximum route-enumeration count.
- Maximum search nodes and timeout.
- Asset scale and clearance.
- Wall stroke width.

The Halloween profile shall initially reproduce the PRD's five acts and finale:

| Act | Grid | Candy range | Loop range | Dead-end range |
|---|---:|---:|---:|---:|
| 1 | 8×8 | 5–7 | 3–4 | 4–5 |
| 2 | 10×10 | 7–9 | 4–5 | 5–6 |
| 3 | 12×12 | 9–11 | 5–6 | 6–7 |
| 4 | 14×14 | 11–13 | 6–8 | 7–8 |
| 5 | 16×16 | 13–15 | 8–10 | 8–9 |
| Finale | 18×18 | 16–18 | 10–12 | 9–10 |

The profile remains data-driven so a child 6–7 book can use smaller grids and fewer routes, while older-child and adult profiles can increase complexity.

### 7.4 Start and finish placement

The default Halloween profile selects the start from the top-left configured region and the finish from the bottom-right configured region. The exact cells vary by seed. The generic engine must support:

- Corner regions.
- Boundary cells.
- Arbitrary configured cell sets.
- Interior starts and finishes.

## 8. Route rules and candy scoring

### 8.1 Valid route

A valid route:

1. Starts at the start cell.
2. Ends at the finish cell.
3. Follows only open graph edges.
4. Never visits a cell twice.

Physical erasing and retrying is allowed for the child, but a completed route is scored as one simple path. This prevents a player from collecting every branch by repeatedly backtracking.

### 8.2 Route enumeration

Use depth-first search with a visited bitset. For accepted mazes, enumerate all simple start-to-finish paths exactly.

Required safety controls:

- Maximum enumerated routes.
- Maximum search nodes.
- Optional time limit.
- Explicit `exact: false` status if a limit interrupts enumeration.
- Candidate rejection when exact validation is required but enumeration is incomplete.

The Halloween profile shall use the PRD's practical cap of 200,000 paths and a per-maze timeout configured in the profile.

### 8.3 Dead ends

A non-terminal cell with graph degree one is a dead end.

For the Halloween profile:

- Retain the configured dead-end count.
- Place a dead-end vector in each accepted dead-end cell.
- Do not place collectibles in dead-end cells.
- Do not allow a dead-end asset to cover the only visible opening or a junction.

This corrects an ambiguity in the earlier architecture proposal, which mentioned optionally placing collectibles in dead ends. The Halloween PRD's rule is better for a child-facing puzzle and is the repository default for this profile.

### 8.4 Candy placement

Candy placement happens after topology generation and route enumeration.

For v1:

- Every collectible is worth one candy.
- Collectibles are placed only in eligible traversable cells.
- Start, finish, dead ends, blocked cells, junction-obscuring cells, and asset-collision cells are excluded.
- No two collectibles may be adjacent in the same corridor run when the active profile enables the spacing rule.
- The selected visual asset is sampled from `maze-vectors/collectibles`.

The scorer counts collectibles on the route's distinct cells.

### 8.5 Acceptance rules

The engine shall support the following generic constraints:

- Unique maximum-score route.
- Minimum gap between best and second-best score.
- Best route is not a shortest route.
- Maximum best-route length relative to the shortest route.
- Minimum number of tempting routes.
- Candy range.
- No candy in dead ends.
- Candy spacing.

The Halloween profile shall implement the PRD's C1–C8 rules as profile data. A different book may relax or replace them without changing the solver.

The back matter displays only:

- The highest-candy route.
- Its candy total.
- The maze number.

The analysis output may retain all route scores for QA.

## 9. Rendering requirements

### 9.1 Shared geometry

All renderers shall use the same coordinate conversion:

```text
cell coordinates -> normalized maze geometry -> SVG or PDF page coordinates
```

No renderer may independently reconstruct wall positions from a different interpretation of the maze.

### 9.2 SVG output

Every accepted maze shall be renderable as an individual SVG containing:

- Maze walls.
- Start vector.
- Finish vector.
- Collectibles.
- Dead-end vectors.
- Optional maze number and score furniture when requested.

SVG is the inspection and reusable-vector output. It is not the canonical maze data.

### 9.3 PDF output

The PDF renderer shall:

- Generate exact configured page dimensions in points.
- Draw maze walls and path lines as vectors.
- Place SVG assets with explicit dimensions.
- Keep artwork inside the no-bleed safe area.
- Support supplied front matter as single pages.
- Produce one page per PDF page, never a 2-up spread.
- Support a non-outlined editable proof build and a final production build according to the selected font policy.

ReportLab is the default PDF implementation. `pypdf` may be used for page merging/reordering. SVG conversion shall be cached and validated.

## 10. Book page assembly

The assembler shall build an explicit page plan before rendering.

Each page record shall include:

```json
{
  "pageNumber": 6,
  "side": "left",
  "kind": "story",
  "sceneNumber": 1,
  "facingPairId": "scene-001"
}
```

The default Halloween layout is:

- Supplied front matter first.
- Story page on the left.
- Maze page on the right.
- Repeated story/maze facing pairs.
- Solutions section at the back.

The planner must use physical page parity and insert blank pages where necessary. It must not assume that the first content page has a particular side.

The following values are book configuration, not engine constants:

- Front-matter page count.
- Story page side.
- Maze page side.
- Maze-square dimensions.
- Tally position.
- Solutions per page.
- Solution section starting side.
- Whether a finale or end page is inserted.

For the Halloween PRD, the target is 112 pages, with 50 scene spreads and 9 solution thumbnails per solutions page. The universal assembler must also support other page counts and thumbnail densities.

## 11. Preflight requirements

### 11.1 Book-package preflight

Fail the build on:

- Invalid `book.json` schema.
- Missing required folders or assets.
- Missing or duplicate scene numbers.
- Scene count not equal to maze count.
- Unsupported locale or color profile.
- Unsupported bleed/layout combination.

### 11.2 Maze preflight

Fail the build on:

- Invalid coordinates or graph edges.
- Unreachable finish.
- Repeated-cell route in the solution output.
- Route that uses a closed edge.
- Incomplete route enumeration where exact validation is required.
- Route count outside the active profile.
- Dead-end count outside the active profile.
- Non-unique highest-candy route.
- Score mismatch.
- Candy in a forbidden cell.
- Asset overlap or wall/junction obstruction.
- Non-deterministic output for the same seed.

### 11.3 SVG asset preflight

For the first black-and-white profile, validate:

- SVG 1.1 or supported plain SVG.
- Pure black artwork and transparent background.
- No unsupported filters or embedded raster images.
- No accidental text, watermark, or background rectangle.
- Explicit viewBox.
- Adequate padding and minimum feature size according to the selected profile.
- No invalid or unsafe external references.

### 11.4 PDF preflight

The build shall run:

```bash
qpdf --check output/<book-id>/book-interior.pdf
pdftotext output/<book-id>/book-interior.pdf output/<book-id>/book-interior.txt
pdftoppm -jpeg -r 72 output/<book-id>/book-interior.pdf output/<book-id>/validate-page
```

It shall also check with `pypdf`:

- Page count.
- Page dimensions.
- Page rotation.
- Page ordering.
- Presence of expected text markers.

The build should render and inspect representative pages: front matter, first story page, first maze, a difficult maze, a solution page, and the final page.

## 12. Output contract

```text
output/<book-id>/
├── normalized-book.json
├── mazes/
│   ├── 001.json
│   ├── 001.analysis.json
│   ├── 001.svg
│   └── ...
├── pages/
│   ├── page-001.pdf
│   ├── page-002.pdf
│   └── ...
├── page-plan.json
├── contact-sheet.png
├── preflight.json
├── book-interior-editable.pdf
└── book-interior.pdf
```

The intermediate JSON and SVG outputs are first-class artifacts. The final PDF must be reproducible from them without regenerating topology.

## 13. CLI requirements

Minimum commands:

```bash
python cli.py book validate --book books/<book-id>
python cli.py maze generate --book books/<book-id> --index 1
python cli.py maze validate --book books/<book-id> --index 1
python cli.py maze render --book books/<book-id> --index 1 --format svg,pdf
python cli.py book generate-mazes --book books/<book-id>
python cli.py book assemble --book books/<book-id>
python cli.py book build --book books/<book-id>
python cli.py book preflight --book books/<book-id>
```

Optional flags:

```bash
--seed <integer>
--force
--only <start:end>
--profile <profile-id>
--output <directory>
--format svg,pdf,json
```

`book build` shall be equivalent to the safe sequence:

```text
validate package
-> validate assets
-> generate or load cached maze data
-> simulate and validate every maze
-> render individual SVGs
-> plan pages
-> render pages
-> assemble PDF
-> run preflight
```

If any required stage fails, the final PDF shall not be reported as successful.

## 14. Testing requirements

### Unit tests

- Edge normalization.
- Grid-to-graph conversion from the `mazelib` adapter.
- Graph-to-wall rendering.
- Reachability.
- Dead-end detection.
- Simple-path enumeration.
- No-revisit enforcement.
- Candy scoring.
- Unique maximum selection.
- Asset role resolution.
- Page parity and facing pairs.
- SVG validation.

### Property tests

Across many seeds:

- Every accepted maze reaches its finish.
- Every solution route contains no repeated cell.
- Every solution route follows open edges.
- The selected route has the reported maximum score.
- All required assets resolve.
- The same book seed and maze index produce identical canonical JSON.
- Changing maze index does not alter other maze outputs.

### Fixture tests

Keep fixture book folders for:

- A small child profile.
- The 50-maze Halloween profile.
- An older-child profile.
- A book with multiple collectible variants.
- A book with no page-vectors folder.
- A book with supplied front matter.

## 15. Acceptance criteria for repository v1

The repository is complete when:

1. A new book can be created by copying a book folder and editing only `book.json` and/or its SVGs.
2. No generator source-code change is required to switch themes.
3. The Halloween book uses the required beginning, ending, maze dead-end, maze collectible, and optional page-vector folders.
4. The story is loaded from one JSON file as one title/text scene per maze.
5. `mazelib` is isolated behind an adapter and its output is converted to the canonical graph model.
6. Every generated maze has a machine-readable JSON representation and an SVG representation.
7. Every accepted maze is exactly solvable under the no-revisited-cell rule.
8. The Halloween profile produces a unique highest-candy route and rejects failures instead of exporting them.
9. Dead ends receive dead-end assets and no dead-end cell receives a collectible under the Halloween profile.
10. The assembler creates left-story/right-maze facing pairs according to configured page parity.
11. The back matter shows only the highest-candy route and candy total for each maze.
12. The final interior PDF is black-and-white, no-bleed, single-page, and passes PDF preflight.
13. Cached maze JSON can be re-rendered without regenerating the maze.
14. The repository produces a contact sheet and machine-readable preflight report.
15. A failed maze, missing asset, invalid story, invalid SVG, or invalid PDF causes a non-zero build result.

## 16. Deferred scope

The following are intentionally deferred from repository v1:

- Cover generation.
- Marketplace pricing and royalties.
- KDP upload automation.
- Tax forms and legal imprint generation.
- Automated AI text or image generation.
- Color interiors.
- Non-square or non-grid mazes.
- Weighted collectibles, unless enabled by a later profile.
- Interactive digital or ebook output.
- Multiple solution display modes beyond highest-candy route.

## Final architectural decision

Use the Halloween PRD's maze mechanics and quality constraints, but do not use its monolithic repository layout. Use the universal book-package layout defined here:

```text
books/<book-id>/book.json
books/<book-id>/assets/beginning-vectors/
books/<book-id>/assets/ending-vectors/
books/<book-id>/assets/maze-vectors/dead-end/
books/<book-id>/assets/maze-vectors/collectibles/
books/<book-id>/assets/page-vectors/       # optional
```

Use `mazelib` only as a replaceable base-generation adapter. Use the canonical open-edge grid graph as the internal contract. Keep all route simulation, candy scoring, asset placement, SVG rendering, and PDF assembly independent of the external maze library.

## 17. Build-ready locked contract

This section is normative and overrides any earlier wording in this document that is less specific or inconsistent. The implementation should be built against this section as the final repository contract.

### 17.1 Normative language

- **MUST** means required for repository v1.
- **MUST NOT** means a build failure or prohibited behavior.
- **SHOULD** means the default implementation unless a documented technical reason prevents it.
- **MAY** means optional and must not be required by the core build.

The repository is a Python package plus portable book packages. A book package is data and assets only. Adding or copying a book package MUST NOT require changing generator source code.

### 17.2 Final repository and book-package layout

The final layout is:

```text
maze-book-generator/
├── cli.py
├── pyproject.toml
├── README.md
├── schemas/
│   ├── book.schema.json
│   ├── maze-data.schema.json
│   ├── maze-analysis.schema.json
│   └── difficulty-profile.schema.json
├── profiles/
│   ├── child_6_7.json
│   ├── halloween_6_10.json
│   ├── older_child.json
│   └── adult.json
├── fonts/
│   └── <licensed-font-files>
├── src/maze_book/
│   ├── cli.py
│   ├── errors.py
│   ├── model/
│   ├── generation/
│   ├── simulation/
│   ├── assets/
│   ├── content/
│   ├── rendering/
│   ├── book/
│   └── preflight/
├── books/
│   └── <book-id>/
│       ├── book.json
│       ├── assets/
│       │   ├── beginning-vectors/
│       │   ├── ending-vectors/
│       │   ├── maze-vectors/
│       │   │   ├── dead-end/
│       │   │   └── collectibles/
│       │   └── page-vectors/       # optional
│       └── front-matter.pdf        # optional, single-page PDF
├── tests/
└── output/
```

The exact asset folder names are part of the public contract and are case-sensitive:

```text
beginning-vectors
ending-vectors
maze-vectors/dead-end
maze-vectors/collectibles
page-vectors
```

The words “beginning” and “ending” describe maze roles, not page positions. The beginning folder supplies the start marker; the ending folder supplies the finish marker.

The repository MUST support a book with no `page-vectors` folder. Story pages then render without artwork or use built-in text-only furniture.

### 17.3 One-file book input contract

Each book package MUST have exactly one required input/configuration file:

```text
books/<book-id>/book.json
```

All story text for the book MUST be inside this file under `content.scenes`. A second story Markdown/YAML/JSON file is not required for v1. The generator MAY later support external content files, but they are outside the v1 contract.

All paths in `book.json` are POSIX-style paths relative to the directory containing `book.json`. Absolute paths and `..` path traversal MUST be rejected.

The following is the canonical shape. Field names, enum values, and semantics are implementation contracts:

```json
{
  "schemaVersion": "1.0",
  "book": {
    "id": "jims-halloween-maze-adventure",
    "title": "Jim's Halloween Maze Adventure",
    "subtitle": "50 Candy Collecting Mazes for Kids Ages 6 to 10",
    "locale": "en-US",
    "seed": 42001,
    "mazeCount": 50,
    "profileId": "halloween_6_10",
    "contentOrigin": {
      "text": "human",
      "images": "ai-generated-and-vector-cleaned",
      "translations": "not-applicable"
    }
  },
  "print": {
    "trimWidthIn": 8.5,
    "trimHeightIn": 11.0,
    "interiorBleed": false,
    "colorMode": "black-and-white",
    "paper": "white",
    "safeMarginsIn": {
      "inside": 0.625,
      "outside": 0.5,
      "top": 0.5,
      "bottom": 0.5
    },
    "gutterIn": 0.625
  },
  "generation": {
    "startRegion": { "rowMin": 0, "rowMax": 2, "colMin": 0, "colMax": 2 },
    "finishRegion": { "rowMin": -3, "rowMax": -1, "colMin": -3, "colMax": -1 },
    "maxAttemptsPerMaze": 500,
    "baseGenerator": "mazelib_recursive_backtracker",
    "exactRouteEnumeration": true
  },
  "layout": {
    "frontMatterPdf": "front-matter.pdf",
    "storyPageSide": "left",
    "mazePageSide": "right",
    "mazeSquareIn": 6.75,
    "tallyPosition": "below",
    "showMazeNumber": true,
    "showBestPossibleScore": true,
    "solutionsPerPage": 9,
    "solutionsStartSide": "right",
    "insertEndPage": true,
    "insertBlankPagesForParity": true
  },
  "assets": {
    "beginningVectorsDir": "assets/beginning-vectors",
    "endingVectorsDir": "assets/ending-vectors",
    "deadEndVectorsDir": "assets/maze-vectors/dead-end",
    "collectibleVectorsDir": "assets/maze-vectors/collectibles",
    "pageVectorsDir": "assets/page-vectors",
    "startAsset": "jim_start.svg",
    "finishAsset": "candy_bucket.svg",
    "deadEndAssetPolicy": "random-from-folder",
    "collectibleAssetPolicy": "random-from-folder"
  },
  "content": {
    "scenes": [
      {
        "number": 1,
        "title": "The Attic Hunt",
        "text": "Jim's costume box is somewhere up in the attic. Find a way through before the sun goes down.",
        "pageVector": "jim_costume.svg"
      }
    ]
  },
  "outputs": {
    "writeMazeJson": true,
    "writeAnalysisJson": true,
    "writeMazeSvg": true,
    "writeStandaloneMazePdf": true,
    "writeBookPdf": true,
    "writeContactSheet": true,
    "writePreflightReport": true,
    "writeEditableProofPdf": true
  }
}
```

The real Halloween file MUST contain 50 scenes. The example contains one scene only to keep the contract readable.

Path-resolution rules are normative:

- `assets.beginningVectorsDir`, `assets.endingVectorsDir`, `assets.deadEndVectorsDir`, `assets.collectibleVectorsDir`, and `assets.pageVectorsDir` are directories relative to `book.json`.
- `assets.startAsset` is a filename relative to `beginningVectorsDir`.
- `assets.finishAsset` is a filename relative to `endingVectorsDir`.
- `content.scenes[].pageVector`, when present, is a filename relative to `pageVectorsDir`.
- `layout.frontMatterPdf` may be `null` when front matter is intentionally supplied outside this build; when non-null it is a relative path to a PDF inside the book package.
- Every resolved path MUST remain inside the book package after normalization. Symlinks that resolve outside the package MUST be rejected.

The JSON Schema MUST enforce at least:

- `schemaVersion`, `book`, `print`, `generation`, `layout`, `assets`, `content`, and `outputs` are present.
- `book.id` is a safe lowercase kebab-case identifier and matches the book-folder name.
- `mazeCount` is a positive integer.
- `content.scenes` contains exactly `mazeCount` entries.
- Scene numbers are unique, consecutive, and equal to `1..mazeCount`.
- Every scene has a non-empty title and text.
- Paths are relative and remain inside the book package.
- `interiorBleed` is `false` for the v1 profile.
- `storyPageSide` and `mazePageSide` are opposite sides.
- `solutionsPerPage` is a positive integer.
- `startAsset` and `finishAsset` resolve to existing SVG files.

Unknown top-level fields SHOULD be rejected, with an `extensions` object reserved for future additions. This prevents silent misspellings such as `mazeCounnt`.

### 17.4 Difficulty-profile contract

Profiles are repository-level JSON files and are referenced by `book.profileId`. A profile MUST define:

- The maze index ranges it covers, or a deterministic mapping function.
- Grid size for each range.
- Target loop/cycle range.
- Valid simple-route count range.
- Dead-end count range.
- Dead-end depth range when enabled.
- Candy count range.
- Minimum best-score margin.
- Whether the shortest route may be the best route.
- Maximum best-route length relative to shortest route.
- Minimum number of tempting routes.
- Maximum enumerated routes.
- Maximum search nodes.
- Generation timeout.
- Asset scale, wall width, and cell-clearance values.
- Candy-spacing policy.

A profile MUST be data-driven. Age labels are metadata; acceptance is based on measurable constraints.

The supplied `halloween_6_10` profile MUST encode these ranges from the Halloween PRD:

| Act | Maze numbers | Grid | Cell size | Candy range | Loop range `k` | Dead-end range | Margin | Wall stroke |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 1. Getting Ready | 1–10 | 8×8 | 0.844 in | 5–7 | 3–4 | 4–5 | 2 | 3.00 pt |
| 2. The Neighborhood | 11–20 | 10×10 | 0.675 in | 7–9 | 4–5 | 5–6 | 2 | 3.00 pt |
| 3. The Dark End of the Street | 21–30 | 12×12 | 0.563 in | 9–11 | 5–6 | 6–7 | 2 | 2.75 pt |
| 4. The Haunted Half Hour | 31–40 | 14×14 | 0.482 in | 11–13 | 6–8 | 7–8 | 1 | 2.50 pt |
| 5. The Long Way Home | 41–48 | 16×16 | 0.422 in | 13–15 | 8–10 | 8–9 | 1 | 2.50 pt |
| Finale | 49–50 | 18×18 | 0.375 in | 16–18 | 10–12 | 9–10 | 1 | 2.25 pt |

Cell size is derived (`layout.mazeSquareIn / cols`) and is informative, not an input. The
0.375 in floor is the practical minimum for a child drawing with a pencil, which is why
the grid is capped at 18×18. `k` MUST stay within 3–12 across all acts: below 3 there are
too few genuinely different routes for the puzzle to exist, above 12 the simple-route count
explodes combinatorially and the maze reads as an open field rather than a maze.

The supplied `child_6_7` profile SHOULD use smaller grids and fewer routes than the Halloween profile. The implementation MUST support adding profiles without changing source code.

### 17.5 Canonical `MazeData` contract

`MazeData` is the only source of truth for a maze. SVG, PDF, PNG, and solution art are derived outputs and MUST NOT be parsed as input to determine topology.

The canonical format is:

```json
{
  "schemaVersion": "1.0",
  "mazeId": "jims-halloween-maze-adventure-001",
  "bookId": "jims-halloween-maze-adventure",
  "mazeIndex": 1,
  "seed": 42001001,
  "grid": {
    "rows": 8,
    "cols": 8,
    "coordinateSystem": "row-major-top-left"
  },
  "start": { "row": 0, "col": 0 },
  "finish": { "row": 7, "col": 7 },
  "openEdges": [
    [[0, 0], [0, 1]],
    [[0, 1], [1, 1]]
  ],
  "blockedCells": [],
  "assets": [
    {
      "assetId": "assets/beginning-vectors/jim_start.svg",
      "role": "start",
      "cell": { "row": 0, "col": 0 },
      "value": 0,
      "scale": 0.60,
      "rotationDeg": 0
    },
    {
      "assetId": "assets/ending-vectors/candy_bucket.svg",
      "role": "finish",
      "cell": { "row": 7, "col": 7 },
      "value": 0,
      "scale": 0.70,
      "rotationDeg": 0
    },
    {
      "assetId": "assets/maze-vectors/collectibles/candy_lolly.svg",
      "role": "collectible",
      "cell": { "row": 3, "col": 4 },
      "value": 1,
      "scale": 0.60,
      "rotationDeg": 0
    }
  ],
  "metadata": {
    "profileId": "halloween_6_10",
    "attempt": 1,
    "generatorId": "mazelib_recursive_backtracker"
  }
}
```

Canonicalization rules:

- Coordinates are zero-based.
- All coordinates are `{row, col}` objects except the compact edge representation.
- Every open edge joins orthogonally adjacent in-bounds cells.
- The two cells in an edge are sorted lexicographically by `(row, col)`.
- Edges are sorted lexicographically and contain no duplicates.
- Every traversable cell is in bounds and not blocked.
- `start` and `finish` are distinct and not blocked.
- `assetId` is a normalized relative path from the book package.
- Asset lists are sorted deterministically by role and cell before serialization.
- JSON is written with stable key ordering and UTF-8 encoding so deterministic builds can be byte-compared.

The implementation MUST include a schema validator and a semantic validator. JSON Schema alone cannot prove edge adjacency, graph reachability, or route correctness.

### 17.6 Canonical `MazeAnalysis` contract

Analysis is stored separately from `MazeData`:

```json
{
  "schemaVersion": "1.0",
  "mazeId": "jims-halloween-maze-adventure-001",
  "exact": true,
  "reachable": true,
  "simplePathCount": 3,
  "deadEndCells": [[0, 3], [2, 6]],
  "shortestRouteLength": 27,
  "bestRouteLength": 31,
  "bestCandyTotal": 5,
  "secondBestCandyTotal": 3,
  "uniqueHighestCandy": true,
  "bestRoute": [[0, 0], [0, 1], [1, 1]],
  "routeSummaries": [
    {
      "routeIndex": 0,
      "length": 27,
      "candyTotal": 1,
      "cells": [[0, 0], [0, 1], [1, 1]]
    }
  ],
  "validation": {
    "passed": true,
    "errors": [],
    "warnings": []
  }
}
```

The `bestRoute` is mandatory for an accepted maze. `routeSummaries` MUST be written for v1 because they make scoring and QA auditable. A future profile MAY omit full cell lists for very large adult mazes, but it MUST retain enough information to re-score and verify the selected route.

A route is valid only if it starts at `start`, ends at `finish`, follows open edges, and contains no repeated cell. Physical erasing and retrying is allowed; route scoring never grants a child credit for revisiting a cell.

### 17.7 Generation algorithm and library boundary

The first implementation MUST expose a generator interface similar to:

```python
class BaseMazeGenerator(Protocol):
    def generate(self, rows: int, cols: int, seed: int) -> BaseGrid:
        ...
```

`generation/adapter_mazelib.py` MUST implement this interface using the `john-science/mazelib` package. The package MAY change its internal API or be replaced later; no other module may import it directly.

The full generator MUST execute this deterministic pipeline:

1. Derive independent seeds from `(book.seed, book.id, mazeIndex, attempt)` for topology, endpoints, candy placement, and asset variants.
2. Generate a connected base spanning-tree maze through the adapter. If the adapter returns an invalid or disconnected grid, reject the attempt.
3. Convert the adapter grid to the canonical open-edge graph.
4. Select start and finish from configured regions, resolving negative region bounds against the grid dimensions. Reject if they are equal or fail minimum-distance rules.
5. Add openings between currently adjacent-but-closed cells until the profile's loop target is reached. The loop/cycle count is calculated as:

   ```text
   cyclomatic_number = E - V + C
   ```

   where `E` is the number of open edges, `V` the number of traversable cells, and `C` the number of connected components. For a connected grid, `C = 1`. The implementation MUST measure the result rather than assume each wall removal adds a useful loop.
6. Run graph analysis and reject attempts outside connectivity, dead-end, distance, or density constraints.
7. Enumerate all simple start-to-finish paths using depth-first search with a visited bitset.
8. Reject the attempt if exact enumeration exceeds the profile's route, node, or time limits.
9. Place candies with deterministic sampling and score every route.
10. Require the profile's candy constraints, including a unique highest-candy route where enabled.
11. Place one dead-end vector in each eligible non-terminal dead-end cell.
12. Place deterministic start, finish, collectible, and dead-end asset variants.
13. Run semantic validation and write `MazeData` only after validation passes.

The implementation MUST NOT rely on a library solver for exact simple-path enumeration or candy scoring. Those are product logic and belong in the repository.

### 17.8 Candy and dead-end rules

For repository v1:

- Every collectible has value `1` unless a future profile explicitly enables weighted values.
- Collectibles MUST NOT be placed on start, finish, blocked, dead-end, or visually unsafe cells.
- Dead-end cells are non-terminal traversable cells with graph degree one.
- The finish is never reported as an unwanted dead end.
- The start is excluded from dead-end asset placement even if its degree is one.
- Every remaining eligible dead end receives one dead-end asset.
- A collectible MAY be on a route-specific cell or a shared route cell, but the profile decides how much shared candy is allowed.
- The scorer counts each collectible at most once because routes contain no repeated cells.
- Asset variant selection is deterministic and uses sorted filenames.

Halloween v1 MUST enforce:

- Unique maximum score.
- Best score minus second-best score at least the profile margin.
- Shortest route is not the selected best route.
- Best route length is no more than 1.6 times the shortest route length.
- At least three routes collect at least 60% of the best score.
- Candy count is inside the act range.
- No two candies are adjacent in the same corridor run.

If a candidate cannot satisfy the active profile after the configured placement attempts, the maze MUST be regenerated from a new attempt seed. It MUST NOT be exported with relaxed constraints.

### 17.9 Asset validation and placement

All assets used by v1 MUST be SVG files. PNG/JPEG assets are out of scope for the book package contract.

The SVG validator MUST reject an asset with:

- Missing or invalid `viewBox`.
- External references, scripts, unsafe entities, or unsupported filters.
- Embedded raster images.
- Text nodes.
- A non-transparent background rectangle.
- Non-black artwork when the active color mode is black-and-white.
- Unsupported strokes or effects when the asset profile forbids them.

The default Halloween asset profile SHOULD enforce the PRD's plain-icon rules: pure black artwork, transparent background, centered composition, minimum padding, and no live strokes. The exact minimum feature size is profile data.

Placement rules:

- Assets are centered in cells unless a profile explicitly provides an anchor.
- The renderer uses the SVG viewBox and requested scale to calculate a footprint.
- The footprint MUST stay inside the cell's safe inset.
- The footprint MUST NOT cover a wall segment, opening, junction, or another asset beyond the configured tolerance.
- Placement validation is performed in normalized maze coordinates before SVG or PDF rendering.
- A visually decorative asset MUST NOT alter graph traversability.
- An actual obstacle is unsupported in v1; it must not be simulated by painting over a route.

### 17.10 Page planning and parity

The page planner is a pure planning step that runs before rendering. It produces `page-plan.json` with one record per physical page.

For every page, the record MUST include:

```json
{
  "pageNumber": 6,
  "side": "left",
  "kind": "story",
  "sceneNumber": 1,
  "facingPairId": "scene-001"
}
```

Physical parity is normative:

- Even page numbers are `left` pages.
- Odd page numbers are `right` pages.
- A story/maze pair is valid only when the story and maze are adjacent and on the configured opposite sides.
- If the next available page cannot start the pair on the required side, the planner inserts a blank page when `insertBlankPagesForParity` is true.
- The planner MUST fail instead of silently flipping a pair when blank insertion is disabled.

If `front-matter.pdf` is supplied, the assembler MUST read its actual page count with a PDF parser. The user MUST NOT have to duplicate that count in `book.json`. The front-matter PDF MUST contain single pages, not a 2-up spread.

The default main sequence is:

```text
front matter
-> optional parity blank
-> story page
-> maze page
-> story page
-> maze page
-> ...
-> optional end page
-> solutions pages
```

The Halloween target is 112 pages: five front-matter pages, 50 story/maze pairs on pages 6–105, one end page on page 106, and six solution pages on pages 107–112 at nine thumbnails per page. The assembler MUST calculate this result from inputs and MUST fail if the configured expected page count is present and does not match.

### 17.11 Rendering and fonts

The canonical geometry pipeline is:

```text
MazeData -> normalized geometry -> SVG renderer
                         -> PDF renderer
                         -> solution renderer
```

The SVG and PDF renderers MUST consume the same normalized geometry functions. They MUST NOT independently infer walls or routes.

ReportLab is the v1 PDF renderer. It MUST:

- Create exact page dimensions in points from the configured trim size.
- Draw maze walls and solution routes as vectors.
- Place SVG assets with explicit dimensions.
- Keep all interior artwork inside the configured safe area when bleed is false.
- Produce one physical page per PDF page.
- Preserve enough text as text for proofreading and extraction.

For v1, fonts MUST be embedded in the editable and production PDFs. Text-to-outline conversion is deferred because it makes proofreading and text preflight harder and is not required by the repository contract. A future production profile MAY add outlining.

The repository MUST either bundle licensed fonts under `fonts/` or require a configured font path that passes a preflight check. It MUST NOT silently fall back to an unconfigured system font.

### 17.12 Caching and reproducibility

The generator MUST cache canonical maze artifacts and reuse them during assembly.

A cache entry is reusable only when all of these are unchanged:

- Normalized `book.json` hash.
- Profile file hash.
- Generator version.
- Relevant asset file hashes.
- Maze index and derived seed.

The output MUST record these values in `normalized-book.json` and maze metadata. `--force` bypasses the cache.

Changing one maze's attempt or regenerating one index MUST NOT modify other maze JSON files. File enumeration MUST be sorted before deterministic sampling.

### 17.13 CLI contract

The root entry point MUST support these commands and 1-based maze indexes:

```bash
python cli.py book validate --book books/<book-id>
python cli.py maze generate --book books/<book-id> --index 1
python cli.py maze validate --book books/<book-id> --index 1
python cli.py maze render --book books/<book-id> --index 1 --format svg,pdf
python cli.py book generate-mazes --book books/<book-id>
python cli.py book assemble --book books/<book-id>
python cli.py book build --book books/<book-id>
python cli.py book preflight --book books/<book-id>
```

Required behavior:

- `book validate` validates JSON, paths, profile, and assets without generating a maze.
- `maze generate` generates one maze and its analysis; it does not assemble a book.
- `maze validate` performs schema and semantic validation and returns non-zero on failure.
- `maze render` renders cached maze data and MUST NOT silently regenerate it.
- `book generate-mazes` generates or loads all maze artifacts and writes the contact sheet.
- `book assemble` requires valid cached maze artifacts and builds pages/PDF without changing maze topology.
- `book build` executes validation, asset checks, maze generation/loading, simulation, rendering, assembly, and PDF preflight.
- `book preflight` checks existing outputs without changing them.

Optional flags MUST include:

```text
--seed INTEGER
--profile PROFILE_ID
--force
--only START:END
--output DIRECTORY
--format svg,pdf,json
--editable / --no-editable
```

Exit codes MUST be stable:

```text
0 success
2 invalid input/configuration
3 maze generation or semantic-validation failure
4 rendering or assembly failure
5 PDF/preflight failure
```

### 17.14 Output contract

A successful build MUST write:

```text
output/<book-id>/
├── normalized-book.json
├── mazes/
│   ├── 001.json
│   ├── 001.analysis.json
│   ├── 001.svg
│   └── ...
├── pages/
│   ├── page-001.pdf
│   ├── page-002.pdf
│   └── ...
├── page-plan.json
├── contact-sheet.png
├── preflight.json
├── book-interior-editable.pdf
└── book-interior.pdf
```

The production PDF MUST NOT be announced as successful unless all required preflight checks pass. If an intermediate artifact exists but a later stage fails, the CLI MUST return a non-zero exit code and report the failed stage.

### 17.15 Required PDF preflight

The production build MUST run:

```bash
qpdf --check output/<book-id>/book-interior.pdf
pdftotext output/<book-id>/book-interior.pdf output/<book-id>/book-interior.txt
pdftoppm -jpeg -r 72 output/<book-id>/book-interior.pdf output/<book-id>/validate-page
```

It MUST additionally inspect the PDF with `pypdf` and verify:

- Expected page count.
- Every page has the configured trim dimensions in points.
- No unexpected rotation.
- Single-page page ordering.
- Required text markers, including scene numbers and solution labels.
- Story/maze parity from `page-plan.json`.

If a required external validation executable is unavailable, `book build` MUST fail in CI/production mode. A local diagnostic mode MAY run parser and render checks and clearly report that the external check was skipped.

### 17.16 Required tests

Before repository v1 is accepted, tests MUST cover:

- Book JSON schema and path-safety validation.
- Scene count and scene-number validation.
- Asset discovery, deterministic ordering, and missing-asset failures.
- SVG safety and monochrome validation.
- `mazelib` grid-to-graph conversion.
- Edge canonicalization and graph symmetry.
- Reachability and dead-end detection.
- Cyclomatic-number calculation.
- Exact simple-path enumeration on hand-built graphs.
- No-revisit enforcement.
- Candy scoring and unique maximum selection.
- Halloween C1–C8 constraints.
- Deterministic generation from the same book seed and maze index.
- Independence of other maze indexes when one maze is regenerated.
- Asset footprint and wall/junction collision checks.
- SVG and PDF geometry equivalence for a fixture maze.
- Page parity, blank-page insertion, and facing-pair planning.
- Solutions showing only the selected highest-candy route.
- PDF page size, page count, text markers, and preflight failure behavior.

The repository MUST include fixture packages for:

- A tiny child maze used for fast tests.
- The 50-maze Halloween package.
- A package with multiple collectible variants.
- A package without `page-vectors`.
- A package with supplied front matter.
- A package intentionally containing invalid JSON or SVG for negative tests.

### 17.17 Final definition of done

The repository v1 is complete only when all of the following are true:

1. Copying `books/jims-halloween-maze-adventure/` to a new folder and editing only `book.json` and SVG assets is enough to start a new book.
2. No generator source-code change is required to change theme, story, asset names, collectible variants, or maze count.
3. The exact required asset folders are discovered and used automatically.
4. The complete story is read from one JSON file.
5. `mazelib` is isolated behind one adapter and can be replaced without changing downstream modules.
6. Every accepted maze has canonical JSON, separate analysis JSON, and SVG output.
7. Every accepted maze is exactly validated under the no-revisited-cell rule.
8. The Halloween profile creates a unique highest-candy route and rejects failed candidates.
9. Dead ends receive dead-end assets, and dead-end cells receive no collectibles.
10. Story/maze facing pairs preserve physical page parity.
11. The solutions section shows only the highest-candy route and its candy total.
12. The final interior PDF is black-and-white, no-bleed, single-page, and passes parser, render, and `qpdf --check` gates.
13. Cached maze JSON can be assembled again without regenerating topology.
14. A contact sheet and machine-readable preflight report are produced.
15. Any invalid configuration, missing asset, invalid SVG, failed maze constraint, page-layout error, or PDF-preflight error produces a non-zero result.

This is the final build target for the repository. Publishing prices, listing metadata, cover creation, KDP upload automation, tax/legal pages, AI generation, color interiors, non-grid mazes, and interactive editions remain outside repository v1.

---

## 18. Reconciliation with the prior single-book PRD (normative amendments)

`additional-context-old-file.md` is the earlier, single-book PRD for *Jim's Halloween Maze
Adventure*. It is superseded as a whole by this document, but several of its specifications
are sharper than the generalized wording above. The items in this section are **adopted as
normative amendments to §17** and MUST be implemented. Everything in the prior PRD that is
not listed here is either already covered by §17, is book-specific business content
(pricing, metadata, cover, legal), or is deliberately rejected (§18.9).

### 18.1 Braiding rationale and the loop budget

A perfect maze is a spanning tree with exactly one simple path between any two cells, so
"collect the most candy" has no meaning — the player has no choice. Braiding is therefore
not decoration, it is the mechanic. Adopted requirements:

- The generator MUST produce a perfect maze first, then braid deliberately to a measured
  target `k`.
- `k` MUST be within 3–12 for every maze in every profile (see §17.4).
- New edges MUST create *meaningful* loops. A profile MUST expose `minLoopLength`, and a
  candidate edge MUST be rejected if the cycle it would close is shorter than that value
  (default 6). This forbids closing useless 2×2 pockets.
- Dead ends MUST NOT be braided away entirely: dead ends are what makes a grid read as a
  maze and are the only legal home for dead-end assets.

### 18.2 Route-count floor

In addition to the existing `maxRoutes` ceiling, a profile MUST define `minSimpleRoutes`
(default 8). An attempt whose exact simple-route count is below that floor MUST be rejected
as "too closed" and retried with the next attempt seed, in the same way a cap breach is
rejected as "too open".

### 18.3 Candy-placement weighting

`simulation/candy.py` MUST weight candidate cells by *route frequency* — the fraction of
enumerated routes passing through the cell — and MUST draw predominantly from the 30–70%
band, with a minority drawn from the low-frequency band to reward exploration. Cells on
every route are free candy and carry no decision; cells on a single route are punishing.
This weighting is what makes a placement read as designed rather than random, and it is a
requirement, not a heuristic detail.

### 18.4 Additional per-maze validation

§11.2/§17.8 are extended. An accepted maze MUST additionally satisfy:

- **Edge-hugging:** the best route MUST NOT run within one cell of the grid border for more
  than 50% of its length.
- **Reachability:** at most 35% of traversable cells may be unreachable from the start
  (a book maze SHOULD normally be fully reachable; this is a hard ceiling, and the
  reachable-cell count MUST be recorded in `MazeAnalysis`).
- **Icon overlap:** no two placed assets may have intersecting rendered bounding boxes.
- **Solution clearance:** the rendered solution stroke MUST keep at least 2 pt clearance
  from any wall stroke at the size it is drawn, so the answer line never visually merges
  with a wall.

### 18.5 Hardened SVG asset contract

§17.9 asset validation MUST enforce, exactly, the following (the smallest icons render at
about 5.5 mm on an 18×18 finale maze, which is where sloppy line art fails):

| Rule | Requirement |
|---|---|
| Format | SVG 1.1, plain SVG (not Inkscape SVG), `viewBox="0 0 100 100"` |
| Composition | Artwork centred, ≥ 6 units of empty padding on all four sides |
| Color | Pure black `#000000` fills only — no greys, gradients, `opacity`, `fill-opacity` |
| Geometry | All strokes converted to filled paths; **no `stroke` attribute anywhere** |
| Minimum feature | No line, gap or hole thinner than 7 units in the 100-unit viewBox |
| Complexity | ≤ 12 distinct subpaths per icon |
| Text | No text elements |
| Background | No background rectangle; transparent |
| Raster | No embedded raster, no external references |

Live strokes are rejected because stroke width does not scale with the icon: a stroked icon
that looks correct at 0.6 in becomes a black blob at 5.5 mm and spindly when enlarged.

The validator's subset definition MUST be the single source shared with the internal
SVG→PDF converter (§17.11), so a file that validates is by construction convertible.

### 18.6 Page-furniture measurements

§17.10/§10 are extended with these exact measurements, which MUST be layout defaults and
MUST be config-overridable:

**Maze page (recto, odd):** maze square 6.75 × 6.75 in, horizontally centred in the live
area, top edge 0.75 in below the top margin. Tally strip directly below the maze,
6.75 in wide × 1.35 in tall: one 0.28 in tick box per available candy, a "Total" write-in
box 0.9 × 0.5 in, and the printed "Best possible" number. Maze number 11 pt in the outside
bottom corner. `layout.tallyPosition: "below"` is the default; `"right"` uses a 1.2 in
vertical tally column and shrinks the maze square to 5.9 in — maze size matters more than
layout novelty, so `"below"` MUST remain the default.

**Story page (verso, even):** scene number 28 pt centred, 3.2 in from the top; title 34 pt
all-caps centred display face; body 16 pt centred, at most two lines, 1.5 line spacing; one
decorative page vector at 0.6 in centred below the body; optional corner ornament in the
outside top corner, story pages only. The page is deliberately mostly empty — white space
is what makes a two-color interior read as designed rather than thin.

**Solutions pages:** 9 mazes per page in a 3×3 grid, each thumbnail 2.1 × 2.1 in; best
route drawn as a 1.5 pt solid line with rounded joins against 0.75 pt walls; candies on the
best route drawn as small filled dots, never full icons (icons are illegible at that
scale); caption `"<n>. Best: <total> candies"` under each thumbnail.

### 18.7 Parity is a two-page invariant

Front matter MUST have an **odd** page count so scene 1's story page lands on an even
(left) page. Any front-matter or divider content MUST be added or removed **two pages at a
time**; a one-page change flips every spread in the book. This MUST be asserted in
preflight against the actual page count of the supplied front-matter PDF, not assumed.

### 18.8 Hardened PDF preflight

§17.15 is extended. `book preflight` MUST check all of the following and MUST exit 5 on any
failure:

| Check | Requirement |
|---|---|
| Page count | Equals `layout.expectedPageCount` and is even |
| Page size | Every page exactly `trimWidthIn × 72` × `trimHeightIn × 72` pt (612 × 792 for 8.5 × 11), no rotation flags |
| Fonts | Zero embedded fonts in the outlined interior (`book-interior.pdf`) |
| Color space | DeviceGray only — no RGB, CMYK or spot colors |
| Ink | Pure black `0.0` gray; no tint below 100% anywhere |
| Transparency | Fully flattened; no transparency groups |
| Safe area | No mark within 0.25 in of any trim edge |
| Raster | No embedded raster images at all; vector only |
| Parity | Page 6 and every subsequent even page in the scene range is a story page |
| Cross-check | Every printed "Best possible" number equals `bestCandyTotal` in the corresponding `MazeAnalysis` JSON |
| Structure | `qpdf --check` clean; `pdftotext` finds expected text markers; `pdftoppm` renders every page |

The cross-check is mandatory and non-negotiable: a book whose printed target score
disagrees with its own answer key is a defect that reaches every copy.

### 18.9 Deliberately rejected from the prior PRD

- **`svglib`.** §17.11 requires one shared geometry/subset definition; a second SVG code
  path is how SVG and PDF output diverge. The internal minimal converter is kept.
- **`PyYAML` / YAML configs.** The locked contract is JSON (`book.json`, profiles,
  `MazeData`, `MazeAnalysis`) with JSON Schema validation and byte-stable serialization.
- **`hash((BOOK_ID, scene_id))` for seeding.** Python's `hash()` is salted per process and
  is not reproducible. §17.12's explicit derived seed streams replace it.
- **Wall-dict `Cell` data model.** Four booleans per cell can encode a wall disagreement
  between neighbours; the open-edge graph of §17.5 cannot.
- **Book-specific module names** (`src/jimmaze/`, `render_maze.py`, `build_cover.py`) and
  Make targets. The repository is theme-agnostic; §17.2 and the §17.13 CLI stand.

### 18.10 Out of repository scope (recorded, not implemented)

The prior PRD's commercial and compliance content is real and useful but is **not** part of
repository v1 and MUST NOT be encoded in `src/`: print-cost and royalty arithmetic,
per-marketplace pricing, cover geometry and spine width, listing metadata and keywords,
AI-content disclosure answers, German imprint/GPSR/DNB/Buchpreisbindung obligations, the
publishing timeline, and the human acceptance tests (printed 5.5 mm icon test, contact-sheet
eyeball review, and a real 7–9 year old completing mazes 1, 25 and 50 unaided). These belong
in per-book documentation. Two of them are load-bearing for quality and are therefore
surfaced by the tooling even though the judgement stays human: the contact sheet
(`output/<book-id>/contact-sheet.png`) exists so the eyeball review is possible, and the
asset validator exists so the 5.5 mm test is only ever needed on files that already pass
the mechanical rules.
