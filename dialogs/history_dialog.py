# -*- coding: utf-8 -*-
"""
Dialogue d'historique des synchronisations (façon « git log »).
Affiche la liste des révisions avec leur résumé, et permet
de consulter les détails d'une révision sélectionnée.

UX soignée : icônes, couleurs, disposition claire.
"""

import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTreeWidget,
    QTreeWidgetItem, QPushButton, QGroupBox, QTextEdit,
    QHeaderView, QAbstractItemView, QWidget, QSplitter,
    QFrame
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont, QBrush, QIcon

# Police Poppins (fallback : Segoe UI / sans-serif)
_SKETCHER_FONT_FAMILY = "Poppins"

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICONS_DIR = os.path.join(_PLUGIN_DIR, "icons")


def _icon(name):
    path = os.path.join(_ICONS_DIR, name)
    return QIcon(path) if os.path.isfile(path) else QIcon()


# ── Couleurs pour les badges ──
_CLR_INSERT = QColor(46, 160, 67)     # vert
_CLR_UPDATE = QColor(56, 132, 244)    # bleu
_CLR_DELETE = QColor(218, 54, 51)     # rouge
_CLR_CONFLICT = QColor(227, 179, 65)  # jaune
_CLR_PULL = QColor(130, 80, 220)      # violet
_CLR_SUCCESS = QColor(46, 160, 67)
_CLR_FAILED = QColor(218, 54, 51)
_CLR_ROW_ALT = QColor(248, 249, 251)
_CLR_ROW_SELECTED = QColor(230, 240, 255)


