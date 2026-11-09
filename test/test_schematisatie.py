"""Schematisatie van de AHN-profielen tot een Hydra-NL profiel (.prfl) per vak."""

import csv
import json
import os

import fiona
import numpy as np
import pytest
import rasterio
import rasterio.transform
import shapely.geometry
from conftest import CRS, FakeTiles, write_lines

from ahnprofielcreator import profielen_per_vak as ppv
from ahnprofielcreator import schematisatie
from ahnprofielcreator.profielen import schem_cross
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


# ---------- Fit van voorland, talud, bermen en kruin ----------

# Synthetische profielen: knikpunten (x, z) van voorland-begin tot kruintop (x = 2), buitenkruinlijn op x = -2.
SYNTHETISCH = {
    0: ([-50, -22, -2, 2], [3.0, 3.2, 9.0, 9.0]),
    1: ([-50, -40, -30, -15, -4, 2], [2.5, 2.6, 4.6, 4.9, 8.5, 8.6]),  # Brede, flauwe berm (1:50)
    2: ([-50, -42, -34, -26, -18, -10, -2, 2], [2.0, 2.2, 4.2, 4.52, 6.0, 6.2, 8.8, 8.8]),  # 1:25 en 1:40
}


def synthetisch(bermen, ruis, seed=1):
    kx, kz = SYNTHETISCH[bermen]
    x = np.arange(-50, 2.01, schem_cross.FitInstellingen().stap)
    z = np.interp(x, kx, kz) + np.random.default_rng(seed).normal(0, ruis, len(x))
    return x, z, kx, kz


@pytest.mark.parametrize("bermen", [0, 1, 2])
@pytest.mark.parametrize("ruis", [0.05, 0.15])
def test_fit_finds_number_and_position_of_berms(bermen, ruis):
    x, z, kx, kz = synthetisch(bermen, ruis)
    gekozen, modellen = schem_cross.fit_profiel(x, z, 2.0, kz[-1])
    assert gekozen == bermen
    model = modellen[gekozen]
    # Knikpunten van teen tot buitenkruinlijn binnen 1,5 m van de werkelijke
    np.testing.assert_allclose(model["dijk_x"], kx[1:-1], atol=1.5)
    assert model["soorten"] == "vt" + "bt" * bermen + "k"
    assert schem_cross.controleer(model["dijk_x"].round(3), model["dijk_z"].round(3)) == prflcheck.ProfielError.OK


def test_berm_criteria_can_reject_berm():
    x, z, _, kz = synthetisch(1, 0.05)
    # Met een eis van 5 m hoge taluds naast de berm is de berm van 2 m boven de teen geen berm meer
    gekozen, modellen = schem_cross.fit_profiel(x, z, 2.0, kz[-1], schem_cross.FitInstellingen(min_taludhoogte=5))
    assert gekozen == 0 and (1 not in modellen or not modellen[1]["geldig"])
    # Met een te hoge eis aan de verbetering ook niet
    gekozen, _ = schem_cross.fit_profiel(x, z, 2.0, kz[-1], schem_cross.FitInstellingen(berm_winst_abs=1.0))
    assert gekozen == 0


def test_fit_respects_slope_limits():
    # Te steil (1:0.5), te flauw (1:200) en tussen 1:8 en 1:15: het resultaat volgt toch de Hydra-regels.
    x = np.arange(-40, 0.01, schem_cross.FitInstellingen().stap)
    z = np.interp(x, [-40, -30, -20, -10, -8, 0], [2.0, 2.05, 3.0, 3.8, 7.8, 8.0])
    gekozen, modellen = schem_cross.fit_profiel(x, z, 0.0, 8.0)
    model = modellen[gekozen]
    assert schem_cross.controleer(model["dijk_x"].round(3), model["dijk_z"].round(3)) == prflcheck.ProfielError.OK


def test_voorland_follows_hydra_rules():
    x = np.arange(-100, -19.99, schem_cross.FitInstellingen().stap)
    # Uiterwaard op 4 m met een kade (5 m) en een laagte (3 m) bij de teen op x = -20
    z = np.interp(x, [-100, -70, -65, -60, -30, -25, -20], [4.0, 4.0, 5.0, 4.0, 4.0, 3.0, 3.2])
    punten, verlaagd = schem_cross.voorland_punten(x, z, -20.0, 3.2)
    assert punten[-1].tolist() == [-20.0, 3.2]  # Eindigt op de teen
    dx, dz = np.diff(punten[:, 0]), np.diff(punten[:, 1])
    assert (dx >= 10).all()
    assert (np.abs(dz / dx) <= 0.1).all()
    assert (punten[1:, 1] >= punten[0, 1]).all()  # Geen punt lager dan het eerste
    assert verlaagd > 0  # De laagte bij de teen ligt lager dan het begin: eerste punt verlaagd


def test_voorland_too_short():
    x = np.arange(-25, -19.99, schem_cross.FitInstellingen().stap)
    assert schem_cross.voorland_punten(x, np.full(len(x), 3.0), -20.0, 3.0) == (None, 0.0)


