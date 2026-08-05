# -*- coding: utf-8 -*-
"""Bénéficiaires des bons de carburant.

La colonne « Nom » du classeur repris désigne tantôt un employé (« Camara »,
« Sandrine »), tantôt un service interne (« Usine », « Personnel »), tantôt un
tiers (« Douanes », « CCC », « Chambre de commerce »). D'où un référentiel
dédié, avec un lien FACULTATIF vers la fiche employé.
"""

from odoo import _, api, fields, models

from .secretariat_fuel_type import normalize_label


class SecretariatBeneficiary(models.Model):
    _name = 'secretariat.fuel.beneficiary'
    _description = "Bénéficiaire de bon de carburant"
    _order = 'name'

    name = fields.Char(
        string="Nom",
        required=True,
        help="Nom porté sur le bon (employé, service ou organisme).",
    )
    active = fields.Boolean(string="Actif", default=True)

    category = fields.Selection(
        [
            ('employee', 'Employé'),
            ('department', 'Service interne'),
            ('external', 'Tiers / organisme'),
        ],
        string="Catégorie",
        default='employee',
        required=True,
    )
    employee_id = fields.Many2one(
        'hr.employee.public',
        string="Employé",
        help="Fiche employé correspondante, quand le bénéficiaire est un "
             "membre du personnel.",
    )
    note = fields.Text(string="Notes")

    currency_id = fields.Many2one(
        'res.currency', string="Devise",
        default=lambda self: self.env.company.currency_id, readonly=True)

    normalized_name = fields.Char(
        compute='_compute_normalized_name', store=True, index=True,
        string="Clé de rapprochement",
    )

    voucher_count = fields.Integer(
        string="Bons", compute='_compute_voucher_stats')
    quantity_total = fields.Float(
        string="Quantité totale", compute='_compute_voucher_stats',
        digits='Product Unit of Measure')
    amount_total = fields.Monetary(
        string="Montant total", compute='_compute_voucher_stats',
        currency_field='currency_id')
    last_voucher_date = fields.Date(
        string="Dernier bon", compute='_compute_voucher_stats')

    _sql_constraints = [
        ('name_uniq', 'unique(name)', "Un bénéficiaire portant ce nom existe déjà."),
    ]

    @api.depends('name')
    def _compute_normalized_name(self):
        for record in self:
            record.normalized_name = normalize_label(record.name)

    def _compute_voucher_stats(self):
        stats = {}
        if self.ids:
            data = self.env['secretariat.fuel.voucher'].read_group(
                [('beneficiary_id', 'in', self.ids), ('state', '!=', 'cancelled')],
                ['quantity:sum', 'amount_total:sum', 'date:max'],
                ['beneficiary_id'],
            )
            stats = {
                group['beneficiary_id'][0]: (
                    group['beneficiary_id_count'], group['quantity'],
                    group['amount_total'], group.get('date'))
                for group in data if group['beneficiary_id']
            }
        for record in self:
            count, qty, amount, last = stats.get(record.id, (0, 0.0, 0.0, False))
            record.voucher_count = count
            record.quantity_total = qty
            record.amount_total = amount
            record.last_voucher_date = last or False

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id:
            self.category = 'employee'
            if not self.name:
                self.name = self.employee_id.name

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons — %s", self.name),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form,pivot,graph',
            'domain': [('beneficiary_id', '=', self.id)],
            'context': {'default_beneficiary_id': self.id},
        }

    @api.model
    def find_or_create(self, label, create_missing=True):
        """Retrouve un bénéficiaire à partir d'un libellé libre (import Excel).

        Rattache automatiquement l'employé homonyme quand il en existe un seul.
        """
        key = normalize_label(label)
        if not key:
            return self.browse()
        existing = self.with_context(active_test=False).search(
            [('normalized_name', '=', key)], limit=1)
        if existing or not create_missing:
            return existing
        values = {'name': str(label).strip()}
        employees = self.env['hr.employee.public'].search(
            [('name', '=ilike', values['name'])], limit=2)
        if len(employees) == 1:
            values.update(employee_id=employees.id, category='employee')
        return self.create(values)
