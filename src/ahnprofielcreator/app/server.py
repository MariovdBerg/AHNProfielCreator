# -*- coding: utf-8 -*-
"""
Lokale webapp om de invoer en uitvoer van de profielen te bekijken.

Gebruikt alleen de standaardbibliotheek en de pakketten die het project al nodig heeft (fiona, rasterio).
Leaflet staat in ``static/vendor``; alleen de achtergrondkaarten komen van internet (PDOK).

De app werkt in een werkmap met projecten: elk project is een map met ``invoer``, ``uitvoer`` en de
instellingen in ``config.ini``. Starten::

    python app.py --werkmap <werkmap> --project <naam>
"""

import argparse
import collections
import configparser
import dataclasses
import http.server
import json
import logging
import math
import os
import re
import threading
import traceback
import urllib.parse
import webbrowser
from pathlib import Path

import fiona
from rasterio.warp import transform

from .. import profielen_per_vak as ppv
from ..demo import DEMO_DIR, DEMO_NAME, is_demo
from ..profielen.constants import DEFAULT_CROSS_ID, DEFAULT_POINT_ID, NODATA

STATIC_DIR = Path(__file__).parent / "static"
WGS84 = "EPSG:4326"
RD = "EPSG:28992"  # Aangenomen als een shapefile geen .prj heeft
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}
FIELDS = {f.name: f for f in dataclasses.fields(ppv.Settings)}
# Het project bepaalt de projectmap; die staat niet in het formulier of in config.ini.
PROJECT_FIELD = "projectmap"


def _default(name):
    default = FIELDS[name].default
    return None if default is dataclasses.MISSING else default


def clean_settings(raw):
    """Zet invoer uit het formulier of INI-bestand om naar waarden voor `Settings`."""
    unknown = set(raw) - set(FIELDS)
    if unknown:
        raise ValueError(f"Onbekende instelling(en): {', '.join(sorted(unknown))}")

    settings = {}
    for name, field in FIELDS.items():
        value = raw.get(name)
        if isinstance(value, str):
            # Paden uit de Verkenner ('Kopiëren als pad') hebben aanhalingstekens.
            value = value.strip().strip('"').strip()
        if value is None or value == "":
            continue
        if field.type in (int, float):
            value = field.type(value)
        settings[name] = value
    return settings


def validate_settings(settings):
    """Controleer de waarden (zie `Settings`); ontbrekende verplichte paden mogen nog, die komen bij de berekening."""
    required = {name: "" for name, f in FIELDS.items() if f.default is dataclasses.MISSING}
    ppv.Settings(**{**required, **settings})


def is_project(path):
    """Een project is een map met een map invoer of een config.ini."""
    return os.path.isdir(os.path.join(path, ppv.INPUT_DIR)) or os.path.isfile(os.path.join(path, ppv.CONFIG_FILE))


def check_project_name(name):
    """Een projectnaam is een mapnaam in de werkmap, zonder pad."""
    name = str(name or "").strip()
    if not name or name in (".", "..") or re.search(r'[\\/:*?"<>|]', name):
        raise ValueError(f"Ongeldige projectnaam '{name}': gebruik een mapnaam zonder \\ / : * ? \" < > |.")
    return name


def to_wgs84(crs, coordinates):
    """
    Reken de coördinaten van geometrieën (GeoJSON-nesting, elk type) om naar WGS84.

    Alle punten gaan in één aanroep: `fiona.transform.transform_geom` kost per lijn ~6 ms,
    wat bij duizenden dwarslijnen het openen van de app minuten liet duren.
    """
    points = []

    def flatten(c):
        if isinstance(c[0], (int, float)):
            points.append(c)
        else:
            for part in c:
                flatten(part)

    for c in coordinates:
        flatten(c)
    if not points:
        return list(coordinates)
    xs, ys = transform(crs, WGS84, [p[0] for p in points], [p[1] for p in points])
    converted = iter(zip(xs, ys, points))

    def rebuild(c):
        if isinstance(c[0], (int, float)):
            x, y, p = next(converted)
            return (x, y, *p[2:])  # Hoogte (z) ongewijzigd
        return [rebuild(part) for part in c]

    return [rebuild(c) for c in coordinates]


