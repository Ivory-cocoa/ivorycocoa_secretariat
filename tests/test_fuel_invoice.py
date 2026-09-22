# -*- coding: utf-8 -*-
"""Factures de quinzaine : regroupement, contrôle du relevé et verrous."""

import base64

from dateutil.relativedelta import relativedelta
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import SecretariatCase


def reference_month(env):
    """Premier jour du mois précédent.

    Les tests se calent sur un mois révolu : un bon daté du 20 serait sinon
    « utilisé dans le futur » les vingt premiers jours de chaque mois, et la
    suite échouerait selon la date d'exécution.
    """
    today = fields.Date.context_today(env['secretariat.fuel.voucher'])
    return today.replace(day=1) - relativedelta(months=1)


@tagged('post_install', '-at_install')
class TestFuelInvoice(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.Invoice = cls.env['secretariat.fuel.invoice']
        cls.fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.station = cls.env['res.partner'].create({
            'name': 'Yasmina Petroleum', 'is_company': True})
        cls.other_station = cls.env['res.partner'].create({
            'name': 'Station Zone 4', 'is_company': True})
        cls.env.company.secretariat_fuel_station_id = cls.station
        cls.vehicle = cls.env['secretariat.vehicle'].create({'name': '2900 LP01'})

    def _voucher(self, day, station=None, **overrides):
        """Bon du mois de référence, daté du jour `day`."""
        month = reference_month(self.env)
        values = {
            'name': overrides.pop('name', False) or str(9000 + day),
            'date': month.replace(day=day),
            'vehicle_id': self.vehicle.id,
            'fuel_type_id': self.fuel.id,
            'supplier_id': (station or self.station).id if station is not False else False,
            'price_unit': 875.0,
            'quantity': 10.0,
        }
        values.update(overrides)
        return self.Voucher.create(values)

    def _invoice(self, half='first', station=None):
        month = reference_month(self.env)
        return self.Invoice.create({
            'station_id': (station or self.station).id,
            'period_year': month.year,
            'period_month': str(month.month),
            'period_half': half,
        })

    # ------------------------------------------------------------------
    # Quinzaines
    # ------------------------------------------------------------------

    def test_period_bounds(self):
        first = self.Invoice._period_bounds(2026, '2', 'first')
        second = self.Invoice._period_bounds(2026, '2', 'second')
        self.assertEqual(
            (str(first[0]), str(first[1])), ('2026-02-01', '2026-02-15'))
        self.assertEqual(
            (str(second[0]), str(second[1])), ('2026-02-16', '2026-02-28'))

    def test_period_of_a_date(self):
        self.assertEqual(
            self.Invoice._period_of('2026-03-15'), (2026, '3', 'first'))
        self.assertEqual(
            self.Invoice._period_of('2026-03-16'), (2026, '3', 'second'))

    # ------------------------------------------------------------------
    # Collecte
    # ------------------------------------------------------------------

    def test_collect_takes_only_matching_vouchers(self):
        inside = self._voucher(3)
        self._voucher(20)                                  # autre quinzaine
        self._voucher(4, station=self.other_station)       # autre station
        cancelled = self._voucher(5, name='9500')
        cancelled.action_cancel()

        invoice = self._invoice()
        invoice.action_collect_vouchers()

        self.assertEqual(invoice.voucher_ids, inside)
        self.assertEqual(invoice.voucher_count, 1)
        self.assertAlmostEqual(invoice.amount_total, 8750.0)

    def test_collect_uses_the_usage_date(self):
        """Un bon émis avant la quinzaine mais servi dedans y est facturé."""
        month = reference_month(self.env)
        voucher = self._voucher(2, date_used=month.replace(day=20))
        invoice = self._invoice(half='second')
        invoice.action_collect_vouchers()
        self.assertEqual(invoice.voucher_ids, voucher)

    def test_a_voucher_is_never_billed_twice(self):
        """Une seconde collecte ne reprend pas ce qui est déjà facturé."""
        voucher = self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.action_collect_vouchers()
        self.assertEqual(invoice.voucher_ids, voucher)
        self.assertEqual(invoice.voucher_count, 1)

    # ------------------------------------------------------------------
    # Validation et verrous
    # ------------------------------------------------------------------

    def test_confirm_validates_draft_vouchers(self):
        voucher = self._voucher(3)
        self.assertEqual(voucher.state, 'draft')
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.action_confirm()
        self.assertEqual(invoice.state, 'confirmed')
        self.assertEqual(voucher.state, 'confirmed')

    def test_empty_invoice_cannot_be_confirmed(self):
        with self.assertRaises(UserError):
            self._invoice().action_confirm()

    def test_difference_requires_an_explanation(self):
        self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.statement_amount = 9000.0
        self.assertTrue(invoice.has_difference)
        self.assertAlmostEqual(invoice.amount_difference, 250.0)
        with self.assertRaises(UserError):
            invoice.action_confirm()
        invoice.difference_reason = "Bon 9004 servi mais non remis au secrétariat."
        invoice.action_confirm()
        self.assertEqual(invoice.state, 'confirmed')

    def test_zero_statement_is_not_a_difference(self):
        """Zéro veut dire « relevé non saisi », pas « la station ne demande rien »."""
        self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        self.assertFalse(invoice.has_difference)
        self.assertAlmostEqual(invoice.amount_difference, 0.0)

    def test_confirmed_invoice_locks_its_vouchers(self):
        voucher = self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.action_confirm()
        # Même repassé en brouillon, le bon reste figé par la facture.
        voucher.action_draft()
        with self.assertRaises(ValidationError):
            voucher.quantity = 99.0
        with self.assertRaises(ValidationError):
            voucher.supplier_id = self.other_station

    def test_billed_voucher_cannot_be_cancelled_nor_deleted(self):
        voucher = self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        with self.assertRaises(ValidationError):
            voucher.action_cancel()
        with self.assertRaises(ValidationError):
            voucher.unlink()

    def test_detaching_a_voucher_needs_a_draft_invoice(self):
        voucher = self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.action_confirm()
        with self.assertRaises(ValidationError):
            voucher.action_detach_invoice()
        invoice.action_draft()
        voucher.action_detach_invoice()
        self.assertFalse(voucher.invoice_id)

    def test_cancelling_an_invoice_releases_its_vouchers(self):
        voucher = self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.action_cancel()
        self.assertEqual(invoice.state, 'cancelled')
        self.assertFalse(voucher.invoice_id)
        self.assertFalse(voucher.is_invoiced)

    def test_payment_workflow(self):
        self._voucher(3)
        invoice = self._invoice()
        invoice.action_collect_vouchers()
        invoice.action_confirm()
        invoice.action_mark_paid()
        self.assertEqual(invoice.state, 'paid')
        self.assertTrue(invoice.payment_date)
        with self.assertRaises(UserError):
            invoice.action_draft()
        invoice.action_unpay()
        self.assertEqual(invoice.state, 'confirmed')
        self.assertFalse(invoice.payment_date)

    @mute_logger('odoo.sql_db')
    def test_one_invoice_per_station_and_quinzaine(self):
        """Garde-fou en base : deux secrétaires ne peuvent pas doubler la facture."""
        self._invoice()
        self.env.flush_all()
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self._invoice()
                self.env.flush_all()

    def test_a_cancelled_invoice_frees_the_period(self):
        first = self._invoice()
        first.action_cancel()
        self.env.flush_all()
        second = self._invoice()
        self.env.flush_all()
        self.assertTrue(second.id)


@tagged('post_install', '-at_install')
class TestFuelInvoiceWizards(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.Invoice = cls.env['secretariat.fuel.invoice']
        cls.fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.station = cls.env['res.partner'].create({
            'name': 'Yasmina Petroleum', 'is_company': True})
        cls.other_station = cls.env['res.partner'].create({
            'name': 'Station Zone 4', 'is_company': True})
        cls.env.company.secretariat_fuel_station_id = cls.station
        cls.today = reference_month(cls.env)

    def _voucher(self, day, station, **overrides):
        values = {
            'name': overrides.pop('name', False) or str(8000 + day),
            'date': self.today.replace(day=day),
            'fuel_type_id': self.fuel.id,
            'supplier_id': station.id if station else False,
            'price_unit': 875.0,
            'quantity': 10.0,
        }
        values.update(overrides)
        return self.Voucher.create(values)

    def _wizard(self, half='first', **overrides):
        values = {
            'period_year': self.today.year,
            'period_month': str(self.today.month),
            'period_half': half,
        }
        values.update(overrides)
        return self.env['secretariat.fuel.invoice.generate.wizard'].create(values)

    def test_generate_creates_one_invoice_per_station(self):
        self._voucher(3, self.station)
        self._voucher(4, self.station, name='8104')
        self._voucher(5, self.other_station)

        wizard = self._wizard()
        self.assertEqual(wizard.candidate_count, 3)
        wizard.action_generate()

        invoices = self.Invoice.search([])
        self.assertEqual(len(invoices), 2)
        by_station = {inv.station_id: inv for inv in invoices}
        self.assertEqual(by_station[self.station].voucher_count, 2)
        self.assertEqual(by_station[self.other_station].voucher_count, 1)

    def test_generate_attaches_vouchers_without_station(self):
        orphan = self._voucher(3, False)
        self.assertFalse(orphan.supplier_id)
        self._wizard().action_generate()
        self.assertEqual(orphan.supplier_id, self.station)
        self.assertTrue(orphan.invoice_id)

    def test_generate_leaves_orphans_alone_without_a_default_station(self):
        self.env.company.secretariat_fuel_station_id = False
        orphan = self._voucher(3, False)
        wizard = self._wizard()
        self.assertEqual(wizard.candidate_count, 0)
        self.assertEqual(wizard.orphan_count, 1)
        with self.assertRaises(UserError):
            wizard.action_generate()
        self.assertFalse(orphan.invoice_id)

    def test_generate_completes_an_existing_draft_invoice(self):
        self._voucher(3, self.station)
        self._wizard().action_generate()
        self._voucher(6, self.station, name='8106')
        self._wizard().action_generate()
        invoices = self.Invoice.search([])
        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.voucher_count, 2)

    def test_generate_does_not_touch_a_confirmed_invoice(self):
        self._voucher(3, self.station)
        self._wizard().action_generate()
        self.Invoice.search([]).action_confirm()
        self._voucher(6, self.station, name='8106')
        with self.assertRaises(UserError):
            self._wizard().action_generate()

    # ------------------------------------------------------------------
    # État annuel
    # ------------------------------------------------------------------

    def _annual_wizard(self):
        return self.env['secretariat.fuel.invoice.annual.wizard'].create({
            'year': self.today.year})

    def test_annual_report_data(self):
        self._voucher(3, self.station)
        self._voucher(20, self.other_station)
        self._wizard().action_generate()
        self._wizard(half='second').action_generate()

        data = self._annual_wizard().report_data()
        self.assertEqual(data['invoice_count'], 2)
        self.assertEqual(data['voucher_count'], 2)
        self.assertAlmostEqual(data['grand_total'], 17500.0)
        self.assertAlmostEqual(
            data['month_totals'][self.today.month], 17500.0)
        self.assertEqual(len(data['months']), 12)

    def test_annual_report_pdf_renders(self):
        self._voucher(3, self.station)
        self._wizard().action_generate()
        wizard = self._annual_wizard()
        html = self.env['ir.actions.report']._render_qweb_html(
            'ivorycocoa_secretariat.report_fuel_invoice_annual', wizard.ids)[0]
        self.assertIn(b'ANNUEL', html)

    def test_annual_report_xlsx(self):
        self._voucher(3, self.station)
        self._wizard().action_generate()
        wizard = self._annual_wizard()
        wizard.action_generate_xlsx()
        self.assertEqual(wizard.state, 'done')
        self.assertTrue(wizard.file_data)
        self.assertTrue(base64.b64decode(wizard.file_data).startswith(b'PK'))

    def test_annual_report_refuses_an_empty_year(self):
        with self.assertRaises(UserError):
            self._annual_wizard().action_print()

    def test_invoice_pdf_renders(self):
        self._voucher(3, self.station)
        self._wizard().action_generate()
        invoice = self.Invoice.search([])
        html = self.env['ir.actions.report']._render_qweb_html(
            'ivorycocoa_secretariat.report_fuel_invoice', invoice.ids)[0]
        self.assertIn(b'FACTURE DE STATION-SERVICE', html)
        self.assertIn(invoice.name.encode(), html)

    # ------------------------------------------------------------------
    # Tableau de bord
    # ------------------------------------------------------------------

    def test_billing_dashboard_section(self):
        self._voucher(3, self.station)
        data = self.env['secretariat.dashboard'].get_dashboard_data('month')
        self.assertIn('billing', data, "La section facturation a échoué.")
        billing = data['billing']
        self.assertEqual(billing['pending_total']['count'], 1)
        self.assertTrue(billing['current_period']['label'])

        self._wizard().action_generate()
        data = self.env['secretariat.dashboard'].get_dashboard_data('month')
        self.assertEqual(data['billing']['stats']['draft_count'], 1)

    def test_billing_dashboard_flags_vouchers_without_station(self):
        self._voucher(3, False)
        data = self.env['secretariat.dashboard'].get_dashboard_data('month')
        actions = [reco['action'] for reco in data['billing']['recommendations']]
        self.assertIn('no_station', actions)