def test_representative_profile_quantile_and_coverage():
    d = np.arange(-10, 10.01, 1.0)
    profielen = [(d, np.full(len(d), h)) for h in (1.0, 2.0, 3.0, 4.0, 5.0)]
    profielen.append((d, np.full(len(d), np.nan)))
    x, z = schem_cross.representatief_profiel(profielen, 20, kwantiel=0.5)
    assert z[x == 0][0] == pytest.approx(3.0)
    assert np.isnan(z[x == -15][0])  # Buiten de dwarslijnen


def test_limits_come_from_prflcheck(monkeypatch):
    checks = prflcheck.checks
    inst = schem_cross.FitInstellingen()
    grenzen = schem_cross.hydra_grenzen(inst)
    # Binnen de grenzen van prflcheck, met hooguit een kleine marge
    lo, hi = grenzen.tan["t"]
    assert checks.TANASTL < lo < checks.TANASTL * 1.05 and checks.TANAMAX * 0.99 < hi < checks.TANAMAX
    lo, hi = grenzen.tan["b"]
    assert checks.TANAMIN < lo < checks.TANAMIN * 1.05 and checks.TANAFLW * 0.98 < hi < checks.TANAFLW
    assert checks.TANAMINVOORLAND * 0.99 < grenzen.voorland_tan < checks.TANAMINVOORLAND
    assert grenzen.max_bermen == checks.NBERMEN and grenzen.min_voorland == checks.DELTAXMINVOORLAND
    # Een wijziging in prflcheck werkt door
    monkeypatch.setattr(checks, "TANASTL", 1 / 6)
    monkeypatch.setattr(checks, "NBERMEN", 1)
    grenzen = schem_cross.hydra_grenzen(inst)
    assert 1 / 6 < grenzen.tan["t"][0] < 1 / 5.9 and grenzen.max_bermen == 1
    x, z, _, kz = synthetisch(2, 0.05)
    gekozen, modellen = schem_cross.fit_profiel(x, z, 2.0, kz[-1])
    assert gekozen <= 1 and max(modellen) == 1


def test_too_few_decimals_for_limits():
    with pytest.raises(ValueError, match="decimalen"):
        schem_cross.hydra_grenzen(schem_cross.FitInstellingen(decimalen=1))


def test_ruwheid_limits_from_prflcheck(monkeypatch):
    monkeypatch.setattr(prflcheck.checks, "RUWHEIDLAAG", 0.7)
    with pytest.raises(ValueError, match="schem_ruwheid"):
        ppv.Settings(projectmap="uit", input_line="dijk.shp", ahn_file_loc="ahn", schem_ruwheid=0.6)


def test_fit_settings_are_used(tmp_path):
    x, z, _, kz = synthetisch(1, 0.05)
    gekozen, modellen = schem_cross.fit_profiel(x, z, 2.0, kz[-1], schem_cross.FitInstellingen(stap=1.0, decimalen=2))
    assert gekozen == 1
    assert np.allclose(modellen[1]["knopen"] % 1.0, 0)  # Knikpunten op het raster van 1 m
    model = modellen[1]
    assert schem_cross.controleer(model["dijk_x"].round(2), model["dijk_z"].round(2)) == prflcheck.ProfielError.OK
    gekozen, modellen = schem_cross.fit_profiel(x, z, 2.0, kz[-1], schem_cross.FitInstellingen(voorland_delen=2))
    assert modellen[0]["soorten"] == "vvtk"
    settings = ppv.Settings(
        projectmap="uit", input_line="dijk.shp", ahn_file_loc="ahn", schem_stap=1, schem_decimalen=2,
        schem_min_taludbreedte=3, schem_voorland_fit=20, schem_voorland_delen=2,
    )
    inst = schematisatie.fit_instellingen(settings)
    assert (inst.stap, inst.decimalen, inst.min_taludbreedte, inst.voorland_fit, inst.voorland_delen) == (1, 2, 3, 20, 2)


def test_zoek_kruin_within_search_distance():
    x = np.array([-30.0, -2.0, 2.0, 25.0, 30.0])
    z = np.array([2.0, 6.0, 6.0, 4.0, 9.0])  # Hoger punt op 30 m (buiten het zoekgebied)
    assert schem_cross.zoek_kruin(x, z, 10) == (-2.0, 6.0)  # Bij gelijke hoogte het verst naar buiten
    assert schem_cross.zoek_kruin(x, z, 40) == (30.0, 9.0)


def test_mean_direction_around_north():
    assert schematisatie.mean_direction([350, 10]) == 0.0
    assert schematisatie.mean_direction([170, 190, None]) == 180.0
    assert schematisatie.mean_direction([]) is None


def test_traject():
    assert schematisatie.traject([{"traject": "44-1"}, {"traject": " 44-1"}, {"traject": "43-6"}]) == "44-1, 43-6"
    assert schematisatie.traject([{"traject": None}, {"traject": ""}, {}]) is None


