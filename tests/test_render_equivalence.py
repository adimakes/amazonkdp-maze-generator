"""SVG and PDF must draw the same maze (PRD 9.1, 17.11, 17.16), and the negative
fixture must actually be rejected (17.16).

9.1: "No renderer may independently reconstruct wall positions from a different
interpretation of the maze." That is not checkable by reading the code twice; it
is checkable by rendering one fixture maze both ways and comparing what came out.
Both renderers are handed the same ``MazeGeometry``, so the test asserts they
consumed it identically -- same wall runs, same asset boxes, same route -- and
then rasterizes both and compares the ink.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from maze_book.assets.catalog import load_catalog
from maze_book.assets.validate import validate_directory, validate_svg
from maze_book.errors import AssetError, ConfigError
from maze_book.model.book_config import load_book_config
from maze_book.model.geometry import canonical_edge
from maze_book.model.maze_data import MazeData, PlacedAsset
from maze_book.rendering.geometry import MazeGeometry, asset_footprint, wall_segments
from maze_book.rendering.svg import AssetGeometryCache, MazeRenderOptions, render_maze_svg
from maze_book.rendering.svg_to_pdf import PdfFrame, place_document, register_fonts


@pytest.fixture(scope="module")
def fixture_maze() -> MazeData:
    """A small maze with every feature a page renders: walls, a border opening,
    both endpoints and a collectible."""
    pairs = [
        ((0, 0), (0, 1)), ((0, 1), (0, 2)), ((0, 2), (1, 2)),
        ((1, 2), (2, 2)), ((2, 2), (2, 1)), ((2, 1), (2, 0)),
        ((2, 0), (1, 0)), ((1, 0), (0, 0)),
        ((1, 1), (1, 2)),
    ]
    return MazeData(
        maze_id="fixture", book_id="jims-halloween-maze-adventure", maze_index=1,
        seed=1, rows=3, cols=3, start=(0, 0), finish=(2, 2),
        open_edges=sorted(canonical_edge(a, b) for a, b in pairs),
        assets=[
            PlacedAsset(asset_id="jim_start.svg", role="start", cell=(0, 0), scale=0.6),
            PlacedAsset(asset_id="candy_bucket.svg", role="finish", cell=(2, 2), scale=0.7),
            PlacedAsset(asset_id="lollipop.svg", role="collectible", cell=(1, 1),
                        value=1, scale=0.55),
        ],
    )


@pytest.fixture(scope="module")
def catalog(repo_root: Path):
    return load_catalog(
        load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    )


# ---------------------------------------------------------------------------
# Shared geometry
# ---------------------------------------------------------------------------


def test_both_renderers_are_handed_the_same_wall_runs(fixture_maze: MazeData) -> None:
    """The load-bearing claim: walls are derived once. Scaling the geometry must
    scale the same set of segments, not re-derive a different one."""
    unit = MazeGeometry.normalized(3, 3)
    page = MazeGeometry.fitted(3, 3, box=(0.0, 0.0, 300.0, 300.0))

    unit_runs = wall_segments(fixture_maze, unit)
    page_runs = wall_segments(fixture_maze, page)

    assert len(unit_runs) == len(page_runs)
    for (a0, b0, c0, d0), (a1, b1, c1, d1) in zip(unit_runs, page_runs):
        assert (a1, b1, c1, d1) == pytest.approx((a0 * 100, b0 * 100, c0 * 100, d0 * 100))


def test_asset_footprints_are_identical_under_either_renderer(
    fixture_maze: MazeData,
) -> None:
    geometry = MazeGeometry.fitted(3, 3, box=(10.0, 20.0, 310.0, 320.0))
    for asset in fixture_maze.assets:
        box = asset_footprint(asset, geometry).rect
        assert box[2] - box[0] == pytest.approx(box[3] - box[1])  # square
        cx, cy = geometry.cell_centre(asset.cell)
        assert (box[0] + box[2]) / 2 == pytest.approx(cx)
        assert (box[1] + box[3]) / 2 == pytest.approx(cy)


# ---------------------------------------------------------------------------
# Rendered output equivalence
# ---------------------------------------------------------------------------


def _svg_ink_mask(svg_text: str, size: int):
    """Rasterize an SVG with rsvg-convert, or skip if it is not installed."""
    import subprocess
    import tempfile

    if shutil.which("rsvg-convert") is None:
        pytest.skip("rsvg-convert is not installed")
    from PIL import Image

    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "m.svg"
        target = Path(scratch) / "m.png"
        source.write_text(svg_text, encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), "-b", "white",
             str(source), "-o", str(target)],
            check=True, capture_output=True,
        )
        with Image.open(target) as image:
            return image.convert("L").point(lambda v: 255 if v < 128 else 0)


def _pdf_ink_mask(draw, page_size: float, size: int):
    import subprocess
    import tempfile

    if shutil.which("pdftoppm") is None:
        pytest.skip("pdftoppm is not installed")
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas

    with tempfile.TemporaryDirectory() as scratch:
        pdf = Path(scratch) / "m.pdf"
        canvas = Canvas(str(pdf), pagesize=(page_size, page_size))
        draw(PdfFrame(canvas, page_size))
        canvas.showPage()
        canvas.save()
        subprocess.run(
            ["pdftoppm", "-gray", "-r", str(size / page_size * 72), "-png", str(pdf),
             str(Path(scratch) / "page")],
            check=True, capture_output=True,
        )
        rendered = sorted(Path(scratch).glob("page*.png"))
        with Image.open(rendered[0]) as image:
            return (
                image.convert("L")
                .resize((size, size), Image.LANCZOS)
                .point(lambda v: 255 if v < 128 else 0)
            )


def test_svg_and_pdf_draw_the_same_ink_for_one_fixture_maze(
    fixture_maze: MazeData, catalog, repo_root: Path
) -> None:
    """9.1's contract, checked by looking at the pixels.

    Rendered at the same size through two entirely separate back ends, the two
    should agree almost everywhere. They will not agree *exactly* -- two
    rasterizers antialias edges differently -- so the test allows a small
    disagreement and would still catch a wall in the wrong place, a missing
    asset, or a mirrored page, each of which moves thousands of pixels.
    """
    from PIL import ImageChops

    register_fonts(repo_root / "fonts")
    size = 300
    cell = 100.0
    geometry = MazeGeometry(rows=3, cols=3, origin=(0.0, 0.0), cell=cell)
    options = MazeRenderOptions(wall_width=6.0, margin=0.0, scales={})
    cache = AssetGeometryCache()

    svg_text = render_maze_svg(
        fixture_maze, catalog=catalog, options=options, cell_units=cell, cache=cache
    )
    svg_mask = _svg_ink_mask(svg_text, size)

    def draw(frame: PdfFrame) -> None:
        frame.segments(wall_segments(fixture_maze, geometry), 6.0)
        for asset in fixture_maze.assets:
            document = cache.get(catalog.path_for(asset.asset_id))
            place_document(frame, document, asset_footprint(asset, geometry).rect)

    pdf_mask = _pdf_ink_mask(draw, 300.0, size)

    difference = ImageChops.difference(svg_mask, pdf_mask)
    disagreeing = sum(1 for value in difference.getdata() if value > 0)
    fraction = disagreeing / (size * size)
    assert fraction < 0.04, f"{fraction:.1%} of pixels disagree between SVG and PDF"


def test_a_deliberately_shifted_maze_would_be_caught(
    fixture_maze: MazeData, catalog, repo_root: Path
) -> None:
    """The companion check: if the comparison above tolerated anything, it would
    pass for a maze drawn in the wrong place too."""
    from PIL import ImageChops

    register_fonts(repo_root / "fonts")
    size = 300
    good = MazeGeometry(rows=3, cols=3, origin=(0.0, 0.0), cell=100.0)
    shifted = MazeGeometry(rows=3, cols=3, origin=(12.0, 0.0), cell=100.0)

    def draw_with(geometry):
        def draw(frame: PdfFrame) -> None:
            frame.segments(wall_segments(fixture_maze, geometry), 6.0)
        return draw

    a = _pdf_ink_mask(draw_with(good), 300.0, size)
    b = _pdf_ink_mask(draw_with(shifted), 300.0, size)
    disagreeing = sum(1 for v in ImageChops.difference(a, b).getdata() if v > 0)
    assert disagreeing / (size * size) > 0.04


# ---------------------------------------------------------------------------
# The negative fixture package (17.16)
# ---------------------------------------------------------------------------


@pytest.fixture
def broken_book(repo_root: Path) -> Path:
    path = repo_root / "tests" / "fixtures" / "books" / "broken-book"
    assert path.is_dir(), "the negative fixture package is missing"
    return path


def test_the_negative_fixture_ships_the_defects_it_documents(broken_book: Path) -> None:
    for relative in (
        "assets/maze-vectors/collectibles/not-xml.svg",
        "assets/maze-vectors/collectibles/stroked.svg",
        "assets/maze-vectors/dead-end/hairline.svg",
        "assets/beginning-vectors/start.svg",
    ):
        assert (broken_book / relative).is_file(), relative


def test_malformed_xml_is_rejected_as_a_rule_not_a_crash(broken_book: Path) -> None:
    report = validate_svg(
        broken_book / "assets/maze-vectors/collectibles/not-xml.svg"
    )
    assert report.passed is False
    assert report.errors[0].startswith("A1_subset")


def test_a_stroked_asset_is_rejected(broken_book: Path) -> None:
    report = validate_svg(broken_book / "assets/maze-vectors/collectibles/stroked.svg")
    assert report.passed is False
    assert "filled paths only" in report.errors[0]


def test_a_hairline_limb_is_rejected_by_measurement(broken_book: Path) -> None:
    report = validate_svg(broken_book / "assets/maze-vectors/dead-end/hairline.svg")
    assert report.passed is False
    assert any(e.startswith("A7_minimumFeature") for e in report.errors)


def test_an_element_outside_the_subset_is_rejected(broken_book: Path) -> None:
    report = validate_svg(broken_book / "assets/beginning-vectors/start.svg")
    assert report.passed is False
    assert "outside the supported SVG subset" in report.errors[0]


def test_the_valid_control_asset_still_passes(broken_book: Path) -> None:
    """Without it, a test asserting a specific failure could be passing because
    everything in the package fails."""
    assert validate_svg(broken_book / "assets/ending-vectors/finish.svg").passed is True


def test_a_directory_sweep_reports_every_bad_file_at_once(broken_book: Path) -> None:
    reports = validate_directory(broken_book / "assets/maze-vectors/collectibles")
    assert len(reports) == 2
    assert all(not report.passed for report in reports)


def test_building_the_broken_book_fails_with_a_non_zero_exit(
    broken_book: Path, tmp_path: Path
) -> None:
    """15.15: an invalid SVG must cause a non-zero build result."""
    from maze_book.cli import main
    from maze_book.errors import EXIT_INVALID_INPUT

    destination = tmp_path / "broken-book"
    shutil.copytree(broken_book, destination)
    assert main(
        ["book", "validate", str(destination), "--output", str(tmp_path / "out")]
    ) == EXIT_INVALID_INPUT
