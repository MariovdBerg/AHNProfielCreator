"""Webapp-server: API, beveiliging van de lokale app en statische bestanden."""

import http.client
import http.server
import json
import os
import threading

import fiona
import pytest
import shapely.geometry
from conftest import CRS, write_lines

from ahnprofielcreator.app import server as app_server


@pytest.fixture
def app(tmp_path):
    dijk = write_lines(tmp_path / "dijk.shp", [(shapely.geometry.LineString([(150000, 450000), (150100, 450000)]), {"vak_id": "A"})])
    (tmp_path / "uit").mkdir()
    config = {"input_line": dijk, "ahn_file_loc": str(tmp_path / "ahn")}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app_server.Handler)
    httpd.state = app_server.State(tmp_path, "uit", config)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def request(app, method, path, body=None, headers=None):
    port = app.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port)
    headers = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    try:
        data = json.loads(data)
    except ValueError:
        pass
    return response.status, data


def test_settings_and_layers(app):
    status, data = request(app, "GET", "/api/settings")
    assert status == 200
    assert data["files"]["vakken"]["exists"]
    status, data = request(app, "GET", "/api/layers")
    assert status == 200
    assert len(data["layers"]["vakken"]["features"]) == 1
    lon, lat = data["layers"]["vakken"]["features"][0]["geometry"]["coordinates"][0]
    assert 4 < lon < 7 and 51 < lat < 53  # Omgerekend naar WGS84


def test_schem_needs_profiles(app, monkeypatch):
    monkeypatch.setattr(app_server.ppv.schematisatie, "import_prflcheck", lambda: None)  # Ook zonder prflcheck
    status, data = request(app, "POST", "/api/schem", {})
    assert status == 400
    assert "start eerst de berekening" in data["error"]
    assert app.state.run["state"] == "idle"


def test_schem_needs_prflcheck(app, monkeypatch):
    def missing():
        raise ImportError("prflcheck ontbreekt")

    monkeypatch.setattr(app_server.ppv.schematisatie, "import_prflcheck", missing)
    status, data = request(app, "POST", "/api/schem", {})
    assert status == 400
    assert "prflcheck ontbreekt" in data["error"]
    assert app.state.run["state"] == "idle"


def test_profiles_include_schematisation(app, tmp_path):
    paths = app.state.paths()
    os.makedirs(os.path.dirname(paths["schematisatie"]))
    schema = {
        "geometry": "3D LineString",
        "properties": {"vak_id": "str", "CROSS_ID": "int", "mid_x": "float", "mid_y": "float"},
    }
    with fiona.open(paths["profielen"], "w", "ESRI Shapefile", schema, crs=CRS) as sink:
        sink.write(
            {
                "geometry": shapely.geometry.mapping(shapely.geometry.LineString([(150050, 449990, 1), (150050, 450010, 3)])),
                "properties": {"vak_id": "A", "CROSS_ID": 1, "mid_x": 150050.0, "mid_y": 450000.0},
            }
        )
    with open(paths["schematisatie"], "w", encoding="utf-8") as f:
        json.dump({"instellingen": {"kwantiel": 0.2}, "vakken": [{"vak": "A", "bestand": "A.prfl"}]}, f)

    status, data = request(app, "GET", "/api/profiles?vak=A")
    assert status == 200
    assert data["profiles"][0]["d"] == [-10.0, 10.0]
    assert data["schem"]["bestand"] == "A.prfl" and data["schem_kwantiel"] == 0.2
    assert data["schem_stale"] is False
    status, data = request(app, "GET", "/api/settings")
    assert data["files"]["schematisatie"]["exists"]


def test_update_settings_validates(app):
    settings = dict(app.state.settings, interval_length="0")
    status, data = request(app, "POST", "/api/settings", settings)
    assert status == 400
    assert "interval_length" in data["error"]


def test_update_settings_refused_while_running(app):
    app.state.run["state"] = "running"
    status, data = request(app, "POST", "/api/settings", app.state.settings)
    assert status == 400
    assert "berekening" in data["error"]


def test_invalid_json(app):
    status, data = request(app, "POST", "/api/settings", b"{nee", {"Content-Type": "application/json"})
    assert status == 400
    assert "JSON" in data["error"]


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/plain"},  # Kan zonder CORS-preflight vanaf een andere site
        {"Content-Type": "application/json", "Origin": "https://evil.example"},
        {"Content-Type": "application/json", "Host": "evil.example:8050"},  # DNS-rebinding
    ],
)
def test_cross_site_post_refused(app, headers):
    status, _ = request(app, "POST", "/api/run", b"{}", headers)
    assert status == 403
    assert app.state.run["state"] == "idle"


def test_wrong_host_get_refused(app):
    status, _ = request(app, "GET", "/api/settings", headers={"Host": "evil.example"})
    assert status == 403


def test_static_files(app):
    assert request(app, "GET", "/")[0] == 200
    assert request(app, "GET", "/static/app.js")[0] == 200
    assert request(app, "GET", "/static/../server.py")[0] == 404
    assert request(app, "GET", "/static/%2e%2e/server.py")[0] == 404


def test_read_cache_keeps_one_version(app, tmp_path):
    path = app.state.paths()["vakken"]
    app.state.read(path)
    write_lines(path, [(shapely.geometry.LineString([(0, 0), (1, 1)]), {"vak_id": "B"}), (shapely.geometry.LineString([(0, 0), (2, 2)]), {"vak_id": "C"})])
    _, features = app.state.read(path)
    assert len(features) == 2
    assert len(app.state.cache) == 1


def wait_for_run(app):
    for _ in range(200):
        if app.state.run["state"] != "running":
            return app.state.run_status()
        threading.Event().wait(0.05)
    raise TimeoutError("Taak loopt nog")


