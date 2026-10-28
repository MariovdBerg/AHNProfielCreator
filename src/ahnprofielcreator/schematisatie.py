# -*- coding: utf-8 -*-
"""
Schematiseren van de AHN-profielen tot één Hydra-NL profiel (.prfl) per dijkvak.

Per vak:

1. Alle dwarslijnen met AHN-hoogtes samen tot één representatief profiel: per afstand tot de dijklijn het
   kwantiel `schem_kwantiel` van de hoogtes, met stappen van 2 m en vereenvoudigd (`SchemProfile`).
2. De kruin: het hoogste punt van dat profiel binnen `schem_kruin_zoekafstand` van de dijklijn.
3. Het buitentalud (van de buitenzijde tot de kruin) aangepast aan de regels van Hydra-NL
   (`SchemProfile.maak_hydra_profiel`): hellingen tussen 1:1 en 1:100, geen delen tussen 1:8 en 1:15,
   hoogstens twee bermen, beginnen en eindigen met een talud.
4. Weggeschreven als .prfl (versie 4) met `prflcheck` (ProfielCheckPython) en gecontroleerd met de
   Hydra/Ringtoets-regels van dat pakket.

De buitenzijde moet bekend zijn (buitenlijn opgegeven of afgeleid): de dwarslijnen beginnen dan aan de
buitenkant, zodat een negatieve afstand tot de dijklijn buiten ligt.
"""

import collections
import csv
import json
import math
import os
import re

import fiona
import numpy as np

from .profielen import SchemProfile
from .profielen.constants import DEFAULT_CROSS_ID, NODATA

XSTEP = 0.1  # [m] grid voor het representatieve profiel
HYDRA_STEP = 2.0  # [m] minimale breedte van een profieldeel
RDP_EPS = 0.2  # [m] tolerantie bij het vereenvoudigen
PRFLCHECK_URL = "https://github.com/MariovdBerg/ProfielCheckPython/archive/refs/heads/main.zip"


def import_prflcheck():
    try:
        import prflcheck
    except ImportError:
        raise ImportError(
            "Voor het schematiseren is het pakket prflcheck (ProfielCheckPython) nodig. Installeer het in de "
            f"omgeving met: pip install {PRFLCHECK_URL}"
        ) from None
    return prflcheck


def read_profiles(path, group_id):
    """
    Lees de AHN-dwarslijnen per vak als (d, z): afstand tot de dijklijn (negatief = beginzijde) en hoogte.

    Geeft per vak een lijst met dicts met 'id', 'd', 'z' (NaN zonder hoogte) en 'angle' (richting naar buiten).
    """
    profiles = collections.OrderedDict()
    with fiona.open(path) as source:
        for row in source:
            if row is None or row.geometry is None:
                continue
            props = row.properties
            xyz = np.asarray(row.geometry.coordinates, dtype=float)
            if xyz.ndim != 2 or xyz.shape[1] < 3:
                continue
            offset = math.hypot(props["mid_x"] - xyz[0, 0], props["mid_y"] - xyz[0, 1])
            d = np.hypot(xyz[:, 0] - xyz[0, 0], xyz[:, 1] - xyz[0, 1]) - offset
            z = np.where(xyz[:, 2] == NODATA, np.nan, xyz[:, 2])
            profiles.setdefault(str(props[group_id]), []).append(
                {"id": props.get(DEFAULT_CROSS_ID), "d": d, "z": z, "angle": props.get("angle")}
            )
    return profiles


def mean_direction(angles):
    """Gemiddelde richting [graden t.o.v. noord, 0-360) van hoeken in graden; None zonder hoeken."""
    angles = np.radians([a for a in angles if a is not None and not math.isnan(a)])
    if len(angles) == 0:
        return None
    mean = math.degrees(math.atan2(np.sin(angles).mean(), np.cos(angles).mean())) % 360
    return round(mean, 1) % 360


