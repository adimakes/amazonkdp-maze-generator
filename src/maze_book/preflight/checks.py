"""PDF preflight: the gate between "it built" and "it prints" (PRD 17.15, 18.8).

Every check here exists because the corresponding defect is invisible on screen
and expensive on paper. A page 0.3 pt off trim, a font the press substitutes, a
99% black that halftones into grey on cream stock -- all of them render perfectly
in a viewer and all of them arrive wrong in a carton.

18.8 makes one check non-negotiable and says why: "a book whose printed target
score disagrees with its own answer key is a defect that reaches every copy".
That is the cross-check between each maze page's printed "Best possible" number
and ``bestCandyTotal`` in the analysis JSON, and it is done by reading the text
back out of the finished PDF rather than by trusting the value that was passed to
the renderer -- the point is to catch the renderer, not to agree with it.

External tools are required, not optional. 17.15: "If a required external
validation executable is unavailable, ``book build`` MUST fail in CI/production
mode." ``strict=False`` is the local diagnostic mode, which runs everything else
and reports the skip explicitly; it never reports a skipped check as a pass.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from ..errors import PreflightError
from ..model.analysis import MazeAnalysis
from ..model.labels import DEFAULT_LABELS, Labels, find_best_possible
from ..model.book_config import BookConfig
from ..rendering.page import PT_PER_IN

SAFE_AREA_IN = 0.25
REQUIRED_TOOLS = ("qpdf", "pdftotext", "pdftoppm")

#: Content-stream operators that set a colour outside DeviceGray.
_COLOR_OPS = re.compile(rb"(?<![A-Za-z0-9])(rg|RG|k|K|sc|SC|scn|SCN)(?![A-Za-z0-9])")
_GRAY_OPS = re.compile(rb"([0-9.]+)\s+(g|G)(?![A-Za-z0-9])")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    skipped: bool = False

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "status": "skipped" if self.skipped else ("pass" if self.passed else "fail"),
            "detail": self.detail,
        }


@dataclass
class PreflightReport:
    pdf: Path
    results: list[CheckResult] = field(default_factory=list)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed and not r.skipped]

    @property
    def skipped(self) -> list[CheckResult]:
        return [r for r in self.results if r.skipped]

    @property
    def passed(self) -> bool:
        return not self.failures

    def add(self, name: str, ok: bool, detail: str = "", *, skipped: bool = False) -> CheckResult:
        result = CheckResult(name=name, passed=ok, detail=detail, skipped=skipped)
        self.results.append(result)
        return result

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "pdf": str(self.pdf),
            "passed": self.passed,
            "checks": [r.to_json_obj() for r in self.results],
            "failed": [r.name for r in self.failures],
            "skipped": [r.name for r in self.skipped],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json_obj(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def raise_for_status(self) -> None:
        if self.failures:
            raise PreflightError(
                f"{len(self.failures)} preflight check(s) failed for {self.pdf.name}",
                details=[f"{r.name}: {r.detail}" for r in self.failures],
            )

    def summary(self) -> str:
        ok = sum(1 for r in self.results if r.passed and not r.skipped)
        return (
            f"{self.pdf.name}: {ok} passed, {len(self.failures)} failed, "
            f"{len(self.skipped)} skipped"
        )


# --------------------------------------------------------------------------- #
# Tool availability
# --------------------------------------------------------------------------- #


def missing_tools() -> list[str]:
    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def _run(command: Sequence[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command), capture_output=True, text=True, timeout=timeout, check=False
    )


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #


def check_pages(report: PreflightReport, reader, *, expected_count: int | None,
                width_pt: float, height_pt: float) -> None:
    count = len(reader.pages)
    if expected_count is not None:
        report.add(
            "page-count",
            count == expected_count,
            f"{count} pages, expected {expected_count}",
        )
    report.add("page-count-even", count % 2 == 0, f"{count} pages")

    bad_size: list[str] = []
    rotated: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        if abs(float(box.width) - width_pt) > 0.5 or abs(float(box.height) - height_pt) > 0.5:
            bad_size.append(f"page {index}: {float(box.width):.2f}x{float(box.height):.2f}")
        rotation = page.get("/Rotate", 0) or 0
        if int(rotation) % 360 != 0:
            rotated.append(f"page {index}: /Rotate {rotation}")

    report.add(
        "page-size",
        not bad_size,
        f"expected {width_pt:.0f}x{height_pt:.0f} pt; "
        + (", ".join(bad_size[:5]) if bad_size else "every page matches"),
    )
    report.add(
        "page-rotation", not rotated, ", ".join(rotated[:5]) if rotated else "no rotation flags"
    )


def _font_objects(page) -> list[tuple[str, Any]]:
    resources = page.get("/Resources")
    if resources is None:
        return []
    fonts = resources.get_object().get("/Font")
    if fonts is None:
        return []
    return [(str(name), obj.get_object()) for name, obj in fonts.get_object().items()]


def _is_embedded(font: Any) -> bool:
    descriptors = []
    if "/FontDescriptor" in font:
        descriptors.append(font["/FontDescriptor"].get_object())
    for descendant in font.get("/DescendantFonts", []) or []:
        child = descendant.get_object()
        if "/FontDescriptor" in child:
            descriptors.append(child["/FontDescriptor"].get_object())
    if not descriptors:
        return False
    return any(
        any(key in descriptor for key in ("/FontFile", "/FontFile2", "/FontFile3"))
        for descriptor in descriptors
    )


def check_fonts(report: PreflightReport, reader) -> None:
    """18.8: every font embedded and subset, in both PDFs.

    17.11 defers text-to-outline for v1 because outlining makes proofreading and
    text preflight harder, so the v1 rule is "all embedded", not "none present".
    A non-embedded font is the defect that matters: the press substitutes
    something metrically different and every line re-wraps.
    """
    unembedded: list[str] = []
    seen: set[str] = set()
    for index, page in enumerate(reader.pages, start=1):
        for name, font in _font_objects(page):
            base = str(font.get("/BaseFont", name))
            if base in seen:
                continue
            seen.add(base)
            if not _is_embedded(font):
                unembedded.append(f"page {index}: {base}")
    report.add(
        "fonts-embedded",
        not unembedded,
        ", ".join(unembedded[:5]) if unembedded else f"{len(seen)} font(s), all embedded",
    )


def _content_bytes(page) -> bytes:
    contents = page.get_contents()
    if contents is None:
        return b""
    try:
        return contents.get_data()
    except AttributeError:
        return b"".join(part.get_object().get_data() for part in contents)


def _without_strings(data: bytes) -> bytes:
    """Content stream with its string literals blanked out.

    Operators and text share one byte stream, so a page that *prints* the word
    DARK spaced out as letters contains a lone ``K`` inside a string -- and a
    regex looking for the CMYK operator finds it. The book failed its own
    colour check for setting a running head in solid black. Strings are removed
    before any operator is looked for; nothing a page draws with can hide inside
    one.
    """
    out = bytearray()
    index, length = 0, len(data)
    while index < length:
        byte = data[index]
        if byte == 0x28:  # (
            depth = 1
            index += 1
            while index < length and depth:
                if data[index] == 0x5C:  # backslash escape
                    index += 2
                    continue
                if data[index] == 0x28:
                    depth += 1
                elif data[index] == 0x29:
                    depth -= 1
                index += 1
            out += b" "
            continue
        if byte == 0x3C:  # < opens a hex string, << opens a dictionary
            if index + 1 < length and data[index + 1] == 0x3C:
                out += b"<<"
                index += 2
                continue
            index = data.find(b">", index)
            index = length if index == -1 else index + 1
            out += b" "
            continue
        out.append(byte)
        index += 1
    return bytes(out)


def check_color_and_ink(report: PreflightReport, reader) -> None:
    colored: list[str] = []
    tints: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        data = _without_strings(_content_bytes(page))
        if _COLOR_OPS.search(data):
            operators = sorted({m.decode() for m in _COLOR_OPS.findall(data)})
            colored.append(f"page {index}: {'/'.join(operators)}")
        for value, _ in _GRAY_OPS.findall(data):
            level = float(value)
            if 0.0 < level < 1.0:
                tints.append(f"page {index}: gray {level:g}")
                break
    report.add(
        "color-space-devicegray",
        not colored,
        ", ".join(colored[:5]) if colored else "DeviceGray only",
    )
    report.add(
        "ink-pure-black",
        not tints,
        ", ".join(tints[:5]) if tints else "no tints between 0 and 1",
    )


#: ReportLab draws an image as "q <w> 0 0 <h> <x> <y> cm /Name Do Q". The
#: matrix carries the drawn size in points, which is the other half of the
#: resolution question: pixels alone say nothing until you know how big they
#: are printed.
_IMAGE_DRAW_RE = re.compile(
    rb"([-\d.]+)\s+0\s+0\s+([-\d.]+)\s+[-\d.]+\s+[-\d.]+\s+cm\s*/([A-Za-z0-9_.]+)\s+Do"
)


def check_raster_and_transparency(
    report: PreflightReport, reader, *, min_dpi: float = 300.0
) -> None:
    """Embedded images must be print-ready; transparency must be flattened.

    This check used to demand vector only. That was the right rule while every
    asset was drawn as flat shapes, and the wrong one as soon as a book shipped
    artwork that arrived as a picture: tracing a drawing into the subset throws
    away what the illustrator drew, and the traced result passes every rule
    about lines the drawing no longer has.

    So images are allowed and inspected instead. A monochrome press needs two
    things from one: no grey to halftone, and enough pixels for the size it is
    drawn at. The second is measured rather than assumed -- the drawn size comes
    out of the content stream, so an asset stored for a 5 mm icon is not failed
    for being too small to be a title page.
    """
    problems: list[str] = []
    groups: list[str] = []
    checked = 0
    worst = float("inf")

    for index, page in enumerate(reader.pages, start=1):
        resources = page.get("/Resources")
        images: dict[str, Any] = {}
        if resources is not None:
            xobjects = resources.get_object().get("/XObject")
            if xobjects is not None:
                for name, ref in xobjects.get_object().items():
                    obj = ref.get_object()
                    subtype = str(obj.get("/Subtype", ""))
                    if subtype == "/Image":
                        images[str(name).lstrip("/")] = obj
                        problems.extend(
                            f"page {index}: {name} {problem}"
                            for problem in _image_problems(obj)
                        )
                    elif "/Group" in obj:
                        groups.append(f"page {index}: {name}")
            if "/ExtGState" in resources.get_object():
                for name, ref in resources.get_object()["/ExtGState"].get_object().items():
                    state = ref.get_object()
                    for key in ("/CA", "/ca"):
                        if key in state and float(state[key]) < 1.0:
                            groups.append(f"page {index}: {name} {key}={state[key]}")
        if "/Group" in page:
            groups.append(f"page {index}: page group")

        if not images:
            continue
        for width_pt, height_pt, name in _IMAGE_DRAW_RE.findall(_content_bytes(page)):
            image = images.get(name.decode("latin-1"))
            if image is None:
                continue
            checked += 1
            inches = max(float(width_pt), float(height_pt)) / 72.0
            pixels = max(int(image.get("/Width", 0)), int(image.get("/Height", 0)))
            dpi = pixels / inches if inches > 0 else float("inf")
            worst = min(worst, dpi)
            if dpi < min_dpi:
                problems.append(
                    f"page {index}: /{name.decode('latin-1')} is {pixels} px drawn "
                    f"at {inches:.2f} in, which is {dpi:.0f} dpi against {min_dpi:.0f}"
                )

    report.add(
        "images-print-ready",
        not problems,
        ", ".join(problems[:5])
        if problems
        else (
            f"{checked} placement(s), bitonal, worst {worst:.0f} dpi"
            if checked
            else "vector only"
        ),
    )
    report.add(
        "transparency-flattened",
        not groups,
        ", ".join(groups[:5]) if groups else "no transparency groups or soft alpha",
    )


#: Colour spaces a monochrome interior may use for an image. Anything else puts
#: separations on a press that prints one.
_GRAY_SPACES = {"/DeviceGray", "/CalGray"}


def _image_problems(image) -> list[str]:
    """What is wrong with the image's data, independent of how big it is drawn."""
    problems: list[str] = []
    is_mask = bool(image.get("/ImageMask", False))
    space = image.get("/ColorSpace")
    if not is_mask and str(space) not in _GRAY_SPACES:
        problems.append(f"is {space}, not DeviceGray")
    if not is_mask and _has_grey_levels(image):
        problems.append("carries grey levels between black and white")
    return problems


