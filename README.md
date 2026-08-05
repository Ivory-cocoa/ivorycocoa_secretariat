# Secrétariat

Module « fourre-tout » du secrétariat de direction. Les fonctionnalités sont
ajoutées au fur et à mesure ; chacune vit dans sa propre section de menu, sous
la racine **Secrétariat**, et apporte sa section au tableau de bord.

| Fonctionnalité | Version | Menu |
|---|---|---|
| Tableau de bord | 17.0.2.0.0 | Secrétariat → Tableau de bord |
| Bons de carburant | 17.0.2.0.0 | Secrétariat → Carburant |

---

## 0. Tableau de bord

Composant Owl (`ir.actions.client`, tag `secretariat_dashboard`), c'est l'écran
d'accueil du secrétariat.

### Une coque, des sections

Le module a vocation à couvrir d'autres domaines que le carburant. La
séparation est donc faite dès maintenant :

| Modèle | Rôle |
|---|---|
| `secretariat.dashboard` | La **coque** : période, devise, profil, assemblage des sections |
| `secretariat.dashboard.fuel` | La **section carburant** : tout le calcul métier |

Ajouter une section = ajouter un modèle abstrait exposant `get_section_data()`,
l'inscrire dans `secretariat.dashboard._SECTIONS`, et ajouter son bloc au
template. Rien d'autre à toucher.

Une section qui lève une exception est journalisée et **retirée du retour** :
l'écran s'affiche quand même, amputé de cette section seulement.

### Ce que l'écran montre

* **À traiter** — bons à compléter, brouillons à valider, doublons probables,
  engins en dépassement, dates dans le futur, bénéficiaires sans fiche
  employé. Chaque pastille ouvre la liste correspondante ; les compteurs à
  zéro restent visibles mais estompés.
* **Chiffres de la période** — nombre de bons, quantité, montant, chacun
  comparé en pourcentage à la période précédente de même durée, plus le cumul
  de l'année et le montant du bon moyen.
* **Douze derniers mois** — montant en barres, quantité en courbe (Chart.js
  servi depuis `static/lib/`, sans CDN). Les mois sans bon valent zéro : la
  courbe ne saute pas.
* **Répartition par carburant**, **palmarès des engins** et **des
  bénéficiaires**.
* **Dotations du mois** — jauge de consommation par engin doté.
* **Consommations inhabituelles** — voir plus bas.
* **Derniers bons enregistrés** et **tarifs courants**.
* **Actions du quotidien** — nouveau bon, saisie rapide, import, export,
  état mensuel, recherche.

Périodes disponibles : mois, trimestre, année, douze derniers mois, période
choisie. Le tableau de bord n'utilise **aucun `sudo`** : il ne montre que ce
que l'utilisateur a le droit de lire, règles multi-société comprises.

### Détection des consommations inhabituelles

Un engin est signalé quand la quantité du mois en cours dépasse **1,5 fois** sa
moyenne mensuelle des six mois précédents **et** que l'écart absolu atteint
20 unités. Un engin ayant moins de deux mois d'historique n'est jamais signalé :
on ne crie pas au loup sur une première utilisation.

---

## 1. Bons de carburant

Remplace le classeur Excel tenu à la main (`docs/secretariat/yasmina_petroleum.xlsx`).

### Modèles

