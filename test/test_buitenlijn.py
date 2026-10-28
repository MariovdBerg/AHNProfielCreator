"""Afgeleide buitenlijn (buitenlijn.py, profielen_per_vak.make_outside_line) en de CRS-controle."""

import fiona
import pytest
import shapely.geometry
from conftest import read_rows, write_lines

from ahnprofielcreator import buitenlijn
from ahnprofielcreator.profielen import CrossSections
from ahnprofielcreator.profielen_per_vak import Settings, check_crs, make_outside_line

LineString = shapely.geometry.LineString
TEST_INPUT = "test/test_input/duin/duin.shp"


# ---------- offset_geometry ----------


def test_offset_left_and_right():
    line = LineString([(0, 0), (100, 0)])  # Getekend naar het oosten: links = noord
    (left,) = buitenlijn.offset_geometry(line, 5)
    (right,) = buitenlijn.offset_geometry(line, -5)
    assert {y for _, y in left.coords} == {5}
    assert {y for _, y in right.coords} == {-5}


def test_offset_multilinestring_and_3d():
    geom = shapely.geometry.MultiLineString([[(0, 0, 1), (10, 0, 1)], [(20, 0, 2), (30, 0, 2)]])
    lines = buitenlijn.offset_geometry(geom, 2)
    assert len(lines) == 2
    assert all(not line.has_z for line in lines)  # Hoogte wordt weggelaten


@pytest.mark.parametrize("geom", [None, LineString()])
def test_offset_empty(geom):
    assert buitenlijn.offset_geometry(geom, 5) == []


def test_offset_rejects_points():
    with pytest.raises(TypeError, match="Point"):
        buitenlijn.offset_geometry(shapely.geometry.Point(0, 0), 5)


# ---------- offset_lines_to_file ----------


def test_offset_lines_to_file_keeps_attributes(dijk, out):
    count = buitenlijn.offset_lines_to_file(dijk, out("buiten.shp"), 5, "rechts")
    assert count == 2
    rows = read_rows(out("buiten.shp"))
    assert [props["vak_id"] for _, props in rows] == ["A", "B"]
    assert rows[0][0].bounds[1] == pytest.approx(-5)  # Rechts van een lijn naar het oosten = zuid
    with fiona.open(out("buiten.shp")) as source:
        assert source.crs.to_epsg() == 28992


def test_offset_lines_to_file_skips_null_geometry(tmp_path, out):
    dijk = write_lines(
        tmp_path / "dijk.shp", [(LineString([(0, 0), (100, 0)]), {"vak_id": "A"}), (None, {"vak_id": "B"})]
    )
    assert buitenlijn.offset_lines_to_file(dijk, out("buiten.shp"), 5) == 1


@pytest.mark.parametrize("afstand, zijde, message", [(5, "boven", "links' of 'rechts"), (0, "links", "groter dan 0")])
def test_offset_lines_to_file_invalid(dijk, out, afstand, zijde, message):
    with pytest.raises(ValueError, match=message):
        buitenlijn.offset_lines_to_file(dijk, out("buiten.shp"), afstand, zijde)


# ---------- make_outside_line ----------


def settings(tmp_path, dijk, **kwargs):
    return Settings(cur_dir=str(tmp_path / "uit"), input_line=dijk, ahn_file_loc="", cross_length=20, **kwargs)


def test_make_outside_line_writes_derived_line(tmp_path, dijk):
    s = settings(tmp_path, dijk, outside_offset=5, outside_side="links")
    path = make_outside_line(s)
    assert path == s.outside_line
    assert path.endswith("buitenlijn_offset.shp")
    assert len(read_rows(path)) == 2


def test_derived_line_orients_cross_sections(tmp_path, dijk, out):
    # Buitenlijn links (noord) van de dijkvakken: elke dwarslijn begint aan de noordkant.
    s = settings(tmp_path, dijk, outside_offset=5, outside_side="links")
    make_outside_line(s)
    cs = CrossSections(dijk, out("p.shp"), group_id="vak_id", outside_line_file=s.outside_line)
    cs.interval_points_to_file(interval=10)
    cs.crosssections_to_file(out("d.shp"), length_crosssection=20)
    rows = read_rows(out("d.shp"))
    assert len(rows) == 20
    assert all(geom.coords[0][1] > props["mid_y"] for geom, props in rows)


def test_make_outside_line_not_needed(tmp_path, dijk, logs):
    assert make_outside_line(settings(tmp_path, dijk)) is None  # Geen afstand
    s = settings(tmp_path, dijk, outside_offset=5, input_line_outside=dijk)
    assert make_outside_line(s) is None  # Buitenlijn opgegeven: afstand genegeerd, met waarschuwing
    assert any("genegeerd" in m for m in logs())


def test_make_outside_line_too_far(tmp_path, dijk):
    with pytest.raises(ValueError, match="halve"):
        make_outside_line(settings(tmp_path, dijk, outside_offset=10))  # cross_length 20 -> max < 10


# ---------- check_crs ----------


def test_check_crs_accepts_rd(dijk, tmp_path):
    check_crs(dijk, "Dijkvakken")
    check_crs(TEST_INPUT, "Dijkvakken")  # .prj in ESRI-notatie ("RD_New")
    check_crs(write_lines(tmp_path / "nap.shp", [], crs="EPSG:7415"), "Dijkvakken")  # RD + NAP


def test_check_crs_rejects_other_crs(tmp_path):
    path = write_lines(tmp_path / "wgs.shp", [(LineString([(5, 53), (5.01, 53)]), {"vak_id": "A"})], crs="EPSG:4326")
    with pytest.raises(ValueError, match="EPSG:4326, niet in RD New"):
        check_crs(path, "Dijkvakken")


def test_check_crs_rejects_missing_prj(tmp_path):
    path = write_lines(tmp_path / "geen.shp", [(LineString([(0, 0), (1, 0)]), {"vak_id": "A"})], crs=None)
    assert not (tmp_path / "geen.prj").exists()
    with pytest.raises(ValueError, match="geen coördinatenstelsel"):
        check_crs(path, "Dijkvakken")


def test_make_outside_line_checks_crs(tmp_path):
    path = write_lines(tmp_path / "wgs.shp", [(LineString([(5, 53), (5.01, 53)]), {"vak_id": "A"})], crs="EPSG:4326")
    with pytest.raises(ValueError, match="RD New"):
        make_outside_line(settings(tmp_path, path, outside_offset=5))
