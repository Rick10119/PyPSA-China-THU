# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Heatmap comparison script for MMMU and NMMU scenarios.

This script is a refactored version of plot_heatmap.py to compare
two different configuration scenarios for storage technologies and
aluminum smelter operation.

Main features:
1. Read parameters from MMMU and NMMU config files
2. Load corresponding network results
3. Generate side-by-side comparison heatmaps
4. Support visualization of H2, battery, water storage and aluminum smelters

Scenario differences:
- MMMU: iterative_optimization: true, smelter_flexibility: mid, employment_transfer: unfavorable (U)
- NMMU: iterative_optimization: false, smelter_flexibility: non_constrained, employment_transfer: unfavorable (U)
"""

import yaml
import seaborn as sns
import pandas as pd
import pypsa
import matplotlib.pyplot as plt
import os
import argparse
from pathlib import Path

# Publication: sans-serif (Helvetica / Arial), 6 pt, figure 150×70 mm, PDF output
TEXT_PT = 6
DATE_TICK_PT = 5  # x-axis date labels on heatmaps
FIG_WIDTH_MM = 150
FIG_HEIGHT_MM = 90


def _mm_to_inches(mm: float) -> float:
    return mm / 25.4


def _reduce_heatmap_day_ticks(ax, columns, *, step: int = 15):
    """
    Show x-axis day labels every `step` columns (~half month when step=15).

    Seaborn heatmap places column centers at 0.5, 1.5, ...
    """
    n = len(columns)
    if n == 0:
        return
    cols = list(columns)
    tick_indices = list(range(0, n, step))
    if n > 1 and tick_indices[-1] != n - 1:
        tick_indices.append(n - 1)
    # Ensure 12-27 is listed if that day exists in columns
    for i, c in enumerate(cols):
        if str(c) == "12-27":
            tick_indices.append(i)
            break
    tick_indices = sorted(set(tick_indices))
    # If 12-27 and 12-31 are both ticks a few days apart, matplotlib often drops one label;
    # drop 12-31 so 12-27 stays visible.
    idx_1227 = next((i for i, c in enumerate(cols) if str(c) == "12-27"), None)
    idx_1231 = next((i for i, c in enumerate(cols) if str(c) == "12-31"), None)
    if (
        idx_1227 is not None
        and idx_1231 is not None
        and idx_1227 in tick_indices
        and idx_1231 in tick_indices
        and (idx_1231 - idx_1227) <= 10
    ):
        tick_indices.remove(idx_1231)
    positions = [i + 0.5 for i in tick_indices]
    labels = [str(cols[i]) for i in tick_indices]
    ax.set_xticks(positions)
    ax.set_xticklabels(
        labels,
        rotation=90,
        ha="center",
        va="top",
        fontsize=DATE_TICK_PT,
    )
    # Move labels up by ~one digit height (negative pad pulls tick text toward the axes)
    ax.tick_params(axis="x", which="major", pad=-0.00)


def _style_heatmap_colorbar(ax):
    """Apply TEXT_PT to colorbar ticks and label (no position — layout would overwrite)."""
    try:
        if not ax.collections:
            return
        cb = ax.collections[0].colorbar
        if cb is None:
            return
        cb.ax.tick_params(axis="y", labelsize=TEXT_PT)
        lab = cb.ax.yaxis.label.get_text() or "pu"
        cb.set_label(lab, fontsize=TEXT_PT)
    except (AttributeError, IndexError):
        return


def _shift_heatmap_colorbar_right(ax, dx: float = 0.035) -> None:
    """
    Move the entire colorbar axes (bar + ticks + label) right in figure coordinates.

    Must run *after* plt.tight_layout / subplots_adjust, or the shift is reset.
    """
    try:
        if not ax.collections:
            return
        cb = ax.collections[0].colorbar
        if cb is None:
            return
        pos = cb.ax.get_position()
        cb.ax.set_position([pos.x0 + dx, pos.y0, pos.width, pos.height])
    except (AttributeError, IndexError):
        return


def set_plot_style():
    """
    Set global plotting style.
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
                "lines.linewidth": 1.0,
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

