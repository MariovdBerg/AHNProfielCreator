"""
Testgevallen voor de regressietest (test_reference.py) en het opnieuw maken van de referentie-uitvoer.

De gevallen zijn het demotraject en de stukken normtraject in ``test/test_input/<geval>`` (zie de README daar).
Per geval staat de referentie in ``test/test_output/<geval>``. De AHN-hoogtes van het demotraject komen uit
de meegeleverde strook; die van de andere gevallen uit de AHN5-tiffs in ``AHN_TEST_DIR`` (standaard de
HKV-map op P:). Er wordt nooit gedownload en er is geen terugval op AHN4.

Referentie opnieuw maken (na een bedoelde wijziging), in de geactiveerde omgeving vanuit de projectmap::

    python test/referentie.py            # alle gevallen
    python test/referentie.py ringen     # één geval
"""

import os
import shutil
import sys
from pathlib import Path

import fiona
import shapely.geometry

ROOT = Path(__file__).parent
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT.parent / "src"))

from ahnprofielcreator import ahn, buitenlijn  # noqa: E402
from ahnprofielcreator.demo import DEMO_DIR  # noqa: E402
from ahnprofielcreator.profielen import CrossSectionHeights, CrossSections  # noqa: E402

INPUT = ROOT / "test_input"
OUTPUT = ROOT / "test_output"
AHN_DIR = os.environ.get("AHN_TEST_DIR", r"P:\OpenData\AHN\AHN5\DTM05")

GROUP_ID = "vak_id"
AHN_STEPSIZE = 0.5
OUTSIDE_OFFSET = 20.0  # Afgeleide buitenlijn, links van de tekenrichting

# Per geval: dijkvakken, buitenlijn (None = afleiden), interval en lengte van de dwarslijnen [m].
CASES = {
    "demo": dict(line=DEMO_DIR / "demo_dijkvakken.shp", interval=10.0, cross_length=100.0),
    "ringen": dict(line=INPUT / "ringen" / "ringen.shp", interval=25.0, cross_length=100.0),
    "multipart": dict(line=INPUT / "multipart" / "multipart.shp", interval=10.0, cross_length=100.0),
    "korte_vakken": dict(line=INPUT / "korte_vakken" / "korte_vakken.shp", interval=10.0, cross_length=100.0),
    "haarspeldbocht": dict(line=INPUT / "haarspeldbocht" / "haarspeldbocht.shp", interval=10.0, cross_length=100.0),
    "bladhoek_water": dict(line=INPUT / "bladhoek_water" / "bladhoek_water.shp", interval=10.0, cross_length=100.0),
    "richting_wisselend": dict(line=INPUT / "richting_wisselend" / "richting_wisselend.shp", interval=25.0, cross_length=100.0),
    "duin": dict(line=INPUT / "duin" / "duin.shp", interval=25.0, cross_length=400.0),
}
FILES = ("buitenlijn.shp", "cross_points.shp", "cross_lines.shp", "cross_lines_ahn.shp")


def ahn_dir(name):
    """Map met de AHN5-tiffs voor een geval: de meegeleverde strook voor de demo, anders `AHN_DIR`."""
    return str(DEMO_DIR) if name == "demo" else AHN_DIR


def outside_line(name, out):
    """Buitenlijn van een geval: opgegeven, of afgeleid uit de dijkvakken (geschreven in `out`)."""
    case = CASES[name]
    if case.get("outside"):
        return str(case["outside"])
    path = str(Path(out) / "buitenlijn.shp")
    buitenlijn.offset_lines_to_file(str(case["line"]), path, OUTSIDE_OFFSET, "links")
    return path


def cross_sections(name, out):
    """Stap 1 en 2: intervalpunten en dwarslijnen in map `out`."""
    case = CASES[name]
    out = Path(out)
    cs = CrossSections(
        str(case["line"]), str(out / "cross_points.shp"), group_id=GROUP_ID, outside_line_file=outside_line(name, out)
    )
    cs.interval_points_to_file(interval=case["interval"])
    cs.crosssections_to_file(str(out / "cross_lines.shp"), length_crosssection=case["cross_length"])
    return cs


def local_tiles(folder, cross_lines):
    """AHN5-bladen langs de dwarslijnen uit `folder`, zonder downloads; None als er een ontbreekt."""
    if not os.path.isdir(folder):
        return None
    tiles = ahn.AHNTiles(folder, download_folder=folder)
    with fiona.open(cross_lines) as source:
        lines = [shapely.geometry.shape(row.geometry) for row in source]
    for name in tiles.sheet_names(lines):
        url = tiles.sheets[name][1].get(5)
        path = url and tiles._local_file(ahn.tif_name(url))
        if not path:
            return None
        tiles._files[name] = path  # Geen downloads of terugval op AHN4
    tiles.report_ahn6 = lambda lines: None  # Geen netwerk
    return tiles


def heights(name, out, tiles):
    """Stap 3: AHN-hoogtes langs de dwarslijnen in map `out`."""
    out = Path(out)
    ch = CrossSectionHeights(str(out / "cross_lines.shp"), group_id=GROUP_ID, ahn_tiles=tiles, ahn_stepsize=AHN_STEPSIZE)
    ch.crosssections_height_to_file(str(out / "cross_lines_ahn.shp"))


def make_reference(name):
    out = OUTPUT / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    cross_sections(name, out)
    tiles = local_tiles(ahn_dir(name), out / "cross_lines.shp")
    if tiles is None:
        raise SystemExit(f"{name}: AHN5-tiffs niet (allemaal) gevonden in '{ahn_dir(name)}' (zet AHN_TEST_DIR)")
    heights(name, out, tiles)
    print(f"{name}: referentie in '{out}'")


if __name__ == "__main__":
    for name in sys.argv[1:] or CASES:
        make_reference(name)
