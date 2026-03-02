# -*- coding: utf-8 -*-
"""
Gestionnaire de configuration persistante pour le plugin sketcher.
Stocke les paramètres de connexion, schéma, tables et chemin du GeoPackage
dans un fichier JSON dans le dossier .sketcher/ à côté du projet QGIS.
"""

import json
import os
import glob
import base64
from datetime import datetime


SKETCHER_DIR = ".sketcher"

# Clé "secrète" interne au plugin pour chiffrer légèrement
# certaines informations dans le fichier de configuration.
_SKETCHER_SECRET_KEY = "sketcher-2026-strong-key-for-host-db-schema"


def _xor_encrypt(value, key):
    """
    Chiffre une chaîne UTF-8 par XOR + base64 avec une clé fournie.
    Ce n'est PAS une sécurité forte, mais rend le JSON illisible en clair.
    """
    if not value:
        return ""
    data = value.encode("utf-8")
    k = key.encode("utf-8")
    xored = bytes(b ^ k[i % len(k)] for i, b in enumerate(data))
    return "enc:" + base64.b64encode(xored).decode("ascii")


def _xor_decrypt(value, key):
    """
    Déchiffre une valeur produite par _xor_encrypt.
    Si la valeur ne commence pas par 'enc:', elle est renvoyée telle quelle
    (compatibilité avec les anciens fichiers de configuration).
    """
    if not value or not isinstance(value, str):
        return value
    if not value.startswith("enc:"):
        return value
    try:
        data_b64 = value[4:]
        data = base64.b64decode(data_b64.encode("ascii"))
        k = key.encode("utf-8")
        plain = bytes(b ^ k[i % len(k)] for i, b in enumerate(data))
        return plain.decode("utf-8")
    except Exception:
        # En cas de problème, mieux vaut renvoyer la valeur brute
        return value


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

    def save(self, conn_params, schema, tables_info, gpkg_path,
             project_path=None, project_table=None,
             project_key=None, project_table_schema=None):
        """
        Sauvegarde la configuration.
        
        conn_params : dict  – paramètres de connexion PostgreSQL
        schema      : str   – nom du schéma
        tables_info : list  – [{"table": ..., "geom_col": ..., "pk_col": ...}, ...]
        gpkg_path   : str   – chemin absolu du GeoPackage
        project_path: str   – chemin du projet QGIS (.qgz) local
        project_table: str  – table distante stockant le projet QGIS
        project_key : str   – identifiant unique du projet (clé distante)
        project_table_schema : str – schéma de la table distante
        """
        self._gpkg_path = gpkg_path
        # Chiffrer légèrement host / database / schema dans le JSON
        enc_host = _xor_encrypt(conn_params.get("host", ""), _SKETCHER_SECRET_KEY)
        enc_database = _xor_encrypt(conn_params.get("database", ""), _SKETCHER_SECRET_KEY)
        enc_schema = _xor_encrypt(schema, _SKETCHER_SECRET_KEY)

        project_block = None
        if project_path:
            conn_name = conn_params.get("conn_name", "") or "default"
            project_name = os.path.splitext(
                os.path.basename(project_path)
            )[0]
            project_block = {
                "local_path": project_path,
                "table": project_table or "qgis_projects",
                "table_schema": project_table_schema or schema,
                "key": project_key or project_name or f"{conn_name}:{schema}",
                "last_checksum": None,
                "last_sync": None,
            }

        self._config = {
            "version": "1.0",
            "created": datetime.now().isoformat(),
            "connection": {
                "host": enc_host,
                "port": conn_params.get("port", "5432"),
                "database": enc_database,
                "username": conn_params.get("username", ""),
                # Ne PAS stocker le mot de passe en clair
                # on le récupérera depuis la connexion QGIS
                "conn_name": conn_params.get("conn_name", ""),
                "authcfg": conn_params.get("authcfg", ""),
            },
            "schema": enc_schema,
            "tables": tables_info,
            "gpkg_path": gpkg_path,
            "last_sync": datetime.now().isoformat(),
        }
        if project_block:
            self._config["project"] = project_block
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

            # Déchiffrer les champs éventuellement chiffrés
            conn = self._config.get("connection", {}) or {}
            conn["host"] = _xor_decrypt(conn.get("host", ""), _SKETCHER_SECRET_KEY)
            conn["database"] = _xor_decrypt(conn.get("database", ""), _SKETCHER_SECRET_KEY)
            self._config["connection"] = conn
            self._config["schema"] = _xor_decrypt(self._config.get("schema", ""), _SKETCHER_SECRET_KEY)

            project = self._config.get("project")
            if isinstance(project, dict):
                if not project.get("table"):
                    project["table"] = "qgis_projects"
                if not project.get("table_schema"):
                    project["table_schema"] = self._config.get("schema", "")
                if not project.get("key"):
                    local_path = project.get("local_path", "")
                    if local_path:
                        project["key"] = os.path.splitext(
                            os.path.basename(local_path)
                        )[0]
                    if not project.get("key"):
                        conn_name = conn.get("conn_name", "") or "default"
                        project["key"] = f"{conn_name}:{self._config.get('schema', '')}"
                self._config["project"] = project

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

    def update_project_sync_state(self, checksum, synced_at=None):
        """Met à jour l'état de synchronisation du projet QGIS."""
        if "project" not in self._config:
            return
        if synced_at is None:
            synced_at = datetime.now().isoformat()
        self._config["project"]["last_checksum"] = checksum
        self._config["project"]["last_sync"] = synced_at
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
                # Déchiffrer éventuellement le schéma pour l'affichage
                raw_schema = data.get("schema", "?")
                schema = _xor_decrypt(raw_schema, _SKETCHER_SECRET_KEY) or "?"
                conn = data.get("connection", {}).get("conn_name", "?")
                last_sync = data.get("last_sync", "jamais")
                display = f"{conn} / {schema}  (sync: {last_sync[:16]})"
                results.append((display, config_file, gpkg))
            except (json.JSONDecodeError, IOError):
                continue
        return results