def test_steps_need_previous_step(app):
    for task, message in (("dwarslijnen", "intervalpunten"), ("profielen", "dwarslijnen")):
        status, data = request(app, "POST", "/api/run", {"task": task})
        assert status == 400 and message in data["error"]
    status, data = request(app, "POST", "/api/run", {"task": "onbekend"})
    assert status == 400 and "Onbekende taak" in data["error"]

    status, _ = request(app, "POST", "/api/run", {"task": "punten"})
    assert status == 200
    assert wait_for_run(app)["state"] == "done"
    assert request(app, "GET", "/api/settings")[1]["files"]["punten"]["exists"]


def test_outside_line_step(app):
    status, data = request(app, "POST", "/api/run", {"task": "buitenlijn"})
    assert status == 400 and "afstand" in data["error"]
    app.state.settings["input_line_outside"] = app.state.settings["input_line"]
    status, data = request(app, "POST", "/api/run", {"task": "buitenlijn"})
    assert status == 400 and "al een buitenlijn" in data["error"]

    del app.state.settings["input_line_outside"]
    app.state.settings["outside_offset"] = 5.0
    # Valideren maakt de buitenlijn niet meer; de knop wel.
    request(app, "POST", "/api/settings", app.state.settings)
    assert not request(app, "GET", "/api/settings")[1]["files"]["buitenlijn"]["exists"]
    status, _ = request(app, "POST", "/api/run", {"task": "buitenlijn"})
    assert status == 200
    assert wait_for_run(app)["state"] == "done"
    assert request(app, "GET", "/api/settings")[1]["files"]["buitenlijn"]["exists"]


def test_save_settings(app, tmp_path):
    path = str(tmp_path / "uit" / "config.ini")
    before = dict(app.state.settings)
    status, data = request(app, "POST", "/api/save", {})
    assert status == 200 and data["path"] == path
    saved = app_server.ppv.load_config(path)
    assert "projectmap" not in saved  # Volgt uit de map
    assert {k: saved[k] for k in before} == before
    assert saved["interval_length"] == 10 and saved["model"] == "DTM"  # Ook de standaardwaarden
    assert app.state.settings == saved  # Het formulier toont wat in het bestand staat
    assert request(app, "GET", "/api/settings")[1]["config_path"] == path
    assert request(app, "POST", "/api/save", {})[0] == 200  # Overschrijft het eigen config.ini


def test_projects(app, tmp_path):
    info = request(app, "GET", "/api/settings")[1]
    assert info["werkmap"] == str(tmp_path) and info["project"] == "uit"
    assert info["projects"] == []  # 'uit' heeft (nog) geen invoer of config.ini
    assert "projectmap" not in [f["name"] for f in info["fields"]]
    assert info["files"]["punten"]["path"] == str(tmp_path / "uit" / "uitvoer" / "cross_points.shp")

    status, info = request(app, "POST", "/api/project", {"name": "nieuw", "new": True})
    assert status == 200 and info["project"] == "nieuw" and info["projects"] == ["nieuw"]
    assert (tmp_path / "nieuw" / "invoer").is_dir() and (tmp_path / "nieuw" / "uitvoer").is_dir()
    # Meteen een volledig config.ini, met de standaardwaarden; de verplichte paden nog leeg.
    config = (tmp_path / "nieuw" / "config.ini").read_text(encoding="utf-8")
    assert "\ninput_line =\n" in config and "\nschem_decimalen = 3\n" in config
    assert app.state.settings["cross_length"] == 200 and "input_line" not in app.state.settings
    assert request(app, "POST", "/api/project", {"name": "nieuw", "new": True})[0] == 409

    # Relatieve invoerpaden gelden t.o.v. de invoer van het project.
    request(app, "POST", "/api/settings", {"input_line": "dijk.shp", "ahn_file_loc": "."})
    info = request(app, "GET", "/api/settings")[1]
    assert info["files"]["vakken"]["path"] == str(tmp_path / "nieuw" / "invoer" / "dijk.shp")
    request(app, "POST", "/api/save", {})

    assert request(app, "POST", "/api/project", {"name": "uit"})[0] == 200
    assert app.state.projectmap == str(tmp_path / "uit") and app.state.settings == {}  # Geen config.ini
    assert request(app, "POST", "/api/project", {"name": "nieuw"})[0] == 200
    assert app.state.settings["input_line"] == "dijk.shp" and app.state.settings["ahn_file_loc"] == "."  # Uit nieuw/config.ini
    assert request(app, "POST", "/api/project", {"name": "bestaat_niet"})[0] == 400


@pytest.mark.parametrize("name", ["", "..", "a/b", "..\\uit", "c:"])
def test_project_name_refused(app, name):
    status, data = request(app, "POST", "/api/project", {"name": name, "new": True})
    assert status == 400 and "projectnaam" in data["error"]
    status, data = request(app, "POST", "/api/project", {"name": name})
    assert status == 400


def test_without_project(tmp_path):
    state = app_server.State(tmp_path)
    info = state.settings_info()
    assert info["project"] is None and info["files"]["punten"]["path"] is None
    with pytest.raises(ValueError, match="project"):
        state.start_run("punten")


def test_main_opens_demo_by_default(monkeypatch):
    """Zonder argumenten in de ontwikkelmap: werkmap is de huidige map, project demo."""
    opened = {}
    monkeypatch.chdir(app_server.DEMO_DIR.parent)
    monkeypatch.setattr(app_server.http.server.ThreadingHTTPServer, "serve_forever", lambda self: opened.update(state=self.state))
    assert app_server.main(["--no-browser", "--port", "0"]) == 0
    assert opened["state"].projectmap == str(app_server.DEMO_DIR)
    assert opened["state"].settings_info()["demo"]
