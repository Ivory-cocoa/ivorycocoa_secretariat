/** @odoo-module **/

/**
 * Tableau de bord du secrétariat.
 *
 * La coque (période, en-tête, actions rapides) est indépendante des sections
 * métier : le module a vocation à couvrir d'autres domaines que le carburant,
 * une section supplémentaire viendra s'ajouter au template sans toucher ici.
 *
 * Le composant ne calcule rien : tout vient d'un unique appel à
 * `secretariat.dashboard.get_dashboard_data`.
 */

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const CHART_JS_LOCAL = "/ivorycocoa_secretariat/static/lib/chartjs/chart.umd.min.js";

// Palette des carburants — stable d'un rechargement à l'autre.
const FUEL_PALETTE = [
    "#305496", "#0ea5e9", "#10b981", "#f59e0b",
    "#ef4444", "#8b5cf6", "#14b8a6", "#f97316",
    "#64748b", "#ec4899",
];

const EMPTY_STATE = {
    stats: {
        voucher_count: 0, quantity: 0, amount: 0,
        previous_voucher_count: 0, previous_quantity: 0, previous_amount: 0,
        amount_delta: null, quantity_delta: null, count_delta: null,
        average_amount: 0, ytd_amount: 0, ytd_quantity: 0, ytd_count: 0,
    },
    todo: {
        draft: 0, incomplete: 0, future: 0, duplicates: 0,
        duplicate_ids: [], over_quota: 0, unlinked_beneficiaries: 0,
    },
    monthly_trend: [],
    fuel_split: [],
    top_vehicles: [],
    top_beneficiaries: [],
    quota_watch: [],
    anomalies: [],
    recent: [],
    price_reference: [],
};

export class SecretariatDashboard extends Component {
    static template = "ivorycocoa_secretariat.SecretariatDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.trendChartRef = useRef("trendChart");
        this.fuelChartRef = useRef("fuelChart");
        this._charts = {};
        this._reducedMotion =
            window.matchMedia &&
            window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        this.state = useState({
            loading: true,
            chartsAvailable: true,
            period: "month",
            dateFrom: "",
            dateTo: "",
            periodLabel: "",
            previousLabel: "",
            currency: { symbol: "", position: "after", decimals: 0 },
            user: { name: "", is_manager: false },
            fuel: JSON.parse(JSON.stringify(EMPTY_STATE)),
        });

