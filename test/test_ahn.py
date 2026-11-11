"""AHN-kaartbladen: veldnamen, lokaal zoeken, downloaden en de AHN6-melding (zonder netwerk)."""

import io
import os
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


# ---------- Opbouw van de AHN-map ----------


def make_files(root, *paths):
    """Maak lege bestanden (en de mappen ervoor) onder `root`."""
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"")
    return root


def test_product_folder(tmp_path):
    assert ahn.product_dir_name("dsm", 5) == "DSM5"
    assert ahn.product_folder(str(tmp_path), 5, "DTM", 0.5) == str(tmp_path / "AHN5" / "DTM05")
    make_files(tmp_path, "ahn4/dtm05/M_01CZ1.TIF")  # Hoofd-/kleine letters maken niet uit
    assert ahn.product_folder(str(tmp_path), 4, "DTM", 0.5) == str(tmp_path / "ahn4" / "dtm05")


def test_check_folder_ok(tmp_path):
    # Wat verder in de AHN-map staat (losse tiffs, andere mappen), maakt niet uit.
    make_files(tmp_path, "M_01CZ1.TIF", "oud/x.TIF", "AHN7/y.TIF", "ahn5/dtm05/blok1/2023_M_01CZ1.TIF")
    assert ahn.check_folder(str(tmp_path), 5, "DTM", 0.5) is None


def test_check_folder_warns_without_product_folder(tmp_path):
    make_files(tmp_path, "AHN5/DSM05/2023_R_01CZ1.TIF")
    warning = ahn.check_folder(str(tmp_path), 5, "DTM", 0.5, "AHN-map (ahn_file_loc)")
    assert warning.startswith(f"AHN-map (ahn_file_loc) '{tmp_path}' heeft geen map '{os.path.join('AHN5', 'DTM05')}'")
    assert "waarschijnlijk" not in warning


@pytest.mark.parametrize("sub", ["AHN5/DTM05", "AHN5", "ahn4/dtm05", "AHN5/DSM5"])
def test_check_folder_error_for_version_or_product_folder(tmp_path, sub):
    # ahn_file_loc wijst naar een productmap (of versiemap) zelf: fout, anders worden alle bladen gedownload.
    make_files(tmp_path, f"{sub}/2023_M_01CZ1.TIF")
    with pytest.raises(ValueError, match="lijkt zelf een versie- of productmap") as error:
        ahn.check_folder(str(tmp_path / sub), 5, "DTM", 0.5, "AHN-map (ahn_file_loc)")
    message = str(error.value)
    assert message.startswith("AHN-map (ahn_file_loc)")
    assert f"hier waarschijnlijk '{tmp_path}'" in message
    assert os.path.join("<AHN-map>", "AHN5", "DTM05") in message


def test_check_folder_error_for_product_folder_without_version(tmp_path):
    make_files(tmp_path, "DTM05/2023_M_01CZ1.TIF")
    with pytest.raises(ValueError, match="lijkt zelf een versie- of productmap") as error:
        ahn.check_folder(str(tmp_path / "DTM05"), 5, "DTM", 0.5)
    assert "waarschijnlijk" not in str(error.value)  # Geen versiemap erboven: geen suggestie


def test_check_folder_accepts_version_named_root_with_layout(tmp_path):
    # Heet de AHN-map toevallig zelf 'AHN5' maar heeft die de juiste opbouw, dan is er niets aan de hand.
    make_files(tmp_path, "AHN5/AHN5/DTM05/2023_M_01CZ1.TIF")
    assert ahn.check_folder(str(tmp_path / "AHN5"), 5, "DTM", 0.5) is None


def test_check_folder_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="bestaat niet"):
        ahn.check_folder(str(tmp_path / "geen"), 5, "DTM", 0.5)


