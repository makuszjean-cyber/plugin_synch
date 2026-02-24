# PostGIS Sketcher – Plugin QGIS

**Auteur** : Rodrigue Gasore, Gracier Sikuli, Joel Nyakasaza 
**Version** : 1.0.0  
**Compatibilité** : QGIS 3.16+

---

## Description

Plugin QGIS permettant de travailler **en ligne** (connexion directe PostGIS) ou **hors ligne** (copie locale GeoPackage) avec vos couches stockées dans une base PostgreSQL/PostGIS, puis de **synchroniser** les modifications avec la base de données.

---

## Installation

### Méthode manuelle

1. Copiez le dossier `synch` dans le répertoire des plugins QGIS :
   - **Windows** : `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux** : `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **macOS** : `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`

2. Redémarrez QGIS.

3. Allez dans **Extensions → Gérer les extensions** et activez **« PostGIS Sketcher »**.

4. Le plugin apparaît dans :
   - La barre d'outils **sketcher**
   - Le menu **Base de données → sketcher**

### Prérequis

- QGIS 3.16 ou supérieur (LTR recommandé)
- PostgreSQL/PostGIS accessible
- Une connexion PostGIS configurée dans QGIS (Couche → Gestionnaire de sources de données → PostGIS)
- `psycopg2` (inclus dans l'installation standard de QGIS)

---

## Utilisation

### 1. Charger des couches

1. Cliquez sur le bouton **sketcher** dans la barre d'outils (ou menu Base de données → sketcher).
2. Sélectionnez votre **connexion PostGIS** dans la liste déroulante.
3. Choisissez le **schéma** (ex. `rodrigue`). Les schémas disponibles sont listés automatiquement.
4. Cliquez sur **« Charger les tables »** pour afficher les tables du schéma.
5. Cochez les tables à charger.
6. Choisissez le mode :
   - **En ligne** : connexion directe à PostGIS (édition en temps réel).
   - **Hors ligne** : téléchargement vers un fichier GeoPackage local.
7. Pour le mode hors ligne, indiquez le chemin du fichier `.gpkg` via le bouton « Parcourir… ».
8. Cliquez sur **« Charger les couches »**.

Les couches sont ajoutées au projet dans un **groupe de couches** nommé comme le schéma (ex. `rodrigue`).

### 2. Travailler hors ligne

- Les couches hors ligne sont des copies locales stockées dans le GeoPackage.
- Vous pouvez **éditer**, **ajouter**, **supprimer** des entités et **modifier les attributs** librement.
- Aucune connexion à la base n'est nécessaire pendant le travail.
- Un fichier de configuration `.sketcher_config.json` est créé à côté du GeoPackage pour permettre la resynchronisation ultérieure.

### 3. Synchroniser avec la base

1. Reconnectez-vous au réseau.
2. Cliquez sur **« Synchroniser »** (depuis le dialogue principal ou le bouton de la barre d'outils).
3. Le plugin compare vos couches locales avec la base PostGIS :
   - **Insertions** : nouvelles entités ajoutées localement → seront insérées en base.
   - **Mises à jour** : entités modifiées localement → seront mises à jour en base.
   - **Suppressions** : entités supprimées localement → seront supprimées en base.
   - **Conflits** : entités modifiées **à la fois** en local et en base → vous devez choisir.
4. Le dialogue de revue affiche un résumé détaillé par table.
5. Pour les conflits, vous pouvez choisir :
   - **Priorité locale** : écraser la version en base par la version locale.
   - **Priorité serveur** : garder la version en base.
   - **Ignorer** : ne rien faire pour cette entité.
6. Cliquez sur **« Confirmer et synchroniser »** pour appliquer.

---

## Architecture technique

```
synch/
├── __init__.py                  # Point d'entrée QGIS
├── metadata.txt                 # Métadonnées du plugin
├── sketcher_plugin.py             # Classe principale (sketcher)
├── icon.png                     # Icône du plugin
├── dialogs/
│   ├── __init__.py
│   ├── main_dialog.py           # Dialogue de sélection connexion/schéma/tables
│   └── sync_review_dialog.py    # Dialogue de revue de synchronisation
├── core/
│   ├── __init__.py
│   ├── config_manager.py        # Persistance de la configuration
│   ├── offline_manager.py       # Téléchargement PostGIS → GeoPackage
│   └── sync_manager.py          # Comparaison et synchronisation
└── README.md
```

### Stratégie de synchronisation

Le plugin utilise une approche **« three-way merge »** :

1. **Baseline** : copie de l'état de la base au moment du téléchargement (stockée dans le GeoPackage avec le suffixe `_sketcher_baseline`).
2. **Local** : état actuel des couches éditées par l'utilisateur.
3. **Remote** : état actuel de la base PostGIS.

La comparaison se fait via la **clé primaire** de chaque table :
- Lignes présentes en local mais pas dans la baseline → **insertions locales**
- Lignes présentes dans la baseline mais pas en local → **suppressions locales**
- Lignes modifiées vs baseline → **mises à jour locales**
- Si une même ligne est modifiée en local **et** en base → **conflit**

### Format de stockage hors ligne

- **GeoPackage** (`.gpkg`) : format standard OGC, compact, portable.
- Chaque table sélectionnée produit deux couches dans le GeoPackage :
  - `ma_table` (copie de travail)
  - `ma_table_sketcher_baseline` (référence pour la synchronisation)

---

## Limitations et extensions futures

### Limitations actuelles
- La détection de conflits se fait sur la base de la clé primaire. Si la table n'a pas de clé primaire, le plugin utilise le champ `id` ou le premier champ.
- Les séquences PostgreSQL (auto-increment) ne sont pas synchronisées : les nouvelles lignes insérées en local utilisent les valeurs de PK générées par le GeoPackage. Assurez-vous que vos séquences PostgreSQL sont correctement configurées.
- Les relations (clés étrangères) entre tables ne sont pas vérifiées lors de la synchronisation.

### Extensions possibles
- **Synchronisation bidirectionnelle complète** : rapatrier les modifications distantes vers le GeoPackage local.
- **Synchronisation partielle** : ne synchroniser que certaines tables ou certains champs.
- **Historique des synchronisations** : journal des opérations effectuées.
- **Support des triggers** : exécuter les triggers PostgreSQL lors de la synchronisation.
- **Mode « merge interactif »** : éditeur visuel pour résoudre les conflits attribut par attribut.

---

## Licence

Ce plugin est distribué sous licence libre. Vous êtes libre de l'utiliser, le modifier et le redistribuer.
