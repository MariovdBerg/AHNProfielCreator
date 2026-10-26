"""AHN-kaartbladen: veldnamen, bestandsnamen en lokaal zoeken (zonder downloads)."""

import shapely.geometry
import pytest

from ahnprofielcreator import ahn


@pytest.mark.parametrize(
    "version, model, resolution, field",
    [
        (5, "DTM", 0.5, "AHN5_05M_M"),
        (4, "dsm", 5, "AHN4_5M_R"),
        (2, "DTM", 0.5, "AHN2_05M_I"),
        (1, "DTM", 5, "AHN1_5M"),
    ],
)
def test_index_field(version, model, resolution, field):
    assert ahn.index_field(version, model, resolution) == field


@pytest.mark.parametrize(
    "url, name",
    [
        ("https://basisdata.nl/hwh-ahn/AHN5/2023_M_01CZ1.TIF", "2023_M_01CZ1.TIF"),
        ("https://basisdata.nl/hwh-ahn/AHN4/M_01CZ1.zip", "M_01CZ1.TIF"),
        ("https://x/M_01CZ1.tif.zip?a=1", "M_01CZ1.tif"),
    ],
)
def test_tif_name(url, name):
    assert ahn.tif_name(url) == name


def test_check_product_errors():
    with pytest.raises(ValueError):
        ahn.check_product("DEM", 0.5)
    with pytest.raises(ValueError):
        ahn.check_product("DTM", 1)


@pytest.fixture
def tiles(tmp_path):
    folder, downloads = tmp_path / "ahn", tmp_path / "downloads"
    folder.mkdir()
    downloads.mkdir()
    for path in (folder / "M_01CZ1_DEFLATE.tif", folder / "2023_M_01CZ2.TIF", downloads / "2023_m_01cz2.tif"):
        path.write_bytes(b"")
    return ahn.AHNTiles(str(folder), download_folder=str(downloads)), folder


def test_local_file(tiles):
    t, folder = tiles
    assert t._local_file("M_01CZ1.TIF") == str(folder / "M_01CZ1_DEFLATE.tif")  # Extra achtervoegsel mag
    assert t._local_file("2023_M_01CZ2.TIF") == str(folder / "2023_M_01CZ2.TIF")  # Map met tiffs wint
    assert t._local_file("2023_M_01CZ3.TIF") is None


def test_sheet_names_and_fallback(tiles, monkeypatch):
    t, _ = tiles
    line = shapely.geometry.LineString([(150000, 450000), (150100, 450000)])
    names = t.sheet_names([line])
    assert names
    # Geen lokale tiff en downloaden mislukt: geen bestand, geen exception.
    monkeypatch.setattr(t, "_download", lambda url: None)
    assert t.file(names[0]) is None
