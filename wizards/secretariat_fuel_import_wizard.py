# -*- coding: utf-8 -*-
"""Import du classeur Excel des bons de carburant.

Le classeur repris (« yasmina_petroleum.xlsx ») est tenu à la main : une
feuille par période, des lignes de total en bas de feuille, des orthographes
flottantes (« Gasoil » / « gasoil », « Gaz » / « GAZ » / « gaz »), quelques
dates saisies en texte, une cellule « #VALUE! », et surtout des feuilles
recopiées d'un mois sur l'autre — 165 des 1 377 lignes du classeur analysé
sont des doublons stricts.

L'import est donc conçu pour être tolérant ET idempotent :

* détection de la ligne d'en-tête et des colonnes par leur libellé ;
* lignes sans numéro de bon ignorées (ce sont les totaux de bas de feuille) ;
* dates au séparateur oublié (« 25/022026 ») reconstituées, avec une note sur
  le bon créé ;
* prix illisible remplacé par le prix courant du carburant, également signalé ;
* dédoublonnage dans le fichier ET vis-à-vis des bons déjà en base ;
* les lignes rejetées sont listées et téléchargeables en CSV, aucune ligne
  n'est perdue silencieusement.

Sur le classeur de référence (1 377 lignes, 15 feuilles) : 1 376 lignes
exploitables, 165 doublons écartés, 1 211 bons créés, 1 ligne rejetée (date
réellement absente).
"""

import base64
import csv
import io
import logging
from datetime import date, datetime, timedelta

from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.secretariat_fuel_type import normalize_label

_logger = logging.getLogger(__name__)

try:
    import openpyxl
except ImportError:  # pragma: no cover - dépendance déclarée au manifeste
    openpyxl = None

# Correspondance libellé de colonne (normalisé) -> clé interne
_COLUMN_ALIASES = {
    'numero': 'name',
    'num': 'name',
    'n bon': 'name',
    'numero du bon': 'name',
    'date': 'date',
    'engin': 'vehicle',
    'vehicule': 'vehicle',
    'nom': 'beneficiary',
    'beneficiaire': 'beneficiary',
    'carburant': 'fuel',
    'produit': 'fuel',
    'prix unitaire': 'price',
    'prix': 'price',
    'pu': 'price',
    'quantite': 'quantity',
    'qte': 'quantity',
    'total': 'total',
    'montant': 'total',
}
_REQUIRED_COLUMNS = ('name', 'date', 'fuel', 'quantity')

# Origine des numéros de série de dates Excel (système 1900, décalage inclus)
_EXCEL_EPOCH = datetime(1899, 12, 30)
# Taille maximale du classeur accepté (le classeur de référence pèse ~200 Ko)
_MAX_FILE_SIZE = 25 * 1024 * 1024
# Écart toléré entre le total du fichier et prix × quantité
_TOTAL_TOLERANCE = 1.0
# Nombre de lignes scannées à la recherche de l'en-tête
_HEADER_SCAN_ROWS = 10


class _LabelResolver:
    """Donne à chaque libellé du fichier un identifiant stable.

    La clé de dédoublonnage d'un bon contient l'identifiant de l'engin et du
    carburant. Il faut donc pouvoir la calculer AVANT toute création de
    référentiel — sinon l'import crée des engins pour des lignes qu'il écarte
    ensuite comme doublons.

    * un libellé connu prend l'identifiant réel du référentiel : la clé se
      compare alors directement à celles des bons déjà en base ;
    * un libellé inconnu prend un identifiant négatif, unique et stable : deux
      engins inconnus DIFFÉRENTS ne sont pas confondus, et aucun bon existant
      ne peut porter cet identifiant ;
    * un libellé vide vaut 0, comme un engin absent en base.
    """

    def __init__(self, known_ids):
        self._known = known_ids
        self._unknown = {}
        self._next = -1

    def key_id(self, label):
        normalized = normalize_label(label)
        if not normalized:
            return 0
        if normalized in self._known:
            return self._known[normalized]
        if normalized not in self._unknown:
            self._unknown[normalized] = self._next
            self._next -= 1
        return self._unknown[normalized]


