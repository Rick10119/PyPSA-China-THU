#!/usr/bin/env python3
"""
Figure 1: cost competitiveness of shifting 1 kWh electricity use.

This script replaces the former schematic values with bottom-up estimates
derived from local PyPSA-China cost/price data plus documented public sources.
All plotted values are CNY per kWh shifted or made available.

Run with the project environment, for example:

    conda run -n pypsa python scripts/plot_flexibility_cost_timescales.py
"""

from __future__ import annotations

import csv
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
logging.getLogger("fontTools").setLevel(logging.WARNING)
logging.getLogger("pypsa").setLevel(logging.WARNING)
logging.getLogger("pypsa.network.io").setLevel(logging.ERROR)

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


OUT_DIR = ROOT / "results" / "figures"
DATA_OUT_DIR = ROOT / "data" / "flexibility_cost_timescales"
COST_FILE = ROOT / "data" / "costs" / "costs_2025.csv"
DISPATCH_PRICE_DIR = ROOT / "results" / "version-0602.1H.2" / "prices" / "dispatch_segmented" / "positive"
SYSTEM_2025_PRICE_FILE = DISPATCH_PRICE_DIR / "dispatch_segmented_prices-ll-current+FCG-linear2050-2025_nodal_marginal.csv"
SYSTEM_2050_PRICE_FILE = (
    ROOT
    / "results"
    / "version-0120.1H.1-MMMF-2050-15p"
    / "postnetworks"
    / "positive"
    / "postnetwork-ll-current+FCG-linear2050-2050.nc"
)

FX_CNY_PER_EUR = 7.8
FX_CNY_PER_USD = 7.2
DISCOUNT_RATE = 0.07
CHARGE_ELECTRICITY_CNY_PER_KWH = 0.30
CHINA_2025_TYPICAL_INTRADAY_SPREAD_CNY_PER_KWH = 0.45
ALUMINIUM_WAREHOUSING_CNY_PER_TONNE_MONTH = 6.9
ALUMINIUM_WAREHOUSING_MAX_MONTHS = 2.0
STEEL_WAREHOUSING_CNY_PER_TONNE_MONTH = 2.0
STEEL_WAREHOUSING_MAX_MONTHS = 2.0


@dataclass(frozen=True)
class TimeScale:
    label: str
    key: str
    duration_h: float
    cycles_per_year: float
    note: str


SCALES = [
    TimeScale("Daily", "daily", 24.0, 365.0, "24 h window, daily cycling"),
    TimeScale("Weekly", "weekly", 168.0, 365.0 / 7.0, "7 d window, 365/7 cycles per year"),
    TimeScale("Monthly", "monthly", 730.0, 8760.0 / 730.0, "730 h window, about 12 cycles per year"),
    TimeScale("Seasonal", "seasonal", 2160.0, 8760.0 / 2160.0, "3 month window, about 4 cycles per year"),
    TimeScale("Annual", "annual", 8760.0, 1.0, "8760 h window, annual cycling"),
]


STYLE = {
    "system_2025_market": {
        "color": "#111111",
        "lw": 2.25,
        "ls": (0, (5, 2.6)),
        "marker": None,
        "label": "System value, 2025",
    },
    "system_2050_carbon_neutral": {
        "color": "#111111",
        "lw": 2.9,
        "ls": "-",
        "marker": None,
        "label": "System value, 2050 CN",
    },
    "battery": {
        "color": "#3763A6",
        "lw": 2.4,
        "ls": "-",
        "marker": "o",
        "label": "Battery storage",
    },
    "hydrogen": {
        "color": "#1B9A8A",
        "lw": 2.4,
        "ls": "-",
        "marker": "o",
        "label": "Hydrogen storage",
    },
    "aluminium_no_excess": {
        "color": "#D0604C",
        "lw": 2.1,
        "ls": (0, (3, 2.5)),
        "marker": None,
        "label": "Aluminium, no excess",
    },
    "steel_no_excess": {
        "color": "#8B5A9B",
        "lw": 2.1,
        "ls": (0, (3, 2.5)),
        "marker": None,
        "label": "Steel, no excess",
    },
    "data_center_no_excess": {
        "color": "#727272",
        "lw": 2.1,
        "ls": (0, (3, 2.5)),
        "marker": None,
        "label": "Data centres, no excess",
    },
    "aluminium_sunk": {
        "color": "#D0604C",
        "lw": 2.3,
        "ls": "-",
        "marker": "s",
        "label": "Aluminium, sunk excess",
    },
    "steel_sunk": {
        "color": "#8B5A9B",
        "lw": 2.3,
        "ls": "-",
        "marker": "s",
        "label": "Steel, sunk excess",
    },
    "data_center_sunk": {
        "color": "#727272",
        "lw": 2.3,
        "ls": "-",
        "marker": "s",
        "label": "Data centres, sunk excess",
    },
}


