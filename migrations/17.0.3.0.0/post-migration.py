# -*- coding: utf-8 -*-
"""17.0.3.0.0 — deux dates par bon, numérotation suivie, facturation.

Ce que la migration fait, et ce qu'elle ne fait **pas** :

* elle cale le compteur des bons au-dessus du plus grand numéro déjà
  enregistré, et donne des seuils de délai aux sociétés existantes ;
* elle **ne renumérote pas** l'historique. Les bons repris du classeur gardent
  leur numéro tel qu'il a été saisi (« 6275 »), les nouveaux en portent huit
  (« 00006276 »). Le dédoublonnage compare les numéros à leur valeur, pas à
  leur écriture : les deux formes se rapprochent correctement.
* elle ne touche pas non plus à ``date_used`` : un bon de l'ancien classeur
  n'a qu'une date, et inventer une date d'utilisation fausserait la toute
  première statistique que la direction va regarder. ``date_effective``
  retombe sur la date d'émission, les chiffres historiques sont inchangés.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    from odoo.addons.ivorycocoa_secretariat.hooks import (
        align_companies, align_voucher_sequence)
    align_companies(env)
    align_voucher_sequence(env)

    cr.execute("""
        SELECT COUNT(*) FROM secretariat_fuel_voucher WHERE date_used IS NOT NULL
    """)
    used = cr.fetchone()[0]
    cr.execute("SELECT COUNT(*) FROM secretariat_fuel_voucher")
    total = cr.fetchone()[0]
    _logger.info(
        "Secrétariat 17.0.3.0.0 : %s bon(s) sur %s portent une date "
        "d'utilisation. Les autres seront rattachés à leur date d'émission "
        "tant qu'elle n'est pas renseignée.", used, total)