def find_crest(x, z, zoekafstand):
    """
    Kruin: het hoogste punt van de lijn (x, z) met |x| <= `zoekafstand` (afstand tot de dijklijn).

    Kandidaten zijn de knikpunten binnen die afstand en de randen van het zoekgebied (geïnterpoleerd).
    """
    lo = max(-zoekafstand, x[0])
    hi = min(zoekafstand, x[-1])
    if lo > hi:
        raise ValueError(f"Het profiel ligt niet binnen {zoekafstand:g} m van de dijklijn")
    candidates = np.concatenate([[lo, hi], x[(x >= lo) & (x <= hi)]])
    heights = np.interp(candidates, x, z)
    i = int(np.argmax(heights))
    return float(candidates[i]), float(heights[i])


def file_name(vak):
    """Bestandsnaam voor een vak: alleen letters, cijfers, '-', '_' en '.'."""
    name = re.sub(r"[^\w.-]+", "_", str(vak), flags=re.ASCII).strip("._")
    return f"{name or 'vak'}.prfl"


def schematise_vak(vak, profiles, settings):
    """Schematiseer de profielen van één vak; geeft een dict met het resultaat (zie `schematise`)."""
    result = {
        "vak": vak,
        "n_profielen": len(profiles),
        "richting": mean_direction(p["angle"] for p in profiles),
        "representatief": None,
        "kruin": None,
        "hydra": None,
        "aanpassingen": [],
        "bestand": None,
        "check": None,
        "fout": None,
    }
    # Grid binnen het bereik van de dwarslijnen, op hele stappen van XSTEP.
    half = math.floor(round(settings.cross_length / 2 / XSTEP, 6)) * XSTEP
    x, z, clean = SchemProfile.maak_geschematiseerd_profiel(
        [(p["d"], p["z"]) for p in profiles],
        -half,
        half,
        quantile=settings.schem_kwantiel,
        xstep=XSTEP,
        rdp_eps=RDP_EPS,
        min_hydra_step=HYDRA_STEP,
    )
    if clean is None or x is None or len(x) < 2:
        raise ValueError("geen profielen met AHN-hoogtes")
    result["representatief"] = {"x": _rounded(x), "z": _rounded(z)}

    crest_x, crest_z = find_crest(x, z, settings.schem_kruin_zoekafstand)
    result["kruin"] = {"x": round(crest_x, 2), "z": round(crest_z, 3)}

    # Buitentalud tot en met de kruin (de kruin hoeft geen knikpunt te zijn).
    outside = x < crest_x
    xb = np.append(x[outside], crest_x)
    zb = np.append(z[outside], crest_z)
    if len(xb) < 2:
        raise ValueError(f"geen buitentalud: de kruin (x = {crest_x:.1f} m) ligt aan het begin van de dwarslijn")
    xh, zh, _, history = SchemProfile.maak_hydra_profiel(xb, zb, rdp_eps=RDP_EPS, hydra_step=HYDRA_STEP)
    result["aanpassingen"] = [msg for msg, *_ in history[1:]]
    if len(xh) < 2:
        raise ValueError("na het aanpassen aan de Hydra-regels blijft geen buitentalud over")
    result["hydra"] = {"x": _rounded(xh), "z": _rounded(zh)}
    return result


def _rounded(values, decimals=3):
    return [round(float(v), decimals) for v in values]


def make_prfl(result, settings, prflcheck):
    """Profiel (prflcheck) uit het resultaat van `schematise_vak`: alleen dijkpunten, geen voorland of dam."""
    hydra = result["hydra"]
    punten = [prflcheck.ProfielPunt(x, z, settings.schem_ruwheid) for x, z in zip(hydra["x"], hydra["z"])]
    memo = [
        f"Geschematiseerd uit {result['n_profielen']} AHN-profielen van vak {result['vak']} (AHNProfielCreator).",
        f"Kwantiel {settings.schem_kwantiel:g}, kruin binnen {settings.schem_kruin_zoekafstand:g} m van de dijklijn "
        f"(x = {result['kruin']['x']:g} m). x = afstand tot de dijklijn [m].",
    ]
    if result["aanpassingen"]:
        memo.append("Aanpassingen: " + "; ".join(dict.fromkeys(result["aanpassingen"])) + ".")
    return prflcheck.Profiel(
        id=re.sub(r"\s+", "_", str(result["vak"])),
        richting=result["richting"] if result["richting"] is not None else math.nan,
        kruinhoogte=round(result["kruin"]["z"], 2),
        damhoogte=0.0,
        dijk_punten=punten,
        memo="\n".join(memo),
    )


