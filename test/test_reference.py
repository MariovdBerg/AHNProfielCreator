"""
Regressietest: de volledige keten per testgeval (zie referentie.py) moet de referentie in test/test_output opleveren.

Stap 1 en 2 (buitenlijn, punten en dwarslijnen) draaien altijd, net als stap 3 (AHN-hoogtes) voor het demotraject:
daarvan komt de AHN-strook mee met het pakket. Stap 3 voor de andere gevallen heeft de AHN5-tiffs nodig en
draait alleen met ``pytest -m ahn``; de map met tiffs komt uit ``AHN_TEST_DIR`` (standaard ``ahn_data`` in de ontwikkelmap).
Er wordt nooit gedownload: ontbreekt een blad, dan wordt de test overgeslagen.

Referentie opnieuw maken (na een bedoelde wijziging): ``python test/referentie.py``.
"""

import math

import fiona
import pytest
import referentie

MET_AHN_TEST_DIR = [name for name in referentie.CASES if name != "demo"]  # Hoogtes uit AHN_TEST_DIR


def read(path):
    with fiona.open(path) as source:
        return dict(source.schema["properties"]), [(row.geometry.coordinates, dict(row.properties)) for row in source]


def assert_same(path, reference):
    schema, rows = read(path)
    ref_schema, ref_rows = read(reference)
    assert schema == ref_schema
    assert len(rows) == len(ref_rows)
    for i, ((coords, props), (ref_coords, ref_props)) in enumerate(zip(rows, ref_rows)):
        assert props.keys() == ref_props.keys(), f"feature {i}"
        for key, value in props.items():
            if isinstance(value, float):
                assert value == pytest.approx(ref_props[key], abs=1e-6), f"feature {i}, {key}"
            else:
                assert value == ref_props[key], f"feature {i}, {key}"
        assert_coords(coords, ref_coords, f"feature {i}")


def assert_coords(coords, ref_coords, where):
    if isinstance(coords[0], (int, float)):
        coords, ref_coords = [coords], [ref_coords]
    if coords and isinstance(coords[0][0], (list, tuple)):  # MultiLineString
        assert len(coords) == len(ref_coords), where
        for part, ref_part in zip(coords, ref_coords):
            assert_coords(part, ref_part, where)
        return
    assert len(coords) == len(ref_coords), where
    for c, r in zip(coords, ref_coords):
        assert c[:2] == pytest.approx(r[:2], abs=1e-6), where
        if len(c) > 2:
            # Hoogte: nodata moet nodata blijven, anders tot op de mm gelijk.
            assert (c[2] == r[2]) or (not math.isnan(c[2]) and c[2] == pytest.approx(r[2], abs=1e-3)), where


@pytest.fixture(scope="module")
def cross_sections(tmp_path_factory):
    """Stap 1 en 2 per geval, één keer per testsessie; geeft de outputmap."""
    made = {}

    def make(name):
        if name not in made:
            out = tmp_path_factory.mktemp(name)
            referentie.cross_sections(name, out)
            made[name] = out
        return made[name]

    return make


@pytest.mark.parametrize("name", list(referentie.CASES))
@pytest.mark.parametrize("file", ["cross_points.shp", "cross_lines.shp"])
def test_cross_sections_match_reference(cross_sections, name, file):
    assert_same(cross_sections(name) / file, referentie.OUTPUT / name / file)


@pytest.mark.parametrize("name", [n for n, case in referentie.CASES.items() if not case.get("outside")])
def test_derived_outside_line_matches_reference(cross_sections, name):
    assert_same(cross_sections(name) / "buitenlijn.shp", referentie.OUTPUT / name / "buitenlijn.shp")


def check_heights(cross_sections, name):
    out = cross_sections(name)
    tiles = referentie.local_tiles(referentie.ahn_dir(name), out / "cross_lines.shp")
    if tiles is None:
        pytest.skip(f"AHN5-tiffs voor '{name}' niet (allemaal) gevonden in '{referentie.ahn_dir(name)}' (zet AHN_TEST_DIR)")
    referentie.heights(name, out, tiles)
    assert_same(out / "cross_lines_ahn.shp", referentie.OUTPUT / name / "cross_lines_ahn.shp")


def test_demo_heights_match_reference(cross_sections):
    check_heights(cross_sections, "demo")


@pytest.mark.ahn
@pytest.mark.parametrize("name", MET_AHN_TEST_DIR)
def test_ahn_profiles_match_reference(cross_sections, name):
    check_heights(cross_sections, name)
