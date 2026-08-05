# -*- coding: utf-8 -*-
"""Types de carburant (Super, Gasoil, Lubrifiant, Gaz, B12…).

Le prix courant sert de valeur par défaut sur le bon de carburant : le bon
conserve ensuite SON prix, car les tarifs changent en cours d'année (le Super
est passé de 820 à 875 F/L, le Gasoil de 675 à 700 F/L sur le classeur repris).
"""

import re
import unicodedata

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def normalize_label(value):
    """Clé de rapprochement d'un libellé saisi à la main.

    Minuscules, sans accent, sans ponctuation ni espaces multiples. Permet de
    rapprocher « Gasoil », « gasoil », « GAZ » / « Gaz » / « gaz », ou encore
    « lubrifiant  » (avec espace final) d'un même référentiel.
    """
    if not value:
        return ''
    text = str(value).strip().lower()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


class SecretariatFuelType(models.Model):
    _name = 'secretariat.fuel.type'
    _description = "Type de carburant"
    _inherit = ['mail.thread']
    _order = 'sequence, name'

    name = fields.Char(
        string="Libellé",
        required=True,
        help="Nom du carburant tel qu'il figure sur le bon (Super, Gasoil…)",
    )
    code = fields.Char(
        string="Code",
        help="Code court facultatif, utilisé dans les exports.",
    )
    sequence = fields.Integer(string="Séquence", default=10)
    active = fields.Boolean(string="Actif", default=True)

    uom_label = fields.Selection(
        [('liter', 'Litre'), ('unit', 'Unité'), ('kg', 'Kilogramme')],
        string="Unité",
        default='liter',
        required=True,
        help="Unité de la quantité saisie sur le bon. Les lubrifiants et le "
             "gaz se comptent à l'unité (bidon, bouteille), pas au litre.",
    )
    price = fields.Float(
        string="Prix unitaire courant",
        digits='Product Price',
        tracking=True,
        help="Prix proposé par défaut à la création d'un bon. Le bon conserve "
             "le prix qui lui a été appliqué, même si ce prix change ensuite. "
             "L'historique des changements de tarif est conservé dans la "
             "discussion de la fiche.",
    )
    color = fields.Integer(string="Couleur")

    normalized_name = fields.Char(
        string="Clé de rapprochement",
        compute='_compute_normalized_name',
        store=True,
        index=True,
        help="Libellé normalisé (sans accent ni casse) utilisé par l'import "
             "Excel pour rattacher les variantes d'orthographe.",
    )

    voucher_count = fields.Integer(
        string="Bons", compute='_compute_voucher_count')

    _sql_constraints = [
        ('name_company_uniq', 'unique(name)',
         "Un type de carburant portant ce libellé existe déjà."),
    ]

    @api.depends('name')
    def _compute_normalized_name(self):
        for record in self:
            record.normalized_name = normalize_label(record.name)

    def _compute_voucher_count(self):
        counts = {}
        if self.ids:
            data = self.env['secretariat.fuel.voucher'].read_group(
                [('fuel_type_id', 'in', self.ids)], ['fuel_type_id'], ['fuel_type_id'])
            counts = {
                group['fuel_type_id'][0]: group['fuel_type_id_count']
                for group in data if group['fuel_type_id']
            }
        for record in self:
            record.voucher_count = counts.get(record.id, 0)

    @api.constrains('price')
    def _check_price(self):
        for record in self:
            if record.price < 0:
                raise ValidationError(_("Le prix unitaire ne peut pas être négatif."))

    def action_view_vouchers(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Bons — %s", self.name),
            'res_model': 'secretariat.fuel.voucher',
            'view_mode': 'tree,form,pivot,graph',
            'domain': [('fuel_type_id', '=', self.id)],
        }

    @api.model
    def find_or_create(self, label, create_missing=True):
        """Retrouve un type de carburant à partir d'un libellé libre.

        Utilisé par l'import Excel : rapproche sur le libellé normalisé, puis
        crée le référentiel manquant si demandé.
        """
        key = normalize_label(label)
        if not key:
            return self.browse()
        existing = self.with_context(active_test=False).search(
            [('normalized_name', '=', key)], limit=1)
        if existing or not create_missing:
            return existing
        return self.create({'name': str(label).strip()})
