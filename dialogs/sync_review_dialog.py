# -*- coding: utf-8 -*-
"""
Dialogue de revue de synchronisation bidirectionnelle.
Affiche les changements locaux, distants et conflits détectés.
Permet à l'utilisateur de :
  - confirmer les changements locaux à pousser
  - choisir quels changements distants importer
  - résoudre les conflits (local / serveur / ignorer)
"""

import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget,
    QTableWidget, QTableWidgetItem, QPushButton, QGroupBox,
    QComboBox, QHeaderView, QMessageBox, QCheckBox, QWidget,
    QAbstractItemView, QScrollArea, QLineEdit, QFrame,
    QSizePolicy,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont, QIcon

# Police Poppins (fallback : Segoe UI / sans-serif)
_SKETCHER_FONT_FAMILY = "Poppins"

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "icons")


def _icon(name):
    path = os.path.join(_ICONS_DIR, name)
    return QIcon(path) if os.path.isfile(path) else QIcon()

def _sketcher_font(size=9, bold=False):
    ft = QFont(_SKETCHER_FONT_FAMILY)
    ft.setPointSize(size)
    ft.setBold(bold)
    return ft


def _format_value_for_display(val, max_len=100):
    """
    Formate une valeur pour l'affichage dans les tableaux de revue.
    Convertit QDate/QDateTime/QTime en chaîne lisible (évite "PyQt5.QtCore.QDate...").
    """
    if val is None:
        return ""
    if hasattr(val, "isNull") and val.isNull():
        return ""
    try:
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime
        if hasattr(val, "value"):
            val = val.value()
        if QDate and isinstance(val, QDate):
            return f"{val.year():04d}-{val.month():02d}-{val.day():02d}"
        if QDateTime and isinstance(val, QDateTime):
            d = val.date()
            t = val.time()
            return (f"{d.year():04d}-{d.month():02d}-{d.day():02d} "
                    f"{t.hour():02d}:{t.minute():02d}:{t.second():02d}")
        if QTime and isinstance(val, QTime):
            return f"{val.hour():02d}:{val.minute():02d}:{val.second():02d}"
    except ImportError:
        pass
    s = str(val)
    return s[:max_len] if len(s) > max_len else s


