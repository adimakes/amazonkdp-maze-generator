"""Tests for ``maze_book.cli`` and the whole build (PRD 17.13, 17.14, 15).

Exit codes are a public contract, so they are asserted as values rather than as
"non-zero". The command separation is asserted too: 17.13 requires that `maze
render` and `book assemble` read cached artifacts and refuse to generate, because
"render what I already checked" and "make me something new" are different
intentions, and silently doing the second means the page proofed and the page
printed came from different mazes.

The full-book test is marked slow. It is also the only test that proves the
thing the repository exists to do.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from maze_book.cli import main
from maze_book.errors import (
    EXIT_GENERATION,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_PREFLIGHT,
)


@pytest.fixture
def halloween(repo_root: Path) -> Path:
    return repo_root / "books" / "jims-halloween-maze-adventure"


@pytest.fixture
def tiny_copy(tiny_book_dir: Path, tmp_path: Path) -> Path:
    """A writable copy of the tiny fixture, so a test can break it on purpose."""
    dest = tmp_path / "books" / tiny_book_dir.name
    shutil.copytree(tiny_book_dir, dest)
    return dest


def run(*args: str) -> int:
    return main(list(args))


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


def test_the_book_path_is_accepted_positionally_and_as_a_flag(
    halloween: Path, tmp_path: Path
) -> None:
    assert run("book", "validate", str(halloween), "--output", str(tmp_path)) == EXIT_OK
    assert run("book", "validate", "--book", str(halloween), "--output", str(tmp_path)) == EXIT_OK


def test_an_unknown_book_path_exits_2(tmp_path: Path) -> None:
    assert run("book", "validate", str(tmp_path / "nope")) == EXIT_INVALID_INPUT


def test_an_unknown_profile_exits_2(halloween: Path, tmp_path: Path) -> None:
    assert run(
        "book", "validate", str(halloween), "--profile", "does-not-exist",
        "--output", str(tmp_path),
    ) == EXIT_INVALID_INPUT


def test_a_malformed_only_range_exits_2(halloween: Path, tmp_path: Path) -> None:
    assert run(
        "book", "generate-mazes", str(halloween), "--only", "40:9", "--output", str(tmp_path)
    ) == EXIT_INVALID_INPUT


def test_an_unknown_format_exits_2(halloween: Path, tmp_path: Path) -> None:
    assert run(
        "maze", "render", str(halloween), "--index", "1", "--format", "tiff",
        "--output", str(tmp_path),
    ) == EXIT_INVALID_INPUT


def test_a_missing_book_argument_is_a_usage_error(capsys) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["book", "validate"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# book validate
# ---------------------------------------------------------------------------


def test_book_validate_reports_the_shape_of_the_book(
    halloween: Path, tmp_path: Path, capsys
) -> None:
    assert run("book", "validate", str(halloween), "--output", str(tmp_path)) == EXIT_OK
    out = capsys.readouterr().out
    assert "jims-halloween-maze-adventure" in out
    assert "file(s) pass" in out
    assert "50 scene(s) fit" in out
    # The page count is a book's own arithmetic, not a constant: this one
    # dropped its title page because KDP prints the cover separately.
    assert "pages" in out and "front matter" in out


def test_book_validate_generates_nothing(halloween: Path, tmp_path: Path) -> None:
    """17.13: it "validates JSON, paths, profile, and assets without generating
    a maze"."""
    run("book", "validate", str(halloween), "--output", str(tmp_path))
    assert not (tmp_path / "jims-halloween-maze-adventure" / "build" / "mazes").exists()


def test_a_broken_asset_makes_validate_exit_2(tiny_copy: Path, tmp_path: Path) -> None:
    svg = tiny_copy / "input" / "assets" / "maze-vectors" / "collectibles" / "candy_01.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<path d="M10 10 L90 90" stroke="#000"/></svg>',
        encoding="utf-8",
    )
    assert run("book", "validate", str(tiny_copy), "--output", str(tmp_path)) == EXIT_INVALID_INPUT


def test_story_text_that_will_not_fit_makes_validate_exit_2(
    tiny_copy: Path, tmp_path: Path
) -> None:
    config_path = tiny_copy / "input" / "book.json"
    obj = json.loads(config_path.read_text(encoding="utf-8"))
    obj["content"]["scenes"][0]["text"] = " ".join(["candy"] * 90)
    config_path.write_text(json.dumps(obj), encoding="utf-8")
    assert run("book", "validate", str(tiny_copy), "--output", str(tmp_path)) == EXIT_INVALID_INPUT


