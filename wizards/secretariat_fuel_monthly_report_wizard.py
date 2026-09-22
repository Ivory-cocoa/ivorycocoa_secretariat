# -*- coding: utf-8 -*-
"""État récapitulatif mensuel des bons de carburant (PDF).

Le classeur d'origine calculait ses totaux à la main en bas de feuille. Cet
état les reprend et les complète : totaux du mois, répartition par carburant,
par engin et par bénéficiaire, dépassements de dotation, puis — au choix — le
détail des bons.

C'est le document à faire viser par la direction : il porte donc une ligne de
signature et rappelle la période exacte couverte.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.secretariat_fuel_voucher import month_label_fr


class SecretariatFuelMonthlyReportWizard(models.TransientModel):
    _name = 'secretariat.fuel.monthly.report.wizard'
    _description = "État récapitulatif mensuel des bons de carburant"

    date_from = fields.Date(
        string="Du", required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date(
        string="Au", required=True,
        default=lambda self: (
            fields.Date.context_today(self).replace(day=1)
            + relativedelta(months=1, days=-1)))

    # Les tables de liaison portent un nom explicite : le nom déduit du modèle
    # dépasserait les 63 caractères admis par PostgreSQL.
    fuel_type_ids = fields.Many2many(
        'secretariat.fuel.type', relation='secretariat_fuel_monthly_fuel_rel',
        column1='wizard_id', column2='fuel_type_id', string="Carburants",
        help="Laisser vide pour tous les carburants.")
    vehicle_ids = fields.Many2many(
        'secretariat.vehicle', relation='secretariat_fuel_monthly_vehicle_rel',
        column1='wizard_id', column2='vehicle_id', string="Engins",
        help="Laisser vide pour tous les engins.")
    beneficiary_ids = fields.Many2many(
        'secretariat.fuel.beneficiary',
        relation='secretariat_fuel_monthly_beneficiary_rel',
        column1='wizard_id', column2='beneficiary_id', string="Bénéficiaires",
        help="Laisser vide pour tous les bénéficiaires.")

    include_detail = fields.Boolean(
        string="Détail des bons", default=True,
        help="Ajoute la liste des bons de la période après les synthèses.")
    include_draft = fields.Boolean(
        string="Inclure les brouillons", default=True,
        help="Décochez pour n'arrêter l'état que sur les bons validés.")

    signatory = fields.Char(
        string="Établi par",
        default=lambda self: self.env.user.name,
        help="Nom porté au bas de l'état, au-dessus de la ligne de signature.")

    # =========================================================================
    # ACTIONS
    # =========================================================================

    def action_previous_month(self):
        """Cale la période sur le mois précédent — le cas le plus fréquent."""
        self.ensure_one()
        start = self.date_from.replace(day=1) - relativedelta(months=1)
        self.write({
            'date_from': start,
            'date_to': start + relativedelta(months=1, days=-1),
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_print(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("La date de début est postérieure à la date de fin."))
        if not self._vouchers():
            raise UserError(_(
                "Aucun bon de carburant sur cette période avec ces filtres."))
        return self.env.ref(
            'ivorycocoa_secretariat.action_report_fuel_monthly').report_action(self)

    # =========================================================================
    # DONNÉES DU RAPPORT
    # =========================================================================

    def _domain(self):
        self.ensure_one()
        states = ['confirmed'] if not self.include_draft else ['draft', 'confirmed']
        # Borné sur la date de référence (utilisation si connue, émission
        # sinon) : l'état récapitulatif arrête une consommation, pas une
        # émission de carnets.
        domain = [
            ('date_effective', '>=', self.date_from),
            ('date_effective', '<=', self.date_to),
            ('state', 'in', states),
        ]
        if self.fuel_type_ids:
            domain.append(('fuel_type_id', 'in', self.fuel_type_ids.ids))
        if self.vehicle_ids:
            domain.append(('vehicle_id', 'in', self.vehicle_ids.ids))
        if self.beneficiary_ids:
            domain.append(('beneficiary_id', 'in', self.beneficiary_ids.ids))
        return domain

    def _vouchers(self):
        return self.env['secretariat.fuel.voucher'].search(
            self._domain(), order='date_effective asc, name asc, id asc')

    @api.model
    def _bucket(self, vouchers, key):
        """[(libellé, nb, quantité, montant)] trié par montant décroissant."""
        buckets = {}
        for voucher in vouchers:
            label = key(voucher)
            row = buckets.setdefault(label, [label, 0, 0.0, 0.0])
            row[1] += 1
            row[2] += voucher.quantity
            row[3] += voucher.amount_total
        return sorted(buckets.values(), key=lambda row: row[3], reverse=True)

    def report_data(self):
        """Tout ce dont le template a besoin, calculé une seule fois."""
        self.ensure_one()
        vouchers = self._vouchers()
        unspecified = _("(non précisé)")
        return {
            'vouchers': vouchers,
            'period_label': self._period_label(),
            'count': len(vouchers),
            'quantity': sum(vouchers.mapped('quantity')),
            'amount': sum(vouchers.mapped('amount_total')),
            'by_fuel': self._bucket(
                vouchers, lambda v: v.fuel_type_id.name or unspecified),
            'by_vehicle': self._bucket(
                vouchers, lambda v: v.vehicle_id.name or unspecified),
            'by_beneficiary': self._bucket(
                vouchers, lambda v: v.beneficiary_id.name or unspecified),
            'over_quota': self._over_quota(vouchers),
            'currency': self.env.company.currency_id,
            'company': self.env.company,
        }

    def _period_label(self):
        self.ensure_one()
        month_start = self.date_from.replace(day=1)
        if self.date_from == month_start \
                and self.date_to == month_start + relativedelta(months=1, days=-1):
            return month_label_fr(month_start)
        return _("du %(from)s au %(to)s", **{
            'from': self.date_from.strftime('%d/%m/%Y'),
            'to': self.date_to.strftime('%d/%m/%Y'),
        })

    def _over_quota(self, vouchers):
        """Engins de la période ayant dépassé leur dotation mensuelle.

        Le rapprochement se fait sur la consommation de la période éditée, pas
        sur celle du mois en cours : un état d'avril doit refléter avril.
        """
        self.ensure_one()
        rows = []
        for vehicle in vouchers.mapped('vehicle_id'):
            if vehicle.monthly_quota <= 0 and vehicle.monthly_budget <= 0:
                continue
            lines = vouchers.filtered(lambda v, ve=vehicle: v.vehicle_id == ve)
            quantity = sum(lines.mapped('quantity'))
            amount = sum(lines.mapped('amount_total'))
            rates = []
            if vehicle.monthly_quota > 0:
                rates.append(quantity / vehicle.monthly_quota * 100.0)
            if vehicle.monthly_budget > 0:
                rates.append(amount / vehicle.monthly_budget * 100.0)
            rate = max(rates) if rates else 0.0
            if rate > 100.0:
                rows.append({
                    'name': vehicle.name,
                    'quota': vehicle.monthly_quota,
                    'budget': vehicle.monthly_budget,
                    'quantity': quantity,
                    'amount': amount,
                    'rate': round(rate, 1),
                })
        return sorted(rows, key=lambda row: row['rate'], reverse=True)
