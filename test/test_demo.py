"""Het meegeleverde demotraject: rekent offline door met de meegeleverde AHN-strook."""

import numpy as np
import pytest
from conftest import read_rows

from ahnprofielcreator import ahn
from ahnprofielcreator import profielen_per_vak as ppv
from ahnprofielcreator.app import server as app_server
from ahnprofielcreator.demo import DEMO_NAME, demo_config
from ahnprofielcreator.profielen.constants import NODATA


@pytest.fixture
def offline(monkeypatch):
    """Geen downloads en geen AHN6-controle (internet)."""

    def no_download(self, url):
        raise AssertionError(f"De demo mag niets downloaden: {url}")

    monkeypatch.setattr(ahn.AHNTiles, "_download", no_download)
    monkeypatch.setattr(ahn.AHNTiles, "report_ahn6", lambda self, lines: None)


def test_demo_runs_offline(tmp_path, offline, logs):
    s = ppv.Settings(**demo_config(str(tmp_path / "demo")), interval_length=50)
    heights = ppv.run(s)

    assert list(heights) == [f"DV_{i:02d}" for i in range(1, 7)]
    profiles = read_rows(s.output_cross_ahn)
    assert len(profiles) == 60  # 3 km, om de 50 m
    z = np.array([c[2] for geom, _ in profiles for c in geom.coords])
    assert np.mean(z == NODATA) < 0.05
    assert 8 < z[z != NODATA].max() < 12  # Kruin van de Lekdijk ~9 m NAP
    assert not [m for m in logs() if "geen hoogte" in m or "AHN4" in m]


def test_demo_note_until_settings_change(tmp_path):
    state = app_server.State(demo_config(str(tmp_path / "demo")), demo=True)
    info = state.settings_info()
    assert info["demo"] == DEMO_NAME
    assert info["files"]["vakken"]["exists"] and info["files"]["buitenlijn"]["exists"]

    state.settings = dict(state.settings, interval_length=25.0)
    assert state.settings_info()["demo"] is None
    assert app_server.State({}).settings_info()["demo"] is None