def _has_grey_levels(image) -> bool:
    """Whether the image data holds any value that is not 0 or 255."""
    try:
        data = image.get_data()
    except Exception:  # pragma: no cover - undecodable filter
        return True
    return any(byte not in (0, 255) for byte in data[:65536])


def check_parity(report: PreflightReport, plan_obj: dict[str, Any]) -> None:
    """18.7/17.10: even pages are left, odd are right, and every story page in
    the scene range sits on an even page."""
    problems: list[str] = []
    for page in plan_obj["pages"]:
        number, side, kind = page["pageNumber"], page["side"], page["kind"]
        expected_side = "left" if number % 2 == 0 else "right"
        if side != expected_side:
            problems.append(f"page {number} is {side}, parity says {expected_side}")
        if kind == "story" and number % 2 != 0:
            problems.append(f"story page {number} is odd; 18.7 requires an even (left) page")
        if kind == "maze" and number % 2 == 0:
            problems.append(f"maze page {number} is even; it must face the story page")
    report.add(
        "story-maze-parity",
        not problems,
        "; ".join(problems[:5]) if problems else "every spread is story-left, maze-right",
    )


def check_text_markers(
    report: PreflightReport,
    pages_text: list[str],
    *,
    plan_obj: dict[str, Any],
    scene_titles: dict[int, str],
    labels: Labels = DEFAULT_LABELS,
) -> None:
    missing: list[str] = []
    for page in plan_obj["pages"]:
        number = page["pageNumber"]
        if number > len(pages_text):
            missing.append(f"page {number}: not present in extracted text")
            continue
        text = pages_text[number - 1]
        if page["kind"] == "story":
            title = scene_titles.get(page["sceneNumber"], "")
            if title and title not in text:
                missing.append(f"page {number}: story title {title!r} not found")
        elif page["kind"] == "solutions":
            if not find_best_possible(labels, text):
                missing.append(f"page {number}: no solution caption found")
    report.add(
        "text-markers",
        not missing,
        "; ".join(missing[:5]) if missing else "scene titles and solution labels present",
    )


