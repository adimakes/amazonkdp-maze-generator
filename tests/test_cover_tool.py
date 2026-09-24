"""tools/make_cover.py: which art it uses, and where it puts things on it.

A cover is replaced by dropping an image into a folder, so the rule for which
file wins has to be simple and unforgiving: exactly one image per face, any
name. And the sample mazes are pasted onto cards measured on the image, so the
image and the cards have to be placed by the same arithmetic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture(scope="module")
def cover_tool(repo_root: Path):
    spec = importlib.util.spec_from_file_location(
        "make_cover", repo_root / "tools" / "make_cover.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _image(path: Path, size=(30, 40)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "purple").save(path)
    return path


def test_any_name_is_used_when_it_is_the_only_image(cover_tool, tmp_path: Path) -> None:
    art = _image(tmp_path / "cover-front" / "new new cover front (2x).jpg")
    (tmp_path / "cover-front" / ".DS_Store").write_bytes(b"\0")
    (tmp_path / "cover-front" / "notes.txt").write_text("not an image")
    assert cover_tool.face_image(tmp_path, "front") == art


@pytest.mark.parametrize("count", [0, 2])
def test_anything_but_one_image_is_refused(cover_tool, tmp_path: Path, count: int) -> None:
    (tmp_path / "cover-back").mkdir()
    for index in range(count):
        _image(tmp_path / "cover-back" / f"back-{index}.png")
    with pytest.raises(SystemExit, match="exactly one back cover image"):
        cover_tool.face_image(tmp_path, "back")


def test_a_missing_folder_says_where_to_put_the_art(cover_tool, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="cover-front"):
        cover_tool.face_image(tmp_path, "front")


def test_three_by_four_art_is_cropped_top_and_bottom_not_squashed(cover_tool) -> None:
    """A 3:4 image on a 23:30 panel: full width, overflowing equally top and bottom."""
    box = (0.0, 0.0, 8.625 * 72, 11.25 * 72)
    x, y, width, height = cover_tool.fitted((1792, 2400), box)
    assert (x, width) == pytest.approx((0.0, 8.625 * 72))
    assert height / width == pytest.approx(2400 / 1792)
    assert y == pytest.approx(-(height - 11.25 * 72) / 2.0)
    assert y < 0.0


def test_effective_dpi_is_pixels_over_printed_inches(cover_tool, tmp_path: Path) -> None:
    art = _image(tmp_path / "art.png", size=(1792, 2400))
    placement = cover_tool.fitted((1792, 2400), (0.0, 0.0, 8.625 * 72, 11.25 * 72))
    assert cover_tool.effective_dpi(art, placement) == pytest.approx(1792 / 8.625)
