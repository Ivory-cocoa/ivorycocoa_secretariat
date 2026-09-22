# -*- coding: utf-8 -*-
"""Facture de station-service — le relevé de quinzaine à régler.

La station sert les bons au fil de l'eau et présente sa facture deux fois par
mois. Ce modèle est le **document de contrôle** du secrétariat : il regroupe
les bons d'une station sur une quinzaine, en calcule le montant, et le
confronte à celui que la station réclame.

Trois partis pris :

* **La quinzaine est une période civile**, pas un intervalle libre : du 1er au
  15, puis du 16 à la fin du mois. Les bornes sont donc calculées, jamais
  saisies — deux factures qui se chevauchent factureraient deux fois les mêmes
  bons, et rien dans le document ne le montrerait.
* **Le rattachement se fait sur la date de référence du bon** (utilisation si
  elle est connue, émission sinon) : on paie la station pour ce qu'elle a
  servi, pas pour ce que le secrétariat a écrit.
* **L'écart avec le relevé de la station ne bloque pas**, mais il doit être
  justifié : valider une facture qui ne tombe pas juste exige une explication
  écrite. C'est la seule trace qui restera dans six mois.

Un bon n'appartient qu'à une seule facture (``invoice_id``), et une facture
validée fige les bons qu'elle porte (cf. ``INVOICE_LOCKED_FIELDS`` sur le bon).
"""

import calendar

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

from .secretariat_fuel_voucher import MONTH_NAMES_FR, format_date_fr

# Quinzaines : clé → (premier jour, dernier jour ou None pour « fin du mois »).
HALVES = (
    ('first', 1, 15),
    ('second', 16, None),
)

MONTH_SELECTION = [(str(index), MONTH_NAMES_FR[index]) for index in range(1, 13)]


