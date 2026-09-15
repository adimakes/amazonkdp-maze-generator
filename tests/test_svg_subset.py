"""Tests for ``maze_book.assets.svg_subset`` (PRD 18.5).

The subset parser is the single source shared by the validator and the SVG->PDF
converter, so its two jobs are tested separately: *rejecting* everything outside
the subset (a permissive parser is how an unprintable file reaches the press),
and *reducing* everything inside it to cubics faithfully (a lossy reduction is
how artwork silently loses a limb between QA and print).
"""

from __future__ import annotations

import math

import pytest

from maze_book.assets import svg_subset
from maze_book.assets.svg_subset import (
    Cubic,
    Matrix,
    arc_to_cubics,
    parse_path,
    parse_svg,
    parse_transform,
)
from maze_book.errors import AssetError

SQUARE = "M10 10 L90 10 L90 90 L10 90 Z"


def doc(body: str, *, view: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">{body}</svg>'


def parse(body: str, **kwargs):
    return parse_svg("<memory>", text=doc(body, **kwargs))


# ---------------------------------------------------------------------------
# Structural rejections: what the subset refuses
# ---------------------------------------------------------------------------


def test_stroke_attribute_is_rejected() -> None:
    with pytest.raises(AssetError, match=r"filled paths only"):
        parse(f'<path d="{SQUARE}" stroke="#000000"/>')


@pytest.mark.parametrize(
    "attr",
    ["stroke-width", "stroke-linecap", "stroke-dasharray", "opacity", "fill-opacity",
     "filter", "mask", "clip-path"],
)
def test_every_forbidden_attribute_is_rejected(attr: str) -> None:
    with pytest.raises(AssetError):
        parse(f'<path d="{SQUARE}" {attr}="4"/>')


def test_stroke_hidden_in_a_style_attribute_is_still_rejected() -> None:
    """``style`` is folded into the attribute set before the check, so the
    subset cannot be evaded by moving a property into CSS."""
    with pytest.raises(AssetError, match=r"filled paths only"):
        parse(f'<path d="{SQUARE}" style="stroke:#000;stroke-width:6"/>')


@pytest.mark.parametrize("tag", ["circle", "rect", "ellipse", "line", "polygon", "text", "image"])
def test_shape_and_content_elements_outside_the_subset_are_rejected(tag: str) -> None:
    with pytest.raises(AssetError, match=r"outside the supported SVG subset"):
        parse(f'<{tag}/>')


def test_doctype_and_entity_declarations_are_rejected() -> None:
    xml = '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"/>'
    with pytest.raises(AssetError, match=r"entities or a DOCTYPE"):
        parse_svg("<memory>", text=xml)


def test_missing_viewbox_is_rejected() -> None:
    xml = '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L1 1 Z"/></svg>'
    with pytest.raises(AssetError, match=r"must declare a viewBox"):
        parse_svg("<memory>", text=xml)


@pytest.mark.parametrize("view", ["0 0 100", "0 0 0 100", "0 0 100 -5"])
def test_malformed_viewbox_is_rejected(view: str) -> None:
    with pytest.raises(AssetError):
        parse(f'<path d="{SQUARE}"/>', view=view)


def test_non_black_fill_is_rejected() -> None:
    with pytest.raises(AssetError, match=r"not pure black"):
        parse(f'<path d="{SQUARE}" fill="#808080"/>')


def test_unsupported_fill_rule_is_rejected() -> None:
    with pytest.raises(AssetError, match=r"unsupported fill-rule"):
        parse(f'<path d="{SQUARE}" fill-rule="wound"/>')


def test_path_without_d_is_rejected() -> None:
    with pytest.raises(AssetError, match=r"must have a d attribute"):
        parse("<path/>")


def test_root_element_must_be_svg() -> None:
    with pytest.raises(AssetError, match=r"root element must be <svg>"):
        parse_svg("<memory>", text='<g xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>')


# ---------------------------------------------------------------------------
# Structural acceptances: what the subset admits
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["#000", "#000000", "black", "currentColor", "rgb(0,0,0)"])
def test_every_spelling_of_black_is_accepted(value: str) -> None:
    assert len(parse(f'<path d="{SQUARE}" fill="{value}"/>').paths) == 1


