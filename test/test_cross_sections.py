"""Intervalpunten en dwarslijnen (profielen.CrossSections)."""

import logging
import math

import pytest
import shapely.geometry
from conftest import read_rows, write_lines

from ahnprofielcreator.profielen import CrossSections
from ahnprofielcreator.profielen.constants import DEFAULT_POINT_ID

LineString = shapely.geometry.LineString


def make_cross_sections(dijk, outside, out, length=20):
    cs = CrossSections(dijk, out("punten.shp"), group_id="vak_id", outside_line_file=outside)
    cs.interval_points_to_file(interval=10)
    cs.crosssections_to_file(out("dwarslijnen.shp"), length_crosssection=length)
    return read_rows(out("punten.shp")), read_rows(out("dwarslijnen.shp"))


def test_interval_points(dijk, out):
    points, _ = make_cross_sections(dijk, None, out)
    assert len(points) == 20
    assert [p[1][DEFAULT_POINT_ID] for p in points] == list(range(1, 21))
    xs = sorted(round(g.x, 2) for g, props in points if props["vak_id"] == "A")
    assert xs == [round(0.01 + 10 * i, 2) for i in range(10)]


def test_cross_sections_perpendicular(dijk, out):
    _, lines = make_cross_sections(dijk, None, out, length=20)
    assert len(lines) == 20
    for geom, props in lines:
        assert geom.length == pytest.approx(20)
        (x0, y0), (x1, y1) = geom.coords
        assert x0 == pytest.approx(x1)  # Dijk loopt oost-west, dwarslijn noord-zuid
        assert (y0 + y1) / 2 == pytest.approx(props["mid_y"])


def test_outside_line_per_vak_orients_cross_sections(dijk, out, tmp_path):
    # Buitenlijn van A ten zuiden, van B ten noorden: dwarslijnen beginnen aan de buitenkant.
    outside = write_lines(
        tmp_path / "buiten.shp",
        [(LineString([(0, -5), (100, -5)]), {"vak_id": "A"}), (LineString([(0, 55), (100, 55)]), {"vak_id": "B"})],
    )
    _, lines = make_cross_sections(dijk, outside, out)
    for geom, props in lines:
        start_y = geom.coords[0][1]
        if props["vak_id"] == "A":
            assert start_y < props["mid_y"]
        else:
            assert start_y > props["mid_y"]


def test_outside_line_without_group_attribute(dijk, out, tmp_path):
    # Eén buitenlijn zonder vak-attribuut gaf altijd een ValueError.
    outside = write_lines(
        tmp_path / "buiten.shp", [(LineString([(0, 25), (100, 25)]), {"naam": "x"})], {"naam": "str"}
    )
    _, lines = make_cross_sections(dijk, outside, out, length=60)
    assert len(lines) == 20
    for geom, props in lines:
        # Buitenlijn ligt tussen de vakken: A begint noordelijk, B zuidelijk.
        assert (geom.coords[0][1] > props["mid_y"]) == (props["vak_id"] == "A")


def test_outside_line_missing_vak_raises(dijk, out, tmp_path):
    outside = write_lines(tmp_path / "buiten.shp", [(LineString([(0, -5), (100, -5)]), {"vak_id": "A"})])
    with pytest.raises(ValueError, match="geen lijn voor 'vak_id': B"):
        make_cross_sections(dijk, outside, out)


def test_skipped_cross_sections_keep_point_attributes(dijk, out, tmp_path):
    # Buitenlijn dekt maar een deel van elk vak: daar ontbreken dwarslijnen. De attributen van de
    # overige dwarslijnen moeten bij hun eigen punt horen (verschoven POINT_ID's waren de bug).
    outside = write_lines(
        tmp_path / "buiten.shp",
        [(LineString([(0, -5), (45, -5)]), {"vak_id": "A"}), (LineString([(55, 55), (100, 55)]), {"vak_id": "B"})],
    )
    points, lines = make_cross_sections(dijk, outside, out)
    assert 0 < len(lines) < len(points)
    by_id = {props[DEFAULT_POINT_ID]: geom for geom, props in points}
    for _, props in lines:
        point = by_id[props[DEFAULT_POINT_ID]]
        assert (point.x, point.y) == pytest.approx((props["mid_x"], props["mid_y"]))


def test_skipped_points_logged_once(dijk, out, tmp_path, caplog):
    outside = write_lines(
        tmp_path / "buiten.shp",
        [(LineString([(0, -5), (45, -5)]), {"vak_id": "A"}), (LineString([(55, 55), (100, 55)]), {"vak_id": "B"})],
    )
    logging.disable(logging.NOTSET)
    with caplog.at_level(logging.WARNING, logger="CrossSections"):
        make_cross_sections(dijk, outside, out)
    warnings = [r.getMessage() for r in caplog.records if r.name == "CrossSections" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Geen dwarslijn voor 11 van 20 punten" in warnings[0]
    assert "Per vak: B (6), A (5)." in warnings[0]  # Meeste overgeslagen punten eerst


def test_outside_line_overlapping_cross_section(dijk, out, tmp_path):
    # Buitenlijn valt deels samen met een dwarslijn (intersectie is dan een lijn, geen punt).
    outside = write_lines(
        tmp_path / "buiten.shp",
        [
            (LineString([(0, -5), (30.01, -5), (30.01, -9), (100, -9)]), {"vak_id": "A"}),
            (LineString([(0, 55), (100, 55)]), {"vak_id": "B"}),
        ],
    )
    _, lines = make_cross_sections(dijk, outside, out)
    assert len(lines) == 20


def test_outside_line_crossing_dike_skips_point(tmp_path, out):
    # Buitenlijn snijdt de dijklijn precies in een intervalpunt: daar is geen buitenkant te bepalen.
    dijk = write_lines(tmp_path / "dijk.shp", [(LineString([(0, 0), (100, 0)]), {"vak_id": "A"})])
    outside = write_lines(tmp_path / "buiten.shp", [(LineString([(30.01, -10), (30.01, 10)]), {"vak_id": "A"})])
    _, lines = make_cross_sections(dijk, outside, out, length=40)
    assert all(not math.isclose(props["mid_x"], 30.01) for _, props in lines)


def test_duplicate_group_id_raises(tmp_path, out):
    dijk = write_lines(
        tmp_path / "dijk.shp",
        [(LineString([(0, 0), (100, 0)]), {"vak_id": "A"}), (LineString([(100, 0), (200, 0)]), {"vak_id": "A"})],
    )
    with pytest.raises(ValueError, match="uniek.*: A"):
        CrossSections(dijk, out("p.shp"), group_id="vak_id").interval_points(10)


def test_missing_group_attribute_raises(dijk, out):
    with pytest.raises(ValueError, match="'vak_nr'"):
        CrossSections(dijk, out("p.shp"), group_id="vak_nr").interval_points(10)


def test_null_geometry_is_skipped(tmp_path, out):
    dijk = write_lines(
        tmp_path / "dijk.shp", [(LineString([(0, 0), (100, 0)]), {"vak_id": "A"}), (None, {"vak_id": "B"})]
    )
    outside = write_lines(
        tmp_path / "buiten.shp", [(LineString([(0, -5), (100, -5)]), {"vak_id": "A"}), (None, {"vak_id": "B"})]
    )
    points, lines = make_cross_sections(dijk, outside, out)
    assert len(points) == len(lines) == 10
