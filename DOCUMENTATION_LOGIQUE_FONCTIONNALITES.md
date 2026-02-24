# Documentation complète — Logique interne du plugin Sketcher (PostGIS/QGIS)

Ce document est un cours technique progressif qui explique la logique fonctionnelle du plugin `synch` (Sketcher), en s’adressant à un lecteur débutant qui veut comprendre non seulement *ce que fait* le plugin, mais surtout *comment* et *pourquoi* il le fait.

La section centrale de ce document est l’explication détaillée du **three-way merge** (fusion à trois états), qui est le cœur de la synchronisation.

---

## Table des matières

1. [Objectif du plugin](#1-objectif-du-plugin)
2. [Architecture générale](#2-architecture-générale)
3. [Concepts fondamentaux à connaître](#3-concepts-fondamentaux-à-connaître)
4. [Cycle complet d’utilisation](#4-cycle-complet-dutilisation)
5. [Three-way merge expliqué en profondeur](#5-three-way-merge-expliqué-en-profondeur)
6. [Détection des conflits et stratégies de résolution](#6-détection-des-conflits-et-stratégies-de-résolution)
7. [Appariement des lignes locales NEW avec les lignes distantes](#7-appariement-des-lignes-locales-new-avec-les-lignes-distantes)
8. [Détection et suppression des doublons local/serveur](#8-détection-et-suppression-des-doublons-localserveur)
9. [Application transactionnelle des changements](#9-application-transactionnelle-des-changements)
10. [Mise à jour de la baseline](#10-mise-à-jour-de-la-baseline)
11. [Historique des révisions](#11-historique-des-révisions)
12. [Asynchronisme et réactivité UI](#12-asynchronisme-et-réactivité-ui)
13. [Gestion des types date/heure (QDate)](#13-gestion-des-types-dateheure-qdate)
14. [Affichage responsive et logs défilables](#14-affichage-responsive-et-logs-défilables)
15. [Limites actuelles et améliorations possibles](#15-limites-actuelles-et-améliorations-possibles)
16. [Glossaire](#16-glossaire)

---

## 1. Objectif du plugin

Le plugin Sketcher permet de travailler sur des couches PostGIS de deux manières :

- **Mode en ligne** : édition directe de la base.
- **Mode hors ligne** : édition locale dans un GeoPackage, puis synchronisation.

La synchronisation n’est pas un simple “copier-coller”. C’est une logique de comparaison fine entre trois états de la donnée, afin d’identifier :

- ce qui a changé en local,
- ce qui a changé côté serveur,
- ce qui est en conflit,
- ce qui doit être poussé, tiré, ignoré ou fusionné.

---

## 2. Architecture générale

### 2.1 Orchestration

- `sketcher_plugin.py`  
  Point d’entrée fonctionnel : initialise les actions, ouvre les dialogues, lance les tâches d’analyse/applique, gère les retours utilisateur.

### 2.2 Interface utilisateur

- `dialogs/main_dialog.py` : choix connexion/schéma/tables/mode.
- `dialogs/sync_review_dialog.py` : revue des changements, conflits, doublons.
- `dialogs/history_dialog.py` : historique des révisions.
- `dialogs/help_dialog.py` : documentation intégrée avec recherche.
- `dialogs/sync_result_dialog.py` : journal de synchronisation défilable.

### 2.3 Logique métier

- `core/offline_manager.py` : création GeoPackage + baseline.
- `core/sync_manager.py` : analyse des écarts + application des changements.
- `core/config_manager.py` : persistance de configuration.
- `core/revision_manager.py` : gestion de l’historique.
- `core/sync_task.py` : exécution non bloquante via `QgsTask`.

---

## 3. Concepts fondamentaux à connaître

Avant de comprendre le three-way merge, il faut clarifier trois mots clés :

- **Baseline** : copie de référence prise au moment du passage hors ligne.
- **Local** : état actuel de la couche dans le GeoPackage (après édition utilisateur).
- **Remote** : état actuel de la table sur PostGIS.

La baseline sert de “photo de départ”. Sans elle, on ne peut pas savoir ce qui a changé *depuis* le dernier point commun.

---

## 4. Cycle complet d’utilisation

### Étape A — Préparation hors ligne

1. L’utilisateur choisit connexion/schéma/tables.
2. Le plugin copie chaque table dans le GeoPackage :
   - `table` (travail),
   - `table_sketcher_baseline` (référence).
3. Le plugin écrit la configuration JSON.

### Étape B — Analyse

Pour chaque table, le plugin lit baseline/local/remote, puis calcule :

- inserts/updates/deletes locaux,
- inserts/updates/deletes distants,
- conflits,
- appariements local↔remote,
- doublons locaux et distants.

### Étape C — Revue utilisateur

L’utilisateur valide :

- les imports distants,
- les stratégies de conflit,
- la suppression de doublons.

### Étape D — Application

1. Push local vers PostGIS.
2. Suppression de doublons serveur (si demandée).
3. Pull distant vers GeoPackage.
4. Mise à jour baseline.
5. Enregistrement d’une révision.

---

## 5. Three-way merge expliqué en profondeur

Le **three-way merge** consiste à comparer trois versions d’une même donnée :

- version de référence : **baseline**,
- version locale : **local**,
- version serveur : **remote**.

On ne compare pas local à remote directement, car cela ne dit pas “qui a changé quoi depuis le point commun”.  
Le point commun est justement la baseline.

### 5.1 Détection locale (local vs baseline)

Pour une table donnée :

- `local_pks - baseline_pks` => insertions locales.
- `baseline_pks - local_pks` => suppressions locales.
- PK présentes dans les deux mais contenu différent => mises à jour locales.

### 5.2 Détection distante (remote vs baseline)

Même logique côté serveur :

- `remote_pks - baseline_pks` => insertions distantes.
- `baseline_pks - remote_pks` => suppressions distantes.
- PK présentes dans les deux mais contenu différent => mises à jour distantes.

### 5.3 Pourquoi utiliser un hash

Le plugin calcule un hash de ligne pour filtrer vite les candidats modifiés.

- Si hash local == hash baseline, la ligne n’a probablement pas changé.
- Si hash différent, il confirme ensuite avec comparaison champ par champ.

Cela apporte un compromis performance/fiabilité.

### 5.4 Matrice conceptuelle des cas

Pour une PK donnée (ou une ligne logique), voici les cas essentiels :

| Local vs Baseline | Remote vs Baseline | Interprétation |
|---|---|---|
| inchangé | inchangé | rien à faire |
| modifié | inchangé | changement local à pousser |
| inchangé | modifié | changement distant à importer |
| modifié | modifié (même PK) | conflit potentiel |

Le plugin implémente cette logique table par table.

### 5.5 Important : lignes sans PK (NEW)

Les lignes locales sans PK ne peuvent pas être comparées par identifiant.  
C’est précisément ce qui rend nécessaire un mécanisme d’**appariement par contenu** (section 7).

---

## 6. Détection des conflits et stratégies de résolution

Le plugin identifie des conflits lorsque des opérations incompatibles touchent la même PK :

- update local + update distant,
- update local + delete distant,
- delete local + update distant,
- insert local + insert distant sur la même PK.

Stratégies disponibles :

- **Local** : la version locale prime.
- **Serveur** : la version distante prime.
- **Ignorer** : ne pas appliquer pour cette ligne conflictuelle.

La stratégie peut être définie globalement ou ajustée cas par cas.

---

## 7. Appariement des lignes locales NEW avec les lignes distantes

### 7.1 Problème réel

Une ligne locale “NEW” (sans PK) peut être la même entité métier qu’une ligne distante déjà créée (avec PK serveur).  
Sans traitement spécifique, le système peut faire :

- push de la ligne locale,
- puis pull de la ligne distante,

ce qui produit des doublons.

### 7.2 Solution implémentée

Le plugin calcule un **hash de contenu incluant la géométrie** (`_row_hash_content`) et réalise un appariement :

- `local_new` ↔ `remote_inserts`

Quand une paire est trouvée :

1. la ligne est retirée du push standard,
2. la ligne est retirée du pull standard,
3. la PK locale est mise à jour avec la PK serveur.

Cette logique évite les doubles insertions tout en réconciliant les identifiants.

---

## 8. Détection et suppression des doublons local/serveur

### 8.1 Détection

Le plugin regroupe les lignes par hash de contenu :

- groupes locaux : `local_duplicate_groups`,
- groupes distants : `remote_duplicate_groups`.

Un groupe de taille > 1 est un doublon.

### 8.2 Revue utilisateur

Dans `sync_review_dialog`, une section “Doublons détectés” propose :

- local : supprimer doublons en gardant une occurrence,
- serveur : idem.

Les cases sont cochées par défaut, mais l’utilisateur garde le contrôle.

### 8.3 Application

- **Serveur** : suppression des PK choisies dans la phase push.
- **Local** :
  - suppression par PK quand disponible,
  - sinon suppression par correspondance de contenu (cas PK null/NEW).

---

## 9. Application transactionnelle des changements

La synchronisation serveur est encadrée par :

- transaction PostgreSQL globale,
- `SAVEPOINT` par table.

Pourquoi c’est important :

- une erreur table A n’oblige pas à annuler table B,
- les messages d’erreur restent précis et exploitables,
- la base garde un état cohérent.

---

## 10. Mise à jour de la baseline

Après application, le plugin recopie chaque couche de travail vers la couche baseline.

Conséquence :

- le prochain cycle d’analyse redémarre depuis un état propre,
- le diff reste stable et reproductible.

---

## 11. Historique des révisions

Chaque synchronisation produit une révision :

- identifiant court,
- numéro,
- date,
- auteur,
- message,
- résumé push/pull/conflits,
- statut.

Les révisions sont stockées en JSON avec écriture atomique (temp + replace), ce qui réduit les risques de corruption.

---

## 12. Asynchronisme et réactivité UI

Le plugin utilise deux `QgsTask` :

- `SyncAnalyzeTask` (analyse),
- `SyncApplyTask` (application).

Avantages :

- l’interface ne se fige pas,
- progression possible,
- gestion claire des erreurs de thread.

---

## 13. Gestion des types date/heure (QDate)

Problème classique : `psycopg2` ne sait pas adapter directement certains types Qt (`QDate`, `QDateTime`, `QTime`).

Le plugin convertit ces types vers des types Python (`date`, `datetime`, `time`) avant exécution SQL.

Résultat : disparition des erreurs du type `can't adapt type 'QDate'`.

---

## 14. Affichage responsive et logs défilables

Deux améliorations UI majeures :

1. **Revue de synchronisation** : zone principale défilable, boutons fixes.
2. **Résultat de synchronisation** : dialogue dédié avec log scrollable.

Objectif : éviter les pertes visuelles de texte et améliorer la lisibilité des gros rapports.

---

## 15. Limites actuelles et améliorations possibles

### Limites

- Le matching par hash suppose une égalité stricte de contenu.
- “Garder la première occurrence” est une stratégie simple, pas toujours métier-optimal.
- Les règles inter-tables complexes ne sont pas encore explicitement arbitrées.

### Pistes d’évolution

- Stratégies de conservation avancées (plus récent, meilleure qualité géométrique, priorité source).
- Rapport de décision exportable (PDF/Markdown) par synchronisation.
- Règles de dédoublonnage configurables par table/champ.

---

## 16. Glossaire

- **PK** : clé primaire (identifiant unique).
- **Baseline** : état de référence initial.
- **Local** : état courant dans le GeoPackage.
- **Remote** : état courant dans PostGIS.
- **Push** : local vers serveur.
- **Pull** : serveur vers local.
- **Conflit** : divergence locale/serveur sur même entité.
- **Appariement** : reconnaissance de deux lignes identiques avec PK différentes.
- **Doublon** : plusieurs lignes au même contenu logique.
- **Savepoint** : point de retour transactionnel SQL.

---

### Conclusion

Le plugin Sketcher implémente une logique de synchronisation avancée, structurée autour d’un three-way merge robuste, enrichi de mécanismes modernes :

- gestion de conflits,
- appariement intelligent NEW ↔ distant,
- détection et suppression de doublons,
- exécution transactionnelle et asynchrone,
- traçabilité par révisions.

Ce document constitue une base de référence pour la formation, la maintenance et les futures évolutions du plugin.

