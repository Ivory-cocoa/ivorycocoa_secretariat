# -*- coding: utf-8 -*-
"""Tests de l'import et de l'export du classeur des bons de carburant.

Les cas testés reproduisent les défauts réellement observés dans le classeur
repris : lignes de total sans numéro, orthographes flottantes des carburants,
date saisie en texte, total incohérent, et feuilles recopiées d'un mois sur
l'autre (source de 165 doublons sur 1 377 lignes).
"""

import base64
import io

from odoo.tests import tagged

from .common import SecretariatCase

try:
    import openpyxl
except ImportError:
    openpyxl = None


def _build_workbook(sheets):
    """sheets = {nom: [lignes]} — chaque ligne est un tuple de 8 valeurs."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title)
        for col, header in enumerate(
                ["Numéro", "Date ", "Engin", "Nom", "Carburant",
                 "Prix Unitaire", "Quantité", "Total"], start=1):
            sheet.cell(1, col, header)
        for row_index, row in enumerate(rows, start=2):
            for col, value in enumerate(row, start=1):
                sheet.cell(row_index, col, value)
    stream = io.BytesIO()
    workbook.save(stream)
    return base64.b64encode(stream.getvalue())


@tagged('post_install', '-at_install')
class TestFuelImportExport(SecretariatCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Voucher = cls.env['secretariat.fuel.voucher']
        cls.Import = cls.env['secretariat.fuel.import.wizard']
        cls.Export = cls.env['secretariat.fuel.export.wizard']

    def setUp(self):
        super().setUp()
        if openpyxl is None:
            self.skipTest("openpyxl absent")

    # =========================================================================
    # IMPORT
    # =========================================================================

    def _run_import(self, sheets, **options):
        wizard = self.Import.create(dict({
            'file_data': _build_workbook(sheets),
            'file_name': 'carnet.xlsx',
        }, **options))
        wizard.action_analyze()
        wizard.action_import()
        return wizard

    def test_import_creates_vouchers_and_referentials(self):
        wizard = self._run_import({'Janvier 2026': [
            (7138, '15/12/2025', 'Moto Jerome', 'Jerome', 'Super', 820, 12, 9840),
            (1566, '19/12/2025', '5150 KS01', 'Mireille', 'Super', 820, 35, 28700),
        ]})
        self.assertEqual(wizard.imported_count, 2)
        self.assertEqual(wizard.error_count, 0)
        self.assertEqual(wizard.created_vehicle_count, 2)
        self.assertEqual(wizard.created_beneficiary_count, 2)

        voucher = self.Voucher.search([('name', '=', '7138')])
        self.assertEqual(len(voucher), 1)
        self.assertEqual(voucher.vehicle_id.name, 'Moto Jerome')
        self.assertAlmostEqual(voucher.amount_total, 9840.0)
        self.assertEqual(voucher.state, 'confirmed')
        self.assertTrue(voucher.imported)

    def test_import_normalizes_fuel_spelling(self):
        """« super », « Gasoil » et « gasoil » ne créent pas de doublons."""
        wizard = self._run_import({'Mai 2026': [
            (1, '02/05/2026', 'A', 'X', 'super', 875, 10, 8750),
            (2, '02/05/2026', 'B', 'Y', 'Gasoil', 700, 10, 7000),
            (3, '03/05/2026', 'C', 'Z', 'gasoil', 700, 10, 7000),
        ]})
        self.assertEqual(wizard.imported_count, 3)
        self.assertEqual(wizard.created_fuel_count, 0)  # Super et Gasoil livrés
        fuel_names = self.Voucher.search(
            [('name', 'in', ['1', '2', '3'])]).mapped('fuel_type_id.name')
        self.assertEqual(set(fuel_names), {'Super', 'Gasoil'})

    def test_import_skips_total_rows(self):
        """Les lignes sans numéro (totaux de bas de feuille) sont ignorées."""
        wizard = self._run_import({'Juin 2026': [
            (10, '01/06/2026', 'A', 'X', 'Super', 875, 10, 8750),
            (None, None, None, None, None, None, None, 8750),
            (None, None, None, None, None, None, None, ' '),
        ]})
        self.assertEqual(wizard.imported_count, 1)
        self.assertEqual(wizard.error_count, 0)

    def test_import_is_idempotent(self):
        """Réimporter le même fichier ne duplique rien."""
        sheets = {'Juillet 2026': [
            (20, '01/07/2026', 'A', 'X', 'Super', 875, 10, 8750),
            (21, '02/07/2026', 'B', 'Y', 'Super', 875, 20, 17500),
        ]}
        first = self._run_import(sheets)
        self.assertEqual(first.imported_count, 2)
        second = self._run_import(sheets)
        self.assertEqual(second.imported_count, 0)
        self.assertEqual(second.duplicate_count, 2)

    def test_import_deduplicates_recopied_sheets(self):
        """Une feuille recopiée dans la suivante n'est comptée qu'une fois."""
        rows = [(30, '01/07/2026', 'A', 'X', 'Super', 875, 10, 8750)]
        wizard = self._run_import({
            'Juillet 2026': rows,
            'juillet 2026 2': rows + [
                (31, '20/07/2026', 'B', 'Y', 'Super', 875, 5, 4375)],
        })
        self.assertEqual(wizard.imported_count, 2)
        self.assertEqual(wizard.duplicate_count, 1)

    def test_a_duplicate_line_creates_no_referential(self):
        """Une ligne écartée ne doit pas laisser d'engin fantôme derrière elle.

        L'import résout les référentiels APRÈS le dédoublonnage : réimporter
        le même fichier ne doit créer ni bon ni référentiel.
        """
        sheets = {'Mars 2026': [
            (100, '02/03/2026', 'Engin fantome', 'Fantome', 'Super', 875, 10, 8750),
        ]}
        first = self._run_import(sheets)
        self.assertEqual(first.created_vehicle_count, 1)

        vehicles_before = self.env['secretariat.vehicle'].search_count([])
        second = self._run_import(sheets)
        self.assertEqual(second.imported_count, 0)
        self.assertEqual(second.duplicate_count, 1)
        self.assertEqual(second.created_vehicle_count, 0)
        self.assertEqual(
            self.env['secretariat.vehicle'].search_count([]), vehicles_before)

    def test_two_unknown_vehicles_are_not_confused(self):
        """Deux engins inconnus différents restent deux lignes distinctes.

        Même numéro, même date, même carburant, même quantité : seul l'engin
        les sépare. Le dédoublonnage se faisant avant toute création, il faut
        que deux libellés inconnus reçoivent des clés distinctes.
        """
        wizard = self._run_import({'Avril 2026': [
            (110, '02/04/2026', 'Inconnu A', 'X', 'Super', 875, 10, 8750),
            (110, '02/04/2026', 'Inconnu B', 'Y', 'Super', 875, 10, 8750),
        ]})
        self.assertEqual(wizard.imported_count, 2)
        self.assertEqual(wizard.duplicate_count, 0)

    def test_the_analysis_announces_what_the_import_produces(self):
        sheets = {'Mai 2026 bis': [
            (120, '02/05/2026', 'A', 'X', 'Super', 875, 10, 8750),
            (120, '02/05/2026', 'A', 'X', 'Super', 875, 10, 8750),
            (121, '03/05/2026', 'B', 'Y', 'Super', 875, 10, 8750),
        ]}
        wizard = self.Import.create({
            'file_data': _build_workbook(sheets), 'file_name': 'carnet.xlsx'})
        wizard.action_analyze()
        announced = sum(wizard.sheet_ids.mapped('new_count'))
        wizard.action_import()
        self.assertEqual(wizard.imported_count, announced)
        self.assertEqual(announced, 2)

    def test_the_report_escapes_what_comes_from_the_file(self):
        """Le compte rendu est en HTML non filtré : rien de brut ne doit passer."""
        wizard = self._run_import({'Mars <b>2026': [
            (130, None, 'A', 'X', 'Super', 875, 10, 8750),
        ]})
        self.assertEqual(wizard.error_count, 1)
        self.assertNotIn('<b>2026', wizard.result_html)
        self.assertIn('&lt;b&gt;2026', wizard.result_html)

    def test_going_back_clears_the_analysis(self):
        wizard = self.Import.create({
            'file_data': _build_workbook({'Juin 2026 bis': [
                (140, '02/06/2026', 'A', 'X', 'Super', 875, 10, 8750)]}),
            'file_name': 'carnet.xlsx'})
        wizard.action_analyze()
        self.assertEqual(wizard.state, 'analyzed')
        wizard.action_back()
        self.assertEqual(wizard.state, 'upload')
        self.assertFalse(wizard.sheet_ids)

    def test_import_reports_rejected_rows(self):
        wizard = self._run_import({'Août 2026': [
            (40, None, 'A', 'X', 'Super', 875, 10, 8750),
            (41, '01/08/2026', 'A', 'X', '', 875, 10, 8750),
            (42, '01/08/2026', 'A', 'X', 'Super', 875, 0, 0),
            (43, '01/08/2026', 'A', 'X', 'Super', 875, 10, 8750),
        ]})
        self.assertEqual(wizard.imported_count, 1)
        self.assertEqual(wizard.error_count, 3)
        self.assertTrue(wizard.error_file)

    def test_import_repairs_malformed_dates(self):
        """Séparateur oublié à la saisie : la date est reconstituée et signalée."""
        wizard = self._run_import({'Février 2026': [
            (45, '25/022026', 'A', 'X', 'Super', 875, 10, 8750),
            (46, '13/03/026', 'B', 'Y', 'Super', 875, 10, 8750),
            (47, '2004/2026', 'C', 'Z', 'Super', 875, 10, 8750),
            (48, '1506/2026', 'D', 'W', 'Super', 875, 10, 8750),
        ]})
        self.assertEqual(wizard.imported_count, 4)
        self.assertEqual(wizard.error_count, 0)
        vouchers = self.Voucher.search([('name', 'in', ['45', '46', '47', '48'])])
        self.assertEqual(
            sorted(str(v.date) for v in vouchers),
            ['2026-02-25', '2026-03-13', '2026-04-20', '2026-06-15'])
        self.assertTrue(all("Date reconstituée" in (v.note or '') for v in vouchers))

    def test_import_falls_back_on_fuel_price(self):
        """Prix illisible (« #VALUE! ») : le prix courant prend le relais."""
        wizard = self._run_import({'Août 2026': [
            (50, '01/08/2026', 'Moto Abou', 'Abou', 'Super', None, 1, '#VALUE!'),
        ]})
        self.assertEqual(wizard.imported_count, 1)
        voucher = self.Voucher.search([('name', '=', '50')])
        self.assertAlmostEqual(
            voucher.price_unit,
            self.env.ref('ivorycocoa_secretariat.fuel_type_super').price)
        self.assertIn("Prix unitaire absent", voucher.note)

    def test_import_flags_inconsistent_total(self):
        wizard = self._run_import({'Août 2026': [
            (60, '02/08/2026', 'A', 'X', 'Super', 875, 10, 99999),
        ]})
        self.assertEqual(wizard.imported_count, 1)
        voucher = self.Voucher.search([('name', '=', '60')])
        self.assertAlmostEqual(voucher.amount_total, 8750.0)
        self.assertIn("Total du fichier", voucher.note)

    def test_import_can_skip_unselected_sheets(self):
        wizard = self.Import.create({
            'file_data': _build_workbook({
                'Janvier 2026': [(70, '05/01/2026', 'A', 'X', 'Super', 875, 10, 8750)],
                'Février 2026': [(71, '05/02/2026', 'B', 'Y', 'Super', 875, 10, 8750)],
            }),
            'file_name': 'carnet.xlsx',
        })
        wizard.action_analyze()
        self.assertEqual(len(wizard.sheet_ids), 2)
        wizard.sheet_ids.filtered(lambda s: s.name == 'Février 2026').selected = False
        wizard.action_import()
        self.assertEqual(wizard.imported_count, 1)
        self.assertFalse(self.Voucher.search([('name', '=', '71')]))

    # =========================================================================
    # MODÈLE VIERGE
    # =========================================================================

    def test_template_is_offered_for_download(self):
        wizard = self.Import.create({})
        action = wizard.action_download_template()
        self.assertEqual(action['type'], 'ir.actions.act_url')
        self.assertIn('field=template_file', action['url'])
        self.assertTrue(wizard.template_file)
        self.assertTrue(wizard.template_file_name.endswith('.xlsx'))

    def test_template_carries_the_expected_columns(self):
        wizard = self.Import.create({})
        wizard.action_download_template()
        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(wizard.template_file)))
        self.assertIn("Mode d'emploi", workbook.sheetnames)
        sheet = workbook['Bons de carburant']
        self.assertEqual(
            [sheet.cell(1, col).value for col in range(1, 9)],
            ["Numéro", "Date", "Engin", "Nom", "Carburant",
             "Prix Unitaire", "Quantité", "Total"])
        # Liste déroulante des carburants : c'est ce qui évite les fautes de
        # frappe à la source.
        self.assertTrue(sheet.data_validations.dataValidation)

    def test_the_blank_template_imports_without_creating_anything(self):
        """Le modèle doit pouvoir être renvoyé tel quel, sans effet de bord.

        Sa feuille « Mode d'emploi » ne doit surtout pas être prise pour des
        données : ses libellés sont volontairement répartis sur des lignes
        différentes, la détection d'en-tête ne peut donc pas s'y accrocher.
        """
        template = self.Import.create({})
        template.action_download_template()

        wizard = self.Import.create({
            'file_data': template.template_file,
            'file_name': 'modele_bons_carburant.xlsx',
        })
        wizard.action_analyze()
        self.assertEqual(sum(wizard.sheet_ids.mapped('row_count')), 0)
        self.assertEqual(sum(wizard.sheet_ids.mapped('error_count')), 0)
        wizard.action_import()
        self.assertEqual(wizard.imported_count, 0)
        self.assertEqual(wizard.created_vehicle_count, 0)

    def test_a_filled_template_imports(self):
        """Le classeur rendu par la secrétaire doit passer sans retouche."""
        template = self.Import.create({})
        template.action_download_template()
        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(template.template_file)))
        sheet = workbook['Bons de carburant']
        for col, value in enumerate(
                (4242, '12/06/2026', 'Moto Yao', 'Yao', 'Super', 875, 8), start=1):
            sheet.cell(2, col, value)
        stream = io.BytesIO()
        workbook.save(stream)

        wizard = self.Import.create({
            'file_data': base64.b64encode(stream.getvalue()),
            'file_name': 'modele_rempli.xlsx',
        })
        wizard.action_analyze()
        wizard.action_import()
        self.assertEqual(wizard.imported_count, 1)
        self.assertEqual(wizard.error_count, 0)
        voucher = self.Voucher.search([('name', '=', '4242')])
        self.assertAlmostEqual(voucher.amount_total, 7000.0)

    # =========================================================================
    # EXPORT
    # =========================================================================

    def test_export_reproduces_the_workbook_layout(self):
        self._run_import({'Janvier 2026': [
            (80, '15/12/2025', 'Moto Jerome', 'Jerome', 'Super', 820, 12, 9840),
            (81, '05/01/2026', '5150 KS01', 'Mireille', 'Super', 820, 35, 28700),
        ]})
        wizard = self.Export.create({'all_dates': True, 'include_summary': True})
        wizard.action_generate()
        self.assertTrue(wizard.file_data)

        workbook = openpyxl.load_workbook(
            io.BytesIO(base64.b64decode(wizard.file_data)), data_only=True)
        # Une feuille par mois CIVIL : décembre 2025 n'est pas rangé dans
        # « Janvier 2026 » comme le faisait le classeur d'origine.
        self.assertIn("Décembre 2025", workbook.sheetnames)
        self.assertIn("Janvier 2026", workbook.sheetnames)
        self.assertIn("Synthèse", workbook.sheetnames)

        sheet = workbook["Janvier 2026"]
        self.assertEqual(
            [sheet.cell(1, c).value for c in range(1, 9)],
            ["Numéro", "Date", "Engin", "Nom", "Carburant",
             "Prix Unitaire", "Quantité", "Total"])
        self.assertEqual(sheet.cell(2, 1).value, 81)
        self.assertEqual(sheet.cell(2, 3).value, '5150 KS01')
        self.assertAlmostEqual(sheet.cell(2, 8).value, 28700.0)

    def test_export_round_trips_through_import(self):
        """Le classeur généré est relisible par l'import, sans doublon."""
        self._run_import({'Janvier 2026': [
            (90, '05/01/2026', 'A', 'X', 'Super', 875, 10, 8750),
            (91, '06/01/2026', 'B', 'Y', 'Gasoil', 700, 20, 14000),
        ]})
        export = self.Export.create({'all_dates': True, 'include_summary': False})
        export.action_generate()

        reimport = self.Import.create({
            'file_data': export.file_data,
            'file_name': export.file_name,
        })
        reimport.action_analyze()
        reimport.action_import()
        self.assertEqual(reimport.error_count, 0)
        self.assertEqual(reimport.imported_count, 0)
        self.assertEqual(reimport.duplicate_count, 2)

    def test_export_without_data_raises(self):
        from odoo.exceptions import UserError
        wizard = self.Export.create({
            'all_dates': False,
            'date_from': '2000-01-01',
            'date_to': '2000-01-31',
        })
        with self.assertRaises(UserError):
            wizard.action_generate()
