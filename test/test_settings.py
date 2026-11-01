"""Instellingen: validatie, INI-bestand en commandline."""

import importlib.util
import os

import pytest

from ahnprofielcreator import ahn
from ahnprofielcreator.profielen_per_vak import Settings, load_config, main, outside_line, output_dir

REQUIRED = {"projectmap": "uit", "input_line": "dijk.shp", "ahn_file_loc": "ahn"}
HAS_PRFLCHECK = importlib.util.find_spec("prflcheck") is not None  # Grenzen van de ruwheid komen uit prflcheck


def test_defaults_and_paths():
    s = Settings(**REQUIRED)
    assert s.output_dir == output_dir("uit") == os.path.join("uit", "uitvoer")
    assert s.output_cross_ahn.endswith("cross_lines_ahn.shp")
    assert s.outside_line is None


def test_input_paths_relative_to_invoer(tmp_path):
    project = tmp_path / "project"
    absolute = str(tmp_path / "elders" / "buiten.shp")
    s = Settings(projectmap=str(project), input_line="dijk.shp", ahn_file_loc=".", input_line_outside=absolute)
    assert s.input_line == str(project / "invoer" / "dijk.shp")
    assert s.ahn_file_loc == str(project / "invoer")
    assert s.input_line_outside == absolute
    # Gedownloade bladen standaard in de werkmap (boven het project), gedeeld door alle projecten.
    assert s.ahn_file_downloaded == str(tmp_path / "ahn_data")
    assert Settings(**REQUIRED).ahn_index == ahn.DEFAULT_INDEX


def test_normalisation():
    s = Settings(**REQUIRED, model="dsm", model_resolutie="5", outside_side=" Rechts ", ahn_versie="4")
    assert (s.model, s.model_resolutie, s.outside_side, s.ahn_versie) == ("DSM", 5.0, "rechts", 4)


@pytest.mark.parametrize(
    "field, value",
    [
        ("interval_length", 0),
        ("interval_length", float("nan")),
        ("cross_length", -1),
        ("ahn_stepsize", 0),
        ("outside_offset", -5),
        ("outside_side", "boven"),
        ("model", "DEM"),
        ("model_resolutie", 1),
        ("schem_kwantiel", 1.5),
        ("schem_ruwheid", 0),
        pytest.param("schem_ruwheid", 0.3, marks=pytest.mark.skipif(not HAS_PRFLCHECK, reason="grenzen uit prflcheck")),
        ("schem_kruin_zoekafstand", 0),
        ("schem_voorland", "misschien"),
        ("schem_berm_winst_rel", 2),
        ("schem_min_bermbreedte", -1),
        ("schem_min_taludbreedte", 0),
        ("schem_stap", 0),
        ("schem_voorland_fit", -1),
        ("schem_voorland_delen", 0),
        ("schem_voorland_delen", 1.5),
        ("schem_decimalen", 7),
    ],
)
def test_invalid_settings(field, value):
    with pytest.raises(ValueError):
        Settings(**REQUIRED, **{field: value})


def test_outside_line_choice():
    assert outside_line("uit", "buiten.shp", 5) == os.path.join("uit", "invoer", "buiten.shp")
    assert outside_line("uit", None, 5) == os.path.join("uit", "uitvoer", "buitenlijn_offset.shp")
    assert outside_line("uit", None, 0) is None


def test_load_config(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[profielen]\n"
        "projectmap = \"C:\\data 100%\"\n"
        "input_line = dijk.shp ; commentaar\n"
        "ahn_file_loc = ahn\n"
        "ahn_versie =\n"
        "interval_length = 25\n"
        "ahn_file_prefix = oud\n",
        encoding="utf-8",
    )
    config = load_config(ini)
    assert config == {"projectmap": "C:\\data 100%", "input_line": "dijk.shp", "ahn_file_loc": "ahn", "interval_length": 25.0}


@pytest.mark.parametrize(
    "text, message",
    [("[andere]\n", "ontbreekt"), ("[profielen]\nonbekend = 1\n", "Onbekende"), ("[profielen]\ncross_length = x\n", "getal"),
     ("[profielen]\ncur_dir = uit\n", "vervangen door projectmap")],
)
def test_load_config_errors(tmp_path, text, message):
    ini = tmp_path / "config.ini"
    ini.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(ini)


def test_main_reports_invalid_settings(capsys):
    with pytest.raises(SystemExit):
        main(["--projectmap", "uit", "--input-line", "dijk.shp", "--ahn-file-loc", "ahn", "--interval-length", "0"])
    assert "interval_length" in capsys.readouterr().err


def test_main_reports_missing_settings(capsys):
    with pytest.raises(SystemExit):
        main(["--projectmap", "uit"])
    assert "--input-line" in capsys.readouterr().err


def test_bundled_index_files_exist():
    assert os.path.isfile(ahn.DEFAULT_INDEX)
    assert os.path.isfile(ahn.AHN6_INDEX)
