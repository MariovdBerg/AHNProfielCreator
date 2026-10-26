# -*- coding: utf-8 -*-
"""
Lokale webapp om de invoer en uitvoer van de profielen te bekijken.

Gebruikt alleen de standaardbibliotheek en de pakketten die het project al nodig heeft (fiona, rasterio).
Leaflet staat in ``static/vendor``; alleen de achtergrondkaarten komen van internet (PDOK).

Starten::

    python app.py --config config.ini
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
import threading
import traceback
import urllib.parse
import webbrowser
from pathlib import Path

import fiona
from rasterio.warp import transform

from .. import profielen_per_vak as ppv
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
    def __init__(self, config):
        self.lock = threading.Lock()
        self.settings = clean_settings(config)
        self.cache = {}
        self.run = {"state": "idle", "log": collections.deque(maxlen=500), "message": ""}

    # ---------- Instellingen en paden ----------

    def value(self, name):
        return self.settings.get(name, _default(name))

    def paths(self):
        paths = {
            "vakken": self.value("input_line"),
            "buitenlijn": ppv.outside_line(
                self.value("cur_dir"),
                self.value("interval_length"),
                self.value("input_line_outside"),
                self.value("outside_offset"),
            ),
            "punten": None,
            "dwarslijnen": None,
            "profielen": None,
        }
        if self.value("cur_dir"):
            out = ppv.output_dir(self.value("cur_dir"), self.value("interval_length"))
            paths["punten"] = os.path.join(out, ppv.OUTPUT_POINTS)
            paths["dwarslijnen"] = os.path.join(out, ppv.OUTPUT_CROSS)
            paths["profielen"] = os.path.join(out, ppv.OUTPUT_CROSS_AHN)
        return paths

    def settings_info(self):
        fields = []
        for name, field in FIELDS.items():
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
        return {"fields": fields, "files": files}

    def make_outside_line(self, force=False):
        """
        Leid de buitenlijn af (zonder AHN-berekening), zodat die direct op de kaart te controleren is.

        Zonder `force` alleen als het bestand nog ontbreekt.
        """
        if self.value("input_line_outside") or not self.value("outside_offset"):
            return
        if not self.value("cur_dir") or not self.value("input_line") or not os.path.isfile(self.value("input_line")):
            return
        if not force and os.path.isfile(self.paths()["buitenlijn"]):
            return
        settings = {"ahn_file_loc": "", **self.settings}
        ppv.make_outside_line(ppv.Settings(**settings))

    # ---------- Shapefiles lezen ----------

    def read(self, path):
        """Lees features (geometrie in de bron-CRS, attributen) met een cache op wijzigingstijd."""
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
        if key not in self.cache:
            with fiona.open(path) as source:
                crs = source.crs if source.crs and source.crs.to_string() else RD
                features = [
                    (row.geometry, dict(row.properties)) for row in source if row is not None and row.geometry
                ]
            self.cache[key] = (crs, features)
        return self.cache[key]

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
        try:
            self.make_outside_line()
        except ValueError:
            pass  # Foute instelling: de melding komt bij Toepassen of de berekening, de kaart laadt wel.
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
        result.sort(key=lambda p: p["id"])
        return {"vak": vak, "outside": bool(self.paths()["buitenlijn"]), "profiles": result}

    # ---------- Berekening draaien ----------

    def start_run(self):
        with self.lock:
            if self.run["state"] == "running":
                raise RuntimeError("Er loopt al een berekening.")
            missing = [n for n, f in FIELDS.items() if f.default is dataclasses.MISSING and n not in self.settings]
            if missing:
                raise ValueError(f"Ontbrekende instelling(en): {', '.join(missing)}")
            settings = ppv.Settings(**self.settings)
            self.run["state"] = "running"
            self.run["message"] = ""
            self.run["log"].clear()

        threading.Thread(target=self._run, args=(settings,), daemon=True).start()

    def _run(self, settings):
        logger = ppv.get_logger()
        handler = ListHandler(self.run["log"])
        logger.addHandler(handler)
        try:
            ppv.run(settings)
            self.run["message"] = "Klaar."
            self.run["state"] = "done"
        except Exception as e:
            self.run["log"].append(traceback.format_exc())
            self.run["message"] = f"Fout: {e}"
            self.run["state"] = "error"
        finally:
            logger.removeHandler(handler)

    def run_status(self):
        return {"state": self.run["state"], "message": self.run["message"], "log": list(self.run["log"])}


def _is_nodata(z):
    return z is None or math.isnan(z) or z == NODATA


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Geen regel per request in de console.

    @property
    def state(self):
        return self.server.state

    def do_GET(self):
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
        body = json.loads(self.rfile.read(length) or b"{}")

        def update_settings():
            self.state.settings = clean_settings(body)
            self.state.make_outside_line(force=True)
            return self.state.settings_info()

        def start_run():
            self.state.start_run()
            return self.state.run_status()

        routes = {"/api/settings": update_settings, "/api/run": start_run}
        if url.path in routes:
            self._api(routes[url.path])
        else:
            self._send(404, b"Not found", "text/plain")

    def _api(self, func):
        try:
            status, data = 200, func()
        except (FileNotFoundError, ValueError, RuntimeError) as e:
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
    parser.add_argument("-c", "--config", help="INI-bestand met instellingen (zelfde als voor ahn-profielen)")
    parser.add_argument("--port", type=int, default=8050, help="poort (standaard: 8050)")
    parser.add_argument("--no-browser", action="store_true", help="open de browser niet automatisch")
    args = parser.parse_args(argv)

    try:
        config = ppv.load_config(args.config) if args.config else {}
    except (OSError, ValueError, configparser.Error) as e:
        parser.error(str(e))

    # Alleen lokaal bereikbaar: de app leest en schrijft bestanden op deze computer.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.state = State(config)

    url = f"http://127.0.0.1:{args.port}/"
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
