# -*- coding: utf-8 -*-
"""
Fenêtre d'aide du plugin sketcher.
Documentation détaillée avec sommaire cliquable et recherche.
"""

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton,
    QLineEdit, QLabel, QFrame
)
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QFont, QTextDocument

_DOC_HTML = """
<style>
body { font-family: 'Poppins', sans-serif; font-size: 10pt; line-height: 1.5; color: #1f2328; }
h1 { font-size: 18pt; color: #0969da; border-bottom: 2px solid #0969da; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 13pt; color: #1f2328; margin-top: 24px; margin-bottom: 8px; }
h3 { font-size: 11pt; color: #656d76; margin-top: 16px; }
p { margin: 8px 0; }
ul, ol { margin: 8px 0; padding-left: 24px; }
li { margin: 4px 0; }
code { background: #f0f4f8; padding: 2px 6px; border-radius: 4px; font-size: 9pt; }
.sommaire { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px; margin: 12px 0; }
.sommaire a { color: #0969da; text-decoration: none; }
.sommaire a:hover { text-decoration: underline; }
.sommaire ul { list-style: none; padding-left: 0; }
.sommaire li { margin: 6px 0; }
</style>

<h1 id="sommaire">Documentation sketcher</h1>
<p><b>sketcher</b> est un plugin QGIS pour travailler avec des couches PostGIS en <b>ligne</b> (connexion directe) ou <b>hors ligne</b> (copie locale GeoPackage), puis synchroniser les modifications avec la base.</p>

<div class="sommaire">
<h3>Sommaire</h3>
<ul>
<li><a href="#introduction">1. Introduction et concepts</a></li>
<li><a href="#acces">2. Accès au plugin</a></li>
<li><a href="#connexion">3. Connexion PostGIS et schéma</a></li>
<li><a href="#tables">4. Sélection des tables</a></li>
<li><a href="#mode">5. Mode en ligne / hors ligne</a></li>
<li><a href="#chargement">6. Chargement des couches</a></li>
<li><a href="#synchronisation">7. Synchronisation (mode hors ligne)</a></li>
<li><a href="#revue">8. Dialogue de revue des changements</a></li>
<li><a href="#conflits">9. Gestion des conflits</a></li>
<li><a href="#historique">10. Historique des synchronisations</a></li>
<li><a href="#stockage">11. Fichiers et stockage</a></li>
<li><a href="#depannage">12. Dépannage</a></li>
</ul>
</div>

<h2 id="introduction">1. Introduction et concepts</h2>
<p>sketcher permet de charger des tables PostGIS dans QGIS de deux façons :</p>
<ul>
<li><b>En ligne</b> : les couches sont lues directement depuis la base PostgreSQL/PostGIS. Chaque modification (édition dans QGIS) est envoyée en temps réel vers la base. Une connexion réseau est requise.</li>
<li><b>Hors ligne</b> : les tables sont copiées dans un fichier GeoPackage local (dossier <code>.sketcher/</code> à côté du projet). Vous éditez les données sans être connecté. Plus tard, vous lancez une <b>synchronisation</b> pour comparer l’état local avec la base et appliquer vos changements (push) ou récupérer ceux du serveur (pull).</li>
</ul>
<p>La synchronisation est <b>bidirectionnelle</b> : le plugin détecte les insertions, mises à jour et suppressions côté local et côté serveur, et affiche un dialogue de revue pour valider ce qui doit être poussé ou importé. En cas de conflit (même enregistrement modifié des deux côtés), vous choisissez la stratégie (priorité locale, serveur ou ignorer).</p>

<h2 id="acces">2. Accès au plugin</h2>
<p>Après activation du plugin, une barre d’outils <b>sketcher</b> apparaît avec quatre boutons (icônes uniquement) :</p>
<ul>
<li><b>PostGIS En ligne / Hors ligne</b> : ouvre le dialogue principal (connexion, schéma, tables, chargement).</li>
<li><b>Synchroniser</b> : lance une synchronisation rapide à partir de la dernière configuration hors ligne enregistrée (sans rouvrir le dialogue).</li>
<li><b>Historique</b> : ouvre la liste des révisions de synchronisation (date, message, nombre de changements).</li>
<li><b>Aide</b> : ouvre cette documentation.</li>
</ul>
<p>Les mêmes entrées sont disponibles dans le menu <i>Base de données → sketcher</i>.</p>

<h2 id="connexion">3. Connexion PostGIS et schéma</h2>
<p>Dans le dialogue principal :</p>
<ul>
<li><b>Connexion</b> : sélectionnez une connexion PostGIS déjà créée dans QGIS (via <i>Panneau du navigateur → PostgreSQL</i> ou <i>Gestionnaire de connexions</i>). Seules les connexions de type PostGIS sont proposées.</li>
<li><b>Rafraîchir</b> : recharge la liste des connexions (utile si vous venez d’en créer une).</li>
<li><b>Schéma</b> : choisissez le schéma PostgreSQL contenant vos tables (ex. <code>public</code>, <code>cadastre</code>). Le champ est éditable : vous pouvez saisir un nom de schéma qui n’apparaît pas encore dans la liste.</li>
<li><b>Charger les tables</b> : interroge la base pour lister les tables du schéma sélectionné (tables avec géométrie). La liste s’affiche dans la section « Tables disponibles ».</li>
</ul>
<p>En cas d’erreur (connexion refusée, schéma invalide), un message s’affiche. Vérifiez les paramètres de la connexion (hôte, port, base, utilisateur, mot de passe) dans le Gestionnaire de connexions QGIS.</p>

<h2 id="tables">4. Sélection des tables</h2>
<p>Une fois les tables chargées, elles apparaissent dans une liste avec des cases à cocher.</p>
<ul>
<li>Cochez les tables que vous souhaitez charger ou synchroniser.</li>
<li><b>Tout sélectionner</b> / <b>Tout désélectionner</b> : coche ou décoche toutes les tables d’un coup.</li>
</ul>
<p>Seules les tables cochées seront prises en compte lors du « Charger les couches » ou de la synchronisation hors ligne.</p>

<h2 id="mode">5. Mode en ligne / hors ligne</h2>
<p>Deux options radio :</p>
<ul>
<li><b>En ligne (connexion directe à la base)</b> : les couches sont ajoutées au projet en se connectant directement à PostGIS. Les éditions sont envoyées immédiatement à la base. Idéal lorsque vous avez une connexion stable.</li>
<li><b>Hors ligne (copie locale GeoPackage)</b> : les tables sont exportées dans un GeoPackage situé dans le dossier <code>.sketcher/</code> à côté du fichier de projet QGIS (<code>.qgz</code>). Les couches du GeoPackage sont ajoutées au projet dans un groupe dont le nom est celui du schéma. Vous pouvez ensuite travailler sans réseau. Pour envoyer ou récupérer les changements, utilisez le bouton <b>Synchroniser</b> (dialogue ou barre d’outils).</li>
</ul>
<p><b>Important</b> : le projet QGIS doit être enregistré au moins une fois pour que le chemin du GeoPackage (relatif au fichier projet) soit valide.</p>

<h2 id="chargement">6. Chargement des couches</h2>
<p>Cliquez sur <b>Charger les couches</b>.</p>
<ul>
<li><b>Mode en ligne</b> : les couches PostGIS sont chargées et ajoutées au projet.</li>
<li><b>Mode hors ligne</b> : le plugin crée ou met à jour le GeoPackage dans <code>.sketcher/</code>, y copie les données des tables sélectionnées, puis ajoute les couches du GeoPackage au projet. Une configuration (connexion, schéma, tables) est enregistrée dans un fichier JSON à côté du GeoPackage pour les synchronisations ultérieures.</li>
</ul>
<p>Une barre de progression et un message de statut indiquent l’avancement. En cas d’échec (connexion perdue, droits insuffisants), un message d’erreur s’affiche.</p>

<h2 id="synchronisation">7. Synchronisation (mode hors ligne)</h2>
<p>Après avoir modifié les données en mode hors ligne :</p>
<ol>
<li>Ouvrez le dialogue principal ou cliquez directement sur <b>Synchroniser</b> dans la barre d’outils (si une configuration hors ligne existe déjà).</li>
<li>Le plugin analyse les différences entre le GeoPackage local et la base PostGIS (insertions, mises à jour, suppressions locales et distantes, conflits).</li>
<li>Un <b>dialogue de revue</b> s’ouvre : vous voyez par table les changements à pousser (vers la base) et à importer (depuis la base). Vous pouvez cocher/décocher les imports distants et définir comment résoudre les conflits.</li>
<li>Optionnel : saisissez un <b>Message de synchronisation</b> (enregistré dans l’historique, comme un message de commit).</li>
<li>Cliquez sur <b>Confirmer et synchroniser</b>. Les modifications sont appliquées ; une révision est enregistrée dans l’historique.</li>
</ol>
<p>Si aucune modification n’est détectée, un message l’indique et aucun dialogue de revue n’est affiché.</p>

<h2 id="revue">8. Dialogue de revue des changements</h2>
<p>Ce dialogue s’affiche lorsqu’il y a des changements à synchroniser.</p>
<ul>
<li><b>Résumé en haut</b> : nombre total d’insertions, mises à jour, suppressions (locales et distantes) et conflits.</li>
<li><b>Onglets par table</b> : chaque onglet correspond à une table. Vous y voyez les listes détaillées des enregistrements modifiés (identifiants, types de changement). Pour les changements <b>distants</b> (à importer), une case à cocher permet d’inclure ou d’exclure chaque import.</li>
<li><b>Stratégie globale des conflits</b> (si des conflits existent) : menu déroulant pour choisir « Priorité locale », « Priorité serveur » ou « Ignorer les conflits », puis bouton <b>Appliquer à tous</b> pour affecter cette stratégie à tous les conflits.</li>
<li><b>Message de synchronisation</b> : champ optionnel pour un message enregistré dans l’historique.</li>
<li><b>Confirmer et synchroniser</b> : lance l’application des changements validés. <b>Annuler</b> : ferme sans appliquer.</li>
</ul>

<h2 id="conflits">9. Gestion des conflits</h2>
<p>Un <b>conflit</b> survient lorsqu’un même enregistrement (même clé primaire) a été modifié à la fois en local et sur le serveur depuis la dernière synchronisation.</p>
<p>Trois stratégies possibles :</p>
<ul>
<li><b>Priorité locale (écraser la base)</b> : la version locale écrase celle du serveur.</li>
<li><b>Priorité serveur (garder la base)</b> : la version du serveur est conservée ; vos modifications locales pour cet enregistrement sont abandonnées.</li>
<li><b>Ignorer les conflits</b> : le changement en conflit n’est ni poussé ni importé pour cet enregistrement.</li>
</ul>
<p>Vous pouvez appliquer une stratégie globale via le menu « Stratégie » puis <b>Appliquer à tous</b>, ou traiter les conflits individuellement dans les onglets si l’interface le permet.</p>

<h2 id="historique">10. Historique des synchronisations</h2>
<p>Le bouton <b>Historique</b> (ou l’entrée équivalente dans le dialogue principal) ouvre une fenêtre listant les révisions de synchronisation.</p>
<ul>
<li>Pour chaque révision : numéro, date, message, auteur, nombre de changements poussés (push) et importés (pull), conflits résolus, statut (réussi / échec).</li>
<li>En sélectionnant une révision, le détail s’affiche en bas (résumé par table, message de commit, etc.).</li>
</ul>
<p>L’historique est stocké dans le dossier <code>.sketcher/</code> (fichier par configuration). Il permet de suivre qui a synchronisé quoi et quand.</p>

<h2 id="stockage">11. Fichiers et stockage</h2>
<ul>
<li><b>Dossier .sketcher/</b> : il est créé au même niveau que le fichier de projet QGIS (<code>.qgz</code>). Il contient :
<ul>
<li>Un GeoPackage par combinaison connexion + schéma (ex. <code>ma_connexion_public.gpkg</code>).</li>
<li>Un fichier de configuration JSON par GeoPackage (ex. <code>ma_connexion_public_config.json</code>) : paramètres de connexion (sans mot de passe), schéma, liste des tables, chemin du GeoPackage.</li>
<li>Les fichiers d’historique des révisions (pour l’affichage dans la fenêtre Historique).</li>
</ul>
</li>
<li>Le <b>mot de passe</b> PostgreSQL n’est pas stocké en clair ; il est récupéré à chaque fois depuis la connexion QGIS (ou AuthConfig).</li>
<li>Si vous utilisez plusieurs connexions ou schémas, chaque combinaison a son propre GeoPackage et sa propre config.</li>
</ul>

<h2 id="depannage">12. Dépannage</h2>
<ul>
<li><b>Impossible de déterminer le chemin du GeoPackage</b> : enregistrez le projet QGIS (Fichier → Enregistrer) pour que le chemin du fichier projet soit défini.</li>
<li><b>Connexion refusée / erreur PostgreSQL</b> : vérifiez dans QGIS (Gestionnaire de connexions ou Préférences) que la connexion PostGIS est correcte (hôte, port, base, utilisateur, mot de passe). Testez la connexion dans le navigateur QGIS.</li>
<li><b>Erreur lors du chargement des schémas ou des tables</b> : vérifiez les droits de l’utilisateur PostgreSQL sur le schéma et les tables (SELECT, et en écriture si vous éditez).</li>
<li><b>Conflits</b> : choisissez une stratégie (priorité locale, serveur ou ignorer) dans le dialogue de revue. En « Priorité serveur », vos modifications locales pour les enregistrements en conflit sont perdues.</li>
<li><b>Aucune donnée hors ligne / Aucun fichier de configuration</b> : vous avez cliqué sur Synchroniser ou Historique sans avoir d’abord chargé des couches en mode hors ligne pour ce projet. Ouvrez le dialogue principal, choisissez connexion, schéma, tables, mode hors ligne, puis « Charger les couches ».</li>
<li><b>Couches ou données incohérentes après sync</b> : rechargez les couches du projet (retirer et rajouter le groupe) ou fermez et rouvrez le projet pour forcer le rechargement du GeoPackage.</li>
</ul>
"""


