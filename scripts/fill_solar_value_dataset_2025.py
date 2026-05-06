#!/usr/bin/env python3
"""
Fill 2025 rows in solar_value_dataset.xlsx from dispatch results.

Inputs:
- dispatch segmented network (.nc): solar generation/demand/capacity side metrics
- mapped dispatch prices CSV: nodal price time series for value-factor metrics

Writes only rows 2-33 (first province block) in the workbook.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

import pandas as pd
import pypsa
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
VERSION_DIR = ROOT / "results" / "version-0505.1H.2"
NETWORK_PATH = (
    VERSION_DIR
    / "dispatch_segmented"
    / "positive"
    / "postnetwork-dispatch-seg-ll-current+FCG-linear2050-2025.nc"
)
PRICE_CSV_PATH = (
    VERSION_DIR
    / "prices"
    / "dispatch_segmented"
    / "positive"
    / "dispatch_segmented_prices-ll-current+FCG-linear2050-2025_mapped.csv"
)
XLSX_PATH = VERSION_DIR / "solar_value_dataset.xlsx"
BACKUP_PATH = VERSION_DIR / "solar_value_dataset.2025-backup.xlsx"
REAL_SOLAR_CAP_PATH = ROOT / "data" / "existing_infrastructure" / "solar capacity.csv"
CAP_COMPARE_PATH = VERSION_DIR / "solar_capacity_compare_2025.csv"

# Workbook row block for 2025
ROW_START = 2
ROW_END = 33

# Workbook province -> source province names
PROVINCE_MAP = {
    "Xizang": "Tibet",
    "WestInnerMongolia": "InnerMongolia",
    "EastInnerMongolia": "InnerMongolia",
}


def _mapped_name(province: str) -> str:
    return PROVINCE_MAP.get(province, province)


def _group_sum_by_bus(frame: pd.DataFrame, bus_of_component: pd.Series) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(index=frame.index)
    return frame.T.groupby(bus_of_component).sum().T


def _load_real_solar_capacity_2025() -> pd.Series:
    cap = pd.read_csv(REAL_SOLAR_CAP_PATH)
    required_cols = ["Region", "2010", "2015", "2020", "2025"]
    if any(c not in cap.columns for c in required_cols):
        raise ValueError(
            "Expected columns 'Region', '2010', '2015', '2020', '2025' in solar capacity.csv"
        )
    real_cap = cap.set_index("Region")[["2010", "2015", "2020", "2025"]].astype(float).sum(axis=1)
    return real_cap


def _compute_metrics() -> pd.DataFrame:
    n = pypsa.Network(NETWORK_PATH)

    prices = pd.read_csv(PRICE_CSV_PATH)
    prices = prices.rename(columns={"snapshot": "time"})
    prices["time"] = pd.to_datetime(prices["time"])
    prices = prices.set_index("time").sort_index()

    snapshots = pd.DatetimeIndex(n.snapshots)
    prices = prices.reindex(snapshots)
    if prices.isna().any().any():
        raise ValueError("Mapped price CSV and network snapshots do not align exactly.")

    # Select solar generators (include all carriers containing 'solar').
    solar_mask = n.generators.carrier.astype(str).str.contains("solar", case=False, regex=False)
    solar_gens = n.generators.index[solar_mask]

    if len(solar_gens) == 0:
        raise ValueError("No solar generators found in network.")

    solar_dispatch = n.generators_t.p[solar_gens].clip(lower=0.0)
    solar_bus = n.generators.loc[solar_gens, "bus"]
    solar_dispatch_bus = _group_sum_by_bus(solar_dispatch, solar_bus)

    avail = n.generators_t.p_max_pu[solar_gens].multiply(
        n.generators.loc[solar_gens, "p_nom_opt"], axis=1
    )
    solar_available_bus = _group_sum_by_bus(avail, solar_bus)

    solar_capacity_bus = n.generators.loc[solar_gens].groupby("bus")["p_nom_opt"].sum()
    real_solar_capacity_bus = _load_real_solar_capacity_2025()

    ac_buses = n.buses.index[n.buses.carrier.astype(str) == "AC"]

    # System total generation by AC bus/hour:
    # use all generators connected to AC buses, clipped at >=0 to represent generation output.
    all_ac_gen_mask = n.generators.bus.isin(ac_buses)
    all_ac_gens = n.generators.index[all_ac_gen_mask]
    all_gen_dispatch = n.generators_t.p[all_ac_gens].clip(lower=0.0)
    all_gen_bus = n.generators.loc[all_ac_gens, "bus"]
    total_gen_bus = _group_sum_by_bus(all_gen_dispatch, all_gen_bus)

    # Standard value-factor denominator:
    # system weighted-average market value = sum(total_gen * nodal_price) / sum(total_gen)
    system_value_num = 0.0
    system_gen_sum = 0.0
    common_system_buses = [b for b in prices.columns if b in total_gen_bus.columns]
    for b in common_system_buses:
        g = total_gen_bus[b]
        p = prices[b].astype(float)
        system_value_num += float((g * p).sum())
        system_gen_sum += float(g.sum())
    system_avg_market_value = (system_value_num / system_gen_sum) if system_gen_sum > 0 else 0.0

    # Provincial demand on AC buses.
    load_values = n.loads_t.p_set if hasattr(n.loads_t, "p_set") else n.loads_t.p
    load_mask = n.loads.bus.isin(ac_buses)
    load_cols = n.loads.index[load_mask]
    loads = load_values[load_cols].clip(lower=0.0)
    load_bus = n.loads.loc[load_cols, "bus"]
    load_bus_ts = _group_sum_by_bus(loads, load_bus)

    provinces = sorted(set(ac_buses).union(prices.columns))
    weight_sum = float(n.snapshot_weightings.generators.sum())

    cap_compare_rows: list[dict[str, float | str]] = []
    rows: list[dict[str, float | str]] = []
    for province in provinces:
        dispatch_s = solar_dispatch_bus.get(province, pd.Series(0.0, index=snapshots))
        available_s = solar_available_bus.get(province, pd.Series(0.0, index=snapshots))
        total_gen_s = total_gen_bus.get(province, pd.Series(0.0, index=snapshots))
        load_s = load_bus_ts.get(province, pd.Series(0.0, index=snapshots))
        price_s = prices.get(province, pd.Series(0.0, index=snapshots)).astype(float)

        solar_mwh = float(dispatch_s.sum())
        solar_gwh = solar_mwh / 1000.0
        demand_mwh = float(load_s.sum())
        total_gen_mwh = float(total_gen_s.sum())
        available_mwh = float(available_s.sum())
        nc_cap_mw = float(solar_capacity_bus.get(province, 0.0))
        real_cap_mw = float(real_solar_capacity_bus.get(province, nc_cap_mw))
        cap_ratio = (real_cap_mw / nc_cap_mw) if nc_cap_mw > 0 else 0.0
        solar_mwh_for_penetration = solar_mwh * cap_ratio

        pv_value_num = float((dispatch_s * price_s).sum())
        pv_avg_market_value = (pv_value_num / solar_mwh) if solar_mwh > 0 else 0.0
        value_num = pv_avg_market_value
        value_den = system_avg_market_value
        value_factor = (value_num / value_den) if value_den > 0 else 0.0
        # Conservative provincial penetration: take the lower of
        # load-based and generation-based shares.
        penetration_load = (solar_mwh_for_penetration / demand_mwh) if demand_mwh > 0 else 0.0
        penetration_gen = (solar_mwh_for_penetration / total_gen_mwh) if total_gen_mwh > 0 else 0.0
        penetration = min(penetration_load, penetration_gen)
        curtailment = ((available_mwh - solar_mwh) / available_mwh) if available_mwh > 0 else 0.0
        cap_factor = (solar_mwh / (nc_cap_mw * weight_sum)) if nc_cap_mw > 0 and weight_sum > 0 else 0.0

        cap_compare_rows.append(
            {
                "province": province,
                "nc_solar_capacity_mw": nc_cap_mw,
                "real_solar_capacity_mw": real_cap_mw,
                "real_to_nc_ratio": cap_ratio,
            }
        )

        rows.append(
            {
                "province": province,
                "solar_ele_GWh": solar_gwh,
                "value_factor_numerator": value_num,
                "value_factor_denominator": value_den,
                "value_factor": value_factor,
                "solar_penetration": penetration,
                "solar_curtailment_rate": curtailment,
                "solar_capacity_factor": cap_factor,
            }
        )

    pd.DataFrame(cap_compare_rows).set_index("province").to_csv(CAP_COMPARE_PATH)
    return pd.DataFrame(rows).set_index("province")


def _write_workbook(metrics: pd.DataFrame) -> None:
    copy2(XLSX_PATH, BACKUP_PATH)

    wb = load_workbook(XLSX_PATH)
    ws = wb["Sheet1"]

    for row in range(ROW_START, ROW_END + 1):
        zone = ws.cell(row=row, column=1).value
        if not zone:
            continue
        zone = str(zone)
        source_zone = _mapped_name(zone)
        if source_zone not in metrics.index:
            raise KeyError(f"Province not found in computed metrics: {zone} -> {source_zone}")

        m = metrics.loc[source_zone]
        ws.cell(row=row, column=2, value=2025)
        ws.cell(row=row, column=3, value=float(m["solar_ele_GWh"]))
        ws.cell(row=row, column=4, value=float(m["value_factor_numerator"]))
        ws.cell(row=row, column=5, value=float(m["value_factor_denominator"]))
        ws.cell(row=row, column=6, value=float(m["value_factor"]))
        ws.cell(row=row, column=7, value=float(m["solar_penetration"]))
        ws.cell(row=row, column=8, value=float(m["solar_curtailment_rate"]))
        ws.cell(row=row, column=9, value=float(m["solar_capacity_factor"]))

    wb.save(XLSX_PATH)


def main() -> None:
    metrics = _compute_metrics()
    _write_workbook(metrics)
    print(f"Filled 2025 rows in: {XLSX_PATH}")
    print(f"Backup created: {BACKUP_PATH}")


if __name__ == "__main__":
    main()