class ListHandler(logging.Handler):
    """Bewaar logberichten van een run, zodat de webpagina ze kan tonen."""

    def __init__(self, records):
        super().__init__()
        self.records = records

    def emit(self, record):
        self.records.append(self.format(record))


class State:
    def __init__(self, werkmap, project=None, settings=None):
        """
        Toestand van de app in `werkmap`, met `project` (naam of pad) geopend.

        De instellingen komen uit ``config.ini`` in het project, of uit `settings` als die opgegeven zijn.
        """
        self.lock = threading.Lock()
        self.werkmap = os.path.abspath(werkmap)
        self.projectmap = None
        self.settings = {}
        self.cache = {}
        self.run = {"state": "idle", "task": None, "log": collections.deque(maxlen=500), "message": ""}
        if project:
            self.open_project(project, settings)

    # ---------- Projecten ----------

    @property
    def config_path(self):
        """INI-bestand met de instellingen van het project."""
        return os.path.join(self.projectmap, ppv.CONFIG_FILE) if self.projectmap else None

    def projects(self):
        """Namen van de projecten in de werkmap."""
        if not os.path.isdir(self.werkmap):
            return []
        names = [e.name for e in os.scandir(self.werkmap) if e.is_dir() and is_project(e.path)]
        return sorted(names, key=str.lower)

    def open_project(self, project, settings=None):
        """Open een project: een naam in de werkmap of een pad naar een projectmap."""
        path = os.path.abspath(os.path.join(self.werkmap, project))
        if not os.path.isdir(path):
            raise FileNotFoundError(f"Project '{project}' bestaat niet in '{self.werkmap}'.")
        if settings is None:
            config = os.path.join(path, ppv.CONFIG_FILE)
            settings = ppv.load_config(config) if os.path.isfile(config) else {}
        settings = clean_settings(settings)
        settings.pop(PROJECT_FIELD, None)
        with self.lock:
            if self.run["state"] == "running":
                raise RuntimeError("Er loopt een berekening; wacht tot die klaar is.")
            self.projectmap = path
            self.settings = settings
            self.run.update(state="idle", task=None, message="")
            self.run["log"].clear()
            self.cache.clear()

    def new_project(self, name):
        """Maak een project (met de mappen invoer en uitvoer) in de werkmap en open het."""
        name = check_project_name(name)
        if self.run["state"] == "running":
            raise RuntimeError("Er loopt een berekening; wacht tot die klaar is.")
        path = os.path.join(self.werkmap, name)
        if os.path.exists(path):
            raise FileExistsError(f"'{path}' bestaat al.")
        for sub in (ppv.INPUT_DIR, ppv.OUTPUT_DIR):
            os.makedirs(os.path.join(path, sub))
        # Meteen een volledige config.ini: alle instellingen met hun standaardwaarde.
        ppv.write_config(os.path.join(path, ppv.CONFIG_FILE), {})
        self.open_project(name)

    def require_project(self):
        if not self.projectmap:
            raise ValueError("Kies of maak eerst een project.")

    # ---------- Instellingen en paden ----------

    def value(self, name):
        if name == PROJECT_FIELD:
            return self.projectmap
        return self.settings.get(name, _default(name))

    def full_settings(self, settings=None):
        """Instellingen voor `Settings`: die van het formulier plus de projectmap."""
        return {**(self.settings if settings is None else settings), PROJECT_FIELD: self.projectmap or ""}

    def paths(self):
        project = self.projectmap
        vakken = self.value("input_line")
        paths = {
            "vakken": ppv.input_path(project, vakken) if project else vakken,
            "buitenlijn": ppv.outside_line(project, self.value("input_line_outside"), self.value("outside_offset")),
            "punten": None,
            "dwarslijnen": None,
            "profielen": None,
            "schematisatie": None,
        }
        if project:
            out = ppv.output_dir(project)
            paths["punten"] = os.path.join(out, ppv.OUTPUT_POINTS)
            paths["dwarslijnen"] = os.path.join(out, ppv.OUTPUT_CROSS)
            paths["profielen"] = os.path.join(out, ppv.OUTPUT_CROSS_AHN)
            paths["schematisatie"] = os.path.join(out, ppv.OUTPUT_PRFL_DIR, ppv.OUTPUT_SCHEM)
        return paths

    def schem_stale(self):
        """True als de schematisatie ouder is dan de AHN-profielen (de berekening is daarna opnieuw gedaan)."""
        paths = self.paths()
        if not all(paths[k] and os.path.isfile(paths[k]) for k in ("profielen", "schematisatie")):
            return False
        return os.path.getmtime(paths["schematisatie"]) < os.path.getmtime(paths["profielen"])

    def settings_info(self):
        fields = []
        for name, field in FIELDS.items():
            if name == PROJECT_FIELD:
                continue
            fields.append(
                {
                    "name": name,
                    "value": self.settings.get(name),
                    "default": _default(name),
                    "required": field.default is dataclasses.MISSING,
                    "number": field.type in (int, float),
                }
            )
        paths = self.paths()
        files = {k: {"path": p, "exists": bool(p) and os.path.isfile(p)} for k, p in paths.items()}
        project = self.projectmap
        return {
            "fields": fields,
            "files": files,
            "werkmap": self.werkmap,
            "projects": self.projects(),
            "project": os.path.basename(project) if project else None,
            "projectmap": project,
            "input_dir": ppv.input_dir(project) if project else None,
            "output_dir": ppv.output_dir(project) if project else None,
            "demo": DEMO_NAME if is_demo(project) else None,
            "schem_stale": self.schem_stale(),
            "config_path": self.config_path,
        }

    def save_config(self):
        """Sla de instellingen op in config.ini van het project: alle instellingen, ook de standaardwaarden."""
        self.require_project()
        path = self.config_path
        ppv.write_config(path, self.settings)
        # Opnieuw inlezen, zodat het formulier ook de geschreven standaardwaarden toont.
        self.settings = clean_settings(ppv.load_config(path))
        return {"path": path}

    # ---------- Shapefiles lezen ----------

    def read(self, path):
        """Lees features (geometrie in de bron-CRS, attributen) met een cache op wijzigingstijd."""
        stat = os.stat(path)
        key = os.path.abspath(path)
        version = (stat.st_mtime_ns, stat.st_size)
        # Per bestand alleen de laatste versie bewaren, zodat de cache niet blijft groeien.
        if key not in self.cache or self.cache[key][0] != version:
            with fiona.open(path) as source:
                crs = source.crs if source.crs and source.crs.to_string() else RD
                features = [
                    (row.geometry, dict(row.properties)) for row in source if row is not None and row.geometry
                ]
            self.cache[key] = (version, (crs, features))
        return self.cache[key][1]

    def read_schem(self, path):
        """Resultaten van de schematisatie per vak (schematisatie.json), met een cache op wijzigingstijd."""
        stat = os.stat(path)
        key = os.path.abspath(path)
        version = (stat.st_mtime_ns, stat.st_size)
        if key not in self.cache or self.cache[key][0] != version:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["vakken"] = {str(r["vak"]): r for r in data["vakken"]}
            self.cache[key] = (version, data)
        return self.cache[key][1]

    def geojson(self, path, properties):
        """GeoJSON in WGS84; `properties(geom, props)` bepaalt welke attributen naar de kaart gaan."""
        crs, features = self.read(path)
        coords = to_wgs84(crs, [g.coordinates for g, _ in features])
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": rd_geom.type, "coordinates": c},
                    "properties": properties(rd_geom, props),
                }
                for c, (rd_geom, props) in zip(coords, features)
            ],
        }

    def layers(self):
        paths = self.paths()
        group = self.value("group_id")
        layers = {}

        def load(name, properties):
            path = paths[name]
            if not path or not os.path.isfile(path):
                layers[name] = None
                return
            layers[name] = self.geojson(path, properties)

        def vak(props):
            return str(props.get(group, ""))

        load("vakken", lambda g, p: {"vak": vak(p), "props": p})
        load("buitenlijn", lambda g, p: {"vak": vak(p)})

        # Punten zonder dwarslijn (geen snijpunt met de buitenlijn) herkennen aan mid_x/mid_y.
        mids, nodata = set(), {}
        if paths["dwarslijnen"] and os.path.isfile(paths["dwarslijnen"]):
            for _, props in self.read(paths["dwarslijnen"])[1]:
                mids.add((round(props["mid_x"], 2), round(props["mid_y"], 2)))
        if paths["profielen"] and os.path.isfile(paths["profielen"]):
            for geom, props in self.read(paths["profielen"])[1]:
                z = [c[2] if len(c) > 2 else NODATA for c in geom.coordinates]
                missing = sum(1 for v in z if _is_nodata(v))
                nodata[props.get(DEFAULT_CROSS_ID)] = round(missing / len(z), 3) if z else 1.0

        def point_props(geom, props):
            x, y = geom.coordinates[:2]
            return {
                "vak": vak(props),
                "id": props.get(DEFAULT_POINT_ID),
                "skipped": bool(mids) and (round(x, 2), round(y, 2)) not in mids,
            }

        def cross_props(geom, props):
            cross_id = props.get(DEFAULT_CROSS_ID)
            return {
                "vak": vak(props),
                "id": cross_id,
                "angle": props.get("angle"),
                "nodata": nodata.get(cross_id),
            }

        load("punten", point_props)
        load("dwarslijnen", cross_props)
        return {"layers": layers, "has_profiles": bool(nodata)}

    def profiles(self, vak):
        """Hoogteprofielen van één vak: afstand tot de dijklijn (negatief = beginzijde) en hoogte."""
        path = self.paths()["profielen"]
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("Er zijn nog geen AHN-profielen voor deze instellingen.")

        group = self.value("group_id")
        result = []
        for geom, props in self.read(path)[1]:
            if str(props.get(group, "")) != vak:
                continue
            coords = geom.coordinates
            x0, y0 = coords[0][:2]
            offset = math.hypot(props["mid_x"] - x0, props["mid_y"] - y0)
            result.append(
                {
                    "id": props.get(DEFAULT_CROSS_ID),
                    "d": [round(math.hypot(c[0] - x0, c[1] - y0) - offset, 2) for c in coords],
                    "z": [None if _is_nodata(c[2]) else round(c[2], 3) for c in coords],
                }
            )
        result.sort(key=lambda p: (p["id"] is None, p["id"] or 0))

        # Geschematiseerd profiel van dit vak (als de schematisatie gedaan is).
        schem_path = self.paths()["schematisatie"]
        schem, kwantiel = None, None
        if schem_path and os.path.isfile(schem_path):
            data = self.read_schem(schem_path)
            schem = data["vakken"].get(vak)
            kwantiel = data.get("instellingen", {}).get("kwantiel")
        return {
            "vak": vak,
            "outside": bool(self.paths()["buitenlijn"]),
            "profiles": result,
            "schem": schem,
            "schem_kwantiel": kwantiel,
            "schem_stale": self.schem_stale(),
        }

    # ---------- Berekening draaien ----------

    # De berekening is alle stappen behalve de schematisatie; elke stap kan ook los.
    TASKS = {
        "buitenlijn": ppv.make_outside_line,
        "punten": ppv.make_points,
        "dwarslijnen": ppv.make_cross_lines,
        "profielen": ppv.make_profiles,
        "berekening": ppv.run,
        "schematisatie": ppv.schematise,
    }
    NO_AHN = ("buitenlijn", "punten", "dwarslijnen")  # Stappen die de AHN-map niet nodig hebben

    def start_run(self, task="berekening"):
        """Start de berekening, een losse stap of (daarna) de schematisatie in een aparte thread."""
        if task not in self.TASKS:
            raise ValueError(f"Onbekende taak: {task}")
        with self.lock:
            if self.run["state"] == "running":
                raise RuntimeError("Er loopt al een berekening.")
            self.require_project()
            settings = self.full_settings()
            if task in self.NO_AHN:
                settings = {"ahn_file_loc": "", **settings}
            missing = [n for n, f in FIELDS.items() if f.default is dataclasses.MISSING and n not in settings]
            if missing:
                raise ValueError(f"Ontbrekende instelling(en): {', '.join(missing)}")
            settings = ppv.Settings(**settings)
            if task == "buitenlijn":
                if settings.input_line_outside:
                    raise ValueError("Er is al een buitenlijn opgegeven; maak dat veld leeg om er een af te leiden.")
                if not settings.outside_offset:
                    raise ValueError("Vul een afstand in om de buitenlijn af te leiden (outside_offset).")
            elif task == "dwarslijnen":
                ppv.require_file(settings.output_points, "genereer eerst de intervalpunten")
            elif task == "profielen":
                ppv.require_file(settings.output_cross, "genereer eerst de dwarslijnen")
            elif task == "schematisatie":
                ppv.schematisatie.import_prflcheck()
                if not os.path.isfile(settings.output_cross_ahn):
                    raise FileNotFoundError("Er zijn nog geen AHN-profielen voor deze instellingen; start eerst de berekening.")
            self.run["state"] = "running"
            self.run["task"] = task
            self.run["message"] = ""
            self.run["log"].clear()

        threading.Thread(target=self._run, args=(self.TASKS[task], settings), daemon=True).start()

    def _run(self, func, settings):
        logger = ppv.get_logger()
        handler = ListHandler(self.run["log"])
        logger.addHandler(handler)
        try:
            func(settings)
            self.run["message"] = "Klaar."
            self.run["state"] = "done"
        except Exception as e:
            self.run["log"].append(traceback.format_exc())
            self.run["message"] = f"Fout: {e}"
            self.run["state"] = "error"
        finally:
            logger.removeHandler(handler)

    def run_status(self):
        return {
            "state": self.run["state"],
            "task": self.run["task"],
            "message": self.run["message"],
            "log": list(self.run["log"]),
        }


