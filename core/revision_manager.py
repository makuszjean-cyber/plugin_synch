# -*- coding: utf-8 -*-
"""
Gestionnaire de l'historique des révisions (façon Git).
Chaque synchronisation réussie crée une « révision » contenant :
  - un identifiant unique (rev_id)
  - un horodatage
  - un message de commit (saisi par l'utilisateur)
  - un résumé quantitatif des changements
  - les tables concernées

Les révisions sont stockées dans un fichier JSON
à côté de la configuration dans .sketcher/.
"""

import json
import os
import uuid
import getpass
from datetime import datetime

import logging

logger = logging.getLogger("sketcher.revision")

# Nombre maximal de révisions conservées
MAX_REVISIONS = 200


class RevisionManager:
    """Historique Git-like des synchronisations."""

    def __init__(self, gpkg_path):
        self._gpkg_path = gpkg_path

    # ─────────────────── Chemins ───────────────────

    @property
    def revisions_path(self):
        """Chemin du fichier JSON de révisions."""
        if not self._gpkg_path:
            return None
        directory = os.path.dirname(self._gpkg_path)
        gpkg_name = os.path.splitext(
            os.path.basename(self._gpkg_path))[0]
        return os.path.join(directory, f"{gpkg_name}_revisions.json")

    # ─────────────────── Lecture / écriture ───────────────────

    def _load_all(self):
        """Charge la liste des révisions depuis le fichier JSON."""
        path = self.revisions_path
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, IOError):
            return []

    def _save_all(self, revisions):
        """Écrit la liste des révisions dans le fichier JSON.
        
        Utilise un fichier temporaire + rename pour éviter la
        corruption en cas de crash.
        """
        path = self.revisions_path
        if not path:
            return
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(revisions, f, indent=2,
                          ensure_ascii=False, default=str)
            # Remplacement atomique (Windows : écraser si existe)
            if os.path.exists(path):
                os.replace(tmp_path, path)
            else:
                os.rename(tmp_path, path)
        except (IOError, OSError) as exc:
            logger.error(
                "Impossible d'écrire les révisions dans "
                "'%s' : %s", path, exc)
            # Nettoyer le fichier temporaire
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    # ─────────────────── API publique ───────────────────

    def add_revision(self, commit_message, all_changes,
                     conflict_strategies=None, remote_actions=None,
                     messages=None, success=True):
        """
        Enregistre une nouvelle révision après synchronisation.

        commit_message     : str  – message libre de l'utilisateur
        all_changes        : dict – résultat de analyze_changes()
        conflict_strategies: dict – stratégies de résolution
        remote_actions     : dict – actions distantes choisies
        messages           : list – messages de résultat
        success            : bool – la synchro a-t-elle réussi ?

        Retourne le dict de la révision créée.
        """
        revisions = self._load_all()

        # ── Construire le résumé ──
        summary = self._build_summary(
            all_changes, conflict_strategies, remote_actions)

        # ── Identifier l'utilisateur ayant appliqué les modifications ──
        try:
            user = getpass.getuser()
        except Exception:
            user = None
        if not user:
            user = os.environ.get("USERNAME") or os.environ.get("USER") or "inconnu"

        rev = {
            "rev_id": str(uuid.uuid4())[:8],
            "rev_number": len(revisions) + 1,
            "timestamp": datetime.now().isoformat(),
            "message": commit_message or self._auto_message(summary),
            "user": user,
            "success": success,
            "summary": summary,
            "messages": messages or [],
        }

        revisions.append(rev)

        # Pruning : garder les N dernières
        if len(revisions) > MAX_REVISIONS:
            revisions = revisions[-MAX_REVISIONS:]

        self._save_all(revisions)
        logger.info(
            "Révision #%d enregistrée : %s",
            rev["rev_number"], rev["message"][:60])
        return rev

    def list_revisions(self, limit=50):
        """
        Retourne les dernières révisions (les plus récentes d'abord).
        """
        revisions = self._load_all()
        # Tri décroissant par numéro
        revisions.sort(key=lambda r: r.get("rev_number", 0),
                       reverse=True)
        return revisions[:limit]

    def get_revision(self, rev_id):
        """Retourne une révision par son ID."""
        for r in self._load_all():
            if r.get("rev_id") == rev_id:
                return r
        return None

    def get_stats(self):
        """
        Retourne des statistiques globales :
        - nombre total de synchros
        - dernière synchro (date + message)
        - total inserts/updates/deletes poussés
        """
        revisions = self._load_all()
        if not revisions:
            return {
                "total_syncs": 0,
                "last_sync": None,
                "last_message": None,
                "total_pushed": {"inserts": 0, "updates": 0,
                                 "deletes": 0},
                "total_pulled": {"inserts": 0, "updates": 0,
                                 "deletes": 0},
            }

        last = max(revisions,
                   key=lambda r: r.get("rev_number", 0))
        total_push = {"inserts": 0, "updates": 0, "deletes": 0}
        total_pull = {"inserts": 0, "updates": 0, "deletes": 0}

        for rev in revisions:
            s = rev.get("summary", {})
            push = s.get("pushed", {})
            pull = s.get("pulled", {})
            for k in ("inserts", "updates", "deletes"):
                total_push[k] += push.get(k, 0)
                total_pull[k] += pull.get(k, 0)

        return {
            "total_syncs": len(revisions),
            "last_sync": last.get("timestamp"),
            "last_message": last.get("message"),
            "total_pushed": total_push,
            "total_pulled": total_pull,
        }

    def count_revisions(self):
        """Nombre total de révisions."""
        return len(self._load_all())

    # ─────────────────── Helpers privés ───────────────────

    @staticmethod
    def _build_summary(all_changes, conflict_strategies=None,
                       remote_actions=None):
        """
        Construit un résumé quantitatif de la synchronisation.
        """
        pushed = {"inserts": 0, "updates": 0, "deletes": 0}
        pulled = {"inserts": 0, "updates": 0, "deletes": 0}
        conflicts_resolved = 0
        tables = []

        for table_name, ch in (all_changes or {}).items():
            n_ins = len(ch.get("inserts", []))
            n_upd = len(ch.get("updates", []))
            n_del = len(ch.get("deletes", []))
            n_conf = len(ch.get("conflicts", []))

            pushed["inserts"] += n_ins
            pushed["updates"] += n_upd
            pushed["deletes"] += n_del
            conflicts_resolved += n_conf

            # Distants importés
            ra = (remote_actions or {}).get(table_name, {})
            n_ri = len(ra.get("import_inserts", []))
            n_ru = len(ra.get("import_updates", []))
            n_rd = len(ra.get("apply_deletes", []))
            pulled["inserts"] += n_ri
            pulled["updates"] += n_ru
            pulled["deletes"] += n_rd

            if n_ins or n_upd or n_del or n_ri or n_ru or n_rd \
                    or n_conf:
                tables.append({
                    "table": table_name,
                    "pushed": {"inserts": n_ins, "updates": n_upd,
                               "deletes": n_del},
                    "pulled": {"inserts": n_ri, "updates": n_ru,
                               "deletes": n_rd},
                    "conflicts": n_conf,
                })

        return {
            "pushed": pushed,
            "pulled": pulled,
            "conflicts_resolved": conflicts_resolved,
            "tables": tables,
        }

    @staticmethod
    def _auto_message(summary):
        """Génère un message de commit automatique si aucun n'est fourni."""
        parts = []
        push = summary.get("pushed", {})
        pull = summary.get("pulled", {})
        conf = summary.get("conflicts_resolved", 0)
        tables = summary.get("tables", [])

        if push.get("inserts"):
            parts.append(f"+{push['inserts']} ins")
        if push.get("updates"):
            parts.append(f"~{push['updates']} upd")
        if push.get("deletes"):
            parts.append(f"-{push['deletes']} del")
        if pull.get("inserts") or pull.get("updates") \
                or pull.get("deletes"):
            n = (pull.get("inserts", 0) + pull.get("updates", 0)
                 + pull.get("deletes", 0))
            parts.append(f"Pull {n} importée(s)")
        if conf:
            parts.append(f"Conflits {conf}")

        table_names = [t["table"] for t in tables[:3]]
        prefix = ", ".join(table_names)
        if len(tables) > 3:
            prefix += f" (+{len(tables) - 3})"

        if parts:
            return f"Sync {prefix} : {' | '.join(parts)}"
        return "Synchronisation sans modifications"
