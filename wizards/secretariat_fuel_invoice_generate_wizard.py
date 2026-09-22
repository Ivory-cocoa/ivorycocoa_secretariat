# -*- coding: utf-8 -*-
"""Génération des factures de quinzaine, une par station.

Le geste réel du secrétariat : en fin de quinzaine, prendre tous les bons
servis, les trier par station et sortir une facture par station à confronter
au relevé. Cet assistant fait exactement cela, et rien d'autre.

Deux précautions qui évitent les doubles facturations :

* une station qui a **déjà** une facture non annulée sur la quinzaine n'en
  reçoit pas une seconde : ses bons manquants sont ajoutés à la facture
  existante si elle est encore en brouillon, et l'assistant le dit ;
* les bons déjà rattachés à une facture ne sont jamais repris.

L'aperçu est calculé avant toute écriture : la secrétaire voit ce qui va être
créé, station par station, avant de valider.
"""

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.secretariat_fuel_invoice import MONTH_SELECTION


class SecretariatFuelInvoiceGenerateWizard(models.TransientModel):
    _name = 'secretariat.fuel.invoice.generate.wizard'
    _description = "Génération des factures de quinzaine"

    period_year = fields.Integer(
        string="Année", required=True,
        default=lambda self: fields.Date.context_today(self).year)
    period_month = fields.Selection(
        MONTH_SELECTION, string="Mois", required=True,
        default=lambda self: str(fields.Date.context_today(self).month))
    period_half = fields.Selection(
        [
            ('first', "1re quinzaine (1 → 15)"),
            ('second', "2e quinzaine (16 → fin du mois)"),
        ],
        string="Quinzaine", required=True,
        default=lambda self: self.env['secretariat.fuel.invoice']._default_period_half())

    date_from = fields.Date(string="Du", compute='_compute_bounds')
    date_to = fields.Date(string="Au", compute='_compute_bounds')

    station_ids = fields.Many2many(
        'res.partner',
        relation='secretariat_fuel_invoice_gen_station_rel',
        column1='wizard_id', column2='partner_id',
        string="Stations",
        domain="[('is_company', '=', True)]",
        help="Laisser vide pour facturer toutes les stations ayant des bons "
             "sur la quinzaine.")
    assign_default_station = fields.Boolean(
        string="Rattacher les bons sans station",
        default=True,
        help="Les bons de la quinzaine dont la station n'est pas renseignée "
             "sont rattachés à la station par défaut de la société avant "
             "d'être facturés. Sans cela, ils resteraient hors facturation.")
    default_station_id = fields.Many2one(
        'res.partner', string="Station par défaut",
        related='company_id.secretariat_fuel_station_id', readonly=True)
    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company)

    preview_html = fields.Html(
        string="Aperçu", compute='_compute_preview', sanitize=False)
    candidate_count = fields.Integer(
        string="Bons facturables", compute='_compute_preview',
        help="Bons qui seront repris dans une facture par cette génération.")
    orphan_count = fields.Integer(
        string="Bons sans station", compute='_compute_preview',
        help="Bons de la quinzaine dont la station n'est pas renseignée. Ils "
             "ne sont pas facturables en l'état.")

    # =========================================================================
    # PÉRIODE ET APERÇU
    # =========================================================================

    @api.depends('period_year', 'period_month', 'period_half')
    def _compute_bounds(self):
        Invoice = self.env['secretariat.fuel.invoice']
        for wizard in self:
            wizard.date_from, wizard.date_to = Invoice._period_bounds(
                wizard.period_year, wizard.period_month, wizard.period_half)

    @api.depends('date_from', 'date_to', 'station_ids',
                 'assign_default_station', 'company_id')
    def _compute_preview(self):
        for wizard in self:
            rows = wizard._collect()
            # Les bons sans station sont comptés à part : ils apparaissent
            # dans l'aperçu, mais ne seront pas facturés — les additionner
            # promettrait une facture qui ne viendra pas.
            wizard.candidate_count = sum(
                row['count'] for row in rows if not row['orphan'])
            wizard.orphan_count = sum(
                row['count'] for row in rows if row['orphan'])
            wizard.preview_html = wizard._render_preview(rows)

    def action_previous_half(self):
        """Cale l'assistant sur la quinzaine précédente — le cas courant."""
        self.ensure_one()
        previous = self.date_from - relativedelta(days=1)
        year, month, half = self.env['secretariat.fuel.invoice']._period_of(previous)
        self.write({
            'period_year': year, 'period_month': month, 'period_half': half})
        return self._reopen()

    # =========================================================================
    # COLLECTE
    # =========================================================================

    def _voucher_domain(self, station=None):
        self.ensure_one()
        domain = [
            ('company_id', '=', self.company_id.id),
            ('state', '!=', 'cancelled'),
            ('invoice_id', '=', False),
            ('date_effective', '>=', self.date_from),
            ('date_effective', '<=', self.date_to),
        ]
        if station is not None:
            domain.append(('supplier_id', '=', station.id if station else False))
        elif self.station_ids:
            domain.append(('supplier_id', 'in', self.station_ids.ids))
        return domain

    def _collect(self):
        """[{station, bons, montant, facture existante}] — sans rien écrire."""
        self.ensure_one()
        if not self.date_from or not self.date_to:
            return []
        Voucher = self.env['secretariat.fuel.voucher']
        groups = Voucher.read_group(
            self._voucher_domain(), ['amount_total:sum'], ['supplier_id'],
            lazy=False)

        stations = self.station_ids
        fallback = self.default_station_id if self.assign_default_station else False
        rows = {}
        for group in groups:
            station_data = group['supplier_id']
            station = self.env['res.partner'].browse(
                station_data[0]) if station_data else self.env['res.partner']
            unassigned = not station
            if unassigned:
                if not fallback:
                    # Sans station de repli, ces bons ne sont pas facturables :
                    # on les signale au lieu de les faire disparaître.
                    rows.setdefault('orphans', {
                        'station': self.env['res.partner'],
                        'count': 0, 'amount': 0.0, 'orphan': True,
                        'invoice': self.env['secretariat.fuel.invoice'],
                    })
                    rows['orphans']['count'] += group['__count']
                    rows['orphans']['amount'] += group['amount_total'] or 0.0
                    continue
                station = fallback
            if stations and station not in stations:
                continue
            row = rows.setdefault(station.id, {
                'station': station, 'count': 0, 'amount': 0.0, 'orphan': False,
                'invoice': self._existing_invoice(station),
            })
            row['count'] += group['__count']
            row['amount'] += group['amount_total'] or 0.0
        ordered = sorted(
            rows.values(), key=lambda row: (row['orphan'], -row['amount']))
        return ordered

    def _existing_invoice(self, station):
        """Facture déjà ouverte pour cette station sur cette quinzaine."""
        self.ensure_one()
        return self.env['secretariat.fuel.invoice'].search([
            ('company_id', '=', self.company_id.id),
            ('station_id', '=', station.id),
            ('period_year', '=', self.period_year),
            ('period_month', '=', self.period_month),
            ('period_half', '=', self.period_half),
            ('state', '!=', 'cancelled'),
        ], limit=1)

    def _render_preview(self, rows):
        """Aperçu lisible — ce qui sera créé, complété, ou laissé de côté."""
        self.ensure_one()
        if not rows:
            return Markup(
                '<div class="alert alert-info mb-0">Aucun bon à facturer sur '
                'cette quinzaine.</div>')
        currency = self.company_id.currency_id
        lines = []
        for row in rows:
            amount = "%s %s" % (
                "{:,.0f}".format(row['amount']).replace(",", " "),
                currency.symbol or '')
            if row['orphan']:
                lines.append(
                    '<tr class="table-warning"><td>Station non renseignée</td>'
                    '<td class="text-end">%s</td><td class="text-end">%s</td>'
                    '<td>Non facturable — renseignez la station ou cochez '
                    'le rattachement automatique</td></tr>'
                    % (row['count'], amount))
                continue
            invoice = row['invoice']
            if not invoice:
                status = '<span class="text-success">Nouvelle facture</span>'
            elif invoice.state == 'draft':
                status = ('Complète la facture <b>%s</b> (brouillon)'
                          % invoice.name)
            else:
                status = ('<span class="text-danger">Facture <b>%s</b> déjà '
                          'validée — ces bons resteront à facturer</span>'
                          % invoice.name)
            lines.append(
                '<tr><td>%s</td><td class="text-end">%s</td>'
                '<td class="text-end">%s</td><td>%s</td></tr>'
                % (row['station'].display_name, row['count'], amount, status))
        return Markup(
            '<table class="table table-sm mb-0">'
            '<thead><tr><th>Station</th><th class="text-end">Bons</th>'
            '<th class="text-end">Montant</th><th>Effet</th></tr></thead>'
            '<tbody>%s</tbody></table>' % "".join(lines))

    # =========================================================================
    # GÉNÉRATION
    # =========================================================================

    def action_generate(self):
        self.ensure_one()
        rows = [row for row in self._collect() if not row['orphan']]
        if not rows:
            raise UserError(_(
                "Aucun bon facturable sur %(period)s.\n\n"
                "Soit les bons de cette quinzaine sont déjà facturés, soit "
                "leur station n'est pas renseignée.",
                period=self.env['secretariat.fuel.invoice']._period_label(
                    self.date_from, self.date_to)))

        Invoice = self.env['secretariat.fuel.invoice']
        touched = Invoice.browse()
        skipped = []
        for row in rows:
            station = row['station']
            self._assign_station(station)
            invoice = row['invoice']
            if invoice and invoice.state != 'draft':
                skipped.append(invoice)
                continue
            if not invoice:
                invoice = Invoice.create({
                    'station_id': station.id,
                    'period_year': self.period_year,
                    'period_month': self.period_month,
                    'period_half': self.period_half,
                    'company_id': self.company_id.id,
                })
            invoice.action_collect_vouchers()
            touched |= invoice

        if not touched:
            raise UserError(_(
                "Rien n'a été généré : les factures de cette quinzaine sont "
                "déjà validées (%s).",
                ", ".join(invoice.name for invoice in skipped)))

        action = {
            'type': 'ir.actions.act_window',
            'name': _("Factures de la quinzaine"),
            'res_model': 'secretariat.fuel.invoice',
            'domain': [('id', 'in', touched.ids)],
            'view_mode': 'tree,form',
        }
        if len(touched) == 1:
            action.update({
                'view_mode': 'form', 'res_id': touched.id, 'domain': []})
        return action

    def _assign_station(self, station):
        """Rattache à la station les bons de la quinzaine qui n'en ont pas."""
        self.ensure_one()
        if not self.assign_default_station or station != self.default_station_id:
            return
        orphans = self.env['secretariat.fuel.voucher'].search(
            self._voucher_domain(station=self.env['res.partner']))
        if orphans:
            orphans.write({'supplier_id': station.id})

    def _reopen(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