def test_tiles_check_folders(tmp_path, logs):
    make_files(tmp_path, "ahn/M_01CZ1.TIF", "downloads/M_01CZ2.TIF", "goed/AHN5/DTM05/2023_M_01CZ1.TIF")
    with pytest.raises(FileNotFoundError):
        ahn.AHNTiles(str(tmp_path / "geen"))
    with pytest.raises(ValueError, match=re.escape("AHN-map (ahn_file_loc)")):
        ahn.AHNTiles(str(tmp_path / "goed" / "AHN5" / "DTM05"))
    # Geen strikte opbouw meer: geen fout, wel een waarschuwing als AHN5/DTM05 ontbreekt.
    ahn.AHNTiles(str(tmp_path / "ahn"), download_folder=str(tmp_path / "downloads"))
    assert any("heeft geen map" in m for m in logs())
    n = len(logs())
    # Een downloadmap die nog niet bestaat, mag: die wordt bij de eerste download gemaakt.
    ahn.AHNTiles(str(tmp_path / "goed"), download_folder=str(tmp_path / "nieuw"))
    assert len(logs()) == n


# ---------- Lokaal zoeken ----------


@pytest.fixture
def tiles(tmp_path):
    folder, downloads = tmp_path / "ahn", tmp_path / "downloads"
    make_files(folder, "AHN4/DTM05/M_01CZ1_DEFLATE.tif", "AHN5/DTM05/2023_M_01CZ2.TIF")
    make_files(downloads, "AHN5/DTM05/2023_m_01cz2.tif")
    return ahn.AHNTiles(str(folder), download_folder=str(downloads)), folder


def test_local_file(tiles):
    t, folder = tiles
    ahn4, ahn5 = folder / "AHN4" / "DTM05", folder / "AHN5" / "DTM05"
    assert t._local_file("M_01CZ1.TIF", 4) == str(ahn4 / "M_01CZ1_DEFLATE.tif")  # Extra achtervoegsel mag
    assert t._local_file("2023_M_01CZ2.TIF", 5) == str(ahn5 / "2023_M_01CZ2.TIF")  # AHN-map wint
    assert t._local_file("2023_M_01CZ3.TIF", 5) is None
    # Alleen in de map van de versie: AHN3 heeft dezelfde bestandsnamen als AHN4.
    assert t._local_file("M_01CZ1.TIF", 3) is None
    assert t._local_file("M_01CZ1.TIF", 5) is None


def test_local_file_in_subfolders(tmp_path):
    folder = make_files(
        tmp_path,
        "AHN5/DTM05/blok1/2023_M_01CZ1.TIF",
        "AHN5/DTM05/blok2/sub/2023_M_01CZ2.TIF",
        "AHN5/DTM05/b/2023_M_01CZ3.TIF",
        "AHN5/DTM05/a/2023_M_01CZ3.TIF",  # Zelfde naam, zelfde diepte: eerste op alfabet
        "AHN5/DTM05/x/y/2023_M_01CZ4.TIF",
        "AHN5/DTM05/z/2023_M_01CZ4.TIF",  # Minst diep wint
        "AHN5/DTM05/blok1/leesmij.txt",
        "AHN4/DTM05/oud/M_01CZ1.TIF",
        "AHN5/DSM05/2023_R_01CZ1.TIF",
    )
    t = ahn.AHNTiles(str(folder))
    ahn5 = folder / "AHN5" / "DTM05"
    assert t._local_file("2023_M_01CZ1.TIF", 5) == str(ahn5 / "blok1" / "2023_M_01CZ1.TIF")
    assert t._local_file("2023_M_01CZ2.TIF", 5) == str(ahn5 / "blok2" / "sub" / "2023_M_01CZ2.TIF")
    assert t._local_file("2023_M_01CZ3.TIF", 5) == str(ahn5 / "a" / "2023_M_01CZ3.TIF")
    assert t._local_file("2023_M_01CZ4.TIF", 5) == str(ahn5 / "z" / "2023_M_01CZ4.TIF")
    # Terugval op AHN4 zoekt alleen in AHN4/DTM05 (ook in submappen), niet in de map van AHN5 of een ander product.
    assert t._local_file("M_01CZ1.TIF", 4) == str(folder / "AHN4" / "DTM05" / "oud" / "M_01CZ1.TIF")
    assert t._local_file("M_01CZ1.TIF", 3) is None


