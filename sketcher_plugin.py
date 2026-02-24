# -*- coding: utf-8 -*-
"""
PostGIS Sketcher – Classe principale du plugin QGIS.
Gère l'initialisation, les boutons de la barre d'outils et
orchestre les dialogues et la logique métier.

Synchronisation non-bloquante via QgsTask.
"""

import os
import logging
from qgis.PyQt.QtWidgets import (
    QAction, QMessageBox, QToolBar, QInputDialog
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsApplication

from .dialogs.main_dialog import MainDialog
from .dialogs.sync_review_dialog import SyncReviewDialog
from .dialogs.sync_result_dialog import show_sync_result
from .dialogs.history_dialog import HistoryDialog
from .dialogs.help_dialog import HelpDialog
from .core.offline_manager import OfflineManager
from .core.sync_manager import SyncManager
from .core.sync_task import SyncAnalyzeTask, SyncApplyTask
from .core.config_manager import ConfigManager
from .core.revision_manager import RevisionManager
from .core.font_loader import load_plugin_fonts

logger = logging.getLogger("sketcher.plugin")


class sketcher:
    """Plugin QGIS : PostGIS en ligne / hors ligne avec synchronisation."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.toolbar = None
        self.dlg = None
        # Références tâches async (empêche le GC)
        self._current_task = None
        self._current_config = None
        self._cfg = None
        self._sync_parent = None
        self._commit_message = ""
        self._all_changes = None
        self._conflict_strategies = None
        self._remote_actions = None

    # ══════════════════════════════════════════════
    # Initialisation / Fermeture
    # ══════════════════════════════════════════════

    def initGui(self):
        """Appelé au chargement du plugin par QGIS."""
        load_plugin_fonts(self.plugin_dir)

        self.toolbar = self.iface.addToolBar("sketcher")
        self.toolbar.setObjectName("sketcher")
        self.toolbar.setToolButtonStyle(Qt.ToolButtonIconOnly)

        icons_dir = os.path.join(os.path.abspath(self.plugin_dir), "icons")

        # Action principale : ouvrir le dialogue
        self.action_main = QAction(
            "PostGIS En ligne / Hors ligne",
            self.iface.mainWindow()
        )
        _icon_main = os.path.join(icons_dir, "refresh-svgrepo-com.svg")
        self.action_main.setIcon(QIcon(_icon_main))
        self.action_main.triggered.connect(self.run)
        self.toolbar.addAction(self.action_main)
        self.iface.addPluginToDatabaseMenu(
            "sketcher", self.action_main)
        self.actions.append(self.action_main)

        # Action synchronisation rapide
        self.action_sync = QAction(
            "Synchroniser",
            self.iface.mainWindow()
        )
        _icon_sync = os.path.join(icons_dir, "upload-cicle-svgrepo-com.svg")
        self.action_sync.setIcon(QIcon(_icon_sync))
        self.action_sync.triggered.connect(self.run_sync_from_config)
        self.toolbar.addAction(self.action_sync)
        self.iface.addPluginToDatabaseMenu(
            "sketcher", self.action_sync)
        self.actions.append(self.action_sync)

        # Action historique
        self.action_history = QAction(
            "Historique",
            self.iface.mainWindow()
        )
        _icon_history = os.path.join(icons_dir, "refresh-ccw-clock-svgrepo-com.svg")
        self.action_history.setIcon(QIcon(_icon_history))
        self.action_history.triggered.connect(self.run_history)
        self.toolbar.addAction(self.action_history)
        self.iface.addPluginToDatabaseMenu(
            "sketcher", self.action_history)
        self.actions.append(self.action_history)

        # Action aide
        self.action_help = QAction(
            "Aide",
            self.iface.mainWindow()
        )
        _icon_help = os.path.join(icons_dir, "status-warning-borderless-svgrepo-com.svg")
        self.action_help.setIcon(QIcon(_icon_help))
        self.action_help.triggered.connect(self.run_help)
        self.toolbar.addAction(self.action_help)
        self.iface.addPluginToDatabaseMenu(
            "sketcher", self.action_help)
        self.actions.append(self.action_help)

    def unload(self):
        """Appelé à la fermeture du plugin."""
        for action in self.actions:
            self.iface.removePluginDatabaseMenu(
                "sketcher", action)
        if self.toolbar:
            del self.toolbar
        self.actions.clear()

    # ══════════════════════════════════════════════
    # Action principale
    # ══════════════════════════════════════════════

    def run(self):
        """Ouvre le dialogue principal."""
        self.dlg = MainDialog(self.iface, self.iface.mainWindow())
        self.dlg.btn_load.clicked.connect(self._on_load)
        self.dlg.btn_sync.clicked.connect(self._on_sync)
        self.dlg.btn_history.clicked.connect(
            lambda: self._show_history(parent=self.dlg))
        # Mettre à jour le bloc d'état
        self._refresh_sync_status()
        self.dlg.exec_()

    # ══════════════════════════════════════════════
    # Chargement des couches
    # ══════════════════════════════════════════════

    def _on_load(self):
        """Déclenché par le bouton 'Charger les couches'."""
        tables = self.dlg.get_selected_tables()
        schema = self.dlg.get_schema()

        if not tables:
            QMessageBox.warning(
                self.dlg, "Aucune table",
                "Veuillez sélectionner au moins une table.")
            return

        if not schema:
            QMessageBox.warning(
                self.dlg, "Schéma manquant",
                "Veuillez saisir un nom de schéma.")
            return

        conn_params = self.dlg.get_conn_params()
        offline_mgr = OfflineManager(self.iface)

        if self.dlg.is_offline_mode():
            # ── Mode hors ligne ──
            gpkg_path = self.dlg.get_gpkg_path()
            if not gpkg_path:
                QMessageBox.warning(
                    self.dlg, "Erreur",
                    "Impossible de déterminer le chemin du "
                    "GeoPackage.\nVérifiez que le projet est "
                    "sauvegardé.")
                return

            self.dlg.progress.setVisible(True)
            self.dlg.progress.setRange(0, len(tables))

            def progress_cb(current, total, msg):
                self.dlg.progress.setValue(current)
                self.dlg.set_status(msg)

            success, messages = offline_mgr.download_tables(
                conn_params, schema, tables, gpkg_path,
                progress_cb)
            self.dlg.progress.setVisible(False)

            if success:
                added = offline_mgr.add_layers_to_project(
                    gpkg_path, schema, tables)
                self.dlg.set_status(
                    f"Mode hors ligne : {len(added)} couche(s) "
                    f"ajoutée(s) dans « {schema} ».\n"
                    + "\n".join(messages))
                QMessageBox.information(
                    self.dlg, "Téléchargement terminé",
                    f"{len(added)} couche(s) téléchargée(s) et "
                    f"ajoutée(s) au projet dans « {schema} ».\n\n"
                    "Vous pouvez maintenant travailler hors "
                    "ligne.\nUtilisez 'Synchroniser' pour "
                    "envoyer vos modifications.")
            else:
                self.dlg.set_status(
                    "Erreur : " + "\n".join(messages))
                QMessageBox.critical(
                    self.dlg, "Erreur",
                    "Erreur lors du téléchargement :\n"
                    + "\n".join(messages))
        else:
            # ── Mode en ligne ──
            added = offline_mgr.add_online_layers(
                conn_params, schema, tables)
            self.dlg.set_status(
                f"Mode en ligne : {len(added)} couche(s) "
                f"ajoutée(s) dans « {schema} ».")
            if added:
                QMessageBox.information(
                    self.dlg, "Couches ajoutées",
                    f"{len(added)} couche(s) PostGIS ajoutée(s) "
                    f"au projet dans « {schema} ».")

    # ══════════════════════════════════════════════
    # Synchronisation
    # ══════════════════════════════════════════════

    def _on_sync(self):
        """Lancé depuis le dialogue principal."""
        self._pick_and_sync(parent=self.dlg)

    def run_sync_from_config(self):
        """Synchronisation rapide depuis la barre d'outils."""
        self._pick_and_sync(parent=self.iface.mainWindow())

    def _pick_and_sync(self, parent=None):
        """
        Cherche les configs dans .sketcher/.
        1 config → sync directe.
        N configs → menu déroulant.
        0 config → message d'info.
        """
        if parent is None:
            parent = self.iface.mainWindow()

        configs = ConfigManager.find_configs_in_sketcher_dir()

        if not configs:
            QMessageBox.information(
                parent, "Aucune donnée hors ligne",
                "Aucun fichier de synchronisation trouvé "
                "dans .sketcher/.\n\n"
                "Téléchargez d'abord des couches en mode "
                "hors ligne.")
            return

        if len(configs) == 1:
            display, config_path, gpkg_path = configs[0]
            self._do_sync_async(gpkg_path, parent=parent)
        else:
            items = [c[0] for c in configs]
            chosen, ok = QInputDialog.getItem(
                parent, "Choisir la configuration",
                "Plusieurs jeux de données hors ligne "
                "détectés.\nSélectionnez celui à synchroniser :",
                items, 0, False)
            if ok and chosen:
                idx = items.index(chosen)
                display, config_path, gpkg_path = configs[idx]
                self._do_sync_async(gpkg_path, parent=parent)

    # ══════════════════════════════════════════════
    # Synchronisation asynchrone (QgsTask)
    # ══════════════════════════════════════════════

    def _do_sync_async(self, gpkg_path, parent=None):
        """Lance l'analyse en arrière-plan via QgsTask."""
        if parent is None:
            parent = self.iface.mainWindow()
        self._sync_parent = parent

        # Charger la config
        cfg = ConfigManager(gpkg_path)
        config = cfg.load(gpkg_path)
        if not config:
            QMessageBox.warning(
                parent, "Configuration introuvable",
                "Aucun fichier de configuration sketcher "
                "trouvé à côté du GeoPackage.")
            return

        # Récupérer le mot de passe
        conn_name = config.get("connection", {}).get(
            "conn_name", "")
        if conn_name:
            from qgis.core import QgsSettings
            s = QgsSettings()
            pwd = s.value(
                f"PostgreSQL/connections/{conn_name}/password", "")
            if pwd:
                config["connection"]["password"] = pwd

        self._current_config = config
        self._cfg = cfg

        # Afficher la progression dans la barre de messages
        self.iface.messageBar().pushMessage(
            "sketcher",
            "Analyse des changements en cours… "
            "(voir barre de progression ci-dessous)",
            level=0, duration=0)

        # Lancer la tâche d'analyse
        task = SyncAnalyzeTask(config)
        task.taskCompleted.connect(self._on_analyze_complete)
        task.taskTerminated.connect(self._on_analyze_failed)
        self._current_task = task
        QgsApplication.taskManager().addTask(task)
        logger.info("Tâche d'analyse lancée.")

    def _on_analyze_complete(self):
        """Appelé dans le thread principal à la fin de l'analyse."""
        self.iface.messageBar().clearWidgets()
        task = self._current_task
        if not task or not task.all_changes:
            return

        all_changes = task.all_changes
        parent = self._sync_parent

        # Vérifier s'il y a des changements ou des doublons
        has_changes = False
        for ch in all_changes.values():
            if (ch.get("inserts") or ch.get("updates")
                    or ch.get("deletes") or ch.get("conflicts")
                    or ch.get("remote_inserts")
                    or ch.get("remote_updates")
                    or ch.get("remote_deletes")
                    or ch.get("local_duplicate_groups")
                    or ch.get("remote_duplicate_groups")):
                has_changes = True
                break

        if not has_changes:
            QMessageBox.information(
                parent, "Aucune modification",
                "Aucune modification détectée "
                "(locale ou distante).\n"
                "Les données sont synchronisées.")
            return

        # Afficher le dialogue de revue
        review_dlg = SyncReviewDialog(all_changes, parent)
        if review_dlg.exec_():
            strategies = review_dlg.get_conflict_strategies()
            remote_actions = review_dlg.get_remote_actions()
            duplicate_deletions = review_dlg.get_duplicate_deletions()
            commit_message = review_dlg.get_commit_message()

            # Garder les références pour la révision
            self._commit_message = commit_message
            self._all_changes = all_changes
            self._conflict_strategies = strategies
            self._remote_actions = remote_actions

            # Lancer la tâche d'application
            self.iface.messageBar().pushMessage(
                "sketcher",
                "Synchronisation en cours…",
                level=0, duration=0)

            apply_task = SyncApplyTask(
                self._current_config, all_changes,
                strategies, remote_actions,
                duplicate_deletions=duplicate_deletions)
            apply_task.taskCompleted.connect(
                self._on_apply_complete)
            apply_task.taskTerminated.connect(
                self._on_apply_failed)
            self._current_task = apply_task
            QgsApplication.taskManager().addTask(apply_task)
            logger.info("Tâche de synchronisation lancée.")

    def _on_analyze_failed(self):
        """Appelé si l'analyse échoue."""
        self.iface.messageBar().clearWidgets()
        task = self._current_task
        error = task.error_msg if task else "Erreur inconnue"
        logger.error("Analyse échouée : %s", error)
        QMessageBox.critical(
            self._sync_parent, "Erreur d'analyse",
            f"Erreur lors de l'analyse des changements :\n\n"
            f"{error}")

    def _on_apply_complete(self):
        """Appelé dans le thread principal après la synchro."""
        self.iface.messageBar().clearWidgets()
        task = self._current_task
        if not task:
            return

        self._cfg.update_last_sync()

        # ── Enregistrer la révision ──
        gpkg_path = self._current_config.get("gpkg_path", "")
        rev_mgr = RevisionManager(gpkg_path)
        rev = rev_mgr.add_revision(
            commit_message=self._commit_message,
            all_changes=self._all_changes,
            conflict_strategies=self._conflict_strategies,
            remote_actions=self._remote_actions,
            messages=task.messages,
            success=task.success,
        )

        if task.success:
            has_errors = any("[ERREUR]" in m for m in task.messages)
            if has_errors:
                logger.warning(
                    "Synchronisation terminée avec des erreurs.")
            else:
                logger.info("Synchronisation réussie.")
            rev_num = rev.get("rev_number", "?")
            rev_msg = rev.get("message", "")
            title = "Synchronisation terminée"
            first_line = (
                "Synchronisation terminée avec des erreurs (voir le détail ci-dessous)."
                if has_errors
                else "Synchronisation réussie."
            )
            detail_lines = [
                f"Révision #{rev_num}",
                rev_msg or "(aucun message)",
                "",
            ] + list(task.messages)
            show_sync_result(
                self._sync_parent, title, first_line, detail_lines)
        else:
            logger.warning(
                "Synchronisation partielle : %s",
                "; ".join(task.messages))
            show_sync_result(
                self._sync_parent,
                "Synchronisation partielle",
                "Certaines opérations ont échoué. Détail ci-dessous (défilement possible).",
                task.messages)

        # Rafraîchir le statut dans le dialogue principal
        self._refresh_sync_status()

    def _on_apply_failed(self):
        """Appelé si la synchronisation échoue."""
        self.iface.messageBar().clearWidgets()
        task = self._current_task
        error = task.error_msg if task else "Erreur inconnue"
        msgs = task.messages if task else []
        logger.error("Synchronisation échouée : %s", error)

        # Enregistrer l'échec dans l'historique
        gpkg_path = (self._current_config or {}).get(
            "gpkg_path", "")
        if gpkg_path and self._all_changes is not None:
            try:
                rev_mgr = RevisionManager(gpkg_path)
                rev_mgr.add_revision(
                    commit_message=self._commit_message,
                    all_changes=self._all_changes,
                    conflict_strategies=self._conflict_strategies,
                    remote_actions=self._remote_actions,
                    messages=msgs,
                    success=False,
                )
            except Exception as exc:
                logger.warning(
                    "Impossible d'enregistrer l'échec dans "
                    "l'historique : %s", exc)

        QMessageBox.critical(
            self._sync_parent,
            "Erreur de synchronisation",
            f"Erreur fatale :\n{error}\n\n"
            + "\n".join(msgs))

    # ══════════════════════════════════════════════
    # Historique
    # ══════════════════════════════════════════════

    def run_history(self):
        """Ouvre le dialogue d'historique depuis la barre d'outils."""
        self._show_history(parent=self.iface.mainWindow())

    def _show_history(self, parent=None):
        """Affiche le dialogue d'historique des synchronisations."""
        if parent is None:
            parent = self.iface.mainWindow()

        configs = ConfigManager.find_configs_in_sketcher_dir()
        if not configs:
            QMessageBox.information(
                parent, "Aucun historique",
                "Aucune configuration de synchronisation "
                "trouvée.\nTéléchargez d'abord des couches en "
                "mode hors ligne.")
            return

        # Si plusieurs configs, demander laquelle
        if len(configs) == 1:
            gpkg_path = configs[0][2]
        else:
            items = [c[0] for c in configs]
            chosen, ok = QInputDialog.getItem(
                parent, "Choisir la configuration",
                "Sélectionnez le jeu de données dont "
                "afficher l'historique :",
                items, 0, False)
            if not ok or not chosen:
                return
            idx = items.index(chosen)
            gpkg_path = configs[idx][2]

        rev_mgr = RevisionManager(gpkg_path)
        dlg = HistoryDialog(rev_mgr, parent)
        dlg.exec_()

    # ══════════════════════════════════════════════
    # Aide
    # ══════════════════════════════════════════════

    def run_help(self):
        """Ouvre la fenêtre d'aide avec le guide utilisateur."""
        dlg = HelpDialog(self.iface.mainWindow())
        dlg.exec_()

    # ══════════════════════════════════════════════
    # Rafraîchissement du statut
    # ══════════════════════════════════════════════

    def _refresh_sync_status(self):
        """Met à jour le bloc d'état dans le dialogue principal."""
        if not self.dlg:
            return
        try:
            configs = ConfigManager.find_configs_in_sketcher_dir()
            if not configs:
                return
            # Utiliser la première config trouvée
            gpkg_path = configs[0][2]
            rev_mgr = RevisionManager(gpkg_path)
            stats = rev_mgr.get_stats()
            self.dlg.update_sync_status(stats)
        except Exception as e:
            logger.debug("Refresh status: %s", e)
