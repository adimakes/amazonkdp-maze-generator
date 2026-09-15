"""Generate the placeholder Halloween SVG set (PRD 17.9 subset).

These are placeholders in *subject* only. Every one of them obeys the same rules
the shipping artwork will have to obey, because the point of a placeholder is to
exercise the pipeline honestly:

*   a ``viewBox`` and no ``width``/``height``, so the renderer scales by cell
    size rather than by whatever the illustrator's canvas happened to be;
*   no external references, scripts, entities, filters, raster images or text
    nodes -- the validator in ``assets/validate.py`` rejects all of those;
*   no background rectangle, so the maze walls stay visible behind the art;
*   pure ``#000000``, since the interior prints black-and-white. Where a shape
    needs a hole (a ghost's eye on a filled body) it is drawn with ``fill-rule``
    rather than with white paint, which would print as a white block on cream
    paper.

Stroke widths are heavy on purpose: a collectible at scale 0.5 in an 18x18 grid
is about 0.19 in on the page, so a 1-unit stroke on a 100-unit viewBox would come
out at half a point and disappear into the paper texture. Everything here uses 5
to 7 units, round-capped.

Run with: uv run python tools/make_placeholder_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

STROKE = 'fill="none" stroke="#000000" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"'
THIN = 'fill="none" stroke="#000000" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"'
SOLID = 'fill="#000000"'


def svg(body: str, *, view: str = "0 0 100 100") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">\n{body}\n</svg>\n'
    )


# --------------------------------------------------------------------------- #
# Collectibles -- read at 0.19 in, so silhouettes with one distinguishing mark
# --------------------------------------------------------------------------- #

COLLECTIBLES = {
    "candy_wrapped.svg": svg(
        f'  <ellipse cx="50" cy="50" rx="24" ry="19" {STROKE}/>\n'
        f'  <path d="M26 50 L8 36 L12 50 L8 64 Z" {STROKE}/>\n'
        f'  <path d="M74 50 L92 36 L88 50 L92 64 Z" {STROKE}/>\n'
        f'  <path d="M42 44 L58 56 M58 44 L42 56" {THIN}/>'
    ),
    "lollipop.svg": svg(
        f'  <circle cx="50" cy="38" r="26" {STROKE}/>\n'
        f'  <path d="M50 38 A9 9 0 0 1 59 47 A18 18 0 0 1 41 47 A26 26 0 0 1 67 47" {THIN}/>\n'
        f'  <path d="M50 64 L50 94" {STROKE}/>'
    ),
    "candy_corn.svg": svg(
        f'  <path d="M50 8 L74 88 Q50 96 26 88 Z" {STROKE}/>\n'
        f'  <path d="M36 46 L64 46 M31 66 L69 66" {THIN}/>'
    ),
    "chocolate_bar.svg": svg(
        f'  <rect x="20" y="26" width="60" height="48" rx="6" {STROKE}/>\n'
        f'  <path d="M40 26 L40 74 M60 26 L60 74 M20 50 L80 50" {THIN}/>'
    ),
    "gummy_worm.svg": svg(
        f'  <path d="M16 62 Q28 30 40 54 Q52 78 64 46 Q74 20 86 44" '
        f'fill="none" stroke="#000000" stroke-width="13" stroke-linecap="round"/>\n'
        f'  <circle cx="86" cy="44" r="3" {SOLID}/>'
    ),
    "candy_cane.svg": svg(
        f'  <path d="M62 92 L62 40 A18 18 0 0 1 34 40" '
        f'fill="none" stroke="#000000" stroke-width="12" stroke-linecap="round"/>\n'
        f'  <path d="M40 30 L52 38 M62 54 L74 54 M62 74 L74 74" {THIN}/>'
    ),
}

# --------------------------------------------------------------------------- #
# Dead ends -- "wrong turn" markers, so each one is a small warning
# --------------------------------------------------------------------------- #

DEAD_ENDS = {
    "web_corner.svg": svg(
        f'  <path d="M6 6 L94 62 M6 6 L62 94 M6 6 L74 74 M6 6 L40 92 M6 6 L92 40" {THIN}/>\n'
        f'  <path d="M34 12 Q26 26 12 34 M56 22 Q40 44 22 56 M78 34 Q54 62 34 78" {THIN}/>'
    ),
    "black_cat.svg": svg(
        f'  <path d="M30 40 L22 16 L42 30 M70 40 L78 16 L58 30" {STROKE}/>\n'
        f'  <circle cx="50" cy="52" r="24" {STROKE}/>\n'
        f'  <circle cx="41" cy="48" r="4" {SOLID}/>\n'
        f'  <circle cx="59" cy="48" r="4" {SOLID}/>\n'
        f'  <path d="M50 58 L50 64 M50 64 L42 68 M50 64 L58 68" {THIN}/>\n'
        f'  <path d="M22 56 L4 52 M22 62 L6 66 M78 56 L96 52 M78 62 L94 66" {THIN}/>'
    ),
    "bat.svg": svg(
        f'  <circle cx="50" cy="50" r="14" {STROKE}/>\n'
        f'  <path d="M42 40 L36 26 L48 34 M58 40 L64 26 L52 34" {THIN}/>\n'
        f'  <path d="M36 46 Q18 30 6 44 Q16 46 12 60 Q26 60 36 56 Z" {STROKE}/>\n'
        f'  <path d="M64 46 Q82 30 94 44 Q84 46 88 60 Q74 60 64 56 Z" {STROKE}/>'
    ),
    "ghost.svg": svg(
        f'  <path d="M22 88 L22 46 A28 28 0 0 1 78 46 L78 88 L66 78 L54 88 L42 78 Z" {STROKE}/>\n'
        f'  <circle cx="40" cy="46" r="5" {SOLID}/>\n'
        f'  <circle cx="60" cy="46" r="5" {SOLID}/>\n'
        f'  <path d="M44 62 Q50 68 56 62" {THIN}/>'
    ),
    "gravestone.svg": svg(
        f'  <path d="M24 92 L24 40 A26 26 0 0 1 76 40 L76 92 Z" {STROKE}/>\n'
        f'  <path d="M50 34 L50 66 M36 46 L64 46" {STROKE}/>\n'
        f'  <path d="M10 92 L90 92" {STROKE}/>'
    ),
}

# --------------------------------------------------------------------------- #
# Endpoints -- the identity of the book, named explicitly in book.json
# --------------------------------------------------------------------------- #

START = {
    "jim_start.svg": svg(
        f'  <circle cx="46" cy="26" r="16" {STROKE}/>\n'
        f'  <circle cx="41" cy="24" r="3" {SOLID}/>\n'
        f'  <circle cx="52" cy="24" r="3" {SOLID}/>\n'
        f'  <path d="M30 18 Q46 2 62 18" {THIN}/>\n'
        f'  <path d="M46 42 L46 70 M46 50 L26 60 M46 50 L68 58 '
        f'M46 70 L34 94 M46 70 L58 94" {STROKE}/>\n'
        f'  <path d="M62 58 L78 58 L74 78 L66 78 Z" {THIN}/>'
    ),
}

FINISH = {
    "candy_bucket.svg": svg(
        f'  <path d="M20 44 L28 88 Q50 94 72 88 L80 44 Z" {STROKE}/>\n'
        f'  <path d="M20 44 L80 44" {STROKE}/>\n'
        f'  <path d="M30 44 Q50 8 70 44" {STROKE}/>\n'
        f'  <path d="M38 58 L44 66 M38 66 L44 58" {THIN}/>\n'
        f'  <ellipse cx="60" cy="64" rx="8" ry="6" {THIN}/>\n'
        f'  <path d="M40 78 L62 78" {THIN}/>'
    ),
}

# --------------------------------------------------------------------------- #
# Page vectors -- story-page decoration, drawn larger and lighter
# --------------------------------------------------------------------------- #

PAGE_VECTORS = {
    "jim_costume.svg": svg(
        f'  <path d="M50 14 L74 30 L68 86 L32 86 L26 30 Z" {STROKE}/>\n'
        f'  <path d="M50 14 L50 86" {THIN}/>\n'
        f'  <path d="M26 30 L8 46 M74 30 L92 46" {STROKE}/>\n'
        f'  <path d="M38 44 L44 52 M56 52 L62 44 M40 66 Q50 76 60 66" {THIN}/>'
    ),
    "jim_head.svg": svg(
        f'  <circle cx="50" cy="52" r="30" {STROKE}/>\n'
        f'  <circle cx="40" cy="48" r="4" {SOLID}/>\n'
        f'  <circle cx="60" cy="48" r="4" {SOLID}/>\n'
        f'  <path d="M38 66 Q50 78 62 66" {STROKE}/>\n'
        f'  <path d="M24 34 Q50 6 76 34" {STROKE}/>\n'
        f'  <path d="M20 52 L8 52 M80 52 L92 52" {THIN}/>'
    ),
    "deco_moon.svg": svg(
        f'  <path d="M62 12 A40 40 0 1 0 62 88 A32 32 0 1 1 62 12 Z" {STROKE}/>\n'
        f'  <circle cx="46" cy="34" r="5" {THIN}/>\n'
        f'  <circle cx="38" cy="58" r="7" {THIN}/>\n'
        f'  <circle cx="52" cy="72" r="4" {THIN}/>'
    ),
    "deco_tree.svg": svg(
        f'  <path d="M50 96 L50 44" {STROKE}/>\n'
        f'  <path d="M50 62 L24 40 M50 54 L74 34 M50 76 L28 66 M50 70 L76 58" {STROKE}/>\n'
        f'  <path d="M24 40 L12 26 M24 40 L14 44 M74 34 L86 22 M74 34 L88 38" {THIN}/>\n'
        f'  <path d="M34 96 Q50 88 66 96" {THIN}/>'
    ),
    "deco_ghost.svg": svg(
        f'  <path d="M20 90 L20 42 A30 30 0 0 1 80 42 L80 90 L66 78 L50 90 L34 78 Z" {STROKE}/>\n'
        f'  <circle cx="38" cy="44" r="6" {SOLID}/>\n'
        f'  <circle cx="62" cy="44" r="6" {SOLID}/>\n'
        f'  <ellipse cx="50" cy="64" rx="9" ry="7" {STROKE}/>'
    ),
    "deco_bat.svg": svg(
        f'  <circle cx="50" cy="52" r="12" {STROKE}/>\n'
        f'  <path d="M43 43 L38 30 L49 37 M57 43 L62 30 L51 37" {THIN}/>\n'
        f'  <path d="M38 48 Q16 28 2 44 Q14 48 10 64 Q28 64 38 58 Z" {STROKE}/>\n'
        f'  <path d="M62 48 Q84 28 98 44 Q86 48 90 64 Q72 64 62 58 Z" {STROKE}/>\n'
        f'  <circle cx="45" cy="50" r="3" {SOLID}/>\n'
        f'  <circle cx="55" cy="50" r="3" {SOLID}/>'
    ),
    "deco_spider.svg": svg(
        f'  <path d="M50 4 L50 30" {THIN}/>\n'
        f'  <ellipse cx="50" cy="52" rx="18" ry="22" {STROKE}/>\n'
        f'  <path d="M32 42 L10 30 L4 46 M32 52 L8 54 M32 62 L10 76 L18 88" {THIN}/>\n'
        f'  <path d="M68 42 L90 30 L96 46 M68 52 L92 54 M68 62 L90 76 L82 88" {THIN}/>\n'
        f'  <circle cx="44" cy="44" r="3" {SOLID}/>\n'
        f'  <circle cx="56" cy="44" r="3" {SOLID}/>'
    ),
    "deco_gravestone.svg": svg(
        f'  <path d="M22 88 L22 36 A28 28 0 0 1 78 36 L78 88 Z" {STROKE}/>\n'
        f'  <path d="M50 28 L50 62 M34 42 L66 42" {STROKE}/>\n'
        f'  <path d="M4 88 L96 88" {STROKE}/>\n'
        f'  <path d="M14 88 Q20 78 26 88 M74 88 Q80 76 86 88" {THIN}/>'
    ),
    "deco_web_corner.svg": svg(
        f'  <path d="M4 4 L96 60 M4 4 L60 96 M4 4 L76 76 M4 4 L36 94 M4 4 L94 36" {THIN}/>\n'
        f'  <path d="M30 10 Q22 22 10 30 M54 20 Q38 40 20 54 M78 32 Q54 60 32 78" {THIN}/>\n'
        f'  <path d="M62 62 L62 78" {THIN}/>\n'
        f'  <ellipse cx="62" cy="84" rx="7" ry="9" {SOLID}/>'
    ),
}

TARGETS = {
    "assets/beginning-vectors": START,
    "assets/ending-vectors": FINISH,
    "assets/maze-vectors/dead-end": DEAD_ENDS,
    "assets/maze-vectors/collectibles": COLLECTIBLES,
    "assets/page-vectors": PAGE_VECTORS,
}


def main(package_dir: Path) -> int:
    written = 0
    for rel, files in TARGETS.items():
        directory = package_dir / rel
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (directory / name).write_text(content, encoding="utf-8")
            written += 1
        print(f"{rel}: {len(files)} file(s)")
    print(f"wrote {written} placeholder SVGs to {package_dir}")
    return 0


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("books/jims-halloween-maze-adventure")
    raise SystemExit(main(target))
