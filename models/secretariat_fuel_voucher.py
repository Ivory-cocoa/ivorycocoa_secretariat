# -*- coding: utf-8 -*-
"""Bon de carburant.

Reprend une ligne du classeur tenu par le secrétariat :

    Numéro | Date | Engin | Nom | Carburant | Prix Unitaire | Quantité | Total

Deux partis pris issus de l'analyse du classeur existant :

* **Engin et bénéficiaire ne sont pas obligatoires en base.** Le classeur
  repris comporte des lignes sans engin ou sans nom ; les rendre obligatoires
  ferait échouer l'import de l'historique. Ces bons sont marqués « incomplet »
  et un filtre dédié permet à la secrétaire de les compléter.
* **Le numéro n'est pas unique.** Le même numéro de souche revient sur
  plusieurs lignes (un bon Super + un bon Lubrifiant le même jour, carnets
  différents…). L'unicité serait fausse ; le dédoublonnage de l'import se fait
  sur la combinaison numéro + date + engin + carburant + quantité.

Le second parti pris a un revers : rien n'empêche la re-saisie accidentelle du
même bon. Plutôt qu'une contrainte fausse, la saisie déclenche un
**avertissement non bloquant** quand un bon du même numéro existe déjà à la
même date.

**Numérotation.** Le numéro est désormais *proposé* sur huit caractères par un
compteur (``ir.sequence``), à partir d'un numéro de départ paramétrable. Il
reste modifiable : la souche papier fait foi, et un compteur qui imposerait son
propre numéro finirait par diverger du carnet. Le compteur se recale sur le
plus grand numéro réellement utilisé (cf. ``_sync_number_sequence``), si bien
qu'une correction manuelle ne casse pas la suite. Les numéros purement
numériques sont comparés à leur valeur (« 6275 » et « 00006275 » sont le même
bon) : l'historique saisi sans zéros de remplissage continue donc d'être
dédoublonné correctement.

**Deux dates.** ``date`` est la date d'**émission** du bon, ``date_used`` sa
date d'**utilisation** à la station — elles diffèrent souvent, et l'écart est
précisément ce que la direction veut voir. ``date_effective`` (utilisation,
sinon émission) est la date de référence : c'est elle qui rattache le bon à un
mois de consommation et à une quinzaine de facturation. Un bon importé de
l'ancien classeur n'a qu'une date ; le repli le laisse dans son mois d'origine.
"""

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools import float_compare, float_is_zero

# Mois en français, indexés de 1 à 12 — utilisés pour nommer les feuilles de
# l'export au même format que le classeur d'origine (« Janvier 2026 »).
MONTH_NAMES_FR = [
    None,
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]

# Longueur du numéro de bon quand la séquence n'a pas été trouvée.
DEFAULT_NUMBER_PADDING = 8

# Champs verrouillés par la validation du bon.
LOCKED_FIELDS = ('name', 'date', 'vehicle_id', 'beneficiary_id',
                 'fuel_type_id', 'price_unit', 'quantity')

# Champs verrouillés par la facturation : une facture de quinzaine validée
# porte un montant arrêté avec la station. Tout ce qui le compose est figé,
# y compris la station elle-même et la date d'utilisation — qui déterminent
# la facture de rattachement.
INVOICE_LOCKED_FIELDS = LOCKED_FIELDS + ('supplier_id', 'date_used')

# États de facture qui figent les bons rattachés.
INVOICE_LOCKING_STATES = ('confirmed', 'paid')

# États du suivi d'utilisation, dans l'ordre de gravité.
USAGE_ALERT_KEYS = ('used', 'pending', 'late', 'critical')


