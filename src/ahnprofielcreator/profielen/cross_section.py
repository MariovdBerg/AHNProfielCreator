# -*- coding: utf-8 -*-
"""
Created on Wed Jun  5 11:44:20 2019

@author: dupuits
"""


import bisect
import collections
import logging
import math
import os
import fiona
import numpy as np
import rasterio
import rasterio.errors
import rasterio.windows
import shapely.geometry
import shapely.ops
import shapely.prepared
import tqdm.auto as tqdm


from .constants import *


class IntervalPoints:
    def __init__(self, input_line_file, group_id=None, start_offset=0.01, logger=None, log_level=logging.INFO):
        # Initialize class variables.
        self.input_line_file = input_line_file
        self.line_crs = None
        self.line_schema = None
        self.line_input_props = None
        self.input_lines = None
        self.group_id = group_id
        self.default_group_id = DEFAULT_GROUP_ID
        self.start_offset = start_offset

        if logger is None:
            # Create logger
            logger = logging.getLogger("CrossSections")
            logger.setLevel(log_level)
            if len(logger.handlers) == 0:
                ch = logging.StreamHandler()
                ch.setLevel(log_level)
                logger.addHandler(ch)
        self.log_level = log_level
        self.logger = logger

    def process_input_line(self):
        with fiona.open(self.input_line_file, "r") as source:
            self.line_crs = source.crs
            self.line_schema = source.schema
            self.line_input_props = collections.OrderedDict()
            self.input_lines = collections.OrderedDict()
            self.input_line = None

            if self.group_id is not None and self.group_id not in source.schema["properties"]:
                raise ValueError(f"Attribuut '{self.group_id}' (group_id) ontbreekt in '{self.input_line_file}'")

            duplicates = set()
            for i, row in enumerate(source):
                if row is None or row["geometry"] is None:
                    continue
                if self.group_id is not None and str(row["properties"][self.group_id]) in self.input_lines:
                    duplicates.add(str(row["properties"][self.group_id]))

                # Read all rows with (multi-)linestrings into multilinestrings.
                if row["geometry"]["type"] in ["LineString", "MultiLineString"]:
                    if row["geometry"]["type"] == "LineString":
                        geom = shapely.geometry.MultiLineString([row["geometry"]["coordinates"]])
                    else:
                        geom = shapely.geometry.MultiLineString(row["geometry"]["coordinates"])

                    if self.group_id is None:
                        self.input_lines[str(i)] = geom
                        self.line_input_props[str(i)] = row["properties"]
                    else:
                        self.input_lines[str(row["properties"][self.group_id])] = geom
                        self.line_input_props[str(row["properties"][self.group_id])] = row["properties"]

            if duplicates:
                raise ValueError(
                    f"'{self.group_id}' moet uniek zijn in '{self.input_line_file}'; meerdere lijnen voor: "
                    + ", ".join(sorted(duplicates))
                )

            if len(self.input_lines) > 0:
                self.input_line = []
                for ml in self.input_lines.values():
                    for sl in ml.geoms:
                        self.input_line.append(sl)
                self.input_line = shapely.geometry.MultiLineString(self.input_line)

    def write_points_output(self, output_file, points):
        self.logger.debug(f"Writing interval points to '{output_file}'...")

        if self.input_lines is None:
            self.process_input_line()

        key = self.default_group_id
        if self.group_id is not None:
            key = self.group_id

        # Extend existing schema.
        schema = self.line_schema.copy()
        schema["geometry"] = "Point"
        schema["properties"][DEFAULT_POINT_ID] = "int"
        schema["properties"][key] = "str"

        with fiona.open(output_file, "w", "ESRI Shapefile", schema, crs=self.line_crs) as source:
            total = sum([len(p) for p in points.values()])
            with tqdm.tqdm(total=total, desc=f"Writing interval points to '{output_file}'") as pbar:
                i = 0
                for subkey, subpoints in points.items():
                    for point in subpoints:
                        i += 1

                        props = dict(self.line_input_props[subkey])  # Gewone dict: fiona-attributen worden onveranderlijk
                        props[DEFAULT_POINT_ID] = i
                        props[key] = subkey
                        source.write(
                            {
                                "geometry": shapely.geometry.mapping(point),
                                "properties": props,
                            }
                        )
                        pbar.update(1)

        self.logger.debug("Done.")

    def interval_points(self, interval):
        self.logger.debug(f"Creating interval points with spacing {interval}m...")

        if self.input_lines is None:
            self.process_input_line()

        spacing = collections.OrderedDict()
        for key, input_line in self.input_lines.items():
            spacing[key] = np.arange(self.start_offset, input_line.length, interval).round(decimals=3)

        total = sum([v.size for v in spacing.values()])
        with tqdm.tqdm(total=total, desc=f"Creating interval points with spacing {interval}m") as pbar:
            points = collections.OrderedDict()
            for key, input_line in self.input_lines.items():
                subpoints = []
                for distance in spacing[key]:
                    subpoints.append(input_line.interpolate(distance))
                    pbar.update(1)
                points[key] = subpoints

        return points

    def interval_points_to_file(self, output_points_file, interval=100.0):
        # Create points along the line for the given interval.
        points = self.interval_points(interval)

        # Write points to shapefile.
        self.write_points_output(output_points_file, points)

        return points


