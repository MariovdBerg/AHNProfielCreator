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
import math
import os
import sys
from typing import Optional

import fiona

from . import ahn, buitenlijn, schematisatie
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
    # Zonder buitenlijn: leid die af door de dijkvakken zoveel meter te verschuiven (0 = niet afleiden)
    outside_offset: float = 0.0
    outside_side: str = "links"  # Kant van de buitenlijn t.o.v. de lijnrichting: links of rechts
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
    # [m] lengte dwarsdoorsnede (helft naar beide kanten); 100 m buiten past bij HR-locaties op ~100 m van de kruin
    cross_length: float = 200.0
    ahn_stepsize: float = 0.5  # [m] stapgrootte voor AHN hoogte
    # Schematisatie tot een Hydra-NL profiel (.prfl) per vak (optionele stap na de berekening)
    schem_kwantiel: float = 0.2  # [-] kwantiel van de hoogtes van de profielen in een vak
    schem_kruin_zoekafstand: float = 10.0  # [m] zoek de kruin binnen deze afstand van de dijklijn
    schem_ruwheid: float = 1.0  # [-] ruwheid van het talud (grenzen uit prflcheck: 0.5 - 1.0)
    schem_voorland: str = "ja"  # Voorland (buiten de teen) in het .prfl: ja of nee
    schem_min_bermbreedte: float = 3.0  # [m] een berm is minstens zo breed
    schem_min_taludhoogte: float = 0.5  # [m] de taluds naast een berm zijn minstens zo hoog
    # Een berm telt alleen mee als de afwijking (RMSE) van het profiel er zowel relatief als absoluut zoveel door daalt
    schem_berm_winst_rel: float = 0.2  # [-] fractie, 0.2 = 20%
    schem_berm_winst_abs: float = 0.03  # [m]
    # Overige instellingen van de fit
    schem_min_taludbreedte: float = 2.0  # [m] een talud is minstens zo breed
    schem_stap: float = 0.5  # [m] raster van de knikpunten en van het representatieve profiel
    # [m] in de fit telt alleen het voorland tot zover (plus de dijkbreedte) buiten de eerst gevonden teen
    schem_voorland_fit: float = 30.0
    schem_voorland_delen: int = 1  # Delen voorland in de fit (alleen om de teen te vinden)
    schem_decimalen: int = 3  # Afronding van x en z in het .prfl

    def __post_init__(self):
        """Controleer de instellingen, zodat fouten meteen met een duidelijke melding opvallen."""
        positief = ("interval_length", "cross_length", "ahn_stepsize", "schem_kruin_zoekafstand",
                    "schem_min_taludbreedte", "schem_stap")
        for name in positief:
            value = float(getattr(self, name))
            if not value > 0:  # Ook NaN afkeuren
                raise ValueError(f"'{name}' moet groter dan 0 zijn (is {value:g})")
            setattr(self, name, value)
        for name, low, high in (
            ("schem_kwantiel", 0.0, 1.0),
            ("schem_ruwheid", *ruwheid_grenzen()),
            ("schem_min_bermbreedte", 0.0, math.inf),
            ("schem_min_taludhoogte", 0.0, math.inf),
            ("schem_berm_winst_rel", 0.0, 1.0),
            ("schem_berm_winst_abs", 0.0, math.inf),
            ("schem_voorland_fit", 0.0, math.inf),
        ):
            value = float(getattr(self, name))
            if not low <= value <= high:
                raise ValueError(f"'{name}' moet tussen {low:g} en {high:g} liggen (is {value:g})")
            setattr(self, name, value)
        for name, low, high in (("schem_voorland_delen", 1, 3), ("schem_decimalen", 1, 6)):
            value = float(getattr(self, name))
            if not (value == int(value) and low <= value <= high):
                raise ValueError(f"'{name}' moet een geheel getal van {low} tot en met {high} zijn (is {value:g})")
            setattr(self, name, int(value))
        self.schem_voorland = str(self.schem_voorland).strip().lower()
        if self.schem_voorland not in ("ja", "nee"):
            raise ValueError(f"'schem_voorland' moet ja of nee zijn (is '{self.schem_voorland}')")
        self.outside_offset = float(self.outside_offset or 0.0)
        if not self.outside_offset >= 0:
            raise ValueError(f"'outside_offset' mag niet negatief zijn (is {self.outside_offset:g})")
        self.outside_side = str(self.outside_side).strip().lower()
        if self.outside_side not in buitenlijn.ZIJDEN:
            raise ValueError(f"'outside_side' moet links of rechts zijn (is '{self.outside_side}')")
        self.model, self.model_resolutie = ahn.check_product(self.model, self.model_resolutie)
        self.ahn_versie = int(self.ahn_versie)

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

    @property
    def output_prfl_dir(self):
        return os.path.join(self.output_dir, OUTPUT_PRFL_DIR)

    @property
    def output_schem(self):
        return os.path.join(self.output_prfl_dir, OUTPUT_SCHEM)

    @property
    def outside_line(self):
        return outside_line(
            self.cur_dir, self.interval_length, self.input_line_outside, self.outside_offset
        )


