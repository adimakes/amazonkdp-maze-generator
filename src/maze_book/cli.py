"""The command-line contract (PRD 17.13).

Eight subcommands over two nouns, and exit codes that are a public contract::

    0  success
    2  invalid input / configuration
    3  maze generation or semantic failure
    4  rendering or assembly failure
    5  PDF / preflight failure

Every deliberate error in this package carries its own ``exit_code``
(``errors.py``), so ``main`` translates an exception into a status by asking it
rather than by mapping stages to numbers here -- one place to change, and no way
for a stage to report the wrong class of failure.

The commands are deliberately not interchangeable. ``maze render`` and ``book
assemble`` read cached artifacts and refuse to generate; ``book generate-mazes``
generates and writes; only ``book build`` does both. 17.13 requires that split
because "render what I already checked" and "make me something new" are
different intentions, and silently doing the second when asked for the first
means the page proofed and the page printed came from different mazes.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .assets.catalog import AssetCatalog, load_catalog
from .assets.validate import raise_for_reports, validate_svg
from .book.artifacts import (
    BookFingerprint,
    LoadedMaze,
    MazeStore,
    OutputPaths,
    resolve_only,
    write_normalized_book,
)
from .book.assemble import assemble, write_maze_svg
from .book.contact_sheet import render_contact_sheet
from .book.page_plan import PagePlan, front_matter_page_count, plan_pages
from .errors import (
    EXIT_OK,
    ConfigError,
    MazeBookError,
    PreflightError,
    RenderingError,
)
from .model.book_config import BookConfig, load_book_config, repo_root
from .model.json_io import read_json
from .model.profile import Profile, load_profile
from .preflight.runner import preflight_from_outputs, run_preflight

DEFAULT_OUTPUT = "output"


# --------------------------------------------------------------------------- #
# Shared context
# --------------------------------------------------------------------------- #


class Context:
    """Everything a subcommand needs, loaded once and validated on the way in."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = repo_root()
        self.book_dir = Path(args.book).resolve()
        self.config: BookConfig = load_book_config(self.book_dir, require_assets=True)

        if getattr(args, "seed", None) is not None:
            self.config.book.seed = args.seed
        profile_id = getattr(args, "profile", None) or self.config.book.profile_id
        self.profile: Profile = load_profile(self.root / "profiles", profile_id)
        self.profile.covers(self.config.book.maze_count)

        self.catalog: AssetCatalog = load_catalog(self.config)
        output_root = Path(getattr(args, "output", None) or (self.root / DEFAULT_OUTPUT))
        self.paths = OutputPaths(output_root, self.config.book.id)
        self.fonts_dir = self.root / "fonts"

    @property
    def indices(self) -> list[int]:
        return resolve_only(getattr(self.args, "only", None), self.config.book.maze_count)

    def store(self) -> MazeStore:
        return MazeStore(
            config=self.config, profile=self.profile, catalog=self.catalog,
            paths=self.paths, fingerprint=BookFingerprint.build(
                self.config, self.profile, self.catalog
            ),
        )

    def front_matter(self) -> Path | None:
        return self.config.front_matter_path

    def plan(self) -> PagePlan:
        return plan_pages(
            front_matter_pages=front_matter_page_count(self.front_matter()),
            maze_count=self.config.book.maze_count,
            layout=self.config.layout,
        )


