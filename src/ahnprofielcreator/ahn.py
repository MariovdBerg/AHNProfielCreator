# -*- coding: utf-8 -*-
"""
AHN-kaartbladen zoeken, lokaal vinden en zo nodig downloaden.

De kaartbladindex is de bladwijzer van AHN (``bladwijzer.gpkg``, bladen van 5 x 6,25 km). Die
heeft per blad een downloadlink per AHN-versie en product, bijv. veld ``AHN5_05M_M`` voor AHN5
DTM 0,5 m. Per blad wordt eerst lokaal gezocht naar de tiff met de naam uit de link (in de map met AHN
tiffs en de downloadmap), anders wordt die gedownload naar de downloadmap. Heeft een blad geen (werkende) link voor de gevraagde versie, dan valt het terug
op AHN4, dat heel Nederland dekt.

AHN6 gebruikt bladen van 1 x 1 km (``bladwijzer_AHN6.gpkg``) en wordt alleen gemeld als het langs
het traject beschikbaar is.
"""

import concurrent.futures
import logging
import os
import pathlib
import shutil
import ssl
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import fiona
import shapely.geometry
import shapely.prepared
import tqdm.auto as tqdm

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INDEX = str(PROJECT_DIR / "bladwijzer.gpkg")
DEFAULT_INDEX_ID = "AHN"
AHN6_INDEX = str(PROJECT_DIR / "bladwijzer_AHN6.gpkg")
AHN6_LAYER = "bladindeling_aoi"  # Met 'jaar' (inwinjaar) per km-blad
FALLBACK_VERSION = 4  # AHN4 dekt heel Nederland
DOWNLOAD_URL = "https://basisdata.nl/hwh-ahn"
TIMEOUT = 60  # [s]

MODELS = {"DTM": "M", "DSM": "R"}  # Letter in de veld- en bestandsnamen
RESOLUTIONS = {0.5: "05M", 5.0: "5M"}
AHN6_DIRS = {
    ("DTM", 0.5): "02a_DTM_50cm",
    ("DTM", 5.0): "02b_DTM_5m",
    ("DSM", 0.5): "03a_DSM_50cm",
    ("DSM", 5.0): "03b_DSM_5m",
}


def check_product(model, resolution):
    """Controleer model (DTM/DSM) en resolutie (0.5/5 m); geeft (model, resolutie) genormaliseerd terug."""
    model = str(model).strip().upper()
    if model not in MODELS:
        raise ValueError(f"model moet DTM of DSM zijn (is '{model}')")
    resolution = float(resolution)
    if resolution not in RESOLUTIONS:
        raise ValueError(f"model_resolutie moet 0.5 of 5 zijn (is {resolution:g})")
    return model, resolution


def index_field(version, model, resolution):
    """Veld in de bladwijzer met de downloadlink, bijv. AHN5_05M_M voor AHN5 DTM 0,5 m."""
    model, resolution = check_product(model, resolution)
    res = RESOLUTIONS[resolution]
    if version == 1 and model == "DTM":
        return f"AHN1_{res}"
    if version == 2 and model == "DTM" and res == "05M":
        return "AHN2_05M_I"  # Geïnterpoleerd; AHN2_05M_N is niet geïnterpoleerd
    return f"AHN{version}_{res}_{MODELS[model]}"


def tif_name(url):
    """Naam van de tiff bij een downloadlink: 2023_M_01CZ1.TIF, of M_01CZ1.TIF uit M_01CZ1.zip."""
    name = os.path.basename(urllib.parse.urlparse(url).path)
    if name.lower().endswith(".zip"):
        name = name[:-4]
        if not name.lower().endswith((".tif", ".tiff")):
            name += ".TIF"
    return name


def _stem(name):
    return os.path.splitext(name)[0].lower()


def _certifi_context():
    try:
        import certifi
    except ImportError:
        return None
    return ssl.create_default_context(cafile=certifi.where())


_CERTIFI_CONTEXT = _certifi_context()


def urlopen(request):
    """
    `urllib.request.urlopen` met de certificaten van certifi.

    In de Windows-certificaatopslag kan een verlopen tussencertificaat staan, waardoor OpenSSL de
    keten van basisdata.nl afkeurt. Lukt het met certifi niet (bijv. achter een proxy met een eigen
    certificaat), dan alsnog met de certificaten van Windows.
    """
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT, context=_CERTIFI_CONTEXT)
    except urllib.error.URLError as e:
        if _CERTIFI_CONTEXT is None or not isinstance(e.reason, ssl.SSLError):
            raise
        return urllib.request.urlopen(request, timeout=TIMEOUT)


