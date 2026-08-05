# -*- coding: utf-8 -*-
"""Tests du tableau de bord du secrétariat.

Le composant Owl ne calcule rien : tout vient de
``secretariat.dashboard.get_dashboard_data``. C'est donc cette méthode qui est
mise à l'épreuve — structure du retour, comparaison de périodes, compteurs
« à traiter », détection des doublons et des consommations inhabituelles.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSecretariatDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Dashboard = cls.env['secretariat.dashboard']
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.super_fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.gasoil = cls.env.ref('ivorycocoa_secretariat.fuel_type_gasoil')
        cls.today = fields.Date.context_today(cls.Voucher)
        cls.month_start = cls.today.replace(day=1)
        cls.vehicle = cls.env['secretariat.vehicle'].create({'name': 'Camion TDB'})
        cls.beneficiary = cls.env['secretariat.fuel.beneficiary'].create({
            'name': 'Usine TDB', 'category': 'department'})

    def _voucher(self, name, quantity, date=None, fuel=None, **overrides):
        values = {
            'name': name,
            'date': date or self.month_start,
            'vehicle_id': self.vehicle.id,
            'beneficiary_id': self.beneficiary.id,
            'fuel_type_id': (fuel or self.super_fuel).id,
            'price_unit': 875.0,
            'quantity': quantity,
        }
        values.update(overrides)
        return self.Voucher.create(values)

    # =========================================================================
    # STRUCTURE
    # =========================================================================

    def test_payload_shape(self):
        data = self.Dashboard.get_dashboard_data('month')
        self.assertIn('fuel', data['sections'])
        for key in ('period', 'currency', 'user', 'fuel'):
            self.assertIn(key, data)
        for key in ('stats', 'todo', 'monthly_trend', 'fuel_split',
                    'top_vehicles', 'top_beneficiaries', 'quota_watch',
                    'anomalies', 'recent', 'price_reference'):
            self.assertIn(key, data['fuel'])
        self.assertEqual(len(data['fuel']['monthly_trend']), 12)

    def test_every_period_is_accepted(self):
        for period in ('month', 'quarter', 'year', 'last12'):
            data = self.Dashboard.get_dashboard_data(period)
            self.assertTrue(data['period']['label'])
            self.assertTrue(data['period']['date_from'] <= data['period']['date_to'])

    def test_custom_period_swaps_reversed_dates(self):
        data = self.Dashboard.get_dashboard_data(
            'custom', str(self.today), str(self.today - relativedelta(days=10)))
        self.assertTrue(data['period']['date_from'] <= data['period']['date_to'])

    # =========================================================================
    # CHIFFRES
    # =========================================================================

    def test_totals_and_variation_against_the_previous_period(self):
        self._voucher('D1', 10)
        self._voucher('D2', 30)
        previous_month = self.month_start - relativedelta(months=1)
        self._voucher('D0', 20, date=previous_month)

        stats = self.Dashboard.get_dashboard_data('month')['fuel']['stats']
        self.assertEqual(stats['voucher_count'], 2)
        self.assertAlmostEqual(stats['quantity'], 40.0)
        self.assertAlmostEqual(stats['amount'], 35000.0)
        self.assertAlmostEqual(stats['previous_quantity'], 20.0)
        # 40 contre 20 sur la période précédente : +100 %
        self.assertAlmostEqual(stats['quantity_delta'], 100.0)

    def test_variation_is_none_without_a_comparison_base(self):
        stats = self.Dashboard.get_dashboard_data('month')['fuel']['stats']
        self.assertIsNone(stats['amount_delta'])

    def test_cancelled_vouchers_are_excluded(self):
        voucher = self._voucher('D3', 50)
        voucher.action_cancel()
        stats = self.Dashboard.get_dashboard_data('month')['fuel']['stats']
        self.assertEqual(stats['voucher_count'], 0)

    def test_split_by_fuel(self):
        self._voucher('D4', 10, fuel=self.super_fuel)
        self._voucher('D5', 10, fuel=self.gasoil)
        split = self.Dashboard.get_dashboard_data('month')['fuel']['fuel_split']
        names = {row['name'] for row in split}
        self.assertEqual(names, {'Super', 'Gasoil'})

    # =========================================================================
    # À TRAITER
    # =========================================================================

    def test_todo_counts_drafts_and_incomplete_vouchers(self):
        self._voucher('D6', 10)                       # complet, brouillon
        self._voucher('D7', 10, vehicle_id=False)     # incomplet
        todo = self.Dashboard.get_dashboard_data('month')['fuel']['todo']
        self.assertEqual(todo['draft'], 2)
        self.assertEqual(todo['incomplete'], 1)

    def test_todo_detects_exact_re_entries(self):
        """Même numéro, même date, même engin, même carburant, même quantité."""
        self._voucher('D8', 25)
        twin = self._voucher('D8', 25)
        todo = self.Dashboard.get_dashboard_data('month')['fuel']['todo']
        self.assertEqual(todo['duplicates'], 1)
        self.assertEqual(todo['duplicate_ids'], [twin.id])

    def test_a_same_number_on_two_fuels_is_not_a_duplicate(self):
        """Le carnet fait légitimement revenir un numéro sur deux carburants."""
        self._voucher('D9', 25, fuel=self.super_fuel)
        self._voucher('D9', 25, fuel=self.gasoil)
        todo = self.Dashboard.get_dashboard_data('month')['fuel']['todo']
        self.assertEqual(todo['duplicates'], 0)

    def test_todo_counts_vehicles_over_quota(self):
        self.vehicle.monthly_quota = 10.0
        self._voucher('D10', 50)
        todo = self.Dashboard.get_dashboard_data('month')['fuel']['todo']
        self.assertEqual(todo['over_quota'], 1)

    # =========================================================================
    # ANOMALIES
    # =========================================================================

    def test_anomaly_needs_history(self):
        """Une première consommation, si grosse soit-elle, n'est pas signalée."""
        self._voucher('D11', 500)
        anomalies = self.Dashboard.get_dashboard_data('month')['fuel']['anomalies']
        self.assertFalse(anomalies)

    def test_anomaly_flags_a_month_well_above_the_habit(self):
        for offset in (1, 2, 3):
            self._voucher('H%s' % offset, 20,
                          date=self.month_start - relativedelta(months=offset))
        self._voucher('D12', 200)
        anomalies = self.Dashboard.get_dashboard_data('month')['fuel']['anomalies']
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0]['id'], self.vehicle.id)
        self.assertAlmostEqual(anomalies[0]['average'], 20.0)
        self.assertGreater(anomalies[0]['deviation'], 100.0)

    def test_a_steady_month_is_not_flagged(self):
        for offset in (1, 2, 3):
            self._voucher('S%s' % offset, 100,
                          date=self.month_start - relativedelta(months=offset))
        self._voucher('D13', 105)
        anomalies = self.Dashboard.get_dashboard_data('month')['fuel']['anomalies']
        self.assertFalse(anomalies)

    # =========================================================================
    # TENDANCE
    # =========================================================================

    def test_trend_fills_the_empty_months(self):
        self._voucher('D14', 12)
        trend = self.Dashboard.get_dashboard_data('month')['fuel']['monthly_trend']
        self.assertEqual(len(trend), 12)
        current = trend[-1]
        self.assertEqual(current['month'], self.month_start.strftime('%Y-%m'))
        self.assertAlmostEqual(current['quantity'], 12.0)
        # Les mois sans bon existent quand même, à zéro : la courbe ne saute pas.
        self.assertTrue(all('quantity' in month for month in trend))

    def test_a_broken_section_does_not_break_the_screen(self):
        """La coque encaisse une section en erreur et rend le reste."""
        original = type(self.env['secretariat.dashboard.fuel']).get_section_data

        def boom(self, *args, **kwargs):
            raise ValueError("section HS")

        type(self.env['secretariat.dashboard.fuel']).get_section_data = boom
        try:
            data = self.Dashboard.get_dashboard_data('month')
        finally:
            type(self.env['secretariat.dashboard.fuel']).get_section_data = original
        self.assertEqual(data['sections'], [])
        self.assertIn('period', data)