def _say(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# book validate
# --------------------------------------------------------------------------- #


def cmd_book_validate(context: Context) -> int:
    config, profile = context.config, context.profile
    _say(f"book        {config.book.id} -- {config.book.title}")
    _say(f"profile     {profile.profile_id} ({profile.age_label})")
    _say(f"mazes       {config.book.maze_count}, seed {config.book.seed}")

    assets = context.catalog.all_files()
    reports = [validate_svg(asset.path) for asset in assets]
    raise_for_reports(reports, label=config.book.id)
    _say(f"assets      {len(assets)} SVG(s) pass the 18.5 subset")

    from .content.loader import prepare_scenes
    from .rendering.page import PageMetrics
    from .rendering.svg_to_pdf import register_fonts

    register_fonts(context.fonts_dir)
    metrics = PageMetrics.from_print_spec(config.print)
    scenes = prepare_scenes(config, width=metrics.live_width("left"))
    _say(f"content     {len(scenes)} scene(s) fit the story page")

    plan = context.plan()
    _say(
        f"pages       {plan.total_pages} "
        f"({plan.front_matter_pages} front matter, "
        f"{len(plan.of_kind('solutions'))} solution page(s))"
    )
    _say("OK")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# maze commands
# --------------------------------------------------------------------------- #


def cmd_maze_generate(context: Context) -> int:
    store = context.store()
    indices = [context.args.index] if context.args.index else context.indices
    for index, loaded in store.ensure(indices, force=context.args.force):
        source = "cached" if loaded.from_cache else "generated"
        _say(
            f"maze {index:>3} {source:<9} {loaded.maze.rows}x{loaded.maze.cols} "
            f"k={loaded.analysis.loop_count} "
            f"candy={loaded.analysis.best_candy_total} "
            f"routes={loaded.analysis.simple_path_count}"
        )
    return EXIT_OK


def cmd_maze_validate(context: Context) -> int:
    from .errors import SemanticError

    store = context.store()
    indices = [context.args.index] if context.args.index else context.indices
    problems: list[str] = []
    for index in indices:
        loaded = store.require(index)
        analysis = loaded.analysis
        if not analysis.exact:
            problems.append(f"maze {index}: route enumeration was not exact")
        if not analysis.passed:
            problems.append(f"maze {index}: {'; '.join(analysis.errors) or 'analysis did not pass'}")
        band = context.profile.band_for(index)
        from .rendering.geometry import validate_placements

        for issue in validate_placements(
            loaded.maze, clearance_fraction=band.cell_clearance_fraction
        ):
            problems.append(f"maze {index}: {issue}")
    if problems:
        raise SemanticError(
            f"{len(problems)} semantic problem(s) across {len(indices)} maze(s)",
            details=problems,
        )
    _say(f"{len(indices)} maze(s) pass schema and semantic validation")
    return EXIT_OK


def cmd_maze_render(context: Context) -> int:
    formats = _formats(context.args.format, default=("svg",))
    store = context.store()
    indices = [context.args.index] if context.args.index else context.indices

    from .rendering.svg import AssetGeometryCache

    cache = AssetGeometryCache()
    written = 0
    for index in indices:
        loaded = store.require(index)  # 17.13: never silently regenerates
        band = context.profile.band_for(index)
        if "svg" in formats:
            path = context.paths.maze_svg(index)
            write_maze_svg(
                path, loaded.maze, loaded.analysis,
                catalog=context.catalog, band=band, cache=cache,
            )
            written += 1
        if "json" in formats:
            written += 1  # already written by generate; presence is the artifact
        if "pdf" in formats:
            _write_maze_pdf(context, loaded, band)
            written += 1
    _say(f"rendered {written} artifact(s) for {len(indices)} maze(s) into {context.paths.mazes}")
    return EXIT_OK


def _write_maze_pdf(context: Context, loaded: LoadedMaze, band) -> None:
    from reportlab.pdfgen.canvas import Canvas

    from .rendering.maze_page import draw_maze_page, plan_maze_page
    from .rendering.page import PageMetrics
    from .rendering.svg import AssetGeometryCache
    from .rendering.svg_to_pdf import PdfFrame, register_fonts

    register_fonts(context.fonts_dir)
    metrics = PageMetrics.from_print_spec(context.config.print)
    path = context.paths.maze_pdf(loaded.maze.maze_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(
        str(path), pagesize=metrics.page_size, initialFontName="Vera", initialFontSize=16.0
    )
    frame = PdfFrame(canvas, metrics.height)
    layout = plan_maze_page(
        loaded.maze, metrics=metrics, side="right",
        candy_count=len(loaded.maze.collectible_cells()),
        maze_square_in=context.config.layout.maze_square_in,
        tally_position=context.config.layout.tally_position,
        show_maze_number=context.config.layout.show_maze_number,
    )
    draw_maze_page(
        frame, loaded.maze, loaded.analysis, layout,
        catalog=context.catalog, band=band, cache=AssetGeometryCache(),
        show_best_possible=context.config.layout.show_best_possible_score,
    )
    canvas.showPage()
    canvas.save()


# --------------------------------------------------------------------------- #
# book generate-mazes / assemble / build / preflight
# --------------------------------------------------------------------------- #


def cmd_book_generate_mazes(context: Context) -> int:
    store = context.store()
    indices = context.indices
    stages: dict[str, int] = {}

    def on_rejection(record) -> None:
        stages[record.stage] = stages.get(record.stage, 0) + 1

    started = time.perf_counter()
    loaded_all: dict[int, LoadedMaze] = {}
    generated = 0
    for index, loaded in store.ensure(
        indices, force=context.args.force, on_rejection=on_rejection
    ):
        loaded_all[index] = loaded
        if not loaded.from_cache:
            generated += 1
        if index % 10 == 0 or index == indices[-1]:
            _say(f"  ... {index}/{indices[-1]}")

    elapsed = time.perf_counter() - started
    _say(
        f"{len(loaded_all)} maze(s) ready ({generated} generated, "
        f"{len(loaded_all) - generated} cached) in {elapsed:.1f}s"
    )
    if stages:
        ordered = dict(sorted(stages.items(), key=lambda kv: -kv[1]))
        _say(f"rejections by stage: {ordered}")

    if context.config.outputs.write_contact_sheet:
        entries = [(loaded_all[i].maze, loaded_all[i].analysis) for i in sorted(loaded_all)]
        render_contact_sheet(entries, context.paths.contact_sheet)
        _say(f"contact sheet -> {context.paths.contact_sheet}")

    if context.config.outputs.write_maze_svg:
        from .rendering.svg import AssetGeometryCache

        cache = AssetGeometryCache()
        for index in sorted(loaded_all):
            write_maze_svg(
                context.paths.maze_svg(index), loaded_all[index].maze,
                loaded_all[index].analysis, catalog=context.catalog,
                band=context.profile.band_for(index), cache=cache,
            )
        _say(f"{len(loaded_all)} maze SVG(s) -> {context.paths.mazes}")

    return EXIT_OK


def cmd_book_assemble(context: Context) -> int:
    store = context.store()
    plan = context.plan()
    mazes = {index: store.require(index) for index in range(1, context.config.book.maze_count + 1)}

    write_normalized_book(
        context.paths, config=context.config, profile=context.profile,
        fingerprint=store.fingerprint, front_matter_pages=plan.front_matter_pages,
    )
    plan.write(context.paths.page_plan)
    _say(f"page plan   {plan.total_pages} pages -> {context.paths.page_plan}")

    result = assemble(
        config=context.config, profile=context.profile, catalog=context.catalog,
        paths=context.paths, plan=plan, mazes=mazes, fonts_dir=context.fonts_dir,
        front_matter=context.front_matter(),
        write_pages=True,
        write_editable=context.config.outputs.write_editable_proof_pdf
        and context.args.editable,
        on_progress=lambda message: _say(f"  {message}"),
    )
    _say(f"interior    {result.interior}")
    if context.args.editable and context.config.outputs.write_editable_proof_pdf:
        _say(f"editable    {result.editable}")
    return EXIT_OK


def cmd_book_preflight(context: Context) -> int:
    report = preflight_from_outputs(
        config=context.config, paths=context.paths, strict=not context.args.lenient
    )
    report.write(context.paths.preflight)
    for result in report.results:
        mark = "skip" if result.skipped else ("ok  " if result.passed else "FAIL")
        _say(f"  [{mark}] {result.name}: {result.detail}")
    _say(report.summary())
    report.raise_for_status()
    _say(f"preflight report -> {context.paths.preflight}")
    return EXIT_OK


def cmd_book_build(context: Context) -> int:
    _say("== validate ==")
    cmd_book_validate(context)
    _say("\n== generate mazes ==")
    cmd_book_generate_mazes(context)
    _say("\n== assemble ==")
    cmd_book_assemble(context)
    _say("\n== preflight ==")
    cmd_book_preflight(context)
    _say(f"\n{context.config.book.id} built: {context.paths.interior}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def _formats(value: str | None, *, default: tuple[str, ...]) -> set[str]:
    if not value:
        return set(default)
    wanted = {part.strip().lower() for part in value.split(",") if part.strip()}
    unknown = wanted - {"svg", "pdf", "json"}
    if unknown:
        raise ConfigError(
            f"--format has unknown value(s) {sorted(unknown)}; expected svg, pdf or json"
        )
    return wanted


def _add_common(parser: argparse.ArgumentParser, *, index: bool = False) -> None:
    parser.add_argument("book", nargs="?", help="path to books/<book-id>")
    parser.add_argument("--book", dest="book_flag", help="path to books/<book-id>")
    parser.add_argument("--seed", type=int, help="override book.seed")
    parser.add_argument("--profile", help="override book.profileId")
    parser.add_argument("--output", help=f"output root (default {DEFAULT_OUTPUT}/)")
    parser.add_argument("--only", help="restrict to a 1-based maze range, e.g. 1:5")
    parser.add_argument("--force", action="store_true", help="bypass the artifact cache")
    if index:
        parser.add_argument("--index", type=int, help="a single 1-based maze index")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maze-book", description="Generate KDP-ready puzzle-book interiors."
    )
    top = parser.add_subparsers(dest="noun", required=True)

    book = top.add_parser("book", help="whole-book commands").add_subparsers(
        dest="verb", required=True
    )

    validate = book.add_parser("validate", help="validate config, assets, content and parity")
    _add_common(validate)
    validate.set_defaults(handler=cmd_book_validate)

    generate = book.add_parser("generate-mazes", help="generate or load every maze artifact")
    _add_common(generate)
    generate.set_defaults(handler=cmd_book_generate_mazes)

    assemble_cmd = book.add_parser("assemble", help="build pages and PDFs from cached mazes")
    _add_common(assemble_cmd)
    assemble_cmd.add_argument(
        "--editable", dest="editable", action="store_true", default=True,
        help="also write book-interior-editable.pdf (default)",
    )
    assemble_cmd.add_argument(
        "--no-editable", dest="editable", action="store_false",
        help="skip the editable proof PDF",
    )
    assemble_cmd.set_defaults(handler=cmd_book_assemble)

    build = book.add_parser("build", help="validate, generate, assemble and preflight")
    _add_common(build)
    build.add_argument("--editable", dest="editable", action="store_true", default=True)
    build.add_argument("--no-editable", dest="editable", action="store_false")
    build.add_argument(
        "--lenient", action="store_true",
        help="local diagnostic mode: report missing external tools instead of failing",
    )
    build.set_defaults(handler=cmd_book_build)

    preflight = book.add_parser("preflight", help="check existing outputs, changing nothing")
    _add_common(preflight)
    preflight.add_argument(
        "--lenient", action="store_true",
        help="local diagnostic mode: report missing external tools instead of failing",
    )
    preflight.set_defaults(handler=cmd_book_preflight)

    maze = top.add_parser("maze", help="single-maze commands").add_subparsers(
        dest="verb", required=True
    )

    maze_generate = maze.add_parser("generate", help="generate one maze and its analysis")
    _add_common(maze_generate, index=True)
    maze_generate.set_defaults(handler=cmd_maze_generate)

    maze_validate = maze.add_parser("validate", help="schema and semantic validation")
    _add_common(maze_validate, index=True)
    maze_validate.set_defaults(handler=cmd_maze_validate)

    maze_render = maze.add_parser("render", help="render cached maze data")
    _add_common(maze_render, index=True)
    maze_render.add_argument("--format", help="svg,pdf,json (default svg)")
    maze_render.set_defaults(handler=cmd_maze_render)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    book = args.book or args.book_flag
    if not book:
        parser.error("a book package path is required (positionally or via --book)")
    args.book = book

    try:
        context = Context(args)
        return int(args.handler(context))
    except MazeBookError as exc:
        _report(exc)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _report(exc: MazeBookError) -> None:
    print(f"error: {exc.message}", file=sys.stderr)
    details = exc.details
    if isinstance(details, (list, tuple)):
        for line in list(details)[:40]:
            print(f"  - {line}", file=sys.stderr)
        if len(details) > 40:
            print(f"  ... and {len(details) - 40} more", file=sys.stderr)
    elif isinstance(details, dict):
        for key, value in details.items():
            print(f"  {key}: {value}", file=sys.stderr)
    elif details:
        print(f"  {details}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