def test_profiel_id():
    assert schematisatie.profiel_id({"vak": "DV 01", "traject": "44-1"}) == "44-1_DV_01"
    assert schematisatie.profiel_id({"vak": "DV_01", "traject": None}) == "DV_01"  # Zonder traject alleen het vak


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
    dijk = write_lines(
        tmp_path / "dijk.shp",
        [(shapely.geometry.LineString([(10, 50), (90, 50)]), {"vak_id": "A", "traject": "44-1"})],
        properties={"vak_id": "str", "traject": "str"},
    )
    return ppv.Settings(
        projectmap=str(tmp_path / "uit"),
        input_line=dijk,
        ahn_file_loc=str(tmp_path),
        outside_offset=5,
        outside_side="rechts",  # Rechts van een lijn naar het oosten = zuid
        interval_length=10,
        cross_length=80,  # 40 m buiten: 20 m voorland voor de teen
        ahn_stepsize=0.5,
    )


def test_schematise_dike(dike_settings):
    ppv.run(dike_settings)
    (result,) = ppv.schematise(dike_settings)

    assert result["check"]["naam"] == "OK"
    assert result["n_profielen"] == 8
    assert result["richting"] == pytest.approx(180, abs=0.5)  # Buitenzijde in het zuiden
    hydra = result["hydra"]
    # Eén talud van teen tot kruin (het vlakke voorland is geen berm), voorland van 20 m tot de teen.
    assert result["bermen"] == 0
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
    assert profiel.id == "44-1_A" and profiel.richting == pytest.approx(180, abs=0.5)
    assert profiel.kruinhoogte == pytest.approx(Z_CREST, abs=0.1)
    voorland = result["voorland"]
    assert voorland["x"][0] == pytest.approx(-40, abs=0.5) and voorland["x"][-1] == hydra["x"][0]
    assert voorland["z"] == pytest.approx([Z_TOE] * len(voorland["z"]), abs=0.05)
    assert profiel.voorland == len(voorland["x"])
    with open(dike_settings.output_schem, encoding="utf-8") as f:
        assert json.load(f)["vakken"][0]["bestand"] == "A.prfl"
    assert result["traject"] == "44-1"
    with open(dike_settings.output_schem.replace(".json", ".csv"), encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    assert len(rows) == 1 and rows[0]["vak"] == "A" and rows[0]["traject"] == "44-1"


def test_schematise_stops_for_vak_in_more_trajecten(dike_settings):
    # Profielen van vak A met een ander traject (de dijkvakken zelf hebben een unieke vak_id met één traject):
    # dat mag niet, de schematisatie stopt zonder iets te schrijven.
    ppv.run(dike_settings)
    path = dike_settings.output_cross_ahn
    with fiona.open(path) as src:
        meta, rows = src.meta, list(src)
    with fiona.open(path, "w", **meta) as dst:
        for i, row in enumerate(rows):
            props = dict(row.properties, traject="43-6" if i >= len(rows) // 2 else "44-1")
            dst.write(fiona.Feature(geometry=row.geometry, properties=fiona.Properties(**props)))
    with pytest.raises(ValueError, match=r"vak A \(44-1, 43-6\)"):
        ppv.schematise(dike_settings)
    assert not os.path.exists(dike_settings.output_prfl_dir)


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
    settings = ppv.Settings(projectmap=str(tmp_path), input_line="dijk.shp", ahn_file_loc=str(tmp_path))
    with pytest.raises(ValueError, match="buitenzijde"):
        ppv.schematise(settings)


def test_schematise_needs_profiles(tmp_path):
    settings = ppv.Settings(
        projectmap=str(tmp_path), input_line="dijk.shp", input_line_outside="buiten.shp", ahn_file_loc=str(tmp_path)
    )
    with pytest.raises(FileNotFoundError, match="start eerst de berekening"):
        ppv.schematise(settings)


def test_main_schematiseren(dike_settings, tmp_path):
    ppv.main(
        [
            "--projectmap", dike_settings.projectmap, "--input-line", dike_settings.input_line,
            "--ahn-file-loc", str(tmp_path), "--outside-offset", "5", "--outside-side", "rechts",
            "--cross-length", "60", "--schematiseren", "--schem-kwantiel", "0.5", "--schem-voorland", "nee",
        ]
    )
    with open(dike_settings.output_schem, encoding="utf-8") as f:
        data = json.load(f)
    assert data["instellingen"]["kwantiel"] == 0.5
    assert data["vakken"][0]["check"]["code"] == 0
    assert data["instellingen"]["voorland"] == "nee" and data["vakken"][0]["voorland"] is None


def test_works_without_prflcheck(monkeypatch):
    """Zonder prflcheck werkt alles behalve het schematiseren, met een duidelijke melding."""
    for name in ("prflcheck", "prflcheck.checks"):
        monkeypatch.setitem(__import__("sys").modules, name, None)  # import faalt
    settings = ppv.Settings(projectmap="uit", input_line="dijk.shp", ahn_file_loc="ahn", schem_ruwheid=0.3)
    assert settings.schem_ruwheid == 0.3  # Zonder prflcheck geen grenzen voor de ruwheid
    with pytest.raises(ImportError, match="pip install"):
        schem_cross.hydra_grenzen(schem_cross.FitInstellingen())
