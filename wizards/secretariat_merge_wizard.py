# -*- coding: utf-8 -*-
"""Fusion de deux référentiels qui désignent la même chose.

L'import crée un engin ou un bénéficiaire par libellé rencontré. La reprise du
classeur de référence en a produit 212 et 105, et il y a forcément des
doublons : « Camara » et « Camara M. » sont une seule personne, « 2900 LP01 »
et « 2900LP 01 » un seul véhicule.

Fusionner, c'est reporter les bons de l'un sur l'autre et supprimer le
premier. L'opération est irréversible, d'où l'écran de confirmation qui dit
exactement combien de bons vont bouger — et d'où la trace laissée dans les
notes de l'enregistrement conservé : les anciens libellés y restent
retrouvables par la recherche, ce qui compte quand on cherche un bon d'après
ce qui était écrit sur la souche.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Modèle fusionnable -> (clé interne, champ de la sélection, champ de la cible)
_KINDS = {
    'secretariat.vehicle': ('vehicle', 'vehicle_ids', 'target_vehicle_id'),
    'secretariat.fuel.beneficiary': (
        'beneficiary', 'beneficiary_ids', 'target_beneficiary_id'),
}


class SecretariatMergeWizard(models.TransientModel):
    _name = 'secretariat.merge.wizard'
    _description = "Fusion de référentiels du secrétariat"

    kind = fields.Selection(
        [('vehicle', "Engins"), ('beneficiary', "Bénéficiaires")],
        string="Référentiel", required=True, readonly=True)

    # Deux jeux de champs plutôt qu'une référence générique : les listes
    # déroulantes restent typées, donc utilisables telles quelles dans le
    # formulaire, et le domaine de la cible se limite tout seul à la sélection.
    vehicle_ids = fields.Many2many(
        'secretariat.vehicle', 'secretariat_merge_vehicle_rel',
        'wizard_id', 'vehicle_id', string="Engins à fusionner")
    target_vehicle_id = fields.Many2one(
        'secretariat.vehicle', string="Engin conservé",
        domain="[('id', 'in', vehicle_ids)]")
    beneficiary_ids = fields.Many2many(
        'secretariat.fuel.beneficiary', 'secretariat_merge_beneficiary_rel',
        'wizard_id', 'beneficiary_id', string="Bénéficiaires à fusionner")
    target_beneficiary_id = fields.Many2one(
        'secretariat.fuel.beneficiary', string="Bénéficiaire conservé",
        domain="[('id', 'in', beneficiary_ids)]")

    record_count = fields.Integer(compute='_compute_summary')
    voucher_count = fields.Integer(
        string="Bons concernés", compute='_compute_summary')
    absorbed_names = fields.Char(
        string="Sera supprimé", compute='_compute_summary')

    # =========================================================================
    # OUVERTURE
    # =========================================================================

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        model_name = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids') or []
        if model_name not in _KINDS:
            raise UserError(_(
                "La fusion ne s'applique qu'aux engins et aux bénéficiaires."))
        if len(active_ids) < 2:
            raise UserError(_(
                "Sélectionnez au moins deux enregistrements à fusionner."))

        kind, selection_field, target_field = _KINDS[model_name]
        records = self.env[model_name].browse(active_ids).exists()
        values['kind'] = kind
        values[selection_field] = [(6, 0, records.ids)]
        # Par défaut, on conserve celui qui porte déjà le plus de bons : c'est
        # celui dont l'historique est le plus lourd à déplacer, et le plus
        # probablement le « bon » libellé.
        values[target_field] = max(
            records, key=lambda record: record.voucher_count).id
        return values

    # =========================================================================
    # RÉSUMÉ
    # =========================================================================

    def _selection(self):
        self.ensure_one()
        return self.vehicle_ids if self.kind == 'vehicle' else self.beneficiary_ids

    def _target(self):
        self.ensure_one()
        return (self.target_vehicle_id if self.kind == 'vehicle'
                else self.target_beneficiary_id)

    @api.depends('vehicle_ids', 'target_vehicle_id',
                 'beneficiary_ids', 'target_beneficiary_id')
    def _compute_summary(self):
        for wizard in self:
            records = wizard._selection()
            target = wizard._target()
            absorbed = records - target
            wizard.record_count = len(records)
            wizard.voucher_count = sum(absorbed.mapped('voucher_count'))
            wizard.absorbed_names = ", ".join(absorbed.mapped('name'))

    # =========================================================================
    # ACTION
    # =========================================================================

    def action_merge(self):
        self.ensure_one()
        records = self._selection()
        target = self._target()
        if len(records) < 2:
            raise UserError(_("Il faut au moins deux enregistrements à fusionner."))
        if not target:
            raise UserError(_("Choisissez l'enregistrement à conserver."))
        if target not in records:
            raise UserError(_(
                "L'enregistrement conservé doit faire partie de la sélection."))

        absorbed_names = ", ".join((records - target).mapped('name'))
        moved = records.merge_into(target)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Fusion effectuée"),
                'message': _(
                    "%(names)s → %(target)s. %(count)s bon(s) reporté(s).",
                    names=absorbed_names, target=target.name, count=moved),
                'type': 'success',
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
