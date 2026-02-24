# -*- coding: utf-8 -*-
"""
Gestionnaire de synchronisation bidirectionnel.
Compare les couches locales (GeoPackage) avec les tables PostGIS
et effectue une synchronisation bidirectionnelle complète.

Stratégie :
  1. Pour chaque table, charger 3 versions :
     - baseline : état initial lors du téléchargement
     - local    : état actuel édité par l'utilisateur
     - remote   : état actuel dans PostGIS

  2. Comparer toutes les colonnes (attributs + géométrie) via hash SHA256
     avec tolérance configurable pour les flottants.

  3. Détecter :
     - Changements locaux  (inserts, updates, deletes)
     - Changements distants (inserts, updates, deletes)
     - Conflits (même PK modifiée des deux côtés)

  4. Appliquer :
     - Changements locaux → PostGIS (avec résolution auto PK via séquence/MAX+1)
     - Changements distants → GeoPackage local
     - Conflits → selon stratégie utilisateur

  5. Transactions avec SAVEPOINT par table + rollback automatique.
  6. Mise à jour de la baseline avec l'état final.
"""

import os
import hashlib
import logging
from datetime import date, datetime, time

from qgis.core import (
    QgsVectorLayer, QgsDataSourceUri, QgsFeature,
    QgsProject, QgsVectorFileWriter,
    QgsCoordinateTransformContext, QgsGeometry,
    QgsFeatureRequest
)

# ── Logger ──────────────────────────────────────────
logger = logging.getLogger("sketcher.sync")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)

# Colonnes internes à exclure
_INTERNAL_COLS = {"fid", "__sketcher_geom__"}

# Tolérance pour comparaison de flottants
FLOAT_TOLERANCE = 1e-8

# Types Qt date/time (psycopg2 ne les adapte pas)
try:
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime
except ImportError:
    QDate = QDateTime = QTime = None


def _adapt_value_for_pg(val):
    """
    Convertit les types Qt (QDate, QDateTime, QTime) en types Python
    pour que psycopg2 puisse les envoyer à PostgreSQL.
    """
    if val is None:
        return None
    if hasattr(val, "isNull") and val.isNull():
        return None
    if QDate and isinstance(val, QDate):
        return date(val.year(), val.month(), val.day())
    if QDateTime and isinstance(val, QDateTime):
        if hasattr(val, "toPyDateTime"):
            return val.toPyDateTime()
        d = val.date()
        t = val.time()
        return datetime(
            d.year(), d.month(), d.day(),
            t.hour(), t.minute(), t.second(),
            t.msec() * 1000 if hasattr(t, "msec") else 0,
        )
    if QTime and isinstance(val, QTime):
        return time(
            val.hour(), val.minute(), val.second(),
            val.msec() * 1000 if hasattr(val, "msec") else 0,
        )
    # QVariant peut envelopper un QDate etc.
    if hasattr(val, "value"):
        return _adapt_value_for_pg(val.value())
    return val


