# Ontwikkelen en testen

Voor wie aan de code werkt. Installeer eerst de omgeving en het pakket zoals in de [README](README.md#installatie),
inclusief `python -m pip install -e .`, en activeer de omgeving.

## Opbouw van de code

```
src/ahnprofielcreator/
├─ profielen_per_vak.py  Settings, run(), schematise() en de commandline (ahn-profielen)
├─ profielen/            intervalpunten, dwarslijnen en AHN-hoogtes (CrossSections, CrossSectionHeights)
├─ buitenlijn.py         buitenlijn afleiden uit de dijkvakken (offset)
├─ ahn.py                kaartbladindex, lokale tiffs zoeken en ontbrekende bladen downloaden
├─ schematisatie.py      .prfl per vak (zie docs/schematisatie.md)
├─ demo.py               instellingen van het demotraject
├─ app/                  webapp: server.py (standaardbibliotheek) en static/ (Leaflet)
└─ data/                 bladwijzer(_AHN6).gpkg
demo/                    project met het demotraject: config.ini, invoer/ en uitvoer/ (niet in git)
```

## Tests

In de geactiveerde omgeving, vanuit de ontwikkelmap:

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

De tiffs worden standaard gezocht in `ahn_data` in de ontwikkelmap (daar zet de app ook downloads neer); een
andere map geef je op met de omgevingsvariabele `AHN_TEST_DIR`. Ontbreekt er een blad, dan wordt de test overgeslagen.

## Referentie opnieuw maken

Verandert de uitvoer bewust, maak de referentie dan opnieuw (met `AHN_TEST_DIR` als de tiffs niet in
`ahn_data` staan):

```bash
python test/referentie.py
```

Met een naam erachter (bijv. `python test/referentie.py ringen`) alleen dat geval.

Een `.dbf` waarvan alleen de datum in de header verandert (byte 4, de dag) hoef je niet te committen.
