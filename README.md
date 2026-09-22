# Secrétariat

Module « fourre-tout » du secrétariat de direction. Les fonctionnalités sont
ajoutées au fur et à mesure ; chacune vit dans sa propre section de menu, sous
la racine **Secrétariat**, et apporte sa section au tableau de bord.

| Fonctionnalité | Version | Menu |
|---|---|---|
| Tableau de bord | 17.0.3.0.0 | Secrétariat → Tableau de bord |
| Bons de carburant | 17.0.3.0.0 | Secrétariat → Carburant |
| Facturation des stations | 17.0.3.0.0 | Secrétariat → Facturation |

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
| `secretariat.dashboard.fuel` | La **section carburant** : consommation, dotations, délais d'utilisation |
| `secretariat.dashboard.billing` | La **section facturation** : quinzaine en cours, encours, écarts |

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

**Le numéro est proposé, jamais imposé.** Un compteur (`ir.sequence`,
padding 8) propose le prochain numéro à la création, à partir d'un numéro de
départ réglable dans **Configuration → Paramètres du secrétariat**. Il reste
modifiable : c'est la souche papier qui fait foi, et un compteur qui
imposerait son propre numéro finirait par diverger du carnet.

Trois précautions rendent ce compromis tenable :

* le formulaire **lit** le compteur sans le consommer — une saisie abandonnée
  ne troue pas la numérotation ;
* après chaque création, le compteur se **recale** au-dessus du plus grand
  numéro utilisé (jamais en dessous : un bon saisi en retard avec un vieux
  numéro ne fait pas redescendre la suite) ;
* les numéros purement numériques sont comparés à leur **valeur** :
  « 6275 » et « 00006275 » sont le même bon. L'historique, saisi sans zéros de
  remplissage, continue donc d'être dédoublonné correctement — et l'import du
  classeur reprend les numéros **tels quels**, sans renuméroter le passé.

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

### Deux dates : émission et utilisation

Un bon est **émis** un jour et **utilisé** un autre — parfois des semaines plus
tard. L'écart est précisément ce que la direction veut voir.

| Champ | Sens |
|---|---|
| `date` | Date d'**émission** : le bon est établi et remis au bénéficiaire |
| `date_used` | Date d'**utilisation** : le bon est servi à la station (vide tant qu'il n'est pas revenu) |
| `date_effective` | **Date de référence** = utilisation si connue, émission sinon |
| `usage_delay_days` | Écart entre les deux, en jours |
| `usage_alert` | `used` / `pending` / `late` / `critical`, selon les seuils de la société |

`date_effective` est la date qui **rattache** le bon à un mois de consommation,
à une dotation mensuelle et à une quinzaine de facturation : on compte le
carburant quand il est servi, pas quand le carnet est rempli. Les bons repris
de l'ancien classeur n'ont qu'une date — le repli les laisse dans leur mois
d'origine, les chiffres historiques ne bougent pas.

`usage_alert` n'est **pas stocké** : un bon non utilisé vieillit tout seul, et
un champ stocké afficherait une alerte périmée jusqu'au prochain recalcul. Il
porte une méthode `search=` qui retraduit chaque état en bornes de date, si
bien qu'il reste filtrable depuis la vue de recherche et le tableau de bord.

Les seuils (alerte et critique, 15 et 30 jours par défaut) se règlent par
société dans **Configuration → Paramètres du secrétariat**. Ils ne bloquent
rien : ils alimentent les statistiques, les pastilles et les **recommandations**
du tableau de bord — le rôle du secrétariat est de constater, pas d'autoriser.

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

Un bouton **Télécharger le modèle Excel** ouvre l'assistant sur un classeur
prêt à remplir : les huit colonnes au bon libellé, la liste déroulante des
carburants connus, le total calculé par formule, et une feuille
**Mode d'emploi** qui dit, colonne par colonne, ce qui est obligatoire et ce
qui ne l'est pas.