def ruwheid_grenzen():
    """
    Grenzen voor de ruwheid van een dijkdeel uit de controle van prflcheck (RUWHEIDLAAG, RUWHEIDHOOG).

    prflcheck is alleen nodig voor het schematiseren; zonder dat pakket alleen 'groter dan 0'.
    """
    try:
        import prflcheck.checks as checks
    except ImportError:
        return 1e-12, math.inf
    return checks.RUWHEIDLAAG, checks.RUWHEIDHOOG


OUTPUT_POINTS = "cross_points.shp"
OUTPUT_CROSS = "cross_lines.shp"
OUTPUT_CROSS_AHN = "cross_lines_ahn.shp"
OUTPUT_OUTSIDE = "buitenlijn_offset.shp"
OUTPUT_PRFL_DIR = "prfl"  # Geschematiseerde profielen, één .prfl per vak
OUTPUT_SCHEM = "schematisatie.json"  # Overzicht van de schematisatie (in OUTPUT_PRFL_DIR, met een .csv ernaast)

# Vervallen instellingen: de bestandsnamen komen nu uit de downloadlinks in de kaartbladindex.
REMOVED_SETTINGS = ("ahn_file_prefix", "ahn_file_suffix", "ahn_file_transform")


def output_dir(cur_dir, interval_length):
    """Outputmap binnen de werkmap, per interval (zoals in het notebook)."""
    return os.path.join(cur_dir, f"output_per_vak_{float(interval_length):g}m")


def outside_line(cur_dir, interval_length, input_line_outside, outside_offset):
    """
    Buitenlijn die de berekening gebruikt: de opgegeven shapefile, anders de uit de dijkvakken
    afgeleide lijn in de outputmap (als `outside_offset` > 0), anders geen.
    """
    if input_line_outside:
        return input_line_outside
    if outside_offset and float(outside_offset) > 0 and cur_dir:
        return os.path.join(output_dir(cur_dir, interval_length), OUTPUT_OUTSIDE)
    return None


RD_EPSG = (28992, 7415)  # RD New, en RD New + NAP-hoogte (zoals de AHN-tiffs)


def check_crs(path, label):
    """
    Controleer dat een invoerbestand in RD New staat.

    Alle afstanden (interval, dwarslijnen, verschuiving) zijn in meters en de AHN-bladen in RD; een ander
    coördinatenstelsel gaat zonder deze controle ongemerkt mis (bijv. 5 graden in plaats van 5 m).
    """
    with fiona.open(path) as source:
        crs = source.crs
    if not crs or not crs.to_string():
        raise ValueError(
            f"{label} '{path}' heeft geen coördinatenstelsel (.prj ontbreekt). "
            "Ken RD New (EPSG:28992) toe, bijv. in QGIS."
        )
    epsg = crs.to_epsg()
    if epsg not in RD_EPSG:
        name = f"EPSG:{epsg}" if epsg else f"een onbekend stelsel ({crs.to_string()[:60]}...)"
        raise ValueError(
            f"{label} '{path}' staat in {name}, niet in RD New (EPSG:28992). "
            "Herprojecteer het bestand naar RD New, bijv. in QGIS."
        )


def check_input_crs(settings):
    """Controleer het coördinatenstelsel van de dijkvakken en (als opgegeven) de buitenlijn."""
    check_crs(settings.input_line, "Dijkvakken")
    if settings.input_line_outside:
        check_crs(settings.input_line_outside, "Buitenlijn")


