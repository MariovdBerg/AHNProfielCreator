"""
Wat elk testgeval uit test/test_input laat zien (zie de README daar), op de referentie-uitvoer.

test_reference.py controleert dat de berekening die referentie oplevert; deze tests leggen vast waarom het
geval erin zit, zodat een nieuwe referentie het bedoelde gedrag niet ongemerkt verliest.
"""

import collections

import numpy as np
import pytest
import referentie
import shapely.geometry
from conftest import read_rows

from ahnprofielcreator import ahn
from ahnprofielcreator.profielen.constants import NODATA


def rows(name, file):
    return read_rows(referentie.OUTPUT / name / file)


def per_vak(items):
    return collections.Counter(props["vak_id"] for _, props in items)


def skipped_points(name):
    """Punten zonder dwarslijn (de dwarslijn snijdt de buitenlijn niet) per vak."""
    mids = {(round(p["mid_x"], 2), round(p["mid_y"], 2)) for _, p in rows(name, "cross_lines.shp")}
    points = rows(name, "cross_points.shp")
    return per_vak([(g, p) for g, p in points if (round(g.x, 2), round(g.y, 2)) not in mids])


def test_ringen_orientation_decides_side_of_outside_line():
    # 84-1 met de klok mee: links = buiten de ring. 18-1 tegen de klok in: links = binnen de ring.
    rings = {p["vak_id"]: g for g, p in read_rows(referentie.CASES["ringen"]["line"])}
    assert all(ring.is_closed for ring in rings.values())
    assert not shapely.LinearRing(rings["84-1"].coords).is_ccw
    assert shapely.LinearRing(rings["18-1"].coords).is_ccw
    outside = {p["vak_id"]: g for g, p in rows("ringen", "buitenlijn.shp")}
    assert not shapely.geometry.Polygon(rings["84-1"].coords).contains(outside["84-1"])
    assert shapely.geometry.Polygon(rings["18-1"].coords).contains(outside["18-1"])
    assert set(per_vak(rows("ringen", "cross_lines.shp"))) == {"84-1", "18-1"}


def test_multipart_points_on_every_part():
    (geom, _), = read_rows(referentie.CASES["multipart"]["line"])
    assert len(geom.geoms) == 3
    points = [g for g, _ in rows("multipart", "cross_points.shp")]
    for part in geom.geoms:
        assert any(part.distance(p) < 1e-6 for p in points)
    assert len(rows("multipart", "cross_lines.shp")) == len(points)


def test_short_vakken_get_at_least_one_profile():
    lengths = {p["vak_id"]: g.length for g, p in read_rows(referentie.CASES["korte_vakken"]["line"])}
    assert lengths["228_03"] == pytest.approx(5, abs=0.1)  # Korter dan het interval van 10 m
    assert per_vak(rows("korte_vakken", "cross_points.shp")) == {"228_01": 6, "228_02": 2, "228_03": 1}
    assert per_vak(rows("korte_vakken", "cross_lines.shp")) == {"228_01": 6, "228_02": 2, "228_03": 1}


def test_short_vakken_mostly_without_heights():
    # Ligt in bebouwd gebied: de meeste punten hebben geen hoogte (waarschuwing voor blad 69AN2).
    z = np.array([c[2] for g, _ in rows("korte_vakken", "cross_lines_ahn.shp") for c in g.coords])
    assert np.mean(z == NODATA) > 0.5


def test_haarspeldbocht_skips_points_in_the_bend():
    skipped = skipped_points("haarspeldbocht")
    assert skipped["204a_01"] == 0
    assert skipped["204a_02"] > 0  # De bocht ligt in vak 02


def test_no_skipped_points_on_straight_dike():
    assert sum(skipped_points("demo").values()) == 0


def test_bladhoek_water_touches_four_sheets(tmp_path):
    tiles = ahn.AHNTiles(str(tmp_path))  # Alleen de bladwijzer; er wordt niets gezocht of gedownload
    lines = [g for g, _ in rows("bladhoek_water", "cross_lines.shp")]
    assert len(tiles.sheet_names(lines)) == 4


def test_bladhoek_water_has_no_heights_on_water_side():
    # Water (geen AHN) aan de buitenzijde: het begin van de dwarslijnen mist vaker een hoogte dan het eind.
    begin, end = [], []
    for geom, _ in rows("bladhoek_water", "cross_lines_ahn.shp"):
        z = np.array([c[2] for c in geom.coords])
        begin.append(np.mean(z[: len(z) // 2] == NODATA))
        end.append(np.mean(z[len(z) // 2 :] == NODATA))
    assert np.mean(begin) > 2 * np.mean(end)


def side(vak, geom):
    """+1 als de dwarslijn links van de (demo)dijkvak begint, -1 als rechts."""
    mid = shapely.geometry.Point(geom.interpolate(0.5, normalized=True))
    s = vak.project(mid)
    a, b = vak.interpolate(max(s - 1, 0)), vak.interpolate(min(s + 1, vak.length))
    start = geom.coords[0]
    cross = (b.x - a.x) * (start[1] - mid.y) - (b.y - a.y) * (start[0] - mid.x)
    return 1 if cross > 0 else -1


def test_richting_wisselend_flips_outside_side_of_reversed_vakken():
    # Zelfde vakken als de demo (buitenzijde links), maar vak 02, 04 en 06 omgekeerd getekend.
    demo = {p["vak_id"][-2:]: g for g, p in read_rows(referentie.CASES["demo"]["line"])}
    sides = collections.defaultdict(set)
    for geom, props in rows("richting_wisselend", "cross_lines.shp"):
        nr = props["vak_id"][-2:]
        sides[nr].add(side(demo[nr], geom))
    assert dict(sides) == {"01": {1}, "02": {-1}, "03": {1}, "04": {-1}, "05": {1}, "06": {-1}}


def test_demo_cross_lines_start_outside():
    demo = {p["vak_id"]: g for g, p in read_rows(referentie.CASES["demo"]["line"])}
    assert {side(demo[p["vak_id"]], g) for g, p in rows("demo", "cross_lines.shp")} == {1}


def test_duin_crest_not_on_the_line():
    # Duin: het hoogste punt ligt meestal niet bij de lijn en het profiel is breder dan 100 m.
    offsets = []
    for geom, props in rows("duin", "cross_lines_ahn.shp"):
        assert geom.length == pytest.approx(400, abs=0.5)
        z = np.array([c[2] for c in geom.coords])
        z[z == NODATA] = np.nan
        top = shapely.geometry.Point(geom.coords[int(np.nanargmax(z))][:2])
        offsets.append(top.distance(shapely.geometry.Point(props["mid_x"], props["mid_y"])))
    assert np.median(offsets) > 10
