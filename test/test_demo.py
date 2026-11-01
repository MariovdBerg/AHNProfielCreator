"""Het demotraject: rekent offline door met de meegeleverde AHN-strook."""

import numpy as np
import pytest
from conftest import read_rows

from ahnprofielcreator import ahn
from ahnprofielcreator import profielen_per_vak as ppv
from ahnprofielcreator.app import server as app_server
from ahnprofielcreator.demo import DEMO_DIR, DEMO_NAME, demo_config
from ahnprofielcreator.profielen.constants import NODATA


@pytest.fixture
def offline(monkeypatch):
    """Geen downloads en geen AHN6-controle (internet)."""

    def no_download(self, url):
        raise AssertionError(f"De demo mag niets downloaden: {url}")

    monkeypatch.setattr(ahn.AHNTiles, "_download", no_download)
    monkeypatch.setattr(ahn.AHNTiles, "report_ahn6", lambda self, lines: None)


def test_demo_runs_offline(tmp_path, offline, logs):
    s = ppv.Settings(**{**demo_config(str(tmp_path / "demo")), "interval_length": 50})
    assert s.input_line == str(DEMO_DIR / "invoer" / "demo_dijkvakken.shp")
    heights = ppv.run(s)

    assert list(heights) == [f"DV_{i:02d}" for i in range(1, 7)]
    assert s.output_cross_ahn == str(tmp_path / "demo" / "uitvoer" / ppv.OUTPUT_CROSS_AHN)
    profiles = read_rows(s.output_cross_ahn)
    assert len(profiles) == 60  # 3 km, om de 50 m
    z = np.array([c[2] for geom, _ in profiles for c in geom.coords])
    # Dwarslijnen van 200 m: bij een paar vakken ligt de Lek binnen 100 m van de dijk (water: geen AHN), ~8%
    assert np.mean(z == NODATA) < 0.1
    assert 8 < z[z != NODATA].max() < 12  # Kruin van de Lekdijk ~9 m NAP
    assert not [m for m in logs() if "geen hoogte" in m or "AHN4" in m]


def test_demo_config_uses_project_folder():
    config = demo_config()
    assert config["projectmap"] == str(DEMO_DIR)
    assert config["input_line"] == "demo_dijkvakken.shp"  # Relatief t.o.v. demo/invoer
    assert ppv.Settings(**config).output_dir == str(DEMO_DIR / "uitvoer")


def test_demo_config_is_complete(tmp_path):
    """demo/config.ini bevat alle instellingen, zoals `write_config` die schrijft (na een wijziging: opnieuw maken)."""
    path = DEMO_DIR / ppv.CONFIG_FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    keys = [line.split("=")[0].strip() for line in lines if line and not line.startswith(";") and "=" in line]
    assert keys == [name for _, names in ppv.CONFIG_LAYOUT for name in names]
    header = tuple(line[2:] for line in lines[: lines.index("")])
    ppv.write_config(tmp_path / "config.ini", ppv.load_config(path), header=header)
    assert (tmp_path / "config.ini").read_text(encoding="utf-8").splitlines() == lines


def test_demo_project_in_app():
    state = app_server.State(DEMO_DIR.parent, DEMO_DIR.name)
    info = state.settings_info()
    assert info["demo"] == DEMO_NAME
    assert DEMO_DIR.name in info["projects"]
    assert info["files"]["vakken"]["exists"]
    # Geen buitenlijn meegeleverd: die wordt afgeleid in de uitvoermap.
    assert info["files"]["buitenlijn"]["path"] == str(DEMO_DIR / "uitvoer" / ppv.OUTPUT_OUTSIDE)
    assert app_server.State(DEMO_DIR.parent).settings_info()["demo"] is None
