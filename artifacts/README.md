# artifacts/

Scratch space for artwork and one-off outputs that are **not** part of a book
package. Nothing here is read by the build — a file only becomes an asset once it
is copied into `books/<book-id>/assets/` under one of the fixed folder names.

| Datei | Inhalt |
| --- | --- |
| `ghost.svg` | Cartoon-Geist, monochrom, `viewBox 0 0 100 100`, ein Pfad mit vier Subpfaden |
| `ghost.png` | Derselbe Geist, 1024 × 1024, RGBA mit transparentem Hintergrund |

`ghost.svg` besteht den Asset-Validator des Repos:

```
pad 10.0 | 4 subpaths | ink 30%
```

Gebaut wurde er parametrisch statt von Hand: Kuppel, Seiten, die sich zum Saum
verjüngen, und ein Saum aus Halbkreis-Lappen mit gerundeten Einbuchtungen. Diese
Rundung ist keine Stilfrage — der übliche Geistersaum aus tangentialen Kreisen
läuft zwischen den Lappen spitz zu, und eine Spitze liegt unter den 7 Einheiten,
die `min_feature_units` verlangt. Bei 5,5 mm sind das 0,39 mm, und darunter läuft
die Stelle im Druck zu.

Zum Einbauen in ein Buch:

```bash
cp artifacts/ghost.svg books/<book-id>/assets/maze-vectors/collectibles/
uv run maze-book book validate books/<book-id>
```
