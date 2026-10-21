# AHNProfielCreator

Genereert AHN profielen langs een traject, per dijkvak. Dit is de package-versie van
`profielen_per_vak.ipynb`:

1. interval punten langs de dijkvaklijnen (`cross_points.shp`);
2. dwarslijnen door die punten, georiënteerd met de buitenlijn (`cross_lines.shp`);
3. AHN hoogtes langs de dwarslijnen (`cross_lines_ahn.shp`, 3D LineString).

De output komt in `<cur_dir>/output_per_vak_<interval_length>m/`.

`hkvpy.profielen` is opgenomen als `ahnprofielcreator.profielen`; hkvpy hoeft dus niet
geïnstalleerd te zijn. `SchemProfile` heeft daarnaast het pakket `rdp` nodig (`pip install -e .[schem]`).

## Installatie

Maak eenmalig de conda-omgeving aan (fiona, rasterio, shapely, numpy, tqdm):

```bash
conda env create -f environment.yml
```

Voor de webapp is dat genoeg. Voor het commando `ahn-profielen` of gebruik vanuit eigen scripts
daarna nog, in die omgeving:

```bash
pip install -e .
```

## Instellingen

Kopieer `config.example.ini` naar `config.ini` en pas de paden aan. Alle instellingen staan in de
sectie `[profielen]`; paden schrijf je zonder aanhalingstekens en een lege waarde betekent de
standaardwaarde. Commentaar begint met `;`.

## Gebruik

Commandline:

```bash
ahn-profielen --config config.ini
```

Losse opties overschrijven de waarden uit het configuratiebestand, bijv. `--interval-length 25`.
Zie `ahn-profielen --help` voor alle opties. `python -m ahnprofielcreator` werkt ook.

Vanuit Python:

```python
from ahnprofielcreator import Settings, run

run(Settings(
    cur_dir=r"...\profiel_shapes",
    input_line=r"...\WAD_GEKB_basisanalyse_profielen_v2.shp",
    input_line_outside=r"...\WAD_GEKB_basisanalyse_profielen_v2_outsideline.shp",
    group_id="vak_id",
    ahn_index=r"P:\OpenData\AHN\Kaartbladen_AHN5_HKV.shp",
    ahn_file_loc=r"P:\OpenData\AHN\AHN5\DTM05",
    interval_length=10,
    cross_length=100.0,
    ahn_stepsize=0.5,
))
```

AHN tiff-files moeten heten `{ahn_file_prefix}{bladnaam in kleine letters}{ahn_file_suffix}.tif`
(standaard `m_<blad>_deflate.tif`).

## Webapp

Een lokale webapp om invoer en uitvoer te bekijken en de berekening te starten. Installeren van
het pakket is niet nodig: dubbelklik op `start_app.bat`. Dat activeert de conda-omgeving
`ahnprofielcreator`, gebruikt `config.ini` naast het bestand (als die er is) en opent de browser op
<http://127.0.0.1:8050/>. Stoppen: sluit het venster of druk Ctrl+C.

- Ander configuratiebestand: sleep het `.ini`-bestand op `start_app.bat`, of `start_app.bat pad\naar\config.ini`.
- Andere conda-omgeving: pas `AHN_ENV` bovenin `start_app.bat` aan (of zet de omgevingsvariabele `AHN_ENV`).

Zonder het `.bat`-bestand, in een geactiveerde omgeving:

```bash
python app.py --config config.ini
```

Andere poort: `--port`. Na `pip install -e .` werkt ook `ahn-profielen-app --config config.ini`.

- **Kaart:** dijkvakken, buitenlijn, intervalpunten en dwarslijnen. Punten waarvoor geen dwarslijn
  gemaakt kon worden (geen snijpunt met de buitenlijn) staan in rood. Dwarslijnen kunnen per vak of
  op AHN-dekking gekleurd worden, zodat ontbrekende AHN-tegels opvallen.
- **Profiel:** klik op een dwarslijn voor het hoogteprofiel, met de andere profielen uit het vak in
  grijs. Bladeren met ◀ ▶ of de pijltjestoetsen. De x-as is de afstand tot de dijklijn; met een
  buitenlijn staat de buitenzijde links.
- **Instellingen:** aanpassen en *Toepassen* toont de bestanden bij die instellingen;
  *Berekening starten* voert alle stappen uit en laadt daarna het resultaat.

De app gebruikt alleen de standaardbibliotheek en fiona. Leaflet staat in
`src/ahnprofielcreator/app/static/vendor/leaflet`. Alleen de achtergrondkaarten (BRT, luchtfoto,
AHN) komen van internet, via PDOK; zonder internet werkt de app ook, maar zonder achtergrond.
De app is alleen bereikbaar vanaf de eigen computer (127.0.0.1).
