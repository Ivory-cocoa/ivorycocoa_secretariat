# -*- coding: utf-8 -*-
"""Tests du modèle « bon de carburant »."""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import SecretariatCase


@tagged('post_install', '-at_install')
class TestFuelVoucher(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.super = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.vehicle = cls.env['secretariat.vehicle'].create({'name': '2900 LP01'})
        cls.beneficiary = cls.env['secretariat.fuel.beneficiary'].create({
            'name': 'Daf', 'category': 'employee'})

    def _voucher(self, **overrides):
        values = {
            'name': '6275',
            'date': '2026-01-07',
            'vehicle_id': self.vehicle.id,
            'beneficiary_id': self.beneficiary.id,
            'fuel_type_id': self.super.id,
            'price_unit': 820.0,
            'quantity': 50.0,
        }
        values.update(overrides)
        return self.env['secretariat.fuel.voucher'].create(values)

    def test_amount_total_is_price_times_quantity(self):
        voucher = self._voucher()
        self.assertAlmostEqual(voucher.amount_total, 41000.0)
        voucher.quantity = 35.0
        self.assertAlmostEqual(voucher.amount_total, 28700.0)

    def test_price_defaults_to_fuel_type_price(self):
        """Sans prix saisi, le prix courant du carburant est repris."""
        voucher = self._voucher(price_unit=0.0)
        self.assertAlmostEqual(voucher.price_unit, self.super.price)

    def test_incomplete_flag(self):
        complete = self._voucher()
        self.assertFalse(complete.is_incomplete)
        without_vehicle = self._voucher(vehicle_id=False)
        self.assertTrue(without_vehicle.is_incomplete)

    def test_negative_quantity_is_refused(self):
        with self.assertRaises(ValidationError):
            self._voucher(quantity=-5.0)

    def test_confirmed_voucher_is_locked(self):
        voucher = self._voucher()
        voucher.action_confirm()
        self.assertEqual(voucher.state, 'confirmed')
        with self.assertRaises(ValidationError):
            voucher.quantity = 10.0
        with self.assertRaises(ValidationError):
            voucher.unlink()
        # Le retour en brouillon rouvre la modification
        voucher.action_draft()
        voucher.quantity = 10.0
        self.assertAlmostEqual(voucher.amount_total, 8200.0)

    def test_duplicate_numbers_are_allowed(self):
        """Le même numéro de souche peut couvrir deux carburants le même jour."""
        lubricant = self.env.ref('ivorycocoa_secretariat.fuel_type_lubrifiant')
        first = self._voucher()
        second = self._voucher(fuel_type_id=lubricant.id, quantity=1.0,
                               price_unit=3500.0)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.name, second.name)

    def test_referential_matching_is_accent_and_case_insensitive(self):
        FuelType = self.env['secretariat.fuel.type']
        self.assertEqual(FuelType.find_or_create('  SUPER '), self.super)
        self.assertEqual(FuelType.find_or_create('super'), self.super)
        created = FuelType.find_or_create('Éthanol')
        self.assertEqual(created.name, 'Éthanol')
        self.assertEqual(FuelType.find_or_create('ethanol'), created)

    # =========================================================================
    # VERROU DE VALIDATION
    # =========================================================================

    def test_rewriting_the_same_value_on_a_confirmed_voucher_is_allowed(self):
        """Le verrou refuse un CHANGEMENT, pas une écriture à l'identique.

        Sans cette nuance, l'édition multiple sur une colonne déjà à la bonne
        valeur — ou une simple re-sauvegarde — échouerait sans raison.
        """
        voucher = self._voucher()
        voucher.action_confirm()
        voucher.write({'quantity': 50.0, 'price_unit': 820.0})  # valeurs actuelles
        self.assertEqual(voucher.state, 'confirmed')
        with self.assertRaises(ValidationError):
            voucher.write({'quantity': 51.0})

    def test_a_confirmed_voucher_still_accepts_its_free_fields(self):
        voucher = self._voucher()
        voucher.action_confirm()
        voucher.note = "Vu avec la direction"
        self.assertEqual(voucher.note, "Vu avec la direction")

    def test_a_voucher_without_price_cannot_be_confirmed(self):
        voucher = self._voucher(price_unit=0.0, fuel_type_id=self.env.ref(
            'ivorycocoa_secretariat.fuel_type_super').id)
        voucher.price_unit = 0.0
        with self.assertRaises(ValidationError):
            voucher.action_confirm()

    # =========================================================================
    # GARDE-FOUS DE SAISIE
    # =========================================================================

    def test_an_absurd_future_date_is_refused(self):
        far = fields.Date.context_today(self.env['secretariat.fuel.voucher']) \
            + relativedelta(years=2)
        with self.assertRaises(ValidationError):
            self._voucher(date=far)

    def test_a_date_in_the_near_future_only_warns(self):
        tomorrow = fields.Date.context_today(
            self.env['secretariat.fuel.voucher']) + relativedelta(days=1)
        voucher = self._voucher(date=tomorrow)  # accepté
        draft = self.env['secretariat.fuel.voucher'].new({'date': tomorrow})
        result = draft._onchange_date_in_future()
        self.assertTrue(result and 'warning' in result)
        self.assertEqual(voucher.date, tomorrow)

    def test_a_probable_re_entry_is_signalled(self):
        self._voucher()
        draft = self.env['secretariat.fuel.voucher'].new({
            'name': '6275', 'date': '2026-01-07'})
        result = draft._onchange_check_duplicate()
        self.assertTrue(result and 'warning' in result)
        self.assertIn("6275", result['warning']['message'])

    def test_a_new_number_raises_no_warning(self):
        self._voucher()
        draft = self.env['secretariat.fuel.voucher'].new({
            'name': '9999', 'date': '2026-01-07'})
        self.assertFalse(draft._onchange_check_duplicate())

    def test_changing_the_fuel_realigns_the_proposed_price(self):
        """« Super » corrigé en « Gasoil » ne doit pas garder le prix du Super."""
        gasoil = self.env.ref('ivorycocoa_secretariat.fuel_type_gasoil')
        draft = self.env['secretariat.fuel.voucher'].new({
            'fuel_type_id': self.super.id})
        draft._onchange_fuel_type_id()
        self.assertAlmostEqual(draft.price_unit, self.super.price)
        draft.fuel_type_id = gasoil
        draft._onchange_fuel_type_id()
        self.assertAlmostEqual(draft.price_unit, gasoil.price)

    def test_a_hand_typed_price_survives_a_fuel_change(self):
        gasoil = self.env.ref('ivorycocoa_secretariat.fuel_type_gasoil')
        draft = self.env['secretariat.fuel.voucher'].new({
            'fuel_type_id': self.super.id, 'price_unit': 913.0})
        draft.fuel_type_id = gasoil
        draft._onchange_fuel_type_id()
        self.assertAlmostEqual(draft.price_unit, 913.0)

    def test_the_number_is_trimmed_on_write(self):
        voucher = self._voucher(name='  6275  ')
        self.assertEqual(voucher.name, '6275')