        onWillStart(async () => {
            await this._loadChartJS();
            await this.loadData();
        });
        onMounted(() => this._renderCharts());
        onWillUnmount(() => this._destroyCharts());
    }

    // ------------------------------------------------------------------
    // Chargement
    // ------------------------------------------------------------------

    async _loadChartJS() {
        if (window.Chart) {
            return;
        }
        try {
            await loadJS(CHART_JS_LOCAL);
        } catch (error) {
            // Le tableau de bord reste utilisable : seuls les graphiques
            // disparaissent, les chiffres et les listes sont là.
            this.state.chartsAvailable = false;
            console.warn("Chart.js indisponible — graphiques désactivés.", error);
        }
    }

    async loadData() {
        this.state.loading = true;
        try {
            const result = await this.orm.call(
                "secretariat.dashboard",
                "get_dashboard_data",
                [
                    this.state.period,
                    this.state.period === "custom" ? this.state.dateFrom : null,
                    this.state.period === "custom" ? this.state.dateTo : null,
                ]
            );
            this.state.periodLabel = result.period?.label || "";
            this.state.previousLabel = result.period?.previous_label || "";
            this.state.currency = result.currency || this.state.currency;
            this.state.user = result.user || this.state.user;
            this.state.fuel = Object.assign(
                JSON.parse(JSON.stringify(EMPTY_STATE)), result.fuel || {});
        } catch (error) {
            this.notification.add(
                "Le tableau de bord n'a pas pu être chargé.", { type: "danger" });
            console.error("Tableau de bord secrétariat :", error);
        } finally {
            this.state.loading = false;
        }
        // Les canvas ne sont montés qu'après le rendu déclenché ci-dessus.
        setTimeout(() => this._renderCharts(), 60);
    }

    onPeriodChange(ev) {
        this.state.period = ev.target.value;
        if (this.state.period !== "custom") {
            this.loadData();
        }
    }

    onDateFromChange(ev) {
        this.state.dateFrom = ev.target.value;
        this._reloadIfCustomComplete();
    }

    onDateToChange(ev) {
        this.state.dateTo = ev.target.value;
        this._reloadIfCustomComplete();
    }

    _reloadIfCustomComplete() {
        if (this.state.dateFrom && this.state.dateTo) {
            this.loadData();
        }
    }

    // ------------------------------------------------------------------
    // Mise en forme
    // ------------------------------------------------------------------

    formatNumber(value, decimals = 0) {
        return (value || 0).toLocaleString("fr-FR", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
    }

    formatAmount(value) {
        const amount = this.formatNumber(Math.round(value || 0));
        const { symbol, position } = this.state.currency;
        if (!symbol) {
            return amount;
        }
        return position === "before" ? `${symbol} ${amount}` : `${amount} ${symbol}`;
    }

    /** Classe et signe de la variation par rapport à la période précédente. */
    deltaClass(delta) {
        if (delta === null || delta === undefined) {
            return "sec-delta--flat";
        }
        if (delta > 0) {
            return "sec-delta--up";
        }
        return delta < 0 ? "sec-delta--down" : "sec-delta--flat";
    }

    deltaLabel(delta) {
        if (delta === null || delta === undefined) {
            return "—";
        }
        const sign = delta > 0 ? "+" : "";
        return `${sign}${this.formatNumber(delta, 1)} %`;
    }

    /** Part d'une ligne dans le plus gros total de sa liste, en %. */
    barWidth(value, rows, key = "amount") {
        const max = Math.max(1, ...rows.map((row) => row[key] || 0));
        return Math.round(((value || 0) / max) * 100);
    }

    quotaClass(rate) {
        if (rate > 100) {
            return "sec-quota--over";
        }
        return rate >= 80 ? "sec-quota--warn" : "sec-quota--ok";
    }

    get todoItems() {
        const todo = this.state.fuel.todo;
        return [
            {
                key: "incomplete", label: "Bons à compléter", icon: "fa-pencil-square-o",
                count: todo.incomplete, level: "warn",
                hint: "Il leur manque un engin, un bénéficiaire, un prix ou une quantité.",
            },
            {
                key: "draft", label: "Brouillons à valider", icon: "fa-hourglass-half",
                count: todo.draft, level: "info",
                hint: "Bons enregistrés mais pas encore validés.",
            },
            {
                key: "duplicates", label: "Doublons probables", icon: "fa-clone",
                count: todo.duplicates, level: "danger",
                hint: "Même numéro, même date, même engin, même carburant et même quantité.",
            },
            {
                key: "over_quota", label: "Engins en dépassement", icon: "fa-tachometer",
                count: todo.over_quota, level: "danger",
                hint: "Dotation mensuelle dépassée ce mois-ci.",
            },
            {
                key: "future", label: "Dates dans le futur", icon: "fa-calendar-times-o",
                count: todo.future, level: "warn",
                hint: "Souvent une faute de frappe dans l'année.",
            },
            {
                key: "unlinked_beneficiaries", label: "Bénéficiaires sans fiche",
                icon: "fa-user-times", count: todo.unlinked_beneficiaries, level: "info",
                hint: "Bénéficiaires déclarés « employé » mais non rattachés à une fiche RH.",
            },
        ];
    }

    get hasTodo() {
        return this.todoItems.some((item) => item.count > 0);
    }

    // ------------------------------------------------------------------
    // Graphiques
    // ------------------------------------------------------------------

    _destroyCharts() {
        Object.values(this._charts).forEach((chart) => chart && chart.destroy());
        this._charts = {};
    }

    /**
     * Crée le graphique, ou met ses données à jour en place : Chart.js anime
     * alors la transition, sans clignotement au rechargement.
     */
    _upsertChart(key, el, config) {
        const existing = this._charts[key];
        if (!el) {
            if (existing) {
                existing.destroy();
                delete this._charts[key];
            }
            return;
        }
        if (existing && existing.canvas === el) {
            existing.data = config.data;
            existing.update();
            return;
        }
        if (existing) {
            existing.destroy();
        }
        this._charts[key] = new Chart(el.getContext("2d"), config);
    }

    _renderCharts() {
        if (!window.Chart) {
            return;
        }
        this._renderTrendChart();
        this._renderFuelChart();
    }

    _renderTrendChart() {
        const trend = this.state.fuel.monthly_trend;
        const el = trend.length ? this.trendChartRef.el : null;
        this._upsertChart("trend", el, {
            type: "bar",
            data: {
                labels: trend.map((month) => month.short),
                datasets: [
                    {
                        label: "Montant",
                        data: trend.map((month) => Math.round(month.amount)),
                        backgroundColor: "rgba(48, 84, 150, 0.5)",
                        borderColor: "#305496",
                        borderWidth: 1,
                        borderRadius: 3,
                        yAxisID: "y",
                        order: 2,
                    },
                    {
                        label: "Quantité",
                        type: "line",
                        data: trend.map((month) => month.quantity),
                        borderColor: "#f59e0b",
                        backgroundColor: "rgba(245, 158, 11, 0.15)",
                        borderWidth: 2,
                        pointRadius: 3,
                        tension: 0.3,
                        yAxisID: "y1",
                        order: 1,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: "index", intersect: false },
                animation: this._reducedMotion ? false : { duration: 500 },
                plugins: { legend: { labels: { boxWidth: 14 } } },
                scales: {
                    y: {
                        beginAtZero: true,
                        position: "left",
                        title: { display: true, text: "Montant" },
                    },
                    y1: {
                        beginAtZero: true,
                        position: "right",
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: "Quantité" },
                    },
                },
            },
        });
    }

    _renderFuelChart() {
        const split = this.state.fuel.fuel_split;
        const el = split.length ? this.fuelChartRef.el : null;
        this._upsertChart("fuel", el, {
            type: "doughnut",
            data: {
                labels: split.map((row) => row.name),
                datasets: [{
                    data: split.map((row) => Math.round(row.amount)),
                    backgroundColor: split.map(
                        (_row, index) => FUEL_PALETTE[index % FUEL_PALETTE.length]),
                    borderWidth: 2,
                    hoverOffset: 6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "60%",
                animation: this._reducedMotion ? false : { duration: 500 },
                plugins: {
                    legend: { position: "bottom", labels: { padding: 10, boxWidth: 12 } },
                },
            },
        });
    }

    // ------------------------------------------------------------------
    // Navigation
    // ------------------------------------------------------------------

    _openAction(xmlId) {
        this.action.doAction(xmlId);
    }

    _openList(name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "secretariat.fuel.voucher",
            views: [[false, "list"], [false, "form"]],
            domain,
            target: "current",
        });
    }

    openTodo(key) {
        const todo = this.state.fuel.todo;
        switch (key) {
            case "incomplete":
                return this._openAction(
                    "ivorycocoa_secretariat.action_secretariat_fuel_voucher_incomplete");
            case "draft":
                return this._openList("Brouillons à valider", [["state", "=", "draft"]]);
            case "duplicates":
                return this._openList(
                    "Doublons probables", [["id", "in", todo.duplicate_ids || []]]);
            case "over_quota":
                return this.action.doAction(
                    "ivorycocoa_secretariat.action_secretariat_vehicle_over_quota");
            case "future":
                return this._openList("Bons datés dans le futur", [
                    ["date", ">", new Date().toISOString().slice(0, 10)],
                    ["state", "!=", "cancelled"],
                ]);
            case "unlinked_beneficiaries":
                return this.action.doAction({
                    type: "ir.actions.act_window",
                    name: "Bénéficiaires sans fiche employé",
                    res_model: "secretariat.fuel.beneficiary",
                    views: [[false, "list"], [false, "form"]],
                    domain: [["category", "=", "employee"], ["employee_id", "=", false]],
                    target: "current",
                });
            default:
                return undefined;
        }
    }

    newVoucher() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Nouveau bon de carburant",
            res_model: "secretariat.fuel.voucher",
            views: [[false, "form"]],
            target: "current",
        });
    }

    quickEntry() {
        this._openAction("ivorycocoa_secretariat.action_secretariat_fuel_voucher_quick");
    }

    openImport() {
        this._openAction("ivorycocoa_secretariat.action_secretariat_fuel_import_wizard");
    }

    openExport() {
        this._openAction("ivorycocoa_secretariat.action_secretariat_fuel_export_wizard");
    }

    openMonthlyReport() {
        this._openAction(
            "ivorycocoa_secretariat.action_secretariat_fuel_monthly_report_wizard");
    }

    openAllVouchers() {
        this._openAction("ivorycocoa_secretariat.action_secretariat_fuel_voucher");
    }

    openVoucher(voucherId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "secretariat.fuel.voucher",
            res_id: voucherId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openVehicle(vehicleId) {
        if (!vehicleId) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "secretariat.vehicle",
            res_id: vehicleId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openBeneficiary(beneficiaryId) {
        if (!beneficiaryId) {
            return;
        }
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "secretariat.fuel.beneficiary",
            res_id: beneficiaryId,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("secretariat_dashboard", SecretariatDashboard);