SOURCES = {
    "local_costs_2025": {
        "source": "data/costs/costs_2025.csv",
        "url": "local file",
        "notes": "Battery, electrolysis, fuel cell, and underground hydrogen storage costs used in the PyPSA-China cost table.",
    },
    "china_2025_intraday_spread": {
        "source": "CESC, 谁在深夜为电费买单：2025年现货电价峰谷差的12省密码",
        "url": "https://www.cescexpo.cn/%E8%B0%81%E5%9C%A8%E6%B7%B1%E5%A4%9C%E4%B8%BA%E7%94%B5%E8%B4%B9%E4%B9%B0%E5%8D%95-2025%E5%B9%B4%E7%8E%B0%E8%B4%A7%E7%94%B5%E4%BB%B7%E5%B3%B0%E8%B0%B7%E5%B7%AE%E7%9A%8412%E7%9C%81%E5%AF%86%E7%A0%81",
        "notes": "Uses annual average daily intraday spot spread from China provincial spot markets: Shandong is about 449 CNY/MWh and the medium-volatility provinces are about 300-500 CNY/MWh. Rounded proxy used here: 0.45 CNY/kWh across cycle periods.",
    },
    "dispatch_segmented_2050": {
        "source": "results/version-0120.1H.1-MMMF-2050-15p/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2050.nc",
        "url": "local file",
        "notes": "2050 MMMF carbon-neutral postnetwork. Value uses buses_t.marginal_price for the 31 AC provincial buses, converted from EUR/MWh to CNY/kWh.",
    },
    "aluminium_local_docs": {
        "source": "docs/README_aluminum_iterative.md and docs/flexible_aluminum_smelting_intro.md",
        "url": "local files",
        "notes": "Aluminium potline size and electricity intensity: 13.3 MWh/t in the model documentation; Chinese capacity data are in data/aluminum_demand/.",
    },
    "aluminium_project_capex": {
        "source": "CNAL/世铝网, 万基铝业58万吨电解铝建设项目",
        "url": "https://news.cnal.com/2026/03-27/1774570998661258.shtml",
        "notes": "Project investment is about 4.595 billion CNY for 580 kt/year capacity, used as a contemporary electrolytic-aluminium capex proxy.",
    },
    "steel_capex_intensity": {
        "source": "Steelonthenet EAF capital investment costs and industry EAF electricity-intensity ranges",
        "url": "https://www.steelonthenet.com/resources/capital-investment/eaf.html",
        "notes": "EAF steel: capex proxy 143 USD per annual tonne capacity; electricity intensity 440 kWh/t steel.",
    },
    "data_center_capex": {
        "source": "JLL data-centre cost estimates and IEA/Uptime Institute efficiency context",
        "url": "https://www.us.jll.com/en/trends-and-insights/research/data-center-outlook",
        "notes": "Hyperscale data centre capex proxy: 10.7 million USD/MW IT load; PUE/flexibility context from IEA and Uptime Institute public reporting.",
    },
    "seasonal_working_capital": {
        "source": "docs/季节性生产资金成本计算.md",
        "url": "local file",
        "notes": "Working-capital carrying cost follows the incremental WCR area method: compare seasonal/cyclical production with balanced production and apply the funding rate to the extra inventory-value time integral.",
    },
    "aluminium_warehousing_cost": {
        "source": "User-provided warehousing cost note: 仓储成本计算说明（供迁移）",
        "url": "conversation note",
        "notes": "Physical warehousing for aluminium and alumina is modeled separately from WACC: conservative upper bound 1 USD/t/month for about 2 months, or about 14 CNY/t-Al, excluding transport and locked-cash costs.",
    },
    "steel_warehousing_cost": {
        "source": "Warehouse-area method adapted from user-provided 仓储成本计算说明（供迁移）",
        "url": "conversation note",
        "notes": "Physical warehousing for steel is modeled separately from WACC using the same inventory-area method. Assumes denser finished steel storage, approximated at 2 CNY/t-steel/month and capped at about 2 months, excluding transport and locked-cash costs.",
    },
}


def crf(lifetime_years: float, discount_rate: float = DISCOUNT_RATE) -> float:
    return discount_rate / (1.0 - (1.0 + discount_rate) ** (-lifetime_years))