class SyncReviewDialog(QDialog):
    """Affiche un résumé des changements et demande confirmation."""

    STRATEGY_LOCAL = "local"
    STRATEGY_REMOTE = "remote"
    STRATEGY_SKIP = "skip"

    def __init__(self, changes_by_table, parent=None):
        super().__init__(parent)
        self.changes = changes_by_table
        self.conflict_strategies = {}
        self._remote_checkboxes = {}  # {(table, category, pk): QCheckBox}
        self._duplicate_checkboxes = {}  # table_name -> {"local": [(group, QCheckBox), ...], "remote": [...]}
        self.setWindowTitle("sketcher – Revue de synchronisation")
        _win_icon = _icon("cloud-sync-svgrepo-com.svg")
        if not _win_icon.isNull():
            self.setWindowIcon(_win_icon)
        self.setMinimumSize(820, 620)
        self.setFont(_sketcher_font())
        self._build_ui()

    # ──────────────────────────────────────────────
    # Construction de l'interface
    # ──────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Résumé global (bandeau coloré) — reste visible en haut
        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            "QFrame {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "    stop:0 #e8f4fd, stop:1 #f0f4f8);"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 6px;"
            "  padding: 8px;"
            "}"
        )
        sf_layout = QVBoxLayout(summary_frame)
        sf_layout.setContentsMargins(10, 6, 10, 6)
        summary_label = QLabel(self._compute_summary())
        summary_label.setWordWrap(True)
        summary_label.setFont(_sketcher_font(9, bold=False))
        sf_layout.addWidget(summary_label)
        layout.addWidget(summary_frame)

        # Contenu défilable (onglets, conflits, doublons, message)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(8)
        scroll_layout.setContentsMargins(0, 4, 0, 4)

        # Onglets par table
        self.tabs = QTabWidget()
        for table_name, changes in self.changes.items():
            tab = self._build_table_tab(table_name, changes)
            n_local = (len(changes.get("inserts", []))
                       + len(changes.get("updates", []))
                       + len(changes.get("deletes", [])))
            n_remote = (len(changes.get("remote_inserts", []))
                        + len(changes.get("remote_updates", []))
                        + len(changes.get("remote_deletes", [])))
            n_conf = len(changes.get("conflicts", []))
            label = f"{table_name} (Push {n_local} Pull {n_remote}"
            if n_conf:
                label += f" Conflits {n_conf}"
            label += ")"
            self.tabs.addTab(tab, label)
        scroll_layout.addWidget(self.tabs)

        # Stratégie globale de conflits
        if self._has_conflicts():
            conflict_box = QGroupBox("Résolution globale des conflits")
            conflict_box.setFont(_sketcher_font(9))
            cbl = QHBoxLayout(conflict_box)
            cbl.addWidget(QLabel("Stratégie :"))
            self.combo_strategy = QComboBox()
            self.combo_strategy.addItem(
                "Priorité locale (écraser la base)",
                self.STRATEGY_LOCAL)
            self.combo_strategy.addItem(
                "Priorité serveur (garder la base)",
                self.STRATEGY_REMOTE)
            self.combo_strategy.addItem(
                "Ignorer les conflits",
                self.STRATEGY_SKIP)
            cbl.addWidget(self.combo_strategy)
            btn_strat = QPushButton("Appliquer à tous")
            btn_strat.setFont(_sketcher_font(9))
            btn_strat.clicked.connect(self._apply_global_strategy)
            cbl.addWidget(btn_strat)
            scroll_layout.addWidget(conflict_box)

        # ── Doublons détectés (proposition de suppression) ──
        self._add_duplicates_section(scroll_layout)

        # ── Message de commit (façon Git) ──
        commit_group = QGroupBox("Message de synchronisation")
        commit_group.setStyleSheet(
            "QGroupBox {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 6px;"
            "  margin-top: 8px;"
            "  padding-top: 16px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin;"
            "  left: 10px;"
            "  padding: 0 4px;"
            "}"
        )
        commit_layout = QVBoxLayout(commit_group)
        self.edit_commit_message = QLineEdit()
        self.edit_commit_message.setPlaceholderText(
            "Ex : Synchronisation de la table... ")
        self.edit_commit_message.setMinimumHeight(30)
        self.edit_commit_message.setStyleSheet(
            "QLineEdit {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 4px;"
            "  padding: 4px 8px;"
            "}"
            "QLineEdit:focus {"
            "  border-color: #0969da;"
            "}"
        )
        commit_layout.addWidget(self.edit_commit_message)
        hint_label = QLabel(
            "Ce message sera enregistré dans l'historique "
            "des synchronisations. Laissez vide pour un "
            "message automatique.")
        hint_label.setStyleSheet(
            "font-family: 'Poppins'; font-size: 9pt; font-weight: normal; "
            "color: #656d76; font-style: italic;")
        hint_label.setWordWrap(True)
        commit_layout.addWidget(hint_label)
        scroll_layout.addWidget(commit_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

        # Boutons (toujours visibles en bas)
        btn_layout = QHBoxLayout()
        self.btn_confirm = QPushButton("Confirmer et synchroniser")
        self.btn_confirm.setIcon(_icon("cloud-sync-svgrepo-com.svg"))
        self.btn_confirm.setMinimumHeight(38)
        self.btn_confirm.setStyleSheet(
            "QPushButton {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  background-color: #2ea043;"
            "  color: white;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 6px 16px;"
            "}"
            "QPushButton:hover {"
            "  background-color: #238636;"
            "}"
            "QPushButton:pressed {"
            "  background-color: #1a7f37;"
            "}"
        )
        self.btn_confirm.clicked.connect(self._on_confirm)
        btn_layout.addWidget(self.btn_confirm)

        btn_cancel = QPushButton("Annuler")
        btn_cancel.setFont(_sketcher_font(9))
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    # ──────────────────────────────────────────────
    # Résumé
    # ──────────────────────────────────────────────

    def _compute_summary(self):
        t_ins = t_upd = t_del = t_conf = 0
        t_ri = t_ru = t_rd = 0
        for ch in self.changes.values():
            t_ins += len(ch.get("inserts", []))
            t_upd += len(ch.get("updates", []))
            t_del += len(ch.get("deletes", []))
            t_conf += len(ch.get("conflicts", []))
            t_ri += len(ch.get("remote_inserts", []))
            t_ru += len(ch.get("remote_updates", []))
            t_rd += len(ch.get("remote_deletes", []))
        parts = []
        if t_ins:
            parts.append(f"Push {t_ins} insertion(s) locale(s)")
        if t_upd:
            parts.append(f"Push {t_upd} mise(s) à jour locale(s)")
        if t_del:
            parts.append(f"Push {t_del} suppression(s) locale(s)")
        if t_ri:
            parts.append(f"↓ {t_ri} insertion(s) distante(s)")
        if t_ru:
            parts.append(f"Pull {t_ru} mise(s) à jour distante(s)")
        if t_rd:
            parts.append(f"Pull {t_rd} suppression(s) distante(s)")
        if t_conf:
            parts.append(f"{t_conf} conflit(s)")
        if not parts:
            return "Aucune modification détectée."
        return "Résumé : " + " | ".join(parts)

    def _has_conflicts(self):
        return any(ch.get("conflicts") for ch in self.changes.values())

    # ──────────────────────────────────────────────
    # Onglet par table
    # ──────────────────────────────────────────────

    def _build_table_tab(self, table_name, changes):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # ══════ CHANGEMENTS LOCAUX → SERVEUR ══════
        has_local = (changes.get("inserts")
                     or changes.get("updates")
                     or changes.get("deletes"))
        if has_local:
            lbl = QLabel("── Changements locaux → Serveur ──")
            lbl.setFont(_sketcher_font(9))
            layout.addWidget(lbl)

        inserts = changes.get("inserts", [])
        if inserts:
            layout.addWidget(
                QLabel(f"Insertions locales ({len(inserts)})"))
            layout.addWidget(self._make_change_table(
                inserts, QColor(200, 255, 200)))

        updates = changes.get("updates", [])
        if updates:
            layout.addWidget(
                QLabel(f"Mises \u00e0 jour locales ({len(updates)})"))
            layout.addWidget(self._make_change_table(
                updates, QColor(200, 220, 255)))

        deletes = changes.get("deletes", [])
        if deletes:
            layout.addWidget(
                QLabel(f"Suppressions locales ({len(deletes)})"))
            layout.addWidget(self._make_change_table(
                deletes, QColor(255, 200, 200)))

        # ══════ CHANGEMENTS DISTANTS → LOCAL ══════
        r_ins = changes.get("remote_inserts", [])
        r_upd = changes.get("remote_updates", [])
        r_del = changes.get("remote_deletes", [])
        has_remote = r_ins or r_upd or r_del

        if has_remote:
            lbl = QLabel("── Changements distants → Local ──")
            lbl.setFont(_sketcher_font(9))
            layout.addWidget(lbl)

            # Boutons globaux
            hl = QHBoxLayout()
            btn_all = QPushButton("Tout importer")
            btn_all.clicked.connect(
                lambda checked=False, tn=table_name:
                    self._toggle_remote_checks(tn, True))
            btn_none = QPushButton("Tout ignorer")
            btn_none.clicked.connect(
                lambda checked=False, tn=table_name:
                    self._toggle_remote_checks(tn, False))
            hl.addWidget(btn_all)
            hl.addWidget(btn_none)
            hl.addStretch()
            layout.addLayout(hl)

        if r_ins:
            layout.addWidget(QLabel(
                f"Nouvelles lignes distantes ({len(r_ins)})"))
            layout.addWidget(self._make_remote_table(
                table_name, "import_inserts", r_ins,
                QColor(220, 255, 220)))

        if r_upd:
            layout.addWidget(QLabel(
                f"Modifications distantes ({len(r_upd)})"))
            layout.addWidget(self._make_remote_table(
                table_name, "import_updates", r_upd,
                QColor(220, 235, 255)))

        if r_del:
            layout.addWidget(QLabel(
                f"Suppressions distantes ({len(r_del)})"))
            layout.addWidget(self._make_remote_table(
                table_name, "apply_deletes", r_del,
                QColor(255, 230, 220)))

        # ══════ CONFLITS ══════
        conflicts = changes.get("conflicts", [])
        if conflicts:
            lbl = QLabel(f"── Conflits ({len(conflicts)}) ──")
            lbl.setFont(_sketcher_font(9))
            layout.addWidget(lbl)
            layout.addWidget(
                self._make_conflict_table(table_name, conflicts))

        if not has_local and not has_remote and not conflicts:
            layout.addWidget(
                QLabel("Aucune modification pour cette table."))

        layout.addStretch()
        scroll.setWidget(widget)
        return scroll

    # ──────────────────────────────────────────────
    # Tables de changements locaux
    # ──────────────────────────────────────────────

    def _make_change_table(self, items, color=None):
        """Table affichant PK + premiers attributs."""
        if not items:
            return QLabel("(vide)")

        sample = items[0]
        attrs = sample.get("attributes") or sample.get("local") or {}
        data_cols = [k for k in list(attrs.keys())[:6]
                     if k not in _SKIP_DISPLAY_COLS]
        cols = ["PK"] + data_cols

        tbl = QTableWidget(len(items), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setMaximumHeight(min(180, 30 + 25 * len(items)))

        for r, item_data in enumerate(items):
            pk_val = item_data.get("pk", "")
            tbl.setItem(r, 0, QTableWidgetItem(
                str(pk_val) if pk_val else "NEW"))
            data = (item_data.get("attributes")
                    or item_data.get("local") or {})
            for c, col in enumerate(data_cols, 1):
                val = data.get(col, "")
                cell = QTableWidgetItem(_format_value_for_display(val))
                if color:
                    cell.setBackground(color)
                tbl.setItem(r, c, cell)
        return tbl

    # ──────────────────────────────────────────────
    # Tables de changements distants (avec checkboxes)
    # ──────────────────────────────────────────────

    def _make_remote_table(self, table_name, category, items,
                           color=None):
        """Table avec checkboxes pour les changements distants."""
        if not items:
            return QLabel("(vide)")

        sample = items[0]
        attrs = (sample.get("attributes")
                 or sample.get("remote") or {})
        data_cols = [k for k in list(attrs.keys())[:5]
                     if k not in _SKIP_DISPLAY_COLS]
        cols = ["Importer", "PK"] + data_cols

        tbl = QTableWidget(len(items), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setMaximumHeight(min(200, 30 + 25 * len(items)))

        for r, item_data in enumerate(items):
            pk_val = item_data.get("pk", "")

            # Checkbox – cochée par défaut
            cb = QCheckBox()
            cb.setChecked(True)
            key = (table_name, category, pk_val)
            self._remote_checkboxes[key] = cb
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.addWidget(cb)
            hl.setAlignment(Qt.AlignCenter)
            hl.setContentsMargins(0, 0, 0, 0)
            tbl.setCellWidget(r, 0, w)

            # PK
            tbl.setItem(r, 1, QTableWidgetItem(str(pk_val)))

            # Attributs
            data = (item_data.get("attributes")
                    or item_data.get("remote") or {})
            for c, col in enumerate(data_cols, 2):
                val = data.get(col, "")
                cell = QTableWidgetItem(_format_value_for_display(val))
                if color:
                    cell.setBackground(color)
                tbl.setItem(r, c, cell)
        return tbl

    def _toggle_remote_checks(self, table_name, checked):
        """Coche/décoche toutes les checkboxes d'une table."""
        for (tn, cat, pk), cb in self._remote_checkboxes.items():
            if tn == table_name:
                cb.setChecked(checked)

    # ──────────────────────────────────────────────
    # Table de conflits
    # ──────────────────────────────────────────────

    def _make_conflict_table(self, table_name, conflicts):
        """Table de conflits avec sélecteur de stratégie."""
        cols = ["PK", "Champs différents",
                "Valeur locale", "Valeur serveur", "Stratégie"]
        tbl = QTableWidget(len(conflicts), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setMaximumHeight(min(200, 30 + 25 * len(conflicts)))

        for r, conflict in enumerate(conflicts):
            pk_val = conflict.get("pk", "")
            tbl.setItem(r, 0, QTableWidgetItem(str(pk_val)))

            local = conflict.get("local", {})
            remote = conflict.get("remote", {})
            diff_fields = [
                k for k in set(list(local.keys())
                               + list(remote.keys()))
                if k not in _SKIP_DISPLAY_COLS
                and _format_value_for_display(local.get(k, "")) != _format_value_for_display(remote.get(k, ""))
            ]
            tbl.setItem(r, 1, QTableWidgetItem(
                ", ".join(diff_fields[:5])))
            tbl.setItem(r, 2, QTableWidgetItem(
                "; ".join(f"{k}={_format_value_for_display(local.get(k, ''))}"
                          for k in diff_fields[:3])))
            tbl.setItem(r, 3, QTableWidgetItem(
                "; ".join(f"{k}={_format_value_for_display(remote.get(k, ''))}"
                          for k in diff_fields[:3])))

            combo = QComboBox()
            combo.addItem("Local", self.STRATEGY_LOCAL)
            combo.addItem("Serveur", self.STRATEGY_REMOTE)
            combo.addItem("Ignorer", self.STRATEGY_SKIP)
            combo.currentIndexChanged.connect(
                lambda idx, t=table_name, pk=pk_val, cb=combo:
                    self._set_conflict_strategy(
                        t, pk, cb.currentData()))
            tbl.setCellWidget(r, 4, combo)

            for c in range(4):
                item = tbl.item(r, c)
                if item:
                    item.setBackground(QColor(255, 255, 180))

            # Stratégie par défaut
            self.conflict_strategies[
                (table_name, pk_val)] = self.STRATEGY_LOCAL

        return tbl

    # ──────────────────────────────────────────────
    # Doublons
    # ──────────────────────────────────────────────

    def _add_duplicates_section(self, layout):
        """Ajoute la section Doublons détectés si au moins une table en a."""
        has_any = False
        for ch in self.changes.values():
            if ch.get("local_duplicate_groups") or ch.get("remote_duplicate_groups"):
                has_any = True
                break
        if not has_any:
            return

        dup_box = QGroupBox("Doublons détectés")
        dup_box.setFont(_sketcher_font(9))
        dup_box.setStyleSheet(
            "QGroupBox { font-family: 'Poppins'; font-size: 9pt; "
            "border: 1px solid #d0d7de; border-radius: 6px; "
            "margin-top: 8px; padding-top: 14px; }"
            "QGroupBox::title { left: 10px; padding: 0 4px; }"
        )
        dup_layout = QVBoxLayout(dup_box)
        dup_layout.setContentsMargins(10, 4, 10, 8)

        hint = QLabel(
            "Lignes identiques (même contenu) détectées. "
            "Cochez pour supprimer les doublons en gardant une occurrence par groupe.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #656d76; font-size: 9pt;")
        dup_layout.addWidget(hint)

        for table_name, changes in self.changes.items():
            loc_groups = changes.get("local_duplicate_groups", [])
            rem_groups = changes.get("remote_duplicate_groups", [])
            if not loc_groups and not rem_groups:
                continue
            tbl_label = QLabel(f"Table <b>{table_name}</b>")
            tbl_label.setFont(_sketcher_font(9))
            dup_layout.addWidget(tbl_label)
            self._duplicate_checkboxes[table_name] = {"local": [], "remote": []}
            for i, group in enumerate(loc_groups):
                pks = [str(g.get("pk") or "NEW") for g in group]
                lbl = QLabel(f"  Local : {len(group)} lignes identiques (PK: {', '.join(pks[:5])}{'…' if len(pks) > 5 else ''})")
                lbl.setStyleSheet("font-size: 9pt;")
                cb = QCheckBox("Supprimer les doublons (garder 1)")
                cb.setChecked(True)
                cb.setFont(_sketcher_font(9))
                self._duplicate_checkboxes[table_name]["local"].append((group, cb))
                dup_layout.addWidget(lbl)
                dup_layout.addWidget(cb)
            for i, group in enumerate(rem_groups):
                pks = [str(g.get("pk")) for g in group]
                lbl = QLabel(f"  Serveur : {len(group)} lignes identiques (PK: {', '.join(pks[:5])}{'…' if len(pks) > 5 else ''})")
                lbl.setStyleSheet("font-size: 9pt;")
                cb = QCheckBox("Supprimer les doublons (garder 1)")
                cb.setChecked(True)
                cb.setFont(_sketcher_font(9))
                self._duplicate_checkboxes[table_name]["remote"].append((group, cb))
                dup_layout.addWidget(lbl)
                dup_layout.addWidget(cb)

        layout.addWidget(dup_box)

    def get_duplicate_deletions(self):
        """
        Retourne les suppressions de doublons choisies par l'utilisateur.
        Format : { table_name: {
            "local": [(pk ou None, attrs), ...],  # lignes à supprimer en local
            "remote": [pk1, pk2, ...],            # PK à supprimer sur le serveur
        }}
        Pour chaque groupe coché : on garde la première occurrence, on supprime les autres.
        """
        result = {}
        for table_name, kinds in self._duplicate_checkboxes.items():
            loc_to_del = []
            rem_to_del = []
            for group, cb in kinds["local"]:
                if not cb.isChecked():
                    continue
                for item in group[1:]:
                    loc_to_del.append((item.get("pk"), item.get("attrs", {})))
            for group, cb in kinds["remote"]:
                if not cb.isChecked():
                    continue
                for item in group[1:]:
                    rem_to_del.append(item.get("pk"))
            if loc_to_del or rem_to_del:
                result[table_name] = {"local": loc_to_del, "remote": rem_to_del}
        return result

    # ──────────────────────────────────────────────
    # Stratégies
    # ──────────────────────────────────────────────

    def _set_conflict_strategy(self, table, pk, strategy):
        self.conflict_strategies[(table, pk)] = strategy

    def _apply_global_strategy(self):
        strategy = self.combo_strategy.currentData()
        for key in self.conflict_strategies:
            self.conflict_strategies[key] = strategy
        QMessageBox.information(
            self, "Stratégie appliquée",
            f"Tous les conflits → stratégie : {strategy}")

    # ──────────────────────────────────────────────
    # Confirmation
    # ──────────────────────────────────────────────

    def _on_confirm(self):
        self.accept()

    def get_commit_message(self):
        """Retourne le message de commit saisi par l'utilisateur."""
        return self.edit_commit_message.text().strip()

    def get_conflict_strategies(self):
        """Retourne {(table, pk): strategy}."""
        return dict(self.conflict_strategies)

    def get_remote_actions(self):
        """
        Retourne les actions pour les modifications distantes.
        Format : { table_name: {
            "import_inserts": [pk1, ...],
            "import_updates": [pk1, ...],
            "apply_deletes": [pk1, ...],
        }}
        """
        actions = {}
        for (table_name, category, pk_val), cb \
                in self._remote_checkboxes.items():
            if not cb.isChecked():
                continue
            if table_name not in actions:
                actions[table_name] = {
                    "import_inserts": [],
                    "import_updates": [],
                    "apply_deletes": [],
                }
            actions[table_name][category].append(pk_val)
        return actions


# Colonnes à ne pas afficher dans les tables de revue
_SKIP_DISPLAY_COLS = {"__sketcher_geom__", "fid"}
