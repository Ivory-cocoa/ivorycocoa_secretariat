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
* ``secretariat.dashboard.billing`` est la **section facturation** : quinzaine
  en cours, factures à contrôler, à payer, écarts avec les relevés des
  stations.

Les deux sections métier produisent, en plus de leurs chiffres, des
**recommandations** : une phrase qui dit quoi faire, et pourquoi. Elles sont
calculées ici plutôt que dans le navigateur, parce qu'elles dépendent de seuils
paramétrés par la société.

Aucun ``sudo`` : le tableau de bord ne montre que ce que l'utilisateur a le
droit de lire, règles multi-société comprises.
"""

import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models

from .secretariat_fuel_voucher import month_label_fr

_logger = logging.getLogger(__name__)


def group_month_start(group, key):
    """Premier jour du mois d'un groupe « <champ>:month ».

    La borne est lue dans ``__range``, seul endroit qui la donne sans
    ambiguïté : le libellé du groupe est traduit (« August 2026 »), et
    ``__domain`` mélange les bornes du groupe à celles du domaine de départ —
    s'y fier ramènerait tous les groupes au même mois.
    """
    return fields.Date.to_date(group['__range'][key]['from'])

# Nombre de mois affichés dans la courbe de tendance.
TREND_MONTHS = 12
# Mois abrégés, indexés de 1 à 12 — libellés courts des axes de graphique.
MONTH_SHORT_FR = [
    None,
    "Janv.", "Févr.", "Mars", "Avr.", "Mai", "Juin",
    "Juil.", "Août", "Sept.", "Oct.", "Nov.", "Déc.",
]
# Profondeur d'historique servant de référence à la détection d'anomalies.
BASELINE_MONTHS = 6
# Un mois est « anormal » au-delà de ce multiple de la moyenne de l'engin…
ANOMALY_RATIO = 1.5
# … et seulement si l'écart absolu est significatif (bruit des petits engins).
ANOMALY_MIN_GAP = 20.0
# Longueur des palmarès et de la liste des derniers bons.
TOP_LIMIT = 8
RECENT_LIMIT = 10
# Tranches du délai d'utilisation (borne haute incluse, None = au-delà).
DELAY_BUCKETS = (
    ("Le jour même", 0),
    ("1 à 2 jours", 2),
    ("3 à 7 jours", 7),
    ("8 à 15 jours", 15),
    ("16 à 30 jours", 30),
    ("Plus de 30 jours", None),
)
# En deçà de cette part de bons datés, la statistique de délai n'est pas
# représentative — on le dit plutôt que de laisser croire à une moyenne.
DELAY_COVERAGE_MIN = 50.0


class SecretariatDashboard(models.AbstractModel):
    _name = 'secretariat.dashboard'
    _description = "Tableau de bord du secrétariat — données agrégées"

    # Sections assemblées, dans l'ordre d'affichage : (clé, modèle fournisseur)
    _SECTIONS = (
        ('fuel', 'secretariat.dashboard.fuel'),
        ('billing', 'secretariat.dashboard.billing'),
    )

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
        todo = self._todo()
        usage_delay = self._usage_delay(period_domain, previous_start, previous_end)

        return {
            'stats': self._stats(current, previous, start, end),
            'todo': todo,
            'usage_delay': usage_delay,
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
            'recommendations': self._recommendations(todo, usage_delay, current),
        }

    # =========================================================================
    # BRIQUES
    # =========================================================================

    @api.model
    def _domain(self, start, end, include_cancelled=False):
        """Période de CONSOMMATION.

        On borne sur ``date_effective`` (utilisation si elle est connue,
        émission sinon) : un bon émis le 28 mars et servi le 2 avril pèse sur
        avril. Les bons repris de l'ancien classeur n'ont qu'une date — le
        repli les laisse dans leur mois d'origine, les chiffres historiques ne
        bougent donc pas.
        """
        domain = [('date_effective', '>=', start), ('date_effective', '<=', end)]
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
            'late_usage': Voucher.search_count(
                Voucher._usage_alert_domain('critical')
                + [('state', '!=', 'cancelled')]),
            'uninvoiced': Voucher.search_count([
                ('invoice_id', '=', False),
                ('supplier_id', '!=', False),
                ('state', '!=', 'cancelled'),
                ('date_effective', '<=', today),
            ]),
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
            ['quantity:sum', 'amount_total:sum'], ['date_effective:month'], lazy=False)
        for group in data:
            month_start = self._group_month_start(group)
            bucket = buckets.get((month_start.year, month_start.month))
            if bucket:
                bucket['quantity'] = group['quantity'] or 0.0
                bucket['amount'] = group['amount_total'] or 0.0
                bucket['count'] = group['__count']
        return list(buckets.values())

    @staticmethod
    def _group_month_start(group, key='date_effective:month'):
        return group_month_start(group, key)

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
            ['quantity:sum'], ['vehicle_id', 'date_effective:month'], lazy=False)

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
    def _usage_delay(self, period_domain, previous_start, previous_end):
        """Écart entre l'émission d'un bon et son utilisation.

        Deux populations, à ne pas confondre :

        * les bons **utilisés** de la période, qui donnent le délai réellement
          observé (moyenne, médiane, répartition) ;
        * les bons **en circulation**, émis et jamais revenus — eux n'ont pas
          de délai, ils ont un âge, et c'est le vrai sujet de vigilance.

        La médiane est donnée à côté de la moyenne parce qu'un seul bon oublié
        six mois suffit à tirer la moyenne d'une quinzaine vers le haut.
        """
        Voucher = self.env['secretariat.fuel.voucher']
        alert_days, critical_days = \
            self.env.company._secretariat_usage_thresholds()

        rows = Voucher.search_read(
            period_domain + [('date_used', '!=', False)], ['usage_delay_days'])
        delays = sorted(row['usage_delay_days'] or 0 for row in rows)
        total_period = Voucher.search_count(period_domain)

        previous_rows = Voucher.search_read(
            self._domain(previous_start, previous_end) + [('date_used', '!=', False)],
            ['usage_delay_days'])
        previous_delays = [row['usage_delay_days'] or 0 for row in previous_rows]

        pending_domain = [('date_used', '=', False), ('state', '!=', 'cancelled')]
        pending = self._totals(pending_domain)
        late_count = Voucher.search_count(
            Voucher._usage_alert_domain('late') + [('state', '!=', 'cancelled')])
        critical = Voucher.search(
            Voucher._usage_alert_domain('critical') + [('state', '!=', 'cancelled')],
            order='date asc, id asc')

        average = sum(delays) / len(delays) if delays else 0.0
        previous_average = (
            sum(previous_delays) / len(previous_delays) if previous_delays else 0.0)
        today = fields.Date.context_today(self)
        return {
            'alert_days': alert_days,
            'critical_days': critical_days,
            'used_count': len(delays),
            'period_count': total_period,
            'coverage': round(len(delays) / total_period * 100.0, 1) if total_period else 0.0,
            'coverage_min': DELAY_COVERAGE_MIN,
            'average': round(average, 1),
            'median': self._median(delays),
            'max': delays[-1] if delays else 0,
            'same_day': sum(1 for delay in delays if delay <= 0),
            'previous_average': round(previous_average, 1),
            'average_delta': self._delta(average, previous_average),
            'buckets': self._delay_buckets(delays),
            'pending_count': pending['count'],
            'pending_amount': pending['amount'],
            'late_count': late_count,
            'critical_count': len(critical),
            'critical_ids': critical.ids[:200],
            'oldest': [{
                'id': voucher.id,
                'name': voucher.name,
                'date_label': voucher.date.strftime('%d/%m/%Y') if voucher.date else '',
                'age': (today - voucher.date).days if voucher.date else 0,
                'vehicle': voucher.vehicle_id.name or '',
                'beneficiary': voucher.beneficiary_id.name or '',
                'amount': voucher.amount_total,
            } for voucher in critical[:TOP_LIMIT]],
        }

    @staticmethod
    def _median(values):
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return float(values[middle])
        return round((values[middle - 1] + values[middle]) / 2.0, 1)

    @staticmethod
    def _delay_buckets(delays):
        """Répartition des délais observés, en nombre et en part.

        Les tranches forment une partition : chaque délai compte dans une
        seule d'entre elles, et la somme des tranches vaut le nombre de bons
        utilisés. La dernière tranche (« au-delà ») ramasse ce qui dépasse la
        borne précédente — pas l'ensemble des délais.
        """
        rows = []
        lower = -1
        for label, limit in DELAY_BUCKETS:
            if limit is None:
                count = sum(1 for delay in delays if delay > lower)
            else:
                count = sum(1 for delay in delays if lower < delay <= limit)
                lower = limit
            rows.append({
                'label': label,
                'count': count,
                'share': round(count / len(delays) * 100.0, 1) if delays else 0.0,
            })
        return rows

    @api.model
    def _recommendations(self, todo, usage_delay, current):
        """Ce qu'il faut faire, et pourquoi — en français, pas en chiffres.

        Chaque recommandation porte un niveau, une phrase d'action et la clé
        de l'écran à ouvrir. Elles sont volontairement peu nombreuses : une
        liste de vingt conseils ne se lit pas.
        """
        messages = []
        if usage_delay['critical_count']:
            messages.append({
                'level': 'danger',
                'icon': 'fa-hourglass-end',
                'title': _("%(count)s bon(s) en circulation depuis plus de "
                           "%(days)s jours",
                           count=usage_delay['critical_count'],
                           days=usage_delay['critical_days']),
                'message': _(
                    "Ces bons ont été émis et jamais utilisés. Tant qu'ils "
                    "circulent, ils peuvent être servis n'importe quand — y "
                    "compris sur un budget qui n'est plus le bon. Relancez "
                    "les porteurs, ou annulez ceux qui n'ont plus d'objet."),
                'action': 'critical_usage',
            })
        elif usage_delay['late_count']:
            messages.append({
                'level': 'warn',
                'icon': 'fa-hourglass-half',
                'title': _("%(count)s bon(s) non utilisés depuis plus de "
                           "%(days)s jours",
                           count=usage_delay['late_count'],
                           days=usage_delay['alert_days']),
                'message': _(
                    "Rien d'alarmant pour l'instant, mais ces bons dorment. "
                    "Un rappel au bénéficiaire suffit en général à les faire "
                    "revenir."),
                'action': 'late_usage',
            })
        if usage_delay['used_count'] and usage_delay['average'] > usage_delay['alert_days']:
            messages.append({
                'level': 'warn',
                'icon': 'fa-clock-o',
                'title': _("Délai moyen d'utilisation : %(average)s jours",
                           average=usage_delay['average']),
                'message': _(
                    "Au-delà du seuil de %(days)s jours fixé par la société "
                    "(médiane : %(median)s j). Un bon utilisé longtemps après "
                    "son émission fausse le rattachement des consommations au "
                    "mois — et retarde d'autant la facture de la station.",
                    days=usage_delay['alert_days'],
                    median=usage_delay['median']),
                'action': 'late_usage',
            })
        if usage_delay['period_count'] and usage_delay['coverage'] < DELAY_COVERAGE_MIN:
            messages.append({
                'level': 'info',
                'icon': 'fa-calendar-check-o',
                'title': _("Date d'utilisation renseignée sur %(rate)s %% des bons",
                           rate=usage_delay['coverage']),
                'message': _(
                    "En deçà de la moitié, la moyenne affichée ne veut pas "
                    "dire grand-chose. À la réception des souches, "
                    "sélectionnez les bons revenus et utilisez « Marquer "
                    "comme utilisés aujourd'hui »."),
                'action': 'pending_usage',
            })
        if todo['duplicates']:
            messages.append({
                'level': 'danger',
                'icon': 'fa-clone',
                'title': _("%(count)s doublon(s) probable(s)",
                           count=todo['duplicates']),
                'message': _(
                    "Même numéro, même date, même engin, même carburant et "
                    "même quantité. Chaque doublon gonfle la consommation et "
                    "sera payé deux fois s'il part en facturation."),
                'action': 'duplicates',
            })
        if todo['incomplete']:
            messages.append({
                'level': 'info',
                'icon': 'fa-pencil-square-o',
                'title': _("%(count)s bon(s) à compléter", count=todo['incomplete']),
                'message': _(
                    "Sans engin ni bénéficiaire, ces bons ne remontent dans "
                    "aucun palmarès et échappent au suivi des dotations."),
                'action': 'incomplete',
            })
        if not messages and current['count']:
            messages.append({
                'level': 'ok',
                'icon': 'fa-check-circle',
                'title': _("Rien à signaler"),
                'message': _(
                    "Les bons sont complets, utilisés dans les délais et sans "
                    "doublon. Le registre est fiable."),
                'action': False,
            })
        return messages

    @api.model
    def _price_reference(self):
        """Tarifs courants — la secrétaire les a sous les yeux à la saisie."""
        return [{
            'id': fuel.id,
            'name': fuel.name,
            'price': fuel.price,
            'uom': dict(fuel._fields['uom_label'].selection).get(fuel.uom_label, ''),
        } for fuel in self.env['secretariat.fuel.type'].search([])]


class SecretariatDashboardBilling(models.AbstractModel):
    """Section « Facturation » du tableau de bord.

    Répond à trois questions, dans cet ordre : qu'est-ce qui n'est pas encore
    facturé, qu'est-ce qui attend d'être payé, et où sont les écarts avec les
    relevés des stations.
    """

    _name = 'secretariat.dashboard.billing'
    _description = "Tableau de bord — section facturation"

    @api.model
    def get_section_data(self, start, end, previous_start, previous_end):
        Invoice = self.env['secretariat.fuel.invoice']
        today = fields.Date.context_today(self)
        year, month, half = Invoice._period_of(today)
        period_start, period_end = Invoice._period_bounds(year, month, half)

        pending = self._pending_by_station(period_end)
        return {
            'current_period': {
                'label': Invoice._period_label(period_start, period_end),
                'half_label': Invoice.half_label(half),
                'month_label': month_label_fr(period_start),
                'date_from': fields.Date.to_string(period_start),
                'date_to': fields.Date.to_string(period_end),
                'closed': today >= period_end,
                'days_left': max(0, (period_end - today).days),
            },
            'stats': self._stats(start, end),
            'pending_by_station': pending,
            'pending_total': {
                'count': sum(row['count'] for row in pending),
                'amount': sum(row['amount'] for row in pending),
            },
            'monthly': self._monthly(end),
            'recent': self._recent(),
            'recommendations': self._recommendations(pending, period_end),
        }

    # =========================================================================
    # BRIQUES
    # =========================================================================

    @api.model
    def _stats(self, start, end):
        """Chiffres de la période affichée, plus les encours sans période.

        Les encours (à contrôler, à payer, en retard) ignorent volontairement
        la période : une facture de mars impayée reste à payer en août, et
        doit rester visible même quand l'écran est réglé sur le mois en cours.
        """
        Invoice = self.env['secretariat.fuel.invoice']
        today = fields.Date.context_today(self)
        period = self._totals([
            ('date_to', '>=', start), ('date_to', '<=', end),
            ('state', '!=', 'cancelled'),
        ])
        draft = self._totals([('state', '=', 'draft')])
        to_pay = self._totals([('state', '=', 'confirmed')])
        paid = self._totals([
            ('state', '=', 'paid'),
            ('payment_date', '>=', start), ('payment_date', '<=', end),
        ])
        gaps = Invoice.search([
            ('has_difference', '=', True), ('state', '!=', 'cancelled')])
        return {
            'period_count': period['count'],
            'period_amount': period['amount'],
            'draft_count': draft['count'],
            'draft_amount': draft['amount'],
            'to_pay_count': to_pay['count'],
            'to_pay_amount': to_pay['amount'],
            'paid_count': paid['count'],
            'paid_amount': paid['amount'],
            'overdue_count': Invoice.search_count([
                ('state', '=', 'confirmed'),
                ('date_due', '!=', False), ('date_due', '<', today),
            ]),
            'difference_count': len(gaps),
            'difference_amount': sum(gaps.mapped('amount_difference')),
        }

    @api.model
    def _totals(self, domain):
        """(nombre, montant) de factures sur un domaine."""
        result = self.env['secretariat.fuel.invoice'].read_group(
            domain, ['amount_total:sum'], [], lazy=False)
        if not result:
            return {'count': 0, 'amount': 0.0}
        return {
            'count': result[0]['__count'],
            'amount': result[0]['amount_total'] or 0.0,
        }

    @api.model
    def _pending_by_station(self, period_end):
        """Bons non facturés, par station — le travail qui reste à faire.

        Borné à la fin de la quinzaine en cours : un bon servi la quinzaine
        suivante n'est pas « en retard de facturation », il n'est simplement
        pas encore facturable.
        """
        data = self.env['secretariat.fuel.voucher'].read_group(
            [
                ('invoice_id', '=', False),
                ('state', '!=', 'cancelled'),
                ('date_effective', '<=', period_end),
            ],
            ['amount_total:sum', 'quantity:sum'], ['supplier_id'], lazy=False)
        rows = []
        for group in data:
            station = group['supplier_id']
            rows.append({
                'id': station[0] if station else 0,
                'name': station[1] if station else _("Station non renseignée"),
                'unassigned': not station,
                'count': group['__count'],
                'quantity': group['quantity'] or 0.0,
                'amount': group['amount_total'] or 0.0,
            })
        rows.sort(key=lambda row: (not row['unassigned'], -row['amount']))
        return rows

    @api.model
    def _monthly(self, end):
        """Montant facturé par mois de l'année affichée — douze cases pleines."""
        year = end.year
        buckets = {}
        for month in range(1, 13):
            start = fields.Date.to_date('%04d-%02d-01' % (year, month))
            buckets[month] = {
                'month': month,
                'label': month_label_fr(start),
                'short': MONTH_SHORT_FR[month],
                'amount': 0.0,
                'count': 0,
            }
        data = self.env['secretariat.fuel.invoice'].read_group(
            [
                ('date_to', '>=', fields.Date.to_date('%04d-01-01' % year)),
                ('date_to', '<=', fields.Date.to_date('%04d-12-31' % year)),
                ('state', '!=', 'cancelled'),
            ],
            ['amount_total:sum'], ['date_to:month'], lazy=False)
        for group in data:
            month_start = group_month_start(group, 'date_to:month')
            bucket = buckets.get(month_start.month)
            if bucket and month_start.year == year:
                bucket['amount'] = group['amount_total'] or 0.0
                bucket['count'] = group['__count']
        return {'year': year, 'months': list(buckets.values())}

    @api.model
    def _recent(self):
        invoices = self.env['secretariat.fuel.invoice'].search(
            [], order='date_to desc, id desc', limit=RECENT_LIMIT)
        return [{
            'id': invoice.id,
            'name': invoice.name,
            'station': invoice.station_id.display_name or '',
            'period': invoice.period_label,
            'count': invoice.voucher_count,
            'amount': invoice.amount_total,
            'difference': invoice.amount_difference,
            'has_difference': invoice.has_difference,
            'state': invoice.state,
        } for invoice in invoices]

    @api.model
    def _recommendations(self, pending, period_end):
        messages = []
        unassigned = next((row for row in pending if row['unassigned']), None)
        if unassigned:
            messages.append({
                'level': 'warn',
                'icon': 'fa-question-circle',
                'title': _("%(count)s bon(s) sans station",
                           count=unassigned['count']),
                'message': _(
                    "Sans station, un bon n'entre dans aucune facture : il "
                    "sera servi et jamais payé, ou payé sans contrôle. "
                    "Renseignez la station, ou définissez une station par "
                    "défaut dans les paramètres du secrétariat."),
                'action': 'no_station',
            })
        billable = [row for row in pending if not row['unassigned']]
        if billable:
            messages.append({
                'level': 'info',
                'icon': 'fa-file-text-o',
                'title': _("%(count)s bon(s) à facturer sur %(stations)s "
                           "station(s)",
                           count=sum(row['count'] for row in billable),
                           stations=len(billable)),
                'message': _(
                    "La quinzaine se termine le %(date)s. Utilisez « Générer "
                    "les factures de la quinzaine » : une facture par station, "
                    "prête à être confrontée au relevé.",
                    date=period_end.strftime('%d/%m/%Y')),
                'action': 'generate_invoices',
            })
        Invoice = self.env['secretariat.fuel.invoice']
        today = fields.Date.context_today(self)
        overdue = Invoice.search([
            ('state', '=', 'confirmed'),
            ('date_due', '!=', False), ('date_due', '<', today),
        ])
        if overdue:
            messages.append({
                'level': 'danger',
                'icon': 'fa-exclamation-triangle',
                'title': _("%(count)s facture(s) en retard de paiement",
                           count=len(overdue)),
                'message': _(
                    "Leur échéance est dépassée, pour un total de "
                    "%(amount)s. Une station impayée finit par refuser de "
                    "servir les bons.",
                    amount=self._format_amount(sum(overdue.mapped('amount_total')))),
                'action': 'overdue',
            })
        gaps = Invoice.search([
            ('has_difference', '=', True), ('state', '=', 'draft')])
        if gaps:
            messages.append({
                'level': 'warn',
                'icon': 'fa-balance-scale',
                'title': _("%(count)s facture(s) ne tombent pas juste",
                           count=len(gaps)),
                'message': _(
                    "Le montant réclamé par la station diffère du total des "
                    "bons enregistrés. Cherchez d'abord un bon oublié à la "
                    "saisie, puis un bon servi hors quinzaine."),
                'action': 'differences',
            })
        return messages

    @api.model
    def _format_amount(self, amount):
        return "%s %s" % (
            "{:,.0f}".format(amount or 0.0).replace(",", " "),
            self.env.company.currency_id.symbol or '')
