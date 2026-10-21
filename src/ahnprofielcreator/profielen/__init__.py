# -*- coding: utf-8 -*-
"""
Created on Fri Jun 14 15:08:28 2019

@author: dupuits
"""


from . import cross_section


IntervalPoints = cross_section.IntervalPoints
CrossSections = cross_section.CrossSections
CrossSectionHeights = cross_section.CrossSectionHeights
minmax_per_segment = cross_section.minmax_per_segment
RiskeerProfiles = cross_section.RiskeerProfiles


def __getattr__(name):
    # schem_cross heeft het extra pakket 'rdp' nodig; alleen importeren als SchemProfile gebruikt wordt.
    if name == "SchemProfile":
        from . import schem_cross

        return schem_cross.SchemProfile
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