> La feuille « Mode d'emploi » ne peut pas être prise pour des données : la
> détection d'en-tête exige de trouver Numéro, Date, Carburant **et** Quantité
> sur une même ligne, or le mode d'emploi les répartit sur des lignes
> différentes. Le modèle vierge se réimporte donc sans rien créer — c'est
> testé.

`Secrétariat → Carburant → Importer depuis Excel`, en trois temps :

1. **Fichier** — dépôt du `.xlsx` et options (créer les référentiels manquants,
   ignorer les doublons, état des bons créés, station).
2. **Analyse** — la liste des feuilles est proposée avec, pour chacune, le
   nombre de lignes, de nouveautés, de doublons et d'erreurs. On décoche ce
   qu'on ne veut pas reprendre. Un bouton **Retour** ramène à l'étape 1.
2 bis. **Aperçu** — l'étape 2 montre aussi les huit premières lignes *telles
   qu'elles ont été comprises*, chaque valeur en face de sa colonne. Si l'en-tête
   avait été mal reconnu, cela se voit avant de s'engager, pas après. Les
   libellés absents de la base y portent une pastille « nouveau » : c'est là
   qu'on repère la faute de frappe qui allait créer un engin de plus.
3. **Résultat** — compte rendu, référentiels créés, et le classeur des lignes
   non reprises.

#### La boucle de correction

Les lignes rejetées ressortent **au format même du modèle d'import**, cellule
fautive surlignée en rouge et motif en clair. La secrétaire corrige, renvoie le
fichier tel quel, et c'est fini : ni CSV à convertir, ni classeur à
reconstruire. Les colonnes « Motif du rejet » et « Origine » sont ignorées à la
relecture, elles peuvent rester.

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

### Doublons de référentiels

L'import crée un engin par libellé rencontré : la reprise du classeur en a
produit **212 engins et 105 bénéficiaires**. La normalisation rattrape la casse
et les accents, pas « Camara » contre « Camara M. ».

Le filtre **Doublons probables**, dans la recherche des engins et des
bénéficiaires, rapproche les libellés voisins (ressemblance ≥ 85 %). Une règle
supplémentaire évite le piège des immatriculations : **deux libellés dont les
chiffres diffèrent ne sont pas le même objet** — « AA 720 AC01 » et
« AA 790 AC01 » sont deux véhicules. Une suite de chiffres qui en prolonge une
autre reste tolérée, pour ne pas séparer « AA 892 NJ » de « AA 892 NJ01 ».

Sur les données reprises, le filtre signale 43 engins et 22 bénéficiaires. Cela
reste une **suggestion** : « AA 164 KT01 » et « AA 164 VT01 » peuvent être une
faute de frappe comme deux véhicules. D'où une fusion explicite et confirmée,
jamais automatique.

Sélectionner les enregistrements → menu **Action → Fusionner**. L'assistant
propose de conserver celui qui porte le plus de bons (le moins de choses à
déplacer), annonce combien de bons vont bouger, et écrit les anciens libellés
dans les notes du survivant — ils restent ainsi retrouvables par la recherche,
ce qui compte quand on cherche un bon d'après ce qui était écrit sur la souche.

> La fusion repointe aussi les bons **validés**. C'est la seule entorse au
> verrou, elle est portée par un contexte dédié et ne desserre que l'engin et
> le bénéficiaire : le numéro, la date, le prix et la quantité restent figés.

### Rapports PDF

| Rapport | Format | Où |
|---|---|---|
| **Bon de carburant** | A5 portrait, en-tête propre, deux lignes de signature | Bouton **Imprimer** du bon, ou action groupée depuis la liste |
| **État récapitulatif** | A4, totaux, trois répartitions, dépassements, détail facultatif, ligne de signature | `Carburant → État récapitulatif mensuel` |

L'état récapitulatif est le document à faire viser par la direction. Un bouton
**Mois précédent** cale la période d'un clic sur le cas le plus fréquent.

