# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
This script generates monthly residual load plots for selected provinces in the PyPSA-China model.
Residual load is defined as:
electricity load + electric heating + methanation electricity + net exports + battery/PHS loss
- (solar + wind + hydro generation, excluding curtailment).
"""

from _helpers import configure_logging
import pandas as pd
import pypsa
import matplotlib.pyplot as plt
import os
import logging

logger = logging.getLogger(__name__)

# Publication: sans-serif (Helvetica / Arial), 6 pt, figure width 150 mm
TEXT_PT = 7
FIG_WIDTH_MM = 140
FIG_HEIGHT_MM = 70


def _mm_to_inches(mm: float) -> float:
    return mm / 25.4


def set_plot_style():
    """
    Sets up the plotting style for all matplotlib plots in this script.
    """
    plt.style.use(
        [
            "classic",
            "seaborn-v0_8-whitegrid",
            {
                "axes.grid": False,
                "grid.linestyle": "--",
                "grid.color": "0.6",
                "hatch.color": "white",
                "patch.linewidth": 0.5,
                "lines.linewidth": 1.2,
                "pdf.fonttype": 42,
            },
        ]
    )
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica", "Arial", "Helvetica Neue", "DejaVu Sans"],
            "font.size": TEXT_PT,
            "axes.labelsize": TEXT_PT,
            "axes.titlesize": TEXT_PT,
            "xtick.labelsize": TEXT_PT,
            "ytick.labelsize": TEXT_PT,
            "legend.fontsize": TEXT_PT,
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
        }
    )

def filter_network_by_province(n, target_province=None):
    """
    Filter the network to include only components from a specific province.
    """
    if target_province is None:
        return n

    logger.info(f"Filtering network for province: {target_province}")

    n_filtered = n.copy()
    province_buses = n_filtered.buses[
        n_filtered.buses.index.str.contains(target_province, case=False)
    ].index

    if len(province_buses) == 0:
        logger.warning(f"No buses found for province {target_province}")
        return n_filtered

    non_province_generators = n_filtered.generators[
        ~n_filtered.generators.bus.isin(province_buses)
    ].index
    if len(non_province_generators) > 0:
        n_filtered.mremove("Generator", non_province_generators)

    non_province_loads = n_filtered.loads[
        ~n_filtered.loads.bus.isin(province_buses)
    ].index
    if len(non_province_loads) > 0:
        n_filtered.mremove("Load", non_province_loads)

    non_province_storage = n_filtered.storage_units[
        ~n_filtered.storage_units.bus.isin(province_buses)
    ].index
    if len(non_province_storage) > 0:
        n_filtered.mremove("StorageUnit", non_province_storage)

    non_province_stores = n_filtered.stores[
        ~n_filtered.stores.bus.isin(province_buses)
    ].index
    if len(non_province_stores) > 0:
        n_filtered.mremove("Store", non_province_stores)

    non_province_links = n_filtered.links[
        ~(
            n_filtered.links.bus0.isin(province_buses)
            | n_filtered.links.bus1.isin(province_buses)
        )
    ].index
    if len(non_province_links) > 0:
        n_filtered.mremove("Link", non_province_links)

    non_province_lines = n_filtered.lines[
        ~(
            n_filtered.lines.bus0.isin(province_buses)
            | n_filtered.lines.bus1.isin(province_buses)
        )
    ].index
    if len(non_province_lines) > 0:
        n_filtered.mremove("Line", non_province_lines)

    non_province_buses = n_filtered.buses[
        ~n_filtered.buses.index.isin(province_buses)
    ].index
    if len(non_province_buses) > 0:
        n_filtered.mremove("Bus", non_province_buses)

    return n_filtered

def _match_by_carrier(df, carriers):
    matches = []
    for carrier in carriers:
        exact = df[df.carrier == carrier].index.tolist()
        matches.extend(exact)
        partial = df[df.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
        matches.extend([m for m in partial if m not in matches])
    return matches

def _match_by_carrier_exact(df, carriers):
    return df.index[df.carrier.isin(carriers)].tolist()

def _monthly_energy(series, weights):
    energy = series.multiply(weights, axis=0).sum(axis=1)
    return energy.groupby(energy.index.month).sum() / 1e6

def _link_consumption_monthly(p0_df, links, weights):
    if not links:
        return pd.Series(0.0, index=range(1, 13))
    # For electricity-consuming links on bus0, p0 is typically positive (withdrawal)
    consumption = p0_df[links].clip(lower=0)
    return _monthly_energy(consumption, weights)

def calculate_monthly_net_load(n, target_province):
    """
    Calculate monthly residual load as:
    electricity load + electric heating + methanation electricity + net exports + battery/PHS loss
    - (solar + wind + hydro generation, excluding curtailment).
    """
    weights = n.snapshot_weightings.generators
    if target_province is None:
        province_buses = n.buses.index
        province_label = "National"
    else:
        province_buses = n.buses.index[n.buses.index.str.contains(target_province, case=False)]
        province_label = target_province

    if len(province_buses) == 0:
        logger.warning(f"No buses found for province {province_label}")

    # Link power time series (used for several components)
    link_p0 = n.links_t.p0 if hasattr(n, "links_t") and hasattr(n.links_t, "p0") else None

    # Electricity load (exclude heat/aluminum)
    elec_loads = pd.DataFrame()
    if hasattr(n, "loads_t") and hasattr(n.loads_t, "p_set"):
        province_loads = n.loads.index[n.loads.bus.isin(province_buses)]
        province_loads = province_loads.intersection(n.loads_t.p_set.columns)
        elec_loads = n.loads_t.p_set[province_loads].filter(
            regex='^(?!.*(heat|aluminum)).*$', axis=1
        )
    if elec_loads.empty:
        logger.warning("No electricity loads found.")
        monthly_elec_load = pd.Series(0.0, index=range(1, 13))
    else:
        monthly_elec_load = _monthly_energy(elec_loads, weights)

    # AC-side H2 links electricity consumption (e.g., AC-H2)
    h2_monthly = pd.Series(0.0, index=range(1, 13))
    if link_p0 is not None and hasattr(n, "links"):
        ac_buses = n.buses.index[n.buses.carrier.str.contains("AC", case=False, na=False)]
        province_ac_buses = ac_buses.intersection(province_buses)
        h2_links = _match_by_carrier(n.links, ["H2", "hydrogen"])
        h2_links = [l for l in h2_links if n.links.at[l, "bus0"] in province_ac_buses]
        h2_links = [l for l in h2_links if l in link_p0.columns]
        if h2_links:
            h2_monthly = _link_consumption_monthly(link_p0, h2_links, weights)

    # Link electricity consumption for ground heat pump, methanation, resistive heater (use p0 on AC bus)
    link_p0 = n.links_t.p0 if hasattr(n, "links_t") and hasattr(n.links_t, "p0") else None
    if link_p0 is None or n.links.empty:
        ghp_monthly = pd.Series(0.0, index=range(1, 13))
        meth_monthly = pd.Series(0.0, index=range(1, 13))
        rh_monthly = pd.Series(0.0, index=range(1, 13))
    else:
        ac_buses = n.buses.index[n.buses.carrier.str.contains("AC", case=False, na=False)]
        province_ac_buses = ac_buses.intersection(province_buses)
        ac_links = n.links.index[n.links.bus0.isin(province_ac_buses)]

        ghp_links = _match_by_carrier(n.links, ["ground heat pump", "heat pump"])
        meth_links = _match_by_carrier(n.links, ["methanation", "Sabatier"])
        rh_links = _match_by_carrier(n.links, ["resistive heater"])

        ghp_links = [l for l in ghp_links if l in ac_links]
        meth_links = [l for l in meth_links if l in ac_links]
        rh_links = [l for l in rh_links if l in ac_links]

        ghp_links = [l for l in ghp_links if l in link_p0.columns]
        meth_links = [l for l in meth_links if l in link_p0.columns]
        rh_links = [l for l in rh_links if l in link_p0.columns]

        if ghp_links:
            ghp_monthly = _link_consumption_monthly(link_p0, ghp_links, weights)
        else:
            logger.warning("No ground heat pump links found.")
            ghp_monthly = pd.Series(0.0, index=range(1, 13))

        if meth_links:
            meth_monthly = _link_consumption_monthly(link_p0, meth_links, weights)
        else:
            logger.warning("No methanation links found.")
            meth_monthly = pd.Series(0.0, index=range(1, 13))

        if rh_links:
            rh_monthly = _link_consumption_monthly(link_p0, rh_links, weights)
        else:
            logger.warning("No resistive heater links found.")
            rh_monthly = pd.Series(0.0, index=range(1, 13))

    # Renewable generation (solar, wind, hydro) from generators and links
    gen_p = n.generators_t.p if hasattr(n, "generators_t") and hasattr(n.generators_t, "p") else None
    link_p1 = n.links_t.p1 if hasattr(n, "links_t") and hasattr(n.links_t, "p1") else None

    wind_carriers = ["wind", "onwind", "offwind"]
    solar_carriers = ["solar"]
    hydro_carriers = ["hydro", "ror", "hydroelectricity"]
    nuclear_carriers = ["nuclear"]
    wind_monthly = pd.Series(0.0, index=range(1, 13))
    solar_monthly = pd.Series(0.0, index=range(1, 13))
    hydro_monthly = pd.Series(0.0, index=range(1, 13))
    nuclear_monthly = pd.Series(0.0, index=range(1, 13))

    if gen_p is not None and hasattr(n, "generators"):
        wind_gens = _match_by_carrier_exact(n.generators, wind_carriers)
        solar_gens = _match_by_carrier_exact(n.generators, solar_carriers)
        hydro_gens = _match_by_carrier_exact(n.generators, hydro_carriers)
        nuclear_gens = _match_by_carrier_exact(n.generators, nuclear_carriers)

        wind_gens = [g for g in wind_gens if n.generators.at[g, "bus"] in province_buses]
        solar_gens = [g for g in solar_gens if n.generators.at[g, "bus"] in province_buses]
        hydro_gens = [g for g in hydro_gens if n.generators.at[g, "bus"] in province_buses]
        nuclear_gens = [g for g in nuclear_gens if n.generators.at[g, "bus"] in province_buses]

        wind_gens = [g for g in wind_gens if g in gen_p.columns]
        solar_gens = [g for g in solar_gens if g in gen_p.columns]
        hydro_gens = [g for g in hydro_gens if g in gen_p.columns]
        nuclear_gens = [g for g in nuclear_gens if g in gen_p.columns]

        if wind_gens:
            wind_monthly += _monthly_energy(gen_p[wind_gens], weights)
        if solar_gens:
            solar_monthly += _monthly_energy(gen_p[solar_gens], weights)
        if hydro_gens:
            hydro_monthly += _monthly_energy(gen_p[hydro_gens], weights)
        if nuclear_gens:
            nuclear_monthly += _monthly_energy(gen_p[nuclear_gens], weights)

        if not (wind_gens or solar_gens or hydro_gens or nuclear_gens):
            logger.warning("No renewable generators found (solar/wind/hydro).")

    # Note: renewable generation is taken from generators only to avoid
    # double-counting offshore wind converter links (e.g. offwind-ac/offwind-dc).

    # Net exports are excluded from both display and residual load calculation

    # Battery and PHS energy loss within province
    battery_loss_monthly = pd.Series(0.0, index=range(1, 13))
    phs_loss_monthly = pd.Series(0.0, index=range(1, 13))
    battery_loss_ts = pd.Series(0.0, index=n.snapshots)
    phs_loss_ts = pd.Series(0.0, index=n.snapshots)

    if hasattr(n, "storage_units_t") and hasattr(n.storage_units_t, "p"):
        if "carrier" in n.storage_units.columns:
            logger.info(
                "Storage unit carriers (sample): %s",
                ", ".join(sorted(n.storage_units.carrier.unique())[:10])
            )
        phs_su = n.storage_units.index[
            n.storage_units.carrier.str.contains("PHS", case=False, na=False)
            & n.storage_units.bus.isin(province_buses)
        ].intersection(n.storage_units_t.p.columns)

        logger.info("PHS storage units matched in %s: %s", province_label, len(phs_su))

        if len(phs_su) > 0:
            p_storage = n.storage_units_t.p[phs_su]
            charge = (-p_storage).clip(lower=0)
            discharge = p_storage.clip(lower=0)
            eff_store = n.storage_units.loc[phs_su, "efficiency_store"].fillna(1.0)
            eff_dispatch = n.storage_units.loc[phs_su, "efficiency_dispatch"].fillna(1.0)

            loss = charge.mul(1 - eff_store, axis=1) + discharge.mul(
                (1 / eff_dispatch) - 1, axis=1
            )
            phs_loss_ts += loss.clip(lower=0).sum(axis=1)

    if hasattr(n, "stores_t") and hasattr(n.stores_t, "p"):
        if "carrier" in n.stores.columns:
            logger.info(
                "Store carriers (sample): %s",
                ", ".join(sorted(n.stores.carrier.unique())[:10])
            )
        phs_stores = n.stores.index[
            n.stores.carrier.str.contains("PHS", case=False, na=False)
            & n.stores.bus.isin(province_buses)
        ].intersection(n.stores_t.p.columns)

        logger.info("PHS stores matched in %s: %s", province_label, len(phs_stores))

        if len(phs_stores) > 0:
            p_store = n.stores_t.p[phs_stores]
            charge = (-p_store).clip(lower=0)
            discharge = p_store.clip(lower=0)

            loss = pd.DataFrame(0.0, index=p_store.index, columns=phs_stores)
            if "efficiency_store" in n.stores.columns and "efficiency_dispatch" in n.stores.columns:
                eff_store = n.stores.loc[phs_stores, "efficiency_store"].fillna(1.0)
                eff_dispatch = n.stores.loc[phs_stores, "efficiency_dispatch"].fillna(1.0)
                loss += charge.mul(1 - eff_store, axis=1) + discharge.mul(
                    (1 / eff_dispatch) - 1, axis=1
                )

            if hasattr(n.stores_t, "e") and "standing_loss" in n.stores.columns:
                e_store = n.stores_t.e[phs_stores]
                standing_loss = n.stores.loc[phs_stores, "standing_loss"].fillna(0.0)
                loss += e_store.mul(standing_loss, axis=1)

            phs_loss_ts += loss.clip(lower=0).sum(axis=1)

    # Link losses for battery (e.g. AC-battery, battery-AC)
    if hasattr(n, "links_t") and hasattr(n.links_t, "p0") and hasattr(n.links_t, "p1"):
        battery_buses = n.buses.index[n.buses.carrier.str.contains("battery", case=False, na=False)]
        if len(battery_buses) == 0:
            battery_buses = n.buses.index[n.buses.index.str.contains(" battery", case=False, na=False)]
        ac_buses = n.buses.index[n.buses.carrier.str.contains("AC", case=False, na=False)]

        link_units = n.links.index[
            (
                (n.links.bus0.isin(ac_buses) & n.links.bus1.isin(battery_buses))
                | (n.links.bus1.isin(ac_buses) & n.links.bus0.isin(battery_buses))
            )
            & (n.links.bus0.isin(province_buses) | n.links.bus1.isin(province_buses))
        ]
        link_units = link_units.intersection(n.links_t.p0.columns).intersection(n.links_t.p1.columns)
        logger.info("Battery links matched in %s: %s", province_label, len(link_units))
        if len(link_units) > 0:
            link_p0 = n.links_t.p0[link_units]
            link_p1 = n.links_t.p1[link_units]
            link_loss = link_p0 + link_p1
            battery_loss_ts = link_loss.sum(axis=1)

    if battery_loss_ts.abs().sum() > 0:
        battery_loss_monthly = _monthly_energy(battery_loss_ts.to_frame("battery_loss"), weights)
    if phs_loss_ts.abs().sum() > 0:
        phs_loss_monthly = _monthly_energy(phs_loss_ts.to_frame("phs_loss"), weights)

    electric_heating = ghp_monthly + rh_monthly
    data = pd.DataFrame({
        "Other Electricity Load": monthly_elec_load,
        "Electric Heating": electric_heating,
        "Methanation Electricity": meth_monthly,
        "Battery Loss": battery_loss_monthly,
        "PHS Loss": phs_loss_monthly,
        "Wind Generation": wind_monthly,
        "Solar Generation": solar_monthly,
        "Hydro Generation": hydro_monthly,
    }).reindex(range(1, 13), fill_value=0.0)

    data["Net Load"] = (
        data["Other Electricity Load"]
        + data["Electric Heating"]
        + data["Methanation Electricity"]
        + data["Battery Loss"]
        + data["PHS Loss"]
        - data["Wind Generation"]
        - data["Solar Generation"]
        - data["Hydro Generation"]
    )
    return data

def plot_monthly_net_demand(monthly_data, scenario, planning_horizon, target_province):
    """
    Plot the monthly residual load curve.
    """
    fig, ax = plt.subplots(
        figsize=(_mm_to_inches(FIG_WIDTH_MM), _mm_to_inches(FIG_HEIGHT_MM)),
    )

    colors = {
        "Other Electricity Load": "#1f77b4",
        "Electric Heating": "#ff7f0e",
        "Wind Generation": "#00FF00",
        "Solar Generation": "#FFD700",
        "Hydro Generation": "#0000FF",
        "Net Load": "blue",
    }

    # Display labels: only first word capitalized
    label_map = {
        "Other Electricity Load": "Other electricity load",
        "Electric Heating": "Electric heating",
        "Wind Generation": "Wind generation",
        "Solar Generation": "Solar generation",
        "Hydro Generation": "Hydro generation",
        "Net Load": "Residual demand",
    }

    # Plot components as stacked areas (positive)
    positive_components = [
        "Other Electricity Load",
        "Electric Heating",
    ]
    ax.stackplot(
        monthly_data.index,
        [monthly_data[label].values for label in positive_components],
        labels=[label_map[label] for label in positive_components],
        colors=[colors.get(label, "#808080") for label in positive_components],
        alpha=0.6,
    )

    # Plot renewable generation as negative area
    renewables = ["Solar Generation", "Hydro Generation", "Wind Generation"]
    ax.stackplot(
        monthly_data.index,
        [-monthly_data[label].values for label in renewables],
        labels=[label_map[label] for label in renewables],
        colors=[colors.get(label, "#17becf") for label in renewables],
        alpha=0.5,
    )

    # Plot residual load
    net_load = monthly_data["Net Load"]
    ax.plot(
        monthly_data.index,
        net_load,
        's--',
        color=colors["Net Load"],
        label=label_map["Net Load"],
        markersize=3.5,
        linewidth=1.2,
    )

    ax.set_xlabel("Month", fontsize=TEXT_PT)
    ax.set_ylabel("Demand / generation (TWh)", fontsize=TEXT_PT)
    title_province = target_province or "National"
    # For the national case, omit the figure title; keep it for provincial plots.
    if title_province != "National":
        ax.set_title(
            f"Monthly residual demand and components - {planning_horizon} ({title_province})",
            fontsize=TEXT_PT,
        )

    ax.set_xlim(1.0, 12.0)
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        fontsize=TEXT_PT,
    )
    ax.tick_params(axis="y", labelsize=TEXT_PT)
    ax.grid(True, alpha=0.3)
    
    # Legend: show heat load (Electric heating) first, then electricity (Other electricity load)
    handles, labels = ax.get_legend_handles_labels()
    heat_label = label_map["Electric Heating"]
    elec_label = label_map["Other Electricity Load"]
    if heat_label in labels and elec_label in labels:
        idx_heat = labels.index(heat_label)
        idx_elec = labels.index(elec_label)
        handles = [handles[idx_heat], handles[idx_elec]] + [h for i, h in enumerate(handles) if i not in (idx_heat, idx_elec)]
        labels = [heat_label, elec_label] + [l for i, l in enumerate(labels) if i not in (idx_heat, idx_elec)]
    ncol_legend = max(1, (len(labels) + 1) // 2)
    ax.legend(
        handles=handles,
        labels=labels,
        fontsize=TEXT_PT,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),
        ncol=ncol_legend,
        frameon=False,
    )

    plt.tight_layout()
    # Reserve space for bottom legend; do not use right>1 (breaks PDF bbox width)
    plt.subplots_adjust(bottom=0.28)

    output_dir = "results/monthly_net_demand"
    os.makedirs(output_dir, exist_ok=True)
    safe_province = (target_province or "National").replace(" ", "")
    plot_filename = f"{output_dir}/net_demand_{planning_horizon}_{safe_province}.pdf"
    fig.savefig(plot_filename, format="pdf", bbox_inches="tight", facecolor="white")
    plt.close()
    
    # Save data to CSV
    csv_filename = f"{output_dir}/net_demand_{planning_horizon}_{safe_province}.csv"
    monthly_data.to_csv(csv_filename)
    
    print(f"Plot saved to {plot_filename}")
    print(f"Data saved to {csv_filename}")

if __name__ == "__main__":
    # Default scenario if not run via snakemake
    scenario = "version-0120.1H.1-MMMU-2050-15p"
    planning_horizon = "2050"
    network_path = f"results/{scenario}/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2050.nc"
    # scenario = "version-0120.1H.1-MMMF-2050-15p"
    # planning_horizon = "2050"
    # network_path = f"results/{scenario}/postnetworks/positive/postnetwork-ll-current+neighbor-linear2050-2050.nc"
    
    if 'snakemake' not in globals():
        from types import SimpleNamespace
        class MockSnakemake:
            def __init__(self):
                self.input = SimpleNamespace(network=network_path)
                self.wildcards = SimpleNamespace(planning_horizons=planning_horizon)
                self.config = {}
                self.rule = 'plot_monthly_net_demand'
                self.log = self.MockLog()
            
            class MockLog:
                def __init__(self): self.python = "logs/plot_monthly_net_demand.log"
                def get(self, key, default): return getattr(self, key, default)
                def __getitem__(self, key): return self.python if key == 0 or key == 'python' else None
                def __iter__(self): return iter([self.python])
                def __len__(self): return 1
                def __bool__(self): return True
        
        snakemake = MockSnakemake()
    else:
        planning_horizon = snakemake.wildcards.planning_horizons
        network_path = snakemake.input.network
    
    configure_logging(snakemake)
    set_plot_style()
    
    if not os.path.exists(network_path):
        print(f"Error: Network file not found at {network_path}")
        exit(1)
        
    print(f"Loading network from: {network_path}")
    n = pypsa.Network(network_path)
    
    # Typical provinces for regional analysis
    provinces = ["National", "Xinjiang", "Yunnan", "InnerMongolia", "Guangxi", "Shandong"]

    for province in provinces:
        print(f"Processing province: {province}")

        print("Calculating monthly residual load...")
        target_province = None if province == "National" else province
        monthly_data = calculate_monthly_net_load(n, target_province)

        print("Generating plot...")
        plot_monthly_net_demand(monthly_data, scenario, planning_horizon, province)
    
    print("Done.")
