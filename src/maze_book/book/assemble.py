"""Page rendering and PDF assembly (PRD 17.10, 17.11, 17.14, 18.7).

Assembly never changes maze topology. It reads cached artifacts, draws each
physical page according to the plan, and merges the result with the supplied
front matter. 17.13 is explicit that ``book assemble`` "requires valid cached
maze artifacts and builds pages/PDF without changing maze topology" -- so if an
artifact is missing this fails rather than quietly generating a different maze
than the one that was proofed.

Two PDFs come out, on purpose (17.11):

* ``book-interior-editable.pdf`` keeps live text, so a proofreader can select
  it, a spellchecker can read it, and ``pdftotext`` can find the markers
  preflight looks for;
* ``book-interior.pdf`` is the production file.

For v1 both embed their fonts rather than outlining text. 17.11 defers outlining
because it makes proofreading and text preflight harder, and 18.8 restates the
check accordingly: every font embedded, not zero fonts present. The two files are
therefore the same content today; keeping them separate is what lets a future
production profile add outlining to one without touching the other's contract.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from reportlab.pdfgen.canvas import Canvas

from ..assets.catalog import AssetCatalog
from ..content.loader import (
    DEFAULT_BODY_SIZE,
    SceneText,
    page_vector_path,
    prepare_scenes,
)
from ..errors import RenderingError
from ..model.analysis import MazeAnalysis
from ..model.book_config import BookConfig
from ..model.maze_data import MazeData
from ..model.profile import Profile
from ..rendering.geometry import raise_for_placements
from ..rendering.maze_page import draw_maze_page, plan_maze_page
from ..rendering.page import PageMetrics
from ..rendering.solutions import draw_solutions_page, plan_solutions_page
from ..rendering.story_page import draw_story_page, plan_story_page
from ..rendering.svg import AssetGeometryCache, MazeRenderOptions, render_maze_svg
from ..rendering.svg_to_pdf import PdfFrame, register_fonts
from .artifacts import LoadedMaze, OutputPaths
from .page_plan import (
    KIND_BLANK,
    KIND_END,
    KIND_FRONT_MATTER,
    KIND_MAZE,
    KIND_SOLUTIONS,
    KIND_STORY,
    PagePlan,
    PageRecord,
)

def _canvas(target, page_size) -> Canvas:
    """A canvas whose *initial* font is a bundled face, not Helvetica.

    ReportLab writes ``BT /F1 12 Tf 14.4 TL ET`` into every page's preamble using
    ``initialFontName``, which defaults to Helvetica. That registers Helvetica in
    each page's resource dictionary even when nothing ever draws with it -- and a
    base-14 font is not embedded, so 18.8's "every font embedded" check fails on
    a font the book does not use. Naming a bundled face here means the only fonts
    in the file are ones we ship and embed.
    """
    return Canvas(
        target,
        pagesize=page_size,
        initialFontName="Vera",
        initialFontSize=DEFAULT_BODY_SIZE,
    )


#: Printed on the end page. Kept here rather than in a book package because it is
#: furniture, not content: a book with no end page simply does not plan one.
END_PAGE_LINES = ("THE END", "Now go and count your candy.")


@dataclass(slots=True)
class AssemblyResult:
    page_count: int
    interior: Path
    editable: Path
    page_files: list[Path] = field(default_factory=list)


class BookAssembler:
    def __init__(
        self,
        *,
        config: BookConfig,
        profile: Profile,
        catalog: AssetCatalog,
        paths: OutputPaths,
        plan: PagePlan,
        mazes: dict[int, LoadedMaze],
        fonts_dir: Path,
    ) -> None:
        self.config = config
        self.profile = profile
        self.catalog = catalog
        self.paths = paths
        self.plan = plan
        self.mazes = mazes
        self.metrics = PageMetrics.from_print_spec(config.print)
        self.cache = AssetGeometryCache()
        register_fonts(fonts_dir)
        self.scenes: dict[int, SceneText] = {
            scene.number: scene
            for scene in prepare_scenes(config, width=self.metrics.live_width("left"))
        }

    # -- one page -----------------------------------------------------------

    def draw_page(self, canvas: Canvas, record: PageRecord) -> None:
        frame = PdfFrame(canvas, self.metrics.height)
        if record.kind in (KIND_BLANK, KIND_FRONT_MATTER):
            return
        if record.kind == KIND_STORY:
            self._draw_story(frame, record)
        elif record.kind == KIND_MAZE:
            self._draw_maze(frame, record)
        elif record.kind == KIND_SOLUTIONS:
            self._draw_solutions(frame, record)
        elif record.kind == KIND_END:
            self._draw_end(frame, record)
        else:
            raise RenderingError(f"unknown page kind {record.kind!r} on page {record.page_number}")

    def _draw_story(self, frame: PdfFrame, record: PageRecord) -> None:
        scene = self.scenes[record.scene_number]
        vector = page_vector_path(self.config, scene)
        layout = plan_story_page(
            scene, metrics=self.metrics, side=record.side, has_vector=vector is not None
        )
        draw_story_page(frame, scene, layout, cache=self.cache, vector_path=vector)

    def _draw_maze(self, frame: PdfFrame, record: PageRecord) -> None:
        loaded = self.mazes[record.maze_index]
        band = self.profile.band_for(record.maze_index)
        raise_for_placements(loaded.maze, clearance_fraction=band.cell_clearance_fraction)
        layout = plan_maze_page(
            loaded.maze,
            metrics=self.metrics,
            side=record.side,
            candy_count=len(loaded.maze.collectible_cells()),
            maze_square_in=self.config.layout.maze_square_in,
            tally_position=self.config.layout.tally_position,
            show_maze_number=self.config.layout.show_maze_number,
        )
        draw_maze_page(
            frame, loaded.maze, loaded.analysis, layout,
            catalog=self.catalog, band=band, cache=self.cache,
            show_best_possible=self.config.layout.show_best_possible_score,
        )

    def _draw_solutions(self, frame: PdfFrame, record: PageRecord) -> None:
        entries = [
            (self.mazes[index].maze, self.mazes[index].analysis)
            for index in record.solution_indices
        ]
        layout = plan_solutions_page(
            metrics=self.metrics, side=record.side, count=len(entries),
            per_page=self.config.layout.solutions_per_page,
        )
        draw_solutions_page(frame, entries, layout)

    def _draw_end(self, frame: PdfFrame, record: PageRecord) -> None:
        live_x0, live_y0, live_x1, live_y1 = self.metrics.live_box(record.side)
        centre_x = (live_x0 + live_x1) / 2.0
        canvas = frame.canvas
        canvas.saveState()
        canvas.setFillGray(0.0)
        canvas.setFont("Vera-Bold", 34.0)
        canvas.drawCentredString(centre_x, frame.y(live_y0 + 230.0), END_PAGE_LINES[0])
        canvas.setFont("Vera", 16.0)
        canvas.drawCentredString(centre_x, frame.y(live_y0 + 290.0), END_PAGE_LINES[1])
        canvas.restoreState()

    # -- whole book ---------------------------------------------------------

    def render_body(self) -> bytes:
        """Every planned page except the front matter, as one in-memory PDF.

        Front-matter pages are emitted as blanks here and replaced during the
        merge, so page numbers line up one-to-one with the plan at every stage;
        renumbering after a merge is how an off-by-one reaches print.
        """
        buffer = io.BytesIO()
        canvas = _canvas(buffer, self.metrics.page_size)
        canvas.setTitle(self.config.book.title)
        canvas.setAuthor(self.config.book.id)
        canvas.setSubject(self.config.book.subtitle or "")
        canvas.setCreator("maze-book")
        for record in self.plan.pages:
            self.draw_page(canvas, record)
            canvas.showPage()
        canvas.save()
        return buffer.getvalue()

    def write_page_pdfs(self, records: Sequence[PageRecord] | None = None) -> list[Path]:
        """One single-page PDF per planned page (17.14 ``pages/page-NNN.pdf``).

        These are the inspection artifact: a reviewer who needs page 63 should not
        have to open a 112-page file to find it.
        """
        written: list[Path] = []
        self.paths.pages.mkdir(parents=True, exist_ok=True)
        for record in records or self.plan.pages:
            if record.kind == KIND_FRONT_MATTER:
                continue
            path = self.paths.page_pdf(record.page_number)
            canvas = _canvas(str(path), self.metrics.page_size)
            self.draw_page(canvas, record)
            canvas.showPage()
            canvas.save()
            written.append(path)
        return written


def merge_front_matter(
    body_pdf: bytes, front_matter: Path | None, *, front_matter_pages: int
) -> bytes:
    """Replace the leading placeholder pages with the supplied front matter."""
    from pypdf import PdfReader, PdfWriter

    body = PdfReader(io.BytesIO(body_pdf))
    writer = PdfWriter()

    if front_matter_pages:
        if front_matter is None:
            raise RenderingError(
                f"the plan reserves {front_matter_pages} front-matter page(s) but "
                f"layout.frontMatterPdf is null"
            )
        source = PdfReader(str(front_matter))
        if len(source.pages) != front_matter_pages:
            raise RenderingError(
                f"front matter has {len(source.pages)} page(s) but the plan was built "
                f"for {front_matter_pages}"
            )
        for page in source.pages:
            writer.add_page(page)

    for page in body.pages[front_matter_pages:]:
        writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def assemble(
    *,
    config: BookConfig,
    profile: Profile,
    catalog: AssetCatalog,
    paths: OutputPaths,
    plan: PagePlan,
    mazes: dict[int, LoadedMaze],
    fonts_dir: Path,
    front_matter: Path | None,
    write_pages: bool = True,
    write_editable: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> AssemblyResult:
    assembler = BookAssembler(
        config=config, profile=profile, catalog=catalog,
        paths=paths, plan=plan, mazes=mazes, fonts_dir=fonts_dir,
    )

    if on_progress:
        on_progress(f"rendering {plan.total_pages} pages")
    body = assembler.render_body()

    merged = merge_front_matter(
        body, front_matter, front_matter_pages=plan.front_matter_pages
    )

    paths.base.mkdir(parents=True, exist_ok=True)
    paths.interior.write_bytes(merged)
    if write_editable:
        paths.editable.write_bytes(merged)

    page_files: list[Path] = []
    if write_pages:
        if on_progress:
            on_progress("writing per-page PDFs")
        page_files = assembler.write_page_pdfs()

    return AssemblyResult(
        page_count=plan.total_pages,
        interior=paths.interior,
        editable=paths.editable,
        page_files=page_files,
    )


def write_maze_svg(
    path: Path,
    maze: MazeData,
    analysis: MazeAnalysis,
    *,
    catalog: AssetCatalog,
    band,
    cache: AssetGeometryCache | None = None,
    with_solution: bool = False,
) -> None:
    """The per-maze inspection SVG of 17.14 (``mazes/NNN.svg``)."""
    options = MazeRenderOptions(
        wall_width=band.wall_width_pt,
        route_width=band.solution_route_width_pt,
        margin=band.wall_width_pt * 2.0,
        scales={
            "start": band.start_scale, "finish": band.finish_scale,
            "collectible": band.collectible_scale, "dead-end": band.dead_end_scale,
        },
        route=tuple(analysis.best_route) if with_solution else None,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_maze_svg(
            maze, catalog=catalog, options=options, cell_units=72.0,
            cache=cache or AssetGeometryCache(),
        ),
        encoding="utf-8",
    )