class CrossSections(IntervalPoints):
    def __init__(
        self,
        input_line_file,
        input_points_file,
        outside_line_file=None,
        group_id=None,
        tolerance=1e-8,
        start_offset=0.01,
        logger=None,
        log_level=logging.INFO,
    ):
        # Initialize base class.
        super().__init__(
            input_line_file, group_id=group_id, logger=logger, log_level=log_level, start_offset=start_offset
        )

        # User-supplied arguments for this class.
        self.input_points_file = input_points_file
        self.tolerance = tolerance
        self.outside_line_file = outside_line_file

        # Input points defaults.
        self.point_crs = None
        self.point_schema = None
        self.point_input_props = None
        self.input_points = None

        # Default outside line
        self.outside_line = None

        # Index of the input point per created cross-section (see interval_crosssections).
        self.cross_point_indices = None

    def process_input_points(self):
        with fiona.open(self.input_points_file, "r") as source:
            self.point_crs = source.crs
            self.point_schema = source.schema
            self.point_input_props = collections.OrderedDict()
            self.input_points = collections.OrderedDict()

            for row in source:
                if self.group_id is None:
                    key = str(row["properties"][self.default_group_id])
                else:
                    key = str(row["properties"][self.group_id])

                if key not in self.input_points:
                    self.input_points[key] = []

                point = shapely.geometry.Point(row["geometry"]["coordinates"])
                self.input_points[key].append(point)

                if key not in self.point_input_props:
                    self.point_input_props[key] = []
                self.point_input_props[key].append(row["properties"])

    def process_outside_line(self):
        if self.outside_line_file is None:
            self.outside_line = None
        else:
            with fiona.open(self.outside_line_file, "r") as source:
                geoms = {}
                geoms_single = []
                for row in source:
                    if row is None or row["geometry"] is None:
                        continue
                    k = None
                    if self.group_id is not None and self.group_id in row["properties"]:
                        k = str(row["properties"][self.group_id])
                        if k not in geoms:
                            geoms[k] = []

                    # Read all rows with (multi-)linestrings into multilinestrings.
                    if row["geometry"]["type"] in ["LineString", "MultiLineString"]:
                        if row["geometry"]["type"] == "LineString":
                            if k is not None:
                                geoms[k].append(shapely.geometry.LineString(row["geometry"]["coordinates"]))
                            else:
                                geoms_single.append(shapely.geometry.LineString(row["geometry"]["coordinates"]))
                        else:
                            for coords in row["geometry"]["coordinates"]:
                                if k is not None:
                                    geoms[k].append(shapely.geometry.LineString(coords))
                                else:
                                    geoms_single.append(shapely.geometry.LineString(coords))

                if len(geoms) > 0 and len(geoms_single) > 0:
                    raise ValueError("both geoms and geoms_single are not empty, this is most unexpected...")
                elif len(geoms) == 0 and len(geoms_single) == 0:
                    raise ValueError(f"Buitenlijn '{self.outside_line_file}' bevat geen lijnen")
                elif len(geoms) > 0:
                    self.outside_line = {k: shapely.geometry.MultiLineString(v) for k, v in geoms.items()}
                elif len(geoms_single) > 0:
                    self.outside_line = shapely.geometry.MultiLineString(geoms_single)

    def write_crosssections_output(self, output_file, cross_points, crosssections, angles, point_indices=None):
        # `point_indices[key][j]` is the index of the input point of cross-section j. Cross-sections can be
        # skipped (no intersection with the outside line), so j is not necessarily the point index.
        self.logger.debug(f"Writing cross-sections to '{output_file}'...")

        if self.input_lines is None:
            self.process_input_line()
        if self.input_points is None:
            self.process_input_points()

        key = str(self.default_group_id)
        if self.group_id is not None:
            key = str(self.group_id)

        # Extend existing schema.
        schema = self.point_schema.copy()
        schema["geometry"] = "LineString"
        schema["properties"][DEFAULT_CROSS_ID] = "int"
        schema["properties"][key] = "str"
        schema["properties"]["mid_x"] = "float"
        schema["properties"]["mid_y"] = "float"
        schema["properties"][DEFAULT_LEFTRIGHT] = "bool"
        schema["properties"][DEFAULT_TOPBOTTOM] = "bool"
        schema["properties"]["angle"] = "float"

        with fiona.open(output_file, "w", "ESRI Shapefile", schema, crs=self.point_crs) as source:
            total = sum([len(c) for c in crosssections.values()])
            with tqdm.tqdm(total=total, desc=f"Writing cross-sections to '{output_file}'") as pbar:
                i = 0
                for subkey, subcross in crosssections.items():
                    points = [p for p in cross_points[subkey]]

                    for j, line in enumerate(subcross):
                        i += 1
                        p1 = shapely.geometry.Point(list(line.coords)[0])
                        p2 = shapely.geometry.Point(list(line.coords)[-1])

                        point_index = j if point_indices is None else point_indices[subkey][j]
                        props = dict(self.point_input_props[subkey][point_index])  # Gewone dict: fiona-attributen worden onveranderlijk
                        props[DEFAULT_CROSS_ID] = i
                        props[key] = subkey
                        props["mid_x"] = points[j].x
                        props["mid_y"] = points[j].y
                        props[DEFAULT_LEFTRIGHT] = bool(p1.x <= p2.x)
                        props[DEFAULT_TOPBOTTOM] = bool(p1.y >= p2.y)
                        props["angle"] = angles[subkey][j]

                        source.write(
                            {
                                "geometry": shapely.geometry.mapping(line),
                                "properties": props,
                            }
                        )
                        pbar.update(1)

        self.logger.debug("Done.\n")

    @staticmethod
    def get_snapped_points(line, points):
        # Linear referencing: https://en.wikipedia.org/wiki/Linear_referencing
        snapped_points = [line.interpolate(line.project(p)) for p in points]
        snapped_points = shapely.geometry.MultiPoint(snapped_points)

        return snapped_points

    @staticmethod
    def get_line_coords(line):
        # Get coordinates of (multi-) linestring.
        coords = []
        for l in line.geoms:
            coords += list(l.coords)
        coords = shapely.geometry.MultiPoint(coords)

        # Get the distances of the coordinate relative to the first coordinate.
        coords_dist = []
        for point in coords.geoms:
            coords_dist.append(line.project(point))

        # Ordered (sorted) dictionary of coordinates along line.
        line_coords = dict(zip(coords_dist, coords.geoms))
        line_coords = collections.OrderedDict(sorted(line_coords.items()))

        return line_coords

    @staticmethod
    def get_enclosing_coords(point, line, line_coords):
        # Distance along line for the desired point.
        dist_point = line.project(point)

        # Find the insertion point in the list of sorted coordinates.
        distances = list(line_coords.keys())
        insert_ind = bisect.bisect_left(distances, dist_point)

        # Sanity checks for the found index.
        if insert_ind >= len(distances):
            insert_ind = len(distances) - 1
        if insert_ind == 0:
            insert_ind = 1

        # Return the enclosing coordinates.
        ind1 = distances[insert_ind - 1]
        ind2 = distances[insert_ind]
        point1 = line_coords[ind1]
        point2 = line_coords[ind2]

        return point1, point2

    @staticmethod
    def determine_angle(point1, point2):
        xdist = point2.x - point1.x
        ydist = point2.y - point1.y
        rad_angle = math.atan2(xdist, ydist)
        if rad_angle < 0:
            rad_angle += 2 * math.pi
        deg_angle = math.degrees(rad_angle)

        return deg_angle

    @staticmethod
    def determine_profile_angle(center_point, offset_point):
        xdist = offset_point.x - center_point.x
        ydist = offset_point.y - center_point.y

        if xdist == 0 and ydist == 0:
            raise ValueError("Cannot determine angle (xdist and ydist are zero)")
        elif xdist == 0 and ydist > 0:
            angle = 0
        elif xdist == 0 and ydist < 0:
            angle = 180
        elif xdist > 0 and ydist == 0:
            angle = 90
        elif xdist < 0 and ydist == 0:
            angle = 270
        else:
            if xdist > 0 and ydist > 0:
                angle = math.degrees(math.atan2(abs(xdist), abs(ydist)))
            elif xdist > 0 and ydist < 0:
                angle = 180 - math.degrees(math.atan2(abs(xdist), abs(ydist)))
            elif xdist < 0 and ydist < 0:
                angle = 180 + math.degrees(math.atan2(abs(xdist), abs(ydist)))
            elif xdist < 0 and ydist > 0:
                angle = 360 - math.degrees(math.atan2(abs(xdist), abs(ydist)))
            else:
                raise ValueError(f"Unknown condition in angle calculation")

        return angle

    def create_crosssection(self, point, line, line_coords, length_crosssection, key=None, idx=None):
        angle = None
        if line.distance(point) <= self.tolerance:
            # Point is on the line, get the closest coordinates.
            point1, point2 = self.get_enclosing_coords(point, line, line_coords)

            if point2 is not None:
                angle = self.determine_angle(point1, point2)

        if angle is None:
            raise ValueError(f"Could not find a cross-section angle for {point}")

        x_0 = point.x - 0.5 * length_crosssection * math.cos(math.radians(angle))
        x_1 = point.x + 0.5 * length_crosssection * math.cos(math.radians(angle))
        y_0 = point.y + 0.5 * length_crosssection * math.sin(math.radians(angle))
        y_1 = point.y - 0.5 * length_crosssection * math.sin(math.radians(angle))
        cross_section = shapely.geometry.LineString([(x_0, y_0), (x_1, y_1)])

        # Check if the cross-section needs to be reverted
        prof_angle = None
        if self.outside_line is not None:
            if isinstance(self.outside_line, dict):
                outline = self.outside_line[key]
            else:
                outline = self.outside_line
            pbuiten = cross_section.intersection(outline)
            if not pbuiten.is_empty:
                # Closest intersection to the point; also works when the outside line partly overlaps the
                # cross-section (LineString or GeometryCollection instead of (Multi)Point).
                pbuiten = shapely.ops.nearest_points(pbuiten, point)[0]
                if pbuiten.distance(point) <= self.tolerance:
                    # Outside line crosses the dike line here: the outside cannot be determined.
                    pbuiten = shapely.geometry.Point()

            if not pbuiten.is_empty:
                dbuiten = cross_section.project(pbuiten)
                dtraject = cross_section.project(point)
                if dbuiten > dtraject:
                    cross_section = shapely.geometry.LineString(cross_section.coords[::-1])
                prof_angle = self.determine_profile_angle(point, pbuiten)
            else:
                cross_section = None
                # Summarised per group in interval_crosssections.
                self.logger.debug(f"Could not find intersection with outside line, skipping {key} - {idx}")

        return cross_section, prof_angle

    def interval_crosssections(self, length_crosssection):
        self.logger.debug(f"Creating cross-sections with length {length_crosssection}m...")

        if self.input_lines is None:
            self.process_input_line()
        if self.input_points is None:
            self.process_input_points()
        if self.outside_line is None:
            self.process_outside_line()

        if isinstance(self.outside_line, dict):
            missing = [k for k in self.input_lines if k not in self.outside_line]
            if missing:
                raise ValueError(
                    f"Buitenlijn '{self.outside_line_file}' heeft geen lijn voor '{self.group_id}': "
                    + ", ".join(missing)
                )

        missing = [k for k in self.input_lines if k not in self.input_points]
        if missing:
            raise ValueError(f"Geen intervalpunten voor (te korte lijnen?): {', '.join(missing)}")

        total = sum([len(self.input_points[k]) for k in self.input_lines])
        with tqdm.tqdm(total=total, desc=f"Creating cross-sections with length {length_crosssection}m") as pbar:
            cross_points = collections.OrderedDict()
            cross_sections = collections.OrderedDict()
            cross_section_angles = collections.OrderedDict()
            self.cross_point_indices = collections.OrderedDict()
            for key, line in self.input_lines.items():
                # Initialize empty list of cross-sections.
                cross_points[key] = []
                cross_sections[key] = []
                cross_section_angles[key] = []
                self.cross_point_indices[key] = []

                # Snap points to line.
                snapped_points = self.get_snapped_points(line, self.input_points[key])

                if snapped_points.geom_type == "MultiPoint":
                    line_coords = self.get_line_coords(line)
                    for idx, point in enumerate(snapped_points.geoms):
                        c_s, angle = self.create_crosssection(
                            point, line, line_coords, length_crosssection, key=key, idx=idx
                        )
                        if c_s is not None:
                            cross_points[key].append(point)
                            cross_sections[key].append(c_s)
                            cross_section_angles[key].append(angle)
                            self.cross_point_indices[key].append(idx)
                        pbar.update(1)
                else:
                    raise ValueError(f"Snapped points have unknown geometry ({snapped_points.geom_type})")

        self.log_skipped(cross_sections)
        return cross_points, cross_sections, cross_section_angles

    def log_skipped(self, cross_sections, max_groups=10):
        """One warning for all points without a cross-section (no intersection with the outside line)."""
        skipped = {k: len(self.input_points[k]) - len(v) for k, v in cross_sections.items()}
        skipped = {k: n for k, n in skipped.items() if n > 0}
        if not skipped:
            return
        total = sum(len(self.input_points[k]) for k in cross_sections)
        groups = ", ".join(f"{k} ({n})" for k, n in sorted(skipped.items(), key=lambda kn: -kn[1])[:max_groups])
        if len(skipped) > max_groups:
            groups += f" en nog {len(skipped) - max_groups} vak(ken)"
        self.logger.warning(
            f"Geen dwarslijn voor {sum(skipped.values())} van {total} punten: de dwarslijn snijdt de buitenlijn "
            f"niet (of snijdt de dijklijn zelf). Per vak: {groups}."
        )

    def interval_points_to_file(self, interval=100.0):
        # Wrap parent method with already supplied input points file.
        super().interval_points_to_file(self.input_points_file, interval=interval)

    def crosssections_to_file(self, output_crosssections_file, length_crosssection=100.0):
        # find crosssections at points.
        cross_points, cross_sections, angles = self.interval_crosssections(length_crosssection)

        # Write crosssections to shapefile.
        self.write_crosssections_output(
            output_crosssections_file, cross_points, cross_sections, angles, point_indices=self.cross_point_indices
        )

        return cross_sections