class HistoryDialog(QDialog):
    """Affiche l'historique des synchronisations."""

    def __init__(self, revision_manager, parent=None):
        super().__init__(parent)
        self.rev_mgr = revision_manager
        self.setWindowTitle("sketcher – Historique des synchronisations")
        _icon_path = os.path.join(_ICONS_DIR, "refresh-ccw-clock-svgrepo-com.svg")
        if os.path.isfile(_icon_path):
            self.setWindowIcon(QIcon(_icon_path))
        self.setMinimumSize(900, 600)        
        self.setFont(QFont(_SKETCHER_FONT_FAMILY, 9))        
        self._build_ui()
        self._load_revisions()

    # ══════════════════════════════════════════════
    # Construction de l'interface
    # ══════════════════════════════════════════════

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # ── En-tête ──
        header = self._make_header()
        main_layout.addWidget(header)

        # ── Statistiques globales ──
        self.stats_label = QLabel()
        self.stats_label.setWordWrap(True)
        self.stats_label.setStyleSheet(
            "QLabel {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  background: #f0f4f8;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 6px;"
            "  padding: 10px;"
            "  color: #1f2328;"
            "}"
        )
        main_layout.addWidget(self.stats_label)

        # ── Splitter : liste + détails ──
        splitter = QSplitter(Qt.Vertical)

        # Arbre des révisions
        self.tree = QTreeWidget()
        self.tree.setFont(QFont(_SKETCHER_FONT_FAMILY, 9))
        self.tree.setHeaderLabels([
            "#", "Date", "Message", "Auteur",
            "Push", "Pull", "Conflits", "Statut"
        ])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setSortingEnabled(True)
        self.tree.setMinimumHeight(200)

        # Ajuster les largeurs de colonnes
        header_view = self.tree.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(
            2, QHeaderView.Stretch)  # Message = stretch
        self.tree.setColumnWidth(0, 45)   # #
        self.tree.setColumnWidth(1, 150)  # Date
        self.tree.setColumnWidth(3, 90)   # Auteur
        self.tree.setColumnWidth(4, 80)   # Push
        self.tree.setColumnWidth(5, 80)   # Pull
        self.tree.setColumnWidth(6, 40)   # Conflits
        self.tree.setColumnWidth(7, 70)   # Statut

        self.tree.currentItemChanged.connect(self._on_item_selected)
        splitter.addWidget(self.tree)

        # Zone de détails
        details_frame = QFrame()
        details_frame.setFrameShape(QFrame.StyledPanel)
        details_layout = QVBoxLayout(details_frame)
        details_layout.setContentsMargins(8, 8, 8, 8)

        lbl_details = QLabel("Détails de la révision")
        ft = QFont(_SKETCHER_FONT_FAMILY)
        ft.setPointSize(9)
        lbl_details.setFont(ft)
        details_layout.addWidget(lbl_details)

        self.details_text = QTextEdit()
        self.details_text.setReadOnly(True)
        self.details_text.setMinimumHeight(120)
        self.details_text.setStyleSheet(
            "QTextEdit {"
            "  background: #fafbfc;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 4px;"
            "  padding: 6px;"
            "  font-family: 'Poppins', sans-serif;"
            "  font-size: 9pt; font-weight: normal;"
            "}"
        )
        details_layout.addWidget(self.details_text)
        splitter.addWidget(details_frame)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter, 1)

        # ── Boutons ──
        btn_layout = QHBoxLayout()

        self.btn_refresh = QPushButton("  Rafraîchir  ")
        self.btn_refresh.setFont(QFont(_SKETCHER_FONT_FAMILY, 9))
        self.btn_refresh.setMinimumHeight(32)
        self.btn_refresh.clicked.connect(self._load_revisions)
        btn_layout.addWidget(self.btn_refresh)

        btn_layout.addStretch()

        self.btn_close = QPushButton("  Fermer  ")
        self.btn_close.setFont(QFont(_SKETCHER_FONT_FAMILY, 9))
        self.btn_close.setMinimumHeight(32)
        self.btn_close.clicked.connect(self.accept)
        btn_layout.addWidget(self.btn_close)

        main_layout.addLayout(btn_layout)

    def _make_header(self):
        """Crée le bandeau d'en-tête."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 4)

        title = QLabel("Historique des synchronisations")
        ft = QFont(_SKETCHER_FONT_FAMILY)
        ft.setPointSize(14)
        title.setFont(ft)
        layout.addWidget(title)

        layout.addStretch()

        subtitle = QLabel(
            "Chaque synchronisation est enregistrée comme "
            "une révision.")
        subtitle.setStyleSheet("color: #656d76;")
        layout.addWidget(subtitle)

        return widget

    # ══════════════════════════════════════════════
    # Chargement des données
    # ══════════════════════════════════════════════

    def _load_revisions(self):
        """Charge et affiche les révisions dans l'arbre."""
        self.tree.clear()
        self.details_text.clear()

        revisions = self.rev_mgr.list_revisions(limit=100)

        for rev in revisions:
            item = QTreeWidgetItem()

            # #
            rev_num = rev.get("rev_number", "?")
            item.setText(0, str(rev_num))
            item.setTextAlignment(0, Qt.AlignCenter)

            # Date
            ts = rev.get("timestamp", "")
            if ts:
                try:
                    dt = ts[:16].replace("T", " ")
                except Exception:
                    dt = ts
            else:
                dt = "—"
            item.setText(1, dt)

            # Message
            msg = rev.get("message", "")
            item.setText(2, msg)
            ft = QFont(_SKETCHER_FONT_FAMILY)
            ft.setPointSize(9)
            item.setFont(2, ft)

            # Auteur
            item.setText(3, rev.get("user", ""))

            # Push
            summary = rev.get("summary", {})
            push = summary.get("pushed", {})
            push_total = (push.get("inserts", 0)
                          + push.get("updates", 0)
                          + push.get("deletes", 0))
            item.setText(4, str(push_total) if push_total else "—")
            if push_total:
                item.setIcon(4, _icon("cloud-sync-svgrepo-com.svg"))
                item.setForeground(4, QBrush(_CLR_INSERT))

            # Pull
            pull = summary.get("pulled", {})
            pull_total = (pull.get("inserts", 0)
                          + pull.get("updates", 0)
                          + pull.get("deletes", 0))
            item.setText(5, str(pull_total) if pull_total else "—")
            if pull_total:
                item.setForeground(5, QBrush(_CLR_PULL))

            # Conflits
            n_conf = summary.get("conflicts_resolved", 0)
            item.setText(6, str(n_conf) if n_conf else "—")
            if n_conf:
                item.setForeground(6, QBrush(_CLR_CONFLICT))

            # Statut (icônes du plugin, pas d’emoji)
            is_ok = rev.get("success", True)
            item.setText(7, "Réussi" if is_ok else "Échec")
            item.setTextAlignment(7, Qt.AlignCenter)
            if is_ok:
                item.setIcon(7, _icon("status-success-borderless-svgrepo-com.svg"))
                item.setForeground(7, QBrush(_CLR_SUCCESS))
            else:
                item.setIcon(7, _icon("status-failed-borderless-svgrepo-com.svg"))
                item.setForeground(7, QBrush(_CLR_FAILED))

            # Stocker la révision complète
            item.setData(0, Qt.UserRole, rev)

            self.tree.addTopLevelItem(item)

        # Statistiques globales
        stats = self.rev_mgr.get_stats()
        self._update_stats(stats)

        # Sélectionner la première
        if self.tree.topLevelItemCount() > 0:
            self.tree.setCurrentItem(
                self.tree.topLevelItem(0))

    def _update_stats(self, stats):
        """Met à jour le label de statistiques."""
        total = stats.get("total_syncs", 0)
        if total == 0:
            self.stats_label.setText(
                "Aucune synchronisation enregistrée.")
            return

        last_ts = stats.get("last_sync", "—")
        if last_ts and len(last_ts) > 16:
            last_ts = last_ts[:16].replace("T", " ")

        tp = stats.get("total_pushed", {})
        tl = stats.get("total_pulled", {})

        parts = [
            f"{total} synchronisation(s) au total",
            f"Dernière : {last_ts}",
        ]

        push_parts = []
        if tp.get("inserts"):
            push_parts.append(f"+{tp['inserts']} ins")
        if tp.get("updates"):
            push_parts.append(f"~{tp['updates']} upd")
        if tp.get("deletes"):
            push_parts.append(f"-{tp['deletes']} del")
        if push_parts:
            parts.append(
                f"Total poussé : {' | '.join(push_parts)}")

        pull_parts = []
        if tl.get("inserts"):
            pull_parts.append(f"+{tl['inserts']} ins")
        if tl.get("updates"):
            pull_parts.append(f"~{tl['updates']} upd")
        if tl.get("deletes"):
            pull_parts.append(f"-{tl['deletes']} del")
        if pull_parts:
            parts.append(
                f"Total importé : {' | '.join(pull_parts)}")

        self.stats_label.setText("  |  ".join(parts))

    # ══════════════════════════════════════════════
    # Détails d'une révision
    # ══════════════════════════════════════════════

    def _on_item_selected(self, current, previous):
        """Affiche les détails de la révision sélectionnée."""
        if not current:
            self.details_text.clear()
            return

        rev = current.data(0, Qt.UserRole)
        if not rev:
            return

        html = self._format_revision_details(rev)
        self.details_text.setHtml(html)

    @staticmethod
    def _format_revision_details(rev):
        """Formate une révision en HTML pour affichage."""
        lines = []

        # En-tête
        lines.append(
            f'<div style="margin-bottom:8px;">'
            f'<span style="font-size:12pt;font-weight:normal;">'
            f'Révision #{rev.get("rev_number", "?")}</span>  '
            f'<span style="color:#656d76;">'
            f'{rev.get("timestamp", "")[:19].replace("T", " ")}'
            f'</span>'
            f'</div>'
        )

        # Message
        msg = rev.get("message", "")
        lines.append(
            f'<div style="background:#f6f8fa;border:1px solid '
            f'#d0d7de;border-radius:4px;padding:8px;'
            f'margin-bottom:8px;font-weight:normal;">'
            f'{msg}</div>'
        )

        # Auteur
        user = rev.get("user", "")
        if user:
            lines.append(
                f'<div style="color:#656d76;margin-bottom:6px;">'
                f'Auteur : {user}</div>'
            )

        # Statut
        is_ok = rev.get("success", True)
        status_text = "Réussie" if is_ok else "Échouée"
        color = "#2ea043" if is_ok else "#da3633"
        lines.append(
            f'<div style="margin-bottom:8px;color:{color};">'
            f'{status_text}</div>'
        )

        # Résumé par table
        summary = rev.get("summary", {})
        tables = summary.get("tables", [])
        if tables:
            lines.append(
                '<div style="margin-top:6px;">'
                'Détail par table :</div>'
                '<table style="border-collapse:collapse;'
                'width:100%;margin-top:4px;">'
                '<tr style="background:#f0f4f8;">'
                '<th style="padding:4px 8px;text-align:left;'
                'border-bottom:1px solid #d0d7de;">Table</th>'
                '<th style="padding:4px 8px;text-align:center;'
                'border-bottom:1px solid #d0d7de;">Push</th>'
                '<th style="padding:4px 8px;text-align:center;'
                'border-bottom:1px solid #d0d7de;">Pull</th>'
                '<th style="padding:4px 8px;text-align:center;'
                'border-bottom:1px solid #d0d7de;">Conflits'
                '</th></tr>'
            )
            for t in tables:
                p = t.get("pushed", {})
                l = t.get("pulled", {})
                push_str = _format_counts(p)
                pull_str = _format_counts(l)
                conf = t.get("conflicts", 0)
                lines.append(
                    f'<tr>'
                    f'<td style="padding:3px 8px;'
                    f'border-bottom:1px solid #eee;">'
                    f'{t["table"]}</td>'
                    f'<td style="padding:3px 8px;text-align:'
                    f'center;border-bottom:1px solid #eee;'
                    f'color:#2ea043;">{push_str}</td>'
                    f'<td style="padding:3px 8px;text-align:'
                    f'center;border-bottom:1px solid #eee;'
                    f'color:#8250dc;">{pull_str}</td>'
                    f'<td style="padding:3px 8px;text-align:'
                    f'center;border-bottom:1px solid #eee;'
                    f'color:#e3b341;">'
                    f'{"—" if not conf else conf}</td>'
                    f'</tr>'
                )
            lines.append('</table>')

        # Messages d'exécution
        msgs = rev.get("messages", [])
        if msgs:
            lines.append(
                '<div style="margin-top:10px;">'
                'Journal :</div>'
                '<div style="background:#f6f8fa;'
                'border:1px solid #d0d7de;border-radius:4px;'
                'padding:6px;margin-top:4px;font-size:9pt;'
                'font-family:monospace;color:#1f2328;">'
            )
            for m in msgs:
                lines.append(f'{m}<br/>')
            lines.append('</div>')

        return "\n".join(lines)


def _format_counts(d):
    """Formate un dict {inserts, updates, deletes} en texte court."""
    parts = []
    if d.get("inserts"):
        parts.append(f"+{d['inserts']}")
    if d.get("updates"):
        parts.append(f"~{d['updates']}")
    if d.get("deletes"):
        parts.append(f"-{d['deletes']}")
    return " ".join(parts) if parts else "—"
