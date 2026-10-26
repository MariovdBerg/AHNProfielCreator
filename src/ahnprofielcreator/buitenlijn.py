# -*- coding: utf-8 -*-
"""
Buitenlijn afleiden uit de dijkvaklijnen, door ze loodrecht op de lijnrichting te verschuiven
(zoals "Offset Curves" in QGIS).

De buitenlijn bepaalt alleen aan welke kant van de dijklijn de buitenzijde ligt. Een verschoven kopie
van de dijkvakken met hetzelfde vak-attribuut volstaat daarvoor, zolang de verschuiving kleiner is
dan de halve lengte van de dwarslijnen.
"""

import fiona
import shapely
import shapely.geometry

ZIJDEN = ("links", "rechts")


def offset_geometry(geom, distance):
    """
    Verschuif een (Multi)LineString; positief = links, negatief = rechts van de lijnrichting.

    Geeft een lijst met LineStrings terug: één lijn kan bij scherpe bochten in meerdere delen uiteenvallen.
    """
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        parts = [geom]
    elif geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
    else:
        raise TypeError(f"Geometrietype '{geom.geom_type}' wordt niet ondersteund, alleen lijnen.")

    lines = []
    for part in parts:
        offset = shapely.force_2d(part).offset_curve(distance, join_style="round")
        if offset.geom_type == "MultiLineString":
            lines.extend(g for g in offset.geoms if not g.is_empty)
        elif not offset.is_empty:
            lines.append(offset)
    return lines


def offset_lines_to_file(input_file, output_file, afstand, zijde="links"):
    """
    Schrijf de dijkvaklijnen uit `input_file`, `afstand` meter naar `zijde` verschoven, naar `output_file`.

    De attributen (o.a. het vak-id) blijven behouden. Geeft het aantal geschreven lijnen terug.
    """
    if zijde not in ZIJDEN:
        raise ValueError(f"Zijde van de buitenlijn moet 'links' of 'rechts' zijn, niet '{zijde}'.")
    if afstand <= 0:
        raise ValueError(f"Afstand van de buitenlijn moet groter dan 0 zijn (is {afstand:g}).")
    distance = afstand if zijde == "links" else -afstand

    with fiona.open(input_file) as source:
        schema = source.schema.copy()
        schema["geometry"] = "MultiLineString"
        rows = [
            (offset_geometry(shapely.geometry.shape(row.geometry), distance), dict(row.properties))
            for row in source
            if row is not None and row.geometry
        ]
        crs = source.crs

    count = 0
    with fiona.open(output_file, "w", driver="ESRI Shapefile", crs=crs, schema=schema, encoding="utf-8") as sink:
        for lines, props in rows:
            if not lines:
                continue
            sink.write(
                {"geometry": shapely.geometry.mapping(shapely.geometry.MultiLineString(lines)), "properties": props}
            )
            count += 1
    return count
