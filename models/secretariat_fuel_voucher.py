# -*- coding: utf-8 -*-
"""Bon de carburant.

Reprend une ligne du classeur tenu par le secrétariat :

    Numéro | Date | Engin | Nom | Carburant | Prix Unitaire | Quantité | Total

Deux partis pris issus de l'analyse du classeur existant :

* **Engin et bénéficiaire ne sont pas obligatoires en base.** Le classeur
  repris comporte des lignes sans engin ou sans nom ; les rendre obligatoires
  ferait échouer l'import de l'historique. Ces bons sont marqués « incomplet »
  et un filtre dédié permet à la secrétaire de les compléter.
* **Le numéro n'est pas unique.** Le même numéro de souche revient sur
  plusieurs lignes (un bon Super + un bon Lubrifiant le même jour, carnets
  différents…). L'unicité serait fausse ; le dédoublonnage de l'import se fait
  sur la combinaison numéro + date + engin + carburant + quantité.

Le second parti pris a un revers : rien n'empêche la re-saisie accidentelle du
même bon. Plutôt qu'une contrainte fausse, la saisie déclenche un
**avertissement non bloquant** quand un bon du même numéro existe déjà à la
même date.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare, float_is_zero

# Mois en français, indexés de 1 à 12 — utilisés pour nommer les feuilles de
# l'export au même format que le classeur d'origine (« Janvier 2026 »).
MONTH_NAMES_FR = [
    None,
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

# Champs verrouillés par la validation du bon.
LOCKED_FIELDS = ('name', 'date', 'vehicle_id', 'beneficiary_id',
                 'fuel_type_id', 'price_unit', 'quantity')


class SecretariatFuelVoucher(models.Model):
    _name = 'secretariat.fuel.voucher'
    _description = "Bon de carburant"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string="Numéro du bon",
        required=True,
        tracking=True,
        index=True,
        help="Numéro imprimé sur la souche du bon.",
    )
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
        index=True,
    )
    vehicle_id = fields.Many2one(
        'secretariat.vehicle',
        string="Engin",
        tracking=True,
        index=True,
        ondelete='restrict',
    )
    beneficiary_id = fields.Many2one(
        'secretariat.fuel.beneficiary',
        string="Bénéficiaire",
        tracking=True,
        index=True,
        ondelete='restrict',
    )
    fuel_type_id = fields.Many2one(
        'secretariat.fuel.type',
        string="Carburant",
        required=True,
        tracking=True,
        index=True,
        ondelete='restrict',
    )
    uom_label = fields.Selection(
        related='fuel_type_id.uom_label', string="Unité", readonly=True)

    price_unit = fields.Float(
        string="Prix unitaire",
        digits='Product Price',
        tracking=True,
        help="Prix appliqué à ce bon. Pré-rempli avec le prix courant du "
             "carburant, il reste modifiable et n'est plus recalculé ensuite.",
    )
    quantity = fields.Float(
        string="Quantité",
        digits='Product Unit of Measure',
        tracking=True,
    )
    amount_total = fields.Monetary(
        string="Total",
        compute='_compute_amount_total',
        store=True,
        currency_field='currency_id',
        tracking=True,
    )

    company_id = fields.Many2one(
        'res.company', string="Société",
        required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', string="Devise",
        related='company_id.currency_id', readonly=True)
    supplier_id = fields.Many2one(
        'res.partner',
        string="Station",
        domain="[('is_company', '=', True)]",
        help="Station-service auprès de laquelle le bon est honoré.",
    )

    state = fields.Selection(
        [
            ('draft', 'Brouillon'),
            ('confirmed', 'Validé'),
            ('cancelled', 'Annulé'),
        ],
        string="État",
        default='draft',
        required=True,
        tracking=True,
        index=True,
        copy=False,
    )

    is_incomplete = fields.Boolean(
        string="Incomplet",
        compute='_compute_is_incomplete',
        store=True,
        help="Le bon manque d'un engin, d'un bénéficiaire, d'un prix ou d'une "
             "quantité — typiquement une ligne reprise de l'ancien classeur.",
    )
    imported = fields.Boolean(
        string="Importé",
        default=False,
        copy=False,
        readonly=True,
        help="Bon créé par l'import du classeur Excel.",
    )
    import_source = fields.Char(
        string="Origine de l'import",
        copy=False,
        readonly=True,
        help="Fichier et feuille d'origine, pour retrouver la ligne source.",
    )
    note = fields.Text(string="Observations")

    # =========================================================================
    # INDEX
    # =========================================================================

    def init(self):
        """Index de dédoublonnage.

        L'import compare chaque ligne du classeur à la base sur
        numéro + date ; l'avertissement de re-saisie fait la même recherche à
        chaque frappe. Un index composite évite le parcours séquentiel dès que
        le registre dépasse quelques milliers de bons.
        """
        super().init()
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS secretariat_fuel_voucher_dedup_idx
            ON secretariat_fuel_voucher (name, date)
        """)

    # =========================================================================
    # CALCULS
    # =========================================================================

    @api.depends('price_unit', 'quantity')
    def _compute_amount_total(self):
        for voucher in self:
            voucher.amount_total = (voucher.price_unit or 0.0) * (voucher.quantity or 0.0)

    @api.depends('vehicle_id', 'beneficiary_id', 'price_unit', 'quantity')
    def _compute_is_incomplete(self):
        for voucher in self:
            voucher.is_incomplete = not (
                voucher.vehicle_id
                and voucher.beneficiary_id
                and voucher.price_unit > 0
                and voucher.quantity > 0
            )

    # =========================================================================
    # ONCHANGE
    # =========================================================================

    @api.onchange('fuel_type_id')
    def _onchange_fuel_type_id(self):
        """Aligne le prix sur le carburant choisi.

        Le prix n'est remplacé que s'il est vide ou s'il correspond encore au
        tarif courant d'un autre carburant : une correction de carburant en
        cours de saisie (« Super » rectifié en « Gasoil ») ne doit pas laisser
        l'ancien prix, mais un prix saisi à la main doit être respecté.
        """
        if not self.fuel_type_id:
            return
        rounding = 0.01
        proposed_prices = self.env['secretariat.fuel.type'].with_context(
            active_test=False).search([]).mapped('price')
        keeps_manual_price = self.price_unit and not any(
            float_is_zero(self.price_unit - price, precision_rounding=rounding)
            for price in proposed_prices)
        if not keeps_manual_price:
            self.price_unit = self.fuel_type_id.price

    @api.onchange('vehicle_id')
    def _onchange_vehicle_id(self):
        """Propose le bénéficiaire habituel de l'engin."""
        if self.vehicle_id and self.vehicle_id.employee_id and not self.beneficiary_id:
            beneficiary = self.env['secretariat.fuel.beneficiary'].search(
                [('employee_id', '=', self.vehicle_id.employee_id.id)], limit=1)
            if beneficiary:
                self.beneficiary_id = beneficiary

    @api.onchange('name', 'date')
    def _onchange_check_duplicate(self):
        """Signale une re-saisie probable — sans jamais l'interdire.

        Le numéro de bon n'est pas unique (cf. docstring du modèle), une
        contrainte serait fausse. L'avertissement, lui, attrape le cas
        courant : le même bon saisi deux fois.
        """
        if not self.name or not self.date:
            return
        domain = [
            ('name', '=', self.name.strip()),
            ('date', '=', self.date),
            ('state', '!=', 'cancelled'),
        ]
        if self._origin.id:
            domain.append(('id', '!=', self._origin.id))
        twin = self.search(domain, limit=1)
        if twin:
            return {'warning': {
                'title': _("Bon déjà enregistré ?"),
                'message': _(
                    "Un bon n° %(name)s du %(date)s existe déjà "
                    "(%(vehicle)s — %(fuel)s, %(quantity)s).\n\n"
                    "Ce n'est pas forcément une erreur : la même souche peut "
                    "couvrir deux carburants. Vérifiez simplement qu'il ne "
                    "s'agit pas d'une double saisie.",
                    name=twin.name,
                    date=format_date_fr(self.env, twin.date),
                    vehicle=twin.vehicle_id.name or _("engin non précisé"),
                    fuel=twin.fuel_type_id.name or _("carburant non précisé"),
                    quantity=twin.quantity,
                ),
            }}

    @api.onchange('date')
    def _onchange_date_in_future(self):
        if self.date and self.date > fields.Date.context_today(self):
            return {'warning': {
                'title': _("Date à venir"),
                'message': _(
                    "La date du bon (%s) est postérieure à aujourd'hui. "
                    "Vérifiez la saisie : une inversion de chiffres dans "
                    "l'année est vite arrivée.", format_date_fr(self.env, self.date)),
            }}

    @api.onchange('vehicle_id', 'quantity', 'price_unit', 'date')
    def _onchange_check_quota(self):
        """Avertit quand le bon fait franchir la dotation mensuelle de l'engin."""
        vehicle = self.vehicle_id
        if not vehicle or not self.date or not self.quantity:
            return
        if vehicle.monthly_quota <= 0 and vehicle.monthly_budget <= 0:
            return
        start = self.date.replace(day=1)
        end = start + relativedelta(months=1, days=-1)
        domain = [
            ('vehicle_id', '=', vehicle.id),
            ('date', '>=', start), ('date', '<=', end),
            ('state', '!=', 'cancelled'),
        ]
        if self._origin.id:
            domain.append(('id', '!=', self._origin.id))
        siblings = self.search(domain)
        quantity = sum(siblings.mapped('quantity')) + (self.quantity or 0.0)
        amount = sum(siblings.mapped('amount_total')) + \
            (self.quantity or 0.0) * (self.price_unit or 0.0)

        exceeded = []
        if vehicle.monthly_quota > 0 and quantity > vehicle.monthly_quota:
            exceeded.append(_(
                "quantité : %(used)s sur %(quota)s alloués",
                used=round(quantity, 2), quota=round(vehicle.monthly_quota, 2)))
        if vehicle.monthly_budget > 0 and amount > vehicle.monthly_budget:
            exceeded.append(_(
                "montant : %(used)s sur %(budget)s alloués",
                used=round(amount), budget=round(vehicle.monthly_budget)))
        if exceeded:
            return {'warning': {
                'title': _("Plafond mensuel dépassé"),
                'message': _(
                    "Avec ce bon, %(vehicle)s dépasse sa dotation de "
                    "%(month)s (%(details)s).\n\n"
                    "Le bon reste enregistrable — le secrétariat constate ce "
                    "qui a été servi, il ne l'autorise pas.",
                    vehicle=vehicle.name,
                    month=month_label_fr(start),
                    details=" ; ".join(exceeded)),
            }}

    # =========================================================================
    # CONTRAINTES
    # =========================================================================

    @api.constrains('quantity', 'price_unit')
    def _check_amounts(self):
        for voucher in self:
            if voucher.quantity < 0:
                raise ValidationError(_("La quantité ne peut pas être négative."))
            if voucher.price_unit < 0:
                raise ValidationError(_("Le prix unitaire ne peut pas être négatif."))

    @api.constrains('date')
    def _check_date_is_plausible(self):
        """Garde-fou de dernier recours contre les dates aberrantes.

        L'avertissement de saisie couvre le cas courant ; cette contrainte
        arrête les fautes de frappe grossières (« 2062 » pour « 2026 ») qui
        fausseraient durablement toutes les statistiques.
        """
        limit = fields.Date.context_today(self) + relativedelta(years=1)
        for voucher in self:
            if voucher.date and voucher.date > limit:
                raise ValidationError(_(
                    "La date du bon %(name)s (%(date)s) est trop lointaine — "
                    "plus d'un an dans le futur. Corrigez la saisie.",
                    name=voucher.name, date=voucher.date))

    # =========================================================================
    # CRUD
    # =========================================================================

    @api.model_create_multi
    def create(self, vals_list):
        """Complète le prix depuis le référentiel quand il n'est pas fourni."""
        for vals in vals_list:
            if not vals.get('price_unit') and vals.get('fuel_type_id'):
                fuel_type = self.env['secretariat.fuel.type'].browse(vals['fuel_type_id'])
                vals['price_unit'] = fuel_type.price
            if vals.get('name'):
                vals['name'] = str(vals['name']).strip()
        return super().create(vals_list)

    def write(self, vals):
        """Un bon validé est figé — mais seulement sur un changement réel.

        Comparer les valeurs avant de refuser évite de bloquer les écritures
        idempotentes : édition multiple sur une colonne déjà à la bonne valeur,
        re-sauvegarde d'un formulaire non modifié, recalculs de l'ORM.
        """
        if vals.get('name'):
            vals['name'] = str(vals['name']).strip()
        touched = set(LOCKED_FIELDS) & set(vals)
        if touched:
            locked = self.filtered(
                lambda v: v.state == 'confirmed' and v._has_real_change(vals, touched))
            if locked:
                raise ValidationError(_(
                    "Le bon %(name)s est validé : repassez-le en brouillon pour "
                    "le modifier.", name=", ".join(locked.mapped('name')[:5]),
                ))
        return super().write(vals)

    def _has_real_change(self, vals, field_names):
        """Vrai si l'une des valeurs écrites diffère de la valeur en base."""
        self.ensure_one()
        for field_name in field_names:
            field = self._fields[field_name]
            new_value = vals[field_name]
            current = self[field_name]
            if field.type == 'many2one':
                if (current.id or False) != (new_value or False):
                    return True
            elif field.type == 'float':
                if float_compare(current or 0.0, new_value or 0.0,
                                 precision_rounding=0.000001) != 0:
                    return True
            elif field.type == 'date':
                if current != fields.Date.to_date(new_value):
                    return True
            elif (current or '') != (new_value or ''):
                return True
        return False

    def unlink(self):
        confirmed = self.filtered(lambda v: v.state == 'confirmed')
        if confirmed:
            raise ValidationError(_(
                "Impossible de supprimer un bon validé (%(name)s). Annulez-le "
                "d'abord.", name=", ".join(confirmed.mapped('name')[:5]),
            ))
        return super().unlink()

    def copy_data(self, default=None):
        """Un bon dupliqué repart en brouillon, sans marque d'import."""
        default = dict(default or {})
        default.setdefault('date', fields.Date.context_today(self))
        return super().copy_data(default)

    # =========================================================================
    # WORKFLOW
    # =========================================================================

    def action_confirm(self):
        for voucher in self:
            if voucher.state != 'draft':
                continue
            if not voucher.fuel_type_id or voucher.quantity <= 0:
                raise ValidationError(_(
                    "Le bon %s doit avoir un carburant et une quantité "
                    "strictement positive avant d'être validé.", voucher.name))
            if voucher.price_unit <= 0:
                raise ValidationError(_(
                    "Le bon %s n'a pas de prix unitaire : le montant serait "
                    "nul et fausserait les statistiques.", voucher.name))
            voucher.state = 'confirmed'
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_draft(self):
        self.write({'state': 'draft'})
        return True

    # =========================================================================
    # ACTIONS D'ÉCRAN
    # =========================================================================

    @api.model
    def action_open_import_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Importer le classeur Excel"),
            'res_model': 'secretariat.fuel.import.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    @api.model
    def action_open_export_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Générer le classeur Excel"),
            'res_model': 'secretariat.fuel.export.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_print(self):
        """Imprime le bon.

        ``config=False`` : le bon utilise ``web.basic_layout`` et dessine son
        propre en-tête. Sans cela, Odoo intercalerait l'assistant de choix de
        mise en page tant que la société n'en a pas configuré une — sans le
        moindre effet sur ce document.
        """
        self.ensure_one()
        return self.env.ref(
            'ivorycocoa_secretariat.action_report_fuel_voucher'
        ).report_action(self, config=False)

    # =========================================================================
    # HELPERS PARTAGÉS AVEC LES ASSISTANTS
    # =========================================================================

    def dedup_key(self):
        """Clé de dédoublonnage d'un bon (cf. docstring du modèle)."""
        self.ensure_one()
        return self._dedup_key(
            self.name, self.date, self.vehicle_id.id,
            self.fuel_type_id.id, self.quantity)

    @staticmethod
    def _dedup_key(name, date, vehicle_id, fuel_type_id, quantity):
        return (
            (name or '').strip().lower(),
            str(date or ''),
            vehicle_id or 0,
            fuel_type_id or 0,
            round(quantity or 0.0, 3),
        )

    def month_label(self):
        """« Janvier 2026 » — libellé de regroupement mensuel du bon."""
        self.ensure_one()
        return month_label_fr(self.date)


def month_label_fr(value):
    """« Janvier 2026 » à partir d'une date."""
    if not value:
        return ''
    return "%s %s" % (MONTH_NAMES_FR[value.month], value.year)


def format_date_fr(env, value):
    """Date au format court français, pour les messages destinés à l'écran."""
    if not value:
        return ''
    return fields.Date.to_date(value).strftime('%d/%m/%Y')
