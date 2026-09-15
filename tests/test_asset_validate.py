"""Tests for ``maze_book.assets.validate`` (PRD 18.5 hardened icon rules).

Each of the nine rule codes gets a file that trips it and, where the boundary is
the interesting part, a neighbouring file that does not. The geometric rules
(A6-A8) additionally get their machinery checked directly: the distance
transform against brute force, and the fringe-versus-vanished split that keeps a
rounded corner from reading as a hairline.

The last section sweeps every SVG actually shipped in the repository. That is
the regression that matters day to day -- a hand-edited asset that stops
measuring clean fails here rather than at the press.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from maze_book.assets.svg_subset import parse_svg
from maze_book.assets.validate import (
    AssetProfile,
    Mask,
    distance_transform,
    raise_for_reports,
    rasterize,
    validate_directory,
    validate_svg,
)
from maze_book.errors import AssetError


def doc(body: str, *, view: str = "0 0 100 100") -> str:
    """Wrap a body into an SVG document. Bare path data is given a default
    ``<path>`` element, so a test about a *shape* need not restate the markup."""
    if not body.lstrip().startswith("<"):
        body = f'<path d="{body}"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{body}</svg>'


def check(body: str, *, view: str = "0 0 100 100", geometry: bool = True, **profile_kwargs):
    profile = AssetProfile(**profile_kwargs) if profile_kwargs else None
    return validate_svg(
        "<memory>.svg", text=doc(body, view=view), geometry=geometry, profile=profile
    )


def rect_path(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"M{x0} {y0} L{x1} {y0} L{x1} {y1} L{x0} {y1} Z"


def disc_path(cx: float, cy: float, r: float, *, sweep: int = 1) -> str:
    return (
        f"M{cx - r} {cy} A{r} {r} 0 1 {sweep} {cx + r} {cy} "
        f"A{r} {r} 0 1 {sweep} {cx - r} {cy} Z"
    )


def codes(report) -> set[str]:
    return {error.split(":", 1)[0] for error in report.errors}


# ---------------------------------------------------------------------------
# A1_subset -- structural failure is reported as a rule, not an exception
# ---------------------------------------------------------------------------


def test_a1_reports_a_parse_failure_as_a_rule_rather_than_raising() -> None:
    """``validate_svg`` never raises for a rule failure. A directory sweep must
    be able to report *every* bad file in one pass, which it cannot do if the
    first malformed one aborts the loop."""
    report = check('<path d="M0 0 L10 10 Z" stroke="#000"/>')
    assert report.passed is False
    assert codes(report) == {"A1_subset"}
    assert "filled paths only" in report.errors[0]


def test_a1_short_circuits_the_remaining_checks() -> None:
    report = check("<circle/>")
    assert report.constraints == {"A1_subset": False}
    assert report.bbox is None


# ---------------------------------------------------------------------------
# A2_viewBox
# ---------------------------------------------------------------------------


def test_a2_rejects_a_viewbox_other_than_the_profile_s() -> None:
    report = check(rect_path(10, 10, 90, 90), view="0 0 200 200")
    assert "A2_viewBox" in codes(report)
    assert "silently resizes every placement" in report.errors[0]


def test_a2_accepts_the_profile_viewbox() -> None:
    assert check(rect_path(10, 10, 90, 90)).constraints["A2_viewBox"] is True


def test_a2_honours_a_non_default_profile_viewbox() -> None:
    profile = AssetProfile(view_box=(0.0, 0.0, 200.0, 200.0))
    report = validate_svg(
        "<memory>.svg", text=doc(rect_path(20, 20, 180, 180), view="0 0 200 200"), profile=profile
    )
    assert report.constraints["A2_viewBox"] is True


# ---------------------------------------------------------------------------
# A3_padding and A4_centered
# ---------------------------------------------------------------------------


def test_a3_rejects_artwork_touching_the_viewbox_edge() -> None:
    report = check(rect_path(2, 10, 90, 90))
    assert "A3_padding" in codes(report)
    assert "left 2.0" in report.errors[0]


def test_a3_accepts_padding_exactly_at_the_floor() -> None:
    """Six units is the documented minimum, not an exclusive bound -- artwork
    drawn precisely to the guide must not fail."""
    report = check(rect_path(6, 6, 94, 94))
    assert report.constraints["A3_padding"] is True


def test_a3_names_every_short_side_at_once() -> None:
    report = check(rect_path(1, 2, 99, 98))
    message = next(e for e in report.errors if e.startswith("A3_padding"))
    for side in ("left", "bottom", "right", "top"):
        assert side in message


def test_a4_rejects_artwork_pushed_off_centre() -> None:
    report = check(rect_path(6, 6, 30, 30))
    assert "A4_centered" in codes(report)


def test_a4_tolerates_the_lopsidedness_of_a_hand_drawn_icon() -> None:
    """Composition is 'centred', but no illustrator centres to the pixel and an
    icon whose ink is slightly lopsided still lands correctly in its cell."""
    report = check(rect_path(6, 6, 90, 90))  # centre (48,48), 2.8 units off
    assert report.constraints["A4_centered"] is True


# ---------------------------------------------------------------------------
# A5_subpathBudget -- a document-wide budget
# ---------------------------------------------------------------------------


def test_a5_counts_subpaths_across_the_whole_document_not_per_element() -> None:
    body = "".join(
        f'<path d="{rect_path(8 + 6 * i, 8, 12 + 6 * i, 92)}"/>' for i in range(13)
    )
    report = check(body, geometry=False)
    assert report.subpath_count == 13
    assert "A5_subpathBudget" in codes(report)


def test_a5_counts_multiple_subpaths_inside_one_element() -> None:
    d = " ".join(rect_path(8 + 6 * i, 8, 12 + 6 * i, 92) for i in range(13))
    report = check(f'<path d="{d}"/>', geometry=False)
    assert report.subpath_count == 13
    assert "A5_subpathBudget" in codes(report)


def test_a5_accepts_exactly_the_budget() -> None:
    d = " ".join(rect_path(8 + 6 * i, 8, 12 + 6 * i, 92) for i in range(12))
    report = check(f'<path d="{d}"/>', geometry=False)
    assert report.constraints["A5_subpathBudget"] is True


# ---------------------------------------------------------------------------
# A9_notEmpty and A6_coverage
# ---------------------------------------------------------------------------


def test_a9_rejects_a_document_with_no_filled_artwork() -> None:
    report = check(f'<path fill="none" d="{rect_path(10, 10, 90, 90)}"/>')
    assert "A9_notEmpty" in codes(report)
    assert "would print blank" in report.errors[0]
    # The composition rules that depend on a bbox are recorded as failed, not skipped.
    for code in ("A3_padding", "A4_centered", "A5_subpathBudget", "A6_coverage"):
        assert report.constraints[code] is False


def test_a6_rejects_a_background_rectangle() -> None:
    """A background rect is what an inverted drawing or a stray artboard looks
    like, and it would black out the cell it sits in. It necessarily trips A3 as
    well: the largest rectangle that keeps 6 units of padding covers 77%, under
    the 85% ceiling, so nothing reaches A6 without first eating its padding."""
    report = check(rect_path(0, 0, 100, 100))
    assert {"A6_coverage", "A3_padding"} <= codes(report)
    assert report.coverage_fraction == pytest.approx(1.0)


def test_a6_accepts_the_largest_icon_that_still_keeps_its_padding() -> None:
    report = check(rect_path(6, 6, 94, 94))
    assert report.constraints["A6_coverage"] is True
    assert report.coverage_fraction == pytest.approx(0.7744, abs=0.01)


def test_a6_accepts_a_normal_icon() -> None:
    report = check(disc_path(50, 50, 30))
    assert report.constraints["A6_coverage"] is True
    assert report.coverage_fraction == pytest.approx(math.pi * 900 / 10000, abs=0.01)


def test_a6_rejects_paths_that_enclose_no_area() -> None:
    """A 'drawing' made of degenerate back-and-forth paths has a bbox and passes
    composition, but rasterizes to nothing and prints blank."""
    report = check('<path d="M20 50 L80 50 L20 50 Z"/>')
    assert "A6_coverage" in codes(report)
    assert "prints blank" in report.errors[0]
    assert report.constraints["A7_minimumFeature"] is False


# ---------------------------------------------------------------------------
# A7_minimumFeature
# ---------------------------------------------------------------------------


def test_a7_rejects_a_hairline_limb() -> None:
    # 22 units of 3-unit-thick limb protrude past the disc on each side: 66 sq
    # units, comfortably past the 49-unit fringe tolerance.
    body = f'<path d="{disc_path(50, 50, 20)}"/><path d="{rect_path(8, 48.5, 92, 51.5)}"/>'
    report = check(body)
    assert "A7_minimumFeature" in codes(report)
    assert "thinner than 7 units" in next(e for e in report.errors if e.startswith("A7"))


def test_a7_accepts_a_limb_at_the_minimum_width() -> None:
    body = f'<path d="{disc_path(50, 50, 20)}"/><path d="{rect_path(8, 46, 92, 54)}"/>'
    assert check(body).constraints["A7_minimumFeature"] is True


def test_a7_excuses_a_short_stub_as_corner_rounding_not_a_limb() -> None:
    """The same 3-unit thickness, protruding only 6 units: 18 sq units of loss,
    which is the scale of an acute corner rather than of a limb the press would
    drop. Area is what separates the two, and only for a region still attached
    to something thicker."""
    body = f'<path d="{disc_path(50, 50, 24)}"/><path d="{rect_path(20, 48.5, 80, 51.5)}"/>'
    assert check(body).constraints["A7_minimumFeature"] is True


def test_a7_does_not_fire_on_the_rounding_of_a_sharp_corner() -> None:
    """Opening shaves r^2(1 - pi/4) = 2.6 sq units off every right angle. If that
    counted, no rectangle would ever validate."""
    report = check(rect_path(20, 20, 80, 80))
    assert report.constraints["A7_minimumFeature"] is True


def test_a7_reports_where_the_offending_region_is() -> None:
    body = f'<path d="{disc_path(50, 50, 20)}"/><path d="{rect_path(8, 48.5, 92, 51.5)}"/>'
    message = next(e for e in check(body).errors if e.startswith("A7"))
    assert "near (" in message and "sq units" in message


def test_widest_feature_is_measured_even_when_the_file_passes() -> None:
    """'Which rule failed' is only half an answer; the measurement is what tells
    an illustrator what to change."""
    report = check(disc_path(50, 50, 30))
    assert report.widest_feature_units == pytest.approx(60.0, abs=1.5)


# ---------------------------------------------------------------------------
# A8_minimumGap
# ---------------------------------------------------------------------------


def test_a8_rejects_a_narrow_channel_between_two_blocks() -> None:
    body = (
        f'<path d="{rect_path(20, 20, 48, 80)}"/>'
        f'<path d="{rect_path(52, 20, 80, 80)}"/>'
    )  # a 4-unit slot
    report = check(body)
    assert "A8_minimumGap" in codes(report)
    assert "fills in with toner" in next(e for e in report.errors if e.startswith("A8"))


def test_a8_accepts_a_channel_at_the_minimum_width() -> None:
    body = (
        f'<path d="{rect_path(20, 20, 45, 80)}"/>'
        f'<path d="{rect_path(53, 20, 78, 80)}"/>'
    )  # an 8-unit slot
    assert check(body).constraints["A8_minimumGap"] is True


def test_a8_rejects_a_pinhole_even_though_its_area_is_tiny() -> None:
    """A component that vanishes outright is judged by *whether* it survived
    opening, not by area: a 4-unit hole is 16 sq units, far under any fringe
    tolerance, and it is exactly the hole that closes up on press."""
    body = (
        f'<path fill-rule="evenodd" d="{disc_path(50, 50, 30)} {disc_path(50, 50, 2)}"/>'
    )
    report = check(body)
    assert "A8_minimumGap" in codes(report)


def test_a8_accepts_a_hole_wide_enough_to_survive_the_press() -> None:
    body = f'<path fill-rule="evenodd" d="{disc_path(50, 50, 30)} {disc_path(50, 50, 6)}"/>'
    assert check(body).constraints["A8_minimumGap"] is True


def test_a8_ignores_the_white_padding_ring_outside_the_artwork() -> None:
    """Outside the bbox is white by design. Judging it as a gap would fail every
    asset for having padding -- the very thing A3 requires."""
    report = check(disc_path(50, 50, 20))
    assert report.constraints["A8_minimumGap"] is True
    assert report.passed is True


def test_a8_catches_a_slot_that_is_open_at_one_end() -> None:
    """Restricting to the bbox rather than to 'white not connected to the border'
    is deliberate: a 3-unit slot open at one end still fills with toner."""
    body = (
        f'<path d="{rect_path(20, 20, 48, 80)}"/>'
        f'<path d="{rect_path(51, 20, 80, 60)}"/>'
    )
    assert "A8_minimumGap" in codes(check(body))


# ---------------------------------------------------------------------------
# Rasterization and fill rules
# ---------------------------------------------------------------------------


def test_nonzero_subpaths_wound_alike_union_rather_than_cancel() -> None:
    """The rasterizer fills every nonzero subpath in one pass, so two shapes
    wound the same way paint their overlap twice. This is why the placeholder
    generator normalizes winding: wound against each other, the overlap would
    punch a hole clean through both."""
    same = doc(f'<path d="{disc_path(40, 50, 18)}"/><path d="{disc_path(60, 50, 18)}"/>')
    mask = rasterize(parse_svg("<memory>", text=same), samples_per_unit=2.0)
    assert mask[(100, 100)] == 1  # the overlap at (50,50) is painted


def test_nonzero_subpaths_wound_against_each_other_cancel_in_their_overlap() -> None:
    opposed = doc(
        f'<path d="{disc_path(40, 50, 18, sweep=1)}"/>'
        f'<path d="{disc_path(60, 50, 18, sweep=0)}"/>'
    )
    mask = rasterize(parse_svg("<memory>", text=opposed), samples_per_unit=2.0)
    assert mask[(100, 100)] == 0  # winding sums to zero: a hole


def test_evenodd_puts_a_hole_inside_a_shape() -> None:
    body = f'<path fill-rule="evenodd" d="{disc_path(50, 50, 30)} {disc_path(50, 50, 10)}"/>'
    mask = rasterize(parse_svg("<memory>", text=doc(body)), samples_per_unit=2.0)
    assert mask[(100, 100)] == 0   # centre is the hole
    assert mask[(100, 60)] == 1    # 20 units out is ink


def test_the_two_fill_rule_groups_are_rasterized_independently() -> None:
    """An evenodd element and a nonzero element in the same file must not XOR
    against each other -- the gravestone's carved cross and its ground bar are
    exactly this arrangement."""
    body = (
        f'<path fill-rule="evenodd" d="{disc_path(50, 40, 26)} {disc_path(50, 40, 8)}"/>'
        f'<path d="{rect_path(20, 60, 80, 84)}"/>'
    )
    report = check(body)
    assert report.passed is True, report.errors


# ---------------------------------------------------------------------------
# Distance transform
# ---------------------------------------------------------------------------


def _brute_force_distance(mask: Mask) -> list[float]:
    background = [
        (i % mask.width, i // mask.width) for i, v in enumerate(mask.bits) if v == 0
    ]
    out = []
    for index in range(len(mask.bits)):
        if not mask.bits[index]:
            out.append(0.0)
            continue
        x, y = index % mask.width, index // mask.width
        out.append(min(math.hypot(x - bx, y - by) for bx, by in background) / mask.scale)
    return out


def test_distance_transform_is_exact_not_an_approximation() -> None:
    """Felzenszwalb & Huttenlocher's separable transform is the *exact* Euclidean
    distance, not a chamfer approximation -- checked against brute force, which
    is only affordable on a mask this small."""
    mask = Mask(12, 12, 1.0)
    for y in range(3, 9):
        for x in range(2, 10):
            mask.bits[y * 12 + x] = 1
    assert distance_transform(mask) == pytest.approx(_brute_force_distance(mask), abs=1e-9)


def test_distance_transform_of_an_all_foreground_mask_is_infinite() -> None:
    mask = Mask(4, 4, 1.0)
    mask.bits = bytearray([1] * 16)
    assert all(value == float("inf") for value in distance_transform(mask))


def test_inverted_swaps_foreground_and_background() -> None:
    mask = Mask(3, 1, 1.0)
    mask.bits = bytearray([1, 0, 1])
    assert list(mask.inverted().bits) == [0, 1, 0]
    assert mask.count() == 2


# ---------------------------------------------------------------------------
# Reporting and directory sweeps
# ---------------------------------------------------------------------------


def test_summary_names_the_file_state_and_every_measurement() -> None:
    summary = check(disc_path(50, 50, 30)).summary()
    for fragment in ("ok", "bbox", "pad", "subpaths", "ink", "widest"):
        assert fragment in summary


def test_raise_for_status_carries_every_error_in_details() -> None:
    report = check(rect_path(2, 2, 98, 98))
    with pytest.raises(AssetError, match=r"failed asset validation") as excinfo:
        report.raise_for_status()
    assert len(excinfo.value.details) == len(report.errors) >= 2


def test_raise_for_reports_lists_every_failing_file_in_one_raise(tmp_path: Path) -> None:
    """One run must surface every problem. Raising on the first bad file turns
    fixing a folder into a serial guessing game."""
    (tmp_path / "good.svg").write_text(doc(disc_path(50, 50, 30)), encoding="utf-8")
    (tmp_path / "bad_a.svg").write_text(doc(rect_path(1, 1, 99, 99)), encoding="utf-8")
    (tmp_path / "bad_b.svg").write_text(doc('<path d="M0 0" stroke="#000"/>'), encoding="utf-8")

    reports = validate_directory(tmp_path)
    assert [r.path.name for r in reports] == ["bad_a.svg", "bad_b.svg", "good.svg"]

    with pytest.raises(AssetError, match=r"2 of 3 test asset\(s\) failed") as excinfo:
        raise_for_reports(reports, label="test")
    named = "\n".join(excinfo.value.details)
    assert "bad_a.svg" in named and "bad_b.svg" in named and "good.svg" not in named


def test_raise_for_reports_is_silent_when_everything_passes(tmp_path: Path) -> None:
    (tmp_path / "good.svg").write_text(doc(disc_path(50, 50, 30)), encoding="utf-8")
    raise_for_reports(validate_directory(tmp_path), label="test")  # must not raise


def test_validate_directory_rejects_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(AssetError, match=r"asset directory not found"):
        validate_directory(tmp_path / "nope")


def test_geometry_false_skips_the_rasterized_rules_only() -> None:
    body = f'<path d="{disc_path(50, 50, 24)}"/><path d="{rect_path(20, 49, 80, 51)}"/>'
    fast = check(body, geometry=False)
    assert "A7_minimumFeature" not in fast.constraints
    assert fast.constraints["A3_padding"] is True
    assert "A7_minimumFeature" in check(body).constraints


# ---------------------------------------------------------------------------
# The assets actually shipped in this repository
# ---------------------------------------------------------------------------


def _shipped_svgs(repo_root: Path) -> list[Path]:
    paths = sorted(repo_root.glob("books/*/assets/**/*.svg"))
    assert paths, "expected the Halloween book package to ship SVG assets"
    return paths


def test_every_shipped_asset_passes_the_full_18_5_rule_set(repo_root: Path) -> None:
    failures = []
    for path in _shipped_svgs(repo_root):
        report = validate_svg(path)
        if not report.passed:
            failures.append(f"{path.relative_to(repo_root)}: {report.errors}")
    assert failures == []


def test_no_shipped_asset_contains_a_stroke_attribute(repo_root: Path) -> None:
    """Belt and braces alongside A1: a grep-level check states the 18.5 rule in
    the form an illustrator will actually search for."""
    offenders = [
        str(path.relative_to(repo_root))
        for path in _shipped_svgs(repo_root)
        if "stroke" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_halloween_package_ships_the_folders_the_contract_names(repo_root: Path) -> None:
    book = repo_root / "books" / "jims-halloween-maze-adventure" / "assets"
    for folder in (
        "beginning-vectors", "ending-vectors",
        "maze-vectors/dead-end", "maze-vectors/collectibles", "page-vectors",
    ):
        directory = book / folder
        assert directory.is_dir(), folder
        assert list(directory.glob("*.svg")), f"{folder} is empty"