class CrossSectionHeights:
    nodata = NODATA
    lines_char = collections.OrderedDict(
        [
            (981, "Maaiveld buitenwaarts"),
            (985, "Teen geul"),
            (983, "Insteek geul"),
            (916, "Teen dijk buitenwaarts"),
            (7, "Kruin buitenberm"),
            (12, "Insteek buitenberm"),
            (918, "Kruin buitentalud"),
            (19, "Verkeersbelasting kant binnenwaarts"),
            (18, "Verkeersbelasting kant buitenwaarts"),
            (917, "Kruin binnentalud"),
            (13, "Insteek binnenberm"),
            (6, "Kruin binnenberm"),
            (915, "Teen dijk binnenwaarts"),
            (14, "Insteek sloot dijkzijde"),
            (15, "Slootbodem dijkzijde"),
            (16, "Slootbodem polderzijde"),
            (17, "Insteek sloot polderzijde"),
            (0, "Maaiveld binnenwaarts"),
        ]
    )
    extra_lines_char = {
        903: "Insteek buitendijkse sloot (rivierzijde)",
        904: "Insteek buitendijkse sloot (waterkeringzijde)",
        905: "Bodem buitendijkse sloot (rivierzijde)",
        906: "Bodem buitendijkse sloot (waterkeringzijde)",
        907: "As bodem sloot",
        910: "As buitendijkse sloot (rivierzijde)",
        919: "Kruin midden",
        970: "Maaiveld muur (rivierzijde)",
        972: "Bovenkant muur (rivierzijde)",
        973: "Bovenkant muur (polderzijde)",
        971: "Maaiveld muur (polderzijde)",
        980: "Maaiveld achterland",
        982: "Linker insteek geul",
        984: "Linker teen geul",
        986: "As teen geul",
    }

    def __init__(
        self,
        crosssections_file,
        group_id=None,
        ahn_tiles=None,
        ahn_stepsize=0.5,
        lines_file=None,
        lines_id=None,
        lines_tolerance=1e-8,
        lines_filter_col=None,
        lines_filter_val=None,
        logger=None,
        log_level=logging.INFO,
    ):

        # User-supplied arguments.
        self.crosssections_file = crosssections_file
        self.default_group_id = DEFAULT_GROUP_ID
        self.group_id = group_id

        self.lines_filter_col = None
        self.lines_filter_val = None
        if lines_filter_col is not None and lines_filter_val is not None:
            self.lines_filter_col = lines_filter_col
            self.lines_filter_val = lines_filter_val

        # AHN (ahnprofielcreator.ahn.AHNTiles: kaartbladen en lokale/gedownloade tiffs)
        self.ahn_tiles = ahn_tiles
        self.ahn_stepsize = ahn_stepsize

        # 3D lines file
        self.lines_file = lines_file
        self.lines_id = lines_id
        self.lines_tolerance = lines_tolerance

        # Input cross-sections.
        self.cross_crs = None
        self.cross_schema = None
        self.cross_input_props = None
        self.cross_lines = None

        if logger is None:
            # Create logger
            logger = logging.getLogger("CrossSections")
            logger.setLevel(log_level)
            if len(logger.handlers) == 0:
                ch = logging.StreamHandler()
                ch.setLevel(log_level)
                logger.addHandler(ch)
        self.log_level = log_level
        self.logger = logger

    def process_crosssections(self):
        with fiona.open(self.crosssections_file, "r") as source:
            self.cross_crs = source.crs
            self.cross_schema = source.schema
            self.cross_input_props = collections.OrderedDict()
            self.cross_lines = collections.OrderedDict()

            for row in source:
                key = self.default_group_id
                if self.group_id is not None:
                    key = row["properties"][self.group_id]

                if key not in self.cross_lines:
                    self.cross_lines[key] = []

                l_s = shapely.geometry.LineString(row["geometry"]["coordinates"])
                self.cross_lines[key].append(l_s)

                if key not in self.cross_input_props:
                    self.cross_input_props[key] = []
                self.cross_input_props[key].append(row["properties"])

    def write_crosssections_output(self, output_file, crosssections_height, method):
        self.logger.debug(f"Writing cross-sections with heights to '{output_file}'...")

        self.process_crosssections()

        key = self.default_group_id
        if self.group_id is not None:
            key = self.group_id

        # Extend existing schema.
        schema = self.cross_schema.copy()
        schema["properties"][DEFAULT_CROSS_ZD] = "int"
        schema["properties"][key] = "str"
        if method.lower() == "lines":
            schema["properties"][DEFAULT_CROSS_PNM] = "str"
            schema["properties"][DEFAULT_CROSS_PID] = "int"
            schema["geometry"] = "3D Point"
        elif method.lower() == "ahn":
            schema["geometry"] = "3D LineString"

        with fiona.open(output_file, "w", "ESRI Shapefile", schema, crs=self.cross_crs) as source:
            total = sum([len(c) for c in crosssections_height.values()])
            with tqdm.tqdm(total=total, desc=f"Writing cross-sections with heights to '{output_file}'") as pbar:
                i = 0
                for subkey, subcross in crosssections_height.items():
                    for j, line in enumerate(subcross):
                        i += 1

                        props = dict(self.cross_input_props[subkey][j])  # Gewone dict: fiona-attributen worden onveranderlijk
                        props[DEFAULT_CROSS_ZD] = i
                        props[key] = subkey

                        if method.lower() == "lines":
                            for l_e, l_i in line:
                                props[DEFAULT_CROSS_PID] = l_i
                                props[DEFAULT_CROSS_PNM] = None
                                if l_i in self.lines_char:
                                    props[DEFAULT_CROSS_PNM] = self.lines_char[l_i]
                                elif l_i in self.extra_lines_char:
                                    props[DEFAULT_CROSS_PNM] = self.extra_lines_char[l_i]
                                source.write(
                                    {
                                        "geometry": shapely.geometry.mapping(l_e),
                                        "properties": props,
                                    }
                                )
                        elif method.lower() == "ahn":
                            source.write(
                                {
                                    "geometry": shapely.geometry.mapping(line),
                                    "properties": props,
                                }
                            )
                        pbar.update(1)

        self.logger.debug("Done.")

    def process_lines_file(self):
        self.logger.debug(f"Processing 3D lines file '{self.lines_file}' for column '{self.lines_id}'...")

        elev_lines, elev_prep, elev_props = None, None, None
        with fiona.open(self.lines_file, "r") as source:
            elev_lines, elev_prep, elev_props = [], [], []

            for row in tqdm.tqdm(
                source, desc=f"Processing 3D lines file '{self.lines_file}' for column '{self.lines_id}'"
            ):
                if row is None or row["geometry"] is None:
                    continue

                if self.lines_filter_col is not None and self.lines_filter_val is not None:
                    # Ignore lines which do not comply with the given filter.
                    if row["properties"][self.lines_filter_col] != self.lines_filter_val:
                        continue

                elev_lines.append(shapely.geometry.shape(row["geometry"]))
                elev_prep.append(shapely.prepared.prep(elev_lines[-1]))
                elev_props.append(row["properties"][self.lines_id])

        return elev_lines, elev_prep, elev_props

    @staticmethod
    def get_closest_unique_point(line, inter_geo):
        # Multiple matches for a supposedly unique point. Only
        # use the one closest to the center point.
        m_p = [e for e in inter_geo if e.geom_type.lower() == "point"]

        # Center point on line.
        center = line.interpolate(0.5, normalized=True)

        # Point with the shortest distance to the line.
        shortest_dist = math.inf
        match = None
        for point in m_p:
            distance = abs(center.distance(point))
            if distance < shortest_dist:
                shortest_dist = distance
                match = point

        if match is None:
            raise ValueError("Could not find a closest unique point")

        return match

    @staticmethod
    def find_raster_height(dataset, heights, geom, nodata, window=None):
        # `heights` is the part of the raster read with `window` (None = the whole raster).
        height = nodata

        row, col = dataset.index(geom.x, geom.y)
        if window is not None:
            row -= int(window.row_off)
            col -= int(window.col_off)
        maxrow, maxcol = heights.shape
        if 0 <= row < maxrow and 0 <= col < maxcol:
            value = heights[row, col]
            if value != dataset.nodata and not np.isnan(value):
                height = float(value)

        return shapely.geometry.Point(geom.x, geom.y, height)

    def read_raster_window(self, dataset, lines):
        """Read only the part of the raster around `lines` (a full 0.5 m AHN sheet is ~500 MB)."""
        bounds = shapely.geometry.MultiLineString(lines).bounds
        window = rasterio.windows.from_bounds(*bounds, transform=dataset.transform)
        # One extra cell on each side, so points on the edge of the bounds are inside the window.
        window = rasterio.windows.Window(
            math.floor(window.col_off) - 1,
            math.floor(window.row_off) - 1,
            math.ceil(window.width) + 3,
            math.ceil(window.height) + 3,
        )
        try:
            window = window.intersection(rasterio.windows.Window(0, 0, dataset.width, dataset.height))
        except rasterio.errors.WindowError:
            return np.empty((0, 0)), rasterio.windows.Window(0, 0, 0, 0)
        return dataset.read(1, window=window), window

    def warn_empty_sheet(self, name, path, n_inside, n_missing):
        """Warn if most points on an AHN sheet got no height, e.g. a sheet that is only partly surveyed."""
        if n_inside == 0 or n_missing / n_inside < EMPTY_SHEET_FRACTION:
            return
        self.logger.warning(
            f"Blad {name}: {100 * n_missing / n_inside:.0f}% van de {n_inside} punten op dit blad heeft geen "
            f"hoogte ('{os.path.basename(path)}'). Het blad is mogelijk (nog) niet volledig ingewonnen of ligt "
            "grotendeels in water; controleer de AHN-dekking op de kaart of kies een andere AHN-versie."
        )

    def crosssections_height_ahn(self, dry_run=False):
        ahn_map = {}
        total_lines = 0
        all_lines = []
        for ckey, lines in self.cross_lines.items():
            total_lines += len(lines)
            all_lines += lines

            # Keep track which ckeys correspond to which ahn sheet
            for n_ahn in self.ahn_tiles.sheet_names(lines):
                if n_ahn not in ahn_map:
                    ahn_map[n_ahn] = {"valid": None, "colkeys": []}
                ahn_map[n_ahn]["colkeys"].append(ckey)

        ahn_files = ", ".join(ahn_map.keys())
        self.logger.info(f"Finding heights for {total_lines} line(s) in {len(ahn_map)} AHN raster file(s): {ahn_files}...")
        if all_lines:
            self.ahn_tiles.report_ahn6(all_lines)

        if dry_run:
            self.logger.info("DRY RUN MODE, returning list of required AHN tiles")
            return ahn_map

        # Find local AHN files, download the missing ones.
        for n_ahn, ahndict in ahn_map.items():
            ahndict["valid"] = self.ahn_tiles.file(n_ahn)

        cross_heights = collections.OrderedDict()
        for ahn_index, ahndict in tqdm.tqdm(ahn_map.items(), desc="Finding heights in AHN raster files"):
            # Validate the ahn file
            if ahndict["valid"] is None:
                # AHNTiles has already warned about this sheet.
                self.logger.debug(f"Could not find a valid file for AHN index {ahn_index}")
                continue

            # Create points along the line with the user-supplied stepsize
            # (if the points do not exist already).
            for ckey in ahndict["colkeys"]:
                if ckey not in cross_heights:
                    cross_heights[ckey] = []
                    for i, line in enumerate(self.cross_lines[ckey]):
                        steps = range(0, math.ceil(round(line.length, 2) / self.ahn_stepsize) + 1)
                        points = [line.interpolate(s * self.ahn_stepsize) for s in steps]
                        cross_heights[ckey].append(points)

            # Add height information to points.
            total_lines = sum([len(self.cross_lines[ckey]) for ckey in ahndict["colkeys"]])
            n_inside = n_missing = 0  # Points within this raster, and those without a height
            with tqdm.tqdm(total=total_lines, desc="Parsing lines for this AHN file", leave=False) as pbar:
                with rasterio.open(ahndict["valid"]) as dataset:
                    bounds = dataset.bounds
                    sheet_lines = [line for ckey in ahndict["colkeys"] for line in self.cross_lines[ckey]]
                    ahn_raster, window = self.read_raster_window(dataset, sheet_lines)
                    for ckey in ahndict["colkeys"]:
                        for i, line in enumerate(self.cross_lines[ckey]):
                            for j, point in enumerate(cross_heights[ckey][i]):
                                if point.has_z and not math.isnan(point.z) and point.z != self.nodata:
                                    continue
                                point = self.find_raster_height(dataset, ahn_raster, point, self.nodata, window)
                                cross_heights[ckey][i][j] = point
                                if bounds.left <= point.x < bounds.right and bounds.bottom < point.y <= bounds.top:
                                    n_inside += 1
                                    n_missing += point.z == self.nodata
                            pbar.update(1)
            self.warn_empty_sheet(ahn_index, ahndict["valid"], n_inside, n_missing)

        # Lines without any AHN file get points without heights.
        for ckey, lines in self.cross_lines.items():
            if ckey not in cross_heights:
                cross_heights[ckey] = []
                for line in lines:
                    steps = range(0, math.ceil(round(line.length, 2) / self.ahn_stepsize) + 1)
                    cross_heights[ckey].append([line.interpolate(s * self.ahn_stepsize) for s in steps])

        # Same order as the input cross-sections (the loop above goes per AHN sheet).
        cross_heights = collections.OrderedDict((ckey, cross_heights[ckey]) for ckey in self.cross_lines)

        # Convert each list of points to a linestring.
        self.logger.debug("Converting lists of points to linestrings...")
        total = sum([len(cross_heights[k]) for k in self.cross_lines])
        with tqdm.tqdm(total=total, desc="Converting lists of points to linestrings") as pbar:
            for ckey, lines in self.cross_lines.items():
                for i, points in enumerate(cross_heights[ckey]):
                    # Ensure that each line has a z value. Otherwise, assumptions
                    # might occur (such as 0). We want it to be nodata.
                    fixed_points = []
                    for p in points:
                        if not p.has_z:
                            p = shapely.geometry.Point(p.x, p.y, self.nodata)
                        fixed_points.append(p)

                    # Save list of points as a LineString. Points without height are kept (z = nodata),
                    # so the points stay at a fixed stepsize along the line.
                    cross_heights[ckey][i] = shapely.geometry.LineString(fixed_points)
                    pbar.update(1)

        return cross_heights

    def crosssections_height_lines(self):
        els, epr, epo = self.process_lines_file()

        self.logger.debug("Finding points which intersect cross-sections and 3D lines...")

        total = sum([len(l) for l in self.cross_lines.values()])
        with tqdm.tqdm(total=total, desc="Finding points which intersect cross-sections and 3D lines") as pbar:
            cross_heights = collections.OrderedDict()
            for i, (key, lines) in enumerate(self.cross_lines.items()):
                cross_heights[key] = []

                # Find the intersecting points for all 3D lines.
                ml_lines = shapely.geometry.MultiLineString(lines)
                intersections = [
                    (l.intersection(ml_lines), o) for l, p, o in zip(els, epr, epo) if p.intersects(ml_lines)
                ]

                for line in lines:
                    collect_points = collections.OrderedDict()
                    for elev_points, elev_prop in intersections:
                        inter_geo = []
                        if elev_points.geom_type.lower() == "point":
                            if line.distance(elev_points) < self.lines_tolerance:
                                inter_geo = [elev_points]
                        elif elev_points.geom_type.lower() == "multipoint":
                            inter_geo = [p for p in elev_points.geoms if line.distance(p) <= self.lines_tolerance]
                        elif elev_points.geom_type.lower() == "geometrycollection" and not elev_points:
                            # Empty collection.
                            pass
                        else:
                            # Unexpected type.
                            raise ValueError(
                                f"Unexpected geometry type result for"
                                + f"for intersecting with 3D lines ({elev_points.geom_type})"
                            )

                        if inter_geo:
                            # Collect points per type.
                            if elev_prop not in collect_points:
                                collect_points[elev_prop] = []
                            collect_points[elev_prop] += inter_geo

                    # Reduce multiple points to a single point.
                    matches = []
                    for elev_prop, inter_geo in collect_points.items():
                        if len(inter_geo) > 1:
                            match = self.get_closest_unique_point(line, inter_geo)
                            matches.append((match, elev_prop))
                        elif len(inter_geo) == 1:
                            matches.append((inter_geo[0], elev_prop))

                    # Store the matches
                    cross_heights[key].append(matches)
                    pbar.update(1)

        return cross_heights

    def crosssections_height(self, method, dry_run=False):
        self.logger.debug(f"Creating cross-sections with heights (method: '{method}')...")

        self.process_crosssections()

        if method.lower() == "ahn":
            if self.ahn_tiles is None:
                raise ValueError("AHN tiles are not supplied (ahn_tiles)")
            return self.crosssections_height_ahn(dry_run=dry_run)

        if method.lower() == "lines":
            if self.lines_file is None:
                raise ValueError("3D lines shapefile is not supplied (lines_file)")
            if self.lines_id is None:
                raise ValueError("Field of 3D lines shapefile is not supplied (lines_id)")
            if dry_run:
                self.logger.warning("Lines has no dry-run functionality, ignoring dry_run parameter")
            return self.crosssections_height_lines()

        # Method is not ahn or lines.
        raise ValueError(f"The method should either be 'ahn' or 'lines' (is '{method}')")

    def crosssections_height_to_file(self, output_crosssections_height_file, method):
        # Determine cross-sections with heights.
        crosssections_height = self.crosssections_height(method)

        # Write cross-sections with height to a shapefile.
        self.write_crosssections_output(output_crosssections_height_file, crosssections_height, method)

        return crosssections_height


