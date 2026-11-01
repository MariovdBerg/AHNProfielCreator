# Teststukken uit de normtrajecten

Geknipt uit `nbpw_dijktrajecten_formeel.shp` (normtrajecten primaire waterkeringen, RD New) en opgedeeld
in dijkvakken. Bron: [Nationale Basisbestanden Primaire Waterkeringen](https://data.overheid.nl/dataset/48540-dataset-nationale-basisbestanden-primaire-waterkeringen) van Rijkswaterstaat ([Waterveiligheidsportaal](https://waterveiligheidsportaal.nl)), publiek domein. Elk geval staat in een eigen map, met dezelfde naam als de referentie-uitvoer in
`test/test_output` (bijv. `ringen/ringen.shp`); de referentie van het demotraject (`test/test_output/demo`)
gebruikt de demobestanden uit het pakket. Elke shapefile heeft de velden `vak_id`, `traject` (normtraject), `van_m` en `tot_m`
(afstand langs het normtraject). Er is geen buitenlijn bij; gebruik `outside_offset` (bijv. 20 m, links).
Gecontroleerd met de AHN5/AHN4-tiffs (interval 10 m, dwarslijnen 100 m).

| Bestand | Traject | Wat het test | Resultaat (10 m / 100 m) |
|---|---|---|---|
| `ringen.shp` | 84-1, 18-1 | Twee gesloten ringen, één met en één tegen de klok in getekend: de afgeleide buitenlijn ligt bij de ene ring binnen, bij de andere buiten. | 16 van 646 punten zonder dwarslijn (vooral 18-1) |
| `multipart.shp` | 75-1 | Eén vak als MultiLineString van 3 losse delen (gaten van ~700 m), in willekeurige volgorde en richting. | 128 dwarslijnen |
| `korte_vakken.shp` | 228 | Lijn van 2 punten (75 m), opgedeeld in vakken van 55, 15 en 5 m; het laatste vak is korter dan het interval. | 6, 2 en 1 dwarslijn; veel nodata (bebouwing) |
| `haarspeldbocht.shp` | 204a | Bocht van ~200° over 30 m midden in vak 02: afgeleide buitenlijn valt uiteen, dwarslijnen kruisen elkaar. | 18 van 80 punten zonder dwarslijn |
| `bladhoek_water.shp` | 8-1 | Loopt op 10 m langs de hoek van vier AHN5-bladen (155000, 475000); open water (geen AHN) aan de buitenzijde. | 10% zonder hoogte, oplopend tot 44% aan de waterkant |
| `richting_wisselend.shp` | 44-1 | Het demotraject, met vak 02, 04 en 06 omgekeerd getekend: de afgeleide buitenlijn ligt bij die vakken aan de verkeerde kant. | Rekent door; controle visueel (buitenzijde wisselt) |
| `duin.shp` | 13-3 | Duinwaterkering: de lijn ligt niet op een kruin en het profiel is breder dan 100 m. Gebruik `cross_length` 300–400. | Profiel tot 22,5 m NAP |

Alle stukken liggen op één of twee AHN-bladen, behalve `bladhoek_water`, dat juist een bladgrens moet
raken.
