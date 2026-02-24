# -*- coding: utf-8 -*-
"""
PostGIS Sketcher – Plugin QGIS
Travaillez en ligne ou hors ligne avec vos couches PostGIS,
puis synchronisez les modifications avec la base de données.
"""


def classFactory(iface):
    """Chargement du plugin par QGIS."""
    from .sketcher_plugin import sketcher
    return sketcher(iface)
