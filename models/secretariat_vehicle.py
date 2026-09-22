# -*- coding: utf-8 -*-
"""Engins : ce à quoi le carburant est destiné.

Le classeur repris mélange des immatriculations (« 2900 LP01 »), des motos
nommées d'après leur utilisateur (« Moto Camara »), des matériels d'usine
(« Fourchette », « Car ») et même des lieux (« Domicile PDG »). Le libellé
reste donc du texte libre ; le type et l'immatriculation sont facultatifs.

**Plafond mensuel.** Un engin peut porter une dotation mensuelle, en quantité
et/ou en montant. Elle n'interdit rien — la secrétaire enregistre ce qui a été
servi, elle ne l'autorise pas — mais elle alimente un avertissement à la
saisie, un filtre « engins en dépassement » et une pastille du tableau de bord.
Un plafond à zéro signifie « pas de plafond ».
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .secretariat_fuel_type import normalize_label


class SecretariatVehicle(models.Model):
    _name = 'secretariat.vehicle'
    _description = "Engin"
    _inherit = ['secretariat.referential.mixin']
    _order = 'name'

    _merge_voucher_field = 'vehicle_id'

    name = fields.Char(
        string="Engin",
        required=True,
        help="Libellé de l'engin tel qu'il est écrit sur le bon "
             "(immatriculation, « Moto Camara », « Fourchette »…).",
    )
    active = fields.Boolean(string="Actif", default=True)

    vehicle_type = fields.Selection(
        [
            ('car', 'Véhicule'),
            ('motorcycle', 'Moto'),
            ('truck', 'Camion'),
            ('machine', 'Engin / machine'),
            ('generator', 'Groupe électrogène'),
            ('other', 'Autre'),
        ],
        string="Type",
        default='car',
    )
    license_plate = fields.Char(string="Immatriculation")
    employee_id = fields.Many2one(
        'hr.employee.public',
        string="Affecté à",
        help="Employé habituellement associé à cet engin (facultatif).",
    )
    note = fields.Text(string="Notes")

    currency_id = fields.Many2one(
        'res.currency', string="Devise",
        default=lambda self: self.env.company.currency_id, readonly=True)

    normalized_name = fields.Char(
        compute='_compute_normalized_name', store=True, index=True,
        string="Clé de rapprochement",
    )

    # --- Dotation mensuelle -------------------------------------------------
    monthly_quota = fields.Float(
        string="Plafond mensuel (quantité)",
        digits='Product Unit of Measure',
        help="Quantité mensuelle allouée à cet engin, toutes sources de "
             "carburant confondues. Zéro = pas de plafond. Le dépassement "
             "n'est jamais bloquant : il déclenche un avertissement à la "
             "saisie et remonte au tableau de bord.",
    )
    monthly_budget = fields.Monetary(
        string="Plafond mensuel (montant)",
        currency_field='currency_id',
        help="Montant mensuel alloué à cet engin. Zéro = pas de plafond.",
    )

    month_quantity = fields.Float(
        string="Quantité du mois", compute='_compute_month_usage',
        digits='Product Unit of Measure')
    month_amount = fields.Monetary(
        string="Montant du mois", compute='_compute_month_usage',
        currency_field='currency_id')
    quota_usage_rate = fields.Float(
        string="Consommation du plafond (%)", compute='_compute_month_usage',
        help="Le plus contraignant des deux plafonds (quantité et montant).")
    is_over_quota = fields.Boolean(
        string="En dépassement", compute='_compute_month_usage',
        search='_search_is_over_quota')

    voucher_count = fields.Integer(
        string="Bons", compute='_compute_voucher_stats')
    quantity_total = fields.Float(
        string="Quantité totale", compute='_compute_voucher_stats',
        digits='Product Unit of Measure')
    amount_total = fields.Monetary(
        string="Montant total", compute='_compute_voucher_stats',
        currency_field='currency_id')

    _sql_constraints = [
        ('name_uniq', 'unique(name)', "Un engin portant ce nom existe déjà."),
    ]

    # =========================================================================
    # CALCULS
    # =========================================================================

    @api.depends('name')
    def _compute_normalized_name(self):
        for record in self:
            record.normalized_name = normalize_label(record.name)

    def _compute_voucher_stats(self):
        stats = self._voucher_stats()
        for record in self:
            count, qty, amount = stats.get(record.id, (0, 0.0, 0.0))
            record.voucher_count = count
            record.quantity_total = qty
            record.amount_total = amount

    def _compute_month_usage(self):
        """Consommation du mois civil en cours, rapportée au plafond.

        Bornée sur la date de référence du bon : la dotation d'un engin se
        juge sur le carburant qu'il a réellement consommé dans le mois, pas
        sur les bons qui lui ont été remis.
        """
        start, end = self._current_month_bounds()
        stats = self._voucher_stats([
            ('date_effective', '>=', start), ('date_effective', '<=', end)])
        for record in self:
            _count, quantity, amount = stats.get(record.id, (0, 0.0, 0.0))
            record.month_quantity = quantity
            record.month_amount = amount
            rates = []
            if record.monthly_quota > 0:
                rates.append(quantity / record.monthly_quota * 100.0)
            if record.monthly_budget > 0:
                rates.append(amount / record.monthly_budget * 100.0)
            record.quota_usage_rate = max(rates) if rates else 0.0
            record.is_over_quota = record.quota_usage_rate > 100.0

    def _search_is_over_quota(self, operator, value):
        """Filtre « engins en dépassement » — recalculé à la volée.

        Le dépassement dépend du mois courant : il ne peut pas être stocké.
        Le nombre d'engins se compte en dizaines, la recherche reste bon
        marché.
        """
        if operator not in ('=', '!='):
            raise ValidationError(_("Filtre non pris en charge sur « En dépassement »."))
        over = self.with_context(active_test=False).search([
            '|', ('monthly_quota', '>', 0), ('monthly_budget', '>', 0),
        ]).filtered('is_over_quota')
        wanted = bool(value) if operator == '=' else not value
        return [('id', 'in' if wanted else 'not in', over.ids)]

    # =========================================================================
    # HELPERS
    # =========================================================================

    @api.model
    def _current_month_bounds(self):
        today = fields.Date.context_today(self)
        start = today.replace(day=1)
        return start, start + relativedelta(months=1, days=-1)

    def _voucher_stats(self, extra_domain=None):
        """{id engin: (nb bons, quantité, montant)} — bons annulés exclus."""
        if not self.ids:
            return {}
        domain = [('vehicle_id', 'in', self.ids), ('state', '!=', 'cancelled')]
        domain += extra_domain or []
        data = self.env['secretariat.fuel.voucher'].read_group(
            domain, ['quantity:sum', 'amount_total:sum'], ['vehicle_id'])
        return {
            group['vehicle_id'][0]: (
                group['vehicle_id_count'], group['quantity'], group['amount_total'])
            for group in data if group['vehicle_id']
        }

    @api.constrains('monthly_quota', 'monthly_budget')
    def _check_quota(self):
        for record in self:
            if record.monthly_quota < 0 or record.monthly_budget < 0:
                raise ValidationError(_("Un plafond mensuel ne peut pas être négatif."))

    # =========================================================================
    # ACTIONS
    # =========================================================================

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons — %s", self.name),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form,pivot,graph',
            'domain': [('vehicle_id', '=', self.id)],
            'context': {'default_vehicle_id': self.id},
        }

    def action_view_month_vouchers(self):
        self.ensure_one()
        start, end = self._current_month_bounds()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons du mois — %s", self.name),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form',
            'domain': [
                ('vehicle_id', '=', self.id),
                ('date_effective', '>=', start), ('date_effective', '<=', end),
                ('state', '!=', 'cancelled'),
            ],
            'context': {'default_vehicle_id': self.id},
        }

    @api.model
    def find_or_create(self, label, create_missing=True):
        """Retrouve un engin à partir d'un libellé libre (import Excel)."""
        key = normalize_label(label)
        if not key:
            return self.browse()
        existing = self.with_context(active_test=False).search(
            [('normalized_name', '=', key)], limit=1)
        if existing or not create_missing:
            return existing
        return self.create({'name': str(label).strip()})
