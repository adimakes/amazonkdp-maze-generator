---
name: new-book
description: |
  Author a new puzzle-book package for this repository: the story text, the
  asset treatment, the difficulty profile, the front matter and the cover.
  Use when adding a book under books/, changing a book's theme or scenes,
  turning supplied artwork into assets, writing or revising a profile, or
  reviewing a built interior. Covers the judgement the engine cannot make:
  src/ knows how to build a book, not what makes one worth buying.
metadata:
  version: "1.0.0"
---

# Authoring a book package

The engine in `src/` is book-agnostic and stays that way. Everything it cannot
decide is here: what the scenes say, which drawing survives at 5 mm, what a
profile may promise, and what a page has to show a six-year-old. All of it was
learned by building one book and having it reviewed twice against real printed
pages.

**Read `CLAUDE.md` first** for the architecture and the locked contract, then
this for the judgement. When the two disagree, `CLAUDE.md` wins on mechanism and
this wins on taste.

## Start here

```bash
cp -r books/jims-halloween-maze-adventure books/<new-book-id>
uv run maze-book book validate books/<new-book-id>
```

Then work in this order, because each step constrains the next:

1. **Profile** — grid sizes decide how big every icon prints.
2. **Assets** — printed size decides which drawings survive.
3. **Scenes** — the asset roster decides what the story can refer to.
4. **Front matter and cover** — the built interior decides the spine.
5. **Review** — by looking at rendered pages, not by reading config.

Never add a branch to `src/` for a book. If you want to, the answer is a config
knob or a profile field, and the knob's name goes in `schemas/book.schema.json`
and the README table.

---

## 1. The profile: promises the generator can keep

A profile is a set of promises. Three numbers will make one unsatisfiable while
every schema and unit test still passes; `tests/test_profile.py` guards all
three, and `CLAUDE.md` states them. Beyond those, two things decide how the book
feels.

**The ramp is the product.** A parent buying for a six-year-old needs to see
where to stop, and a ten-year-old needs a reason to start at maze 1. Name every
band (`actName`) — the names are printed above the scene number — and ramp grid
size, candy count and loop budget together. A jump of more than one grid step
between adjacent bands reads as a cliff.

**Small cells punish everything downstream.** An 18×18 finale on a 7.3 in maze
square gives 8.3 mm cells before markers are reserved. Work out the printed size
of every icon *before* writing the band:

```python
cell_mm = maze_square_in * 25.4 / max(rows, cols)
collectible_mm = cell_mm * collectible_scale
```

Below about 4 mm a silhouette cannot hold any feature the drawing is made of,
and the asset validator will say so — correctly. If a band forces that, the band
is too fine, not the artwork.

**Settle profile questions by measuring.** Generate against a patched copy and
count acceptances. Arguing from the numbers is how three shipped profiles came
to accept zero mazes.

---

## 2. Assets: what survives, and how you find out

`tools/import_artifacts.py` traces a manifest of raster artwork into the subset,
validating each result against the profile its printed size earns **before**
writing it. The manifest records one judgement per drawing, and that judgement
has to be made by looking.

### Render both treatments and choose

```bash
# trace one file both ways and compare
uv run python tools/vectorize.py <source.jpeg> /tmp/a.svg --mode solid
uv run python tools/vectorize.py <source.jpeg> /tmp/b.svg --mode solid --flat
```

Then rasterize both at the size they will print and put them side by side. A
contact sheet of the whole folder is worth more than any single file:

```python
from maze_book.assets.svg_subset import parse_svg
from maze_book.assets.validate import rasterize
mask = rasterize(parse_svg(path), samples_per_unit=150 / 100.0)
```

### What each treatment is for

| Mode | Use when | Why |
|---|---|---|
| `solid` | the drawing is an outline and prints small | a contour at 5 mm is 0.22 mm and closes up; the silhouette has to carry it |
| `solid` + `keep_knockouts: false` | the white inside is just fill | candy corn reads as a cone; hollow it reads as an outline of a cone |
| `outline` | the artwork prints large | keeps the drawn line, widened until it prints |

**The knockouts are usually the drawing.** At icon size the white inside the
black is what the eye reads: fill it and a ghost becomes a blob, a gravestone
loses its cross, a web becomes a triangle. `solidify()` keeps them by default.
Turn them off only when the white was never a feature.

### The arithmetic that decides whether a drawing can work at all

Measure the drawn line as a fraction of the drawing's width, in a 100-unit box.
Compare it to what the press needs at the printed size:

```
needed_units = 0.39 mm / printed_mm * 100
```

If the line is much thinner than that, widening it welds neighbouring strokes
together before anything reads. A figure whose line is 0.73 units wide cannot
print at 14 mm — that was true at 15, 20, 26 and 32 mm when tested. **No
parameter fixes it. Pick a different drawing.**

### Choosing the roster

- **Candy must not look like scenery.** Both are black silhouettes of similar
  weight, so size is the only signal: keep `collectibleScale` and
  `deadEndScale` at least 1.3× apart. Don't push further than the finale cell
  allows — an unreadable smudge is not a clearer signal than a small one.
- **Print a key.** The how-to-play page shows the collectibles under one
  heading and the decorations under another, **at the real size ratio**. Rows
  drawn the same size teach the opposite of what the page says.
- **One character.** The start marker, the story-page figure and any decoration
  must not be three different drawings of the same person, and the character's
  own drawing must not appear as page decoration (`assets.mazeDecorations`
  excludes it) — a child reads that as a second protagonist standing somewhere
  meaningless.
- **Drop what does not read.** A corner-anchored web drawn centred in a cell
  reads as floating arcs. Six good dead-end icons beat nine with three
  passengers.

