# -*- coding: utf-8 -*-
"""Tests des deux rapports PDF.

Le rendu HTML suffit à vérifier que les templates s'évaluent et que les
données attendues y figurent ; produire le PDF ferait dépendre la suite de
wkhtmltopdf sans rien prouver de plus sur le contenu.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import SecretariatCase


@tagged('post_install', '-at_install')
class TestSecretariatReports(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.Wizard = cls.env['secretariat.fuel.monthly.report.wizard']
        cls.super_fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.today = fields.Date.context_today(cls.Voucher)
        cls.month_start = cls.today.replace(day=1)
        cls.month_end = cls.month_start + relativedelta(months=1, days=-1)
        cls.vehicle = cls.env['secretariat.vehicle'].create({
            'name': '4242 RP01', 'license_plate': '4242 RP01', 'monthly_quota': 10.0})
        cls.beneficiary = cls.env['secretariat.fuel.beneficiary'].create({
            'name': 'Direction', 'category': 'department'})
        cls.voucher = cls.Voucher.create({
            'name': '9001',
            'date': cls.month_start,
            'vehicle_id': cls.vehicle.id,
            'beneficiary_id': cls.beneficiary.id,
            'fuel_type_id': cls.super_fuel.id,
            'price_unit': 875.0,
            'quantity': 40.0,
        })

    def _render(self, xml_id, records):
        report = self.env.ref(xml_id)
        html, _type = report._render_qweb_html(report.report_name, records.ids)
        return html.decode('utf-8') if isinstance(html, bytes) else html

    # =========================================================================
    # BON DE CARBURANT
    # =========================================================================

    def test_voucher_report_renders(self):
        html = self._render(
            'ivorycocoa_secretariat.action_report_fuel_voucher', self.voucher)
        self.assertIn('BON DE CARBURANT', html)
        self.assertIn('9001', html)
        self.assertIn('4242 RP01', html)
        self.assertIn('Direction', html)
        self.assertIn('Le bénéficiaire', html)

    def test_voucher_report_marks_a_cancelled_voucher(self):
        self.voucher.action_cancel()
        html = self._render(
            'ivorycocoa_secretariat.action_report_fuel_voucher', self.voucher)
        self.assertIn('BON ANNUL', html)

    def test_voucher_report_handles_an_incomplete_voucher(self):
        """Les bons repris sans engin ni bénéficiaire doivent rester imprimables."""
        bare = self.Voucher.create({
            'name': '9002',
            'date': self.month_start,
            'fuel_type_id': self.super_fuel.id,
            'price_unit': 875.0,
            'quantity': 5.0,
        })
        html = self._render(
            'ivorycocoa_secretariat.action_report_fuel_voucher', bare)
        self.assertIn('9002', html)

    def test_action_print_returns_the_report_action(self):
        action = self.voucher.action_print()
        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(
            action['report_name'], 'ivorycocoa_secretariat.report_fuel_voucher')

    # =========================================================================
    # ÉTAT RÉCAPITULATIF
    # =========================================================================

    def _wizard(self, **overrides):
        return self.Wizard.create(dict({
            'date_from': self.month_start,
            'date_to': self.month_end,
        }, **overrides))

    def test_monthly_report_renders_with_totals_and_breakdowns(self):
        html = self._render(
            'ivorycocoa_secretariat.action_report_fuel_monthly', self._wizard())
        self.assertIn('ÉTAT RÉCAPITULATIF', html)
        self.assertIn('Par carburant', html)
        self.assertIn('Par engin', html)
        self.assertIn('Par bénéficiaire', html)
        self.assertIn('4242 RP01', html)

    def test_monthly_report_lists_quota_overruns(self):
        """40 servis pour une dotation de 10 : le dépassement doit apparaître."""
        data = self._wizard().report_data()
        self.assertEqual(len(data['over_quota']), 1)
        self.assertEqual(data['over_quota'][0]['name'], '4242 RP01')
        self.assertAlmostEqual(data['over_quota'][0]['rate'], 400.0)

    def test_monthly_report_can_exclude_drafts(self):
        wizard = self._wizard(include_draft=False)
        self.assertEqual(wizard.report_data()['count'], 0)
        self.voucher.action_confirm()
        self.assertEqual(wizard.report_data()['count'], 1)

    def test_monthly_report_filters_are_applied(self):
        other = self.env['secretariat.vehicle'].create({'name': 'Autre engin'})
        wizard = self._wizard(vehicle_ids=[(6, 0, other.ids)])
        self.assertEqual(wizard.report_data()['count'], 0)

    def test_monthly_report_refuses_an_empty_period(self):
        wizard = self._wizard(
            date_from='2000-01-01', date_to='2000-01-31')
        with self.assertRaises(UserError):
            wizard.action_print()

    def test_monthly_report_refuses_reversed_dates(self):
        wizard = self._wizard(date_from=self.month_end, date_to=self.month_start)
        with self.assertRaises(UserError):
            wizard.action_print()

    def test_previous_month_shortcut(self):
        wizard = self._wizard()
        wizard.action_previous_month()
        expected = self.month_start - relativedelta(months=1)
        self.assertEqual(wizard.date_from, expected)
        self.assertEqual(wizard.date_to, expected + relativedelta(months=1, days=-1))

    def test_period_label_is_a_month_name_when_it_matches_a_month(self):
        self.assertNotIn('du ', self._wizard()._period_label())
        partial = self._wizard(date_to=self.month_start)
        self.assertIn('du ', partial._period_label())
