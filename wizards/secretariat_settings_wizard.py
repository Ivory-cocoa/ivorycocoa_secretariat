# -*- coding: utf-8 -*-
"""Paramètres du secrétariat — accessibles au responsable, pas à l'admin seul.

Ces réglages vivent sur la société (``res.company``) et sur la séquence des
bons. Les deux sont hors de portée d'un profil « Responsable du secrétariat » :
l'écran de configuration d'Odoo exige les droits d'administration, et écrire
sur une séquence aussi. Passer par ce petit assistant évite d'avoir à donner
les droits d'administration à la personne qui, précisément, ne fait que tenir
le carnet.

Les écritures sont faites en ``sudo`` **après** contrôle explicite du groupe :
c'est le groupe métier qui autorise, pas le contournement.
"""

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class SecretariatSettingsWizard(models.TransientModel):
    _name = 'secretariat.settings.wizard'
    _description = "Paramètres du secrétariat"

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company)

    station_id = fields.Many2one(
        'res.partner',
        string="Station-service par défaut",
        domain="[('is_company', '=', True)]",
        help="Proposée d'office à la saisie d'un bon, et utilisée pour "
             "rattacher les bons sans station au moment de la facturation.")

    usage_alert_days = fields.Integer(
        string="Seuil d'alerte (jours)",
        help="Au-delà de ce délai entre l'émission et l'utilisation d'un bon, "
             "le bon est signalé comme tardif.")
    usage_critical_days = fields.Integer(
        string="Seuil critique (jours)",
        help="Au-delà, un bon émis et non utilisé remonte en tête du tableau "
             "de bord : il circule depuis trop longtemps.")

    voucher_next_number = fields.Integer(
        string="Prochain numéro de bon",
        help="Numéro proposé à la prochaine saisie. Réglez-le sur le premier "
             "numéro du carnet en cours ; il avance ensuite tout seul.")
    voucher_padding = fields.Integer(
        string="Longueur du numéro",
        help="Nombre de caractères du numéro, complété par des zéros à "
             "gauche. Huit par défaut.")
    highest_number = fields.Char(
        string="Plus grand numéro enregistré", readonly=True,
        help="Numéro le plus élevé présent dans le registre — le compteur ne "
             "doit pas repasser en dessous.")

    # =========================================================================
    # CHARGEMENT
    # =========================================================================

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        company = self.env.company
        alert, critical = company._secretariat_usage_thresholds()
        sequence = self.env['secretariat.fuel.voucher']._number_sequence()
        values.update({
            'company_id': company.id,
            'station_id': company.secretariat_fuel_station_id.id,
            'usage_alert_days': alert,
            'usage_critical_days': critical,
            'voucher_next_number': sequence.number_next_actual if sequence else 1,
            'voucher_padding': (sequence.padding if sequence else 0) or 8,
            'highest_number': self._highest_number(),
        })
        return values

    @api.model
    def _highest_number(self):
        """Plus grand numéro purement numérique du registre.

        Lu en SQL : la comparaison doit porter sur la valeur du numéro, pas
        sur son écriture — « 9 » est plus petit que « 10 », mais « 9 » est
        plus grand que « 10 » en tri alphabétique.
        """
        self.env.cr.execute("""
            SELECT MAX(CAST(name AS BIGINT))
              FROM secretariat_fuel_voucher
             WHERE name ~ '^[0-9]+$'
        """)
        result = self.env.cr.fetchone()
        return str(result[0]) if result and result[0] is not None else _("aucun")

    # =========================================================================
    # ENREGISTREMENT
    # =========================================================================

    def action_save(self):
        self.ensure_one()
        if not self.env.user.has_group(
                'ivorycocoa_secretariat.group_secretariat_manager'):
            raise AccessError(_(
                "Seul un responsable du secrétariat peut modifier ces "
                "paramètres."))
        if self.usage_critical_days < self.usage_alert_days:
            raise UserError(_(
                "Le seuil critique (%(critical)s j) doit être supérieur ou "
                "égal au seuil d'alerte (%(alert)s j).",
                critical=self.usage_critical_days, alert=self.usage_alert_days))
        if self.voucher_padding < 1 or self.voucher_padding > 20:
            raise UserError(_(
                "La longueur du numéro doit être comprise entre 1 et 20."))
        if self.voucher_next_number < 1:
            raise UserError(_("Le prochain numéro doit être positif."))

        self.company_id.sudo().write({
            'secretariat_fuel_station_id': self.station_id.id,
            'secretariat_usage_alert_days': self.usage_alert_days,
            'secretariat_usage_critical_days': self.usage_critical_days,
        })
        sequence = self.env['secretariat.fuel.voucher']._number_sequence()
        if sequence:
            sequence.write({'padding': self.voucher_padding})
            if sequence.number_next_actual != self.voucher_next_number:
                sequence.number_next_actual = self.voucher_next_number
        return {'type': 'ir.actions.act_window_close'}