Le bon utilise `web.basic_layout` et dessine son propre en-tête : l'impression
n'ouvre donc pas l'assistant de mise en page d'Odoo (`config=False`).

---

## 2. Facturation des stations

Les stations facturent **deux fois par mois** : du 1er au 15, puis du 16 à la
fin du mois. Le modèle `secretariat.fuel.invoice` est le document de contrôle
qui regroupe les bons d'une station sur une quinzaine.

### Trois partis pris

**La quinzaine est une période civile, pas un intervalle libre.** Les bornes
sont calculées à partir de (année, mois, quinzaine) — jamais saisies. Deux
factures qui se chevauchent factureraient deux fois les mêmes bons sans que
rien ne le montre.

**Le rattachement se fait sur `date_effective`.** On paie la station pour ce
qu'elle a servi, pas pour ce que le secrétariat a écrit.

**L'écart avec le relevé ne bloque pas, mais doit être justifié.** Valider une
facture qui ne tombe pas juste exige une explication écrite : c'est la seule
trace qui restera dans six mois.

### Garde-fous

| Garde-fou | Mise en œuvre |
|---|---|
| Un bon n'appartient qu'à une seule facture | `invoice_id` (Many2one) |
| Une seule facture vivante par station et quinzaine | **Index unique partiel** (`WHERE state != 'cancelled'`) — tient face à deux saisies concurrentes, ce qu'un contrôle applicatif ne fait pas |
| Une facture validée fige ses bons | `INVOICE_LOCKED_FIELDS`, y compris la station et la date d'utilisation |
| Un bon facturé ne peut être ni annulé ni supprimé | `action_cancel` / `unlink` |
| Annuler une facture rend ses bons à la facturation | `action_cancel` détache et le journalise |

Le dernier point mérite l'explication : laissés attachés à une facture annulée,
les bons n'apparaîtraient plus comme facturables et seraient purement et
simplement oubliés.

### Le geste courant

**Facturation → Générer les factures de la quinzaine** : l'assistant montre un
**aperçu** station par station (nombre de bons, montant, effet sur une facture
existante) **avant** toute écriture, puis crée une facture par station. Les
bons dont la station n'est pas renseignée sont rattachés à la station par
défaut de la société si elle est définie — sinon ils sont signalés comme non
facturables, jamais escamotés.

Une station qui a déjà une facture non annulée sur la quinzaine n'en reçoit pas
une seconde : ses bons manquants complètent la facture existante si elle est
encore en brouillon, et l'assistant le dit si elle est déjà validée.

### Cycle de vie

`Brouillon` → `Validée` → `Payée`, plus `Annulée`. Valider une facture valide
au passage les bons restés en brouillon qu'elle porte : on ne paie pas un bon
non validé.

### États imprimés

| Document | Format | Contenu |
|---|---|---|
| Facture de station | PDF A4 | Identification, montants confrontés, répartition par carburant, détail des bons, trois signatures |
| État annuel des factures | PDF A4 **paysage** + Excel | Matrice stations × 12 mois, totaux, réglé / reste à payer, écarts cumulés, comparaison N-1 |

Le paysage n'est pas un choix esthétique : douze colonnes de mois plus le total
ne tiennent pas en portrait. Le PDF et le classeur Excel sont produits à partir
du **même** `report_data()` — un chiffre lu dans l'un est celui de l'autre.

---

## 3. Paramètres

Quatre réglages : **station par défaut**, **seuils de délai d'utilisation**,
**numéro de départ des bons** et **longueur du numéro**.

Ils sont accessibles depuis **deux écrans**, selon le profil :

| Écran | Pour qui | Chemin |
|---|---|---|
| **Paramètres → Secrétariat** (`res.config.settings`) | Administrateur | Paramètres, ou Secrétariat → Configuration → Paramètres (administrateur) |
| **Assistant « Paramètres du secrétariat »** | Responsable du secrétariat | Secrétariat → Configuration → Paramètres du secrétariat |