class HelpDialog(QDialog):
    """Fenêtre d'aide : documentation détaillée avec sommaire et recherche."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Aide – sketcher")
        self.setMinimumSize(720, 620)
        self.resize(800, 650)
        self._search_pos = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Barre de recherche
        search_frame = QFrame()
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(0, 4, 0, 4)
        search_layout.addWidget(QLabel("Rechercher :"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Saisir un mot ou une expression...")
        self.search_edit.setMinimumHeight(28)
        self.search_edit.returnPressed.connect(self._find_next)
        search_layout.addWidget(self.search_edit)
        self.btn_find_next = QPushButton("Suivant")
        self.btn_find_next.setMinimumHeight(28)
        self.btn_find_next.clicked.connect(self._find_next)
        search_layout.addWidget(self.btn_find_next)
        self.btn_find_prev = QPushButton("Précédent")
        self.btn_find_prev.setMinimumHeight(28)
        self.btn_find_prev.clicked.connect(self._find_prev)
        search_layout.addWidget(self.btn_find_prev)
        layout.addWidget(search_frame)

        # Contenu HTML avec ancres
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(_DOC_HTML)
        self.browser.setStyleSheet(
            "QTextBrowser {"
            "  font-family: 'Poppins', sans-serif;"
            "  font-size: 10pt;"
            "  padding: 12px;"
            "  border: 1px solid #d0d7de;"
            "  border-radius: 6px;"
            "  background: white;"
            "}"
        )
        layout.addWidget(self.browser)

        # Bouton Fermer
        btn_close = QPushButton("Fermer")
        btn_close.setFont(QFont("Poppins", 9))
        btn_close.setMinimumHeight(32)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _find_next(self):
        text = self.search_edit.text().strip()
        if not text:
            return
        cursor = self.browser.textCursor()
        cursor.setPosition(self._search_pos)
        self.browser.setTextCursor(cursor)
        found = self.browser.find(text)
        if found:
            self._search_pos = self.browser.textCursor().position()
        else:
            # Revenir au début pour une nouvelle recherche
            self._search_pos = 0
            self.browser.moveCursor(QTextDocument.Start)
            found = self.browser.find(text)
            if found:
                self._search_pos = self.browser.textCursor().position()

    def _find_prev(self):
        text = self.search_edit.text().strip()
        if not text:
            return
        cursor = self.browser.textCursor()
        # Pour "Précédent", on recule un peu puis on cherche en arrière
        pos = cursor.position() - len(text) - 1
        if pos < 0:
            pos = 0
        cursor.setPosition(pos)
        self.browser.setTextCursor(cursor)
        found = self.browser.find(text, QTextDocument.FindBackward)
        if found:
            self._search_pos = self.browser.textCursor().position()
        else:
            self.browser.moveCursor(QTextDocument.End)
            found = self.browser.find(text, QTextDocument.FindBackward)
            if found:
                self._search_pos = self.browser.textCursor().position()