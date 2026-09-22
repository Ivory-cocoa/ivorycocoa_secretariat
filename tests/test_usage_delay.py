# -*- coding: utf-8 -*-
"""Écart entre l'émission d'un bon et son utilisation."""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import SecretariatCase


@tagged('post_install', '-at_install')
class TestUsageDelay(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.today = fields.Date.context_today(cls.Voucher)
        cls.env.company.write({
            'secretariat_usage_alert_days': 10,
            'secretariat_usage_critical_days': 30,
        })

    def _voucher(self, issued_days_ago, used_days_ago=None, **overrides):
        values = {
            'name': overrides.pop('name', False) or '1',
            'date': self.today - relativedelta(days=issued_days_ago),
            'fuel_type_id': self.fuel.id,
            'price_unit': 875.0,
            'quantity': 10.0,
        }
        if used_days_ago is not None:
            values['date_used'] = self.today - relativedelta(days=used_days_ago)
        values.update(overrides)
        return self.Voucher.create(values)

    # ------------------------------------------------------------------
    # Champs calculés
    # ------------------------------------------------------------------

    def test_effective_date_falls_back_on_issue_date(self):
        """Un bon repris de l'ancien classeur reste dans son mois d'origine."""
        voucher = self._voucher(issued_days_ago=40)
        self.assertEqual(voucher.date_effective, voucher.date)
        self.assertFalse(voucher.is_used)
        self.assertEqual(voucher.usage_delay_days, 0)

    def test_effective_date_is_the_usage_date(self):
        voucher = self._voucher(issued_days_ago=10, used_days_ago=3)
        self.assertEqual(voucher.date_effective, voucher.date_used)
        self.assertTrue(voucher.is_used)
        self.assertEqual(voucher.usage_delay_days, 7)

    def test_usage_before_issue_is_refused(self):
        with self.assertRaises(ValidationError):
            self._voucher(issued_days_ago=3, used_days_ago=10)

    def test_usage_in_the_future_is_refused(self):
        with self.assertRaises(ValidationError):
            self._voucher(issued_days_ago=3, used_days_ago=-1)

    def test_mark_used_today(self):
        voucher = self._voucher(issued_days_ago=5)
        voucher.action_mark_used_today()
        self.assertEqual(voucher.date_used, self.today)
        self.assertEqual(voucher.usage_delay_days, 5)

    def test_mark_used_today_keeps_an_already_dated_voucher(self):
        voucher = self._voucher(issued_days_ago=8, used_days_ago=6)
        voucher.action_mark_used_today()
        self.assertEqual(voucher.date_used, self.today - relativedelta(days=6))

    # ------------------------------------------------------------------
    # Suivi d'utilisation
    # ------------------------------------------------------------------

    def test_usage_alert_levels(self):
        self.assertEqual(self._voucher(issued_days_ago=2, name='1').usage_alert,
                         'pending')
        self.assertEqual(self._voucher(issued_days_ago=20, name='2').usage_alert,
                         'late')
        self.assertEqual(self._voucher(issued_days_ago=90, name='3').usage_alert,
                         'critical')
        self.assertEqual(
            self._voucher(issued_days_ago=90, used_days_ago=80, name='4').usage_alert,
            'used')

    def test_usage_alert_is_searchable(self):
        """Le champ n'est pas stocké : il doit rester filtrable par domaine."""
        pending = self._voucher(issued_days_ago=2, name='1')
        late = self._voucher(issued_days_ago=20, name='2')
        critical = self._voucher(issued_days_ago=90, name='3')
        used = self._voucher(issued_days_ago=90, used_days_ago=80, name='4')

        self.assertEqual(
            self.Voucher.search([('usage_alert', '=', 'critical')]), critical)
        self.assertEqual(
            self.Voucher.search([('usage_alert', 'in', ['late', 'critical'])]),
            late | critical)
        self.assertEqual(
            self.Voucher.search([('usage_alert', '=', 'used')]), used)
        self.assertEqual(
            self.Voucher.search([('usage_alert', '!=', 'used')]),
            pending | late | critical)

    def test_thresholds_fall_back_when_unset(self):
        """Une société sans paramétrage ne doit pas alerter sur tout."""
        self.env.company.write({
            'secretariat_usage_alert_days': 0,
            'secretariat_usage_critical_days': 0,
        })
        alert, critical = self.env.company._secretariat_usage_thresholds()
        self.assertEqual((alert, critical), (15, 30))
        self.assertEqual(self._voucher(issued_days_ago=3).usage_alert, 'pending')

    # ------------------------------------------------------------------
    # Tableau de bord
    # ------------------------------------------------------------------

    def test_dashboard_delay_statistics(self):
        # Trois bons utilisés dans le mois : délais de 1, 3 et 20 jours.
        self._voucher(issued_days_ago=1, used_days_ago=0, name='1')
        self._voucher(issued_days_ago=5, used_days_ago=2, name='2')
        self._voucher(issued_days_ago=21, used_days_ago=1, name='3')
        # Un bon jamais utilisé, très ancien.
        self._voucher(issued_days_ago=120, name='4')

        data = self.env['secretariat.dashboard'].get_dashboard_data('last12')
        delay = data['fuel']['usage_delay']

        self.assertEqual(delay['used_count'], 3)
        self.assertEqual(delay['median'], 3)
        self.assertAlmostEqual(delay['average'], 8.0, places=1)
        self.assertEqual(delay['max'], 20)
        self.assertEqual(delay['pending_count'], 1)
        self.assertEqual(delay['critical_count'], 1)
        self.assertEqual(sum(row['count'] for row in delay['buckets']), 3)

    def test_dashboard_recommends_chasing_old_vouchers(self):
        self._voucher(issued_days_ago=120, name='4')
        data = self.env['secretariat.dashboard'].get_dashboard_data('month')
        actions = [reco['action'] for reco in data['fuel']['recommendations']]
        self.assertIn('critical_usage', actions)

    def test_consumption_is_dated_by_usage(self):
        """Un bon émis le mois dernier et servi ce mois-ci pèse sur ce mois."""
        month_start = self.today.replace(day=1)
        self._voucher(
            issued_days_ago=(self.today - month_start).days + 5,
            used_days_ago=0, name='1')
        data = self.env['secretariat.dashboard'].get_dashboard_data('month')
        self.assertEqual(data['fuel']['stats']['voucher_count'], 1)
