# -*- coding: utf-8 -*-
"""
Charge les polices Poppins depuis le dossier fonts/ du plugin.
Permet d'utiliser Poppins (Regular, Light, Italic, Medium) de façon
cohérente même si la police n'est pas installée sur le système.
"""

import os
import logging
from qgis.PyQt.QtGui import QFontDatabase

logger = logging.getLogger("sketcher.fonts")

# Nom de la famille une fois chargée
SKETCHER_FONT_FAMILY = "Poppins"

# Fichiers TTF à charger (nom du fichier dans fonts/)
FONT_FILES = [
    "Poppins-Regular.ttf",
    "Poppins-Light.ttf",
    "Poppins-Italic.ttf",
    "Poppins-Medium.ttf",
]


def load_plugin_fonts(plugin_dir):
    """
    Charge les polices du dossier fonts/ du plugin.
    À appeler au démarrage du plugin (initGui).

    plugin_dir : str – chemin du répertoire du plugin (os.path.dirname(__file__) du plugin)

    Retourne le nombre de polices chargées avec succès.
    """
    fonts_dir = os.path.join(plugin_dir, "fonts")
    if not os.path.isdir(fonts_dir):
        logger.warning("Dossier fonts/ introuvable : %s", fonts_dir)
        return 0

    loaded = 0
    for name in FONT_FILES:
        path = os.path.join(fonts_dir, name)
        if not os.path.isfile(path):
            logger.debug("Fichier police absent : %s", path)
            continue
        try:
            fid = QFontDatabase.addApplicationFont(path)
            if fid != -1:
                loaded += 1
                logger.debug("Police chargée : %s (id=%s)", name, fid)
            else:
                logger.warning("Échec chargement : %s", name)
        except Exception as e:
            logger.warning("Erreur chargement %s : %s", name, e)

    if loaded:
        logger.info("Poppins : %d variante(s) chargée(s) depuis %s", loaded, fonts_dir)
    return loaded
