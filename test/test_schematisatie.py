"""Schematisatie van de AHN-profielen tot een Hydra-NL profiel (.prfl) per vak."""

import json
import os

import numpy as np
import pytest
import rasterio
import rasterio.transform
import shapely.geometry
from conftest import CRS, FakeTiles, write_lines

from ahnprofielcreator import profielen_per_vak as ppv
from ahnprofielcreator import schematisatie
from ahnprofielcreator.profielen import SchemProfile
from ahnprofielcreator.profielen.schem_cross import rdp

prflcheck = pytest.importorskip("prflcheck")


def rdp_reference(points, epsilon):
    """Recursieve versie zoals in het pakket rdp (rdp.rdp), ter vergelijking."""
    start, end = points[0], points[-1]
    dmax, index = 0.0, -1
    for i in range(1, len(points)):
        if np.all(start == end):
            d = np.linalg.norm(points[i] - start)
        else:
            d = abs((end[0] - start[0]) * (start[1] - points[i][1]) - (end[1] - start[1]) * (start[0] - points[i][0]))
            d /= np.linalg.norm(end - start)
        if d > dmax:
            index, dmax = i, d
    if dmax > epsilon:
        left = rdp_reference(points[: index + 1], epsilon)
        right = rdp_reference(points[index:], epsilon)
        return np.vstack((left[:-1], right))
    return np.vstack((start, end))


@pytest.mark.parametrize("seed", range(5))
def test_rdp_matches_reference(seed):
    rng = np.random.default_rng(seed)
    x = np.arange(0, 100, 0.5)
    points = np.column_stack([x, np.cumsum(rng.normal(0, 0.3, len(x)))])
    for epsilon in (0.05, 0.2, 1.0):
        np.testing.assert_array_equal(rdp(points, epsilon), rdp_reference(points, epsilon))


def test_rdp_closed_line():
    points = np.array([[0, 0], [1, 1], [2, 0], [0, 0]], dtype=float)
    np.testing.assert_array_equal(rdp(points, 0.1), rdp_reference(points, 0.1))


def test_hydra_profile_follows_rules():
    # Grillig buitentalud: steil stuk, berm tussen 1:8 en 1:15, twee bermen achter elkaar, kleine stappen.
    x = np.array([-40.0, -30.0, -29.0, -20.0, -10.0, -8.0, -3.0, 0.0])
    z = np.array([2.0, 2.5, 4.0, 4.7, 5.0, 5.1, 7.0, 8.0])
    xh, zh, _, history = SchemProfile.maak_hydra_profiel(x, z)
    assert SchemProfile.hydra_fouten(xh, zh) == []
    assert np.diff(xh).min() >= 2
    assert len(history) > 1  # Er is iets aangepast
    profiel = prflcheck.Profiel(dijk_punten=[prflcheck.ProfielPunt(a, b, 1.0) for a, b in zip(xh, zh)], kruinhoogte=zh[-1])
    assert profiel.check() == prflcheck.ProfielError.OK


@pytest.mark.parametrize(
    "x, z, fout",
    [
        ([0, 1], [0, 2], "steiler dan 1:1"),
        ([0, 10, 12], [0, 0.05, 1], "flauwer dan 1:100"),
        ([0, 10, 20], [0, 1, 3], "tussen 1:8 en 1:15"),
        ([0, 2, 22, 42, 44], [0, 1, 2, 3, 4], "twee bermen achter elkaar"),
        ([0, 20, 22], [0, 1, 3], "begint of eindigt met een berm"),
    ],
)
def test_hydra_fouten(x, z, fout):
    assert fout in SchemProfile.hydra_fouten(x, z)


def test_find_crest_within_search_distance():
    x = np.array([-30.0, -2.0, 2.0, 25.0, 30.0])
    z = np.array([2.0, 6.0, 6.0, 4.0, 9.0])  # Hoger punt op 30 m (buiten het zoekgebied)
    assert schematisatie.find_crest(x, z, 10) == (-2.0, 6.0)
    assert schematisatie.find_crest(x, z, 40) == (30.0, 9.0)


def test_mean_direction_around_north():
    assert schematisatie.mean_direction([350, 10]) == 0.0
    assert schematisatie.mean_direction([170, 190, None]) == 180.0
    assert schematisatie.mean_direction([]) is None


def test_file_name():
    assert schematisatie.file_name("DV 01/a") == "DV_01_a.prfl"
    assert schematisatie.file_name("..") == "vak.prfl"


# ---------- Hele keten met een nep-dijk ----------

TOE, CREST, Z_TOE, Z_CREST = 20.0, 2.0, 2.0, 6.0  # Teen 20 m, kruin 2 m buiten de dijklijn