def schematise(settings, logger):
    """
    Schematiseer de AHN-profielen (uitvoer van `run`) per vak tot een .prfl in `settings.output_prfl_dir`.

    Schrijft ook `schematisatie.json` (voor de webapp) en `schematisatie.csv` (overzicht per vak) en geeft
    de resultaten per vak terug.
    """
    prflcheck = import_prflcheck()
    if not settings.outside_line:
        raise ValueError(
            "Schematiseren kan alleen als de buitenzijde bekend is: geef een buitenlijn op of leid die af "
            "(outside_offset)."
        )
    if not os.path.isfile(settings.output_cross_ahn):
        raise FileNotFoundError(
            f"Er zijn nog geen AHN-profielen ('{settings.output_cross_ahn}'); start eerst de berekening."
        )

    profiles = read_profiles(settings.output_cross_ahn, settings.group_id)
    out_dir = settings.output_prfl_dir
    os.makedirs(out_dir, exist_ok=True)
    remove_previous(settings)

    results = []
    for vak, vak_profiles in profiles.items():
        try:
            result = schematise_vak(vak, vak_profiles, settings)
        except ValueError as e:
            result = {"vak": vak, "n_profielen": len(vak_profiles), "fout": str(e)}
            logger.warning(f"Vak {vak}: niet geschematiseerd, {e}.")
            results.append(result)
            continue
        profiel = make_prfl(result, settings, prflcheck)
        check = profiel.check()
        result["check"] = {"code": check.code, "naam": check.name, "omschrijving": check.description}
        result["kruinhoogte"] = profiel.kruinhoogte
        result["bestand"] = file_name(vak)
        profiel.save(os.path.join(out_dir, result["bestand"]))
        if check.code == 0:
            logger.info(f"Vak {vak}: {len(result['hydra']['x'])} punten, kruin {profiel.kruinhoogte:.2f} m NAP.")
        else:
            logger.warning(f"Vak {vak}: profiel voldoet niet aan de Hydra-regels ({check.code}: {check.description})")
        results.append(result)

    write_summary(settings, results)
    n_ok = sum(1 for r in results if r.get("check") and r["check"]["code"] == 0)
    n_files = sum(1 for r in results if r.get("bestand"))
    logger.info(f"Klaar: {n_files} van {len(results)} vakken geschematiseerd ({n_ok} zonder fouten), output in '{out_dir}'.")
    return results


def remove_previous(settings):
    """Verwijder de .prfl-bestanden van een vorige schematisatie (alleen die in diens overzicht staan)."""
    try:
        with open(settings.output_schem, encoding="utf-8") as f:
            previous = json.load(f)["vakken"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    for result in previous:
        name = result.get("bestand") if isinstance(result, dict) else None
        if name and name == os.path.basename(name):
            path = os.path.join(settings.output_prfl_dir, name)
            if os.path.isfile(path):
                os.remove(path)


def write_summary(settings, results):
    """Overzicht voor de webapp (json) en per vak (csv, met ; voor Excel)."""
    data = {
        "instellingen": {
            "kwantiel": settings.schem_kwantiel,
            "kruin_zoekafstand": settings.schem_kruin_zoekafstand,
            "ruwheid": settings.schem_ruwheid,
        },
        "vakken": results,
    }
    with open(settings.output_schem, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    columns = ["vak", "bestand", "n_profielen", "kruinhoogte", "richting", "n_punten", "check", "omschrijving"]
    with open(os.path.splitext(settings.output_schem)[0] + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(columns)
        for r in results:
            check = r.get("check") or {}
            writer.writerow(
                [
                    r["vak"],
                    r.get("bestand") or "",
                    r["n_profielen"],
                    _decimal(r.get("kruinhoogte")),
                    _decimal(r.get("richting")),
                    len(r["hydra"]["x"]) if r.get("hydra") else "",
                    check.get("code", ""),
                    check.get("omschrijving") or r.get("fout") or "",
                ]
            )


def _decimal(value):
    """Getal met decimale komma (Nederlandse Excel); leeg voor None."""
    return "" if value is None else f"{value:g}".replace(".", ",")
