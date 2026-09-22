# -*- coding: utf-8 -*-
"""Mise en cohérence à l'installation.

Deux réglages ne peuvent pas être livrés en données XML :

* les **seuils de délai d'utilisation** doivent exister sur les sociétés déjà
  créées, qui n'ont pas vu passer la valeur par défaut du champ ;
* le **compteur des bons** doit démarrer au-dessus du plus grand numéro déjà
  enregistré, sinon la première saisie proposerait un numéro déjà utilisé.

La même mise en cohérence est rejouée par la migration 17.0.3.0.0, pour les
bases qui mettent le module à jour au lieu de l'installer.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    align_companies(env)
    align_voucher_sequence(env)


def align_companies(env):
    """Donne des seuils exploitables aux sociétés antérieures au module."""
    companies = env['res.company'].sudo().search([
        '|',
        ('secretariat_usage_alert_days', '=', 0),
        ('secretariat_usage_critical_days', '=', 0),
    ])
    for company in companies:
        alert, critical = company._secretariat_usage_thresholds()
        company.write({
            'secretariat_usage_alert_days': alert,
            'secretariat_usage_critical_days': critical,
        })


def align_voucher_sequence(env):
    """Cale le compteur juste au-dessus du plus grand numéro enregistré."""
    sequence = env['secretariat.fuel.voucher']._number_sequence()
    if not sequence or sequence.implementation != 'standard':
        return
    env.cr.execute("""
        SELECT MAX(CAST(name AS BIGINT))
          FROM secretariat_fuel_voucher
         WHERE name ~ '^[0-9]+$'
    """)
    result = env.cr.fetchone()
    highest = result[0] if result and result[0] is not None else None
    if highest is None:
        return
    if highest + 1 > sequence.number_next_actual:
        sequence.number_next_actual = highest + 1
        _logger.info(
            "Secrétariat : compteur des bons calé sur %s.", highest + 1)
