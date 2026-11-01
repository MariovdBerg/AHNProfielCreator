# AHNProfielCreator

Genereert AHN-profielen langs een traject, per dijkvak. Optioneel kan per dijkvak een .prfl-bestand gegenereerd worden
op basis van de AHN-profielen. De app werkt als volgt:

0. lever één shapefile bestand aan van een traject, opgedeeld in dijkvakken (`*.shp`);
1. genereer een buitenlijn van het traject om de orientatie te bepalen (`*_buitenlijn.shp`);
2. genereer interval punten (elke `x` meter) langs de dijkvaklijnen (`cross_points.shp`);
3. genereer dwarslijnen door die punten, georiënteerd met de buitenlijn (`cross_lines.shp`);
4. genereer AHN-profielen langs de dwarslijnen (`cross_lines_ahn.shp`, 3D LineString);
5. optioneel: schematiseer per dijkvak één `.prfl` profiel (`prfl/<vak>.prfl`), zie [Schematiseren](#schematiseren-prfl).

De output komt in `<cur_dir>/output_per_vak_<interval_length>m/`.

## Installatie

Maak eenmalig de conda-omgeving aan (certifi, fiona, rasterio, shapely, numpy, tqdm, en `prflcheck` voor het
schematiseren):

```bash
conda env create -f environment.yml
```

Bestaat de omgeving al, installeer `prflcheck` ([ProfielCheckPython](https://github.com/MariovdBerg/ProfielCheckPython),
niet op PyPI) dan los, in de geactiveerde omgeving. Zonder `prflcheck` werkt alles behalve het schematiseren.

```bash
pip install https://github.com/MariovdBerg/ProfielCheckPython/archive/refs/heads/main.zip
```

Voor de webapp is dit genoeg. Voor het commando `ahn-profielen` of gebruik vanuit eigen scripts
daarna nog, in die omgeving:

```bash
pip install -e .
```

**Activeer de omgeving altijd** (`conda activate ahnprofielcreator`) voordat je Python, `ahn-profielen` of
`pytest` start; `start_app.bat` doet dat zelf. Roep je `python.exe` uit de omgeving direct aan zonder te
activeren, dan vindt Windows de DLL's van numpy, GDAL en PROJ niet (die staan in `Library\bin`). Python
stopt dan zonder melding, of met een fout zoals `Windows fatal exception: code 0xc06d007f`, of
`Cannot find ... (GDAL_DATA is not defined)`. Dat geldt ook voor een IDE: kies daar de interpreter van
de conda-omgeving (bijv. in VS Code via *Python: Select Interpreter*), zodat die de omgeving activeert.

## Instellingen

Kopieer `config.example.ini` naar `config.ini` en pas de paden aan. Alle instellingen staan in de
sectie `[profielen]`; paden schrijf je zonder aanhalingstekens en een lege waarde betekent de
standaardwaarde. Commentaar begint met `;`.

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
  verkeerde kant. Controleer dat op de kaart in de webapp (knop *Genereer buitenlijn*).

### Benodigde AHN-data

Om de AHN-profielen te genereren, wordt gebruik gemaakt van AHN. 
De hoogtes worden uit **lokale** AHN GeoTIFF's gelezen (`ahn_file_loc`).
Ontbrekende kaartbladen downloadt de app zelf (`ahn_file_downloaded`).

1. **Kaartbladindex** (`ahn_index`, standaard de meegeleverde `bladwijzer.gpkg` in `src/ahnprofielcreator/data`): 
   de bladwijzer van AHN met de kaartbladen van 5 × 6,25 km en per blad downloadlinks per AHN-versie en product
   ([bron](https://basisdata.nl/hwh-ahn/AUX/bladwijzer.gpkg), [kaart](https://basisdata.nl/hwh-ahn/AUX/bladwijzer/index.html)). De bladnaam staat in attribuut `AHN` (`ahn_index_id`).
   Een eigen index (.gpkg of .shp, RD New) mag ook, als die dezelfde velden heeft.
2. **Map met AHN tiffs** (`ahn_file_loc`): hier zoekt de app de tiffs.
3. **Map voor downloads** (`ahn_file_downloaded`, optioneel): hier komen gedownloade bladen en hier
   wordt ook gezocht. Leeg = `ahn_file_loc`. Handig als `ahn_file_loc` alleen-lezen is of als je er 
   geen downloads in wilt.

**Let op: AHN6** gebruikt bladen van 1 × 1 km en zit niet in `bladwijzer.gpkg`. Met `bladwijzer_AHN6.gpkg`
(ook meegeleverd) controleert de app wel of AHN6 langs het traject al te downloaden is en meldt dat in
de log; voor de berekening gebruikt de app AHN6 (nog) niet.

De app checkt welke kaartbladen de gegenereerde dwarslijnen doorsnijdt. De gebruiker kiest zelf welke data gebruikt
wordt om de AHN-profielen te genereren:

| Instelling | Standaard | Mogelijk |
|---|---|---|
| `ahn_versie` | `5` | 1 t/m 5 (met terugval op 4 voor versies boven 4) |
| `model` | `DTM` | `DTM` (maaiveld) of `DSM` (oppervlakte) |
| `model_resolutie` | `0.5` | `0.5` of `5` [m] |

De werkwijze per kaartblad dat de dwarslijnen raakt:

1. Zoek in `ahn_file_loc` en `ahn_file_downloaded` de tiff met de naam uit de downloadlink, 
   bijv. `2023_M_01CZ1.TIF` (AHN5) of `M_01CZ1.TIF` (AHN4, uit `M_01CZ1.zip`). 
   Hoofd-/kleine letters maken niet uit en een extra achtervoegsel mag, zodat ook `M_01CZ1_DEFLATE.tif` gevonden wordt.
2. Niet gevonden: download het blad naar `ahn_file_downloaded` (AHN4-zips worden uitgepakt). Bij 0,5 m is dat ~200 MB per blad.
3. Heeft het blad geen (werkende) link voor de gekozen versie, dan valt de app terug op AHN4, dat
   heel Nederland dekt. AHN5 wordt sinds 2023 in drie jaar ingewonnen ([voortgang](https://www.ahn.nl/voortgang-ahn5-2023)).
   De terugval staat als waarschuwing in de log.

Bladen zonder tiff worden met een waarschuwing overgeslagen; in de webapp kun je de dwarslijnen op
AHN-dekking kleuren om dat te zien. Heeft minstens de helft van de punten op een blad geen hoogte, dan
staat dat als waarschuwing in de log. Dat kan water zijn, maar ook een AHN5-blad dat nog maar deels
ingewonnen is (bijv. 40BZ2: het blad bestaat, maar bevat 0,1% data ten tijde van ontwikkeling).
De app valt dan niet terug op AHN4; kies zo nodig zelf `ahn_versie = 4`.
**Let op: zet tiffs van AHN3 en AHN4 niet in dezelfde map: die hebben dezelfde bestandsnamen.**

De invoer-shapes (dijkvaklijnen en buitenlijn) moeten in RD New (EPSG:28992, of RD + NAP, EPSG:7415)
staan, met een `.prj`-bestand. Dat wordt bij de start gecontroleerd: een ander coördinatenstelsel of een
ontbrekende `.prj` geeft een foutmelding en er wordt niets berekend. Herprojecteer het bestand dan eerst,
bijv. in QGIS (*Vector › Data Management Tools › Reproject Layer*), of ken RD New toe als de coördinaten
al in RD staan maar de `.prj` ontbreekt.

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
    input_line=r"...\dijkvakken.shp",
    input_line_outside=r"...\buitenlijn.shp",
    outside_offset=0.0,  # of: input_line_outside=None en bijv. 5 m om de buitenlijn af te leiden
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
))
```

### Schematiseren (.prfl)

Een optionele stap ná de berekening: per dijkvak één geschematiseerd profiel (`.prfl`, versie 4).
In de webapp met de knop *Schematiseren (.prfl)*, op de commandline met `--schematiseren`
(`ahn-profielen --config config.ini --schematiseren`) en vanuit Python met `schematise(settings)` na `run(settings)`.
Nodig: een buitenlijn (opgegeven of afgeleid), zodat bekend is welke kant buiten ligt, en het pakket `prflcheck`.

Per vak:

1. **Representatief profiel:** alle dwarslijnen van het vak samen, per afstand tot de dijklijn (stappen van 0,5 m)
   het kwantiel `schem_kwantiel` (standaard 0,2) van de AHN-hoogtes. Een laag kwantiel geeft een wat lager,
   voorzichtiger profiel. Waar minder dan de helft van de dwarslijnen een hoogte heeft (water), telt het niet mee.
2. **Kruin:** het hoogste punt van dat profiel binnen `schem_kruin_zoekafstand` (standaard 10 m) van de dijklijn.
   Ligt de dijklijn niet op of bij de kruin, vergroot dan deze afstand.
3. **Fit van het profiel:** de buitenzijde tot de kruin wordt in één keer gefit als
   *voorland | talud | (berm | talud) × k | kruin*, voor 0 bermen tot het maximale aantal (2). De hellingen zijn
   per deel begrensd met de regels uit de controle van `prflcheck`. Op elke grens zit een kleine marge, zodat
   een profiel na het afronden van z op `schem_decimalen` decimalen niet net over een grens valt. Het resultaat
   is daardoor altijd een geldig profiel.

Instellingen van de fit (in de app onder *Schematisatie (.prfl)*):

   | Instelling | Standaard | Betekenis |
   |---|---|---|
   | `schem_min_taludbreedte` | 2 m | minimale breedte van een talud |
   | `schem_stap` | 0,5 m | raster van de knikpunten en van het representatieve profiel |
   | `schem_voorland_fit` | 30 m | het voorland telt in de fit alleen tot zover (plus de dijkbreedte) buiten de teen, zodat een verre, hogere uiterwaard de teen niet omlaag trekt |
   | `schem_voorland_delen` | 1 | delen voorland in de fit; meer delen kan een berm aan de teen als voorland zien |
   | `schem_decimalen` | 3 | decimalen van x en z in het `.prfl`; minder decimalen geeft een grotere marge op de grenzen |

4. **Aantal bermen:** een extra berm laat het profiel altijd iets beter passen, ook als er geen berm is. Een berm
   telt daarom alleen mee als:
   - hij minstens `schem_min_bermbreedte` breed is (standaard 3 m);
   - de taluds eronder en erboven elk minstens `schem_min_taludhoogte` hoog zijn (standaard 0,5 m); anders is
     het voorland of een afgeronde kruin;
   - de afwijking van het representatieve profiel (RMSE: de gemiddelde verticale afstand, van de teen tot de
     kruin, voor alle modellen over hetzelfde stuk; een ruw voorland telt dus niet mee) er zowel met minstens
     `schem_berm_winst_rel` (standaard 0,2 = 20%) als met minstens `schem_berm_winst_abs` (standaard 0,03 m)
     door daalt. Het percentage zorgt dat de berm een wezenlijk deel van de afwijking verklaart, de centimeters
     dat het om meer gaat dan ruis. Voorbeeld: 0,15 → 0,06 m (60%, 9 cm) is een berm; 0,14 → 0,11 m (19%, 2,7 cm)
     niet; 0,020 → 0,015 m (25%, maar 5 mm) ook niet.
5. **Voorland** (`schem_voorland = ja`, standaard): het deel buiten de teen, vereenvoudigd tot punten die voldoen
   aan de regels van `prflcheck`: minstens 10 m uit elkaar, hellingen hooguit 1:10 en geen punt lager dan het eerste.
   Ligt er bij de teen een laagte of sloot die lager is dan het begin van het voorland, dan zakt het eerste punt
   naar dat niveau (voorzichtig: dieper water aan het begin van het voorland); dat staat in het memo. Het voorland
   loopt tot het eind van de dwarslijn of de waterlijn: AHN meet niet onder water. Korter dan 10 m: geen voorland.
6. **Wegschrijven en controleren:** met `prflcheck` (ProfielCheckPython), zonder dam, ruwheid `schem_ruwheid`
   (standaard 1,0; tussen de grenzen van `prflcheck`, 0,5 en 1,0), kruinhoogte van stap 2 (naar boven afgerond op cm) en als richting de gemiddelde dijknormaal
   naar buiten (graden t.o.v. noord). De x is de afstand tot de dijklijn. Het memo vermeldt het aantal bermen, de
   RMSE per model en het voorland. Elk profiel wordt gecontroleerd met de Hydra/Ringtoets-regels; een afgekeurd
   profiel wordt wel weggeschreven, met een waarschuwing in de log.

De bestanden komen in `<outputmap>/prfl/`: `<vak>.prfl`, `schematisatie.csv` (overzicht per vak, voor Excel)
met per vak het aantal bermen, de voorlandpunten en de RMSE, en `schematisatie.json` (voor de webapp). Bij opnieuw schematiseren worden de `.prfl`-bestanden van de vorige
keer eerst verwijderd; andere bestanden in de map blijven staan. Controleer het resultaat altijd: de schematisatie
is een automatisch startpunt, geen vervanging van een beoordeling van het profiel.

## Webapp

Een lokale webapp om invoer en uitvoer te bekijken en de berekening te starten. Installeren van
het pakket is niet nodig: dubbelklik op `start_app.bat`. Dat activeert de conda-omgeving
`ahnprofielcreator`, gebruikt `config.ini` naast het bestand (als die er is) en opent de browser op
<http://127.0.0.1:8050/>. Stoppen: sluit het venster of druk Ctrl+C.

- **Demo:** zonder `config.ini` (of met `--demo`) start de app met een demotraject: 3 km Lekdijk bij
  Schalkwijk (normtraject 44-1) in zes dijkvakken, met buitenlijn. Een uitgeknipte AHN5-strook komt mee,
  dus *Berekening starten* werkt direct en zonder internet (dwarslijnen tot 200 m). De output komt in
  `output\demo`. Zie `src/ahnprofielcreator/data/demo`.
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
  profiel (gestippeld), het Hydra-NL profiel (oranje) en het voorland (groen), met het aantal bermen en de uitkomst van de controle. Is de
  berekening daarna opnieuw gedaan, dan staat de schematisatie als *verouderd* bij de bestanden.

De app gebruikt alleen de standaardbibliotheek en fiona (en `prflcheck` voor het schematiseren). Leaflet staat in
`src/ahnprofielcreator/app/static/vendor/leaflet`. Van internet komen de achtergrondkaarten (BRT,
luchtfoto, AHN, via PDOK) en, tijdens de berekening, ontbrekende AHN-kaartbladen (via basisdata.nl).
Zonder internet werkt de app ook, maar zonder achtergrond en alleen met tiffs die al lokaal staan.
De app is alleen bereikbaar vanaf de eigen computer (127.0.0.1).

## Tests

In de geactiveerde omgeving, vanuit de projectmap:

```bash
pytest
```

Dit draait de snelle tests (ongeveer 20 s), waaronder een regressietest die per testgeval de buitenlijn,
intervalpunten en dwarslijnen vergelijkt met de referentie in `test/test_output/<geval>`. De testgevallen
zijn het demotraject en stukken normtraject in `test/test_input/<geval>` (gesloten ringen, multipart,
korte vakken, haarspeldbocht, bladhoek met water, wisselende tekenrichting en een duin); de README daar
beschrijft wat elk geval test, `test/test_gevallen.py` legt dat vast. De instellingen per geval staan in
`test/referentie.py`.

De AHN-hoogtes van het demotraject komen uit de meegeleverde strook en worden altijd vergeleken. Die van
de andere gevallen hebben de AHN5-tiffs (DTM 0,5 m) nodig en draaien alleen op verzoek (zonder downloads):

```bash
pytest -m ahn
```

De tiffs worden standaard gezocht in `P:\OpenData\AHN\AHN5\DTM05`; een andere map geef je op met de
omgevingsvariabele `AHN_TEST_DIR`. Ontbreekt er een blad, dan wordt de test overgeslagen.

Verandert de uitvoer bewust, maak de referentie dan opnieuw (met `AHN_TEST_DIR` als de tiffs niet op P:
staan):

```bash
python test/referentie.py
```

Met een naam erachter (bijv. `python test/referentie.py ringen`) alleen dat geval.

Een `.dbf` waarvan alleen de datum in de header verandert (byte 4, de dag) hoef je niet te committen.
