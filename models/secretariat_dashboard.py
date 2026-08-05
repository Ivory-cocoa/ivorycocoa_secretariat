# -*- coding: utf-8 -*-
"""Données agrégées du tableau de bord du secrétariat.

Deux modèles abstraits, volontairement séparés :

* ``secretariat.dashboard`` est la **coque**. Elle règle la période, la devise,
  le profil de l'utilisateur, puis assemble les sections déclarées dans
  ``_SECTIONS``. Le tableau de bord du secrétariat a vocation à couvrir
  d'autres domaines que le carburant : une nouvelle section s'ajoutera ici,
  sans toucher au reste.
* ``secretariat.dashboard.fuel`` est la **section carburant**. Tout le calcul
  métier y vit.

Aucun ``sudo`` : le tableau de bord ne montre que ce que l'utilisateur a le
droit de lire, règles multi-société comprises.
"""

import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .secretariat_fuel_voucher import month_label_fr

_logger = logging.getLogger(__name__)

# Nombre de mois affichés dans la courbe de tendance.
TREND_MONTHS = 12
# Profondeur d'historique servant de référence à la détection d'anomalies.
BASELINE_MONTHS = 6
# Un mois est « anormal » au-delà de ce multiple de la moyenne de l'engin…
ANOMALY_RATIO = 1.5
# … et seulement si l'écart absolu est significatif (bruit des petits engins).
ANOMALY_MIN_GAP = 20.0
# Longueur des palmarès et de la liste des derniers bons.
TOP_LIMIT = 8
RECENT_LIMIT = 10