def test_sheet_names_and_fallback(tiles, monkeypatch):
    t, _ = tiles
    line = shapely.geometry.LineString([(150000, 450000), (150100, 450000)])
    names = t.sheet_names([line])
    assert names
    # Geen lokale tiff en downloaden mislukt: geen bestand, geen exception.
    monkeypatch.setattr(t, "_download", lambda url, version: None)
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
    path = t._download(url, 5)
    folder = tmp_path / "downloads" / "AHN5" / "DTM05"
    assert path == str(folder / "2023_M_99AA1.TIF")
    assert open(path, "rb").read() == b"tiff"
    assert files(folder) == ["2023_M_99AA1.TIF", "2023_m_01cz2.tif"]  # Geen .part over
    assert t._local_file("2023_M_99AA1.TIF", 5) == path  # Daarna lokaal gevonden


def test_download_zip_is_unpacked(tiles, web, tmp_path):
    t, _ = tiles
    url = "https://x/AHN4/M_99AA1.zip"
    web.pages[url] = zip_bytes({"map/M_99AA1.TIF": b"tiff", "M_99AA1.tfw": b"x"})
    path = t._download(url, 4)
    folder = tmp_path / "downloads" / "AHN4" / "DTM05"
    assert path == str(folder / "M_99AA1.TIF")  # Alleen de bestandsnaam, nooit mappen uit de zip
    assert open(path, "rb").read() == b"tiff"
    assert files(folder) == ["M_99AA1.TIF"]  # Geen zip of .part over
    assert files(tmp_path / "downloads") == ["AHN4", "AHN5"]


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
    assert t._download(url, 4) is None
    assert any(level_text in m for m in logs())
    assert files(tmp_path / "downloads" / "AHN4" / "DTM05") == []  # Niets achtergebleven


def test_fallback_to_ahn4(tiles, web, tmp_path, logs):
    t, _ = tiles
    url5, url4 = "https://x/AHN5/2023_M_99AA1.TIF", "https://x/AHN4/M_99AA1.zip"
    t.sheets["99AA1"] = (None, {5: url5, 4: url4})
    web.pages[url4] = zip_bytes({"M_99AA1.TIF": b"tiff"})
    assert t.file("99AA1") == str(tmp_path / "downloads" / "AHN4" / "DTM05" / "M_99AA1.TIF")
    assert web.requests == [url5, url4]
    assert any("geen AHN5, AHN4" in m for m in logs())
    t.file("99AA1")
    assert len(web.requests) == 2  # Tweede keer uit het geheugen


def test_local_file_needs_no_download(tiles, web):
    t, folder = tiles
    t.sheets["01CZ1"] = (None, {5: "https://x/AHN5/2023_M_01CZ1.TIF", 4: "https://x/AHN4/M_01CZ1.zip"})
    t.sheets["01CZ2"] = (None, {5: "https://x/AHN5/2023_M_01CZ2.TIF"})
    assert t.file("01CZ2") == str(folder / "AHN5" / "DTM05" / "2023_M_01CZ2.TIF")
    assert web.requests == []
    # Geen AHN5 lokaal of online, wel AHN4 lokaal (M_01CZ1_DEFLATE.tif): alleen AHN5 wordt geprobeerd.
    assert t.file("01CZ1") == str(folder / "AHN4" / "DTM05" / "M_01CZ1_DEFLATE.tif")
    assert web.requests == ["https://x/AHN5/2023_M_01CZ1.TIF"]


def test_fallback_to_local_ahn4_in_subfolder(tmp_path, web):
    folder = make_files(tmp_path, "AHN4/DTM05/deel/M_01CZ1.TIF", "AHN3/DTM05/M_01CZ1.TIF")
    t = ahn.AHNTiles(str(folder))
    t.sheets["01CZ1"] = (None, {5: "https://x/AHN5/2023_M_01CZ1.TIF", 4: "https://x/AHN4/M_01CZ1.zip"})
    assert t.file("01CZ1") == str(folder / "AHN4" / "DTM05" / "deel" / "M_01CZ1.TIF")  # Niet die van AHN3
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
