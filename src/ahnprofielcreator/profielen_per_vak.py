# -*- coding: utf-8 -*-
"""
Genereren AHN profielen langs een traject (per dijkvak).

Omzetting van het notebook ``profielen_per_vak.ipynb``:

1. Maak interval punten langs de dijkvaklijnen.
2. Maak dwarslijnen (dwarsdoorsneden) door deze punten, georiënteerd met de buitenlijn.
3. Bepaal AHN hoogtes langs de dwarslijnen.

Gebruik vanuit Python::

    from ahnprofielcreator import Settings, run
    run(Settings(cur_dir=..., input_line=..., ...))

of vanaf de commandline::

    ahn-profielen --config config.ini
"""

import argparse
import configparser
import dataclasses
import logging
import os
import sys
from typing import Optional

from . import ahn
from .profielen import CrossSectionHeights, CrossSections


@dataclasses.dataclass
class Settings:
    # Werkmap voor opslaan
    cur_dir: str
    # Shapefile met dijkvak lijnen (bevat attribuut `group_id`)
    input_line: str
    # Map met lokale AHN tiffs; ontbrekende bladen worden hierin gedownload
    ahn_file_loc: str
    # Shapefile met buitenlijn, bepaalt de oriëntatie van de dwarslijnen (optioneel)
    input_line_outside: Optional[str] = None
    group_id: str = "vak_id"  # Attribuut in shape om te groeperen
    # Nodige AHN informatie
    ahn_file_downloaded: Optional[str] = None  # Map voor gedownloade bladen; standaard ahn_file_loc
    ahn_index: str = ahn.DEFAULT_INDEX  # Kaartbladen met downloadlinks (bladwijzer.gpkg van AHN)
    ahn_index_id: str = ahn.DEFAULT_INDEX_ID  # Attribuut in kaartbladen met de bladnaam
    ahn_versie: int = 5  # AHN-versie; bladen zonder deze versie vallen terug op AHN4
    model: str = "DTM"  # DTM (maaiveld) of DSM (oppervlakte)
    model_resolutie: float = 0.5  # [m] resolutie van het AHN-model: 0.5 of 5
    # Instellingen
    interval_length: float = 10  # [m] per meter langs normtraject
    cross_length: float = 100.0  # [m] lengte dwarsdoorsnede (helft naar beide kanten)
    ahn_stepsize: float = 0.5  # [m] stapgrootte voor AHN hoogte

    @property
    def output_dir(self):
        return output_dir(self.cur_dir, self.interval_length)

    @property
    def output_points(self):
        return os.path.join(self.output_dir, OUTPUT_POINTS)

    @property
    def output_cross(self):
        return os.path.join(self.output_dir, OUTPUT_CROSS)

    @property
    def output_cross_ahn(self):
        return os.path.join(self.output_dir, OUTPUT_CROSS_AHN)


OUTPUT_POINTS = "cross_points.shp"
OUTPUT_CROSS = "cross_lines.shp"
OUTPUT_CROSS_AHN = "cross_lines_ahn.shp"

# Vervallen instellingen: de bestandsnamen komen nu uit de downloadlinks in de kaartbladindex.
REMOVED_SETTINGS = ("ahn_file_prefix", "ahn_file_suffix", "ahn_file_transform")


def output_dir(cur_dir, interval_length):
    """Outputmap binnen de werkmap, per interval (zoals in het notebook)."""
    return os.path.join(cur_dir, f"output_per_vak_{float(interval_length):g}m")


def get_logger():
    """Logger voor de stappen en de profielen-klassen; schrijft naar de console als niemand anders luistert."""
    logger = logging.getLogger("ahnprofielcreator")
    logger.setLevel(logging.INFO)
    if len(logger.handlers) == 0:
        logger.addHandler(logging.StreamHandler())
    return logger


def run(settings):
    """Voer alle stappen uit het notebook uit en geef de AHN-dwarslijnen per groep terug."""
    logger = get_logger()

    # Eerst de kaartbladindex lezen, zodat fouten in de AHN-instellingen meteen opvallen.
    tiles = ahn.AHNTiles(
        settings.ahn_file_loc,
        download_folder=settings.ahn_file_downloaded,
        index=settings.ahn_index,
        index_id=settings.ahn_index_id,
        version=settings.ahn_versie,
        model=settings.model,
        resolution=settings.model_resolutie,
        logger=logger,
    )
    os.makedirs(settings.output_dir, exist_ok=True)

    # Initialize class voor intervalpunten en bijbehorende dwarslijnen.
    cs = CrossSections(
        settings.input_line,
        settings.output_points,
        group_id=settings.group_id,
        outside_line_file=settings.input_line_outside,
        logger=logger,
    )

    # Maak interval punten.
    logger.info(f"Stap 1/3: intervalpunten om de {settings.interval_length:g} m...")
    cs.interval_points_to_file(interval=settings.interval_length)

    # Maak dwarslijnen.
    logger.info(f"Stap 2/3: dwarslijnen van {settings.cross_length:g} m...")
    cs.crosssections_to_file(settings.output_cross, length_crosssection=settings.cross_length)

    # Initialize class.
    ch = CrossSectionHeights(
        settings.output_cross,
        group_id=settings.group_id,
        ahn_tiles=tiles,
        ahn_stepsize=settings.ahn_stepsize,
        logger=logger,
    )

    # AHN hoogtes voor dwarslijnen.
    logger.info(f"Stap 3/3: AHN{tiles.version} {tiles.product} hoogtes om de {settings.ahn_stepsize:g} m...")
    heights = ch.crosssections_height_to_file(settings.output_cross_ahn, "ahn")
    logger.info(f"Klaar, output in '{settings.output_dir}'.")
    return heights