def create_df(n, tech, province_filter=None):
    """
    Create a heatmap-ready dataframe for a given storage technology.
    
    Parameters:
    -----------
    n : pypsa.Network
        PyPSA Network with simulation results
    tech : str
        Storage technology to analyse ('H2', 'battery', or 'water')
    province_filter : str, optional
        If given, only include storage assets in the selected province
    
    Returns:
    --------
    tuple
        (pivoted dataframe for heatmap, base power in MW)
    """
    # Select storage assets for the given technology
    stores = n.stores_t.p.filter(like=tech)
    
    # Apply province filter if given
    if province_filter:
        province_stores = stores.columns[stores.columns.str.contains(province_filter, case=False)]
        if len(province_stores) > 0:
            stores = stores[province_stores]
        else:
            print(f"Warning: No {tech} storage found in province {province_filter}")
            return pd.DataFrame(), 0
    
    # Compute maximum power as base for normalization
    base = abs(stores.sum(axis=1)).max()
    if base == 0:
        print(f"Warning: {tech} power is zero {'in province ' + province_filter if province_filter else 'nationwide'}")
        return pd.DataFrame(), 0
    
    # Normalize by base
    df = stores.sum(axis=1) / base
    df = df.to_frame()
    df.reset_index(inplace=True)
    renames = {0: 'p_store'}
    df.rename(columns=renames, inplace=True)
    
    # Timestamps are already in China local time
    date = n.stores_t.p.filter(like='water').index
    df['Hour'] = date.hour
    df['Day'] = date.strftime('%m-%d')
    
    # Build pivot table for heatmap
    summary = pd.pivot_table(data=df, index='Hour', columns='Day', values='p_store')
    summary = summary.fillna(0).infer_objects(copy=False)
    return summary, base

def create_aluminum_df(n, province_filter=None):
    """
    Create a heatmap-ready dataframe for aluminum smelter operation.
    
    Parameters:
    -----------
    n : pypsa.Network
        PyPSA Network with simulation results
    province_filter : str, optional
        If given, only include aluminum smelters in the selected province
    
    Returns:
    --------
    tuple
        (pivoted dataframe for heatmap, base power in MW)
    """
    # Aluminum smelter links (power from the electricity bus)
    aluminum_links = n.links_t.p0.filter(like='aluminum smelter')
    
    if aluminum_links.empty:
        print("Warning: No aluminum smelter links found in network")
        return pd.DataFrame(), 0
    
    # Apply province filter if given
    if province_filter:
        province_smelters = aluminum_links.columns[aluminum_links.columns.str.contains(province_filter, case=False)]
        if len(province_smelters) > 0:
            aluminum_links = aluminum_links[province_smelters]
        else:
            print(f"Warning: No aluminum smelters found in province {province_filter}")
            return pd.DataFrame(), 0
    
    # Compute maximum power as base for normalization
    base = abs(aluminum_links.sum(axis=1)).max()
    
    if base == 0:
        print(f"Warning: Aluminum smelter power is zero {'in province ' + province_filter if province_filter else 'nationwide'}")
        return pd.DataFrame(), 0
    
    # Normalize by base
    df = aluminum_links.sum(axis=1) / base
    df = df.to_frame()
    df.reset_index(inplace=True)
    renames = {0: 'p_smelter'}
    df.rename(columns=renames, inplace=True)
    
    # Timestamps are already in China local time
    date = aluminum_links.index
    df['Hour'] = date.hour
    df['Day'] = date.strftime('%m-%d')
    
    # Build pivot table for heatmap
    summary = pd.pivot_table(data=df, index='Hour', columns='Day', values='p_smelter')
    summary = summary.fillna(0).infer_objects(copy=False)
    return summary, base

def get_aluminum_storage_daily_average(n, province_filter=None):
    """
    Compute daily average aluminum storage to overlay on the heatmap.
    
    Parameters:
    -----------
    n : pypsa.Network
        PyPSA Network with simulation results
    province_filter : str, optional
        If given, only include storage in the selected province
    
    Returns:
    --------
    tuple
        (daily average storage level, minimum storage level for normalization)
    """
    # Aluminum storage time series
    aluminum_stores = n.stores_t.e.filter(like='aluminum storage')
    
    if aluminum_stores.empty:
        print("Warning: No aluminum storage found in network")
        return pd.Series(), 0
    
    # Apply province filter if given
    if province_filter:
        province_stores = aluminum_stores.columns[aluminum_stores.columns.str.contains(province_filter, case=False)]
        if len(province_stores) > 0:
            aluminum_stores = aluminum_stores[province_stores]
        else:
            print(f"Warning: No aluminum storage found in province {province_filter}")
            return pd.Series(), 0
    
    # Aggregate total storage
    total_storage = aluminum_stores.sum(axis=1)
    
    if total_storage.empty:
        print(f"Warning: Aluminum storage data is empty {'in province ' + province_filter if province_filter else 'nationwide'}")
        return pd.Series(), 0
    
    # Minimum storage level for normalization
    min_storage = total_storage.min()
    
    # Daily average storage
    daily_avg = total_storage.groupby(total_storage.index.strftime('%m-%d')).mean()
    
    # Normalize by subtracting the minimum
    daily_avg_normalized = daily_avg - min_storage
    
    return daily_avg_normalized, min_storage

