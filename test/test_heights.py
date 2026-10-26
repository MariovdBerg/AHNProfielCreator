"""AHN-hoogtes langs dwarslijnen (profielen.CrossSectionHeights) met een kleine nep-GeoTIFF."""

import numpy as np
import pytest
import rasterio
import rasterio.transform
import shapely.geometry
from conftest import read_rows, write_lines

from ahnprofielcreator.profielen import CrossSectionHeights
from ahnprofielcreator.profielen.constants import NODATA

NODATA_TIF = -9999.0


class FakeTiles:
    """Eén kaartblad met een lokale tiff, zonder bladwijzer of downloads."""

    version = 5
    product = "DTM 0.5 m"

    def __init__(self, path, extent):
        self.path = path
        self.extent = shapely.geometry.box(*extent)

    def sheet_names(self, lines):
        return ["blad"] if self.extent.intersects(shapely.geometry.MultiLineString(lines)) else []

    def file(self, name):
        return self.path

    def report_ahn6(self, lines):
        pass


@pytest.fixture
def tif(tmp_path):
    """Raster van 0..100 x 0..100 m met cellen van 1 m; hoogte = x-coördinaat van de cel (0.5, 1.5, ...)."""
    path = tmp_path / "ahn.tif"
    data = np.tile(np.arange(100, dtype="float32") + 0.5, (100, 1))
    data[:, 60:70] = NODATA_TIF  # Gat in de dekking
    transform = rasterio.transform.from_origin(0, 100, 1, 1)
    with rasterio.open(
        path, "w", driver="GTiff", width=100, height=100, count=1, dtype="float32",
        crs="EPSG:28992", transform=transform, nodata=NODATA_TIF,
    ) as dst:
        dst.write(data, 1)
    return str(path)


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
