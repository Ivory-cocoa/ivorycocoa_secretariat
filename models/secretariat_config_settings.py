# -*- coding: utf-8 -*-
"""Paramètres du secrétariat dans l'écran **Paramètres** d'Odoo.

C'est l'endroit où un administrateur s'attend à les trouver. Le responsable du
secrétariat, lui, passe par l'assistant ``secretariat.settings.wizard`` —
``res.config.settings`` exige les droits d'administration, qu'on ne va pas
donner à la personne qui tient le carnet.

Les deux écrans écrivent **le même** stockage : les champs de ``res.company``
pour la station et les seuils, la séquence pour la numérotation, et le même
point d'entrée ``secretariat.fuel.voucher._configure_numbering()`` pour les
contrôles. Impossible qu'ils divergent.
"""

from odoo import _, api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # --- Portés par la société (related : écrits par le mécanisme standard) --
    secretariat_fuel_station_id = fields.Many2one(
        related='company_id.secretariat_fuel_station_id', readonly=False,
        string="Station-service par défaut")
    secretariat_usage_alert_days = fields.Integer(
        related='company_id.secretariat_usage_alert_days', readonly=False,
        string="Délai d'utilisation — alerte (jours)")
    secretariat_usage_critical_days = fields.Integer(
        related='company_id.secretariat_usage_critical_days', readonly=False,
        string="Délai d'utilisation — critique (jours)")

    # --- Portés par la séquence (lus et écrits par get_values / set_values) --
    secretariat_voucher_start_number = fields.Integer(
        string="Numéro de départ des bons",
        help="Numéro proposé au prochain bon créé. Réglez-le sur le premier "
             "numéro du carnet en cours : il avancera ensuite tout seul, et "
             "se recalera automatiquement si vous corrigez un numéro à la "
             "saisie.")
    secretariat_voucher_padding = fields.Integer(
        string="Longueur du numéro",
        help="Nombre de caractères du numéro, complété par des zéros à "
             "gauche. Huit par défaut.")
    secretariat_voucher_highest_number = fields.Char(
        string="Plus grand numéro enregistré",
        compute='_compute_secretariat_voucher_highest_number',
        help="Numéro le plus élevé présent dans le registre. Un numéro de "
             "départ inférieur reproposera des numéros déjà servis — ce n'est "
             "pas interdit (un carnet peut recommencer plus bas), mais mieux "
             "vaut le savoir.")

    # =========================================================================
    # LECTURE ET ÉCRITURE
    # =========================================================================

    def _compute_secretariat_voucher_highest_number(self):
        highest = self.env['secretariat.fuel.voucher']._highest_recorded_number()
        label = str(highest) if highest is not None else _("aucun")
        for settings in self:
            settings.secretariat_voucher_highest_number = label

    @api.model
    def get_values(self):
        values = super().get_values()
        sequence = self.env['secretariat.fuel.voucher']._number_sequence()
        values.update({
            'secretariat_voucher_start_number': (
                sequence.number_next_actual if sequence else 1),
            'secretariat_voucher_padding': (
                sequence.padding if sequence else 0) or 8,
        })
        return values

    def set_values(self):
        super().set_values()
        self.env['secretariat.fuel.voucher']._configure_numbering(
            start_number=self.secretariat_voucher_start_number,
            padding=self.secretariat_voucher_padding,
        )
