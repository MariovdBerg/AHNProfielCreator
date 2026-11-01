# AHNProfielCreator

Genereert AHN-hoogteprofielen dwars op een dijktraject, per dijkvak. Optioneel maakt de app per dijkvak één
geschematiseerd profiel (`.prfl`) voor Hydra-NL/Ringtoets.

```
dijkvakken (.shp) ─┬─> 1. buitenlijn          buitenlijn_offset.shp   (alleen als die afgeleid wordt)
                   ├─> 2. intervalpunten      cross_points.shp        (elke interval_length m)
                   ├─> 3. dwarslijnen         cross_lines.shp         (beginnen aan de buitenzijde)
AHN-tiffs ─────────┴─> 4. AHN-profielen       cross_lines_ahn.shp     (3D-lijnen met hoogtes)
                       5. schematiseren       prfl/<vak>.prfl         (optioneel)
```

Er is een webapp om de invoer en uitvoer op een kaart te bekijken en de berekening te starten. Rekenen kan ook
vanaf de commandline of vanuit Python.

## Inhoud

- [Snel starten (demo)](#snel-starten-demo)
- [Installatie](#installatie)
- [Invoer](#invoer): [dijkvakken](#dijkvakken), [buitenlijn](#buitenlijn), [AHN-data](#ahn-data), [instellingen](#instellingen)
- [Gebruik](#gebruik): [webapp](#webapp), [commandline](#commandline), [Python](#python), [schematiseren](#schematiseren-prfl)
- [Uitvoer](#uitvoer)
- Verder: [methode van de schematisatie](docs/schematisatie.md), [ontwikkelen en testen](CONTRIBUTING.md)

## Snel starten (demo)

Er komt een demotraject mee: 3 km Lekdijk bij Schalkwijk (normtraject 44-1) in zes dijkvakken, met een
uitgeknipte AHN5-strook. De buitenlijn leidt de app zelf af uit de dijkvakken. Daarmee draai je een volledige
berekening zonder eigen data en zonder AHN-downloads. Nodig: [Miniforge](https://conda-forge.org/download/) of
een andere conda en (voor het schematiseren) internet bij het aanmaken van de omgeving.

1. Haal de code op en ga naar de projectmap:

   ```bash
   git clone https://github.com/MariovdBerg/AHNProfielCreator.git
   ```

   ```bash
   cd AHNProfielCreator
   ```

2. Maak de conda-omgeving aan (eenmalig, een paar minuten) en activeer die:

   ```bash
   conda env create -f environment.yml
   ```

   ```bash
   conda activate ahnprofielcreator
   ```

3. Start de demo, op een van twee manieren:

   - **Webapp:** dubbelklik op `start_app.bat` (zonder `config.ini` in de projectmap). De browser opent op
     <http://127.0.0.1:8050/> met het demotraject op de kaart. Klik *Berekening starten* en daarna
     *Schematiseren (.prfl)*, en klik op een dwarslijn voor het profiel. De output komt in
     `output\demo\output_per_vak_10m\`.
   - **Commandline:** installeer het pakket en reken met het voorbeeldbestand, dat al naar de demo wijst:

     ```bash
     pip install -e .
     ```

     ```bash
     ahn-profielen --config config.example.ini --schematiseren
     ```

     De output komt in `output\output_per_vak_10m\`.

De berekening duurt enkele seconden. Op de commandline eindigt de log met:

```text
Klaar, output in '...\output\output_per_vak_10m'.
Schematiseren: één Hydra-NL profiel (.prfl) per vak, kwantiel 0.2...
Vak DV_01: 0 berm(en), voorland 3 punten, kruin 8.97 m NAP, RMSE 0.08 m.
...
Klaar: 6 van 6 vakken geschematiseerd (6 zonder fouten), output in '...\output\output_per_vak_10m\prfl'.
```

In de outputmap staan dan de afgeleide buitenlijn, 300 intervalpunten, 300 dwarslijnen met AHN-hoogtes en per
vak een `.prfl`-bestand; zie [Uitvoer](#uitvoer) voor wat er in elk bestand staat.

Vanuit Python gaat het zo (na `pip install -e .`):

```python
from ahnprofielcreator import Settings, run, schematise
from ahnprofielcreator.demo import demo_config

settings = Settings(**demo_config())  # output in output/demo in de huidige map
run(settings)
schematise(settings)
```

Voor een eigen traject: kopieer `config.example.ini` naar `config.ini` en vervang de demopaden door je eigen
dijkvakken en AHN-map; zie [Invoer](#invoer).

## Installatie

Maak eenmalig de conda-omgeving aan (certifi, fiona, rasterio, shapely, numpy, tqdm, pytest en `prflcheck`
voor het schematiseren):

```bash
conda env create -f environment.yml
```

Bestaat de omgeving al, installeer `prflcheck` ([ProfielCheckPython](https://github.com/MariovdBerg/ProfielCheckPython),
niet op PyPI) dan los, in de geactiveerde omgeving. Zonder `prflcheck` werkt alles behalve het schematiseren.

```bash
pip install https://github.com/MariovdBerg/ProfielCheckPython/archive/refs/heads/main.zip
```

Voor de webapp via `start_app.bat` is dit genoeg. Voor het commando `ahn-profielen` of gebruik vanuit eigen
scripts installeer je daarna nog het pakket, in die omgeving en vanuit de projectmap:

```bash
pip install -e .
```

**Activeer de omgeving altijd** (`conda activate ahnprofielcreator`) voordat je Python, `ahn-profielen` of
`pytest` start; `start_app.bat` doet dat zelf. Roep je `python.exe` uit de omgeving direct aan zonder te
activeren, dan vindt Windows de DLL's van numpy, GDAL en PROJ niet (die staan in `Library\bin`). Python
stopt dan zonder melding, of met een fout zoals `Windows fatal exception: code 0xc06d007f`, of
`Cannot find ... (GDAL_DATA is not defined)`. Dat geldt ook voor een IDE: kies daar de interpreter van
de conda-omgeving (bijv. in VS Code via *Python: Select Interpreter*), zodat die de omgeving activeert.

## Invoer

Wat je aanlevert, in het kort:

| Invoer | Verplicht | Instelling |
|---|---|---|
| Dijkvakken: lijnen, één per vak | ja | `input_line`, `group_id` |
| Buitenlijn: geeft aan welke kant buiten ligt | nee, maar nodig voor de richting van de dwarslijnen en voor het schematiseren; kan ook afgeleid worden | `input_line_outside` of `outside_offset` + `outside_side` |
| AHN-tiffs: lokale map; ontbrekende bladen worden gedownload | ja (een map) | `ahn_file_loc`, `ahn_file_downloaded` |
| Werkmap voor de uitvoer | ja | `cur_dir` |

### Dijkvakken

Eén shapefile (`input_line`) met de dijklijn, opgedeeld in dijkvakken:

- **Geometrie:** `LineString` of `MultiLineString`. Gesloten ringen en vakken uit meerdere delen mogen.
  Andere geometrieën (punten, vlakken) worden overgeslagen.
- **Vak-attribuut:** het attribuut `group_id` (standaard `vak_id`) moet bestaan en uniek zijn: één feature per
  vak. De naam komt terug in de uitvoer en als bestandsnaam van het `.prfl`.
- **Coördinaten:** RD New (EPSG:28992, of RD + NAP, EPSG:7415), met een `.prj`-bestand. Een ander stelsel of een
  ontbrekende `.prj` geeft een foutmelding en er wordt niets berekend. Herprojecteer het bestand dan eerst, bijv.
  in QGIS (*Vector › Data Management Tools › Reproject Layer*), of ken RD New toe als de coördinaten al in RD
  staan maar de `.prj` ontbreekt.
- **Ligging:** de lijn ligt het best op of bij de kruin. De afstand in de profielen is de afstand tot deze lijn.
- **Lengte:** elk vak moet langer zijn dan de intervalafstand (`interval_length`), anders krijgt het geen punten.
- **Tekenrichting:** maakt alleen uit als de buitenlijn wordt afgeleid (zie hieronder).
- Overige attributen worden overgenomen in de uitvoer.

### Buitenlijn

De buitenlijn bepaalt alleen aan welke kant van de dijklijn de buitenzijde ligt: dwarslijnen worden zo gedraaid
dat ze aan de buitenkant beginnen. Zonder buitenlijn blijft de richting van de dwarslijnen onbepaald en kan er
niet geschematiseerd worden. Er zijn twee manieren:

1. **Opgeven** (`input_line_outside`): een lijn-shapefile in RD New aan de buitenzijde, bijv. de waterlijn of de
   buitenteen. Elke dwarslijn moet de buitenlijn snijden, dus binnen de halve `cross_length` van de dijklijn,
   en de buitenlijn mag de dijklijn niet kruisen. Heeft de buitenlijn het vak-attribuut (`group_id`), dan wordt
   per vak de eigen buitenlijn gebruikt; dan moet elk vak er een hebben.
2. **Afleiden** (zoals "Offset Curves" in QGIS): laat `input_line_outside` leeg en zet `outside_offset` op een
   afstand in meters, bijv. `20`. De dijkvakken worden dan zoveel naar links of rechts (`outside_side`) van hun
   tekenrichting verschoven en weggeschreven als `buitenlijn_offset.shp` in de outputmap. Het demotraject werkt
   zo. Let op:
   - De afstand moet kleiner zijn dan de halve `cross_length`, anders snijden de dwarslijnen de lijn niet.
   - Links/rechts is ten opzichte van de richting waarin elk dijkvak getekend is. Zijn de vakken niet
     allemaal in dezelfde richting getekend, dan ligt de afgeleide buitenlijn bij sommige vakken aan de
     verkeerde kant. Controleer dat op de kaart in de webapp (knop *Genereer buitenlijn*).

Een intervalpunt waarvan de dwarslijn de buitenlijn niet snijdt, krijgt geen dwarslijn; de webapp toont zulke
punten in rood.

### AHN-data

De hoogtes worden uit **lokale** AHN GeoTIFF's gelezen. Ontbrekende kaartbladen downloadt de app zelf.

1. **Map met AHN-tiffs** (`ahn_file_loc`): hier zoekt de app de tiffs. Binnen HKV bijv.
   `P:\OpenData\AHN\AHN5\DTM05`.
2. **Map voor downloads** (`ahn_file_downloaded`, optioneel): hier komen gedownloade bladen en hier wordt ook
   gezocht. Leeg = `ahn_file_loc`. Handig als `ahn_file_loc` alleen-lezen is of als je er geen downloads in wilt.
3. **Kaartbladindex** (`ahn_index`, standaard de meegeleverde `bladwijzer.gpkg` in `src/ahnprofielcreator/data`):
   de bladwijzer van AHN met de kaartbladen van 5 × 6,25 km en per blad downloadlinks per AHN-versie en product
   ([bron](https://basisdata.nl/hwh-ahn/AUX/bladwijzer.gpkg), [kaart](https://basisdata.nl/hwh-ahn/AUX/bladwijzer/index.html)).
   De bladnaam staat in attribuut `AHN` (`ahn_index_id`). Een eigen index (.gpkg of .shp, RD New) mag ook, als
   die dezelfde velden heeft.

Welke AHN-data gebruikt wordt, kies je zelf:

| Instelling | Standaard | Mogelijk |
|---|---|---|
| `ahn_versie` | `5` | 1 t/m 5 (met terugval op 4 voor versies boven 4) |
| `model` | `DTM` | `DTM` (maaiveld) of `DSM` (oppervlakte) |
| `model_resolutie` | `0.5` | `0.5` of `5` [m] |

De werkwijze per kaartblad dat de dwarslijnen raakt:

1. Zoek in `ahn_file_loc` en `ahn_file_downloaded` de tiff met de naam uit de downloadlink,
   bijv. `2023_M_01CZ1.TIF` (AHN5) of `M_01CZ1.TIF` (AHN4, uit `M_01CZ1.zip`).
   Hoofd-/kleine letters maken niet uit en een extra achtervoegsel mag, zodat ook `M_01CZ1_DEFLATE.tif` gevonden wordt.
2. Niet gevonden: download het blad naar `ahn_file_downloaded` (AHN4-zips worden uitgepakt). Bij 0,5 m is dat
   ~200 MB per blad.
3. Heeft het blad geen (werkende) link voor de gekozen versie, dan valt de app terug op AHN4, dat
   heel Nederland dekt. AHN5 wordt sinds 2023 in drie jaar ingewonnen ([voortgang](https://www.ahn.nl/voortgang-ahn5-2023)).
   De terugval staat als waarschuwing in de log.

Bladen zonder tiff worden met een waarschuwing overgeslagen; in de webapp kun je de dwarslijnen op
AHN-dekking kleuren om dat te zien. Heeft minstens de helft van de punten op een blad geen hoogte, dan
staat dat als waarschuwing in de log. Dat kan water zijn, maar ook een AHN5-blad dat nog maar deels
ingewonnen is (bijv. 40BZ2: het blad bestaat, maar bevat 0,1% data ten tijde van ontwikkeling).
De app valt dan niet terug op AHN4; kies zo nodig zelf `ahn_versie = 4`.

**Let op:**
- Zet tiffs van AHN3 en AHN4 niet in dezelfde map: die hebben dezelfde bestandsnamen.
- AHN6 gebruikt bladen van 1 × 1 km en zit niet in `bladwijzer.gpkg`. Met `bladwijzer_AHN6.gpkg` (ook
  meegeleverd) controleert de app wel of AHN6 langs het traject al te downloaden is en meldt dat in de log;
  voor de berekening gebruikt de app AHN6 (nog) niet.

Licentie: AHN1 t/m AHN4 vallen onder CC0, AHN5 en later onder CC BY 4.0 (bronvermelding: AHN).
De downloads gebruiken de certificaten van `certifi`, omdat de Windows-certificaatopslag een
verlopen tussencertificaat kan bevatten; lukt dat niet, dan die van Windows.

### Instellingen

De instellingen staan in een `.ini`-bestand. Kopieer `config.example.ini` naar `config.ini` en pas de paden
aan. Alle instellingen staan in de sectie `[profielen]`; paden schrijf je zonder aanhalingstekens en een lege
waarde betekent de standaardwaarde. Commentaar begint met `;`. In de webapp kun je de instellingen ook
aanpassen en opslaan.

Relatieve paden gelden vanaf de map waarin je het commando start, niet vanaf de map van het `.ini`-bestand.
De paden in `config.example.ini` werken daardoor alleen vanuit de projectmap; gebruik elders absolute paden.
`start_app.bat` gaat zelf naar de projectmap.

| Instelling | Standaard | Betekenis |
|---|---|---|
| `cur_dir` | (verplicht) | werkmap; de uitvoer komt in `<cur_dir>\output_per_vak_<interval_length>m` |
| `input_line` | (verplicht) | shapefile met de dijkvakken |
| `group_id` | `vak_id` | vak-attribuut in de dijkvakken (en eventueel de buitenlijn) |
| `input_line_outside` | leeg | shapefile met de buitenlijn |
| `outside_offset` | leeg (= niet afleiden) | [m] zonder buitenlijn: leid die af door de dijkvakken zoveel te verschuiven |
| `outside_side` | `links` | kant van de afgeleide buitenlijn t.o.v. de tekenrichting: `links` of `rechts` |
| `ahn_file_loc` | (verplicht) | map met lokale AHN-tiffs |
| `ahn_file_downloaded` | leeg (= `ahn_file_loc`) | map voor gedownloade bladen |
| `ahn_versie`, `model`, `model_resolutie` | `5`, `DTM`, `0.5` | welke AHN-data; zie [AHN-data](#ahn-data) |
| `ahn_index`, `ahn_index_id` | `bladwijzer.gpkg`, `AHN` | kaartbladindex met downloadlinks |
| `interval_length` | `10` | [m] afstand tussen de dwarslijnen langs elk vak |
| `cross_length` | `200` | [m] lengte van een dwarslijn, de helft aan elke kant van de dijklijn |
| `ahn_stepsize` | `0.5` | [m] afstand tussen de hoogtepunten langs een dwarslijn |
| `schem_*` | | instellingen van het schematiseren; zie [docs/schematisatie.md](docs/schematisatie.md#instellingen) |

## Gebruik

### Webapp

Een lokale webapp om invoer en uitvoer te bekijken en de berekening te starten. Installeren van
het pakket is niet nodig: dubbelklik op `start_app.bat`. Dat activeert de conda-omgeving
`ahnprofielcreator`, gebruikt `config.ini` naast het bestand (als die er is) en opent de browser op
<http://127.0.0.1:8050/>. Stoppen: sluit het venster of druk Ctrl+C.

- **Demo:** zonder `config.ini` (of met `--demo`) start de app met het [demotraject](#snel-starten-demo).
  De output komt dan in `output\demo`.
- Ander configuratiebestand: sleep het `.ini`-bestand op `start_app.bat`, of `start_app.bat pad\naar\config.ini`.
- Andere conda-omgeving: pas `AHN_ENV` bovenin `start_app.bat` aan (of zet de omgevingsvariabele `AHN_ENV`).

Zonder het `.bat`-bestand, in een geactiveerde omgeving:

```bash
python app.py --config config.ini
```

Andere poort: `--port`. Na `pip install -e .` werkt ook `ahn-profielen-app --config config.ini`.

Wat de app laat zien:

- **Kaart:** dijkvakken, buitenlijn, intervalpunten en dwarslijnen. Punten waarvoor geen dwarslijn
  gemaakt kon worden (geen snijpunt met de buitenlijn) staan in rood. Dwarslijnen kunnen per vak of
  op AHN-dekking gekleurd worden, zodat ontbrekende AHN-tegels opvallen.
- **Profiel:** klik op een dwarslijn voor het hoogteprofiel, met de andere profielen uit het vak in
  grijs. Bladeren met ◀ ▶ of de pijltjestoetsen. De x-as is de afstand tot de dijklijn; met een
  buitenlijn staat de buitenzijde links.
- **Instellingen:** aanpassen en *Valideren* controleert ze en toont de bestanden bij die instellingen
  (de instellingen gelden dan voor de app, maar staan nog niet in een bestand). *Opslaan naar .ini*
  valideert en slaat ze op in een (nieuw) `.ini`-bestand, te gebruiken met `--config` of `start_app.bat`.
  Commentaar in een bestaand bestand dat je overschrijft gaat verloren.
- **Stappen:** *Berekening starten* voert alle stappen uit (behalve het schematiseren) en laadt daarna het
  resultaat. Elke stap kan ook los: *Genereer buitenlijn* (alleen zonder opgegeven buitenlijn en met een
  afstand), *Genereer intervalpunten*, *Genereer dwarslijnen* en *Genereer AHN-profielen*. Een stap
  gebruikt de uitvoer van de vorige; de knop is pas actief als die er is. Pas je bijv. de lengte van de
  dwarslijnen aan, genereer dan ook de stappen daarna opnieuw.
- **Schematiseren (.prfl):** na de berekening; daarna toont de profielgrafiek per vak ook het representatieve
  profiel (gestippeld), het Hydra-NL profiel (oranje) en het voorland (groen), met het aantal bermen en de
  uitkomst van de controle. Is de berekening daarna opnieuw gedaan, dan staat de schematisatie als *verouderd*
  bij de bestanden.

De app gebruikt alleen de standaardbibliotheek en fiona (en `prflcheck` voor het schematiseren). Leaflet staat in
`src/ahnprofielcreator/app/static/vendor/leaflet`. Van internet komen de achtergrondkaarten (BRT,
luchtfoto, AHN, via PDOK) en, tijdens de berekening, ontbrekende AHN-kaartbladen (via basisdata.nl).
Zonder internet werkt de app ook, maar zonder achtergrond en alleen met tiffs die al lokaal staan.
De app is alleen bereikbaar vanaf de eigen computer (127.0.0.1).

### Commandline

Na `pip install -e .`, in een geactiveerde omgeving:

```bash
ahn-profielen --config config.ini
```

Losse opties overschrijven de waarden uit het configuratiebestand, bijv. `--interval-length 25`. Met
`--schematiseren` volgt daarna het schematiseren. Zie `ahn-profielen --help` voor alle opties.
`python -m ahnprofielcreator` werkt ook.

### Python

```python
from ahnprofielcreator import Settings, run, schematise

settings = Settings(
    cur_dir=r"...\profiel_shapes",
    input_line=r"...\dijkvakken.shp",
    input_line_outside=r"...\buitenlijn.shp",  # of: None en outside_offset=20 om de buitenlijn af te leiden
    outside_side="links",
    group_id="vak_id",
    ahn_file_loc=r"...\ahn_data",  # lokale tiffs
    ahn_file_downloaded=None,  # map voor downloads; None = ahn_file_loc
    ahn_versie=5,  # standaard; terugval op AHN4
    model="DTM",
    model_resolutie=0.5,
    interval_length=10,
    cross_length=200.0,
    ahn_stepsize=0.5,
)
run(settings)
schematise(settings)  # optioneel
```

`run` geeft per vak de dwarslijnen met hoogtes terug; de bestanden staan daarna in `settings.output_dir`.

### Schematiseren (.prfl)

Een optionele stap ná de berekening: per dijkvak één geschematiseerd profiel (`.prfl`, versie 4). In de webapp
met de knop *Schematiseren (.prfl)*, op de commandline met `--schematiseren` en vanuit Python met
`schematise(settings)` na `run(settings)`. Nodig: een buitenlijn (opgegeven of afgeleid) en het pakket `prflcheck`.

Per vak neemt de app een laag kwantiel (standaard 0,2) van alle AHN-profielen als representatief profiel, zoekt
de kruin bij de dijklijn en fit aan de buitenzijde een profiel met voorland, taluds en 0 tot 2 bermen dat voldoet
aan de regels van Hydra-NL/Ringtoets. De werkwijze en alle instellingen staan in
[docs/schematisatie.md](docs/schematisatie.md). Controleer het resultaat altijd: de schematisatie is een
automatisch startpunt, geen vervanging van een beoordeling van het profiel.

## Uitvoer

Alles komt in de outputmap `<cur_dir>\output_per_vak_<interval_length>m\` (bijv. `output\output_per_vak_10m`).
Alle shapefiles staan in RD New. Elke stap overschrijft de bestanden van een vorige berekening met dezelfde
instellingen.

| Bestand | Inhoud |
|---|---|
| `buitenlijn_offset.shp` | afgeleide buitenlijn, één lijn per vak met de attributen van de dijkvakken (alleen bij `outside_offset`) |
| `cross_points.shp` | intervalpunten op de dijkvakken, elke `interval_length` m vanaf het begin van elk vak |
| `cross_lines.shp` | dwarslijnen door de intervalpunten, lengte `cross_length` |
| `cross_lines_ahn.shp` | dezelfde dwarslijnen als 3D-lijn met AHN-hoogtes: het eigenlijke resultaat |
| `prfl\<vak>.prfl` | geschematiseerd profiel per vak (na het schematiseren) |
| `prfl\schematisatie.csv` | overzicht van de schematisatie per vak |
| `prfl\schematisatie.json` | hetzelfde overzicht met de profielen, voor de webapp |

### Attributen van de shapefiles

Alle attributen van de dijkvakken (zoals `vak_id`) worden overgenomen. Daarbij komen:

| Attribuut | In | Betekenis |
|---|---|---|
| `POINT_ID` | punten, dwarslijnen | volgnummer van het intervalpunt over het hele traject |
| `CROSS_ID` | dwarslijnen | volgnummer van de dwarslijn over het hele traject |
| `mid_x`, `mid_y` | dwarslijnen | RD-coördinaten van het intervalpunt (het snijpunt met de dijklijn) |
| `angle` | dwarslijnen | richting van de dijklijn naar de buitenzijde, in graden t.o.v. noord (met de klok mee); leeg zonder buitenlijn |
| `LEFTRIGHT`, `TOPBOTTOM` | dwarslijnen | of de lijn van west naar oost resp. van noord naar zuid loopt (voor het tekenen) |
| `CROSS_Z_ID` | AHN-profielen | volgnummer van het profiel |

### De AHN-profielen lezen

Elke lijn in `cross_lines_ahn.shp` is een 3D-lijn met een punt om de `ahn_stepsize` m (standaard 0,5 m):

- **x, y:** RD-coördinaten; **z:** hoogte in m NAP uit het gekozen AHN-model.
- **Richting:** met een buitenlijn begint de lijn aan de buitenzijde. De afstand tot de dijklijn is dan de
  afstand langs de lijn min de halve `cross_length`: negatief = buiten, positief = binnen.
- **Geen hoogte = `-99999`.** Punten zonder AHN-hoogte (water, een blad zonder tiff, een deels ingewonnen blad)
  krijgen z = -99999. Filter die weg voordat je gaat plotten of rekenen, anders zie je diepe pieken.

Voorbeeld in Python:

```python
import fiona
import numpy as np

with fiona.open(r"output\output_per_vak_10m\cross_lines_ahn.shp") as src:
    for row in src:
        xyz = np.array(row.geometry.coordinates)
        afstand = np.hypot(*(xyz[:, :2] - xyz[0, :2]).T) - 200.0 / 2  # cross_length / 2
        z = np.where(xyz[:, 2] == -99999, np.nan, xyz[:, 2])
        print(row.properties["vak_id"], np.nanmax(z))
```

### Overzicht van de schematisatie

`prfl\schematisatie.csv` heeft één regel per vak, met `;` als scheidingsteken en een decimale komma (voor
Nederlandse Excel; in pandas: `sep=";", decimal=","`):

| Kolom | Betekenis |
|---|---|
| `vak`, `bestand` | vak en naam van het `.prfl`-bestand |
| `n_profielen` | aantal AHN-profielen in het vak |
| `kruinhoogte` | [m NAP] kruinhoogte in het `.prfl` |
| `richting` | [graden t.o.v. noord] gemiddelde dijknormaal naar buiten |
| `bermen` | aantal bermen in het profiel (0 – 2) |
| `n_dijkpunten`, `n_voorlandpunten` | aantal punten van het dijkprofiel en van het voorland |
| `voorland_verlaagd` | [m] hoeveel het eerste voorlandpunt verlaagd is (laagte of sloot bij de teen) |
| `rmse` | [m] afwijking van het gekozen profiel t.o.v. het representatieve profiel |
| `check` | uitkomst van de controle door `prflcheck`: `0` = goedgekeurd |
| `omschrijving` | toelichting bij de controle, of de foutmelding als het vak niet geschematiseerd kon worden |
