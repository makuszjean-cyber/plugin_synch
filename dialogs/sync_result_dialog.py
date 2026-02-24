# -*- coding: utf-8 -*-
"""
Dialogue d'affichage du résultat de synchronisation.
Contenu défilable (scroll) pour garder le détail visible.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTextEdit, QPushButton, QFrame
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont


def show_sync_result(parent, title, first_line, messages_list):
    """
    Affiche le résultat de la synchronisation dans une fenêtre
    avec zone défilable (le texte ne disparaît pas).
    messages_list : liste de chaînes (lignes du log).
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setMinimumSize(480, 400)
    dlg.resize(560, 500)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(10)

    # Ligne de statut (fixe)
    header = QLabel(first_line)
    header.setWordWrap(True)
    header.setStyleSheet(
        "font-family: 'Poppins', sans-serif; font-size: 10pt; "
        "font-weight: bold; color: #1f2328; padding: 4px 0;"
    )
    layout.addWidget(header)

    # Zone défilable pour le détail
    log = QTextEdit()
    log.setReadOnly(True)
    log.setPlainText("\n".join(messages_list))
    log.setStyleSheet(
        "QTextEdit {"
        "  font-family: 'Consolas', 'Monaco', monospace;"
        "  font-size: 9pt;"
        "  padding: 8px;"
        "  border: 1px solid #d0d7de;"
        "  border-radius: 6px;"
        "  background: #f6f8fa;"
        "}"
    )
    log.setMinimumHeight(200)
    layout.addWidget(log, 1)

    # Bouton Fermer
    btn = QPushButton("Fermer")
    btn.setMinimumHeight(32)
    btn.clicked.connect(dlg.accept)
    layout.addWidget(btn)

    dlg.exec_()
