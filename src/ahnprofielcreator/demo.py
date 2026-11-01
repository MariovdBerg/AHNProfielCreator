# -*- coding: utf-8 -*-
"""
Demotraject dat met het pakket meekomt: 3 km Lekdijk bij Schalkwijk (normtraject 44-1), in 6 dijkvakken.
Er is geen buitenlijn meegeleverd: die wordt afgeleid door de dijkvakken 20 m naar links te verschuiven.

De AHN5-hoogtes (DTM 0,5 m) staan in een uitgeknipte strook van 100 m aan weerszijden van de dijk, met
de naam van het kaartblad (``2023_M_39AZ1_demo.tif``). De demo werkt daardoor zonder downloads, zolang
de dwarslijnen niet langer zijn dan 200 m. Bron AHN: CC BY 4.0, AHN.
"""

import os

from .ahn import DATA_DIR

DEMO_DIR = DATA_DIR / "demo"
DEMO_NAME = "Lekdijk bij Schalkwijk (traject 44-1)"


def demo_config(cur_dir=None):
    """
    Instellingen voor het demotraject (sleutels als in `Settings`).

    De output komt in `cur_dir`, standaard ``output/demo`` in de huidige map; het pakket zelf kan
    alleen-lezen zijn. Ontbrekende bladen (bij langere dwarslijnen) worden daar ook in gedownload.
    """
    cur_dir = cur_dir or os.path.join(os.getcwd(), "output", "demo")
    return {
        "cur_dir": cur_dir,
        "input_line": str(DEMO_DIR / "demo_dijkvakken.shp"),
        # Geen buitenlijn meegeleverd: de app leidt die af. De Lek (buitenzijde) ligt links van de tekenrichting.
        "outside_offset": 20.0,
        "outside_side": "links",
        "group_id": "vak_id",
        "ahn_file_loc": str(DEMO_DIR),
        "ahn_file_downloaded": os.path.join(cur_dir, "ahn_data"),
    }
