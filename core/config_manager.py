# -*- coding: utf-8 -*-
"""
Gestionnaire de configuration persistante pour le plugin sketcher.
Stocke les paramètres de connexion, schéma, tables et chemin du GeoPackage
dans un fichier JSON dans le dossier .sketcher/ à côté du projet QGIS.
"""

import json
import os
import glob
from datetime import datetime


SKETCHER_DIR = ".sketcher"


class ConfigManager:
    """Lit et écrit la configuration de synchronisation."""

    def __init__(self, gpkg_path=None):
        self._gpkg_path = gpkg_path
        self._config = {}

    @property
    def config_path(self):
        """Chemin du fichier config JSON, à côté du GeoPackage dans .sketcher/."""
        if not self._gpkg_path:
            return None
        directory = os.path.dirname(self._gpkg_path)
        gpkg_name = os.path.splitext(os.path.basename(self._gpkg_path))[0]
        return os.path.join(directory, f"{gpkg_name}_config.json")

    def set_gpkg_path(self, gpkg_path):
        self._gpkg_path = gpkg_path

    def save(self, conn_params, schema, tables_info, gpkg_path):
        """
        Sauvegarde la configuration.
        
        conn_params : dict  – paramètres de connexion PostgreSQL
        schema      : str   – nom du schéma
        tables_info : list  – [{"table": ..., "geom_col": ..., "pk_col": ...}, ...]
        gpkg_path   : str   – chemin absolu du GeoPackage
        """
        self._gpkg_path = gpkg_path
        self._config = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "connection": {
                "host": conn_params.get("host", ""),
                "port": conn_params.get("port", "5432"),
                "database": conn_params.get("database", ""),
                "username": conn_params.get("username", ""),
                # Ne PAS stocker le mot de passe en clair
                # on le récupérera depuis la connexion QGIS
                "conn_name": conn_params.get("conn_name", ""),
                "authcfg": conn_params.get("authcfg", ""),
            },
            "schema": schema,
            "tables": tables_info,
            "gpkg_path": gpkg_path,
            "last_sync": datetime.now().isoformat(),
        }
        config_path = self.config_path
        if config_path:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
        return self._config

    def load(self, gpkg_path=None):
        """
        Charge la configuration depuis le fichier JSON.
        Retourne le dict ou None si pas trouvé.
        """
        if gpkg_path:
            self._gpkg_path = gpkg_path
        config_path = self.config_path
        if not config_path or not os.path.exists(config_path):
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
            return self._config
        except (json.JSONDecodeError, IOError):
            return None

    def get(self, key, default=None):
        return self._config.get(key, default)

    def update_last_sync(self):
        """Met à jour le timestamp de dernière synchronisation."""
        self._config["last_sync"] = datetime.now().isoformat()
        config_path = self.config_path
        if config_path:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)

    @staticmethod
    def get_sketcher_dir():
        """
        Retourne le chemin du dossier .sketcher/ à côté du projet QGIS.
        Crée le dossier s'il n'existe pas.
        """
        from qgis.core import QgsProject
        project_path = QgsProject.instance().absolutePath()
        if not project_path:
            # Pas de projet sauvegardé → utiliser le répertoire utilisateur
            project_path = os.path.expanduser("~")
        sketcher_dir = os.path.join(project_path, SKETCHER_DIR)
        os.makedirs(sketcher_dir, exist_ok=True)
        return sketcher_dir

    @staticmethod
    def auto_gpkg_path(conn_name, schema):
        """
        Génère automatiquement le chemin du GeoPackage dans .sketcher/.
        Format : .sketcher/<conn>_<schema>.gpkg
        """
        sketcher_dir = ConfigManager.get_sketcher_dir()
        safe_name = f"{conn_name}_{schema}".replace(" ", "_")
        return os.path.join(sketcher_dir, f"{safe_name}.gpkg")

    @staticmethod
    def find_configs_in_sketcher_dir():
        """
        Cherche tous les fichiers *_config.json dans le dossier .sketcher/.
        Retourne une liste de tuples (display_name, config_path, gpkg_path).
        """
        from qgis.core import QgsProject
        project_path = QgsProject.instance().absolutePath()
        if not project_path:
            project_path = os.path.expanduser("~")
        sketcher_dir = os.path.join(project_path, SKETCHER_DIR)
        if not os.path.isdir(sketcher_dir):
            return []

        results = []
        for config_file in glob.glob(os.path.join(sketcher_dir, "*_config.json")):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                gpkg = data.get("gpkg_path", "")
                schema = data.get("schema", "?")
                conn = data.get("connection", {}).get("conn_name", "?")
                last_sync = data.get("last_sync", "jamais")
                display = f"{conn} / {schema}  (sync: {last_sync[:16]})"
                results.append((display, config_file, gpkg))
            except (json.JSONDecodeError, IOError):
                continue
        return results