class SecretariatFuelInvoice(models.Model):
    _name = 'secretariat.fuel.invoice'
    _description = "Facture de station-service"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_to desc, station_id, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string="Référence", required=True, copy=False, readonly=True,
        default=lambda self: _("Nouvelle"), index=True)

    station_id = fields.Many2one(
        'res.partner',
        string="Station",
        required=True,
        tracking=True,
        index=True,
        ondelete='restrict',
        domain="[('is_company', '=', True)]",
        default=lambda self: self.env.company.secretariat_fuel_station_id,
        help="Station-service qui présente la facture.",
    )

    # --- Période -------------------------------------------------------------
    period_year = fields.Integer(
        string="Année", required=True, tracking=True,
        default=lambda self: fields.Date.context_today(self).year)
    period_month = fields.Selection(
        MONTH_SELECTION, string="Mois", required=True, tracking=True,
        default=lambda self: str(fields.Date.context_today(self).month))
    period_half = fields.Selection(
        [
            ('first', "1re quinzaine (1 → 15)"),
            ('second', "2e quinzaine (16 → fin du mois)"),
        ],
        string="Quinzaine", required=True, tracking=True,
        default=lambda self: self._default_period_half())

    date_from = fields.Date(
        string="Du", compute='_compute_period_bounds', store=True, index=True)
    date_to = fields.Date(
        string="Au", compute='_compute_period_bounds', store=True, index=True)
    period_label = fields.Char(
        string="Période", compute='_compute_period_label')

    # --- Contenu -------------------------------------------------------------
    voucher_ids = fields.One2many(
        'secretariat.fuel.voucher', 'invoice_id', string="Bons facturés")
    voucher_count = fields.Integer(
        string="Nombre de bons", compute='_compute_totals', store=True)
    quantity_total = fields.Float(
        string="Quantité totale", compute='_compute_totals', store=True,
        digits='Product Unit of Measure')
    amount_total = fields.Monetary(
        string="Total des bons", compute='_compute_totals', store=True,
        currency_field='currency_id', tracking=True,
        help="Somme des bons repris sur cette facture — ce que le secrétariat "
             "a enregistré.")

    # --- Confrontation avec le relevé de la station --------------------------
    statement_amount = fields.Monetary(
        string="Montant réclamé", currency_field='currency_id', tracking=True,
        help="Montant porté sur la facture remise par la station. Laissez à "
             "zéro tant que vous ne l'avez pas sous les yeux.")
    amount_difference = fields.Monetary(
        string="Écart", compute='_compute_difference', store=True,
        currency_field='currency_id',
        help="Montant réclamé moins total des bons. Positif : la station "
             "demande plus que ce qui est enregistré.")
    has_difference = fields.Boolean(
        string="Écart constaté", compute='_compute_difference', store=True)
    difference_reason = fields.Text(
        string="Explication de l'écart",
        help="Obligatoire pour valider une facture qui ne tombe pas juste : "
             "bon manquant, bon servi hors quinzaine, erreur de la station…")

    station_invoice_ref = fields.Char(
        string="N° de la facture station", tracking=True, copy=False)
    station_invoice_date = fields.Date(
        string="Date de la facture station", tracking=True, copy=False)
    date_due = fields.Date(string="Échéance de paiement", tracking=True, copy=False)

    payment_date = fields.Date(string="Payée le", tracking=True, copy=False, readonly=True)
    payment_reference = fields.Char(
        string="Référence du règlement", tracking=True, copy=False,
        help="Numéro de chèque, d'ordre de virement ou de reçu.")

    state = fields.Selection(
        [
            ('draft', "Brouillon"),
            ('confirmed', "Validée"),
            ('paid', "Payée"),
            ('cancelled', "Annulée"),
        ],
        string="État", default='draft', required=True, tracking=True,
        index=True, copy=False)

    note = fields.Text(string="Observations")
    company_id = fields.Many2one(
        'res.company', string="Société", required=True, index=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', string="Devise",
        related='company_id.currency_id', readonly=True)

    # =========================================================================
    # INDEX
    # =========================================================================

    def init(self):
        """Une seule facture vivante par station et par quinzaine.

        Index **partiel** : une facture annulée ne compte pas, sans quoi il
        serait impossible de refaire la facture d'une quinzaine après une
        erreur. C'est le seul garde-fou qui tienne face à deux secrétaires qui
        génèrent la même quinzaine au même moment — la vérification applicative
        ne verrait rien dans une transaction concurrente.
        """
        super().init()
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS secretariat_fuel_invoice_period_uniq
            ON secretariat_fuel_invoice
               (company_id, station_id, period_year, period_month, period_half)
            WHERE state != 'cancelled'
        """)

    # =========================================================================
    # CALCULS
    # =========================================================================

    @api.model
    def _default_period_half(self):
        return 'first' if fields.Date.context_today(self).day <= 15 else 'second'

    @api.depends('period_year', 'period_month', 'period_half')
    def _compute_period_bounds(self):
        for invoice in self:
            start, end = self._period_bounds(
                invoice.period_year, invoice.period_month, invoice.period_half)
            invoice.date_from = start
            invoice.date_to = end

    @api.depends('date_from', 'date_to')
    def _compute_period_label(self):
        for invoice in self:
            invoice.period_label = self._period_label(
                invoice.date_from, invoice.date_to)

    @api.depends('voucher_ids', 'voucher_ids.amount_total',
                 'voucher_ids.quantity', 'voucher_ids.state')
    def _compute_totals(self):
        for invoice in self:
            lines = invoice.voucher_ids.filtered(lambda v: v.state != 'cancelled')
            invoice.voucher_count = len(lines)
            invoice.quantity_total = sum(lines.mapped('quantity'))
            invoice.amount_total = sum(lines.mapped('amount_total'))

    @api.depends('statement_amount', 'amount_total', 'currency_id')
    def _compute_difference(self):
        for invoice in self:
            rounding = invoice.currency_id.rounding or 0.01
            if float_is_zero(invoice.statement_amount, precision_rounding=rounding):
                # Zéro = « pas encore renseigné », pas « la station ne demande
                # rien ». On n'invente pas un écart égal au total des bons.
                invoice.amount_difference = 0.0
                invoice.has_difference = False
                continue
            difference = invoice.statement_amount - invoice.amount_total
            invoice.amount_difference = difference
            invoice.has_difference = not float_is_zero(
                difference, precision_rounding=rounding)

    # =========================================================================
    # CONTRAINTES
    # =========================================================================

    @api.constrains('period_year')
    def _check_period_year(self):
        current = fields.Date.context_today(self).year
        for invoice in self:
            if not 2000 <= invoice.period_year <= current + 1:
                raise ValidationError(_(
                    "L'année %(year)s n'est pas plausible pour une facture de "
                    "station.", year=invoice.period_year))

    @api.constrains('voucher_ids', 'station_id', 'date_from', 'date_to')
    def _check_vouchers(self):
        """Les bons repris doivent appartenir à la station et à la quinzaine."""
        for invoice in self:
            for voucher in invoice.voucher_ids:
                if voucher.supplier_id != invoice.station_id:
                    raise ValidationError(_(
                        "Le bon %(name)s a été servi par « %(station)s », pas "
                        "par « %(invoice_station)s ».",
                        name=voucher.name,
                        station=voucher.supplier_id.display_name or _("aucune station"),
                        invoice_station=invoice.station_id.display_name))
                reference = voucher.date_effective
                if reference and not (invoice.date_from <= reference <= invoice.date_to):
                    raise ValidationError(_(
                        "Le bon %(name)s porte la date de référence %(date)s, "
                        "hors de la quinzaine facturée (%(period)s).",
                        name=voucher.name,
                        date=format_date_fr(self.env, reference),
                        period=invoice.period_label))

    # =========================================================================
    # CRUD
    # =========================================================================

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _("Nouvelle"):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'secretariat.fuel.invoice') or _("Nouvelle")
        return super().create(vals_list)

    def unlink(self):
        living = self.filtered(lambda i: i.state not in ('draft', 'cancelled'))
        if living:
            raise ValidationError(_(
                "Impossible de supprimer la facture %(name)s : elle est "
                "validée. Annulez-la d'abord.",
                name=", ".join(living.mapped('name')[:5])))
        # Les bons repris redeviennent facturables ; sans cela ils
        # disparaîtraient des quinzaines suivantes sans laisser de trace.
        self.mapped('voucher_ids').write({'invoice_id': False})
        return super().unlink()

    # =========================================================================
    # WORKFLOW
    # =========================================================================

    def action_collect_vouchers(self):
        """Rattache à la facture tous les bons éligibles de la quinzaine."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_(
                "La facture %s n'est plus en brouillon : son contenu est "
                "arrêté.", self.name))
        if not self.station_id:
            raise UserError(_("Choisissez d'abord la station à facturer."))
        candidates = self.env['secretariat.fuel.voucher'].search(
            self._eligible_voucher_domain())
        if not candidates:
            return self._notify(
                _("Aucun bon à reprendre"),
                _("Aucun bon de « %(station)s » n'attend d'être facturé sur "
                  "%(period)s.",
                  station=self.station_id.display_name,
                  period=self.period_label),
                'warning')
        candidates.write({'invoice_id': self.id})
        self.message_post(body=_(
            "%(count)s bon(s) repris sur la quinzaine %(period)s.",
            count=len(candidates), period=self.period_label))
        return self._notify(
            _("Bons repris"),
            _("%(count)s bon(s) ajoutés à la facture.", count=len(candidates)))

    def action_confirm(self):
        """Arrête la facture : les bons sont validés et figés."""
        for invoice in self:
            if invoice.state != 'draft':
                continue
            if not invoice.voucher_count:
                raise UserError(_(
                    "La facture %s ne reprend aucun bon : il n'y a rien à "
                    "payer.", invoice.name))
            if invoice.has_difference and not (invoice.difference_reason or '').strip():
                raise UserError(_(
                    "La facture %(name)s présente un écart de %(difference)s "
                    "avec le relevé de la station.\n\n"
                    "Expliquez cet écart avant de valider : c'est la seule "
                    "trace qui restera de ce qui a été accepté.",
                    name=invoice.name,
                    difference=invoice._format_amount(invoice.amount_difference)))
            drafts = invoice.voucher_ids.filtered(lambda v: v.state == 'draft')
            if drafts:
                # Valider la facture vaut validation des bons qu'elle porte :
                # on ne paie pas un bon resté en brouillon.
                drafts.action_confirm()
            invoice.state = 'confirmed'
        return True

    def action_mark_paid(self):
        for invoice in self:
            if invoice.state != 'confirmed':
                raise UserError(_(
                    "Seule une facture validée peut être marquée payée "
                    "(%s).", invoice.name))
            invoice.write({
                'state': 'paid',
                'payment_date': invoice.payment_date
                or fields.Date.context_today(invoice),
            })
        return True

    def action_unpay(self):
        """Revient sur un paiement enregistré par erreur."""
        for invoice in self:
            if invoice.state != 'paid':
                continue
            invoice.write({
                'state': 'confirmed',
                'payment_date': False,
                'payment_reference': False,
            })
            invoice.message_post(body=_("Paiement annulé : facture revenue à « Validée »."))
        return True

    def action_draft(self):
        for invoice in self:
            if invoice.state == 'paid':
                raise UserError(_(
                    "La facture %s est payée. Annulez d'abord le paiement.",
                    invoice.name))
            invoice.state = 'draft'
        return True

    def action_cancel(self):
        """Annule la facture et rend ses bons à la facturation.

        Les bons sont détachés : laissés attachés à une facture annulée, ils
        n'apparaîtraient plus comme facturables et seraient tout simplement
        oubliés.
        """
        for invoice in self:
            if invoice.state == 'paid':
                raise UserError(_(
                    "La facture %s est payée : annulez d'abord le paiement.",
                    invoice.name))
            released = invoice.voucher_ids
            if released:
                numbers = ", ".join(released.mapped('name')[:20])
                released.write({'invoice_id': False})
                invoice.message_post(body=_(
                    "Facture annulée — %(count)s bon(s) rendus à la "
                    "facturation : %(numbers)s",
                    count=len(released), numbers=numbers))
            invoice.state = 'cancelled'
        return True

    # =========================================================================
    # ACTIONS D'ÉCRAN
    # =========================================================================

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons — %s", self.name),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form',
            'domain': [('invoice_id', '=', self.id)],
        }

    def action_view_candidates(self):
        """Les bons que cette facture pourrait encore reprendre."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons facturables — %s", self.period_label),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form',
            'domain': self._eligible_voucher_domain(),
        }

    def action_print(self):
        """Imprime la facture.

        ``config=False`` : sans cela, Odoo renvoie l'assistant de mise en page
        tant que la société n'a pas de modèle de document — et perd le
        document à imprimer au passage.
        """
        self.ensure_one()
        return self.env.ref(
            'ivorycocoa_secretariat.action_report_fuel_invoice'
        ).report_action(self, config=False)

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _eligible_voucher_domain(self):
        """Bons facturables sur cette quinzaine, pour cette station."""
        self.ensure_one()
        return [
            ('company_id', '=', self.company_id.id),
            ('supplier_id', '=', self.station_id.id),
            ('state', '!=', 'cancelled'),
            ('invoice_id', '=', False),
            ('date_effective', '>=', self.date_from),
            ('date_effective', '<=', self.date_to),
        ]

    def _format_amount(self, amount):
        self.ensure_one()
        return "%s %s" % (
            "{:,.0f}".format(amount or 0.0).replace(",", " "),
            self.currency_id.symbol or '')

    def _notify(self, title, message, level='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': level,
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    def report_data(self):
        """Données du PDF, calculées une seule fois."""
        self.ensure_one()
        lines = self.voucher_ids.filtered(lambda v: v.state != 'cancelled') \
            .sorted(lambda v: (v.date_effective or v.date, v.name))
        buckets = {}
        for voucher in lines:
            label = voucher.fuel_type_id.name or _("(non précisé)")
            row = buckets.setdefault(label, [label, 0, 0.0, 0.0])
            row[1] += 1
            row[2] += voucher.quantity
            row[3] += voucher.amount_total
        return {
            'lines': lines,
            'by_fuel': sorted(buckets.values(), key=lambda row: row[3], reverse=True),
            'currency': self.currency_id,
            'company': self.company_id,
        }

    # =========================================================================
    # HELPERS DE PÉRIODE — partagés avec les assistants et le tableau de bord
    # =========================================================================

    @api.model
    def _period_bounds(self, year, month, half):
        """(premier jour, dernier jour) d'une quinzaine."""
        year = int(year or fields.Date.context_today(self).year)
        month = int(month or fields.Date.context_today(self).month)
        last_day = calendar.monthrange(year, month)[1]
        for key, start_day, end_day in HALVES:
            if key == (half or 'first'):
                return (
                    fields.Date.to_date('%04d-%02d-%02d' % (year, month, start_day)),
                    fields.Date.to_date(
                        '%04d-%02d-%02d' % (year, month, end_day or last_day)),
                )
        raise ValidationError(_("Quinzaine inconnue : %s", half))

    @api.model
    def _period_of(self, date):
        """(année, mois, quinzaine) d'une date — l'inverse de _period_bounds."""
        date = fields.Date.to_date(date)
        return date.year, str(date.month), 'first' if date.day <= 15 else 'second'

    @api.model
    def _period_label(self, start, end):
        if not start or not end:
            return ''
        return _("du %(from)s au %(to)s", **{
            'from': start.strftime('%d/%m/%Y'),
            'to': end.strftime('%d/%m/%Y'),
        })

    @api.model
    def half_label(self, half):
        return _("1re quinzaine") if half == 'first' else _("2e quinzaine")