---

## 3. Scenes: fifty of them, and they must not sound like fifty

Scenes live in `book.json` under `content.scenes[]`. Two hard constraints, then
the taste.

**Hard limits.** The body must fit two lines at the body size, and the title
must fit one line at the title size. `book validate` reports both per scene:

```bash
uv run maze-book book validate books/<book-id>
```

**Run the `humanizer` skill over the whole set**, then check the statistics,
because the failure mode is invisible one scene at a time. A first draft of
fifty scenes had 48 in the same two-sentence shape and 41 titles starting "The",
and each one read fine alone.

```python
from collections import Counter
Counter(t.count(".") + t.count("!") + t.count("?") for t in texts)   # want a spread
sum(1 for title in titles if title.startswith("The "))               # want under half
sum(1 for t in texts if CHARACTER in t)                              # want 40+ of 50
```

Aim for roughly 8 one-sentence scenes, 5 three-sentence ones, and the rest two.

**Address the child.** A book where nothing is asked of the reader is a list of
captions. Put a direct question in a dozen scenes: *Can you get him there before
dark? Which way, quick?*

**Give the character one trait and let it recur.** The line reviewers quoted
back was *"Jim knows it is rubber. Jim still ducks."* Three lines of that across
fifty scenes is the difference between a name and a person.

**Continuity is checkable and gets checked.** If scene 6 is where the character
finds the bucket, scene 1 cannot already have it. Read the set start to finish
once, as a reader would.

**Match the locale.** `book.locale` is not decoration: `practise`/`practice`,
`tick`/`color in`, `sweets`/`candy`, `garden`/`yard`, `wardrobe`/`closet`. Pick
one vocabulary and sweep for the other.

**The story goal and the finish marker will disagree**, because the marker is
the same asset on every page while the text sends the character somewhere new.
Say so once on the how-to-play page — *wherever he is going, the bucket marks
the finish* — rather than contorting fifty scenes.

---

## 4. Front matter and cover

**Front matter must have an odd page count**, and changes go **two pages at a
time**: a one-page change flips every spread in the book.

- The words live in `content.howToPlay`, `content.dedication` and
  `content.meetPage`, never in `tools/`. A default that names one book's
  character puts it in every other book's front matter.
- **Spend the fifth page.** Amazon's preview opens on exactly this run, so a
  half title that reprints page 1 costs a sale. Introduce the character.
- Set `book.author`, or the copyright line names the book as its own owner.
- `book.printContentOrigin` decides whether the AI-origin line is printed.
  **KDP's own disclosure is made in the publishing form and is required either
  way**; this only decides whether the buyer reads it too. It is the
  publisher's call, so ask rather than assume.
- Left-align numbered lists. A centred numbered list is the clearest
  self-published tell in a book.

**The cover** is built after the interior, because its spine comes from the
built PDF's page count:

```bash
uv run python tools/make_cover.py --book books/<book-id>
```

- **Show the real mazes.** If the back-cover artwork has sample cards, measure
  them once and record them as `cover.cards` with `cover.samples`; the tool
  pastes real pages in. Invented samples that look nothing like the interior are
  the "the inside doesn't look like the cover" complaint that costs stars.
- The barcode block is 2.0 × 1.2 in, inset 0.25 in from the trimmed corner.
  Artwork usually reserves less; the larger clearing wins, and say so, because
  it may cover part of the design.
- The title on the cover and the title in `book.json` **must match** — KDP
  requires it. The artwork is fixed, so the metadata follows the artwork.

---

## 5. Review: look at the pages

Constraints catch per-maze defects. They cannot see that the book is dull.

**Read the contact sheet first** (`output/<book-id>/contact-sheet.png`): fifty
mazes and their answers in one image shows the ramp, repetition and any solution
that hugs an edge in a few seconds.

**Then render and look at real pages.**

```bash
pdftoppm -r 110 -png -f 6 -l 7 output/<book-id>/book-interior.pdf /tmp/p
```

Look at: the front matter, an early spread, a middle spread, a late spread, a
solutions page, and the last page.

**Then have it reviewed from outside.** Spawn an agent as a children's book
editor reviewing from the child's and the parent's side at once. Tell it to
render pages and actually look at them, that the artwork set is fixed, and to
rank findings and end with the three-star review the book would get. Two passes
is right: the second verifies the first landed and catches what the fixes broke.

**Verify a disputed finding by measuring it yourself.** One review reported the
spine off-centre with crammed text; measuring the rendered wrap showed both
correct. Reviewers work from rasterized pixels and can be wrong. Measure, then
say so.

### What reviews found that nothing else would

Worth checking directly on any new book:

- Can a six-year-old tell in one second where to start? A drawing is not the
  word START.
- Can they tell candy from scenery three inches apart on a page — not side by
  side in a key?
- Does "THE END" come after the answers?
- Does a story page have anything on it below the text?
- Does the grid sit in the same place on every page? Flip through and watch.
- Does the folio read as a page number when the book has none?
- Is the answer key usable — can you tell which end is the start?

---

## Checklist before shipping

```bash
uv run pytest -q                                              # all green
uv run maze-book book build books/<book-id> --force           # all preflight checks
uv run python tools/make_cover.py --book books/<book-id>
```

- Preflight passes with **no skips**. A skip that reads as a pass is how an
  unchecked PDF gets announced as print-ready.
- Build twice and compare hashes: the interior and the cover are byte-identical
  for a fixed input.
- The contact sheet shows a ramp, not a plateau.
- Someone outside the build has looked at real pages and said what is wrong.