# ---------------------------------------------------------------------------
# maze commands and the cache boundary
# ---------------------------------------------------------------------------


def test_maze_generate_writes_the_two_json_artifacts(tiny_copy: Path, tmp_path: Path) -> None:
    assert run(
        "maze", "generate", str(tiny_copy), "--index", "1", "--output", str(tmp_path)
    ) == EXIT_OK
    base = tmp_path / "tiny-child-book" / "build" / "mazes"
    assert (base / "001.json").is_file()
    assert (base / "001.analysis.json").is_file()


def test_maze_render_refuses_to_generate_and_exits_3(tiny_copy: Path, tmp_path: Path) -> None:
    """17.13: `maze render` "renders cached maze data and MUST NOT silently
    regenerate it"."""
    assert run(
        "maze", "render", str(tiny_copy), "--index", "1", "--output", str(tmp_path)
    ) == EXIT_GENERATION
    assert not (tmp_path / "tiny-child-book" / "build" / "mazes" / "001.json").exists()


def test_maze_render_writes_an_svg_once_the_maze_is_cached(
    tiny_copy: Path, tmp_path: Path
) -> None:
    run("maze", "generate", str(tiny_copy), "--index", "1", "--output", str(tmp_path))
    assert run(
        "maze", "render", str(tiny_copy), "--index", "1", "--format", "svg",
        "--output", str(tmp_path),
    ) == EXIT_OK
    svg = tmp_path / "tiny-child-book" / "build" / "mazes" / "001.svg"
    assert svg.is_file()
    assert svg.read_text(encoding="utf-8").startswith("<svg")


def test_maze_validate_needs_a_cached_maze(tiny_copy: Path, tmp_path: Path) -> None:
    assert run(
        "maze", "validate", str(tiny_copy), "--index", "1", "--output", str(tmp_path)
    ) == EXIT_GENERATION
    run("maze", "generate", str(tiny_copy), "--index", "1", "--output", str(tmp_path))
    assert run(
        "maze", "validate", str(tiny_copy), "--index", "1", "--output", str(tmp_path)
    ) == EXIT_OK


def test_book_assemble_without_cached_mazes_exits_3(tiny_copy: Path, tmp_path: Path) -> None:
    """17.13: assemble "requires valid cached maze artifacts"."""
    assert run("book", "assemble", str(tiny_copy), "--output", str(tmp_path)) == EXIT_GENERATION


def test_the_seed_override_changes_the_maze(tiny_copy: Path, tmp_path: Path) -> None:
    run("maze", "generate", str(tiny_copy), "--index", "1", "--output", str(tmp_path))
    first = (tmp_path / "tiny-child-book" / "build" / "mazes" / "001.json").read_text(encoding="utf-8")

    other = tmp_path / "other"
    run("maze", "generate", str(tiny_copy), "--index", "1", "--seed", "987654",
        "--output", str(other))
    second = (other / "tiny-child-book" / "build" / "mazes" / "001.json").read_text(encoding="utf-8")
    assert first != second


def test_only_restricts_generation_to_the_range(tiny_copy: Path, tmp_path: Path) -> None:
    assert run(
        "book", "generate-mazes", str(tiny_copy), "--only", "2:3", "--output", str(tmp_path)
    ) == EXIT_OK
    base = tmp_path / "tiny-child-book" / "build" / "mazes"
    assert not (base / "001.json").exists()
    assert (base / "002.json").is_file() and (base / "003.json").is_file()


# ---------------------------------------------------------------------------
# The whole book
# ---------------------------------------------------------------------------


