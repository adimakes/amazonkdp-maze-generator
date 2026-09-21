"""Tests for the page renderers and the SVG/PDF output paths (PRD 9, 17.11, 18.6).

Layout is planned as *data* before anything is drawn, which is what lets these
tests assert a box to the point instead of rendering a PDF and squinting at it.
The 18.6 measurements are checked as measurements; the drawing code is exercised
end to end and then read back out of the PDF, because the only honest proof that
text reached the page is extracting it again.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from maze_book.assets.svg_subset import parse_svg
from maze_book.content.loader import SceneText
from maze_book.errors import RenderingError
from maze_book.model.geometry import canonical_edge
from maze_book.model.maze_data import MazeData, PlacedAsset
from maze_book.rendering import solutions as sol
from maze_book.rendering.maze_page import (
    MAZE_TOP_OFFSET_IN,
    TALLY_HEIGHT_IN,
    TICK_BOX_IN,
    plan_maze_page,
)
from maze_book.rendering.page import PT_PER_IN, PageMetrics, side_for_page
from maze_book.rendering.story_page import (
    NUMBER_TOP_IN,
    VECTOR_SIZE_IN,
    plan_story_page,
)
from maze_book.rendering.svg import (
    AssetGeometryCache,
    MazeRenderOptions,
    dots_element,
    render_maze_svg,
    subpath_to_d,
    walls_element,
)


def metrics() -> PageMetrics:
    from maze_book.model.book_config import SafeMargins, PrintSpec

    return PageMetrics.from_print_spec(
        PrintSpec(
            trim_width_in=8.5, trim_height_in=11.0, interior_bleed=False,
            color_mode="black-and-white", paper="white",
            safe_margins_in=SafeMargins(inside=0.625, outside=0.5, top=0.5, bottom=0.5),
            gutter_in=0.625,
        )
    )


def small_maze(rows: int = 6, cols: int = 6, candies: int = 5) -> MazeData:
    edges = []
    for row in range(rows):
        for col in range(cols):
            if col + 1 < cols:
                edges.append(canonical_edge((row, col), (row, col + 1)))
            if row + 1 < rows:
                edges.append(canonical_edge((row, col), (row + 1, col)))
    assets = [
        PlacedAsset(asset_id="start.svg", role="start", cell=(0, 0), scale=0.6),
        PlacedAsset(asset_id="finish.svg", role="finish", cell=(rows - 1, cols - 1), scale=0.7),
    ]
    for index in range(candies):
        assets.append(
            PlacedAsset(
                asset_id="candy.svg", role="collectible",
                cell=(1 + index // cols, index % cols), value=1, scale=0.55,
            )
        )
    return MazeData(
        maze_id="m", book_id="b", maze_index=7, seed=1, rows=rows, cols=cols,
        start=(0, 0), finish=(rows - 1, cols - 1), open_edges=edges, assets=assets,
    )


# ---------------------------------------------------------------------------
# PageMetrics
# ---------------------------------------------------------------------------


def test_even_pages_are_left_and_odd_pages_are_right() -> None:
    assert [side_for_page(n) for n in (1, 2, 3, 6, 7)] == [
        "right", "left", "right", "left", "right"
    ]


def test_the_inside_margin_swaps_edges_with_the_page_side() -> None:
    """The binding is on the left of a recto and the right of a verso; deriving
    it from the page number is what stops one page coming out mirrored."""
    m = metrics()
    assert m.left_margin("right") == pytest.approx(0.625 * PT_PER_IN)
    assert m.left_margin("left") == pytest.approx(0.5 * PT_PER_IN)
    assert m.right_margin("right") == pytest.approx(0.5 * PT_PER_IN)


def test_the_live_box_is_the_trim_less_its_margins() -> None:
    m = metrics()
    assert m.live_box("right") == pytest.approx((45.0, 36.0, 576.0, 756.0))
    assert m.live_width("right") == pytest.approx(531.0)
    assert m.live_height() == pytest.approx(720.0)


def test_the_gutter_is_not_added_to_the_inside_margin() -> None:
    """model/book_config.py computes the usable area as trim - inside - outside.
    A renderer using a different usable area than the validator checks is the
    two-sites-disagree bug this codebase keeps designing out."""
    m = metrics()
    assert m.live_width("right") + m.inside + m.outside == pytest.approx(m.width)


def test_the_outside_edge_follows_the_side() -> None:
    m = metrics()
    # On a verso the binding is on the right, so the *outside* is the left edge
    # and carries the 0.5 in outside margin, not the 0.625 in inside one.
    assert m.outside_x("right") == pytest.approx(576.0)
    assert m.outside_x("left") == pytest.approx(36.0)


# ---------------------------------------------------------------------------
# 18.6 maze page measurements
# ---------------------------------------------------------------------------


def test_the_maze_square_is_centred_and_sized_as_18_6_states() -> None:
    m = metrics()
    layout = plan_maze_page(
        small_maze(), metrics=m, side="right", candy_count=5, maze_square_in=6.75
    )
    x0, y0, x1, y1 = layout.maze_box
    assert x1 - x0 == pytest.approx(6.75 * PT_PER_IN)
    assert y1 - y0 == pytest.approx(6.75 * PT_PER_IN)
    live_x0, live_y0, live_x1, _ = m.live_box("right")
    assert (x0 + x1) / 2.0 == pytest.approx((live_x0 + live_x1) / 2.0)
    assert y0 == pytest.approx(live_y0 + MAZE_TOP_OFFSET_IN * PT_PER_IN)


def test_the_tally_strip_sits_directly_below_the_maze_at_the_stated_size() -> None:
    layout = plan_maze_page(
        small_maze(), metrics=metrics(), side="right", candy_count=5, maze_square_in=6.75
    )
    assert layout.tally_box[1] == pytest.approx(layout.maze_box[3])
    assert layout.tally_box[3] - layout.tally_box[1] == pytest.approx(
        TALLY_HEIGHT_IN * PT_PER_IN
    )
    assert layout.tally_box[0] == pytest.approx(layout.maze_box[0])
    assert layout.tally_box[2] == pytest.approx(layout.maze_box[2])


@pytest.mark.parametrize("candies", [1, 5, 11, 18])
def test_one_tick_box_per_candy_at_the_stated_size(candies: int) -> None:
    layout = plan_maze_page(
        small_maze(), metrics=metrics(), side="right",
        candy_count=candies, maze_square_in=6.75,
    )
    assert len(layout.tick_boxes) == candies
    for box in layout.tick_boxes:
        assert box[2] - box[0] == pytest.approx(TICK_BOX_IN * PT_PER_IN)
        assert box[3] - box[1] == pytest.approx(TICK_BOX_IN * PT_PER_IN)


def test_tick_boxes_wrap_rather_than_shrink_when_there_are_many() -> None:
    """Eighteen boxes squeezed onto one row would each be smaller than a child's
    pencil tick."""
    layout = plan_maze_page(
        small_maze(), metrics=metrics(), side="right", candy_count=18, maze_square_in=6.75
    )
    rows = {round(box[1], 3) for box in layout.tick_boxes}
    assert len(rows) >= 2


def test_every_tick_box_stays_inside_the_tally_strip() -> None:
    for candies in (5, 12, 18):
        layout = plan_maze_page(
            small_maze(), metrics=metrics(), side="right",
            candy_count=candies, maze_square_in=6.75,
        )
        tx0, ty0, tx1, ty1 = layout.tally_box
        for box in layout.tick_boxes:
            assert tx0 - 1e-6 <= box[0] and box[2] <= tx1 + 1e-6, candies
            assert ty0 - 1e-6 <= box[1] and box[3] <= ty1 + 1e-6, candies


def test_all_page_furniture_stays_inside_the_live_area() -> None:
    m = metrics()
    live = m.live_box("right")
    layout = plan_maze_page(
        small_maze(), metrics=m, side="right", candy_count=18, maze_square_in=6.75
    )
    box = layout.bounding_box()
    assert box[0] >= live[0] - 1e-6 and box[1] >= live[1] - 1e-6
    assert box[2] <= live[2] + 1e-6 and box[3] <= live[3] + 1e-6


def test_the_maze_number_sits_in_the_outside_bottom_corner() -> None:
    m = metrics()
    right = plan_maze_page(
        small_maze(), metrics=m, side="right", candy_count=5, maze_square_in=6.75
    )
    left = plan_maze_page(
        small_maze(), metrics=m, side="left", candy_count=5, maze_square_in=6.75
    )
    assert right.number_origin[0] == pytest.approx(m.outside_x("right"))
    assert left.number_origin[0] == pytest.approx(m.outside_x("left"))
    assert right.number_origin[1] == pytest.approx(m.live_box("right")[3])


def test_the_maze_number_can_be_switched_off() -> None:
    layout = plan_maze_page(
        small_maze(), metrics=metrics(), side="right", candy_count=5,
        maze_square_in=6.75, show_maze_number=False,
    )
    assert layout.number_origin is None


def test_the_right_hand_tally_shrinks_the_maze_as_18_6_requires() -> None:
    below = plan_maze_page(
        small_maze(), metrics=metrics(), side="right", candy_count=9,
        maze_square_in=6.75, tally_position="below",
    )
    right = plan_maze_page(
        small_maze(), metrics=metrics(), side="right", candy_count=9,
        maze_square_in=6.75, tally_position="right",
    )
    assert right.maze_box[2] - right.maze_box[0] == pytest.approx(5.9 * PT_PER_IN)
    assert below.maze_box[2] - below.maze_box[0] > right.maze_box[2] - right.maze_box[0]


def test_an_unknown_tally_position_is_rejected() -> None:
    with pytest.raises(RenderingError, match=r"not 'below' or 'right'"):
        plan_maze_page(
            small_maze(), metrics=metrics(), side="right", candy_count=5,
            maze_square_in=6.75, tally_position="diagonal",
        )


def test_an_oversized_maze_square_is_rejected_rather_than_overflowing() -> None:
    with pytest.raises(RenderingError, match=r"taller than the live area"):
        plan_maze_page(
            small_maze(), metrics=metrics(), side="right", candy_count=5, maze_square_in=9.0
        )


def test_the_maze_geometry_matches_the_planned_box() -> None:
    layout = plan_maze_page(
        small_maze(8, 8), metrics=metrics(), side="right", candy_count=5, maze_square_in=6.75
    )
    assert layout.geometry.cell == pytest.approx(6.75 * PT_PER_IN / 8)
    assert layout.geometry.bounds == pytest.approx(layout.maze_box)


# ---------------------------------------------------------------------------
# 18.6 story page measurements
# ---------------------------------------------------------------------------


def scene_text(lines: int = 2) -> SceneText:
    return SceneText(
        number=12, title="THE ATTIC HUNT",
        body_lines=tuple(f"line {i}" for i in range(lines)),
        page_vector="jim_head.svg",
    )


def test_the_scene_number_sits_3_2_inches_from_the_top() -> None:
    layout = plan_story_page(scene_text(), metrics=metrics(), side="left", has_vector=True)
    assert layout.number_baseline == pytest.approx(NUMBER_TOP_IN * PT_PER_IN + 28.0)


def test_story_elements_stack_downward_in_18_6_order() -> None:
    layout = plan_story_page(scene_text(), metrics=metrics(), side="left", has_vector=True)
    assert layout.number_baseline < layout.title_baseline < layout.body_baselines[0]
    assert layout.body_baselines[0] < layout.body_baselines[1]
    assert layout.vector_box[1] > layout.body_baselines[-1]


def test_body_leading_is_one_and_a_half_times_the_size() -> None:
    layout = plan_story_page(scene_text(), metrics=metrics(), side="left", has_vector=False)
    gap = layout.body_baselines[1] - layout.body_baselines[0]
    assert gap == pytest.approx(16.0 * 1.5)


def test_everything_is_centred_on_the_live_area() -> None:
    m = metrics()
    layout = plan_story_page(scene_text(), metrics=m, side="left", has_vector=True)
    live_x0, _, live_x1, _ = m.live_box("left")
    assert layout.centre_x == pytest.approx((live_x0 + live_x1) / 2.0)
    assert (layout.vector_box[0] + layout.vector_box[2]) / 2.0 == pytest.approx(layout.centre_x)


def test_the_decorative_vector_is_square_and_big_enough_to_read() -> None:
    """0.6 in is 15 mm, at which the costume drawing's sheet folds collapse into
    noise -- and it was the only picture on four inches of otherwise blank page."""
    layout = plan_story_page(scene_text(), metrics=metrics(), side="left", has_vector=True)
    width = layout.vector_box[2] - layout.vector_box[0]
    assert width == pytest.approx(VECTOR_SIZE_IN * PT_PER_IN)
    assert layout.vector_box[3] - layout.vector_box[1] == pytest.approx(width)
    assert width >= 1.0 * PT_PER_IN


def test_a_book_with_no_page_vectors_plans_no_vector_box() -> None:
    """17.2: the repository must support a book with no page-vectors folder."""
    layout = plan_story_page(scene_text(), metrics=metrics(), side="left", has_vector=False)
    assert layout.vector_box is None


def test_the_story_page_leaves_most_of_the_page_empty() -> None:
    """18.6: "the page is deliberately mostly empty". The emptiness is the
    specification, so it is asserted rather than left to drift."""
    m = metrics()
    layout = plan_story_page(scene_text(), metrics=m, side="left", has_vector=True)
    top = layout.number_baseline - 28.0          # the number's cap height
    bottom = max(layout.vector_box[3], layout.body_baselines[-1])
    # The content block spans about a third of the page, with clear air above
    # and below it. Measuring the block rather than where it ends keeps the
    # assertion about the design instead of about one arbitrary offset.
    assert (bottom - top) < m.height * 0.40
    assert top > m.height * 0.25
    assert bottom < m.height * 0.70


def test_the_corner_ornament_goes_to_the_outside_edge() -> None:
    m = metrics()
    left = plan_story_page(
        scene_text(), metrics=m, side="left", has_vector=False, has_ornament=True
    )
    right = plan_story_page(
        scene_text(), metrics=m, side="right", has_vector=False, has_ornament=True
    )
    assert left.ornament_box[0] == pytest.approx(m.live_box("left")[0])
    assert right.ornament_box[2] == pytest.approx(m.live_box("right")[2])


# ---------------------------------------------------------------------------
# 18.6 solution pages
# ---------------------------------------------------------------------------


def test_nine_thumbnails_per_page_at_2_1_inches() -> None:
    layout = sol.plan_solutions_page(metrics=metrics(), side="right", count=9)
    assert len(layout.slots) == 9
    for slot in layout.slots:
        assert slot.box[2] - slot.box[0] == pytest.approx(sol.THUMBNAIL_IN * PT_PER_IN)
        assert slot.box[3] - slot.box[1] == pytest.approx(sol.THUMBNAIL_IN * PT_PER_IN)


def test_thumbnails_form_three_columns() -> None:
    layout = sol.plan_solutions_page(metrics=metrics(), side="right", count=9)
    columns = sorted({round(slot.box[0], 3) for slot in layout.slots})
    assert len(columns) == 3


def test_a_short_final_page_keeps_the_grid_columns() -> None:
    """Centring the remainder would make the last page read as a different
    table from the five before it."""
    full = sol.plan_solutions_page(metrics=metrics(), side="right", count=9)
    short = sol.plan_solutions_page(metrics=metrics(), side="right", count=5)
    assert len(short.slots) == 5
    assert [round(s.box[0], 3) for s in short.slots[:3]] == [
        round(s.box[0], 3) for s in full.slots[:3]
    ]


def test_every_thumbnail_stays_inside_the_live_area() -> None:
    m = metrics()
    live = m.live_box("right")
    layout = sol.plan_solutions_page(metrics=m, side="right", count=9)
    for slot in layout.slots:
        assert slot.box[0] >= live[0] - 1e-6 and slot.box[2] <= live[2] + 1e-6
        assert slot.box[1] >= live[1] - 1e-6
        assert slot.caption_centre[1] <= live[3] + 1e-6


def test_the_caption_reads_as_18_6_specifies() -> None:
    from maze_book.model.analysis import MazeAnalysis

    analysis = MazeAnalysis(
        maze_id="m", maze_index=7, exact=True, reachable=10, reachable_fraction=1.0,
        loop_count=3, simple_path_count=8, dead_end_cells=[], shortest_route_length=5,
        best_route_length=7, best_candy_total=6, second_best_candy_total=4,
        unique_highest_candy=True, best_route=[],
    )
    # Same words as the maze page, so a child comparing the two is comparing
    # the same thing -- and so preflight can read both back with one pattern.
    assert sol.caption_for(7, analysis) == "7. Best possible: 6 candies"
    analysis.best_candy_total = 1
    assert sol.caption_for(7, analysis) == "7. Best possible: 1 candy"


def test_fifty_mazes_need_six_solution_pages() -> None:
    assert sol.solution_page_count(50) == 6
    assert sol.solution_page_count(9) == 1
    assert sol.solution_page_count(10) == 2
    assert sol.solution_page_count(0) == 0


# ---------------------------------------------------------------------------
# SVG output
# ---------------------------------------------------------------------------


def test_wall_segments_become_one_path_element() -> None:
    """One element with many M/L pairs: the stroke settings are identical for
    every wall, and repeating them per segment is the bulk of a naive maze SVG."""
    element = walls_element([(0.0, 0.0, 10.0, 0.0), (0.0, 0.0, 0.0, 10.0)], 3.0)
    assert element.count("<path") == 1
    assert element.count("M") == 2
    assert 'stroke-width="3"' in element


def test_dots_are_emitted_as_cubic_circles_not_circle_elements() -> None:
    """<circle> is outside the subset the converter shares with the validator."""
    element = dots_element([(5.0, 5.0)], 2.0)
    assert "<circle" not in element and element.count("C") == 4


def test_subpath_to_d_places_and_scales_geometry() -> None:
    document = parse_svg(
        "<memory>",
        text='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path d="M0 0 L100 0 L100 100 Z"/></svg>',
    )
    data = subpath_to_d(document.paths[0].subpath, scale=0.5, offset=(10.0, 20.0))
    assert data.startswith("M10 20")
    assert data.endswith("Z")


def test_a_standalone_maze_svg_has_a_viewbox_and_no_fixed_size(tmp_path: Path, repo_root: Path) -> None:
    from maze_book.assets.catalog import load_catalog
    from maze_book.model.book_config import load_book_config

    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    catalog = load_catalog(config)
    maze = small_maze(6, 6, candies=3)
    for asset in maze.assets:
        asset_id = {
            "start": "jim_start.svg", "finish": "candy_bucket.svg",
            "collectible": "lollipop.svg",
        }[asset.role]
        object.__setattr__(asset, "asset_id", asset_id)

    svg = render_maze_svg(
        maze, catalog=catalog,
        options=MazeRenderOptions(wall_width=3.0, margin=10.0, scales={}),
        cell_units=72.0, cache=AssetGeometryCache(),
    )
    header = svg.split(">", 1)[0]
    assert header.startswith("<svg")
    # No width/height on the root element: the file scales to whatever it is
    # placed in. Checked on the header alone, since "stroke-width" contains
    # "width=" and would make a whole-document substring test always fail.
    assert "viewBox=" in header
    assert " width=" not in header and " height=" not in header
    assert svg.count("<path") >= 4  # walls plus each placed asset
    assert "<circle" not in svg and "<rect" not in svg


def test_rendering_the_same_maze_twice_is_byte_identical(repo_root: Path) -> None:
    """Determinism is a contract, and a renderer that reorders its own output
    breaks the artifact cache as surely as a reseeded generator would."""
    from maze_book.assets.catalog import load_catalog
    from maze_book.model.book_config import load_book_config

    config = load_book_config(repo_root / "books" / "jims-halloween-maze-adventure")
    catalog = load_catalog(config)
    maze = small_maze(5, 5, candies=2)
    for asset in maze.assets:
        object.__setattr__(
            asset, "asset_id",
            {"start": "jim_start.svg", "finish": "candy_bucket.svg",
             "collectible": "lollipop.svg"}[asset.role],
        )
    options = MazeRenderOptions(wall_width=3.0, margin=8.0, scales={})
    first = render_maze_svg(maze, catalog=catalog, options=options)
    second = render_maze_svg(maze, catalog=catalog, options=options)
    assert first == second


def test_the_asset_cache_parses_each_file_once(repo_root: Path) -> None:
    cache = AssetGeometryCache()
    path = repo_root / "books/jims-halloween-maze-adventure/assets/page-vectors/deco_ghost.svg"
    assert cache.get(path) is cache.get(path)


def test_a_non_square_asset_viewbox_is_rejected(tmp_path: Path) -> None:
    """The subset fixes a square viewBox so the renderer can scale by cell size;
    a non-square one would scale the axes differently and distort the art."""
    from maze_book.rendering.svg_to_pdf import PdfFrame, place_document
    from reportlab.pdfgen.canvas import Canvas

    document = parse_svg(
        "<memory>",
        text='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
        '<path d="M10 10 L90 10 L90 40 Z"/></svg>',
    )
    frame = PdfFrame(Canvas(str(tmp_path / "x.pdf")), 792.0)
    with pytest.raises(RenderingError, match=r"viewBox must be square"):
        place_document(frame, document, (0.0, 0.0, 50.0, 50.0))


def test_a_non_square_placement_box_is_rejected(tmp_path: Path) -> None:
    from maze_book.rendering.svg_to_pdf import PdfFrame, place_document
    from reportlab.pdfgen.canvas import Canvas

    document = parse_svg(
        "<memory>",
        text='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path d="M10 10 L90 10 L90 90 Z"/></svg>',
    )
    frame = PdfFrame(Canvas(str(tmp_path / "x.pdf")), 792.0)
    with pytest.raises(RenderingError, match=r"placement box must be square"):
        place_document(frame, document, (0.0, 0.0, 50.0, 30.0))


# ---------------------------------------------------------------------------
# The y-flip
# ---------------------------------------------------------------------------


def test_the_pdf_frame_flips_y_exactly_once(tmp_path: Path) -> None:
    """Maze geometry is y-down from the top-left; PDF is y-up from the
    bottom-left. Scattering that arithmetic is how one element ends up mirrored
    about the page centre while everything around it is right."""
    from maze_book.rendering.svg_to_pdf import PdfFrame
    from reportlab.pdfgen.canvas import Canvas

    frame = PdfFrame(Canvas(str(tmp_path / "x.pdf")), 792.0)
    assert frame.y(0.0) == 792.0
    assert frame.y(792.0) == 0.0
    assert frame.point((100.0, 36.0)) == (100.0, 756.0)


def test_fonts_must_be_present_rather_than_falling_back(tmp_path: Path) -> None:
    """17.11: raise rather than fall back, so a stripped checkout fails loudly
    instead of shipping Helvetica."""
    from maze_book.rendering.svg_to_pdf import register_fonts

    with pytest.raises(RenderingError, match=r"bundled font file\(s\) missing"):
        register_fonts(tmp_path)


def test_thumbnail_widths_default_to_18_6_and_are_overridable() -> None:
    """18.6 gives the thumbnail its own weights and requires them to be defaults
    that config can override. They are deliberately not the profile's
    solutionWallWidthPt / solutionRouteWidthPt, which size a *full-size* solution
    where the maze is 6.75 in rather than 2.1 in: at thumbnail scale the
    profile's 1.2-1.4 pt wall against its 1.8-1.9 pt route leaves the answer line
    barely heavier than the maze it runs through.
    """
    import inspect

    assert (sol.WALL_WIDTH_PT, sol.ROUTE_WIDTH_PT) == (0.75, 1.5)
    for function in (sol.draw_solution_thumbnail, sol.draw_solutions_page):
        parameters = inspect.signature(function).parameters
        assert parameters["wall_width"].default == sol.WALL_WIDTH_PT
        assert parameters["route_width"].default == sol.ROUTE_WIDTH_PT


def test_the_profile_solution_widths_are_not_dead_config(repo_root: Path) -> None:
    """Every profile field should reach something. solutionWallWidthPt reached
    nothing at all until the full-size solution renderer used it."""
    source = (repo_root / "src" / "maze_book" / "book" / "assemble.py").read_text(
        encoding="utf-8"
    )
    assert "band.solution_wall_width_pt" in source
    assert "band.solution_route_width_pt" in source
