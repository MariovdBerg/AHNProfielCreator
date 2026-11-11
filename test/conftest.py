"""Gedeelde hulpmiddelen voor de tests: kleine synthetische shapefiles in RD New en een nep-AHN-tiff."""

import logging
import os

import fiona
import numpy as np
import pytest
import rasterio
import rasterio.transform
import shapely.geometry

CRS = "EPSG:28992"
NODATA_TIF = -9999.0
TRAJECT = "44-1"  # Traject van de synthetische dijkvakken


@pytest.fixture(autouse=True)
def quiet_logging():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture
def logs(caplog):
    """Logberichten (vanaf INFO) van de logger 'ahnprofielcreator' als lijst met teksten."""
    logging.disable(logging.NOTSET)
    caplog.set_level(logging.INFO, logger="ahnprofielcreator")
    # Alleen eigen berichten, niet die van GDAL/fiona/rasterio.
    return lambda: [r.getMessage() for r in caplog.records if r.name.startswith("ahnprofielcreator")]


def write_lines(path, rows, properties=None, crs=CRS):
    """
    Schrijf (geometrie, attributen)-paren als lijnen-shapefile; geometrie None = null-geometrie.

    Zonder `properties` krijgt het bestand de attributen vak_id en traject (standaard traject TRAJECT).
    """
    if properties is None:
        properties = {"vak_id": "str", "traject": "str"}
        rows = [(geom, {"traject": TRAJECT, **props}) for geom, props in rows]
    schema = {"geometry": "LineString", "properties": properties}
    with fiona.open(path, "w", "ESRI Shapefile", schema, crs=crs) as sink:
        for geom, props in rows:
            sink.write(
                {"geometry": None if geom is None else shapely.geometry.mapping(geom), "properties": props}
            )
    return str(path)


@pytest.fixture
def dijk(tmp_path):
    """Twee rechte dijkvakken van 100 m: A langs y=0 en B langs y=50."""
    return write_lines(
        tmp_path / "dijk.shp",
        [
            (shapely.geometry.LineString([(0, 0), (100, 0)]), {"vak_id": "A"}),
            (shapely.geometry.LineString([(0, 50), (100, 50)]), {"vak_id": "B"}),
        ],
    )


def read_rows(path):
    with fiona.open(path) as source:
        return [(shapely.geometry.shape(row.geometry), dict(row.properties)) for row in source]


@pytest.fixture
def out(tmp_path):
    """Pad in de tijdelijke map."""
    return lambda name: os.path.join(tmp_path, name)


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
        crs=CRS, transform=transform, nodata=NODATA_TIF,
    ) as dst:
        dst.write(data, 1)
    return str(path)
