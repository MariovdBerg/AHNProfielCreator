"""
Regressietest: de volledige keten op test/test_input moet de referentie in test/test_output opleveren.

Stap 1 en 2 (punten en dwarslijnen) draaien altijd. Stap 3 (AHN-hoogtes) heeft de AHN5-tiffs nodig en
draait alleen met ``pytest -m ahn``; de map met tiffs komt uit ``AHN_TEST_DIR`` (standaard de HKV-map op P:).
Er wordt nooit gedownload: ontbreekt een blad, dan wordt de test overgeslagen.

Referentie opnieuw maken (na een bedoelde wijziging): zie "Tests" in de README.
"""

import math
import os
from pathlib import Path

import fiona
import pytest
import shapely.geometry

from ahnprofielcreator import ahn
from ahnprofielcreator.profielen import CrossSectionHeights, CrossSections

ROOT = Path(__file__).parent
INPUT_LINE = str(ROOT / "test_input" / "WAD_GEKB_basisanalyse_profielen_v2.shp")
INPUT_OUTSIDE = str(ROOT / "test_input" / "WAD_GEKB_basisanalyse_profielen_v2_outsideline.shp")
REFERENCE = ROOT / "test_output" / "output_per_vak_10m"
AHN_DIR = os.environ.get("AHN_TEST_DIR", r"P:\OpenData\AHN\AHN5\DTM05")

# Instellingen waarmee de referentie gemaakt is.
GROUP_ID = "vak_id"
INTERVAL = 10.0
CROSS_LENGTH = 100.0
AHN_STEPSIZE = 0.5


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
    assert len(coords) == len(ref_coords), where
    for c, r in zip(coords, ref_coords):
        assert c[:2] == pytest.approx(r[:2], abs=1e-6), where
        if len(c) > 2:
            # Hoogte: nodata moet nodata blijven, anders tot op de mm gelijk.
            assert (c[2] == r[2]) or (not math.isnan(c[2]) and c[2] == pytest.approx(r[2], abs=1e-3)), where


@pytest.fixture(scope="module")
def cross_sections(tmp_path_factory):
    out = tmp_path_factory.mktemp("referentie")
    cs = CrossSections(
        INPUT_LINE, str(out / "cross_points.shp"), group_id=GROUP_ID, outside_line_file=INPUT_OUTSIDE
    )
    cs.interval_points_to_file(interval=INTERVAL)
    cs.crosssections_to_file(str(out / "cross_lines.shp"), length_crosssection=CROSS_LENGTH)
    return out


def test_interval_points_match_reference(cross_sections):
    assert_same(cross_sections / "cross_points.shp", REFERENCE / "cross_points.shp")


def test_cross_lines_match_reference(cross_sections):
    assert_same(cross_sections / "cross_lines.shp", REFERENCE / "cross_lines.shp")


def local_tiles():
    """AHN5-bladen langs het traject uit `AHN_DIR`, zonder downloads; None als er een ontbreekt."""
    if not os.path.isdir(AHN_DIR):
        return None
    tiles = ahn.AHNTiles(AHN_DIR, download_folder=AHN_DIR)
    with fiona.open(REFERENCE / "cross_lines.shp") as source:
        lines = [shapely.geometry.shape(row.geometry) for row in source]
    for name in tiles.sheet_names(lines):
        url = tiles.sheets[name][1].get(5)
        path = url and tiles._local_file(ahn.tif_name(url))
        if not path:
            return None
        tiles._files[name] = path  # Geen downloads of terugval op AHN4
    tiles.report_ahn6 = lambda lines: None  # Geen netwerk
    return tiles


@pytest.mark.ahn
def test_ahn_profiles_match_reference(cross_sections):
    tiles = local_tiles()
    if tiles is None:
        pytest.skip(f"AHN5-tiffs langs het traject niet (allemaal) gevonden in '{AHN_DIR}' (zet AHN_TEST_DIR)")
    ch = CrossSectionHeights(
        str(cross_sections / "cross_lines.shp"), group_id=GROUP_ID, ahn_tiles=tiles, ahn_stepsize=AHN_STEPSIZE
    )
    ch.crosssections_height_to_file(str(cross_sections / "cross_lines_ahn.shp"), "ahn")
    assert_same(cross_sections / "cross_lines_ahn.shp", REFERENCE / "cross_lines_ahn.shp")