def minmax_per_segment(line, segment_id, c_points, c_heights, cross_id, tol=1e-8, maxdist=math.inf):
    # Process line segments.
    segments = collections.OrderedDict()
    with fiona.open(line, "r") as source:
        for row in source:
            if row is None:
                continue
            l_s = shapely.geometry.LineString(row["geometry"]["coordinates"])
            segments[row["properties"][segment_id]] = l_s

    # Process cross centerpoints and lines.
    cross_col = collections.OrderedDict()
    with fiona.open(c_points, "r") as source:
        for row in source:
            if row is None:
                continue
            point = shapely.geometry.Point(row["geometry"]["coordinates"])
            cross_col[row["properties"][cross_id]] = {"point": point, "line": None, "rows": []}
    with fiona.open(c_heights, "r") as source:
        for row in source:
            if row is None:
                continue

            # Shorthand key.
            ckey = row["properties"][cross_id]

            # Append the whole row
            cross_col[ckey]["rows"].append(row)

            # Convert geometry info into shapely geometry.
            if row["geometry"]["type"] == "LineString":
                l_s = shapely.geometry.LineString(row["geometry"]["coordinates"])
                cross_col[ckey]["line"] = l_s
            elif row["geometry"]["type"] == "Point":
                if cross_col[ckey]["line"] is None:
                    cross_col[ckey]["line"] = []
                point = shapely.geometry.Point(row["geometry"]["coordinates"])
                cross_col[ckey]["line"].append(point)
            else:
                raise ValueError(f"Unexpected geometry type ({row['geometry']['type']})")

    # Convert lists of points (from 3D lines file) to a linestring.
    for ckey, cdict in cross_col.items():
        if cdict["line"] is None or isinstance(cdict["line"], list):
            if not cdict["line"]:
                # No points, add fictive linestring.
                l_s = shapely.geometry.LineString([(0, 0, NODATA), (1, 1, NODATA)])
            elif len(cdict["line"]) == 1:
                # Single point, add a fictive point to the linestring.
                l_s = shapely.geometry.LineString([(0, 0, NODATA)] + cdict["line"])
            else:
                # Two or more points, convert points to linestring.
                l_s = shapely.geometry.LineString(cdict["line"])
            # Convert list of points to LineString.
            cross_col[ckey]["line"] = l_s

    # Snap centerpoints to joined segments.
    joined = shapely.geometry.MultiLineString([s for s in segments.values()])
    points = [c["point"] for c in cross_col.values()]
    psnapp = CrossSections.get_snapped_points(joined, points)

    # Find for each point the segment on which it lies.
    segment_crosslines = collections.OrderedDict()
    for cid, point in zip(cross_col, psnapp):
        for sid, segment in segments.items():
            if segment.distance(point) <= tol:
                if sid not in segment_crosslines:
                    segment_crosslines[sid] = []
                # Group the lines per segment id
                segment_crosslines[sid].append(cross_col[cid])
        if sid not in segment_crosslines:
            raise ValueError(f"Could not find {sid} in segment_crosslines, {point}")

    # Determine the minimum of the maximum values per segment.
    segment_min_max = collections.OrderedDict()
    for sid, sdictlist in segment_crosslines.items():
        max_vals = []
        for subid, sdict in enumerate(sdictlist):
            z_vals = []
            for crd in list(sdict["line"].coords):
                dist = abs(sdict["point"].distance(shapely.geometry.Point(crd[0:2])))
                if math.isnan(crd[2]) or crd[2] == NODATA or dist > maxdist:
                    continue
                z_vals.append(crd[2])
            if z_vals:
                max_val = max(z_vals)
            else:
                max_val = float("nan")
            segment_crosslines[sid][subid]["max"] = max_val
            max_vals.append(max_val)
        segment_min_max[sid] = min(max_vals)

    return segment_min_max, segment_crosslines


