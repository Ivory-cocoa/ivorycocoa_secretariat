# -*- coding: utf-8 -*-
"""Socle commun des référentiels libres du secrétariat.

Engins et bénéficiaires sont saisis en texte libre, et le plus souvent créés
par l'import à partir de ce qui était écrit sur le bon. La reprise du classeur
de référence en a produit 212 et 105 : il y a forcément des doublons. La
normalisation (sans accent, sans casse) rattrape « GASOIL » et « Gasoil »,
elle ne rattrape pas « Camara » et « Camara M. ».

D'où deux outils, ici parce qu'ils valent pour les deux modèles :

* ``has_similar`` — un filtre « doublons probables » qui rapproche les
  libellés voisins. C'est une **suggestion**, jamais un verdict : « Moto A »
  et « Moto B » se ressemblent sans être la même chose.
* ``action_merge`` — la fusion proprement dite, qui vit dans l'assistant
  ``secretariat.merge.wizard``.
"""

from difflib import SequenceMatcher

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Deux libellés sont « proches » au-delà de ce taux de ressemblance. 0,85
# rapproche « camara » et « camara m » sans confondre « moto a » et « moto b ».
SIMILARITY_THRESHOLD = 0.85
# Au-dessous, les libellés sont trop courts pour que la ressemblance veuille
# dire quoi que ce soit (« A » et « B » se ressemblent à 0 %, « AB » et « AC »
# à 50 %, mais « B12 » et « B13 » à 67 % — et ce sont deux produits).
SIMILARITY_MIN_LENGTH = 4


class SecretariatReferentialMixin(models.AbstractModel):
    _name = 'secretariat.referential.mixin'
    _description = "Référentiel libre du secrétariat"

    # Champ du bon de carburant pointant vers ce référentiel — la fusion s'en
    # sert pour repointer les bons.
    _merge_voucher_field = None

    has_similar = fields.Boolean(
        string="Doublon probable",
        compute='_compute_has_similar',
        search='_search_has_similar',
        help="Un autre enregistrement porte un libellé très voisin. À "
             "vérifier : ce n'est qu'une suggestion.",
    )

    # =========================================================================
    # RAPPROCHEMENT DES LIBELLÉS
    # =========================================================================

    def _compute_has_similar(self):
        similar = self._similar_ids()
        for record in self:
            record.has_similar = record.id in similar

    def _search_has_similar(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_("Filtre non pris en charge sur « Doublon probable »."))
        wanted = bool(value) if operator == '=' else not value
        return [('id', 'in' if wanted else 'not in', list(self._similar_ids()))]

    @api.model
    def _similar_ids(self):
        """Identifiants des enregistrements ayant au moins un voisin proche.

        Comparaison deux à deux : ces référentiels se comptent en centaines,
        le coût quadratique reste négligeable. Les libellés sont d'abord
        regroupés par longueur pour écarter d'emblée les paires sans espoir —
        deux textes dont les longueurs diffèrent de plus d'un quart ne peuvent
        pas atteindre le seuil.
        """
        records = self.with_context(active_test=False).search_read(
            [], ['normalized_name'])
        candidates = [
            (record['id'], record['normalized_name'])
            for record in records
            if record['normalized_name']
            and len(record['normalized_name']) >= SIMILARITY_MIN_LENGTH
        ]
        flagged = set()
        for index, (left_id, left) in enumerate(candidates):
            for right_id, right in candidates[index + 1:]:
                if min(len(left), len(right)) < len(max(left, right, key=len)) * 0.75:
                    continue
                if not self._digits_match(left, right):
                    continue
                if SequenceMatcher(None, left, right).ratio() >= SIMILARITY_THRESHOLD:
                    flagged.add(left_id)
                    flagged.add(right_id)
        return flagged

    @staticmethod
    def _digits_match(left, right):
        """Deux libellés dont les chiffres diffèrent ne sont pas le même objet.

        Sans cette règle, la ressemblance textuelle rapproche « AA 720 AC01 »
        et « AA 790 AC01 » : à une décimale près, ce sont pourtant deux
        véhicules. Idem pour « D30 1130 » et « D30 1140 ».

        La comparaison tolère qu'une suite de chiffres en prolonge une autre,
        pour ne pas séparer « AA 892 NJ » de « AA 892 NJ01 » — là, c'est bien
        la même plaque, notée deux fois.
        """
        left_digits = ''.join(c for c in left if c.isdigit())
        right_digits = ''.join(c for c in right if c.isdigit())
        return (left_digits.startswith(right_digits)
                or right_digits.startswith(left_digits))

    def similar_records(self):
        """Les voisins proches d'un enregistrement donné."""
        self.ensure_one()
        if not self.normalized_name:
            return self.browse()
        others = self.with_context(active_test=False).search([('id', '!=', self.id)])
        return others.filtered(lambda other: (
            other.normalized_name
            and self._digits_match(self.normalized_name, other.normalized_name)
            and SequenceMatcher(
                None, self.normalized_name, other.normalized_name
            ).ratio() >= SIMILARITY_THRESHOLD
        ))

    # =========================================================================
    # FUSION
    # =========================================================================

    def action_open_merge_wizard(self):
        """Ouvre l'assistant de fusion sur la sélection courante."""
        if len(self) < 2:
            raise UserError(_(
                "Sélectionnez au moins deux enregistrements à fusionner."))
        return {
            'type': 'ir.actions.act_window',
            'name': _("Fusionner"),
            'res_model': 'secretariat.merge.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'active_model': self._name,
                'active_ids': self.ids,
            },
        }

    def merge_into(self, target):
        """Reporte les bons de ``self`` sur ``target``, puis supprime ``self``.

        Retourne le nombre de bons déplacés. La trace de la fusion est écrite
        dans les notes de l'enregistrement conservé : les anciens libellés
        restent ainsi retrouvables par la recherche, ce qui compte quand on
        cherche un bon d'après ce qui était écrit sur la souche.
        """
        target.ensure_one()
        losers = self - target
        if not losers:
            return 0

        field_name = self._merge_voucher_field
        Voucher = self.env['secretariat.fuel.voucher']
        vouchers = Voucher.search([(field_name, 'in', losers.ids)])
        if vouchers:
            # Le verrou des bons validés est levé pour ce seul champ, et
            # seulement ici (cf. secretariat.fuel.voucher.write).
            vouchers.with_context(secretariat_referential_merge=True).write(
                {field_name: target.id})

        aliases = ", ".join(losers.mapped('name'))
        trace = _(
            "Fusion du %(date)s : reprend %(aliases)s (%(count)s bon(s)).",
            date=fields.Date.context_today(self).strftime('%d/%m/%Y'),
            aliases=aliases, count=len(vouchers))
        target.note = "\n".join(filter(None, [target.note, trace]))
        losers.unlink()
        return len(vouchers)