def load_cost_table(path: Path = COST_FILE) -> dict[tuple[str, str], dict[str, str]]:
    table: dict[tuple[str, str], dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            table[(row["technology"], row["parameter"])] = row
    return table


def cost_value(costs: dict[tuple[str, str], dict[str, str]], technology: str, parameter: str) -> float:
    return float(costs[(technology, parameter)]["value"])


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("Cannot compute percentile of an empty list.")
    if len(values) == 1:
        return values[0]
    idx = q * (len(values) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    frac = idx - lo
    ordered = sorted(values)
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def weighted_top_h_mean(values: list[float], h_hours: float) -> float:
    """Weighted Top-H mean, matching evaluate_storage_cycles.py's value method."""
    if h_hours <= 0:
        raise ValueError(f"h_hours must be positive, got {h_hours}")
    clean = sorted((v for v in values if math.isfinite(v)), reverse=True)
    if not clean:
        return math.nan

    h_floor = int(math.floor(h_hours))
    h_frac = float(h_hours - h_floor)
    weighted_sum = sum(clean[:h_floor])
    weight = min(float(h_floor), float(len(clean)))
    if h_frac > 1e-12 and h_floor < len(clean):
        weighted_sum += clean[h_floor] * h_frac
        weight += h_frac
    if weight <= 0:
        return math.nan
    return weighted_sum / weight


def weighted_bottom_h_mean(values: list[float], h_hours: float) -> float:
    if h_hours <= 0:
        raise ValueError(f"h_hours must be positive, got {h_hours}")
    clean = sorted(v for v in values if math.isfinite(v))
    if not clean:
        return math.nan

    h_floor = int(math.floor(h_hours))
    h_frac = float(h_hours - h_floor)
    weighted_sum = sum(clean[:h_floor])
    weight = min(float(h_floor), float(len(clean)))
    if h_frac > 1e-12 and h_floor < len(clean):
        weighted_sum += clean[h_floor] * h_frac
        weight += h_frac
    if weight <= 0:
        return math.nan
    return weighted_sum / weight


def load_wide_price_csv(path: Path, *, cny_per_mwh: bool) -> dict[str, list[float]]:
    prices: dict[str, list[float]] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"Empty price file: {path}")
        fieldnames = [name for name in reader.fieldnames if name not in {"snapshot", "hour", "time"}]
        for name in fieldnames:
            prices[name] = []
        for row in reader:
            for name in fieldnames:
                raw = row.get(name, "")
                if raw == "":
                    continue
                value = float(raw)
                prices[name].append(value / 1000.0 if cny_per_mwh else value * FX_CNY_PER_EUR / 1000.0)
    return prices


def load_network_marginal_prices(path: Path) -> dict[str, list[float]]:
    import pypsa

    n = pypsa.Network(path)
    marginal_price = n.buses_t.marginal_price
    ac_buses = n.buses.index[n.buses.carrier == "AC"]
    columns = [bus for bus in ac_buses if bus in marginal_price.columns]
    if not columns:
        columns = list(marginal_price.columns)

    converted = marginal_price[columns].astype(float) * FX_CNY_PER_EUR / 1000.0
    return {str(column): converted[column].tolist() for column in converted.columns}


def value_h_hours_for_scale(scale: TimeScale) -> float:
    if scale.key == "daily":
        return 2.6
    if scale.key == "weekly":
        return 24.0
    return 168.0


def value_window_hours_for_scale(scale: TimeScale) -> int:
    return int(scale.duration_h)


def system_values_from_marginal_prices(path: Path, *, cny_per_mwh: bool = True) -> list[float]:
    """Top-H minus Bottom-H nodal marginal price spread by scale."""
    if not path.exists():
        raise FileNotFoundError(f"Missing nodal marginal price file: {path}")
    if path.suffix.lower() == ".nc":
        price_by_bus = load_network_marginal_prices(path)
    else:
        price_by_bus = load_wide_price_csv(path, cny_per_mwh=cny_per_mwh)

    values: list[float] = []
    for scale in SCALES:
        window = value_window_hours_for_scale(scale)
        h_hours = value_h_hours_for_scale(scale)
        chunk_means: list[float] = []
        for series in price_by_bus.values():
            for start in range(0, len(series), window):
                block = series[start : start + window]
                if len(block) >= max(1, int(math.ceil(h_hours))):
                    top_mean = weighted_top_h_mean(block, h_hours)
                    bottom_mean = weighted_bottom_h_mean(block, h_hours)
                    if math.isfinite(top_mean) and math.isfinite(bottom_mean):
                        chunk_means.append(top_mean - bottom_mean)
        values.append(sum(chunk_means) / len(chunk_means))
    return values


def china_2025_intraday_spread_values() -> list[float]:
    return [CHINA_2025_TYPICAL_INTRADAY_SPREAD_CNY_PER_KWH for _ in SCALES]


def battery_costs(costs: dict[tuple[str, str], dict[str, str]]) -> list[float]:
    energy_capex = cost_value(costs, "battery storage", "investment") * FX_CNY_PER_EUR
    energy_lifetime = cost_value(costs, "battery storage", "lifetime")
    power_capex = cost_value(costs, "battery inverter", "investment") * FX_CNY_PER_EUR
    power_lifetime = cost_value(costs, "battery inverter", "lifetime")
    power_fom = cost_value(costs, "battery inverter", "FOM") / 100.0
    rte = cost_value(costs, "battery inverter", "efficiency")

    out: list[float] = []
    for scale in SCALES:
        annualized = energy_capex * crf(energy_lifetime)
        annualized += (power_capex / scale.duration_h) * (crf(power_lifetime) + power_fom)
        efficiency_loss = CHARGE_ELECTRICITY_CNY_PER_KWH * (1.0 / rte - 1.0)
        out.append(annualized / scale.cycles_per_year + efficiency_loss)
    return out


def hydrogen_costs(costs: dict[tuple[str, str], dict[str, str]]) -> list[float]:
    electrolyser_capex = cost_value(costs, "electrolysis", "investment") * FX_CNY_PER_EUR
    electrolyser_lifetime = cost_value(costs, "electrolysis", "lifetime")
    electrolyser_fom = cost_value(costs, "electrolysis", "FOM") / 100.0
    electrolyser_eff = cost_value(costs, "electrolysis", "efficiency")

    fuel_cell_capex = cost_value(costs, "fuel cell", "investment") * FX_CNY_PER_EUR
    fuel_cell_lifetime = cost_value(costs, "fuel cell", "lifetime")
    fuel_cell_fom = cost_value(costs, "fuel cell", "FOM") / 100.0
    fuel_cell_eff = cost_value(costs, "fuel cell", "efficiency")

    storage_capex = cost_value(costs, "hydrogen storage underground", "investment") * FX_CNY_PER_EUR
    storage_lifetime = cost_value(costs, "hydrogen storage underground", "lifetime")
    storage_fom = cost_value(costs, "hydrogen storage underground", "FOM") / 100.0

    out: list[float] = []
    for scale in SCALES:
        electrolyser_kw_per_kwh_out = 1.0 / (electrolyser_eff * fuel_cell_eff * scale.duration_h)
        fuel_cell_kw_per_kwh_out = 1.0 / scale.duration_h
        h2_kwh_per_kwh_out = 1.0 / fuel_cell_eff

        annualized = electrolyser_kw_per_kwh_out * electrolyser_capex * (
            crf(electrolyser_lifetime) + electrolyser_fom
        )
        annualized += fuel_cell_kw_per_kwh_out * fuel_cell_capex * (
            crf(fuel_cell_lifetime) + fuel_cell_fom
        )
        annualized += h2_kwh_per_kwh_out * storage_capex * (
            crf(storage_lifetime) + storage_fom
        )
        efficiency_loss = CHARGE_ELECTRICITY_CNY_PER_KWH * (
            1.0 / (electrolyser_eff * fuel_cell_eff) - 1.0
        )
        out.append(annualized / scale.cycles_per_year + efficiency_loss)
    return out


def load_no_excess_costs(
    *,
    capex_cny_per_annual_tonne: float | None = None,
    electricity_kwh_per_tonne: float | None = None,
    capex_cny_per_kw_load: float | None = None,
    lifetime_years: float,
    product_value_cny_per_tonne: float | None = None,
    warehousing_cny_per_tonne_month: float | None = None,
    warehousing_max_months: float | None = None,
) -> list[float]:
    if capex_cny_per_kw_load is None:
        if capex_cny_per_annual_tonne is None or electricity_kwh_per_tonne is None:
            raise ValueError("Industrial capex needs either CNY/kW load or CNY/t-y plus kWh/t.")
        capex_cny_per_kw_load = capex_cny_per_annual_tonne / (electricity_kwh_per_tonne / 8760.0)

    out: list[float] = []
    for scale in SCALES:
        capacity_cost = capex_cny_per_kw_load * crf(lifetime_years) / shifted_hours_per_year(scale)
        inventory_cost = 0.0
        if product_value_cny_per_tonne is not None and electricity_kwh_per_tonne is not None:
            inventory_cost = working_capital_cost(
                product_value_cny_per_tonne=product_value_cny_per_tonne,
                electricity_kwh_per_tonne=electricity_kwh_per_tonne,
                scale=scale,
            )
        warehousing_cost = 0.0
        if warehousing_cny_per_tonne_month is not None and electricity_kwh_per_tonne is not None:
            warehousing_cost = physical_warehousing_cost(
                warehousing_cny_per_tonne_month=warehousing_cny_per_tonne_month,
                electricity_kwh_per_tonne=electricity_kwh_per_tonne,
                scale=scale,
                max_months=warehousing_max_months,
            )
        out.append(capacity_cost + inventory_cost + warehousing_cost)
    return out


def shifted_hours_per_year(scale: TimeScale) -> float:
    return scale.cycles_per_year * scale.duration_h


def working_capital_lock_years(scale: TimeScale) -> float:
    """Cash-locking time from the seasonal-production WCR area method."""
    direct_deferral_years = scale.duration_h / 8760.0
    cycle_interval_years = 1.0 / scale.cycles_per_year
    triangular_wcr_years = 0.5 * cycle_interval_years
    return max(direct_deferral_years, triangular_wcr_years)


def working_capital_cost(
    *,
    product_value_cny_per_tonne: float,
    electricity_kwh_per_tonne: float,
    scale: TimeScale,
) -> float:
    return (
        product_value_cny_per_tonne
        * DISCOUNT_RATE
        * working_capital_lock_years(scale)
        / electricity_kwh_per_tonne
    )


def physical_warehousing_months(scale: TimeScale, max_months: float | None = None) -> float:
    months = scale.duration_h / (24.0 * 30.0)
    if max_months is not None:
        months = min(months, max_months)
    return months


def physical_warehousing_cost(
    *,
    warehousing_cny_per_tonne_month: float,
    electricity_kwh_per_tonne: float,
    scale: TimeScale,
    max_months: float | None = None,
) -> float:
    return (
        warehousing_cny_per_tonne_month
        * physical_warehousing_months(scale, max_months=max_months)
        / electricity_kwh_per_tonne
    )


def load_sunk_excess_costs(
    *,
    product_value_cny_per_tonne: float | None = None,
    electricity_kwh_per_tonne: float | None = None,
    fixed_floor_cny_per_kwh: float,
    duration_penalty_cny_per_kwh_sqrt_day: float,
    warehousing_cny_per_tonne_month: float | None = None,
    warehousing_max_months: float | None = None,
) -> list[float]:
    out: list[float] = []
    for scale in SCALES:
        working_capital = 0.0
        if product_value_cny_per_tonne is not None and electricity_kwh_per_tonne is not None:
            working_capital = working_capital_cost(
                product_value_cny_per_tonne=product_value_cny_per_tonne,
                electricity_kwh_per_tonne=electricity_kwh_per_tonne,
                scale=scale,
            )
        warehousing_cost = 0.0
        if warehousing_cny_per_tonne_month is not None and electricity_kwh_per_tonne is not None:
            warehousing_cost = physical_warehousing_cost(
                warehousing_cny_per_tonne_month=warehousing_cny_per_tonne_month,
                electricity_kwh_per_tonne=electricity_kwh_per_tonne,
                scale=scale,
                max_months=warehousing_max_months,
            )
        delay_proxy = duration_penalty_cny_per_kwh_sqrt_day * math.sqrt(scale.duration_h / 24.0)
        out.append(fixed_floor_cny_per_kwh + working_capital + warehousing_cost + delay_proxy)
    return out


def build_series() -> tuple[dict[str, dict[str, object]], list[dict[str, str]]]:
    costs = load_cost_table()
    system_2025_values = china_2025_intraday_spread_values()
    system_2050_values = system_values_from_marginal_prices(SYSTEM_2050_PRICE_FILE, cny_per_mwh=False)

    aluminium_intensity = 13_300.0
    aluminium_capex = 7_922.0
    aluminium_price = 20_000.0

    steel_intensity = 440.0
    steel_capex = 143.0 * FX_CNY_PER_USD
    steel_price = 3_500.0

    data_center_capex_kw = 10.7e6 * FX_CNY_PER_USD / 1000.0

    series = {
        "system_2025_market": {
            "resource": "system",
            "state": "value_2025_marginal_price",
            "type": "value",
            "includes_direct_cost": "NA",
            "values": system_2025_values,
            "source_ids": "china_2025_intraday_spread",
            "notes": "China 2025 typical intraday spot price spread proxy, rounded to 0.45 CNY/kWh from public provincial spot-market spread reporting. Applied across cycle periods because this is an observed intraday-market proxy rather than a long-duration model result.",
        },
        "system_2050_carbon_neutral": {
            "resource": "system",
            "state": "value_2050_carbon_neutral_marginal_price",
            "type": "value",
            "includes_direct_cost": "NA",
            "values": system_2050_values,
            "source_ids": "dispatch_segmented_2050",
            "notes": "2050 MMMF carbon-neutral AC-bus marginal price spread calculated with evaluate_storage_cycles.py Top-H/Bottom-H method: chunk-level weighted top-H minus bottom-H mean, then average across chunks and 31 provincial AC buses.",
        },
        "battery": {
            "resource": "battery",
            "state": "storage",
            "type": "cost",
            "includes_direct_cost": "yes",
            "values": battery_costs(costs),
            "source_ids": "local_costs_2025",
            "notes": "Annualized battery energy and inverter cost divided by cycles; includes round-trip efficiency loss valued at 0.30 CNY/kWh charging electricity.",
        },
        "hydrogen": {
            "resource": "hydrogen",
            "state": "storage",
            "type": "cost",
            "includes_direct_cost": "yes",
            "values": hydrogen_costs(costs),
            "source_ids": "local_costs_2025",
            "notes": "Power-to-hydrogen-to-power using electrolysis, fuel cell, and underground hydrogen storage from local cost table; includes conversion-loss electricity.",
        },
        "aluminium_no_excess": {
            "resource": "aluminium",
            "state": "no_excess_capacity",
            "type": "cost",
            "includes_direct_cost": "no",
            "values": load_no_excess_costs(
                capex_cny_per_annual_tonne=aluminium_capex,
                electricity_kwh_per_tonne=aluminium_intensity,
                lifetime_years=30.0,
                product_value_cny_per_tonne=aluminium_price,
                warehousing_cny_per_tonne_month=ALUMINIUM_WAREHOUSING_CNY_PER_TONNE_MONTH,
                warehousing_max_months=ALUMINIUM_WAREHOUSING_MAX_MONTHS,
            ),
            "source_ids": "aluminium_local_docs;aluminium_project_capex;seasonal_working_capital;aluminium_warehousing_cost",
            "notes": "New smelter-capacity opportunity cost divided by annual shifted kWh, plus WCR-area working-capital carrying cost and conservative physical warehousing for aluminium/alumina; direct process, restart, communication, and transaction costs excluded.",
        },
        "steel_no_excess": {
            "resource": "steel",
            "state": "no_excess_capacity",
            "type": "cost",
            "includes_direct_cost": "no",
            "values": load_no_excess_costs(
                capex_cny_per_annual_tonne=steel_capex,
                electricity_kwh_per_tonne=steel_intensity,
                lifetime_years=25.0,
                product_value_cny_per_tonne=steel_price,
                warehousing_cny_per_tonne_month=STEEL_WAREHOUSING_CNY_PER_TONNE_MONTH,
                warehousing_max_months=STEEL_WAREHOUSING_MAX_MONTHS,
            ),
            "source_ids": "steel_capex_intensity;seasonal_working_capital;steel_warehousing_cost",
            "notes": "EAF capacity opportunity cost divided by annual shifted kWh, plus WCR-area working-capital carrying cost and conservative physical warehousing; direct process, restart, communication, and transaction costs excluded.",
        },
        "data_center_no_excess": {
            "resource": "data_center",
            "state": "no_excess_capacity",
            "type": "cost",
            "includes_direct_cost": "no",
            "values": load_no_excess_costs(
                capex_cny_per_kw_load=data_center_capex_kw,
                lifetime_years=15.0,
            ),
            "source_ids": "data_center_capex",
            "notes": "Additional IT-load capacity divided by annual shifted kWh when no idle server capacity exists; direct SLA, network, and transaction costs excluded.",
        },
        "aluminium_sunk": {
            "resource": "aluminium",
            "state": "sunk_excess_capacity",
            "type": "cost",
            "includes_direct_cost": "no",
            "values": load_sunk_excess_costs(
                product_value_cny_per_tonne=aluminium_price,
                electricity_kwh_per_tonne=aluminium_intensity,
                fixed_floor_cny_per_kwh=0.01,
                duration_penalty_cny_per_kwh_sqrt_day=0.005,
                warehousing_cny_per_tonne_month=ALUMINIUM_WAREHOUSING_CNY_PER_TONNE_MONTH,
                warehousing_max_months=ALUMINIUM_WAREHOUSING_MAX_MONTHS,
            ),
            "source_ids": "aluminium_local_docs;aluminium_project_capex;seasonal_working_capital;aluminium_warehousing_cost",
            "notes": "Existing excess capacity treated as sunk; working-capital, physical warehousing, and scheduling proxy costs retained.",
        },
        "steel_sunk": {
            "resource": "steel",
            "state": "sunk_excess_capacity",
            "type": "cost",
            "includes_direct_cost": "no",
            "values": load_sunk_excess_costs(
                product_value_cny_per_tonne=steel_price,
                electricity_kwh_per_tonne=steel_intensity,
                fixed_floor_cny_per_kwh=0.02,
                duration_penalty_cny_per_kwh_sqrt_day=0.01,
                warehousing_cny_per_tonne_month=STEEL_WAREHOUSING_CNY_PER_TONNE_MONTH,
                warehousing_max_months=STEEL_WAREHOUSING_MAX_MONTHS,
            ),
            "source_ids": "steel_capex_intensity;seasonal_working_capital;steel_warehousing_cost",
            "notes": "Existing excess EAF capacity treated as sunk; working-capital, physical warehousing, and scheduling proxy costs retained.",
        },
        "data_center_sunk": {
            "resource": "data_center",
            "state": "sunk_excess_capacity",
            "type": "cost",
            "includes_direct_cost": "no",
            "values": load_sunk_excess_costs(
                fixed_floor_cny_per_kwh=0.02,
                duration_penalty_cny_per_kwh_sqrt_day=0.012,
            ),
            "source_ids": "data_center_capex",
            "notes": "Idle IT/server capacity treated as sunk; retained cost is a workload-delay/scheduling proxy, excluding SLA and network penalties.",
        },
    }

    assumptions = [
        {
            "parameter": "fx_cny_per_eur",
            "value": f"{FX_CNY_PER_EUR:g}",
            "unit": "CNY/EUR",
            "source_ids": "local_costs_2025",
            "notes": "Matches local PyPSA-China cost conversion convention.",
        },
        {
            "parameter": "fx_cny_per_usd",
            "value": f"{FX_CNY_PER_USD:g}",
            "unit": "CNY/USD",
            "source_ids": "local_costs_2025",
            "notes": "Matches local parameter-update documentation.",
        },
        {
            "parameter": "discount_rate",
            "value": f"{DISCOUNT_RATE:g}",
            "unit": "per unit",
            "source_ids": "local_costs_2025",
            "notes": "Unified market WACC used in local cost table.",
        },
        {
            "parameter": "charge_electricity_price_for_losses",
            "value": f"{CHARGE_ELECTRICITY_CNY_PER_KWH:g}",
            "unit": "CNY/kWh",
            "source_ids": "rmi_china_power_market_2025",
            "notes": "Conservative Chinese spot-market spread anchor used to price storage conversion losses.",
        },
        {
            "parameter": "china_2025_typical_intraday_spread",
            "value": f"{CHINA_2025_TYPICAL_INTRADAY_SPREAD_CNY_PER_KWH:g}",
            "unit": "CNY/kWh",
            "source_ids": "china_2025_intraday_spread",
            "notes": "Observed China spot-market daily intraday spread proxy used for the 2025 system-value line.",
        },
        {
            "parameter": "aluminium_electricity_intensity",
            "value": f"{aluminium_intensity:g}",
            "unit": "kWh/t-Al",
            "source_ids": "aluminium_local_docs",
            "notes": "Model documentation uses 13.3 MWh/t.",
        },
        {
            "parameter": "aluminium_new_capacity_capex",
            "value": f"{aluminium_capex:g}",
            "unit": "CNY/(t-Al/year)",
            "source_ids": "aluminium_project_capex",
            "notes": "Project-level capex proxy for electrolytic-aluminium capacity.",
        },
        {
            "parameter": "steel_eaf_electricity_intensity",
            "value": f"{steel_intensity:g}",
            "unit": "kWh/t-steel",
            "source_ids": "steel_capex_intensity",
            "notes": "Representative EAF steel electricity intensity.",
        },
        {
            "parameter": "steel_eaf_new_capacity_capex",
            "value": f"{steel_capex:g}",
            "unit": "CNY/(t-steel/year)",
            "source_ids": "steel_capex_intensity",
            "notes": "143 USD/(t/year) converted at 7.2 CNY/USD.",
        },
        {
            "parameter": "data_center_capex",
            "value": f"{data_center_capex_kw:g}",
            "unit": "CNY/kW IT load",
            "source_ids": "data_center_capex",
            "notes": "10.7 million USD/MW IT load converted at 7.2 CNY/USD.",
        },
        {
            "parameter": "working_capital_lock_years",
            "value": "max(duration_h / 8760, 0.5 / cycles_per_year)",
            "unit": "years",
            "source_ids": "seasonal_working_capital",
            "notes": "Cash locked in inventory follows the seasonal-production WCR area method; the triangular WCR term is half the cycle interval.",
        },
        {
            "parameter": "annual_shifted_hours_per_kw_no_excess",
            "value": "cycles_per_year * duration_h",
            "unit": "kWh shifted per kW flexible capacity per year",
            "source_ids": "seasonal_working_capital",
            "notes": "No-excess-capacity fixed costs are normalized by annual shifted energy, not by cycles alone.",
        },
        {
            "parameter": "aluminium_warehousing_cost",
            "value": f"{ALUMINIUM_WAREHOUSING_CNY_PER_TONNE_MONTH:g}",
            "unit": "CNY/(t-Al month)",
            "source_ids": "aluminium_warehousing_cost",
            "notes": "Conservative physical warehousing upper bound equivalent to 1 USD/t/month for finished aluminium plus alumina, excluding transport and WACC.",
        },
        {
            "parameter": "aluminium_warehousing_months",
            "value": "min(duration_h / 720, 2)",
            "unit": "months",
            "source_ids": "aluminium_warehousing_cost",
            "notes": "Cross-timescale approximation; seasonal is capped at about 2 months, giving about 14 CNY/t-Al or 0.00105 CNY/kWh at 13.3 MWh/t.",
        },
        {
            "parameter": "steel_warehousing_cost",
            "value": f"{STEEL_WAREHOUSING_CNY_PER_TONNE_MONTH:g}",
            "unit": "CNY/(t-steel month)",
            "source_ids": "steel_warehousing_cost",
            "notes": "Conservative physical warehousing estimate for finished steel based on the same inventory-area method, excluding transport and WACC.",
        },
        {
            "parameter": "steel_warehousing_months",
            "value": "min(duration_h / 720, 2)",
            "unit": "months",
            "source_ids": "steel_warehousing_cost",
            "notes": "Cross-timescale approximation; seasonal is capped at about 2 months, giving about 4 CNY/t-steel or 0.00909 CNY/kWh at 440 kWh/t.",
        },
    ]
    return series, assumptions


def write_data_csv(path: Path, series: dict[str, dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "resource",
                "state",
                "time_scale",
                "time_scale_key",
                "duration_h",
                "cycles_per_year",
                "value_or_cost_cny_per_kwh",
                "type",
                "includes_direct_cost",
                "notes",
                "source_ids",
            ]
        )
        for key, spec in series.items():
            for scale, value in zip(SCALES, spec["values"]):
                writer.writerow(
                    [
                        spec["resource"],
                        spec["state"],
                        scale.label.replace("\n", " "),
                        scale.key,
                        f"{scale.duration_h:g}",
                        f"{scale.cycles_per_year:g}",
                        f"{float(value):.6g}",
                        spec["type"],
                        spec["includes_direct_cost"],
                        spec["notes"],
                        spec["source_ids"],
                    ]
                )


def write_assumptions_csv(path: Path, assumptions: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["parameter", "value", "unit", "source_ids", "notes"])
        writer.writeheader()
        writer.writerows(assumptions)


def write_sources_csv(path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["source_id", "source", "url", "notes"])
        writer.writeheader()
        for source_id, spec in SOURCES.items():
            writer.writerow({"source_id": source_id, **spec})


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "figure.dpi": 150,
            "savefig.dpi": 300,
        }
    )