class RiskeerProfiles:
    nodata = NODATA

    def __init__(
        self,
        profiles_ahn,
        force_outside=True,
        profiles_3dl=None,
        group_id=None,
        cross_zd=None,
        logger=None,
        log_level=logging.INFO,
    ):

        self.profiles_ahn = profiles_ahn
        self.profiles_3dl = profiles_3dl
        self.force_outside = force_outside

        self.default_group_id = DEFAULT_GROUP_ID
        self.group_id = group_id

        self.default_cross_zd = DEFAULT_CROSS_ZD
        self.cross_zd = cross_zd

        if logger is None:
            # Create logger
            logger = logging.getLogger("CrossSections")
            logger.setLevel(log_level)
            if len(logger.handlers) == 0:
                ch = logging.StreamHandler()
                ch.setLevel(log_level)
                logger.addHandler(ch)
        self.logger = logger

    def create_riskeer_input(self, filename, add_kar_points=True):
        geom_ahn, props_ahn = self.process_shape(self.profiles_ahn, shapely.geometry.LineString)

        geom_3dl, props_3dl, geom_3dl_org = None, None, None
        if add_kar_points and self.profiles_3dl is not None:
            geom_3dl, props_3dl = self.process_shape(self.profiles_3dl, shapely.geometry.Point)
            if self.force_outside:
                geom_ahn = self.force_outside_left(geom_ahn, props_ahn, geom_3dl, props_3dl)
            geom_ahn, geom_3dl, geom_3dl_org = self.adapt_points(geom_ahn, props_ahn, geom_3dl, props_3dl)

        self.write_csv(filename, geom_ahn, props_ahn)
        if add_kar_points and self.profiles_3dl is not None:
            self.write_kar_csv(filename, geom_ahn, props_ahn, geom_3dl, props_3dl)

        if self.profiles_3dl is not None:
            lines_3dl = self.convert_3dl_to_line(geom_ahn, props_ahn, geom_3dl_org, props_3dl)
            self.write_csv(filename, lines_3dl, props_ahn, extra_id="solo")
            self.write_kar_csv(
                filename, lines_3dl, props_ahn, geom_3dl_org, props_3dl, ignore_ahn=True, extra_id="solo"
            )

    def process_shape(self, location, geom_type):
        geom, props = None, None
        with fiona.open(location, "r") as source:
            props = collections.OrderedDict()
            geom = collections.OrderedDict()

            for row in source:
                if row["geometry"] is None:
                    continue

                key = self.default_group_id
                if self.group_id is not None:
                    key = row["properties"][self.group_id]

                if key not in geom:
                    geom[key] = []
                geom[key].append(geom_type(row["geometry"]["coordinates"]))

                if key not in props:
                    props[key] = []
                props[key].append(row["properties"])

        return geom, props

    def force_outside_left(self, geom_ahn, props_ahn, geom_3dl, props_3dl):
        for key in geom_ahn:
            for i, (ahn, props) in enumerate(zip(geom_ahn[key], props_ahn[key])):
                # Get all 3DL points for this line
                _, opoints, opoint_props = self._get_3dl_points(props, geom_3dl[key], props_3dl[key])

                # Ignore these props, wrong order in 3dl limburg?
                points, point_props = [], []
                for point, pprop in zip(opoints, opoint_props):
                    if pprop[DEFAULT_CROSS_PNM] not in ["Maaiveld buitenwaarts", "Teen dijk buitenwaarts"]:
                        points.append(point)
                        point_props.append(pprop)

                # Can only say something about the orientation with at least two points
                if len(points) < 2:
                    continue

                startpoint = shapely.geometry.Point(ahn.coords[0][0:2])
                d1 = startpoint.distance(shapely.geometry.Point(points[0].coords[0][0:2]))
                d2 = startpoint.distance(shapely.geometry.Point(points[1].coords[0][0:2]))
                if d1 > d2:
                    self.logger.info(
                        f"flipping {props[self._get_zid()]} because {point_props[0][DEFAULT_CROSS_PNM]} is inside of {point_props[1][DEFAULT_CROSS_PNM]}"
                    )
                    geom_ahn[key][i] = shapely.geometry.LineString(ahn.coords[::-1])

        return geom_ahn

    def convert_3dl_to_line(self, geom_ahn, props_ahn, geom_3dl, props_3dl):
        lines = collections.OrderedDict()
        for key in geom_ahn:
            lines[key] = [None] * len(geom_ahn[key])
            for i, (ahn, props) in enumerate(zip(geom_ahn[key], props_ahn[key])):
                # Get all 3DL points for this line
                _, points, point_props = self._get_3dl_points(props, geom_3dl[key], props_3dl[key])
                if len(points) > 1:
                    lines[key][i] = shapely.geometry.LineString(points)

        return lines

    def _get_zid(self):
        zid = self.default_cross_zd
        if self.cross_zd is not None:
            zid = self.cross_zd

        return zid

    def _get_3dl_points(self, line_props, geom_3dl_subset, props_3dl_subset):
        zid = self._get_zid()
        acceptable_keys = list(CrossSectionHeights.lines_char.values())

        # Get all 3DL points for this line.
        filtered = collections.OrderedDict()
        for j, (p, prop) in enumerate(zip(geom_3dl_subset, props_3dl_subset)):
            if prop[DEFAULT_CROSS_PNM] in acceptable_keys and prop[zid] == line_props[zid]:
                filtered[prop[DEFAULT_CROSS_PNM]] = {"ind": j, "point": p, "prop": prop}

        # Put them in the right order.
        point_inds, points, props = [], [], []
        for k in CrossSectionHeights.lines_char.values():
            if k in filtered:
                point_inds.append(filtered[k]["ind"])
                points.append(filtered[k]["point"])
                props.append(filtered[k]["prop"])

        return point_inds, points, props

    def adapt_points(self, geom_ahn, props_ahn, geom_3dl, props_3dl):
        geom_ahn_org = {k: [shapely.geometry.LineString(l) for l in v] for k, v in geom_ahn.items()}
        geom_3dl_org = {k: [shapely.geometry.Point(p) for p in v] for k, v in geom_3dl.items()}

        for key in geom_ahn:
            for i, (ahn, props) in enumerate(zip(geom_ahn[key], props_ahn[key])):
                # Get all 3DL points for this line
                point_inds, points, point_props = self._get_3dl_points(props, geom_3dl[key], props_3dl[key])

                # Create new line without nodata.
                new_ahn = [c for c in ahn.coords if not math.isnan(c[2]) and c[2] != self.nodata]
                if not new_ahn:
                    continue

                ahn = shapely.geometry.LineString(new_ahn)
                geom_ahn[key][i] = ahn

                if len(ahn.coords) > 0:
                    # Snap points to line.
                    snapped_points = CrossSections.get_snapped_points(ahn, points)

                    # # Create a new linestring which includes the snapped points.
                    # coords = {}
                    # for c in ahn.coords:
                    #     point = shapely.geometry.Point(c)
                    #     dist = ahn.project(point, normalized=True)
                    #     coords[dist] = point
                    # for point in snapped_points:
                    #     dist = ahn.project(point, normalized=True)
                    #     coords[dist] = point
                    # coords = collections.OrderedDict(sorted(coords.items()))
                    # geom_ahn[key][i] = shapely.geometry.LineString(list(coords.values()))

                    startp = shapely.geometry.Point(geom_ahn_org[key][i].coords[0][0:2])
                    endp = shapely.geometry.Point(geom_ahn_org[key][i].coords[-1][0:2])
                    for j, org_point, point, pprop in zip(point_inds, points, snapped_points.geoms, point_props):
                        # Overwrite 3DL points with snapped 3DL points
                        geom_3dl[key][j] = point

                        # Adjust ahn profile to include characteristic points outside ahn profile.
                        # The ahn profile can be shorter due to nodata that has been filtered out.
                        point2d = shapely.geometry.Point(org_point.coords[0][0:2])
                        valid_ahn_start = shapely.geometry.Point(geom_ahn[key][i].coords[0][0:2])
                        valid_ahn_end = shapely.geometry.Point(geom_ahn[key][i].coords[-1][0:2])
                        startoutside = startp.distance(point2d) < startp.distance(valid_ahn_start)
                        stopoutside = endp.distance(point2d) < endp.distance(valid_ahn_end)
                        if startoutside or stopoutside:
                            self.logger.info(f"{props[self._get_zid()]}, adjusting for {pprop[DEFAULT_CROSS_PNM]}")
                        if startoutside:
                            coords = [org_point.coords[0]] + list(geom_ahn[key][i].coords)
                            geom_ahn[key][i] = shapely.geometry.LineString(coords)
                        elif stopoutside:
                            coords = list(geom_ahn[key][i].coords) + [org_point.coords[0]]
                            geom_ahn[key][i] = shapely.geometry.LineString(coords)

        return geom_ahn, geom_3dl, geom_3dl_org

    def write_csv(self, filename, geom_ahn, props_ahn, extra_id=""):
        zid = self._get_zid()

        lines = collections.OrderedDict()
        for key in geom_ahn:
            maxelements = []
            for g in geom_ahn[key]:
                if g is not None:
                    maxelements.append(len([c for c in g.coords if not math.isnan(c[2]) and c[2] != self.nodata]))
                else:
                    maxelements.append(0)
            maxelements = 3 * max(maxelements)

            lines[key] = ["LOCATIONID;X1;Y1;Z1;.....;Xn;Yn;Zn;(Profiel)" + ";".join([""] * (maxelements - 9))]
            for geom, props in zip(geom_ahn[key], props_ahn[key]):
                if geom is None:
                    continue

                numelements = 3 * len([c for c in geom.coords if not math.isnan(c[2]) and c[2] != self.nodata])
                line = [
                    f"{c[0]:.3f};{c[1]:.3f};{c[2]:.3f}"
                    for c in geom.coords
                    if not math.isnan(c[2]) and c[2] != self.nodata
                ]
                line = line + [""] * (maxelements - numelements)
                line = ";".join(line)
                lines[key].append(f"{props[zid]};{line}")

        for key in geom_ahn:
            if len(geom_ahn) == 1:
                fname = str(filename)
            else:
                fname = ".".join(str(filename).split(".")[:-1] + [f"_{key}" + filename.split(".")[-1]])

            if extra_id:
                fname = ".".join(fname.split(".")[:-1] + [extra_id] + [fname.split(".")[-1]])

            with open(fname, "w") as f:
                for line in lines[key]:
                    f.write(f"{line}\n")

    def write_kar_csv(self, filename, geom_ahn, props_ahn, geom_3dl, props_3dl, ignore_ahn=False, extra_id=""):
        zid = self._get_zid()

        lines = collections.OrderedDict()
        for key in geom_ahn:
            # Header
            lines[key] = [
                "LOCATIONID;" + ";".join([f"X_{k};Y_{k};Z_{k}" for k in CrossSectionHeights.lines_char.values()])
            ]

            for ahn, props in zip(geom_ahn[key], props_ahn[key]):
                # Default values. lines_char is already in the correct order.
                kar = collections.OrderedDict()
                for k in CrossSectionHeights.lines_char.values():
                    kar[k] = (-1, -1, -1)

                # Overwrite default values with found 3DL points.
                _, points, point_props = self._get_3dl_points(props, geom_3dl[key], props_3dl[key])
                for p, prop in zip(points, point_props):
                    if prop[DEFAULT_CROSS_PNM] in kar:
                        kar[prop[DEFAULT_CROSS_PNM]] = p.coords[0]
                    else:
                        raise KeyError(f"{prop[DEFAULT_CROSS_PNM]} does not exist...")

                if ahn is None:
                    if ignore_ahn:
                        continue
                    elif not points:
                        continue

                lines[key].append(
                    f"{props[zid]};" + ";".join([f"{k[0]:.3f};{k[1]:.3f};{k[2]:.3f}" for k in kar.values()])
                )

        for key in geom_ahn:
            if len(geom_ahn) == 1:
                fname = str(filename)
            else:
                fname = ".".join(str(filename).split(".")[:-1] + [f"_{key}" + filename.split(".")[-1]])

            extra = ["krp"]
            if extra_id:
                extra = [extra_id, "krp"]
            fname = ".".join(fname.split(".")[:-1] + extra + [fname.split(".")[-1]])

            with open(fname, "w") as f:
                for line in lines[key]:
                    f.write(f"{line}\n")