class AHNTiles:
    """Kaartbladen uit de bladwijzer met de bijbehorende lokale (of gedownloade) tiffs."""

    def __init__(
        self,
        folder,
        download_folder=None,
        index=DEFAULT_INDEX,
        index_id=DEFAULT_INDEX_ID,
        version=5,
        model="DTM",
        resolution=0.5,
        ahn6_index=AHN6_INDEX,
        logger=None,
    ):
        self.folder = folder
        # Downloads apart houden, bijv. als `folder` alleen-lezen is; standaard `folder` zelf.
        self.download_folder = download_folder or folder
        self.index = index
        self.index_id = index_id
        self.model, self.resolution = check_product(model, resolution)
        self.version = int(version)
        self.ahn6_index = ahn6_index
        self.logger = logger or logging.getLogger("ahnprofielcreator")

        versions = [self.version]
        if self.version > FALLBACK_VERSION:
            versions.append(FALLBACK_VERSION)
        self.fields = {v: index_field(v, self.model, self.resolution) for v in versions}

        self.sheets = {}  # bladnaam -> (voorbereide geometrie, {versie: url})
        self._local = None  # kleine-letter stam -> pad, pas bij eerste gebruik
        self._files = {}
        self._read_index()

    @property
    def product(self):
        return f"{self.model} {self.resolution:g} m"

    def _read_index(self):
        if not os.path.isfile(self.index):
            raise FileNotFoundError(f"AHN kaartbladindex '{self.index}' bestaat niet")

        with fiona.open(self.index) as source:
            names = list(source.schema["properties"])
            if self.index_id not in names:
                raise ValueError(f"Attribuut '{self.index_id}' (ahn_index_id) ontbreekt in '{self.index}'")
            if self.fields[self.version] not in names:
                available = ", ".join(n for n in names if n.upper().startswith("AHN") and n != self.index_id)
                raise ValueError(
                    f"Geen downloadlinks voor AHN{self.version} {self.product} in '{self.index}' "
                    f"(veld '{self.fields[self.version]}' ontbreekt; beschikbaar: {available or 'geen'})"
                )
            self.fields = {v: f for v, f in self.fields.items() if f in names}

            for row in source:
                if row is None or row.geometry is None:
                    continue
                name = row.properties[self.index_id]
                if name is None:
                    continue
                urls = {v: row.properties[f] for v, f in self.fields.items() if row.properties[f]}
                geom = shapely.prepared.prep(shapely.geometry.shape(row.geometry))
                self.sheets[str(name)] = (geom, urls)

    def sheet_names(self, lines):
        """Bladnamen van de kaartbladen die de lijnen raken."""
        mls = shapely.geometry.MultiLineString(lines)
        return [name for name, (geom, _) in self.sheets.items() if geom.intersects(mls)]

    def file(self, name):
        """Pad naar de tiff van een blad (lokaal of gedownload), of None als er geen is."""
        if name not in self._files:
            self._files[name] = self._find_or_download(name)
        return self._files[name]

    def _find_or_download(self, name):
        urls = self.sheets[name][1]
        for version in self.fields:
            url = urls.get(version)
            if not url:
                continue
            path = self._local_file(tif_name(url)) or self._download(url)
            if path is None:
                continue
            if version != self.version:
                self.logger.warning(f"Blad {name}: geen AHN{self.version}, AHN{version} {self.product} gebruikt.")
            return path

        self.logger.warning(f"Blad {name}: geen AHN {self.product} beschikbaar (lokaal of als download).")
        return None

    def _local_file(self, name):
        """Lokale tiff bij de naam uit de downloadlink; een extra achtervoegsel mag (M_01CZ1_DEFLATE.tif)."""
        if self._local is None:
            self._local = {}
            # Bij dezelfde naam wint de map met AHN tiffs van de downloadmap.
            for folder in (self.download_folder, self.folder):
                if not os.path.isdir(folder):
                    continue
                for file in os.listdir(folder):
                    path = os.path.join(folder, file)
                    if file.lower().endswith((".tif", ".tiff")) and os.path.isfile(path):
                        self._local[_stem(file)] = path

        stem = _stem(name)
        if stem in self._local:
            return self._local[stem]
        for local_stem, path in self._local.items():
            if local_stem.startswith(stem + "_"):
                return path
        return None

    def _download(self, url):
        os.makedirs(self.download_folder, exist_ok=True)
        name = os.path.basename(urllib.parse.urlparse(url).path)
        part = os.path.join(self.download_folder, name + ".part")
        try:
            with urlopen(url) as response, open(part, "wb") as f:
                size = int(response.headers.get("Content-Length") or 0)
                self.logger.info(f"Downloaden {url} ({size / 1e6:.0f} MB)...")
                with tqdm.tqdm(total=size or None, unit="B", unit_scale=True, desc=name, leave=False) as pbar:
                    while chunk := response.read(1 << 20):
                        f.write(chunk)
                        pbar.update(len(chunk))
            if name.lower().endswith(".zip"):
                path = self._unzip(part)
            else:
                path = os.path.join(self.download_folder, name)
                os.replace(part, path)
        except urllib.error.HTTPError as e:
            self.logger.info(f"Niet beschikbaar: {url} (HTTP {e.code})")
            return None
        except (urllib.error.URLError, OSError, zipfile.BadZipFile) as e:
            self.logger.warning(f"Downloaden mislukt: {url} ({e})")
            return None
        finally:
            if os.path.exists(part):
                os.remove(part)

        self._local[_stem(os.path.basename(path))] = path
        return path

    def _unzip(self, zip_path):
        with zipfile.ZipFile(zip_path) as z:
            members = [m for m in z.namelist() if m.lower().endswith((".tif", ".tiff"))]
            if len(members) != 1:
                raise OSError(f"verwacht één tiff in de zip, gevonden: {len(members)}")
            # Alleen de bestandsnaam gebruiken, nooit mappen uit de zip.
            path = os.path.join(self.download_folder, os.path.basename(members[0]))
            with z.open(members[0]) as src, open(path + ".part", "wb") as dst:
                shutil.copyfileobj(src, dst)
        os.replace(path + ".part", path)
        return path

    def report_ahn6(self, lines):
        """Meld of AHN6 langs de lijnen beschikbaar is (alleen informatie; AHN6 wordt niet gebruikt)."""
        if self.version >= 6 or not self.ahn6_index or not os.path.isfile(self.ahn6_index):
            return
        try:
            mls = shapely.geometry.MultiLineString(lines)
            with fiona.open(self.ahn6_index, layer=AHN6_LAYER) as source:
                sheets = [
                    (int(row.properties["left"]), int(row.properties["bottom"]), row.properties["jaar"])
                    for _, row in source.items(bbox=mls.bounds)
                    if shapely.geometry.shape(row.geometry).intersects(mls)
                ]
        except Exception as e:
            self.logger.debug(f"AHN6-bladwijzer niet te lezen: {e}")
            return
        if not sheets:
            return

        ahn6_dir = AHN6_DIRS[(self.model, self.resolution)]
        code = MODELS[self.model] + ("" if self.resolution == 0.5 else "5")

        def available(sheet):
            left, bottom, year = sheet
            # Zoals de bladwijzer van AHN: 2025 met voorloopnullen, 2026 zonder.
            for name in (f"{left:06d}_{bottom:06d}", f"{left}_{bottom}"):
                url = f"{DOWNLOAD_URL}/AHN6/{ahn6_dir}/AHN6_{year}_{code}_{name}.TIF"
                try:
                    with urlopen(urllib.request.Request(url, method="HEAD")):
                        return True
                except urllib.error.HTTPError:
                    continue
            return False

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                found = list(pool.map(available, sheets))
        except (urllib.error.URLError, OSError) as e:
            self.logger.info(f"Beschikbaarheid AHN6 niet te controleren ({e}).")
            return

        n = sum(found)
        if n:
            years = sorted({year for (_, _, year), ok in zip(sheets, found) if ok})
            self.logger.info(
                f"AHN6 {self.product} is beschikbaar voor {n} van {len(sheets)} km-bladen langs het traject "
                f"(inwinjaar {', '.join(map(str, years))}). Deze app gebruikt AHN{self.version}; AHN6 nog niet."
            )
        else:
            planned = ", ".join(map(str, sorted({year for _, _, year in sheets})))
            self.logger.info(f"AHN6 is langs het traject nog niet beschikbaar (inwinjaar volgens planning: {planned}).")