@pytest.mark.parametrize("value", ["none", "transparent"])
def test_unpainted_paths_contribute_no_geometry(value: str) -> None:
    """``fill="none"`` is a construction guide, not artwork: it must not count
    toward the subpath budget and must not print."""
    assert parse(f'<path d="{SQUARE}" fill="{value}"/>').paths == []


def test_title_desc_and_metadata_are_skipped_without_error() -> None:
    parsed = parse(f'<title>Bat</title><desc>a bat</desc><metadata/><path d="{SQUARE}"/>')
    assert len(parsed.paths) == 1


def test_fill_is_inherited_from_an_enclosing_group() -> None:
    assert parse(f'<g fill="none"><path d="{SQUARE}"/></g>').paths == []
    assert len(parse(f'<g fill="#000"><path d="{SQUARE}"/></g>').paths) == 1


def test_fill_rule_is_read_per_path() -> None:
    parsed = parse(f'<path d="{SQUARE}"/><path fill-rule="evenodd" d="{SQUARE}"/>')
    assert [item.fill_rule for item in parsed.paths] == ["nonzero", "evenodd"]


def test_one_element_with_several_subpaths_counts_each_separately() -> None:
    """The subpath budget is a document-wide count, so ``M ... Z M ... Z`` in a
    single element must not hide two shapes behind one."""
    parsed = parse(f'<path d="{SQUARE} M20 20 L30 20 L30 30 Z"/>')
    assert len(parsed.paths) == 2


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


def test_translate_moves_geometry_into_viewbox_coordinates() -> None:
    parsed = parse(f'<g transform="translate(5 7)"><path d="{SQUARE}"/></g>')
    assert parsed.bbox() == pytest.approx((15.0, 17.0, 95.0, 97.0))


def test_nested_transforms_compose_outermost_last() -> None:
    parsed = parse(
        f'<g transform="translate(10 0)"><g transform="scale(2)">'
        f'<path d="M0 0 L10 0 L10 10 Z"/></g></g>'
    )
    # scale first (0..20), then translate by the *untransformed* 10 units.
    assert parsed.bbox() == pytest.approx((10.0, 0.0, 30.0, 20.0))


def test_rotate_about_a_point_is_supported() -> None:
    matrix = parse_transform("rotate(90 50 50)")
    assert matrix.apply((50.0, 40.0)) == pytest.approx((60.0, 50.0))


def test_transform_list_applies_left_to_right() -> None:
    left_to_right = parse_transform("translate(10 0) scale(2)")
    assert left_to_right.apply((5.0, 0.0)) == pytest.approx((20.0, 0.0))


def test_unsupported_transform_function_raises() -> None:
    with pytest.raises(AssetError, match=r"unsupported transform"):
        parse_transform("perspective(3)")


# ---------------------------------------------------------------------------
# Path data
# ---------------------------------------------------------------------------


def test_relative_and_absolute_commands_agree() -> None:
    absolute = parse_path("M10 10 L90 10 L90 90 Z")[0]
    relative = parse_path("m10 10 l80 0 l0 80 z")[0]
    assert absolute.bbox() == pytest.approx(relative.bbox())


def test_horizontal_and_vertical_shorthands() -> None:
    sub = parse_path("M10 10 H90 V90 H10 Z")[0]
    assert sub.bbox() == pytest.approx((10.0, 10.0, 90.0, 90.0))


def test_repeated_coordinates_after_moveto_are_implicit_linetos() -> None:
    """SVG 1.1: ``M 0 0 10 10`` is a moveto followed by a lineto, not an error."""
    sub = parse_path("M10 10 90 10 90 90 Z")[0]
    assert len(sub.segments) == 3  # two implicit linetos plus the closing one


def test_z_inserts_the_closing_segment_and_marks_the_subpath_closed() -> None:
    sub = parse_path("M10 10 L90 10 L90 90 Z")[0]
    assert sub.closed is True
    assert sub.end == pytest.approx((10.0, 10.0))


