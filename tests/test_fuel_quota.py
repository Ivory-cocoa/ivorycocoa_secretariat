# -*- coding: utf-8 -*-
"""Tests de la dotation mensuelle par engin.

La dotation ne bloque jamais une saisie : le secrétariat constate ce qui a été
servi, il ne l'autorise pas. Elle alimente un avertissement, un filtre et une
pastille du tableau de bord — c'est ce que ces tests vérifient.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFuelQuota(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.super_fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.today = fields.Date.context_today(cls.env['secretariat.vehicle'])
        cls.month_start = cls.today.replace(day=1)
        cls.vehicle = cls.env['secretariat.vehicle'].create({
            'name': 'Pick-up dotation',
            'monthly_quota': 100.0,
        })
        cls.beneficiary = cls.env['secretariat.fuel.beneficiary'].create({
            'name': 'Service technique', 'category': 'department'})

    def _voucher(self, quantity, date=None, vehicle=None):
        return self.Voucher.create({
            'name': 'Q%s' % quantity,
            'date': date or self.month_start,
            'vehicle_id': (vehicle or self.vehicle).id,
            'beneficiary_id': self.beneficiary.id,
            'fuel_type_id': self.super_fuel.id,
            'price_unit': 875.0,
            'quantity': quantity,
        })

    def test_month_usage_counts_only_the_current_month(self):
        self._voucher(40)
        previous_month = self.month_start - relativedelta(months=1)
        self._voucher(90, date=previous_month)
        self.assertAlmostEqual(self.vehicle.month_quantity, 40.0)
        self.assertAlmostEqual(self.vehicle.quota_usage_rate, 40.0)
        self.assertFalse(self.vehicle.is_over_quota)

    def test_cancelled_vouchers_do_not_consume_the_quota(self):
        voucher = self._voucher(120)
        self.assertTrue(self.vehicle.is_over_quota)
        voucher.action_cancel()
        self.vehicle.invalidate_recordset()
        self.assertAlmostEqual(self.vehicle.month_quantity, 0.0)
        self.assertFalse(self.vehicle.is_over_quota)

    def test_over_quota_is_searchable(self):
        self._voucher(150)
        self.vehicle.invalidate_recordset()
        found = self.env['secretariat.vehicle'].search([('is_over_quota', '=', True)])
        self.assertIn(self.vehicle, found)
        excluded = self.env['secretariat.vehicle'].search([('is_over_quota', '=', False)])
        self.assertNotIn(self.vehicle, excluded)

    def test_a_vehicle_without_quota_is_never_flagged(self):
        free = self.env['secretariat.vehicle'].create({'name': 'Sans dotation'})
        self._voucher(500, vehicle=free)
        free.invalidate_recordset()
        self.assertAlmostEqual(free.quota_usage_rate, 0.0)
        self.assertFalse(free.is_over_quota)

    def test_budget_quota_is_taken_into_account(self):
        """Le plus contraignant des deux plafonds l'emporte."""
        self.vehicle.write({'monthly_quota': 1000.0, 'monthly_budget': 50000.0})
        self._voucher(100)  # 100 × 875 = 87 500, soit 175 % du budget
        self.vehicle.invalidate_recordset()
        self.assertTrue(self.vehicle.is_over_quota)
        self.assertAlmostEqual(self.vehicle.quota_usage_rate, 175.0, places=1)

    def test_negative_quota_is_refused(self):
        with self.assertRaises(ValidationError):
            self.vehicle.monthly_quota = -1.0

    def test_saisie_warns_when_the_quota_is_exceeded(self):
        self._voucher(90)
        draft = self.Voucher.new({
            'name': 'W1',
            'date': self.month_start,
            'vehicle_id': self.vehicle.id,
            'fuel_type_id': self.super_fuel.id,
            'price_unit': 875.0,
            'quantity': 30.0,
        })
        result = draft._onchange_check_quota()
        self.assertTrue(result and 'warning' in result)
        self.assertIn("Plafond mensuel", result['warning']['title'])

    def test_saisie_stays_silent_below_the_quota(self):
        self._voucher(10)
        draft = self.Voucher.new({
            'name': 'W2',
            'date': self.month_start,
            'vehicle_id': self.vehicle.id,
            'fuel_type_id': self.super_fuel.id,
            'price_unit': 875.0,
            'quantity': 20.0,
        })
        self.assertFalse(draft._onchange_check_quota())
