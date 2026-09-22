# -*- coding: utf-8 -*-
"""Numérotation des bons : proposée sur huit caractères, jamais imposée."""

from odoo.tests import tagged

from .common import SecretariatCase


@tagged('post_install', '-at_install')
class TestFuelNumbering(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.sequence = cls.Voucher._number_sequence()
        cls.sequence.number_next_actual = 6275

    def _voucher(self, **overrides):
        values = {
            'date': '2026-01-07',
            'fuel_type_id': self.fuel.id,
            'price_unit': 875.0,
            'quantity': 10.0,
        }
        values.update(overrides)
        return self.Voucher.create(values)

    # ------------------------------------------------------------------
    # Proposition
    # ------------------------------------------------------------------

    def test_default_is_padded_to_eight_characters(self):
        self.assertEqual(self.Voucher.default_get(['name'])['name'], '00006275')

    def test_opening_a_form_does_not_consume_a_number(self):
        """Deux formulaires ouverts sans enregistrer proposent le même numéro.

        C'est tout l'intérêt de lire la séquence au lieu de la consommer :
        une saisie abandonnée ne doit pas trouer le carnet.
        """
        first = self.Voucher.default_get(['name'])['name']
        second = self.Voucher.default_get(['name'])['name']
        self.assertEqual(first, second)
        self.assertEqual(self.sequence.number_next_actual, 6275)

    def test_creation_advances_the_counter(self):
        self._voucher(name=self.Voucher.default_get(['name'])['name'])
        self.assertEqual(self.sequence.number_next_actual, 6276)
        self.assertEqual(self.Voucher.default_get(['name'])['name'], '00006276')

    def test_creation_without_number_falls_back_on_the_counter(self):
        """Une création par code (import, script) n'a jamais un numéro vide."""
        voucher = self._voucher()
        self.assertEqual(voucher.name, '00006275')

    # ------------------------------------------------------------------
    # Corrections manuelles
    # ------------------------------------------------------------------

    def test_manual_number_is_padded(self):
        self.assertEqual(self._voucher(name='6300').name, '00006300')

    def test_non_numeric_number_is_respected(self):
        """Tous les carnets ne portent pas des numéros purement numériques."""
        self.assertEqual(self._voucher(name=' B-12/A ').name, 'B-12/A')

    def test_counter_catches_up_after_a_manual_jump(self):
        self._voucher(name='6300')
        self.assertEqual(self.sequence.number_next_actual, 6301)

    def test_counter_never_goes_backwards(self):
        """Un bon saisi en retard ne doit pas faire redescendre le compteur."""
        self._voucher(name='6300')
        self._voucher(name='100')
        self.assertEqual(self.sequence.number_next_actual, 6301)

    def test_renaming_a_voucher_also_moves_the_counter(self):
        voucher = self._voucher(name='6275')
        voucher.name = '7000'
        self.assertEqual(voucher.name, '00007000')
        self.assertEqual(self.sequence.number_next_actual, 7001)

    # ------------------------------------------------------------------
    # Rapprochement avec l'historique
    # ------------------------------------------------------------------

    def test_dedup_key_ignores_padding(self):
        """« 6275 » et « 00006275 » désignent le même bon.

        L'historique a été saisi sans zéros de remplissage : sans cette
        normalisation, l'import recréerait tout le classeur.
        """
        legacy = self.Voucher._dedup_key('6275', '2026-01-07', 1, 1, 10.0)
        padded = self.Voucher._dedup_key('00006275', '2026-01-07', 1, 1, 10.0)
        self.assertEqual(legacy, padded)

    def test_duplicate_warning_sees_through_padding(self):
        """L'avertissement de re-saisie compare les valeurs, pas l'écriture."""
        self.env.cr.execute(
            "UPDATE secretariat_fuel_voucher SET name = %s WHERE id = %s",
            ('6275', self._voucher(name='6275').id))
        self.Voucher.invalidate_model(['name'])

        draft = self.Voucher.new({
            'name': '00006275',
            'date': '2026-01-07',
            'fuel_type_id': self.fuel.id,
        })
        warning = draft._onchange_check_duplicate()
        self.assertTrue(warning, "Le bon jumeau saisi sans zéros doit être vu.")


@tagged('post_install', '-at_install')
class TestSecretariatSettings(SecretariatCase):
    """Paramètres du secrétariat, vus du profil qui s'en sert.

    Le point sensible : la station par défaut vit sur ``res.company`` et le
    compteur sur ``ir.sequence``, deux modèles qu'une secrétaire ne peut pas
    écrire. Si le ``sudo`` du modèle était mal placé, la saisie d'un bon
    échouerait pour tout le monde sauf l'administrateur — l'erreur classique
    qu'aucun test lancé en admin ne voit.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.fuel = cls.env.ref('ivorycocoa_secretariat.fuel_type_super')
        cls.secretary = cls.env['res.users'].create({
            'name': "Secrétaire", 'login': 'secretariat.test.user',
            'groups_id': [(6, 0, [
                cls.env.ref('ivorycocoa_secretariat.group_secretariat_user').id,
            ])],
        })
        cls.manager = cls.env['res.users'].create({
            'name': "Responsable", 'login': 'secretariat.test.manager',
            'groups_id': [(6, 0, [
                cls.env.ref('ivorycocoa_secretariat.group_secretariat_manager').id,
            ])],
        })

    def _voucher(self, **overrides):
        values = {
            'date': '2026-01-07',
            'fuel_type_id': self.fuel.id,
            'price_unit': 875.0,
            'quantity': 10.0,
        }
        values.update(overrides)
        return self.Voucher.create(values)

    def test_a_secretary_can_create_a_voucher(self):
        """Le numéro proposé lit une séquence — hors de portée de son profil."""
        Voucher = self.Voucher.with_user(self.secretary)
        proposed = Voucher.default_get(['name'])['name']
        self.assertTrue(proposed)
        voucher = Voucher.create({
            'name': proposed,
            'date': '2026-01-07',
            'fuel_type_id': self.fuel.id,
            'price_unit': 875.0,
            'quantity': 10.0,
        })
        self.assertTrue(voucher.id)

    def test_a_secretary_can_prepare_an_invoice(self):
        station = self.env['res.partner'].create({
            'name': 'Station test', 'is_company': True})
        invoice = self.env['secretariat.fuel.invoice'].with_user(
            self.secretary).create({'station_id': station.id})
        self.assertTrue(invoice.name.startswith('FC/'))

    def test_settings_are_readable_and_writable_by_the_manager(self):
        wizard = self.env['secretariat.settings.wizard'].with_user(
            self.manager).create({})
        self.assertTrue(wizard.usage_alert_days)
        self.assertTrue(wizard.voucher_padding)

        station = self.env['res.partner'].create({
            'name': 'Station par défaut', 'is_company': True})
        wizard.write({
            'station_id': station.id,
            'usage_alert_days': 7,
            'usage_critical_days': 21,
            'voucher_next_number': 7000,
        })
        wizard.action_save()

        self.assertEqual(self.env.company.secretariat_fuel_station_id, station)
        self.assertEqual(self.env.company.secretariat_usage_alert_days, 7)
        self.assertEqual(
            self.Voucher._number_sequence().number_next_actual, 7000)

    def test_settings_refuse_an_inverted_pair_of_thresholds(self):
        from odoo.exceptions import UserError
        wizard = self.env['secretariat.settings.wizard'].with_user(
            self.manager).create({
                'usage_alert_days': 30, 'usage_critical_days': 10,
                'voucher_next_number': 1, 'voucher_padding': 8,
            })
        with self.assertRaises(UserError):
            wizard.action_save()

    # ------------------------------------------------------------------
    # Écran Paramètres (res.config.settings)
    # ------------------------------------------------------------------

    def test_settings_screen_reads_the_current_numbering(self):
        self.Voucher._configure_numbering(start_number=6500, padding=8)
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.secretariat_voucher_start_number, 6500)
        self.assertEqual(settings.secretariat_voucher_padding, 8)

    def test_settings_screen_sets_the_start_number(self):
        station = self.env['res.partner'].create({
            'name': 'Station des paramètres', 'is_company': True})
        self.env['res.config.settings'].create({
            'secretariat_voucher_start_number': 7200,
            'secretariat_voucher_padding': 6,
            'secretariat_fuel_station_id': station.id,
            'secretariat_usage_alert_days': 12,
            'secretariat_usage_critical_days': 40,
        }).execute()

        self.assertEqual(
            self.Voucher._number_sequence().number_next_actual, 7200)
        self.assertEqual(self.Voucher.default_get(['name'])['name'], '007200')
        self.assertEqual(self.env.company.secretariat_fuel_station_id, station)
        self.assertEqual(self.env.company.secretariat_usage_alert_days, 12)
        self.assertEqual(self.env.company.secretariat_usage_critical_days, 40)

    def test_settings_screen_refuses_an_absurd_length(self):
        from odoo.exceptions import UserError
        settings = self.env['res.config.settings'].create({
            'secretariat_voucher_start_number': 100,
            'secretariat_voucher_padding': 0,
        })
        with self.assertRaises(UserError):
            settings.execute()

    def test_a_start_number_below_the_registry_is_respected(self):
        """Un nouveau carnet peut recommencer plus bas — et ça tient.

        Le recalage automatique ne regarde que les bons qui viennent d'être
        créés, jamais tout le registre : régler le départ à 100 alors qu'un
        bon 6300 existe ne fait pas ressauter le compteur à 6301.
        """
        self._voucher(name='6300')
        self.env['res.config.settings'].create({
            'secretariat_voucher_start_number': 100,
            'secretariat_voucher_padding': 8,
        }).execute()

        proposed = self.Voucher.default_get(['name'])['name']
        self.assertEqual(proposed, '00000100')
        self._voucher(name=proposed)
        self.assertEqual(self.Voucher.default_get(['name'])['name'], '00000101')

    def test_the_settings_page_is_actually_rendered(self):
        """La page Paramètres → Secrétariat se compose et porte ses champs.

        Une vue héritée peut passer le chargement du module et se briser à
        l'affichage. On assemble donc l'arch réellement servie au navigateur.
        """
        arch = self.env['res.config.settings'].get_view(view_type='form')['arch']
        for field in ('secretariat_voucher_start_number',
                      'secretariat_voucher_padding',
                      'secretariat_voucher_highest_number',
                      'secretariat_fuel_station_id',
                      'secretariat_usage_alert_days'):
            self.assertIn(field, arch, "%s absent de l'écran Paramètres" % field)

    def test_both_configuration_screens_share_the_same_storage(self):
        """L'assistant du responsable et l'écran Paramètres ne divergent pas."""
        self.env['res.config.settings'].create({
            'secretariat_voucher_start_number': 4242,
            'secretariat_voucher_padding': 8,
        }).execute()
        wizard = self.env['secretariat.settings.wizard'].with_user(
            self.manager).create({})
        self.assertEqual(wizard.voucher_next_number, 4242)

        wizard.voucher_next_number = 4300
        wizard.action_save()
        settings = self.env['res.config.settings'].create({})
        self.assertEqual(settings.secretariat_voucher_start_number, 4300)
