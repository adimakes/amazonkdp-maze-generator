# broken-book

A negative fixture (PRD 17.16). Every file here is wrong on purpose:

| file | defect | caught by |
|---|---|---|
| `collectibles/not-xml.svg` | truncated, not well-formed XML | `A1_subset` |
| `collectibles/stroked.svg` | `stroke` attributes | `A1_subset` (18.5) |
| `dead-end/hairline.svg` | a 2-unit limb | `A7_minimumFeature` |
| `beginning-vectors/start.svg` | `<circle>`, outside the subset | `A1_subset` |
| `book.json` | mutated per test | varies |

`ending-vectors/finish.svg` is deliberately valid, so a test asserting a
specific failure is not silently passing because everything failed.
