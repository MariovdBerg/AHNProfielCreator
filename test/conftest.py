"""Gedeelde hulpmiddelen voor de tests: kleine synthetische shapefiles in RD New."""

import logging
import os

import fiona
import pytest
import shapely.geometry

CRS = "EPSG:28992"


@pytest.fixture(autouse=True)
def quiet_logging():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def write_lines(path, rows, properties=None):
    """Schrijf (geometrie, attributen)-paren als lijnen-shapefile; geometrie None = null-geometrie."""
    properties = properties or {"vak_id": "str"}
    schema = {"geometry": "LineString", "properties": properties}
    with fiona.open(path, "w", "ESRI Shapefile", schema, crs=CRS) as sink:
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