def plot_comparison_heatmap(n_mmm, n_nmm, config, output_dir, tech, province_filter=None):
    """
    Generate comparison heatmaps for MMMU and NMMU scenarios.
    
    Parameters:
    -----------
    n_mmm : pypsa.Network
        Network object for MMMU scenario
    n_nmm : pypsa.Network
        Network object for NMMU scenario
    config : dict
        Plotting configuration dictionary
    output_dir : str
        Directory to save the heatmap figure
    tech : str
        Technology to plot
    province_filter : str, optional
        If given, only include data in the selected province
    """
    freq = config["freq"]
    planning_horizon = "2050"
    
    # Create province-specific subdirectory when filtering by province
    if province_filter:
        province_dir = os.path.join(output_dir, f"province_{province_filter}")
        os.makedirs(province_dir, exist_ok=True)
        plot_title_suffix = f" in {province_filter}"
    else:
        province_dir = output_dir
        plot_title_suffix = " (National)"
    
    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(_mm_to_inches(FIG_WIDTH_MM), _mm_to_inches(FIG_HEIGHT_MM)),
    )
    
    # MMMU scenario
    if tech == "aluminum":
        df_mmm, base_mmm = create_aluminum_df(n_mmm, province_filter)
        daily_storage_mmm, min_storage_mmm = get_aluminum_storage_daily_average(n_mmm, province_filter)
    else:
        df_mmm, base_mmm = create_df(n_mmm, tech, province_filter)
        daily_storage_mmm, min_storage_mmm = None, None
    
    # NMMU scenario
    if tech == "aluminum":
        df_nmm, base_nmm = create_aluminum_df(n_nmm, province_filter)
        daily_storage_nmm, min_storage_nmm = get_aluminum_storage_daily_average(n_nmm, province_filter)
    else:
        df_nmm, base_nmm = create_df(n_nmm, tech, province_filter)
        daily_storage_nmm, min_storage_nmm = None, None
    
    # Plot MMMU heatmap
    if not df_mmm.empty and base_mmm > 0:
        base_mmm_display = str(int(base_mmm / 1e3))  # display in GW
        
        if tech == "aluminum":
            sns.heatmap(
                df_mmm,
                ax=ax1,
                cmap="coolwarm",
                cbar_kws={"label": "pu", "shrink": 1},
                vmin=0.0,
                vmax=1.0,
            )
        else:
            sns.heatmap(
                df_mmm,
                ax=ax1,
                cmap="coolwarm",
                cbar_kws={"label": "pu", "shrink": 0.8},
                vmin=-1.0,
                vmax=1.0,
            )
        _style_heatmap_colorbar(ax1)

        ax1.set_title("Mid smelter flexibility (core scenario)", fontsize=TEXT_PT)
        # Hide x-axis label (Day) for the first subplot
        ax1.set_xlabel('')
        # ax1.set_xticklabels([])
        # Keep y-axis labels upright
        ax1.set_yticklabels(ax1.get_yticklabels(), rotation=0)
        _reduce_heatmap_day_ticks(ax1, df_mmm.columns)

        # Overlay aluminum storage level (without extra labels)
        if tech == "aluminum" and not daily_storage_mmm.empty:
            day_columns = df_mmm.columns
            storage_values = []
            storage_positions = []
            
            for i, day in enumerate(day_columns):
                if day in daily_storage_mmm.index:
                    storage_values.append(daily_storage_mmm[day]/1e6)  # convert to Mt
                    storage_positions.append(i + 0.5)  # column center
            
            if storage_values:
                ax1_twin = ax1.twinx()
                # Outline in white first
                ax1_twin.plot(storage_positions, storage_values, "w-", linewidth=1.0, zorder=1)
                # Then draw black line inside
                ax1_twin.plot(storage_positions, storage_values, "k-", linewidth=0.7, zorder=2)
                ax1_twin.set_ylabel("Stored aluminum (Mt)", color="black", fontsize=TEXT_PT)
                ax1_twin.tick_params(axis="y", labelcolor="black", labelsize=TEXT_PT)
                ax1_twin.set_xlim(0, len(day_columns))
    
    # Plot NMMU heatmap
    if not df_nmm.empty and base_nmm > 0:
        base_nmm_display = str(int(base_nmm / 1e3))  # display in GW
        
        if tech == "aluminum":
            sns.heatmap(
                df_nmm,
                ax=ax2,
                cmap="coolwarm",
                cbar_kws={"label": "pu", "shrink": 0.8},
                vmin=0.0,
                vmax=1.0,
            )
        else:
            sns.heatmap(
                df_nmm,
                ax=ax2,
                cmap="coolwarm",
                cbar_kws={"label": "pu", "shrink": 0.8},
                vmin=-1.0,
                vmax=1.0,
            )
        _style_heatmap_colorbar(ax2)

        ax2.set_title("Non-constrained smelter flexibility", fontsize=TEXT_PT)
        # Add x-axis label for the second subplot
        ax2.set_xlabel("Day", fontsize=TEXT_PT)
        # Keep y-axis labels upright
        ax2.set_yticklabels(ax2.get_yticklabels(), rotation=0)
        _reduce_heatmap_day_ticks(ax2, df_nmm.columns)

        # Overlay aluminum storage level
        if tech == "aluminum" and not daily_storage_nmm.empty:
            day_columns = df_nmm.columns
            storage_values = []
            storage_positions = []
            
            for i, day in enumerate(day_columns):
                if day in daily_storage_nmm.index:
                    storage_values.append(daily_storage_nmm[day]/1e6)  # convert to Mt
                    storage_positions.append(i + 0.5)  # column center
            
            if storage_values:
                ax2_twin = ax2.twinx()
                # Outline in white first
                ax2_twin.plot(storage_positions, storage_values, "w-", linewidth=1.0, zorder=1)
                # Then draw black line inside
                ax2_twin.plot(
                    storage_positions,
                    storage_values,
                    "k-",
                    linewidth=0.7,
                    label="Stored aluminum",
                    zorder=2,
                )
                ax2_twin.set_ylabel("Stored aluminum (Mt)", color="black", fontsize=TEXT_PT)
                ax2_twin.tick_params(axis="y", labelcolor="black", labelsize=TEXT_PT)
                ax2_twin.legend(
                    loc="lower right",
                    bbox_to_anchor=(1.02, -0.39),
                    fontsize=TEXT_PT,
                    frameon=False,
                )
                ax2_twin.set_xlim(0, len(day_columns))
    
    for _ax in (ax1, ax2):
        _ax.tick_params(axis="both", which="major", labelsize=TEXT_PT)

    plt.tight_layout()
    # Room for colorbars on the right; do not use right>1 (breaks PDF width)
    plt.subplots_adjust(right=0.86, hspace=0.35)
    # Shift colorbars after layout (tight_layout resets manual positions)
    for _ax in (ax1, ax2):
        _shift_heatmap_colorbar_right(_ax)

    # Save figure
    output_path = os.path.join(province_dir, "smelter_flexibility_comparison.pdf")
    fig.savefig(output_path, format="pdf", bbox_inches="tight", facecolor="white")
    print(f"Saved {tech} comparison heatmap to: {output_path}")
    plt.close()

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description='Compare heatmaps between MMMU and NMMU scenarios')
    parser.add_argument('--config-mmm', type=str, 
                      default='configs/config_MMMU_2050_15p.yaml',
                      help='MMMU configuration file path')
    parser.add_argument('--config-nmm', type=str,
                      default='configs/config_NMMU_2050_15p.yaml', 
                      help='NMMU configuration file path')
    parser.add_argument('--output-dir', type=str,
                       default='results/comparison_heatmaps',
                       help='Output directory')
    parser.add_argument('--province', type=str, default=None,
                       help='Province filter (optional)')
    parser.add_argument('--techs', nargs='+', 
                       default=['H2', 'battery', 'water', 'aluminum'],
                       help='List of technologies to compare')
    
    args = parser.parse_args()
    
    # Set drawing style
    set_plot_style()
    
    # Load config files
    print("Loading configuration files...")
    with open(args.config_mmm, 'r', encoding='utf-8') as f:
        config_mmm = yaml.safe_load(f)
    
    with open(args.config_nmm, 'r', encoding='utf-8') as f:
        config_nmm = yaml.safe_load(f)
    
    # Plotting parameters (currently unused, kept for compatibility)
    map_figsize = config_mmm["plotting"]['map']['figsize']
    
    # Build network paths from version strings
    mmm_version = config_mmm['version']
    nmm_version = config_nmm['version']
    
    def _pick_network_path(version: str) -> str:
        candidates = [
            f"results/version-{version}/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2050.nc",
            f"results/version-{version}/postnetworks/positive/postnetwork-ll-current+Neighbor-linear2050-2050.nc",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return candidates[0]

    mmm_network_path = _pick_network_path(mmm_version)
    nmm_network_path = _pick_network_path(nmm_version)
    
    # Check network files
    if not os.path.exists(mmm_network_path):
        print(f"Error: MMMU network file not found: {mmm_network_path}")
        return
    
    if not os.path.exists(nmm_network_path):
        print(f"Error: NMMU network file not found: {nmm_network_path}")
        return
    
    # Load networks
    print("Loading network data...")
    n_mmm = pypsa.Network(mmm_network_path)
    n_nmm = pypsa.Network(nmm_network_path)
    
    # Ensure output directory exists
    os.makedirs(args.output_dir, exist_ok=True)

    # --- Additional output: monthly aluminum production & storage for MMMU ---
    try:
        # Aluminum smelter power (from electricity bus)
        smelter_p = n_mmm.links_t.p0.filter(like='aluminum smelter')
        if not smelter_p.empty:
            # Use absolute power to avoid sign ambiguity, then aggregate by calendar month
            smelter_power_total = smelter_p.abs().sum(axis=1)
            monthly = smelter_power_total.to_frame(name="smelter_power_total_MW")
            monthly["year_month"] = monthly.index.to_period("M").astype(str)
            monthly_stats = (
                monthly.groupby("year_month")["smelter_power_total_MW"]
                .agg(["mean", "sum"])
                .rename(columns={"mean": "smelter_power_MW_mean", "sum": "smelter_energy_MWh"})
            )
            # Convert electricity use to aluminum production using 13.4 MWh per tonne
            # (assuming 1-hour resolution so sum of MW over the month is MWh)
            monthly_stats["smelter_production_tonnes"] = (
                monthly_stats["smelter_energy_MWh"] / 13.4
            )
            monthly_stats["smelter_production_Mt"] = (
                monthly_stats["smelter_production_tonnes"] / 1e6
            )
        else:
            monthly_stats = None

        # Aluminum storage (state of charge)
        storage = n_mmm.stores_t.e.filter(like='aluminum storage')
        if not storage.empty:
            total_storage = storage.sum(axis=1)
            # Convert to Mt consistent with the overlay (divide by 1e6)
            storage_df = total_storage.to_frame(name="storage_Mt")
            storage_df["storage_Mt"] = storage_df["storage_Mt"] / 1e6
            storage_df["year_month"] = storage_df.index.to_period("M").astype(str)
            storage_monthly = (
                storage_df.groupby("year_month")["storage_Mt"]
                .agg(["mean", "min", "max"])
                .rename(
                    columns={
                        "mean": "storage_Mt_mean",
                        "min": "storage_Mt_min",
                        "max": "storage_Mt_max",
                    }
                )
            )
        else:
            storage_monthly = None

        if monthly_stats is not None or storage_monthly is not None:
            # Merge production and storage stats on year_month
            if monthly_stats is None:
                monthly_out = storage_monthly
            elif storage_monthly is None:
                monthly_out = monthly_stats
            else:
                monthly_out = monthly_stats.join(storage_monthly, how="outer")

            csv_path = os.path.join(
                args.output_dir, "mmmu_2050_monthly_aluminum_production_and_storage.csv"
            )
            monthly_out.to_csv(csv_path)
            print(f"Saved MMMU monthly aluminum production & storage to: {csv_path}")
    except Exception as e:
        print(f"Warning: failed to save MMMU monthly aluminum production/storage CSV: {e}")

    # Generate comparison heatmaps
    print("Generating comparison heatmaps...")
    for tech in args.techs:
        print(f"Processing {tech} technology...")
        plot_comparison_heatmap(n_mmm, n_nmm, config_mmm, args.output_dir, tech, args.province)
    
    print("All comparison heatmaps generated successfully!")

if __name__ == "__main__":
    main()