def make_outside_line(settings, logger=None):
    """
    Leid de buitenlijn af uit de dijkvakken als er geen buitenlijn is opgegeven en `outside_offset` > 0.

    Geeft het pad van de afgeleide buitenlijn terug, of None als die niet nodig is.
    """
    logger = logger or get_logger()
    if not settings.outside_offset:
        return None
    if settings.input_line_outside:
        logger.warning("Er is een buitenlijn opgegeven; de afstand voor een afgeleide buitenlijn wordt genegeerd.")
        return None
    if settings.outside_offset >= settings.cross_length / 2:
        raise ValueError(
            f"Afstand van de afgeleide buitenlijn ({settings.outside_offset:g} m) moet kleiner zijn dan de halve "
            f"lengte van de dwarslijn ({settings.cross_length / 2:g} m), anders snijden de dwarslijnen hem niet."
        )
    check_crs(settings.input_line, "Dijkvakken")
    os.makedirs(settings.output_dir, exist_ok=True)
    path = settings.outside_line
    count = buitenlijn.offset_lines_to_file(
        settings.input_line, path, settings.outside_offset, settings.outside_side
    )
    logger.info(
        f"Buitenlijn afgeleid: dijkvakken {settings.outside_offset:g} m naar {settings.outside_side} "
        f"verschoven ({count} lijnen), '{path}'."
    )
    return path


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
    check_input_crs(settings)
    os.makedirs(settings.output_dir, exist_ok=True)
    make_outside_line(settings, logger)

    # Initialize class voor intervalpunten en bijbehorende dwarslijnen.
    cs = CrossSections(
        settings.input_line,
        settings.output_points,
        group_id=settings.group_id,
        outside_line_file=settings.outside_line,
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
    heights = ch.crosssections_height_to_file(settings.output_cross_ahn)
    logger.info(f"Klaar, output in '{settings.output_dir}'.")
    return heights


def schematise(settings):
    """Optionele stap na `run`: schematiseer de AHN-profielen per vak tot een Hydra-NL profiel (.prfl)."""
    logger = get_logger()
    logger.info(f"Schematiseren: één Hydra-NL profiel (.prfl) per vak, kwantiel {settings.schem_kwantiel:g}...")
    return schematisatie.schematise(settings, logger)


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
    parser.add_argument(
        "--outside-offset",
        type=float,
        help="[m] zonder buitenlijn: leid die af door de dijkvakken zoveel te verschuiven (standaard: 0 = niet)",
    )
    parser.add_argument(
        "--outside-side",
        choices=buitenlijn.ZIJDEN,
        help="kant van de afgeleide buitenlijn t.o.v. de lijnrichting (standaard: links)",
    )
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
    parser.add_argument("--cross-length", type=float, help="[m] lengte dwarsdoorsnede (standaard: 200)")
    parser.add_argument("--ahn-stepsize", type=float, help="[m] stapgrootte voor AHN hoogte (standaard: 0.5)")
    parser.add_argument(
        "--schematiseren",
        action="store_true",
        help="schematiseer daarna de profielen per vak tot een Hydra-NL profiel (.prfl); nodig: prflcheck",
    )
    parser.add_argument("--schem-kwantiel", type=float, help="kwantiel van de hoogtes per vak (standaard: 0.2)")
    parser.add_argument(
        "--schem-kruin-zoekafstand", type=float, help="[m] zoek de kruin binnen deze afstand van de dijklijn (standaard: 10)"
    )
    parser.add_argument("--schem-ruwheid", type=float, help="ruwheid van het talud, 0.5 - 1.0 (standaard: 1.0)")
    parser.add_argument("--schem-voorland", choices=("ja", "nee"), help="voorland in het .prfl (standaard: ja)")
    parser.add_argument("--schem-min-bermbreedte", type=float, help="[m] minimale breedte van een berm (standaard: 3)")
    parser.add_argument(
        "--schem-min-taludhoogte", type=float, help="[m] minimale hoogte van de taluds naast een berm (standaard: 0.5)"
    )
    parser.add_argument(
        "--schem-berm-winst-rel", type=float, help="berm telt mee als de RMSE minstens deze fractie daalt (standaard: 0.2)"
    )
    parser.add_argument(
        "--schem-berm-winst-abs", type=float, help="[m] en ook minstens zoveel daalt (standaard: 0.03)"
    )
    parser.add_argument("--schem-min-taludbreedte", type=float, help="[m] minimale breedte van een talud (standaard: 2)")
    parser.add_argument("--schem-stap", type=float, help="[m] raster van de schematisatie (standaard: 0.5)")
    parser.add_argument(
        "--schem-voorland-fit", type=float, help="[m] voorland in de fit tot zover buiten de teen (standaard: 30)"
    )
    parser.add_argument("--schem-voorland-delen", type=int, help="delen voorland in de fit, 1-3 (standaard: 1)")
    parser.add_argument("--schem-decimalen", type=int, help="decimalen van x en z in het .prfl, 1-6 (standaard: 3)")
    return parser, parser.parse_args(argv)


def main(argv=None):
    parser, args = parse_args(argv)

    try:
        config = load_config(args.config) if args.config else {}
    except (OSError, ValueError, configparser.Error) as e:
        parser.error(str(e))
    overrides = {k: v for k, v in vars(args).items() if k not in ("config", "schematiseren") and v is not None}
    config.update(overrides)

    required = [f.name for f in dataclasses.fields(Settings) if f.default is dataclasses.MISSING]
    missing = [name for name in required if name not in config]
    if missing:
        parser.error("ontbrekende instelling(en): " + ", ".join("--" + m.replace("_", "-") for m in missing))

    try:
        settings = Settings(**config)
    except ValueError as e:
        parser.error(str(e))
    if args.schematiseren:
        schematisatie.import_prflcheck()  # Meteen melden als prflcheck ontbreekt, niet pas na de berekening
    run(settings)
    if args.schematiseren:
        schematise(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