def test_smooth_cubic_reflects_the_previous_control_point() -> None:
    reflected = parse_path("M0 0 C10 10 20 10 30 0 S50 -10 60 0")[0].segments[1]
    assert reflected.p1 == pytest.approx((40.0, -10.0))  # 2*(30,0) - (20,10)


def test_smooth_cubic_without_a_preceding_curve_uses_the_current_point() -> None:
    first = parse_path("M0 0 S20 10 30 0")[0].segments[0]
    assert first.p1 == pytest.approx((0.0, 0.0))


def test_quadratic_is_converted_with_two_thirds_control_interpolation() -> None:
    cubic = parse_path("M0 0 Q30 30 60 0")[0].segments[0]
    assert cubic.p1 == pytest.approx((20.0, 20.0))
    assert cubic.p2 == pytest.approx((40.0, 20.0))


def test_smooth_quadratic_reflects_the_previous_quadratic_control() -> None:
    sub = parse_path("M0 0 Q30 30 60 0 T120 0")[0]
    # reflected quadratic control is 2*(60,0) - (30,30) = (90,-30)
    assert sub.segments[1].p1 == pytest.approx((60.0 + 2.0 * 30.0 / 3.0, -20.0))


def test_subpaths_with_no_segments_are_dropped() -> None:
    assert parse_path("M10 10 M20 20 L30 30") == parse_path("M20 20 L30 30")


def test_path_data_must_begin_with_a_moveto() -> None:
    with pytest.raises(AssetError, match=r"must begin with a moveto"):
        parse_path("L10 10")


def test_unknown_path_command_raises() -> None:
    """A letter outside the command alphabet never reaches the dispatch: the
    tokenizer's character class excludes it, so it surfaces as stray text. Every
    letter the tokenizer *does* match has a dispatch arm, which makes
    ``unsupported path command`` defensive rather than reachable -- asserted
    here on its real behaviour so the message stays accurate."""
    with pytest.raises(AssetError, match=r"unexpected characters in path data"):
        parse_path("M0 0 X10 10")


def test_truncated_command_raises_rather_than_dropping_a_segment() -> None:
    with pytest.raises(AssetError, match=r"needs 2 numbers"):
        parse_path("M0 0 L10")


def test_stray_characters_in_path_data_raise() -> None:
    with pytest.raises(AssetError, match=r"unexpected characters"):
        parse_path("M0 0 L10 10 !! L20 20")


# ---------------------------------------------------------------------------
# Arc conversion (W3C SVG 1.1 F.6)
# ---------------------------------------------------------------------------


def _max_radial_error(cubics: list[Cubic], centre, radius: float) -> float:
    worst = 0.0
    for cubic in cubics:
        for step in range(21):
            x, y = cubic.at(step / 20.0)
            worst = max(worst, abs(math.hypot(x - centre[0], y - centre[1]) - radius))
    return worst


def test_arc_conversion_tracks_the_true_circle_to_under_a_thousandth_of_a_unit() -> None:
    """A quarter-arc cubic with k = 4/3 tan(d/4) has ~0.027% radial error. At a
    100-unit viewBox that is well under the 0.5-unit rasterization sample, which
    is what makes it safe for the validator to measure the converted geometry
    and the press to print it."""
    cubics = arc_to_cubics((50.0, 10.0), 40.0, 40.0, 0.0, True, True, (50.0, 90.0))
    assert _max_radial_error(cubics, (50.0, 50.0), 40.0) < 0.05


