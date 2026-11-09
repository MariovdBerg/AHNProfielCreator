# -*- coding: utf-8 -*-
"""
Schematiseren van de AHN-profielen tot één Hydra-NL profiel (.prfl) per dijkvak.

Per vak (zie `profielen.schem_cross`):

1. Alle dwarslijnen met AHN-hoogtes samen tot één representatief profiel: per afstand tot de dijklijn (stappen
   van `schem_stap`) het kwantiel `schem_kwantiel` van de hoogtes.
2. De kruin: het hoogste punt van dat profiel binnen `schem_kruin_zoekafstand` van de dijklijn.
3. Eén fit van voorland | talud | (berm | talud) x k | kruin, met de hellingen begrensd volgens de regels van
   prflcheck, voor 0 tot het maximale aantal bermen. Een berm telt mee volgens de bermcriteria
   (`schem_min_bermbreedte`, `schem_min_taludhoogte`, `schem_berm_winst_rel` en `schem_berm_winst_abs`); de overige
   instellingen van de fit zijn `schem_min_taludbreedte`, `schem_stap`, `schem_voorland_fit`,
   `schem_voorland_delen` en `schem_decimalen`.
4. Optioneel (`schem_voorland`) het voorland buiten de teen, volgens de regels van prflcheck.
5. Weggeschreven als .prfl (versie 4) met `prflcheck` (ProfielCheckPython) en gecontroleerd met de
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

from .profielen import schem_cross
from .profielen.constants import DEFAULT_CROSS_ID, NODATA

PRFLCHECK_URL = schem_cross.PRFLCHECK_URL
import_prflcheck = schem_cross.laad_prflcheck

TRAJECT_FIELD = "traject"  # Attribuut van de dijkvakken met het (norm)traject; optioneel


def read_profiles(path, group_id):
    """
    Lees de AHN-dwarslijnen per vak als (d, z): afstand tot de dijklijn (negatief = beginzijde) en hoogte.

    Geeft per vak een lijst met dicts met 'id', 'd', 'z' (NaN zonder hoogte), 'angle' (richting naar buiten) en
    'traject' (attribuut `TRAJECT_FIELD`, hoofdletterongevoelig; None als dat ontbreekt).
    """
    profiles = collections.OrderedDict()
    with fiona.open(path) as source:
        traject_field = next((k for k in source.schema["properties"] if k.lower() == TRAJECT_FIELD), None)
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
                {
                    "id": props.get(DEFAULT_CROSS_ID), "d": d, "z": z, "angle": props.get("angle"),
                    "traject": props.get(traject_field) if traject_field else None,
                }
            )
    return profiles


def trajecten(profiles):
    """De verschillende trajecten van de profielen van een vak, in volgorde van voorkomen (lege overgeslagen)."""
    names = dict.fromkeys(str(p["traject"]).strip() for p in profiles if p.get("traject") is not None)
    names.pop("", None)
    return list(names)


def traject(profiles):
    """Traject van de profielen van een vak (zie `check_trajecten`); None zonder."""
    return ", ".join(trajecten(profiles)) or None


def check_trajecten(profiles):
    """Fout (ValueError) als een vak in meer dan één traject ligt; noemt alle vakken waarvoor dat geldt."""
    fout = {vak: names for vak, names in ((v, trajecten(p)) for v, p in profiles.items()) if len(names) > 1}
    if fout:
        raise ValueError(
            "Een vak hoort in één traject te liggen; controleer het attribuut "
            f"'{TRAJECT_FIELD}' van de dijkvakken. In meerdere trajecten: "
            + "; ".join(f"vak {vak} ({', '.join(names)})" for vak, names in fout.items())
        )


def mean_direction(angles):
    """Gemiddelde richting [graden t.o.v. noord, 0-360) van hoeken in graden; None zonder hoeken."""
    angles = np.radians([a for a in angles if a is not None and not math.isnan(a)])
    if len(angles) == 0:
        return None
    mean = math.degrees(math.atan2(np.sin(angles).mean(), np.cos(angles).mean())) % 360
    return round(mean, 1) % 360


def file_name(vak):
    """Bestandsnaam voor een vak: alleen letters, cijfers, '-', '_' en '.'."""
    name = re.sub(r"[^\w.-]+", "_", str(vak), flags=re.ASCII).strip("._")
    return f"{name or 'vak'}.prfl"


def profiel_id(result):
    """ID in het .prfl: '{traject}_{vak}', of alleen het vak zonder traject; witruimte wordt '_'."""
    name = f"{result['traject']}_{result['vak']}" if result.get("traject") else str(result["vak"])
    return re.sub(r"\s+", "_", name.strip())


def fit_instellingen(settings):
    """Instellingen van de fit (`schem_cross.FitInstellingen`) uit de instellingen `schem_*`."""
    return schem_cross.FitInstellingen(
        **{f.name: getattr(settings, "schem_" + f.name) for f in schem_cross.dataclasses.fields(schem_cross.FitInstellingen)}
    )


def _line(x, z, decimals=3):
    return {"x": _rounded(x, decimals), "z": _rounded(z, decimals)}


def _rounded(values, decimals=3):
    return [round(float(v), decimals) for v in values]


def schematise_vak(vak, profiles, settings):
    """Schematiseer de profielen van één vak; geeft een dict met het resultaat (zie `schematise`)."""
    result = {
        "vak": vak,
        "traject": traject(profiles),
        "n_profielen": len(profiles),
        "richting": mean_direction(p["angle"] for p in profiles),
        "representatief": None,
        "kruin": None,
        "voorland": None,
        "voorland_verlaagd": 0.0,
        "hydra": None,
        "bermen": None,
        "rmse": None,
        "bestand": None,
        "check": None,
        "fout": None,
    }
    inst = fit_instellingen(settings)
    x, z = schem_cross.representatief_profiel(
        [(p["d"], p["z"]) for p in profiles], settings.cross_length / 2, settings.schem_kwantiel, inst.stap
    )
    ok = ~np.isnan(z)
    if ok.sum() < 2:
        raise ValueError("geen profielen met AHN-hoogtes")
    result["representatief"] = _line(x[ok], z[ok])

    kruin_x, kruin_z = schem_cross.zoek_kruin(x, z, settings.schem_kruin_zoekafstand)
    result["kruin"] = {"x": round(kruin_x, 2), "z": round(kruin_z, 3)}

    gekozen, modellen = schem_cross.fit_profiel(x, z, kruin_x, kruin_z, inst)
    model = modellen[gekozen]
    # Dijkpunten: van de teen tot de buitenkruinlijn (zonder het begin van het voorland en de kruintop).
    result["hydra"] = _line(model["dijk_x"], model["dijk_z"], inst.decimalen)
    result["bermen"] = gekozen
    result["rmse"] = {str(k): round(m["rmse"], 3) for k, m in modellen.items()}

    if settings.schem_voorland == "ja":
        teen_x, teen_z = result["hydra"]["x"][0], result["hydra"]["z"][0]
        punten, verlaagd = schem_cross.voorland_punten(x, z, teen_x, teen_z, inst)
        if punten is not None:
            result["voorland"] = _line(punten[:, 0], punten[:, 1], inst.decimalen)
            result["voorland_verlaagd"] = verlaagd
    return result


def make_prfl(result, settings, prflcheck):
    """Profiel (prflcheck) uit het resultaat van `schematise_vak`: voorland (optioneel) en dijk, geen dam."""
    hydra, voorland = result["hydra"], result["voorland"]
    r = settings.schem_ruwheid
    dijk = [prflcheck.ProfielPunt(x, z, r) for x, z in zip(hydra["x"], hydra["z"])]
    voorland_punten = [prflcheck.ProfielPunt(x, z, r) for x, z in zip(voorland["x"], voorland["z"])] if voorland else []
    rmse = result["rmse"]
    bermen = result["bermen"]
    memo = [
        f"Geschematiseerd uit {result['n_profielen']} AHN-profielen van vak {result['vak']} (AHNProfielCreator).",
        f"x = afstand tot de dijklijn [m]. Kwantiel {settings.schem_kwantiel:g}; kruin binnen "
        f"{settings.schem_kruin_zoekafstand:g} m van de dijklijn (x = {result['kruin']['x']:g} m).",
        f"{bermen} berm(en), RMSE {rmse[str(bermen)]:.2f} m (" + ", ".join(f"{k} berm(en): {v:.2f} m" for k, v in rmse.items()) + ").",
    ]
    if voorland:
        regel = f"Voorland: {len(voorland['x'])} punten"
        if result["voorland_verlaagd"] > 0:
            regel += f"; eerste punt {result['voorland_verlaagd']:.2f} m verlaagd (geen voorlandpunt lager dan het eerste)"
        memo.append(regel + ".")
    return prflcheck.Profiel(
        id=profiel_id(result),
        richting=result["richting"] if result["richting"] is not None else math.nan,
        # Naar boven afronden: een lagere kruinhoogte dan het laatste dijkpunt keurt Hydra af (regel 122).
        kruinhoogte=math.ceil(round(result["kruin"]["z"] * 100, 6)) / 100,
        damhoogte=0.0,
        voorland_punten=voorland_punten,
        dijk_punten=dijk,
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
    schem_cross.hydra_grenzen(fit_instellingen(settings))  # Onmogelijke instellingen meteen melden, niet per vak

    profiles = read_profiles(settings.output_cross_ahn, settings.group_id)
    check_trajecten(profiles)  # Vóór het opruimen, zodat de vorige schematisatie blijft staan
    out_dir = settings.output_prfl_dir
    os.makedirs(out_dir, exist_ok=True)
    remove_previous(settings)

    results = []
    for vak, vak_profiles in profiles.items():
        try:
            result = schematise_vak(vak, vak_profiles, settings)
        except ValueError as e:
            result = {"vak": vak, "traject": traject(vak_profiles), "n_profielen": len(vak_profiles), "fout": str(e)}
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
            voorland = f", voorland {len(result['voorland']['x'])} punten" if result["voorland"] else ""
            logger.info(
                f"Vak {vak}: {result['bermen']} berm(en){voorland}, kruin {profiel.kruinhoogte:.2f} m NAP, "
                f"RMSE {result['rmse'][str(result['bermen'])]:.2f} m."
            )
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
        # Alle instellingen schem_* (zonder voorvoegsel), bijv. 'kwantiel'
        "instellingen": {k[len("schem_") :]: v for k, v in vars(settings).items() if k.startswith("schem_")},
        "vakken": results,
    }
    with open(settings.output_schem, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    columns = [
        "vak", "traject", "bestand", "n_profielen", "kruinhoogte", "richting", "bermen", "n_dijkpunten", "n_voorlandpunten",
        "voorland_verlaagd", "rmse", "check", "omschrijving",
    ]
    with open(os.path.splitext(settings.output_schem)[0] + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(columns)
        for r in results:
            check = r.get("check") or {}
            writer.writerow(
                [
                    r["vak"],
                    r.get("traject") or "",
                    r.get("bestand") or "",
                    r["n_profielen"],
                    _decimal(r.get("kruinhoogte")),
                    _decimal(r.get("richting")),
                    "" if r.get("bermen") is None else r["bermen"],
                    len(r["hydra"]["x"]) if r.get("hydra") else "",
                    len(r["voorland"]["x"]) if r.get("voorland") else "",
                    _decimal(r.get("voorland_verlaagd") if r.get("voorland") else None),
                    _decimal(r["rmse"][str(r["bermen"])] if r.get("rmse") else None),
                    check.get("code", ""),
                    check.get("omschrijving") or r.get("fout") or "",
                ]
            )


def _decimal(value):
    """Getal met decimale komma (Nederlandse Excel); leeg voor None."""
    return "" if value is None else f"{value:g}".replace(".", ",")