def check_best_possible_cross_check(
    report: PreflightReport,
    pages_text: list[str],
    *,
    plan_obj: dict[str, Any],
    analyses: dict[int, MazeAnalysis],
    labels: Labels = DEFAULT_LABELS,
) -> None:
    """18.8's mandatory cross-check, read back out of the finished PDF.

    Comparing what the renderer was *given* would only prove the renderer agrees
    with itself. This reads the number a child will actually see.
    """
    problems: list[str] = []
    checked = 0
    for page in plan_obj["pages"]:
        if page["kind"] != "maze":
            continue
        number, index = page["pageNumber"], page["mazeIndex"]
        if number > len(pages_text):
            problems.append(f"maze {index}: page {number} missing from extracted text")
            continue
        found = find_best_possible(labels, pages_text[number - 1])
        if not found:
            problems.append(f"maze {index}: no 'Best possible' number on page {number}")
            continue
        printed = found[0]
        expected = analyses[index].best_candy_total
        if printed != expected:
            problems.append(
                f"maze {index}: page {number} prints {printed}, analysis says {expected}"
            )
        checked += 1

    # The answer key states the same number in the same words, so it can be read
    # back the same way. A maze page and its answer disagreeing is exactly the
    # kind of fault a child finds and an adult cannot explain.
    for page in plan_obj["pages"]:
        if page["kind"] != "solutions":
            continue
        number = page["pageNumber"]
        if number > len(pages_text):
            continue
        printed = find_best_possible(labels, pages_text[number - 1])
        expected = [analyses[i].best_candy_total for i in page["solutionIndices"]]
        if printed != expected:
            problems.append(
                f"solutions page {number} prints {printed}, answer key says {expected}"
            )
        else:
            checked += len(expected)

    report.add(
        "best-possible-cross-check",
        not problems,
        "; ".join(problems[:5]) if problems else f"{checked} printed total(s) agree with the answer key",
    )


