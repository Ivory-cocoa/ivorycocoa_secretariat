# -*- coding: utf-8 -*-
"""Tests du rapprochement et de la fusion des référentiels.

L'import crée un engin par libellé rencontré ; la reprise du classeur de
référence en a produit 212. Les doublons sont inévitables — la normalisation
rattrape la casse et les accents, pas « Camara » contre « Camara M. ».
"""

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import SecretariatCase


@tagged('post_install', '-at_install')
class TestReferentialMerge(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.Vehicle = cls.env['secretariat.vehicle']
        cls.Beneficiary = cls.env['secretariat.fuel.beneficiary']
        cls.Wizard = cls.env['secretariat.merge.wizard']
        cls.super_fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')

    def _voucher(self, name, vehicle=None, beneficiary=None, state='draft'):
        return self.Voucher.create({
            'name': name,
            'date': '2026-03-10',
            'vehicle_id': vehicle.id if vehicle else False,
            'beneficiary_id': beneficiary.id if beneficiary else False,
            'fuel_type_id': self.super_fuel.id,
            'price_unit': 875.0,
            'quantity': 10.0,
            'state': state,
        })

    def _merge(self, records, target):
        wizard = self.Wizard.with_context(
            active_model=records._name, active_ids=records.ids).create({})
        field = ('target_vehicle_id' if wizard.kind == 'vehicle'
                 else 'target_beneficiary_id')
        wizard.write({field: target.id})
        return wizard

    # =========================================================================
    # RAPPROCHEMENT DES LIBELLÉS
    # =========================================================================

    def test_close_labels_are_flagged(self):
        first = self.Beneficiary.create({'name': 'Camara'})
        second = self.Beneficiary.create({'name': 'Camara M'})
        flagged = self.Beneficiary.search([('has_similar', '=', True)])
        self.assertIn(first, flagged)
        self.assertIn(second, flagged)

    def test_a_lone_label_is_not_flagged(self):
        alone = self.Beneficiary.create({'name': 'Chambre de commerce'})
        self.assertFalse(alone.has_similar)

    def test_distinct_labels_are_not_confused(self):
        """« Moto A » et « Moto B » se ressemblent sans être la même chose."""
        self.Vehicle.create({'name': 'Moto A'})
        self.Vehicle.create({'name': 'Moto B'})
        self.assertFalse(self.Vehicle.search([('has_similar', '=', True)]))

    def test_short_labels_are_left_alone(self):
        """« B12 » et « B13 » se ressemblent à 67 % — et sont deux produits."""
        self.Vehicle.create({'name': 'B12'})
        self.Vehicle.create({'name': 'B13'})
        self.assertFalse(self.Vehicle.search([('has_similar', '=', True)]))

    def test_plates_differing_by_a_digit_are_not_confused(self):
        """« AA 720 AC01 » et « AA 790 AC01 » sont deux véhicules.

        Cas relevé sur les données réelles : la seule ressemblance textuelle
        les rapprochait, à tort.
        """
        self.Vehicle.create({'name': 'AA 720 AC01'})
        self.Vehicle.create({'name': 'AA 790 AC01'})
        self.assertFalse(self.Vehicle.search([('has_similar', '=', True)]))

    def test_the_same_plate_written_two_ways_is_still_flagged(self):
        spaced = self.Vehicle.create({'name': 'AA 845 KT01'})
        dashed = self.Vehicle.create({'name': 'AA-845KT01'})
        flagged = self.Vehicle.search([('has_similar', '=', True)])
        self.assertIn(spaced, flagged)
        self.assertIn(dashed, flagged)

    def test_a_plate_suffix_does_not_break_the_pairing(self):
        """« AA 892 NJ » et « AA 892 NJ01 » : même plaque, notée deux fois."""
        short = self.Vehicle.create({'name': 'AA 892 NJ'})
        long = self.Vehicle.create({'name': 'AA 892 NJ01'})
        self.assertEqual(short.similar_records(), long)

    def test_similar_records_lists_the_neighbours(self):
        keeper = self.Vehicle.create({'name': '2900 LP01'})
        twin = self.Vehicle.create({'name': '2900 LP 01'})
        self.assertEqual(keeper.similar_records(), twin)

    def test_unsupported_operator_is_refused(self):
        with self.assertRaises(UserError):
            self.Vehicle.search([('has_similar', '>', 0)])

    # =========================================================================
    # FUSION
    # =========================================================================

    def test_merge_moves_the_vouchers_and_removes_the_duplicate(self):
        keeper = self.Vehicle.create({'name': '2900 LP01'})
        twin = self.Vehicle.create({'name': '2900 LP 01'})
        self._voucher('M1', vehicle=keeper)
        self._voucher('M2', vehicle=twin)
        self._voucher('M3', vehicle=twin)

        wizard = self._merge(keeper | twin, keeper)
        self.assertEqual(wizard.voucher_count, 2)
        self.assertEqual(wizard.absorbed_names, '2900 LP 01')
        wizard.action_merge()

        self.assertFalse(twin.exists())
        self.assertEqual(keeper.voucher_count, 3)
        self.assertEqual(
            self.Voucher.search_count([('vehicle_id', '=', keeper.id)]), 3)

    def test_merge_moves_confirmed_vouchers_too(self):
        """Le verrou du bon validé ne doit pas empêcher une correction de
        référentiel : l'engin conservé désigne la même réalité."""
        keeper = self.Vehicle.create({'name': 'Fourchette'})
        twin = self.Vehicle.create({'name': 'Fourchete'})
        voucher = self._voucher('M4', vehicle=twin, state='confirmed')

        self._merge(keeper | twin, keeper).action_merge()
        self.assertEqual(voucher.vehicle_id, keeper)
        self.assertEqual(voucher.state, 'confirmed')

    def test_the_rest_of_the_lock_still_holds_during_a_merge(self):
        voucher = self._voucher('M5', state='confirmed')
        with self.assertRaises(ValidationError):
            voucher.with_context(secretariat_referential_merge=True).write(
                {'quantity': 99.0})

    def test_merge_keeps_a_trace_of_the_old_labels(self):
        keeper = self.Beneficiary.create({'name': 'Camara'})
        twin = self.Beneficiary.create({'name': 'Camara M'})
        self._voucher('M6', beneficiary=twin)

        self._merge(keeper | twin, keeper).action_merge()
        self.assertIn('Camara M', keeper.note)

    def test_beneficiaries_merge_as_well(self):
        keeper = self.Beneficiary.create({'name': 'Usine'})
        twin = self.Beneficiary.create({'name': 'Usines'})
        self._voucher('M7', beneficiary=twin)

        wizard = self._merge(keeper | twin, keeper)
        self.assertEqual(wizard.kind, 'beneficiary')
        wizard.action_merge()
        self.assertFalse(twin.exists())
        self.assertEqual(keeper.voucher_count, 1)

    def test_the_busiest_record_is_proposed_by_default(self):
        """Conserver celui qui porte le plus de bons, c'est en déplacer le moins."""
        small = self.Vehicle.create({'name': 'Car'})
        big = self.Vehicle.create({'name': 'Carr'})
        self._voucher('M8', vehicle=big)
        self._voucher('M9', vehicle=big)
        self._voucher('M10', vehicle=small)

        wizard = self.Wizard.with_context(
            active_model='secretariat.vehicle',
            active_ids=(small | big).ids).create({})
        self.assertEqual(wizard.target_vehicle_id, big)

    def test_a_single_record_cannot_be_merged(self):
        lonely = self.Vehicle.create({'name': 'Seul'})
        with self.assertRaises(UserError):
            self.Wizard.with_context(
                active_model='secretariat.vehicle',
                active_ids=lonely.ids).create({})

    def test_the_target_must_belong_to_the_selection(self):
        keeper = self.Vehicle.create({'name': 'Alpha'})
        twin = self.Vehicle.create({'name': 'Alphaa'})
        outsider = self.Vehicle.create({'name': 'Zoulou'})
        wizard = self._merge(keeper | twin, keeper)
        wizard.target_vehicle_id = outsider
        with self.assertRaises(UserError):
            wizard.action_merge()

    def test_merging_an_unsupported_model_is_refused(self):
        with self.assertRaises(UserError):
            self.Wizard.with_context(
                active_model='secretariat.fuel.type',
                active_ids=[1, 2]).create({})
