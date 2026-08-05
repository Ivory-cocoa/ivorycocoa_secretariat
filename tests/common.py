# -*- coding: utf-8 -*-
"""Socle commun des tests du module.

Plusieurs écrans agrègent **tout** le registre : le tableau de bord compte les
bons à compléter sans filtre de période, l'export « toute la période » ratisse
la base entière, l'état mensuel additionne le mois en cours. Des tests qui
posent « il doit y avoir deux bons » ne tiennent donc que sur une base vierge.

C'est une mauvaise propriété : la suite doit pouvoir être lancée sur une base
qui contient déjà des données — celle de recette, voire celle de production —
sans annoncer des échecs qui n'en sont pas. D'où cet isolement : chaque classe
de test repart d'un registre vide.

Rien n'est détruit pour de bon : Odoo enveloppe chaque classe de test dans une
transaction annulée à la fin.
"""

from odoo.tests import TransactionCase


class SecretariatCase(TransactionCase):
    """Cas de test partant d'un registre de bons vide."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._empty_registry()

    @classmethod
    def _empty_registry(cls):
        """Vide bons, engins et bénéficiaires pour la durée de la classe.

        Les types de carburant sont conservés : ce sont des données du module,
        auxquelles les tests se réfèrent par leur identifiant externe.
        """
        vouchers = cls.env['secretariat.fuel.voucher'].with_context(
            active_test=False).search([])
        if vouchers:
            # Un bon validé refuse d'être supprimé — c'est voulu. On le
            # repasse en brouillon, ce que le verrou autorise.
            vouchers.write({'state': 'draft'})
            vouchers.unlink()

        for model in ('secretariat.vehicle', 'secretariat.fuel.beneficiary'):
            records = cls.env[model].with_context(active_test=False).search([])
            if records:
                records.unlink()
