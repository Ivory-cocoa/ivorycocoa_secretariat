# -*- coding: utf-8 -*-
"""Paramètres du secrétariat, portés par la société.

Trois réglages y vivent, tous relus à chaud — aucun n'est recopié sur les
bons existants :

* la **station-service par défaut**, pré-remplie à la saisie d'un bon et
  utilisée pour regrouper les bons en factures de quinzaine ;
* les deux **seuils d'alerte sur le délai d'utilisation** d'un bon, c'est-à-dire
  l'écart entre la date d'émission et la date d'utilisation.

Les seuils sont volontairement lus par un accesseur (`_secretariat_usage_thresholds`)
qui retombe sur 15 et 30 jours quand la colonne est vide : une société créée
avant l'installation du module aurait sinon des seuils à zéro, donc une alerte
permanente sur tous les bons.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Valeurs de repli, appliquées quand la société n'a rien paramétré.
DEFAULT_USAGE_ALERT_DAYS = 15
DEFAULT_USAGE_CRITICAL_DAYS = 30


class ResCompany(models.Model):
    _inherit = 'res.company'

    secretariat_fuel_station_id = fields.Many2one(
        'res.partner',
        string="Station-service par défaut",
        domain="[('is_company', '=', True)]",
        help="Station proposée d'office à la saisie d'un bon de carburant. "
             "C'est elle qui regroupe les bons au moment de la facturation "
             "de la quinzaine ; elle reste modifiable bon par bon.",
    )
    secretariat_usage_alert_days = fields.Integer(
        string="Délai d'utilisation — alerte (jours)",
        default=DEFAULT_USAGE_ALERT_DAYS,
        help="Au-delà de ce nombre de jours entre l'émission d'un bon et son "
             "utilisation, le bon est signalé comme tardif. Sert aux "
             "statistiques et aux recommandations du tableau de bord ; "
             "n'interdit jamais rien.",
    )
    secretariat_usage_critical_days = fields.Integer(
        string="Délai d'utilisation — critique (jours)",
        default=DEFAULT_USAGE_CRITICAL_DAYS,
        help="Seuil au-delà duquel un bon émis et non utilisé devient "
             "préoccupant : il circule depuis trop longtemps.",
    )

    @api.constrains('secretariat_usage_alert_days', 'secretariat_usage_critical_days')
    def _check_secretariat_usage_days(self):
        for company in self:
            if company.secretariat_usage_alert_days < 0 \
                    or company.secretariat_usage_critical_days < 0:
                raise ValidationError(_(
                    "Les seuils de délai d'utilisation ne peuvent pas être négatifs."))
            if company.secretariat_usage_critical_days \
                    and company.secretariat_usage_alert_days \
                    and company.secretariat_usage_critical_days \
                    < company.secretariat_usage_alert_days:
                raise ValidationError(_(
                    "Le seuil critique (%(critical)s j) doit être supérieur ou "
                    "égal au seuil d'alerte (%(alert)s j).",
                    critical=company.secretariat_usage_critical_days,
                    alert=company.secretariat_usage_alert_days))

    def _secretariat_usage_thresholds(self):
        """(alerte, critique) en jours, avec repli sur les valeurs par défaut."""
        self.ensure_one()
        alert = self.secretariat_usage_alert_days or DEFAULT_USAGE_ALERT_DAYS
        critical = self.secretariat_usage_critical_days or DEFAULT_USAGE_CRITICAL_DAYS
        return alert, max(alert, critical)