@pytest.fixture
def dike_tif(tmp_path):
    """
    Dijk langs y = 50 (oost-west) met de buitenzijde in het zuiden: talud 1:4.5 van y = 30 (teen, 2 m) tot y = 48
    (kruin, 6 m), kruin tot y = 52 en binnentalud 1:3 naar 2 m. Cellen van 0,5 m.
    """
    path = tmp_path / "dijk.tif"
    y = 100 - (np.arange(200) + 0.5) * 0.5  # Midden van de rijen, van noord naar zuid
    d = y - 50  # Afstand tot de dijklijn, negatief = zuid = buiten
    outside = np.interp(d, [-TOE, -CREST], [Z_TOE, Z_CREST], left=Z_TOE)
    inside = np.interp(d, [CREST, CREST + 12], [Z_CREST, Z_TOE], right=Z_TOE)
    z = np.where(d < 0, outside, inside).astype("float32")
    data = np.repeat(z[:, None], 200, axis=1)
    transform = rasterio.transform.from_origin(0, 100, 0.5, 0.5)
    with rasterio.open(
        path, "w", driver="GTiff", width=200, height=200, count=1, dtype="float32", crs=CRS, transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)
    return str(path)


@pytest.fixture
def dike_settings(tmp_path, dike_tif, monkeypatch):
    monkeypatch.setattr(ppv.ahn, "AHNTiles", lambda folder, **kwargs: FakeTiles(dike_tif, (0, 0, 100, 100)))
    dijk = write_lines(tmp_path / "dijk.shp", [(shapely.geometry.LineString([(10, 50), (90, 50)]), {"vak_id": "A"})])
    return ppv.Settings(
        cur_dir=str(tmp_path / "uit"),
        input_line=dijk,
        ahn_file_loc=str(tmp_path),
        outside_offset=5,
        outside_side="rechts",  # Rechts van een lijn naar het oosten = zuid
        interval_length=10,
        cross_length=60,
        ahn_stepsize=0.5,
    )


def test_schematise_dike(dike_settings):
    ppv.run(dike_settings)
    (result,) = ppv.schematise(dike_settings)

    assert result["check"]["naam"] == "OK"
    assert result["n_profielen"] == 8
    assert result["richting"] == pytest.approx(180, abs=0.5)  # Buitenzijde in het zuiden
    hydra = result["hydra"]
    # Eén talud van teen tot kruin (het vlakke voorland is geen berm aan het begin).
    assert len(hydra["x"]) == 2
    assert hydra["x"][0] == pytest.approx(-TOE, abs=1)
    assert hydra["z"][0] == pytest.approx(Z_TOE, abs=0.1)
    assert hydra["z"][-1] == pytest.approx(Z_CREST, abs=0.1)
    slope = (hydra["x"][-1] - hydra["x"][0]) / (hydra["z"][-1] - hydra["z"][0])
    assert slope == pytest.approx((TOE - CREST) / (Z_CREST - Z_TOE), abs=0.3)

    # .prfl, json en csv; het .prfl is te lezen en in orde.
    path = os.path.join(dike_settings.output_prfl_dir, "A.prfl")
    profiel = prflcheck.Profiel.from_file(path)
    assert profiel.check() == prflcheck.ProfielError.OK
    assert profiel.id == "A" and profiel.richting == pytest.approx(180, abs=0.5)
    assert profiel.kruinhoogte == pytest.approx(Z_CREST, abs=0.1)
    with open(dike_settings.output_schem, encoding="utf-8") as f:
        assert json.load(f)["vakken"][0]["bestand"] == "A.prfl"
    assert os.path.isfile(dike_settings.output_schem.replace(".json", ".csv"))


def test_schematise_removes_previous_files(dike_settings):
    ppv.run(dike_settings)
    # Vorige schematisatie met een vak dat er nu niet meer is, en een eigen bestand in dezelfde map.
    folder = dike_settings.output_prfl_dir
    os.makedirs(folder)
    for name in ("oud.prfl", "eigen.prfl"):
        open(os.path.join(folder, name), "w").close()
    with open(dike_settings.output_schem, "w", encoding="utf-8") as f:
        json.dump({"vakken": [{"vak": "oud", "bestand": "oud.prfl"}, {"vak": "x", "bestand": "..\\cross_lines.shp"}]}, f)

    ppv.schematise(dike_settings)
    assert sorted(os.listdir(folder)) == ["A.prfl", "eigen.prfl", "schematisatie.csv", "schematisatie.json"]
    assert os.path.isfile(dike_settings.output_cross)  # Alleen bestanden in de map zelf worden verwijderd


def test_schematise_needs_outside_line(tmp_path):
    settings = ppv.Settings(cur_dir=str(tmp_path), input_line="dijk.shp", ahn_file_loc=str(tmp_path))
    with pytest.raises(ValueError, match="buitenzijde"):
        ppv.schematise(settings)


def test_schematise_needs_profiles(tmp_path):
    settings = ppv.Settings(
        cur_dir=str(tmp_path), input_line="dijk.shp", input_line_outside="buiten.shp", ahn_file_loc=str(tmp_path)
    )
    with pytest.raises(FileNotFoundError, match="start eerst de berekening"):
        ppv.schematise(settings)


def test_main_schematiseren(dike_settings, tmp_path):
    ppv.main(
        [
            "--cur-dir", dike_settings.cur_dir, "--input-line", dike_settings.input_line,
            "--ahn-file-loc", str(tmp_path), "--outside-offset", "5", "--outside-side", "rechts",
            "--cross-length", "60", "--schematiseren", "--schem-kwantiel", "0.5",
        ]
    )
    with open(dike_settings.output_schem, encoding="utf-8") as f:
        data = json.load(f)
    assert data["instellingen"]["kwantiel"] == 0.5
    assert data["vakken"][0]["check"]["code"] == 0
