# Demotraject: Lekdijk bij Schalkwijk

Dit is het project `demo` in de werkmap (de ontwikkelmap): de invoer staat in `invoer`, de uitvoer komt in
`uitvoer` en de instellingen staan in `config.ini`. De webapp opent dit project als je geen ander kiest; zie
ook `ahnprofielcreator.demo`.

Het is een ondoorbroken stuk van 3 km uit normtraject 44-1 (Lekdijk, noordoever van de Lek, gemeente Houten),
van 19 750 tot 22 750 m langs het traject. Bron: `nbpw_dijktrajecten_formeel.shp` (normtrajecten primaire
waterkeringen) uit de [Nationale Basisbestanden Primaire Waterkeringen](https://data.overheid.nl/dataset/48540-dataset-nationale-basisbestanden-primaire-waterkeringen) van Rijkswaterstaat ([Waterveiligheidsportaal](https://waterveiligheidsportaal.nl)), publiek domein.

In `invoer`:

- `demo_dijkvakken.shp`: 6 dijkvakken van 500 m (`DV_01` t/m `DV_06`), velden `vak_id`, `traject`,
  `van_m`, `tot_m`.
- Geen buitenlijn: de app leidt die af (`outside_offset = 20`, `outside_side = links`). De Lek
  (buitenzijde) ligt links van de tekenrichting, dus de dijkvakken 20 m naar links verschoven geeft de
  buitenlijn, als `buitenlijn_offset.shp` in de uitvoermap.
- `2023_M_39AZ1_demo.tif`: AHN5 DTM 0,5 m van kaartblad 39AZ1, uitgeknipt tot 100 m rond de dijkvakken
  (daarbuiten nodata), zelfde grid als het blad. De app vindt het via de bladnaam, zodat de demo zonder
  download werkt bij dwarslijnen tot 200 m. Bron: AHN, CC BY 4.0.

Waarom dit stuk:

- Het hele stuk ligt binnen één AHN-kaartblad (39AZ1) dat volledig in AHN5 beschikbaar is.
- Klassiek rivierdijkprofiel: kruin ~9 m NAP precies op de lijn, uiterwaard ~4–5 m aan de buitenzijde,
  polder ~2–3 m aan de binnenzijde. Vrijwel volledige AHN-dekking (1,4% zonder hoogte).
- Rustig verloop zonder scherpe bochten: alle 300 punten (interval 10 m) krijgen een dwarslijn.