def _is_nodata(z):
    return z is None or math.isnan(z) or z == NODATA


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Geen regel per request in de console.

    @property
    def state(self):
        return self.server.state

    def _allowed(self):
        """
        Weiger verzoeken die niet van de app zelf komen.

        De Host-header moet 127.0.0.1/localhost zijn (tegen DNS-rebinding) en een eventuele Origin
        dezelfde host (tegen andere websites die POST'en naar de lokale app).
        """
        port = self.server.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urllib.parse.urlparse(origin).netloc in hosts

    def do_GET(self):
        if not self._allowed():
            self._send(403, b"Forbidden", "text/plain")
            return
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        routes = {
            "/api/settings": self.state.settings_info,
            "/api/layers": self.state.layers,
            "/api/profiles": lambda: self.state.profiles(query.get("vak", [""])[0]),
            "/api/run": self.state.run_status,
        }
        if url.path in routes:
            self._api(routes[url.path])
        elif url.path == "/":
            self._static("index.html")
        elif url.path.startswith("/static/"):
            self._static(urllib.parse.unquote(url.path[len("/static/") :]))
        else:
            self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)  # Altijd uitlezen, ook bij weigeren, anders wordt de verbinding gereset

        # Alleen JSON: dat kan een andere website niet zonder CORS-preflight sturen.
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not self._allowed() or content_type != "application/json":
            self._send(403, b"Forbidden", "text/plain")
            return

        def parse_body():
            try:
                body = json.loads(raw or b"{}")
            except ValueError as e:
                raise ValueError(f"Ongeldige JSON: {e}") from None
            if not isinstance(body, dict):
                raise ValueError("Verwacht een JSON-object")
            return body

        def update_settings():
            self.state.require_project()
            settings = clean_settings(parse_body())
            settings.pop(PROJECT_FIELD, None)
            validate_settings(self.state.full_settings(settings))
            with self.state.lock:
                # Niet tijdens een berekening: die leest o.a. de afgeleide buitenlijn.
                if self.state.run["state"] == "running":
                    raise RuntimeError("Er loopt een berekening; wacht tot die klaar is.")
                self.state.settings = settings
            return self.state.settings_info()

        def save_settings():
            parse_body()
            return self.state.save_config()

        def project():
            body = parse_body()
            if body.get("new"):
                self.state.new_project(body.get("name"))
            else:
                self.state.open_project(check_project_name(body.get("name")))
            return self.state.settings_info()

        def start(task=None):
            body = parse_body()
            self.state.start_run(task or str(body.get("task") or "berekening"))
            return self.state.run_status()

        routes = {
            "/api/settings": update_settings,
            "/api/save": save_settings,
            "/api/project": project,
            "/api/run": start,
            "/api/schem": lambda: start("schematisatie"),
        }
        if url.path in routes:
            self._api(routes[url.path])
        else:
            self._send(404, b"Not found", "text/plain")

    def _api(self, func):
        try:
            status, data = 200, func()
        except FileExistsError as e:
            status, data = 409, {"error": str(e)}
        except (FileNotFoundError, ImportError, ValueError, RuntimeError) as e:
            status, data = 400, {"error": str(e)}
        except Exception as e:
            traceback.print_exc()
            status, data = 500, {"error": f"{type(e).__name__}: {e}"}
        body = json.dumps(data, default=str).encode("utf-8")
        self._send(status, body, "application/json")

    def _static(self, relpath):
        path = (STATIC_DIR / relpath).resolve()
        if not path.is_relative_to(STATIC_DIR.resolve()) or not path.is_file():
            self._send(404, b"Not found", "text/plain")
            return
        content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self._send(200, path.read_bytes(), content_type)

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ahn-profielen-app", description="Bekijk invoer en uitvoer van de AHN profielen in de browser."
    )
    parser.add_argument("-w", "--werkmap", help="map met de projecten (standaard: de huidige map)")
    parser.add_argument(
        "-p", "--project", help=f"project (map in de werkmap) om te openen (standaard: {DEMO_DIR.name}, als dat er is)"
    )
    parser.add_argument(
        "-c", "--config", help="config.ini van een project: opent dat project, met de map erboven als werkmap"
    )
    parser.add_argument("--port", type=int, default=8050, help="poort (standaard: 8050)")
    parser.add_argument("--no-browser", action="store_true", help="open de browser niet automatisch")
    args = parser.parse_args(argv)

    werkmap, project = args.werkmap or os.getcwd(), args.project
    if args.config:
        if args.werkmap or args.project:
            parser.error("geef --config of --werkmap/--project, niet beide")
        if os.path.basename(args.config).lower() != ppv.CONFIG_FILE:
            parser.error(f"--config moet de {ppv.CONFIG_FILE} van een project zijn (in de projectmap)")
        projectmap = os.path.dirname(os.path.abspath(args.config))
        werkmap, project = os.path.dirname(projectmap), os.path.basename(projectmap)
    if not os.path.isdir(werkmap):
        parser.error(f"werkmap '{werkmap}' bestaat niet")
    if project is None and is_project(os.path.join(werkmap, DEMO_DIR.name)):
        project = DEMO_DIR.name

    try:
        state = State(werkmap, project)
    except (OSError, ValueError, RuntimeError, configparser.Error) as e:
        parser.error(str(e))

    # Alleen lokaal bereikbaar: de app leest en schrijft bestanden op deze computer.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.state = state

    url = f"http://127.0.0.1:{args.port}/"
    print(f"Werkmap: {state.werkmap}")
    print(f"Project: {state.projectmap or '(nog geen; kies of maak er een in de app)'}")
    if state.projectmap and is_demo(state.projectmap):
        print(f"Demotraject {DEMO_NAME} geladen.")
    print(f"AHN profielen app draait op {url} (stoppen met Ctrl+C)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