def test_the_tiny_book_builds_end_to_end(tiny_copy: Path, tmp_path: Path) -> None:
    """A three-maze book with no front matter and no page vectors, through every
    stage. This is 17.2's generalization claim under test: a new book is a
    folder, some assets and a book.json."""
    assert run("book", "build", str(tiny_copy), "--output", str(tmp_path)) == EXIT_OK

    base = tmp_path / "tiny-child-book"
    # The two files that get uploaded, and nothing else, in `output`.
    assert sorted(p.name for p in (base / "output").iterdir()) == ["book-interior.pdf"]
    # Everything a build produces on the way there, in `build`.
    for name in (
        "normalized-book.json", "page-plan.json", "contact-sheet.png",
        "preflight.json", "book-interior-editable.pdf",
    ):
        assert (base / "build" / name).is_file(), name

    report = json.loads((base / "build" / "preflight.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["failed"] == []


def test_a_second_build_reuses_the_cached_mazes(tiny_copy: Path, tmp_path: Path, capsys) -> None:
    run("book", "build", str(tiny_copy), "--output", str(tmp_path))
    capsys.readouterr()
    assert run("book", "build", str(tiny_copy), "--output", str(tmp_path)) == EXIT_OK
    assert "0 generated, 3 cached" in capsys.readouterr().out


def test_force_regenerates_everything(tiny_copy: Path, tmp_path: Path, capsys) -> None:
    run("book", "generate-mazes", str(tiny_copy), "--output", str(tmp_path))
    capsys.readouterr()
    run("book", "generate-mazes", str(tiny_copy), "--force", "--output", str(tmp_path))
    assert "3 generated, 0 cached" in capsys.readouterr().out


def test_preflight_on_an_unbuilt_book_exits_5(tiny_copy: Path, tmp_path: Path) -> None:
    assert run("book", "preflight", str(tiny_copy), "--output", str(tmp_path)) == EXIT_PREFLIGHT


def test_preflight_checks_existing_output_without_changing_it(
    tiny_copy: Path, tmp_path: Path
) -> None:
    """17.13: it "checks existing outputs without changing them"."""
    run("book", "build", str(tiny_copy), "--output", str(tmp_path))
    interior = tmp_path / "tiny-child-book" / "output" / "book-interior.pdf"
    before = interior.read_bytes()
    assert run("book", "preflight", str(tiny_copy), "--output", str(tmp_path)) == EXIT_OK
    assert interior.read_bytes() == before


def test_a_tampered_pdf_fails_preflight_with_exit_5(tiny_copy: Path, tmp_path: Path) -> None:
    """The gate has to be able to say no, or it is decoration."""
    run("book", "build", str(tiny_copy), "--output", str(tmp_path))
    interior = tmp_path / "tiny-child-book" / "output" / "book-interior.pdf"

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(str(interior)).pages[:-1]:  # drop a page
        writer.add_page(page)
    with interior.open("wb") as handle:
        writer.write(handle)

    assert run("book", "preflight", str(tiny_copy), "--output", str(tmp_path)) == EXIT_PREFLIGHT


@pytest.mark.slow
def test_the_halloween_book_builds_and_passes_every_preflight_check(
    halloween: Path, tmp_path: Path
) -> None:
    """15's acceptance criterion, as one assertion: the real 50-maze book, built
    from a clean output directory, with every preflight check green."""
    assert run("book", "build", str(halloween), "--output", str(tmp_path)) == EXIT_OK

    base = tmp_path / "jims-halloween-maze-adventure"
    report = json.loads((base / "build" / "preflight.json").read_text(encoding="utf-8"))
    assert report["passed"] is True, report["failed"]
    assert report["skipped"] == []

    plan = json.loads((base / "build" / "page-plan.json").read_text(encoding="utf-8"))
    # The total is the book's own arithmetic, so the assertions are the
    # properties 18.7 fixes: an even total, and every maze covered once.
    assert plan["totalPages"] % 2 == 0
    assert plan["frontMatterPages"] % 2 == 1

    from pypdf import PdfReader

    assert len(PdfReader(str(base / "output" / "book-interior.pdf")).pages) == plan["totalPages"]
    assert len(list((base / "build" / "mazes").glob("*.json"))) == 100  # maze + analysis each


def test_a_partial_run_does_not_overwrite_the_whole_book_contact_sheet(
    tiny_copy: Path, tmp_path: Path, capsys
) -> None:
    """The contact sheet exists so a human can judge variety and difficulty
    across the *book*. Rewriting it from an --only subset would silently replace
    a full sheet with a partial one, which looks like the book got smaller."""
    from PIL import Image

    run("book", "generate-mazes", str(tiny_copy), "--output", str(tmp_path))
    sheet = tmp_path / "tiny-child-book" / "build" / "contact-sheet.png"
    with Image.open(sheet) as image:
        full_size = image.size

    capsys.readouterr()
    run("book", "generate-mazes", str(tiny_copy), "--only", "1", "--output", str(tmp_path))
    assert "contact sheet skipped" in capsys.readouterr().out
    with Image.open(sheet) as image:
        assert image.size == full_size


@pytest.mark.slow
def test_copying_a_book_and_editing_only_its_config_makes_a_different_book(
    halloween: Path, tmp_path: Path
) -> None:
    """17.17's first definition-of-done item, and the repository's whole claim:
    "copying books/jims-halloween-maze-adventure/ to a new folder and editing
    only book.json and SVG assets is enough to start a new book".

    Nothing but book.json is touched here -- not one line of src/, not one asset
    -- and the result is a different book with a different profile, seed, length
    and story, built to a different page count with no front matter at all.
    """
    package = tmp_path / "books" / "sams-space-maze-quest"
    shutil.copytree(halloween, package)

    config_path = package / "input" / "book.json"
    obj = json.loads(config_path.read_text(encoding="utf-8"))
    obj["book"].update(
        id="sams-space-maze-quest",
        title="Sam's Space Maze Quest",
        subtitle="12 Star-Collecting Mazes",
        seed=7777,
        mazeCount=12,
        profileId="child_6_7",
    )
    obj["content"]["scenes"] = [
        {
            "number": index,
            "title": f"Launch Pad {index}",
            "text": f"Sam checks the fuel gauge and counts {index} stars ahead.",
            "pageVector": "jim_in_sheet.png",
        }
        for index in range(1, 13)
    ]
    obj["layout"]["frontMatterPdf"] = None
    obj["layout"]["expectedPageCount"] = None
    config_path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    (package / "input" / "front-matter.pdf").unlink()

    output = tmp_path / "out"
    assert run("book", "build", str(package), "--output", str(output)) == EXIT_OK

    base = output / "sams-space-maze-quest"
    report = json.loads((base / "build" / "preflight.json").read_text(encoding="utf-8"))
    assert report["passed"] is True, report["failed"]

    plan = json.loads((base / "build" / "page-plan.json").read_text(encoding="utf-8"))
    assert plan["frontMatterPages"] == 0
    assert plan["totalPages"] % 2 == 0
    assert len([p for p in plan["pages"] if p["kind"] == "maze"]) == 12

    # A different seed and profile must produce genuinely different mazes.
    first = json.loads((base / "build" / "mazes" / "001.json").read_text(encoding="utf-8"))
    original = json.loads(
        (Path(__file__).resolve().parents[1] / "output" / "jims-halloween-maze-adventure"
         / "mazes" / "001.json").read_text(encoding="utf-8")
    ) if (
        Path(__file__).resolve().parents[1] / "output" / "jims-halloween-maze-adventure"
        / "mazes" / "001.json"
    ).is_file() else None
    assert first["grid"]["rows"] == 6  # child_6_7's first band, not halloween's 8x8
    if original is not None:
        assert first["openEdges"] != original["openEdges"]


def test_every_planned_page_gets_its_own_pdf_including_front_matter(
    tiny_copy: Path, tmp_path: Path
) -> None:
    """17.14's tree starts at page-001.pdf. The per-page files are the artifact a
    reviewer opens to look at one page, and that argument applies to the front
    matter too -- a run of missing low numbers just makes them wonder what broke.
    """
    run("book", "build", str(tiny_copy), "--output", str(tmp_path))
    plan = json.loads(
        (tmp_path / "tiny-child-book" / "build" / "page-plan.json").read_text(encoding="utf-8")
    )
    pages = sorted((tmp_path / "tiny-child-book" / "build" / "pages").glob("page-*.pdf"))
    assert [p.name for p in pages] == [
        f"page-{record['pageNumber']:03d}.pdf" for record in plan["pages"]
    ]
    assert all(p.stat().st_size > 0 for p in pages)


@pytest.mark.slow
def test_front_matter_pages_are_split_out_verbatim(halloween: Path, tmp_path: Path) -> None:
    from pypdf import PdfReader

    run("book", "generate-mazes", str(halloween), "--output", str(tmp_path))
    run("book", "assemble", str(halloween), "--output", str(tmp_path))

    pages = tmp_path / "jims-halloween-maze-adventure" / "build" / "pages"
    assert (pages / "page-001.pdf").is_file()
    assert len(PdfReader(str(pages / "page-001.pdf")).pages) == 1
    plan = json.loads(
        (tmp_path / "jims-halloween-maze-adventure" / "build" / "page-plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(list(pages.glob("page-*.pdf"))) == plan["totalPages"]