Pourquoi deux : ces valeurs vivent sur `res.company` et sur `ir.sequence`, et
`res.config.settings` exige les droits d'**administration**. Il aurait fallu
les donner à la personne qui, précisément, ne fait que tenir le carnet.
L'assistant écrit en `sudo` **après** avoir contrôlé explicitement
l'appartenance au groupe métier : c'est le groupe qui autorise, pas le
contournement.

Les deux écrans écrivent le **même** stockage et partagent le **même** point
d'entrée de contrôle, `secretariat.fuel.voucher._configure_numbering()` — ils
ne peuvent donc pas diverger, et un test le vérifie.

### Numéro de départ

Le numéro de départ est le numéro proposé au **prochain** bon créé : on le
règle sur le premier numéro du carnet en cours, et il avance ensuite tout
seul. Les deux écrans affichent à côté le **plus grand numéro déjà
enregistré**, parce que descendre en dessous fait reproposer des numéros déjà
servis. Ce n'est pas interdit — un nouveau carnet peut légitimement recommencer
plus bas, et le numéro de bon n'est pas unique — mais le choix doit être fait
en connaissance de cause.

Un numéro de départ plus bas **tient** : le recalage automatique ne regarde que
les bons qui viennent d'être créés, jamais tout le registre. Régler le départ à
100 alors qu'un bon 6300 existe donne bien 100, puis 101, 102…

### Profils

| Groupe | Droits |
|---|---|
| **Secrétaire** | Saisie des bons, import, export, impression, gestion des engins et des bénéficiaires. Pas de suppression. |
| **Responsable du secrétariat** | Idem + suppression des bons et configuration des types de carburant. |

---

## Tests

153 tests, répartis en neuf fichiers. Ils partent tous de `SecretariatCase`
(`tests/common.py`), qui **vide le registre des bons** avant chaque classe :
plusieurs écrans agrègent toute la base (bons à compléter, export « toute la
période », état du mois), des tests qui comptent des enregistrements ne
tiendraient donc que sur une base vierge. Grâce à cet isolement, la suite peut
être lancée sur une base déjà peuplée — vérifié sur les 1 211 bons du classeur
repris. Rien n'est détruit : Odoo annule la transaction à la fin.

| Fichier | Couvre |
|---|---|
| `test_fuel_voucher.py` | Calculs, verrou de validation, garde-fous et avertissements de saisie |
| `test_fuel_import_export.py` | Modèle vierge, import tolérant et idempotent, échappement du compte rendu, export et aller-retour |
| `test_fuel_quota.py` | Dotation mensuelle, filtre de dépassement, avertissement |
| `test_reports.py` | Rendu des deux rapports et données de l'état mensuel |
| `test_dashboard.py` | Structure du retour, périodes, variations, compteurs, doublons, anomalies, tendance |
| `test_merge.py` | Rapprochement des libellés (dont le piège des immatriculations) et fusion |
| `test_fuel_numbering.py` | Compteur proposé et non consommé, recalage, normalisation, droits du profil secrétaire |
| `test_usage_delay.py` | Deux dates, délais, `usage_alert` et sa recherche, statistiques et recommandations |
| `test_fuel_invoice.py` | Quinzaines, collecte, verrous, écarts, index unique, assistants, états PDF et Excel |

```bash
# Depuis la racine du projet. Le conteneur jetable évite le conflit
# « concurrent update » avec le serveur principal ; arrêter celui-ci pour un
# premier `-i`, le relancer ensuite.
docker compose -f docker-compose.dev.yml run --rm --no-deps web \
    odoo -d test_secretariat -u ivorycocoa_secretariat \
    --addons-path=/usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons,/mnt/oca-addons \
    --db_host=db --db_user=odoo --db_password=odoo \
    --test-tags=/ivorycocoa_secretariat --http-port=8099 \
    --stop-after-init --log-level=info
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