| Modèle | Rôle |
|---|---|
| `secretariat.fuel.voucher` | Le bon : numéro, date, engin, bénéficiaire, carburant, prix, quantité, total |
| `secretariat.vehicle` | Engin (véhicule, moto, engin d'usine, groupe électrogène…) et sa dotation mensuelle |
| `secretariat.fuel.beneficiary` | Bénéficiaire : employé, service interne ou tiers |
| `secretariat.fuel.type` | Type de carburant et son prix courant (historique des tarifs dans la discussion) |

### Deux partis pris

**Le numéro de bon n'est pas unique.** Le carnet fait revenir le même numéro
sur plusieurs lignes (un bon Super + un bon Lubrifiant le même jour, carnets
différents). Une contrainte d'unicité serait fausse. Le dédoublonnage de
l'import se fait sur `numéro + date + engin + carburant + quantité`.

Le revers, c'est que rien n'empêche la re-saisie accidentelle. D'où un
**avertissement non bloquant** à la saisie quand un bon du même numéro existe
déjà à la même date, et une pastille « doublons probables » au tableau de bord
qui applique la clé complète.

**Engin et bénéficiaire ne sont pas obligatoires en base.** Le classeur repris
comporte des lignes sans engin ou sans nom ; les rendre obligatoires ferait
échouer la reprise de l'historique. Ces bons sont marqués « incomplet » et le
menu **Bons à compléter** les rassemble.

### Saisie

**Secrétariat → Carburant → Saisie rapide** ouvre une liste éditable : on
recopie le carnet ligne à ligne, sans ouvrir de formulaire. Le prix se remplit
depuis le carburant choisi et se réaligne si l'on corrige le carburant — sauf
si un prix a été saisi à la main, qui est alors respecté.

Trois avertissements, tous **non bloquants** :

| Situation | Message |
|---|---|
| Même numéro, même date | « Bon déjà enregistré ? » |
| Date postérieure à aujourd'hui | « Date à venir » |
| Dotation mensuelle de l'engin franchie | « Plafond mensuel dépassé » |

Un seul garde-fou est bloquant : une date à plus d'un an dans le futur est
refusée — c'est une faute de frappe sur l'année, et elle fausserait durablement
toutes les statistiques.

### Verrou de validation

Un bon validé est figé sur ses champs métier. Le verrou porte sur un
**changement réel** de valeur, pas sur l'écriture : réécrire la valeur déjà en
place (édition multiple, re-sauvegarde) passe sans erreur. Les observations et
l'état restent modifiables. Un bon validé ne peut pas être supprimé, seulement
annulé.

### Dotation mensuelle par engin

Facultative, en quantité et/ou en montant (`0` = pas de plafond). Elle
**n'interdit rien** : le secrétariat constate ce qui a été servi, il ne
l'autorise pas. Elle alimente :

* la jauge `quota_usage_rate` sur la fiche et dans la liste des engins ;
* le filtre **En dépassement** (`is_over_quota`, recalculé à la volée — le
  dépassement dépend du mois courant, il ne peut pas être stocké) ;
* l'avertissement de saisie ;
* la pastille et la carte « Dotations du mois » du tableau de bord ;
* la section « Dépassements de dotation » de l'état mensuel — calculée, elle,
  sur la période éditée, pour qu'un état d'avril reflète avril.

Les bons annulés ne consomment pas la dotation.

### Import Excel

`Secrétariat → Carburant → Importer depuis Excel`, en trois temps :

1. **Fichier** — dépôt du `.xlsx` et options (créer les référentiels manquants,
   ignorer les doublons, état des bons créés, station).
2. **Analyse** — la liste des feuilles est proposée avec, pour chacune, le
   nombre de lignes, de nouveautés, de doublons et d'erreurs. On décoche ce
   qu'on ne veut pas reprendre. Un bouton **Retour** ramène à l'étape 1.
3. **Résultat** — compte rendu, référentiels créés, et CSV téléchargeable des
   lignes rejetées.

L'import est **tolérant** et **idempotent** :

* l'en-tête est cherchée dans les dix premières lignes, les colonnes sont
  reconnues par leur libellé (`Numéro`, `Date`, `Engin`, `Nom`, `Carburant`,
  `Prix Unitaire`, `Quantité`, `Total`) ;
* les lignes sans numéro (totaux de bas de feuille) sont ignorées ;
* les orthographes flottantes sont rapprochées sans accent ni casse
  (`gasoil` = `Gasoil`, `GAZ` = `Gaz`, `lubrifiant ` = `Lubrifiant`) ;
* les dates au séparateur oublié (`25/022026`, `13/03/026`, `2004/2026`) sont
  reconstituées, avec une note sur le bon créé ;
* un prix illisible (`#VALUE!`) est remplacé par le prix courant du carburant,
  également signalé en note ;
* un total du fichier incohérent avec `prix × quantité` est signalé en note,
  c'est le calcul qui fait foi ;
* relancer le même import ne crée aucun doublon.

**Le dédoublonnage passe avant la création des référentiels.** Une ligne
écartée ne laisse donc aucun engin fantôme derrière elle, et le nombre annoncé
à l'analyse est exactement celui que produit l'import. Pour y parvenir sans
créer quoi que ce soit, chaque libellé reçoit un identifiant : réel s'il est
connu, négatif et unique s'il ne l'est pas (`_LabelResolver`) — deux engins
inconnus différents restent ainsi distincts.

Les clés de dédoublonnage sont chargées depuis la base **bornées à la plage de
dates du fichier** : inutile de parcourir dix ans de registre pour importer une
feuille de mars.

Mesuré sur le classeur de référence (1 377 lignes, 15 feuilles) :
**1 376 lignes exploitables, 165 doublons écartés, 1 211 bons créés,
1 ligne rejetée** (date réellement absente du fichier).

> Les feuilles du classeur d'origine sont cumulatives : « aout 2026 » est une
> recopie de « juillet 2026 2 » complétée. D'où les 165 doublons — c'est normal,
> et c'est exactement ce que le dédoublonnage traite.

### Export Excel

`Secrétariat → Carburant → Générer le classeur Excel`. Le fichier produit
reprend la présentation du carnet : une feuille par mois **civil**
(« Janvier 2026 »), les huit mêmes colonnes, une ligne de total en bas de
feuille, plus une feuille de synthèse facultative (totaux par mois, carburant,
engin et bénéficiaire).

Le classeur généré est relisible par l'import : un aller-retour
export → import ne crée aucun doublon (test `test_export_round_trips_through_import`).

> Le regroupement se fait sur le mois de la **date du bon**, pas sur le nom de
> la feuille d'origine : dans le classeur repris, la feuille « Janvier 2026 »
> contenait aussi des bons de décembre 2025.

### Rapports PDF

| Rapport | Format | Où |
|---|---|---|
| **Bon de carburant** | A5 portrait, en-tête propre, deux lignes de signature | Bouton **Imprimer** du bon, ou action groupée depuis la liste |
| **État récapitulatif** | A4, totaux, trois répartitions, dépassements, détail facultatif, ligne de signature | `Carburant → État récapitulatif mensuel` |

L'état récapitulatif est le document à faire viser par la direction. Un bouton
**Mois précédent** cale la période d'un clic sur le cas le plus fréquent.

Le bon utilise `web.basic_layout` et dessine son propre en-tête : l'impression
n'ouvre donc pas l'assistant de mise en page d'Odoo (`config=False`).

### Profils

| Groupe | Droits |
|---|---|
| **Secrétaire** | Saisie des bons, import, export, impression, gestion des engins et des bénéficiaires. Pas de suppression. |
| **Responsable du secrétariat** | Idem + suppression des bons et configuration des types de carburant. |

---

## Tests

71 tests, répartis en cinq fichiers :

| Fichier | Couvre |
|---|---|
| `test_fuel_voucher.py` | Calculs, verrou de validation, garde-fous et avertissements de saisie |
| `test_fuel_import_export.py` | Import tolérant et idempotent, échappement du compte rendu, export et aller-retour |
| `test_fuel_quota.py` | Dotation mensuelle, filtre de dépassement, avertissement |
| `test_reports.py` | Rendu des deux rapports et données de l'état mensuel |
| `test_dashboard.py` | Structure du retour, périodes, variations, compteurs, doublons, anomalies, tendance |

```bash
docker exec odoo17-web-dev odoo -d <base> -u ivorycocoa_secretariat \
    --addons-path=/mnt/extra-addons,/mnt/oca-addons,/usr/lib/python3/dist-packages/odoo/addons \
    --test-enable --test-tags /ivorycocoa_secretariat --stop-after-init
```

## Logo

`static/description/icon.png` (1024 × 1024) est **dessiné par programme**, pas
peint : `python3 tools/make_icon.py` le régénère à partir des couleurs et des
proportions déclarées en tête du script. Un **bon détachable** — corps blanc,
souche perforée — vaut pour tout registre tenu par le secrétariat ; la
**goutte ambre** dit la fonctionnalité du jour, en accent. Le symbole reste
donc juste le jour où une deuxième fonctionnalité arrive.

> `ir.ui.menu.web_icon_data` est un champ **stocké**, alimenté depuis le
> fichier au chargement du module : après avoir changé l'icône, il faut mettre
> le module à jour (`-u ivorycocoa_secretariat`) pour la voir apparaître dans
> le menu des applications.

## Dépendances

* `openpyxl` (déjà installé dans l'image Docker du projet) — import et export.
* Chart.js est **embarqué** dans `static/lib/chartjs/` : aucun appel à un CDN.