class SecretariatFuelVoucher(models.Model):
    _name = 'secretariat.fuel.voucher'
    _description = "Bon de carburant"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    name = fields.Char(
        string="Numéro du bon",
        required=True,
        tracking=True,
        index=True,
        copy=False,
        default=lambda self: self._default_voucher_number(),
        help="Numéro imprimé sur la souche du bon, sur huit caractères. Il est "
             "proposé à partir du compteur (Configuration → Paramètres du "
             "secrétariat) et reste modifiable : c'est la souche papier qui "
             "fait foi.",
    )
    date = fields.Date(
        string="Date d'émission",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
        index=True,
        help="Jour où le bon a été établi et remis au bénéficiaire.",
    )
    date_used = fields.Date(
        string="Date d'utilisation",
        tracking=True,
        index=True,
        copy=False,
        help="Jour où le bon a effectivement été servi à la station. Laissez "
             "vide tant que le bon n'est pas revenu : il apparaît alors dans "
             "le suivi des bons en circulation.",
    )
    date_effective = fields.Date(
        string="Date de référence",
        compute='_compute_usage_dates',
        store=True,
        index=True,
        help="Date d'utilisation si elle est connue, date d'émission sinon. "
             "C'est elle qui rattache le bon à un mois de consommation et à "
             "une quinzaine de facturation.",
    )
    usage_delay_days = fields.Integer(
        string="Délai d'utilisation (j)",
        compute='_compute_usage_dates',
        store=True,
        help="Nombre de jours écoulés entre l'émission du bon et son "
             "utilisation. Zéro tant que le bon n'est pas utilisé.",
    )
    is_used = fields.Boolean(
        string="Utilisé",
        compute='_compute_usage_dates',
        store=True,
    )
    usage_alert = fields.Selection(
        [
            ('used', "Utilisé"),
            ('pending', "En circulation"),
            ('late', "Utilisation tardive"),
            ('critical', "En circulation depuis trop longtemps"),
        ],
        string="Suivi d'utilisation",
        compute='_compute_usage_alert',
        search='_search_usage_alert',
        help="Dépend du jour présent : un bon non utilisé vieillit tout seul. "
             "Volontairement non stocké — un champ stocké afficherait une "
             "alerte périmée jusqu'au prochain recalcul.",
    )
    vehicle_id = fields.Many2one(
        'secretariat.vehicle',
        string="Engin",
        tracking=True,
        index=True,
        ondelete='restrict',
    )
    beneficiary_id = fields.Many2one(
        'secretariat.fuel.beneficiary',
        string="Bénéficiaire",
        tracking=True,
        index=True,
        ondelete='restrict',
    )
    fuel_type_id = fields.Many2one(
        'secretariat.fuel.type',
        string="Carburant",
        required=True,
        tracking=True,
        index=True,
        ondelete='restrict',
    )
    uom_label = fields.Selection(
        related='fuel_type_id.uom_label', string="Unité", readonly=True)

    price_unit = fields.Float(
        string="Prix unitaire",
        digits='Product Price',
        tracking=True,
        help="Prix appliqué à ce bon. Pré-rempli avec le prix courant du "
             "carburant, il reste modifiable et n'est plus recalculé ensuite.",
    )
    quantity = fields.Float(
        string="Quantité",
        digits='Product Unit of Measure',
        tracking=True,
    )
    amount_total = fields.Monetary(
        string="Total",
        compute='_compute_amount_total',
        store=True,
        currency_field='currency_id',
        tracking=True,
    )

    company_id = fields.Many2one(
        'res.company', string="Société",
        required=True, default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', string="Devise",
        related='company_id.currency_id', readonly=True)
    supplier_id = fields.Many2one(
        'res.partner',
        string="Station",
        domain="[('is_company', '=', True)]",
        default=lambda self: self.env.company.secretariat_fuel_station_id,
        tracking=True,
        index=True,
        help="Station-service auprès de laquelle le bon est honoré. "
             "Pré-remplie avec la station par défaut de la société ; c'est "
             "elle qui regroupe les bons au moment de la facturation.",
    )

    invoice_id = fields.Many2one(
        'secretariat.fuel.invoice',
        string="Facture de station",
        copy=False,
        readonly=True,
        index=True,
        ondelete='set null',
        help="Facture de quinzaine dans laquelle ce bon a été repris.",
    )
    invoice_state = fields.Selection(
        related='invoice_id.state', string="État de la facture", readonly=True)
    is_invoiced = fields.Boolean(
        string="Facturé",
        compute='_compute_is_invoiced',
        store=True,
        help="Vrai dès que le bon est repris dans une facture non annulée.",
    )

    state = fields.Selection(
        [
            ('draft', 'Brouillon'),
            ('confirmed', 'Validé'),
            ('cancelled', 'Annulé'),
        ],
        string="État",
        default='draft',
        required=True,
        tracking=True,
        index=True,
        copy=False,
    )

    is_incomplete = fields.Boolean(
        string="Incomplet",
        compute='_compute_is_incomplete',
        store=True,
        help="Le bon manque d'un engin, d'un bénéficiaire, d'un prix ou d'une "
             "quantité — typiquement une ligne reprise de l'ancien classeur.",
    )
    imported = fields.Boolean(
        string="Importé",
        default=False,
        copy=False,
        readonly=True,
        help="Bon créé par l'import du classeur Excel.",
    )
    import_source = fields.Char(
        string="Origine de l'import",
        copy=False,
        readonly=True,
        help="Fichier et feuille d'origine, pour retrouver la ligne source.",
    )
    note = fields.Text(string="Observations")

    # =========================================================================
    # INDEX
    # =========================================================================

    def init(self):
        """Index de dédoublonnage.

        L'import compare chaque ligne du classeur à la base sur
        numéro + date ; l'avertissement de re-saisie fait la même recherche à
        chaque frappe. Un index composite évite le parcours séquentiel dès que
        le registre dépasse quelques milliers de bons.
        """
        super().init()
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS secretariat_fuel_voucher_dedup_idx
            ON secretariat_fuel_voucher (name, date)
        """)

    # =========================================================================
    # CALCULS
    # =========================================================================

    @api.depends('price_unit', 'quantity')
    def _compute_amount_total(self):
        for voucher in self:
            voucher.amount_total = (voucher.price_unit or 0.0) * (voucher.quantity or 0.0)

    @api.depends('vehicle_id', 'beneficiary_id', 'price_unit', 'quantity')
    def _compute_is_incomplete(self):
        for voucher in self:
            voucher.is_incomplete = not (
                voucher.vehicle_id
                and voucher.beneficiary_id
                and voucher.price_unit > 0
                and voucher.quantity > 0
            )

    @api.depends('date', 'date_used')
    def _compute_usage_dates(self):
        for voucher in self:
            voucher.date_effective = voucher.date_used or voucher.date
            voucher.is_used = bool(voucher.date_used)
            if voucher.date and voucher.date_used:
                voucher.usage_delay_days = (voucher.date_used - voucher.date).days
            else:
                voucher.usage_delay_days = 0

    @api.depends('invoice_id', 'invoice_id.state')
    def _compute_is_invoiced(self):
        for voucher in self:
            voucher.is_invoiced = bool(voucher.invoice_id) \
                and voucher.invoice_id.state != 'cancelled'

    def _compute_usage_alert(self):
        """Âge d'un bon non utilisé, rapporté aux seuils de la société."""
        today = fields.Date.context_today(self)
        alert_days, critical_days = self.env.company._secretariat_usage_thresholds()
        for voucher in self:
            if voucher.date_used:
                voucher.usage_alert = 'used'
                continue
            elapsed = (today - voucher.date).days if voucher.date else 0
            if elapsed > critical_days:
                voucher.usage_alert = 'critical'
            elif elapsed > alert_days:
                voucher.usage_alert = 'late'
            else:
                voucher.usage_alert = 'pending'

    def _search_usage_alert(self, operator, value):
        """Traduit le suivi d'utilisation en domaine de dates.

        Le champ n'est pas stocké — il dépend du jour présent — mais il doit
        rester filtrable depuis la vue de recherche et depuis le tableau de
        bord. On retraduit donc chaque état en bornes de date, ce qui laisse
        PostgreSQL faire le travail sur l'index de ``date``.
        """
        if operator in ('=', '!='):
            wanted = [value]
        elif operator in ('in', 'not in'):
            wanted = list(value or [])
        else:
            raise ValidationError(_(
                "Filtre non pris en charge sur « Suivi d'utilisation »."))

        keys = {key for key in wanted if key in USAGE_ALERT_KEYS}
        if operator in ('!=', 'not in'):
            keys = set(USAGE_ALERT_KEYS) - keys
        if not keys:
            return expression.FALSE_DOMAIN
        if keys == set(USAGE_ALERT_KEYS):
            return expression.TRUE_DOMAIN
        return expression.OR([
            self._usage_alert_domain(key) for key in sorted(keys)])

    @api.model
    def _usage_alert_domain(self, key):
        """Domaine correspondant à un état du suivi d'utilisation."""
        today = fields.Date.context_today(self)
        alert_days, critical_days = self.env.company._secretariat_usage_thresholds()
        alert_limit = today - relativedelta(days=alert_days)
        critical_limit = today - relativedelta(days=critical_days)
        if key == 'used':
            return [('date_used', '!=', False)]
        pending = [('date_used', '=', False)]
        if key == 'pending':
            return pending + [('date', '>', alert_limit)]
        if key == 'late':
            return pending + [('date', '<=', alert_limit), ('date', '>', critical_limit)]
        return pending + [('date', '<=', critical_limit)]

    # =========================================================================
    # ONCHANGE
    # =========================================================================

    @api.onchange('fuel_type_id')
    def _onchange_fuel_type_id(self):
        """Aligne le prix sur le carburant choisi.

        Le prix n'est remplacé que s'il est vide ou s'il correspond encore au
        tarif courant d'un autre carburant : une correction de carburant en
        cours de saisie (« Super » rectifié en « Gasoil ») ne doit pas laisser
        l'ancien prix, mais un prix saisi à la main doit être respecté.
        """
        if not self.fuel_type_id:
            return
        rounding = 0.01
        proposed_prices = self.env['secretariat.fuel.type'].with_context(
            active_test=False).search([]).mapped('price')
        keeps_manual_price = self.price_unit and not any(
            float_is_zero(self.price_unit - price, precision_rounding=rounding)
            for price in proposed_prices)
        if not keeps_manual_price:
            self.price_unit = self.fuel_type_id.price

    @api.onchange('vehicle_id')
    def _onchange_vehicle_id(self):
        """Propose le bénéficiaire habituel de l'engin."""
        if self.vehicle_id and self.vehicle_id.employee_id and not self.beneficiary_id:
            beneficiary = self.env['secretariat.fuel.beneficiary'].search(
                [('employee_id', '=', self.vehicle_id.employee_id.id)], limit=1)
            if beneficiary:
                self.beneficiary_id = beneficiary

    @api.onchange('name', 'date')
    def _onchange_check_duplicate(self):
        """Signale une re-saisie probable — sans jamais l'interdire.

        Le numéro de bon n'est pas unique (cf. docstring du modèle), une
        contrainte serait fausse. L'avertissement, lui, attrape le cas
        courant : le même bon saisi deux fois.
        """
        if not self.name or not self.date:
            return
        domain = [
            ('name', 'in', self._number_variants(self.name)),
            ('date', '=', self.date),
            ('state', '!=', 'cancelled'),
        ]
        if self._origin.id:
            domain.append(('id', '!=', self._origin.id))
        twin = self.search(domain, limit=1)
        if twin:
            return {'warning': {
                'title': _("Bon déjà enregistré ?"),
                'message': _(
                    "Un bon n° %(name)s du %(date)s existe déjà "
                    "(%(vehicle)s — %(fuel)s, %(quantity)s).\n\n"
                    "Ce n'est pas forcément une erreur : la même souche peut "
                    "couvrir deux carburants. Vérifiez simplement qu'il ne "
                    "s'agit pas d'une double saisie.",
                    name=twin.name,
                    date=format_date_fr(self.env, twin.date),
                    vehicle=twin.vehicle_id.name or _("engin non précisé"),
                    fuel=twin.fuel_type_id.name or _("carburant non précisé"),
                    quantity=twin.quantity,
                ),
            }}

    @api.onchange('date_used')
    def _onchange_date_used(self):
        """Contrôle le sens et l'ampleur de l'écart émission → utilisation."""
        if not self.date_used:
            return
        today = fields.Date.context_today(self)
        if self.date and self.date_used < self.date:
            return {'warning': {
                'title': _("Utilisation avant émission"),
                'message': _(
                    "La date d'utilisation (%(used)s) précède la date "
                    "d'émission (%(issued)s). L'une des deux est fausse : "
                    "l'enregistrement sera refusé en l'état.",
                    used=format_date_fr(self.env, self.date_used),
                    issued=format_date_fr(self.env, self.date)),
            }}
        if self.date_used > today:
            return {'warning': {
                'title': _("Utilisation à venir"),
                'message': _(
                    "La date d'utilisation (%s) est postérieure à "
                    "aujourd'hui. Un bon ne peut pas avoir déjà été servi à "
                    "une date future.", format_date_fr(self.env, self.date_used)),
            }}
        alert_days, _critical = self.env.company._secretariat_usage_thresholds()
        delay = (self.date_used - self.date).days if self.date else 0
        if delay > alert_days:
            return {'warning': {
                'title': _("Bon utilisé tardivement"),
                'message': _(
                    "Ce bon a mis %(delay)s jours à être utilisé, pour un "
                    "seuil d'alerte fixé à %(limit)s jours.\n\n"
                    "Ce n'est pas une erreur de saisie : le chiffre remonte au "
                    "tableau de bord pour que la direction voie combien de "
                    "temps les bons restent en circulation.",
                    delay=delay, limit=alert_days),
            }}

    @api.onchange('date')
    def _onchange_date_in_future(self):
        if self.date and self.date > fields.Date.context_today(self):
            return {'warning': {
                'title': _("Date à venir"),
                'message': _(
                    "La date du bon (%s) est postérieure à aujourd'hui. "
                    "Vérifiez la saisie : une inversion de chiffres dans "
                    "l'année est vite arrivée.", format_date_fr(self.env, self.date)),
            }}

    @api.onchange('vehicle_id', 'quantity', 'price_unit', 'date', 'date_used')
    def _onchange_check_quota(self):
        """Avertit quand le bon fait franchir la dotation mensuelle de l'engin."""
        vehicle = self.vehicle_id
        reference = self.date_used or self.date
        if not vehicle or not reference or not self.quantity:
            return
        if vehicle.monthly_quota <= 0 and vehicle.monthly_budget <= 0:
            return
        # La dotation se juge sur le mois de CONSOMMATION : un bon émis fin
        # mars et servi début avril pèse sur avril.
        start = reference.replace(day=1)
        end = start + relativedelta(months=1, days=-1)
        domain = [
            ('vehicle_id', '=', vehicle.id),
            ('date_effective', '>=', start), ('date_effective', '<=', end),
            ('state', '!=', 'cancelled'),
        ]
        if self._origin.id:
            domain.append(('id', '!=', self._origin.id))
        siblings = self.search(domain)
        quantity = sum(siblings.mapped('quantity')) + (self.quantity or 0.0)
        amount = sum(siblings.mapped('amount_total')) + \
            (self.quantity or 0.0) * (self.price_unit or 0.0)

        exceeded = []
        if vehicle.monthly_quota > 0 and quantity > vehicle.monthly_quota:
            exceeded.append(_(
                "quantité : %(used)s sur %(quota)s alloués",
                used=round(quantity, 2), quota=round(vehicle.monthly_quota, 2)))
        if vehicle.monthly_budget > 0 and amount > vehicle.monthly_budget:
            exceeded.append(_(
                "montant : %(used)s sur %(budget)s alloués",
                used=round(amount), budget=round(vehicle.monthly_budget)))
        if exceeded:
            return {'warning': {
                'title': _("Plafond mensuel dépassé"),
                'message': _(
                    "Avec ce bon, %(vehicle)s dépasse sa dotation de "
                    "%(month)s (%(details)s).\n\n"
                    "Le bon reste enregistrable — le secrétariat constate ce "
                    "qui a été servi, il ne l'autorise pas.",
                    vehicle=vehicle.name,
                    month=month_label_fr(start),
                    details=" ; ".join(exceeded)),
            }}

    # =========================================================================
    # CONTRAINTES
    # =========================================================================

    @api.constrains('quantity', 'price_unit')
    def _check_amounts(self):
        for voucher in self:
            if voucher.quantity < 0:
                raise ValidationError(_("La quantité ne peut pas être négative."))
            if voucher.price_unit < 0:
                raise ValidationError(_("Le prix unitaire ne peut pas être négatif."))

    @api.constrains('date', 'date_used')
    def _check_usage_date(self):
        """L'utilisation ne peut ni précéder l'émission, ni être à venir.

        Contrairement au dépassement de dotation, ce n'est pas un fait à
        constater : c'est une impossibilité matérielle. Un tel couple de dates
        fausserait durablement le délai moyen d'utilisation, donc la
        statistique que la direction regarde.
        """
        today = fields.Date.context_today(self)
        for voucher in self:
            if not voucher.date_used:
                continue
            if voucher.date and voucher.date_used < voucher.date:
                raise ValidationError(_(
                    "Bon %(name)s : la date d'utilisation (%(used)s) précède "
                    "la date d'émission (%(issued)s).",
                    name=voucher.name,
                    used=format_date_fr(self.env, voucher.date_used),
                    issued=format_date_fr(self.env, voucher.date)))
            if voucher.date_used > today:
                raise ValidationError(_(
                    "Bon %(name)s : un bon ne peut pas avoir été utilisé le "
                    "%(used)s, c'est-à-dire dans le futur.",
                    name=voucher.name,
                    used=format_date_fr(self.env, voucher.date_used)))

    @api.constrains('date')
    def _check_date_is_plausible(self):
        """Garde-fou de dernier recours contre les dates aberrantes.

        L'avertissement de saisie couvre le cas courant ; cette contrainte
        arrête les fautes de frappe grossières (« 2062 » pour « 2026 ») qui
        fausseraient durablement toutes les statistiques.
        """
        limit = fields.Date.context_today(self) + relativedelta(years=1)
        for voucher in self:
            if voucher.date and voucher.date > limit:
                raise ValidationError(_(
                    "La date du bon %(name)s (%(date)s) est trop lointaine — "
                    "plus d'un an dans le futur. Corrigez la saisie.",
                    name=voucher.name, date=voucher.date))

    # =========================================================================
    # CRUD
    # =========================================================================

    @api.model_create_multi
    def create(self, vals_list):
        """Complète le prix depuis le référentiel, normalise et suit le numéro."""
        for vals in vals_list:
            if not vals.get('price_unit') and vals.get('fuel_type_id'):
                fuel_type = self.env['secretariat.fuel.type'].browse(vals['fuel_type_id'])
                vals['price_unit'] = fuel_type.price
            if vals.get('name'):
                vals['name'] = self._normalize_voucher_number(vals['name'])
            else:
                # Création par code ou par import : le numéro n'est pas
                # toujours fourni. On ne laisse jamais un bon sans numéro.
                vals['name'] = self._default_voucher_number() or '/'
        vouchers = super().create(vals_list)
        vouchers._sync_number_sequence()
        return vouchers

    def write(self, vals):
        """Un bon validé est figé — mais seulement sur un changement réel.

        Comparer les valeurs avant de refuser évite de bloquer les écritures
        idempotentes : édition multiple sur une colonne déjà à la bonne valeur,
        re-sauvegarde d'un formulaire non modifié, recalculs de l'ORM.
        """
        if vals.get('name'):
            vals['name'] = self._normalize_voucher_number(vals['name'])
        self._check_invoice_lock(vals)
        protected = set(LOCKED_FIELDS)
        if self.env.context.get('secretariat_referential_merge'):
            # Seule la fusion de référentiels lève une partie du verrou, et
            # seulement sur l'engin et le bénéficiaire : elle repointe le bon
            # vers l'enregistrement conservé, qui désigne la MÊME réalité —
            # « Camara » et « Camara M. » sont une seule personne. Le numéro,
            # la date, le prix et la quantité restent figés.
            protected -= {'vehicle_id', 'beneficiary_id'}
        touched = protected & set(vals)
        if touched:
            locked = self.filtered(
                lambda v: v.state == 'confirmed' and v._has_real_change(vals, touched))
            if locked:
                raise ValidationError(_(
                    "Le bon %(name)s est validé : repassez-le en brouillon pour "
                    "le modifier.", name=", ".join(locked.mapped('name')[:5]),
                ))
        result = super().write(vals)
        if vals.get('name'):
            self._sync_number_sequence()
        return result

    def _check_invoice_lock(self, vals):
        """Refuse de toucher à ce qui compose une facture déjà arrêtée.

        Le verrou de validation ne suffit pas : un bon peut être repassé en
        brouillon, alors même que son montant a été facturé à la station et
        payé. Ce second verrou protège l'accord passé avec la station, pas
        l'état du bon.
        """
        touched = set(INVOICE_LOCKED_FIELDS) & set(vals)
        if not touched:
            return
        locked = self.filtered(
            lambda v: v.invoice_id
            and v.invoice_id.state in INVOICE_LOCKING_STATES
            and v._has_real_change(vals, touched))
        if locked:
            raise ValidationError(_(
                "Le bon %(name)s figure sur la facture %(invoice)s, déjà "
                "arrêtée avec la station. Remettez cette facture en brouillon "
                "pour modifier le bon.",
                name=", ".join(locked.mapped('name')[:5]),
                invoice=", ".join(set(locked.mapped('invoice_id.name')))))

    def _has_real_change(self, vals, field_names):
        """Vrai si l'une des valeurs écrites diffère de la valeur en base."""
        self.ensure_one()
        for field_name in field_names:
            field = self._fields[field_name]
            new_value = vals[field_name]
            current = self[field_name]
            if field.type == 'many2one':
                if (current.id or False) != (new_value or False):
                    return True
            elif field.type == 'float':
                if float_compare(current or 0.0, new_value or 0.0,
                                 precision_rounding=0.000001) != 0:
                    return True
            elif field.type == 'date':
                if current != fields.Date.to_date(new_value):
                    return True
            elif (current or '') != (new_value or ''):
                return True
        return False

    def unlink(self):
        invoiced = self.filtered('is_invoiced')
        if invoiced:
            raise ValidationError(_(
                "Impossible de supprimer un bon facturé (%(name)s). Il figure "
                "sur la facture %(invoice)s.",
                name=", ".join(invoiced.mapped('name')[:5]),
                invoice=", ".join(set(invoiced.mapped('invoice_id.name')))))
        confirmed = self.filtered(lambda v: v.state == 'confirmed')
        if confirmed:
            raise ValidationError(_(
                "Impossible de supprimer un bon validé (%(name)s). Annulez-le "
                "d'abord.", name=", ".join(confirmed.mapped('name')[:5]),
            ))
        return super().unlink()

    def copy_data(self, default=None):
        """Un bon dupliqué repart en brouillon, sans marque d'import."""
        default = dict(default or {})
        default.setdefault('date', fields.Date.context_today(self))
        return super().copy_data(default)

    # =========================================================================
    # WORKFLOW
    # =========================================================================

    def action_confirm(self):
        for voucher in self:
            if voucher.state != 'draft':
                continue
            if not voucher.fuel_type_id or voucher.quantity <= 0:
                raise ValidationError(_(
                    "Le bon %s doit avoir un carburant et une quantité "
                    "strictement positive avant d'être validé.", voucher.name))
            if voucher.price_unit <= 0:
                raise ValidationError(_(
                    "Le bon %s n'a pas de prix unitaire : le montant serait "
                    "nul et fausserait les statistiques.", voucher.name))
            voucher.state = 'confirmed'
        return True

    def action_cancel(self):
        """Annule le bon — sauf s'il est déjà facturé.

        Annuler un bon facturé le sortirait des statistiques tout en le
        laissant dans le montant réglé à la station : l'écart deviendrait
        invisible.
        """
        invoiced = self.filtered('is_invoiced')
        if invoiced:
            raise ValidationError(_(
                "Le bon %(name)s figure sur la facture %(invoice)s : retirez-le "
                "d'abord de cette facture (remise en brouillon) avant de "
                "l'annuler.",
                name=", ".join(invoiced.mapped('name')[:5]),
                invoice=", ".join(set(invoiced.mapped('invoice_id.name')))))
        self.write({'state': 'cancelled'})
        return True

    def action_draft(self):
        self.write({'state': 'draft'})
        return True

    # =========================================================================
    # ACTIONS D'ÉCRAN
    # =========================================================================

    @api.model
    def action_open_import_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Importer le classeur Excel"),
            'res_model': 'secretariat.fuel.import.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    @api.model
    def action_open_export_wizard(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _("Générer le classeur Excel"),
            'res_model': 'secretariat.fuel.export.wizard',
            'view_mode': 'form',
            'target': 'new',
        }

    def action_open_invoice(self):
        """Ouvre la facture de station qui reprend ce bon."""
        self.ensure_one()
        if not self.invoice_id:
            return False
        return {
            'type': 'ir.actions.act_window',
            'name': _("Facture de station"),
            'res_model': 'secretariat.fuel.invoice',
            'res_id': self.invoice_id.id,
            'view_mode': 'form',
        }

    def action_detach_invoice(self):
        """Retire le bon de sa facture de quinzaine.

        Possible tant que la facture est en brouillon : le contrôle du relevé
        consiste précisément à écarter les bons qui n'auraient pas dû y être.
        """
        for voucher in self:
            if not voucher.invoice_id:
                continue
            if voucher.invoice_id.state != 'draft':
                raise ValidationError(_(
                    "La facture %(invoice)s n'est plus en brouillon : le bon "
                    "%(name)s ne peut pas en être retiré.",
                    invoice=voucher.invoice_id.name, name=voucher.name))
            voucher.invoice_id = False
        return True

    def action_mark_used_today(self):
        """Marque les bons sélectionnés comme utilisés aujourd'hui.

        Le geste le plus fréquent du retour de carnet : la secrétaire coche
        les bons revenus de la station et les date d'un clic.
        """
        today = fields.Date.context_today(self)
        for voucher in self:
            if voucher.state == 'cancelled' or voucher.date_used:
                continue
            # Un bon émis à une date future serait daté avant son émission.
            voucher.date_used = max(today, voucher.date) if voucher.date else today
        return True

    def action_print(self):
        """Imprime le bon.

        ``config=False`` : le bon utilise ``web.basic_layout`` et dessine son
        propre en-tête. Sans cela, Odoo intercalerait l'assistant de choix de
        mise en page tant que la société n'en a pas configuré une — sans le
        moindre effet sur ce document.
        """
        self.ensure_one()
        return self.env.ref(
            'ivorycocoa_secretariat.action_report_fuel_voucher'
        ).report_action(self, config=False)

    # =========================================================================
    # HELPERS PARTAGÉS AVEC LES ASSISTANTS
    # =========================================================================

    def dedup_key(self):
        """Clé de dédoublonnage d'un bon (cf. docstring du modèle)."""
        self.ensure_one()
        return self._dedup_key(
            self.name, self.date, self.vehicle_id.id,
            self.fuel_type_id.id, self.quantity)

    @staticmethod
    def _dedup_key(name, date, vehicle_id, fuel_type_id, quantity):
        return (
            canonical_number(name),
            str(date or ''),
            vehicle_id or 0,
            fuel_type_id or 0,
            round(quantity or 0.0, 3),
        )

    def month_label(self):
        """« Janvier 2026 » — libellé de regroupement mensuel du bon."""
        self.ensure_one()
        return month_label_fr(self.date)

    # =========================================================================
    # NUMÉROTATION
    # =========================================================================

    @api.model
    def _number_sequence(self):
        """Le compteur des bons, en sudo.

        Lire ``number_next_actual`` interroge la séquence PostgreSQL, et la
        recaler écrit sur ``ir.sequence`` : deux opérations hors de portée
        d'une secrétaire. Le compteur n'est pas une donnée métier sensible —
        il ne fait que proposer le prochain numéro — d'où le sudo, assumé.
        """
        sequence = self.env.ref(
            'ivorycocoa_secretariat.seq_secretariat_fuel_voucher',
            raise_if_not_found=False)
        return sequence.sudo() if sequence else sequence

    @api.model
    def _number_padding(self):
        sequence = self._number_sequence()
        return (sequence.padding if sequence else 0) or DEFAULT_NUMBER_PADDING

    @api.model
    def _default_voucher_number(self):
        """Prochain numéro — LU, jamais consommé.

        Consommer la séquence à l'ouverture du formulaire trouerait la
        numérotation à chaque saisie abandonnée. On se contente de lire le
        compteur ; c'est ``_sync_number_sequence`` qui l'avance après une
        création réelle, en se calant sur le numéro effectivement retenu.
        """
        sequence = self._number_sequence()
        if not sequence:
            return False
        return self._format_voucher_number(sequence.number_next_actual)

    @api.model
    def _format_voucher_number(self, number):
        """« 6276 » → « 00006276 »."""
        try:
            return str(int(number)).zfill(self._number_padding())
        except (TypeError, ValueError):
            return str(number or '')

    @api.model
    def _normalize_voucher_number(self, value):
        """Met un numéro saisi au format du carnet : huit chiffres.

        Un numéro non numérique (« B-12/A ») est respecté tel quel : tous les
        carnets ne se ressemblent pas, et refuser la saisie bloquerait le
        secrétariat pour une question de forme. Le contexte
        ``secretariat_keep_number`` désactive le complément — c'est l'import
        du classeur, qui reprend l'historique tel qu'il a été écrit.
        """
        text = str(value or '').strip()
        if not text.isdigit():
            return text
        if self.env.context.get('secretariat_keep_number'):
            # L'import du classeur reprend l'HISTORIQUE : il doit reproduire
            # le fichier source à l'identique. Compléter ces numéros à huit
            # caractères reviendrait à renuméroter le passé — précisément ce
            # qu'on a choisi de ne pas faire. Le dédoublonnage compare les
            # numéros à leur valeur, les deux écritures se rapprochent donc
            # quand même.
            return text
        padding = self._number_padding()
        return text.zfill(padding) if len(text) <= padding else text

    @api.model
    def _number_variants(self, value):
        """Écritures équivalentes d'un même numéro (avec et sans zéros)."""
        text = str(value or '').strip()
        if not text:
            return []
        if not text.isdigit():
            return [text]
        stripped = str(int(text))
        return list(dict.fromkeys([text, stripped, self._normalize_voucher_number(stripped)]))

    def _sync_number_sequence(self):
        """Recale le compteur sur le plus grand numéro réellement utilisé.

        La secrétaire peut corriger le numéro proposé pour coller à la souche.
        Si elle saute à 6300, le compteur doit repartir de 6301 — sinon il
        reproposerait indéfiniment des numéros déjà consommés sur le carnet.
        Il n'est jamais reculé : un bon saisi en retard avec un vieux numéro
        ne doit pas faire redescendre la numérotation.
        """
        sequence = self._number_sequence()
        if not sequence or sequence.implementation != 'standard':
            return
        numbers = [
            int(name) for name in self.mapped('name')
            if name and str(name).strip().isdigit()
        ]
        if not numbers:
            return
        highest = max(numbers) + 1
        if highest > sequence.number_next_actual:
            sequence.number_next_actual = highest


def canonical_number(value):
    """Forme comparable d'un numéro de bon.

    « 6275 », « 06275 » et « 00006275 » désignent le même bon : le classeur
    d'origine ne mettait pas de zéros de remplissage, le module en met huit.
    Comparer les chaînes brutes ferait passer l'historique et les nouveaux
    bons pour des bons différents — et le dédoublonnage de l'import
    recréerait tout.
    """
    text = str(value or '').strip()
    if text.isdigit():
        return str(int(text))
    return text.lower()


def month_label_fr(value):
    """« Janvier 2026 » à partir d'une date."""
    if not value:
        return ''
    return "%s %s" % (MONTH_NAMES_FR[value.month], value.year)


def format_date_fr(env, value):
    """Date au format court français, pour les messages destinés à l'écran."""
    if not value:
        return ''
    return fields.Date.to_date(value).strftime('%d/%m/%Y')
