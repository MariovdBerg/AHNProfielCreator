"""Instellingen: validatie, INI-bestand en commandline."""

import os

import pytest

from ahnprofielcreator import ahn
from ahnprofielcreator.profielen_per_vak import Settings, load_config, main, outside_line, output_dir

REQUIRED = {"cur_dir": "uit", "input_line": "dijk.shp", "ahn_file_loc": "ahn"}


def test_defaults_and_paths():
    s = Settings(**REQUIRED)
    assert s.output_dir == os.path.join("uit", "output_per_vak_10m")
    assert s.output_cross_ahn.endswith("cross_lines_ahn.shp")
    assert s.outside_line is None
    assert output_dir("uit", 12.5).endswith("output_per_vak_12.5m")


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
    ],
)
def test_invalid_settings(field, value):
    with pytest.raises(ValueError):
        Settings(**REQUIRED, **{field: value})


def test_outside_line_choice():
    assert outside_line("uit", 10, "buiten.shp", 5) == "buiten.shp"
    assert outside_line("uit", 10, None, 5).endswith("buitenlijn_offset.shp")
    assert outside_line("uit", 10, None, 0) is None


def test_load_config(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[profielen]\n"
        "cur_dir = \"C:\\data 100%\"\n"
        "input_line = dijk.shp ; commentaar\n"
        "ahn_file_loc = ahn\n"
        "ahn_versie =\n"
        "interval_length = 25\n"
        "ahn_file_prefix = oud\n",
        encoding="utf-8",
    )
    config = load_config(ini)
    assert config == {"cur_dir": "C:\\data 100%", "input_line": "dijk.shp", "ahn_file_loc": "ahn", "interval_length": 25.0}


@pytest.mark.parametrize(
    "text, message",
    [("[andere]\n", "ontbreekt"), ("[profielen]\nonbekend = 1\n", "Onbekende"), ("[profielen]\ncross_length = x\n", "getal")],
)
def test_load_config_errors(tmp_path, text, message):
    ini = tmp_path / "config.ini"
    ini.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_config(ini)


def test_main_reports_invalid_settings(capsys):
    with pytest.raises(SystemExit):
        main(["--cur-dir", "uit", "--input-line", "dijk.shp", "--ahn-file-loc", "ahn", "--interval-length", "0"])
    assert "interval_length" in capsys.readouterr().err


def test_main_reports_missing_settings(capsys):
    with pytest.raises(SystemExit):
        main(["--cur-dir", "uit"])
    assert "--input-line" in capsys.readouterr().err


def test_bundled_index_files_exist():
    assert os.path.isfile(ahn.DEFAULT_INDEX)
    assert os.path.isfile(ahn.AHN6_INDEX)