CONFIG_SECTION = "profielen"


def load_config(path):
    """
    Lees instellingen uit sectie [profielen] van een INI-bestand (sleutels gelijk aan de velden van `Settings`).

    Lege waarden worden overgeslagen, zodat de standaardwaarde geldt. Vervallen instellingen
    (`REMOVED_SETTINGS`) worden met een waarschuwing genegeerd.
    """
    # Geen interpolatie: '%' in paden moet gewoon kunnen. Commentaar achter een waarde met ' ;' of ' #'.
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
    with open(path, encoding="utf-8-sig") as f:
        parser.read_file(f)
    if not parser.has_section(CONFIG_SECTION):
        raise ValueError(f"Sectie [{CONFIG_SECTION}] ontbreekt in '{path}'")

    fields = {f.name: f for f in dataclasses.fields(Settings)}
    config = {}
    for key, value in parser.items(CONFIG_SECTION):
        if key in REMOVED_SETTINGS:
            get_logger().warning(f"Instelling '{key}' in '{path}' wordt niet meer gebruikt en is genegeerd.")
            continue
        if key not in fields:
            raise ValueError(f"Onbekende instelling in '{path}': {key}")
        value = value.strip().strip('"')
        if value == "":
            continue
        if fields[key].type in (int, float):
            try:
                value = fields[key].type(value)
            except ValueError:
                raise ValueError(f"'{key}' in '{path}' moet een getal zijn (is '{value}')") from None
        config[key] = value

    return config


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="ahn-profielen",
        description="Genereer AHN profielen (dwarslijnen met hoogtes) langs een traject, per dijkvak.",
    )
    parser.add_argument("-c", "--config", help="INI-bestand met instellingen; losse opties overschrijven deze")
    parser.add_argument("--cur-dir", help="werkmap voor opslaan van de output")
    parser.add_argument("--input-line", help="shapefile met dijkvak lijnen")
    parser.add_argument("--input-line-outside", help="shapefile met buitenlijn (oriëntatie dwarslijnen)")
    parser.add_argument("--group-id", help="attribuut in shape om te groeperen (standaard: vak_id)")
    parser.add_argument("--ahn-index", help="AHN kaartbladen met downloadlinks (standaard: bladwijzer.gpkg)")
    parser.add_argument("--ahn-index-id", help="attribuut met bladnaam in de kaartbladen (standaard: AHN)")
    parser.add_argument("--ahn-file-loc", help="map met de AHN tiff-files")
    parser.add_argument(
        "--ahn-file-downloaded", help="map voor gedownloade AHN-bladen (standaard: --ahn-file-loc)"
    )
    parser.add_argument("--ahn-versie", type=int, help="AHN-versie (standaard: 5, met terugval op 4)")
    parser.add_argument("--model", help="DTM of DSM (standaard: DTM)")
    parser.add_argument("--model-resolutie", type=float, help="[m] resolutie AHN-model, 0.5 of 5 (standaard: 0.5)")
    parser.add_argument("--interval-length", type=float, help="[m] afstand tussen profielen (standaard: 10)")
    parser.add_argument("--cross-length", type=float, help="[m] lengte dwarsdoorsnede (standaard: 100)")
    parser.add_argument("--ahn-stepsize", type=float, help="[m] stapgrootte voor AHN hoogte (standaard: 0.5)")
    return parser, parser.parse_args(argv)


def main(argv=None):
    parser, args = parse_args(argv)

    try:
        config = load_config(args.config) if args.config else {}
    except (OSError, ValueError, configparser.Error) as e:
        parser.error(str(e))
    overrides = {k: v for k, v in vars(args).items() if k != "config" and v is not None}
    config.update(overrides)

    required = [f.name for f in dataclasses.fields(Settings) if f.default is dataclasses.MISSING]
    missing = [name for name in required if name not in config]
    if missing:
        parser.error("ontbrekende instelling(en): " + ", ".join("--" + m.replace("_", "-") for m in missing))

    run(Settings(**config))
    return 0


if __name__ == "__main__":
    sys.exit(main())
