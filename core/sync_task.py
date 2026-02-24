# -*- coding: utf-8 -*-
"""
Tâches asynchrones pour la synchronisation sketcher.
Utilise QgsTask pour exécuter l'analyse et l'application
des changements sans bloquer l'interface QGIS.
"""

import logging
from qgis.core import QgsTask

from .sync_manager import SyncManager

logger = logging.getLogger("sketcher.task")


class SyncAnalyzeTask(QgsTask):
    """
    Tâche de fond : analyse les différences entre
    local, baseline et remote pour toutes les tables.

    Résultats accessibles après exécution :
      - all_changes : dict des changements détectés
      - error_msg   : message d'erreur (si échec)
    """

    def __init__(self, config,
                 description="sketcher – Analyse des changements"):
        super().__init__(description, QgsTask.CanCancel)
        self.config = config
        self.sync_mgr = SyncManager()
        self.all_changes = None
        self.error_msg = None

    def run(self):
        """Exécuté dans un thread séparé."""
        try:
            def progress_cb(current, total, msg):
                if self.isCanceled():
                    return
                pct = int(current / total * 100) if total > 0 else 0
                self.setProgress(pct)

            self.all_changes = self.sync_mgr.analyze_changes(
                self.config, progress_callback=progress_cb
            )
            return not self.isCanceled()
        except Exception as e:
            self.error_msg = str(e)
            logger.error("Erreur analyse : %s", e, exc_info=True)
            return False

    def finished(self, result):
        """Appelé dans le thread principal – géré par le plugin."""
        pass


class SyncApplyTask(QgsTask):
    """
    Tâche de fond : applique les changements locaux vers PostGIS
    et importe les modifications distantes dans le GeoPackage.

    Résultats accessibles après exécution :
      - success   : bool
      - messages  : list[str]
      - error_msg : message d'erreur fatale (si échec)
    """

    def __init__(self, config, all_changes, conflict_strategies,
                 remote_actions, duplicate_deletions=None,
                 description="sketcher – Synchronisation"):
        super().__init__(description, QgsTask.CanCancel)
        self.config = config
        self.all_changes = all_changes
        self.conflict_strategies = conflict_strategies
        self.remote_actions = remote_actions
        self.duplicate_deletions = duplicate_deletions or {}
        self.sync_mgr = SyncManager()
        self.success = False
        self.messages = []
        self.error_msg = None

    def run(self):
        """Exécuté dans un thread séparé."""
        try:
            def progress_cb(current, total, msg):
                if self.isCanceled():
                    return
                pct = int(current / total * 100) if total > 0 else 0
                self.setProgress(pct)

            self.success, self.messages = self.sync_mgr.apply_changes(
                self.config,
                self.all_changes,
                self.conflict_strategies,
                self.remote_actions,
                progress_callback=progress_cb,
                duplicate_deletions=self.duplicate_deletions,
            )
            return self.success
        except Exception as e:
            self.error_msg = str(e)
            self.messages = [f"[ERREUR] Erreur fatale : {e}"]
            logger.error("Erreur application : %s", e, exc_info=True)
            return False

    def finished(self, result):
        """Appelé dans le thread principal – géré par le plugin."""
        pass
