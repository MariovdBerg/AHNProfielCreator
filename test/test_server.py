"""Webapp-server: API, beveiliging van de lokale app en statische bestanden."""

import http.client
import http.server
import json
import threading

import pytest
import shapely.geometry
from conftest import write_lines

from ahnprofielcreator.app import server as app_server


@pytest.fixture
def app(tmp_path):
    dijk = write_lines(tmp_path / "dijk.shp", [(shapely.geometry.LineString([(150000, 450000), (150100, 450000)]), {"vak_id": "A"})])
    config = {"cur_dir": str(tmp_path / "uit"), "input_line": dijk, "ahn_file_loc": str(tmp_path / "ahn")}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), app_server.Handler)
    httpd.state = app_server.State(config)
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
