# -*- coding: utf-8 -*-
"""
Demotraject in de ontwikkelmap: 3 km Lekdijk bij Schalkwijk (normtraject 44-1), in 6 dijkvakken.
Er is geen buitenlijn meegeleverd: die wordt afgeleid door de dijkvakken 20 m naar links te verschuiven.

Het is het project ``demo`` in de werkmap (de map met ``src``): de invoer in ``demo/invoer``, de uitvoer in
``demo/uitvoer`` en de instellingen in ``demo/config.ini``. De AHN5-hoogtes (DTM 0,5 m) staan in een
uitgeknipte strook van 100 m aan weerszijden van de dijk, met de naam van het kaartblad
(``invoer/AHN/AHN5/DTM05/2023_M_39AZ1_demo.tif``; de AHN-map is ``invoer/AHN``). De demo werkt daardoor zonder downloads, zolang de dwarslijnen niet langer zijn
dan 200 m. Bron AHN: CC BY 4.0, AHN.
"""

import os
from pathlib import Path

from . import profielen_per_vak as ppv

DEMO_DIR = Path(__file__).resolve().parents[2] / "demo"
DEMO_NAME = "Lekdijk bij Schalkwijk (traject 44-1)"


def demo_config(projectmap=None):
    """
    Instellingen voor het demotraject (sleutels als in `Settings`), uit ``demo/config.ini``.

    Met `projectmap` komt de uitvoer daar in plaats van in ``demo/uitvoer``; de invoerpaden wijzen dan
    naar de invoer van de demo.
    """
    config = ppv.load_config(DEMO_DIR / ppv.CONFIG_FILE)
    if projectmap:
        for name in ppv.INPUT_PATHS:
            if name in config:
                config[name] = ppv.input_path(str(DEMO_DIR), config[name])
    config["projectmap"] = str(projectmap or DEMO_DIR)
    return config


def is_demo(projectmap):
    """True als `projectmap` het demotraject is."""
    return bool(projectmap) and os.path.isdir(projectmap) and os.path.samefile(projectmap, DEMO_DIR)