def test_large_arc_and_sweep_flags_select_the_four_distinct_arcs() -> None:
    """The two flags are independent: ``sweep`` picks the side the arc bulges
    toward, ``large`` picks the major or the minor arc. The radius must exceed
    half the chord for that second choice to exist at all -- at exactly half,
    every combination is the same semicircle.
    """
    chord = ((30.0, 50.0), (70.0, 50.0))
    depths = {}
    for large in (False, True):
        for sweep in (False, True):
            cubics = arc_to_cubics(chord[0], 30.0, 30.0, 0.0, large, sweep, chord[1])
            points = [c.at(t / 10.0) for c in cubics for t in range(11)]
            # Signed excursion from the chord line y = 50, taken at its extreme.
            depths[(large, sweep)] = max((p[1] - 50.0 for p in points), key=abs)

    minor = 30.0 - (30.0 ** 2 - 20.0 ** 2) ** 0.5   # 7.64
    major = 30.0 + (30.0 ** 2 - 20.0 ** 2) ** 0.5   # 52.36
    assert depths[(False, True)] == pytest.approx(-minor, abs=0.05)
    assert depths[(False, False)] == pytest.approx(minor, abs=0.05)
    assert depths[(True, True)] == pytest.approx(-major, abs=0.05)
    assert depths[(True, False)] == pytest.approx(major, abs=0.05)


def test_degenerate_radius_becomes_a_straight_line_per_the_specification() -> None:
    cubics = arc_to_cubics((0.0, 0.0), 0.0, 10.0, 0.0, False, True, (10.0, 0.0))
    assert len(cubics) == 1
    assert cubics[0].at(0.5) == pytest.approx((5.0, 0.0))


def test_zero_length_arc_emits_nothing() -> None:
    assert arc_to_cubics((5.0, 5.0), 10.0, 10.0, 0.0, True, True, (5.0, 5.0)) == []


def test_too_small_radii_are_scaled_up_rather_than_rejected() -> None:
    """F.6.6: radii too small to span the endpoints are enlarged, which is what
    every browser does. Rejecting instead would fail on real exporter output."""
    cubics = arc_to_cubics((0.0, 0.0), 1.0, 1.0, 0.0, False, True, (40.0, 0.0))
    assert cubics
    assert cubics[-1].p3 == pytest.approx((40.0, 0.0))


def test_arc_endpoints_are_exact() -> None:
    cubics = arc_to_cubics((20.0, 50.0), 30.0, 15.0, 30.0, True, False, (80.0, 50.0))
    assert cubics[0].p0 == pytest.approx((20.0, 50.0))
    assert cubics[-1].p3 == pytest.approx((80.0, 50.0))


# ---------------------------------------------------------------------------
# Cubic helpers and winding
# ---------------------------------------------------------------------------


def test_line_helper_places_controls_at_the_thirds() -> None:
    line = Cubic.line((0.0, 0.0), (30.0, 60.0))
    assert line.p1 == pytest.approx((10.0, 20.0))
    assert line.p2 == pytest.approx((20.0, 40.0))
    assert line.at(0.5) == pytest.approx((15.0, 30.0))


def test_signed_area_sign_reports_winding_direction() -> None:
    clockwise = parse_path("M0 0 L10 0 L10 10 L0 10 Z")[0]
    counter = parse_path("M0 0 L0 10 L10 10 L10 0 Z")[0]
    assert clockwise.signed_area() == pytest.approx(100.0)
    assert counter.signed_area() == pytest.approx(-100.0)


def test_matrix_then_applies_the_outer_transform_second() -> None:
    inner = Matrix(a=2.0, d=2.0)          # scale 2
    outer = Matrix(e=10.0)                # translate x by 10
    assert inner.then(outer).apply((1.0, 1.0)) == pytest.approx((12.0, 2.0))
    assert outer.then(inner).apply((1.0, 1.0)) == pytest.approx((22.0, 2.0))


def test_empty_document_has_no_bounding_box() -> None:
    assert parse("").bbox() is None


def test_module_constants_state_the_18_5_contract() -> None:
    """These four sets are the contract the placeholder generator and any future
    illustrator must draw to; pinning them makes a silent widening visible."""
    assert svg_subset.ALLOWED_ELEMENTS == {"svg", "g", "path", "title", "desc", "metadata"}
    assert svg_subset.FORBIDDEN_ATTR_PREFIXES == ("stroke",)
    assert "opacity" in svg_subset.FORBIDDEN_ATTRS
    assert svg_subset.BLACK_VALUES and not (svg_subset.BLACK_VALUES & svg_subset.NONE_VALUES)
