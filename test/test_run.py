"""De volledige keten via run() en main(), met een nep-AHN-tiff in plaats van de bladwijzer."""

import fiona
import pytest
import shapely.geometry
from conftest import FakeTiles, read_rows, write_lines

from ahnprofielcreator import profielen_per_vak as ppv
from ahnprofielcreator.profielen.constants import NODATA

LineString = shapely.geometry.LineString


@pytest.fixture
def fake_tiles(monkeypatch, tif):
    """Vervang AHNTiles door één lokaal blad (de nep-tiff van 0..100 x 0..100 m); onthoudt de argumenten."""
    calls = []

    def make(folder, **kwargs):
        calls.append({"folder": folder, **kwargs})
        return FakeTiles(tif, (0, 0, 100, 100))

    monkeypatch.setattr(ppv.ahn, "AHNTiles", make)
    return calls


@pytest.fixture
def dijk_noord(tmp_path):
    """Eén dijkvak van 100 m langs y=50, midden in de nep-tiff."""
    return write_lines(tmp_path / "dijk.shp", [(LineString([(0, 50), (100, 50)]), {"vak_id": "A"})])


def settings(tmp_path, dijk, **kwargs):
    kwargs = {"interval_length": 25, "cross_length": 20, "ahn_stepsize": 1, **kwargs}
    return ppv.Settings(projectmap=str(tmp_path / "uit"), input_line=dijk, ahn_file_loc=str(tmp_path / "ahn"), **kwargs)


def test_run_writes_all_outputs(tmp_path, dijk_noord, fake_tiles, logs):
    s = settings(tmp_path, dijk_noord)
    heights = ppv.run(s)

    points, lines, profiles = (read_rows(p) for p in (s.output_points, s.output_cross, s.output_cross_ahn))
    assert len(points) == len(lines) == len(profiles) == 4  # 0.01, 25.01, 50.01, 75.01
    assert list(heights) == ["A"] and len(heights["A"]) == 4
    assert s.output_dir == str(tmp_path / "uit" / "uitvoer")

    # Dwarslijn bij x=25.01 ligt in de kolom met hoogte 25.5 (cellen van 1 m, hoogte = x-midden).
    by_x = {round(props["mid_x"], 2): geom for geom, props in profiles}
    z = [c[2] for c in by_x[25.01].coords]
    assert len(z) == 21 and set(z) == {25.5}
    # Dwarslijn bij x=75.01 ligt buiten het gat (60..70); x=50.01 ook.
    assert NODATA not in [c[2] for c in by_x[50.01].coords]

    # AHN-instellingen worden doorgegeven; de log meldt alle stappen.
    assert fake_tiles[0]["version"] == 5 and fake_tiles[0]["model"] == "DTM"
    messages = logs()
    assert any(m.startswith("Stap 3/3") for m in messages)
    assert any(m.startswith("Klaar") for m in messages)


def test_run_with_derived_outside_line(tmp_path, dijk_noord, fake_tiles):
    s = settings(tmp_path, dijk_noord, outside_offset=5, outside_side="rechts")
    ppv.run(s)
    assert len(read_rows(s.outside_line)) == 1
    # Buitenlijn rechts (zuid) van een lijn naar het oosten: dwarslijnen beginnen zuidelijk.
    for geom, props in read_rows(s.output_cross):
        assert geom.coords[0][1] < props["mid_y"]


def test_run_rejects_other_crs_before_writing(tmp_path, fake_tiles):
    dijk = write_lines(tmp_path / "wgs.shp", [(LineString([(5, 53), (5.01, 53)]), {"vak_id": "A"})], crs="EPSG:4326")
    s = settings(tmp_path, dijk)
    with pytest.raises(ValueError, match="RD New"):
        ppv.run(s)
    assert not (tmp_path / "uit").exists()


def test_run_checks_outside_line_crs(tmp_path, dijk_noord, fake_tiles):
    buiten = write_lines(tmp_path / "buiten.shp", [(LineString([(0, 55), (100, 55)]), {"vak_id": "A"})], crs=None)
    with pytest.raises(ValueError, match="Buitenlijn .* geen coördinatenstelsel"):
        ppv.run(settings(tmp_path, dijk_noord, input_line_outside=buiten))


def test_main_with_config(tmp_path, dijk_noord, fake_tiles):
    (tmp_path / "uit").mkdir()
    ini = tmp_path / "uit" / "config.ini"  # Projectmap: de map van het bestand
    ini.write_text(
        f"[profielen]\ninput_line = {dijk_noord}\nahn_file_loc = {tmp_path}\n"
        "interval_length = 50\ncross_length = 20\nahn_stepsize = 2\n",
        encoding="utf-8",
    )
    assert ppv.main(["--config", str(ini), "--interval-length", "25"]) == 0  # Optie wint van het bestand
    with fiona.open(tmp_path / "uit" / "uitvoer" / "cross_lines_ahn.shp") as source:
        rows = list(source)
    assert len(rows) == 4
    assert len(rows[0].geometry.coordinates) == 11  # 20 m in stappen van 2 m


def test_steps_separately(tmp_path, dijk_noord, fake_tiles):
    s = settings(tmp_path, dijk_noord, outside_offset=5)
    with pytest.raises(FileNotFoundError, match="intervalpunten"):
        ppv.make_cross_lines(s)
    ppv.make_points(s)
    assert len(read_rows(s.output_points)) == 4
    with pytest.raises(FileNotFoundError, match="dwarslijnen"):
        ppv.make_profiles(s)
    ppv.make_cross_lines(s)  # Maakt ook de afgeleide buitenlijn
    assert len(read_rows(s.outside_line)) == 1 and len(read_rows(s.output_cross)) == 4
    heights = ppv.make_profiles(s)
    assert len(heights["A"]) == 4 and len(read_rows(s.output_cross_ahn)) == 4


def test_write_config_roundtrip(tmp_path):
    config = {"input_line": r"P:\data dijk\vakken.shp", "ahn_file_loc": "ahn", "interval_length": 25.0}
    path = tmp_path / "project" / "config.ini"
    path.parent.mkdir()
    ppv.write_config(path, {**config, "projectmap": "elders"})
    loaded = ppv.load_config(path)
    assert "projectmap" not in loaded  # De map van het bestand
    # Alle instellingen staan erin, ook de standaardwaarden; leeg alleen waar de standaard geen waarde heeft.
    lines = path.read_text(encoding="utf-8").splitlines()
    written = [line.split("=")[0].strip() for line in lines if line and not line.startswith((";", "["))]
    assert written == [name for _, names in ppv.CONFIG_LAYOUT for name in names]
    assert set(loaded) == set(written) - {"input_line_outside", "ahn_index"}
    assert {k: loaded[k] for k in config} == config
    assert loaded["model"] == "DTM" and loaded["schem_kwantiel"] == 0.2
    # Met de geschreven standaardwaarden rekent de app hetzelfde als zonder.
    projectmap = str(path.parent)
    assert ppv.Settings(projectmap=projectmap, **loaded) == ppv.Settings(projectmap=projectmap, **config)
    with pytest.raises(ValueError, match="input_line"):
        ppv.write_config(path, {"input_line": "dijk ; commentaar"})


def test_config_layout_has_all_settings():
    names = [name for _, names in ppv.CONFIG_LAYOUT for name in names]
    assert sorted(names) == sorted(f.name for f in ppv.dataclasses.fields(ppv.Settings) if f.name != "projectmap")