class SecretariatDashboard(models.AbstractModel):
    _name = 'secretariat.dashboard'
    _description = "Tableau de bord du secrétariat — données agrégées"

    # Sections assemblées, dans l'ordre d'affichage : (clé, modèle fournisseur)
    _SECTIONS = (('fuel', 'secretariat.dashboard.fuel'),)

    # =========================================================================
    # POINT D'ENTRÉE RPC
    # =========================================================================

    @api.model
    def get_dashboard_data(self, period='month', date_from=None, date_to=None):
        """Retourne toutes les données du tableau de bord en un seul appel."""
        start, end, label = self._period_bounds(period, date_from, date_to)
        previous_start, previous_end = self._previous_period(start, end)
        company = self.env.company

        data = {
            'period': {
                'key': period,
                'label': label,
                'date_from': fields.Date.to_string(start),
                'date_to': fields.Date.to_string(end),
                'previous_label': self._period_label(previous_start, previous_end),
            },
            'currency': {
                'symbol': company.currency_id.symbol or '',
                'position': company.currency_id.position or 'after',
                'decimals': company.currency_id.decimal_places,
            },
            'user': {
                'name': self.env.user.name,
                'is_manager': self.env.user.has_group(
                    'ivorycocoa_secretariat.group_secretariat_manager'),
            },
            'sections': [],
        }
        for key, model_name in self._SECTIONS:
            provider = self.env[model_name]
            try:
                data[key] = provider.get_section_data(
                    start, end, previous_start, previous_end)
                data['sections'].append(key)
            except Exception:  # pragma: no cover - une section HS ne casse pas l'écran
                _logger.exception(
                    "Tableau de bord secrétariat : section « %s » indisponible", key)
        return data

    # =========================================================================
    # PÉRIODES
    # =========================================================================

    @api.model
    def _period_bounds(self, period, date_from=None, date_to=None):
        """(début, fin, libellé) de la période demandée."""
        today = fields.Date.context_today(self)
        if period == 'custom' and date_from and date_to:
            start = fields.Date.to_date(date_from)
            end = fields.Date.to_date(date_to)
            if start > end:
                start, end = end, start
            return start, end, self._period_label(start, end)
        if period == 'quarter':
            first_month = 3 * ((today.month - 1) // 3) + 1
            start = today.replace(month=first_month, day=1)
            end = start + relativedelta(months=3, days=-1)
            return start, end, _("Trimestre en cours")
        if period == 'year':
            start = today.replace(month=1, day=1)
            return start, today.replace(month=12, day=31), _("Année %s", today.year)
        if period == 'last12':
            start = (today.replace(day=1) - relativedelta(months=TREND_MONTHS - 1))
            end = start + relativedelta(months=TREND_MONTHS, days=-1)
            return start, end, _("12 derniers mois")
        # Défaut : le mois civil en cours
        start = today.replace(day=1)
        return start, start + relativedelta(months=1, days=-1), month_label_fr(start)

    @api.model
    def _previous_period(self, start, end):
        """Période immédiatement antérieure, de même durée."""
        span = (end - start).days + 1
        previous_end = start - relativedelta(days=1)
        return previous_end - relativedelta(days=span - 1), previous_end

    @api.model
    def _period_label(self, start, end):
        if not start or not end:
            return ''
        if start.day == 1 and (start + relativedelta(months=1, days=-1)) == end:
            return month_label_fr(start)
        return _("du %(from)s au %(to)s",
                 **{'from': start.strftime('%d/%m/%Y'), 'to': end.strftime('%d/%m/%Y')})


class SecretariatDashboardFuel(models.AbstractModel):
    """Section « Carburant » du tableau de bord."""

    _name = 'secretariat.dashboard.fuel'
    _description = "Tableau de bord — section carburant"

    # =========================================================================
    # ASSEMBLAGE DE LA SECTION
    # =========================================================================

    @api.model
    def get_section_data(self, start, end, previous_start, previous_end):
        period_domain = self._domain(start, end)
        current = self._totals(period_domain)
        previous = self._totals(self._domain(previous_start, previous_end))

        return {
            'stats': self._stats(current, previous, start, end),
            'todo': self._todo(),
            'monthly_trend': self._monthly_trend(end),
            'fuel_split': self._split_by(period_domain, 'fuel_type_id',
                                         'secretariat.fuel.type'),
            'top_vehicles': self._top_vehicles(period_domain),
            'top_beneficiaries': self._split_by(
                period_domain, 'beneficiary_id',
                'secretariat.fuel.beneficiary')[:TOP_LIMIT],
            'quota_watch': self._quota_watch(),
            'anomalies': self._anomalies(),
            'recent': self._recent(),
            'price_reference': self._price_reference(),
        }

    # =========================================================================
    # BRIQUES
    # =========================================================================

    @api.model
    def _domain(self, start, end, include_cancelled=False):
        domain = [('date', '>=', start), ('date', '<=', end)]
        if not include_cancelled:
            domain.append(('state', '!=', 'cancelled'))
        return domain

    @api.model
    def _totals(self, domain):
        """(nombre de bons, quantité, montant) sur un domaine."""
        result = self.env['secretariat.fuel.voucher'].read_group(
            domain, ['quantity:sum', 'amount_total:sum'], [], lazy=False)
        if not result:
            return {'count': 0, 'quantity': 0.0, 'amount': 0.0}
        group = result[0]
        return {
            'count': group['__count'],
            'quantity': group['quantity'] or 0.0,
            'amount': group['amount_total'] or 0.0,
        }

    @api.model
    def _stats(self, current, previous, start, end):
        today = fields.Date.context_today(self)
        year_start = today.replace(month=1, day=1)
        year = self._totals(self._domain(year_start, today))
        return {
            'voucher_count': current['count'],
            'quantity': current['quantity'],
            'amount': current['amount'],
            'previous_voucher_count': previous['count'],
            'previous_quantity': previous['quantity'],
            'previous_amount': previous['amount'],
            'amount_delta': self._delta(current['amount'], previous['amount']),
            'quantity_delta': self._delta(current['quantity'], previous['quantity']),
            'count_delta': self._delta(current['count'], previous['count']),
            'average_amount': (
                current['amount'] / current['count'] if current['count'] else 0.0),
            'ytd_amount': year['amount'],
            'ytd_quantity': year['quantity'],
            'ytd_count': year['count'],
        }

    @staticmethod
    def _delta(current, previous):
        """Variation en % — None quand la comparaison n'a pas de sens."""
        if not previous:
            return None
        return round((current - previous) / previous * 100.0, 1)

    @api.model
    def _todo(self):
        """Les compteurs sur lesquels la secrétaire doit agir.

        Volontairement sans filtre de période : un bon incomplet de mars reste
        à compléter en août.
        """
        Voucher = self.env['secretariat.fuel.voucher']
        today = fields.Date.context_today(self)
        duplicates = self._duplicate_ids()
        return {
            'draft': Voucher.search_count([('state', '=', 'draft')]),
            'incomplete': Voucher.search_count([
                ('is_incomplete', '=', True), ('state', '!=', 'cancelled')]),
            'future': Voucher.search_count([
                ('date', '>', today), ('state', '!=', 'cancelled')]),
            'duplicates': len(duplicates),
            'duplicate_ids': duplicates[:200],
            'over_quota': self.env['secretariat.vehicle'].search_count([
                ('is_over_quota', '=', True)]),
            'unlinked_beneficiaries': self.env['secretariat.fuel.beneficiary'].search_count([
                ('category', '=', 'employee'), ('employee_id', '=', False)]),
        }

    @api.model
    def _duplicate_ids(self):
        """Bons ressaisis à l'identique — même clé de dédoublonnage.

        Le numéro de bon n'étant pas unique, on ne peut pas s'appuyer sur une
        contrainte. On regroupe donc par numéro + jour (peu coûteux, indexé),
        et on ne compare finement que les grappes de plus d'un bon.
        """
        Voucher = self.env['secretariat.fuel.voucher']
        groups = Voucher.read_group(
            [('state', '!=', 'cancelled')], [], ['name', 'date:day'], lazy=False)
        suspects = [group for group in groups if group['__count'] > 1]
        if not suspects:
            return []
        candidates = Voucher.browse()
        for group in suspects:
            candidates |= Voucher.search(group['__domain'])
        seen, duplicates = set(), []
        for voucher in candidates.sorted(lambda v: v.id):
            key = voucher.dedup_key()
            if key in seen:
                duplicates.append(voucher.id)
            else:
                seen.add(key)
        return duplicates

    @api.model
    def _monthly_trend(self, end):
        """Quantité et montant des douze derniers mois, mois manquants inclus."""
        last_month_start = end.replace(day=1)
        first_month_start = last_month_start - relativedelta(months=TREND_MONTHS - 1)
        buckets = {}
        cursor = first_month_start
        while cursor <= last_month_start:
            buckets[(cursor.year, cursor.month)] = {
                'label': month_label_fr(cursor),
                'short': "%s %s" % (month_label_fr(cursor)[:3], str(cursor.year)[2:]),
                'month': cursor.strftime('%Y-%m'),
                'quantity': 0.0,
                'amount': 0.0,
                'count': 0,
            }
            cursor += relativedelta(months=1)

        period_end = last_month_start + relativedelta(months=1, days=-1)
        data = self.env['secretariat.fuel.voucher'].read_group(
            self._domain(first_month_start, period_end),
            ['quantity:sum', 'amount_total:sum'], ['date:month'], lazy=False)
        for group in data:
            month_start = self._group_month_start(group)
            bucket = buckets.get((month_start.year, month_start.month))
            if bucket:
                bucket['quantity'] = group['quantity'] or 0.0
                bucket['amount'] = group['amount_total'] or 0.0
                bucket['count'] = group['__count']
        return list(buckets.values())

    @staticmethod
    def _group_month_start(group):
        """Premier jour du mois d'un groupe « date:month ».

        La borne est lue dans ``__range``, seul endroit qui la donne sans
        ambiguïté : ``date:month`` porte un libellé traduit (« August 2026 »),
        et ``__domain`` mélange les bornes du groupe à celles du domaine de
        départ — s'y fier ramènerait tous les groupes au même mois.
        """
        return fields.Date.to_date(group['__range']['date:month']['from'])

    @api.model
    def _split_by(self, domain, field_name, comodel):
        """Répartition (quantité, montant) par référentiel, la plus grosse d'abord."""
        data = self.env['secretariat.fuel.voucher'].read_group(
            domain, ['quantity:sum', 'amount_total:sum'], [field_name], lazy=False)
        rows = []
        for group in data:
            reference = group[field_name]
            rows.append({
                'id': reference[0] if reference else 0,
                'name': reference[1] if reference else _("(non précisé)"),
                'count': group['__count'],
                'quantity': group['quantity'] or 0.0,
                'amount': group['amount_total'] or 0.0,
            })
        rows.sort(key=lambda row: row['amount'], reverse=True)
        return rows

    @api.model
    def _top_vehicles(self, domain):
        """Palmarès des engins, enrichi de leur dotation mensuelle."""
        rows = self._split_by(domain, 'vehicle_id', 'secretariat.vehicle')[:TOP_LIMIT]
        vehicles = self.env['secretariat.vehicle'].browse(
            [row['id'] for row in rows if row['id']])
        quotas = {v.id: (v.monthly_quota, v.monthly_budget) for v in vehicles}
        for row in rows:
            quota, budget = quotas.get(row['id'], (0.0, 0.0))
            row['monthly_quota'] = quota
            row['monthly_budget'] = budget
        return rows

    @api.model
    def _quota_watch(self):
        """Engins dotés d'un plafond, du plus tendu au plus confortable."""
        vehicles = self.env['secretariat.vehicle'].search([
            '|', ('monthly_quota', '>', 0), ('monthly_budget', '>', 0),
        ])
        rows = [{
            'id': vehicle.id,
            'name': vehicle.name,
            'monthly_quota': vehicle.monthly_quota,
            'monthly_budget': vehicle.monthly_budget,
            'quantity': vehicle.month_quantity,
            'amount': vehicle.month_amount,
            'usage_rate': round(vehicle.quota_usage_rate, 1),
            'over': vehicle.is_over_quota,
        } for vehicle in vehicles]
        rows.sort(key=lambda row: row['usage_rate'], reverse=True)
        return rows[:TOP_LIMIT]

    @api.model
    def _anomalies(self):
        """Engins dont la consommation du mois s'écarte nettement de l'habitude.

        Référence : la moyenne des mois complets précédents pour lesquels
        l'engin a effectivement consommé. Un engin sans historique n'est jamais
        signalé — on ne crie pas au loup sur une première utilisation.
        """
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        baseline_start = month_start - relativedelta(months=BASELINE_MONTHS)

        data = self.env['secretariat.fuel.voucher'].read_group(
            self._domain(baseline_start, today) + [('vehicle_id', '!=', False)],
            ['quantity:sum'], ['vehicle_id', 'date:month'], lazy=False)

        history = defaultdict(dict)
        for group in data:
            vehicle = group['vehicle_id']
            if not vehicle:
                continue
            month = self._group_month_start(group)
            history[vehicle][(month.year, month.month)] = group['quantity'] or 0.0

        anomalies = []
        current_key = (month_start.year, month_start.month)
        for vehicle, months in history.items():
            current = months.get(current_key, 0.0)
            past = [qty for key, qty in months.items() if key != current_key and qty]
            if not current or len(past) < 2:
                continue
            average = sum(past) / len(past)
            if current >= average * ANOMALY_RATIO and current - average >= ANOMALY_MIN_GAP:
                anomalies.append({
                    'id': vehicle[0],
                    'name': vehicle[1],
                    'quantity': current,
                    'average': round(average, 2),
                    'deviation': round((current - average) / average * 100.0, 1),
                    'months': len(past),
                })
        anomalies.sort(key=lambda row: row['deviation'], reverse=True)
        return anomalies[:TOP_LIMIT]

    @api.model
    def _recent(self):
        """Les derniers bons enregistrés, pour reprendre la saisie d'un clic."""
        vouchers = self.env['secretariat.fuel.voucher'].search(
            [], order='create_date desc, id desc', limit=RECENT_LIMIT)
        return [{
            'id': voucher.id,
            'name': voucher.name,
            'date': fields.Date.to_string(voucher.date),
            'date_label': voucher.date.strftime('%d/%m/%Y') if voucher.date else '',
            'vehicle': voucher.vehicle_id.name or '',
            'beneficiary': voucher.beneficiary_id.name or '',
            'fuel': voucher.fuel_type_id.name or '',
            'quantity': voucher.quantity,
            'amount': voucher.amount_total,
            'state': voucher.state,
            'incomplete': voucher.is_incomplete,
        } for voucher in vouchers]

    @api.model
    def _price_reference(self):
        """Tarifs courants — la secrétaire les a sous les yeux à la saisie."""
        return [{
            'id': fuel.id,
            'name': fuel.name,
            'price': fuel.price,
            'uom': dict(fuel._fields['uom_label'].selection).get(fuel.uom_label, ''),
        } for fuel in self.env['secretariat.fuel.type'].search([])]