def check_safe_area(
    report: PreflightReport,
    pdf: Path,
    *,
    page_count: int,
    prefix: Path,
    dpi: int = 72,
    threshold: int = 200,
    declared_margins: "dict[str, float] | None" = None,
) -> None:
    """18.8: no mark within 0.25 in of any trim edge, and inside what the book says.

    Two bands, because they answer different questions. The 0.25 in trim band is
    the press's rule and a failure there is a reprint. ``declared_margins`` is
    the book's own promise in ``print.safeMarginsIn``, and a page that breaks it
    still prints -- which is exactly why nothing catches it. A corner ornament
    placed 6 pt above the top margin passed every check in this file while
    riding visibly higher than the page facing it.

    Both are measured by rasterizing and looking, not by trusting the layout
    arithmetic -- the arithmetic is precisely what would be wrong if this check
    were needed.

    Rasterization goes to a temporary directory in a lossless format, because the
    measurement is a threshold against white and JPEG ringing along a hard edge
    is exactly the kind of noise that would make this check lie in both
    directions. The 17.15 JPEG previews are then written from those same pixels,
    so the durable artifact costs no second render.

    The four border bands are cropped and reduced with Pillow rather than walked
    in Python: ``getextrema`` on 49k pixels per page across 112 pages is the
    difference between a preflight that takes a second and one that takes a
    minute.
    """
    import tempfile

    from PIL import Image

    band = int(round(SAFE_AREA_IN * dpi))
    offenders: list[str] = []
    declared: list[str] = []
    rendered_count = 0

    with tempfile.TemporaryDirectory(prefix="maze-book-preflight-") as scratch:
        scratch_prefix = Path(scratch) / "page"
        result = _run(["pdftoppm", "-gray", "-r", str(dpi), str(pdf), str(scratch_prefix)])
        rendered = sorted(Path(scratch).glob("page-*.p*m"))
        if result.returncode != 0 or not rendered:
            report.add("safe-area", False, f"pdftoppm failed: {result.stderr.strip()[:200]}")
            report.add("renders-every-page", False, "pdftoppm produced no output")
            return

        rendered_count = len(rendered)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for page_number, image_path in enumerate(rendered, start=1):
            with Image.open(image_path) as image:
                gray = image.convert("L")
                width, height = gray.size
                bands = {
                    "top": (0, 0, width, band),
                    "bottom": (0, height - band, width, height),
                    "left": (0, 0, band, height),
                    "right": (width - band, 0, width, height),
                }
                for name, box in bands.items():
                    darkest = gray.crop(box).getextrema()[0]
                    if darkest < threshold:
                        offenders.append(
                            f"page {page_number}: ink in the {name} band (gray {darkest})"
                        )
                        break
                if declared_margins:
                    for name, inches in declared_margins.items():
                        edge = int(round(inches * dpi))
                        if edge <= band:
                            continue
                        box = {
                            "top": (0, 0, width, edge),
                            "bottom": (0, height - edge, width, height),
                            "left": (0, 0, edge, height),
                            "right": (width - edge, 0, width, height),
                        }[name]
                        if gray.crop(box).getextrema()[0] < threshold:
                            declared.append(
                                f"page {page_number}: ink inside the declared "
                                f"{inches:g} in {name} margin"
                            )
                            break
                gray.save(prefix.parent / f"{prefix.name}-{page_number:03d}.jpg", quality=70)

    report.add(
        "safe-area",
        not offenders,
        ", ".join(offenders[:5])
        if offenders
        else f"{rendered_count} page(s) clear of the {SAFE_AREA_IN} in trim band",
    )
    if declared_margins:
        report.add(
            "declared-margins",
            not declared,
            ", ".join(declared[:5])
            if declared
            else f"{rendered_count} page(s) inside the margins the book declares",
        )
    report.add(
        "renders-every-page",
        rendered_count == page_count,
        f"pdftoppm rendered {rendered_count} of {page_count} page(s)",
    )


def check_qpdf(report: PreflightReport, pdf: Path, *, strict: bool) -> None:
    if shutil.which("qpdf") is None:
        report.add(
            "qpdf-structure", not strict,
            "qpdf is not installed (brew install qpdf); required in production mode",
            skipped=not strict,
        )
        return
    result = _run(["qpdf", "--check", str(pdf)])
    # qpdf exits 3 for warnings that are not structural errors; only 2+ non-zero
    # with error text means the file is malformed.
    output = (result.stdout + result.stderr).strip()
    ok = result.returncode == 0
    report.add(
        "qpdf-structure", ok, "no structural problems" if ok else output[:400]
    )


def extract_pages_text(pdf: Path, out: Path) -> list[str]:
    """Per-page text via ``pdftotext``. Pages are separated by form feeds."""
    out.parent.mkdir(parents=True, exist_ok=True)
    result = _run(["pdftotext", "-layout", str(pdf), str(out)])
    if result.returncode != 0:
        raise PreflightError(
            f"pdftotext failed on {pdf.name}", details=[result.stderr.strip()[:400]]
        )
    text = out.read_text(encoding="utf-8", errors="replace")
    pages = text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages
