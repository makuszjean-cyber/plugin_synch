# -*- coding: utf-8 -*-
"""
Gestionnaire du mode hors ligne.
Télécharge les tables PostGIS sélectionnées vers un GeoPackage local,
en créant une copie de travail et une copie « baseline » pour la synchronisation.
"""

import os
from qgis.core import (
    QgsVectorLayer, QgsProject, QgsVectorFileWriter,
    QgsDataSourceUri, QgsCoordinateTransformContext,
    QgsLayerTreeGroup, QgsFeature, QgsFields,
    QgsWkbTypes
)
from qgis.PyQt.QtWidgets import QMessageBox

from ..core.config_manager import ConfigManager


class OfflineManager:
    """Gère le téléchargement PostGIS → GeoPackage et l'ajout au projet."""

    def __init__(self, iface):
        self.iface = iface
        self.config = ConfigManager()

    def download_tables(self, conn_params, schema, tables_info, gpkg_path,
                        progress_callback=None):
        """
        Télécharge les tables sélectionnées dans un GeoPackage.

        Pour chaque table, crée deux couches dans le GeoPackage :
          - <table>          : copie de travail (l'utilisateur édite celle-ci)
          - <table>_sketcher_baseline : copie de référence (pour détecter les changements)

        conn_params  : dict avec host, port, database, username, password, authcfg
        schema       : str
        tables_info  : list de dicts {"table", "geom_col", "pk_col"}
        gpkg_path    : str – chemin du fichier GeoPackage
        progress_callback : callable(int, int, str) – (current, total, message)

        Retourne (success: bool, messages: list[str])
        """
        messages = []
        total = len(tables_info)
        if total == 0:
            return False, ["Aucune table sélectionnée."]

        # Supprimer l'ancien fichier si existant
        if os.path.exists(gpkg_path):
            try:
                os.remove(gpkg_path)
            except OSError as e:
                return False, [f"Impossible de supprimer l'ancien GeoPackage : {e}"]

        for idx, info in enumerate(tables_info):
            table_name = info["table"]
            geom_col = info.get("geom_col", "")
            pk_col = info.get("pk_col", "id")

            if progress_callback:
                progress_callback(idx, total, f"Téléchargement de {table_name}…")

            # Construire l'URI PostGIS
            uri = QgsDataSourceUri()
            # Si authcfg est utilisé, ne pas passer le mot de passe
            if conn_params.get("authcfg"):
                uri.setConnection(
                    conn_params.get("host", "localhost"),
                    str(conn_params.get("port", "5432")),
                    conn_params.get("database", ""),
                    conn_params.get("username", ""),
                    ""
                )
                uri.setAuthConfigId(conn_params["authcfg"])
            else:
                uri.setConnection(
                    conn_params.get("host", "localhost"),
                    str(conn_params.get("port", "5432")),
                    conn_params.get("database", ""),
                    conn_params.get("username", ""),
                    conn_params.get("password", "")
                )
            uri.setDataSource(schema, table_name, geom_col if geom_col else None,
                              "", pk_col)

            # Charger la couche PostGIS temporairement
            pg_layer = QgsVectorLayer(uri.uri(False), table_name, "postgres")
            if not pg_layer.isValid():
                messages.append(f"[ERREUR] Impossible de charger la table « {table_name} » depuis PostGIS.")
                continue

            # Écrire la copie de travail
            ok1, msg1 = self._write_to_gpkg(pg_layer, gpkg_path, table_name)
            if not ok1:
                messages.append(f"[ERREUR] Erreur écriture {table_name} : {msg1}")
                continue

            # Écrire la copie baseline
            baseline_name = f"{table_name}_sketcher_baseline"
            ok2, msg2 = self._write_to_gpkg(pg_layer, gpkg_path, baseline_name)
            if not ok2:
                messages.append(f"[ERREUR] Erreur écriture baseline {table_name} : {msg2}")
                continue

            messages.append(f"[OK] {table_name} – {pg_layer.featureCount()} entité(s) téléchargée(s).")
            del pg_layer

        if progress_callback:
            progress_callback(total, total, "Terminé.")

        # Sauvegarder la config
        self.config.save(conn_params, schema, tables_info, gpkg_path)

        return True, messages

    def add_layers_to_project(self, gpkg_path, schema, tables_info):
        """
        Ajoute les couches GeoPackage (copies de travail) au projet QGIS,
        dans un groupe nommé comme le schéma.
        Ne charge PAS les tables _sketcher_baseline.
        """
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        # Créer ou trouver le groupe
        group = root.findGroup(schema)
        if group is None:
            group = root.addGroup(schema)

        added = []
        for info in tables_info:
            table_name = info["table"]
            geom_col = info.get("geom_col", "")

            # URI pour couche GeoPackage
            gpkg_uri = f"{gpkg_path}|layername={table_name}"

            layer = QgsVectorLayer(gpkg_uri, table_name, "ogr")
            if not layer.isValid():
                continue

            project.addMapLayer(layer, False)
            group.addLayer(layer)
            added.append(table_name)

        return added

    def add_online_layers(self, conn_params, schema, tables_info):
        """
        Ajoute les couches en mode en ligne (connexion directe PostGIS)
        dans un groupe nommé comme le schéma.
        """
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        group = root.findGroup(schema)
        if group is None:
            group = root.addGroup(schema)

        added = []
        for info in tables_info:
            table_name = info["table"]
            geom_col = info.get("geom_col", "")
            pk_col = info.get("pk_col", "id")

            uri = QgsDataSourceUri()
            uri.setConnection(
                conn_params.get("host", "localhost"),
                str(conn_params.get("port", "5432")),
                conn_params.get("database", ""),
                conn_params.get("username", ""),
                conn_params.get("password", ""),
            )
            if conn_params.get("authcfg"):
                uri.setAuthConfigId(conn_params["authcfg"])
            uri.setDataSource(schema, table_name, geom_col if geom_col else None,
                              "", pk_col)

            layer = QgsVectorLayer(uri.uri(False), table_name, "postgres")
            if not layer.isValid():
                continue

            project.addMapLayer(layer, False)
            group.addLayer(layer)
            added.append(table_name)

        return added

    # ──────────────────────────────────────────────
    # Utilitaires
    # ──────────────────────────────────────────────
    @staticmethod
    def _write_to_gpkg(source_layer, gpkg_path, layer_name):
        """Écrit une couche source dans un fichier GeoPackage."""
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        options.fileEncoding = "UTF-8"

        # Si le GeoPackage existe déjà, ajouter une couche
        if os.path.exists(gpkg_path):
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        else:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        ctx = QgsCoordinateTransformContext()
        error_code, error_msg = QgsVectorFileWriter.writeAsVectorFormatV2(
            source_layer,
            gpkg_path,
            ctx,
            options
        )

        if error_code != QgsVectorFileWriter.NoError:
            return False, error_msg or f"Code erreur: {error_code}"
        return True, ""
