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

Maak eenmalig de conda-omgeving aan (certifi, fiona, rasterio, shapely, numpy, tqdm):

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
standaardwaarde. Commentaar begint met `;`. De instellingen `ahn_file_prefix`, `ahn_file_suffix` en
`ahn_file_transform` bestaan niet meer; staan ze nog in een oud `config.ini`, dan worden ze met een
waarschuwing genegeerd.

## Gebruik

### Buitenlijn

De buitenlijn bepaalt alleen aan welke kant van de dijklijn de buitenzijde ligt: dwarslijnen worden
zo gedraaid dat ze aan de buitenkant beginnen. Heeft de buitenlijn het vak-attribuut (`group_id`),
dan wordt per vak de eigen buitenlijn gebruikt. Zonder buitenlijn blijft de richting van de
dwarslijnen onbepaald.

Heb je geen buitenlijn, dan kan de app die afleiden uit de dijkvakken (zoals "Offset Curves" in QGIS):
laat `input_line_outside` leeg en zet `outside_offset` op een afstand in meters, bijv. `5`. De
dijkvakken worden dan zoveel naar links of rechts (`outside_side`) van hun tekenrichting verschoven en
weggeschreven als `buitenlijn_offset.shp` in de outputmap. Let op:

- De afstand moet kleiner zijn dan de halve `cross_length`, anders snijden de dwarslijnen de lijn niet.
- Links/rechts is ten opzichte van de richting waarin elk dijkvak getekend is. Zijn de vakken niet
  allemaal in dezelfde richting getekend, dan ligt de afgeleide buitenlijn bij sommige vakken aan de
  verkeerde kant. Controleer dat op de kaart in de webapp (de lijn verschijnt na *Toepassen*).

### Benodigde AHN-data

De hoogtes worden uit **lokale** AHN GeoTIFF's gelezen. Ontbrekende kaartbladen downloadt de app
zelf; je hoeft alleen een map op te geven (`ahn_file_loc`, en eventueel `ahn_file_downloaded`).

1. **Kaartbladindex** (`ahn_index`, standaard de meegeleverde `bladwijzer.gpkg` in `src/ahnprofielcreator/data`): de bladwijzer
   van AHN met de kaartbladen van 5 × 6,25 km en per blad downloadlinks per AHN-versie en product
   ([bron](https://basisdata.nl/hwh-ahn/AUX/bladwijzer.gpkg),
   [kaart](https://basisdata.nl/hwh-ahn/AUX/bladwijzer/index.html)). De bladnaam staat in
   attribuut `AHN` (`ahn_index_id`). Een eigen index (.gpkg of .shp, RD New) mag ook, als die
   dezelfde velden heeft.
2. **Map met AHN tiffs** (`ahn_file_loc`): hier zoekt de app de tiffs.
3. **Map voor downloads** (`ahn_file_downloaded`, optioneel): hier komen gedownloade bladen en hier
   wordt ook gezocht. Leeg = `ahn_file_loc`. Handig als `ahn_file_loc` alleen-lezen is (bijv. op P:)
   of als je er geen downloads in wilt.

Werkwijze per kaartblad dat de dwarslijnen raakt:

1. Zoek in `ahn_file_loc` en `ahn_file_downloaded` de tiff met de naam uit de downloadlink, bijv. `2023_M_01CZ1.TIF` (AHN5)
   of `M_01CZ1.TIF` (AHN4, uit `M_01CZ1.zip`). Hoofd-/kleine letters maken niet uit en een extra
   achtervoegsel mag, zodat ook `M_01CZ1_DEFLATE.tif` gevonden wordt.
2. Niet gevonden: download het blad naar `ahn_file_downloaded` (AHN4-zips worden uitgepakt). Bij 0,5 m is dat ~200 MB per blad.
3. Heeft het blad geen (werkende) link voor de gekozen versie, dan valt de app terug op AHN4, dat
   heel Nederland dekt. AHN5 wordt sinds 2023 in drie jaar ingewonnen
   ([voortgang](https://www.ahn.nl/voortgang-ahn5-2023)). De terugval staat als waarschuwing in de log.

Het product kies je met:

| Instelling | Standaard | Mogelijk |
|---|---|---|
| `ahn_versie` | `5` | 1 t/m 5 (met terugval op 4 voor versies boven 4) |
| `model` | `DTM` | `DTM` (maaiveld) of `DSM` (oppervlakte) |
| `model_resolutie` | `0.5` | `0.5` of `5` [m] |

Bladen zonder tiff worden met een waarschuwing overgeslagen; in de webapp kun je de dwarslijnen op
AHN-dekking kleuren om dat te zien. De invoer-shapes (dijkvaklijnen en buitenlijn) moeten in RD New
staan. Zet tiffs van AHN3 en AHN4 niet in dezelfde map: die hebben dezelfde bestandsnamen.

**AHN6** gebruikt bladen van 1 × 1 km en zit niet in `bladwijzer.gpkg`. Met `bladwijzer_AHN6.gpkg`
(ook meegeleverd) controleert de app wel of AHN6 langs het traject al te downloaden is en meldt dat in
de log; voor de berekening gebruikt de app AHN6 (nog) niet.

**Binnen HKV** staan AHN5 en AHN4 (DTM 0,5 m) op `P:\OpenData\AHN\AHN5\DTM05` en
`P:\OpenData\AHN\AHN4\DTM05`. Die namen worden herkend. Gebruik je zo'n map als `ahn_file_loc`,
zet dan `ahn_file_downloaded` op een eigen map, zodat ontbrekende bladen (bijv. de AHN4-terugval)
niet op P: terechtkomen:

```ini
ahn_file_loc = P:\OpenData\AHN\AHN5\DTM05
ahn_file_downloaded = ahn_data
```

Licentie: AHN1 t/m AHN4 vallen onder CC0, AHN5 en later onder CC BY 4.0 (bronvermelding: AHN).
De downloads gebruiken de certificaten van `certifi`, omdat de Windows-certificaatopslag een
verlopen tussencertificaat kan bevatten; lukt dat niet, dan die van Windows.

### Uitvoeren

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
    outside_offset=0.0,  # of: input_line_outside=None en bijv. 5 m om de buitenlijn af te leiden
    outside_side="links",
    group_id="vak_id",
    ahn_file_loc=r"...\ahn_data",  # lokale tiffs
    ahn_file_downloaded=None,  # map voor downloads; None = ahn_file_loc
    ahn_versie=5,  # standaard; terugval op AHN4
    model="DTM",
    model_resolutie=0.5,
    interval_length=10,
    cross_length=100.0,
    ahn_stepsize=0.5,
))
```

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
`src/ahnprofielcreator/app/static/vendor/leaflet`. Van internet komen de achtergrondkaarten (BRT,
luchtfoto, AHN, via PDOK) en, tijdens de berekening, ontbrekende AHN-kaartbladen (via basisdata.nl).
Zonder internet werkt de app ook, maar zonder achtergrond en alleen met tiffs die al lokaal staan.
De app is alleen bereikbaar vanaf de eigen computer (127.0.0.1).
