# -*- coding: utf-8 -*-
"""
Dialogue principal du plugin sketcher.
Sélection connexion PostGIS / schéma / tables, choix en ligne vs hors ligne.
"""

import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QListWidget, QListWidgetItem, QRadioButton,
    QButtonGroup, QGroupBox, QMessageBox, QProgressBar,
    QAbstractItemView, QWidget, QFrame
)
from qgis.PyQt.QtCore import Qt, QSettings
from qgis.PyQt.QtGui import QFont, QIcon
from qgis.core import (
    QgsSettings, QgsDataSourceUri, QgsProviderRegistry,
    QgsVectorLayer, QgsProject
)

# ── Police Poppins (fallback : Segoe UI / sans-serif) ──
_SKETCHER_FONT_FAMILY = "Poppins"

# Dossier des icônes (racine du plugin)
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


class MainDialog(QDialog):
    """Dialogue principal : connexion, schéma, tables, mode en ligne/hors ligne."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle("sketcher – PostGIS En ligne / Hors ligne")
        _win_icon = _icon("database-download-svgrepo-com.svg")
        if not _win_icon.isNull():
            self.setWindowIcon(_win_icon)
        self.setMinimumSize(560, 680)
        self.setFont(_sketcher_font())
        self._conn_params = {}
        self._build_ui()
        self._connect_signals()
        self._load_connections()

    # ──────────────────────────────────────────────
    # Construction de l'interface
    # ──────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── En-tête stylisé ──
        header_frame = QFrame()
        header_frame.setStyleSheet(
            "QFrame {"
            "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
            "    stop:0 #0969da, stop:1 #1f6feb);"
            "  border-radius: 8px;"
            "  padding: 12px;"
            "}"
        )
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(16, 10, 16, 10)

        title = QLabel("sketcher")
        title.setFont(_sketcher_font(14, bold=False))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: white;")
        header_layout.addWidget(title)

        subtitle = QLabel(
            "Travaillez en ligne ou hors ligne avec PostGIS")
        subtitle.setFont(_sketcher_font(9))
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: rgba(255,255,255,0.85);")
        header_layout.addWidget(subtitle)
        layout.addWidget(header_frame)

        # ── État de la synchronisation ──
        self.sync_status_group = QGroupBox(
            "État de la synchronisation")
        self.sync_status_group.setStyleSheet(
            "QGroupBox {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 6px;"
            "  margin-top: 6px;"
            "  padding-top: 18px;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin;"
            "  left: 10px;"
            "  padding: 0 4px;"
            "}"
        )
        status_layout = QVBoxLayout(self.sync_status_group)
        status_layout.setContentsMargins(10, 4, 10, 8)
        self.lbl_sync_status = QLabel(
            "Aucune donnée hors ligne détectée.")
        self.lbl_sync_status.setWordWrap(True)
        self.lbl_sync_status.setStyleSheet(
            "font-family: 'Poppins'; font-size: 9pt; font-weight: normal; color: #656d76;")
        status_layout.addWidget(self.lbl_sync_status)

        status_btn_row = QHBoxLayout()
        self.btn_history = QPushButton("Historique")
        self.btn_history.setIcon(_icon("refresh-ccw-clock-svgrepo-com.svg"))
        self.btn_history.setToolTip(
            "Voir l'historique des synchronisations")
        self.btn_history.setMinimumHeight(28)
        self.btn_history.setStyleSheet(
            "QPushButton {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 4px;"
            "  padding: 3px 10px;"
            "  background: #f6f8fa;"
            "}"
            "QPushButton:hover { background: #e8ecf0; }"
        )
        status_btn_row.addWidget(self.btn_history)
        status_btn_row.addStretch()
        status_layout.addLayout(status_btn_row)
        layout.addWidget(self.sync_status_group)

        # ── Connexion ──
        conn_group = QGroupBox("Connexion PostGIS")
        conn_group.setFont(_sketcher_font(9, bold=False))
        conn_layout = QVBoxLayout(conn_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Connexion :"))
        self.combo_conn = QComboBox()
        self.combo_conn.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        row1.addWidget(self.combo_conn, 1)
        self.btn_refresh_conn = QPushButton("Rafraîchir")
        self.btn_refresh_conn.setIcon(_icon("database-download-svgrepo-com.svg"))
        self.btn_refresh_conn.setToolTip("Rafraîchir la liste des connexions")
        row1.addWidget(self.btn_refresh_conn)
        conn_layout.addLayout(row1)

        layout.addWidget(conn_group)

        # ── Schéma ──
        schema_group = QGroupBox("Schéma")
        schema_group.setFont(_sketcher_font(9, bold=False))
        schema_layout = QHBoxLayout(schema_group)
        schema_layout.addWidget(QLabel("Schéma :"))
        self.combo_schema = QComboBox()
        self.combo_schema.setEditable(True)
        schema_layout.addWidget(self.combo_schema, 1)
        self.btn_load_tables = QPushButton("Charger les tables")
        self.btn_load_tables.setIcon(_icon("download-circle-svgrepo-com.svg"))
        schema_layout.addWidget(self.btn_load_tables)
        layout.addWidget(schema_group)

        # ── Tables ──
        table_group = QGroupBox("Tables disponibles")
        table_group.setFont(_sketcher_font(9, bold=False))
        table_layout = QVBoxLayout(table_group)

        select_row = QHBoxLayout()
        self.btn_select_all = QPushButton("Tout sélectionner")
        self.btn_deselect_all = QPushButton("Tout désélectionner")
        self.btn_select_all.setFont(_sketcher_font(9))
        self.btn_deselect_all.setFont(_sketcher_font(9))
        select_row.addWidget(self.btn_select_all)
        select_row.addWidget(self.btn_deselect_all)
        select_row.addStretch()
        table_layout.addLayout(select_row)

        self.list_tables = QListWidget()
        self.list_tables.setSelectionMode(QAbstractItemView.NoSelection)
        table_layout.addWidget(self.list_tables)
        layout.addWidget(table_group)

        # ── Mode ──
        mode_group = QGroupBox("Mode de chargement")
        mode_group.setFont(_sketcher_font(9, bold=False))
        mode_layout = QVBoxLayout(mode_group)
        self.radio_online = QRadioButton(
            "En ligne (connexion directe à la base)")
        self.radio_offline = QRadioButton(
            "Hors ligne (copie locale GeoPackage)")
        self.radio_online.setChecked(True)
        mode_layout.addWidget(self.radio_online)
        mode_layout.addWidget(self.radio_offline)
        layout.addWidget(mode_group)

        # ── Barre de progression ──
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setStyleSheet(
            "QProgressBar {"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 4px;"
            "  text-align: center;"
            "  height: 20px;"
            "}"
            "QProgressBar::chunk {"
            "  background-color: #2ea043;"
            "  border-radius: 3px;"
            "}"
        )
        layout.addWidget(self.progress)

        # ── Boutons ──
        btn_layout = QHBoxLayout()
        self.btn_load = QPushButton("Charger les couches")
        self.btn_load.setMinimumHeight(38)
        self.btn_load.setStyleSheet(
            "QPushButton {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  background-color: #0969da;"
            "  color: white;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 6px 16px;"
            "}"
            "QPushButton:hover { background-color: #0860ca; }"
        )
        btn_layout.addWidget(self.btn_load)

        self.btn_sync = QPushButton("Synchroniser")
        self.btn_sync.setIcon(_icon("cloud-sync-svgrepo-com.svg"))
        self.btn_sync.setMinimumHeight(38)
        self.btn_sync.setToolTip(
            "Compare les couches locales (hors ligne) avec PostGIS\n"
            "et envoie les modifications vers la base."
        )
        self.btn_sync.setStyleSheet(
            "QPushButton {"
            "  font-family: 'Poppins'; font-size: 9pt; font-weight: normal;"
            "  background-color: #2ea043;"
            "  color: white;"
            "  border: none;"
            "  border-radius: 6px;"
            "  padding: 6px 16px;"
            "}"
            "QPushButton:hover { background-color: #238636; }"
        )
        btn_layout.addWidget(self.btn_sync)

        self.btn_close = QPushButton("Fermer")
        self.btn_close.setFont(_sketcher_font(9))
        self.btn_close.setMinimumHeight(38)
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        # ── Status ──
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(
            "font-family: 'Poppins'; font-size: 9pt; font-weight: normal; color: #1f2328;")
        layout.addWidget(self.lbl_status)

    # ──────────────────────────────────────────────
    # Signaux
    # ──────────────────────────────────────────────
    def _connect_signals(self):
        self.btn_refresh_conn.clicked.connect(self._load_connections)
        self.combo_conn.currentIndexChanged.connect(self._on_connection_changed)
        self.btn_load_tables.clicked.connect(self._load_tables)
        self.btn_select_all.clicked.connect(self._select_all_tables)
        self.btn_deselect_all.clicked.connect(self._deselect_all_tables)
        self.btn_close.clicked.connect(self.reject)

    # ──────────────────────────────────────────────
    # Connexions PostGIS
    # ──────────────────────────────────────────────
    def _load_connections(self):
        """Charge la liste des connexions PostgreSQL stockées dans QGIS."""
        self.combo_conn.blockSignals(True)
        self.combo_conn.clear()
        s = QgsSettings()
        s.beginGroup("PostgreSQL/connections")
        conns = s.childGroups()
        s.endGroup()
        if conns:
            self.combo_conn.addItems(sorted(conns))
        self.combo_conn.blockSignals(False)
        if conns:
            self._on_connection_changed()

    def _on_connection_changed(self):
        """Quand l'utilisateur change de connexion → charger les schémas."""
        conn_name = self.combo_conn.currentText()
        if not conn_name:
            return
        self._conn_params = self._get_connection_params(conn_name)
        self._load_schemas()

    @staticmethod
    def _get_connection_params(conn_name):
        """Récupère les paramètres d'une connexion PostgreSQL QGIS."""
        s = QgsSettings()
        prefix = f"PostgreSQL/connections/{conn_name}"
        params = {
            "host": s.value(f"{prefix}/host", "localhost"),
            "port": s.value(f"{prefix}/port", "5432"),
            "database": s.value(f"{prefix}/database", ""),
            "username": s.value(f"{prefix}/username", ""),
            "password": s.value(f"{prefix}/password", ""),
            "sslmode": s.value(f"{prefix}/sslmode", "prefer"),
            "conn_name": conn_name,
        }
        # Si authcfg est défini, le stocker aussi
        authcfg = s.value(f"{prefix}/authcfg", "")
        if authcfg:
            params["authcfg"] = authcfg
        return params

    def _make_uri(self, schema=None, table=None, geom_col=None):
        """Construit un QgsDataSourceUri à partir des paramètres courants."""
        uri = QgsDataSourceUri()
        p = self._conn_params
        uri.setConnection(
            p.get("host", "localhost"),
            p.get("port", "5432"),
            p.get("database", ""),
            p.get("username", ""),
            p.get("password", ""),
        )
        if p.get("authcfg"):
            uri.setAuthConfigId(p["authcfg"])
        if schema and table:
            uri.setDataSource(schema, table, geom_col if geom_col else "")
        return uri

    def _get_pg_connection(self):
        """Retourne une connexion psycopg2 directe."""
        try:
            import psycopg2
            p = self._conn_params
            conn = psycopg2.connect(
                host=p.get("host", "localhost"),
                port=p.get("port", "5432"),
                dbname=p.get("database", ""),
                user=p.get("username", ""),
                password=p.get("password", ""),
            )
            conn.set_session(autocommit=False)
            return conn
        except Exception as e:
            QMessageBox.critical(
                self, "Erreur de connexion",
                f"Impossible de se connecter à PostgreSQL :\n{e}"
            )
            return None

    # ──────────────────────────────────────────────
    # Schémas
    # ──────────────────────────────────────────────
    def _load_schemas(self):
        """Charge la liste des schémas depuis la base."""
        self.combo_schema.clear()
        conn = self._get_pg_connection()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name NOT IN ('pg_catalog','information_schema','pg_toast')
                ORDER BY schema_name;
            """)
            schemas = [row[0] for row in cur.fetchall()]
            self.combo_schema.addItems(schemas)
            cur.close()
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Erreur lors du chargement des schémas :\n{e}")
        finally:
            conn.close()

    # ──────────────────────────────────────────────
    # Tables
    # ──────────────────────────────────────────────
    def _load_tables(self):
        """Charge la liste des tables (géospatiales ou non) du schéma."""
        schema = self.combo_schema.currentText().strip()
        if not schema:
            QMessageBox.warning(self, "Schéma manquant", "Veuillez saisir ou sélectionner un schéma.")
            return
        self.list_tables.clear()

        conn = self._get_pg_connection()
        if not conn:
            return
        try:
            cur = conn.cursor()
            # Récupère les tables et vues avec leur colonne de géométrie éventuelle
            cur.execute("""
                SELECT c.relname AS table_name,
                       a.attname AS geom_column,
                       COALESCE(
                           (SELECT con.attname
                            FROM pg_attribute con
                            JOIN pg_constraint pk ON pk.conrelid = c.oid
                            WHERE pk.contype = 'p'
                              AND con.attrelid = c.oid
                              AND con.attnum = ANY(pk.conkey)
                            LIMIT 1
                           ), ''
                       ) AS pk_column
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_attribute a ON a.attrelid = c.oid
                    AND a.atttypid IN (
                        SELECT oid FROM pg_type
                        WHERE typname IN ('geometry','geography')
                    )
                    AND a.attnum > 0
                WHERE n.nspname = %s
                  AND c.relkind IN ('r','v','m','p')
                ORDER BY c.relname;
            """, (schema,))
            rows = cur.fetchall()
            cur.close()

            seen = set()
            for table_name, geom_col, pk_col in rows:
                if table_name in seen:
                    continue
                seen.add(table_name)
                geom_label = f"  [{geom_col}]" if geom_col else "  [pas de géométrie]"
                item = QListWidgetItem(f"{table_name}{geom_label}")
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Unchecked)
                item.setData(Qt.UserRole, {
                    "table": table_name,
                    "geom_col": geom_col or "",
                    "pk_col": pk_col or "id",
                })
                self.list_tables.addItem(item)

            self.lbl_status.setText(
                f"{self.list_tables.count()} table(s) trouvée(s) dans le schéma « {schema} »."
            )
        except Exception as e:
            QMessageBox.warning(self, "Erreur", f"Erreur lors du chargement des tables :\n{e}")
        finally:
            conn.close()

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────
    def _select_all_tables(self):
        for i in range(self.list_tables.count()):
            self.list_tables.item(i).setCheckState(Qt.Checked)

    def _deselect_all_tables(self):
        for i in range(self.list_tables.count()):
            self.list_tables.item(i).setCheckState(Qt.Unchecked)

    def _browse_gpkg(self):
        pass  # Plus utilisé – chemin auto-généré

    def get_selected_tables(self):
        """Retourne la liste des tables cochées avec leurs métadonnées."""
        selected = []
        for i in range(self.list_tables.count()):
            item = self.list_tables.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.data(Qt.UserRole))
        return selected

    def is_offline_mode(self):
        return self.radio_offline.isChecked()

    def get_schema(self):
        return self.combo_schema.currentText().strip()

    def get_gpkg_path(self):
        """Génère automatiquement le chemin du GeoPackage dans .sketcher/."""
        from ..core.config_manager import ConfigManager
        conn_name = self.combo_conn.currentText()
        schema = self.get_schema()
        if conn_name and schema:
            return ConfigManager.auto_gpkg_path(conn_name, schema)
        return ""

    def get_conn_params(self):
        return dict(self._conn_params)

    def set_status(self, msg):
        self.lbl_status.setText(msg)

    def update_sync_status(self, stats):
        """
        Met à jour le bloc d'état de la synchronisation.
        stats : dict retourné par RevisionManager.get_stats()
        """
        total = stats.get("total_syncs", 0)
        if total == 0:
            self.lbl_sync_status.setText(
                "Aucune synchronisation enregistrée.\n"
                "Téléchargez des couches en mode hors ligne "
                "puis utilisez « Synchroniser ».")
            self.lbl_sync_status.setStyleSheet(
                "font-family: 'Poppins'; font-size: 9pt; font-weight: normal; color: #656d76;")
            return

        last_ts = stats.get("last_sync", "—")
        if last_ts and len(last_ts) > 16:
            last_ts = last_ts[:16].replace("T", " ")
        last_msg = stats.get("last_message", "")

        tp = stats.get("total_pushed", {})
        tl = stats.get("total_pulled", {})

        lines = []
        lines.append(
            f"{total} synchronisation(s) effectuée(s)")
        lines.append(
            f"Dernière : {last_ts}")
        if last_msg:
            lines.append(
                f'Message : « <i>{last_msg[:60]}</i> »')

        push_total = sum(tp.values())
        pull_total = sum(tl.values())
        if push_total or pull_total:
            lines.append(
                f"Push {push_total} poussée(s) | "
                f"Pull {pull_total} importée(s) au total")

        self.lbl_sync_status.setText("<br>".join(lines))
        self.lbl_sync_status.setStyleSheet(
            "font-family: 'Poppins'; font-size: 9pt; font-weight: normal; color: #1f2328;")
