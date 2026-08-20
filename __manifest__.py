# -*- coding: utf-8 -*-
{
    'name': 'Secrétariat',
    'version': '17.0.2.2.1',
    'category': 'Administration',
    'summary': "Outils du secrétariat — tableau de bord, bons de carburant, et fonctionnalités à venir",
    'description': """
Secrétariat
===========

Module fourre-tout du secrétariat de direction. Les fonctionnalités sont
ajoutées au fur et à mesure des besoins ; chacune vit dans son propre
sous-menu et apporte sa section au tableau de bord.

Tableau de bord
---------------
Écran d'accueil du secrétariat (composant Owl) : ce qu'il reste à traiter,
les chiffres de la période comparés à la précédente, la tendance sur douze
mois, les palmarès, le suivi des dotations, les consommations inhabituelles,
les derniers bons saisis et les tarifs courants — avec les actions du
quotidien à portée de clic.

Fonctionnalité 1 — Bons de carburant
------------------------------------
Reprend et remplace le classeur Excel tenu à la main (« yasmina_petroleum.xlsx ») :

* Registre des bons de carburant : numéro du bon, date, engin, bénéficiaire,
  type de carburant, prix unitaire, quantité et total (calculé).
* Saisie à la chaîne en liste éditable, avec avertissements non bloquants
  (bon déjà saisi, date à venir, dotation mensuelle dépassée).
* Référentiels : engins, bénéficiaires (employés, services, tiers) et types de
  carburant avec leur prix courant.
* **Dotation mensuelle par engin** (quantité et/ou montant) : suivi de la
  consommation, filtre « en dépassement », pastille au tableau de bord.
* **Import Excel** du classeur existant : détection de l'en-tête, tolérance aux
  variantes d'orthographe des carburants, création automatique des
  référentiels manquants, dédoublonnage (le classeur d'origine recopie les
  feuilles d'un mois sur l'autre) et journal détaillé des lignes ignorées.
* **Export Excel** au format identique au classeur d'origine : une feuille par
  mois, mêmes colonnes, plus une feuille de synthèse optionnelle.
* **Rapports PDF** : le bon lui-même (A5, avec signatures) et l'état
  récapitulatif mensuel à faire viser par la direction.
* Analyse : vues liste / kanban / pivot / graphique pour suivre la
  consommation par engin, par bénéficiaire, par carburant et par mois.

Profils
-------

* **Secrétaire** : saisie et import/export des bons.
* **Responsable du secrétariat** : idem + suppression et configuration.
    """,
    'author': 'ICP',
    'website': 'https://www.ivorycocoa.ci',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'mail',
        'hr',
    ],
    'external_dependencies': {
        'python': ['openpyxl'],
    },
    'data': [
        # Security
        'security/secretariat_security.xml',
        'security/ir.model.access.csv',
        # Data
        'data/secretariat_fuel_data.xml',
        # Reports (les actions sont référencées par les vues)
        'report/secretariat_report_actions.xml',
        'report/secretariat_fuel_voucher_templates.xml',
        'report/secretariat_fuel_monthly_templates.xml',
        # Views
        'views/secretariat_fuel_type_views.xml',
        'views/secretariat_vehicle_views.xml',
        'views/secretariat_beneficiary_views.xml',
        'views/secretariat_fuel_voucher_views.xml',
        'views/secretariat_dashboard_views.xml',
        # Wizards
        'wizards/secretariat_fuel_import_wizard_views.xml',
        'wizards/secretariat_fuel_export_wizard_views.xml',
        'wizards/secretariat_fuel_monthly_report_wizard_views.xml',
        'wizards/secretariat_merge_wizard_views.xml',
        # Menus (en dernier : référencent les actions ci-dessus)
        'views/secretariat_menu_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'ivorycocoa_secretariat/static/src/css/secretariat_dashboard.css',
            'ivorycocoa_secretariat/static/src/js/secretariat_dashboard.js',
            'ivorycocoa_secretariat/static/src/xml/secretariat_dashboard.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
}
