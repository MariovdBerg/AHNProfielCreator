"""AHN-hoogtes langs dwarslijnen (profielen.CrossSectionHeights) met een kleine nep-GeoTIFF."""

import logging

import pytest
import rasterio
import shapely.geometry
from conftest import FakeTiles, read_rows, write_lines

from ahnprofielcreator.profielen import CrossSectionHeights
from ahnprofielcreator.profielen.constants import NODATA


def heights_for(tmp_path, tif, line, stepsize=1.0):
    cross = write_lines(
        tmp_path / "cross.shp", [(line, {"vak_id": "A", "CROSS_ID": 1})], {"vak_id": "str", "CROSS_ID": "int"}
    )
    ch = CrossSectionHeights(cross, group_id="vak_id", ahn_tiles=FakeTiles(tif, (0, 0, 100, 100)), ahn_stepsize=stepsize)
    ch.crosssections_height_to_file(str(tmp_path / "ahn.shp"), "ahn")
    return read_rows(tmp_path / "ahn.shp")[0][0]


def test_heights_from_raster(tmp_path, tif):
    line = heights_for(tmp_path, tif, shapely.geometry.LineString([(10.5, 50), (20.5, 50)]))
    z = [c[2] for c in line.coords]
    assert z == pytest.approx([10.5 + i for i in range(11)])


def test_nodata_and_outside_raster_kept_at_fixed_step(tmp_path, tif):
    # Van x=55.5 tot x=110.5: gat bij 60..70 en buiten het raster vanaf 100.
    line = heights_for(tmp_path, tif, shapely.geometry.LineString([(55.5, 50), (110.5, 50)]))
    coords = line.coords
    assert len(coords) == 56  # Punten zonder hoogte blijven staan (vaste stapgrootte)
    by_x = {round(c[0], 1): c[2] for c in coords}
    assert by_x[55.5] == pytest.approx(55.5)
    assert by_x[65.5] == NODATA
    assert by_x[75.5] == pytest.approx(75.5)
    assert by_x[105.5] == NODATA


def test_line_outside_sheet_gets_nodata(tmp_path, tif):
    line = heights_for(tmp_path, tif, shapely.geometry.LineString([(200, 50), (210, 50)]))
    assert all(c[2] == NODATA for c in line.coords)


def test_raster_window_is_small(tif):
    ch = CrossSectionHeights("unused.shp")
    with rasterio.open(tif) as dataset:
        heights, window = ch.read_raster_window(dataset, [shapely.geometry.LineString([(10, 10), (20, 12)])])
    assert heights.shape == (5, 13)
    assert (window.col_off, window.row_off) == (9, 87)


def warnings_for(tmp_path, tif, logs, line):
    cross = write_lines(
        tmp_path / "cross.shp", [(line, {"vak_id": "A", "CROSS_ID": 1})], {"vak_id": "str", "CROSS_ID": "int"}
    )
    ch = CrossSectionHeights(
        cross, group_id="vak_id", ahn_tiles=FakeTiles(tif, (0, 0, 100, 100)), ahn_stepsize=1.0,
        logger=logging.getLogger("ahnprofielcreator"),
    )
    ch.crosssections_height_to_file(str(tmp_path / "ahn.shp"), "ahn")
    return [m for m in logs() if "geen hoogte" in m]


def test_warning_for_sheet_without_heights(tmp_path, tif, logs):
    # Helemaal in het gat van de dekking (x = 60..70): 'blad' zonder hoogtes.
    messages = warnings_for(tmp_path, tif, logs, shapely.geometry.LineString([(61, 50), (69, 50)]))
    assert len(messages) == 1
    assert "Blad blad: 100%" in messages[0]


def test_no_warning_for_partly_missing_heights(tmp_path, tif, logs):
    # 10 van de 60 punten binnen het raster in het gat; de 21 punten buiten het raster tellen niet mee.
    line = shapely.geometry.LineString([(40.5, 50), (120.5, 50)])
    assert warnings_for(tmp_path, tif, logs, line) == []