class SyncManager:
    """Synchronisation bidirectionnelle local ↔ PostGIS."""

    def __init__(self):
        pass

    # ══════════════════════════════════════════════
    # Hashing & comparaison de lignes
    # ══════════════════════════════════════════════

    @staticmethod
    def _normalize_value(val):
        """Normalise une valeur pour le hashing / la comparaison."""
        if val is None or (hasattr(val, 'isNull') and val.isNull()):
            return "NULL"
        if isinstance(val, float):
            return f"{val:.8f}"
        return str(val)

    @staticmethod
    def _row_hash(row, pk_col=None):
        """
        Calcule un hash SHA256 de toutes les colonnes d'une ligne
        (hors PK et colonnes internes) pour comparaison rapide.
        """
        exclude = set(_INTERNAL_COLS)
        if pk_col:
            exclude.add(pk_col)
        parts = []
        for k in sorted(row.keys()):
            if k in exclude:
                continue
            parts.append(f"{k}={SyncManager._normalize_value(row[k])}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _row_hash_content(row, pk_col=None):
        """
        Hash de tout le contenu (y compris géométrie) pour apparier
        une ligne locale sans PK avec une ligne distante (même donnée).
        """
        exclude = set()
        if pk_col:
            exclude.add(pk_col)
        exclude.add("fid")
        parts = []
        for k in sorted(row.keys()):
            if k in exclude:
                continue
            parts.append(f"{k}={SyncManager._normalize_value(row[k])}")
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _row_differs(row_a, row_b, pk_col=None):
        """
        Compare deux lignes colonne par colonne :
        - Gère NULL vs valeur
        - Tolérance flottants configurable
        - Exclut colonnes internes et PK
        """
        exclude = set(_INTERNAL_COLS)
        if pk_col:
            exclude.add(pk_col)
        all_keys = set(list(row_a.keys()) + list(row_b.keys()))
        for k in all_keys:
            if k in exclude or k.startswith("sketcher_"):
                continue
            va = row_a.get(k)
            vb = row_b.get(k)
            # Gestion NULL
            a_null = va is None or (hasattr(va, 'isNull') and va.isNull())
            b_null = vb is None or (hasattr(vb, 'isNull') and vb.isNull())
            if a_null and b_null:
                continue
            if a_null != b_null:
                return True
            # Flottants avec tolérance
            if isinstance(va, float) and isinstance(vb, float):
                if abs(va - vb) > FLOAT_TOLERANCE:
                    return True
                continue
            # Comparaison standard
            if str(va) != str(vb):
                return True
        return False

    # ══════════════════════════════════════════════
    # Analyse des changements
    # ══════════════════════════════════════════════

    def analyze_changes(self, config, progress_callback=None):
        """
        Analyse les différences entre local, baseline et remote.

        progress_callback : callable(current, total, message) ou None

        Retourne dict { table_name: {
            "inserts", "updates", "deletes",
            "conflicts",
            "remote_inserts", "remote_updates", "remote_deletes",
            "pk_col", "geom_col"
        }}
        """
        gpkg_path = config["gpkg_path"]
        schema = config["schema"]
        conn_info = config["connection"]
        tables = config["tables"]
        all_changes = {}
        total_steps = len(tables) * 4
        step = 0

        for tinfo in tables:
            table_name = tinfo["table"]
            geom_col = tinfo.get("geom_col", "")
            pk_col = tinfo.get("pk_col", "id")

            # ── Étape 1 : Baseline ──
            if progress_callback:
                progress_callback(
                    step, total_steps,
                    f"{table_name} : lecture baseline…")
            baseline_data, _ = self._load_gpkg_layer_data(
                gpkg_path, f"{table_name}_sketcher_baseline", pk_col
            )
            step += 1

            # ── Étape 2 : Local ──
            if progress_callback:
                progress_callback(
                    step, total_steps,
                    f"{table_name} : lecture données locales…")
            local_data, local_new = self._load_gpkg_layer_data(
                gpkg_path, table_name, pk_col
            )
            step += 1

            # ── Étape 3 : Remote ──
            if progress_callback:
                progress_callback(
                    step, total_steps,
                    f"{table_name} : lecture serveur…")
            remote_data = self._load_postgis_layer_data(
                conn_info, schema, table_name, geom_col, pk_col
            )
            step += 1

            if local_data is None or baseline_data is None \
                    or remote_data is None:
                error_msg = ("Impossible de charger une des versions "
                             "de la table.")
                logger.error("%s : %s", table_name, error_msg)
                all_changes[table_name] = {
                    "inserts": [], "updates": [], "deletes": [],
                    "conflicts": [],
                    "remote_inserts": [], "remote_updates": [],
                    "remote_deletes": [],
                    "error": error_msg,
                }
                step += 1
                continue

            # ── Étape 4 : Calcul des différences ──
            if progress_callback:
                progress_callback(
                    step, total_steps,
                    f"{table_name} : calcul des différences…")
            changes = self._compute_changes(
                baseline_data, local_data, remote_data,
                pk_col, local_new
            )
            changes["pk_col"] = pk_col
            changes["geom_col"] = geom_col
            # Doublons : groupes de lignes identiques (même contenu)
            changes["local_duplicate_groups"] = self._compute_duplicate_groups(
                local_data, local_new, pk_col
            )
            changes["remote_duplicate_groups"] = self._compute_duplicate_groups(
                remote_data, [], pk_col
            )
            all_changes[table_name] = changes
            step += 1

            logger.info(
                "%s : %d ins, %d upd, %d del, %d conflits | "
                "distant : %d ins, %d upd, %d del",
                table_name,
                len(changes["inserts"]),
                len(changes["updates"]),
                len(changes["deletes"]),
                len(changes["conflicts"]),
                len(changes["remote_inserts"]),
                len(changes["remote_updates"]),
                len(changes["remote_deletes"]),
            )

        if progress_callback:
            progress_callback(total_steps, total_steps,
                              "Analyse terminée.")
        return all_changes

    @staticmethod
    def _compute_duplicate_groups(data, new_rows, pk_col):
        """
        Détecte les groupes de lignes en double (même contenu).
        data: dict pk -> attrs
        new_rows: list d'attrs (sans PK)
        Retourne une liste de groupes ; chaque groupe = liste de {"pk": pk ou None, "attrs": attrs}.
        """
        from collections import defaultdict
        by_hash = defaultdict(list)
        for pk, attrs in data.items():
            h = SyncManager._row_hash_content(attrs, pk_col)
            by_hash[h].append({"pk": pk, "attrs": attrs})
        for row in new_rows:
            h = SyncManager._row_hash_content(row, pk_col)
            by_hash[h].append({"pk": None, "attrs": row})
        return [group for group in by_hash.values() if len(group) > 1]

    def _compute_changes(self, baseline, local, remote, pk_col,
                         local_new=None):
        """
        Three-way merge : baseline × local × remote.
        Utilise le hash SHA256 pour pré-filtrer les lignes candidates,
        puis comparaison colonne par colonne pour confirmation.
        """
        if local_new is None:
            local_new = []
        baseline_pks = set(baseline.keys())
        local_pks = set(local.keys())
        remote_pks = set(remote.keys())

        # Pré-calcul des hashes
        baseline_hashes = {pk: self._row_hash(r, pk_col)
                           for pk, r in baseline.items()}
        local_hashes = {pk: self._row_hash(r, pk_col)
                        for pk, r in local.items()}
        remote_hashes = {pk: self._row_hash(r, pk_col)
                         for pk, r in remote.items()}

        # ── Changements locaux (local vs baseline) ──
        local_inserts_pks = local_pks - baseline_pks
        local_deletes_pks = baseline_pks - local_pks
        local_updates_pks = set()
        for pk in local_pks & baseline_pks:
            if local_hashes[pk] != baseline_hashes[pk]:
                if self._row_differs(local[pk], baseline[pk], pk_col):
                    local_updates_pks.add(pk)

        # ── Changements distants (remote vs baseline) ──
        remote_inserts_pks = remote_pks - baseline_pks
        remote_deletes_pks = baseline_pks - remote_pks
        remote_updates_pks = set()
        for pk in remote_pks & baseline_pks:
            if remote_hashes[pk] != baseline_hashes[pk]:
                if self._row_differs(remote[pk], baseline[pk], pk_col):
                    remote_updates_pks.add(pk)

        # ── Conflits ──
        conflict_pks = set()
        conflict_pks |= (local_updates_pks & remote_updates_pks)
        conflict_pks |= (local_updates_pks & remote_deletes_pks)
        conflict_pks |= (local_deletes_pks & remote_updates_pks)
        conflict_pks |= (local_inserts_pks & remote_inserts_pks)

        # Retirer des listes normales
        local_inserts_pks -= conflict_pks
        local_updates_pks -= conflict_pks
        local_deletes_pks -= conflict_pks
        remote_inserts_pks -= conflict_pks
        remote_updates_pks -= conflict_pks
        remote_deletes_pks -= conflict_pks

        # ── Appariement local_new ↔ remote_inserts (même contenu, PK différentes) ──
        # Évite de pousser puis ré-importer les mêmes lignes (doublons).
        matched_pairs = []  # [(local_attrs, remote_pk), ...]
        matched_local_indices = set()
        matched_remote_pks = set()
        if local_new and remote_inserts_pks:
            from collections import defaultdict
            by_hash_local = defaultdict(list)
            for i, row in enumerate(local_new):
                h = self._row_hash_content(row, pk_col)
                by_hash_local[h].append(i)
            by_hash_remote = defaultdict(list)
            for pk in remote_inserts_pks:
                h = self._row_hash_content(remote[pk], pk_col)
                by_hash_remote[h].append(pk)
            for h in list(by_hash_local.keys()):
                if h not in by_hash_remote:
                    continue
                loc_list = by_hash_local[h]
                rem_list = by_hash_remote[h]
                n = min(len(loc_list), len(rem_list))
                for j in range(n):
                    idx = loc_list[j]
                    rpk = rem_list[j]
                    matched_pairs.append((local_new[idx], rpk))
                    matched_local_indices.add(idx)
                    matched_remote_pks.add(rpk)

        # ── Construction des résultats ──
        inserts = [
            {"pk": pk, "attributes": local[pk]}
            for pk in sorted(local_inserts_pks, key=str)
        ]
        for i, row in enumerate(local_new):
            if i in matched_local_indices:
                continue
            inserts.append({
                "pk": None, "attributes": row,
                "new_without_pk": True,
            })

        updates = [
            {"pk": pk, "local": local[pk],
             "remote": remote.get(pk, {}),
             "baseline": baseline.get(pk, {})}
            for pk in sorted(local_updates_pks, key=str)
        ]
        deletes = [
            {"pk": pk, "attributes": baseline[pk]}
            for pk in sorted(local_deletes_pks, key=str)
        ]
        conflicts = [
            {"pk": pk, "local": local.get(pk, {}),
             "remote": remote.get(pk, {}),
             "baseline": baseline.get(pk, {})}
            for pk in sorted(conflict_pks, key=str)
        ]

        remote_inserts = [
            {"pk": pk, "attributes": remote[pk]}
            for pk in sorted(remote_inserts_pks, key=str)
            if pk not in matched_remote_pks
        ]
        remote_updates = [
            {"pk": pk, "remote": remote[pk],
             "baseline": baseline.get(pk, {})}
            for pk in sorted(remote_updates_pks, key=str)
        ]
        remote_deletes = [
            {"pk": pk, "attributes": baseline[pk]}
            for pk in sorted(remote_deletes_pks, key=str)
        ]

        return {
            "inserts": inserts,
            "updates": updates,
            "deletes": deletes,
            "conflicts": conflicts,
            "remote_inserts": remote_inserts,
            "remote_updates": remote_updates,
            "remote_deletes": remote_deletes,
            "matched_local_remote": matched_pairs,
        }

    # ══════════════════════════════════════════════
    # Chargement des données
    # ══════════════════════════════════════════════

    def _load_gpkg_layer_data(self, gpkg_path, layer_name, pk_col):
        """Charge une couche GeoPackage → (data, new_rows)."""
        uri = f"{gpkg_path}|layername={layer_name}"
        layer = QgsVectorLayer(uri, layer_name, "ogr")
        if not layer.isValid():
            return None, []
        return self._extract_data(layer, pk_col)

    def _load_postgis_layer_data(self, conn_info, schema, table_name,
                                  geom_col, pk_col):
        """Charge une couche PostGIS → data dict."""
        uri = QgsDataSourceUri()
        uri.setConnection(
            conn_info.get("host", "localhost"),
            str(conn_info.get("port", "5432")),
            conn_info.get("database", ""),
            conn_info.get("username", ""),
            conn_info.get("password", ""),
        )
        if conn_info.get("authcfg"):
            uri.setAuthConfigId(conn_info["authcfg"])
        uri.setDataSource(schema, table_name,
                          geom_col if geom_col else None, "", pk_col)

        layer = QgsVectorLayer(uri.uri(False), table_name, "postgres")
        if not layer.isValid():
            from qgis.core import QgsSettings
            conn_name = conn_info.get("conn_name", "")
            if conn_name:
                s = QgsSettings()
                password = s.value(
                    f"PostgreSQL/connections/{conn_name}/password", ""
                )
                if password:
                    uri.setPassword(password)
                    layer = QgsVectorLayer(
                        uri.uri(False), table_name, "postgres")
            if not layer.isValid():
                logger.error("Impossible de charger %s.%s depuis PostGIS",
                             schema, table_name)
                return None
        data, _ = self._extract_data(layer, pk_col)
        return data

    @staticmethod
    def _extract_data(layer, pk_col):
        """
        Extrait toutes les entités d'une couche.
        Retourne (data, new_rows).
        """
        data = {}
        new_rows = []
        fields = layer.fields()
        field_names = [f.name() for f in fields]

        effective_pk = pk_col if pk_col in field_names else None
        if not effective_pk:
            for candidate in field_names:
                if candidate != "fid":
                    effective_pk = candidate
                    break

        for feat in layer.getFeatures():
            attrs = {}
            for name in field_names:
                if name == "fid":
                    continue
                val = feat[name]
                if val is None or (hasattr(val, 'isNull')
                                   and val.isNull()):
                    attrs[name] = None
                else:
                    attrs[name] = val

            geom = feat.geometry()
            if geom and not geom.isNull():
                attrs["__sketcher_geom__"] = geom.asWkt(precision=8)
            else:
                attrs["__sketcher_geom__"] = None

            pk_val = None
            if effective_pk:
                raw = feat[effective_pk]
                if raw is not None and not (hasattr(raw, 'isNull')
                                            and raw.isNull()):
                    pk_val = raw

            if pk_val is not None:
                data[pk_val] = attrs
            else:
                new_rows.append(attrs)

        return data, new_rows

    # ══════════════════════════════════════════════
    # Résolution automatique de la clé primaire
    # ══════════════════════════════════════════════

    def _resolve_next_pk(self, cur, schema, table, pk_col):
        """
        Détermine la prochaine valeur de clé primaire :
          1. Recherche la séquence PostgreSQL liée (SERIAL/IDENTITY)
          2. Si trouvée → nextval()
          3. Sinon → MAX(pk) + 1
        """
        # 1. Essayer la séquence PostgreSQL
        try:
            cur.execute(
                "SELECT pg_get_serial_sequence(%s, %s)",
                (f'"{schema}"."{table}"', pk_col)
            )
            row = cur.fetchone()
            if row and row[0]:
                seq_name = row[0]
                cur.execute("SELECT nextval(%s)", (seq_name,))
                next_val = cur.fetchone()[0]
                logger.info(
                    "PK auto (séquence %s) : %s = %s",
                    seq_name, pk_col, next_val
                )
                return next_val
        except Exception as e:
            logger.debug(
                "Pas de séquence pour %s.%s.%s : %s",
                schema, table, pk_col, e
            )

        # 2. Fallback : MAX(pk) + 1
        try:
            cur.execute(
                f'SELECT COALESCE(MAX("{pk_col}"), 0) + 1 '
                f'FROM "{schema}"."{table}"'
            )
            next_val = cur.fetchone()[0]
            logger.info(
                "PK auto (MAX+1) : %s = %s pour %s.%s",
                pk_col, next_val, schema, table
            )
            return next_val
        except Exception as e:
            logger.warning(
                "Impossible de déterminer le prochain PK : %s", e
            )
            return 1

    # ══════════════════════════════════════════════
    # Application des changements (push → PostGIS)
    # ══════════════════════════════════════════════

    def apply_changes(self, config, all_changes, conflict_strategies=None,
                      remote_actions=None, progress_callback=None,
                      duplicate_deletions=None):
        """
        Applique les changements bidirectionnels :
          1. Push local → PostGIS (avec SAVEPOINT par table)
          2. Suppression des doublons distants (si choisi)
          3. Pull remote → GeoPackage (+ paires appariées + suppr. doublons locaux)
          4. Mise à jour baseline

        Retourne (success: bool, messages: list[str])
        """
        import psycopg2

        if conflict_strategies is None:
            conflict_strategies = {}
        if remote_actions is None:
            remote_actions = {}
        if duplicate_deletions is None:
            duplicate_deletions = {}

        conn_info = config["connection"]
        schema = config["schema"]
        messages = []

        # Récupérer le mot de passe
        password = conn_info.get("password", "")
        if not password:
            from qgis.core import QgsSettings
            conn_name = conn_info.get("conn_name", "")
            if conn_name:
                s = QgsSettings()
                password = s.value(
                    f"PostgreSQL/connections/{conn_name}/password", ""
                )

        # ── Connexion PostgreSQL ──
        try:
            pg_conn = psycopg2.connect(
                host=conn_info.get("host", "localhost"),
                port=conn_info.get("port", "5432"),
                dbname=conn_info.get("database", ""),
                user=conn_info.get("username", ""),
                password=password,
            )
            pg_conn.set_session(autocommit=False)
            logger.info("Connexion PostgreSQL établie.")
        except Exception as e:
            logger.critical("Connexion PostgreSQL échouée : %s", e)
            return False, [f"[ERREUR] Erreur de connexion PostgreSQL : {e}"]

        # Calculer le total d'opérations pour la progression
        total_ops = sum(
            len(ch.get("inserts", []))
            + len(ch.get("updates", []))
            + len(ch.get("deletes", []))
            + len(ch.get("conflicts", []))
            for ch in all_changes.values()
        )
        if total_ops == 0:
            total_ops = 1
        current_op = 0

        try:
            cur = pg_conn.cursor()

            for table_name, changes in all_changes.items():
                if changes.get("error"):
                    messages.append(
                        f"Attention {table_name} : {changes['error']}")
                    continue

                pk_col = changes.get("pk_col", "id")
                geom_col = changes.get("geom_col", "")
                n_ins = n_upd = n_del = n_conf = 0

                # SAVEPOINT par table
                sp = f"sketcher_{table_name.replace(' ', '_')}"
                cur.execute(f'SAVEPOINT "{sp}"')
                logger.debug("SAVEPOINT %s", sp)

                try:
                    # ── INSERTIONS ──
                    for item in changes.get("inserts", []):
                        current_op += 1
                        if progress_callback:
                            progress_callback(
                                current_op, total_ops,
                                f"{table_name} : insertion…")
                        try:
                            self._pg_insert(
                                cur, schema, table_name,
                                pk_col, geom_col, item)
                            n_ins += 1
                        except Exception as e:
                            pk_d = item.get('pk') or 'NEW'
                            msg = (f"[ERREUR] INSERT {table_name} "
                                   f"pk={pk_d} : {e}")
                            messages.append(msg)
                            logger.error(msg)

                    # ── MISES À JOUR ──
                    for item in changes.get("updates", []):
                        current_op += 1
                        if progress_callback:
                            progress_callback(
                                current_op, total_ops,
                                f"{table_name} : mise à jour…")
                        try:
                            self._pg_update(
                                cur, schema, table_name,
                                pk_col, geom_col, item)
                            n_upd += 1
                        except Exception as e:
                            msg = (f"[ERREUR] UPDATE {table_name} "
                                   f"pk={item['pk']} : {e}")
                            messages.append(msg)
                            logger.error(msg)

                    # ── SUPPRESSIONS ──
                    for item in changes.get("deletes", []):
                        current_op += 1
                        if progress_callback:
                            progress_callback(
                                current_op, total_ops,
                                f"{table_name} : suppression…")
                        try:
                            self._pg_delete(
                                cur, schema, table_name,
                                pk_col, item)
                            n_del += 1
                        except Exception as e:
                            msg = (f"[ERREUR] DELETE {table_name} "
                                   f"pk={item['pk']} : {e}")
                            messages.append(msg)
                            logger.error(msg)

                    # ── CONFLITS ──
                    for item in changes.get("conflicts", []):
                        current_op += 1
                        pk_val = item["pk"]
                        strategy = conflict_strategies.get(
                            (table_name, pk_val), "skip"
                        )
                        if progress_callback:
                            progress_callback(
                                current_op, total_ops,
                                f"{table_name} : conflit pk={pk_val}…")
                        if strategy == "local":
                            try:
                                if item.get("local"):
                                    self._pg_upsert(
                                        cur, schema, table_name,
                                        pk_col, geom_col, item)
                                n_conf += 1
                            except Exception as e:
                                msg = (f"[ERREUR] CONFLIT {table_name} "
                                       f"pk={pk_val} : {e}")
                                messages.append(msg)
                                logger.error(msg)
                        elif strategy == "remote":
                            n_conf += 1
                        # else: skip

                    # ── Suppression des doublons distants (serveur) ──
                    rem_dup_pks = duplicate_deletions.get(
                        table_name, {}).get("remote", [])
                    for pk in rem_dup_pks:
                        try:
                            self._pg_delete(
                                cur, schema, table_name, pk_col, {"pk": pk})
                            n_del += 1
                        except Exception as e:
                            msg = (f"[ERREUR] Suppression doublon "
                                   f"{table_name} pk={pk} : {e}")
                            messages.append(msg)
                            logger.error(msg)

                    # Release savepoint
                    cur.execute(f'RELEASE SAVEPOINT "{sp}"')
                    logger.debug("RELEASE SAVEPOINT %s", sp)

                except Exception as e:
                    cur.execute(f'ROLLBACK TO SAVEPOINT "{sp}"')
                    msg = f"[ERREUR] Rollback table {table_name} : {e}"
                    messages.append(msg)
                    logger.error(msg)
                    continue

                # Résumé table
                parts = []
                if n_ins:
                    parts.append(f"{n_ins} insertion(s)")
                if n_upd:
                    parts.append(f"{n_upd} mise(s) à jour")
                if n_del:
                    parts.append(f"{n_del} suppression(s)")
                if n_conf:
                    parts.append(f"{n_conf} conflit(s) résolu(s)")
                if parts:
                    messages.append(
                        f"[OK] {table_name} : {', '.join(parts)}.")
                else:
                    # Pas d'erreur : simplement aucun changement local
                    messages.append(
                        f"[INFO] {table_name} : aucune modification "
                        f"locale a pousser vers le serveur.")

            pg_conn.commit()
            logger.info("COMMIT PostgreSQL réussi.")
            cur.close()

        except Exception as e:
            pg_conn.rollback()
            logger.critical("Erreur synchronisation, ROLLBACK : %s", e)
            err_lower = str(e).lower()
            if ("server closed" in err_lower
                    or "connection" in err_lower
                    or "could not connect" in err_lower):
                messages.append(
                    f"[ERREUR] Perte de connexion PostgreSQL : {e}\n"
                    "Vérifiez votre connexion réseau.")
            else:
                messages.append(f"[ERREUR] Erreur synchronisation : {e}")
            try:
                pg_conn.close()
            except Exception:
                pass
            return False, messages

        pg_conn.close()

        # ── Pull remote → GeoPackage (+ doublons locaux à supprimer) ──
        if remote_actions or duplicate_deletions:
            if progress_callback:
                progress_callback(
                    total_ops, total_ops,
                    "Import des modifications distantes…")
            remote_msgs = self.apply_remote_to_gpkg(
                config, all_changes, remote_actions,
                duplicate_deletions=duplicate_deletions)
            messages.extend(remote_msgs)

        # ── Mise à jour baseline ──
        if progress_callback:
            progress_callback(
                total_ops, total_ops,
                "Mise à jour de la baseline…")
        self._update_baseline(config)
        logger.info("Baseline mise à jour.")

        return True, messages

    # ══════════════════════════════════════════════
    # Import distant → GeoPackage
    # ══════════════════════════════════════════════

    def apply_remote_to_gpkg(self, config, all_changes, remote_actions,
                            duplicate_deletions=None):
        """Importe les modifications distantes dans le GeoPackage local."""
        if duplicate_deletions is None:
            duplicate_deletions = {}
        gpkg_path = config["gpkg_path"]
        messages = []

        for table_name, changes in all_changes.items():
            pk_col = changes.get("pk_col", "id")

            t_acts = remote_actions.get(table_name, {})
            ins_pks = set(t_acts.get("import_inserts", []))
            upd_pks = set(t_acts.get("import_updates", []))
            del_pks = set(t_acts.get("apply_deletes", []))

            matched_pairs = changes.get("matched_local_remote", [])
            loc_dup = duplicate_deletions.get(table_name, {}).get("local", [])
            if (not ins_pks and not upd_pks and not del_pks
                    and not matched_pairs and not loc_dup):
                continue

            uri = f"{gpkg_path}|layername={table_name}"
            layer = QgsVectorLayer(uri, table_name, "ogr")
            if not layer.isValid():
                msg = (f"[ERREUR] {table_name} (local) : impossible "
                       "d'ouvrir le GeoPackage.")
                messages.append(msg)
                logger.error(msg)
                continue

            layer.startEditing()
            n_imp = n_del_local = 0
            n_matched = 0
            n_dup_local = 0

            # ── Suppression des doublons locaux (garder 1 par groupe) ──
            for pk_val, attrs in loc_dup:
                if pk_val is not None:
                    expr = self._build_pk_expression(pk_col, pk_val)
                    req = QgsFeatureRequest().setFilterExpression(expr)
                    for feat in layer.getFeatures(req):
                        layer.deleteFeature(feat.id())
                        n_dup_local += 1
                        break
                else:
                    target_hash = self._row_hash_content(attrs, pk_col)
                    for feat in layer.getFeatures():
                        feat_attrs = {}
                        for f in layer.fields():
                            nm = f.name()
                            if nm == "fid":
                                continue
                            feat_attrs[nm] = feat[nm]
                        geom = feat.geometry()
                        feat_attrs["__sketcher_geom__"] = (
                            geom.asWkt(precision=8) if geom and not geom.isNull()
                            else None
                        )
                        if self._row_hash_content(feat_attrs, pk_col) == target_hash:
                            layer.deleteFeature(feat.id())
                            n_dup_local += 1
                            break

            # ── Paires appariées (même ligne locale/serveur) : mettre à jour la PK locale ──
            if matched_pairs:
                pk_idx = layer.fields().indexOf(pk_col)
                if pk_idx >= 0:
                    updated_fids = set()
                    for local_attrs, remote_pk in matched_pairs:
                        target_hash = self._row_hash_content(local_attrs, pk_col)
                        for feat in layer.getFeatures():
                            if feat.id() in updated_fids:
                                continue
                            feat_attrs = {}
                            for f in layer.fields():
                                nm = f.name()
                                if nm == "fid":
                                    continue
                                v = feat[nm]
                                feat_attrs[nm] = v
                            geom = feat.geometry()
                            feat_attrs["__sketcher_geom__"] = (
                                geom.asWkt(precision=8) if geom and not geom.isNull()
                                else None
                            )
                            if self._row_hash_content(feat_attrs, pk_col) != target_hash:
                                continue
                            pk_val = feat[pk_col]
                            if pk_val is not None and not (
                                    hasattr(pk_val, "isNull") and pk_val.isNull()):
                                continue
                            layer.changeAttributeValue(
                                feat.id(), pk_idx,
                                _adapt_value_for_pg(remote_pk))
                            updated_fids.add(feat.id())
                            n_matched += 1
                            break
                    if n_matched:
                        logger.debug(
                            "%s : %d ligne(s) appariée(s) (PK locale mise à jour)",
                            table_name, n_matched)

            # ── Import des insertions distantes ──
            for item in changes.get("remote_inserts", []):
                if item["pk"] not in ins_pks:
                    continue
                attrs = dict(item["attributes"])
                geom_wkt = attrs.pop("__sketcher_geom__", None)
                feat = QgsFeature(layer.fields())
                for fld in layer.fields():
                    nm = fld.name()
                    if nm in attrs and nm != "fid":
                        feat[nm] = attrs[nm]
                if geom_wkt:
                    feat.setGeometry(QgsGeometry.fromWkt(geom_wkt))
                layer.addFeature(feat)
                n_imp += 1

            # ── Import des mises à jour distantes ──
            for item in changes.get("remote_updates", []):
                pk_val = item["pk"]
                if pk_val not in upd_pks:
                    continue
                r_attrs = dict(item["remote"])
                geom_wkt = r_attrs.pop("__sketcher_geom__", None)
                expr = self._build_pk_expression(pk_col, pk_val)
                req = QgsFeatureRequest().setFilterExpression(expr)
                for feat in layer.getFeatures(req):
                    for fld in layer.fields():
                        nm = fld.name()
                        if nm in r_attrs and nm != pk_col \
                                and nm != "fid":
                            idx = layer.fields().indexOf(nm)
                            layer.changeAttributeValue(
                                feat.id(), idx, r_attrs[nm])
                    if geom_wkt:
                        layer.changeGeometry(
                            feat.id(),
                            QgsGeometry.fromWkt(geom_wkt))
                    n_imp += 1
                    break

            # ── Application des suppressions distantes ──
            for item in changes.get("remote_deletes", []):
                pk_val = item["pk"]
                if pk_val not in del_pks:
                    continue
                expr = self._build_pk_expression(pk_col, pk_val)
                req = QgsFeatureRequest().setFilterExpression(expr)
                for feat in layer.getFeatures(req):
                    layer.deleteFeature(feat.id())
                    n_del_local += 1
                    break

            if layer.commitChanges():
                parts = []
                if n_dup_local:
                    parts.append(f"{n_dup_local} doublon(s) supprimé(s)")
                if n_matched:
                    parts.append(f"{n_matched} appariée(s) (PK mise à jour)")
                if n_imp:
                    parts.append(f"{n_imp} importee(s)")
                if n_del_local:
                    parts.append(f"{n_del_local} supprimee(s)")
                msg = (f"[OK] {table_name} (local) : "
                       f"{', '.join(parts)}.")
                messages.append(msg)
                logger.info(msg)
            else:
                errs = layer.commitErrors()
                msg = (f"[ERREUR] {table_name} (local) : "
                       f"erreur commit : {'; '.join(errs)}")
                messages.append(msg)
                logger.error(msg)
                layer.rollBack()

        return messages

    # ══════════════════════════════════════════════
    # Opérations SQL unitaires
    # ══════════════════════════════════════════════

    @staticmethod
    def _build_pk_expression(pk_col, pk_val):
        """
        Construit une expression QGIS sécurisée pour filtrer par PK.
        Échappe les apostrophes et backslashes dans les valeurs string.
        """
        if isinstance(pk_val, str):
            escaped = pk_val.replace("\\", "\\\\").replace("'", "\\'")
            return f'"{pk_col}" = \'{escaped}\''
        return f'"{pk_col}" = {pk_val}'

    def _pg_insert(self, cur, schema, table, pk_col, geom_col, item):
        """INSERT avec résolution automatique de la PK."""
        attrs = dict(item.get("attributes", {}))
        geom_wkt = attrs.pop("__sketcher_geom__", None)
        is_new = item.get("new_without_pk", False)

        # Résoudre la PK si NULL
        pk_val = attrs.get(pk_col)
        pk_is_null = (
            pk_val is None or is_new
            or (hasattr(pk_val, 'isNull') and pk_val.isNull())
        )
        if pk_is_null:
            new_pk = self._resolve_next_pk(
                cur, schema, table, pk_col)
            attrs[pk_col] = new_pk
            logger.info(
                "INSERT %s.%s : PK auto-assignée %s = %s",
                schema, table, pk_col, new_pk)

        cols = []
        vals = []
        placeholders = []

        for col, val in attrs.items():
            if col in _INTERNAL_COLS:
                continue
            if geom_col and col == geom_col:
                continue
            cols.append(f'"{col}"')
            vals.append(_adapt_value_for_pg(val))
            placeholders.append("%s")

        if geom_col and geom_wkt:
            cols.append(f'"{geom_col}"')
            srid = self._detect_srid_from_table(
                cur, schema, table, geom_col)
            vals.append(geom_wkt)
            placeholders.append(f"ST_GeomFromText(%s, {srid})")

        if not cols:
            return

        sql = (f'INSERT INTO "{schema}"."{table}" '
               f'({", ".join(cols)}) '
               f'VALUES ({", ".join(placeholders)})')
        cur.execute(sql, vals)
        logger.debug("INSERT OK dans %s.%s", schema, table)

    def _pg_update(self, cur, schema, table, pk_col, geom_col, item):
        """UPDATE une ligne dans PostGIS."""
        attrs = dict(item.get("local", {}))
        pk_val = item["pk"]
        geom_wkt = attrs.pop("__sketcher_geom__", None)

        sets = []
        vals = []
        for col, val in attrs.items():
            if col == pk_col or col in _INTERNAL_COLS \
                    or col == geom_col:
                continue
            sets.append(f'"{col}" = %s')
            vals.append(_adapt_value_for_pg(val))

        if geom_col and geom_wkt:
            srid = self._detect_srid_from_table(
                cur, schema, table, geom_col)
            sets.append(
                f'"{geom_col}" = ST_GeomFromText(%s, {srid})')
            vals.append(geom_wkt)

        if not sets:
            return

        vals.append(_adapt_value_for_pg(pk_val))
        sql = (f'UPDATE "{schema}"."{table}" '
               f'SET {", ".join(sets)} '
               f'WHERE "{pk_col}" = %s')
        cur.execute(sql, vals)
        logger.debug("UPDATE OK %s.%s pk=%s", schema, table, pk_val)

    def _pg_delete(self, cur, schema, table, pk_col, item):
        """DELETE une ligne dans PostGIS."""
        pk_val = _adapt_value_for_pg(item["pk"])
        sql = (f'DELETE FROM "{schema}"."{table}" '
               f'WHERE "{pk_col}" = %s')
        cur.execute(sql, [pk_val])
        logger.debug("DELETE OK %s.%s pk=%s", schema, table, pk_val)

    def _pg_upsert(self, cur, schema, table, pk_col, geom_col, item):
        """Upsert : delete + insert pour résoudre un conflit."""
        pk_val = _adapt_value_for_pg(item["pk"])
        cur.execute(
            f'DELETE FROM "{schema}"."{table}" '
            f'WHERE "{pk_col}" = %s',
            [pk_val])
        mock_item = {"pk": pk_val,
                     "attributes": item.get("local", {})}
        self._pg_insert(cur, schema, table, pk_col, geom_col,
                        mock_item)
        logger.debug("UPSERT OK %s.%s pk=%s",
                     schema, table, pk_val)

    @staticmethod
    def _detect_srid_from_table(cur, schema, table, geom_col):
        """Récupère le SRID depuis geometry_columns."""
        try:
            cur.execute("""
                SELECT srid FROM geometry_columns
                WHERE f_table_schema = %s
                  AND f_table_name = %s
                  AND f_geometry_column = %s
                LIMIT 1;
            """, (schema, table, geom_col))
            row = cur.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    # ══════════════════════════════════════════════
    # Mise à jour de la baseline
    # ══════════════════════════════════════════════

    def _update_baseline(self, config):
        """Recopie les tables de travail vers les tables baseline."""
        gpkg_path = config["gpkg_path"]
        tables = config["tables"]

        for tinfo in tables:
            table_name = tinfo["table"]
            baseline_name = f"{table_name}_sketcher_baseline"

            uri = f"{gpkg_path}|layername={table_name}"
            work_layer = QgsVectorLayer(uri, table_name, "ogr")
            if not work_layer.isValid():
                logger.warning(
                    "Baseline non mise à jour pour %s : "
                    "couche invalide", table_name)
                continue

            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = baseline_name
            options.fileEncoding = "UTF-8"
            options.actionOnExistingFile = \
                QgsVectorFileWriter.CreateOrOverwriteLayer

            ctx = QgsCoordinateTransformContext()
            QgsVectorFileWriter.writeAsVectorFormatV2(
                work_layer, gpkg_path, ctx, options)
            logger.debug("Baseline mise à jour : %s", table_name)
