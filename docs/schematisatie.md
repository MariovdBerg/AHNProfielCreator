# Schematiseren tot .prfl

Een optionele stap ná de berekening: per dijkvak één geschematiseerd profiel (`.prfl`, versie 4) voor
Hydra-NL/Ringtoets. Hoe je de stap start en wat eruit komt, staat in de [README](../README.md#schematiseren-prfl).
Hier staat hoe het profiel tot stand komt en wat de instellingen doen.

Nodig: een buitenlijn (opgegeven of afgeleid), zodat bekend is welke kant buiten ligt, en het pakket `prflcheck`
([ProfielCheckPython](https://github.com/MariovdBerg/ProfielCheckPython)).

Controleer het resultaat altijd: de schematisatie is een automatisch startpunt, geen vervanging van een
beoordeling van het profiel.

## Werkwijze per vak

1. **Representatief profiel:** alle dwarslijnen van het vak samen, per afstand tot de dijklijn (stappen van
   `schem_stap`, standaard 0,5 m) het kwantiel `schem_kwantiel` (standaard 0,2) van de AHN-hoogtes. Een laag
   kwantiel geeft een wat lager, voorzichtiger profiel. Waar minder dan de helft van de dwarslijnen een hoogte
   heeft (water), telt het niet mee.
2. **ID:** Het `ID` van het profiel is `{traject}_{dijkvak}` (bijv. `44-1_DV_01`). Het traject-attribuut van de
   dijkvakken (instelling `traject_id`, standaard `traject`) is daarom verplicht; een vak zonder traject of in meer dan één traject geeft een foutmelding.
2. **Kruin:** het hoogste punt van dat profiel binnen `schem_kruin_zoekafstand` (standaard 10 m) van de dijklijn.
   Ligt de dijklijn niet op of bij de kruin, vergroot dan deze afstand.
3. **Fit van het profiel:** de buitenzijde tot de kruin wordt in één keer gefit als
   *voorland | talud | (berm | talud) × k | kruin*, voor 0 bermen tot het maximale aantal (2). De hellingen zijn
   per deel begrensd met de regels uit de controle van `prflcheck`. Op elke grens zit een kleine marge, zodat
   een profiel na het afronden van z op `schem_decimalen` decimalen niet net over een grens valt. Het resultaat
   is daardoor altijd een geldig profiel.
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
6. **Wegschrijven en controleren:** met `prflcheck`, zonder dam, ruwheid `schem_ruwheid` (standaard 1,0; tussen de
   grenzen van `prflcheck`, 0,5 en 1,0), kruinhoogte van stap 2 (naar boven afgerond op cm) en als richting de
   gemiddelde dijknormaal naar buiten (graden t.o.v. noord). De x is de afstand tot de dijklijn. Het memo vermeldt
   het aantal bermen, de RMSE per model en het voorland. Elk profiel wordt gecontroleerd met de
   Hydra/Ringtoets-regels; een afgekeurd profiel wordt wel weggeschreven, met een waarschuwing in de log.

Bij opnieuw schematiseren worden de `.prfl`-bestanden van de vorige keer eerst verwijderd; andere bestanden in de
map blijven staan.

## Instellingen

Alle instellingen staan ook in `config.example.ini` en in de webapp onder *Schematisatie (.prfl)*. Leeg laten
betekent de standaardwaarde.

| Instelling | Standaard | Betekenis |
|---|---|---|
| `schem_kwantiel` | 0,2 | kwantiel van de hoogtes van de profielen in een vak; lager = voorzichtiger, lager profiel |
| `schem_kruin_zoekafstand` | 10 m | zoek de kruin (hoogste punt) binnen deze afstand van de dijklijn |
| `schem_ruwheid` | 1,0 | ruwheid van het talud in het `.prfl`, 0,5 – 1,0 (1,0 = gras) |
| `schem_voorland` | `ja` | voorland (buiten de teen) in het `.prfl`: `ja` of `nee` |
| `schem_min_bermbreedte` | 3 m | minimale breedte van een berm |
| `schem_min_taludhoogte` | 0,5 m | minimale hoogte van de taluds onder en boven een berm |
| `schem_berm_winst_rel` | 0,2 | een berm moet de RMSE minstens met deze fractie laten dalen... |
| `schem_berm_winst_abs` | 0,03 m | ...en ook minstens met zoveel meter |
| `schem_min_taludbreedte` | 2 m | minimale breedte van een talud |
| `schem_stap` | 0,5 m | raster van de knikpunten en van het representatieve profiel |
| `schem_voorland_fit` | 30 m | het voorland telt in de fit alleen tot zover (plus de dijkbreedte) buiten de teen, zodat een verre, hogere uiterwaard de teen niet omlaag trekt |
| `schem_voorland_delen` | 1 | delen voorland in de fit, 1 – 3; meer delen kan een berm aan de teen als voorland zien |
| `schem_decimalen` | 3 | decimalen van x en z in het `.prfl`, 1 – 6; minder decimalen geeft een grotere marge op de grenzen |

De grenzen voor de hellingen, het aantal bermen, de afstand tussen voorlandpunten en de ruwheid komen uit
`prflcheck`.
