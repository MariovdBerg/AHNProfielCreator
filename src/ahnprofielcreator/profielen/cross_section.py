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
import rasterio.transform
import rasterio.windows
import shapely.geometry
import shapely.ops
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

    def __init__(
        self,
        crosssections_file,
        group_id=None,
        ahn_tiles=None,
        ahn_stepsize=0.5,
        logger=None,
        log_level=logging.INFO,
    ):

        # User-supplied arguments.
        self.crosssections_file = crosssections_file
        self.default_group_id = DEFAULT_GROUP_ID
        self.group_id = group_id

        # AHN (ahnprofielcreator.ahn.AHNTiles: kaartbladen en lokale/gedownloade tiffs)
        self.ahn_tiles = ahn_tiles
        self.ahn_stepsize = ahn_stepsize

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

    def write_crosssections_output(self, output_file, crosssections_height):
        self.logger.debug(f"Writing cross-sections with heights to '{output_file}'...")

        if self.cross_lines is None:
            self.process_crosssections()

        key = self.default_group_id
        if self.group_id is not None:
            key = self.group_id

        # Extend existing schema.
        schema = self.cross_schema.copy()
        schema["properties"][key] = "str"
        schema["geometry"] = "3D LineString"

        with fiona.open(output_file, "w", "ESRI Shapefile", schema, crs=self.cross_crs) as source:
            for subkey, subcross in crosssections_height.items():
                for j, line in enumerate(subcross):
                    props = dict(self.cross_input_props[subkey][j])  # Gewone dict: fiona-attributen worden onveranderlijk
                    props[key] = subkey
                    source.write({"geometry": shapely.geometry.mapping(line), "properties": props})

        self.logger.debug("Done.")

    def sample_points(self, line):
        """x/y of the points along `line` at a fixed stepsize (including the start point), as an (n, 2) array."""
        steps = np.arange(math.ceil(round(line.length, 2) / self.ahn_stepsize) + 1)
        return shapely.get_coordinates(shapely.line_interpolate_point(line, steps * self.ahn_stepsize))

    @staticmethod
    def find_raster_heights(dataset, heights, xy, nodata, window):
        """Heights at the points `xy` from `heights`, the part of the raster read with `window`; nodata elsewhere."""
        z = np.full(len(xy), float(nodata))
        if len(xy) == 0:
            return z
        # Same as dataset.index() per point, but for all points at once.
        rows, cols = rasterio.transform.rowcol(dataset.transform, xy[:, 0], xy[:, 1])
        rows = np.asarray(rows, dtype=np.int64) - int(window.row_off)
        cols = np.asarray(cols, dtype=np.int64) - int(window.col_off)
        maxrow, maxcol = heights.shape
        inside = (rows >= 0) & (rows < maxrow) & (cols >= 0) & (cols < maxcol)
        values = heights[rows[inside], cols[inside]].astype(float)
        valid = ~np.isnan(values)
        if dataset.nodata is not None:
            valid &= values != dataset.nodata
        z[np.flatnonzero(inside)[valid]] = values[valid]
        return z

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

    def crosssections_height_ahn(self):
        ahn_map = collections.OrderedDict()
        all_lines = []
        for ckey, lines in self.cross_lines.items():
            all_lines += lines

            # Keep track which ckeys correspond to which ahn sheet
            for n_ahn in self.ahn_tiles.sheet_names(lines):
                ahn_map.setdefault(n_ahn, []).append(ckey)

        ahn_names = ", ".join(ahn_map.keys())
        self.logger.info(f"Finding heights for {len(all_lines)} line(s) in {len(ahn_map)} AHN raster file(s): {ahn_names}...")
        if all_lines:
            self.ahn_tiles.report_ahn6(all_lines)

        # Find local AHN files, download the missing ones.
        ahn_files = {n_ahn: self.ahn_tiles.file(n_ahn) for n_ahn in ahn_map}

        # Points along each line with the user-supplied stepsize; without a height (yet) z = nodata.
        cross_xy = {ckey: [self.sample_points(line) for line in lines] for ckey, lines in self.cross_lines.items()}
        cross_z = {ckey: [np.full(len(xy), float(self.nodata)) for xy in xys] for ckey, xys in cross_xy.items()}

        for ahn_index, ckeys in tqdm.tqdm(ahn_map.items(), desc="Finding heights in AHN raster files"):
            path = ahn_files[ahn_index]
            if path is None:
                # AHNTiles has already warned about this sheet.
                self.logger.debug(f"Could not find a valid file for AHN index {ahn_index}")
                continue

            # All points of these lines without a height so far (a point can be on more than one sheet).
            todo = [(ckey, i, np.flatnonzero(z == self.nodata)) for ckey in ckeys for i, z in enumerate(cross_z[ckey])]
            xy = np.concatenate([cross_xy[ckey][i][ind] for ckey, i, ind in todo])
            with rasterio.open(path) as dataset:
                bounds = dataset.bounds
                sheet_lines = [line for ckey in ckeys for line in self.cross_lines[ckey]]
                ahn_raster, window = self.read_raster_window(dataset, sheet_lines)
                z = self.find_raster_heights(dataset, ahn_raster, xy, self.nodata, window)

            start = 0
            for ckey, i, ind in todo:
                cross_z[ckey][i][ind] = z[start : start + len(ind)]
                start += len(ind)

            # Points within this raster, and those without a height
            on_sheet = (
                (bounds.left <= xy[:, 0]) & (xy[:, 0] < bounds.right) & (bounds.bottom < xy[:, 1]) & (xy[:, 1] <= bounds.top)
            )
            self.warn_empty_sheet(ahn_index, path, int(on_sheet.sum()), int((z[on_sheet] == self.nodata).sum()))

        # Save the points as a LineString per cross-section, in the order of the input. Points without
        # height are kept (z = nodata), so the points stay at a fixed stepsize along the line.
        return collections.OrderedDict(
            (
                ckey,
                [shapely.linestrings(np.column_stack([xy, z])) for xy, z in zip(cross_xy[ckey], cross_z[ckey])],
            )
            for ckey in self.cross_lines
        )

    def crosssections_height(self):
        self.logger.debug("Creating cross-sections with heights...")

        self.process_crosssections()
        if self.ahn_tiles is None:
            raise ValueError("AHN tiles are not supplied (ahn_tiles)")
        return self.crosssections_height_ahn()

    def crosssections_height_to_file(self, output_crosssections_height_file):
        # Determine cross-sections with heights.
        crosssections_height = self.crosssections_height()

        # Write cross-sections with height to a shapefile.
        self.write_crosssections_output(output_crosssections_height_file, crosssections_height)

        return crosssections_height