class SecretariatFuelImportSheet(models.TransientModel):
    """Une feuille du classeur, telle que proposée à l'import."""

    _name = 'secretariat.fuel.import.sheet'
    _description = "Feuille du classeur à importer"
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'secretariat.fuel.import.wizard', required=True, ondelete='cascade')
    sequence = fields.Integer(string="Ordre")
    name = fields.Char(string="Feuille", readonly=True)
    selected = fields.Boolean(string="Importer", default=True)
    row_count = fields.Integer(string="Lignes de bon", readonly=True)
    new_count = fields.Integer(
        string="Nouvelles", readonly=True,
        help="Lignes qui seront créées : ni déjà en base, ni en doublon dans "
             "le fichier.")
    duplicate_count = fields.Integer(
        string="Doublons", readonly=True,
        help="Lignes déjà présentes en base ou déjà vues dans une feuille "
             "précédente du même fichier.")
    error_count = fields.Integer(string="En erreur", readonly=True)


class SecretariatFuelImportWizard(models.TransientModel):
    _name = 'secretariat.fuel.import.wizard'
    _description = "Import des bons de carburant depuis Excel"

    state = fields.Selection(
        [('upload', 'Fichier'), ('analyzed', 'Analyse'), ('done', 'Résultat')],
        default='upload',
        required=True,
    )

    file_data = fields.Binary(string="Fichier Excel (.xlsx)", attachment=False)
    file_name = fields.Char(string="Nom du fichier")

    sheet_ids = fields.One2many(
        'secretariat.fuel.import.sheet', 'wizard_id', string="Feuilles")

    create_missing = fields.Boolean(
        string="Créer les référentiels manquants",
        default=True,
        help="Crée automatiquement les engins, bénéficiaires et types de "
             "carburant rencontrés dans le fichier et absents de la base.",
    )
    skip_duplicates = fields.Boolean(
        string="Ignorer les doublons",
        default=True,
        help="Un bon est considéré comme déjà importé s'il a le même numéro, "
             "la même date, le même engin, le même carburant et la même "
             "quantité. À décocher uniquement pour forcer un ré-import.",
    )
    target_state = fields.Selection(
        [('draft', 'Brouillon'), ('confirmed', 'Validé')],
        string="État des bons importés",
        default='confirmed',
        required=True,
        help="Les bons repris de l'ancien classeur ont déjà été honorés : "
             "« Validé » est l'état attendu.",
    )
    supplier_id = fields.Many2one(
        'res.partner', string="Station",
        domain="[('is_company', '=', True)]",
        help="Station-service appliquée à tous les bons importés (facultatif).")

    # --- Résultat ---
    imported_count = fields.Integer(string="Bons créés", readonly=True)
    duplicate_count = fields.Integer(string="Doublons ignorés", readonly=True)
    error_count = fields.Integer(string="Lignes en erreur", readonly=True)
    created_vehicle_count = fields.Integer(string="Engins créés", readonly=True)
    created_beneficiary_count = fields.Integer(string="Bénéficiaires créés", readonly=True)
    created_fuel_count = fields.Integer(string="Carburants créés", readonly=True)
    result_html = fields.Html(string="Compte rendu", readonly=True, sanitize=False)
    error_file = fields.Binary(string="Lignes rejetées (CSV)", readonly=True)
    error_file_name = fields.Char(readonly=True)

    # =========================================================================
    # ÉTAPE 1 — ANALYSE
    # =========================================================================

    def action_analyze(self):
        """Ouvre le classeur et propose ses feuilles, avec un décompte.

        Le décompte est calculé exactement comme le fera l'import : même
        lecture, même clé de dédoublonnage. Ce que l'écran annonce est donc ce
        que l'import produira.
        """
        self.ensure_one()
        workbook = self._load_workbook()

        parsed = []
        for index, sheet in enumerate(workbook.worksheets):
            rows, errors = self._parse_sheet(sheet)
            parsed.append((index, sheet.title, rows, errors))

        all_rows = [row for _i, _t, rows, _e in parsed for row in rows]
        existing = self._existing_keys(self._date_bounds(all_rows))
        resolvers = self._label_resolvers()
        seen = set()

        lines = [(5, 0, 0)]
        for index, title, rows, errors in parsed:
            new_count = duplicate_count = 0
            for row in rows:
                key = self._row_key(row, resolvers)
                if key in seen or key in existing:
                    duplicate_count += 1
                else:
                    seen.add(key)
                    new_count += 1
            lines.append((0, 0, {
                'sequence': index,
                'name': title,
                'selected': True,
                'row_count': len(rows),
                'new_count': new_count,
                'duplicate_count': duplicate_count,
                'error_count': len(errors),
            }))
        self.write({'sheet_ids': lines, 'state': 'analyzed'})
        return self._reopen()

    def action_back(self):
        """Retour à l'étape « Fichier » sans fermer l'assistant."""
        self.ensure_one()
        self.write({'state': 'upload', 'sheet_ids': [(5, 0, 0)]})
        return self._reopen()

    # =========================================================================
    # ÉTAPE 2 — IMPORT
    # =========================================================================

    def action_import(self):
        self.ensure_one()
        selected = self.sheet_ids.filtered('selected').mapped('name')
        if not selected:
            raise UserError(_("Sélectionnez au moins une feuille à importer."))

        workbook = self._load_workbook()
        Voucher = self.env['secretariat.fuel.voucher']

        # 1. Lecture des feuilles retenues.
        rows, errors = [], []
        for sheet in workbook.worksheets:
            if sheet.title not in selected:
                continue
            sheet_rows, sheet_errors = self._parse_sheet(sheet)
            rows.extend(sheet_rows)
            errors.extend(sheet_errors)

        # 2. Dédoublonnage AVANT toute création de référentiel : une ligne
        #    écartée ne doit pas laisser derrière elle un engin fantôme.
        existing = self._existing_keys(self._date_bounds(rows)) \
            if self.skip_duplicates else set()
        resolvers = self._label_resolvers()
        seen = set()
        kept = []
        duplicates = 0
        for row in rows:
            key = self._row_key(row, resolvers)
            if self.skip_duplicates and (key in seen or key in existing):
                duplicates += 1
                continue
            seen.add(key)
            kept.append(row)

        # 3. Résolution (et création) des référentiels, sur les seules lignes
        #    effectivement reprises.
        referentials = {'vehicle': dict(), 'beneficiary': dict(), 'fuel': dict()}
        created_counts = {'vehicle': 0, 'beneficiary': 0, 'fuel': 0}
        values_list = []
        for row in kept:
            try:
                values_list.append(self._prepare_voucher_values(
                    row, referentials, created_counts))
            except UserError as exc:
                errors.append(dict(row, error=str(exc)))

        vouchers = Voucher.create(values_list) if values_list else Voucher

        self.write({
            'state': 'done',
            'imported_count': len(vouchers),
            'duplicate_count': duplicates,
            'error_count': len(errors),
            'created_vehicle_count': created_counts['vehicle'],
            'created_beneficiary_count': created_counts['beneficiary'],
            'created_fuel_count': created_counts['fuel'],
            'result_html': self._build_report(len(vouchers), duplicates, errors),
            'error_file': self._build_error_file(errors),
            'error_file_name': errors and "bons_carburant_lignes_rejetees.csv" or False,
        })
        _logger.info(
            "Import bons de carburant : %s créés, %s doublons, %s erreurs (%s)",
            len(vouchers), duplicates, len(errors), self.file_name)
        return self._reopen()

    def action_view_imported(self):
        """Ouvre la liste des bons importés depuis ce fichier."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons importés"),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form,pivot,graph',
            'domain': [('imported', '=', True)],
        }

    # =========================================================================
    # LECTURE DU CLASSEUR
    # =========================================================================

    def _load_workbook(self):
        if openpyxl is None:
            raise UserError(_(
                "La bibliothèque Python « openpyxl » est absente du serveur : "
                "l'import Excel est indisponible."))
        if not self.file_data:
            raise UserError(_("Choisissez d'abord un fichier Excel (.xlsx)."))
        if self.file_name and not self.file_name.lower().endswith(('.xlsx', '.xlsm')):
            raise UserError(_(
                "Format non pris en charge : %s. Enregistrez le classeur au "
                "format .xlsx puis relancez l'import.", self.file_name))
        try:
            content = base64.b64decode(self.file_data)
        except Exception:
            raise UserError(_("Le fichier envoyé est illisible. Renvoyez-le."))
        if len(content) > _MAX_FILE_SIZE:
            raise UserError(_(
                "Le classeur dépasse %s Mo. Découpez-le par période et "
                "importez les morceaux l'un après l'autre.",
                _MAX_FILE_SIZE // (1024 * 1024)))
        try:
            return openpyxl.load_workbook(
                io.BytesIO(content), data_only=True, read_only=False)
        except Exception as exc:
            raise UserError(_(
                "Le fichier n'a pas pu être ouvert : %s\n\nVérifiez qu'il "
                "s'agit bien d'un classeur Excel (.xlsx) non protégé.", exc))

    def _parse_sheet(self, sheet):
        """Retourne (lignes exploitables, lignes en erreur) d'une feuille."""
        columns = self._detect_columns(sheet)
        if not columns:
            return [], []

        header_row, mapping = columns
        rows, errors = [], []
        for row_index in range(header_row + 1, sheet.max_row + 1):
            raw = {
                key: sheet.cell(row_index, col).value
                for key, col in mapping.items()
            }
            # Ligne sans numéro : total de bas de feuille ou ligne vide.
            if raw.get('name') in (None, '') or not str(raw['name']).strip():
                continue
            row = {
                'sheet': sheet.title,
                'row': row_index,
                'raw_name': raw.get('name'),
                'raw_date': raw.get('date'),
                'vehicle': self._clean_text(raw.get('vehicle')),
                'beneficiary': self._clean_text(raw.get('beneficiary')),
                'fuel': self._clean_text(raw.get('fuel')),
            }
            row['name'] = self._clean_number_as_text(raw.get('name'))
            row['date'] = self._to_date(raw.get('date'))
            row['date_repaired'] = False
            if row['date'] is None and raw.get('date'):
                row['date'] = self._repair_date(raw['date'])
                row['date_repaired'] = bool(row['date'])
            row['price'] = self._to_float(raw.get('price'))
            row['quantity'] = self._to_float(raw.get('quantity'))
            row['total'] = self._to_float(raw.get('total'))

            if row['date'] is None:
                errors.append(dict(row, error=_("Date illisible ou absente")))
                continue
            if not row['fuel']:
                errors.append(dict(row, error=_("Carburant absent")))
                continue
            if row['quantity'] is None or row['quantity'] <= 0:
                errors.append(dict(row, error=_("Quantité absente ou nulle")))
                continue
            rows.append(row)
        return rows, errors

    def _detect_columns(self, sheet):
        """Localise la ligne d'en-tête et associe chaque colonne à une clé."""
        max_col = min(sheet.max_column or 0, 40)
        for row_index in range(1, min(_HEADER_SCAN_ROWS, sheet.max_row or 0) + 1):
            mapping = {}
            for col in range(1, max_col + 1):
                key = _COLUMN_ALIASES.get(normalize_label(sheet.cell(row_index, col).value))
                if key and key not in mapping:
                    mapping[key] = col
            if all(required in mapping for required in _REQUIRED_COLUMNS):
                return row_index, mapping
        return None

    # =========================================================================
    # CONVERSIONS TOLÉRANTES
    # =========================================================================

    @staticmethod
    def _clean_text(value):
        if value is None:
            return ''
        return str(value).strip()

    @staticmethod
    def _clean_number_as_text(value):
        """Le numéro de bon est un entier dans le fichier — pas « 7138.0 »."""
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value).strip()

    @staticmethod
    def _to_float(value):
        if value is None or value == '':
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        text = str(value).strip().replace(' ', '').replace(' ', '')
        text = text.replace(',', '.')
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _to_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Numéro de série Excel
            try:
                return (_EXCEL_EPOCH + timedelta(days=float(value))).date()
            except (OverflowError, ValueError):
                return None
        if not value:
            return None
        text = str(value).strip()
        for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y',
                    '%d.%m.%Y', '%Y/%m/%d'):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _repair_date(value):
        """Rattrape les dates saisies avec un séparateur manquant.

        Le classeur repris en contient une poignée : « 25/022026 »,
        « 13/03/026 », « 2004/2026 », « 1506/2026 », « 30/072026 ». On ne
        garde que les chiffres et on relit en jour-mois-année ; le bon
        importé porte alors une note signalant la reconstitution.
        """
        digits = ''.join(c for c in str(value) if c.isdigit())
        if len(digits) == 8:
            day, month, year = digits[:2], digits[2:4], int(digits[4:])
        elif len(digits) == 7:
            # Année sur 3 chiffres : « 13/03/026 »
            day, month, year = digits[:2], digits[2:4], 2000 + int(digits[4:])
        elif len(digits) == 6:
            day, month, year = digits[:2], digits[2:4], 2000 + int(digits[4:])
        else:
            return None
        try:
            return date(year, int(month), int(day))
        except ValueError:
            # Dernière tentative : mois et jour intervertis
            try:
                return date(year, int(day), int(month))
            except ValueError:
                return None

    # =========================================================================
    # PRÉPARATION DES ENREGISTREMENTS
    # =========================================================================

    def _prepare_voucher_values(self, row, referentials, created_counts):
        fuel_type = self._resolve(
            'fuel', row['fuel'], referentials, created_counts,
            self.env['secretariat.fuel.type'])
        if not fuel_type:
            raise UserError(_(
                "Carburant « %s » inconnu (création des référentiels "
                "désactivée)", row['fuel']))

        vehicle = self._resolve(
            'vehicle', row['vehicle'], referentials, created_counts,
            self.env['secretariat.vehicle']) if row['vehicle'] else None
        beneficiary = self._resolve(
            'beneficiary', row['beneficiary'], referentials, created_counts,
            self.env['secretariat.fuel.beneficiary']) if row['beneficiary'] else None

        price = row['price']
        notes = []
        if row.get('date_repaired'):
            notes.append(_(
                "Date reconstituée à partir de la saisie « %(raw)s » du "
                "fichier — à vérifier.", raw=row['raw_date']))
        if price is None or price <= 0:
            price = fuel_type.price
            notes.append(_(
                "Prix unitaire absent du fichier — prix courant du carburant "
                "appliqué (%s).", price))
        computed_total = price * row['quantity']
        if row['total'] is not None and abs(row['total'] - computed_total) > _TOTAL_TOLERANCE:
            notes.append(_(
                "Total du fichier (%(file)s) différent de prix × quantité "
                "(%(computed)s) — c'est le calcul qui fait foi.",
                file=row['total'], computed=computed_total))

        return {
            'name': row['name'],
            'date': row['date'],
            'vehicle_id': vehicle.id if vehicle else False,
            'beneficiary_id': beneficiary.id if beneficiary else False,
            'fuel_type_id': fuel_type.id,
            'price_unit': price,
            'quantity': row['quantity'],
            'state': self.target_state,
            'supplier_id': self.supplier_id.id or False,
            'imported': True,
            'import_source': "%s — %s (ligne %s)" % (
                self.file_name or _("classeur"), row['sheet'], row['row']),
            'note': "\n".join(notes) or False,
        }

    def _resolve(self, kind, label, referentials, created_counts, model):
        """Retrouve (ou crée) un référentiel, avec un cache par libellé."""
        key = normalize_label(label)
        if not key:
            return None
        cache = referentials[kind]
        if key in cache:
            return cache[key]
        record = model.with_context(active_test=False).search(
            [('normalized_name', '=', key)], limit=1)
        if not record and self.create_missing:
            record = model.find_or_create(label)
            created_counts[kind] += 1
        cache[key] = record or None
        return cache[key]

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _date_bounds(rows):
        """(date la plus ancienne, date la plus récente) des lignes lues."""
        dates = [row['date'] for row in rows if row.get('date')]
        return (min(dates), max(dates)) if dates else None

    def _existing_keys(self, bounds=None):
        """Clés de dédoublonnage des bons déjà en base.

        Bornées à la plage de dates du fichier : inutile de charger dix ans de
        registre pour importer une feuille de mars.
        """
        domain = []
        if bounds:
            domain = [('date', '>=', bounds[0]), ('date', '<=', bounds[1])]
        keys = set()
        Voucher = self.env['secretariat.fuel.voucher']
        for record in Voucher.search_read(
                domain, ['name', 'date', 'vehicle_id', 'fuel_type_id', 'quantity']):
            keys.add(Voucher._dedup_key(
                record['name'], record['date'],
                record['vehicle_id'] and record['vehicle_id'][0],
                record['fuel_type_id'] and record['fuel_type_id'][0],
                record['quantity']))
        return keys

    def _label_resolvers(self):
        """Un résolveur de libellés par référentiel entrant dans la clé."""
        resolvers = {}
        for kind, model_name in (('vehicle', 'secretariat.vehicle'),
                                 ('fuel', 'secretariat.fuel.type')):
            records = self.env[model_name].with_context(active_test=False).search_read(
                [], ['normalized_name'])
            resolvers[kind] = _LabelResolver(
                {record['normalized_name']: record['id'] for record in records})
        return resolvers

    def _row_key(self, row, resolvers):
        """Clé de dédoublonnage d'une ligne du fichier, avant création."""
        return self.env['secretariat.fuel.voucher']._dedup_key(
            row['name'],
            row['date'],
            resolvers['vehicle'].key_id(row['vehicle']),
            resolvers['fuel'].key_id(row['fuel']),
            row['quantity'],
        )

    def _build_report(self, imported, duplicates, errors):
        """Compte rendu HTML.

        Tout ce qui vient du classeur (nom de feuille, message d'erreur) est
        échappé : le champ est déclaré ``sanitize=False`` pour préserver la
        mise en forme, il ne doit donc rien recevoir de brut.
        """
        parts = [Markup('<div class="o_secretariat_import_report">')]
        parts.append(Markup('<p><strong>%s</strong> bon(s) créé(s).</p>') % imported)
        if duplicates:
            parts.append(Markup(
                '<p>%s ligne(s) ignorée(s) car déjà présentes en base ou en '
                'double dans le fichier.</p>') % duplicates)
        created = []
        if self.created_vehicle_count:
            created.append(_("%s engin(s)", self.created_vehicle_count))
        if self.created_beneficiary_count:
            created.append(_("%s bénéficiaire(s)", self.created_beneficiary_count))
        if self.created_fuel_count:
            created.append(_("%s type(s) de carburant", self.created_fuel_count))
        if created:
            parts.append(Markup('<p>Référentiels créés : %s.</p>')
                         % escape(", ".join(created)))
        if errors:
            parts.append(Markup(
                '<p class="text-danger"><strong>%s ligne(s) rejetée(s)</strong> — '
                'téléchargez le CSV ci-dessous pour les corriger puis relancez '
                "l'import (les lignes déjà reprises ne seront pas dupliquées).</p>")
                % len(errors))
            parts.append(Markup('<ul>'))
            for error in errors[:10]:
                parts.append(Markup('<li>%s, ligne %s : %s</li>') % (
                    escape(error.get('sheet') or ''),
                    error.get('row') or '',
                    escape(error.get('error') or '')))
            parts.append(Markup('</ul>'))
            if len(errors) > 10:
                parts.append(Markup('<p><em>… et %s autre(s).</em></p>')
                             % (len(errors) - 10))
        parts.append(Markup('</div>'))
        return Markup('').join(parts)

    def _build_error_file(self, errors):
        if not errors:
            return False
        output = io.StringIO()
        writer = csv.writer(output, delimiter=';')
        writer.writerow(["Feuille", "Ligne", "Numéro", "Date", "Engin", "Nom",
                         "Carburant", "Prix", "Quantité", "Total", "Motif"])
        for error in errors:
            writer.writerow([
                error.get('sheet'), error.get('row'), error.get('raw_name'),
                error.get('raw_date'), error.get('vehicle'),
                error.get('beneficiary'), error.get('fuel'),
                error.get('price'), error.get('quantity'), error.get('total'),
                error.get('error'),
            ])
        return base64.b64encode(output.getvalue().encode('utf-8-sig'))

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    @api.onchange('file_data')
    def _onchange_file_data(self):
        """Un nouveau fichier remet l'assistant au point de départ."""
        if self.state != 'upload':
            self.state = 'upload'
            self.sheet_ids = [(5, 0, 0)]
