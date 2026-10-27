"""AHN-kaartbladen: veldnamen, lokaal zoeken, downloaden en de AHN6-melding (zonder netwerk)."""

import io
import re
import ssl
import urllib.error
import zipfile

import fiona
import pytest
import shapely.geometry

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


# ---------- Downloaden (met een nep-urlopen, zonder netwerk) ----------


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


@pytest.fixture
def web(monkeypatch):
    """
    Nep-internet: `web.pages[url]` is bytes (antwoord) of een exception, `web.default(url)` geldt voor
    andere urls (standaard HTTP 404). `web.requests` zijn de opgevraagde urls.
    """

    class Web:
        def __init__(self):
            self.pages, self.requests = {}, []
            self.default = lambda url: urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        def urlopen(self, request):
            url = getattr(request, "full_url", request)
            self.requests.append(url)
            page = self.pages[url] if url in self.pages else self.default(url)
            if isinstance(page, Exception):
                raise page
            return FakeResponse(page)

    web = Web()
    monkeypatch.setattr(ahn, "urlopen", web.urlopen)
    return web


def zip_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buffer.getvalue()


def files(folder):
    return sorted(p.name for p in folder.iterdir())


def test_download_tif(tiles, web, tmp_path):
    t, _ = tiles
    url = "https://x/AHN5/2023_M_99AA1.TIF"
    web.pages[url] = b"tiff"
    path = t._download(url)
    assert path == str(tmp_path / "downloads" / "2023_M_99AA1.TIF")
    assert open(path, "rb").read() == b"tiff"
    assert "2023_M_99AA1.TIF" in files(tmp_path / "downloads") and not any(f.endswith(".part") for f in files(tmp_path / "downloads"))
    assert t._local_file("2023_M_99AA1.TIF") == path  # Daarna lokaal gevonden


def test_download_zip_is_unpacked(tiles, web, tmp_path):
    t, _ = tiles
    url = "https://x/AHN4/M_99AA1.zip"
    web.pages[url] = zip_bytes({"map/M_99AA1.TIF": b"tiff", "M_99AA1.tfw": b"x"})
    path = t._download(url)
    assert path == str(tmp_path / "downloads" / "M_99AA1.TIF")  # Alleen de bestandsnaam, nooit mappen uit de zip
    assert open(path, "rb").read() == b"tiff"
    assert files(tmp_path / "downloads") == ["2023_m_01cz2.tif", "M_99AA1.TIF"]  # Geen zip of .part over


@pytest.mark.parametrize(
    "page, level_text",
    [
        (zip_bytes({"a.tif": b"1", "b.tif": b"2"}), "verwacht één tiff"),
        (b"geen zip", "Downloaden mislukt"),
        (urllib.error.URLError("geen netwerk"), "geen netwerk"),
        (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "Niet beschikbaar"),
    ],
)
def test_download_failures(tiles, web, tmp_path, logs, page, level_text):
    t, _ = tiles
    url = "https://x/AHN4/M_99AA1.zip"
    web.pages[url] = page
    assert t._download(url) is None
    assert any(level_text in m for m in logs())
    assert files(tmp_path / "downloads") == ["2023_m_01cz2.tif"]  # Niets achtergebleven


def test_fallback_to_ahn4(tiles, web, tmp_path, logs):
    t, _ = tiles
    url5, url4 = "https://x/AHN5/2023_M_99AA1.TIF", "https://x/AHN4/M_99AA1.zip"
    t.sheets["99AA1"] = (None, {5: url5, 4: url4})
    web.pages[url4] = zip_bytes({"M_99AA1.TIF": b"tiff"})
    assert t.file("99AA1") == str(tmp_path / "downloads" / "M_99AA1.TIF")
    assert web.requests == [url5, url4]
    assert any("geen AHN5, AHN4" in m for m in logs())
    t.file("99AA1")
    assert len(web.requests) == 2  # Tweede keer uit het geheugen


def test_local_file_needs_no_download(tiles, web):
    t, folder = tiles
    t.sheets["01CZ1"] = (None, {5: "https://x/AHN5/2023_M_01CZ1.TIF", 4: "https://x/AHN4/M_01CZ1.zip"})
    t.sheets["01CZ2"] = (None, {5: "https://x/AHN5/2023_M_01CZ2.TIF"})
    assert t.file("01CZ2") == str(folder / "2023_M_01CZ2.TIF")
    assert web.requests == []
    # Geen AHN5 lokaal of online, wel AHN4 lokaal (M_01CZ1_DEFLATE.tif): alleen AHN5 wordt geprobeerd.
    assert t.file("01CZ1") == str(folder / "M_01CZ1_DEFLATE.tif")
    assert web.requests == ["https://x/AHN5/2023_M_01CZ1.TIF"]


def test_no_tile_available(tiles, web, logs):
    t, _ = tiles
    t.sheets["99AA1"] = (None, {5: "https://x/a.TIF", 4: "https://x/b.zip"})
    assert t.file("99AA1") is None
    assert any("geen AHN DTM 0.5 m beschikbaar" in m for m in logs())


def test_urlopen_falls_back_to_windows_certificates(monkeypatch):
    calls = []

    def fake(request, timeout, context=None):
        calls.append(context)
        if context is not None:
            raise urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        return "ok"

    monkeypatch.setattr(ahn.urllib.request, "urlopen", fake)
    monkeypatch.setattr(ahn, "_CERTIFI_CONTEXT", object())
    assert ahn.urlopen("https://x") == "ok"
    assert calls[1] is None  # Tweede poging zonder certifi


# ---------- AHN6-melding ----------


@pytest.fixture
def ahn6_line():
    """Lijn van 1,5 km midden in een gebied uit de AHN6-bladwijzer (raakt 2 of 3 km-bladen)."""
    with fiona.open(ahn.AHN6_INDEX, layer=ahn.AHN6_LAYER) as source:
        row = next(iter(source))
        left, bottom = int(row.properties["left"]), int(row.properties["bottom"])
    return shapely.geometry.LineString([(left + 100, bottom + 500), (left + 1600, bottom + 500)])


def test_ahn6_available(tiles, web, logs, ahn6_line):
    t, _ = tiles
    web.default = lambda url: b""  # Elke url bestaat
    t.report_ahn6([ahn6_line])
    assert web.requests
    assert all(re.fullmatch(r"https://basisdata\.nl/hwh-ahn/AHN6/02a_DTM_50cm/AHN6_20\d\d_M_\d{6}_\d{6}\.TIF", u) for u in web.requests)
    n = len(web.requests)
    assert any(f"beschikbaar voor {n} van {n} km-bladen" in m for m in logs())


def test_ahn6_not_yet_available(tiles, web, logs, ahn6_line):
    t, _ = tiles
    t.report_ahn6([ahn6_line])
    assert len(web.requests) % 2 == 0  # Per blad twee schrijfwijzen geprobeerd
    assert any("nog niet beschikbaar" in m for m in logs())


def test_ahn6_no_network(tiles, web, logs, ahn6_line):
    t, _ = tiles
    web.default = lambda url: urllib.error.URLError("geen netwerk")
    t.report_ahn6([ahn6_line])
    assert any("niet te controleren" in m for m in logs())


def test_ahn6_skipped_outside_index_or_for_ahn6(tiles, web, logs, ahn6_line):
    t, _ = tiles
    t.report_ahn6([shapely.geometry.LineString([(0, 0), (10, 0)])])  # Buiten de bladwijzer
    t.version = 6
    t.report_ahn6([ahn6_line])
    assert web.requests == [] and logs() == []