def interpolate_curve(values: list[float], points_per_segment: int = 40) -> tuple[np.ndarray, np.ndarray]:
    x = np.arange(len(values))
    xi = np.linspace(0, len(values) - 1, (len(values) - 1) * points_per_segment + 1)
    yi = np.interp(xi, x, values)
    return xi, yi


def label_positions(series: dict[str, dict[str, object]]) -> dict[str, float]:
    endpoints = {key: float(spec["values"][-1]) for key, spec in series.items()}
    ordered = sorted(endpoints.items(), key=lambda item: item[1])
    positions: dict[str, float] = {}
    previous = 0.0
    for key, value in ordered:
        y = max(value, previous * 1.6 if previous else value)
        positions[key] = y
        previous = y
    return positions


def plot_figure(path_stem: Path, series: dict[str, dict[str, object]]) -> None:
    configure_matplotlib()
    x = np.arange(len(SCALES))
    value = np.asarray(series["system_2050_carbon_neutral"]["values"], dtype=float)
    value_x, value_y = interpolate_curve(value.tolist())

    fig, ax = plt.subplots(figsize=(11.2, 6.4), constrained_layout=False)
    fig.subplots_adjust(left=0.115, right=0.77, bottom=0.18, top=0.94)

    ax.fill_between(value_x, 0.005, value_y, color="#DDEFD8", alpha=0.86, zorder=0)

    ax.text(
        0.08,
        0.92,
        "Potentially economic / competitive",
        transform=ax.transAxes,
        color="#416E43",
        fontsize=12.8,
        fontweight="bold",
        va="top",
    )
    ax.text(
        0.08,
        0.872,
        "cost below system value",
        transform=ax.transAxes,
        color="#416E43",
        fontsize=11.3,
        va="top",
    )

    for key, spec in series.items():
        style = STYLE[key]
        values = np.asarray(spec["values"], dtype=float)
        if spec["type"] == "value":
            xi, yi = interpolate_curve(values.tolist())
            ax.plot(xi, yi, color=style["color"], lw=style["lw"], ls=style["ls"], zorder=7)
        else:
            ax.plot(
                x,
                values,
                color=style["color"],
                lw=style["lw"],
                ls=style["ls"],
                marker=style["marker"],
                ms=6,
                mfc="white",
                mec=style["color"],
                mew=1.5,
                zorder=5 if "no_excess" in key else 6,
            )

    positions = label_positions(series)
    label_x = len(SCALES) - 1 + 0.085
    for key, spec in series.items():
        style = STYLE[key]
        end_y = float(spec["values"][-1])
        ax.annotate(
            style["label"],
            xy=(len(SCALES) - 1, end_y),
            xytext=(label_x, positions[key]),
            ha="left",
            va="center",
            fontsize=10.3,
            fontweight="bold" if spec["type"] == "value" else "normal",
            color=style["color"],
            arrowprops={
                "arrowstyle": "-",
                "color": style["color"],
                "alpha": 0.35,
                "lw": 0.8,
                "shrinkA": 0,
                "shrinkB": 4,
            },
            annotation_clip=False,
        )

    all_values = [float(v) for spec in series.values() for v in spec["values"]]
    ymin = max(0.005, min(all_values) / 1.8)
    ymax = max(all_values) * 1.85
    ax.set_yscale("log")
    ax.set_ylim(ymin, ymax)
    ax.set_xlim(0, len(SCALES) - 1)

    yticks = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
    yticks = [tick for tick in yticks if ymin <= tick <= ymax]
    ax.set_yticks(yticks)
    ax.set_yticklabels([f"{tick:g}" for tick in yticks])
    ax.set_xticks(x)
    ax.set_xticklabels([scale.label for scale in SCALES])

    ax.set_xlabel("Cycle period", fontsize=14, labelpad=14)
    ax.set_ylabel("CNY per kWh shifted or made available", fontsize=14, labelpad=18)

    ax.grid(axis="y", which="major", color="#d7d7d7", lw=0.8)
    ax.grid(axis="x", which="major", color="#eeeeee", lw=0.6)
    ax.tick_params(axis="both", which="major", labelsize=11.2, length=0, pad=8)
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_linewidth(1.2)
        ax.spines[spine].set_color("#222222")

    legend_handles = [
        Line2D([0], [0], color="#555555", lw=2.0, ls=(0, (3, 2.5))),
        Line2D(
            [0],
            [0],
            color="#555555",
            lw=2.0,
            marker="s",
            ms=6,
            mfc="white",
            mec="#555555",
            mew=1.4,
        ),
    ]
    ax.legend(
        legend_handles,
        ["no excess capacity", "sunk excess capacity"],
        loc="lower left",
        bbox_to_anchor=(0.01, 0.015),
        frameon=False,
        ncol=2,
        fontsize=10.2,
        handlelength=2.5,
        columnspacing=1.4,
    )

    ax.text(
        0.0,
        -0.255,
        "Storage includes efficiency losses. Industrial/direct process, communication, transaction, and SLA penalties are excluded unless noted in CSV.",
        transform=ax.transAxes,
        fontsize=9.3,
        color="#555555",
        va="top",
    )

    for suffix in ["png", "pdf", "svg"]:
        fig.savefig(path_stem.with_suffix(f".{suffix}"), bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_OUT_DIR.mkdir(parents=True, exist_ok=True)
    series, assumptions = build_series()
    for output_dir in [DATA_OUT_DIR, OUT_DIR]:
        write_data_csv(output_dir / "figure_1_flexibility_cost_timescales_data.csv", series)
        write_assumptions_csv(output_dir / "figure_1_flexibility_cost_timescales_assumptions.csv", assumptions)
        write_sources_csv(output_dir / "figure_1_flexibility_cost_timescales_sources.csv")
    plot_figure(OUT_DIR / "figure_1_flexibility_cost_timescales", series)
    print(f"Wrote data to {DATA_OUT_DIR} and figures to {OUT_DIR}")


if __name__ == "__main__":
    main()
